"""Captions for a selected clip.

The work is split into three honest steps:

1. :func:`build_cues` takes the saved transcript and the clip's in/out points and
   produces cues whose times are **clip-relative**, because the exported file
   starts at zero. A segment that is only partly inside the clip is trimmed to
   the clip, never extended to invent speech that the render will not contain.
2. :func:`build_subtitle_file` turns those cues into a SubStation subtitle, and
   the cues can be written as a sidecar ``.srt`` for other editors.
3. The same cues are written as ``.ass`` with the chosen preset so the captions
   are burned into the render. ASS is used because it can express the sizing,
   safe-area margins, outline/shadow contrast, and word-level highlighting that
   the presets need, which SRT cannot.

Serialisation is delegated to `pysubs2 <https://github.com/tkarabela/pysubs2>`_,
the subtitle library named in TECH_STACK.md, rather than hand-writing both
formats. That library owns style definitions, timestamp formatting, and the ASS
and SRT dialects; this module owns the timing, wrapping, and preset decisions.
Nothing here reads the source video: captions come from the transcript that was
already saved, so this module is pure and fully unit-tested without FFmpeg.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

import pysubs2

from .errors import ApiError

# --------------------------------------------------------------- style model

PRESETS = ("clean", "bold-highlight", "minimal")
FONT_SIZES = ("small", "medium", "large")
PLACEMENTS = ("bottom", "center", "top")
HIGHLIGHT_MODES = ("outline", "shadow", "none")
LINE_LENGTHS = ("auto", "short", "medium", "long")

#: Font size in pixels at a 1920 px tall output. Other heights scale from this so
#: the captions keep the same apparent size relative to the frame.
BASE_FONT_SIZE_PX = {"small": 50, "medium": 66, "large": 84}

#: Extra size for presets that are meant to read bigger than the plain default.
PRESET_SIZE_FACTOR = {"clean": 1.0, "bold-highlight": 1.05, "minimal": 0.92}

#: How much of the frame is kept clear at the top and bottom. Platforms draw
#: their own buttons, captions, and usernames there.
SAFE_AREA_RATIO = {"bottom": 0.12, "center": 0.0, "top": 0.12}
SIDE_MARGIN_RATIO = 0.07

#: Average glyph width as a fraction of the font size, used to work out how many
#: characters fit on a line. Deliberately generous so lines do not overflow.
AVERAGE_GLYPH_WIDTH = 0.5

#: Multipliers applied to the automatic character budget per line.
LINE_LENGTH_FACTOR = {"auto": 1.0, "short": 0.78, "medium": 1.0, "long": 1.28}

MAX_LINES = 2
MAX_CUE_SECONDS = 6.0
#: A caption that is shown for less than this flashes and cannot be read.
MIN_CUE_SECONDS = 0.6
MIN_LINE_LENGTH_CHARS = 12
MAX_LINE_LENGTH_CHARS = 46

#: ASS colours are written as ``&HAABBGGRR``. The highlight colour matches the
#: accent used in the interface so the burned-in captions look intentional.
ASS_TEXT_COLOUR = "&H00FFFFFF"
ASS_HIGHLIGHT_COLOUR = "&H0000C8FF"
ASS_OUTLINE_COLOUR = "&H00000000"
ASS_BOX_COLOUR = "&H00000000"

#: Font used when the chosen preset has no reason to prefer another one. libass
#: falls back through fontconfig for glyphs this font does not cover, which is
#: what lets a Devanagari transcript still render on Windows.
DEFAULT_FONT = "Arial"

PRESET_FONT = {"clean": DEFAULT_FONT, "bold-highlight": DEFAULT_FONT, "minimal": DEFAULT_FONT}


def default_style() -> dict[str, object]:
    """The caption style a new project starts with."""

    return {
        "preset": "bold-highlight",
        "font_size": "medium",
        "placement": "bottom",
        "highlight_mode": "shadow",
        "line_length": "auto",
        "high_contrast": False,
    }


def _enum(value: object, allowed: Sequence[str], field: str, fallback: str) -> str:
    """Validate one enumerated style field, or explain what went wrong."""

    if value is None:
        return fallback

    text = str(value).strip().lower()
    if not text:
        return fallback

    if text not in allowed:
        raise ApiError(
            "invalid_caption_style",
            f"{field} must be one of {', '.join(allowed)} (got '{value}').",
            422,
        )
    return text


def _flag(value: object, fallback: bool) -> bool:
    if value is None:
        return fallback
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    raise ApiError(
        "invalid_caption_style",
        f"high_contrast must be true or false (got '{value}').",
        422,
    )


def normalise_style(raw: dict | None) -> dict[str, object]:
    """Validate a caption style, filling in defaults for anything omitted.

    Every value is checked here rather than at render time so a bad style can
    never reach FFmpeg, where the failure would be much harder to explain.
    """

    source = dict(raw or {})
    defaults = default_style()

    style = {
        "preset": _enum(source.get("preset"), PRESETS, "preset", str(defaults["preset"])),
        "font_size": _enum(
            source.get("font_size"), FONT_SIZES, "font_size", str(defaults["font_size"])
        ),
        "placement": _enum(
            source.get("placement"), PLACEMENTS, "placement", str(defaults["placement"])
        ),
        "highlight_mode": _enum(
            source.get("highlight_mode"),
            HIGHLIGHT_MODES,
            "highlight_mode",
            str(defaults["highlight_mode"]),
        ),
        "line_length": _enum(
            source.get("line_length"),
            LINE_LENGTHS,
            "line_length",
            str(defaults["line_length"]),
        ),
        "high_contrast": _flag(source.get("high_contrast"), bool(defaults["high_contrast"])),
    }
    return style


@dataclass(frozen=True)
class CaptionGeometry:
    """Resolved pixel sizes and margins for one output resolution."""

    width: int
    height: int
    font_size: int
    outline: float
    shadow: float
    border_style: int
    alignment: int
    margin_v: int
    margin_h: int
    max_line_chars: int
    max_lines: int

    @property
    def safe_area(self) -> int:
        return self.margin_v

    def as_dict(self) -> dict[str, object]:
        return {
            "width": self.width,
            "height": self.height,
            "font_size": self.font_size,
            "max_line_chars": self.max_line_chars,
            "max_lines": self.max_lines,
            "border_style": self.border_style,
            "alignment": self.alignment,
            "margin_v": self.margin_v,
            "margin_h": self.margin_h,
        }


def resolve_geometry(
    style: dict[str, object], *, width: int, height: int, lines: int = MAX_LINES
) -> CaptionGeometry:
    """Turn a validated style into the numbers an ASS style line needs."""

    if width <= 0 or height <= 0:
        raise ApiError(
            "invalid_render_size",
            f"Output size must be positive (got {width}x{height}).",
            422,
        )

    preset = str(style.get("preset", "bold-highlight"))
    font_size = int(
        round(
            BASE_FONT_SIZE_PX[str(style.get("font_size", "medium"))]
            * PRESET_SIZE_FACTOR.get(preset, 1.0)
            * (height / 1920)
        )
    )
    font_size = max(18, font_size)

    highlight_mode = str(style.get("highlight_mode", "shadow"))
    if highlight_mode == "outline":
        outline, shadow, border_style = max(2.0, font_size * 0.06), 0.0, 1
    elif highlight_mode == "shadow":
        outline, shadow, border_style = max(1.0, font_size * 0.035), max(2.0, font_size * 0.05), 1
    else:
        outline, shadow, border_style = 0.0, 0.0, 1

    if style.get("high_contrast"):
        # An opaque box behind the words is the strongest contrast available and
        # is why high contrast replaces the outline rather than adding to it.
        outline, shadow, border_style = max(6.0, font_size * 0.18), 0.0, 3

    placement = str(style.get("placement", "bottom"))
    alignment = {"bottom": 2, "center": 5, "top": 8}[placement]

    margin_h = int(round(width * SIDE_MARGIN_RATIO))
    margin_v = int(round(height * SAFE_AREA_RATIO[placement]))

    usable = max(1, width - 2 * margin_h)
    per_line = usable / max(1.0, font_size * AVERAGE_GLYPH_WIDTH)
    factor = LINE_LENGTH_FACTOR[str(style.get("line_length", "auto"))]
    max_line_chars = int(round(per_line * factor))
    max_line_chars = min(MAX_LINE_LENGTH_CHARS, max(MIN_LINE_LENGTH_CHARS, max_line_chars))

    return CaptionGeometry(
        width=width,
        height=height,
        font_size=font_size,
        outline=round(outline, 2),
        shadow=round(shadow, 2),
        border_style=border_style,
        alignment=alignment,
        margin_v=margin_v,
        margin_h=margin_h,
        max_line_chars=max_line_chars,
        max_lines=max(1, lines),
    )


# ------------------------------------------------------------------- timing


def milliseconds(seconds: object) -> int:
    """Whole milliseconds since the clip start, which is what subtitles store."""

    return max(0, int(round(_number(seconds) * 1000)))


# ------------------------------------------------------------------ wrapping


def wrap_text(text: str, max_line_chars: int, max_lines: int = MAX_LINES) -> list[str]:
    """Wrap words into at most ``max_lines`` lines.

    Words that still do not fit stay on the last line instead of being dropped:
    a caption that silently loses speech is worse than one line that is slightly
    too long, and the safe-area margins keep it inside the frame.
    """

    words = str(text or "").split()
    if not words:
        return []

    limit = max(1, int(max_line_chars))
    lines: list[str] = []
    current = ""

    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > limit:
            lines.append(current)
            current = word
        else:
            current = candidate

    if current:
        lines.append(current)

    if len(lines) > max(1, max_lines):
        head = lines[: max(1, max_lines) - 1]
        head.append(" ".join(lines[max(1, max_lines) - 1 :]))
        return head
    return lines


# -------------------------------------------------------------------- cues


def _number(value: object, fallback: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback


def _cue_words(segment: dict, clip_start: float, clip_end: float) -> list[dict[str, object]]:
    """Clip a segment's word timings to the clip window, keeping their order."""

    words: list[dict[str, object]] = []
    for raw in segment.get("words") or []:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("word") or "").strip()
        if not text:
            continue
        start = max(_number(raw.get("start")), clip_start)
        end = min(_number(raw.get("end")), clip_end)
        if end <= start:
            continue
        words.append(
            {"start": round(start - clip_start, 3), "end": round(end - clip_start, 3), "text": text}
        )
    return words


