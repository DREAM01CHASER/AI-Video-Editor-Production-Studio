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

import re
import shutil
from pathlib import Path

from fastapi import APIRouter, Body, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse

from .captions import (
    build_cues,
    caption_summary,
    default_style,
    normalise_style,
    resolve_geometry,
    to_ass,
    to_srt,
)
from .clips import GENERATOR_HEURISTIC, suggest_clips
from .errors import ApiError
from .llm import LlmSettings, make_ranker
from .jobs import JobRegistry
from .media import probe_video
from .projects import ProjectStore
from .render import (
    build_ffmpeg_command,
    ffmpeg_available,
    normalise_export,
    run_ffmpeg_render,
)
from .storage import CONTENT_TYPES_BY_EXTENSION, validate_upload_metadata
from .transcription import TranscriptionService

router = APIRouter(prefix="/api", tags=["projects"])

UPLOAD_CHUNK_SIZE = 1024 * 1024
TRANSCRIBE_JOB_KIND = "transcribe"
RENDER_JOB_KIND = "render"

#: Exported subtitles are plain text; the browser downloads rather than plays them.
SUBTITLE_MEDIA_TYPE = "application/x-subrip"
_MEDIA_TYPES_BY_EXPORT_SUFFIX = {".mp4": "video/mp4", ".srt": SUBTITLE_MEDIA_TYPE}


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


def _export_media_type(path: Path) -> str:
    return _MEDIA_TYPES_BY_EXPORT_SUFFIX.get(path.suffix.lower(), "application/octet-stream")


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


# ------------------------------------------------------------------- clips

#: Slack allowed for a clip end, so rounding cannot push a clip past the source.
CLIP_EDIT_TOLERANCE = 0.5


def _source_duration(project: dict) -> float:
    metadata = project.get("source_metadata") or {}
    try:
        return float(metadata.get("duration") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _clip_title(value: object, fallback: str) -> str:
    text = " ".join(str(value or "").split())
    return text[:120] or fallback


@router.get("/projects/{project_id}/clips")
def get_project_clips(project_id: str, request: Request) -> dict[str, object]:
    """Return saved candidate clips, empty before any are suggested."""

    return _store(request).load_clips(project_id)


@router.post("/projects/{project_id}/suggest-clips")
def suggest_project_clips(project_id: str, request: Request) -> dict[str, object]:
    """Detect candidate clips from the saved transcript and store them.

    This runs synchronously on purpose: the heuristic is a pure computation over
    a few hundred segments, so wrapping it in a background job would only add a
    polling loop without any benefit.
    """

    store = _store(request)
    project = store.load(project_id)

    if not project.get("transcript"):
        raise ApiError(
            "transcript_required",
            "Transcribe this project before suggesting clips.",
            409,
        )

    transcript = store.load_transcript(project_id)

    # The optional LLM pass is built only when a key is configured, and it can
    # only re-label candidates: the clip times always come from the transcript.
    runtime = request.app.state.settings
    ranker = None
    if runtime.llm_api_key:
        ranker = make_ranker(
            LlmSettings(
                api_key=runtime.llm_api_key,
                base_url=runtime.llm_base_url,
                model=runtime.llm_model,
            )
        )

    result = suggest_clips(transcript, ranker=ranker)
    analysis = result["analysis"]

    if ranker is not None:
        if analysis["generator"] == GENERATOR_HEURISTIC:
            analysis["notes"].append(
                "An LLM key is configured but the optional pass was unavailable, so "
                "the transparent heuristic result is shown."
            )
        else:
            analysis["llm_model"] = runtime.llm_model

    saved = store.save_clips(project_id, result["clips"], analysis)

    return {
        "clips": saved.get("clips") or [],
        "analysis": saved.get("clip_analysis"),
    }


@router.patch("/projects/{project_id}/clips")
def update_project_clips(
    request: Request, project_id: str, payload: dict = Body(...)
) -> dict[str, object]:
    """Save edits to candidate clip in/out points, titles, and scores.

    Every timestamp is validated against the project's own clips and the source
    duration, so a bad or hostile edit cannot be stored. Only the clips named in
    the request change; the rest are left alone.
    """

    store = _store(request)
    project = store.load(project_id)
    existing = project.get("clips") or []

    if not existing:
        raise ApiError(
            "no_clips", "This project has no candidate clips to edit yet.", 404
        )

    updates = payload.get("clips")
    if not isinstance(updates, list):
        raise ApiError(
            "invalid_clip_edit", "Send a JSON object containing a 'clips' list.", 422
        )

    duration = _source_duration(project) or max(
        float(clip.get("source_end") or 0.0) for clip in existing
    )
    by_id = {str(clip.get("id")): clip for clip in existing}

    for item in updates:
        if not isinstance(item, dict):
            raise ApiError(
                "invalid_clip_edit", "Each clip edit must be a JSON object.", 422
            )

        clip_id = str(item.get("id") or "")
        clip = by_id.get(clip_id)
        if clip is None:
            raise ApiError(
                "invalid_clip_edit",
                f"This project has no candidate clip with id '{clip_id}'.",
                422,
            )

        try:
            start = float(item.get("source_start"))
            end = float(item.get("source_end"))
        except (TypeError, ValueError) as exc:
            raise ApiError(
                "invalid_clip_edit",
                f"Clip {clip_id} needs numeric source_start and source_end values.",
                422,
            ) from exc

        if start < 0:
            raise ApiError(
                "invalid_clip_edit",
                f"Clip {clip_id} cannot start before 0 seconds.",
                422,
            )
        if end <= start:
            raise ApiError(
                "invalid_clip_edit",
                f"Clip {clip_id} ends at {end:.2f}s, which is not after its start at "
                f"{start:.2f}s.",
                422,
            )
        if duration and end > duration + CLIP_EDIT_TOLERANCE:
            raise ApiError(
                "invalid_clip_edit",
                f"Clip {clip_id} ends at {end:.2f}s, past the end of the "
                f"{duration:.2f}s source.",
                422,
            )

        clip["source_start"] = round(start, 3)
        clip["source_end"] = round(end, 3)
        clip["duration"] = round(end - start, 3)
        # Mark the clip as human-adjusted so the UI can show that it no longer
        # reflects the original suggestion.
        clip["edited"] = True

        if "title" in item:
            clip["title"] = _clip_title(item.get("title"), str(clip.get("title") or clip_id))

        if "score" in item:
            try:
                score = float(item.get("score"))
            except (TypeError, ValueError) as exc:
                raise ApiError(
                    "invalid_clip_edit",
                    f"Clip {clip_id} needs a numeric score between 0 and 1.",
                    422,
                ) from exc
            clip["score"] = round(min(1.0, max(0.0, score)), 3)

    saved = store.replace_clips(project_id, existing)
    return {"clips": saved.get("clips") or []}


# ------------------------------------------------------- captions and export


#: Characters kept when a clip id becomes part of a filename.
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9_-]+")


