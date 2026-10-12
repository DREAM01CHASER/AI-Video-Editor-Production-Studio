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


def test_shell_labels_sample_data_and_explains_export_requirements(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/").text
    # Markup is wrapped across lines, so compare against collapsed text.
    flat = " ".join(body.split())
    assert "Demo data" in body
    assert "Sample data is illustrative" in flat
    # Rendering is real now, but it renders a *selected clip*, so the button ships
    # disabled and its title says why rather than leaving a dead control.
    assert 'id="btn-export" class="btn btn-primary btn--block" type="button" disabled' in body
    assert "Select a clip first" in flat
    assert "Rendering is not implemented yet" not in flat
    # No feature may be advertised before it is built, and none may still be
    # announced as belonging to a future milestone.
    assert "arrive with the next milestone" not in flat
    assert "Transitions arrive with" not in flat
    assert "Milestone 9" in flat  # the footer names the milestone that is now built


def test_render_controls_and_result_player_are_present(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/").text
    for element_id in [
        "line-length",
        "high-contrast",
        "caption-preview",
        "caption-preview-status",
        "export-caption-mode",
        "export-write-srt",
        "export-progress",
        "export-progress-fill",
        "export-status",
        "export-error",
        "export-notes",
        "btn-export-cancel",
        "export-result",
        "export-video",
        "export-download",
        "export-srt-link",
    ]:
        assert f'id="{element_id}"' in body, element_id


def test_the_timeline_editor_controls_are_present(tmp_path: Path) -> None:
    """Milestone 6: the timeline is editable, so it needs its own controls."""

    body = _client(tmp_path).get("/").text
    for element_id in [
        "timeline-summary",
        "timeline-status",
        "timeline-error",
        "timeline-save-state",
        "btn-timeline-in",
        "btn-timeline-out",
        "output-fps",
    ]:
        assert f'id="{element_id}"' in body, element_id

    # The playhead buttons ship disabled with a reason instead of doing nothing.
    assert 'id="btn-timeline-in" class="btn btn-ghost" type="button" disabled' in body
    assert "Select a clip first" in body
    # Frame rates are offered from the timeline's own list.
    for fps in ('value="24"', 'value="25"', 'value="30"', 'value="60"'):
        assert fps in body


def test_the_asset_library_and_item_inspector_are_present(tmp_path: Path) -> None:
    """Milestone 7: local assets are imported, placed, and edited."""

    body = _client(tmp_path).get("/").text
    for element_id in [
        "asset-count",
        "asset-status",
        "asset-error",
        "asset-search",
        "asset-kind-filter",
        "asset-file-input",
        "btn-import-broll",
        "btn-import-sfx",
        "btn-add-broll",
        "btn-add-sfx",
        "timeline-placement",
        "item-panel",
        "item-name",
        "item-status",
        "item-fields",
        "item-start",
        "item-end",
        "item-gain",
        "item-fade",
        "item-placement",
        "item-fit",
        "btn-item-delete",
        "item-error",
    ]:
        assert f'id="{element_id}"' in body, element_id

    # Import and placement ship disabled with a reason, never as dead controls.
    assert 'id="btn-import-broll" class="btn btn-secondary" type="button" disabled' in body
    assert 'id="btn-import-sfx" class="btn btn-secondary" type="button" disabled' in body
    assert 'id="btn-add-broll" class="btn btn-ghost" type="button" disabled' in body
    assert 'id="btn-add-sfx" class="btn btn-ghost" type="button" disabled' in body


def test_the_stock_search_panel_is_present_and_off_by_default(tmp_path: Path) -> None:
    """Milestone 8: stock search is optional and must say so when unconfigured."""

    body = _client(tmp_path).get("/").text
    for element_id in [
        "stock-panel",
        "stock-query",
        "stock-orientation",
        "stock-count-select",
        "btn-stock-search",
        "stock-status",
        "stock-error",
        "stock-results",
        "stock-count",
    ]:
        assert f'id="{element_id}"' in body, element_id

    # The search button ships disabled with a reason, never as a dead control.
    assert "disabled" in body.split('id="btn-stock-search"', 1)[1][:140]
    # Nothing is downloaded before the user picks a result.
    assert "Nothing is downloaded until you press" in body


def test_transitions_and_sound_design_have_real_controls(tmp_path: Path) -> None:
    """Milestone 9: no control may ship that does not reach the live API."""

    body = _client(tmp_path).get("/").text
    for element_id in [
        "btn-add-transition",
        "item-transition-kind-field",
        "item-transition-kind",
        "item-transition-at-field",
        "item-transition-at",
        "item-transition-duration-field",
        "item-transition-duration",
        "sound-design-controls",
        "sound-design-enabled",
        "sound-design-kind",
        "sound-design-count",
        "sound-design-state",
        "sound-design-status",
    ]:
        assert f'id="{element_id}"' in body, element_id

    # Every transition the renderer implements is offered, and nothing more.
    options = body.split('id="item-transition-kind"', 1)[1].split("</select>", 1)[0]
    assert options.count("<option") == 3
    for kind in ("fade", "slide-left", "wipe-left"):
        assert f'value="{kind}"' in options

    # The button ships disabled with a reason, never as a dead control.
    assert 'id="btn-add-transition" class="btn btn-ghost" type="button" disabled' in body

    # Automatic sound design is opt-in, and the interface says what it does.
    assert 'id="sound-design-enabled" class="input"' in body
    assert "Off — no automatic sound" in body
    # The hint is wrapped across lines in the markup, so compare on collapsed text.
    flat = " ".join(body.split())
    assert "mixed under the speech" in flat


def test_the_transition_and_sound_design_controls_are_wired_to_the_api(tmp_path: Path) -> None:
    """A control that is never connected is worse than one that is missing."""

    script = _client(tmp_path).get("/static/app.js").text

    assert 'els.btnAddTransition.addEventListener("click", addTransition);' in script
    assert 'els.soundDesignEnabled.addEventListener("change"' in script
    assert 'els.soundDesignKind.addEventListener("change"' in script
    assert 'els.soundDesignCount.addEventListener("change"' in script
    # Both go through the one validated timeline save, so the project JSON and
    # the next render can never disagree with the panel.
    assert script.count("saveTimeline({") >= 3
    assert "sound_design: { ...(timeline.sound_design || {}), ...changes" in script


def test_static_assets_are_served(tmp_path: Path) -> None:
    client = _client(tmp_path)
    css = client.get("/static/styles.css")
    js = client.get("/static/app.js")
    assert css.status_code == 200
    assert js.status_code == 200
    assert ".workspace" in css.text
    # The previous shell shipped a non-existent DOM API; guard against a revival.
    assert "addChange" not in js.text
