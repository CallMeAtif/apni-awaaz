"""Apni Awaaz HTTP API. The contract is frozen in docs/TEAM_BRIEF.md, section 5."""
from __future__ import annotations

import asyncio
import importlib
import json
import logging
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import audio as audiolib
from app import gnani, llm, store, verify
from app.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("apni.main")


# --------------------------------------------------------------------------- errors


class ApiError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


AUDIO_STATUS = {"AUDIO_TOO_SHORT": 400, "AUDIO_TOO_LONG": 413, "BAD_AUDIO": 400}


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message}},
        headers={"Cache-Control": "no-store"},
    )


# --------------------------------------------------------------------------- app


_speak_locks: dict[str, asyncio.Lock] = {}  # one in-flight TTS call per cache key (double taps)


async def _warm_upstreams() -> dict:
    """Open the NVIDIA and Gnani connections (no billable Gnani call) so the first real
    request after a (re)start or a long pause is not a slow cold call."""
    t0 = time.perf_counter()

    async def safe(coro) -> bool:
        try:
            return bool(await coro)
        except Exception:  # noqa: BLE001 - warmup is best effort
            return False

    llm_ok, gnani_ok = await asyncio.gather(safe(llm.warmup()), safe(gnani.warmup()))
    result = {"llm": llm_ok, "gnani": gnani_ok, "ms": _ms(t0)}
    log.info("warmup: %s", result)
    return result


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.check_startup()  # raises ConfigError with a clear message if GNANI_API_KEY/ffmpeg is missing
    settings.ensure_dirs()
    _speak_locks.clear()
    log.info("Apni Awaaz backend ready (data dir %s)", settings.data_dir)
    warm_task = asyncio.create_task(_warm_upstreams()) if settings.warmup_on_startup else None
    yield
    if warm_task is not None and not warm_task.done():
        warm_task.cancel()
    await gnani.aclose()
    if hasattr(llm, "aclose"):
        try:
            await llm.aclose()
        except Exception:  # noqa: BLE001 - shutdown must not fail
            pass


app = FastAPI(title="Apni Awaaz", version="0.1.0", lifespan=lifespan)


class NoStoreJSONMiddleware:
    """Adds `Cache-Control: no-store` to every JSON response."""

    def __init__(self, asgi_app):
        self.app = asgi_app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if headers.get("content-type", "").startswith("application/json") and "cache-control" not in headers:
                    headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_wrapper)


app.add_middleware(NoStoreJSONMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Cache", "Server-Timing"],
)


@app.exception_handler(ApiError)
async def _api_error(_: Request, exc: ApiError):
    return error_response(exc.status, exc.code, exc.message)


@app.exception_handler(gnani.GnaniError)
async def _gnani_error(_: Request, exc: gnani.GnaniError):
    return error_response(exc.status, exc.code, exc.message)


@app.exception_handler(audiolib.AudioError)
async def _audio_error(_: Request, exc: audiolib.AudioError):
    return error_response(AUDIO_STATUS.get(exc.code, 400), exc.code, exc.message)


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError):
    errs = exc.errors()
    if errs:
        loc = ".".join(str(p) for p in errs[0].get("loc", ()) if p != "body")
        msg = f"{loc}: {errs[0].get('msg', 'invalid')}" if loc else errs[0].get("msg", "invalid")
    else:
        msg = "Invalid request."
    return error_response(400, "BAD_REQUEST", msg)


@app.exception_handler(StarletteHTTPException)
async def _http_error(_: Request, exc: StarletteHTTPException):
    code = "NOT_FOUND" if exc.status_code == 404 else "BAD_REQUEST"
    return error_response(exc.status_code, code, str(exc.detail))


@app.exception_handler(Exception)
async def _unexpected(_: Request, exc: Exception):
    log.exception("Unhandled error: %s", exc)
    return error_response(500, "UPSTREAM_ERROR", "Something went wrong on the server. Please try again.")


# --------------------------------------------------------------------------- helpers

_EXT_BY_MIME = {
    "audio/webm": ".webm", "video/webm": ".webm",
    "audio/mp4": ".mp4", "video/mp4": ".mp4", "audio/m4a": ".m4a", "audio/x-m4a": ".m4a",
    "audio/aac": ".aac", "audio/ogg": ".ogg", "application/ogg": ".ogg", "audio/opus": ".opus",
    "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/wave": ".wav", "audio/vnd.wave": ".wav",
    "audio/mpeg": ".mp3", "audio/flac": ".flac",
}
_ALLOWED_EXT = set(_EXT_BY_MIME.values()) | {".oga"}


