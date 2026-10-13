"""Automatic B-roll suggestion: where it goes, for how long, and from where.

``FEATURES.md`` (Milestone 7/8) asks B-roll to *illustrate* the conversation rather
than appear at random, and ``PROJECT.md`` insists every automatic decision stays
editable. This module produces those decisions as plain data — a list of
placements with a start, an end, a reason, and a duration reason — so the
interface can show them, the user can change them, and the same validated timeline
``PATCH`` places them.

Three source modes are supported, matching the three the interface offers:

* ``local``  — only the user's own imported B-roll.
* ``online`` — only the configured stock provider (Pexels).
* ``auto``   — a local asset when one matches the words, otherwise an online query.

Nothing is downloaded here. An online proposal carries the query; a file is fetched
only when the user accepts the proposal, through the existing stock import path.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from .errors import ApiError
from .stock import derive_keywords

#: The source modes the interface offers.
SOURCE_MODES = ("local", "online", "auto")

#: How long an insert may run. Long enough to read, short enough to be a cutaway
#: rather than a second edit.
MIN_ITEM_SECONDS = 1.5
MAX_ITEM_SECONDS = 4.0

#: A short insert is a glance; the cap is what stops a clip becoming a slideshow.
MAX_ITEMS = 5
DEFAULT_MAX_ITEMS = 3

#: Inserts may cover at most this share of the clip. The rest keeps the speaker on
#: screen, which is what "keep the speaker visible when the visual adds little"
#: means in practice.
MAX_COVERAGE = 0.4

#: Two inserts closer than this read as one jumpy sequence.
MIN_GAP_SECONDS = 0.5

#: A segment shorter than this has no room for a cutaway of its own.
MIN_SEGMENT_SECONDS = 1.2

#: The fraction of a spoken segment an insert covers. Covering the whole sentence
#: hides the speaker for its entire length; a little under it keeps both.
COVERAGE_OF_SEGMENT = 0.8


def normalise_mode(value: object) -> str:
    """Validate one of the three source modes."""

    mode = str(value or "auto").strip().lower()
    if mode not in SOURCE_MODES:
        raise ApiError(
            "invalid_broll_mode",
            f"mode must be one of {', '.join(SOURCE_MODES)} (got '{value}').",
            422,
        )
    return mode


def normalise_max_items(value: object, fallback: int = DEFAULT_MAX_ITEMS) -> int:
    if value is None or value == "":
        return fallback
    try:
        count = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ApiError(
            "invalid_broll_request", f"max_items must be a number (got '{value}').", 422
        ) from exc
    return max(0, min(MAX_ITEMS, count))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def item_duration(segment_seconds: float) -> float:
    """How long an insert should run for a segment of this length.

    The insert is a fraction of the spoken turn, bounded at both ends: a glance for
    a short line, a cutaway for a longer one, and never longer than the cap however
    long the sentence runs.
    """

    wanted = segment_seconds * COVERAGE_OF_SEGMENT
    return round(_clamp(wanted, MIN_ITEM_SECONDS, MAX_ITEM_SECONDS), 3)


def _candidates(
    segments: Sequence[dict], clip_start: float, clip_end: float
) -> list[dict]:
    """Transcript moments inside the clip that could carry a cutaway."""

    found: list[dict] = []
    for segment in segments or []:
        if not isinstance(segment, dict):
            continue
        try:
            start = float(segment.get("start") or 0.0)
            end = float(segment.get("end") or 0.0)
        except (TypeError, ValueError):
            continue
        if end <= clip_start or start >= clip_end:
            continue

        text = " ".join(str(segment.get("text") or "").split())
        if not text:
            continue

        visible_start = max(start, clip_start)
        visible_end = min(end, clip_end)
        if visible_end - visible_start < MIN_SEGMENT_SECONDS:
            continue

        keywords = derive_keywords(text)
        if not keywords:
            # Nothing concrete to illustrate, so a cutaway here would be decoration.
            continue

        found.append(
            {
                "start": visible_start,
                "end": visible_end,
                "text": text,
                "keywords": keywords,
            }
        )
    return found


def _overlaps(start: float, end: float, chosen: Iterable[dict]) -> bool:
    for item in chosen:
        if start < float(item["end"]) + MIN_GAP_SECONDS and end > float(item["start"]) - MIN_GAP_SECONDS:
            return True
    return False


def _local_match(assets: Sequence[dict], keywords: Sequence[str]) -> dict | None:
    """The best B-roll asset whose label or tags match the spoken words."""

    best: tuple[int, dict] | None = None
    for asset in assets or []:
        if not isinstance(asset, dict) or str(asset.get("kind")) != "broll":
            continue
        haystack = " ".join(
            [str(asset.get("label") or ""), " ".join(str(tag) for tag in asset.get("tags") or [])]
        ).lower()
        score = sum(1 for keyword in keywords if keyword in haystack)
        if score and (best is None or score > best[0]):
            best = (score, asset)
    return best[1] if best else None


def propose_broll(
    segments: Sequence[dict],
    *,
    clip_start: float,
    clip_end: float,
    assets: Sequence[dict] = (),
    mode: str = "auto",
    max_items: int = DEFAULT_MAX_ITEMS,
) -> dict[str, object]:
    """Propose editable B-roll placements for one clip.

    Returns a dictionary with the chosen ``items`` and a plain-language ``note``
    that always says what happened — including why nothing could be placed, so a
    silent "no B-roll" is not possible.
    """

    chosen_mode = normalise_mode(mode)
    limit = normalise_max_items(max_items)
    clip_duration = max(0.0, float(clip_end) - float(clip_start))

    result: dict[str, object] = {
        "mode": chosen_mode,
        "clip_start": round(float(clip_start), 3),
        "clip_end": round(float(clip_end), 3),
        "clip_duration": round(clip_duration, 3),
        "items": [],
        "note": None,
    }

    if clip_duration <= 0:
        result["note"] = "The clip has no length yet, so there is nowhere to place an insert."
        return result

    candidates = _candidates(segments, clip_start, clip_end)
    if not candidates:
        result["note"] = (
            "No transcribed sentence inside this clip has enough concrete words to "
            "illustrate, so no B-roll was proposed. Keep the speaker on screen or "
            "import your own clip and place it by hand."
        )
        return result

    # The strongest sentences win: more usable keywords first, then the earliest,
    # so the choice is explainable rather than a hidden score.
    ranked = sorted(candidates, key=lambda item: (-len(item["keywords"]), item["start"]))

    budget = clip_duration * MAX_COVERAGE
    placed: list[dict] = []
    covered = 0.0

    for candidate in ranked:
        if len(placed) >= limit:
            break

        duration = item_duration(candidate["end"] - candidate["start"])
        end = candidate["start"] + duration
        if end > clip_end:
            end = clip_end
        start = round(end - duration, 3)
        if start < clip_start:
            start = round(clip_start, 3)
            end = round(min(clip_end, start + duration), 3)

        # One cutaway is worth having even on a short clip, where a strict coverage
        # cap would otherwise reject every insert. It is still kept under half the
        # clip so the speaker stays the main picture; later inserts obey the cap.
        if placed:
            if covered + (end - start) > budget + 1e-9:
                continue
        elif end - start > max(MIN_ITEM_SECONDS, clip_duration * 0.5):
            continue
        if _overlaps(start, end, placed):
            continue

        placed.append({"start": start, "end": end, "keywords": candidate["keywords"],
                       "text": candidate["text"]})
        covered += end - start

    placed.sort(key=lambda item: item["start"])

    items: list[dict] = []
    for index, item in enumerate(placed, start=1):
        duration = round(item["end"] - item["start"], 3)
        query = ", ".join(item["keywords"][:4])
        source = _resolve_source(chosen_mode, assets, item["keywords"], query)
        items.append(
            {
                "index": index,
                "start": round(item["start"], 3),
                "end": round(item["end"], 3),
                "duration": duration,
                "query": query,
                "keywords": item["keywords"][:6],
                "source": source,
                "reason": (
                    f"“{item['text'][:90]}…” is about {query}, so a cutaway here "
                    "shows what is being described."
                ),
                "duration_reason": (
                    f"{duration:g}s covers most of a {round(item['end'] - item['start'], 1)}s "
                    "spoken turn without hiding the speaker for the whole sentence."
                ),
            }
        )

    result["items"] = items
    if items:
        result["note"] = (
            f"Proposed {len(items)} B-roll insert(s) covering "
            f"{round(sum(float(i['duration']) for i in items), 1)}s of the "
            f"{round(clip_duration, 1)}s clip, leaving the speaker on screen the rest."
        )
    else:
        result["note"] = (
            "The sentences with something to show are too close together to space "
            "inserts apart, so none were proposed. Add one by hand for a specific "
            "moment."
        )
    return result


def _resolve_source(
    mode: str, assets: Sequence[dict], keywords: Sequence[str], query: str
) -> dict[str, object]:
    """Where the visual for one proposal would come from, without fetching it."""

    if mode in {"local", "auto"}:
        match = _local_match(assets, keywords)
        if match is not None:
            return {
                "type": "local",
                "asset_id": str(match.get("id")),
                "label": match.get("label"),
                "label_kind": "local library",
            }
        if mode == "local":
            return {
                "type": "none",
                "query": query,
                "label": f"No local B-roll matches “{query}”",
                "label_kind": "local library",
            }

    return {
        "type": "online",
        "provider": "pexels",
        "query": query,
        "label": f"Search stock for “{query}”",
        "label_kind": "stock",
    }


def build_broll_item(
    proposal: dict,
    *,
    asset_id: str,
    asset_duration: float,
    placement: str = "full",
    fit: str = "cover",
) -> dict[str, object]:
    """Turn an accepted proposal into a timeline B-roll item.

    The insert shows the start of the asset unless the asset is shorter than the
    interval, in which case the interval is shortened to the asset rather than
    freezing or blacking the frame.
    """

    start = round(float(proposal.get("start") or 0.0), 3)
    end = round(float(proposal.get("end") or 0.0), 3)
    if end <= start:
        raise ApiError(
            "invalid_broll_proposal", "A B-roll proposal must end after it starts.", 422
        )

    length = end - start
    if asset_duration and asset_duration < length:
        end = round(start + asset_duration, 3)

    return {
        "source": asset_id,
        "source_start": 0.0,
        "source_end": round(end - start, 3),
        "start": start,
        "end": end,
        "placement": placement,
        "fit": fit,
    }
