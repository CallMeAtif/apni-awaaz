"""gnani.py client: headers, retries, error mapping, usage log. All HTTP mocked by respx."""
from __future__ import annotations

import json

import httpx
import pytest

from app import gnani
from app.config import ConfigError, Settings, settings
from tests.conftest import CLONE_WAV, EMBEDDING, STT_OK, VOICE_WAV


async def test_headers_user_agent_and_key(api):
    await gnani.transcribe(VOICE_WAV)
    req = api.routes["stt"].calls.last.request
    assert req.headers["user-agent"] == "ApniAwaaz/0.1"  # Cloudflare 1010 otherwise
    assert req.headers["x-api-key-id"] == "test-gnani-key"


async def test_transcribe_fields(api):
    text = await gnani.transcribe(VOICE_WAV, keyterms=["Papa", "प्रिया"], previous_text="चाय?")
    assert text == STT_OK["transcript"]
    body = api.routes["stt"].calls.last.request.content
    assert b'name="keyterms"\r\n\r\n["Papa", "\xe0\xa4\xaa' in body  # JSON array, unicode kept
    assert 'name="previous_text"\r\n\r\nचाय?'.encode() in body


async def test_create_embedding_returns_whole_object(api):
    assert await gnani.create_embedding(VOICE_WAV) == EMBEDDING


async def test_embedding_missing_is_upstream_error(api):
    api.routes["embed"].respond(json={"success": True, "data": {}})
    with pytest.raises(gnani.GnaniError) as e:
        await gnani.create_embedding(VOICE_WAV)
    assert e.value.code == "UPSTREAM_ERROR"


async def test_402_maps_to_out_of_credits(api):
    api.routes["tts"].respond(402, json={"message": "no credits"})
    with pytest.raises(gnani.GnaniError) as e:
        await gnani.speak_stock("पानी चाहिए")
    assert (e.value.code, e.value.status) == ("OUT_OF_CREDITS", 402)
    assert api.routes["tts"].call_count == 1  # no retry on 402


async def test_429_retries_twice_then_succeeds(api):
    api.routes["tts"].side_effect = [httpx.Response(429), httpx.Response(429),
                                     httpx.Response(200, content=CLONE_WAV.read_bytes())]
    wav = await gnani.speak_clone("हाँ बेटा", EMBEDDING)
    assert wav[:4] == b"RIFF"
    assert api.routes["tts"].call_count == 3


async def test_429_backoff_uses_retry_after(api, monkeypatch):
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(gnani.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(gnani, "RETRY_BACKOFF_S", (0.6, 1.5))
    api.routes["stt"].side_effect = [httpx.Response(429, headers={"retry-after": "2"}), httpx.Response(429),
                                     httpx.Response(200, json=STT_OK)]
    await gnani.transcribe(VOICE_WAV)
    assert slept == [2.0, 1.5]


async def test_401_and_timeout(api):
    api.routes["stt"].respond(401, json={"error": "INVALID_API_KEY"})
    with pytest.raises(gnani.GnaniError) as e:
        await gnani.transcribe(VOICE_WAV)
    assert e.value.code == "UPSTREAM_ERROR" and "key" in e.value.message
    api.routes["stt"].side_effect = httpx.ReadTimeout("slow")
    with pytest.raises(gnani.GnaniError) as e:
        await gnani.transcribe(VOICE_WAV)
    assert (e.value.code, e.value.status) == ("UPSTREAM_ERROR", 504)


async def test_usage_log_every_attempt(api, isolated_data):
    api.routes["stt"].side_effect = [httpx.Response(429), httpx.Response(200, json=STT_OK)]
    await gnani.transcribe(VOICE_WAV)
    await gnani.speak_stock("पानी चाहिए")
    rows = [json.loads(line) for line in (isolated_data / "usage.jsonl").read_text().splitlines()]
    assert [r["status"] for r in rows] == [429, 200, 200]
    assert rows[0]["endpoint"] == "/stt/v3" and rows[0]["audio_seconds"] == pytest.approx(9.44, abs=0.01)
    assert rows[1]["attempt"] == 2
    assert rows[2]["endpoint"] == "/api/v1/tts/inference" and rows[2]["chars"] == len("पानी चाहिए")
    assert all(isinstance(r["ms"], int) and "ts" in r for r in rows)
    assert all("test-gnani-key" not in json.dumps(r) for r in rows)


def test_missing_key_fails_clearly():
    s = Settings(gnani_api_key=None, nvidia_api_key=None)
    with pytest.raises(ConfigError, match="GNANI_API_KEY"):
        s.check_startup()
    assert s.missing_keys() == ["GNANI_API_KEY", "NVIDIA_API_KEY"]


async def test_missing_key_at_call_time(api, monkeypatch):
    monkeypatch.setattr(settings, "gnani_api_key", None)
    await gnani.aclose()
    with pytest.raises(gnani.GnaniError) as e:
        await gnani.transcribe(VOICE_WAV)
    assert "GNANI_API_KEY" in e.value.message
    assert api.routes["stt"].call_count == 0
