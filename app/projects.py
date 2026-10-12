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
from .transcription import transcript_summary

PROJECT_FILE_NAME = "project.json"
TRANSCRIPT_FILE_NAME = "transcript.json"
TRANSCRIPT_RELATIVE_PATH = f"transcripts/{TRANSCRIPT_FILE_NAME}"

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
    "fps": 30,
    "caption_mode": "burn",
    "write_srt": True,
}

#: Renders are kept as a short history so reopening a project shows the file that
#: was actually produced, not just the newest one. Older entries stop being
#: listed after this many; their files are left on disk for the user to delete.
MAX_RECORDED_EXPORTS = 30


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
            "transcript": None,
            "clips": [],
            "clip_analysis": None,
            "caption_style": dict(DEFAULT_CAPTION_STYLE),
            "export": dict(DEFAULT_EXPORT),
            "assets": [],
            # The item tracks start empty but present, so the timeline can show
            # every track without inventing data for old projects.
            "timeline": {
                "version": 1,
                "broll": [],
                "sfx": [],
                "transitions": [],
                "updated_at": None,
            },
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

    def _resolve_inside(
        self, project_id: str, relative: str, *, code: str, message: str
    ) -> Path:
        """Resolve a project-relative path, refusing any escape from the project."""

        project_root = self._project_root(project_id).resolve()
        candidate = (project_root / relative).resolve()

        if not candidate.is_relative_to(project_root) or not candidate.is_file():
            raise ApiError(code, message, 404)
        return candidate

    def source_path(self, project: dict) -> Path:
        """Resolve the project's source video, refusing any path escape."""

        return self._resolve_inside(
            str(project.get("project_id", "")),
            str(project.get("source_video") or ""),
            code="source_missing",
            message="The source video for this project is not available.",
        )

    def save_transcript(self, project_id: str, document: dict) -> dict:
        """Write the transcript JSON and reference it from ``project.json``.

        The transcript is written to its own file rather than inlined into the
        project document, so a long transcript does not bloat every project read.
        """

        project = self.load(project_id)
        transcripts = ProjectWorkspace(
            self._project_root(project_id)
        ).ensure_layout().transcripts

        target = transcripts / TRANSCRIPT_FILE_NAME
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(document, indent=2), encoding="utf-8")
        temporary.replace(target)

        project["transcript"] = transcript_summary(document)
        return self.save(project)

    def transcript_path(self, project: dict) -> Path:
        """Resolve the project's saved transcript, refusing any path escape."""

        reference = project.get("transcript") or {}
        return self._resolve_inside(
            str(project.get("project_id", "")),
            str(reference.get("path") or TRANSCRIPT_RELATIVE_PATH),
            code="transcript_not_found",
            message="This project has no transcript yet.",
        )

    def load_transcript(self, project_id: str) -> dict:
        """Read a saved transcript document, or raise a structured 404."""

        project = self.load(project_id)
        path = self.transcript_path(project)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ApiError(
                "transcript_unreadable",
                f"The saved transcript for project '{project_id}' could not be read.",
                500,
            ) from exc

    def project_workspace(self, project_id: str) -> ProjectWorkspace:
        """Return this project's on-disk layout, creating any missing folders."""

        return ProjectWorkspace(self._project_root(project_id)).ensure_layout()

    def save_settings(
        self,
        project_id: str,
        *,
        caption_style: dict | None = None,
        export: dict | None = None,
    ) -> dict:
        """Save caption and export settings, leaving the rest of the project alone.

        These live in ``project.json`` because the project document is the source
        of truth for the edit, and the next render must use the saved choices.
        """

        project = self.load(project_id)
        if caption_style is not None:
            project["caption_style"] = caption_style
        if export is not None:
            project["export"] = export
        return self.save(project)

    def save_timeline(
        self,
        project_id: str,
        *,
        source_start: float,
        source_end: float,
        clip_id: str,
        caption_style: dict,
        export: dict,
        tracks: dict[str, list],
    ) -> dict:
        """Write one validated timeline edit into the project document.

        The whole edit is saved in a single write: the clip's in/out points, the
        caption style, the render settings, and the item tracks. Nothing is
        half-applied, so the next render always matches the timeline the user saw.
        """

        project = self.load(project_id)
        found = False
        for clip in project.get("clips") or []:
            if str(clip.get("id")) != clip_id:
                continue
            clip["source_start"] = source_start
            clip["source_end"] = source_end
            clip["duration"] = round(max(0.0, source_end - source_start), 3)
            clip["edited"] = True
            found = True
            break

        if not found:
            raise ApiError(
                "clip_not_found", f"No clip with id '{clip_id}' exists in this project.", 404
            )

        project["caption_style"] = caption_style
        project["export"] = export
        timeline = dict(project.get("timeline") or {})
        timeline.update(tracks)
        # The timeline is stamped here so every saved edit is dated, whatever the
        # caller remembered to include.
        timeline["updated_at"] = _utcnow()
        timeline["version"] = int(timeline.get("version") or 1)
        project["timeline"] = timeline
        return self.save(project)

    def record_export(self, project_id: str, entry: dict) -> dict:
        """Add a finished render to the project's export history."""

        project = self.load(project_id)
        history = [item for item in (project.get("exports") or []) if isinstance(item, dict)]
        # The timestamp is stamped here so every recorded render has one, whatever
        # the caller remembered to include.
        stamped = {"created_at": _utcnow(), **entry}
        project["exports"] = [stamped, *history][:MAX_RECORDED_EXPORTS]
        return self.save(project)

    def load_exports(self, project_id: str) -> dict:
        """Return saved renders plus the settings that produced them."""

        project = self.load(project_id)
        exports = [item for item in (project.get("exports") or []) if isinstance(item, dict)]
        return {
            "exports": exports,
            "latest": exports[0] if exports else None,
            "caption_style": project.get("caption_style") or dict(DEFAULT_CAPTION_STYLE),
            "export_settings": project.get("export") or dict(DEFAULT_EXPORT),
        }

    def resolve_export_path(self, project_id: str, filename: str) -> Path:
        """Resolve a file inside the project's ``exports/`` folder safely.

        The name is treated as a single segment, so ``../project.json`` and any
        other escape attempt is rejected before the filesystem is touched.
        """

        candidate = Path(str(filename or ""))
        if (
            not candidate.name
            or candidate.name != str(filename)
            or candidate.suffix.lower() not in {".mp4", ".srt"}
        ):
            raise ApiError(
                "invalid_export_name", "That export file name is not valid.", 400
            )

        return self._resolve_inside(
            project_id,
            f"exports/{candidate.name}",
            code="export_not_found",
            message=f"No export named '{candidate.name}' exists for this project.",
        )

    def save_clips(self, project_id: str, clips: list[dict], analysis: dict) -> dict:
        """Store suggested clips and the analysis that produced them.

        Clips live in ``project.json`` because the project document is the source
        of truth for the edit, and they stay small: a handful of candidates.
        """

        project = self.load(project_id)
        project["clips"] = clips
        project["clip_analysis"] = analysis
        return self.save(project)

    def load_clips(self, project_id: str) -> dict:
        """Return saved clips plus whether they still match the current transcript."""

        project = self.load(project_id)
        analysis = project.get("clip_analysis") or None
        transcript = project.get("transcript") or {}

        # Re-transcribing makes earlier suggestions out of date, so say so rather
        # than presenting stale suggestions as current.
        based_on = (analysis or {}).get("based_on_transcript_generated_at")
        current = transcript.get("generated_at")
        stale = bool(based_on and current and based_on != current)

        return {
            "clips": project.get("clips") or [],
            "analysis": analysis,
            "stale": stale,
            "has_transcript": bool(project.get("transcript")),
            "source_duration": (project.get("source_metadata") or {}).get("duration"),
        }

    def replace_clips(self, project_id: str, clips: list[dict]) -> dict:
        """Save edited clips without touching the analysis that produced them."""

        project = self.load(project_id)
        project["clips"] = clips
        return self.save(project)

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
                    "has_transcript": bool(project.get("transcript")),
                }
            )

        summaries.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return summaries
