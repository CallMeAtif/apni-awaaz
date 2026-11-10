from __future__ import annotations

import httpx
import pytest

from app import verify
from app.config import settings
from tests.conftest import CLONE_WAV
from tests.test_api import enroll


# The three failures measured live on 10 Nov must be rejected.
@pytest.mark.parametrize("expected, heard", [
    ("चश्मा दे दो", "चश्मा दे दो, चश्मा दे दो।"),
    ("पानी चाहिए।", "पानी चाहिए, पानी चाहिए।"),
    ("चश्मा दे दो।", "चश्मा दे दो है जे।"),
    ("दर्द हो रहा है", "कुछ और ही बोला"),
    ("पानी चाहिए", ""),
])
def test_rejects_repeats_additions_and_wrong_words(expected, heard):
    ok, _ = verify.speech_matches(expected, heard)
    assert not ok


# Clean clips, with the punctuation/nukta/chandrabindu differences STT and the LLM disagree on.
@pytest.mark.parametrize("expected, heard", [
    ("पानी चाहिए", "पानी चाहिए।"),
    ("भूख लगी है।", "भूख लगी है"),
    ("हाँ बेटा, दाल चावल बढ़िया रहेगा।", "हां बेटा दाल चावल बढिया रहेगा।"),
    ("थोड़ा पानी दे दो।", "थोडा पानी दे दो"),
    ("आज मुझे थोड़ा आराम चाहिए। क्या तुम मेरे लिए एक कप चाय बना दोगे?",
     "आज मुझे थोड़ा आराम चाहिए, क्या तुम मेरे लिए एक कप चाय बना दोगे?"),
])
def test_accepts_clean_clips(expected, heard):
    ok, score = verify.speech_matches(expected, heard)
    assert ok and score >= verify.MIN_RATIO


def _stt(text):
    return httpx.Response(200, json={"success": True, "transcript": text, "model": "prisma-v3.0"})


def test_speak_regenerates_until_clip_matches_then_caches(client, api, monkeypatch):
    monkeypatch.setattr(settings, "verify_clone_tts", True)
    monkeypatch.setattr(settings, "trim_clone_tail", False)
    vid = enroll(client, CLONE_WAV.parent / "synthetic_voice_deepak_9s.wav").json()["voice_id"]
    api.routes["stt"].side_effect = [_stt("चश्मा दे दो, चश्मा दे दो।"), _stt("चश्मा दे दो।")]

    r = client.post("/api/speak", json={"voice_id": vid, "text": "चश्मा दे दो"})
    assert r.status_code == 200 and r.headers["x-cache"] == "MISS"
    assert api.routes["tts"].call_count == 2
    sent = api.routes["stt"].calls.last.request.content
    assert b"verbatim" in sent

    assert client.post("/api/speak", json={"voice_id": vid, "text": "चश्मा दे दो"}).headers["x-cache"] == "HIT"
    assert api.routes["tts"].call_count == 2


def test_speak_plays_best_attempt_but_does_not_cache_when_all_fail(client, api, monkeypatch):
    monkeypatch.setattr(settings, "verify_clone_tts", True)
    monkeypatch.setattr(settings, "trim_clone_tail", False)
    vid = enroll(client, CLONE_WAV.parent / "synthetic_voice_deepak_9s.wav").json()["voice_id"]
    api.routes["stt"].side_effect = lambda request: _stt("पानी चाहिए, पानी चाहिए।")

    r = client.post("/api/speak", json={"voice_id": vid, "text": "पानी चाहिए"})
    assert r.status_code == 200 and r.content[:4] == b"RIFF"
    assert api.routes["tts"].call_count == settings.verify_max_attempts
    assert client.post("/api/speak", json={"voice_id": vid, "text": "पानी चाहिए"}).headers["x-cache"] == "MISS"


def test_speak_still_plays_if_self_check_stt_fails(client, api, monkeypatch):
    monkeypatch.setattr(settings, "verify_clone_tts", True)
    monkeypatch.setattr(settings, "trim_clone_tail", False)
    vid = enroll(client, CLONE_WAV.parent / "synthetic_voice_deepak_9s.wav").json()["voice_id"]
    api.routes["stt"].side_effect = lambda request: httpx.Response(500, json={"detail": "boom"})

    r = client.post("/api/speak", json={"voice_id": vid, "text": "दर्द हो रहा है"})
    assert r.status_code == 200 and api.routes["tts"].call_count == 1


