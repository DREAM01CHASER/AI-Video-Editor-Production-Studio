"""FastAPI entry point for the Milestone 0 application foundation."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .storage import Workspace


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or Settings.from_environment()
    workspace = Workspace(runtime_settings.workspace_dir).ensure_layout()
    frontend_dir = runtime_settings.project_root / "frontend"

    application = FastAPI(
        title="AI Video Production Studio",
        description="Local-first video editing assistant API",
        version="0.1.0",
    )
    application.state.settings = runtime_settings
    application.state.workspace = workspace
    application.mount(
        "/static", StaticFiles(directory=str(frontend_dir)), name="static"
    )

    @application.get("/api/health", tags=["system"])
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "service": "ai-video-production-studio",
            "workspace": "ready",
        }

    @application.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(frontend_dir / "index.html")

    return application


app = create_app()