def _safe_name(value: object, fallback: str) -> str:
    cleaned = _UNSAFE_FILENAME.sub("-", str(value or "")).strip("-")
    return cleaned[:48] or fallback


def _allocate_export_path(directory: Path, stem: str, suffix: str) -> Path:
    """Pick a free filename so an earlier export is never overwritten."""

    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / f"{stem}{suffix}"
    counter = 2
    while candidate.exists():
        candidate = directory / f"{stem}-{counter}{suffix}"
        counter += 1
    return candidate


def _transcript_segments(store: ProjectStore, project: dict) -> list[dict]:
    """Read saved transcript segments, or an empty list when there are none."""

    if not project.get("transcript"):
        return []

    try:
        document = store.load_transcript(str(project.get("project_id")))
    except ApiError:
        # An unreadable transcript must not stop a video-only export.
        return []

    segments = document.get("segments")
    return [segment for segment in segments or [] if isinstance(segment, dict)]


def _clip_by_id(project: dict, clip_id: str | None) -> dict:
    """Find one candidate clip, saying clearly when it is missing."""

    clips = [clip for clip in (project.get("clips") or []) if isinstance(clip, dict)]
    if not clips:
        raise ApiError(
            "no_clips", "This project has no candidate clips to export yet.", 409
        )

    target = str(clip_id or "").strip()
    if not target:
        raise ApiError(
            "invalid_clip_id", "Send a 'clip_id' naming the clip to export.", 422
        )

    for clip in clips:
        if str(clip.get("id")) == target:
            return clip

    raise ApiError(
        "clip_not_found",
        f"This project has no candidate clip with id '{target}'.",
        404,
    )


def _public_cues(cues: list[dict]) -> list[dict]:
    """The cue fields the interface needs, without the internal word details."""

    return [
        {
            "index": cue["index"],
            "start": cue["start"],
            "end": cue["end"],
            "text": cue["text"],
            "lines": cue["lines"],
        }
        for cue in cues
    ]


def _caption_plan(
    segments: list[dict], clip: dict, style: dict, export: dict
) -> dict[str, object]:
    """Build clip-relative cues and the geometry that will draw them."""

    geometry = resolve_geometry(style, width=int(export["width"]), height=int(export["height"]))
    cues = build_cues(
        segments,
        clip_start=float(clip.get("source_start") or 0.0),
        clip_end=float(clip.get("source_end") or 0.0),
        geometry=geometry,
    )
    return {"geometry": geometry, "cues": cues, "summary": caption_summary(cues)}


