"""Small, dependency-free application configuration helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .llm import DEFAULT_BASE_URL as DEFAULT_LLM_BASE_URL
from .llm import DEFAULT_MODEL as DEFAULT_LLM_MODEL
from .stock import DEFAULT_BASE_URL as DEFAULT_PEXELS_BASE_URL


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


#: The optional environment file, read at startup if it exists.
ENV_FILENAME = ".env"


def load_env_file(path: Path | None = None, *, override: bool = False) -> dict[str, str]:
    """Read a simple ``KEY=VALUE`` file into the process environment.

    The README, ``.env.example`` and every stock/LLM error message tell the user
    to put their keys in ``.env``, so the file has to actually be read — without
    this, configuring an optional key appears to work and then does nothing.

    This is hand-written rather than a new dependency: the format is a handful of
    lines, and ``TECH_STACK.md`` asks not to add a library unless it is needed.

    Existing environment variables always win, so a value exported in the shell
    still overrides the file, and anything already exported is left alone.
    """

    target = path or (PROJECT_ROOT / ENV_FILENAME)
    loaded: dict[str, str] = {}

    try:
        raw = target.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        # A missing file is the normal case, so this is not an error worth raising.
        return loaded

    for line in raw.splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if text.lower().startswith("export "):
            text = text[7:].strip()
        key, separator, value = text.partition("=")
        if not separator:
            continue
        key = key.strip()
        if not key:
            continue

        value = value.strip()
        # A quoted value keeps its inner spaces and loses the quotes.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        else:
            # An unquoted value may carry a trailing comment.
            value = value.split(" #", 1)[0].strip()

        loaded[key] = value
        if override or key not in os.environ:
            os.environ[key] = value

    return loaded


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

    #: Optional LLM pass for clip labelling. It stays disabled unless a key is
    #: supplied, and the heuristic result is used whenever it is unavailable.
    llm_api_key: str | None = None
    llm_base_url: str = DEFAULT_LLM_BASE_URL
    llm_model: str = DEFAULT_LLM_MODEL

    #: Optional Pexels key for stock B-roll search. With no key the local asset
    #: library keeps working and the stock panel explains why search is off.
    pexels_api_key: str | None = None
    pexels_base_url: str = DEFAULT_PEXELS_BASE_URL

    @classmethod
    def from_environment(cls) -> "Settings":
        # The optional keys are documented as living in `.env`, so it is read
        # before anything is looked up. Values already exported in the shell win.
        load_env_file()

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
            llm_api_key=os.getenv("LLM_API_KEY", "").strip() or None,
            llm_base_url=_text(os.getenv("LLM_BASE_URL"), DEFAULT_LLM_BASE_URL),
            llm_model=_text(os.getenv("LLM_MODEL"), DEFAULT_LLM_MODEL),
            pexels_api_key=os.getenv("PEXELS_API_KEY", "").strip() or None,
            pexels_base_url=_text(os.getenv("PEXELS_BASE_URL"), DEFAULT_PEXELS_BASE_URL),
        )
