"""Async client for Gnani (api.vachana.ai): Prisma v3 STT, voice-clone embedding, VC + stock TTS.

Every live HTTP attempt is appended to data/usage.jsonl (credits are scarce).
429 -> up to 2 retries with backoff. 402 -> GnaniError("OUT_OF_CREDITS").
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app import audio
from app.config import ConfigError, settings

log = logging.getLogger("apni.gnani")

STT_PATH = "/stt/v3"
EMBED_PATH = "/api/v1/tts/voice-clone/embeddings"
TTS_PATH = "/api/v1/tts/inference"

MAX_RETRIES = 2
RETRY_BACKOFF_S = (0.6, 1.5)  # tests set this to (0, 0)
MAX_RETRY_AFTER_S = 3.0

TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=15.0, pool=5.0)
# A fresh TLS connection to api.vachana.ai measured ~1 s; keep idle connections much longer than
# httpx's 5 s default so a tap after a pause does not pay the handshake again.
LIMITS = httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=300.0)


class GnaniError(Exception):
    """`code` is a contract error code; `status` is the HTTP status we return to the browser."""

    def __init__(self, code: str, message: str, status: int = 502):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# --------------------------------------------------------------------------- client

_client: httpx.AsyncClient | None = None
_client_loop: asyncio.AbstractEventLoop | None = None


def _get_client() -> httpx.AsyncClient:
    """One keep-alive client per event loop (saves a TLS handshake per call)."""
    global _client, _client_loop
    loop = asyncio.get_running_loop()
    if _client is None or _client.is_closed or _client_loop is not loop:
        try:
            key = settings.require_gnani_key()
        except ConfigError as exc:
            raise GnaniError("UPSTREAM_ERROR", str(exc), 500) from exc
        _client = httpx.AsyncClient(
            base_url=settings.gnani_base_url,
            headers={"X-API-Key-ID": key, "User-Agent": settings.user_agent, "Accept": "*/*"},
            timeout=TIMEOUT,
            limits=LIMITS,
        )
        _client_loop = loop
    return _client


async def warmup() -> bool:
    """Open the pooled TLS connection to api.vachana.ai with a NON-billable request
    (GET / with an empty key header -> 401 MISSING_API_KEY), so the first real STT/TTS
    call after a (re)start does not pay the ~1 s handshake. Nothing is billed, so it is
    not written to usage.jsonl. Never raises."""
    try:
        r = await _get_client().get("/", headers={"X-API-Key-ID": ""}, timeout=5.0)
        log.info("Gnani warmup: HTTP %d (connection open, not billed)", r.status_code)
        return True
    except Exception as exc:  # noqa: BLE001 - best effort
        log.warning("Gnani warmup failed: %s", type(exc).__name__)
        return False


async def aclose() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        try:
            await _client.aclose()
        except RuntimeError:  # client belonged to a loop that is already gone (tests)
            pass
    _client = None


# --------------------------------------------------------------------------- usage log


def _log_usage(endpoint: str, status: int | str, ms: int, *, chars: int | None = None,
               audio_seconds: float | None = None, attempt: int = 1, note: str | None = None) -> None:
    row: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "endpoint": endpoint,
        "status": status,
        "ms": ms,
    }
    if chars is not None:
        row["chars"] = chars
    if audio_seconds is not None:
        row["audio_seconds"] = round(audio_seconds, 2)
    if attempt > 1:
        row["attempt"] = attempt
    if note:
        row["note"] = note
    try:
        settings.usage_log.parent.mkdir(parents=True, exist_ok=True)
        with settings.usage_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as exc:  # never fail a user request because logging failed
        log.warning("Could not write usage log: %s", exc)


# --------------------------------------------------------------------------- core request


def _error_detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        if isinstance(body, dict):
            for k in ("message", "detail", "error"):
                if body.get(k):
                    return str(body[k])[:200]
    except ValueError:
        pass
    return resp.text[:200]


async def _request(path: str, *, json_body: dict | None = None, files: dict | None = None,
                   data: dict | None = None, chars: int | None = None,
                   audio_seconds: float | None = None) -> httpx.Response:
    client = _get_client()
    attempt = 0
    while True:
        attempt += 1
        t0 = time.perf_counter()
        try:
            resp = await client.post(path, json=json_body, files=files, data=data)
        except httpx.TimeoutException as exc:
            _log_usage(path, "timeout", int((time.perf_counter() - t0) * 1000), chars=chars,
                       audio_seconds=audio_seconds, attempt=attempt)
            raise GnaniError("UPSTREAM_ERROR", "Gnani took too long to respond. Please try again.", 504) from exc
        except httpx.TransportError as exc:
            _log_usage(path, "network_error", int((time.perf_counter() - t0) * 1000), chars=chars,
                       audio_seconds=audio_seconds, attempt=attempt)
            raise GnaniError("UPSTREAM_ERROR", "Could not reach Gnani. Check the internet connection.", 502) from exc
        ms = int((time.perf_counter() - t0) * 1000)
        _log_usage(path, resp.status_code, ms, chars=chars, audio_seconds=audio_seconds, attempt=attempt)

        if resp.status_code == 429 and attempt <= MAX_RETRIES:
            delay = RETRY_BACKOFF_S[min(attempt - 1, len(RETRY_BACKOFF_S) - 1)]
            try:
                delay = max(delay, min(float(resp.headers.get("retry-after", 0)), MAX_RETRY_AFTER_S))
            except ValueError:
                pass
            log.warning("Gnani 429 on %s, retry %d/%d in %.1fs", path, attempt, MAX_RETRIES, delay)
            await asyncio.sleep(delay)
            continue
        if resp.status_code < 400:
            return resp
        detail = _error_detail(resp)
        log.warning("Gnani %s on %s: %s", resp.status_code, path, detail)
        if resp.status_code == 402:
            raise GnaniError("OUT_OF_CREDITS", "Gnani credits are exhausted.", 402)
        if resp.status_code == 429:
            raise GnaniError("UPSTREAM_ERROR", "Gnani is rate limiting us. Wait a moment and try again.", 503)
        if resp.status_code == 401:
            raise GnaniError("UPSTREAM_ERROR", "Gnani rejected the API key.", 502)
        raise GnaniError("UPSTREAM_ERROR", f"Gnani error {resp.status_code}: {detail}", 502)


def _json(resp: httpx.Response) -> dict:
    try:
        body = resp.json()
    except ValueError as exc:
        raise GnaniError("UPSTREAM_ERROR", "Gnani returned an unreadable response.") from exc
    if not isinstance(body, dict):
        raise GnaniError("UPSTREAM_ERROR", "Gnani returned an unexpected response.")
    if body.get("success") is False:
        raise GnaniError("UPSTREAM_ERROR", f"Gnani: {body.get('message') or body.get('error') or 'request failed'}")
    return body


# --------------------------------------------------------------------------- STT


async def transcribe(wav_path: Path, keyterms: list[str] | None = None, previous_text: str | None = None) -> str:
    """Prisma v3, hi-IN, codemix (Hindi in Devanagari, English words in Latin). Returns the transcript."""
    return await transcribe_bytes(Path(wav_path).read_bytes(), keyterms=keyterms, previous_text=previous_text)


async def transcribe_bytes(
    wav_bytes: bytes,
    fmt: str = "codemix",
    keyterms: list[str] | None = None,
    previous_text: str | None = None,
) -> str:
    """Same as transcribe() but from in-memory WAV bytes. `fmt="verbatim"` writes everything in
    Devanagari, which is what the voice-clone self-check compares against."""
    try:
        seconds = audio.wav_duration(wav_bytes)
    except ValueError:
        seconds = None
    form = {"model": "prisma-v3.0", "language_code": "hi-IN", "format": fmt}
    if keyterms:
        form["keyterms"] = json.dumps(keyterms, ensure_ascii=False)
    if previous_text:
        form["previous_text"] = previous_text[-500:]
    resp = await _request(
        STT_PATH,
        files={"audio_file": ("audio.wav", wav_bytes, "audio/wav")},  # Gnani detects format by extension
        data=form,
        audio_seconds=seconds,
    )
    body = _json(resp)
    transcript = body.get("transcript")
    if not isinstance(transcript, str):
        raise GnaniError("UPSTREAM_ERROR", "Gnani STT response had no transcript.")
    return transcript.strip()


# --------------------------------------------------------------------------- voice clone


async def create_embedding(wav_path: Path) -> dict:
    """Returns the whole `voice_clone_embedding` object ({embedding, shape, dtype}) for VC TTS."""
    wav_bytes = Path(wav_path).read_bytes()
    try:
        seconds = audio.wav_duration(wav_bytes)
    except ValueError:
        seconds = None
    resp = await _request(
        EMBED_PATH,
        files={"audio_file": ("voice.wav", wav_bytes, "audio/wav")},
        audio_seconds=seconds,
    )
    body = _json(resp)
    emb = (body.get("data") or {}).get("voice_clone_embedding")
    if not isinstance(emb, dict) or not emb.get("embedding"):
        raise GnaniError("UPSTREAM_ERROR", "Gnani did not return a voice embedding. Try a clearer recording.")
    return emb


# --------------------------------------------------------------------------- TTS

_SENTENCE_END = re.compile(r"(?<=[।॥.?!])\s+|(?<=[।॥])")
_CLAUSE_END = re.compile(r"(?<=[,;:])\s+")


def normalize_text(text: str) -> str:
    """Collapse whitespace and apply Unicode NFC, so canonically equal Devanagari (e.g. the
    precomposed nukta letter U+095C vs ड + ़) gives one TTS cache key: a clip pre-warmed by
    scripts/prewarm_demo.sh is a HIT whichever form the LLM or the phrase board used."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text or "")).strip()


