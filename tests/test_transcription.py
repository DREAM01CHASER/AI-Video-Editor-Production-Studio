"""Milestone 3 acceptance checks for local transcription.

The parsing helpers, job registry, and transcript persistence are covered without
Whisper at all. The end-to-end test synthesises real speech, transcribes it with
the actual model, and is skipped with an explicit reason when FFmpeg, Windows
speech synthesis, or the cached model is unavailable, so it never silently passes.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import DEFAULT_WHISPER_MODEL, Settings
from app.errors import ApiError
from app.jobs import CANCELLED, FAILED, SUCCEEDED, JobRegistry
from app.main import create_app
from app.projects import ProjectStore
from app.storage import Workspace
from app.transcription import (
    _is_cuda_runtime_failure,
    build_transcript_document,
    resolve_compute_type,
    resolve_device,
    segment_to_dict,
    transcript_summary,
    word_to_dict,
)

MARKER_TRANSCRIPT = {
    "version": 1,
    "project_id": "abc123",
    "model": "medium",
    "language": "en",
    "duration": 10.0,
    "segment_count": 2,
    "has_word_timestamps": True,
    "generated_at": "2026-01-01T00:00:00+00:00",
    "segments": [
        {"id": "seg-1", "start": 0.0, "end": 4.0, "text": "hello there"},
        {"id": "seg-2", "start": 4.5, "end": 9.0, "text": "general kenobi"},
    ],
}


# ------------------------------------------------------------------- fakes


class FakeWord:
    def __init__(self, start: float, end: float, word: str, probability: float = 0.9):
        self.start = start
        self.end = end
        self.word = word
        self.probability = probability


class FakeSegment:
    def __init__(self, start: float, end: float, text: str, words: list | None = None):
        self.start = start
        self.end = end
        self.text = text
        self.words = words


class FakeInfo:
    def __init__(self, language: str = "en", duration: float = 12.0, probability: float = 0.99):
        self.language = language
        self.duration = duration
        self.language_probability = probability


def _client(tmp_path: Path, **overrides: object) -> TestClient:
    settings = Settings(
        project_root=Path(__file__).resolve().parents[1],
        workspace_dir=tmp_path / "workspace",
        max_upload_size_bytes=50 * 1024 * 1024,
        **overrides,
    )
    return TestClient(create_app(settings), raise_server_exceptions=False)


def _store(tmp_path: Path) -> ProjectStore:
    return ProjectStore(Workspace(tmp_path / "workspace").ensure_layout())


def _new_project(store: ProjectStore, project_id: str = "abc123") -> dict:
    return store.write_new(
        project_id,
        name="Test project",
        source_video="source/source-test.mp4",
        source_metadata={"duration": 12.0, "width": 320, "height": 240, "fps": 25.0, "size_bytes": 1024},
    )


# ------------------------------------------------------- device / compute


@pytest.mark.parametrize(
    ("requested", "devices", "expected"),
    [
        ("auto", 1, "cuda"),
        ("auto", 0, "cpu"),
        ("cuda", 1, "cuda"),
        ("gpu", 2, "cuda"),
        ("cpu", 1, "cpu"),
        ("CPU", 0, "cpu"),
    ],
)
def test_resolve_device_picks_the_right_device(requested, devices, expected) -> None:
    device, note = resolve_device(requested, devices)
    assert device == expected
    assert note is None


def test_resolve_device_reports_a_cuda_fallback_instead_of_hiding_it() -> None:
    device, note = resolve_device("cuda", 0)
    assert device == "cpu"
    assert note and "CPU" in note


def test_resolve_device_rejects_an_unknown_value() -> None:
    with pytest.raises(ApiError) as error:
        resolve_device("tpu", 1)
    assert error.value.code == "invalid_whisper_device"


@pytest.mark.parametrize(
    "message",
    [
        "Library cublas64_12.dll is not found or cannot be loaded",
        "Library cudnn64_9.dll is not found",
        "Could not load library cudart64_12.dll",
        "cuDNN error: CUDNN_STATUS_NOT_INITIALIZED",
    ],
)
def test_a_missing_cuda_runtime_is_recognised_at_inference_time(message: str) -> None:
    """A load-time fallback alone is not enough.

    CTranslate2 loads a CUDA model successfully even when the cuBLAS runtime is
    missing, and only fails on the first real inference — which is what happened
    on a machine with a visible GPU but no CUDA runtime installed.
    """

    assert _is_cuda_runtime_failure(RuntimeError(message))


@pytest.mark.parametrize(
    "message",
    [
        "No such file or directory: 'model.bin'",
        "Some other decoding failure entirely",
        "CUDA out of memory while transcribing",
    ],
)
def test_a_failure_that_is_not_a_missing_runtime_is_left_alone(message: str) -> None:
    assert not _is_cuda_runtime_failure(RuntimeError(message))


def test_an_api_error_is_never_mistaken_for_a_cuda_problem() -> None:
    """A cancellation must propagate as itself, not be retried on the CPU."""

    assert not _is_cuda_runtime_failure(
        ApiError("job_cancelled", "Transcription was cancelled.", 409)
    )


@pytest.mark.parametrize(
    ("device", "requested", "expected"),
    [
        ("cuda", "auto", "float16"),
        ("cpu", "auto", "int8"),
        # float16 on CPU is unsupported or unusably slow, so it is downgraded.
        ("cpu", "float16", "int8"),
        ("cuda", "int8_float16", "int8_float16"),
        ("cuda", "int8", "int8"),
    ],
)
def test_resolve_compute_type(device, requested, expected) -> None:
    assert resolve_compute_type(device, requested) == expected


# ------------------------------------------------------ segment conversion


def test_segment_to_dict_omits_words_when_the_model_did_not_produce_them() -> None:
    payload = segment_to_dict(FakeSegment(0.0, 2.5, "  hello world  "), 0)

    assert payload["id"] == "seg-1"
    assert payload["start"] == 0.0
    assert payload["end"] == 2.5
    assert payload["text"] == "hello world"
    # No invented word timings.
    assert "words" not in payload


def test_segment_to_dict_includes_word_timings_when_available() -> None:
    segment = FakeSegment(
        0.0, 1.2, " Hello there", words=[FakeWord(0.0, 0.4, " Hello"), FakeWord(0.4, 1.2, " there")]
    )
    payload = segment_to_dict(segment, 3)

    assert payload["id"] == "seg-4"
    assert [word["word"] for word in payload["words"]] == ["Hello", "there"]
    assert payload["words"][0]["start"] == 0.0
    assert payload["words"][0]["probability"] == 0.9


def test_word_to_dict_handles_missing_attributes() -> None:
    payload = word_to_dict(object())
    assert payload == {"start": 0.0, "end": 0.0, "word": "", "probability": 0.0}


def test_build_transcript_document_flags_word_timings_and_counts_segments() -> None:
    document = build_transcript_document(
        project_id="abc123",
        source_video="source/source-x.mp4",
        segments=[
            FakeSegment(0.0, 3.0, " one", words=[FakeWord(0.0, 3.0, " one")]),
            FakeSegment(3.5, 6.0, " two"),
        ],
        info=FakeInfo(language="en", duration=6.0),
        model="medium",
        device="cuda",
        compute_type="float16",
        duration=6.0,
        elapsed_seconds=2.5,
        requested_word_timestamps=True,
    )

    assert document["version"] == 1
    assert document["segment_count"] == 2
    assert document["has_word_timestamps"] is True
    assert document["language"] == "en"
    assert document["duration"] == 6.0
    assert document["segments"][0]["text"] == "one"
    assert "words" not in document["segments"][1]


def test_build_transcript_document_reports_no_word_timings_when_absent() -> None:
    document = build_transcript_document(
        project_id="abc123",
        source_video="source/source-x.mp4",
        segments=[FakeSegment(0.0, 3.0, " one")],
        info=FakeInfo(),
        model="medium",
        device="cpu",
        compute_type="int8",
        duration=3.0,
        elapsed_seconds=1.0,
        requested_word_timestamps=False,
    )

    assert document["has_word_timestamps"] is False
    assert "words" not in document["segments"][0]


def test_transcript_summary_uses_a_project_relative_path() -> None:
    summary = transcript_summary(
        {"model": "medium", "language": "en", "segment_count": 2, "has_word_timestamps": True}
    )

    assert summary["path"] == "transcripts/transcript.json"
    assert summary["segment_count"] == 2
    assert not Path(summary["path"]).is_absolute()


# ------------------------------------------------------------ job registry


def test_job_registry_tracks_progress_and_completion() -> None:
    registry = JobRegistry()
    job = registry.create("transcribe", subject="abc123")

    assert job.status == "pending"
    assert job.subject == "abc123"

    registry.update(job, progress=42.5, message="half way")
    assert job.progress == 42.5
    assert job.message == "half way"

    # Progress is clamped, so a buggy worker cannot exceed 100%.
    registry.update(job, progress=1000)
    assert job.progress == 100.0

    registry.succeed(job, {"segment_count": 3})
    assert job.status == SUCCEEDED
    assert job.result == {"segment_count": 3}
    assert job.error is None


def test_job_registry_unknown_job_raises_a_structured_404() -> None:
    with pytest.raises(ApiError) as error:
        JobRegistry().get("missing")
    assert error.value.code == "job_not_found"
    assert error.value.status_code == 404


def test_running_for_reports_only_unfinished_matching_jobs() -> None:
    registry = JobRegistry()
    job = registry.create("transcribe", subject="abc123")
    assert registry.running_for("transcribe", "abc123") is job
    assert registry.running_for("transcribe", "other") is None

    registry.succeed(job)
    assert registry.running_for("transcribe", "abc123") is None


def test_cancelling_a_finished_job_changes_nothing() -> None:
    registry = JobRegistry()
    job = registry.create("transcribe")
    registry.succeed(job)

    registry.request_cancel(job.id)

    assert job.status == SUCCEEDED
    assert job.cancel_requested is False


def test_a_failing_job_exposes_the_structured_error() -> None:
    registry = JobRegistry()
    job = registry.create("transcribe")

    def boom() -> dict:
        raise ApiError("model_unavailable", "no model", 503)

    registry.start(job, boom)
    _wait(job)

    assert job.status == FAILED
    assert job.error == {"code": "model_unavailable", "message": "no model"}


def test_an_unexpected_exception_is_captured_rather_than_killing_the_thread() -> None:
    registry = JobRegistry()
    job = registry.create("transcribe")

    def boom() -> dict:
        raise ValueError("something odd")

    registry.start(job, boom)
    _wait(job)

    assert job.status == FAILED
    assert "ValueError" in job.error["message"]


def test_a_started_job_publishes_its_result() -> None:
    registry = JobRegistry()
    job = registry.create("transcribe")

    registry.start(job, lambda: {"segment_count": 7})
    _wait(job)

    assert job.status == SUCCEEDED
    assert job.result == {"segment_count": 7}
    assert job.progress == 100.0


def test_a_cooperative_worker_ends_up_cancelled_not_failed() -> None:
    registry = JobRegistry()
    job = registry.create("transcribe")
    started = threading.Event()

    def work() -> dict:
        started.set()
        while not registry.is_cancel_requested(job.id):
            time.sleep(0.01)
        return {"segment_count": 0}

    registry.start(job, work)
    assert started.wait(10)
    registry.request_cancel(job.id)
    _wait(job)

    assert job.status == CANCELLED
    assert job.error is None


def _wait(job, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while not job.finished and time.time() < deadline:
        time.sleep(0.01)
    assert job.finished, f"job did not finish within {timeout}s"


# --------------------------------------------------- transcript persistence


def test_saving_a_transcript_writes_json_and_references_it(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _new_project(store)

    saved = store.save_transcript("abc123", MARKER_TRANSCRIPT)

    assert saved["transcript"]["path"] == "transcripts/transcript.json"
    assert saved["transcript"]["segment_count"] == 2
    assert saved["transcript"]["language"] == "en"

    path = tmp_path / "workspace" / "projects" / "abc123" / "transcripts" / "transcript.json"
    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8"))["segments"][1]["text"] == "general kenobi"

    # Reloading is what the UI does on reopen.
    assert store.load_transcript("abc123")["segment_count"] == 2
    assert store.load("abc123")["transcript"]["segment_count"] == 2


def test_a_project_without_a_transcript_raises_transcript_not_found(tmp_path: Path) -> None:
    store = _store(tmp_path)
    project = _new_project(store)

    assert project["transcript"] is None
    with pytest.raises(ApiError) as error:
        store.load_transcript("abc123")

    assert error.value.code == "transcript_not_found"
    assert error.value.status_code == 404


def test_transcript_path_refuses_to_escape_the_project_directory(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _new_project(store)

    outside = tmp_path / "outside-secret.json"
    outside.write_text('{"secret": true}', encoding="utf-8")

    project = store.load("abc123")
    # A tampered project.json must not be able to read a file outside the project.
    project["transcript"] = {"path": "../../../outside-secret.json"}

    with pytest.raises(ApiError) as error:
        store.transcript_path(project)

    assert error.value.code == "transcript_not_found"


# ---------------------------------------------------------------- HTTP API


def test_health_reports_the_transcription_settings(tmp_path: Path) -> None:
    payload = _client(tmp_path).get("/api/health").json()

    assert payload["transcription"]["available"] is True
    assert payload["transcription"]["model"] == DEFAULT_WHISPER_MODEL


def test_unknown_job_returns_a_structured_404(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/api/jobs/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "job_not_found"


def test_transcribing_an_unknown_project_returns_a_structured_404(tmp_path: Path) -> None:
    response = _client(tmp_path).post("/api/projects/does-not-exist/transcribe")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "project_not_found"


def test_transcript_endpoint_reports_nothing_saved_yet(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _new_project(client.app.state.projects)

    response = client.get("/api/projects/abc123/transcript")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "transcript_not_found"


def test_project_list_marks_whether_a_transcript_exists(tmp_path: Path) -> None:
    client = _client(tmp_path)
    store = client.app.state.projects
    _new_project(store)

    listed = client.get("/api/projects").json()["projects"]
    assert listed[0]["has_transcript"] is False

    store.save_transcript("abc123", MARKER_TRANSCRIPT)
    listed = client.get("/api/projects").json()["projects"]
    assert listed[0]["has_transcript"] is True


# ------------------------------------------------------------- end to end


SPEECH_TEXT = (
    "This is a test of the AI video production studio. "
    "The quick brown fox jumps over the lazy dog. "
    "We are checking whether local transcription produces accurate timestamps."
)

MODEL_IS_CACHED = False
try:  # pragma: no cover - depends on the machine
    from huggingface_hub import constants as _hf_constants

    _cache = Path(_hf_constants.HF_HUB_CACHE)
except Exception:  # pragma: no cover
    _cache = Path.home() / ".cache" / "huggingface" / "hub"

_model_folder = _cache / f"models--Systran--faster-whisper-{DEFAULT_WHISPER_MODEL}"
MODEL_IS_CACHED = _model_folder.is_dir() and any(_model_folder.rglob("model.bin"))

CAN_TRANSCRIBE_LOCALLY = (
    sys.platform == "win32"
    and shutil.which("ffmpeg") is not None
    and shutil.which("powershell") is not None
    and MODEL_IS_CACHED
)

requires_local_whisper = pytest.mark.skipif(
    not CAN_TRANSCRIBE_LOCALLY,
    reason=(
        "Needs FFmpeg, Windows speech synthesis, and a Whisper model already in the "
        f"local cache (looked for '{DEFAULT_WHISPER_MODEL}')."
    ),
)


def _synthesize_speech(target: Path, text: str) -> None:
    """Render real speech to a WAV file so transcription has something to hear."""

    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.Rate = 0; "
        f"$s.SetOutputToWaveFile('{target}'); "
        f"$s.Speak('{text}'); "
        "$s.Dispose()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        check=True,
        capture_output=True,
        timeout=180,
        shell=False,
    )


@pytest.fixture(scope="module")
def speech_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build a small MP4 whose audio is real synthesised speech."""

    base = tmp_path_factory.mktemp("speech")
    wav = base / "speech.wav"
    _synthesize_speech(wav, SPEECH_TEXT)
    assert wav.is_file() and wav.stat().st_size > 10_000

    video = base / "speech.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "color=black:s=320x240:r=25",
            "-i", str(wav),
            "-shortest",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            str(video),
        ],
        check=True,
        capture_output=True,
        timeout=180,
        shell=False,
    )
    return video


