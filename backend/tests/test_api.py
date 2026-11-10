"""HTTP contract tests (docs/TEAM_BRIEF.md section 5). Gnani + NVIDIA are mocked by respx."""
from __future__ import annotations

import json
import struct

import httpx
import pytest

from app import audio, store
from app.config import settings
from tests.conftest import (
    CLONE_WAV, EMBEDDING, FAMILY_TRANSCRIPT, GNANI, STT_OK, form_field, multipart_wav, streaming_header, upload,
)

ERROR_CODES = {"AUDIO_TOO_SHORT", "AUDIO_TOO_LONG", "BAD_AUDIO", "OUT_OF_CREDITS", "UPSTREAM_ERROR",
               "NOT_FOUND", "BAD_REQUEST"}


def assert_error(resp: httpx.Response, status: int, code: str):
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code and code in ERROR_CODES
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    assert resp.headers["cache-control"] == "no-store"


def enroll(client, path, **form):
    data = {"name": "Ramesh (synthetic)", "consent": "true", **form}
    return client.post("/api/voices", files=upload(path), data=data)


# ----------------------------------------------------------------- simple GETs


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "gnani_key": True, "llm_key": True}
    assert r.headers["cache-control"] == "no-store"


def test_enroll_passage(client):
    r = client.get("/api/enroll/passage")
    assert r.status_code == 200
    assert set(r.json()) == {"text"}
    assert len(r.json()["text"]) > 50


def test_phrases_shape(client):
    r = client.get("/api/phrases")
    assert r.status_code == 200
    cats = r.json()
    assert isinstance(cats, list) and cats
    for c in cats:
        assert {"category", "category_en", "phrases"} <= set(c)
        for p in c["phrases"]:
            assert {"text", "en", "emoji"} <= set(p)


def test_phrases_fallback_when_lead_module_breaks(client, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main, "_lead_attr", lambda m, a: None)
    assert client.get("/api/phrases").json() == main._FALLBACK_PHRASES
    assert client.get("/api/enroll/passage").json() == {"text": main._FALLBACK_PASSAGE}


def test_unknown_route_uses_error_shape(client):
    assert_error(client.get("/api/nope"), 404, "NOT_FOUND")


