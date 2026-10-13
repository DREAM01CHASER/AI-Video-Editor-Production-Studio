"""Milestone 9 acceptance checks for transitions and automatic sound design.

The model tests are offline: filter fragments, bounds, the toggle, and the edit
round-trip through ``project.json``. The render tests write **real media** with
FFmpeg and then measure the finished file, because "the transition is in the
export" is only true if the pixels and the audio actually changed.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.errors import ApiError
from app.main import create_app
from app.render import build_composite_filters, build_filter_chain, ffmpeg_available
from app.sound_design import (
    AUTO_SFX_COUNT_CHOICES,
    AUTO_SFX_DURATION_SECONDS,
    AUTO_SFX_KINDS,
    LEAD_SECONDS,
    MAX_AUTO_SFX,
    build_synth_command,
    describe as sound_design_note,
    gain_db,
    normalise_settings,
    propose_auto_sfx,
    render_synth_into,
)
from app.timeline import SFX_MAX_GAIN_DB, apply_timeline_patch, build_timeline, stored_timeline
from app.transitions import (
    build_transition_chain,
    describe as transition_note,
    normalise_duration,
    transition_filter,
)

TRANSCRIPT_GENERATED_AT = "2026-01-01T00:00:00+00:00"

CLIP = {
    "id": "clip-1",
    "title": "The five minute rule",
    "reason": "Self-contained tip.",
    "score": 0.71,
    "source_start": 1.0,
    "source_end": 11.0,
    "duration": 10.0,
}

CAPTION_STYLE = {
    "preset": "bold-highlight",
    "font_size": "medium",
    "placement": "bottom",
    "highlight_mode": "shadow",
    "line_length": "auto",
    "high_contrast": False,
}

EXPORT = {
    "aspect_ratio": "9:16",
    "resolution": "1080x1920",
    "format": "mp4",
    "fps": 30,
    "caption_mode": "none",
    "write_srt": False,
}

EXPORT_PLAN = {
    **EXPORT,
    "width": 1080,
    "height": 1920,
    "upscaled": False,
    "notes": [],
}


def _project_document(**overrides: object) -> dict:
    project = {
        "project_id": "abc123",
        "name": "Milestone 9 test",
        "source_video": "source/source-x.mp4",
        "source_metadata": {
            "duration": 30.0,
            "width": 1920,
            "height": 1080,
            "fps": 25.0,
            "size_bytes": 4096,
        },
        "transcript": {
            "generated_at": TRANSCRIPT_GENERATED_AT,
            "path": "transcripts/transcript.json",
        },
        "clips": [dict(CLIP)],
        "clip_analysis": {"generator": "heuristic"},
        "caption_style": dict(CAPTION_STYLE),
        "export": dict(EXPORT),
        "assets": [],
    }
    project.update(overrides)
    return project


def _client(tmp_path: Path) -> TestClient:
    settings = Settings(
        project_root=Path(__file__).resolve().parents[1],
        workspace_dir=tmp_path / "workspace",
        max_upload_size_bytes=50 * 1024 * 1024,
    )
    return TestClient(create_app(settings), raise_server_exceptions=False)


def _project(tmp_path: Path) -> TestClient:
    client = _client(tmp_path)
    store = client.app.state.projects
    store.write_new(
        "abc123",
        name="Milestone 9 test",
        source_video="source/source-x.mp4",
        source_metadata={
            "duration": 30.0,
            "width": 1920,
            "height": 1080,
            "fps": 25.0,
            "size_bytes": 4096,
        },
    )
    store.save_clips("abc123", [dict(CLIP)], {"generator": "heuristic"})
    return client


def _stored_project(tmp_path: Path) -> dict:
    path = tmp_path / "workspace" / "projects" / "abc123" / "project.json"
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------- transitions


def test_every_transition_kind_and_edge_produces_a_filter() -> None:
    """Each kind has to draw something at each edge, or the track is a dead button."""

    for kind in ("fade", "slide-left", "wipe-left"):
        for at in ("start", "end"):
            fragment = transition_filter(
                kind,
                at=at,
                duration=0.5,
                width=1080,
                height=1920,
                clip_duration=10.0,
            )
            assert fragment, f"{kind} at {at} produced no filter"
            # Nothing but digits, letters and punctuation the filter parser accepts.
            assert "{" not in fragment and "}" not in fragment


def test_a_transition_longer_than_half_the_clip_is_clamped_not_drawn() -> None:
    """An over-long request is bounded, never drawn at its asked-for length."""

    from app.transitions import MAX_SECONDS

    # Both bounds apply: the 2s ceiling, and half the clip.
    assert normalise_duration(9.0, clip_duration=10.0) == MAX_SECONDS
    assert normalise_duration(4.0, clip_duration=3.0) == 1.5
    # Nothing in, nothing out: an unset transition is a plain cut.
    assert normalise_duration(0.0, clip_duration=10.0) == 0.0
    assert (
        transition_filter(
            "fade", at="start", duration=0.0, width=640, height=360, clip_duration=10.0
        )
        is None
    )
    # A request for half the clip is shortened to the ceiling rather than drawn
    # across five seconds of a ten-second clip.
    fragment = transition_filter(
        "fade", at="start", duration=5.0, width=640, height=360, clip_duration=10.0
    )
    assert fragment == f"fade=t=in:st=0:d={MAX_SECONDS:.3f}"

    # But on a clip too short to hold any transition, nothing is drawn at all.
    assert (
        transition_filter(
            "fade", at="start", duration=0.5, width=640, height=360, clip_duration=0.4
        )
        is None
    )


def test_an_end_transition_is_anchored_to_the_end_of_the_clip() -> None:
    fragment = transition_filter(
        "fade", at="end", duration=0.5, width=640, height=360, clip_duration=10.0
    )
    assert "st=9.500" in fragment


def test_the_chain_puts_start_transitions_before_end_ones() -> None:
    chain = build_transition_chain(
        [
            {"kind": "fade", "at": "end", "duration": 0.5},
            {"kind": "wipe-left", "at": "start", "duration": 0.4},
        ],
        width=640,
        height=360,
        clip_duration=10.0,
    )
    assert chain.index("drawbox") < chain.index("fade=t=out")


def test_two_transitions_of_the_same_kind_at_one_edge_do_not_stack() -> None:
    """Drawing both would be an accident of ordering rather than an edit."""

    chain = build_transition_chain(
        [
            {"kind": "fade", "at": "start", "duration": 0.5},
            {"kind": "fade", "at": "start", "duration": 0.3},
        ],
        width=640,
        height=360,
        clip_duration=10.0,
    )
    assert chain.count("fade=t=in") == 1


def test_no_transition_leaves_the_chain_empty() -> None:
    assert build_transition_chain([], width=640, height=360, clip_duration=10.0) == ""


def test_the_transition_note_names_the_edge_in_plain_language() -> None:
    note = transition_note([{"kind": "slide-left", "at": "end", "duration": 0.5}])
    assert "Slide" in note and "out" in note
    assert transition_note([]) == ""


def test_a_transition_alone_reaches_the_rendered_filter_chain() -> None:
    chain = build_filter_chain(
        1080, 1920, None, duration=10.0, transitions=[{"kind": "fade", "at": "start", "duration": 0.5}]
    )
    assert "fade=t=in" in chain
    # Transitions come after the scale so they cover the finished picture.
    assert chain.index("crop=1080:1920") < chain.index("fade=t=in")


def test_transitions_apply_after_broll_is_composited() -> None:
    """A fade must not let an insert hang over black."""

    filters = build_composite_filters(
        width=640,
        height=360,
        duration=10.0,
        broll=[{"item": {"start": 1.0, "end": 2.0}, "index": 1}],
        transitions=[{"kind": "fade", "at": "start", "duration": 0.5}],
    )
    graph = filters["graph"]
    assert graph.index("overlay") < graph.rindex("fade=t=in")


# ----------------------------------------------------------------- the edit


def test_a_transition_is_now_stored_because_the_renderer_can_draw_it() -> None:
    """Milestone 9 is the milestone that made this track real."""

    patch = apply_timeline_patch(
        _project_document(),
        dict(CLIP),
        {"clip_id": "clip-1", "transitions": [{"kind": "fade", "at": "start", "duration": 0.5}]},
        current_style=CAPTION_STYLE,
        current_export=EXPORT,
    )
    assert patch["tracks"]["transitions"][0]["kind"] == "fade"


def test_nothing_is_left_unrendered_now() -> None:
    from app.timeline import UNRENDERED_TRACKS

    assert UNRENDERED_TRACKS == {}


def test_an_invalid_transition_is_still_refused() -> None:
    with pytest.raises(ApiError):
        apply_timeline_patch(
            _project_document(),
            dict(CLIP),
            {"clip_id": "clip-1", "transitions": [{"kind": "explode", "at": "start", "duration": 0.5}]},
            current_style=CAPTION_STYLE,
            current_export=EXPORT,
        )


def test_the_timeline_shows_the_sound_design_settings_and_proposals() -> None:
    project = _project_document(
        timeline={
            "broll": [{"id": "broll-1", "source": "source", "start": 2.0, "end": 4.0}],
            "transitions": [{"id": "transition-1", "kind": "fade", "at": "end", "duration": 0.5}],
            "sound_design": {"enabled": True, "kind": "whoosh", "max_effects": 2},
        }
    )
    timeline = build_timeline(project, CLIP, caption_style=CAPTION_STYLE, export=EXPORT_PLAN)

    sound = timeline["sound_design"]
    assert sound["enabled"] is True
    assert sound["kind"] == "whoosh"
    assert len(sound["proposed"]) >= 1
    # The proposals follow the real picture changes rather than being invented.
    assert all(0.0 <= float(e["start"]) < 10.0 for e in sound["proposed"])


def test_a_project_saved_before_milestone_nine_gets_sound_design_defaults() -> None:
    stored = stored_timeline(_project_document())
    assert stored["sound_design"] == {"enabled": False, "kind": "whoosh", "max_effects": 2}


# ------------------------------------------------------------ sound design


def test_the_toggle_is_off_by_default() -> None:
    """Automatic sound is opt-in: the export should never surprise the user."""

    assert normalise_settings(None)["enabled"] is False


def test_the_toggle_removes_every_proposal() -> None:
    timeline = {"broll": [{"start": 2.0, "end": 4.0}], "transitions": []}
    assert propose_auto_sfx(timeline, clip_duration=10.0, enabled=False) == []
    assert propose_auto_sfx(timeline, clip_duration=10.0, enabled=True)


@pytest.mark.parametrize(
    "bad",
    [{"enabled": "yes"}, {"enabled": 1}, {"kind": "boom"}, {"max_effects": -1}, {"max_effects": 99}],
)
def test_bad_sound_design_settings_are_refused_with_a_reason(bad: dict) -> None:
    with pytest.raises(ApiError) as error:
        normalise_settings(bad)
    assert "sound_design" in error.value.message


def test_a_valid_choice_is_kept() -> None:
    for choice in AUTO_SFX_COUNT_CHOICES:
        assert normalise_settings({"max_effects": choice})["max_effects"] == choice


def test_an_unknown_effect_kind_is_refused() -> None:
    with pytest.raises(ApiError):
        propose_auto_sfx({}, clip_duration=10.0, enabled=True, kind="explosion")
    with pytest.raises(ApiError):
        build_synth_command("explosion", 0.5, Path("x.wav"))


def test_effects_land_on_picture_changes_and_nowhere_else() -> None:
    timeline = {
        "broll": [{"start": 2.0, "end": 4.0}],
        "transitions": [{"kind": "fade", "at": "end", "duration": 0.5}],
    }
    effects = propose_auto_sfx(timeline, clip_duration=10.0, enabled=True)

    # A normal effect leads its marker by LEAD_SECONDS, so the marker is recovered
    # from the start. The end marker is the exception: its effect is anchored to
    # finish on the cut, so it is recovered from the effect's end.
    markers = set()
    for effect in effects:
        start = float(effect["start"])
        end = float(effect["end"])
        if abs(end - 10.0) < 0.01:
            markers.add(round(end, 2))
        else:
            markers.add(round(start + LEAD_SECONDS, 2))
    # The B-roll entrance (2.0s), the B-roll exit (4.0s), and the clip's end (10.0s).
    assert markers == {2.0, 4.0, 10.0}


def test_an_end_transition_alone_still_places_an_effect() -> None:
    """A transition at the clip's end is a picture change, so it gets an effect.

    The effect must finish on the cut rather than begin there: an effect that
    started at the clip's very end would never be heard.
    """

    effects = propose_auto_sfx(
        {
            "broll": [],
            "transitions": [{"kind": "fade", "at": "end", "duration": 0.5}],
        },
        clip_duration=6.0,
        enabled=True,
    )

    assert len(effects) == 1
    effect = effects[0]
    assert effect["end"] == 6.0
    assert round(6.0 - float(effect["start"]), 3) == AUTO_SFX_DURATION_SECONDS
    assert float(effect["start"]) < 6.0


def test_sound_design_never_buries_the_speech() -> None:
    """The whole point of the gain ceiling is that speech stays the loudest thing."""

    assert gain_db() <= SFX_MAX_GAIN_DB
    for effect in propose_auto_sfx(
        {"broll": [{"start": 2.0, "end": 4.0}]}, clip_duration=10.0, enabled=True
    ):
        assert effect["gain_db"] <= SFX_MAX_GAIN_DB


def test_overlapping_cuts_do_not_produce_a_stampede() -> None:
    timeline = {
        "broll": [
            {"start": 2.0, "end": 3.0},
            {"start": 2.05, "end": 3.05},
            {"start": 2.1, "end": 3.1},
        ]
    }
    effects = propose_auto_sfx(timeline, clip_duration=10.0, enabled=True, max_effects=4)
    assert len(effects) <= 2


def test_the_number_of_effects_is_capped_even_with_many_cuts() -> None:
    timeline = {"broll": [{"start": float(index), "end": float(index) + 0.5} for index in range(1, 20)]}
    for limit in AUTO_SFX_COUNT_CHOICES:
        effects = propose_auto_sfx(
            timeline, clip_duration=30.0, enabled=True, max_effects=limit
        )
        assert len(effects) == min(limit, MAX_AUTO_SFX)


def test_an_edit_with_nothing_happening_gets_no_automatic_effects() -> None:
    """No cuts, no cuts-and-transitions, no sound design."""

    assert propose_auto_sfx({"broll": [], "transitions": []}, clip_duration=10.0, enabled=True) == []


def test_the_sound_design_note_is_readable() -> None:
    effects = propose_auto_sfx({"broll": [{"start": 2.0, "end": 4.0}]}, clip_duration=10.0, enabled=True)
    assert "under the speech" in sound_design_note(effects)
    assert sound_design_note([]) == ""


# ------------------------------------------------------------- API + saved


def test_the_edit_reaches_the_saved_project_json(tmp_path: Path) -> None:
    client = _project(tmp_path)

    response = client.patch(
        "/api/projects/abc123/timeline",
        json={
            "clip_id": "clip-1",
            "transitions": [
                {"kind": "fade", "at": "start", "duration": 0.5},
                {"kind": "wipe-left", "at": "end", "duration": 0.4},
            ],
            "sound_design": {"enabled": True, "kind": "pop", "max_effects": 3},
        },
    )

    assert response.status_code == 200, response.text
    stored = _stored_project(tmp_path)
    assert len(stored["timeline"]["transitions"]) == 2
    assert stored["timeline"]["sound_design"] == {
        "enabled": True,
        "kind": "pop",
        "max_effects": 3,
    }
    # The response is re-read from the saved project, so it matches the file.
    assert len(response.json()["transitions"]) == 2


def test_a_refused_sound_design_setting_changes_nothing(tmp_path: Path) -> None:
    client = _project(tmp_path)
    before = _stored_project(tmp_path)

    response = client.patch(
        "/api/projects/abc123/timeline",
        json={"clip_id": "clip-1", "sound_design": {"kind": "explosion"}},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_sound_design"
    assert _stored_project(tmp_path)["timeline"] == before.get("timeline", {})


def test_switching_the_toggle_off_saves_the_choice(tmp_path: Path) -> None:
    client = _project(tmp_path)

    client.patch(
        "/api/projects/abc123/timeline",
        json={"clip_id": "clip-1", "sound_design": {"enabled": True}},
    )
    assert _stored_project(tmp_path)["timeline"]["sound_design"]["enabled"] is True

    client.patch(
        "/api/projects/abc123/timeline",
        json={"clip_id": "clip-1", "sound_design": {"enabled": False}},
    )
    assert _stored_project(tmp_path)["timeline"]["sound_design"]["enabled"] is False


# -------------------------------------------------------- real FFmpeg runs

requires_ffmpeg = pytest.mark.skipif(
    not ffmpeg_available(),
    reason="FFmpeg is not on PATH, so a real render cannot be verified.",
)


def _ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _mean_luma(video: Path, at: float, duration: float = 0.08) -> float:
    """Mean luma of the whole frame over a short window, measured by FFmpeg."""

    completed = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-v", "info",
            "-ss", f"{at:.3f}", "-t", f"{duration:.3f}",
            "-i", str(video),
            "-vf", "signalstats,metadata=print",
            "-f", "null", "-",
        ],
        capture_output=True, text=True, timeout=180,
    )
    values = [
        float(line.split("=")[1])
        for line in completed.stderr.split("\n")
        if "lavfi.signalstats.YAVG" in line and "=" in line
    ]
    assert values, "FFmpeg reported no luma, so the frame was never measured."
    return sum(values) / len(values)


@requires_ffmpeg
def test_every_transition_filter_renders_in_real_ffmpeg(tmp_path: Path) -> None:
    """A filter string that FFmpeg rejects is not a feature."""

    from app.transitions import MAX_SECONDS

    for kind in ("fade", "slide-left", "wipe-left"):
        chain = build_transition_chain(
            [{"kind": kind, "at": "start", "duration": 0.4}],
            width=640,
            height=360,
            clip_duration=4.0,
        )
        output = tmp_path / f"{kind}.mp4"
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=gray:size=640x360:rate=25:duration=4",
                "-vf", chain, "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", str(output),
            ],
            check=True, capture_output=True, text=True, timeout=180,
        )
        assert output.is_file() and output.stat().st_size > 0, f"{kind} produced no video"
    assert MAX_SECONDS > 0


@requires_ffmpeg
def test_each_effect_kind_synthesises_real_audio(tmp_path: Path) -> None:
    for kind in AUTO_SFX_KINDS:
        output = tmp_path / f"{kind}.wav"
        render_synth_into(kind, 0.7, output)

        assert output.is_file(), f"{kind} produced no file"
        assert output.stat().st_size > 1000, f"{kind} wrote an empty file"

        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_name,sample_rate,channels", "-of", "default=nw=1", str(output)],
            capture_output=True, text=True, timeout=60,
        )
        assert "pcm_s16le" in probe.stdout
        assert "sample_rate=48000" in probe.stdout


@requires_ffmpeg
def test_the_whoosh_really_swells(tmp_path: Path) -> None:
    """The envelope is the point of a whoosh, so measure it rather than assume it."""

    output = tmp_path / "whoosh.wav"
    render_synth_into("whoosh", 0.7, output)

    def mean_volume(at: float) -> float:
        completed = subprocess.run(
            ["ffmpeg", "-hide_banner", "-v", "info", "-ss", f"{at:.3f}", "-t", "0.14",
             "-i", str(output), "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, timeout=120,
        )
        for line in completed.stderr.split("\n"):
            if "mean_volume" in line:
                return float(line.split("mean_volume:")[1].strip().split()[0])
        raise AssertionError("no volume was measured")

    head, middle, tail = mean_volume(0.0), mean_volume(0.28), mean_volume(0.6)
    assert middle > head and middle > tail, (
        f"a whoosh must be loudest in the middle, got {head} / {middle} / {tail}"
    )


@requires_ffmpeg
def test_a_fade_really_darkens_the_rendered_file(tmp_path: Path) -> None:
    """Measure the exported frames: the start must go to black and then recover."""

    source = tmp_path / "gray.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "color=c=gray:size=640x360:rate=25:duration=4",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(source),
    )

    plain = tmp_path / "plain.mp4"
    _ffmpeg("-i", str(source), "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", str(plain))

    faded = tmp_path / "faded.mp4"
    chain = build_transition_chain(
        [{"kind": "fade", "at": "start", "duration": 1.0}],
        width=640, height=360, clip_duration=4.0,
    )
    _ffmpeg("-i", str(source), "-vf", chain, "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", str(faded))

    plain_luma = _mean_luma(plain, 0.0)
    faded_luma = _mean_luma(faded, 0.0)
    recovered_luma = _mean_luma(faded, 2.0)

    assert faded_luma < plain_luma / 2, (
        f"the first frame should be black, got {faded_luma} against {plain_luma}"
    )
    assert recovered_luma > faded_luma * 2, "the clip should recover after the fade"


@requires_ffmpeg
def test_a_mix_with_no_source_audio_is_not_cut_short() -> None:
    """A later effect must not be dropped just because the source is silent.

    ``amix=duration=first`` ends the mix when its first input does. With speech in
    the mix that input spans the whole clip, so it is right; with a silent source
    the first input is one effect, and ``first`` would silently cut the rest.
    """

    without_speech = build_composite_filters(
        width=640, height=360, duration=10.0,
        sfx=[{"item": {"start": 1.0, "end": 2.0}, "index": 1},
             {"item": {"start": 5.0, "end": 6.0}, "index": 2}],
        source_has_audio=False,
    )
    assert "duration=longest" in without_speech["graph"]
    # And the track is padded to the clip, so the export is not a stub.
    assert "apad" in without_speech["graph"]
    assert "atrim=end=10.000" in without_speech["graph"]

    with_speech = build_composite_filters(
        width=640, height=360, duration=10.0,
        sfx=[{"item": {"start": 1.0, "end": 2.0}, "index": 1},
             {"item": {"start": 5.0, "end": 6.0}, "index": 2}],
        source_has_audio=True,
    )
    assert "duration=first" in with_speech["graph"]


def test_an_automatic_effect_is_not_attenuated_twice() -> None:
    """The gain belongs to the mix graph or to the file, never to both.

    A whoosh does use ``volume`` for its swell, so the check is specifically for
    the *constant* gain the mix graph is going to apply from ``gain_db``.
    """

    import re

    from app.sound_design import build_synth_command

    constant_gain = re.compile(r"volume=(\d*\.?\d+)")
    for kind in AUTO_SFX_KINDS:
        command = build_synth_command(kind, 0.7, Path("x.wav"))
        graph = command[command.index("-i") + 1]
        # A plain number would re-apply gain_db on top of the mix graph's own.
        assert not constant_gain.search(graph), (
            f"{kind} bakes a fixed gain into the file, which the mix then applies again"
        )
        # -18 dB applied twice would be -36 dB, effectively inaudible.
        assert f"volume={10 ** (gain_db() / 20.0):.4f}" not in graph


def test_synthesised_effects_mix_under_the_speech(tmp_path: Path) -> None:
    """An effect must be audible in its own window and must not lift the rest."""

    from app.render import build_composite_filters

    # A silent source is the honest floor: anything measured above it came from
    # the automatic effect alone.
    source = tmp_path / "silent.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "color=c=black:size=640x360:rate=25:duration=6",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(source),
    )

    effects = propose_auto_sfx(
        {"broll": [{"start": 2.0, "end": 3.0}]}, clip_duration=6.0, enabled=True
    )
    assert effects

    entries = []
    for position, effect in enumerate(effects, start=1):
        path = tmp_path / f"auto-{position}.wav"
        render_synth_into(str(effect["kind"]),
                          float(effect["end"]) - float(effect["start"]), path)
        entries.append({"item": {**effect, "path": path}, "index": position})

    filters = build_composite_filters(
        width=640, height=360, duration=6.0,
        sfx=entries, source_has_audio=False,
    )
    assert filters["audio"], "the effect should still produce an audio track"

    mixed = tmp_path / "mixed.mp4"
    inputs: list[str] = []
    for position, effect in enumerate(effects, start=1):
        inputs += ["-i", str(tmp_path / f"auto-{position}.wav")]

    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source), *inputs,
            "-filter_complex", filters["graph"],
            "-map", filters["video"], "-map", filters["audio"],
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(mixed),
        ],
        check=True, capture_output=True, text=True, timeout=180,
    )

    def mean_volume(at: float, window: float = 0.3) -> float:
        completed = subprocess.run(
            ["ffmpeg", "-hide_banner", "-v", "info", "-ss", f"{at:.3f}", "-t", f"{window:.3f}",
             "-i", str(mixed), "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, timeout=120,
        )
        for line in completed.stderr.split("\n"):
            if "mean_volume" in line:
                return float(line.split("mean_volume:")[1].strip().split()[0])
        raise AssertionError("no volume was measured")

    inside = mean_volume(float(effects[0]["start"]) + 0.1)
    # The source is silent, so anywhere the effect is not playing is the floor.
    # 5.2s is still inside the 6s clip but long after the last effect.
    outside = mean_volume(5.2)
    assert inside > outside + 6, (
        f"the effect should only be audible in its own window: {inside} vs {outside}"
    )