"""HTTP routes for project creation, local video ingest, and transcription.

Uploads are streamed to a project-scoped temp file in bounded chunks, probed with
FFprobe, then moved into the project's ``source/`` directory, so a large video is
never held in memory.

Transcription is asynchronous: ``POST /api/projects/{id}/transcribe`` starts a
background job and returns it, and the client polls ``GET /api/jobs/{job_id}``
for real progress. ``GET /api/projects/{id}/transcript`` returns the saved
transcript so the UI can restore it without re-running the model.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse

from .errors import ApiError
from .jobs import JobRegistry
from .media import probe_video
from .projects import ProjectStore
from .storage import CONTENT_TYPES_BY_EXTENSION, validate_upload_metadata
from .transcription import TranscriptionService

router = APIRouter(prefix="/api", tags=["projects"])

UPLOAD_CHUNK_SIZE = 1024 * 1024
TRANSCRIBE_JOB_KIND = "transcribe"


def _store(request: Request) -> ProjectStore:
    return request.app.state.projects


def _jobs(request: Request) -> JobRegistry:
    return request.app.state.jobs


def _transcriber(request: Request) -> TranscriptionService:
    return request.app.state.transcriber


def _media_type(path: Path) -> str:
    content_types = CONTENT_TYPES_BY_EXTENSION.get(path.suffix.lower())
    if not content_types:
        return "application/octet-stream"
    return sorted(content_types)[0]


async def _stream_to_disk(upload: UploadFile, target: Path, max_bytes: int) -> int:
    """Write an upload to disk in chunks, rejecting anything over the limit."""

    written = 0
    target.parent.mkdir(parents=True, exist_ok=True)

    with target.open("wb") as handle:
        while True:
            chunk = await upload.read(UPLOAD_CHUNK_SIZE)
            if not chunk:
                break
            written += len(chunk)
            if written > max_bytes:
                raise ApiError(
                    "file_too_large",
                    f"The upload exceeds the {max_bytes // (1024 * 1024)} MB limit.",
                    status_code=413,
                )
            handle.write(chunk)

    if written == 0:
        raise ApiError("empty_upload", "The uploaded file is empty.", status_code=400)

    return written


@router.get("/projects")
def list_projects(request: Request) -> dict[str, object]:
    """List saved projects for the workspace sidebar."""

    return {"projects": _store(request).list_summaries()}


@router.post("/projects", status_code=201)
async def create_project(
    request: Request,
    file: UploadFile = File(...),
    name: str | None = Form(default=None),
) -> dict[str, object]:
    """Create a project from one uploaded video and return its metadata."""

    settings = request.app.state.settings
    store = _store(request)

    filename = file.filename or ""
    # Validate name and declared type before writing anything to disk.
    validate_upload_metadata(filename, file.content_type)

    project_workspace = store.new_project_workspace()
    part_path = project_workspace.temp / "upload.part"

    try:
        written = await _stream_to_disk(file, part_path, settings.max_upload_size_bytes)
        metadata = probe_video(part_path)

        # Reuse the shared name/size policy for the final destination path.
        destination = project_workspace.allocate_source_path(
            filename=filename,
            content_type=file.content_type,
            size_bytes=written,
            max_size_bytes=settings.max_upload_size_bytes,
        )
        part_path.replace(destination)

        resolved_name = (name or Path(filename).stem or "Untitled project").strip()[:120]
        project = store.write_new(
            project_workspace.root.name,
            name=resolved_name,
            source_video=f"source/{destination.name}",
            source_metadata=metadata.as_dict(),
        )
    except BaseException:
        # A failed ingest must not leave a half-written project behind.
        shutil.rmtree(project_workspace.root, ignore_errors=True)
        raise
    finally:
        await file.close()

    return project


@router.get("/projects/{project_id}")
def get_project(project_id: str, request: Request) -> dict[str, object]:
    """Return the saved JSON project document."""

    return _store(request).load(project_id)


@router.get("/projects/{project_id}/source")
def get_project_source(project_id: str, request: Request) -> FileResponse:
    """Serve a project's source video so the browser can preview it."""

    store = _store(request)
    project = store.load(project_id)
    path = store.source_path(project)
    return FileResponse(path, media_type=_media_type(path))


@router.get("/projects/{project_id}/transcript")
def get_project_transcript(project_id: str, request: Request) -> dict[str, object]:
    """Return the saved transcript for a project.

    Raises a structured 404 when the project has not been transcribed yet, so the
    UI can tell "not transcribed" apart from "transcription failed".
    """

    return _store(request).load_transcript(project_id)


@router.post("/projects/{project_id}/transcribe", status_code=202)
def start_transcription(project_id: str, request: Request) -> dict[str, object]:
    """Start local transcription and return the job to poll.

    The work runs on a background thread because transcription takes far longer
    than an HTTP request, and the project may only have one transcription in
    flight at a time.
    """

    # Capture everything the worker needs now: the Request is not valid once the
    # response has been sent.
    store = _store(request)
    registry = _jobs(request)
    service = _transcriber(request)

    project = store.load(project_id)
    source_path = store.source_path(project)

    existing = registry.running_for(TRANSCRIBE_JOB_KIND, project_id)
    if existing is not None:
        raise ApiError(
            "transcription_in_progress",
            f"A transcription is already running for this project (job "
            f"{existing.id[:8]}). Wait for it to finish or cancel it first.",
            409,
        )

    source_video = str(project.get("source_video") or "")
    metadata = project.get("source_metadata") or {}
    duration_hint = float(metadata.get("duration") or 0.0)

    job = registry.create(TRANSCRIBE_JOB_KIND, subject=project_id)

    def work() -> dict[str, object]:
        document, _loaded = service.transcribe(
            source_path=source_path,
            project_id=project_id,
            source_video=source_video,
            duration_hint=duration_hint,
            progress=lambda value, message: registry.update(
                job, progress=value, message=message
            ),
            should_cancel=lambda: registry.is_cancel_requested(job.id),
        )
        registry.update(job, progress=99.0, message="Saving transcript…")
        saved = store.save_transcript(project_id, document)
        return {
            "project_id": project_id,
            "transcript": saved.get("transcript"),
            "segment_count": document.get("segment_count"),
            "language": document.get("language"),
            "model": document.get("model"),
            "device": document.get("device"),
            "has_word_timestamps": document.get("has_word_timestamps"),
        }

    registry.start(job, work)
    return job.as_dict()


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> dict[str, object]:
    """Return the current state of a background job."""

    return _jobs(request).get(job_id).as_dict()


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request) -> dict[str, object]:
    """Ask a running job to stop.

    The work checks the flag as it makes progress, so cancellation happens
    between decoded segments rather than killing the thread mid-write.
    """

    return _jobs(request).request_cancel(job_id).as_dict()
