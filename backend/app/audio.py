"""Audio plumbing: browser uploads -> WAV mono s16le, durations, trimming, WAV header repair.

ffmpeg runs as an asyncio subprocess so a conversion never blocks the event loop.
Header parsing/rewriting is pure Python (fast, no subprocess) with an ffmpeg fallback
for anything that is not plain 16-bit PCM.
"""
from __future__ import annotations

import asyncio
import logging
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.config import settings

log = logging.getLogger("apni.audio")

FFMPEG_TIMEOUT_S = 30.0
UNKNOWN_SIZE = 0xFFFFFFFF


class AudioError(Exception):
    """A user-facing audio problem. `code` is one of the contract error codes."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------- ffmpeg


async def _run(cmd: list[str], timeout: float = FFMPEG_TIMEOUT_S) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise AudioError("BAD_AUDIO", "Audio processing timed out.")
    if proc.returncode != 0:
        tail = err.decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
        log.warning("%s failed (rc=%s): %s", Path(cmd[0]).name, proc.returncode, tail[0][:300])
        raise AudioError("BAD_AUDIO", "Could not read this audio. Please record again.")
    return out


async def convert_to_wav(src: Path, dst: Path, sample_rate: int, max_seconds: float | None = None) -> Path:
    """Decode any browser upload (webm/opus, mp4/aac, ogg, wav, m4a, mp3...) to WAV mono s16le."""
    cmd = [settings.ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", str(src)]
    if max_seconds is not None:
        cmd += ["-t", f"{max_seconds:.3f}"]
    cmd += [
        "-vn", "-map_metadata", "-1",
        "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le",
        "-fflags", "+bitexact", "-flags:a", "+bitexact",  # no LIST/encoder chunk: clean 44-byte header
        "-f", "wav", str(dst),
    ]
    await _run(cmd)
    if not dst.exists() or dst.stat().st_size <= 44:
        raise AudioError("BAD_AUDIO", "The recording contains no audio.")
    return dst


async def probe_duration(path: Path) -> float:
    """Duration in seconds. WAVs are read from the header; anything else via ffprobe."""
    try:
        return wav_duration(path.read_bytes())
    except ValueError:
        pass
    out = await _run([
        settings.ffprobe, "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ])
    try:
        return float(out.decode().strip())
    except ValueError:
        raise AudioError("BAD_AUDIO", "Could not measure the audio duration.")


# --------------------------------------------------------------------------- WAV bytes


@dataclass
class Wav:
    channels: int
    sample_rate: int
    sample_width: int  # bytes per sample
    pcm: bytes

    @property
    def block_align(self) -> int:
        return self.channels * self.sample_width

    @property
    def duration(self) -> float:
        return len(self.pcm) / (self.block_align * self.sample_rate)


def parse_wav(data: bytes) -> Wav:
    """Parse a PCM WAV, tolerating streaming headers (RIFF/data size 0xFFFFFFFF or 0).

    Raises ValueError for non-WAV or non-integer-PCM data.
    """
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a RIFF/WAVE file")
    fmt: tuple[int, int, int, int] | None = None
    off = 12
    while off + 8 <= len(data):
        cid = data[off:off + 4]
        size = struct.unpack_from("<I", data, off + 4)[0]
        body = off + 8
        if cid == b"fmt ":
            if size < 16 or body + 16 > len(data):
                raise ValueError("truncated fmt chunk")
            audio_format, channels, rate, _, _, bits = struct.unpack_from("<HHIIHH", data, body)
            if audio_format == 0xFFFE and size >= 40:  # WAVE_FORMAT_EXTENSIBLE: real format in SubFormat GUID
                audio_format = struct.unpack_from("<H", data, body + 24)[0]
            fmt = (audio_format, channels, rate, bits)
        elif cid == b"data":
            if fmt is None:
                raise ValueError("data chunk before fmt chunk")
            audio_format, channels, rate, bits = fmt
            if audio_format != 1 or bits not in (8, 16, 24, 32) or channels < 1 or rate < 1:
                raise ValueError(f"unsupported WAV format {audio_format}/{bits}-bit")
            available = len(data) - body
            if size in (UNKNOWN_SIZE, 0) or size > available:
                size = available  # streaming header: data runs to the end of the file
            width = bits // 8
            block = channels * width
            size -= size % block
            return Wav(channels, rate, width, data[body:body + size])
        if size == UNKNOWN_SIZE:
            raise ValueError("unknown-size chunk before data")
        off = body + size + (size & 1)
    raise ValueError("no data chunk")


def build_wav(w: Wav) -> bytes:
    """Canonical 44-byte-header PCM WAV."""
    data_len = len(w.pcm)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_len, b"WAVE",
        b"fmt ", 16, 1, w.channels, w.sample_rate, w.sample_rate * w.block_align, w.block_align, w.sample_width * 8,
        b"data", data_len,
    )
    return header + w.pcm


def wav_duration(data: bytes) -> float:
    return parse_wav(data).duration


def normalize_wav_bytes(data: bytes) -> bytes:
    """Rewrite any PCM WAV (incl. Gnani's streaming header) to a canonical header with true sizes."""
    return build_wav(parse_wav(data))


async def normalize_wav(data: bytes, sample_rate: int | None = None) -> bytes:
    """normalize_wav_bytes with an ffmpeg fallback for formats the pure-Python path rejects."""
    try:
        return normalize_wav_bytes(data)
    except ValueError as exc:
        log.info("Pure-Python WAV normalise failed (%s); using ffmpeg.", exc)
    with tempfile.TemporaryDirectory(prefix="apni-norm-") as tmp:
        src, dst = Path(tmp) / "in.bin", Path(tmp) / "out.wav"
        src.write_bytes(data)
        await convert_to_wav(src, dst, sample_rate or settings.tts_sample_rate)
        return dst.read_bytes()


def concat_wavs(parts: list[bytes], gap_ms: int = 120) -> bytes:
    """Concatenate WAVs of the same format, with a short silence between sentences."""
    if not parts:
        raise ValueError("nothing to concatenate")
    wavs = [parse_wav(p) for p in parts]
    first = wavs[0]
    for w in wavs[1:]:
        if (w.channels, w.sample_rate, w.sample_width) != (first.channels, first.sample_rate, first.sample_width):
            raise ValueError("cannot concatenate WAVs with different formats")
    if len(wavs) == 1:
        return build_wav(first)
    gap = b"\x00" * (int(first.sample_rate * gap_ms / 1000) * first.block_align)
    return build_wav(Wav(first.channels, first.sample_rate, first.sample_width, gap.join(w.pcm for w in wavs)))


def trim_wav_file(path: Path, max_seconds: float) -> float:
    """Trim a WAV file in place to at most max_seconds. Returns the resulting duration."""
    w = parse_wav(path.read_bytes())
    max_bytes = int(max_seconds * w.sample_rate) * w.block_align
    if len(w.pcm) > max_bytes:
        w = Wav(w.channels, w.sample_rate, w.sample_width, w.pcm[:max_bytes])
        path.write_bytes(build_wav(w))
    return w.duration
