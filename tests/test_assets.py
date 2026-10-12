"""Milestone 7 acceptance checks for local B-roll and sound effects.

The model tests are pure. The import tests write real media with FFmpeg and then
read ``project.json`` from disk, and the render tests at the end measure the
exported file: a bright frame proves the B-roll insert was composited, and a
loudness measurement proves the effect was mixed and time-bounded.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.assets import (
    MAX_SFX_SECONDS,
    MAX_TAGS,
    allocate_asset_path,
    asset_from_import,
    media_type_for,
    normalise_kind,
    normalise_label,
    normalise_tags,
    search_assets,
    used_in_timeline,
    validate_asset_metadata,
)
from app.config import Settings
from app.errors import ApiError
from app.main import create_app
from app.media import MediaMetadata
from app.render import (
    build_composite_filters,
    broll_box,
    broll_fit_filter,
    ffmpeg_available,
    subprocess as render_subprocess,
)

TRANSCRIPT_GENERATED_AT = "2026-01-01T00:00:00+00:00"

CLIP = {
    "id": "clip-1",
    "title": "The five minute rule",
    "reason": "Self-contained tip.",
    "score": 0.71,
    "source_start": 0.0,
    "source_end": 8.0,
    "duration": 8.0,
}


def _metadata(**overrides: object) -> MediaMetadata:
    values = {
        "duration": 4.0,
        "size_bytes": 1024,
        "width": 1280,
        "height": 720,
        "fps": 30.0,
        "has_video": True,
        "has_audio": True,
    }
    values.update(overrides)
    return MediaMetadata(**values)  # type: ignore[arg-type]


# ------------------------------------------------------------- validation


@pytest.mark.parametrize(
    ("filename", "kind", "message"),
    [
        ("clip.mp4", "broll", None),
        ("clip.MOV", "broll", None),
        ("effect.wav", "sfx", None),
        ("effect.mp3", "sfx", None),
        ("effect.mp4", "sfx", "Unsupported sfx file type"),
        ("clip.txt", "broll", "Unsupported broll file type"),
        ("../escape.mp4", "broll", "filename is invalid"),
        ("sub/escape.mp4", "broll", "filename is invalid"),
    ],
)
def test_asset_filenames_are_validated_per_kind(
    filename: str, kind: str, message: str | None
) -> None:
    if message is None:
        assert validate_asset_metadata(filename, None, kind)
        return

    with pytest.raises(ApiError) as error:
        validate_asset_metadata(filename, None, kind)
    assert message in error.value.message


def test_a_conflicting_declared_type_is_refused() -> None:
    with pytest.raises(ApiError) as error:
        validate_asset_metadata("effect.wav", "video/mp4", "sfx")

    assert error.value.code == "invalid_asset"


def test_an_unknown_kind_is_refused() -> None:
    with pytest.raises(ApiError) as error:
        normalise_kind("music")

    assert error.value.code == "invalid_asset_kind"


def test_tags_are_normalised_deduplicated_and_bounded() -> None:
    assert normalise_tags("City, Night ,city") == ["city", "night"]
    assert normalise_tags(["  A  ", "b"]) == ["a", "b"]
    assert normalise_tags(None) == []

    with pytest.raises(ApiError):
        normalise_tags(",".join(f"tag{index}" for index in range(MAX_TAGS + 1)))

    with pytest.raises(ApiError):
        normalise_tags("x" * 40)


def test_labels_fall_back_and_are_trimmed() -> None:
    assert normalise_label("  Rooftop   run  ", "fallback") == "Rooftop run"
    assert normalise_label("", "clip-1") == "clip-1"
    assert len(normalise_label("x" * 200, "fallback")) <= 80


def test_an_asset_record_describes_the_file() -> None:
    asset = asset_from_import(
        asset_id="broll-1",
        kind="broll",
        label="Rooftop",
        stored_name="broll-abc.mp4",
        original_name="Rooftop.mp4",
        tags=["city"],
        metadata=_metadata(),
    )

    assert asset["kind"] == "broll"
    assert asset["file"] == "broll-abc.mp4"
    assert asset["original_name"] == "Rooftop.mp4"
    assert asset["duration"] == 4.0
    assert asset["created_at"]


@pytest.mark.parametrize(
    ("kind", "metadata", "message"),
    [
        ("broll", _metadata(has_video=False, height=0), "no video stream"),
        ("sfx", _metadata(has_audio=False), "no audio stream"),
        ("sfx", _metadata(duration=MAX_SFX_SECONDS + 5), "cannot be longer than"),
        ("sfx", _metadata(duration=0.0), "duration could not be read"),
    ],
)
def test_an_unusable_import_is_refused(kind: str, metadata: MediaMetadata, message: str) -> None:
    with pytest.raises(ApiError) as error:
        asset_from_import(
            asset_id="asset-1",
            kind=kind,
            label="Thing",
            stored_name="asset-1.mp4",
            original_name="thing.mp4",
            tags=[],
            metadata=metadata,
        )

    assert message in error.value.message


def test_search_filters_by_kind_and_text() -> None:
    assets = [
        {"id": "broll-1", "kind": "broll", "label": "Rooftop", "tags": ["city"], "created_at": "2026-01-02"},
        {"id": "broll-2", "kind": "broll", "label": "Beach", "tags": ["sunset"], "created_at": "2026-01-03"},
        {"id": "sfx-1", "kind": "sfx", "label": "Whoosh", "tags": ["transition"], "created_at": "2026-01-01"},
    ]

    assert [item["id"] for item in search_assets(assets, query="roof")] == ["broll-1"]
    assert [item["id"] for item in search_assets(assets, query="sunset")] == ["broll-2"]
    assert [item["id"] for item in search_assets(assets, kind="sfx")] == ["sfx-1"]
    # Newest first, so a freshly imported asset is easy to find.
    assert [item["id"] for item in search_assets(assets, kind="broll")] == ["broll-2", "broll-1"]

    with pytest.raises(ApiError):
        search_assets(assets, kind="music")


def test_used_in_timeline_reports_the_tracks() -> None:
    project = {
        "timeline": {
            "broll": [{"id": "broll-1", "source": "broll-asset"}],
            "sfx": [{"id": "sfx-1", "asset_id": "sfx-asset"}],
        }
    }

    assert used_in_timeline(project, "broll-asset") == ["broll"]
    assert used_in_timeline(project, "sfx-asset") == ["sfx"]
    assert used_in_timeline(project, "other") == []


def test_stored_asset_names_are_generated(tmp_path: Path) -> None:
    first = allocate_asset_path(tmp_path, "broll", ".mp4")
    second = allocate_asset_path(tmp_path, "broll", ".mp4")

    assert first.name.startswith("broll-") and first.name.endswith(".mp4")
    assert first != second


# ------------------------------------------------------------- the filter graph


def test_a_broll_inset_is_bounded_by_its_own_times() -> None:
    graph = build_composite_filters(
        width=1080,
        height=1920,
        duration=8.0,
        broll=[
            {
                "index": 1,
                "item": {"start": 2.0, "end": 5.0, "placement": "full", "fit": "cover"},
            }
        ],
    )["graph"]

    assert "overlay=0:0:enable='between(t,2.000,5.000)'" in graph
    assert graph.startswith("[0:v]scale=1080:1920")
    assert "[vout]" in graph


@pytest.mark.parametrize(
    ("placement", "expected"),
    [("full", 1920), ("top", 960), ("bottom", 960)],
)
def test_broll_boxes_match_their_placement(placement: str, expected: int) -> None:
    _width, height, _y = broll_box({"placement": placement}, 1080, 1920)

    assert height == expected


def test_an_insert_can_fill_its_box_or_fit_inside_it() -> None:
    cover = broll_fit_filter({"fit": "cover"}, 1080, 960)
    contain = broll_fit_filter({"fit": "contain"}, 1080, 960)

    assert "force_original_aspect_ratio=increase" in cover
    assert "crop=1080:960" in cover
    assert "force_original_aspect_ratio=decrease" in contain
    assert "pad=1080:960" in contain


def test_a_sound_effect_is_delayed_gained_faded_and_mixed_under_the_speech() -> None:
    graph = build_composite_filters(
        width=1080,
        height=1920,
        duration=8.0,
        sfx=[
            {
                "index": 1,
                "item": {"start": 1.5, "end": 2.5, "gain_db": -12.0, "fade_seconds": 0.2},
            }
        ],
    )["graph"]

    assert "adelay=1500:all=1" in graph
    assert "afade=t=in:st=0:d=0.200" in graph
    assert "afade=t=out:st=0.800:d=0.200" in graph
    # -12 dB is about 0.25 of full scale, well below the speech.
    assert "volume=0.2512" in graph
    # amix would halve every input with its default normalisation.
    assert "amix=inputs=2:duration=first:normalize=0" in graph
    assert "[aout]" in graph


def test_a_video_only_source_produces_a_video_only_graph() -> None:
    result = build_composite_filters(
        width=1080,
        height=1920,
        duration=8.0,
        broll=[{"index": 1, "item": {"start": 0.0, "end": 2.0}}],
        source_has_audio=False,
    )

    assert result["audio"] is None
    assert "amix" not in result["graph"]


def test_a_clip_without_audio_still_gets_its_effect() -> None:
    """An effect on a silent clip becomes the soundtrack rather than being dropped."""

    result = build_composite_filters(
        width=1080,
        height=1920,
        duration=8.0,
        sfx=[{"index": 1, "item": {"start": 1.0, "end": 2.0}}],
        source_has_audio=False,
    )

    assert "[abase]" not in result["graph"]
    assert "amix" not in result["graph"]
    assert result["audio"] == "[aout]"


# -------------------------------------------------------------------- api


def _client(tmp_path: Path) -> TestClient:
    settings = Settings(
        project_root=Path(__file__).resolve().parents[1],
        workspace_dir=tmp_path / "workspace",
        max_upload_size_bytes=20 * 1024 * 1024,
    )
    return TestClient(create_app(settings), raise_server_exceptions=False)


def _project(tmp_path: Path) -> TestClient:
    client = _client(tmp_path)
    store = client.app.state.projects
    store.write_new(
        "abc123",
        name="Asset test",
        source_video="source/source-x.mp4",
        source_metadata={
            "duration": 8.0,
            "width": 640,
            "height": 360,
            "fps": 25.0,
            "size_bytes": 2048,
        },
    )
    store.save_clips("abc123", [dict(CLIP)], {"generator": "heuristic"})
    return client


def _ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _synth_video(path: Path, *, color: str = "white", seconds: int = 4, rate: int = 25) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ffmpeg(
        "-f", "lavfi", "-i", f"color=c={color}:size=320x180:rate={rate}:duration={seconds}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        str(path),
    )


def _synth_audio(path: Path, *, frequency: int = 1000, seconds: int = 3) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ffmpeg(
        "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration={seconds}",
        "-c:a", "libmp3lame" if path.suffix == ".mp3" else "pcm_s16le",
        str(path),
    )


requires_ffmpeg = pytest.mark.skipif(
    not ffmpeg_available(),
    reason="FFmpeg is not on PATH, so an import or render cannot be verified.",
)


@requires_ffmpeg
def test_importing_a_broll_clip_stores_it_in_the_project(tmp_path: Path) -> None:
    client = _project(tmp_path)
    source = tmp_path / "rooftop.mp4"
    _synth_video(source)

    with source.open("rb") as handle:
        response = client.post(
            "/api/projects/abc123/assets",
            data={"kind": "broll", "label": "Rooftop run", "tags": "city, night"},
            files={"file": ("Rooftop run.mp4", handle, "video/mp4")},
        )

    assert response.status_code == 201, response.text
    asset = response.json()
    assert asset["kind"] == "broll"
    assert asset["label"] == "Rooftop run"
    assert asset["tags"] == ["city", "night"]
    assert asset["duration"] == pytest.approx(4.0, abs=0.3)
    assert asset["url"].endswith(f"/assets/{asset['id']}/file")
    # The stored name is generated, so the user's own filename never becomes a path.
    assert asset["file"] != "Rooftop run.mp4"
    assert " " not in asset["file"]

    stored = tmp_path / "workspace" / "projects" / "abc123" / "assets" / asset["file"]
    assert stored.is_file()

    saved = json.loads(
        (tmp_path / "workspace" / "projects" / "abc123" / "project.json").read_text(encoding="utf-8")
    )
    assert [item["id"] for item in saved["assets"]] == [asset["id"]]

    # The library can be played back and searched.
    assert client.get(asset["url"]).status_code == 200
    listing = client.get("/api/projects/abc123/assets", params={"q": "city"}).json()
    assert [item["id"] for item in listing["assets"]] == [asset["id"]]
    assert listing["counts"] == {"broll": 1, "sfx": 0}
    assert client.get("/api/projects/abc123/assets", params={"q": "beach"}).json()["total"] == 0


@requires_ffmpeg
def test_importing_the_wrong_kind_or_type_is_refused(tmp_path: Path) -> None:
    client = _project(tmp_path)
    video = tmp_path / "clip.mp4"
    _synth_video(video)

    with video.open("rb") as handle:
        response = client.post(
            "/api/projects/abc123/assets",
            data={"kind": "sfx"},
            files={"file": ("clip.mp4", handle, "video/mp4")},
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_asset"

    with video.open("rb") as handle:
        response = client.post(
            "/api/projects/abc123/assets",
            data={"kind": "music"},
            files={"file": ("clip.mp4", handle, "video/mp4")},
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_asset_kind"

    # Nothing was left behind by the refused imports.
    library = tmp_path / "workspace" / "projects" / "abc123" / "assets"
    assert not library.exists() or list(library.iterdir()) == []
    assert client.get("/api/projects/abc123/assets").json()["total"] == 0


@requires_ffmpeg
def test_an_asset_used_by_the_timeline_cannot_be_deleted(tmp_path: Path) -> None:
    client = _project(tmp_path)
    audio = tmp_path / "whoosh.mp3"
    _synth_audio(audio)

    with audio.open("rb") as handle:
        asset = client.post(
            "/api/projects/abc123/assets",
            data={"kind": "sfx", "label": "Whoosh"},
            files={"file": ("whoosh.mp3", handle, "audio/mpeg")},
        ).json()

    in_use = client.patch(
        "/api/projects/abc123/timeline",
        json={
            "clip_id": "clip-1",
            "sfx": [
                {
                    "asset_id": asset["id"],
                    "start": 1.0,
                    "end": 2.0,
                    "gain_db": -10.0,
                    "fade_seconds": 0.1,
                }
            ],
        },
    )
    assert in_use.status_code == 200, in_use.text

    refused = client.delete(f"/api/projects/abc123/assets/{asset['id']}")
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "asset_in_use"
    assert "sfx track" in refused.json()["error"]["message"]

    # Removing the item first makes the delete possible.
    assert client.patch(
        "/api/projects/abc123/timeline", json={"clip_id": "clip-1", "sfx": []}
    ).status_code == 200
    deleted = client.delete(f"/api/projects/abc123/assets/{asset['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["assets"] == []
    assert client.get("/api/projects/abc123/assets").json()["total"] == 0
    assert client.get(f"/api/projects/abc123/assets/{asset['id']}/file").status_code == 404


def test_a_hand_edited_asset_path_cannot_escape_the_project(tmp_path: Path) -> None:
    client = _project(tmp_path)
    store = client.app.state.projects
    project = store.load("abc123")
    project["assets"] = [
        {"id": "broll-1", "kind": "broll", "label": "Sneaky", "file": "../../project.json"}
    ]
    store.save(project)

    response = client.get("/api/projects/abc123/assets/broll-1/file")

    assert response.status_code in {400, 404}
    assert response.status_code != 200


def test_a_broll_item_must_reference_existing_media(tmp_path: Path) -> None:
    client = _project(tmp_path)
    missing = client.patch(
        "/api/projects/abc123/timeline",
        json={"clip_id": "clip-1", "broll": [{"source": "broll-nope", "start": 1.0, "end": 2.0}]},
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "invalid_asset"


def test_a_broll_insert_cannot_run_past_its_source(tmp_path: Path) -> None:
    client = _project(tmp_path)
    response = client.patch(
        "/api/projects/abc123/timeline",
        json={
            "clip_id": "clip-1",
            "broll": [
                {
                    "source": "source",
                    "source_start": 6.0,
                    "source_end": 30.0,
                    "start": 1.0,
                    "end": 5.0,
                }
            ],
        },
    )

    assert response.status_code == 422
    assert "only 8.00s long" in response.json()["error"]["message"]


def test_two_broll_inserts_cannot_cover_the_same_seconds(tmp_path: Path) -> None:
    client = _project(tmp_path)
    response = client.patch(
        "/api/projects/abc123/timeline",
        json={
            "clip_id": "clip-1",
            "broll": [
                {"source": "source", "start": 0.0, "end": 4.0},
                {"source": "source", "start": 3.0, "end": 6.0},
            ],
        },
    )

    assert response.status_code == 422
    assert "cannot overlap" in response.json()["error"]["message"]


def test_a_sound_effect_cannot_be_louder_than_the_speech(tmp_path: Path) -> None:
    client = _project(tmp_path)
    store = client.app.state.projects
    project = store.load("abc123")
    project["assets"] = [
        {"id": "sfx-1", "kind": "sfx", "label": "Whoosh", "file": "sfx-1.mp3", "duration": 3.0}
    ]
    store.save(project)

    response = client.patch(
        "/api/projects/abc123/timeline",
        json={
            "clip_id": "clip-1",
            "sfx": [{"asset_id": "sfx-1", "start": 1.0, "end": 2.0, "gain_db": 6.0}],
        },
    )

    assert response.status_code == 422
    assert "gain_db must be between" in response.json()["error"]["message"]


def test_a_sound_effect_asset_cannot_be_placed_on_the_broll_track(tmp_path: Path) -> None:
    client = _project(tmp_path)
    store = client.app.state.projects
    project = store.load("abc123")
    project["assets"] = [
        {"id": "sfx-1", "kind": "sfx", "label": "Whoosh", "file": "sfx-1.mp3", "duration": 3.0}
    ]
    store.save(project)

    response = client.patch(
        "/api/projects/abc123/timeline",
        json={"clip_id": "clip-1", "broll": [{"source": "sfx-1", "start": 1.0, "end": 2.0}]},
    )

    assert response.status_code == 422
    assert "cannot be used on the broll track" in response.json()["error"]["message"]


# ------------------------------------------------- real render with items


def _run_job(client: TestClient, job_id: str, timeout: float = 300.0) -> dict:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = client.get(f"/api/jobs/{job_id}").json()
        if payload["status"] in {"succeeded", "failed", "cancelled"}:
            return payload
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


def _frame_luma(video: Path, at: float, crop: str = "320:180:0:0") -> float:
    """Average luma of one frame region, measured by FFmpeg itself."""

    completed = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-v", "info", "-ss", str(at), "-i", str(video),
            "-frames:v", "1", "-vf", f"crop={crop},signalstats,metadata=print",
            "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    values = re.findall(r"signalstats\.YAVG=([0-9.]+)", completed.stdout + completed.stderr)
    assert values, completed.stderr[-400:]
    return max(float(value) for value in values)


def _loudness(video: Path, at: float, seconds: float) -> float:
    """Mean volume in dBFS of one window of the export."""

    completed = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-v", "info", "-ss", str(at), "-t", str(seconds),
            "-i", str(video), "-vn", "-af", "volumedetect", "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    match = re.search(r"mean_volume:\s*(-?[0-9.]+) dB", completed.stdout + completed.stderr)
    assert match, (completed.stdout + completed.stderr)[-500:]
    return float(match.group(1))


@requires_ffmpeg
def test_a_broll_insert_and_a_sound_effect_reach_the_export(tmp_path: Path) -> None:
    """The Milestone 7 acceptance path: overlay one clip, mix one effect, export."""

    client = _project(tmp_path)
    store = client.app.state.projects
    workspace = store.project_workspace("abc123")

    # A silent dark source, so every bright pixel and every decibel is the edit.
    _ffmpeg(
        "-f", "lavfi", "-i", "color=c=black:size=320x180:rate=25:duration=8",
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(workspace.source / "source-x.mp4"),
    )

    broll_source = tmp_path / "white.mp4"
    _synth_video(broll_source, color="white", seconds=4)
    with broll_source.open("rb") as handle:
        broll_asset = client.post(
            "/api/projects/abc123/assets",
            data={"kind": "broll", "label": "White insert"},
            files={"file": ("white.mp4", handle, "video/mp4")},
        ).json()

    sfx_source = tmp_path / "whoosh.wav"
    _synth_audio(sfx_source, frequency=1000, seconds=2)
    with sfx_source.open("rb") as handle:
        sfx_asset = client.post(
            "/api/projects/abc123/assets",
            data={"kind": "sfx", "label": "Whoosh"},
            files={"file": ("whoosh.wav", handle, "audio/wav")},
        ).json()

    edited = client.patch(
        "/api/projects/abc123/timeline",
        json={
            "clip_id": "clip-1",
            "captions": {"enabled": False},
            "broll": [
                {
                    "source": broll_asset["id"],
                    "source_start": 0.0,
                    "source_end": 2.0,
                    "start": 2.0,
                    "end": 4.0,
                    "placement": "full",
                    "fit": "cover",
                }
            ],
            "sfx": [
                {
                    "asset_id": sfx_asset["id"],
                    "start": 5.0,
                    "end": 6.5,
                    "gain_db": -6.0,
                    "fade_seconds": 0.1,
                }
            ],
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["broll"][0]["id"] == "broll-1"
    assert edited.json()["sfx"][0]["id"] == "sfx-1"

    saved = json.loads(
        (tmp_path / "workspace" / "projects" / "abc123" / "project.json").read_text(encoding="utf-8")
    )
    assert saved["timeline"]["broll"][0]["source"] == broll_asset["id"]
    assert saved["assets"][0]["kind"] in {"broll", "sfx"}

    started = client.post("/api/projects/abc123/render", json={"clip_id": "clip-1"})
    assert started.status_code == 202, started.text
    job = _run_job(client, started.json()["job_id"])
    assert job["status"] == "succeeded", job.get("error")
    result = job["result"]
    assert result["broll_count"] == 1
    assert result["sfx_count"] == 1
    assert any("B-roll insert" in note for note in result["notes"])
    assert any("mixed under the speech" in note for note in result["notes"])

    output = tmp_path / "workspace" / "projects" / "abc123" / "exports" / result["file"]
    assert output.is_file()

    # The insert is visible only inside its own window and nowhere else.
    during_insert = _frame_luma(output, at=3.0)
    before_insert = _frame_luma(output, at=1.0)
    after_insert = _frame_luma(output, at=6.0)
    assert during_insert > 200.0, f"the insert was not composited (luma {during_insert})"
    assert before_insert < 40.0, f"the insert leaked outside its window (luma {before_insert})"
    assert after_insert < 40.0, f"the insert leaked outside its window (luma {after_insert})"

    # The effect is audible only inside its own window: the source track is silent,
    # so anything above the digital-silence floor has to be the effect.
    during_effect = _loudness(output, at=5.2, seconds=0.8)
    before_effect = _loudness(output, at=3.5, seconds=0.8)
    assert during_effect > -45.0, f"the effect was not mixed in ({during_effect} dB)"
    assert before_effect < -60.0, f"audio appeared outside the effect ({before_effect} dB)"
    assert during_effect - before_effect > 30.0
