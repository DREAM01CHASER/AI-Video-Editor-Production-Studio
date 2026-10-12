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

Milestone 4 makes clip suggestion real:

- `POST /api/projects/{project_id}/suggest-clips` proposes 3–5 candidate clips
  from the saved transcript, and `PATCH .../clips` saves your in/out edits.
- Detection uses a **transparent heuristic** over transcript segments, so it needs
  no API key, no download, and no network. Each candidate reports the signals
  behind its score: opening hook markers, whether the cut points land on sentence
  boundaries, words per second, and length.
- Every score is labelled an **estimate of how self-contained and information-dense
  a moment is**. It is never presented as a prediction of virality, and no copy
  claims a clip will perform well.
- Candidates that duplicate an already-chosen moment are dropped and the count is
  reported, while any remaining overlap is flagged on the candidates themselves.
- Whisper does not always punctuate, so when a transcript has little sentence
  punctuation the clip edges are described as estimates that may need a trim —
  rather than being presented as exact sentence boundaries.
- An **optional** LLM pass can rewrite titles, reasons, and scores when a key is
  configured. It is off by default, it can never change clip times, and if it is
  unavailable the heuristic result is used and the analysis says why.

Milestone 5 makes captions and the first real export:

- `GET /api/projects/{project_id}/captions?clip_id=...` builds the caption cues
  for one clip, and `POST /api/projects/{project_id}/render` renders it to MP4 in
  the background, reporting **real FFmpeg progress** and supporting cancel.
- Caption timings are **shifted to clip-relative time** from the saved transcript,
  so the first cue starts at `0.0s` in the exported file. Speech is never
  invented: a segment that only partly overlaps the clip is trimmed to the clip.
- Three presets (**Clean**, **Bold Highlight**, **Minimal**) plus font size,
  placement, line length, highlight mode, and a high-contrast box. The preview in
  the inspector is built from the same cues the render burns in.
- Styled captions are written as **ASS** and burned in by FFmpeg; the optional
  sidecar is a real **SRT**. Subtitle serialisation uses `pysubs2` (the library
  named in `TECH_STACK.md`) and the resulting file is read back by the tests.
- Output defaults to **1080 × 1920 vertical**, with the source aspect preserved by
  scaling to cover and cropping the centre. 1440 × 2560 is offered only as an
  explicit choice and is described as an upscale; nothing is silently enlarged.
- Every render reports its file name, size, caption count, and render time, and
  the exported MP4 plays in the app with a download link. A failed or cancelled
  render deletes its partial file instead of leaving a broken video behind.

The controls are all wired to the live API — no button in the interface is a demo
anymore. The sample project stays clearly labelled as sample data.

Milestone 6 makes the edit real and editable:

- `GET /api/projects/{project_id}/timeline` returns the edit for one clip and
  `PATCH .../timeline` saves it. The **project JSON is the edit** — clip in/out
  points, caption track, render settings, and the item tracks all live in
  `project.json`, never in a hidden FFmpeg command.
- The timeline is editable from the bottom panel: **drag the clip block to move
  it, drag its edges to trim it**, click the ruler to move the playhead, or use
  the in/out fields and the *set in/out at playhead* buttons. Every change is
  saved and immediately visible, and the panel shows a *Saved edit* state.
- **One write path, one copy of every setting.** A timeline edit is validated as
  a whole and then written in a single save, so the inspector, the timeline, and
  the next render can never disagree. An edit that does not fit the source video
  is refused with an explanation and leaves the stored edit untouched.
- The frame rate (**24/25/30/60 fps**) is now part of the edit, so the export
  reshapes the source to the rate the timeline asked for and says so when that
  differs from the source.
- B-roll, sound-effect, and transition tracks are shown with their own lanes, and
  their items are **validated but refused until the renderer can draw them** (see
  Milestones 7 and 9). Both the timeline and the render say which milestone
  brings each one rather than storing an item the export would silently ignore.
- Changes are re-read from the backend after every save, so the interface always
  shows the saved edit rather than the attempted one.

Milestone 7 adds local B-roll and sound effects:

- `POST /api/projects/{project_id}/assets` imports **your own** video or audio
  file into the project's library; `GET .../assets` searches it by kind, name, and
  tag, `GET .../assets/{id}/file` previews it, and `DELETE .../assets/{id}` removes
  it. An asset that the timeline still uses is refused with the track named.
- Imports are streamed to a temporary file, probed with FFprobe, validated (a
  B-roll asset must have video, a sound effect must have audio, and each has a
  length limit) and only then moved into `assets/` under a **generated** name. A
  refused or failed import leaves nothing behind, and the original filename is
  kept only as a label — it can never become a path.
- Nothing is discovered by scanning folders: an asset exists only because the user
  chose that file, which is what "never use arbitrary files" means here.
- Items are placed from the timeline at the playhead: **B-roll** overlays another
  window of the source video or an imported clip, with placement (full frame, top
  half, bottom half) and fit (cover or contain); **sound effects** are mixed under
  the speech with gain and fade. Both are editable in the item inspector.