def _group_words(
    words: list[dict[str, object]], geometry: CaptionGeometry
) -> list[dict[str, object]]:
    """Split word timings into cue-sized groups that fit the caption box."""

    limit = geometry.max_line_chars * geometry.max_lines
    groups: list[dict[str, object]] = []
    current: list[dict[str, object]] = []
    length = 0

    for word in words:
        text = str(word["text"])
        addition = len(text) + (1 if current else 0)
        if current and length + addition > limit:
            groups.append({"words": current})
            current, length = [], 0
        current.append(word)
        length += addition

    if current:
        groups.append({"words": current})

    cues: list[dict[str, object]] = []
    for group in groups:
        members = group["words"]
        text = " ".join(str(word["text"]) for word in members)
        cues.append(
            {
                "start": _number(members[0]["start"]),
                "end": _number(members[-1]["end"]),
                "text": text,
                "words": members,
                "from_words": True,
            }
        )
    return cues


def _merge_cues(cues: Iterable[dict[str, object]], clip_duration: float) -> list[dict[str, object]]:
    """Sort, trim, and tidy cues so they can be rendered without surprises.

    Cues never overlap (overlapping ASS events draw on top of each other), never
    run past the clip, and are never so short that they flash.
    """

    ordered = sorted(
        (cue for cue in cues if str(cue.get("text") or "").strip()),
        key=lambda cue: (_number(cue.get("start")), _number(cue.get("end"))),
    )

    final: list[dict[str, object]] = []
    for cue in ordered:
        start = max(0.0, _number(cue.get("start")))
        end = min(_number(cue.get("end")), clip_duration)

        if final:
            previous_end = _number(final[-1]["end"])
            if start < previous_end:
                if previous_end - start >= MIN_CUE_SECONDS:
                    # Shorten the earlier cue rather than dropping speech.
                    start = previous_end
                else:
                    final[-1]["end"] = start
        if end <= start:
            continue

        cue = dict(cue)
        cue["start"] = round(start, 3)
        cue["end"] = round(end, 3)
        final.append(cue)

    # Give very short cues room to be read, without running into the next one.
    for index, cue in enumerate(final):
        duration = _number(cue["end"]) - _number(cue["start"])
        if duration >= MIN_CUE_SECONDS:
            continue
        ceiling = _number(final[index + 1]["start"]) if index + 1 < len(final) else clip_duration
        target = min(_number(cue["start"]) + MIN_CUE_SECONDS, ceiling)
        if target > _number(cue["end"]):
            cue["end"] = round(target, 3)

    return final