def test_cors_preflight_for_vite(client):
    r = client.options("/api/speak", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
    r = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert "X-Cache" in r.headers["access-control-expose-headers"]


# ----------------------------------------------------------------- voices


def test_enroll_list_delete(client, api, isolated_data):
    r = enroll(client, CLONE_WAV.parent / "synthetic_voice_deepak_9s.wav")
    assert r.status_code == 201, r.text
    v = r.json()
    assert set(v) == {"voice_id", "name", "duration_s", "created_at"}
    assert v["name"] == "Ramesh (synthetic)"
    assert v["duration_s"] == pytest.approx(9.44, abs=0.01)

    sent = multipart_wav(api.routes["embed"].calls.last.request)
    w = audio.parse_wav(sent)
    assert (w.channels, w.sample_rate, w.sample_width) == (1, 44100, 2)

    rec = json.loads((isolated_data / "voices" / f"{v['voice_id']}.json").read_text())
    assert rec["embedding"] == EMBEDDING and rec["name"] == v["name"]
    assert (isolated_data / "voices" / f"{v['voice_id']}.wav").exists()

    r = client.get("/api/voices")
    assert r.status_code == 200
    assert r.json() == [{"voice_id": v["voice_id"], "name": v["name"], "created_at": v["created_at"]}]

    r = client.delete(f"/api/voices/{v['voice_id']}")
    assert r.status_code == 204 and r.content == b""
    assert not (isolated_data / "voices" / f"{v['voice_id']}.json").exists()
    assert not (isolated_data / "voices" / f"{v['voice_id']}.wav").exists()
    assert client.get("/api/voices").json() == []
    assert_error(client.delete(f"/api/voices/{v['voice_id']}"), 404, "NOT_FOUND")


def test_enroll_webm(client, api, audio_files):
    r = enroll(client, audio_files["webm"])
    assert r.status_code == 201, r.text
    assert r.json()["duration_s"] == pytest.approx(9.44, abs=0.1)
    assert multipart_wav(api.routes["embed"].calls.last.request)[:4] == b"RIFF"


@pytest.mark.parametrize("consent", [None, "", "false", "no"])
def test_enroll_requires_consent(client, api, audio_files, consent):
    data = {"name": "x"} if consent is None else {"name": "x", "consent": consent}
    r = client.post("/api/voices", files=upload(audio_files["wav"]), data=data)
    assert_error(r, 400, "BAD_REQUEST")
    assert "onsent" in r.json()["error"]["message"]
    assert api.routes["embed"].call_count == 0


def test_enroll_too_short(client, api, audio_files):
    assert_error(enroll(client, audio_files["short"]), 400, "AUDIO_TOO_SHORT")
    assert api.routes["embed"].call_count == 0


def test_enroll_too_long_is_trimmed_to_30s(client, api, audio_files):
    r = enroll(client, audio_files["long"])  # 40 s
    assert r.status_code == 201, r.text
    assert r.json()["duration_s"] == pytest.approx(30.0, abs=0.01)
    assert audio.wav_duration(multipart_wav(api.routes["embed"].calls.last.request)) <= 30.0


def test_enroll_missing_audio(client, api):
    assert_error(client.post("/api/voices", data={"name": "x", "consent": "true"}), 400, "BAD_REQUEST")


def test_enroll_bad_audio(client, api, audio_files):
    assert_error(enroll(client, audio_files["garbage"]), 400, "BAD_AUDIO")
    assert api.routes["embed"].call_count == 0


def test_enroll_out_of_credits(client, api, audio_files):
    api.routes["embed"].respond(402, json={"success": False, "message": "insufficient credits"})
    assert_error(enroll(client, audio_files["wav"]), 402, "OUT_OF_CREDITS")
    assert client.get("/api/voices").json() == []


# ----------------------------------------------------------------- listen


def test_listen_webm_contract(client, api, fake_llm, audio_files, monkeypatch):
    monkeypatch.setattr(settings, "stt_keyterms", ["Priya", "Sunita"])
    r = client.post("/api/listen", files=upload(audio_files["webm"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"transcript", "suggestions", "timings"}
    assert body["transcript"] == FAMILY_TRANSCRIPT
    assert len(body["suggestions"]) == 3
    assert all(set(s) == {"text", "intent"} for s in body["suggestions"])
    assert set(body["timings"]) == {"stt_ms", "llm_ms"}
    assert all(isinstance(v, int) and v >= 0 for v in body["timings"].values())
    assert r.headers["cache-control"] == "no-store"

    req = api.routes["stt"].calls.last.request
    assert form_field(req, "model") == "prisma-v3.0"
    assert form_field(req, "language_code") == "hi-IN"
    assert form_field(req, "format") == "codemix"
    assert json.loads(form_field(req, "keyterms")) == ["Priya", "Sunita"]
    assert b'filename="audio.wav"' in req.content
    w = audio.parse_wav(multipart_wav(req))
    assert (w.channels, w.sample_rate, w.sample_width) == (1, 16000, 2)
    assert fake_llm["suggest"] == [(FAMILY_TRANSCRIPT, [])]


def test_listen_passes_history(client, api, fake_llm, audio_files):
    history = [{"role": "family", "text": "Papa, chai piyoge?"}, {"role": "user", "text": "हाँ"},
               {"role": "robot", "text": "dropped"}, "junk"]
    r = client.post("/api/listen", files=upload(audio_files["wav"]),
                    data={"history": json.dumps(history, ensure_ascii=False)})
    assert r.status_code == 200, r.text
    assert fake_llm["suggest"][0][1] == history[:2]
    req = api.routes["stt"].calls.last.request
    assert form_field(req, "previous_text") == "हाँ"
    assert form_field(req, "keyterms") is None  # off by default


def test_listen_bad_history_json(client, api, fake_llm, audio_files):
    r = client.post("/api/listen", files=upload(audio_files["wav"]), data={"history": "{not json"})
    assert_error(r, 400, "BAD_REQUEST")
    assert api.routes["stt"].call_count == 0


def test_listen_empty_transcript_skips_llm(client, api, fake_llm, audio_files):
    api.routes["stt"].respond(json={**STT_OK, "transcript": "  "})
    r = client.post("/api/listen", files=upload(audio_files["wav"]))
    assert r.status_code == 200
    assert r.json()["transcript"] == "" and r.json()["suggestions"] == []
    assert r.json()["timings"]["llm_ms"] == 0
    assert fake_llm["suggest"] == []


@pytest.mark.parametrize("kind", ["m4a", "mp4", "ogg"])
def test_listen_other_browser_formats(client, api, fake_llm, audio_files, kind):
    r = client.post("/api/listen", files=upload(audio_files[kind]))
    assert r.status_code == 200, r.text
    assert audio.parse_wav(multipart_wav(api.routes["stt"].calls.last.request)).sample_rate == 16000


def test_listen_too_short(client, api, audio_files):
    assert_error(client.post("/api/listen", files=upload(audio_files["tiny"])), 400, "AUDIO_TOO_SHORT")
    assert api.routes["stt"].call_count == 0


def test_listen_too_long(client, api, audio_files):
    assert_error(client.post("/api/listen", files=upload(audio_files["long"])), 413, "AUDIO_TOO_LONG")
    assert api.routes["stt"].call_count == 0


def test_listen_slightly_long_is_trimmed(client, api, fake_llm, audio_files):
    r = client.post("/api/listen", files=upload(audio_files["slightly_long"]))  # 30.5 s
    assert r.status_code == 200, r.text
    assert audio.wav_duration(multipart_wav(api.routes["stt"].calls.last.request)) == pytest.approx(30.0, abs=0.01)


def test_listen_bad_audio(client, api, audio_files):
    assert_error(client.post("/api/listen", files=upload(audio_files["garbage"])), 400, "BAD_AUDIO")
    empty = {"audio": ("rec.webm", b"", "audio/webm")}
    assert_error(client.post("/api/listen", files=empty), 400, "BAD_AUDIO")


def test_listen_missing_audio(client, api):
    assert_error(client.post("/api/listen", data={"history": "[]"}), 400, "BAD_REQUEST")


def test_listen_out_of_credits(client, api, fake_llm, audio_files):
    api.routes["stt"].respond(402, json={"success": False, "message": "Insufficient credits"})
    assert_error(client.post("/api/listen", files=upload(audio_files["wav"])), 402, "OUT_OF_CREDITS")
    assert fake_llm["suggest"] == []


def test_listen_429_then_ok(client, api, fake_llm, audio_files):
    api.routes["stt"].side_effect = [httpx.Response(429, json={"error": "RATE_LIMITED"}), httpx.Response(200, json=STT_OK)]
    r = client.post("/api/listen", files=upload(audio_files["wav"]))
    assert r.status_code == 200, r.text
    assert api.routes["stt"].call_count == 2


def test_listen_upstream_500(client, api, fake_llm, audio_files):
    api.routes["stt"].respond(500, json={"success": False, "message": "internal"})
    assert_error(client.post("/api/listen", files=upload(audio_files["wav"])), 502, "UPSTREAM_ERROR")


def test_listen_with_real_llm_module_and_mocked_nvidia(client, api, audio_files):
    """Whatever llm.py does (stub or real Nemotron call), the contract shape holds and nothing goes live."""
    r = client.post("/api/listen", files=upload(audio_files["wav"]))
    assert r.status_code == 200, r.text
    sugg = r.json()["suggestions"]
    assert isinstance(sugg, list) and sugg
    assert all(isinstance(s["text"], str) and s["text"] and isinstance(s["intent"], str) for s in sugg)


def test_listen_survives_llm_exception(client, api, audio_files, monkeypatch):
    from app import llm

    async def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(llm, "suggest_replies", boom)
    r = client.post("/api/listen", files=upload(audio_files["wav"]))
    assert r.status_code == 200 and r.json()["suggestions"] == []


# ----------------------------------------------------------------- suggest / compose


def test_suggest(client, api, fake_llm):
    r = client.post("/api/suggest", json={"transcript": FAMILY_TRANSCRIPT,
                                          "history": [{"role": "family", "text": "Papa?"}]})
    assert r.status_code == 200
    assert set(r.json()) == {"suggestions"} and len(r.json()["suggestions"]) == 3
    assert fake_llm["suggest"] == [(FAMILY_TRANSCRIPT, [{"role": "family", "text": "Papa?"}])]


def test_suggest_empty_transcript(client, api, fake_llm):
    assert client.post("/api/suggest", json={"transcript": " "}).json() == {"suggestions": []}
    assert fake_llm["suggest"] == []


def test_suggest_bad_body(client, api):
    assert_error(client.post("/api/suggest", json={"history": []}), 400, "BAD_REQUEST")


def test_compose(client, api, fake_llm):
    r = client.post("/api/compose", json={"text": "thoda paani de do"})
    assert r.status_code == 200
    assert r.json() == {"text": "थोड़ा पानी दे दो"}
    assert r.headers["cache-control"] == "no-store"


def test_compose_errors(client, api, fake_llm):
    assert_error(client.post("/api/compose", json={"text": "   "}), 400, "BAD_REQUEST")
    assert_error(client.post("/api/compose", content=b"not json", headers={"content-type": "application/json"}),
                 400, "BAD_REQUEST")
    assert fake_llm["compose"] == []


# ----------------------------------------------------------------- speak


def test_speak_stock_fallback_and_cache(client, api, isolated_data):
    api.routes["tts"].respond(content=streaming_header(CLONE_WAV.read_bytes()), headers={"content-type": "audio/wav"})
    text = "हाँ बेटा, दाल चावल बढ़िया रहेगा।"
    r1 = client.post("/api/speak", json={"voice_id": None, "text": text})
    assert r1.status_code == 200, r1.text
    assert r1.headers["content-type"] == "audio/wav"
    assert r1.headers["x-cache"] == "MISS"
    # streaming header was normalised: real sizes
    assert struct.unpack_from("<I", r1.content, 4)[0] == len(r1.content) - 8
    assert struct.unpack_from("<I", r1.content, 40)[0] == len(r1.content) - 44

    sent = json.loads(api.routes["tts"].calls.last.request.content)
    assert sent["voice"] == "Prakash" and sent["model"] == "timbre-v3.0" and sent["language"] == "hi-IN"
    assert sent["text"] == text
    assert sent["audio_config"]["sample_rate"] == 44100

    r2 = client.post("/api/speak", json={"voice_id": None, "text": f"  {text} "})
    assert r2.status_code == 200 and r2.headers["x-cache"] == "HIT"
    assert r2.content == r1.content
    assert api.routes["tts"].call_count == 1
    key = store.cache_key("timbre-v3.0", "stock-Prakash", text)
    assert (isolated_data / "cache" / "tts" / "stock-Prakash" / f"{key}.wav").exists()


def test_speak_clone_and_delete_purges_cache(client, api, isolated_data):
    vid = enroll(client, CLONE_WAV.parent / "synthetic_voice_deepak_9s.wav").json()["voice_id"]
    r = client.post("/api/speak", json={"voice_id": vid, "text": "दर्द हो रहा है"})
    assert r.status_code == 200 and r.headers["x-cache"] == "MISS"
    sent = json.loads(api.routes["tts"].calls.last.request.content)
    assert sent["model"] == "vachana-vc-v1"
    assert sent["speaker_embedding"] == EMBEDDING
    assert "voice" not in sent and "language" not in sent
    assert client.post("/api/speak", json={"voice_id": vid, "text": "दर्द हो रहा है"}).headers["x-cache"] == "HIT"
    assert api.routes["tts"].call_count == 1
    assert (isolated_data / "cache" / "tts" / vid).is_dir()

    assert client.delete(f"/api/voices/{vid}").status_code == 204
    assert not (isolated_data / "cache" / "tts" / vid).exists()
    assert_error(client.post("/api/speak", json={"voice_id": vid, "text": "दर्द हो रहा है"}), 404, "NOT_FOUND")


def test_speak_long_text_is_split_and_concatenated(client, api):
    sentence = "आज मौसम बहुत अच्छा है और मैं बाहर टहलने जाना चाहता हूँ।"  # 54 chars
    text = " ".join([sentence] * 5)  # 274 chars
    r = client.post("/api/speak", json={"voice_id": None, "text": text})
    assert r.status_code == 200, r.text
    calls = api.routes["tts"].calls
    assert calls.call_count == 2
    sent = [json.loads(c.request.content)["text"] for c in calls]
    assert all(len(t) <= 180 for t in sent) and " ".join(sent) == text
    assert audio.wav_duration(r.content) == pytest.approx(2 * 3.2238 + 0.12, abs=0.02)


def test_speak_validation(client, api):
    assert_error(client.post("/api/speak", json={"voice_id": None, "text": "क" * 301}), 400, "BAD_REQUEST")
    assert_error(client.post("/api/speak", json={"voice_id": None, "text": "  "}), 400, "BAD_REQUEST")
    assert_error(client.post("/api/speak", json={"voice_id": None}), 400, "BAD_REQUEST")
    assert_error(client.post("/api/speak", json={"voice_id": "does-not-exist", "text": "हाँ"}), 404, "NOT_FOUND")
    assert_error(client.post("/api/speak", json={"voice_id": "../../etc/passwd", "text": "हाँ"}), 404, "NOT_FOUND")
    assert api.routes["tts"].call_count == 0


def test_speak_out_of_credits_is_not_cached(client, api):
    api.routes["tts"].respond(402, json={"success": False, "message": "Insufficient credits"})
    assert_error(client.post("/api/speak", json={"voice_id": None, "text": "पानी चाहिए"}), 402, "OUT_OF_CREDITS")
    api.routes["tts"].respond(content=CLONE_WAV.read_bytes(), headers={"content-type": "audio/wav"})
    r = client.post("/api/speak", json={"voice_id": None, "text": "पानी चाहिए"})
    assert r.status_code == 200 and r.headers["x-cache"] == "MISS"


def test_speak_429_retry_then_give_up(client, api):
    api.routes["tts"].side_effect = [httpx.Response(429)] * 3
    assert_error(client.post("/api/speak", json={"voice_id": None, "text": "पानी चाहिए"}), 503, "UPSTREAM_ERROR")
    assert api.routes["tts"].call_count == 3  # 1 try + 2 retries


def test_speak_upstream_json_instead_of_audio(client, api):
    api.routes["tts"].respond(json={"success": False, "message": "text too long"})
    assert_error(client.post("/api/speak", json={"voice_id": None, "text": "पानी चाहिए"}), 502, "UPSTREAM_ERROR")


# ----------------------------------------------------------------- warmup + variety (Phase 2)


def test_warmup_opens_connections_without_billable_calls(client, api, isolated_data):
    gnani_root = api.get(f"{GNANI}/").respond(401, json={"detail": {"error_code": "MISSING_API_KEY"}})
    r = client.post("/api/warmup")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["llm"] is True and body["gnani"] is True and isinstance(body["ms"], int)
    assert gnani_root.call_count == 1
    assert gnani_root.calls[0].request.headers["x-api-key-id"] == ""  # unauthenticated: never billed
    assert not api.routes["stt"].called and not api.routes["tts"].called and not api.routes["embed"].called
    assert not (isolated_data / "usage.jsonl").exists() or (isolated_data / "usage.jsonl").read_text() == ""


def test_warmup_never_fails(client, api):
    api.get(f"{GNANI}/").mock(side_effect=httpx.ConnectError("offline"))
    api.routes["nvidia"].respond(500)
    r = client.post("/api/warmup")
    assert r.status_code == 200 and r.json()["gnani"] is False and r.json()["llm"] is False


def test_listen_is_deterministic_and_suggest_asks_for_variety(client, api, fake_llm, audio_files):
    assert client.post("/api/listen", files=upload(audio_files["wav"])).status_code == 200
    assert client.post("/api/suggest", json={"transcript": FAMILY_TRANSCRIPT}).status_code == 200
    assert fake_llm["variety"] == [False, True]