def _pack(pieces: list[str], limit: int, joiner: str = " ") -> list[str]:
    chunks: list[str] = []
    cur = ""
    for p in pieces:
        cand = f"{cur}{joiner}{p}" if cur else p
        if len(cand) <= limit:
            cur = cand
        else:
            if cur:
                chunks.append(cur)
            cur = p
    if cur:
        chunks.append(cur)
    return chunks


def split_text(text: str, limit: int | None = None) -> list[str]:
    """Split text into chunks of <= limit chars on sentence boundaries (।, ., ?, !),
    falling back to clause boundaries and then words for very long sentences."""
    limit = limit or settings.tts_chunk_chars
    text = normalize_text(text)
    if len(text) <= limit:
        return [text] if text else []
    pieces: list[str] = []
    for sentence in (s.strip() for s in _SENTENCE_END.split(text)):
        if not sentence:
            continue
        if len(sentence) <= limit:
            pieces.append(sentence)
            continue
        for clause in (c.strip() for c in _CLAUSE_END.split(sentence)):
            if len(clause) <= limit:
                pieces.append(clause)
            else:
                words = _pack(clause.split(" "), limit)
                for w in words:  # a single "word" longer than the limit: hard cut
                    pieces.extend(w[i:i + limit] for i in range(0, len(w), limit))
    return _pack(pieces, limit)


