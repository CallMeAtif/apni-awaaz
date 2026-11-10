"""Runtime configuration: secrets from apni-awaaz/.env, paths, tool locations, CORS.

Secrets are loaded into os.environ (python-dotenv, existing env vars win) so any module
can read them, but they are never logged or printed. Use `settings.gnani_api_key` /
`settings.nvidia_api_key` instead of reading .env yourself.
"""
from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

log = logging.getLogger("apni.config")

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
ENV_FILE = PROJECT_ROOT / ".env"

# override=False: values already in the environment (e.g. dummy keys set by tests) win.
load_dotenv(ENV_FILE, override=False)


class ConfigError(RuntimeError):
    """Raised when a required setting (usually an API key) is missing."""


def _find_binary(name: str, default: str) -> str:
    env = os.environ.get(f"{name.upper()}_PATH")
    if env:
        return env
    if Path(default).exists():
        return default
    return shutil.which(name) or default


@dataclass
class Settings:
    gnani_api_key: str | None = field(
        default_factory=lambda: os.environ.get("GNANI_API_KEY") or os.environ.get("GNANI_KEY") or None
    )
    nvidia_api_key: str | None = field(default_factory=lambda: os.environ.get("NVIDIA_API_KEY") or None)

    gnani_base_url: str = "https://api.vachana.ai"
    user_agent: str = "ApniAwaaz/0.1"  # Cloudflare 403/1010s Python's default UA: always send ours.

    ffmpeg: str = field(default_factory=lambda: _find_binary("ffmpeg", "/opt/homebrew/bin/ffmpeg"))
    ffprobe: str = field(default_factory=lambda: _find_binary("ffprobe", "/opt/homebrew/bin/ffprobe"))

    # backend/data/ (gitignored). Tests point this at a tmp dir.
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("APNI_DATA_DIR", BACKEND_DIR / "data")))

    cors_origins: list[str] = field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    # Audio rules (see the HTTP contract in docs/TEAM_BRIEF.md).
    stt_sample_rate: int = 16_000
    enroll_sample_rate: int = 44_100
    max_audio_s: float = 30.0  # Gnani STT + voice-clone limit
    listen_tolerance_s: float = 1.0  # 30-31 s recordings are trimmed, longer ones rejected
    min_listen_s: float = 0.4
    min_enroll_s: float = 5.0
    max_upload_bytes: int = 20 * 1024 * 1024

    # TTS rules.
    max_speak_chars: int = 300
    tts_chunk_chars: int = 180
    stock_voice: str = "Prakash"  # timbre-v3.0 male voice: the Ramesh fallback
    stock_model: str = "timbre-v3.0"
    clone_model: str = "vachana-vc-v1"
    # Check each new voice-cloned clip with Prisma and regenerate it if words were repeated or added
    # (app/verify.py). Costs one short STT call per new clip; verified clips are cached.
    verify_clone_tts: bool = True
    verify_max_attempts: int = 3
    # Cut the trailing "aaahhh" the clone model adds after a silent gap (verified by Prisma).
    trim_clone_tail: bool = True
    tts_sample_rate: int = 44_100

    # Optional Prisma keyterms (JSON array). Off by default: measured live, plain hi-IN/codemix
    # already transcribed the demo line perfectly ("पापा, ...") and a keyterm "Papa" only switched
    # it to Latin script ("Papa, ..."). Latency was the same either way.
    stt_keyterms: list[str] = field(default_factory=list)
    # Open the NVIDIA + Gnani connections when the server starts (tests switch this off).
    warmup_on_startup: bool = field(
        default_factory=lambda: os.environ.get("APNI_WARMUP_ON_STARTUP", "1").lower() not in ("0", "false", "no")
    )

    @property
    def voices_dir(self) -> Path:
        return self.data_dir / "voices"

    @property
    def tts_cache_dir(self) -> Path:
        return self.data_dir / "cache" / "tts"

    @property
    def usage_log(self) -> Path:
        return self.data_dir / "usage.jsonl"

    def missing_keys(self) -> list[str]:
        missing = []
        if not self.gnani_api_key:
            missing.append("GNANI_API_KEY")
        if not self.nvidia_api_key:
            missing.append("NVIDIA_API_KEY")
        return missing

    def require_gnani_key(self) -> str:
        if not self.gnani_api_key:
            raise ConfigError(f"GNANI_API_KEY is not set. Add it to {ENV_FILE} (never commit it).")
        return self.gnani_api_key

    def check_startup(self) -> None:
        """Fail fast and clearly at server start if the Gnani key is missing.

        The NVIDIA key only produces a warning: llm.py falls back to generic replies.
        """
        self.require_gnani_key()
        if not self.nvidia_api_key:
            log.warning("NVIDIA_API_KEY is not set in %s: reply suggestions will use fallbacks.", ENV_FILE)
        for tool in (self.ffmpeg, self.ffprobe):
            if not Path(tool).exists() and not shutil.which(tool):
                raise ConfigError(f"{tool} not found. Install ffmpeg (brew install ffmpeg) or set FFMPEG_PATH.")

    def ensure_dirs(self) -> None:
        self.voices_dir.mkdir(parents=True, exist_ok=True)
        self.tts_cache_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
