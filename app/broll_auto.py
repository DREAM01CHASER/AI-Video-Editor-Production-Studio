"""The "Add B-Rolls" pass: from the clip's transcript to placed inserts.

:mod:`app.broll` decides *where* an insert should go and how long it should run.
This module decides *what* goes there. It searches the stock provider for both
video and photos, ranks everything that comes back against the clip's own frame
and the insert's length, and reports the files that need downloading — reusing a
file the project already has instead of fetching the same asset twice.

Nothing here touches the network or the filesystem. The search and the download
are injected by the caller, so every decision this module makes can be tested, and
reasoned about, without a provider key.
"""

from __future__ import annotations

from typing import Callable, Iterable, Sequence

#: One search, as the caller provides it: a query in, normalised stock metadata out.
SearchFn = Callable[[str], list[dict]]

#: Ranking weights, kept in one place so the order is explainable rather than
#: a pile of magic numbers buried in a comparison.
W_ORIENTATION = 4.0
W_RESOLUTION = 2.0
W_KEYWORDS = 3.0
W_DURATION = 3.0
P_ASPECT = 2.0
P_REPEAT = 100.0

#: Motion is worth a little extra. This is a video edit, so a clip that fits the
#: moment is preferred over an equally suitable still — but not so strongly that a
#: well-matched photo loses to an unrelated clip. Without this, a portrait photo
#: wins every insert and the clip becomes a slideshow.
W_MOTION = 0.75


