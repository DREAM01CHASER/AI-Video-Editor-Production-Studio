"""The editable edit: a timeline document derived from the project JSON.

DESIGN.md is explicit that the serializable project is the source of truth and
that the edit must never live inside a hidden FFmpeg command. This module is the
one place that turns the saved project into the edit the interface shows, and the
one place that validates an edited timeline back into the project document.

Nothing here duplicates storage. The clip's in/out points stay in ``clips``, the
caption style stays in ``caption_style``, and the render settings stay in
``export``; the timeline simply composes them for editing and writes validated
values back to those same fields. That way there is exactly one copy of every
setting and the next render cannot disagree with what the user saw.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Sequence

from .errors import ApiError

TIMELINE_VERSION = 1

#: Frame rates offered for an export. Low rates make movement look choppy and very
#: high rates slow the render down, so the list is deliberately short.
FPS_CHOICES = (24, 25, 30, 60)
DEFAULT_FPS = 30

#: B-roll vertical placement and how it fills its box.
BROLL_PLACEMENTS = ("full", "top", "bottom")
BROLL_FITS = ("cover", "contain")

#: Transitions are cuts-level effects, so their duration is bounded to what still
#: reads as a transition rather than a dissolve into a different scene.
TRANSITION_KINDS = ("fade", "slide-left", "wipe-left")
MAX_TRANSITION_SECONDS = 2.0

#: A sound effect is mixed *under* the speech, so its gain is capped below the
#: voice level. The cap is deliberate: an effect that can be louder than the words
#: is how a mix ends up burying the speaker.
SFX_MIN_GAIN_DB = -60.0
SFX_MAX_GAIN_DB = -3.0
DEFAULT_SFX_GAIN_DB = -12.0
DEFAULT_SFX_FADE_SECONDS = 0.15

#: Tracks the editor shows but the renderer cannot honour yet. Refusing an item is
#: the honest answer: storing it and quietly dropping it from the render would make
#: the interface lie about what the exported file contains.
UNRENDERED_TRACKS = {
    "transitions": "Transitions arrive with Milestone 9.",
}

_TRACK_KEYS = ("broll", "sfx", "transitions")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _number(value: object, field: str) -> float:
    """Read a time or gain value, refusing anything that is not a number."""

    if isinstance(value, bool) or value is None:
        raise ApiError(
            "invalid_timeline", f"{field} must be a number (got '{value}').", 422
        )
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ApiError(
            "invalid_timeline", f"{field} must be a number (got '{value}').", 422
        ) from exc


def empty_tracks() -> dict[str, list]:
    """The item lists a project starts with: present, so the tracks can be shown."""

    return {key: [] for key in _TRACK_KEYS}


def stored_timeline(project: dict) -> dict:
    """The stored timeline section, with defaults filled in for older projects."""

    stored = project.get("timeline")
    timeline = dict(stored) if isinstance(stored, dict) else {}
    timeline.setdefault("version", TIMELINE_VERSION)
    for key in _TRACK_KEYS:
        items = timeline.get(key)
        timeline[key] = [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    timeline.setdefault("updated_at", None)
    return timeline


def normalise_fps(
    value: object, fallback: int = DEFAULT_FPS, *, code: str = "invalid_timeline"
) -> int:
    """Validate a frame rate against the short list the app supports."""

    if value is None or value == "":
        return fallback
    if isinstance(value, bool):
        raise ApiError(code, f"fps must be a number (got '{value}').", 422)
    try:
        fps = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ApiError(code, f"fps must be a number (got '{value}').", 422) from exc

    if fps not in FPS_CHOICES:
        raise ApiError(
            code,
            f"fps must be one of {', '.join(str(choice) for choice in FPS_CHOICES)} "
            f"(got {value}).",
            422,
        )
    return fps


# --------------------------------------------------------------------- items


def _item_time(item: dict, field: str) -> float:
    return _number(item.get(field), field)


def _validate_broll_item(
    item: dict,
    clip_duration: float,
    asset_ids: set[str],
    asset_durations: dict[str, float] | None = None,
) -> dict:
    source = str(item.get("source") or "").strip()
    if not source:
        raise ApiError(
            "invalid_timeline",
            "A B-roll item needs a source: either the project's own video or an "
            "imported asset.",
            422,
        )
    if source not in asset_ids and source not in {"source", "video"}:
        raise ApiError(
            "invalid_asset",
            f"No asset with id '{source}' exists in this project.",
            404,
        )

    placement = str(item.get("placement") or "full").strip().lower()
    if placement not in BROLL_PLACEMENTS:
        raise ApiError(
            "invalid_timeline",
            f"placement must be one of {', '.join(BROLL_PLACEMENTS)} (got '{placement}').",
            422,
        )

    fit = str(item.get("fit") or "cover").strip().lower()
    if fit not in BROLL_FITS:
        raise ApiError(
            "invalid_timeline",
            f"fit must be one of {', '.join(BROLL_FITS)} (got '{fit}').",
            422,
        )

    start, end = _item_time(item, "start"), _item_time(item, "end")
    _check_range(start, end, clip_duration, "B-roll item")

    asset_start = item.get("source_start")
    asset_end = item.get("source_end")
    if asset_start is not None or asset_end is not None:
        asset_start = _number(asset_start or 0.0, "source_start")
        asset_end = _number(asset_end if asset_end is not None else start + (end - start), "source_end")
        if asset_end <= asset_start:
            raise ApiError(
                "invalid_timeline",
                "A B-roll item's source range must end after it starts.",
                422,
            )
    else:
        asset_start, asset_end = 0.0, round(end - start, 3)

    # The inserted seconds have to exist in whatever the insert comes from, or the
    # render would show black frames the timeline never mentioned.
    limit = (asset_durations or {}).get("source" if source in {"source", "video"} else source)
    if limit and asset_end > limit + 0.05:
        raise ApiError(
            "invalid_timeline",
            f"The insert shows {asset_start:g}s → {asset_end:g}s of "
            f"'{source}', which is only {limit:.2f}s long.",
            422,
        )

    return {
        "source": source,
        "source_start": round(asset_start, 3),
        "source_end": round(asset_end, 3),
        "start": round(start, 3),
        "end": round(end, 3),
        "placement": placement,
        "fit": fit,
    }


def _validate_sfx_item(item: dict, clip_duration: float, asset_ids: set[str]) -> dict:
    asset_id = str(item.get("asset_id") or "").strip()
    if not asset_id:
        raise ApiError(
            "invalid_timeline", "A sound-effect item needs an asset_id.", 422
        )
    if asset_id not in asset_ids:
        raise ApiError(
            "invalid_asset", f"No asset with id '{asset_id}' exists in this project.", 404
        )

    start, end = _item_time(item, "start"), _item_time(item, "end")
    _check_range(start, end, clip_duration, "Sound-effect item")

    gain = _number(item.get("gain_db", DEFAULT_SFX_GAIN_DB), "gain_db")
    if not SFX_MIN_GAIN_DB <= gain <= SFX_MAX_GAIN_DB:
        raise ApiError(
            "invalid_timeline",
            f"gain_db must be between {SFX_MIN_GAIN_DB:g} and {SFX_MAX_GAIN_DB:g} "
            f"(got {gain:g}).",
            422,
        )

    fade = _number(item.get("fade_seconds", DEFAULT_SFX_FADE_SECONDS), "fade_seconds")
    if fade < 0 or fade > (end - start):
        raise ApiError(
            "invalid_timeline",
            "fade_seconds must be zero or more and no longer than the effect itself.",
            422,
        )

    return {
        "asset_id": asset_id,
        "start": round(start, 3),
        "end": round(end, 3),
        "gain_db": round(gain, 2),
        "fade_seconds": round(fade, 3),
    }


def _validate_transition_item(item: dict, clip_duration: float) -> dict:
    kind = str(item.get("kind") or "").strip().lower()
    if kind not in TRANSITION_KINDS:
        raise ApiError(
            "invalid_timeline",
            f"kind must be one of {', '.join(TRANSITION_KINDS)} (got '{kind}').",
            422,
        )

    at = str(item.get("at") or "start").strip().lower()
    if at not in {"start", "end"}:
        raise ApiError(
            "invalid_timeline",
            "A transition happens at the clip's start or end (got "
            f"'{item.get('at')}').",
            422,
        )

    duration = _number(item.get("duration", 0.5), "duration")
    if duration <= 0 or duration > min(MAX_TRANSITION_SECONDS, clip_duration / 2):
        raise ApiError(
            "invalid_timeline",
            "A transition must be longer than zero and no more than "
            f"{MAX_TRANSITION_SECONDS:g}s (and no more than half the clip).",
            422,
        )

    return {"kind": kind, "at": at, "duration": round(duration, 3)}


def _check_range(start: float, end: float, clip_duration: float, label: str) -> None:
    if end <= start:
        raise ApiError(
            "invalid_timeline",
            f"{label} must end after it starts (got {start:g}s → {end:g}s).",
            422,
        )
    if start < 0 or end > clip_duration + 0.01:
        raise ApiError(
            "invalid_timeline",
            f"{label} must sit inside the {clip_duration:.2f}s clip "
            f"(got {start:g}s → {end:g}s).",
            422,
        )


def _check_asset_kinds(
    items: Sequence[dict], asset_kinds: dict[str, str], *, field: str, expected: str
) -> None:
    """A B-roll insert must use a B-roll asset, and an effect a sound effect."""

    for item in items:
        asset_id = str(item.get(field) or "")
        if asset_id in {"source", "video"}:
            continue
        kind = asset_kinds.get(asset_id)
        if kind and kind != expected:
            raise ApiError(
                "invalid_asset",
                f"Asset '{asset_id}' is a {kind} asset, so it cannot be used on the "
                f"{expected} track.",
                422,
            )


def _check_broll_overlaps(items: Sequence[dict]) -> None:
    """Refuse overlapping B-roll inserts so the picture has one clear meaning.

    Two overlays covering the same seconds would draw on top of each other, and
    which one wins would be an accident of ordering rather than an edit.
    """

    ordered = sorted(items, key=lambda item: float(item["start"]))
    for previous, current in zip(ordered, ordered[1:]):
        if float(current["start"]) < float(previous["end"]) - 0.01:
            raise ApiError(
                "invalid_timeline",
                "B-roll inserts cannot overlap: "
                f"{previous['start']:g}s → {previous['end']:g}s and "
                f"{current['start']:g}s → {current['end']:g}s share time. "
                "Trim one of them first.",
                422,
            )


def _assign_ids(items: Sequence[dict], prefix: str, existing: set[str]) -> list[dict]:
    """Give every item a stable, unique id, keeping ids the editor already had."""

    assigned: list[dict] = []
    used = set(existing)
    counter = 0

    for item in items:
        counter += 1
        item_id = str(item.get("id") or "").strip()
        if not item_id or item_id in used:
            while True:
                item_id = f"{prefix}-{counter}"
                counter += 1
                if item_id not in used:
                    break
        used.add(item_id)
        assigned.append({**item, "id": item_id})
    return assigned


def normalise_track_items(
    raw_items: Iterable[dict] | None,
    *,
    track: str,
    clip_duration: float,
    asset_ids: set[str],
    asset_durations: dict[str, float] | None = None,
) -> list[dict]:
    """Validate one track's items, in order.

    Refusing an item the renderer cannot honour is deliberate: an edit that is
    stored but never rendered would make the export disagree with the timeline.
    """

    items = [item for item in (raw_items or []) if isinstance(item, dict)]
    if track == "broll":
        prepared = [
            _validate_broll_item(item, clip_duration, asset_ids, asset_durations)
            for item in items
        ]
        _check_broll_overlaps(prepared)
        return _assign_ids(prepared, "broll", set())
    if track == "sfx":
        prepared = [_validate_sfx_item(item, clip_duration, asset_ids) for item in items]
        return _assign_ids(prepared, "sfx", set())
    if track == "transitions":
        prepared = [_validate_transition_item(item, clip_duration) for item in items]
        return _assign_ids(prepared, "transition", set())

    raise ApiError("invalid_timeline", f"Unknown track '{track}'.", 422)


# ------------------------------------------------------------ composed view


def build_timeline(
    project: dict,
    clip: dict,
    *,
    caption_style: dict,
    export: dict,
    plan: dict | None = None,
) -> dict[str, object]:
    """Compose the edit the interface shows for one clip."""

    start = float(clip.get("source_start") or 0.0)
    end = float(clip.get("source_end") or 0.0)
    stored = stored_timeline(project)

    notes: list[str] = []
    if plan and plan.get("notes"):
        notes.extend(str(note) for note in plan["notes"])

    return {
        "version": TIMELINE_VERSION,
        "project_id": project.get("project_id"),
        "clip_id": clip.get("id"),
        "clip_title": clip.get("title"),
        "duration": round(max(0.0, end - start), 3),
        "video": {
            "source_start": round(start, 3),
            "source_end": round(end, 3),
            "duration": round(max(0.0, end - start), 3),
            "fps": export.get("fps", DEFAULT_FPS),
            "width": export.get("width"),
            "height": export.get("height"),
            "aspect_ratio": export.get("aspect_ratio"),
        },
        "captions": {
            "enabled": export.get("caption_mode") != "none",
            "caption_mode": export.get("caption_mode"),
            **caption_style,
        },
        "broll": stored["broll"],
        "sfx": stored["sfx"],
        "transitions": stored["transitions"],
        "render": {
            key: export.get(key)
            for key in (
                "aspect_ratio",
                "resolution",
                "width",
                "height",
                "format",
                "fps",
                "caption_mode",
                "write_srt",
                "upscaled",
            )
        },
        "tracks_not_rendered": dict(UNRENDERED_TRACKS),
        "notes": notes,
        "updated_at": stored.get("updated_at"),
    }


def apply_timeline_patch(
    project: dict,
    clip: dict,
    patch: dict,
    *,
    current_style: dict,
    current_export: dict,
) -> dict[str, object]:
    """Validate an edited timeline and describe the values to write back.

    The returned dictionary is applied to the project document by the caller, so
    one save writes the whole edit and a rejected timeline changes nothing.
    """

    from .captions import normalise_style  # local import keeps the modules acyclic

    body = dict(patch or {})
    clip_duration = max(
        0.0,
        float(clip.get("source_end") or 0.0) - float(clip.get("source_start") or 0.0),
    )

    # --- the clip's own in/out points
    video = body.get("video") or {}
    if not isinstance(video, dict):
        raise ApiError("invalid_timeline", "video must be a JSON object.", 422)

    # The frame rate shown on the video track is the render's frame rate: one
    # value, stored once, so the inspector cannot disagree with the timeline.
    requested_fps = video.get("fps")

    start = float(clip.get("source_start") or 0.0)
    end = float(clip.get("source_end") or 0.0)
    if "source_start" in video:
        start = _number(video.get("source_start"), "source_start")
    if "source_end" in video:
        end = _number(video.get("source_end"), "source_end")
    if end <= start:
        raise ApiError(
            "invalid_timeline",
            f"The clip must end after it starts (got {start:g}s → {end:g}s).",
            422,
        )

    source_duration = float((project.get("source_metadata") or {}).get("duration") or 0.0)
    if source_duration and end > source_duration + 0.01:
        raise ApiError(
            "invalid_timeline",
            f"The clip cannot end at {end:.2f}s because the source video is "
            f"{source_duration:.2f}s long.",
            422,
        )

    clip_duration = round(end - start, 3)

    # --- caption track
    captions = body.get("captions") or {}
    if not isinstance(captions, dict):
        raise ApiError("invalid_timeline", "captions must be a JSON object.", 422)
    style = normalise_style({**current_style, **{k: v for k, v in captions.items() if k != "enabled"}})
    caption_mode = str(current_export.get("caption_mode") or "burn")
    if "enabled" in captions:
        enabled = captions.get("enabled")
        if not isinstance(enabled, bool):
            raise ApiError(
                "invalid_timeline",
                f"captions.enabled must be true or false (got '{enabled}').",
                422,
            )
        caption_mode = "burn" if enabled else "none"

    # --- render settings
    render = body.get("render") or {}
    if not isinstance(render, dict):
        raise ApiError("invalid_timeline", "render must be a JSON object.", 422)
    export_patch = {
        k: v
        for k, v in render.items()
        if k in {"aspect_ratio", "resolution", "format", "caption_mode", "write_srt", "fps"}
    }
    export_patch["caption_mode"] = export_patch.get("caption_mode", caption_mode)
    fps_value = export_patch.pop("fps", None)
    if fps_value is None:
        fps_value = requested_fps
    export_patch["fps"] = normalise_fps(
        fps_value, int(current_export.get("fps") or DEFAULT_FPS)
    )

    # --- item tracks
    assets = [asset for asset in (project.get("assets") or []) if isinstance(asset, dict)]
    asset_ids = {str(asset.get("id")) for asset in assets if asset.get("id")}
    asset_kinds = {str(asset.get("id")): str(asset.get("kind")) for asset in assets}
    asset_durations = {
        str(asset.get("id")): float(asset.get("duration") or 0.0) for asset in assets
    }
    asset_durations["source"] = float(
        (project.get("source_metadata") or {}).get("duration") or 0.0
    )
    tracks: dict[str, list] = {}
    for key in _TRACK_KEYS:
        if key in body:
            items = normalise_track_items(
                body.get(key),
                track=key,
                clip_duration=clip_duration,
                asset_ids=asset_ids,
                asset_durations=asset_durations,
            )
            # A track the renderer cannot honour yet is refused here, at the edit
            # boundary, so the timeline can never describe an export that would
            # silently leave the item out.
            if items and key in UNRENDERED_TRACKS:
                raise ApiError("unsupported_track", UNRENDERED_TRACKS[key], 422)
            if key == "broll":
                _check_asset_kinds(items, asset_kinds, field="source", expected="broll")
            if key == "sfx":
                _check_asset_kinds(items, asset_kinds, field="asset_id", expected="sfx")
            tracks[key] = items

    if not tracks:
        stored = stored_timeline(project)
        tracks = {key: stored[key] for key in _TRACK_KEYS}

    return {
        "source_start": round(start, 3),
        "source_end": round(end, 3),
        "caption_style": style,
        "export": export_patch,
        "tracks": tracks,
        "fps": export_patch["fps"],
        "updated_at": _utcnow(),
    }
