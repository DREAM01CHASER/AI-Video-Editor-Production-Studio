"""The local asset library: the user's own B-roll clips and sound effects.

Assets belong to one project and are imported from the user's own files. Nothing
is fetched from the internet here, and nothing is discovered by scanning the
filesystem: an asset only exists after the user chose that exact file, which is
what "never automatically use arbitrary files" means in practice.

Every stored file gets a generated name, so a filename from outside can never
become a path: the original name is kept only as a label for the interface.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from .errors import ApiError
from .media import MediaMetadata
from .storage import CONTENT_TYPES_BY_EXTENSION, SUPPORTED_VIDEO_EXTENSIONS

BROLL = "broll"
SFX = "sfx"
KINDS = (BROLL, SFX)

#: Sound effects are audio-only, so the accepted containers differ from video.
AUDIO_EXTENSIONS = frozenset({".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"})

CONTENT_TYPES_BY_AUDIO_EXTENSION = {
    ".mp3": frozenset({"audio/mpeg", "audio/mp3"}),
    ".wav": frozenset({"audio/wav", "audio/x-wav", "audio/wave"}),
    ".m4a": frozenset({"audio/mp4", "audio/x-m4a", "audio/m4a"}),
    ".aac": frozenset({"audio/aac", "audio/x-aac"}),
    ".ogg": frozenset({"audio/ogg", "application/ogg"}),
    ".flac": frozenset({"audio/flac", "audio/x-flac"}),
}

#: A short B-roll insert is a cutaway; a very long one is a second edit.
MAX_BROLL_SECONDS = 120.0
#: Long enough for a whoosh or a riser, short enough to be an effect.
MAX_SFX_SECONDS = 30.0

MAX_TAGS = 6
MAX_TAG_LENGTH = 24
MAX_LABEL_LENGTH = 80

_MEDIA_TYPES_BY_SUFFIX = {
    **{suffix: sorted(types)[0] for suffix, types in CONTENT_TYPES_BY_EXTENSION.items()},
    **{suffix: sorted(types)[0] for suffix, types in CONTENT_TYPES_BY_AUDIO_EXTENSION.items()},
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalise_kind(value: object) -> str:
    """Validate the kind of asset being imported."""

    kind = str(value or "").strip().lower()
    if kind not in KINDS:
        raise ApiError(
            "invalid_asset_kind",
            f"kind must be one of {', '.join(KINDS)} (got '{value}').",
            422,
        )
    return kind


def media_type_for(path: Path) -> str:
    """The content type to serve a stored asset with."""

    return _MEDIA_TYPES_BY_SUFFIX.get(path.suffix.lower(), "application/octet-stream")


def accepted_extensions(kind: str) -> frozenset[str]:
    return SUPPORTED_VIDEO_EXTENSIONS if kind == BROLL else AUDIO_EXTENSIONS


def validate_asset_filename(filename: str, kind: str) -> str:
    """Validate an imported asset's name, returning its normalized extension.

    The name is never used as a destination path, so traversal and collisions are
    impossible by construction.
    """

    if not filename or "\x00" in filename or "/" in filename or "\\" in filename:
        raise ApiError("invalid_asset", "The asset filename is invalid.", 422)

    extension = Path(filename).suffix.lower()
    allowed = accepted_extensions(kind)
    if extension not in allowed:
        raise ApiError(
            "invalid_asset",
            f"Unsupported {kind} file type. Use "
            f"{', '.join(sorted(item.lstrip('.').upper() for item in allowed))}.",
            422,
        )
    return extension


def validate_asset_metadata(
    filename: str, content_type: str | None, kind: str
) -> str:
    """Validate name and declared type before a byte is written to disk."""

    extension = validate_asset_filename(filename, kind)

    normalized = (content_type or "").split(";", 1)[0].strip().lower()
    known = CONTENT_TYPES_BY_EXTENSION.get(extension) or CONTENT_TYPES_BY_AUDIO_EXTENSION.get(
        extension
    )
    if normalized and known and normalized not in known:
        # A browser often reports a generic type, so only a *conflicting* claim is
        # rejected; the extension and the probe decide what the file really is.
        if normalized not in {"application/octet-stream", "binary/octet-stream"}:
            raise ApiError(
                "invalid_asset",
                "The file type does not match its filename extension.",
                422,
            )
    return extension


def validate_asset_size(size_bytes: int, max_size_bytes: int) -> None:
    if size_bytes <= 0:
        raise ApiError("empty_upload", "The uploaded file is empty.", 400)
    if size_bytes > max_size_bytes:
        raise ApiError(
            "file_too_large",
            f"The upload exceeds the {max_size_bytes // (1024 * 1024)} MB limit.",
            413,
        )


def normalise_tags(raw: object) -> list[str]:
    """Validate free-form tags, which are what makes the library searchable."""

    if raw is None:
        return []

    if isinstance(raw, str):
        items: Sequence[str] = [piece for piece in raw.replace(";", ",").split(",")]
    elif isinstance(raw, (list, tuple)):
        items = [str(item) for item in raw]
    else:
        raise ApiError(
            "invalid_asset", "tags must be a comma-separated string or a list.", 422
        )

    tags: list[str] = []
    for item in items:
        tag = " ".join(str(item).split()).lower()
        if not tag:
            continue
        if len(tag) > MAX_TAG_LENGTH:
            raise ApiError(
                "invalid_asset",
                f"Each tag must be {MAX_TAG_LENGTH} characters or fewer (got '{tag}').",
                422,
            )
        if tag not in tags:
            tags.append(tag)

    if len(tags) > MAX_TAGS:
        raise ApiError(
            "invalid_asset", f"Use at most {MAX_TAGS} tags (got {len(tags)}).", 422
        )
    return tags


def normalise_label(raw: object, fallback: str) -> str:
    """The user-facing name of an asset, kept short and free of path characters."""

    text = " ".join(str(raw or "").split())
    if not text:
        text = fallback
    return text[:MAX_LABEL_LENGTH]


def allocate_asset_path(directory: Path, kind: str, extension: str) -> Path:
    """Return a free, generated storage path inside the project's asset folder."""

    directory.mkdir(parents=True, exist_ok=True)
    while True:
        candidate = directory / f"{kind}-{uuid.uuid4().hex}{extension}"
        if not candidate.exists():
            return candidate


