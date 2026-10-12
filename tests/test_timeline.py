"""Milestone 6 acceptance checks for the editable timeline.

The model tests are pure and offline. The API tests use a synthetic project and
then read ``project.json`` from disk, because "the UI edit is saved" is only true
if the file on disk really changed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.errors import ApiError
from app.main import create_app
from app.timeline import (
    DEFAULT_FPS,
    FPS_CHOICES,
    apply_timeline_patch,
    build_timeline,
    normalise_fps,
    normalise_track_items,
    stored_timeline,
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
    "caption_mode": "burn",
    "write_srt": True,
}

#: The same settings after ``normalise_export`` resolved the pixel size.
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
        "name": "Timeline test",
        "source_video": "source/source-x.mp4",
        "source_metadata": {
            "duration": 30.0,
            "width": 1920,
            "height": 1080,
            "fps": 25.0,
            "size_bytes": 4096,
        },
        "transcript": {"generated_at": TRANSCRIPT_GENERATED_AT, "path": "transcripts/transcript.json"},
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


def _project(tmp_path: Path, **overrides: object) -> TestClient:
    client = _client(tmp_path)
    store = client.app.state.projects
    store.write_new(
        "abc123",
        name="Timeline test",
        source_video="source/source-x.mp4",
        source_metadata={
            "duration": 30.0,
            "width": 1920,
            "height": 1080,
            "fps": 25.0,
            "size_bytes": 4096,
        },
    )
    if overrides.get("with_clip", True):
        store.save_clips("abc123", [dict(CLIP)], {"generator": "heuristic"})
    return client


def _stored_project(tmp_path: Path) -> dict:
    path = tmp_path / "workspace" / "projects" / "abc123" / "project.json"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- document


def test_the_timeline_composes_the_saved_project() -> None:
    project = _project_document()
    timeline = build_timeline(
        project, CLIP, caption_style=CAPTION_STYLE, export=EXPORT_PLAN
    )

    assert timeline["clip_id"] == "clip-1"
    assert timeline["duration"] == 10.0
    assert timeline["video"]["source_start"] == 1.0
    assert timeline["video"]["source_end"] == 11.0
    assert timeline["video"]["fps"] == 30
    assert (timeline["video"]["width"], timeline["video"]["height"]) == (1080, 1920)
    assert timeline["captions"]["preset"] == "bold-highlight"
    assert timeline["captions"]["enabled"] is True
    assert timeline["render"]["resolution"] == "1080x1920"
    assert timeline["broll"] == [] and timeline["sfx"] == [] and timeline["transitions"] == []


def test_a_project_without_a_stored_timeline_still_gets_empty_tracks() -> None:
    project = _project_document()
    project.pop("timeline", None)

    assert stored_timeline(project)["broll"] == []


def test_switching_captions_off_shows_up_in_the_timeline() -> None:
    timeline = build_timeline(
        _project_document(),
        CLIP,
        caption_style=CAPTION_STYLE,
        export={**EXPORT_PLAN, "caption_mode": "none"},
    )

    assert timeline["captions"]["enabled"] is False


# -------------------------------------------------------------------- fps


def test_frame_rate_defaults_and_is_validated() -> None:
    assert normalise_fps(None) == DEFAULT_FPS
    assert normalise_fps("") == DEFAULT_FPS
    for choice in FPS_CHOICES:
        assert normalise_fps(choice) == choice

    with pytest.raises(ApiError) as error:
        normalise_fps(12)

    assert error.value.code == "invalid_timeline"
    assert "fps must be one of" in error.value.message


# ------------------------------------------------------------------ items


def test_a_broll_item_from_the_project_source_is_accepted() -> None:
    items = normalise_track_items(
        [
            {
                "source": "source",
                "source_start": 20.0,
                "source_end": 23.0,
                "start": 4.0,
                "end": 7.0,
                "fit": "cover",
                "placement": "full",
            }
        ],
        track="broll",
        clip_duration=10.0,
        asset_ids=set(),
    )

    assert items[0]["id"] == "broll-1"
    assert items[0]["source"] == "source"
    assert items[0]["start"] == 4.0 and items[0]["end"] == 7.0


def test_a_broll_item_outside_the_clip_is_refused() -> None:
    with pytest.raises(ApiError) as error:
        normalise_track_items(
            [{"source": "source", "start": 8.0, "end": 12.0}],
            track="broll",
            clip_duration=10.0,
            asset_ids=set(),
        )

    assert "inside the 10.00s clip" in error.value.message


def test_a_broll_item_naming_an_unknown_asset_is_refused() -> None:
    with pytest.raises(ApiError) as error:
        normalise_track_items(
            [{"source": "asset-9", "start": 1.0, "end": 2.0}],
            track="broll",
            clip_duration=10.0,
            asset_ids={"asset-1"},
        )

    assert error.value.code == "invalid_asset"


def test_a_sound_effect_keeps_its_gain_and_fade() -> None:
    items = normalise_track_items(
        [
            {
                "asset_id": "asset-1",
                "start": 2.0,
                "end": 3.0,
                "gain_db": -8.5,
                "fade_seconds": 0.25,
            }
        ],
        track="sfx",
        clip_duration=10.0,
        asset_ids={"asset-1"},
    )

    assert items[0]["gain_db"] == -8.5
    assert items[0]["fade_seconds"] == 0.25


@pytest.mark.parametrize(
    ("item", "message"),
    [
        ({"asset_id": "a", "start": 2.0, "end": 2.0}, "end after it starts"),
        ({"asset_id": "a", "start": 0.0, "end": 1.0, "gain_db": 30}, "gain_db must be"),
        ({"asset_id": "a", "start": 0.0, "end": 1.0, "fade_seconds": 4.0}, "fade_seconds"),
        ({"start": 0.0, "end": 1.0}, "needs an asset_id"),
    ],
)
def test_an_invalid_sound_effect_is_refused(item: dict, message: str) -> None:
    with pytest.raises(ApiError) as error:
        normalise_track_items(
            [item], track="sfx", clip_duration=10.0, asset_ids={"a"}
        )

    assert message in error.value.message


def test_a_transition_is_bounded_by_the_clip() -> None:
    items = normalise_track_items(
        [{"kind": "fade", "at": "start", "duration": 0.6}],
        track="transitions",
        clip_duration=10.0,
        asset_ids=set(),
    )
    assert items[0]["kind"] == "fade"

    with pytest.raises(ApiError) as error:
        normalise_track_items(
            [{"kind": "fade", "at": "start", "duration": 8.0}],
            track="transitions",
            clip_duration=10.0,
            asset_ids=set(),
        )

    assert "no more than 2s" in error.value.message


@pytest.mark.parametrize("track", ["broll", "sfx", "transitions"])
def test_a_track_the_renderer_cannot_honour_yet_is_refused_with_a_reason(track: str) -> None:
    """Storing an item the export would ignore would make the timeline lie.

    The item itself is valid — it is the *edit* that is refused until the render
    can draw it, and the message says which milestone brings it.
    """

    item = {
        "broll": {"source": "source", "start": 1.0, "end": 2.0},
        "sfx": {"asset_id": "a", "start": 1.0, "end": 2.0},
        "transitions": {"kind": "fade", "at": "start", "duration": 0.5},
    }[track]

    assert normalise_track_items(
        [item], track=track, clip_duration=10.0, asset_ids={"a"}
    )

    with pytest.raises(ApiError) as error:
        apply_timeline_patch(
            _project_document(assets=[{"id": "a", "kind": "sfx"}]),
            dict(CLIP),
            {"clip_id": "clip-1", track: [item]},
            current_style=CAPTION_STYLE,
            current_export=EXPORT,
        )

    assert error.value.code == "unsupported_track"
    assert "Milestone" in error.value.message


# ------------------------------------------------------------------- patch


def test_a_patch_moves_the_clip_and_keeps_the_settings_consistent() -> None:
    project = _project_document()
    patch = apply_timeline_patch(
        project,
        dict(CLIP),
        {
            "video": {"source_start": 2.0, "source_end": 9.5, "fps": 60},
            "captions": {"enabled": False, "preset": "minimal"},
            "render": {"resolution": "1080x1080", "aspect_ratio": "1:1"},
        },
        current_style=CAPTION_STYLE,
        current_export=EXPORT,
    )

    assert patch["source_start"] == 2.0
    assert patch["source_end"] == 9.5
    assert patch["caption_style"]["preset"] == "minimal"
    assert patch["export"]["caption_mode"] == "none"
    assert patch["export"]["resolution"] == "1080x1080"
    assert patch["fps"] == 60


def test_a_patch_cannot_push_a_clip_past_the_end_of_the_source() -> None:
    with pytest.raises(ApiError) as error:
        apply_timeline_patch(
            _project_document(),
            dict(CLIP),
            {"video": {"source_end": 45.0}},
            current_style=CAPTION_STYLE,
            current_export=EXPORT,
        )

    assert "source video is 30.00s long" in error.value.message


def test_a_patch_that_does_not_name_a_clip_is_not_applied(tmp_path: Path) -> None:
    client = _project(tmp_path)
    response = client.patch("/api/projects/abc123/timeline", json={})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_clip_id"


# --------------------------------------------------------------------- api


def test_the_timeline_needs_candidate_clips_first(tmp_path: Path) -> None:
    client = _project(tmp_path, with_clip=False)
    response = client.get("/api/projects/abc123/timeline")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "no_clips"


def test_an_unknown_clip_id_is_a_clear_error(tmp_path: Path) -> None:
    client = _project(tmp_path)
    response = client.get("/api/projects/abc123/timeline?clip_id=clip-9")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "clip_not_found"


def test_the_timeline_route_returns_the_first_clip_by_default(tmp_path: Path) -> None:
    response = _project(tmp_path).get("/api/projects/abc123/timeline")

    assert response.status_code == 200
    body = response.json()
    assert body["clip_id"] == "clip-1"
    assert body["video"]["fps"] == 30
    assert body["tracks_not_rendered"]["broll"]


def test_editing_the_timeline_changes_the_saved_project_on_disk(tmp_path: Path) -> None:
    client = _project(tmp_path)

    response = client.patch(
        "/api/projects/abc123/timeline",
        json={
            "clip_id": "clip-1",
            "video": {"source_start": 3.0, "source_end": 12.0, "fps": 25},
            "captions": {"enabled": True, "preset": "clean"},
            "render": {"resolution": "1080x1080", "aspect_ratio": "1:1", "write_srt": False},
            "broll": [],
        },
    )
    assert response.status_code == 200, response.text

    saved = _stored_project(tmp_path)
    clip = saved["clips"][0]
    assert clip["source_start"] == 3.0
    assert clip["source_end"] == 12.0
    assert clip["duration"] == 9.0
    assert clip["edited"] is True
    assert saved["caption_style"]["preset"] == "clean"
    assert saved["export"]["resolution"] == "1080x1080"
    assert saved["export"]["aspect_ratio"] == "1:1"
    assert saved["export"]["fps"] == 25
    assert saved["export"]["write_srt"] is False
    assert saved["timeline"]["broll"] == []
    assert saved["timeline"]["updated_at"]

    # The response describes the same edit the file now holds.
    body = response.json()
    assert body["video"]["source_start"] == 3.0
    assert body["duration"] == 9.0
    assert body["render"]["fps"] == 25


def test_a_refused_timeline_edit_changes_nothing_on_disk(tmp_path: Path) -> None:
    client = _project(tmp_path)
    before = _stored_project(tmp_path)

    response = client.patch(
        "/api/projects/abc123/timeline",
        json={
            "clip_id": "clip-1",
            "video": {"source_start": 4.0, "source_end": 12.0},
            "broll": [{"source": "source", "start": 1.0, "end": 2.0}],
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsupported_track"
    assert _stored_project(tmp_path) == before


def test_an_edit_that_does_not_fit_the_source_is_refused(tmp_path: Path) -> None:
    client = _project(tmp_path)
    response = client.patch(
        "/api/projects/abc123/timeline",
        json={"clip_id": "clip-1", "video": {"source_end": 900.0}},
    )

    assert response.status_code == 422
    assert "source video is 30.00s long" in response.json()["error"]["message"]


def test_the_timeline_rejects_a_caption_preset_the_renderer_cannot_draw(tmp_path: Path) -> None:
    client = _project(tmp_path)
    response = client.patch(
        "/api/projects/abc123/timeline",
        json={"clip_id": "clip-1", "captions": {"preset": "neon-vhs"}},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_caption_style"


def test_a_hand_edited_project_with_an_unrendered_track_cannot_render(tmp_path: Path) -> None:
    """A project edited outside the app must not export something it cannot draw."""

    client = _project(tmp_path)
    store = client.app.state.projects
    project = store.load("abc123")
    project["timeline"] = {
        "version": 1,
        "broll": [{"id": "broll-1", "source": "source", "start": 1.0, "end": 2.0}],
        "sfx": [],
        "transitions": [],
    }
    store.save(project)

    response = client.post("/api/projects/abc123/render", json={"clip_id": "clip-1"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsupported_track"
    assert "Milestone 7" in response.json()["error"]["message"]


def test_saving_render_settings_keeps_the_frame_rate(tmp_path: Path) -> None:
    client = _project(tmp_path)
    response = client.patch(
        "/api/projects/abc123/settings", json={"export": {"fps": 24}}
    )

    assert response.status_code == 200, response.text
    assert response.json()["export"]["fps"] == 24
    assert _stored_project(tmp_path)["export"]["fps"] == 24

    # The timeline reports the same value, because there is only one copy of it.
    assert client.get("/api/projects/abc123/timeline").json()["video"]["fps"] == 24
