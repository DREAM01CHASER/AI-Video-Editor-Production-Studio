"""FFprobe-based video metadata reading.

Metadata is read from the file path with ``subprocess`` argument arrays, so a
large source video is never loaded into memory. The parsing helpers are pure
functions and are unit-tested without FFmpeg installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .errors import ApiError

FFPROBE_BINARY = "ffprobe"
PROBE_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class VideoMetadata:
    """The subset of source metadata the editor needs for a project."""

    duration: float
    width: int
    height: int
    fps: float
    size_bytes: int

    def as_dict(self) -> dict[str, float | int]:
        return {
            "duration": self.duration,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class MediaMetadata:
    """What an imported asset needs: how long it is, and whether it has video."""

    duration: float
    size_bytes: int
    width: int
    height: int
    fps: float
    has_video: bool
    has_audio: bool

    def as_dict(self) -> dict[str, float | int | bool]:
        return {
            "duration": self.duration,
            "size_bytes": self.size_bytes,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "has_video": self.has_video,
            "has_audio": self.has_audio,
        }


def ffprobe_available() -> bool:
    """Report whether FFprobe can be found on PATH."""

    return shutil.which(FFPROBE_BINARY) is not None


def parse_frame_rate(value: object) -> float:
    """Convert an FFprobe frame-rate field such as ``"30000/1001"`` to a float."""

    if not isinstance(value, str) or not value.strip():
        return 0.0

    text = value.strip()
    if "/" in text:
        numerator, _, denominator = text.partition("/")
        try:
            top = float(numerator)
            bottom = float(denominator)
        except ValueError:
            return 0.0
        if bottom == 0:
            return 0.0
        return round(top / bottom, 3)

    try:
        return round(float(text), 3)
    except ValueError:
        return 0.0


def _positive_float(value: object) -> float:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed > 0 else 0.0


def _first_video_stream(payload: dict) -> dict | None:
    """Pick the first real video stream, skipping embedded cover art."""

    streams = payload.get("streams") or []
    for stream in streams:
        if not isinstance(stream, dict) or stream.get("codec_type") != "video":
            continue
        if (stream.get("disposition") or {}).get("attached_pic"):
            continue
        if int(stream.get("width") or 0) <= 0 or int(stream.get("height") or 0) <= 0:
            continue
        return stream
    return None


def metadata_from_probe(payload: dict, size_bytes: int) -> VideoMetadata:
    """Turn an FFprobe JSON payload into :class:`VideoMetadata`."""

    stream = _first_video_stream(payload)
    if stream is None:
        raise ApiError(
            "no_video_stream",
            "The file does not contain a readable video stream.",
            status_code=422,
        )

    container = payload.get("format") or {}
    duration = _positive_float(container.get("duration"))
    if duration == 0.0:
        duration = _positive_float(stream.get("duration"))

    fps = parse_frame_rate(stream.get("avg_frame_rate")) or parse_frame_rate(
        stream.get("r_frame_rate")
    )

    return VideoMetadata(
        duration=round(duration, 3),
        width=int(stream["width"]),
        height=int(stream["height"]),
        fps=fps,
        size_bytes=int(size_bytes),
    )


def metadata_from_media_probe(payload: dict, size_bytes: int) -> MediaMetadata:
    """Turn an FFprobe payload into :class:`MediaMetadata`.

    Unlike :func:`metadata_from_probe` this does not require a video stream, so the
    same call describes an audio-only sound effect and a B-roll clip.
    """

    streams = [s for s in (payload.get("streams") or []) if isinstance(s, dict)]
    video = _first_video_stream(payload)
    has_audio = any(stream.get("codec_type") == "audio" for stream in streams)

    container = payload.get("format") or {}
    duration = _positive_float(container.get("duration"))
    if duration == 0.0:
        for stream in streams:
            duration = _positive_float(stream.get("duration"))
            if duration:
                break

    if duration == 0.0 and video is None and not has_audio:
        raise ApiError(
            "unreadable_media",
            "The file contains no readable audio or video stream.",
            status_code=422,
        )

    return MediaMetadata(
        duration=round(duration, 3),
        size_bytes=int(size_bytes),
        width=int((video or {}).get("width") or 0),
        height=int((video or {}).get("height") or 0),
        fps=parse_frame_rate((video or {}).get("avg_frame_rate"))
        or parse_frame_rate((video or {}).get("r_frame_rate")),
        has_video=video is not None,
        has_audio=has_audio,
    )


def probe_media(path: Path) -> MediaMetadata:
    """Read duration and stream information for any local media file."""

    payload = _run_ffprobe(path)
    return metadata_from_media_probe(payload, size_bytes=path.stat().st_size)


def _run_ffprobe(path: Path) -> dict:
    """Run FFprobe and return the parsed JSON, with clear failures."""

    if not ffprobe_available():
        raise ApiError(
            "ffprobe_unavailable",
            "FFprobe was not found on PATH. Install FFmpeg and restart the app.",
            status_code=503,
        )

    command = [
        FFPROBE_BINARY,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            shell=False,
        )
    except FileNotFoundError as exc:
        raise ApiError(
            "ffprobe_unavailable",
            "FFprobe was not found on PATH. Install FFmpeg and restart the app.",
            status_code=503,
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ApiError(
            "probe_timeout", "Reading this file's metadata timed out.", status_code=504
        ) from exc

    if completed.returncode != 0:
        raise ApiError(
            "unreadable_media",
            "FFprobe could not read this file. It may be corrupt or in an unsupported format.",
            status_code=422,
        )

    try:
        return json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ApiError(
            "unreadable_media", "FFprobe returned output that could not be parsed.", status_code=422
        ) from exc


def probe_video(path: Path) -> VideoMetadata:
    """Read metadata for a local video file using FFprobe."""

    payload = _run_ffprobe(path)
    return metadata_from_probe(payload, size_bytes=path.stat().st_size)
