"""Small, dependency-free application configuration helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WORKSPACE = PROJECT_ROOT / "workspace"
DEFAULT_MAX_UPLOAD_SIZE_MB = 500


def _positive_int(value: str | None, default: int) -> int:
    """Read a positive integer environment value, falling back when omitted."""

    if value is None or not value.strip():
        return default

    try:
        parsed = int(value)
    except ValueError:
        return default

    return parsed if parsed > 0 else default


@dataclass(frozen=True)
class Settings:
    """Runtime settings for the foundation application."""

    project_root: Path
    workspace_dir: Path
    max_upload_size_bytes: int

    @classmethod
    def from_environment(cls) -> "Settings":
        workspace_value = os.getenv("AI_VIDEO_WORKSPACE", "workspace").strip()
        workspace_dir = Path(workspace_value).expanduser()
        if not workspace_dir.is_absolute():
            workspace_dir = PROJECT_ROOT / workspace_dir

        max_upload_size_mb = _positive_int(
            os.getenv("MAX_UPLOAD_SIZE_MB"), DEFAULT_MAX_UPLOAD_SIZE_MB
        )

        return cls(
            project_root=PROJECT_ROOT,
            workspace_dir=workspace_dir.resolve(),
            max_upload_size_bytes=max_upload_size_mb * 1024 * 1024,
        )
