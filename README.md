# AI Video Production Studio

Local-first, browser-based video editing assistant for turning long-form video
into editable short-form clips. The browser is the interface and the Python API
runs locally on the user's machine.

## Milestone status

Milestone 0 establishes the repository and safe local foundation:

- FastAPI backend with a working `GET /api/health` endpoint.
- Minimal static frontend served by the backend.
- Project-scoped workspace directories for source media, transcripts, assets,
  previews, temporary files, and exports.
- Upload metadata validation for supported video types, size limits, and safe
  generated filenames.
- Temporary-file cleanup scoped to an individual project.

Video ingest, transcription, clip suggestions, captions, and rendering are
planned for later milestones and are not represented as working controls yet.

## Windows setup

Install Python 3.11 or newer, then run these commands from the repository root
in PowerShell:

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

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000/> in a browser. The health endpoint is available at
<http://127.0.0.1:8000/api/health>, and FastAPI's local API documentation is at
<http://127.0.0.1:8000/docs>.

Stop the server with `Ctrl+C`.

## Run tests

```powershell
python -m pytest
```

## System tools for later milestones

Later video inspection and rendering milestones require FFmpeg and FFprobe to
be installed separately and available on `PATH`. They are intentionally not a
dependency of this foundation milestone.

## Local data and secrets

Source media, generated previews, exports, temporary files, model caches, logs,
and environment files must remain outside Git. Do not commit API keys or media
files. The project defaults to local processing and will only use an external
API when a later feature explicitly adds an opt-in integration.
