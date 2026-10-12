"""Milestone 5 acceptance checks for captions.

These tests are offline and deterministic: captions are built from a synthetic
transcript, so no model, no FFmpeg, and no source video are involved. The checks
focus on what a burned-in caption can get wrong: clip-relative timing, text that
runs off the frame, and characters that would inject ASS override tags.
"""

from __future__ import annotations

import pytest
import pysubs2

from app.captions import (
    MAX_CUE_SECONDS,
    MIN_CUE_SECONDS,
    build_cues,
    build_subtitle_file,
    caption_summary,
    default_style,
    milliseconds,
    normalise_style,
    resolve_geometry,
    to_ass,
    to_srt,
    wrap_text,
)
from app.errors import ApiError

#: A transcript for a clip that runs 7.5s–19.0s: one segment entirely before the
#: clip, two inside it, one that straddles its end, and one entirely after it.
ROWS = [
    (0.0, 4.0, "Welcome back to the show."),
    (8.0, 12.0, "Here is the first thing about editing speed"),
    (12.0, 17.0, "you should never spend more than five minutes per minute of footage"),
    (17.0, 22.0, "and that is the whole rule in practice"),
    (24.0, 30.0, "and that is everything for today"),
]

CLIP_START = 7.5
CLIP_END = 19.0


def style_for(**overrides: object) -> dict[str, object]:
    return normalise_style({**default_style(), **overrides})


def geometry_for(**overrides: object):
    return resolve_geometry(style_for(**overrides), width=1080, height=1920)


def segments(rows=None) -> list[dict]:
    rows = rows if rows is not None else ROWS
    return [
        {"id": f"seg-{index + 1}", "start": start, "end": end, "text": text}
        for index, (start, end, text) in enumerate(rows)
    ]


def cues_for(**overrides: object) -> list[dict]:
    return build_cues(
        segments(), clip_start=CLIP_START, clip_end=CLIP_END, geometry=geometry_for(**overrides)
    )


# ----------------------------------------------------------- style validation


def test_default_style_is_a_complete_valid_style() -> None:
    assert normalise_style(default_style()) == default_style()


def test_normalise_style_fills_in_every_missing_field() -> None:
    style = normalise_style({"preset": "minimal"})

    assert style["preset"] == "minimal"
    assert set(style) == set(default_style())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("preset", "fancy"),
        ("font_size", "huge"),
        ("placement", "left"),
        ("highlight_mode", "sparkle"),
        ("line_length", "endless"),
    ],
)
def test_normalise_style_rejects_unknown_values(field: str, value: str) -> None:
    with pytest.raises(ApiError) as error:
        normalise_style({**default_style(), field: value})

    assert error.value.code == "invalid_caption_style"
    assert field in error.value.message


def test_normalise_style_rejects_a_non_boolean_high_contrast() -> None:
    with pytest.raises(ApiError) as error:
        normalise_style({**default_style(), "high_contrast": "maybe"})

    assert error.value.code == "invalid_caption_style"


# ----------------------------------------------------------------- geometry


def test_geometry_keeps_captions_inside_the_bottom_safe_area() -> None:
    geometry = geometry_for(placement="bottom")

    assert geometry.alignment == 2
    assert geometry.margin_v == round(1920 * 0.12)
    assert geometry.margin_h == round(1080 * 0.07)
    # The safe area is what stops platform buttons covering the words.
    assert geometry.safe_area == geometry.margin_v


def test_geometry_centres_and_aligns_to_the_top_when_asked() -> None:
    assert geometry_for(placement="center").alignment == 5
    assert geometry_for(placement="center").margin_v == 0
    assert geometry_for(placement="top").alignment == 8
    assert geometry_for(placement="top").margin_v == round(1920 * 0.12)


def test_geometry_scales_the_font_with_the_output_height() -> None:
    large = resolve_geometry(style_for(), width=1080, height=1920)
    small = resolve_geometry(style_for(), width=540, height=960)

    # Half the height must mean roughly half the text, or a smaller export would
    # show captions in a different apparent size.
    assert small.font_size < large.font_size
    assert abs(small.font_size * 2 - large.font_size) <= 2


def test_bigger_font_sizes_leave_room_for_fewer_characters() -> None:
    small = geometry_for(font_size="small").max_line_chars
    large = geometry_for(font_size="large").max_line_chars

    assert large < small


