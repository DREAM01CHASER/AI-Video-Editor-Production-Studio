from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, load_env_file
from app.main import create_app
from app.storage import UploadValidationError, Workspace, validate_upload


# ------------------------------------------------------------------ .env


def test_the_env_file_is_actually_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A key in `.env` has to reach the settings, or configuring it does nothing.

    The README, `.env.example` and every stock/LLM error message tell the user to
    put their keys in `.env`. Without this the file was never opened, so the keys
    were silently ignored and both optional features stayed switched off.
    """

    env = tmp_path / ".env"
    env.write_text(
        "PEXELS_API_KEY=pexels-secret\n"
        "LLM_API_KEY=llm-secret\n"
        "LLM_BASE_URL=https://example.invalid/v1\n"
        "LLM_MODEL=some-model\n",
        encoding="utf-8",
    )

    for name in ("PEXELS_API_KEY", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)

    loaded = load_env_file(env)

    assert loaded["PEXELS_API_KEY"] == "pexels-secret"
    assert loaded["LLM_API_KEY"] == "llm-secret"


def test_the_env_file_reaches_the_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / ".env"
    env.write_text("PEXELS_API_KEY=pexels-secret\nLLM_MODEL=chosen-model\n", encoding="utf-8")

    for name in ("PEXELS_API_KEY", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL",
                 "PEXELS_BASE_URL", "AI_VIDEO_WORKSPACE", "MAX_UPLOAD_SIZE_MB"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("app.config.PROJECT_ROOT", tmp_path)

    settings = Settings.from_environment()

    assert settings.pexels_api_key == "pexels-secret"
    assert settings.llm_model == "chosen-model"


def test_a_missing_env_file_is_not_an_error(tmp_path: Path) -> None:
    """Most users have no `.env` at all, so its absence must stay silent."""

    assert load_env_file(tmp_path / "does-not-exist.env") == {}


def test_an_exported_variable_beats_the_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A value set in the shell must not be overwritten by the file."""

    env = tmp_path / ".env"
    env.write_text("LLM_MODEL=from-file\n", encoding="utf-8")
    monkeypatch.setenv("LLM_MODEL", "from-shell")

    loaded = load_env_file(env)

    assert loaded["LLM_MODEL"] == "from-file"  # what the file says
    import os

    assert os.environ["LLM_MODEL"] == "from-shell"  # what the app actually uses


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("KEY=value", "value"),
        ("KEY=value with spaces", "value with spaces"),
        ("KEY='quoted value'", "quoted value"),
        ('KEY="quoted value"', "quoted value"),
        ("KEY=value # trailing comment", "value"),
        ("export KEY=exported", "exported"),
        ("  KEY=indented  ", "indented"),
        ("KEY=", ""),
    ],
)
def test_the_env_file_parses_the_shapes_people_actually_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, line: str, expected: str
) -> None:
    env = tmp_path / ".env"
    env.write_text(line + "\n", encoding="utf-8")
    monkeypatch.delenv("KEY", raising=False)

    assert load_env_file(env).get("KEY", None) == expected


def test_comments_and_blank_lines_are_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "# a comment\n"
        "\n"
        "   \n"
        "REAL_KEY=real\n"
        "no_equals_sign_here\n"
        "=novalue\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("REAL_KEY", raising=False)

    loaded = load_env_file(env)

    assert loaded == {"REAL_KEY": "real"}


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
