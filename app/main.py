"""FastAPI entry point for the local AI Video Production Studio."""

from __future__ import annotations

import importlib.util
import logging
import sys

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import router as api_router
from .config import Settings
from .errors import register_error_handlers
from .jobs import JobRegistry
from .projects import ProjectStore
from .storage import Workspace
from .transcription import TranscriptionService

logger = logging.getLogger("app.main")


def _configure_app_logging() -> None:
    """Make the app's own log lines visible in the console.

    Uvicorn configures only its own loggers and leaves the root logger bare, so
    without this an INFO line — the chosen transcription device, the interpreter
    in use — never leaves the process, even though it is exactly what a user
    needs when transcription unexpectedly runs on the CPU. Only the ``app``
    logger tree is touched; uvicorn keeps its own format.
    """

    app_logger = logging.getLogger("app")
    if app_logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s:     %(message)s"))
    app_logger.addHandler(handler)
    app_logger.setLevel(logging.INFO)
    # Do not also print through the root logger, where a bare tree would only
    # echo warnings and drop info.
    app_logger.propagate = False


_configure_app_logging()


def transcription_available() -> bool:
    """Report whether faster-whisper can be imported, without loading a model."""

    return importlib.util.find_spec("faster_whisper") is not None


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or Settings.from_environment()
    workspace = Workspace(runtime_settings.workspace_dir).ensure_layout()
    frontend_dir = runtime_settings.project_root / "frontend"

    application = FastAPI(
        title="AI Video Production Studio",
        description="Local-first video editing assistant API",
        version="0.5.0",
    )
    application.state.settings = runtime_settings
    application.state.workspace = workspace
    application.state.projects = ProjectStore(workspace)
    application.state.jobs = JobRegistry()
    application.state.transcriber = TranscriptionService(runtime_settings)

    plan = application.state.transcriber.compute_plan()
    logger.info(
        "studio ready: python=%s | workspace=%s | transcription=%s/%s (%s)",
        sys.executable,
        workspace.root,
        plan["device"],
        plan["compute_type"],
        plan["summary"],
    )

    register_error_handlers(application)
    application.include_router(api_router)

    application.mount(
        "/static", StaticFiles(directory=str(frontend_dir)), name="static"
    )

    @application.get("/api/health", tags=["system"])
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "service": "ai-video-production-studio",
            "workspace": "ready",
            "transcription": {
                "available": transcription_available(),
                "model": runtime_settings.whisper_model,
                "device": runtime_settings.whisper_device,
                # Which device transcription can really use, and the reason when
                # that is not the GPU the machine appears to have. Reported before
                # a job runs so the choice is never a silent one.
                "compute": application.state.transcriber.compute_plan(),
            },
        }

    @application.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(frontend_dir / "index.html")

    return application


app = create_app()
