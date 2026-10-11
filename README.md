# AI Video Production Studio

Local-first, browser-based video editing assistant for turning long-form video
into editable short-form clips. The browser is the interface and the Python API
runs locally on the user's machine.

## Milestone status

Milestone 0 established the repository and safe local foundation:

- FastAPI backend with a working `GET /api/health` endpoint.
- Minimal static frontend served by the backend.
- Project-scoped workspace directories for source media, transcripts, assets,
  previews, temporary files, and exports.
- Upload metadata validation for supported video types, size limits, and safe
  generated filenames.
- Temporary-file cleanup scoped to an individual project.

Milestone 1 adds the frontend shell:

- Dark, desktop-first three-zone workspace (projects/assets sidebar, preview and
  clip candidates, settings inspector) with a bottom timeline.
- Project header, backend status, and a source-metadata panel.
- Video import drop zone and file picker.
- Processing panel with steps, progress, and plain-language error messages.
- Clip-candidate list, caption controls, and export settings panels.
- The shell loads with a clearly labelled sample project, so it works without
  AI models or API keys.

Milestone 2 makes video import real:

- `POST /api/projects` accepts one uploaded video and returns a project id.
- Uploads are **streamed to disk in bounded chunks**, so a large video is never
  held in memory; the size limit is enforced while writing.
- Metadata (duration, resolution, frame rate, size) is read locally with
  **FFprobe** and a copy is saved alongside the source video.
- Each project is a folder in the workspace holding `project.json` plus
  `source/`, `transcripts/`, `assets/`, `temp/`, and `exports/`.
- Failed ingests are cleaned up and never leave a half-written project behind.
- All failures return one JSON shape: `{"error": {"code": "...", "message": "..."}}`.
- The frontend import button, drop zone, project list, metadata panel, and
  preview are wired to the live API.

Transcription, clip suggestions, captions, and rendering are planned for later
milestones. Their controls are present for layout but **disabled or clearly
marked as a demo** — no button pretends to transcribe video or export a file.

## Working directory

Every command in this README must be run from the project folder
`AI-Video-Editor-Production-Studio` — the folder that contains `app/`,
`frontend/`, `tests/`, and `requirements.txt`. It is **not** the folder above it.

```powershell
cd "$HOME\Documents\Ai video production studio\AI-Video-Editor-Production-Studio"
```

Running the server from the parent folder fails with
`ModuleNotFoundError: No module named 'app'` because Python looks for the
`app` package in the current directory.

## Windows setup

Install Python 3.11 or newer, then run these commands from the project folder
shown above in PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

For development checks, install the test dependency too:

```powershell
python -m pip install -r requirements-dev.txt
```

The application uses `workspace/` inside the repository by default. To point it
at another local directory, set `AI_VIDEO_WORKSPACE` before starting the server.
See `.env.example` for the supported foundation settings. Keep `.env` local;
it is ignored by Git.

## Run the local app

From the project folder (`ls app` should list `main.py`):

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000/> in a browser. The health endpoint is available at
<http://127.0.0.1:8000/api/health>, and FastAPI's local API documentation is at
<http://127.0.0.1:8000/docs>.

Stop the server with `Ctrl+C`.

| Error | Cause and fix |
|---|---|
| `ModuleNotFoundError: No module named 'app'` | You are in the wrong folder. `cd` into the project folder and retry. |
| `ModuleNotFoundError: No module named 'uvicorn'` | Dependencies are not installed for the Python you are running. Activate `.venv`, or `python -m pip install -r requirements.txt`. |
| `[Errno 10048] address already in use` | A server is already on port 8000. Stop it, or start this one with `--port 8001`. |

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Liveness and workspace status. |
| `POST` | `/api/projects` | Upload one video and create a project (multipart `file`, optional `name`). |
| `GET` | `/api/projects` | List saved projects for the sidebar. |
| `GET` | `/api/projects/{project_id}` | Read the saved JSON project document. |
| `GET` | `/api/projects/{project_id}/source` | Serve the source video for browser preview. |

Upload limits and supported formats come from `.env`/`.env.example`
(`MAX_UPLOAD_SIZE_MB`). Accepted containers are MP4, MOV, M4V, AVI, MKV, and
WebM; the original filename is never used as the stored filename.

## Run tests

```powershell
python -m pytest
```

The suite also asserts the frontend shell exposes the expected layout and keeps
sample data clearly labelled. The interface can be opened without any API key,
AI model, or uploaded media.

## System tools

**FFprobe and FFmpeg must be installed and available on `PATH`.** Video import
reads metadata with FFprobe, so importing reports a clear error if the tools are
missing. FFmpeg itself is used from the rendering milestone onward; both are
system tools, not Python packages.

Check the installation with:

```powershell
ffprobe -version
ffmpeg -version
```

## Local data and secrets

Source media, generated previews, exports, temporary files, model caches, logs,
and environment files must remain outside Git. Do not commit API keys or media
files. The project defaults to local processing and will only use an external
API when a later feature explicitly adds an opt-in integration.
