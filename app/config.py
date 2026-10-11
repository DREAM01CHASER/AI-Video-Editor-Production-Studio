"""Small, dependency-free application configuration helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WORKSPACE = PROJECT_ROOT / "workspace"
DEFAULT_MAX_UPLOAD_SIZE_MB = 500

# Transcription defaults. "medium" is a reasonable accuracy/speed balance for a
# mid-range GPU; override with WHISPER_MODEL for a lighter or stronger model.
DEFAULT_WHISPER_MODEL = "medium"
DEFAULT_WHISPER_DEVICE = "auto"
DEFAULT_WHISPER_COMPUTE_TYPE = "auto"
DEFAULT_WHISPER_BEAM_SIZE = 5

#: Model sizes faster-whisper downloads by name. Any other value is treated as a
#: local path to an already-converted CTranslate2 model directory.
KNOWN_WHISPER_MODELS = (
    "tiny",
    "base",
    "small",
    "medium",
    "large-v3",
    "distil-large-v3",
)


def _positive_int(value: str | None, default: int) -> int:
    """Read a positive integer environment value, falling back when omitted."""

    if value is None or not value.strip():
        return default

    try:
        parsed = int(value)
    except ValueError:
        return default

    return parsed if parsed > 0 else default


def _boolean(value: str | None, default: bool) -> bool:
    """Read a boolean environment value, falling back when omitted."""

    if value is None or not value.strip():
        return default

    return value.strip().lower() in {"1", "true", "yes", "on"}


def _text(value: str | None, default: str) -> str:
    """Read a non-empty string environment value, falling back when omitted."""

    if value is None or not value.strip():
        return default

    return value.strip()


@dataclass(frozen=True)
class Settings:
    """Runtime settings for the local application."""

    project_root: Path
    workspace_dir: Path
    max_upload_size_bytes: int

    # Transcription settings carry defaults so a Settings instance can still be
    # constructed with just the paths in tests.
    whisper_model: str = DEFAULT_WHISPER_MODEL
    whisper_device: str = DEFAULT_WHISPER_DEVICE
    whisper_compute_type: str = DEFAULT_WHISPER_COMPUTE_TYPE
    whisper_language: str | None = None
    whisper_beam_size: int = DEFAULT_WHISPER_BEAM_SIZE
    whisper_word_timestamps: bool = True
    whisper_allow_model_download: bool = True
    whisper_cpu_threads: int = 0
    #: Root for downloaded model weights. ``None`` uses the normal Hugging Face
    #: cache, which is also shared with other local tools.
    model_cache_dir: Path | None = None

    @classmethod
    def from_environment(cls) -> "Settings":
        workspace_value = os.getenv("AI_VIDEO_WORKSPACE", "workspace").strip()
        workspace_dir = Path(workspace_value).expanduser()
        if not workspace_dir.is_absolute():
            workspace_dir = PROJECT_ROOT / workspace_dir

        max_upload_size_mb = _positive_int(
            os.getenv("MAX_UPLOAD_SIZE_MB"), DEFAULT_MAX_UPLOAD_SIZE_MB
        )

        cache_value = os.getenv("WHISPER_MODEL_CACHE_DIR", "").strip()
        model_cache_dir = Path(cache_value).expanduser().resolve() if cache_value else None

        language_value = os.getenv("WHISPER_LANGUAGE", "").strip()

        return cls(
            project_root=PROJECT_ROOT,
            workspace_dir=workspace_dir.resolve(),
            max_upload_size_bytes=max_upload_size_mb * 1024 * 1024,
            whisper_model=_text(os.getenv("WHISPER_MODEL"), DEFAULT_WHISPER_MODEL),
            whisper_device=_text(
                os.getenv("WHISPER_DEVICE"), DEFAULT_WHISPER_DEVICE
            ).lower(),
            whisper_compute_type=_text(
                os.getenv("WHISPER_COMPUTE_TYPE"), DEFAULT_WHISPER_COMPUTE_TYPE
            ).lower(),
            whisper_language=language_value or None,
            whisper_beam_size=_positive_int(
                os.getenv("WHISPER_BEAM_SIZE"), DEFAULT_WHISPER_BEAM_SIZE
            ),
            whisper_word_timestamps=_boolean(
                os.getenv("WHISPER_WORD_TIMESTAMPS"), True
            ),
            whisper_allow_model_download=_boolean(
                os.getenv("WHISPER_ALLOW_DOWNLOAD"), True
            ),
            whisper_cpu_threads=_positive_int(os.getenv("WHISPER_CPU_THREADS"), 0),
            model_cache_dir=model_cache_dir,
        )
