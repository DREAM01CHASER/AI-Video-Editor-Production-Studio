"""Milestone 5 acceptance checks for exporting a captioned clip.

The validation and command-building tests are offline and deterministic. The
render tests at the end run **real FFmpeg**: they synthesise a short black video,
render it through the same code path the API uses, then measure the caption area
of the output so "the captions were burned in" is a measurement rather than an
assumption. They are skipped with an explicit reason when FFmpeg is unavailable.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import api as api_module
from app.config import Settings
from app.errors import ApiError
from app.main import create_app
from app.render import (
    build_ffmpeg_command,
    build_filter_chain,
    escape_filter_path,
    ffmpeg_available,
    normalise_export,
    parse_progress_line,
    progress_percent,
    run_ffmpeg_render,
    subtitle_filter_value,
)

TRANSCRIPT_GENERATED_AT = "2026-01-01T00:00:00+00:00"

SPOKEN_ROWS = [
    (1.0, 5.0, "the five minute editing rule is the one thing"),
    (5.0, 9.0, "nobody follows and it shows up in retention"),
    (9.0, 14.0, "so set a timer and stop when it goes off"),
]

CLIPS = [
    {
        "id": "clip-1",
        "title": "The five minute rule",
        "reason": "Self-contained tip with a number in the opening line.",
        "score": 0.71,
        "source_start": 0.5,
        "source_end": 8.5,
        "duration": 8.0,
        "signals": {"hook": 0.5, "density": 1.0, "self_contained": 0.5},
    }
]


# ----------------------------------------------------------------- helpers


def _client(tmp_path: Path, **overrides: object) -> TestClient:
    settings = Settings(
        project_root=Path(__file__).resolve().parents[1],
        workspace_dir=tmp_path / "workspace",
        max_upload_size_bytes=50 * 1024 * 1024,
        **overrides,
    )
    return TestClient(create_app(settings), raise_server_exceptions=False)


def transcript_document() -> dict:
    return {
        "version": 1,
        "project_id": "abc123",
        "generated_at": TRANSCRIPT_GENERATED_AT,
        "segments": [
            {"id": f"seg-{index + 1}", "start": start, "end": end, "text": text}
            for index, (start, end, text) in enumerate(SPOKEN_ROWS)
        ],
    }


def _project(
    tmp_path: Path,
    *,
    with_clips: bool = True,
    with_transcript: bool = True,
    source_name: str = "source-x.mp4",
    **overrides: object,
) -> TestClient:
    """A project with a (possibly fake) source file, transcript, and clips."""

    client = _client(tmp_path, **overrides)
    store = client.app.state.projects
    store.write_new(
        "abc123",
        name="Export test",
        source_video=f"source/{source_name}",
        source_metadata={
            "duration": 30.0,
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "size_bytes": 4096,
        },
    )
    workspace = store.project_workspace("abc123")
    (workspace.source / source_name).write_bytes(b"0")

    if with_transcript:
        store.save_transcript("abc123", transcript_document())
    if with_clips:
        store.save_clips("abc123", CLIPS, {"generator": "heuristic"})
    return client


def _run_job(client: TestClient, job_id: str, timeout: float = 240.0) -> dict:
    """Poll a background job until it reaches a terminal state."""

    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = client.get(f"/api/jobs/{job_id}").json()
        if payload["status"] in {"succeeded", "failed", "cancelled"}:
            return payload
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


# ------------------------------------------------------ export settings


def test_export_defaults_to_1080x1920_vertical_mp4() -> None:
    plan = normalise_export(None)

    assert plan["resolution"] == "1080x1920"
    assert (plan["width"], plan["height"]) == (1080, 1920)
    assert plan["aspect_ratio"] == "9:16"
    assert plan["format"] == "mp4"
    assert plan["caption_mode"] == "burn"
    assert plan["upscaled"] is False


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"resolution": "720x1280"}, "resolution must be"),
        ({"aspect_ratio": "4:5"}, "aspect_ratio must be"),
        # A square size on a vertical export would be stretched, so it is refused.
        ({"resolution": "1080x1080", "aspect_ratio": "9:16"}, "cannot be exported as"),
        ({"format": "gif"}, "format must be"),
        ({"caption_mode": "sidecar"}, "caption_mode must be"),
        ({"write_srt": "yes"}, "write_srt must be"),
    ],
)
def test_export_settings_reject_values_the_renderer_cannot_honour(
    payload: dict, message: str
) -> None:
    with pytest.raises(ApiError) as error:
        normalise_export(payload)

    assert error.value.code == "invalid_export_settings"
    assert message in error.value.message


def test_a_2k_export_of_a_1080p_source_is_described_as_an_upscale() -> None:
    plan = normalise_export(
        {"resolution": "1440x2560", "aspect_ratio": "9:16"},
        source_width=1920,
        source_height=1080,
    )

    assert plan["upscaled"] is True
    assert any("upscaled" in note for note in plan["notes"])


def test_a_source_as_tall_as_the_output_is_not_called_an_upscale() -> None:
    plan = normalise_export(
        {"resolution": "1440x2560", "aspect_ratio": "9:16"},
        source_width=2160,
        source_height=3840,
    )

    assert plan["upscaled"] is False


def test_switching_captions_off_is_stated_in_the_plan() -> None:
    plan = normalise_export({"caption_mode": "none"})

    assert any("Captions are switched off" in note for note in plan["notes"])


# ------------------------------------------------------- filter and command


def test_filter_chain_scales_to_cover_then_crops_the_centre() -> None:
    assert build_filter_chain(1080, 1920) == (
        "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"
    )


def test_filter_chain_only_mentions_subtitles_when_captions_are_on() -> None:
    assert "ass=" not in build_filter_chain(1080, 1920)
    assert build_filter_chain(1080, 1920, "captions-clip-1.ass").endswith(
        "ass=captions-clip-1.ass"
    )


def test_filter_path_escaping_handles_windows_drive_paths() -> None:
    escaped = escape_filter_path(r"C:\Users\me\out\captions.ass")

    assert escaped == r"'C\:/Users/me/out/captions.ass'"
    # A bare filename is left alone so the filter value stays readable.
    assert subtitle_filter_value("captions-clip-1.ass") == "captions-clip-1.ass"
    assert subtitle_filter_value(r"C:\tmp\a.ass").startswith("'")


def test_ffmpeg_command_seeks_before_reading_the_input() -> None:
    command = build_ffmpeg_command(
        source_path=Path("source.mp4"),
        output_path=Path("out.mp4"),
        start=12.5,
        end=20.0,
        width=1080,
        height=1920,
        subtitle_name="captions-clip-1.ass",
    )

    # Seeking first is what keeps a long source from being decoded end to end.
    assert command.index("-ss") < command.index("-i")
    assert command[command.index("-ss") + 1] == "12.500"
    assert command[command.index("-t") + 1] == "7.500"


def test_ffmpeg_command_sets_vertical_output_audio_and_progress() -> None:
    command = build_ffmpeg_command(
        source_path=Path("source.mp4"),
        output_path=Path("out.mp4"),
        start=0.0,
        end=5.0,
        width=1080,
        height=1920,
    )

    assert command[command.index("-c:v") + 1] == "libx264"
    assert command[command.index("-c:a") + 1] == "aac"
    assert command[command.index("-pix_fmt") + 1] == "yuv420p"
    assert command[command.index("-movflags") + 1] == "+faststart"
    assert command[command.index("-progress") + 1] == "pipe:1"
    assert command[command.index("-vf") + 1].startswith("scale=1080:1920")
    # The output path is last, and no shell string is involved anywhere.
    assert command[-1] == "out.mp4"
    assert all(isinstance(part, str) for part in command)


def test_a_negative_start_is_clamped_instead_of_passed_through() -> None:
    command = build_ffmpeg_command(
        source_path=Path("source.mp4"),
        output_path=Path("out.mp4"),
        start=-3.0,
        end=5.0,
        width=1080,
        height=1920,
    )

    assert command[command.index("-ss") + 1] == "0.000"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("out_time_us=1500000", ("out_time_us", "1500000")),
        ("progress=end", ("progress", "end")),
        ("", None),
        ("not a pair", None),
    ],
)
def test_progress_lines_are_parsed(line: str, expected: tuple[str, str] | None) -> None:
    assert parse_progress_line(line) == expected


def test_progress_stops_short_of_one_hundred_percent() -> None:
    assert progress_percent(0, 10.0) == 0.0
    assert progress_percent(5_000_000, 10.0) == 50.0
    # The bar must not claim to be done while FFmpeg is still writing the file.
    assert progress_percent(60_000_000, 10.0) == 99.0
    assert progress_percent(1_000_000, 0.0) == 0.0


# --------------------------------------------------------------- API rules


def test_render_needs_a_clip_id(tmp_path: Path) -> None:
    client = _project(tmp_path)
    response = client.post("/api/projects/abc123/render", json={})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_clip_id"


def test_render_explains_a_project_without_candidates(tmp_path: Path) -> None:
    client = _project(tmp_path, with_clips=False)
    response = client.post(
        "/api/projects/abc123/render", json={"clip_id": "clip-1"}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "no_clips"


def test_render_rejects_an_unknown_clip(tmp_path: Path) -> None:
    client = _project(tmp_path)
    response = client.post(
        "/api/projects/abc123/render", json={"clip_id": "clip-99"}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "clip_not_found"


def test_render_reports_missing_ffmpeg_in_plain_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _project(tmp_path)
    monkeypatch.setattr(api_module, "ffmpeg_available", lambda: False)

    response = client.post(
        "/api/projects/abc123/render", json={"clip_id": "clip-1"}
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ffmpeg_unavailable"
    assert "FFmpeg" in response.json()["error"]["message"]


def test_render_rejects_export_settings_that_do_not_match(tmp_path: Path) -> None:
    client = _project(tmp_path)
    response = client.post(
        "/api/projects/abc123/render",
        json={
            "clip_id": "clip-1",
            "export": {"resolution": "1080x1080", "aspect_ratio": "9:16"},
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_export_settings"


def test_settings_are_validated_and_saved_for_the_next_render(tmp_path: Path) -> None:
    client = _project(tmp_path)

    saved = client.patch(
        "/api/projects/abc123/settings",
        json={
            "caption_style": {"preset": "minimal", "font_size": "large"},
            "export": {"resolution": "1440x2560", "aspect_ratio": "9:16"},
        },
    )

    assert saved.status_code == 200
    payload = saved.json()
    assert payload["caption_style"]["preset"] == "minimal"
    assert payload["caption_style"]["font_size"] == "large"
    assert payload["export"]["resolution"] == "1440x2560"
    # The upscale warning comes from the same code that builds the FFmpeg command.
    assert any("upscaled" in note for note in payload["plan"]["notes"])

    reopened = client.get("/api/projects/abc123").json()
    assert reopened["caption_style"]["preset"] == "minimal"
    assert reopened["export"]["resolution"] == "1440x2560"


def test_settings_reject_a_bad_caption_preset(tmp_path: Path) -> None:
    client = _project(tmp_path)
    response = client.patch(
        "/api/projects/abc123/settings", json={"caption_style": {"preset": "sparkly"}}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_caption_style"


def test_caption_preview_starts_at_zero_for_the_clip(tmp_path: Path) -> None:
    client = _project(tmp_path)
    response = client.get("/api/projects/abc123/captions?clip_id=clip-1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["cue_count"] == len(payload["cues"])
    assert len(payload["cues"]) > 0
    # The clip starts at 0.5s, and the first speech starts at 1.0s.
    assert payload["cues"][0]["start"] == pytest.approx(0.5, abs=0.01)
    assert payload["srt"].startswith("1\n00:00:00,500 --> ")
    assert payload["geometry"]["max_line_chars"] > 0


def test_caption_preview_needs_a_known_clip(tmp_path: Path) -> None:
    client = _project(tmp_path)

    assert (
        client.get("/api/projects/abc123/captions?clip_id=nope").status_code == 404
    )
    assert client.get("/api/projects/abc123/captions").status_code == 422


def test_exports_start_empty_and_then_list_the_render(tmp_path: Path) -> None:
    client = _project(tmp_path)
    listing = client.get("/api/projects/abc123/exports").json()

    assert listing["exports"] == []
    assert listing["latest"] is None
    assert listing["export_settings"]["resolution"] == "1080x1920"


@pytest.mark.parametrize(
    ("name", "status"),
    [
        # A traversal attempt must never reach the filesystem; the name is rejected
        # before any path is resolved.
        ("..%5Cproject.json", 400),
        ("%2E%2E%2Fproject.json", 404),  # collapsed during routing, still refused
        ("project.json", 400),  # the project document is not an export
        ("missing.mp4", 404),
    ],
)
def test_export_downloads_stay_inside_the_exports_folder(
    tmp_path: Path, name: str, status: int
) -> None:
    client = _project(tmp_path)
    assert client.get(f"/api/projects/abc123/exports/{name}").status_code == status


# -------------------------------------------------------- real FFmpeg run

requires_ffmpeg = pytest.mark.skipif(
    not ffmpeg_available(),
    reason="FFmpeg is not on PATH, so a real render cannot be verified.",
)

CAPTION_CROP = "1080:500:0:1300"


def _ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _black_source(path: Path, seconds: int = 12) -> None:
    """A black video, so any bright pixel in the export is a burned-in caption."""

    path.parent.mkdir(parents=True, exist_ok=True)
    _ffmpeg(
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:size=640x360:rate=25:duration={seconds}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={seconds}",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(path),
    )


def _caption_area_luma(video: Path, at: float) -> float:
    """Average luma of the bottom caption area, measured by FFmpeg itself."""

    completed = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-v",
            "info",
            "-ss",
            str(at),
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            f"crop={CAPTION_CROP},signalstats,metadata=print",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )

    values = re.findall(
        r"signalstats\.YAVG=([0-9.]+)", completed.stdout + completed.stderr
    )
    assert values, f"FFmpeg reported no luma statistics: {completed.stderr[-400:]}"
    return max(float(value) for value in values)


def _probe(path: Path) -> dict:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return json.loads(completed.stdout)


@requires_ffmpeg
def test_a_real_render_writes_a_captioned_vertical_mp4(tmp_path: Path) -> None:
    client = _project(tmp_path)
    source = tmp_path / "workspace" / "projects" / "abc123" / "source" / "source-x.mp4"
    _black_source(source)

    started = client.post(
        "/api/projects/abc123/render",
        json={"clip_id": "clip-1"},
    )
    assert started.status_code == 202

    job = _run_job(client, started.json()["job_id"])
    assert job["status"] == "succeeded", job.get("error")
    result = job["result"]

    # The file is really on disk, in the project's own exports folder.
    output = tmp_path / "workspace" / "projects" / "abc123" / "exports" / result["file"]
    assert output.is_file()
    assert output.stat().st_size > 0
    assert result["size_bytes"] == output.stat().st_size

    # Vertical 1080 × 1920 with the clip's duration and an audio track.
    probed = _probe(output)
    video = next(s for s in probed["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1080, 1920)
    assert any(s["codec_type"] == "audio" for s in probed["streams"])
    assert float(probed["format"]["duration"]) == pytest.approx(8.0, abs=0.3)

    # The captions are burned in: a black source is no longer black there.
    captioned = _caption_area_luma(output, at=4.0)
    assert captioned > 18.0, f"no captions appear in the export (luma {captioned})"

    # And the sidecar subtitle file is a real, parseable SRT.
    sidecar = tmp_path / "workspace" / "projects" / "abc123" / "exports" / result["srt_file"]
    assert sidecar.is_file()
    body = sidecar.read_text(encoding="utf-8")
    assert body.startswith("1\n00:00:00,500 --> ")
    assert result["cue_count"] == body.count("-->")
    assert result["font_warnings"] == []

    # Downloading it through the API returns the same bytes the renderer wrote.
    download = client.get(result["url"])
    assert download.status_code == 200
    assert download.headers["content-type"] == "video/mp4"
    assert len(download.content) == result["size_bytes"]

    # A render with the captions switched off leaves that area black, so the
    # measurement above really is the captions and not the source video.
    plain = client.post(
        "/api/projects/abc123/render",
        json={"clip_id": "clip-1", "export": {"caption_mode": "none", "write_srt": False}},
    )
    plain_job = _run_job(client, plain.json()["job_id"])
    assert plain_job["status"] == "succeeded", plain_job.get("error")
    plain_output = (
        tmp_path
        / "workspace"
        / "projects"
        / "abc123"
        / "exports"
        / plain_job["result"]["file"]
    )
    assert plain_job["result"]["srt_file"] is None
    assert _caption_area_luma(plain_output, at=4.0) < captioned - 5.0

    # The subtitle script used by FFmpeg is temporary and is cleaned up after a
    # successful render, so the project's temp folder does not grow.
    temp = tmp_path / "workspace" / "projects" / "abc123" / "temp"
    assert list(temp.glob("*.ass")) == []

    # The project remembers both renders, newest first.
    listing = client.get("/api/projects/abc123/exports").json()
    assert [entry["file"] for entry in listing["exports"]] == [
        plain_job["result"]["file"],
        result["file"],
    ]
    assert all(entry["available"] for entry in listing["exports"])


@requires_ffmpeg
def test_a_devanagari_transcript_reports_caption_fonts_honestly(tmp_path: Path) -> None:
    """A non-Latin transcript must render, and say so if a font was missing."""

    client = _project(tmp_path)
    store = client.app.state.projects
    store.save_transcript(
        "abc123",
        {
            "version": 1,
            "project_id": "abc123",
            "generated_at": TRANSCRIPT_GENERATED_AT,
            "segments": [
                {"id": "seg-1", "start": 1.0, "end": 7.0, "text": "जब हमारा टोपिक इंशुरन्स फ्रॉड है"},
                {"id": "seg-2", "start": 7.0, "end": 12.0, "text": "आप कैसे बच सकते हैं इससे"},
            ],
        },
    )
    source = tmp_path / "workspace" / "projects" / "abc123" / "source" / "source-x.mp4"
    _black_source(source)

    started = client.post("/api/projects/abc123/render", json={"clip_id": "clip-1"})
    job = _run_job(client, started.json()["job_id"])

    assert job["status"] == "succeeded", job.get("error")
    result = job["result"]
    assert result["cue_count"] >= 2
    # Either the fonts were there, or the result says they were not. Both are
    # honest; silently dropping glyphs would not be.
    assert isinstance(result["font_warnings"], list)
    output = tmp_path / "workspace" / "projects" / "abc123" / "exports" / result["file"]
    assert _caption_area_luma(output, at=3.0) > 18.0


@requires_ffmpeg
def test_a_failed_render_keeps_no_half_written_file(tmp_path: Path) -> None:
    client = _project(tmp_path)
    # A file that FFprobe could read is not required here: the point is that a
    # source FFmpeg cannot decode must fail loudly and leave nothing behind.
    corrupt = tmp_path / "workspace" / "projects" / "abc123" / "source" / "source-x.mp4"
    corrupt.write_bytes(b"not really a video")

    started = client.post("/api/projects/abc123/render", json={"clip_id": "clip-1"})
    job = _run_job(client, started.json()["job_id"])

    assert job["status"] == "failed"
    assert job["error"]["code"] == "render_failed"
    exports = tmp_path / "workspace" / "projects" / "abc123" / "exports"
    assert list(exports.glob("*.mp4")) == []
    assert client.get("/api/projects/abc123/exports").json()["exports"] == []


@requires_ffmpeg
def test_cancelling_a_render_stops_ffmpeg_and_reports_it(tmp_path: Path) -> None:
    work = tmp_path / "render"
    work.mkdir()
    command = build_ffmpeg_command(
        source_path=tmp_path / "long.mp4",
        output_path=work / "long.mp4",
        start=0.0,
        end=60.0,
        width=1080,
        height=1920,
    )
    # A long synthetic source keeps FFmpeg busy long enough to cancel mid-render.
    _ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=640x360:rate=25:duration=60",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        str(tmp_path / "long.mp4"),
    )

    started = time.time()
    with pytest.raises(ApiError) as error:
        run_ffmpeg_render(
            command=command,
            clip_seconds=60.0,
            work_dir=work,
            should_cancel=lambda: True,
        )

    assert error.value.code == "render_cancelled"
    # Cancelling must end the process, not wait for the whole clip.
    assert time.time() - started < 30.0