def test_rejects_lost_first_consonant():
    assert not verify.speech_matches("पानी चाहिए", "आनी चाहिए।")[0]


def test_pad_for_stt_adds_leading_silence():
    import io, wave
    raw = CLONE_WAV.read_bytes()
    padded = verify.pad_for_stt(raw, lead_s=0.25)
    with wave.open(io.BytesIO(raw)) as a, wave.open(io.BytesIO(padded)) as b:
        assert b.getnframes() == a.getnframes() + int(a.getframerate() * 0.25)
    assert verify.pad_for_stt(b"not a wav") == b"not a wav"


def test_rejects_one_extra_word_on_short_phrase():
    assert not verify.speech_matches("चश्मा दे दो", "और चश्मा दे दो।")[0]


# ----------------------------------------------------------------- tail trimming


def _tone_wav(parts, rate=44100):
    """parts: [(seconds, amplitude)], a 220 Hz tone per part (amplitude 0 = digital silence)."""
    import io, math, wave, array
    a = array.array("h")
    for secs, amp in parts:
        for i in range(int(secs * rate)):
            a.append(int(amp * math.sin(2 * math.pi * 220 * i / rate)))
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(a.tobytes())
    return out.getvalue()


WORDS_GAP_AAH = _tone_wav([(0.8, 9000), (0.2, 0), (0.8, 9000)])


def test_finds_gap_before_trailing_sound():
    cands = verify.tail_cut_candidates(WORDS_GAP_AAH, "पानी चाहिए")
    assert len(cands) == 1 and abs(cands[0] - 0.8) < 0.03


def test_no_candidate_without_gap_or_for_early_comma_pause():
    assert verify.tail_cut_candidates(_tone_wav([(1.5, 9000)]), "पानी चाहिए") == []
    comma = _tone_wav([(0.35, 9000), (0.15, 0), (1.6, 9000)])
    assert verify.tail_cut_candidates(comma, "हाँ बेटा, दाल चावल बढ़िया रहेगा।") == []


def test_cut_wav_length_and_silent_end():
    import io, wave
    cut = verify.cut_wav(WORDS_GAP_AAH, 0.8)
    with wave.open(io.BytesIO(cut)) as w:
        dur = w.getnframes() / w.getframerate()
        frames = w.readframes(w.getnframes())
    assert abs(dur - (0.8 + 0.06 + 0.12)) < 0.01
    assert set(frames[-200:]) == {0}


def test_speak_trims_tail_when_words_survive(client, api, monkeypatch):
    monkeypatch.setattr(settings, "verify_clone_tts", True)
    vid = enroll(client, CLONE_WAV.parent / "synthetic_voice_deepak_9s.wav").json()["voice_id"]
    api.routes["tts"].side_effect = lambda request: httpx.Response(200, content=WORDS_GAP_AAH, headers={"content-type": "audio/wav"})
    api.routes["stt"].side_effect = lambda request: _stt("पानी चाहिए।")
    r = client.post("/api/speak", json={"voice_id": vid, "text": "पानी चाहिए"})
    assert r.status_code == 200 and len(r.content) < len(WORDS_GAP_AAH) * 0.6
    assert api.routes["stt"].call_count == 2


def test_speak_keeps_clip_when_cut_loses_words(client, api, monkeypatch):
    monkeypatch.setattr(settings, "verify_clone_tts", True)
    vid = enroll(client, CLONE_WAV.parent / "synthetic_voice_deepak_9s.wav").json()["voice_id"]
    api.routes["tts"].side_effect = lambda request: httpx.Response(200, content=WORDS_GAP_AAH, headers={"content-type": "audio/wav"})
    api.routes["stt"].side_effect = [_stt("पानी चाहिए।"), _stt("पानी")]
    r = client.post("/api/speak", json={"voice_id": vid, "text": "पानी चाहिए"})
    assert r.status_code == 200 and abs(len(r.content) - len(WORDS_GAP_AAH)) < 200


def test_speech_complete_tolerates_mishearing_but_not_missing_words():
    assert verify.speech_complete("दवाई का समय हो गया", "तबाही का समय हो गया।")
    assert not verify.speech_complete("मुझे उठकर बैठना है", "मुझे उठकर।")
    assert not verify.speech_complete("चश्मा दे दो", "रहा।")