def build_cues(
    segments: Sequence[dict],
    *,
    clip_start: float,
    clip_end: float,
    geometry: CaptionGeometry,
    use_word_timings: bool = True,
) -> list[dict[str, object]]:
    """Build clip-relative caption cues from saved transcript segments.

    Timestamps are shifted so the first frame of the exported clip is zero,
    which is what a burned-in caption and a sidecar SRT both need.
    """

    if clip_end <= clip_start:
        raise ApiError(
            "invalid_clip_range",
            f"The clip ends at {clip_end:.2f}s, which is not after its start at "
            f"{clip_start:.2f}s.",
            422,
        )

    duration = clip_end - clip_start
    cues: list[dict[str, object]] = []

    for segment in segments or []:
        if not isinstance(segment, dict):
            continue

        segment_start = _number(segment.get("start"))
        segment_end = _number(segment.get("end"))
        if segment_end <= clip_start or segment_start >= clip_end:
            continue

        text = " ".join(str(segment.get("text") or "").split())
        if not text:
            continue

        words = _cue_words(segment, clip_start, clip_end) if use_word_timings else []
        if len(words) > 1:
            cues.extend(_group_words(words, geometry))
            continue

        start = max(segment_start, clip_start) - clip_start
        end = min(segment_end, clip_end) - clip_start
        if end <= start:
            continue
        cues.append({"start": start, "end": end, "text": text, "words": [], "from_words": False})

    prepared: list[dict[str, object]] = []
    for cue in _merge_cues(cues, duration):
        # A cue left on screen unchanged for too long reads as a frozen caption.
        if _number(cue["end"]) - _number(cue["start"]) > MAX_CUE_SECONDS:
            cue["end"] = round(_number(cue["start"]) + MAX_CUE_SECONDS, 3)

        lines = wrap_text(str(cue["text"]), geometry.max_line_chars, geometry.max_lines)
        if not lines:
            continue

        cue["lines"] = lines
        cue["index"] = len(prepared) + 1
        prepared.append(cue)

    return prepared