@router.get("/projects/{project_id}/captions")
def get_project_captions(
    project_id: str, request: Request, clip_id: str = Query(...)
) -> dict[str, object]:
    """Preview the captions for one clip, before spending time on a render.

    The cues are the same ones the render will burn in, so what the user reads
    here is exactly what appears in the exported file.
    """

    store = _store(request)
    project = store.load(project_id)
    style = normalise_style(project.get("caption_style"))
    export = normalise_export(
        project.get("export"),
        source_width=int((project.get("source_metadata") or {}).get("width") or 0),
        source_height=int((project.get("source_metadata") or {}).get("height") or 0),
    )

    clip = _clip_by_id(project, clip_id)
    plan = _caption_plan(_transcript_segments(store, project), clip, style, export)

    return {
        "clip_id": str(clip.get("id")),
        "clip_title": clip.get("title"),
        "style": style,
        "geometry": plan["geometry"].as_dict(),
        "cues": _public_cues(plan["cues"]),
        "srt": to_srt(plan["cues"]),
        "summary": plan["summary"],
        "based_on_transcript_generated_at": (project.get("transcript") or {}).get(
            "generated_at"
        ),
    }


@router.patch("/projects/{project_id}/settings")
def update_project_settings(
    request: Request, project_id: str, payload: dict | None = Body(default=None)
) -> dict[str, object]:
    """Save caption and export settings for the next render.

    Both halves are validated before anything is written, so the project can
    never hold a setting the renderer would refuse.
    """

    store = _store(request)
    project = store.load(project_id)
    body = payload or {}
    metadata = project.get("source_metadata") or {}

    style: dict | None = None
    if "caption_style" in body:
        incoming = body.get("caption_style")
        if incoming is not None and not isinstance(incoming, dict):
            raise ApiError(
                "invalid_caption_style", "caption_style must be a JSON object.", 422
            )
        current = project.get("caption_style") or default_style()
        style = normalise_style({**current, **(incoming or {})})

    incoming_export = body.get("export")
    if incoming_export is not None and not isinstance(incoming_export, dict):
        raise ApiError("invalid_export_settings", "export must be a JSON object.", 422)

    plan = normalise_export(
        {**(project.get("export") or {}), **(incoming_export or {})},
        source_width=int(metadata.get("width") or 0),
        source_height=int(metadata.get("height") or 0),
    )
    export_document = {
        key: plan[key]
        for key in ("aspect_ratio", "resolution", "format", "caption_mode", "write_srt")
    }

    saved = store.save_settings(
        project_id, caption_style=style, export=export_document
    )

    return {
        "caption_style": saved.get("caption_style"),
        "export": saved.get("export"),
        "plan": plan,
    }


@router.get("/projects/{project_id}/exports")
def list_project_exports(project_id: str, request: Request) -> dict[str, object]:
    """List finished renders, newest first, noting any file that is gone."""

    store = _store(request)
    payload = store.load_exports(project_id)
    exports_dir = store.project_workspace(project_id).exports

    exports: list[dict] = []
    for entry in payload["exports"]:
        name = str(entry.get("file") or "")
        # A render whose file was deleted by hand must not be offered as a link.
        exports.append({**entry, "available": bool(name) and (exports_dir / name).is_file()})

    latest = exports[0] if exports else None
    return {
        "exports": exports,
        "latest": latest,
        "caption_style": payload["caption_style"],
        "export_settings": payload["export_settings"],
    }


@router.get("/projects/{project_id}/exports/{filename}")
def get_project_export(
    project_id: str, filename: str, request: Request
) -> FileResponse:
    """Serve a rendered MP4 or its subtitle sidecar to the browser."""

    path = _store(request).resolve_export_path(project_id, filename)
    return FileResponse(
        path,
        media_type=_export_media_type(path),
        filename=path.name,
    )


