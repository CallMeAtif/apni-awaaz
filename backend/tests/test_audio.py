"""audio.py and text-splitting units (no network)."""
from __future__ import annotations

import struct

import pytest

from app import audio, gnani
from tests.conftest import CLONE_WAV, VOICE_WAV, streaming_header


def test_normalize_streaming_header():
    raw = streaming_header(CLONE_WAV.read_bytes())
    fixed = audio.normalize_wav_bytes(raw)
    assert fixed[:4] == b"RIFF" and fixed[8:12] == b"WAVE"
    assert struct.unpack_from("<I", fixed, 4)[0] == len(fixed) - 8
    assert fixed[36:40] == b"data"
    assert struct.unpack_from("<I", fixed, 40)[0] == len(fixed) - 44
    assert audio.wav_duration(fixed) == pytest.approx(3.22, abs=0.01)


def test_normalize_skips_list_chunk():
    # the Deepak fixture has a LIST/INFO chunk before data
    fixed = audio.normalize_wav_bytes(VOICE_WAV.read_bytes())
    assert fixed[36:40] == b"data"
    assert audio.wav_duration(fixed) == pytest.approx(9.44, abs=0.01)


def test_parse_rejects_non_wav():
    with pytest.raises(ValueError):
        audio.parse_wav(b"not a wav at all")


def test_concat_adds_durations_and_gap():
    a = CLONE_WAV.read_bytes()
    out = audio.concat_wavs([a, streaming_header(a)], gap_ms=100)
    assert audio.wav_duration(out) == pytest.approx(2 * 3.2238 + 0.1, abs=0.01)


def test_concat_rejects_mixed_formats():
    w = audio.parse_wav(CLONE_WAV.read_bytes())
    other = audio.build_wav(audio.Wav(1, 16000, 2, w.pcm[:3200]))
    with pytest.raises(ValueError):
        audio.concat_wavs([CLONE_WAV.read_bytes(), other])


async def test_convert_webm_to_16k_wav(audio_files, tmp_path):
    out = await audio.convert_to_wav(audio_files["webm"], tmp_path / "o.wav", 16000)
    w = audio.parse_wav(out.read_bytes())
    assert (w.channels, w.sample_rate, w.sample_width) == (1, 16000, 2)
    assert w.duration == pytest.approx(9.44, abs=0.1)
    assert out.read_bytes()[36:40] == b"data"  # clean 44-byte header


@pytest.mark.parametrize("kind", ["m4a", "mp4", "ogg", "wav"])
async def test_convert_browser_formats(audio_files, tmp_path, kind):
    out = await audio.convert_to_wav(audio_files[kind], tmp_path / "o.wav", 44100, max_seconds=5)
    w = audio.parse_wav(out.read_bytes())
    assert (w.channels, w.sample_rate) == (1, 44100)
    assert w.duration == pytest.approx(5.0, abs=0.05)


async def test_convert_garbage_is_bad_audio(audio_files, tmp_path):
    with pytest.raises(audio.AudioError) as e:
        await audio.convert_to_wav(audio_files["garbage"], tmp_path / "o.wav", 16000)
    assert e.value.code == "BAD_AUDIO"


async def test_probe_and_trim(audio_files, tmp_path):
    out = await audio.convert_to_wav(audio_files["long"], tmp_path / "o.wav", 16000)
    assert await audio.probe_duration(out) == pytest.approx(40.0, abs=0.05)
    assert await audio.probe_duration(audio_files["webm"]) == pytest.approx(9.44, abs=0.1)  # via ffprobe
    assert audio.trim_wav_file(out, 30.0) == pytest.approx(30.0, abs=0.001)
    assert await audio.probe_duration(out) == pytest.approx(30.0, abs=0.001)


# ----------------------------------------------------------------- text splitting


def test_split_short_text_is_one_chunk():
    assert gnani.split_text("  हाँ   बेटा।  ") == ["हाँ बेटा।"]
    assert gnani.split_text("") == []


def test_split_long_text_on_sentence_boundaries():
    s1 = "आज मौसम बहुत अच्छा है और मैं बाहर टहलने जाना चाहता हूँ।"
    s2 = "क्या तुम मेरे साथ चलोगी?"
    s3 = "Doctor ne kaha tha roz thoda chalna zaroori hai!"
    text = " ".join([s1, s2, s3] * 3)
    chunks = gnani.split_text(text, limit=180)
    assert len(chunks) > 1
    assert all(len(c) <= 180 for c in chunks)
    assert " ".join(chunks) == text  # nothing lost or reordered
    assert all(c.endswith(("।", "?", "!", ".")) for c in chunks)


def test_split_sentence_without_punctuation_falls_back_to_words():
    text = " ".join(["शब्द"] * 80)  # 399 chars, no sentence end
    chunks = gnani.split_text(text, limit=180)
    assert all(len(c) <= 180 for c in chunks)
    assert " ".join(chunks) == text
