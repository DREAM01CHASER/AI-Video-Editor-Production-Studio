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

Milestone 3 makes transcription real:

- `POST /api/projects/{project_id}/transcribe` starts a background job and
  `GET /api/jobs/{job_id}` reports **real progress**, measured from decoded
  segments rather than animated.
- Speech is transcribed **locally** with `faster-whisper`; the source video is
  decoded directly, so no intermediate audio file is written.
- Word-level timings are saved per segment when the model provides them, and
  segment timings are used when it does not — timings are never invented.
- The transcript is saved to `transcripts/transcript.json` and referenced from
  `project.json`, so reopening a project restores it without re-running the model.
- GPU is used when available, with an automatic CPU/int8 fallback recorded in the
  transcript, and a cancel/retry control in the UI.

Clip suggestions, captions, and rendering are planned for later milestones. Their
controls are present for layout but **disabled or clearly marked as a demo** — no
button pretends to suggest clips, caption video, or export a file.

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
| `POST` | `/api/projects/{project_id}/transcribe` | Start local transcription; returns `202` and the job. |
| `GET` | `/api/jobs/{job_id}` | Poll a job's status, progress, and result. |
| `POST` | `/api/jobs/{job_id}/cancel` | Ask a running job to stop between segments. |
| `GET` | `/api/projects/{project_id}/transcript` | Read the saved transcript (`404` until it exists). |

Upload limits and supported formats come from `.env`/`.env.example`
(`MAX_UPLOAD_SIZE_MB`). Accepted containers are MP4, MOV, M4V, AVI, MKV, and
WebM; the original filename is never used as the stored filename.

## Run tests

```powershell
python -m pytest
```

The suite asserts the frontend shell exposes the expected layout and keeps sample
data clearly labelled, and it covers the transcription helpers, the job registry,
and transcript persistence without needing a model at all. The end-to-end
transcription test synthesises real speech, runs the **real** Whisper model, and
checks that the transcript is readable with usable timestamps; it is skipped with
an explicit reason when FFmpeg, Windows speech synthesis, or the cached model is
unavailable, so it never silently passes.

The interface can be opened without any API key or uploaded media, but
transcription needs `faster-whisper` and a model.

## Transcription settings

Transcription defaults to the `medium` model and can be tuned through `.env`
(see `.env.example` for every option):

| Setting | Default | Meaning |
|---|---|---|
| `WHISPER_MODEL` | `medium` | Model name, or a path to a converted CTranslate2 model directory. |
| `WHISPER_DEVICE` | `auto` | `auto`, `cpu`, or `cuda`. `auto` prefers a GPU when one is visible. |
| `WHISPER_COMPUTE_TYPE` | `auto` | `auto` means `float16` on GPU and `int8` on CPU. |
| `WHISPER_LANGUAGE` | *(detect)* | Leave empty to detect the spoken language. |
| `WHISPER_ALLOW_DOWNLOAD` | `true` | Allow downloading weights on first use. |

**The first transcription downloads model weights**, which takes a while and
needs an internet connection; after that the model is reused from the normal
Hugging Face cache. Set `WHISPER_ALLOW_DOWNLOAD=false` to stay offline —
transcription then reports a clear error unless the model is already cached.
A CUDA device that is visible but missing its cuDNN/cuBLAS runtime falls back to
CPU automatically, and the fallback is recorded in the transcript.

## System tools

**FFprobe and FFmpeg must be installed and available on `PATH`.** Video import
reads metadata with FFprobe, so importing reports a clear error if the tools are
missing. Transcription itself does not need FFmpeg because `faster-whisper`
decodes audio through PyAV; FFmpeg is required from the rendering milestone
onward. Both are system tools, not Python packages.

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
