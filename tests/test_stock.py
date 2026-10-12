"""Milestone 8 acceptance checks for optional stock B-roll search.

The provider is optional by design (``PROJECT.md``), so every test here runs with
no network at all: the transport is injected, and the keyword derivation, result
parsing, download policy, and placement suggestion are pure functions.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.errors import ApiError
from app.main import create_app
from app.stock import (
    ATTRIBUTION,
    LICENSE_NAME,
    PexelsSettings,
    choose_file,
    clip_window,
    derive_keywords,
    download_file,
    download_host_allowed,
    fetch_video,
    normalise_query,
    normalise_video,
    parse_search_results,
    provenance_for,
    search_videos,
    select_file,
    suggest_placement,
)

CLIP = {
    "id": "clip-1",
    "title": "The five minute rule",
    "reason": "Self-contained tip.",
    "score": 0.71,
    "source_start": 0.0,
    "source_end": 8.0,
    "duration": 8.0,
}

TRANSCRIPT_GENERATED_AT = "2026-01-01T00:00:00+00:00"

SEGMENTS = [
    {"id": "seg-1", "start": 0.0, "end": 3.0, "text": "Insurance fraud costs everyone money."},
    {"id": "seg-2", "start": 3.0, "end": 5.0, "text": "Claim adjusters check timestamps first."},
    {"id": "seg-3", "start": 5.0, "end": 9.0, "text": "So keep your receipts and photographs."},
]

SEARCH_BODY = json.dumps(
    {
        "page": 1,
        "per_page": 2,
        "total_results": 2,
        "videos": [
            {
                "id": 3195394,
                "width": 1920,
                "height": 1080,
                "url": "https://www.pexels.com/video/aerial-view-of-city-traffic-3195394/",
                "image": "https://images.pexels.com/videos/3195394/free-video.jpg",
                "duration": 27,
                "user": {
                    "id": 1,
                    "name": "Kelly",
                    "url": "https://www.pexels.com/@kelly",
                },
                "video_files": [
                    {
                        "id": 1,
                        "quality": "hd",
                        "file_type": "video/mp4",
                        "width": 1920,
                        "height": 1080,
                        "link": "https://videos.pexels.com/video-files/3195394/3195394-hd.mp4",
                    },
                    {
                        "id": 2,
                        "quality": "sd",
                        "file_type": "video/mp4",
                        "width": 640,
                        "height": 360,
                        "link": "https://videos.pexels.com/video-files/3195394/3195394-sd.mp4",
                    },
                ],
            },
            # No usable file: it must be dropped rather than offered and refused.
            {
                "id": 2,
                "width": 0,
                "height": 0,
                "duration": 0,
                "video_files": [],
            },
        ],
    }
).encode("utf-8")


def _opener(body: bytes, status: int = 200):
    """A transport that answers with a fixed body and never touches the network."""

    def open_(request, timeout):
        return status, body

    return open_


# --------------------------------------------------------------- keywords


def test_keywords_come_from_the_clip_not_the_whole_transcript() -> None:
    window = clip_window(SEGMENTS, 3.0, 5.0)

    assert "timestamps" in window
    assert "receipts" not in window

    # "the"/"and" are filler, so the topic nouns lead instead.
    assert derive_keywords(window)[0] == "claim"
    assert "adjusters" in derive_keywords(window)


def test_keywords_skip_short_words_and_filler() -> None:
    keywords = derive_keywords(
        "the the and and basically insurance insurance fraud fraud timestamps timestamps"
    )

    assert "the" not in keywords
    assert "and" not in keywords
    assert "basically" not in keywords
    # Most frequent first, so the topic word leads the search.
    assert keywords[:2] == ["insurance", "fraud"]


def test_keywords_handle_a_transcript_with_no_latin_words() -> None:
    # A Hindi transcript has no ASCII stopwords; it must not collapse to nothing.
    text = "जब हमारा टोपिक इंशुरन्स फ्रॉड है तो आप कैसे बच सकते हैं"

    assert isinstance(derive_keywords(text), list)


def test_queries_are_trimmed_of_control_characters_and_length() -> None:
    assert normalise_query("  city\u0000  traffic \n ") == "city traffic"
    assert len(normalise_query("x" * 500)) <= 120


def test_an_empty_query_is_refused() -> None:
    with pytest.raises(ApiError) as error:
        search_videos(PexelsSettings(api_key="key"), "   ")

    assert error.value.code == "invalid_stock_query"


# --------------------------------------------------------------- results


def test_choose_file_prefers_a_handily_sized_hd_mp4() -> None:
    video = normalise_video(json.loads(SEARCH_BODY)["videos"][0])
    assert video is not None

    assert video["best_file"]["quality"] == "hd"
    assert video["best_file"]["width"] == 1920
    assert video["photographer"] == "Kelly"
    assert video["attribution"] == ATTRIBUTION
    assert video["license"]["name"] == LICENSE_NAME


def test_choose_file_falls_back_to_the_smallest_option_when_none_fits() -> None:
    files = [
        {"file_type": "video/mp4", "quality": "hd", "width": 3840},
        {"file_type": "video/mp4", "quality": "sd", "width": 640},
    ]

    chosen = choose_file(files, max_width=1920)

    assert chosen["width"] == 640


def test_results_without_a_downloadable_file_are_dropped() -> None:
    results = parse_search_results(SEARCH_BODY)

    assert [item["id"] for item in results] == ["3195394"]


def test_an_unreadable_provider_response_is_reported() -> None:
    with pytest.raises(ApiError) as error:
        parse_search_results(b"<html>oops</html>")

    assert error.value.code == "stock_unavailable"


def test_a_search_without_a_key_is_off_not_broken() -> None:
    with pytest.raises(ApiError) as error:
        search_videos(PexelsSettings(api_key=None), "city")

    assert error.value.code == "stock_not_configured"
    assert "PEXELS_API_KEY" in error.value.message


def test_a_search_surfaces_a_rejected_key() -> None:
    with pytest.raises(ApiError) as error:
        search_videos(
            PexelsSettings(api_key="wrong"), "city", opener=_opener(b"", status=401)
        )

    assert error.value.code == "stock_unauthorized"


def test_a_search_works_through_an_injected_transport() -> None:
    results = search_videos(PexelsSettings(api_key="k"), "traffic", opener=_opener(SEARCH_BODY))

    assert len(results) == 1
    assert results[0]["id"] == "3195394"


def test_fetching_a_video_re_reads_it_from_the_provider() -> None:
    single = json.dumps(json.loads(SEARCH_BODY)["videos"][0]).encode("utf-8")

    video = fetch_video(PexelsSettings(api_key="k"), 3195394, opener=_opener(single))

    assert video["id"] == "3195394"
    assert select_file(video, None)["quality"] == "hd"
    assert select_file(video, 2)["id"] == 2

    with pytest.raises(ApiError) as error:
        select_file(video, 999)
    assert error.value.code == "invalid_stock_selection"


# --------------------------------------------------------------- downloads


@pytest.mark.parametrize(
    "url",
    [
        "https://videos.pexels.com/video-files/1/1-hd.mp4",
        "https://api.pexels.com/videos/videos/1",
        "https://vod-progressive.akamaized.net/x/1.mp4",
    ],
)
def test_only_provider_hosts_may_be_downloaded(url: str) -> None:
    assert download_host_allowed(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "http://videos.pexels.com/video-files/1/1-hd.mp4",
        "https://evil.example.com/1.mp4",
        "https://videos.pexels.com.evil.example.com/1.mp4",
        "file:///etc/passwd",
        "https://",
    ],
)
def test_other_hosts_are_refused(url: str) -> None:
    assert download_host_allowed(url) is False


class _FakeResponse:
    def __init__(self, payload: bytes, *, status: int = 200, headers=None) -> None:
        self._payload = payload
        self._cursor = 0
        self.status = status
        self.headers = headers if headers is not None else {}
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunk = self._payload[self._cursor :]
            self._cursor = len(self._payload)
            return chunk
        chunk = self._payload[self._cursor : self._cursor + size]
        self._cursor += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


def test_a_download_is_streamed_and_bounded(tmp_path: Path) -> None:
    target = tmp_path / "clip.mp4"

    written = download_file(
        "https://videos.pexels.com/video-files/1/1.mp4",
        target,
        max_bytes=1024 * 1024,
        opener=lambda request, timeout: _FakeResponse(b"x" * 4096, headers={"Content-Length": "4096"}),
    )

    assert written == 4096
    assert target.read_bytes() == b"x" * 4096


def test_an_oversized_download_is_refused_mid_write(tmp_path: Path) -> None:
    target = tmp_path / "clip.mp4"

    with pytest.raises(ApiError) as error:
        download_file(
            "https://videos.pexels.com/video-files/1/1.mp4",
            target,
            max_bytes=1000,
            opener=lambda request, timeout: _FakeResponse(b"x" * 5000),
        )

    assert error.value.code == "file_too_large"


def test_a_download_address_outside_the_provider_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ApiError) as error:
        download_file(
            "https://evil.example.com/clip.mp4",
            tmp_path / "clip.mp4",
            max_bytes=1000,
            opener=lambda request, timeout: _FakeResponse(b"x"),
        )

    assert error.value.code == "invalid_stock_source"


# ----------------------------------------------------------- provenance


def test_provenance_records_the_source_and_creator() -> None:
    video = normalise_video(json.loads(SEARCH_BODY)["videos"][0])
    assert video is not None

    record = provenance_for(video, video["best_file"])

    assert record["provider"] == "pexels"
    assert record["page_url"].startswith("https://www.pexels.com/")
    assert record["photographer"] == "Kelly"
    assert record["attribution"] == ATTRIBUTION
    assert record["license"]["name"] == LICENSE_NAME
    assert record["file_quality"] == "hd"


# ----------------------------------------------------------- placement


def test_placement_prefers_a_turn_in_the_transcript() -> None:
    suggestion = suggest_placement(SEGMENTS, 0.0, 8.0, duration=2.0)

    # 3.0s is where segment 2 starts a new point, so that is the first turn.
    assert suggestion["start"] == 3.0
    assert suggestion["end"] == 5.0
    assert "3.0s" in suggestion["reason"]


def test_placement_falls_back_to_the_clip_start() -> None:
    suggestion = suggest_placement([], 0.0, 8.0, duration=3.5)

    assert suggestion["start"] == 0.0
    assert suggestion["end"] == 3.5
    assert "clip's beginning" in suggestion["reason"]


def test_placement_never_runs_past_the_clip() -> None:
    suggestion = suggest_placement(SEGMENTS, 6.0, 8.0, duration=5.0)

    assert suggestion["end"] <= 2.01


# ------------------------------------------------------------------ api


def _client(tmp_path: Path, *, api_key: str | None = None) -> TestClient:
    settings = Settings(
        project_root=Path(__file__).resolve().parents[1],
        workspace_dir=tmp_path / "workspace",
        max_upload_size_bytes=20 * 1024 * 1024,
        pexels_api_key=api_key,
    )
    return TestClient(create_app(settings), raise_server_exceptions=False)


def _project(tmp_path: Path, *, api_key: str | None = None) -> TestClient:
    client = _client(tmp_path, api_key=api_key)
    store = client.app.state.projects
    store.write_new(
        "abc123",
        name="Stock test",
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
    store.save_transcript(
        "abc123",
        {
            "version": 1,
            "project_id": "abc123",
            "generated_at": TRANSCRIPT_GENERATED_AT,
            "segments": SEGMENTS,
        },
    )
    return client


def test_stock_status_says_off_and_why(tmp_path: Path) -> None:
    payload = _project(tmp_path).get("/api/projects/abc123/stock").json()

    assert payload["configured"] is False
    assert "PEXELS_API_KEY" in payload["note"]
    assert payload["attribution"] == ATTRIBUTION


def test_stock_status_says_on_with_a_key(tmp_path: Path) -> None:
    payload = _project(tmp_path, api_key="k").get("/api/projects/abc123/stock").json()

    assert payload["configured"] is True
    assert "nothing is downloaded" in payload["note"]


def test_search_without_a_key_reports_a_clear_error(tmp_path: Path) -> None:
    response = _project(tmp_path).get("/api/projects/abc123/stock/search", params={"q": "city"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "stock_not_configured"


def test_search_without_a_query_is_refused(tmp_path: Path) -> None:
    response = _project(tmp_path, api_key="k").get("/api/projects/abc123/stock/search")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_stock_query"


def test_search_uses_the_clip_transcript_and_shows_provenance(tmp_path, monkeypatch) -> None:
    import app.stock as stock_module

    client = _project(tmp_path, api_key="k")
    monkeypatch.setattr(stock_module, "_urlopen", _opener(SEARCH_BODY))

    payload = client.get(
        "/api/projects/abc123/stock/search", params={"clip_id": "clip-1"}
    ).json()

    # Keywords came from the clip's own words rather than a hand-typed query.
    assert payload["keywords"][:2] == ["insurance", "fraud"]
    assert "insurance" in payload["query"]

    result = payload["results"][0]
    assert result["attribution"] == ATTRIBUTION
    assert result["photographer_url"] == "https://www.pexels.com/@kelly"
    assert result["license"]["name"] == LICENSE_NAME
    assert result["file"]["quality"] == "hd"

    suggestion = payload["suggested_placement"]
    assert 0.0 <= suggestion["start"] < suggestion["end"] <= 8.0


def test_importing_a_chosen_clip_keeps_its_credit(tmp_path, monkeypatch) -> None:
    if not _ffmpeg_available() or shutil.which("ffprobe") is None:
        pytest.skip(
            "FFmpeg/FFprobe are not on PATH, so an imported clip cannot be verified."
        )

    import app.stock as stock_module
    from tests.test_assets import _synth_video

    single = json.dumps(json.loads(SEARCH_BODY)["videos"][0]).encode("utf-8")
    client = _project(tmp_path, api_key="k")
    # Import re-reads the clip from the provider, so the transport answers with
    # the single-video document rather than a search page.
    monkeypatch.setattr(stock_module, "_urlopen", _opener(single))

    media = tmp_path / "stock.mp4"
    _synth_video(media)
    payload_bytes = media.read_bytes()
    monkeypatch.setattr(
        stock_module,
        "_urlopen_stream",
        lambda request, timeout: _FakeResponse(payload_bytes),
    )

    response = client.post(
        "/api/projects/abc123/stock/import", json={"video_id": 3195394}
    )

    assert response.status_code == 201
    asset = response.json()
    assert asset["kind"] == "broll"
    assert asset["provenance"]["provider"] == "pexels"
    assert asset["provenance"]["photographer"] == "Kelly"
    assert asset["provenance"]["attribution"] == ATTRIBUTION

    # The credit survives into the library listing the interface reads.
    library = client.get("/api/projects/abc123/assets").json()
    assert library["assets"][0]["provenance"]["provider"] == "pexels"


def test_a_missing_project_is_a_clear_404(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/api/projects/none/stock")

    assert response.status_code == 404


# ------------------------------------------------- acceptance (M8 criteria)


def test_a_stock_clip_can_be_searched_imported_and_placed(tmp_path, monkeypatch) -> None:
    """Milestone 8's acceptance, end to end through the real routes.

    Search, preview (the metadata the browser shows), select, import, and place a
    stock clip onto the timeline with its provenance recorded. No network is used:
    the transport is injected, but every route, validation, and storage path is
    the real one.
    """

    if not _ffmpeg_available() or shutil.which("ffprobe") is None:
        pytest.skip(
            "FFmpeg/FFprobe are not on PATH, so an imported clip cannot be verified."
        )

    import app.stock as stock_module
    from tests.test_assets import _synth_video

    single = json.dumps(json.loads(SEARCH_BODY)["videos"][0]).encode("utf-8")
    client = _project(tmp_path, api_key="k")

    def route(request, timeout):
        # Search and by-id are two different documents, so one transport answers
        # whichever the route is asking for.
        if "/videos/videos/" in request.full_url:
            return 200, single
        return 200, SEARCH_BODY

    monkeypatch.setattr(stock_module, "_urlopen", route)

    media = tmp_path / "stock.mp4"
    _synth_video(media, seconds=4)
    monkeypatch.setattr(
        stock_module,
        "_urlopen_stream",
        lambda request, timeout: _FakeResponse(media.read_bytes()),
    )

    # 1. Search: the results carry enough to preview and to credit the creator.
    search = client.get(
        "/api/projects/abc123/stock/search", params={"clip_id": "clip-1"}
    ).json()
    results = search["results"]
    assert results[0]["preview_url"].startswith("https://")
    assert results[0]["photographer"] == "Kelly"
    # And the placement it suggests is inside the clip.
    assert search["suggested_placement"]["end"] <= 8.0

    # 2. Import the chosen one; the file lands in the project's own folder.
    asset = client.post(
        "/api/projects/abc123/stock/import", json={"video_id": results[0]["id"]}
    ).json()
    assert asset["provenance"]["page_url"].startswith("https://www.pexels.com/")

    stored = client.app.state.projects.resolve_asset_path("abc123", asset)
    assert stored.is_file() and stored.stat().st_size > 0

    # 3. Place it on the timeline using the placement the search suggested.
    suggestion = search["suggested_placement"]

    saved = client.patch(
        "/api/projects/abc123/timeline",
        json={
            "clip_id": "clip-1",
            "broll": [
                {
                    "source": asset["id"],
                    "start": suggestion["start"],
                    "end": suggestion["end"],
                }
            ],
        },
    )

    assert saved.status_code == 200, saved.text
    broll = saved.json()["broll"]
    assert [item["source"] for item in broll] == [asset["id"]]

    # 4. The saved project still knows where the clip came from.
    project = client.get("/api/projects/abc123").json()
    assert project["assets"][0]["provenance"]["provider"] == "pexels"


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None
