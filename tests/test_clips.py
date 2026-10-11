"""Milestone 4 acceptance checks for candidate clip detection.

Everything here is deterministic and offline: the heuristic is pure Python, the
optional LLM pass is exercised through an injected transport so no request is
ever made, and the API tests use a synthetic transcript instead of a real model.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.clips import (
    MAX_SCORE,
    TITLE_MIN_WORDS,
    build_units,
    candidate_windows,
    density_score,
    duration_score,
    first_sentence,
    flag_relations,
    hook_score,
    is_boundary_after,
    is_sentence_end,
    make_title,
    overlap_ratio,
    overlap_seconds,
    public_candidate,
    punctuation_ratio,
    score_candidate,
    select_candidates,
    starts_new_thought,
    suggest_clips,
    text_similarity,
)
from app.config import Settings
from app.errors import ApiError
from app.llm import LlmSettings, make_ranker, parse_ranking, request_ranking
from app.main import create_app

#: A synthetic podcast-style transcript: a strong hook, filler, and a second one.
SPOKEN_ROWS = [
    (0.0, 6.0, "Welcome back to the show."),
    (6.0, 14.0, "Today we are talking about the five minute editing rule."),
    (14.0, 22.0, "Why does that matter so much? Because most creators waste hours."),
    (22.0, 30.0, "Let me explain what the rule actually is."),
    (30.0, 38.0, "You should never spend more than five minutes editing one minute of footage."),
    (38.0, 46.0, "That sounds impossible at first, right?"),
    (46.0, 54.0, "But here is the thing. The best editors all do this."),
    (54.0, 62.0, "They set a timer and they stop when it goes off."),
    (62.0, 70.0, "Now let us talk about the worst advice for new creators."),
    (70.0, 80.0, "Actually 90 percent of the editors I interview say the same thing."),
    (80.0, 88.0, "Your first 100 videos will be bad and that is fine."),
    (88.0, 96.0, "So stop worrying about the camera and start publishing."),
]

TRANSCRIPT_GENERATED_AT = "2026-01-01T00:00:00+00:00"


def transcript_document(rows=None, *, generated_at: str = TRANSCRIPT_GENERATED_AT) -> dict:
    rows = rows if rows is not None else SPOKEN_ROWS
    return {
        "version": 1,
        "project_id": "abc123",
        "generated_at": generated_at,
        "segments": [
            {"id": f"seg-{index + 1}", "start": start, "end": end, "text": text}
            for index, (start, end, text) in enumerate(rows)
        ],
    }


def _client(tmp_path: Path, **overrides: object) -> TestClient:
    settings = Settings(
        project_root=Path(__file__).resolve().parents[1],
        workspace_dir=tmp_path / "workspace",
        max_upload_size_bytes=50 * 1024 * 1024,
        **overrides,
    )
    return TestClient(create_app(settings), raise_server_exceptions=False)


def _project_with_transcript(tmp_path: Path, **overrides: object) -> TestClient:
    client = _client(tmp_path, **overrides)
    client.app.state.projects.write_new(
        "abc123",
        name="P",
        source_video="source/source-x.mp4",
        source_metadata={
            "duration": 96.0,
            "width": 320,
            "height": 240,
            "fps": 25.0,
            "size_bytes": 1024,
        },
    )
    client.app.state.projects.save_transcript("abc123", transcript_document())
    return client


# ------------------------------------------------------------- pure helpers


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Done.", True),
        ("Really?", True),
        ("Stop!", True),
        ('He said "go home."', True),
        ("and then", False),
        ("", False),
    ],
)
def test_is_sentence_end(text: str, expected: bool) -> None:
    assert is_sentence_end(text) is expected


def test_text_similarity_ignores_stopwords_and_is_symmetric() -> None:
    a = "the five minute editing rule for creators"
    b = "the five minute editing rule for editors"
    assert 0.0 < text_similarity(a, b) < 1.0
    assert text_similarity(a, b) == text_similarity(b, a)
    assert text_similarity(a, a) == 1.0
    assert text_similarity("", a) == 0.0


def test_overlap_helpers_measure_shared_time() -> None:
    left = {"source_start": 0.0, "source_end": 20.0}
    right = {"source_start": 10.0, "source_end": 30.0}

    assert overlap_seconds(left, right) == 10.0
    # Half of the shorter (20s) clip.
    assert overlap_ratio(left, right) == 0.5
    # Symmetric, and disjoint clips share nothing.
    assert overlap_ratio(right, left) == 0.5
    assert overlap_ratio(left, {"source_start": 40.0, "source_end": 50.0}) == 0.0


def test_hook_score_rewards_numbers_questions_and_superlatives() -> None:
    score, labels = hook_score("Why does that matter? 90 percent of editors agree.")
    assert score == 1.0
    assert "a question" in labels
    assert "a concrete number" in labels

    plain_score, plain_labels = hook_score("And then we walked to the store together.")
    assert plain_score == 0.0
    assert plain_labels == []


def test_hook_score_only_reads_the_opening() -> None:
    # The hook marker is buried far past the opening window.
    buried = "we talked for a while about nothing much at all and then " * 4 + "90 percent?"
    score, labels = hook_score(buried)
    assert labels == [] or score < 1.0


@pytest.mark.parametrize(
    ("rate", "expected"),
    [(2.5, 1.0), (1.8, 1.0), (3.8, 1.0), (0.5, 0.0), (6.0, 0.0)],
)
def test_density_score_peaks_inside_the_comfortable_band(rate: float, expected: float) -> None:
    assert density_score(rate) == pytest.approx(expected)


def test_density_score_tapers_between_the_extremes() -> None:
    assert 0.0 < density_score(1.2) < 1.0
    assert 0.0 < density_score(4.4) < 1.0


def test_duration_score_prefers_the_ideal_length() -> None:
    assert duration_score(32.0) == 1.0
    assert duration_score(25.0) == 1.0
    # At the window bounds the score is at its floor but never negative.
    assert duration_score(18.0) == pytest.approx(0.4)
    assert duration_score(60.0) == pytest.approx(0.4)
    assert duration_score(0.0) == 0.0


def test_build_units_drops_unusable_segments() -> None:
    units = build_units(
        [
            {"start": 0.0, "end": 4.0, "text": "  Hello there.  "},
            {"start": 4.0, "end": 8.0, "text": "   "},  # no words
            "not a dict",  # type: ignore[list-item]
            {"start": "bad", "end": 12.0, "text": "numbers are wrong"},
            {"start": 20.0, "end": 10.0, "text": "inverted timings are clamped"},
        ]
    )

    assert [unit.text for unit in units] == [
        "Hello there.",
        "inverted timings are clamped",
    ]
    # An inverted end is clamped forward rather than producing a negative length.
    assert units[1].end >= units[1].start
    assert units[1].duration >= 0


def test_make_title_uses_spoken_words_and_truncates_cleanly() -> None:
    # A trailing full stop reads like a sentence in a title and is dropped…
    assert make_title("welcome back to the show.", 0.0) == "Welcome back to the show"
    # …but "!" and "?" carry the hook and are kept.
    assert make_title("hello there, friend!", 0.0) == "Hello there, friend!"
    assert make_title("really?", 0.0) == "Really?"
    assert make_title("   ", 65.0) == "Clip from 1:05"

    long_text = "word " * 40
    title = make_title(long_text, 0.0, limit=40)
    assert len(title) <= 41
    assert title.endswith("…")
    assert not title.endswith(" …")


def test_candidate_windows_respect_the_length_bounds() -> None:
    units = build_units(transcript_document()["segments"])
    windows = candidate_windows(units)

    assert windows
    for first, last in windows:
        length = units[last].end - units[first].start
        assert 18.0 <= length <= 60.0


# ------------------------------------------------------------- selection


def _fake_candidate(candidate_id: str, start: float, end: float, score: float, text: str) -> dict:
    return {
        "id": candidate_id,
        "title": candidate_id,
        "reason": "…",
        "source_start": start,
        "source_end": end,
        "duration": end - start,
        "score": score,
        "score_label": "estimate",
        "signals": {},
        "_text": text,
        "_words_per_second": 2.5,
        "_hook_labels": [],
        "_starts_sentence": True,
        "_ends_sentence": True,
    }


def test_select_candidates_keeps_the_best_and_drops_duplicates() -> None:
    strong = _fake_candidate("a", 100.0, 130.0, 0.9, "alpha beta gamma")
    duplicate = _fake_candidate("b", 105.0, 135.0, 0.8, "alpha beta gamma")
    other = _fake_candidate("c", 10.0, 40.0, 0.7, "delta epsilon zeta")

    chosen, dropped = select_candidates([strong, duplicate, other], limit=5)

    assert dropped == 1
    # Ordered by time and renumbered, so the timeline reads naturally.
    assert [item["id"] for item in chosen] == ["clip-1", "clip-2"]
    assert chosen[0]["source_start"] == 10.0


def test_select_candidates_respects_the_limit() -> None:
    candidates = [
        _fake_candidate(str(index), index * 100.0, index * 100.0 + 30.0, 0.5, f"text {index}")
        for index in range(8)
    ]
    chosen, _ = select_candidates(candidates, limit=3)
    assert len(chosen) == 3


def test_flag_relations_ignores_trivial_overlap_but_flags_real_ones() -> None:
    trivial = _fake_candidate("a", 0.0, 30.0, 0.9, "one two three")
    barely = _fake_candidate("b", 29.0, 59.0, 0.8, "four five six")
    flag_relations([trivial, barely])
    # A one second touch must not raise a warning that would then mean nothing.
    assert "overlaps_with" not in trivial
    assert "overlaps_with" not in barely

    first = _fake_candidate("a", 0.0, 30.0, 0.9, "shared words here")
    second = _fake_candidate("b", 12.0, 42.0, 0.8, "shared words here")
    flag_relations([first, second])
    assert first["overlaps_with"] == ["b"]
    assert second["overlaps_with"] == ["a"]
    # Identical wording is also a near-duplicate.
    assert first["near_duplicate_of"] == "b"


def test_public_candidate_hides_internal_scoring_fields() -> None:
    public = public_candidate(_fake_candidate("clip-1", 0.0, 30.0, 0.7, "spoken words"))

    assert not any(key.startswith("_") for key in public)
    assert public["id"] == "clip-1"
    assert public["score_label"] == "estimate"
    assert "spoken words" not in json.dumps(public)


# ---------------------------------------------------------- suggest_clips


def test_suggest_clips_returns_a_small_readable_set() -> None:
    result = suggest_clips(transcript_document())
    clips = result["clips"]

    assert 3 <= len(clips) <= 5
    for clip in clips:
        assert 0 <= clip["score"] <= MAX_SCORE
        assert clip["score_label"] == "estimate"
        assert clip["source_end"] > clip["source_start"]
        assert clip["title"].strip()
        assert clip["reason"].strip()
        assert set(clip["signals"]) == {
            "hook",
            "self_contained",
            "density",
            "duration_fit",
            "opening_boundary",
        }

    analysis = result["analysis"]
    assert analysis["generator"] == "heuristic"
    assert analysis["clip_count"] == len(clips)
    assert analysis["based_on_transcript_generated_at"] == TRANSCRIPT_GENERATED_AT


def test_suggested_clips_never_promise_virality() -> None:
    result = suggest_clips(transcript_document())
    prose = " ".join(
        [clip["reason"] for clip in result["clips"]] + [result["analysis"]["score_meaning"]]
    ).lower()

    assert "guarantee" not in prose
    assert "viral" not in prose.replace("not a prediction of performance or virality", "")
    assert "not a prediction" in result["analysis"]["score_meaning"].lower()


def test_suggest_clips_reports_that_dropped_candidates_were_overlapping() -> None:
    analysis = suggest_clips(transcript_document())["analysis"]
    assert analysis["candidates_considered"] > analysis["clip_count"]
    assert any("overlapping" in note for note in analysis["notes"])


def test_suggest_clips_falls_back_to_one_clip_for_a_very_short_recording() -> None:
    short = transcript_document([(0.0, 4.0, "A very short thought."), (4.0, 9.0, "That is all.")])
    result = suggest_clips(short)

    assert len(result["clips"]) == 1
    assert result["clips"][0]["source_start"] == 0.0
    assert any("shorter than the target" in note for note in result["analysis"]["notes"])


def test_suggest_clips_rejects_an_empty_transcript() -> None:
    with pytest.raises(ApiError) as error:
        suggest_clips({"segments": []})

    assert error.value.code == "empty_transcript"
    assert error.value.status_code == 422


def test_suggest_clips_labels_a_custom_ranker_as_the_generator() -> None:
    seen: list[int] = []

    def ranker(candidates: list[dict]) -> str:
        seen.append(len(candidates))
        for candidate in candidates:
            candidate["title"] = "Model title"
            candidate["_reason_override"] = "Model reason."
        return "llm"

    result = suggest_clips(transcript_document(), ranker=ranker)

    assert seen == [len(result["clips"])]
    assert result["analysis"]["generator"] == "llm"
    assert all(clip["title"] == "Model title" for clip in result["clips"])
    assert all(clip["reason"] == "Model reason." for clip in result["clips"])


def test_suggest_clips_ignores_a_ranker_that_produced_nothing() -> None:
    result = suggest_clips(transcript_document(), ranker=lambda candidates: None)

    assert result["analysis"]["generator"] == "heuristic"
    assert all(clip["title"] != "Model title" for clip in result["clips"])


# --------------------------------------------------------------- LLM adapter


def _completion(content: str) -> bytes:
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")


def test_llm_is_disabled_without_a_key() -> None:
    settings = LlmSettings(api_key=None)
    assert settings.enabled is False

    def explode(*_: object) -> tuple[int, bytes]:
        raise AssertionError("the transport must not be used without a key")

    assert request_ranking(settings, [{"id": "clip-1", "_text": "hi"}], opener=explode) is None


def test_parse_ranking_reads_plain_and_fenced_json() -> None:
    valid = {"clip-1", "clip-2"}

    plain = parse_ranking(
        _completion('{"clips":[{"id":"clip-1","title":"T","reason":"R","score":0.8}]}'), valid
    )
    assert plain == {"clip-1": {"title": "T", "reason": "R", "score": 0.8}}

    fenced = parse_ranking(
        _completion('```json\n{"clips":[{"id":"clip-2","title":"F"}]}\n```'), valid
    )
    assert fenced == {"clip-2": {"title": "F"}}


def test_parse_ranking_rejects_unknown_ids_and_clamps_scores() -> None:
    ranking = parse_ranking(
        _completion(
            '{"clips":[{"id":"invented","title":"Nope"},'
            '{"id":"clip-1","score":5.5}]}'
        ),
        {"clip-1"},
    )

    assert ranking == {"clip-1": {"score": 1.0}}


@pytest.mark.parametrize(
    "body",
    [
        b"not json at all",
        b"{}",
        json.dumps({"choices": []}).encode(),
        json.dumps({"choices": [{"message": {"content": "no json inside"}}]}).encode(),
        json.dumps({"choices": [{"message": {"content": '{"clips":"wrong shape"}'}}]}).encode(),
    ],
)
def test_parse_ranking_returns_none_for_unusable_bodies(body: bytes) -> None:
    assert parse_ranking(body, {"clip-1"}) is None


def test_llm_failure_falls_back_to_the_heuristic() -> None:
    def failing_opener(request: object, timeout: int) -> tuple[int, bytes]:
        raise OSError("network is down")

    ranker = make_ranker(LlmSettings(api_key="test-key"), opener=failing_opener)
    calls: list[str] = []

    def flaky(candidates: list[dict]) -> str | None:
        calls.append("called")
        return ranker(candidates)

    result = suggest_clips(transcript_document(), ranker=flaky)

    assert calls == ["called"]
    assert result["analysis"]["generator"] == "heuristic"


def test_llm_http_error_falls_back_to_the_heuristic() -> None:
    ranker = make_ranker(
        LlmSettings(api_key="test-key"), opener=lambda request, timeout: (500, b"server error")
    )
    assert suggest_clips(transcript_document(), ranker=ranker)["analysis"]["generator"] == "heuristic"


def test_llm_success_relabels_clips_without_touching_their_times() -> None:
    def opener(request: object, timeout: int) -> tuple[int, bytes]:
        return 200, _completion(
            '{"clips":[{"id":"clip-1","title":"A sharper title","reason":"Model reason.","score":0.91}]}'
        )

    ranker = make_ranker(LlmSettings(api_key="test-key"), opener=opener)
    baseline = suggest_clips(transcript_document())
    result = suggest_clips(transcript_document(), ranker=ranker)

    assert result["analysis"]["generator"] == "llm"
    assert result["clips"][0]["title"] == "A sharper title"
    assert result["clips"][0]["reason"] == "Model reason."
    assert result["clips"][0]["score"] == 0.91

    # The boundaries must be identical: the model never supplies timestamps.
    assert [c["source_start"] for c in result["clips"]] == [
        c["source_start"] for c in baseline["clips"]
    ]
    assert [c["source_end"] for c in result["clips"]] == [
        c["source_end"] for c in baseline["clips"]
    ]


# ------------------------------------------------------------------ HTTP API


def test_clips_endpoint_is_empty_before_suggesting(tmp_path: Path) -> None:
    payload = _project_with_transcript(tmp_path).get("/api/projects/abc123/clips").json()

    assert payload["clips"] == []
    assert payload["analysis"] is None
    assert payload["has_transcript"] is True
    assert payload["stale"] is False


def test_suggesting_clips_requires_a_transcript(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.app.state.projects.write_new(
        "abc123",
        name="P",
        source_video="source/source-x.mp4",
        source_metadata={"duration": 60.0},
    )

    response = client.post("/api/projects/abc123/suggest-clips")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "transcript_required"


def test_suggesting_clips_persists_them_and_the_analysis(tmp_path: Path) -> None:
    client = _project_with_transcript(tmp_path)

    response = client.post("/api/projects/abc123/suggest-clips")
    assert response.status_code == 200, response.text
    body = response.json()
    assert 3 <= len(body["clips"]) <= 5
    assert body["analysis"]["generator"] == "heuristic"

    # Reloading the project restores the same clips from project.json.
    stored = client.get("/api/projects/abc123/clips").json()
    assert len(stored["clips"]) == len(body["clips"])
    assert stored["analysis"]["clip_count"] == len(body["clips"])

    document = json.loads(
        (
            tmp_path / "workspace" / "projects" / "abc123" / "project.json"
        ).read_text(encoding="utf-8")
    )
    assert len(document["clips"]) == len(body["clips"])
    assert document["clip_analysis"]["generator"] == "heuristic"


def test_suggesting_clips_for_an_unknown_project_is_a_404(tmp_path: Path) -> None:
    response = _client(tmp_path).post("/api/projects/nope/suggest-clips")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "project_not_found"


def test_a_configured_but_unavailable_llm_says_so_and_keeps_the_heuristic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.llm._urlopen", lambda request, timeout: (_ for _ in ()).throw(OSError("no network")))
    client = _project_with_transcript(tmp_path, llm_api_key="test-key")

    body = client.post("/api/projects/abc123/suggest-clips").json()

    assert body["analysis"]["generator"] == "heuristic"
    assert any("unavailable" in note for note in body["analysis"]["notes"])


def test_editing_a_clip_saves_the_new_times(tmp_path: Path) -> None:
    client = _project_with_transcript(tmp_path)
    clips = client.post("/api/projects/abc123/suggest-clips").json()["clips"]
    target = clips[0]

    response = client.patch(
        "/api/projects/abc123/clips",
        json={"clips": [{"id": target["id"], "source_start": 1.0, "source_end": 21.5}]},
    )

    assert response.status_code == 200, response.text
    edited = [clip for clip in response.json()["clips"] if clip["id"] == target["id"]][0]
    assert edited["source_start"] == 1.0
    assert edited["source_end"] == 21.5
    assert edited["duration"] == 20.5
    assert edited["edited"] is True

    # Other clips are untouched, and the edit survives a reload.
    others = [clip for clip in response.json()["clips"] if clip["id"] != target["id"]]
    assert all(clip.get("edited") is not True for clip in others)
    reloaded = client.get("/api/projects/abc123/clips").json()["clips"]
    assert [clip for clip in reloaded if clip["id"] == target["id"]][0]["source_end"] == 21.5


def test_editing_can_retitle_and_rescore_a_clip(tmp_path: Path) -> None:
    client = _project_with_transcript(tmp_path)
    target = client.post("/api/projects/abc123/suggest-clips").json()["clips"][0]

    response = client.patch(
        "/api/projects/abc123/clips",
        json={
            "clips": [
                {
                    "id": target["id"],
                    "source_start": target["source_start"],
                    "source_end": target["source_end"],
                    "title": "  My   own title  ",
                    "score": 0.42,
                }
            ]
        },
    )

    edited = [clip for clip in response.json()["clips"] if clip["id"] == target["id"]][0]
    assert edited["title"] == "My own title"
    assert edited["score"] == 0.42


@pytest.mark.parametrize(
    ("label", "edit", "expected_fragment"),
    [
        ("end before start", {"source_start": 10, "source_end": 5}, "not after its start"),
        ("negative start", {"source_start": -5, "source_end": 10}, "cannot start before 0"),
        ("past the source", {"source_start": 10, "source_end": 99999}, "past the end of the"),
        ("non numeric", {"source_start": "abc", "source_end": 10}, "numeric source_start"),
    ],
)
def test_invalid_clip_edits_are_rejected(
    tmp_path: Path, label: str, edit: dict, expected_fragment: str
) -> None:
    client = _project_with_transcript(tmp_path)
    clips = client.post("/api/projects/abc123/suggest-clips").json()["clips"]
    body = {"id": clips[0]["id"], **edit}

    response = client.patch("/api/projects/abc123/clips", json={"clips": [body]})

    assert response.status_code == 422, label
    error = response.json()["error"]
    assert error["code"] == "invalid_clip_edit"
    assert expected_fragment in error["message"]


def test_editing_an_unknown_clip_is_rejected(tmp_path: Path) -> None:
    client = _project_with_transcript(tmp_path)
    client.post("/api/projects/abc123/suggest-clips")

    response = client.patch(
        "/api/projects/abc123/clips",
        json={"clips": [{"id": "invented", "source_start": 1, "source_end": 5}]},
    )

    assert response.status_code == 422
    assert "no candidate clip with id 'invented'" in response.json()["error"]["message"]


def test_editing_clips_before_any_exist_is_a_404(tmp_path: Path) -> None:
    response = _project_with_transcript(tmp_path).patch(
        "/api/projects/abc123/clips",
        json={"clips": [{"id": "clip-1", "source_start": 1, "source_end": 5}]},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "no_clips"


def test_clips_go_stale_when_the_transcript_is_regenerated(tmp_path: Path) -> None:
    client = _project_with_transcript(tmp_path)
    client.post("/api/projects/abc123/suggest-clips")
    assert client.get("/api/projects/abc123/clips").json()["stale"] is False

    client.app.state.projects.save_transcript(
        "abc123", transcript_document(generated_at="2026-06-06T12:00:00+00:00")
    )

    assert client.get("/api/projects/abc123/clips").json()["stale"] is True


# ---------------------------------------- transcripts without sentence punctuation

#: Real Whisper output regularly has no final punctuation at all, so the boundary
#: logic must not depend on it. These rows mirror a transcript captured from the
#: actual model on a real audio file.
UNPUNCTUATED_ROWS = [
    (0.00, 1.92, "Welcome back to the show"),
    (1.92, 5.16, "Today we are talking about the five minute editing rule"),
    (5.16, 7.80, "Why does that matter so much"),
    (8.52, 11.62, "Because most creators waste hours on a single clip"),
    (11.62, 14.40, "Let me explain what the rule actually is"),
    (14.96, 18.88, "You should never spend more than five minutes editing one minute of footage"),
    (18.88, 22.68, "That sounds impossible at first right"),
    (23.14, 24.74, "But here is the thing"),
    (24.74, 27.50, "The best editors I know all do this"),
    (27.50, 30.90, "They set a timer and they stop when it goes off"),
    (30.90, 34.68, "Now let us talk about the worst advice for new creators"),
    (35.80, 39.70, "Actually 90% of the creators I interview say the same thing"),
    (40.34, 43.76, "Your first hundred videos will be bad and that is fine"),
    (43.76, 47.52, "So stop worrying about the camera and start publishing"),
]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("But here is the thing", True),
        ("So what now", True),
        ("Let me explain", True),
        ("The best editors do this", False),
        ("They set a timer", False),
        ("", False),
    ],
)
def test_starts_new_thought(text: str, expected: bool) -> None:
    assert starts_new_thought(text) is expected


def test_is_boundary_after_uses_punctuation_or_a_discourse_starter() -> None:
    units = build_units(transcript_document(UNPUNCTUATED_ROWS)["segments"])
    index = {unit.text: unit.index for unit in units}

    # "Because ..." opens a new thought, so the clip may safely end before it.
    assert is_boundary_after(units, index["Because most creators waste hours on a single clip"])
    # "But ..." likewise, and both have no punctuation of their own.
    assert is_boundary_after(units, index["Why does that matter so much"])
    # "The best editors ..." continues the previous clause, so it is not a cut point.
    assert is_boundary_after(units, index["But here is the thing"]) is False
    assert is_boundary_after(units, 10_000) is False


def test_punctuation_ratio_separates_punctuated_from_unpunctuated() -> None:
    punctuated = build_units(transcript_document()["segments"])
    unpunctuated = build_units(transcript_document(UNPUNCTUATED_ROWS)["segments"])

    assert len(unpunctuated) == len(UNPUNCTUATED_ROWS)
    assert punctuation_ratio(punctuated) == 1.0
    assert punctuation_ratio(unpunctuated) == 0.0
    assert punctuation_ratio([]) == 0.0


def test_first_sentence_stops_at_the_first_readable_clause() -> None:
    units = build_units(transcript_document(UNPUNCTUATED_ROWS)["segments"])
    index = {unit.text: unit.index for unit in units}
    assert len(units) > 8

    # Once the opening clause reads as a title, later clauses are not joined on.
    assert (
        first_sentence(units, index["But here is the thing"], len(units) - 1)
        == "But here is the thing"
    )

    # A very short opener still extends until it reads as a title.
    short = build_units(
        transcript_document(
            [(0.0, 1.0, "So"), (1.0, 5.0, "here is what changed for me this year")]
        )["segments"]
    )
    assert len(short) == 2
    assert first_sentence(short, 0, 1) == "So here is what changed for me this year"

    # A punctuated transcript still stops exactly on the sentence end.
    punctuated = build_units(transcript_document()["segments"])
    assert first_sentence(punctuated, 0, len(punctuated) - 1) == "Welcome back to the show."


def test_titles_do_not_run_two_clauses_together() -> None:
    clips = suggest_clips(transcript_document(UNPUNCTUATED_ROWS))["clips"]

    assert clips
    for clip in clips:
        assert clip["title"].strip()
        assert len(clip["title"]) <= 90
    # The regression: these two clauses must never be merged into one title.
    assert not any("the thing The best editors" in clip["title"] for clip in clips)


def test_an_unpunctuated_transcript_is_described_honestly() -> None:
    result = suggest_clips(transcript_document(UNPUNCTUATED_ROWS))
    analysis = result["analysis"]

    assert analysis["sentence_punctuation"] is False
    assert analysis["sentence_punctuation_ratio"] == 0.0
    assert any("no sentence punctuation" in note for note in analysis["notes"])
    for clip in result["clips"]:
        # It must not claim something it cannot know.
        assert "no sentence punctuation" in clip["reason"]
        assert "complete sentence" not in clip["reason"]


def test_a_punctuated_transcript_keeps_the_exact_wording() -> None:
    result = suggest_clips(transcript_document())

    assert result["analysis"]["sentence_punctuation"] is True
    for clip in result["clips"]:
        assert "no sentence punctuation" not in clip["reason"]


def test_a_single_stray_mark_does_not_make_edges_look_exact() -> None:
    rows = list(UNPUNCTUATED_ROWS)
    rows[2] = (5.16, 7.80, "Why does that matter so much?")

    analysis = suggest_clips(transcript_document(rows))["analysis"]

    # 1 punctuated segment out of 14 is 7%, far below the trust threshold.
    assert 0 < analysis["sentence_punctuation_ratio"] < 0.5
    assert analysis["sentence_punctuation"] is False


def test_title_min_words_is_a_small_readable_number() -> None:
    assert 3 <= TITLE_MIN_WORDS <= 8


def test_score_candidate_marks_an_opening_that_starts_mid_thought() -> None:
    units = build_units(transcript_document()["segments"])
    # Start on a unit whose predecessor did not end a sentence.
    mid = score_candidate(units, 1, 3, preceding_ends_sentence=False)
    clean = score_candidate(units, 0, 3, preceding_ends_sentence=True)

    assert mid["signals"]["opening_boundary"] == 0.3
    assert clean["signals"]["opening_boundary"] == 1.0
    assert mid["score"] < clean["score"]
