"""Ask the configured model *where the picture should change*, and what to show.

:mod:`app.broll` decides where an insert goes from the transcript's own words, and
:mod:`app.broll_auto` decides which stock file fills it. This module sits between
them and is the intelligent pass ``FEATURES.md`` describes: the model reads the
selected clip's transcript and names the few moments that are worth illustrating,
with a *visual* search query rather than the sentence's keywords.

Three constraints keep it safe, and they are the same ones :mod:`app.llm` follows
for clip labels:

* **The transcript is the only input.** The model sees the clip's own segments and
  the clip's length; it cannot invent a moment outside the clip.
* **Every time it returns is validated.** A moment is clamped inside the clip,
  padded to a readable minimum, trimmed to the cutaway maximum, and dropped when
  it cannot be placed — so a hallucinated range can never reach the timeline.
* **Failure is a fallback, not an error.** No key, a network error, a non-200, a
  body that is not JSON, a JSON body with no usable moments — each returns an
  empty plan with a plain-language reason, and the caller keeps the transparent
  keyword result.

Times are **clip-relative** (0 = the clip's first frame) because that is what a
timeline B-roll item measures, and the clip's absolute position in the source is
only sent as context.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Sequence

from .broll import MAX_ITEM_SECONDS, MAX_ITEMS, MIN_GAP_SECONDS, MIN_ITEM_SECONDS
from .broll import overlap as overlaps
from .llm import LlmSettings, Opener, extract_json_object
from .stock import normalise_query

#: A search query the provider can actually use: a short phrase, never a sentence.
MIN_QUERY_CHARACTERS = 2
MAX_QUERY_CHARACTERS = 80

#: How much transcript is sent. A clip is a moment, not a whole video, so this is
#: generous while still refusing to ship an entire interview to a third party.
MAX_TRANSCRIPT_CHARACTERS = 6000

#: The provider's own vocabulary for a still image, mapped to ours.
ASSET_TYPES = {
    "video": "video",
    "clip": "video",
    "footage": "video",
    "image": "photo",
    "photo": "photo",
    "still": "photo",
    "picture": "photo",
}

MAX_REASON_CHARACTERS = 240

SYSTEM_PROMPT = (
    "You plan cutaway visuals for a short-form vertical video edit. You are given "
    "the transcript of one clip. Choose the few moments that are worth illustrating "
    "with stock footage or a still photo, and describe each with a specific visual "
    "search query. Reply with JSON only, in the exact shape asked for. Never invent "
    "words the speaker did not say, and never place a moment outside the clip."
)


def _number(value: object) -> float | None:
    """A float, or None when the value is not a usable number."""

    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def clip_sentences(
    segments: Sequence[dict], *, clip_start: float, clip_end: float
) -> list[dict]:
    """The transcript sentences inside the clip, with their clip-relative times.

    A sentence that starts just before the cut still counts, because the words are
    spoken while the clip is playing; it is trimmed to the clip's own window.
    """

    sentences: list[dict] = []
    for segment in segments or []:
        if not isinstance(segment, dict):
            continue
        start = _number(segment.get("start"))
        end = _number(segment.get("end"))
        if start is None or end is None or end <= start:
            continue
        if end <= clip_start or start >= clip_end:
            continue

        text = " ".join(str(segment.get("text") or "").split())
        if not text:
            continue

        visible_start = max(start, clip_start)
        visible_end = min(end, clip_end)
        sentences.append(
            {
                "start": round(visible_start - clip_start, 3),
                "end": round(visible_end - clip_start, 3),
                "text": text,
            }
        )
    sentences.sort(key=lambda item: item["start"])
    return sentences


def build_prompt(
    sentences: Sequence[dict], *, clip_duration: float, max_items: int
) -> str:
    """Describe the clip and the rules, in the model's own terms."""

    lines: list[str] = []
    used = 0
    for sentence in sentences:
        line = f"[{sentence['start']:.1f}s-{sentence['end']:.1f}s] {sentence['text']}"
        if used + len(line) > MAX_TRANSCRIPT_CHARACTERS:
            lines.append("… (the rest of the clip is omitted)")
            break
        lines.append(line)
        used += len(line)

    return (
        "Transcript of the selected clip. Times are seconds from the clip's first "
        "frame; the clip runs from 0.0s to "
        f"{clip_duration:.1f}s.\n\n"
        + "\n".join(lines)
        + "\n\n"
        f"Choose at most {max_items} moments in this clip worth illustrating with "
        "stock footage or a still photo, and no more than one per idea.\n"
        "Rules:\n"
        "- Only pick a moment that describes something visible: a place, an object, "
        "a person, or an action. Skip greetings, opinions, filler, and anything "
        "abstract.\n"
        "- Do not pick one moment per sentence and do not space them at fixed "
        "intervals. Pick the few that matter most.\n"
        "- Give each moment a specific visual search query of 2 to 6 plain English "
        'words, such as "person training alone in gym" rather than "motivation".\n'
        "- Use type \"image\" for an object, document, place, or diagram; use "
        "\"video\" for movement, a scene, or a person doing something.\n"
        f"- Each moment runs between {MIN_ITEM_SECONDS:g}s and "
        f"{MAX_ITEM_SECONDS:g}s and must stay inside the clip.\n\n"
        "Reply with JSON only: {\"moments\":[{\"start\":1.5,\"end\":4.0,"
        "\"query\":\"person training alone in gym\",\"reason\":\"why this moment "
        "suits a cutaway\",\"type\":\"video\"}]}"
    )