def _number(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def aspect_of(width: object, height: object) -> float:
    """Width divided by height, with a safe fallback for an unreadable size."""

    wide, tall = _number(width), _number(height)
    if wide <= 0 or tall <= 0:
        return 0.0
    return wide / tall


def same_orientation(candidate_aspect: float, box_aspect: float) -> bool:
    """Whether two sizes are both portrait, both landscape, or both square."""

    if candidate_aspect <= 0 or box_aspect <= 0:
        return False
    return (candidate_aspect < 1.0) == (box_aspect < 1.0)


def keyword_hits(candidate: dict, keywords: Sequence[str]) -> int:
    """How many of the spoken keywords appear in a candidate's own description."""

    haystack = " ".join(
        [
            str(candidate.get("alt") or ""),
            str(candidate.get("photographer") or ""),
        ]
    ).lower()
    return sum(1 for word in keywords if word and str(word).lower() in haystack)


def duration_fit(candidate: dict, wanted_seconds: float) -> float:
    """How well a clip's length matches the interval that needs filling.

    A clip at least as long as the interval can be shown without looping; a much
    longer one wastes bandwidth and is usually unrelated to the moment. A still
    image has no duration at all, which is not a problem: the interval decides.
    """

    if str(candidate.get("media_type")) == "photo":
        return 1.0
    length = _number(candidate.get("duration"))
    if wanted_seconds <= 0 or length <= 0:
        return 0.5
    if length >= wanted_seconds:
        return 1.0 if length <= wanted_seconds * 2.5 else 0.6
    return 0.2


def score_candidate(
    candidate: dict,
    *,
    box_width: int,
    box_height: int,
    wanted_seconds: float = 0.0,
    used_ids: Iterable[str] = (),
    keywords: Sequence[str] = (),
) -> float:
    """Score one stock result for one insert. Higher is better."""

    box_aspect = aspect_of(box_width, box_height)
    candidate_aspect = aspect_of(candidate.get("width"), candidate.get("height"))

    score = 0.0
    if same_orientation(candidate_aspect, box_aspect):
        score += W_ORIENTATION
    if candidate_aspect > 0 and box_aspect > 0:
        # A matching shape crops less away than a very different one.
        score -= P_ASPECT * min(1.0, abs(candidate_aspect - box_aspect))

    try:
        # Covering the output means the insert is never upscaled.
        if float(candidate.get("width") or 0) >= box_width:
            score += W_RESOLUTION
    except (TypeError, ValueError):
        pass

    score += W_KEYWORDS * keyword_hits(candidate, keywords)

    fit = duration_fit(candidate, wanted_seconds)
    score += W_DURATION * fit
    if str(candidate.get("media_type")) != "photo" and fit >= 1.0:
        # A moving cutaway that covers the interval outright.
        score += W_MOTION

    if candidate_key(candidate) in set(used_ids):
        score -= P_REPEAT
    return round(score, 4)


def candidate_key(candidate: dict) -> str:
    """A stable identity for one provider asset, photos and videos alike."""

    return f"{candidate.get('provider') or 'stock'}:{candidate.get('media_type') or 'video'}:{candidate.get('id')}"


def rank_candidates(
    candidates: Sequence[dict],
    *,
    box_width: int,
    box_height: int,
    wanted_seconds: float = 0.0,
    used_ids: Iterable[str] = (),
    keywords: Sequence[str] = (),
) -> list[dict]:
    """Order stock results best-first for one insert.

    Ties keep the provider's own relevance order, because that order is the only
    other signal available and re-sorting equal scores arbitrarily would make the
    choice look random.
    """

    used = set(str(item) for item in used_ids)
    scored = [
        (
            -score_candidate(
                candidate,
                box_width=box_width,
                box_height=box_height,
                wanted_seconds=wanted_seconds,
                used_ids=used,
                keywords=keywords,
            ),
            position,
            candidate,
        )
        for position, candidate in enumerate(candidates)
        if isinstance(candidate, dict) and candidate.get("id")
    ]
    scored.sort(key=lambda entry: (entry[0], entry[1]))
    return [dict(entry[2]) for entry in scored]


def describe_choice(
    candidate: dict, *, box_width: int, box_height: int, wanted_seconds: float
) -> str:
    """A short, honest reason for the interface: what was chosen and why."""

    media = "photo" if str(candidate.get("media_type")) == "photo" else "video"
    width = int(_number(candidate.get("width")))
    height = int(_number(candidate.get("height")))
    shape = "portrait" if aspect_of(width, height) < 1 else "landscape"
    length = _number(candidate.get("duration"))

    if media == "photo":
        fit = (
            f"a {shape} photo ({width}×{height}) filling the "
            f"{box_width}×{box_height} frame"
        )
    else:
        fit = (
            f"a {shape} {length:g}s clip ({width}×{height}) trimmed to "
            f"{wanted_seconds:g}s"
        )
    return f"Chosen: {fit}."


def plan_fetches(
    items: Sequence[dict],
    *,
    search: SearchFn,
    box_width: int,
    box_height: int,
    used_ids: Iterable[str] = (),
    per_query: int = 6,
) -> dict[str, object]:
    """Decide which online insert would download which provider file.

    Each online proposal is searched once — never once per word — and the best
    result that has not already been used wins. A query that fails is recorded and
    the rest continue, so one unlucky topic cannot empty the whole clip.
    """

    avoid = {str(item) for item in used_ids}
    taken: list[str] = []
    fetches: list[dict] = []
    skipped: list[dict] = []
    errors: list[str] = []

    for position, item in enumerate(items, start=1):
        source = item.get("source") or {}
        kind = str(source.get("type") or "")
        keywords = [str(word) for word in item.get("keywords") or []]
        wanted = max(0.0, _number(item.get("duration")))
        index = int(item.get("index") or position)

        if kind == "local":
            skipped.append({"index": index, "reason": "Already matched to a local asset."})
            continue
        if kind != "online":
            skipped.append(
                {
                    "index": index,
                    "reason": str(source.get("label") or "No source could be resolved."),
                }
            )
            continue

        query = str(item.get("query") or source.get("query") or "").strip()
        if not query:
            skipped.append({"index": index, "reason": "The proposal had no search query."})
            continue

        try:
            results = list(search(query))
        except Exception as exc:  # noqa: BLE001 - one bad query must not stop the pass
            errors.append(f"“{query}”: {exc}")
            skipped.append({"index": index, "reason": f"The stock search failed ({exc})."})
            continue

        ranked = rank_candidates(
            results[: max(1, per_query)],
            box_width=box_width,
            box_height=box_height,
            wanted_seconds=wanted,
            used_ids=[*avoid, *taken],
            keywords=keywords,
        )
        # The same asset twice in one pass reads as a mistake, so a candidate that
        # is already spoken for is skipped even when it is the only thing on offer.
        choice = next(
            (candidate for candidate in ranked if candidate_key(candidate) not in taken),
            None,
        )
        if choice is None:
            skipped.append(
                {
                    "index": index,
                    "reason": (
                        f"The only result for “{query}” is already used in this clip."
                        if ranked
                        else f"No stock result for “{query}” was usable as a cutaway."
                    ),
                }
            )
            continue

        taken.append(candidate_key(choice))
        fetches.append(
            {
                "index": index,
                "start": item.get("start"),
                "end": item.get("end"),
                "duration": item.get("duration"),
                "query": query,
                "keywords": keywords,
                "candidate": choice,
                "reason": describe_choice(
                    choice,
                    box_width=box_width,
                    box_height=box_height,
                    wanted_seconds=wanted,
                ),
            }
        )

    return {
        "fetches": fetches,
        "skipped": skipped,
        "errors": errors,
        "used_ids": [*avoid, *taken],
    }


def plan_note(plan: dict, *, matched_locally: int = 0) -> str:
    """One sentence describing what the pass decided, for the interface."""

    fetches = len(plan.get("fetches") or [])
    skipped = len(plan.get("skipped") or [])
    parts: list[str] = []
    if matched_locally:
        parts.append(f"{matched_locally} insert(s) use your own library")
    if fetches:
        parts.append(f"{fetches} stock file(s) will be downloaded")
    if skipped:
        parts.append(f"{skipped} proposal(s) were skipped")
    if not parts:
        return "Nothing to add: no sentence in this clip had something to show."
    return "; ".join(parts) + "."
