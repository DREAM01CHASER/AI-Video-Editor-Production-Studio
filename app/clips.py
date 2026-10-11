"""Candidate short-form clip detection from a transcript.

The MVP detects clips with a deterministic, inspectable heuristic instead of a
model, so it works with no API key, no download, and no network. Every score is
built from signals that are reported back to the UI, and the score is labelled as
an **estimate of self-containedness and information density** — never a promise
that a clip will go viral.

What the heuristic looks for (see ``FEATURES.md``):

* **Self-contained meaning** — a clip should start and end on sentence
  boundaries, so it does not open or close mid-thought.
* **Hook strength** — concrete numbers, questions, direct address, contrast
  words, and "there is a catch" markers near the opening.
* **Information density** — words per second inside a band where speech is
  neither padded nor rushed.
* **Duration fit** — short-form clips want roughly half a minute.

Candidates are selected for diversity (strongly overlapping windows are dropped so
the list is not five views of the same moment), and whatever overlap remains is
reported on the candidates themselves rather than hidden.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache

from .errors import ApiError

GENERATOR_HEURISTIC = "heuristic"
GENERATOR_LLM = "llm"

MIN_CLIPS = 3
MAX_CLIPS = 5

#: Clip length window in seconds. Windows are built between these bounds and the
#: duration signal peaks at the ideal length.
TARGET_MIN_SECONDS = 18.0
TARGET_IDEAL_SECONDS = 32.0
TARGET_MAX_SECONDS = 60.0

#: Comfortable speaking-rate band in words per second (~110-230 words/minute).
DENSITY_LOW = 1.8
DENSITY_HIGH = 3.8
DENSITY_FLOOR = 0.9
DENSITY_CEILING = 5.0

#: A new candidate is dropped when it overlaps an already-chosen clip by more
#: than this share of the shorter clip, to keep the list diverse.
MAX_SELECTION_OVERLAP = 0.4
#: Shared time below this share of the shorter clip is not worth warning about;
#: otherwise every pair of adjacent clips would be flagged and the flag would
#: stop meaning anything.
FLAG_OVERLAP_THRESHOLD = 0.1
NEAR_DUPLICATE_SIMILARITY = 0.78

#: Relative weight of each signal in the final score. They sum to 1.0.
SIGNAL_WEIGHTS = {
    "hook": 0.30,
    "self_contained": 0.25,
    "density": 0.20,
    "duration_fit": 0.15,
    "opening_boundary": 0.10,
}

#: Highest score a heuristic candidate may receive, so the UI can never show a
#: perfect 1.00 for a guess.
MAX_SCORE = 0.97

SENTENCE_ENDINGS = (".", "!", "?", "…", "。", "！", "？", "।", "॥")
_TRAILING_QUOTES = "\"'”’)]}"

#: (pattern, weight, plain-language label) for opening-hook markers.
HOOK_PATTERNS: tuple[tuple[re.Pattern[str], float, str], ...] = (
    (re.compile(r"\b\d[\d.,]*%?\b"), 1.0, "a concrete number"),
    (re.compile(r"\?"), 0.9, "a question"),
    (re.compile(r"\b(?:secret|mistake|myth|truth|problem|reason|trick|hack)\b", re.I), 0.9, "a curiosity word"),
    (re.compile(r"\b(?:never|always|best|worst|biggest|fastest|most|least|only|every)\b", re.I), 0.8, "a superlative"),
    (re.compile(r"\b(?:you|your|you're|yours)\b", re.I), 0.7, "direct address to the viewer"),
    (re.compile(r"\b(?:but|however|actually|instead|surprisingly|unfortunately)\b", re.I), 0.6, "a contrast word"),
    (re.compile(r"\b(?:because|so\s+that|which\s+means|that's\s+why|here's\s+why)\b", re.I), 0.5, "a causal link"),
)

#: Below this share of Latin-script words, the English-only hook and cue word
#: lists are treated as unavailable rather than reporting "no hook".
LATIN_CUES_MIN_SHARE = 0.5

#: Below this share of punctuated segments, cut points are treated as estimates
#: and the output says so instead of claiming complete sentences.
SENTENCE_PUNCTUATION_MIN_RATIO = 0.5

#: A segment is usually a whole clause, so once the title has this many words it
#: is readable and no further segment should be joined onto it.
TITLE_MIN_WORDS = 5

#: How many of the opening words the hook signal inspects.
HOOK_WORD_WINDOW = 30

#: Words that commonly open a new sentence or a new thought. Whisper sometimes
#: returns segments with no final punctuation at all, so punctuation alone is not
#: enough to find a safe cut; a following unit that opens with one of these reads
#: as a fresh start rather than a continuation of the previous clause.
DISCOURSE_STARTERS = frozenset(
    {
        "but", "however", "so", "now", "actually", "finally", "therefore",
        "instead", "meanwhile", "still", "also", "then", "first", "second",
        "third", "next", "let", "here", "why", "what", "how", "if",
        "because", "thanks", "welcome", "today", "pick", "record", "publish",
        "write", "fix", "stop", "remember",
    }
)

#: Two strong markers already read as a strong hook, so the score is normalised
#: against the two heaviest markers (a number plus a question or curiosity word)
#: rather than an arbitrary total.
STRONG_HOOK_WEIGHT = round(
    sum(sorted((weight for _, weight, _ in HOOK_PATTERNS), reverse=True)[:2]), 4
)

#: Used to tell whether the English-only cue words can say anything at all.
_LATIN_LETTER = re.compile(r"[A-Za-z]")

#: Unicode mark categories. A regex character class cannot cover these for every
#: script, and `\w` excludes them, which is why Devanagari vowel signs split
#: "हमारा" into "हम" + "र" and inflated every word count.
_COMBINING_MARKS = frozenset({"Mn", "Mc", "Me"})
_APOSTROPHES = frozenset({"'", "\u2019"})
_STOPWORDS = frozenset(
    "the a an and or but if then than that this these those of to in on at for with "
    "is are was were be been being it its as by from so we you your i my me not no".split()
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(text: object) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def is_sentence_end(text: str) -> bool:
    """True when the text reads as the end of a sentence."""

    return _clean(text).rstrip(_TRAILING_QUOTES).endswith(SENTENCE_ENDINGS)


def starts_new_thought(text: str) -> bool:
    """True when a segment opens with a word that usually begins a new sentence."""

    tokens = tokenize(text)
    return bool(tokens) and tokens[0] in DISCOURSE_STARTERS


def timecode(seconds: float) -> str:
    """Format seconds as ``M:SS`` for generated fallback titles."""

    total = max(0, int(round(seconds or 0)))
    return f"{total // 60}:{total % 60:02d}"


def _scan_tokens(text: str) -> tuple[str, ...]:
    """Split text into words, keeping combining marks attached to their base.

    Works for any script because it uses Unicode categories rather than a fixed
    character range: letters and digits start a word, and combining marks
    (Devanagari matras, Arabic diacritics, Latin accents, …) continue it.
    """

    tokens: list[str] = []
    current: list[str] = []

    for char in text:
        is_word_char = char.isalnum() or char == "_" or unicodedata.category(char) in _COMBINING_MARKS
        if is_word_char:
            current.append(char)
        elif char in _APOSTROPHES and current:
            current.append(char)
        elif current:
            tokens.append("".join(current).strip("'\u2019"))
            current = []

    if current:
        tokens.append("".join(current).strip("'\u2019"))

    return tuple(token for token in tokens if token)


@lru_cache(maxsize=8192)
def _cached_tokens(text: str) -> tuple[str, ...]:
    return _scan_tokens(text)


def tokenize(text: str) -> list[str]:
    """Lower-cased words in any script, with combining marks kept intact."""

    return list(_cached_tokens(_clean(text).lower()))


def latin_token_share(text: str) -> float:
    """Share of words written in the Latin script.

    The hook and discourse-cue word lists are English-only, so on a transcript in
    another script those signals cannot fire. Reporting the share lets the output
    say that instead of implying a clip has no hook.
    """

    tokens = tokenize(text)
    if not tokens:
        return 0.0
    return round(sum(1 for token in tokens if _LATIN_LETTER.search(token)) / len(tokens), 4)


def text_similarity(left: str, right: str) -> float:
    """Jaccard similarity over content words, used for near-duplicate detection."""

    left_tokens = {token for token in tokenize(left) if token not in _STOPWORDS}
    right_tokens = {token for token in tokenize(right) if token not in _STOPWORDS}
    if not left_tokens or not right_tokens:
        return 0.0
    return round(len(left_tokens & right_tokens) / len(left_tokens | right_tokens), 4)


def overlap_seconds(left: dict, right: dict) -> float:
    return max(
        0.0,
        min(left["source_end"], right["source_end"])
        - max(left["source_start"], right["source_start"]),
    )


def overlap_ratio(left: dict, right: dict) -> float:
    """Shared time as a share of the shorter clip."""

    shortest = min(
        left["source_end"] - left["source_start"], right["source_end"] - right["source_start"]
    )
    if shortest <= 0:
        return 0.0
    return round(overlap_seconds(left, right) / shortest, 4)


# ------------------------------------------------------------------- signals


def hook_score(text: str) -> tuple[float, list[str]]:
    """Score how strongly the opening grabs attention, plus the markers found."""

    words = _clean(text).split()
    opening = " ".join(words[:HOOK_WORD_WINDOW])
    if not opening:
        return 0.0, []

    labels: list[str] = []
    total = 0.0
    for pattern, weight, label in HOOK_PATTERNS:
        if pattern.search(opening):
            total += weight
            labels.append(label)

    return round(min(1.0, total / STRONG_HOOK_WEIGHT), 4), labels


def density_score(words_per_second: float) -> float:
    """Peak inside the comfortable band, tapering off for padded or rushed speech."""

    if words_per_second <= 0:
        return 0.0
    if DENSITY_LOW <= words_per_second <= DENSITY_HIGH:
        return 1.0
    if words_per_second < DENSITY_LOW:
        span = DENSITY_LOW - DENSITY_FLOOR
        return round(max(0.0, (words_per_second - DENSITY_FLOOR) / span), 4)
    span = DENSITY_CEILING - DENSITY_HIGH
    return round(max(0.0, (DENSITY_CEILING - words_per_second) / span), 4)


def duration_score(seconds: float) -> float:
    """Prefer clips near the ideal length, with a floor at the window bounds."""

    if seconds <= 0:
        return 0.0
    if abs(seconds - TARGET_IDEAL_SECONDS) <= 8.0:
        return 1.0
    if seconds < TARGET_IDEAL_SECONDS:
        span = TARGET_IDEAL_SECONDS - TARGET_MIN_SECONDS
        if span <= 0:
            return 1.0
        return round(0.4 + 0.6 * max(0.0, (seconds - TARGET_MIN_SECONDS) / span), 4)
    span = TARGET_MAX_SECONDS - TARGET_IDEAL_SECONDS
    if span <= 0:
        return 1.0
    return round(0.4 + 0.6 * max(0.0, (TARGET_MAX_SECONDS - seconds) / span), 4)


# ---------------------------------------------------------------------- units


@dataclass
class Unit:
    """One transcript segment, treated as an indivisible speech unit."""

    index: int
    start: float
    end: float
    text: str
    ends_sentence: bool
    starts_new_thought: bool = False

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def word_count(self) -> int:
        return len(tokenize(self.text))


def build_units(segments: list[dict]) -> list[Unit]:
    """Turn transcript segments into ordered, timed units.

    Segments with no usable text are dropped rather than being scored as empty
    speech. Timings are clamped so a malformed segment cannot invert a window.
    """

    units: list[Unit] = []
    for segment in segments or []:
        if not isinstance(segment, dict):
            continue
        text = _clean(segment.get("text"))
        if not text:
            continue
        try:
            start = max(0.0, float(segment.get("start", 0.0) or 0.0))
            end = max(start, float(segment.get("end", 0.0) or 0.0))
        except (TypeError, ValueError):
            continue
        units.append(
            Unit(
                index=len(units),
                start=round(start, 3),
                end=round(end, 3),
                text=text,
                ends_sentence=is_sentence_end(text),
                starts_new_thought=starts_new_thought(text),
            )
        )
    return units


def is_boundary_after(units: list[Unit], index: int) -> bool:
    """True when a clip may end after ``units[index]`` without cutting a thought.

    A closing punctuation mark is conclusive. When the transcript has none, a
    following unit that opens with a discourse word still marks a safe cut.
    """

    if not 0 <= index < len(units):
        return False
    if units[index].ends_sentence:
        return True
    following = units[index + 1] if index + 1 < len(units) else None
    return bool(following and following.starts_new_thought)


def punctuation_ratio(units: list[Unit]) -> float:
    """Share of segments that end with sentence punctuation.

    The cutoff for trusting cut points is proportional rather than "any", because
    a real Whisper transcript was observed with only 3 of 32 segments punctuated:
    a single stray "?" must not make unpunctuated edges look exact.
    """

    if not units:
        return 0.0
    return round(sum(1 for unit in units if unit.ends_sentence) / len(units), 4)


def candidate_windows(units: list[Unit]) -> list[tuple[int, int]]:
    """Every (first, last) unit range whose length is inside the clip window."""

    windows: list[tuple[int, int]] = []
    total = len(units)
    for first in range(total):
        for last in range(first, total):
            length = units[last].end - units[first].start
            if length >= TARGET_MIN_SECONDS:
                if length <= TARGET_MAX_SECONDS:
                    windows.append((first, last))
                break
    return windows


# ------------------------------------------------------------- presentation


def first_sentence(units: list[Unit], first: int, last: int) -> str:
    """The opening sentence of a window, used to derive a title."""

    parts: list[str] = []
    for unit in units[first : last + 1]:
        parts.append(unit.text)
        if unit.ends_sentence:
            break
        # Without punctuation, stop once the title is readable rather than running
        # the next clause onto it and producing one long run-on sentence.
        if len(tokenize(" ".join(parts))) >= TITLE_MIN_WORDS:
            break
    return _clean(" ".join(parts))


def make_title(text: str, start: float, *, limit: int = 72) -> str:
    """Derive a title from the words actually spoken, never invented."""

    cleaned = _clean(text).rstrip(_TRAILING_QUOTES).strip(" -:;,")
    # A title does not end in a full stop, but a "!" or "?" is meaningful and is
    # kept because it often carries the hook.
    cleaned = cleaned.rstrip(".…").strip(" -:;,")
    if not cleaned:
        return f"Clip from {timecode(start)}"
    if len(cleaned) > limit:
        cut = cleaned[:limit].rsplit(" ", 1)[0].rstrip(",;:-")
        cleaned = f"{cut}…"
    return cleaned[0].upper() + cleaned[1:]


def make_reason(
    *,
    starts_sentence: bool,
    ends_sentence: bool,
    hook_labels: list[str],
    words_per_second: float,
    duration: float,
    overlaps: bool,
    boundaries_certain: bool = True,
    hook_cues_available: bool = True,
) -> str:
    """Explain the signals behind a score in plain language.

    ``boundaries_certain`` is False when the transcript carries no sentence
    punctuation, in which case the cut points are estimates and the reason says so
    rather than claiming the clip stands on complete sentences.
    """

    if not boundaries_certain:
        boundary = (
            "Cut at transcript segment edges; this transcript has no sentence "
            "punctuation, so check the in/out points by ear."
        )
    elif starts_sentence and ends_sentence:
        boundary = "Starts and ends on complete sentences, so it stands on its own."
    elif starts_sentence:
        boundary = "Starts on a complete sentence but is cut before the next one ends."
    elif ends_sentence:
        boundary = "Ends on a complete sentence but begins mid-thought."
    else:
        boundary = "Begins and ends mid-thought; trim the in/out points before exporting."

    if hook_labels:
        hook = f"Early hook markers: {', '.join(hook_labels[:3])}."
    elif not hook_cues_available:
        hook = (
            "Opening hook cues are English-only for now, so this score relies on "
            "pace, length, and cut points."
        )
    else:
        hook = "No strong hook marker early on, so it may need a tighter start."

    if words_per_second >= DENSITY_HIGH:
        pace = f"Fast pace at {words_per_second:.1f} words/second."
    elif words_per_second <= DENSITY_LOW:
        pace = f"Relaxed pace at {words_per_second:.1f} words/second."
    else:
        pace = f"Steady {words_per_second:.1f} words/second."

    parts = [boundary, hook, pace, f"{duration:.0f}s long."]
    if overlaps:
        parts.append("Overlaps another candidate, so only one of the two should be kept.")
    return " ".join(parts)


def score_candidate(
    units: list[Unit],
    first: int,
    last: int,
    *,
    preceding_ends_sentence: bool,
    boundaries_certain: bool = True,
    hook_cues_available: bool = True,
) -> dict[str, object]:
    """Score one unit range and describe it as a candidate clip."""

    window = units[first : last + 1]
    text = _clean(" ".join(unit.text for unit in window))
    start = units[first].start
    end = units[last].end
    duration = max(0.0, end - start)

    word_count = len(tokenize(text))
    words_per_second = round(word_count / duration, 3) if duration > 0 else 0.0

    starts_sentence = first == 0 or preceding_ends_sentence
    ends_sentence = is_boundary_after(units, last)
    if starts_sentence and ends_sentence:
        self_contained = 1.0
    elif ends_sentence:
        self_contained = 0.6
    elif starts_sentence:
        self_contained = 0.5
    else:
        self_contained = 0.25

    hook, labels = hook_score(text)

    signals = {
        "hook": hook,
        "self_contained": self_contained,
        "density": density_score(words_per_second),
        "duration_fit": duration_score(duration),
        "opening_boundary": 1.0 if starts_sentence else 0.3,
    }

    raw = sum(SIGNAL_WEIGHTS[key] * signals[key] for key in SIGNAL_WEIGHTS)
    score = round(min(MAX_SCORE, max(0.0, raw)), 3)

    return {
        "id": f"window-{first}-{last}",
        "title": make_title(first_sentence(units, first, last), start),
        "reason": make_reason(
            starts_sentence=starts_sentence,
            ends_sentence=ends_sentence,
            hook_labels=labels,
            words_per_second=words_per_second,
            duration=duration,
            overlaps=False,
            boundaries_certain=boundaries_certain,
            hook_cues_available=hook_cues_available,
        ),
        "source_start": round(start, 3),
        "source_end": round(end, 3),
        "duration": round(duration, 3),
        "score": score,
        "score_label": "estimate",
        "signals": signals,
        "_text": text,
        "_words_per_second": words_per_second,
        "_hook_labels": labels,
        "_starts_sentence": starts_sentence,
        "_ends_sentence": ends_sentence,
        "_boundaries_certain": boundaries_certain,
        "_hook_cues_available": hook_cues_available,
    }


# --------------------------------------------------------------- selection


def select_candidates(
    candidates: list[dict], *, limit: int = MAX_CLIPS, max_overlap: float = MAX_SELECTION_OVERLAP
) -> tuple[list[dict], int]:
    """Choose a diverse, best-first set and report how many were dropped.

    Candidates that merely duplicate an already-chosen moment are dropped; the
    result is ordered by time so the timeline reads naturally.
    """

    ordered = sorted(candidates, key=lambda item: (-float(item["score"]), float(item["source_start"])))
    chosen: list[dict] = []
    dropped = 0

    for candidate in ordered:
        if len(chosen) >= limit:
            break
        if any(overlap_ratio(candidate, kept) > max_overlap for kept in chosen):
            dropped += 1
            continue
        chosen.append(candidate)

    chosen.sort(key=lambda item: float(item["source_start"]))
    for index, candidate in enumerate(chosen):
        candidate["id"] = f"clip-{index + 1}"
    return chosen, dropped


def flag_relations(candidates: list[dict]) -> None:
    """Record overlap and near-duplicate relationships between kept candidates."""

    for candidate in candidates:
        candidate.pop("overlaps_with", None)
        candidate.pop("near_duplicate_of", None)

    for index, left in enumerate(candidates):
        for right in candidates[index + 1 :]:
            if overlap_ratio(left, right) >= FLAG_OVERLAP_THRESHOLD:
                left.setdefault("overlaps_with", []).append(right["id"])
                right.setdefault("overlaps_with", []).append(left["id"])
            if text_similarity(left["_text"], right["_text"]) >= NEAR_DUPLICATE_SIMILARITY:
                left["near_duplicate_of"] = right["id"]
                right["near_duplicate_of"] = left["id"]


def public_candidate(candidate: dict) -> dict[str, object]:
    """Strip internal scoring fields and refresh the reason with overlap facts."""

    overlaps_with = list(candidate.get("overlaps_with") or [])
    public = {
        key: value
        for key, value in candidate.items()
        if not key.startswith("_") and key not in {"overlaps_with", "near_duplicate_of"}
    }
    # The optional LLM pass may have written a labelled reason; keep it rather
    # than regenerating the heuristic wording over the top of it.
    override = candidate.get("_reason_override")
    public["reason"] = (
        str(override)
        if override
        else make_reason(
            starts_sentence=bool(candidate.get("_starts_sentence")),
            ends_sentence=bool(candidate.get("_ends_sentence")),
            hook_labels=list(candidate.get("_hook_labels") or []),
            words_per_second=float(candidate.get("_words_per_second") or 0.0),
            duration=float(candidate["duration"]),
            overlaps=bool(overlaps_with),
            boundaries_certain=bool(candidate.get("_boundaries_certain", True)),
            hook_cues_available=bool(candidate.get("_hook_cues_available", True)),
        )
    )
    if overlaps_with:
        public["overlaps_with"] = overlaps_with
    if candidate.get("near_duplicate_of"):
        public["near_duplicate_of"] = candidate["near_duplicate_of"]
    return public


# ------------------------------------------------------------------- entry


def suggest_clips(
    transcript: dict,
    *,
    limit: int = MAX_CLIPS,
    generator: str = GENERATOR_HEURISTIC,
    ranker: "object | None" = None,
) -> dict[str, object]:
    """Produce candidate clips from a saved transcript document.

    ``ranker`` is an optional callback (see :mod:`app.llm`) that may re-label the
    chosen candidates. It returns a generator label to record, or ``None`` when it
    produced nothing usable; the heuristic result is used either way.
    """

    segments = transcript.get("segments") if isinstance(transcript, dict) else None
    units = build_units(list(segments or []))
    if not units:
        raise ApiError(
            "empty_transcript",
            "The transcript has no speech to analyse. Transcribe the project first.",
            422,
        )

    notes: list[str] = []
    windows = candidate_windows(units)

    if not windows:
        # Short recording: fall back to the whole thing rather than nothing.
        windows = [(0, len(units) - 1)]
        notes.append(
            "This recording is shorter than the target clip length, so the whole "
            "transcript is offered as a single candidate."
        )

    # Whisper does not always punctuate; when it has not, cut points are estimates
    # and the analysis says so rather than presenting them as exact.
    punctuation = punctuation_ratio(units)
    boundaries_certain = punctuation >= SENTENCE_PUNCTUATION_MIN_RATIO

    # The cue word lists are English-only, so on another script the score leans on
    # pace and length, and the output says so rather than implying "no hook".
    latin_share = latin_token_share(" ".join(unit.text for unit in units))
    hook_cues_available = latin_share >= LATIN_CUES_MIN_SHARE

    scored = [
        score_candidate(
            units,
            first,
            last,
            preceding_ends_sentence=(
                is_boundary_after(units, first - 1) if first > 0 else True
            ),
            boundaries_certain=boundaries_certain,
            hook_cues_available=hook_cues_available,
        )
        for first, last in windows
    ]

    chosen, dropped = select_candidates(scored, limit=limit)

    # An optional extra pass may rewrite titles, reasons, and scores. Clip times
    # are never taken from it, so the boundaries above always stand.
    if callable(ranker):
        label = ranker(chosen)
        if label:
            generator = str(label)

    flag_relations(chosen)

    if dropped:
        notes.append(
            f"{dropped} overlapping candidate(s) were dropped so the list suggests "
            "different moments instead of the same one repeatedly."
        )
    if len(chosen) < MIN_CLIPS:
        notes.append(
            f"Only {len(chosen)} candidate(s) were found; the transcript may be short "
            "or repetitive."
        )
    if not boundaries_certain:
        notes.append(
            "This transcript has no sentence punctuation, so clip edges follow "
            "segment boundaries and may need a small manual trim."
        )
    if not hook_cues_available:
        notes.append(
            "This transcript is not in Latin script, and the opening-hook word "
            "lists are English-only for now, so scores lean on pace and length."
        )

    clips = [public_candidate(candidate) for candidate in chosen]

    analysis = {
        "generator": generator,
        "generated_at": _utcnow(),
        "based_on_transcript_generated_at": (
            transcript.get("generated_at") if isinstance(transcript, dict) else None
        ),
        "clip_count": len(clips),
        "sentence_punctuation": boundaries_certain,
        "sentence_punctuation_ratio": punctuation,
        "hook_cues_available": hook_cues_available,
        "latin_token_share": latin_share,
        "candidates_considered": len(scored),
        "candidates_dropped_for_overlap": dropped,
        "signals": sorted(SIGNAL_WEIGHTS),
        "score_meaning": (
            "score is a 0-1 estimate of how self-contained and information-dense the "
            "moment is, based on the listed signals. It is not a prediction of "
            "performance or virality."
        ),
        "notes": notes,
    }

    return {"clips": clips, "analysis": analysis}