def parse_moments(
    body: bytes | str, *, clip_duration: float, max_items: int
) -> list[dict]:
    """Validate a model reply into clip-relative visual moments.

    Anything unusable is dropped rather than repaired into something the speaker
    never said: a bad time, a missing query, a moment outside the clip. What
    survives is ordered, spaced, and capped, so the caller can place it directly.
    """

    if clip_duration < MIN_ITEM_SECONDS or max_items <= 0:
        return []

    if isinstance(body, bytes):
        try:
            payload = json.loads(body.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, TypeError, AttributeError):
            return []
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, AttributeError):
            return []
        data = extract_json_object(content if isinstance(content, str) else "")
    else:
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            return []
        # A bare list is accepted so a model that answers with just the moments
        # (with or without the wrapper object) still produces a usable plan.
        if isinstance(data, list):
            data = {"moments": data}

    if isinstance(data, dict) and not isinstance(data.get("moments"), list):
        # Some models name the field differently; accept the common paraphrases.
        for key in ("moments", "cuts", "b_roll", "broll", "placements", "items"):
            if isinstance(data.get(key), list):
                data = {"moments": data[key]}
                break

    raw = data.get("moments") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []

    accepted: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue

        start = _number(entry.get("start"))
        end = _number(entry.get("end"))
        if start is None or end is None:
            continue

        query = _clean_query(entry.get("query") or entry.get("search_query"))
        if not query:
            continue

        # Clamp inside the clip, then give the moment a readable length.
        start = min(max(0.0, start), clip_duration)
        end = min(max(0.0, end), clip_duration)
        if end <= start:
            end = start + MIN_ITEM_SECONDS
        if end > clip_duration:
            # Slide the moment back rather than losing it at the clip's end.
            start, end = max(0.0, clip_duration - MIN_ITEM_SECONDS), clip_duration
        if end - start > MAX_ITEM_SECONDS:
            end = start + MAX_ITEM_SECONDS
        if end - start < MIN_ITEM_SECONDS:
            start = max(0.0, end - MIN_ITEM_SECONDS)

        start, end = round(start, 3), round(end, 3)
        if end - start < MIN_ITEM_SECONDS or end > clip_duration + 1e-6:
            continue

        media_type = ASSET_TYPES.get(
            str(entry.get("type") or entry.get("media_type") or "video").strip().lower(),
            "video",
        )
        reason = " ".join(str(entry.get("reason") or "").split())[:MAX_REASON_CHARACTERS]

        accepted.append(
            {
                "start": start,
                "end": end,
                "query": query,
                "media_type": media_type,
                "reason": reason,
            }
        )

    accepted.sort(key=lambda item: item["start"])

    # Spaced apart, so the model cannot turn the clip into a slideshow, and capped
    # to what the user asked for.
    chosen: list[dict] = []
    for moment in accepted:
        if len(chosen) >= max_items:
            break
        if any(
            overlaps(moment["start"], moment["end"], item["start"], item["end"], MIN_GAP_SECONDS)
            for item in chosen
        ):
            continue
        chosen.append(moment)
    return chosen