def _audio_config() -> dict:
    return {
        "sample_rate": settings.tts_sample_rate,
        "num_channels": 1,
        "sample_width": 2,
        "encoding": "linear_pcm",
        "container": "wav",
    }


async def _tts_call(body: dict) -> bytes:
    resp = await _request(TTS_PATH, json_body=body, chars=len(body["text"]))
    ctype = resp.headers.get("content-type", "")
    if "json" in ctype:
        _json(resp)  # raises with Gnani's message if success == false
        raise GnaniError("UPSTREAM_ERROR", "Gnani TTS returned JSON instead of audio.")
    try:
        return await audio.normalize_wav(resp.content, settings.tts_sample_rate)
    except audio.AudioError as exc:
        raise GnaniError("UPSTREAM_ERROR", "Gnani TTS returned unreadable audio.") from exc


async def _speak_chunks(text: str, make_body) -> bytes:
    chunks = split_text(text)
    if not chunks:
        raise GnaniError("BAD_REQUEST", "Nothing to speak.", 400)
    parts = [await _tts_call(make_body(c)) for c in chunks]  # sequential: keeps order, avoids 429 bursts
    try:
        return audio.concat_wavs(parts)
    except ValueError as exc:
        raise GnaniError("UPSTREAM_ERROR", f"Could not join TTS audio: {exc}") from exc


async def speak_clone(text: str, embedding: dict) -> bytes:
    """Speak Devanagari text in the cloned voice (vachana-vc-v1). Returns a normalised WAV."""
    return await _speak_chunks(text, lambda chunk: {
        "text": chunk,
        "model": settings.clone_model,
        "speaker_embedding": embedding,
        "audio_config": _audio_config(),
    })


async def speak_stock(text: str, voice: str = "Prakash", model: str | None = None) -> bytes:
    """Speak with a stock timbre-v3.0 voice (hi-IN). Returns a normalised WAV."""
    return await _speak_chunks(text, lambda chunk: {
        "text": chunk,
        "voice": voice,
        "model": model or settings.stock_model,
        "language": "hi-IN",
        "speed": 1.0,
        "audio_config": _audio_config(),
    })