def test_line_length_choices_change_the_character_budget() -> None:
    short = geometry_for(line_length="short").max_line_chars
    auto = geometry_for(line_length="auto").max_line_chars
    long = geometry_for(line_length="long").max_line_chars

    assert short < auto < long


def test_high_contrast_draws_an_opaque_box_instead_of_an_outline() -> None:
    plain = geometry_for(highlight_mode="outline")
    contrast = geometry_for(highlight_mode="outline", high_contrast=True)

    assert plain.border_style == 1
    assert contrast.border_style == 3
    assert contrast.outline > plain.outline
    assert contrast.shadow == 0


def test_geometry_rejects_a_non_positive_output_size() -> None:
    with pytest.raises(ApiError) as error:
        resolve_geometry(style_for(), width=0, height=1920)

    assert error.value.code == "invalid_render_size"


# ------------------------------------------------------------ cue building


def test_cues_are_shifted_to_clip_relative_time() -> None:
    cues = cues_for()

    # The clip starts at 7.5s, so speech from 8.0s appears at 0.5s in the export.
    assert cues[0]["start"] == pytest.approx(0.5, abs=0.01)
    assert cues[0]["end"] == pytest.approx(4.5, abs=0.01)
    assert cues[-1]["end"] == pytest.approx(CLIP_END - CLIP_START, abs=0.01)


def test_speech_before_the_clip_is_not_pulled_into_it() -> None:
    cues = cues_for()
    texts = " ".join(str(cue["text"]) for cue in cues)

    assert "Welcome back" not in texts


def test_speech_after_the_clip_is_dropped() -> None:
    texts = " ".join(str(cue["text"]) for cue in cues_for())

    assert "everything for today" not in texts


def test_a_segment_straddling_the_clip_end_keeps_only_the_spoken_part() -> None:
    cues = cues_for()
    straddling = [cue for cue in cues if "whole rule" in str(cue["text"])]

    assert len(straddling) == 1
    assert straddling[0]["end"] == pytest.approx(CLIP_END - CLIP_START, abs=0.01)


def test_a_segment_that_starts_before_the_clip_is_trimmed_to_zero() -> None:
    cues = build_cues(
        segments([(9.0, 16.0, "this speech started before the clip did")]),
        clip_start=10.0,
        clip_end=20.0,
        geometry=geometry_for(),
    )

    # Trimmed at the clip edge rather than shifted, so the caption matches the
    # audio the export really contains.
    assert cues[0]["start"] == 0.0
    assert cues[0]["end"] == pytest.approx(6.0)


def test_cues_never_run_past_the_end_of_the_clip() -> None:
    clip_length = CLIP_END - CLIP_START

    for cue in cues_for():
        assert cue["end"] <= clip_length + 0.01
        assert cue["start"] < cue["end"]


def test_cues_never_overlap_each_other() -> None:
    cues = cues_for()

    for previous, following in zip(cues, cues[1:]):
        assert following["start"] >= previous["end"]


def test_a_short_cue_is_extended_so_it_can_be_read() -> None:
    cues = build_cues(
        segments([(0.0, 0.15, "yes")]),
        clip_start=0.0,
        clip_end=10.0,
        geometry=geometry_for(),
    )

    assert cues[0]["end"] - cues[0]["start"] >= MIN_CUE_SECONDS


def test_a_long_segment_is_capped_at_the_maximum_cue_length() -> None:
    cues = build_cues(
        segments([(0.0, 30.0, "one very long uninterrupted sentence that keeps going")]),
        clip_start=0.0,
        clip_end=30.0,
        geometry=geometry_for(),
    )

    assert cues[0]["end"] - cues[0]["start"] == pytest.approx(MAX_CUE_SECONDS)


def test_wrapped_lines_fit_the_character_budget() -> None:
    geometry = geometry_for()
    for cue in cues_for():
        assert len(cue["lines"]) <= geometry.max_lines
        for index, line in enumerate(cue["lines"]):
            # The last line is allowed to overflow rather than lose speech.
            if index < len(cue["lines"]) - 1:
                assert len(line) <= geometry.max_line_chars


