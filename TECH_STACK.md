# Technical Stack and Open-Source Research

## 1. Stack recommendation

Use the smallest stack that can deliver the end-to-end MVP.

| Layer | Recommendation | Reason |
|---|---|---|
| Frontend | HTML + CSS + vanilla JavaScript | Matches the project owner's preference; low setup and dependency overhead. |
| Local API | Python + FastAPI + Uvicorn | Simple local HTTP API, typed request/response models, useful interactive API docs. |
| Video inspection | FFmpeg / FFprobe | Reliable media metadata, trimming, scaling, audio mixing, filters, and export. |
| Speech transcription | `faster-whisper` | Local Whisper inference using CTranslate2; supports timestamps and configurable compute types. |
| Styled subtitles | `pysubs2` + ASS subtitles rendered by FFmpeg | Pure-Python subtitle editing and styling; ASS supports positioning and style definitions. |
| Clip scoring | Small optional LLM API adapter + deterministic fallback | Avoid requiring a large local LLM; app remains usable without a key. |
| Stock video | Pexels API adapter, optional | Official API supports video search; show provider credit and comply with current terms/rate limits. |
| Local assets | Filesystem folders + JSON project files | No database needed for MVP. |
| Timeline | Custom lightweight DOM-based timeline | Enough for trim/move/inspector edits without introducing a large editor framework. |
| Testing | `pytest` for backend utilities; manual end-to-end sample | Small footprint and easy to run. |

### Avoid initially

- Docker
- PostgreSQL/SQLite unless project persistence demonstrably needs a database
- Redis/Celery/background queue frameworks
- React/Next.js or a heavy component library
- WhisperX and diarization dependencies in the MVP
- A locally hosted LLM
- Training a model or LoRA
- Multiple overlapping video-editing libraries
- Bundling huge stock libraries, model weights, or transition packs in Git

Add a dependency only when a specific implemented feature needs it. Pin tested versions after the environment works.

## 2. Recommended repository references

### A. faster-whisper — local transcription

Repository: https://github.com/SYSTRAN/faster-whisper

Use for local speech-to-text. It is based on CTranslate2 and can return segment and word timestamps. Its README states that its audio decoding uses PyAV, so a separate system FFmpeg installation is not required merely for transcription; **the application still needs FFmpeg/FFprobe for rendering and media operations**.

Implementation notes:
- Start with a small model and provide a CPU/int8 fallback.
- Download model weights on first use rather than committing them.
- Make model size configurable.
- Do not assume the GPU environment is ready just because an NVIDIA GPU exists; test CUDA/cuDNN compatibility and preserve CPU fallback.
- Word-level timing is helpful for captions, but still inspect/handle imperfect timestamps.

Reference: https://github.com/SYSTRAN/faster-whisper

### B. pysubs2 — subtitle editing and ASS styling

Repository: https://github.com/tkarabela/pysubs2

Use to read/write subtitle formats and create styled ASS subtitles. It is pure Python with no runtime dependencies according to its project README.

Implementation notes:
- Use ASS for burned-in styled captions and SRT for optional sidecar subtitles.
- Start with 2–3 original presets; do not clone a proprietary creator's exact template.
- Implement readable line breaks, safe-area placement, outline/shadow, and optional keyword emphasis.
- If word timings are missing, use segment-level captions rather than pretending word timing is accurate.

Reference: https://github.com/tkarabela/pysubs2

### C. FFmpeg — rendering engine

Website/docs: https://ffmpeg.org/documentation.html

Use FFmpeg CLI (invoked safely with Python `subprocess` argument arrays) for trimming, scaling/cropping, audio mixing, subtitle burn-in, and MP4 export.

Implementation notes:
- Never build a shell command by concatenating untrusted user input.
- Check process exit code and output-file existence.
- Capture logs and surface helpful errors.
- Use a temporary render file, then rename it after successful completion.
- Keep video processing outside the browser; browser preview is not the renderer.
- FFmpeg is not a Python package; document how to install it on Windows and verify both `ffmpeg` and `ffprobe` are on PATH.

Reference: https://ffmpeg.org/documentation.html

### D. Pexels API — optional stock video search

Official docs: https://www.pexels.com/api/documentation/

Use only after local B-roll selection works. Pexels documents a REST API with video endpoints. The integration must follow current API rules, rate limits, and attribution guidance. Show a visible “Media provided by Pexels” link/credit where required, and record the source URL/creator when available.

Important:
- The API is optional and needs a user-supplied API key.
- Never put the key in frontend JavaScript or commit it to Git.
- Keep the app functional if no key is configured.
- Search and preview first; download/use only after the user chooses an asset.
- Recheck the current Pexels terms before a public release.

Reference: https://www.pexels.com/api/documentation/

### E. Optional timeline library — evaluate, do not adopt blindly

Repository: https://github.com/Ektie/react-video-timeline-editor

This is a React multi-track timeline component with trimming, splitting, moving, captions, and browser preview. However, it introduces React and is licensed AGPL-3.0 according to its repository page. For this project's lightweight vanilla-JS MVP, **do not integrate it by default**. Consider it only if the custom timeline is the main blocker and the team understands the license implications.

