"""Milestone 2 acceptance checks for local backend and video ingest.

Parsing helpers are covered without FFmpeg installed. The end-to-end ingest
tests generate a real sample clip with FFmpeg and are skipped when the system
tools are unavailable, so the suite stays honest rather than silently passing.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.media import ffprobe_available, metadata_from_probe, parse_frame_rate
from app.errors import ApiError

SYSTEM_TOOLS_MISSING = not ffprobe_available() or shutil.which("ffmpeg") is None
requires_ffmpeg = pytest.mark.skipif(
    SYSTEM_TOOLS_MISSING, reason="FFmpeg and FFprobe must be on PATH for ingest tests."
)


# ----------------------------------------------------------------- pure unit


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("30000/1001", 29.97),
        ("25/1", 25.0),
        (" 30 ", 30.0),
        ("0/0", 0.0),
        ("", 0.0),
        (None, 0.0),
        ("not-a-rate", 0.0),
    ],
)
def test_parse_frame_rate(value: object, expected: float) -> None:
    assert parse_frame_rate(value) == expected


def test_metadata_from_probe_ignores_cover_art_and_uses_container_duration() -> None:
    payload = {
        "streams": [
            {"codec_type": "video", "width": 600, "height": 600, "disposition": {"attached_pic": 1}},
            {
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": "30000/1001",
                "r_frame_rate": "30/1",
            },
            {"codec_type": "audio"},
        ],
        "format": {"duration": "782.456"},
    }

    metadata = metadata_from_probe(payload, size_bytes=1024)

    assert metadata.width == 1920
    assert metadata.height == 1080
    assert metadata.fps == 29.97
    assert metadata.duration == 782.456
    assert metadata.size_bytes == 1024


def test_metadata_from_probe_rejects_a_file_without_video() -> None:
    with pytest.raises(ApiError) as error:
        metadata_from_probe({"streams": [{"codec_type": "audio"}], "format": {}}, 10)

    assert error.value.code == "no_video_stream"
    assert error.value.status_code == 422


# ------------------------------------------------------------ ingest helpers


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generate a deterministic 2 second, 320x240, 25 fps test clip."""

    target = tmp_path_factory.mktemp("media") / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
            "-t", "2", "-pix_fmt", "yuv420p",
            str(target),
        ],
        check=True,
        capture_output=True,
        shell=False,
    )
    return target


def _client(tmp_path: Path, *, max_upload_size_bytes: int = 50 * 1024 * 1024) -> TestClient:
    settings = Settings(
        project_root=Path(__file__).resolve().parents[1],
        workspace_dir=tmp_path / "workspace",
        max_upload_size_bytes=max_upload_size_bytes,
    )
    return TestClient(create_app(settings), raise_server_exceptions=False)


def _upload(client: TestClient, path: Path, filename: str = "sample.mp4", **form: str):
    with path.open("rb") as handle:
        return client.post(
            "/api/projects",
            files={"file": (filename, handle, "video/mp4")},
            data=form or None,
        )


# ------------------------------------------------------------ end to end


@requires_ffmpeg
def test_ingest_returns_project_id_and_correct_metadata(tmp_path: Path, sample_video: Path) -> None:
    client = _client(tmp_path)
    response = _upload(client, sample_video, name="Test episode")

    assert response.status_code == 201, response.text
    project = response.json()

    assert project["project_id"]
    assert project["name"] == "Test episode"
    assert project["clips"] == []

    metadata = project["source_metadata"]
    assert metadata["width"] == 320
    assert metadata["height"] == 240
    assert metadata["fps"] == 25.0
    assert 1.8 <= metadata["duration"] <= 2.2
    assert metadata["size_bytes"] == sample_video.stat().st_size

    # The source video lives under the project workspace, never in a flat uploads dir.
    project_dir = tmp_path / "workspace" / "projects" / project["project_id"]
    source_file = project_dir / project["source_video"]
    assert source_file.is_file()
    assert source_file.parent == project_dir / "source"
    assert source_file.name.startswith("source-")
    assert source_file.name != "sample.mp4"
    assert (project_dir / "project.json").is_file()
    # A successful ingest must leave no partial upload behind.
    assert list((project_dir / "temp").iterdir()) == []


@requires_ffmpeg
def test_ingested_project_can_be_read_listed_and_previewed(
    tmp_path: Path, sample_video: Path
) -> None:
    client = _client(tmp_path)
    project_id = _upload(client, sample_video).json()["project_id"]

    fetched = client.get(f"/api/projects/{project_id}")
    assert fetched.status_code == 200
    assert fetched.json()["project_id"] == project_id

    listing = client.get("/api/projects")
    assert listing.status_code == 200
    assert [item["project_id"] for item in listing.json()["projects"]] == [project_id]

    source = client.get(f"/api/projects/{project_id}/source")
    assert source.status_code == 200
    assert source.headers["content-type"] == "video/mp4"
    assert len(source.content) == sample_video.stat().st_size


def test_missing_project_returns_structured_404(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/api/projects/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "project_not_found"


@requires_ffmpeg
def test_oversized_upload_is_rejected_and_leaves_no_project(
    tmp_path: Path, sample_video: Path
) -> None:
    client = _client(tmp_path, max_upload_size_bytes=1024)
    response = _upload(client, sample_video)

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"
    projects_dir = tmp_path / "workspace" / "projects"
    assert list(projects_dir.iterdir()) == []


def test_unsupported_type_is_rejected_before_writing(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.post(
        "/api/projects",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_upload"


@requires_ffmpeg
def test_corrupt_video_returns_structured_error(tmp_path: Path) -> None:
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"this is not a video")

    response = _upload(_client(tmp_path), broken)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unreadable_media"