def test_wrapping_never_drops_words() -> None:
    text = "alpha beta gamma delta epsilon zeta eta theta"

    lines = wrap_text(text, 12, 2)

    assert " ".join(lines).split() == text.split()


def test_wrapping_keeps_one_word_that_is_longer_than_a_line() -> None:
    assert wrap_text("supercalifragilistic", 5, 2) == ["supercalifragilistic"]


def test_word_timings_are_used_when_the_model_provided_them() -> None:
    rows = [
        {
            "id": "seg-1",
            "start": 0.0,
            "end": 6.0,
            "text": "one two three four",
            "words": [
                {"start": 0.5, "end": 1.0, "word": "one"},
                {"start": 1.0, "end": 1.5, "word": "two"},
                {"start": 1.5, "end": 2.0, "word": "three"},
                {"start": 2.0, "end": 2.5, "word": "four"},
            ],
        }
    ]  # a segment dict, exactly as the transcript file stores it

    cues = build_cues(rows, clip_start=0.0, clip_end=6.0, geometry=geometry_for())

    assert cues[0]["start"] == pytest.approx(0.5)
    assert cues[0]["end"] == pytest.approx(2.5)
    assert cues[0]["from_words"] is True


def test_word_timings_can_be_ignored_for_segment_timing() -> None:
    rows = [
        {
            "id": "seg-1",
            "start": 0.0,
            "end": 6.0,
            "text": "one two three four",
            "words": [
                {"start": 0.5, "end": 1.0, "word": "one"},
                {"start": 1.0, "end": 1.5, "word": "two"},
            ],
        }
    ]

    cues = build_cues(
        rows, clip_start=0.0, clip_end=6.0, geometry=geometry_for(), use_word_timings=False
    )  # type: ignore[arg-type]

    assert cues[0]["start"] == 0.0
    assert cues[0]["end"] == pytest.approx(6.0)


def test_no_speech_produces_no_cues_and_says_why() -> None:
    cues = build_cues([], clip_start=0.0, clip_end=10.0, geometry=geometry_for())
    summary = caption_summary(cues)

    assert cues == []
    assert summary["cue_count"] == 0
    assert "no transcribed speech" in str(summary["note"]).lower()


def test_an_inverted_clip_range_is_rejected() -> None:
    with pytest.raises(ApiError) as error:
        build_cues(segments(), clip_start=10.0, clip_end=5.0, geometry=geometry_for())

    assert error.value.code == "invalid_clip_range"


# ------------------------------------------------------------------ writers


def test_srt_timestamps_are_zero_padded() -> None:
    body = to_srt(
        [
            {"start": 1.5, "end": 61.25, "text": "hello there", "lines": ["hello there"]},
            {"start": 3661.5, "end": 3662.0, "text": "late", "lines": ["late"]},
            {"start": -4.0, "end": 1.0, "text": "clamped", "lines": ["clamped"]},
        ]
    )

    assert "00:00:01,500 --> 00:01:01,250" in body
    assert "01:01:01,500 --> 01:01:02,000" in body
    # A cue before the clip starts is clamped to zero rather than written negative.
    assert "00:00:00,000 --> 00:00:01,000" in body


def test_ass_timestamps_use_hundredths() -> None:
    body = to_ass(
        [{"start": 61.25, "end": 3661.5, "text": "hello", "lines": ["hello"]}],
        style_for(),
        geometry_for(),
    )

    assert "0:01:01.25,1:01:01.50" in body


def test_milliseconds_are_rounded_and_never_negative() -> None:
    assert milliseconds(1.2345) == 1234
    assert milliseconds(1.25) == 1250
    assert milliseconds(None) == 0
    assert milliseconds(-3.0) == 0


def test_srt_contains_one_block_per_cue_with_clip_relative_times() -> None:
    cues = cues_for()
    body = to_srt(cues)

    assert body.count("-->") == len(cues)
    assert body.startswith("1\n00:00:00,500 --> ")


def test_ass_declares_the_output_resolution_and_safe_margins() -> None:
    body = to_ass(cues_for(), style_for(), geometry_for())

    assert "PlayResX: 1080" in body
    assert "PlayResY: 1920" in body
    assert "WrapStyle: 2" in body
    assert ",76,76,230,1" in body  # left/right/vertical margins, bottom alignment
    assert body.count("Dialogue: 0,") == len(cues_for())


