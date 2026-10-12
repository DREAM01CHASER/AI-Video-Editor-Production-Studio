"""Clip transitions, drawn with FFmpeg filters rather than bundled assets.

``TECH_STACK.md`` warns against bundling transition packs in Git, and ``FEATURES.md``
(Milestone 9) asks for transitions *only where they improve the edit* and only
when licensing allows. Every transition here is therefore **synthesised by FFmpeg
itself**: no asset is downloaded, committed, or redistributed, so there is no
licence question to answer at all.

Three kinds are offered, each a different way for a clip to begin or end:

* ``fade``       — dissolve to or from black.
* ``slide-left`` — the picture slides in from the right, or out to the right.
* ``wipe-left``  — the picture is revealed from the left edge, or hidden to it.

Transitions happen at the clip's own start or end. There is no second clip to
dissolve with, so "transition" here means *how this clip arrives or leaves*, which
is what a short-form clip actually needs and keeps the timing unambiguous.
"""

from __future__ import annotations

from typing import Sequence

from .timeline import MAX_TRANSITION_SECONDS

#: A transition longer than this stops reading as one; a short clip cannot hold
#: a transition longer than half of itself either.
MAX_SECONDS = MAX_TRANSITION_SECONDS

#: Plain-language names, so the interface never has to show a raw filter kind.
_LABELS = {
    "fade": "Fade",
    "slide-left": "Slide",
    "wipe-left": "Wipe",
}


def _seconds(value: object) -> float:
    """Read a stored duration, treating anything unreadable as no transition."""

    try:
        return max(0.0, float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def normalise_duration(value: object, clip_duration: float) -> float:
    """Clamp a requested transition to something that will actually render."""

    try:
        duration = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0

    limit = min(MAX_SECONDS, max(0.0, clip_duration / 2))
    return round(min(max(duration, 0.0), limit), 3)


def transition_filter(
    kind: str,
    *,
    at: str,
    duration: float,
    width: int,
    height: int,
    clip_duration: float,
) -> str | None:
    """The filter fragment for one transition, or None when it does nothing.

    A zero-length transition would be a plain cut, which is what happens when no
    transition is set, so returning None keeps the two cases identical rather
    than adding a no-op to the graph.
    """

    length = normalise_duration(duration, clip_duration)
    if length <= 0 or width <= 0 or height <= 0:
        return None
    if clip_duration <= 0 or length * 2 >= clip_duration:
        # A transition at least as long as the clip would hide all of it.
        return None

    started = str(kind or "").strip().lower()
    where = str(at or "start").strip().lower()

    # The moment this transition begins, as an offset from the clip's start.
    anchor = 0.0 if where != "end" else max(0.0, float(clip_duration) - length)

    if started == "fade":
        if where == "end":
            return f"fade=t=out:st={anchor:.3f}:d={length:.3f}"
        return f"fade=t=in:st=0:d={length:.3f}"

    if started == "slide-left":
        # Pad black beside the picture, then slide a window across it. At the
        # start the window begins on the black and lands on the picture; at the
        # end it does the reverse.
        canvas = width * 2
        if where == "end":
            x = f"{width}*max(0,min(1,(t-{anchor:.3f})/{length:.3f}))"
            return (
                f"pad={canvas}:{height}:0:0:color=black,"
                f"crop={width}:{height}:x='{x}':y=0,setsar=1"
            )
        x = f"{width}*(1-max(0,min(1,t/{length:.3f})))"
        return (
            f"pad={canvas}:{height}:{width}:0:color=black,"
            f"crop={width}:{height}:x='{x}':y=0,setsar=1"
        )

    if started == "wipe-left":
        # A black bar that shrinks to reveal the picture, or grows to hide it.
        if where == "end":
            x = f"{width}*max(0,min(1,(t-{anchor:.3f})/{length:.3f}))"
            return f"drawbox=x='{x}':y=0:w={width}:h={height}:color=black:t=fill"
        x = f"{width}*max(0,min(1,1-t/{length:.3f}))"
        return f"drawbox=x='{x}':y=0:w={width}:h={height}:color=black:t=fill"

    return None


def build_transition_chain(
    transitions: Sequence[dict],
    *,
    width: int,
    height: int,
    clip_duration: float,
) -> str:
    """All the clip's transitions as one filter fragment, in a fixed order.

    Start transitions are applied before end transitions so the result does not
    depend on the order the items happen to be stored in.
    """

    ordered = sorted(
        transitions,
        key=lambda item: (
            0 if str(item.get("at") or "start") != "end" else 1,
            -normalise_duration(item.get("duration"), clip_duration),
            str(item.get("id") or ""),
        ),
    )

    filters: list[str] = []
    seen: set[tuple[str, str]] = set()
    for item in ordered:
        at = str(item.get("at") or "start").strip().lower()
        kind = str(item.get("kind") or "").strip().lower()
        # Two transitions of the same kind at the same edge would draw on top of
        # each other; the first one wins and the rest are ignored.
        if (kind, at) in seen:
            continue
        fragment = transition_filter(
            kind,
            at=at,
            duration=item.get("duration", 0.5),
            width=width,
            height=height,
            clip_duration=clip_duration,
        )
        if fragment:
            seen.add((kind, at))
            filters.append(fragment)

    return ",".join(filters)


def describe(transitions: Sequence[dict]) -> str:
    """A plain-language description for the interface and the render notes."""

    if not transitions:
        return ""
    parts = [
        f"{_LABELS.get(str(item.get('kind')), item.get('kind'))} "
        f"{'in' if str(item.get('at') or 'start') != 'end' else 'out'} "
        f"({_seconds(item.get('duration')):g}s)"
        for item in transitions
    ]
    return "Transitions: " + ", ".join(parts) + "."