async def _save_upload(upload: UploadFile | None, tmp: Path) -> Path:
    """Write the multipart `audio` upload to tmp/, keeping a sensible extension for ffmpeg."""
    if upload is None:
        raise ApiError("BAD_REQUEST", "Missing multipart field 'audio'.")
    ext = Path(upload.filename or "").suffix.lower()
    if ext not in _ALLOWED_EXT:
        mime = (upload.content_type or "").split(";")[0].strip().lower()
        ext = _EXT_BY_MIME.get(mime, ".bin")  # ffmpeg sniffs the real container anyway
    dst = tmp / f"upload{ext}"
    size = 0
    with dst.open("wb") as f:
        while chunk := await upload.read(1 << 20):
            size += len(chunk)
            if size > settings.max_upload_bytes:
                raise ApiError("AUDIO_TOO_LONG", "The recording file is too large.", 413)
            f.write(chunk)
    if size == 0:
        raise audiolib.AudioError("BAD_AUDIO", "The recording is empty. Please record again.")
    return dst


def _clean_history(raw: Any) -> list[dict]:
    """Keep only well-formed [{"role": "family"|"user", "text": str}] items, oldest first."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ApiError("BAD_REQUEST", "history must be a JSON array of {role, text}.")
    out = []
    for item in raw:
        if isinstance(item, dict) and item.get("role") in ("family", "user") and isinstance(item.get("text"), str):
            text = item["text"].strip()
            if text:
                out.append({"role": item["role"], "text": text[:500]})
    return out[-10:]


def _parse_history_field(history: str | None) -> list[dict]:
    if history is None or not history.strip():
        return []
    try:
        return _clean_history(json.loads(history))
    except json.JSONDecodeError:
        raise ApiError("BAD_REQUEST", "history must be valid JSON.")


async def _suggest(transcript: str, history: list[dict], *, variety: bool = False) -> list[dict]:
    try:
        raw = await llm.suggest_replies(transcript, history, variety=variety)
    except Exception:  # llm.py promises not to raise; never let it break /api/listen anyway
        log.exception("llm.suggest_replies raised")
        return []
    out = []
    for s in raw or []:
        if isinstance(s, dict) and isinstance(s.get("text"), str) and s["text"].strip():
            out.append({"text": s["text"].strip(), "intent": str(s.get("intent") or "other")})
    return out


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


# Lead-owned modules (phrases.py, passage.py) are imported lazily and defensively so the
# API keeps working while they are being written; a later successful import is picked up.
_FALLBACK_PASSAGE = (
    "नमस्ते, मेरा नाम रमेश है। सुबह की पहली चाय मुझे बहुत पसंद है। "
    "बारिश के मौसम में हम सब बालकनी में बैठकर गरम पकौड़े खाते हैं। "
    "मेरी बेटी प्रिया रोज़ शाम को फ़ोन करती है, और उसकी हँसी सुनकर दिन अच्छा हो जाता है। "
    "आज बाज़ार से ताज़ी सब्ज़ियाँ, आम और थोड़ा दही लाना है। धन्यवाद, फिर मिलेंगे।"
)
_FALLBACK_PHRASES = [
    {
        "category": "ज़रूरतें",
        "category_en": "Needs",
        "phrases": [
            {"text": "पानी चाहिए", "en": "I need water", "emoji": "💧"},
            {"text": "मुझे बाथरूम जाना है", "en": "I need the bathroom", "emoji": "🚻"},
            {"text": "मुझे भूख लगी है", "en": "I am hungry", "emoji": "🍲"},
        ],
    },
    {
        "category": "तबीयत",
        "category_en": "Health",
        "phrases": [
            {"text": "दर्द हो रहा है", "en": "I am in pain", "emoji": "🤕"},
            {"text": "मैं ठीक हूँ", "en": "I am fine", "emoji": "🙂"},
        ],
    },
]


def _lead_attr(module: str, attr: str) -> Any | None:
    try:
        return getattr(importlib.import_module(f"app.{module}"), attr)
    except Exception as exc:  # noqa: BLE001 - missing or half-written file
        log.warning("app.%s.%s unavailable (%s); using built-in fallback", module, attr, type(exc).__name__)
        return None


# --------------------------------------------------------------------------- models


class SuggestIn(BaseModel):
    transcript: str
    history: list[Any] | None = None


class ComposeIn(BaseModel):
    text: str


class SpeakIn(BaseModel):
    voice_id: str | None = None
    text: str


# --------------------------------------------------------------------------- routes


@app.get("/api/health")
async def health():
    return {"ok": True, "gnani_key": bool(settings.gnani_api_key), "llm_key": bool(settings.nvidia_api_key)}


@app.post("/api/warmup")
async def warmup():
    """Called by the Talk screen on mount and every ~4 min while visible. No billable call."""
    return await _warm_upstreams()


@app.get("/api/enroll/passage")
async def enroll_passage():
    text = _lead_attr("passage", "ENROLL_PASSAGE")
    if not isinstance(text, str) or not text.strip():
        text = _FALLBACK_PASSAGE
    return {"text": text}


@app.get("/api/phrases")
async def phrases():
    get_phrases = _lead_attr("phrases", "get_phrases")
    if callable(get_phrases):
        try:
            data = get_phrases()
            if isinstance(data, list) and data:
                return data
        except Exception:  # noqa: BLE001
            log.exception("phrases.get_phrases raised; using fallback")
    return _FALLBACK_PHRASES


@app.post("/api/voices", status_code=201)
async def create_voice(
    audio: UploadFile | None = File(None),
    name: str = Form(""),
    consent: str = Form(""),
):
    if consent.strip().lower() not in ("true", "1", "yes", "on"):
        raise ApiError("BAD_REQUEST", "Consent is required: confirm this is your own voice and you agree to it being cloned.")
    name = " ".join(name.split())[:60] or "My voice"
    with tempfile.TemporaryDirectory(prefix="apni-enroll-") as tmp:
        src = await _save_upload(audio, Path(tmp))
        wav = Path(tmp) / "enroll.wav"
        # -t 30 in ffmpeg: anything longer is trimmed to Gnani's 30 s limit.
        await audiolib.convert_to_wav(src, wav, settings.enroll_sample_rate, max_seconds=settings.max_audio_s)
        duration = await audiolib.probe_duration(wav)
        if duration < settings.min_enroll_s:
            raise audiolib.AudioError(
                "AUDIO_TOO_SHORT", f"Recording is {duration:.1f} s; please record at least {settings.min_enroll_s:.0f} s."
            )
        embedding = await gnani.create_embedding(wav)
        rec = store.save_voice(name, embedding, wav, duration)
    return JSONResponse(
        status_code=201,
        content={"voice_id": rec["voice_id"], "name": rec["name"], "duration_s": rec["duration_s"],
                 "created_at": rec["created_at"]},
    )


@app.get("/api/voices")
async def list_voices():
    return store.list_voices()


@app.delete("/api/voices/{voice_id}", status_code=204)
async def delete_voice(voice_id: str):
    if not store.delete_voice(voice_id):
        raise ApiError("NOT_FOUND", "No such voice.", 404)
    return Response(status_code=204)


@app.post("/api/listen")
async def listen(audio: UploadFile | None = File(None), history: str | None = Form(None)):
    hist = _parse_history_field(history)
    t_start = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="apni-listen-") as tmp:
        src = await _save_upload(audio, Path(tmp))
        wav = Path(tmp) / "listen.wav"
        hard_cap = settings.max_audio_s + settings.listen_tolerance_s
        await audiolib.convert_to_wav(src, wav, settings.stt_sample_rate, max_seconds=hard_cap + 0.5)
        duration = await audiolib.probe_duration(wav)
        if duration < settings.min_listen_s:
            raise audiolib.AudioError("AUDIO_TOO_SHORT", "The recording is too short. Hold the button and speak.")
        if duration > hard_cap:
            raise audiolib.AudioError("AUDIO_TOO_LONG", f"Please keep it under {settings.max_audio_s:.0f} seconds.")
        if duration > settings.max_audio_s:
            audiolib.trim_wav_file(wav, settings.max_audio_s)
        convert_ms = _ms(t_start)

        t0 = time.perf_counter()
        transcript = await gnani.transcribe(
            wav,
            keyterms=settings.stt_keyterms or None,
            previous_text=hist[-1]["text"] if hist else None,
        )
        stt_ms = _ms(t0)

    suggestions: list[dict] = []
    llm_ms = 0
    if transcript:
        t1 = time.perf_counter()
        suggestions = await _suggest(transcript, hist)
        llm_ms = _ms(t1)
    return JSONResponse(
        {"transcript": transcript, "suggestions": suggestions, "timings": {"stt_ms": stt_ms, "llm_ms": llm_ms}},
        headers={"Server-Timing": f"convert;dur={convert_ms}, stt;dur={stt_ms}, llm;dur={llm_ms}"},
    )


@app.post("/api/suggest")
async def suggest(body: SuggestIn):
    transcript = body.transcript.strip()
    if not transcript:
        return {"suggestions": []}
    # "More options": ask for different suggestions than the deterministic /api/listen ones.
    return {"suggestions": await _suggest(transcript, _clean_history(body.history), variety=True)}


@app.post("/api/compose")
async def compose(body: ComposeIn):
    text = gnani.normalize_text(body.text)
    if not text:
        raise ApiError("BAD_REQUEST", "Type something first.")
    if len(text) > 500:
        raise ApiError("BAD_REQUEST", "Text is too long (max 500 characters).")
    try:
        out = await llm.to_devanagari(text)
    except Exception:  # llm.py promises not to raise
        log.exception("llm.to_devanagari raised")
        out = text
    return {"text": (out or text).strip()}


async def _trim_clone_tail(text: str, wav: bytes) -> bytes:
    """Remove the trailing "aaahhh" vachana-vc-v1 adds after a silent gap (app/verify.py). A cut is
    kept only if Prisma still hears every word; at most two cut points are tried."""
    if not settings.trim_clone_tail:
        return wav
    for t in verify.tail_cut_candidates(wav, text)[:2]:
        cut = verify.cut_wav(wav, t)
        try:
            heard = await gnani.transcribe_bytes(verify.pad_for_stt(cut), fmt="verbatim")
        except Exception:
            log.warning("tail trim skipped (STT failed) for %r", text, exc_info=True)
            return wav
        ok = verify.speech_complete(text, heard)
        log.info("tail trim at %.2fs: ok=%s text=%r heard=%r", t, ok, text, heard)
        if ok:
            return cut
    return wav


async def _speak_clone_verified(text: str, embedding: dict) -> tuple[bytes, bool]:
    """Generate a cloned clip and check it with Prisma; regenerate if words were repeated or added.

    Returns (wav, verified). If Prisma itself fails we play the clip rather than block speech."""
    if not settings.verify_clone_tts:
        return await gnani.speak_clone(text, embedding), True
    best, best_score = b"", -1.0
    for attempt in range(1, settings.verify_max_attempts + 1):
        wav = await gnani.speak_clone(text, embedding)
        try:
            heard = await gnani.transcribe_bytes(verify.pad_for_stt(wav), fmt="verbatim")
        except Exception:
            log.warning("clone self-check skipped (STT failed) for %r", text, exc_info=True)
            return wav, True
        ok, score = verify.speech_matches(text, heard)
        log.info("clone self-check attempt %d: ok=%s score=%.2f text=%r heard=%r", attempt, ok, score, text, heard)
        if ok:
            return await _trim_clone_tail(text, wav), True
        if score > best_score:
            best, best_score = wav, score
    log.warning("clone self-check failed %d times for %r; playing best attempt uncached", settings.verify_max_attempts, text)
    return best, False


@app.post("/api/speak")
async def speak(body: SpeakIn):
    text = gnani.normalize_text(body.text)
    if not text:
        raise ApiError("BAD_REQUEST", "Nothing to speak.")
    if len(text) > settings.max_speak_chars:
        raise ApiError("BAD_REQUEST", f"Text is too long (max {settings.max_speak_chars} characters).")

    voice = None
    if body.voice_id:
        voice = store.get_voice(body.voice_id)
        if voice is None:
            raise ApiError("NOT_FOUND", "That voice no longer exists. Bank your voice again.", 404)
        model, cache_voice = settings.clone_model, body.voice_id
    else:
        model, cache_voice = settings.stock_model, f"stock-{settings.stock_voice}"

    t0 = time.perf_counter()
    key = store.cache_key(model, cache_voice, text)
    lock = _speak_locks.setdefault(key, asyncio.Lock())
    async with lock:
        wav = store.cache_get(model, cache_voice, text)
        if wav is not None:
            cache = "HIT"
        else:
            verified = True
            if voice is not None:
                wav, verified = await _speak_clone_verified(text, voice["embedding"])
            else:
                wav = await gnani.speak_stock(text, voice=settings.stock_voice)
            if verified:  # an unverified best-effort clip is played but not cached, so a later tap retries
                store.cache_put(model, cache_voice, text, wav)
            cache = "MISS"
    return Response(
        content=wav,
        media_type="audio/wav",
        headers={"X-Cache": cache, "Server-Timing": f"tts;dur={_ms(t0)}"},
    )
