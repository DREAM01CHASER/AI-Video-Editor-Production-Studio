from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.storage import UploadValidationError, Workspace, validate_upload


def test_health_endpoint_and_frontend_load(tmp_path: Path) -> None:
    settings = Settings(
        project_root=Path(__file__).resolve().parents[1],
        workspace_dir=tmp_path / "workspace",
        max_upload_size_bytes=10 * 1024 * 1024,
    )
    client = TestClient(create_app(settings))

    health_response = client.get("/api/health")
    assert health_response.status_code == 200
    assert health_response.json()["status"] == "ok"
    assert health_response.json()["workspace"] == "ready"

    frontend_response = client.get("/")
    assert frontend_response.status_code == 200
    assert "AI Video Production Studio" in frontend_response.text


def test_workspace_is_project_scoped_and_temp_cleanup_is_safe(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    project = workspace.create_project()
    temporary_file = project.temp / "render-part.tmp"
    temporary_folder = project.temp / "nested"
    temporary_folder.mkdir()
    temporary_file.write_text("temporary", encoding="utf-8")
    (temporary_folder / "nested.tmp").write_text("temporary", encoding="utf-8")

    assert project.source.is_dir()
    assert project.exports.is_dir()
    project.cleanup_temp()
    assert list(project.temp.iterdir()) == []
    assert project.root.parent == workspace.projects


def test_upload_validation_uses_a_generated_filename(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    project = workspace.create_project("project-1")
    path = project.allocate_source_path(
        filename="episode.mp4",
        content_type="video/mp4; codecs=h264",
        size_bytes=100,
        max_size_bytes=1024,
    )

    assert path.parent == project.source
    assert path.name.startswith("source-")
    assert path.name.endswith(".mp4")
    assert path.name != "episode.mp4"


@pytest.mark.parametrize(
    ("filename", "content_type", "size_bytes", "message"),
    [
        ("episode.txt", "text/plain", 10, "Unsupported"),
        ("episode.mp4", "video/webm", 10, "does not match"),
        ("episode.mp4", "video/mp4", 2049, "exceeds"),
        ("..\\outside.mp4", "video/mp4", 10, "invalid"),
    ],
)
def test_upload_validation_rejects_unsafe_metadata(
    filename: str, content_type: str, size_bytes: int, message: str
) -> None:
    with pytest.raises(UploadValidationError, match=message):
        validate_upload(filename, content_type, size_bytes, max_size_bytes=2048)
