# AI Video Production Studio

Local-first, browser-based video editing assistant for turning long-form video
into editable short-form clips. The browser is the interface and the Python API
runs locally on the user's machine.

## Quick start

```powershell
cd "$HOME\Documents\Ai video production studio\AI-Video-Editor-Production-Studio"
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Then open <http://127.0.0.1:8000/>. The health endpoint is at
<http://127.0.0.1:8000/api/health> and the API docs at
<http://127.0.0.1:8000/docs>. Stop with `Ctrl+C`.

**FFmpeg and FFprobe must be installed and on `PATH`** (`winget install
Gyan.FFmpeg`, then reopen PowerShell). Check with `ffmpeg -version`.

Want something to try straight away? Generate a demo clip — it is built on your
machine, so there is no download and no licence question:

```powershell
python scripts/make_demo_media.py
```

Then import `demo-media/demo-source.mp4` in the app.

Both optional integrations are **off by default**. To enable them, copy the
template and fill it in, then restart the server:

```powershell
Copy-Item .env.example .env
notepad .env
```

`.env` is ignored by Git. **Never put a real key in `.env.example`** — that file
is committed.

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
- Six presets: the original **Clean**, **Bold Highlight**, **Minimal**, and three
  short-form looks — **Bold Pop** (the spoken word changes colour and the line
  springs into place), **Karaoke Highlight** (the colour sweeps word by word with
  `\kf`), and **Clean Podcast** (white text, dark outline, no colour change and no
  animation). Bold Highlight switches colour instantly with `\k`; all three
  highlight presets wrap their own words, so a long cue is broken across lines
  rather than clipped at the frame edge.
- Style controls: **font family**, **font size**, **UPPERCASE**, **word
  highlighting on/off**, **animation level** (off / subtle / strong), **line
  length**, **highlight mode**, a **high-contrast box**, and a **movable caption
  position**. The position is a percentage of the frame height measured from the
  chosen edge; it is clamped into the safe band, so the captions never touch the
  frame border whatever it is set to, and the interface shows the reserved safe
  area. The preview in the inspector is built from the same cues the render burns
  in.
- The caption layout is **resolution-independent**: font size, outlines, shadows,
  margins, and the per-line character budget are all derived from the *output*
  size, and the ASS is written with `PlayResX/Y` equal to the output, so 1080 ×
  1920 and 1440 × 2560 keep the same apparent size and the same safe area.
  Devanagari and other scripts rely on libass font fallback; when a glyph has no
  font the export says so instead of silently dropping it.
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

Milestone 8 adds optional stock B-roll search through Pexels:

- `GET .../stock` reports whether a key is configured, and says plainly why not
  when it is not; `GET .../stock/search` searches, and `POST .../stock/import`
  downloads the clip you picked. Every route is scoped to one project.
- **Off unless you turn it on.** Set `PEXELS_API_KEY` in `.env` to enable it.
  With no key the panel explains what is missing and the local library keeps
  working — nothing about editing changes.
- **Search and preview first, download only when you choose.** A search returns
  metadata only. A file enters the project when you press *Add as B-roll*, which
  is the only path that ever downloads a byte.
- **The search is semantic, not generic.** With the box empty, the keywords are
  derived from the transcript of the clip you are editing, so the search matches
  what is actually being said at that moment. A **placement draft** is suggested
  from the transcript's own turns and applied through the same validated timeline
  `PATCH` the editor uses — a refused placement leaves the edit untouched.
- **Provenance is recorded, not just displayed.** Every imported clip keeps its
  provider, page URL, photographer, and licence in the asset record, and the
  result cards show the creator link and "Media provided by Pexels" credit
  wherever the media appears.
- **Only the provider's own hosts may be fetched**, over HTTPS, with the size
  limit enforced while streaming, and the clip's metadata is re-read from the
  provider rather than trusted from the browser. The interface was driven in a
  real browser to confirm a non-Pexels address is refused inside the UI, not only
  in a unit test.

Milestone 9 adds transitions and automatic sound design:

- Transitions are drawn by **FFmpeg itself** — `fade`, `slide-left`, and `wipe-left`
  — so nothing is downloaded, committed, or redistributed, and there is no licence
  to answer. A transition belongs to the clip's own **start or end**, which is what
  a short actually needs and keeps the timing unambiguous. The **Transitions** lane
  on the timeline draws each one as a bar at its edge, sized to its own length, so
  the size of the effect is readable without opening it.
- A transition is applied to the **finished** picture, after B-roll is composited,
  so a fade covers the insert instead of letting it hang over black. Two of the
  same kind at one edge never stack.
- **Automatic sound design is off by default** and needs no asset: the effects are
  **synthesised by FFmpeg** into the project's own `temp/` folder at export time, so
  an automatic effect mixes through *exactly* the same path as a hand-placed one.
  Two kinds are offered — a *whoosh* for a moving picture and a *pop* for a single
  cut.
- Effects are placed **only where the picture already changes** (a B-roll entrance
  or exit, or a transition at the clip's end), are spaced apart, and are capped by
  a setting you control. An edit with no cuts gets no automatic effects at all.
- The mix stays conservative: an automatic effect is capped at the same **−3 dB**
  ceiling as a chosen sound effect and defaults to −18 dB, and the graph mixes with
  `amix=normalize=0` so speech keeps its recorded level.
- The panel shows **exactly what the next render would add, and where**, so an
  automatic effect is never a surprise. Switching it off removes all of them.
- A project saved before Milestone 9 gets the settings defaulted on read, and the
  synthesised files are removed afterwards — a repeated render never accumulates
  audio in `temp/`.
- **Every track is now renderable**, so `UNRENDERED_TRACKS` is empty. The guard
  that refuses an item the renderer cannot draw is kept, so the next track that
  arrives is still refused at the edit boundary rather than silently dropped.

The controls are all wired to the live API — no button in the interface is a demo
anymore. The sample project stays clearly labelled as sample data.

## Automatic B-roll

`POST /api/projects/{project_id}/broll/propose` reads the clip's transcript and
suggests where a cutaway should go, **how long it should run, and from where** — a
draft the user accepts or ignores. `POST .../broll/apply` places accepted items
through the same validated timeline path as a hand-placed insert.

- **Placement follows the words.** A moment is suggested only where a sentence has
  concrete, visual words to illustrate; filler and short lines are skipped.
- **Duration reasons are explicit.** An insert covers about 80% of the spoken turn,
  bounded between 1.5s and 4s, so a short line still gets a readable glance and a
  long sentence stays a cutaway rather than a second edit.
- **The speaker keeps the screen.** Inserts cover at most 40% of the clip, stay at
  least 0.5s apart, and a short asset shortens the insert instead of freezing or
  blacking the frame.
- **Three source modes.** *Local* uses only imported B-roll; *Online* queries the
  configured Pexels key; *Auto* prefers a local asset whose label or tags match the
  words and falls back to a stock query. Nothing is downloaded until the user
  accepts a stock result, and with no key the panel says why while local B-roll
  keeps working.
- **Diagnosable by design.** A proposal that cannot be made is reported with a
  plain-language reason rather than silently skipped.

Milestone 10 adds the release pass: a demo you can run without any media of your
own, and an acceptance test that walks the whole workflow.

- **One command builds the demo.** `python scripts/make_demo_media.py` writes a
  narrated clip, a B-roll insert and a sound effect into `demo-media/` using only
  FFmpeg and Windows speech synthesis. Nothing is downloaded, so the demo carries
  no third-party licence and no media enters Git. Each scene is a distinct flat
  colour, so trimming, B-roll compositing and transitions are all visible by eye.
- **One test proves the whole workflow.** `tests/test_acceptance.py` walks
  PROJECT.md's "definition of done" end to end: start the app, load the
  interface, import a real video, transcribe it with the real Whisper model,
  suggest clips, adjust a clip's in/out points, build captions, save an edit,
  render, and download the result. It inspects the exported file with `ffprobe`
  rather than trusting the API's own report, and reads the saved `project.json`
  from disk to confirm the edit that was rendered is the edit that was saved.
- Two more tests cover the failure messages (an invalid file, an unknown project,
  a clip that does not exist, a path that tries to escape) and confirm the output
  stays inside the project's own folder.
- **`.env` is now genuinely read.** The README, `.env.example` and every
  stock/LLM error message told you to put your keys in `.env`, but nothing ever
  opened the file, so a configured key was silently ignored. It is read at startup
  now, and a variable exported in your shell still wins over the file.

## Known limitations

Stated plainly, because a feature that is not finished should not look finished.

- **No speaker diarization.** The transcript has no "who spoke when", so a
  multi-speaker conversation is one undifferentiated stream of text.
- **Clip scores are estimates, never predictions.** A score describes how
  self-contained and information-dense a moment is. Nothing here predicts whether
  a clip will perform well on a platform.
- **Transcription runs on CPU here.** This machine has a visible NVIDIA GPU but no
  cuBLAS runtime, so it falls back to CPU/int8 — correct, but noticeably slower
  than the GPU path on a machine with a working CUDA runtime.
- **Whisper does not always punctuate.** When a transcript has little sentence
  punctuation, clip edges are described as estimates that may need a trim rather
  than as exact sentence boundaries.
- **Transitions are a small fixed set** (fade, slide, wipe) at the clip's start
  or end. There is no transition *between two shots*, because a short is cut from
  one source here.
- **The timeline is deliberately simple.** It edits one clip at a time and has no
  undo history, snapping, ripple editing, or a multi-clip project view.
- **Stock B-roll needs a Pexels account**, and its licence has conditions of its
  own — check <https://www.pexels.com/license/> before publishing, especially
  where a clip shows a recognisable person or brand.
- **Video is rendered by FFmpeg in software.** Hardware encoding is not wired up,
  so a 1440 × 2560 export is slower than the 1080 × 1920 default.
- **The LLM pass may only rewrite titles, reasons, and scores.** Clip times always
  come from the transcript, so a hallucinated time can never reach a render.

## AI tools used

- **faster-whisper** (CTranslate2, OpenAI Whisper weights) for local
  speech-to-text with word-level timestamps.
- **An optional OpenAI-compatible LLM** for clip titles, reasons, and scores.
  Disabled unless `LLM_API_KEY` is set, and the transparent heuristic is used
  whenever it is unavailable.
- **An optional Pexels API** for stock B-roll search. Disabled unless
  `PEXELS_API_KEY` is set.
- **FFmpeg/FFprobe** for every media operation: probing, trimming, compositing,
  subtitle burn-in, transitions, synthesised sound effects, and export.

No model is trained, no LoRA is fine-tuned, and no media is uploaded anywhere
except to the two optional APIs you explicitly enable.

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
| `GET` | `/api/projects/{project_id}/stock` | Whether stock B-roll search is configured, and on what terms. |
| `GET` | `/api/projects/{project_id}/stock/search` | Search stock B-roll; empty query uses the clip's own transcript words. |
| `POST` | `/api/projects/{project_id}/stock/import` | Download one chosen stock clip into the project, with its credit. |
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

The stock tests are offline too: they cover keyword derivation from a clip's own
transcript, result parsing, rendition selection, the download host policy (a
non-Pexels address is refused, and an oversized download is cut off mid-write),
the recorded provenance, and the placement draft. One acceptance test then walks
the whole path through the real routes — search, import a real FFmpeg-written
clip, place it on the timeline at the suggested point, and confirm the credit is
still in the saved project. The interface itself was also driven in a real
browser, with a stub standing in for the provider, to confirm the results render
with their credit and that a non-Pexels download address is refused inside the
UI rather than only in a test.

The transition and sound-design tests are offline *and* real. Offline they cover
the filter fragment for every kind at every edge, the length bounds, the ordering
and de-duplication of a chain, the settings validation, the proposals that follow
the real picture changes, the cap, and the gain ceiling — then read the saved
`project.json` to prove an edit really was written and that a refused one changed
nothing. With FFmpeg they **measure the finished file**: every filter is rendered,
the fade is shown to take the first frame to black and then recover, the whoosh is
shown to be loudest in the middle rather than assumed to be, and the mixed audio is
measured inside an effect's window and against the silence outside it.

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

## Transitions and automatic sound design

Both features need **no asset, no download, and no licence**: FFmpeg synthesises
both at export time.

**Transitions** are stored on the clip's own `transitions` track and happen at the
clip's start or end. Three kinds are offered — *Fade to black*, *Slide in from the
right*, and *Wipe in from the left* — and a length of up to 2 seconds, further
bounded to half the clip so a transition can never cover the whole thing. Add one
with **+ Transition** and edit it by clicking its marker on the timeline.

**Automatic sound design** is **off until you turn it on**, in *Sound design*:

| Setting | Default | Meaning |
|---|---|---|
| Automatic effects | Off | The master toggle. Off means no automatic sound is added at all. |
| Effect | `whoosh` | `whoosh` marks a moving picture; `pop` marks a single cut. |
| Maximum effects | 2 | A hard cap of 4 applies whatever you choose. |

Effects are placed **only where the picture already changes** — a B-roll entrance or
exit, or a transition at the clip's end — and are never added at arbitrary points.
The panel lists the exact times the next render would use, so nothing is a
surprise. An automatic effect is mixed at **−18 dB**, inside the same **−3 dB**
ceiling as a hand-placed sound effect, so it can never bury the speech.

If FFmpeg cannot create an effect, the export fails with a clear message rather
than quietly exporting without it, and nothing is left behind in `temp/`.

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
CPU automatically, and the fallback is recorded in the transcript. This includes a
runtime that only fails on the **first inference** — CTranslate2 loads a CUDA model
successfully and only then fails on the CUDA libraries, so the retry is applied
during transcription as well as at model load.

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

## Optional stock B-roll search (Pexels)

Stock search is **off by default**. To enable it, create a free key at
<https://www.pexels.com/api/> and put it in `.env`:

| Setting | Default | Meaning |
|---|---|---|
| `PEXELS_API_KEY` | *(empty)* | Empty keeps stock search off. The key stays server-side. |
| `PEXELS_BASE_URL` | `https://api.pexels.com` | Override only for a compatible endpoint. |

With a key set, the **Stock B-roll** panel in the sidebar searches Pexels. Leave
the search box empty to search using the selected clip's own transcript words.
Nothing is downloaded until you press *Add as B-roll* on a result; the clip is
then stored in the project's `assets/` folder under a generated name, with its
creator, page URL, and licence saved alongside it and shown in the library.

Clips may only be downloaded from Pexels's own HTTPS hosts, the size limit is
enforced while streaming, and the clip's metadata is re-read from the provider
rather than trusted from the browser. If the network, the key, or the provider
fails, you get a clear message and your local library keeps working.

Note that Pexels's licence has conditions of its own — check
<https://www.pexels.com/license/> before publishing, especially where a clip
identifies a person or a brand.

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
