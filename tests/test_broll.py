"""Automatic B-roll: placement, duration reasoning, and source modes.

The suggestion logic is pure and offline, so most checks are deterministic. The
placement checks use FFmpeg to prove an accepted insert reaches the timeline
through the same validated path as a hand-placed one.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.broll import (
    MAX_COVERAGE,
    MAX_ITEM_SECONDS,
    MIN_ITEM_SECONDS,
    build_broll_item,
    item_duration,
    normalise_mode,
    propose_broll,
)
from app.config import Settings
from app.errors import ApiError
from app.main import create_app
from app.render import ffmpeg_available

CLIP = {
    "id": "clip-1",
    "title": "Hook",
    "reason": "seeded",
    "score": 0.7,
    "source_start": 0.0,
    "source_end": 12.0,
    "duration": 12.0,
}

#: Two sentences with concrete, visual words, spaced far enough apart to test gaps.
SEGMENTS = [
    {"id": "s1", "start": 1.0, "end": 4.5,
     "text": "The bustling market street was crowded with traffic and neon signs."},
    {"id": "s2", "start": 7.0, "end": 10.0,
     "text": "Business owners reviewed spreadsheets on laptops in the office."},
]

GENERATED_AT = "2026-01-01T00:00:00+00:00"


# ------------------------------------------------------------------ placement


def test_it_suggests_inserts_for_sentences_with_something_to_show() -> None:
    proposal = propose_broll(SEGMENTS, clip_start=0.0, clip_end=12.0, mode="online")

    assert proposal["items"]
    for item in proposal["items"]:
        assert MIN_ITEM_SECONDS <= item["duration"] <= MAX_ITEM_SECONDS
        assert 0.0 <= item["start"] < item["end"] <= 12.0
        assert item["reason"]
        assert item["duration_reason"]


def test_duration_follows_the_spoken_turn_and_is_capped() -> None:
    # A short line still gets a readable glance; a long one is capped so it stays
    # a cutaway rather than a second edit.
    assert item_duration(0.5) == MIN_ITEM_SECONDS
    assert item_duration(30.0) == MAX_ITEM_SECONDS
    assert MIN_ITEM_SECONDS < item_duration(3.0) < MAX_ITEM_SECONDS


def test_inserts_never_cover_the_whole_clip() -> None:
    many = [
        {"id": f"s{i}", "start": float(i * 1.4), "end": float(i * 1.4 + 1.3),
         "text": "A busy crowded market street with traffic and neon signs everywhere."}
        for i in range(10)
    ]
    proposal = propose_broll(many, clip_start=0.0, clip_end=14.0, mode="online", max_items=5)

    covered = sum(float(item["duration"]) for item in proposal["items"])
    assert covered <= 14.0 * MAX_COVERAGE + 0.01
    # The speaker keeps the screen for most of the clip.
    assert covered < 14.0 * 0.5


def test_inserts_are_spaced_apart() -> None:
    proposal = propose_broll(SEGMENTS, clip_start=0.0, clip_end=12.0, mode="online")
    items = sorted(proposal["items"], key=lambda item: item["start"])

    for earlier, later in zip(items, items[1:]):
        assert later["start"] >= earlier["end"]


def test_a_clip_with_nothing_to_show_explains_itself() -> None:
    proposal = propose_broll(
        [{"id": "s1", "start": 0.0, "end": 3.0, "text": "um okay yeah so"}],
        clip_start=0.0,
        clip_end=12.0,
        mode="auto",
    )

    assert proposal["items"] == []
    assert proposal["note"]


# --------------------------------------------------------------- source modes


LOCAL_ASSET = {
    "id": "broll-1",
    "kind": "broll",
    "label": "City traffic at night",
    "tags": ["city", "traffic"],
}


def test_local_mode_matches_an_asset_by_its_words() -> None:
    proposal = propose_broll(
        SEGMENTS, clip_start=0.0, clip_end=12.0, assets=[LOCAL_ASSET], mode="local"
    )

    matched = [item for item in proposal["items"] if item["source"]["type"] == "local"]
    assert matched
    assert matched[0]["source"]["asset_id"] == "broll-1"


def test_local_mode_says_why_nothing_matches() -> None:
    proposal = propose_broll(
        [{"id": "s1", "start": 1.0, "end": 4.0,
          "text": "Quantum physics equations describe particle interactions."}],
        clip_start=0.0,
        clip_end=12.0,
        assets=[LOCAL_ASSET],
        mode="local",
    )

    assert proposal["items"]
    assert proposal["items"][0]["source"]["type"] == "none"
    assert "No local B-roll" in str(proposal["items"][0]["source"]["label"])


def test_auto_uses_local_when_it_matches_and_online_otherwise() -> None:
    proposal = propose_broll(
        SEGMENTS, clip_start=0.0, clip_end=20.0, assets=[LOCAL_ASSET], mode="auto"
    )

    types = {item["source"]["type"] for item in proposal["items"]}
    assert "local" in types
    # The office sentence has no local match, so it falls back to a stock search.
    assert "online" in types


def test_an_unknown_mode_is_refused() -> None:
    with pytest.raises(ApiError) as error:
        normalise_mode("telepathy")

    assert error.value.code == "invalid_broll_mode"


def test_a_short_asset_shortens_the_insert_instead_of_freezing() -> None:
    item = build_broll_item({"start": 5.0, "end": 8.0}, asset_id="broll-1", asset_duration=1.0)

    assert item["end"] == 6.0
    assert item["source_end"] == 1.0
    assert item["source"] == "broll-1"


# ------------------------------------------------------------------- the API


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
        name="B-roll test",
        source_video="source/source-x.mp4",
        source_metadata={"duration": 12.0, "width": 640, "height": 360,
                         "fps": 25.0, "size_bytes": 2048},
    )
    store.save_clips("abc123", [dict(CLIP)], {"generator": "heuristic"})
    store.save_transcript("abc123", {
        "version": 1, "project_id": "abc123", "generated_at": GENERATED_AT,
        "segments": SEGMENTS,
    })
    return client


def test_the_propose_endpoint_returns_editable_placements(tmp_path: Path) -> None:
    client = _project(tmp_path)

    response = client.post(
        "/api/projects/abc123/broll/propose", json={"clip_id": "clip-1", "mode": "auto"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["items"]
    assert body["mode"] == "auto"
    assert body["stock_configured"] is False
    assert body["stock_note"]


def test_the_propose_endpoint_refuses_an_unknown_clip(tmp_path: Path) -> None:
    client = _project(tmp_path)

    response = client.post(
        "/api/projects/abc123/broll/propose", json={"clip_id": "nope", "mode": "auto"}
    )

    assert response.status_code == 404


def test_a_missing_project_is_a_clear_404(tmp_path: Path) -> None:
    client = _project(tmp_path)

    response = client.post(
        "/api/projects/missing/broll/propose", json={"clip_id": "clip-1", "mode": "auto"}
    )

    assert response.status_code == 404


def _ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
        check=True, capture_output=True, text=True, timeout=180,
    )


requires_ffmpeg = pytest.mark.skipif(
    not ffmpeg_available(),
    reason="FFmpeg is not on PATH, so placement cannot be verified.",
)


@requires_ffmpeg
def test_the_apply_endpoint_places_a_local_insert_on_the_timeline(tmp_path: Path) -> None:
    client = _project(tmp_path)
    source = tmp_path / "skyline.mp4"
    _ffmpeg("-f", "lavfi", "-i", "color=c=red:size=320x180:rate=25:duration=3",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(source))

    with source.open("rb") as handle:
        imported = client.post(
            "/api/projects/abc123/assets",
            files={"file": ("skyline.mp4", handle, "video/mp4")},
            data={"kind": "broll", "label": "City traffic", "tags": "city,traffic"},
        )
    assert imported.status_code == 201, imported.text
    asset_id = imported.json()["id"]

    applied = client.post(
        "/api/projects/abc123/broll/apply",
        json={"clip_id": "clip-1", "items": [
            {"asset_id": asset_id, "start": 1.0, "end": 3.5},
        ]},
    )

    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["placed"] == 1
    assert body["broll_count"] == 1
    assert body["broll"][0]["source"] == asset_id
    assert body["broll"][0]["start"] == 1.0


@requires_ffmpeg
def test_apply_refuses_an_asset_that_is_not_broll(tmp_path: Path) -> None:
    client = _project(tmp_path)
    response = client.post(
        "/api/projects/abc123/broll/apply",
        json={"clip_id": "clip-1", "items": [{"asset_id": "does-not-exist", "start": 1.0, "end": 2.0}]},
    )

    assert response.status_code == 404
