"""Shared test setup. NO live calls: every outbound HTTP request must hit a respx route.

Dummy keys are forced into the environment *before* app.config is imported, so the real
keys in apni-awaaz/.env are never loaded during tests (load_dotenv does not override).
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

os.environ["GNANI_API_KEY"] = "test-gnani-key"
os.environ["GNANI_KEY"] = "test-gnani-key"
os.environ["NVIDIA_API_KEY"] = "test-nvidia-key"
os.environ["APNI_WARMUP_ON_STARTUP"] = "0"  # lifespan must not open live connections

import httpx  # noqa: E402
import pytest  # noqa: E402
import respx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import gnani  # noqa: E402
from app.config import settings  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
VOICE_WAV = FIXTURES / "synthetic_voice_deepak_9s.wav"  # 9.44 s, 44.1 kHz mono
CLONE_WAV = FIXTURES / "synthetic_clone_reply_3s.wav"  # 3.22 s VC output
EMBED_RESPONSE = json.loads((FIXTURES / "synthetic_embedding.json").read_text())
EMBEDDING = EMBED_RESPONSE["data"]["voice_clone_embedding"]

GNANI = "https://api.vachana.ai"
STT_URL = f"{GNANI}/stt/v3"
EMBED_URL = f"{GNANI}/api/v1/tts/voice-clone/embeddings"
TTS_URL = f"{GNANI}/api/v1/tts/inference"
NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

FAMILY_TRANSCRIPT = "पापा, आज खाने में क्या बनाऊँ? दाल चावल चलेगा?"
STT_OK = {
    "success": True, "request_id": "test", "transcript": FAMILY_TRANSCRIPT, "model": "prisma-v3.0",
    "processing_time": 0.15, "end_to_end_latency": 0.2, "detected_language_code": "hi-IN",
}
NVIDIA_OK = {
    "id": "test", "object": "chat.completion", "model": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
    "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps([
        {"text": "हाँ बेटा, दाल चावल बढ़िया रहेगा।", "intent": "yes"},
        {"text": "नहीं, आज खिचड़ी बना लो।", "intent": "no"},
        {"text": "तुम क्या खाना चाहती हो?", "intent": "ask"},
    ], ensure_ascii=False)}}],
}


def streaming_header(wav: bytes) -> bytes:
    """Mimic Gnani stock TTS: RIFF and data sizes set to 0xFFFFFFFF."""
    b = bytearray(wav)
    b[4:8] = b"\xff\xff\xff\xff"
    i = b.find(b"data")
    b[i + 4:i + 8] = b"\xff\xff\xff\xff"
    return bytes(b)


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    """Every test gets its own data dir (voices, cache, usage.jsonl) and no retry sleeps."""
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(gnani, "RETRY_BACKOFF_S", (0.0, 0.0))
    monkeypatch.setattr(settings, "verify_clone_tts", False)  # tests that need it switch it on
    settings.ensure_dirs()
    yield tmp_path / "data"


@pytest.fixture
def api():
    """respx router that mocks Gnani + NVIDIA and refuses any other outbound request."""
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        router.post(STT_URL, name="stt").respond(json=STT_OK)
        router.post(EMBED_URL, name="embed").respond(json=EMBED_RESPONSE)
        router.post(TTS_URL, name="tts").respond(
            content=CLONE_WAV.read_bytes(), headers={"content-type": "audio/wav"}
        )
        router.post(NVIDIA_URL, name="nvidia").respond(json=NVIDIA_OK)
        yield router


@pytest.fixture
def client(api):
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def fake_llm(monkeypatch):
    """Deterministic llm.py so contract tests don't depend on the Lead's parsing."""
    from app import llm

    calls: dict[str, list] = {"suggest": [], "compose": [], "variety": []}

    async def suggest_replies(transcript, history=None, n=3, *, variety=False):
        calls["suggest"].append((transcript, history))
        calls["variety"].append(variety)
        return [
            {"text": "हाँ बेटा, दाल चावल बढ़िया रहेगा।", "intent": "yes"},
            {"text": "नहीं, आज खिचड़ी बना लो।", "intent": "no"},
            {"text": "तुम क्या खाना चाहती हो?", "intent": "ask"},
        ][:n]

    async def to_devanagari(text):
        calls["compose"].append(text)
        return "थोड़ा पानी दे दो"

    monkeypatch.setattr(llm, "suggest_replies", suggest_replies)
    monkeypatch.setattr(llm, "to_devanagari", to_devanagari)
    return calls


def _ffmpeg(*args: str) -> None:
    subprocess.run([settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture(scope="session")
def audio_files(tmp_path_factory) -> dict[str, Path]:
    """Test audio derived from fixtures/ with ffmpeg (webm proves browser-format conversion)."""
    d = tmp_path_factory.mktemp("audio")
    src = str(VOICE_WAV)
    out = {"wav": VOICE_WAV}
    _ffmpeg("-i", src, "-c:a", "libopus", "-b:a", "32k", str(d / "voice.webm")); out["webm"] = d / "voice.webm"
    _ffmpeg("-i", src, "-c:a", "aac", "-b:a", "64k", str(d / "voice.m4a")); out["m4a"] = d / "voice.m4a"
    _ffmpeg("-i", src, "-c:a", "aac", "-b:a", "64k", "-f", "mp4", str(d / "voice.mp4")); out["mp4"] = d / "voice.mp4"
    _ffmpeg("-i", src, "-c:a", "libopus", str(d / "voice.ogg")); out["ogg"] = d / "voice.ogg"
    _ffmpeg("-i", src, "-t", "2", str(d / "short.wav")); out["short"] = d / "short.wav"
    _ffmpeg("-i", src, "-t", "0.2", str(d / "tiny.wav")); out["tiny"] = d / "tiny.wav"
    _ffmpeg("-stream_loop", "4", "-i", src, "-t", "40", str(d / "long.wav")); out["long"] = d / "long.wav"
    _ffmpeg("-stream_loop", "3", "-i", src, "-t", "30.5", str(d / "slightly_long.wav"))
    out["slightly_long"] = d / "slightly_long.wav"
    (d / "garbage.webm").write_bytes(os.urandom(4096)); out["garbage"] = d / "garbage.webm"
    return out


MIME = {".wav": "audio/wav", ".webm": "audio/webm", ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".ogg": "audio/ogg"}


def upload(path: Path) -> dict:
    return {"audio": (path.name, path.read_bytes(), MIME.get(path.suffix, "application/octet-stream"))}


def multipart_wav(request: httpx.Request) -> bytes:
    """Extract the WAV bytes sent to Gnani from a captured multipart request."""
    body = request.content
    i = body.find(b"RIFF")
    assert i >= 0, "no WAV in request"
    return body[i:]


def form_field(request: httpx.Request, name: str) -> str | None:
    body = request.content
    marker = f'name="{name}"\r\n\r\n'.encode()
    i = body.find(marker)
    if i < 0:
        return None
    j = body.find(b"\r\n--", i + len(marker))
    return body[i + len(marker):j].decode()