Reference: https://github.com/Ektie/react-video-timeline-editor

### F. Optional AI-ready renderer reference

Repository: https://github.com/abekyo/abekyo-editor

This project describes a self-hosted editor built with Remotion/Next.js and JSON-based projects. It is useful as an architectural reference for treating a timeline as structured project data, but its stack is not the recommended initial stack here because the app owner wants HTML/CSS/JS with a Python backend and minimal installation.

Reference: https://github.com/abekyo/abekyo-editor

## 3. Architecture

```text
Browser UI (HTML/CSS/vanilla JS)
        |
        | HTTP requests to localhost
        v
FastAPI backend
  ├── project and upload service
  ├── FFprobe metadata service
  ├── faster-whisper transcription service
  ├── clip candidate service
  ├── caption/ASS generation service
  ├── local asset and optional Pexels search service
  ├── optional LLM pass for clip labels and B-roll moments
  ├── timeline/project JSON service
  └── FFmpeg render service
        |
        v
Local workspace
  ├── projects/<project-id>/source/
  ├── projects/<project-id>/transcripts/
  ├── projects/<project-id>/project.json
  ├── projects/<project-id>/assets/
  ├── projects/<project-id>/temp/
  └── projects/<project-id>/exports/
```

## 4. API shape (suggested)

Keep the first API small:
- `GET /api/health`
- `POST /api/projects` — create project/upload source video
- `GET /api/projects/{project_id}` — project data and metadata
- `POST /api/projects/{project_id}/transcribe`
- `POST /api/projects/{project_id}/suggest-clips`
- `PATCH /api/projects/{project_id}` — save editable project data
- `POST /api/projects/{project_id}/render`
- `GET /api/jobs/{job_id}` — status/progress for long operations
- `GET /api/projects/{project_id}/exports/{filename}` — serve generated export safely

This is a suggested interface, not a requirement to implement all routes in the first milestone. Use background tasks only as needed; avoid adding a separate queue framework.

## 5. Hardware and model choices

Target development environment is Windows with a mid-range NVIDIA laptop GPU (reported RTX 3050 6 GB VRAM and 16 GB RAM). Do not assume every user's machine matches this.

- Begin with `faster-whisper` small or base model and benchmark a real sample.
- Offer CPU/int8 mode as a fallback.
- Avoid large transcription models as the default.
- Model files should be downloaded on first use and excluded from Git.
- Do not run transcription and high-resolution rendering concurrently by default on a 6 GB VRAM machine.
- Default output to 1080 × 1920 vertical. Expose 1440 × 2560 as an optional 2K-class render only if it works on the target machine and the source supports it; warn that rendering may take longer.
- Use FFmpeg software encoding as the reliable baseline. Hardware encoding may be an optional later optimization after testing.

## 6. Online API and secrets policy

- All API keys belong in `.env` and must be loaded server-side.
- Commit `.env.example`, never `.env`.
- Add `.env`, model caches, uploaded videos, rendered files, logs, and temp files to `.gitignore`.
- If no LLM key is present, use heuristic clip scoring and clearly label it as a basic fallback.
- If no Pexels key is present, use local assets and manual search.
- Do not make a paid service mandatory for the demo.

## 7. Dependency and system hygiene rules

The coding agent MUST follow these rules:

1. Do not install unnecessary global packages or modify system settings.
2. Do not install Docker, WSL, databases, or package managers unless required and explicitly approved.
3. Use a project-local Python virtual environment (`.venv`) and a local Node setup only if Node is actually required.
4. Prefer the Python standard library and small focused libraries where practical.
5. Do not download model weights, stock videos, fonts, or SFX packs until the relevant feature is being implemented and the user understands the size/license.
6. Do not commit model weights, large media, build artifacts, virtual environments, caches, or secrets.
7. Do not add multiple libraries that solve the same problem.
8. Before adding a dependency, state what feature needs it and why existing dependencies cannot do the job.
9. Use `subprocess.run([...], shell=False)` for FFmpeg invocation; never pass untrusted paths through a shell.
10. After each milestone, run tests and commit a known-good state.
11. Keep an optional feature disabled if it adds excessive installation weight or jeopardizes the core workflow.
12. Keep user media local unless the user explicitly opts into an external API/upload.

## 8. License and integration caution

Repository availability does not automatically mean every asset/model can be used without conditions. Before copying code, bundling assets, or redistributing model weights, check each repository's current license and the license of its dependencies. Prefer using maintained packages through their normal package manager instead of copying large codebases into the app. Record attribution and provider provenance where required.

## 9. Research references

- faster-whisper: https://github.com/SYSTRAN/faster-whisper
- pysubs2: https://github.com/tkarabela/pysubs2
- FFmpeg documentation: https://ffmpeg.org/documentation.html
- Pexels API documentation: https://www.pexels.com/api/documentation/
- React video timeline editor (optional; AGPL-3.0): https://github.com/Ektie/react-video-timeline-editor
- Abekyo Editor (architectural reference): https://github.com/abekyo/abekyo-editor
