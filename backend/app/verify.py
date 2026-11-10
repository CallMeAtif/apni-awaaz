"""Self-check for voice-cloned speech.

vachana-vc-v1 sometimes adds or repeats words on very short phrases. Measured live on 10 Nov:
"चश्मा दे दो" came back as "चश्मा दे दो, चश्मा दे दो", "पानी चाहिए।" as "पानी चाहिए, पानी चाहिए", and
"चश्मा दे दो।" as "चश्मा दे दो है जे". Adding a full stop did not help, and the output varies
from run to run. So /api/speak transcribes each new clip with Prisma and regenerates it when
what was spoken doesn't match the text (see main._speak_clone_verified).
"""
from __future__ import annotations

import io
import re
import unicodedata
import wave
from difflib import SequenceMatcher

MIN_RATIO = 0.9  # 0.82 let 'पानी चाहिए' heard as 'आनी चाहिए' through (live, 10 Nov)
_STRIP = re.compile(r"[\s।॥.,!?;:'\"\-–—…()\[\]]+")


def _norm(s: str) -> str:
    # Decompose so precomposed nukta letters (e.g. क़ U+0958) lose their nukta too; STT and the
    # LLM disagree on nukta and chandrabindu often enough that they shouldn't count as errors.
    s = unicodedata.normalize("NFD", s or "").lower()
    s = s.replace("़", "").replace("ँ", "ं")
    return _STRIP.sub("", unicodedata.normalize("NFC", s))


def speech_matches(expected: str, heard: str) -> tuple[bool, float]:
    """Return (ok, score). The check fails when the clip says noticeably more than the text, which
    is how repeats and tacked-on syllables show up, or when the words differ too much."""
    e, h = _norm(expected), _norm(heard)
    if not e:
        return True, 1.0
    if not h:
        return False, 0.0
    ratio = SequenceMatcher(None, e, h).ratio()
    too_long = len(h) > len(e) + max(1, round(0.1 * len(e)))  # one extra word like 'और' fails a short phrase
    score = ratio - (0.5 if too_long else 0.0)
    return (ratio >= MIN_RATIO and not too_long), score



def speech_complete(expected: str, heard: str) -> bool:
    """Check used after cutting a tail: the danger is cutting off real words, which makes the
    transcript shorter. So the length must hold up, while small mishearings are tolerated (after
    a cut, Prisma once heard 'दवाई का समय हो गया' as 'तबाही का समय हो गया')."""
    e, h = _norm(expected), _norm(heard)
    if not e:
        return True
    if not h:
        return False
    tol = max(1, round(0.1 * len(e)))
    return len(e) - tol <= len(h) <= len(e) + tol and SequenceMatcher(None, e, h).ratio() >= 0.7  # length is the real guard

def pad_for_stt(wav: bytes, lead_s: float = 0.25) -> bytes:
    """Prepend silence before sending a clip to Prisma for the self-check (not for playback), so a
    clip that starts abruptly doesn't lose its first consonant in the transcript."""
    try:
        with wave.open(io.BytesIO(wav)) as r:
            params, frames = r.getparams(), r.readframes(r.getnframes())
        silence = b"\x00" * (int(params.framerate * lead_s) * params.sampwidth * params.nchannels)
        out = io.BytesIO()
        with wave.open(out, "wb") as w:
            w.setparams(params)
            w.writeframes(silence + frames)
        return out.getvalue()
    except Exception:  # not a plain PCM WAV: check it as-is
        return wav


# --------------------------------------------------------------------------- tail trimming
# vachana-vc-v1 often finishes the words, goes fully silent, then adds a flat 0.5-1.2 s vowel
# ("aaahhh"). Prisma ignores non-word sounds, so the word check above can't see it. We cut at
# that silent gap and keep the cut only if Prisma still hears every word (main._trim_clone_tail).

_FRAME_S = 0.02
QUIET_DB = -45.0      # a "silent gap" frame
SOUND_DB = -35.0      # a frame with real sound in it
MIN_GAP_S = 0.10
MIN_TAIL_S = 0.15
CHARS_PER_S = 14.0    # measured speaking rate of the cloned voice, used to skip early (comma) pauses


def _read_pcm16(wav: bytes):
    import array
    with wave.open(io.BytesIO(wav)) as r:
        params = r.getparams()
        if params.sampwidth != 2 or params.nchannels != 1:
            return None, params
        samples = array.array("h", r.readframes(r.getnframes()))
    return samples, params


def _frames_db(samples, rate: int) -> list[float]:
    import math
    n = max(1, int(rate * _FRAME_S))
    out = []
    for i in range(0, len(samples), n):
        seg = samples[i:i + n]
        rms = math.sqrt(sum(x * x for x in seg) / max(1, len(seg)))
        out.append(20 * math.log10(rms / 32768 + 1e-9))
    return out


def tail_cut_candidates(wav: bytes, text: str = "") -> list[float]:
    """Times (s) where a silent gap starts and real sound follows it, earliest first. Gaps before
    ~60% of the expected speaking time are skipped, so pauses after a comma aren't offered."""
    try:
        samples, params = _read_pcm16(wav)
    except Exception:
        return []
    if samples is None:
        return []
    db = _frames_db(samples, params.framerate)
    min_gap, min_tail = round(MIN_GAP_S / _FRAME_S), round(MIN_TAIL_S / _FRAME_S)
    earliest = max(0.3, 0.6 * len(_norm(text)) / CHARS_PER_S)
    cands, i = [], 0
    while i < len(db):
        if db[i] < QUIET_DB:
            j = i
            while j < len(db) and db[j] < QUIET_DB:
                j += 1
            start = i * _FRAME_S
            if j - i >= min_gap and start >= earliest and sum(1 for d in db[j:] if d > SOUND_DB) >= min_tail:
                cands.append(round(start, 3))
            i = j
        else:
            i += 1
    return cands


def cut_wav(wav: bytes, t: float, keep_s: float = 0.06, fade_s: float = 0.03, pad_s: float = 0.12) -> bytes:
    """Cut the clip at t (+ a short natural release), fade out to avoid a click, end with silence."""
    samples, params = _read_pcm16(wav)
    if samples is None:
        return wav
    rate = params.framerate
    end = min(len(samples), int((t + keep_s) * rate))
    head = samples[:end]
    fade = min(len(head), int(fade_s * rate))
    for k in range(fade):
        idx = len(head) - fade + k
        head[idx] = int(head[idx] * (1 - (k + 1) / fade))
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setparams(params)
        w.writeframes(head.tobytes() + b"\x00" * (int(pad_s * rate) * 2))
    return out.getvalue()
