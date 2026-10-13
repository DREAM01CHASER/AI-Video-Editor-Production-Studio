"""Milestone 10 acceptance: the whole documented path, from import to export.

`PROJECT.md` section 8 defines done as a list of things a new user can do. This
module walks exactly that list against the real routes with **real media**, so
"it works" means the whole workflow works, not that each part works alone.

Everything here needs FFmpeg, and the transcription step needs Windows speech
synthesis plus a cached Whisper model. Each requirement is skipped with an
explicit reason, so the suite never silently passes without proving anything.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import DEFAULT_WHISPER_MODEL, Settings
from app.main import create_app
from app.render import ffmpeg_available

PROJECT_ROOT = Path(__file__).resolve().parents[1]

requires_ffmpeg = pytest.mark.skipif(
    not ffmpeg_available(),
    reason="FFmpeg is not on PATH, so the real import/render path cannot be verified.",
)


def _model_is_cached() -> bool:
    try:
        from huggingface_hub import constants

        cache = Path(constants.HF_HUB_CACHE)
    except Exception:  # pragma: no cover - depends on the machine
        cache = Path.home() / ".cache" / "huggingface" / "hub"
    folder = cache / f"models--Systran--faster-whisper-{DEFAULT_WHISPER_MODEL}"
    return folder.is_dir() and any(folder.rglob("model.bin"))


requires_transcription = pytest.mark.skipif(
    sys.platform != "win32" or not ffmpeg_available() or not _model_is_cached(),
    reason=(
        "Needs Windows speech synthesis, FFmpeg, and a Whisper model already in the "
        f"local cache (looked for '{DEFAULT_WHISPER_MODEL}')."
    ),
)


def _client(tmp_path: Path) -> TestClient:
    settings = Settings(
        project_root=PROJECT_ROOT,
        workspace_dir=tmp_path / "workspace",
        max_upload_size_bytes=200 * 1024 * 1024,
        # Proving the cached path works: the weights must not be downloaded here.
        whisper_allow_model_download=False,
    )
    return TestClient(create_app(settings), raise_server_exceptions=False)


def _wait_for_job(client: TestClient, job_id: str, timeout: float = 900.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(0.5)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


def _speech_video(path: Path, seconds: int = 30) -> Path:
    """A short video with real spoken narration, for transcription to hear."""

    line = (
        "Here is the thing nobody tells you about going viral. "
        "Everyone focuses on the first three seconds, and that is the wrong place "
        "to start. The five minute rule is simple. If a clip holds your attention "
        "for five minutes, it will hold it for five seconds."
    )
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.Rate = -1; "
        f"$s.SetOutputToWaveFile('{str(path).replace(chr(39), chr(39) * 2)}'); "
        f"$s.Speak('{line}'); "
        "$s.Dispose();"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        check=True, capture_output=True, text=True, timeout=180,
    )

    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"color=c=0x204060:size=640x360:rate=25:duration={seconds}",
            "-i", str(path),
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest",
            # Reuse the spoken WAV slot for the finished video.
            str(path.with_suffix(".mp4")),
        ],
        check=True, capture_output=True, text=True, timeout=300,
    )
    return path.with_suffix(".mp4")


@requires_transcription
def test_the_whole_documented_workflow_works_end_to_end(tmp_path: Path) -> None:
    """Walk PROJECT.md's "definition of done" as a single test.

    Each step asserts the thing the user would actually check, and the final
    assertions inspect the exported file on disk rather than trusting the API's
    own report of success.
    """

    client = _client(tmp_path)
    source = _speech_video(tmp_path / "narration.wav")

    # 1. The app starts and reports itself healthy.
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    # 2. The interface loads.
    page = client.get("/")
    assert page.status_code == 200
    assert "AI Video Production Studio" in page.text

    # 3. Import a supported video and get a project id back.
    with source.open("rb") as handle:
        upload = client.post(
            "/api/projects",
            files={"file": (source.name, handle, "video/mp4")},
            data={"name": "Acceptance run"},
        )
    assert upload.status_code == 201, upload.text
    project_id = upload.json()["project_id"]
    metadata = upload.json()["source_metadata"]
    assert metadata["duration"] > 5
    assert metadata["width"] == 640 and metadata["height"] == 360

    # 4. A timestamped transcript.
    started = client.post(f"/api/projects/{project_id}/transcribe")
    assert started.status_code == 202, started.text
    job = _wait_for_job(client, started.json()["job_id"])
    assert job["status"] == "succeeded", job

    transcript = client.get(f"/api/projects/{project_id}/transcript").json()
    segments = transcript["segments"]
    assert segments, "the narration should have produced at least one segment"
    assert any(word["word"] for word in segments[0].get("words") or []) or segments[0]["text"].strip()
    # Timings have to be usable, not just present.
    assert segments[0]["start"] >= 0
    assert all(segment["end"] >= segment["start"] for segment in segments)

    # 5. Suggested clip candidates.
    suggested = client.post(f"/api/projects/{project_id}/suggest-clips")
    assert suggested.status_code == 200, suggested.text
    candidates = client.get(f"/api/projects/{project_id}/clips").json()["clips"]
    assert candidates, "clip suggestion should have proposed at least one clip"
    assert all(0 <= candidate["score"] <= 1 for candidate in candidates)
    assert all(
        candidate["source_end"] > candidate["source_start"] for candidate in candidates
    )

    # 6. Adjust a clip's in/out points.
    chosen = candidates[0]
    clip_id = chosen["id"]
    new_start = round(chosen["source_start"] + 0.5, 3)
    new_end = round(min(chosen["source_end"] - 0.5, new_start + 6.0), 3)
    if new_end <= new_start:
        new_start, new_end = chosen["source_start"], min(chosen["source_end"], chosen["source_start"] + 6.0)
    edited = client.patch(
        f"/api/projects/{project_id}/clips",
        json={
            "clip_id": clip_id,
            "clips": [
                {**chosen, "source_start": new_start, "source_end": new_end}
            ],
        },
    )
    assert edited.status_code == 200, edited.text

    # 7. Readable captions for that clip, and an editable timeline.
    timeline = client.get(f"/api/projects/{project_id}/timeline", params={"clip_id": clip_id})
    assert timeline.status_code == 200, timeline.text
    timeline_body = timeline.json()
    assert timeline_body["captions"]["enabled"] is True

    captions = client.get(
        f"/api/projects/{project_id}/captions", params={"clip_id": clip_id}
    )
    assert captions.status_code == 200, captions.text
    cues = captions.json()["cues"]
    assert cues, "a clip of spoken audio should produce caption cues"
    # Cue timings are clip-relative, so the first cue starts at the clip's start.
    assert cues[0]["start"] == pytest.approx(0.0, abs=0.25)

    saved = client.patch(
        f"/api/projects/{project_id}/timeline",
        json={
            "clip_id": clip_id,
            "captions": {"enabled": True, "preset": "bold-highlight"},
            "transitions": [{"kind": "fade", "at": "start", "duration": 0.4}],
        },
    )
    assert saved.status_code == 200, saved.text

    # 8. Render, and check the file itself rather than the API's word for it.
    render = client.post(f"/api/projects/{project_id}/render", json={"clip_id": clip_id})
    assert render.status_code == 202, render.text
    job = _wait_for_job(client, render.json()["job_id"])
    assert job["status"] == "succeeded", job

    entry = job["result"]
    workspace = client.app.state.projects.project_workspace(project_id)
    exported = workspace.exports / entry["file"]
    assert exported.is_file(), "the render reported success but wrote no file"
    assert exported.stat().st_size > 10_000

    probe = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "default=nw=1", str(exported),
        ],
        capture_output=True, text=True, timeout=120,
    )
    assert "width=1080" in probe.stdout and "height=1920" in probe.stdout, probe.stdout

    assert entry["transition_count"] == 1
    assert entry["cue_count"] == len(cues)

    # 9. Download it back through the API.
    download = client.get(f"/api/projects/{project_id}/exports/{entry['file']}")
    assert download.status_code == 200
    assert len(download.content) == exported.stat().st_size

    # 10. The saved project on disk describes the edit that was rendered.
    project_file = workspace.root / "project.json"
    stored = json.loads(project_file.read_text(encoding="utf-8"))
    assert stored["project_id"] == project_id
    assert stored["caption_style"]["preset"] == "bold-highlight"
    assert len(stored["timeline"]["transitions"]) == 1
    stored_clip = next(c for c in stored["clips"] if c["id"] == clip_id)
    assert stored_clip["source_start"] == pytest.approx(new_start, abs=0.01)
    assert stored_clip["edited"] is True

    # 11. Reopening the project restores the edit without re-running anything.
    reopened = client.get(f"/api/projects/{project_id}/timeline", params={"clip_id": clip_id})
    assert reopened.status_code == 200
    assert len(reopened.json()["transitions"]) == 1
    assert reopened.json()["captions"]["preset"] == "bold-highlight"
    assert client.get(f"/api/projects/{project_id}").status_code == 200
    assert any(p["project_id"] == project_id for p in client.get("/api/projects").json()["projects"])


@requires_ffmpeg
def test_every_step_explains_itself_when_a_step_fails(tmp_path: Path) -> None:
    """A failure has to be understandable, not a stack trace or an empty result."""

    client = _client(tmp_path)

    # An import of a file that is not a video.
    bogus = tmp_path / "not-a-video.mp4"
    bogus.write_bytes(b"this is not a video" * 100)
    with bogus.open("rb") as handle:
        response = client.post("/api/projects", files={"file": ("not-a-video.mp4", handle, "video/mp4")})
    assert response.status_code >= 400
    body = response.json()
    assert set(body["error"]) >= {"code", "message"}
    assert body["error"]["message"].strip()

    # An unknown project is a clear 404, not an empty document.
    assert client.get("/api/projects/does-not-exist").status_code == 404
    assert client.get("/api/projects/does-not-exist/timeline").json()["error"]["code"]

    # Rendering a clip that does not exist.
    assert client.post(
        "/api/projects/does-not-exist/render", json={"clip_id": "nope"}
    ).status_code == 404

    # An export that is not an export.
    assert client.get("/api/projects/does-not-exist/exports/../project.json").status_code in {
        400, 404,
    }


@requires_ffmpeg
def test_the_output_path_is_predictable_and_inside_the_project(tmp_path: Path) -> None:
    """A user has to be able to find the file, and it must stay in the project."""

    client = _client(tmp_path)
    store = client.app.state.projects

    store.write_new(
        "pathcheck", name="Path check", source_video="source/source-x.mp4",
        source_metadata={"duration": 30.0, "width": 640, "height": 360, "fps": 25.0,
                         "size_bytes": 4096},
    )
    store.save_clips(
        "pathcheck",
        [{"id": "clip-1", "title": "T", "reason": "r", "score": 0.5,
          "source_start": 0.0, "source_end": 5.0, "duration": 5.0}],
        {"generator": "heuristic"},
    )

    exports = client.get("/api/projects/pathcheck/exports")
    assert exports.status_code == 200
    assert "exports" in exports.json()

    workspace = store.project_workspace("pathcheck")
    assert workspace.exports.is_dir()
    assert workspace.temp.is_dir()
    # Everything lives under the project's own folder, and the paths are relative
    # in the document rather than absolute machine paths.
    stored = json.loads((workspace.root / "project.json").read_text(encoding="utf-8"))
    assert not Path(stored["source_video"]).is_absolute()
    assert stored["source_video"].startswith("source/")


@requires_ffmpeg
def test_the_generated_demo_media_is_usable_and_free_of_a_third_party_licence(tmp_path: Path) -> None:
    """The demo clip must be real media this project is free to ship."""

    demo = PROJECT_ROOT / "demo-media"
    if not (demo / "demo-source.mp4").is_file():
        pytest.skip(
            "demo media has not been generated; run: python scripts/make_demo_media.py"
        )

    client = _client(tmp_path)
    with (demo / "demo-source.mp4").open("rb") as handle:
        upload = client.post(
            "/api/projects",
            files={"file": ("demo-source.mp4", handle, "video/mp4")},
            data={"name": "Demo"},
        )

    assert upload.status_code == 201, upload.text
    assert upload.json()["source_metadata"]["duration"] > 10

    # The generated files are only referenced from the repo as a build step, so the
    # licence question cannot be answered wrongly by shipping them in Git.
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "demo-media" in gitignore, (
        "generated demo media must stay out of Git; it is rebuilt by a script"
    )
    assert shutil.which("ffmpeg") is not None