def asset_from_import(
    *,
    asset_id: str,
    kind: str,
    label: str,
    stored_name: str,
    original_name: str,
    tags: Iterable[str],
    metadata: MediaMetadata,
) -> dict[str, object]:
    """Build the asset record that is saved in ``project.json``."""

    if kind == BROLL and not metadata.has_video:
        raise ApiError(
            "invalid_asset",
            "That file has no video stream, so it cannot be used as B-roll.",
            422,
        )
    if kind == SFX and not metadata.has_audio:
        raise ApiError(
            "invalid_asset",
            "That file has no audio stream, so it cannot be used as a sound effect.",
            422,
        )

    limit = MAX_BROLL_SECONDS if kind == BROLL else MAX_SFX_SECONDS
    if metadata.duration <= 0:
        raise ApiError(
            "invalid_asset",
            "The file's duration could not be read, so it cannot be placed on the timeline.",
            422,
        )
    if metadata.duration > limit:
        raise ApiError(
            "invalid_asset",
            f"A {kind} file cannot be longer than {limit:g}s (this one is "
            f"{metadata.duration:.1f}s).",
            422,
        )

    return {
        "id": asset_id,
        "kind": kind,
        "label": label,
        # The stored name is generated; the original is kept only to show the user
        # which of their files this is.
        "file": stored_name,
        "original_name": original_name[:120],
        "tags": list(tags),
        "duration": metadata.duration,
        "width": metadata.width,
        "height": metadata.height,
        "fps": metadata.fps,
        "has_audio": metadata.has_audio,
        "size_bytes": metadata.size_bytes,
        "created_at": _utcnow(),
    }


def find_asset(project: dict, asset_id: str | None) -> dict:
    """Find one asset in a project, or explain that it is missing."""

    target = str(asset_id or "").strip()
    if not target:
        raise ApiError("invalid_asset_id", "Send the 'asset_id' of an asset.", 422)

    for asset in project.get("assets") or []:
        if isinstance(asset, dict) and str(asset.get("id")) == target:
            return asset

    raise ApiError("asset_not_found", f"No asset with id '{target}' exists.", 404)


def search_assets(
    assets: Sequence[dict], *, query: str = "", kind: str | None = None
) -> list[dict]:
    """Filter the library by kind and by a query over labels and tags."""

    wanted_kind = str(kind or "").strip().lower()
    if wanted_kind and wanted_kind not in KINDS:
        raise ApiError(
            "invalid_asset_kind",
            f"kind must be one of {', '.join(KINDS)} (got '{kind}').",
            422,
        )

    needle = " ".join(str(query or "").split()).lower()
    matches: list[dict] = []

    for asset in assets:
        if not isinstance(asset, dict):
            continue
        if wanted_kind and str(asset.get("kind")) != wanted_kind:
            continue
        if needle:
            haystack = " ".join(
                [
                    str(asset.get("label") or ""),
                    str(asset.get("original_name") or ""),
                    " ".join(str(tag) for tag in asset.get("tags") or []),
                ]
            ).lower()
            if needle not in haystack:
                continue
        matches.append(asset)

    matches.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return matches


def public_asset(asset: dict, project_id: str) -> dict[str, object]:
    """The asset as the interface sees it, with a URL that can be played."""

    name = str(asset.get("file") or "")
    return {
        **asset,
        "url": f"/api/projects/{project_id}/assets/{asset.get('id')}/file",
        "available": bool(name),
    }


def used_in_timeline(project: dict, asset_id: str) -> list[str]:
    """The tracks that reference an asset, so a delete can explain itself."""

    timeline = project.get("timeline") or {}
    used: list[str] = []

    for item in timeline.get("broll") or []:
        if isinstance(item, dict) and str(item.get("source")) == asset_id:
            used.append("broll")
            break
    for item in timeline.get("sfx") or []:
        if isinstance(item, dict) and str(item.get("asset_id")) == asset_id:
            used.append("sfx")
            break
    return used