@router.post("/projects/{project_id}/render", status_code=202)
def start_render(
    request: Request, project_id: str, payload: dict | None = Body(default=None)
) -> dict[str, object]:
    """Render one clip to a captioned vertical MP4 and return the job to poll.

    The rendered file is written into the project's own ``exports/`` folder, so a
    cancelled or failed render can be cleaned up without touching anything else.
    """

    store = _store(request)
    registry = _jobs(request)
    body = payload or {}

    project = store.load(project_id)
    metadata = project.get("source_metadata") or {}
    clip = _clip_by_id(project, body.get("clip_id"))

    style = normalise_style({**(project.get("caption_style") or default_style()), **(body.get("caption_style") or {})})
    plan = normalise_export(
        {**(project.get("export") or {}), **(body.get("export") or {})},
        source_width=int(metadata.get("width") or 0),
        source_height=int(metadata.get("height") or 0),
    )

    if not ffmpeg_available():
        raise ApiError(
            "ffmpeg_unavailable",
            "FFmpeg was not found on PATH, so clips cannot be rendered. Install "
            "FFmpeg and restart the app.",
            503,
        )

    existing = registry.running_for(RENDER_JOB_KIND, project_id)
    if existing is not None:
        raise ApiError(
            "render_in_progress",
            f"An export is already running for this project (job "
            f"{existing.id[:8]}). Wait for it to finish or cancel it first.",
            409,
        )

    # Keeping the validated values means the saved project always describes the
    # render that actually ran, and the panel shows the same settings after reload.
    store.save_settings(
        project_id,
        caption_style=style,
        export={
            key: plan[key]
            for key in ("aspect_ratio", "resolution", "format", "caption_mode", "write_srt")
        },
    )

    source_path = store.source_path(project)
    workspace = store.project_workspace(project_id)
    segments = _transcript_segments(store, project)

    clip_id = str(clip.get("id"))
    clip_start = float(clip.get("source_start") or 0.0)
    clip_end = float(clip.get("source_end") or 0.0)
    caption_plan = _caption_plan(segments, clip, style, plan)
    cues = caption_plan["cues"]
    geometry = caption_plan["geometry"]

    # Captions are burned in with ASS; a relative filename lets FFmpeg run from
    # the temp folder, which avoids escaping Windows drive-letter paths.
    burning = plan["caption_mode"] == "burn" and bool(cues)
    subtitle_name = f"captions-{_safe_name(clip_id, 'clip')}.ass"
    subtitle_path = workspace.temp / subtitle_name

    output_path = _allocate_export_path(
        workspace.exports,
        stem=f"{_safe_name(clip_id, 'clip')}-{plan['resolution']}",
        suffix=f".{plan['format']}",
    )
    subtitle_sidecar = output_path.with_suffix(".srt")

    notes = list(plan["notes"])
    if plan["caption_mode"] == "burn" and not cues:
        notes.append(str(caption_plan["summary"]["note"]))

    job = registry.create(RENDER_JOB_KIND, subject=project_id)

    def work() -> dict[str, object]:
        registry.update(job, progress=1.0, message="Preparing captions…")

        if burning:
            subtitle_path.write_text(to_ass(cues, style, geometry), encoding="utf-8")

        command = build_ffmpeg_command(
            source_path=source_path,
            output_path=output_path,
            start=clip_start,
            end=clip_end,
            width=int(plan["width"]),
            height=int(plan["height"]),
            subtitle_name=subtitle_name if burning else None,
        )

        try:
            outcome = run_ffmpeg_render(
                command=command,
                clip_seconds=max(0.001, clip_end - clip_start),
                work_dir=workspace.temp,
                progress=lambda value, message: registry.update(
                    job, progress=value, message=message
                ),
                should_cancel=lambda: registry.is_cancel_requested(job.id),
            )
        except BaseException:
            # Neither a failed nor a cancelled render may leave a broken MP4
            # behind; the .ass file is kept so a failure can be inspected.
            output_path.unlink(missing_ok=True)
            raise

        if burning:
            subtitle_path.unlink(missing_ok=True)

        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise ApiError(
                "render_failed",
                "FFmpeg reported success but wrote no video file. Check that the "
                "source video is readable.",
                500,
            )

        if outcome["font_warnings"]:
            notes.append(
                "Some characters had no matching font on this machine, so they may "
                "be missing in the export. FFmpeg logged: "
                f"{outcome['font_warnings'][0]}"
            )

        srt_name = None
        if plan["write_srt"]:
            subtitle_sidecar.write_text(to_srt(cues), encoding="utf-8")
            srt_name = subtitle_sidecar.name

        registry.update(job, progress=99.0, message="Saving the export…")

        entry = {
            "file": output_path.name,
            "url": f"/api/projects/{project_id}/exports/{output_path.name}",
            "srt_file": srt_name,
            "srt_url": (
                f"/api/projects/{project_id}/exports/{srt_name}" if srt_name else None
            ),
            "clip_id": clip_id,
            "clip_title": clip.get("title"),
            "duration": round(clip_end - clip_start, 3),
            "size_bytes": output_path.stat().st_size,
            "resolution": plan["resolution"],
            "width": plan["width"],
            "height": plan["height"],
            "aspect_ratio": plan["aspect_ratio"],
            "caption_mode": plan["caption_mode"],
            "caption_preset": style["preset"],
            "caption_style": style,
            "cue_count": len(cues),
            "language": (project.get("transcript") or {}).get("language"),
            "upscaled": plan["upscaled"],
            "notes": notes,
            "font_warnings": outcome["font_warnings"],
            "render_seconds": outcome["elapsed_seconds"],
        }
        return store.record_export(project_id, entry).get("exports", [entry])[0]

    registry.start(job, work)
    return job.as_dict()
