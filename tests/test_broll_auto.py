"""Checks for the one-click "Add B-Rolls" pass.

The ranking and the fetch plan are pure, so they are tested without a provider.
The end-to-end test drives the real route with the provider transport and the
download stream injected: every decision, validation, and storage path is the
application's own, and the only thing that is faked is the network.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.broll_auto import (
    aspect_of,
    candidate_key,
    keyword_hits,
    plan_fetches,
    plan_note,
    rank_candidates,
    same_orientation,
    score_candidate,
)
from app.config import Settings
from app.main import create_app
from app.stock import ATTRIBUTION

CLIP = {
    "id": "clip-1",
    "title": "The five minute rule",
    "reason": "Self-contained tip.",
    "score": 0.71,
    "source_start": 0.0,
    "source_end": 8.0,
    "duration": 8.0,
}

SEGMENTS = [
    {"id": "seg-1", "start": 0.0, "end": 3.0, "text": "Insurance fraud costs everyone money."},
    {"id": "seg-2", "start": 3.0, "end": 5.0, "text": "Claim adjusters check timestamps first."},
    {"id": "seg-3", "start": 5.0, "end": 8.0, "text": "So keep your receipts and photographs."},
]

PHOTO_BODY = json.dumps(
    {
        "page": 1,
        "per_page": 1,
        "photos": [
            {
                "id": 555,
                "width": 1200,
                "height": 1800,
                "url": "https://www.pexels.com/photo/receipts-on-a-desk-555/",
                "photographer": "Kelly",
                "photographer_url": "https://www.pexels.com/@kelly",
                "alt": "receipts and photographs on a desk",
                "src": {
                    "medium": "https://images.pexels.com/photos/555/pexels-photo-555-medium.jpeg",
                    "large2x": "https://images.pexels.com/photos/555/pexels-photo-555-large2x.jpg",
                    "original": "https://images.pexels.com/photos/555/pexels-photo-555.jpeg",
                },
                "user": {
                    "id": 1,
                    "name": "Kelly",
                    "url": "https://www.pexels.com/@kelly",
                },
            }
        ],
    }
).encode("utf-8")


def _photo(**overrides) -> dict:
    base = {
        "id": "555",
        "media_type": "photo",
        "width": 1200,
        "height": 1800,
        "duration": 0.0,
        "alt": "receipts and photographs on a desk",
        "photographer": "Kelly",
    }
    return {**base, **overrides}


def _video(**overrides) -> dict:
    base = {
        "id": "999",
        "media_type": "video",
        "width": 1920,
        "height": 1080,
        "duration": 12.0,
        "alt": "city traffic at night",
        "photographer": "Sam",
    }
    return {**base, **overrides}


# ------------------------------------------------------------------ ranking


def test_aspect_and_orientation_are_read_from_the_size() -> None:
    assert aspect_of(1080, 1920) == pytest.approx(0.5625)
    assert aspect_of(0, 1920) == 0.0
    assert same_orientation(0.5625, 0.75) is True
    assert same_orientation(1.77, 0.5625) is False


def test_keyword_hits_look_at_the_description_only() -> None:
    assert keyword_hits(_photo(), ["receipts", "desk"]) == 2
    assert keyword_hits(_photo(), ["volcano"]) == 0


def test_a_portrait_frame_prefers_a_portrait_asset() -> None:
    ranked = rank_candidates(
        [_video(), _photo()], box_width=1080, box_height=1920, wanted_seconds=3.0
    )

    assert ranked[0]["media_type"] == "photo"
    assert ranked[0]["height"] > ranked[0]["width"]


def test_an_asset_already_on_the_timeline_is_not_chosen_again() -> None:
    used = [candidate_key(_photo())]

    ranked = rank_candidates(
        [_photo(), _video()],
        box_width=1080,
        box_height=1920,
        wanted_seconds=3.0,
        used_ids=used,
    )

    assert ranked[0]["media_type"] == "video"


def test_a_clip_that_covers_the_interval_beats_a_much_longer_one() -> None:
    covering = _video(duration=4.0)
    wasteful = _video(id="998", duration=90.0)

    assert score_candidate(
        covering, box_width=1080, box_height=1920, wanted_seconds=4.0
    ) > score_candidate(wasteful, box_width=1080, box_height=1920, wanted_seconds=4.0)


def test_a_clip_that_covers_the_interval_beats_an_equally_suited_photo() -> None:
    """Motion wins a tie: this is a video edit, not a slideshow."""

    portrait_video = _video(width=1080, height=1920, duration=3.0)

    assert score_candidate(
        portrait_video, box_width=1080, box_height=1920, wanted_seconds=3.0
    ) > score_candidate(_photo(), box_width=1080, box_height=1920, wanted_seconds=3.0)


def test_a_photo_is_usable_even_though_it_has_no_duration() -> None:
    assert score_candidate(
        _photo(), box_width=1080, box_height=1920, wanted_seconds=3.0
    ) > 0.0


# -------------------------------------------------------------- fetch plan


def _online(query: str, index: int = 1, duration: float = 3.0) -> dict:
    return {
        "index": index,
        "start": float(index),
        "end": float(index) + duration,
        "duration": duration,
        "query": query,
        "keywords": query.split(),
        "source": {"type": "online", "query": query},
    }


def test_one_search_per_sentence_never_one_per_word() -> None:
    calls: list[str] = []

    def search(query: str) -> list[dict]:
        calls.append(query)
        # A different asset per query, so neither insert is skipped as a repeat.
        return [_photo(id=query.replace(" ", "-"), alt=query)]

    plan = plan_fetches(
        [_online("insurance fraud"), _online("receipts photographs", index=2)],
        search=search,
        box_width=1080,
        box_height=1920,
    )

    assert calls == ["insurance fraud", "receipts photographs"]
    assert len(plan["fetches"]) == 2


def test_a_local_proposal_is_not_searched_at_all() -> None:
    calls: list[str] = []

    def search(query: str) -> list[dict]:  # pragma: no cover - must not be called
        calls.append(query)
        return []

    items = [
        {
            "index": 1,
            "start": 1.0,
            "end": 3.0,
            "duration": 2.0,
            "query": "nothing",
            "keywords": ["nothing"],
            "source": {"type": "local", "asset_id": "broll-1"},
        }
    ]

    plan = plan_fetches(items, search=search, box_width=1080, box_height=1920)

    assert calls == []
    assert plan["fetches"] == []
    assert "local" in plan["skipped"][0]["reason"].lower()


def test_a_failed_query_is_reported_and_the_rest_continue() -> None:
    def search(query: str) -> list[dict]:
        if query == "broken":
            raise RuntimeError("provider said no")
        return [_photo()]

    plan = plan_fetches(
        [_online("broken"), _online("receipts", index=2)],
        search=search,
        box_width=1080,
        box_height=1920,
    )

    assert len(plan["fetches"]) == 1
    assert plan["errors"] and "broken" in plan["errors"][0]
    assert any("stock search failed" in entry["reason"] for entry in plan["skipped"])


def test_the_same_asset_is_not_used_for_two_inserts() -> None:
    def search(query: str) -> list[dict]:
        return [_photo()]

    plan = plan_fetches(
        [_online("receipts"), _online("desk", index=2)],
        search=search,
        box_width=1080,
        box_height=1920,
    )

    # The second query only offered the photo the first one already took, so it is
    # skipped rather than stacking the same cutaway twice in one clip.
    assert len(plan["fetches"]) == 1
    assert "already used" in plan["skipped"][0]["reason"]


def test_the_plan_note_says_what_will_happen() -> None:
    note = plan_note({"fetches": [{}], "skipped": [{}]}, matched_locally=2)

    assert "2 insert(s) use your own library" in note
    assert "1 stock file(s) will be downloaded" in note
    assert "1 proposal(s) were skipped" in note


# ---------------------------------------------------------------- endpoint


def _client(tmp_path: Path, **overrides: object) -> TestClient:
    settings = Settings(
        project_root=Path(__file__).resolve().parents[1],
        workspace_dir=tmp_path / "workspace",
        max_upload_size_bytes=50 * 1024 * 1024,
        **overrides,
    )
    return TestClient(create_app(settings), raise_server_exceptions=False)


def _project(tmp_path: Path, *, api_key: str | None = "k") -> TestClient:
    client = _client(tmp_path, pexels_api_key=api_key)
    store = client.app.state.projects
    store.write_new(
        "abc123",
        name="Add B-rolls",
        source_video="source/source-x.mp4",
        source_metadata={
            "duration": 30.0,
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "size_bytes": 4096,
        },
    )
    workspace = store.project_workspace("abc123")
    (workspace.source / "source-x.mp4").write_bytes(b"0")
    store.save_transcript(
        "abc123",
        {
            "version": 1,
            "project_id": "abc123",
            "generated_at": "2026-01-01T00:00:00+00:00",
            "segments": SEGMENTS,
        },
    )
    store.save_clips("abc123", [CLIP], {"generator": "heuristic"})
    return client


class _FakeResponse:
    """A minimal stand-in for the streamed download response."""

    def __init__(self, payload: bytes):
        self._payload = payload
        self.status = 200
        self.headers = {"Content-Length": str(len(payload))}

    def read(self, size: int = -1) -> bytes:
        chunk, self._payload = self._payload, b""
        return chunk

    def close(self) -> None:
        return None


def _opener(body: bytes, status: int = 200):
    def open_(request, timeout):
        return status, body

    return open_


def _real_jpeg(tmp_path: Path) -> bytes:
    """Real JPEG bytes, so FFprobe reads a genuine still image."""

    if shutil.which("ffmpeg") is None:
        pytest.skip("FFmpeg is not on PATH, so a real still image cannot be made.")
    path = tmp_path / "photo.jpg"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:size=1200x1800",
            "-frames:v",
            "1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return path.read_bytes()


def test_add_b_rolls_downloads_places_and_credits_a_stock_photo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole pass, through the real route with only the network faked."""

    import app.stock as stock_module

    photo_bytes = _real_jpeg(tmp_path)
    client = _project(tmp_path)
    monkeypatch.setattr(stock_module, "_urlopen", _opener(PHOTO_BODY))
    monkeypatch.setattr(
        stock_module, "_urlopen_stream", lambda request, timeout: _FakeResponse(photo_bytes)
    )

    response = client.post(
        "/api/projects/abc123/broll/auto",
        json={"clip_id": "clip-1", "mode": "auto", "max_items": 2},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["added"] >= 1
    assert payload["downloaded"] == payload["added"]

    chosen = payload["selected"][0]
    assert chosen["media_type"] == "photo"
    assert chosen["attribution"] == ATTRIBUTION
    assert chosen["photographer"] == "Kelly"
    assert 0.0 <= chosen["start"] < chosen["end"] <= 8.0

    # The insert is on the clip's timeline, pointing at a real file the renderer
    # can read — not merely listed in the response.
    timeline = client.get("/api/projects/abc123/timeline?clip_id=clip-1").json()
    sources = [item["source"] for item in timeline["broll"]]
    assert chosen["asset_id"] in sources

    asset = client.get("/api/projects/abc123/assets").json()["assets"][0]
    assert asset["id"] == chosen["asset_id"]
    assert asset["kind"] == "broll"
    assert asset["media_kind"] == "image"
    assert asset["provenance"]["media_type"] == "photo"

    stored = client.app.state.projects.resolve_asset_path("abc123", asset)
    assert stored.is_file() and stored.stat().st_size > 0
    assert stored.suffix == ".jpg"

    # And the file really is servable, which is what the browser previews.
    assert client.get(asset["url"]).status_code == 200


def test_a_second_pass_reuses_the_file_instead_of_downloading_it_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.stock as stock_module

    photo_bytes = _real_jpeg(tmp_path)
    client = _project(tmp_path)
    monkeypatch.setattr(stock_module, "_urlopen", _opener(PHOTO_BODY))
    downloads: list[str] = []

    def stream(request, timeout):
        downloads.append(request.full_url)
        return _FakeResponse(photo_bytes)

    monkeypatch.setattr(stock_module, "_urlopen_stream", stream)

    first = client.post("/api/projects/abc123/broll/auto", json={"clip_id": "clip-1"}).json()
    assert first["downloaded"] == 1

    # Clear the timeline so the second pass proposes the same sentence again.
    client.patch("/api/projects/abc123/timeline", json={"clip_id": "clip-1", "broll": []})

    second = client.post("/api/projects/abc123/broll/auto", json={"clip_id": "clip-1"}).json()

    assert len(downloads) == 1, "the provider file was fetched twice"
    assert second["reused"] == 1
    assert second["downloaded"] == 0
    assert second["added"] == 1


def test_a_failed_download_is_reported_and_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interrupted download must not fail the request or leave a partial file."""

    import app.stock as stock_module

    client = _project(tmp_path)
    monkeypatch.setattr(stock_module, "_urlopen", _opener(PHOTO_BODY))

    def broken(request, timeout):
        raise OSError("connection reset by peer")

    monkeypatch.setattr(stock_module, "_urlopen_stream", broken)

    response = client.post("/api/projects/abc123/broll/auto", json={"clip_id": "clip-1"})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["added"] == 0
    assert payload["downloaded"] == 0
    assert payload["errors"], "the failure was not reported"
    assert "insert" in payload["note"].lower()

    assets = tmp_path / "workspace" / "projects" / "abc123" / "assets"
    assert list(assets.glob("*")) == [], "a partial download was left in the library"
    assert list(assets.parent.rglob("*.part")) == []
    assert client.get("/api/projects/abc123/timeline?clip_id=clip-1").json()["broll"] == []


def test_adding_stock_b_roll_without_a_key_explains_itself(tmp_path: Path) -> None:
    client = _project(tmp_path, api_key=None)

    response = client.post("/api/projects/abc123/broll/auto", json={"clip_id": "clip-1"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "stock_not_configured"
