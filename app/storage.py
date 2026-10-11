"""Local workspace and upload-safety primitives.

The foundation does not upload or process media yet. These small utilities establish
the boundaries that later milestones will use for project-specific files.
"""

from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path


SUPPORTED_VIDEO_EXTENSIONS = frozenset(
    {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
)

CONTENT_TYPES_BY_EXTENSION = {
    ".mp4": frozenset({"video/mp4"}),
    ".mov": frozenset({"video/quicktime"}),
    ".m4v": frozenset({"video/x-m4v", "video/mp4"}),
    ".avi": frozenset({"video/x-msvideo", "video/avi"}),
    ".mkv": frozenset({"video/x-matroska"}),
    ".webm": frozenset({"video/webm"}),
}

_PROJECT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


def is_valid_project_id(project_id: str | None) -> bool:
    """Return True when an identifier is a safe single path segment."""

    return bool(project_id) and bool(_PROJECT_ID_PATTERN.fullmatch(project_id))


class UploadValidationError(ValueError):
    """Raised when upload metadata does not meet the local safety policy."""


def validate_upload_metadata(filename: str, content_type: str | None) -> str:
    """Validate name and declared type, returning the normalized extension.

    The original name is used only to determine the extension. It is never used as
    a destination filename, which prevents path traversal and filename collisions.
    """

    if not filename or "\x00" in filename or "/" in filename or "\\" in filename:
        raise UploadValidationError("The upload filename is invalid.")

    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_VIDEO_EXTENSIONS:
        raise UploadValidationError(
            "Unsupported video type. Use MP4, MOV, M4V, AVI, MKV, or WebM."
        )

    normalized_content_type = (content_type or "").split(";", 1)[0].strip().lower()
    if normalized_content_type and normalized_content_type not in CONTENT_TYPES_BY_EXTENSION[
        extension
    ]:
        raise UploadValidationError("The file type does not match its filename extension.")

    return extension


def validate_upload(
    filename: str,
    content_type: str | None,
    size_bytes: int,
    max_size_bytes: int,
) -> str:
    """Validate upload metadata including its declared size.

    The size check is separate from the name/content-type check so a streamed
    upload can be validated up front and then bounded while it is written.
    """

    extension = validate_upload_metadata(filename, content_type)

    if size_bytes < 0:
        raise UploadValidationError("The upload size cannot be negative.")
    if size_bytes > max_size_bytes:
        raise UploadValidationError(
            f"The upload exceeds the {max_size_bytes // (1024 * 1024)} MB limit."
        )

    return extension


def _remove_tree_entry(path: Path) -> None:
    """Remove one temp entry without following a symlink."""

    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


class ProjectWorkspace:
    """Paths owned by one generated project identifier."""

    def __init__(self, root: Path):
        self.root = root
        self.source = root / "source"
        self.transcripts = root / "transcripts"
        self.assets = root / "assets"
        self.previews = root / "previews"
        self.temp = root / "temp"
        self.exports = root / "exports"

    def ensure_layout(self) -> "ProjectWorkspace":
        for directory in (
            self.root,
            self.source,
            self.transcripts,
            self.assets,
            self.previews,
            self.temp,
            self.exports,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        return self

    def allocate_source_path(
        self,
        filename: str,
        content_type: str | None,
        size_bytes: int,
        max_size_bytes: int,
    ) -> Path:
        """Return a safe generated path after validating upload metadata."""

        extension = validate_upload(
            filename, content_type, size_bytes, max_size_bytes
        )
        self.ensure_layout()
        return self.source / f"source-{uuid.uuid4().hex}{extension}"

    def cleanup_temp(self) -> None:
        """Clean only this project's temporary directory contents."""

        self.temp.mkdir(parents=True, exist_ok=True)
        for child in self.temp.iterdir():
            _remove_tree_entry(child)


class Workspace:
    """Root local workspace shared by projects and transient app data."""

    def __init__(self, root: Path):
        self.root = root
        self.projects = root / "projects"
        self.uploads = root / "uploads"
        self.previews = root / "previews"
        self.exports = root / "exports"
        self.temp = root / "temp"

    def ensure_layout(self) -> "Workspace":
        for directory in (
            self.root,
            self.projects,
            self.uploads,
            self.previews,
            self.exports,
            self.temp,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        return self

    def create_project(self, project_id: str | None = None) -> ProjectWorkspace:
        """Create a project workspace using a generated or strictly validated ID."""

        self.ensure_layout()
        resolved_id = project_id or uuid.uuid4().hex
        if not _PROJECT_ID_PATTERN.fullmatch(resolved_id):
            raise ValueError("Invalid project identifier.")

        project_root = self.projects / resolved_id
        project = ProjectWorkspace(project_root)
        return project.ensure_layout()
