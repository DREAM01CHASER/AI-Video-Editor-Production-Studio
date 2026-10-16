"""Checks for the AI pass that reads a clip and names its visual moments.

The model is never called for real: its transport is injected, so what is tested
is this application's prompting, validation, fallback, and the wiring from the
model's reply to a downloaded, placed insert. A reply that is late, wrong, or
unusable must leave the transparent transcript-keyword pass in charge.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.broll_ai import build_prompt, clip_sentences, parse_moments, plan_moments
from app.llm import LlmSettings
from app.config import Settings
from app.main import create_app
from tests.test_broll_auto import (
    CLIP,
    PHOTO_BODY,
    SEGMENTS,
    _FakeResponse,
    _opener,
    _real_jpeg,
    _real_mp4,
)

VIDEO_BODY = json.dumps(
    {
        "videos": [
            {
                "id": 3195394,
                "width": 1920,
                "height": 1080,
                "url": "https://www.pexels.com/video/snowy-mountain-road-3195394/",
                "image": "https://images.pexels.com/videos/3195394/free-video.jpg",
                "duration": 27,
                "user": {"id": 1, "name": "Kelly", "url": "https://www.pexels.com/@kelly"},
                "video_files": [
                    {
                        "id": 1,
                        "quality": "hd",
                        "file_type": "video/mp4",
                        "width": 1920,
                        "height": 1080,
                        "link": "https://videos.pexels.com/video-files/3195394/3195394-hd.mp4",
                    }
                ],
            }
        ]
    }
).encode("utf-8")

#: One movement moment and one object moment: the two kinds the pass can ask for.
MOMENTS_BODY = json.dumps(
    {
        "moments": [
            {
                "start": 0.6,
                "end": 2.8,
                "query": "snowy mountain road at dawn",
                "reason": "the speaker describes a drive in the mountains",
                "type": "video",
            },
            {
                "start": 5.4,
                "end": 7.4,
                "query": "receipts and photographs on a wooden desk",
                "reason": "the speaker tells the viewer to keep the paperwork",
                "type": "image",
            },
        ]
    }
).encode("utf-8")


def _reply(body: bytes, status: int = 200):
    """A chat-completions transport that answers with a fixed body."""

    def open_(request, timeout):
        return status, json.dumps(
            {"choices": [{"message": {"content": body.decode("utf-8")}}]}
        ).encode("utf-8")

    return open_


# --------------------------------------------------------------- validation


def _clip_moments(items: list[dict], *, duration: float = 8.0, cap: int = 3) -> list[dict]:
    return parse_moments(
        json.dumps({"moments": items}), clip_duration=duration, max_items=cap
    )


def test_a_moment_is_kept_with_its_own_query_and_media_kind() -> None:
    moments = _clip_moments(
        [{"start": 1.0, "end": 3.0, "query": "receipts on a desk", "type": "image"}]
    )

    assert len(moments) == 1
    assert moments[0]["start"] == 1.0
    assert moments[0]["end"] == 3.0
    assert moments[0]["query"] == "receipts on a desk"
    assert moments[0]["media_type"] == "photo", "an image moment must ask for a photo"


def test_a_moment_outside_the_clip_is_brought_back_inside_it() -> None:
    moments = _clip_moments(
        [
            {"start": 20.0, "end": 24.0, "query": "night city traffic", "type": "video"},
            {"start": -5.0, "end": -1.0, "query": "a busy office", "type": "photo"},
        ]
    )

    for moment in moments:
        assert 0.0 <= moment["start"] < moment["end"] <= 8.0


def test_a_too_long_moment_is_trimmed_and_a_too_short_one_is_padded() -> None:
    moments = _clip_moments(
        [
            {"start": 0.0, "end": 7.5, "query": "warehouse shelves", "type": "video"},
            {"start": 3.0, "end": 3.1, "query": "a signed contract", "type": "image"},
        ]
    )

    for moment in moments:
        assert 1.5 <= moment["end"] - moment["start"] <= 4.0


def test_moments_too_close_together_are_dropped_and_the_rest_kept() -> None:
    moments = _clip_moments(
        [
            {"start": 1.0, "end": 2.5, "query": "a gym at dawn", "type": "video"},
            {"start": 2.6, "end": 4.0, "query": "a running track", "type": "video"},
            {"start": 6.0, "end": 7.5, "query": "a stopwatch", "type": "image"},
        ]
    )

    assert [moment["query"] for moment in moments] == ["a gym at dawn", "a stopwatch"]


def test_the_number_of_moments_is_capped() -> None:
    moments = _clip_moments(
        [
            {"start": 0.0, "end": 1.6, "query": "a mountain road", "type": "video"},
            {"start": 2.2, "end": 3.8, "query": "a wooden desk", "type": "image"},
            {"start": 4.4, "end": 5.9, "query": "a busy office", "type": "video"},
        ],
        cap=2,
    )

    assert len(moments) == 2


def test_an_unusable_moment_is_dropped_rather_than_repaired() -> None:
    moments = _clip_moments(
        [
            {"query": "no times at all", "type": "video"},
            {"start": 4.0, "end": 2.0, "query": "", "type": "video"},
            "not even an object",
            {"start": 1.0, "end": 3.0, "query": "   ", "type": "image"},
        ]
    )

    assert moments == []


def test_a_bare_list_answer_is_accepted() -> None:
    """Some models answer with just the list; it is still a usable plan."""

    moments = parse_moments(
        json.dumps(
            [{"start": 1.0, "end": 3.0, "query": "a printed receipt", "type": "photo"}]
        ),
        clip_duration=8.0,
        max_items=3,
    )

    assert len(moments) == 1


def test_the_prompt_carries_the_clip_and_forbids_one_moment_per_sentence() -> None:
    sentences = clip_sentences(SEGMENTS, clip_start=0.0, clip_end=8.0)
    prompt = build_prompt(sentences, clip_duration=8.0, max_items=3)

    assert sentences[0]["start"] == 0.0
    assert "Insurance fraud" in prompt
    assert "8.0s" in prompt
    assert "Do not pick one moment per sentence" in prompt
    assert "person training alone in gym" in prompt


def test_a_clip_with_no_words_inside_it_asks_for_nothing() -> None:
    plan = plan_moments(
        LlmSettings(api_key="k"),
        SEGMENTS,
        clip_start=20.0,
        clip_end=25.0,
        opener=_reply(MOMENTS_BODY),
    )

    assert plan["planner"] == "unavailable"
    assert "no transcript inside it" in plan["note"]


# ------------------------------------------------------------------ failures


def test_a_model_that_cannot_be_reached_is_not_an_error() -> None:
    def broken(request, timeout):
        raise OSError("no route to host")

    plan = plan_moments(
        LlmSettings(api_key="k"), SEGMENTS, clip_start=0.0, clip_end=8.0, opener=broken
    )

    assert plan["planner"] == "unavailable"
    assert "could not be reached" in plan["note"]


def test_a_model_that_answers_with_prose_falls_back() -> None:
    plan = plan_moments(
        LlmSettings(api_key="k"),
        SEGMENTS,
        clip_start=0.0,
        clip_end=8.0,
        opener=_reply(b"Sure! Here are some ideas for your video."),
    )

    assert plan["planner"] == "unavailable"
    assert plan["moments"] == []
    assert plan["note"]


def test_an_error_status_falls_back_with_the_code() -> None:
    plan = plan_moments(
        LlmSettings(api_key="k"),
        SEGMENTS,
        clip_start=0.0,
        clip_end=8.0,
        opener=_reply(MOMENTS_BODY, status=500),
    )

    assert plan["planner"] == "unavailable"
    assert "HTTP 500" in plan["note"]


def test_no_key_means_no_model_call() -> None:
    def must_not_run(request, timeout):  # pragma: no cover - must never be called
        raise AssertionError("a request was made without a configured key")

    plan = plan_moments(
        LlmSettings(api_key=None), SEGMENTS, clip_start=0.0, clip_end=8.0, opener=must_not_run
    )

    assert plan["planner"] == "unavailable"


# ---------------------------------------------------------------- endpoints


def _client(tmp_path: Path, **overrides: object) -> TestClient:
    settings = Settings(
        project_root=Path(__file__).resolve().parents[1],
        workspace_dir=tmp_path / "workspace",
        max_upload_size_bytes=50 * 1024 * 1024,
        **overrides,
    )
    return TestClient(create_app(settings), raise_server_exceptions=False)


def _project(tmp_path: Path, *, llm_key: str | None = "test-key") -> TestClient:
    client = _client(tmp_path, pexels_api_key="k", llm_api_key=llm_key)
    store = client.app.state.projects
    store.write_new(
        "abc123",
        name="AI B-roll",
        source_video="source/source-x.mp4",
        source_metadata={
            "duration": 8.0,
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


def test_propose_reports_the_model_as_the_planner(tmp_path, monkeypatch) -> None:
    import app.broll_ai as ai_module

    client = _project(tmp_path)
    monkeypatch.setattr(ai_module, "_urlopen", _reply(MOMENTS_BODY))

    payload = client.post(
        "/api/projects/abc123/broll/propose",
        json={"clip_id": "clip-1", "mode": "online", "max_items": 3},
    ).json()

    assert payload["planner"] == "ai"
    assert payload["ai"]["configured"] is True
    assert payload["ai"]["moments"] == 2
    assert [item["query"] for item in payload["items"]] == [
        "snowy mountain road at dawn",
        "receipts and photographs on a wooden desk",
    ]
    assert [item["source"]["media_type"] for item in payload["items"]] == ["video", "photo"]
    for item in payload["items"]:
        assert 0.0 <= item["start"] < item["end"] <= 8.0, "a moment left the clip"


def test_propose_falls_back_to_the_transcript_when_the_model_fails(
    tmp_path, monkeypatch
) -> None:
    import app.broll_ai as ai_module

    client = _project(tmp_path)
    monkeypatch.setattr(ai_module, "_urlopen", _reply(b"not json at all"))

    payload = client.post(
        "/api/projects/abc123/broll/propose", json={"clip_id": "clip-1", "mode": "online"}
    ).json()

    assert payload["planner"] == "transcript"
    assert payload["items"], "the transcript pass must still propose inserts"
    assert "instead" in payload["ai"]["note"]
    for item in payload["items"]:
        assert 0.0 <= item["start"] < item["end"] <= 8.0


def test_propose_without_a_model_key_is_the_transcript_pass(tmp_path) -> None:
    payload = _project(tmp_path, llm_key=None).post(
        "/api/projects/abc123/broll/propose", json={"clip_id": "clip-1", "mode": "online"}
    ).json()

    assert payload["planner"] == "transcript"
    assert payload["ai"]["configured"] is False


def test_add_b_rolls_searches_each_moment_for_the_media_it_asked_for(
    tmp_path, monkeypatch
) -> None:
    """The whole AI pass: model reply in, downloaded and placed inserts out."""

    import app.broll_ai as ai_module
    import app.stock as stock_module

    client = _project(tmp_path)
    monkeypatch.setattr(ai_module, "_urlopen", _reply(MOMENTS_BODY))

    searched: list[str] = []

    def search(request, timeout):
        searched.append(request.full_url)
        # Each endpoint answers with its own kind, as the real provider does.
        return (200, VIDEO_BODY) if "/videos/search" in request.full_url else (200, PHOTO_BODY)

    jpeg = _real_jpeg(tmp_path)
    mp4 = _real_mp4(tmp_path).read_bytes()

    def stream(request, timeout):
        payload = mp4 if request.full_url.endswith(".mp4") else jpeg
        return _FakeResponse(payload)

    monkeypatch.setattr(stock_module, "_urlopen", search)
    monkeypatch.setattr(stock_module, "_urlopen_stream", stream)

    response = client.post(
        "/api/projects/abc123/broll/auto",
        json={"clip_id": "clip-1", "mode": "online", "max_items": 3},
    )

    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["planner"] == "ai"
    assert payload["added"] == 2
    assert payload["downloaded"] == 2

    kinds = {item["query"]: item["media_type"] for item in payload["selected"]}
    assert kinds == {
        "snowy mountain road at dawn": "video",
        "receipts and photographs on a wooden desk": "photo",
    }

    # The image moment went to the photo endpoint and the movement moment to the
    # video one — never the other way round.
    photo_calls = [url for url in searched if "/v1/search" in url]
    video_calls = [url for url in searched if "/videos/search" in url]
    assert len(photo_calls) == 1 and "receipts" in photo_calls[0]
    assert len(video_calls) == 1 and "snowy" in video_calls[0]

    # The stages say what was done, in order, so the interface can show progress.
    labels = [stage["label"] for stage in payload["stages"]]
    assert labels[:2] == [
        "Analysed the clip's transcript",
        "Generated the search queries",
    ]
    assert labels[-1] == "Placed the B-roll on the timeline"
    assert all(stage["status"] in {"done", "failed", "skipped"} for stage in payload["stages"])

    # And the inserts really are on the clip's timeline, inside its own length.
    timeline = client.get("/api/projects/abc123/timeline?clip_id=clip-1").json()
    assert len(timeline["broll"]) == 2
    for item in timeline["broll"]:
        assert 0.0 <= item["start"] < item["end"] <= 8.0

    assets = client.get("/api/projects/abc123/assets").json()["assets"]
    assert {asset["media_kind"] for asset in assets} == {"image", "video"}


def test_add_b_rolls_still_places_inserts_when_the_model_fails(
    tmp_path, monkeypatch
) -> None:
    import app.broll_ai as ai_module
    import app.stock as stock_module

    client = _project(tmp_path)

    def broken(request, timeout):
        raise OSError("connection reset")

    monkeypatch.setattr(ai_module, "_urlopen", broken)
    photo_bytes = _real_jpeg(tmp_path)
    monkeypatch.setattr(stock_module, "_urlopen", _opener(PHOTO_BODY))
    monkeypatch.setattr(
        stock_module, "_urlopen_stream", lambda request, timeout: _FakeResponse(photo_bytes)
    )

    payload = client.post(
        "/api/projects/abc123/broll/auto", json={"clip_id": "clip-1", "mode": "auto"}
    )

    assert payload.status_code == 200, payload.text
    body = payload.json()
    assert body["planner"] == "transcript"
    assert body["added"] >= 1
    # The reason the model was not used is reported rather than hidden.
    assert "could not be reached" in body["ai"]["note"]
    assert "instead" in body["ai"]["note"]


@pytest.mark.parametrize("body", [b"", b"{}", b'{"moments": "no"}'])
def test_an_empty_or_wrong_shaped_reply_is_never_a_plan(body: bytes) -> None:
    plan = plan_moments(
        LlmSettings(api_key="k"),
        SEGMENTS,
        clip_start=0.0,
        clip_end=8.0,
        opener=_reply(body),
    )

    assert plan["planner"] == "unavailable"
    assert plan["moments"] == []