def _clean_query(value: object) -> str:
    """A provider-ready query, or an empty string when there is nothing to search."""

    text = normalise_query(value)
    if len(text) < MIN_QUERY_CHARACTERS:
        return ""
    if len(text) > MAX_QUERY_CHARACTERS:
        text = text[:MAX_QUERY_CHARACTERS].rsplit(" ", 1)[0].strip() or text[:MAX_QUERY_CHARACTERS]
    if not any(character.isalpha() for character in text):
        return ""
    return text


def request_moments(
    settings: LlmSettings,
    sentences: Sequence[dict],
    *,
    clip_duration: float,
    max_items: int,
    opener: Opener | None = None,
) -> dict[str, object]:
    """Ask the configured endpoint for the clip's visual moments.

    Returns a plan that always says what happened: the moments when the model
    answered usefully, an empty list plus a reason when it did not.
    """

    unavailable = {"planner": "unavailable", "moments": [], "note": ""}

    if not settings.enabled:
        return {**unavailable, "note": "No AI model is configured."}
    if not sentences:
        return {
            **unavailable,
            "note": "This clip has no transcript inside it, so there is nothing for a model to read.",
        }
    if clip_duration < MIN_ITEM_SECONDS:
        return {
            **unavailable,
            "note": "This clip is too short for a cutaway of its own.",
        }

    body = json.dumps(
        {
            "model": settings.model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": build_prompt(
                        sentences, clip_duration=clip_duration, max_items=max_items
                    ),
                },
            ],
        }
    ).encode("utf-8")

    request = urllib.request.Request(  # noqa: S310
        f"{settings.base_url.rstrip('/')}/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {str(settings.api_key).strip()}",
        },
        method="POST",
    )

    try:
        status, response_body = (opener or _urlopen)(request, settings.timeout_seconds)
    except Exception as exc:  # noqa: BLE001 - an optional extra must never break the flow
        return {
            **unavailable,
            "note": f"The AI provider could not be reached ({type(exc).__name__}).",
        }

    if status != 200:
        return {
            **unavailable,
            "note": f"The AI provider refused the request (HTTP {status}).",
        }

    moments = parse_moments(
        response_body, clip_duration=clip_duration, max_items=max_items
    )
    if not moments:
        return {
            **unavailable,
            "note": "The AI model returned no usable visual moment for this clip.",
        }
    return {"planner": "llm", "moments": moments, "note": ""}


def plan_moments(
    settings: LlmSettings,
    segments: Sequence[dict],
    *,
    clip_start: float,
    clip_end: float,
    max_items: int = MAX_ITEMS,
    opener: Opener | None = None,
) -> dict[str, object]:
    """The whole AI pass for one clip: its transcript in, visual moments out."""

    clip_duration = max(0.0, float(clip_end) - float(clip_start))
    sentences = clip_sentences(segments, clip_start=clip_start, clip_end=clip_end)
    plan = request_moments(
        settings,
        sentences,
        clip_duration=clip_duration,
        max_items=max_items,
        opener=opener,
    )
    plan["sentences"] = len(sentences)
    plan["clip_duration"] = round(clip_duration, 3)

    if plan["planner"] == "llm":
        plan["note"] = (
            f"The AI model read {len(sentences)} transcript sentence(s) of this clip "
            f"and chose {len(plan['moments'])} moment(s) worth illustrating."
        )
    return plan


def _urlopen(request: urllib.request.Request, timeout: int) -> tuple[int, bytes]:
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return int(response.status), response.read()