# ------------------------------------------------------------------ writers


def _ass_escape(text: str) -> str:
    """Escape text for an ASS dialogue line.

    Braces start an override block and backslashes start a control sequence, so
    both must be escaped or a stray ``{`` in a transcript would change the style
    of everything after it.
    """

    return (
        str(text or "")
        .replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\r\n", " ")
        .replace("\n", " ")
    )


def _ass_text(cue: dict[str, object], style: dict[str, object], geometry: CaptionGeometry) -> str:
    """Render one cue's text, adding karaoke highlighting when it is meaningful."""

    words = cue.get("words") or []
    highlighted = str(style.get("preset")) == "bold-highlight" and len(words) > 1

    if highlighted:
        pieces: list[str] = []
        for word in words:
            length = max(0.01, _number(word["end"]) - _number(word["start"]))
            # \k switches the following text from the secondary to the primary
            # colour when its time elapses: word-level highlighting in ASS.
            pieces.append(f"{{\\k{max(1, int(round(length * 100)))}}}{_ass_escape(str(word['text']))}")
        return " ".join(pieces)

    return "\\N".join(_ass_escape(line) for line in cue.get("lines") or [])


def _ass_colours(style: dict[str, object]) -> tuple[str, str]:
    """Primary and secondary colour: the second is the un-highlighted karaoke colour."""

    if str(style.get("preset")) == "bold-highlight":
        return ASS_HIGHLIGHT_COLOUR, ASS_TEXT_COLOUR
    return ASS_TEXT_COLOUR, ASS_HIGHLIGHT_COLOUR


def _colour(ass_value: str) -> pysubs2.Color:
    """Convert an ``&HAABBGGRR`` constant above into a pysubs2 colour."""

    value = int(str(ass_value).removeprefix("&H"), 16)
    return pysubs2.Color(
        r=value & 0xFF,
        g=(value >> 8) & 0xFF,
        b=(value >> 16) & 0xFF,
        a=(value >> 24) & 0xFF,
    )


