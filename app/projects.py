"""Project persistence.

The serializable JSON project is the source of truth (see DESIGN.md), so each
project is a directory in the workspace with a ``project.json`` document plus
``source/``, ``transcripts/``, ``assets/``, ``temp/``, and ``exports/`` folders.
No database is used.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .errors import ApiError
from .storage import ProjectWorkspace, Workspace, is_valid_project_id

PROJECT_FILE_NAME = "project.json"

DEFAULT_CAPTION_STYLE = {
    "preset": "bold-highlight",
    "font_size": "medium",
    "placement": "bottom",
    "highlight_mode": "shadow",
}

DEFAULT_EXPORT = {
    "aspect_ratio": "9:16",
    "resolution": "1080x1920",
    "format": "mp4",
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ProjectStore:
    """Create, read, and list projects inside a workspace."""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    def _project_root(self, project_id: str) -> Path:
        if not is_valid_project_id(project_id):
            raise ApiError("invalid_project_id", "The project identifier is invalid.", 400)
        return self.workspace.projects / project_id

    def new_project_workspace(self) -> ProjectWorkspace:
        """Allocate an empty project layout and return it for streaming an upload."""

        return self.workspace.create_project()

    def write_new(
        self,
        project_id: str,
        *,
        name: str,
        source_video: str,
        source_metadata: dict[str, float | int],
    ) -> dict:
        """Create ``project.json`` for a freshly ingested project."""

        project = {
            "project_id": project_id,
            "name": name,
            "created_at": _utcnow(),
            "updated_at": _utcnow(),
            "source_video": source_video,
            "source_metadata": source_metadata,
            "clips": [],
            "caption_style": dict(DEFAULT_CAPTION_STYLE),
            "export": dict(DEFAULT_EXPORT),
            "assets": [],
        }
        return self.save(project)

    def save(self, project: dict) -> dict:
        """Write a project document atomically and stamp its updated time."""

        project_id = str(project.get("project_id", ""))
        project["updated_at"] = _utcnow()

        project_root = self._project_root(project_id)
        project_root.mkdir(parents=True, exist_ok=True)

        target = project_root / PROJECT_FILE_NAME
        temporary = project_root / f"{PROJECT_FILE_NAME}.tmp"
        temporary.write_text(json.dumps(project, indent=2), encoding="utf-8")
        temporary.replace(target)
        return project

    def load(self, project_id: str) -> dict:
        """Read a project document, or raise a structured 404."""

        project_file = self._project_root(project_id) / PROJECT_FILE_NAME
        if not project_file.is_file():
            raise ApiError(
                "project_not_found", f"No project exists with id '{project_id}'.", 404
            )

        try:
            return json.loads(project_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ApiError(
                "project_unreadable",
                f"The saved data for project '{project_id}' could not be read.",
                500,
            ) from exc

    def source_path(self, project: dict) -> Path:
        """Resolve the project's source video, refusing any path escape."""

        relative = str(project.get("source_video") or "")
        project_root = self._project_root(str(project.get("project_id", ""))).resolve()
        candidate = (project_root / relative).resolve()

        if not candidate.is_relative_to(project_root) or not candidate.is_file():
            raise ApiError(
                "source_missing", "The source video for this project is not available.", 404
            )
        return candidate

    def list_summaries(self) -> list[dict]:
        """List saved projects, newest first, without loading full documents."""

        if not self.workspace.projects.is_dir():
            return []

        summaries: list[dict] = []
        for entry in sorted(self.workspace.projects.iterdir()):
            if not entry.is_dir() or not is_valid_project_id(entry.name):
                continue
            project_file = entry / PROJECT_FILE_NAME
            if not project_file.is_file():
                continue
            try:
                project = json.loads(project_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            metadata = project.get("source_metadata") or {}
            summaries.append(
                {
                    "project_id": entry.name,
                    "name": project.get("name") or entry.name,
                    "updated_at": project.get("updated_at"),
                    "duration": metadata.get("duration"),
                    "clip_count": len(project.get("clips") or []),
                }
            )

        summaries.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return summaries
