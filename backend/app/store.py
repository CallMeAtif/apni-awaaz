"""Local storage: banked voices and the TTS disk cache.

Layout (under settings.data_dir, gitignored):
  voices/{voice_id}.json   {"voice_id","name","created_at","duration_s","embedding":{embedding,shape,dtype}}
  voices/{voice_id}.wav    the converted enrolment sample (44.1 kHz mono s16le)
  cache/tts/{voice_key}/{sha256(model|voice_id|text)}.wav

Cache entries are grouped per voice so deleting a voice also deletes every clip spoken in it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def valid_id(voice_id: str | None) -> bool:
    return bool(voice_id) and bool(_ID_RE.match(voice_id))


def _json_path(voice_id: str) -> Path:
    return settings.voices_dir / f"{voice_id}.json"


def _wav_path(voice_id: str) -> Path:
    return settings.voices_dir / f"{voice_id}.wav"


# --------------------------------------------------------------------------- voices


def save_voice(name: str, embedding: dict, sample_wav: Path, duration_s: float) -> dict:
    voice_id = secrets.token_hex(8)
    record = {
        "voice_id": voice_id,
        "name": name,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "duration_s": round(duration_s, 2),
        "embedding": embedding,
    }
    _atomic_write(_wav_path(voice_id), Path(sample_wav).read_bytes())
    _atomic_write(_json_path(voice_id), json.dumps(record, ensure_ascii=False, indent=1).encode("utf-8"))
    return record


def get_voice(voice_id: str) -> dict | None:
    if not valid_id(voice_id):
        return None
    try:
        return json.loads(_json_path(voice_id).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def list_voices() -> list[dict]:
    """Newest first. Embeddings are not included."""
    out = []
    if not settings.voices_dir.exists():
        return out
    for p in settings.voices_dir.glob("*.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append({"voice_id": rec["voice_id"], "name": rec.get("name", ""), "created_at": rec.get("created_at", "")})
    out.sort(key=lambda r: r["created_at"], reverse=True)
    return out


def delete_voice(voice_id: str) -> bool:
    """Delete the voice record, its sample and every cached clip in that voice."""
    if not valid_id(voice_id):
        return False
    existed = _json_path(voice_id).exists()
    _json_path(voice_id).unlink(missing_ok=True)
    _wav_path(voice_id).unlink(missing_ok=True)
    shutil.rmtree(settings.tts_cache_dir / _voice_key(voice_id), ignore_errors=True)
    return existed


# --------------------------------------------------------------------------- TTS cache


def _voice_key(voice_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", voice_id)


def cache_key(model: str, voice_id: str, text: str) -> str:
    return hashlib.sha256(f"{model}|{voice_id}|{text}".encode("utf-8")).hexdigest()


def _cache_path(model: str, voice_id: str, text: str) -> Path:
    return settings.tts_cache_dir / _voice_key(voice_id) / f"{cache_key(model, voice_id, text)}.wav"


def cache_get(model: str, voice_id: str, text: str) -> bytes | None:
    try:
        return _cache_path(model, voice_id, text).read_bytes()
    except FileNotFoundError:
        return None


def cache_put(model: str, voice_id: str, text: str, wav: bytes) -> None:
    _atomic_write(_cache_path(model, voice_id, text), wav)
