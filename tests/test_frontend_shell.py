"""Milestone 1 acceptance checks for the frontend shell.

The shell is static, so these tests assert that the served markup exposes the
layout the interface is meant to provide and that the demo data is labelled
honestly rather than presented as real processing.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


REQUIRED_ELEMENT_IDS = [
    # Project header and status
    "project-name",
    "project-id-value",
    "project-status-value",
    "health-status",
    "data-source-badge",
    # Import
    "drop-zone",
    "file-input",
    "btn-import",
    # Metadata
    "metadata-duration",
    "metadata-resolution",
    "metadata-fps",
    "metadata-size",
    # Preview
    "preview-frame",
    "video-element",
    # Processing status and errors
    "processing-panel",
    "processing-steps",
    "progress-fill",
    "processing-error",
    # Clip candidates
    "candidates-list",
    "candidates-count",
    # Caption controls
    "caption-style",
    "font-size",
    "highlight-mode",
    # Export settings
    "export-panel",
    "aspect-ratio",
    "output-resolution",
    "export-format",
    # Timeline
    "timeline-ruler",
    "timeline-tracks",
]


def _client(tmp_path: Path) -> TestClient:
    settings = Settings(
        project_root=Path(__file__).resolve().parents[1],
        workspace_dir=tmp_path / "workspace",
        max_upload_size_bytes=10 * 1024 * 1024,
    )
    return TestClient(create_app(settings))


def test_shell_exposes_required_layout(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/")
    assert response.status_code == 200
    for element_id in REQUIRED_ELEMENT_IDS:
        assert f'id="{element_id}"' in response.text, element_id


def test_shell_labels_sample_data_and_keeps_export_disabled(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/").text
    assert "Demo data" in body
    assert "Sample data is illustrative" in body
    # Export must not look functional before the render milestone.
    assert 'id="btn-export" class="btn btn-primary btn--block" type="button" disabled' in body


def test_static_assets_are_served(tmp_path: Path) -> None:
    client = _client(tmp_path)
    css = client.get("/static/styles.css")
    js = client.get("/static/app.js")
    assert css.status_code == 200
    assert js.status_code == 200
    assert ".workspace" in css.text
    # The previous shell shipped a non-existent DOM API; guard against a revival.
    assert "addChange" not in js.text