#: ASS aligns text with the numeric keypad layout (2 is bottom centre); pysubs2
#: models the same nine positions as an enum.
ASS_ALIGNMENT = {
    1: pysubs2.Alignment.BOTTOM_LEFT,
    2: pysubs2.Alignment.BOTTOM_CENTER,
    3: pysubs2.Alignment.BOTTOM_RIGHT,
    5: pysubs2.Alignment.MIDDLE_CENTER,
    7: pysubs2.Alignment.TOP_LEFT,
    8: pysubs2.Alignment.TOP_CENTER,
    9: pysubs2.Alignment.TOP_RIGHT,
}


def build_subtitle_file(
    cues: Sequence[dict[str, object]],
    style: dict[str, object],
    geometry: CaptionGeometry,
) -> pysubs2.SSAFile:
    """Build the SubStation document that the ASS export is written from.

    Keeping the intermediate document (rather than a formatted string) means the
    style can be checked as data — the alignment, margins, and colours the render
    uses — which is what the tests read back.
    """

    subs = pysubs2.SSAFile()
    subs.info.update(
        {
            "Title": "AI Video Production Studio captions",
            "ScriptType": "v4.00+",
            "WrapStyle": 2,
            "ScaledBorderAndShadow": "yes",
            "YCbCr Matrix": "TV.709",
            "PlayResX": geometry.width,
            "PlayResY": geometry.height,
        }
    )

    primary, secondary = _ass_colours(style)
    preset = str(style.get("preset"))
    subs.styles["Default"] = pysubs2.SSAStyle(
        fontname=PRESET_FONT.get(preset, DEFAULT_FONT),
        fontsize=geometry.font_size,
        primarycolor=_colour(primary),
        secondarycolor=_colour(secondary),
        outlinecolor=_colour(
            ASS_BOX_COLOUR if geometry.border_style == 3 else ASS_OUTLINE_COLOUR
        ),
        backcolor=_colour(ASS_BOX_COLOUR),
        bold=preset in {"bold-highlight", "clean"},
        borderstyle=geometry.border_style,
        outline=geometry.outline,
        shadow=geometry.shadow,
        alignment=ASS_ALIGNMENT[geometry.alignment],
        marginl=geometry.margin_h,
        marginr=geometry.margin_h,
        marginv=geometry.margin_v,
    )

    for cue in cues:
        text = _ass_text(cue, style, geometry)
        if not text:
            continue
        subs.events.append(
            pysubs2.SSAEvent(
                start=milliseconds(cue["start"]),
                end=milliseconds(cue["end"]),
                text=text,
                style="Default",
            )
        )

    return subs


def to_ass(
    cues: Sequence[dict[str, object]],
    style: dict[str, object],
    geometry: CaptionGeometry,
) -> str:
    """Write cues as an ASS subtitle file sized for the output resolution."""

    return build_subtitle_file(cues, style, geometry).to_string("ass")


def to_srt(cues: Sequence[dict[str, object]]) -> str:
    """Write cues as a plain SRT sidecar file, with override tags stripped."""

    subs = pysubs2.SSAFile()
    for cue in cues:
        lines = cue.get("lines") or wrap_text(
            str(cue.get("text") or ""), MAX_LINE_LENGTH_CHARS
        )
        if not lines:
            continue
        subs.events.append(
            pysubs2.SSAEvent(
                start=milliseconds(cue["start"]),
                end=milliseconds(cue["end"]),
                # SRT has no styling, so the words go out as plain lines.
                text="\n".join(str(line) for line in lines),
            )
        )

    return subs.to_string("srt")


def caption_summary(cues: Sequence[dict[str, object]]) -> dict[str, object]:
    """A small, honest description of what was generated for a clip."""

    if not cues:
        return {
            "cue_count": 0,
            "first_start": None,
            "last_end": None,
            "note": (
                "No transcribed speech falls inside this clip, so the export has no "
                "captions. Check the clip's in/out points or transcribe the source first."
            ),
        }

    return {
        "cue_count": len(cues),
        "first_start": _number(cues[0]["start"]),
        "last_end": _number(cues[-1]["end"]),
        "note": None,
    }