- The mix is deliberately conservative: an effect's gain is **capped at −3 dB** and
  defaults to −12 dB, and the graph mixes with `amix=normalize=0` so speech keeps
  its recorded level instead of being divided down by the effect.
- Inserts cannot overlap each other or run past the media they come from, and the
  export only ever reads files from the project's own folder.
- A source with no audio still gets its effects: the export then carries only the
  sound design, and the result says so.

Milestones 8–10 (stock search, transitions and automatic sound design, and the
release pass) are still to come.

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
| `POST` | `/api/projects/{project_id}/suggest-clips` | Detect candidate clips from the transcript (`409` until transcribed). |
| `GET` | `/api/projects/{project_id}/clips` | Read saved candidates, plus whether they are stale. |
| `PATCH` | `/api/projects/{project_id}/clips` | Save edited in/out points, titles, and scores. |
| `GET` | `/api/projects/{project_id}/captions` | Build the clip-relative caption cues for one clip. |
| `PATCH` | `/api/projects/{project_id}/settings` | Save caption and export settings for the next render. |
| `GET` | `/api/projects/{project_id}/assets` | Search the project's local B-roll and sound-effect library. |
| `POST` | `/api/projects/{project_id}/assets` | Import a local file as a B-roll clip or sound effect. |
| `GET` | `/api/projects/{project_id}/assets/{asset_id}/file` | Preview an imported asset. |
| `DELETE` | `/api/projects/{project_id}/assets/{asset_id}` | Remove an asset that the timeline no longer uses. |
| `GET` | `/api/projects/{project_id}/timeline` | Read the editable edit for one clip. |
| `PATCH` | `/api/projects/{project_id}/timeline` | Save an edited timeline (clip, captions, render settings, tracks). |
| `POST` | `/api/projects/{project_id}/render` | Render the selected clip to a captioned MP4 (`202` + job). |
| `GET` | `/api/projects/{project_id}/exports` | List finished renders, newest first. |
| `GET` | `/api/projects/{project_id}/exports/{filename}` | Download a rendered MP4 or its `.srt` sidecar. |

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

The clip tests are fully offline: they cover the heuristic signals, the boundary
and title handling for transcripts without punctuation, overlap and near-duplicate
flagging, edit validation, and the optional LLM pass through an injected transport
so no request is ever made.

The caption tests are offline too: they cover clip-relative timing, wrapping,
preset geometry, safe-area margins, characters that would otherwise inject ASS
override tags, and they read the written ASS/SRT back to confirm the style and
timings that FFmpeg receives.

The timeline tests are offline: they cover the composed document, frame-rate
validation, item and track validation, and then read the saved `project.json`
from disk to prove an edit really was written, that a refused edit changed
nothing, and that a project hand-edited with an item the renderer cannot draw is
refused at render time instead of exporting something different.

The asset tests cover per-kind file validation, tag handling, the search, the
"in use" refusal, and path safety for a hand-edited asset record. The import and
render tests write **real media** with FFmpeg: one imports a B-roll clip and a
sound effect through the API and exports them, then measures the exported file to
show the insert is composited only inside its own window (frame luma jumps from
the source's level to the insert's and back) and that the effect is audible only
inside its window (loudness against a silent source floor).

The export tests include **real FFmpeg renders** of a synthesised source: they
render a clip, measure the caption area of the output to show the captions really
were burned in, compare it against the same clip rendered with captions off, check
the MP4 is 1080 × 1920 with audio and the requested duration, download it through
the API, and confirm a failed or cancelled render leaves no partial file. One of
them edits the timeline first (square output, 25 fps, captions off) and then
measures the rendered file to show the **export followed the edit**. They are
skipped with an explicit reason when FFmpeg is unavailable.

The interface can be opened without any API key or uploaded media, but
transcription needs `faster-whisper` and a model. Clip suggestion needs neither.

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

## Optional LLM clip labels

Clip detection works with no key at all. If you want model-written titles and
reasons, set an OpenAI-compatible endpoint in `.env`:

| Setting | Default | Meaning |
|---|---|---|
| `LLM_API_KEY` | *(empty)* | Empty keeps the optional pass off. The key stays server-side. |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | Any OpenAI-compatible base URL. |
| `LLM_MODEL` | `gpt-4o-mini` | Model used to re-label candidates. |

The model may only rewrite titles, reasons, and scores. Clip **times always come
from the transcript**, so a hallucinated time range can never reach the timeline or
a render. If the call fails for any reason, the heuristic result is used and the
analysis explains that the optional pass was unavailable.

## System tools

**FFprobe and FFmpeg must be installed and available on `PATH`.** Video import
reads metadata with FFprobe, so importing reports a clear error if the tools are
missing. Transcription itself does not need FFmpeg because `faster-whisper`
decodes audio through PyAV, but **exporting a clip does**: the app reports a clear
message instead of failing if FFmpeg is not installed. Both are system tools, not
Python packages.

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