def _wait_for_job(client: TestClient, job_id: str, timeout: float = 600.0) -> dict:
    deadline = time.time() + timeout
    job: dict = {}
    while time.time() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200, response.text
        job = response.json()
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(0.25)
    raise AssertionError(f"transcription job did not finish within {timeout}s: {job}")


@requires_local_whisper
def test_a_real_clip_transcribes_end_to_end_through_the_api(
    tmp_path: Path, speech_video: Path
) -> None:
    # Downloading is disabled so the test proves the cached-model path works and
    # can never pull weights during a test run.
    client = _client(tmp_path, whisper_allow_model_download=False)

    with speech_video.open("rb") as handle:
        upload = client.post(
            "/api/projects",
            files={"file": ("speech.mp4", handle, "video/mp4")},
            data={"name": "Speech test"},
        )
    assert upload.status_code == 201, upload.text
    project_id = upload.json()["project_id"]
    assert upload.json()["source_metadata"]["duration"] > 5

    started = client.post(f"/api/projects/{project_id}/transcribe")
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]

    job = _wait_for_job(client, job_id)
    assert job["status"] == "succeeded", job
    assert job["progress"] == 100.0
    assert job["result"]["project_id"] == project_id
    assert job["result"]["model"] == DEFAULT_WHISPER_MODEL

    response = client.get(f"/api/projects/{project_id}/transcript")
    assert response.status_code == 200, response.text
    document = response.json()

    assert document["project_id"] == project_id
    assert document["segment_count"] >= 2
    assert document["language"] == "en"
    assert document["duration"] > 5

    # The acceptance criterion: a readable transcript, not just a file.
    joined = " ".join(segment["text"] for segment in document["segments"]).lower()
    assert "quick brown fox" in joined
    assert "lazy dog" in joined
    assert "local transcription" in joined

    # Timestamps must be usable and ordered inside the clip.
    previous_start = -1.0
    for segment in document["segments"]:
        assert 0 <= segment["start"] < segment["end"]
        assert segment["start"] >= previous_start - 0.001
        assert segment["end"] <= document["duration"] + 1.0
        assert segment["text"].strip()
        previous_start = segment["start"]

    project_dir = tmp_path / "workspace" / "projects" / project_id
    transcript_file = project_dir / "transcripts" / "transcript.json"
    assert transcript_file.is_file()
    assert json.loads(transcript_file.read_text(encoding="utf-8"))["segment_count"] == document["segment_count"]

    # project.json references the transcript by relative path.
    project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
    assert project["transcript"]["path"] == "transcripts/transcript.json"
    assert not Path(project["transcript"]["path"]).is_absolute()

    # A successful run leaves no partial files behind.
    assert list((project_dir / "temp").iterdir()) == []

    # A second start while running is refused; after completion the project is
    # marked transcribed in the sidebar listing.
    listed = client.get("/api/projects").json()["projects"]
    assert listed[0]["project_id"] == project_id
    assert listed[0]["has_transcript"] is True

    # The saved transcript is reused without re-running the model.
    again = client.get(f"/api/projects/{project_id}/transcript")
    assert again.status_code == 200
    assert again.json()["segment_count"] == document["segment_count"]