def test_the_subtitle_document_reads_back_with_the_declared_style() -> None:
    geometry = geometry_for()
    parsed = pysubs2.SSAFile.from_string(to_ass(cues_for(), style_for(), geometry))
    style = parsed.styles["Default"]

    assert len(parsed.events) == len(cues_for())
    assert parsed.info["PlayResX"] == "1080"
    assert parsed.info["PlayResY"] == "1920"
    assert style.fontsize == geometry.font_size
    assert style.marginv == geometry.margin_v
    assert style.marginl == style.marginr == geometry.margin_h
    assert int(style.alignment) == geometry.alignment
    assert style.bold is True


def test_a_two_line_cue_keeps_its_line_break_in_the_ass_file() -> None:
    # A raw newline inside a Dialogue line would be parsed as a new format line and
    # corrupt the whole file, so the break has to be written as the ASS escape.
    cues = build_cues(
        segments([(0.0, 4.0, "one two three four five six seven eight nine ten")]),
        clip_start=0.0,
        clip_end=4.0,
        geometry=geometry_for(font_size="large", line_length="short"),
    )
    assert len(cues[0]["lines"]) == 2

    body = to_ass(cues, style_for(), geometry_for(font_size="large", line_length="short"))
    dialogue = [line for line in body.splitlines() if line.startswith("Dialogue:")]

    assert len(dialogue) == 1
    assert "\\N" in dialogue[0]

    parsed = pysubs2.SSAFile.from_string(body)
    assert parsed.events[0].plaintext.splitlines() == cues[0]["lines"]


def test_ass_escapes_characters_that_would_inject_style_tags() -> None:
    cues = build_cues(
        segments([(0.0, 4.0, "a {\\b1}literal brace and a \\ backslash")]),
        clip_start=0.0,
        clip_end=4.0,
        geometry=geometry_for(),
    )

    body = to_ass(cues, style_for(preset="clean"), geometry_for())

    dialogue = [line for line in body.splitlines() if line.startswith("Dialogue:")][0]
    # Nothing after the timestamp may open an override block or control sequence.
    assert "\\{" in dialogue
    assert "\\}" in dialogue
    assert "\\\\" in dialogue
    assert "{\\b1}" not in dialogue


def test_bold_highlight_adds_word_timing_tags() -> None:
    rows = [
        {
            "id": "seg-1",
            "start": 0.0,
            "end": 3.0,
            "text": "one two three",
            "words": [
                {"start": 0.0, "end": 1.0, "word": "one"},
                {"start": 1.0, "end": 2.0, "word": "two"},
                {"start": 2.0, "end": 3.0, "word": "three"},
            ],
        }
    ]
    cues = build_cues(rows, clip_start=0.0, clip_end=3.0, geometry=geometry_for())

    body = to_ass(cues, style_for(preset="bold-highlight"), geometry_for())
    assert "Dialogue: 0," in body

    assert body.count("{\\k") == 3
    assert "&H0000C8FF" in body


def test_other_presets_do_not_add_karaoke_tags() -> None:
    body = to_ass(cues_for(), style_for(preset="clean"), geometry_for())

    assert "{\\k" not in body


def test_high_contrast_switches_the_ass_border_style() -> None:
    body = to_ass(
        cues_for(), style_for(high_contrast=True), geometry_for(high_contrast=True)
    )

    assert ",3," in body  # BorderStyle 3 draws an opaque box


def test_srt_sidecar_carries_the_same_words_without_style_tags() -> None:
    rows = [
        {
            "id": "seg-1",
            "start": 0.0,
            "end": 3.0,
            "text": "one two three",
            "words": [
                {"start": 0.0, "end": 1.0, "word": "one"},
                {"start": 1.0, "end": 2.0, "word": "two"},
                {"start": 2.0, "end": 3.0, "word": "three"},
            ],
        }
    ]
    cues = build_cues(rows, clip_start=0.0, clip_end=3.0, geometry=geometry_for())

    body = to_srt(cues)

    assert "{\\k" not in body
    assert "one" in body and "three" in body


def test_ass_and_srt_describe_the_same_cues() -> None:
    cues = cues_for()

    assert to_srt(cues).count("-->") == to_ass(
        cues, style_for(), geometry_for()
    ).count("Dialogue: 0,")
