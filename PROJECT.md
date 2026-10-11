# AI Video Production Studio — Project Brief

## 1. Product overview

**AI Video Production Studio** is a local-first, browser-based video editing assistant. A user imports a long video (for example, a podcast), and the app helps turn it into several short-form clips suitable for YouTube Shorts, Instagram Reels, and TikTok.

The application should automatically propose the strongest moments, create captions, suggest relevant B-roll, place sound effects and transitions, and render short clips. The user must be able to review and adjust the generated edit before exporting.

The browser is the interface; a Python backend runs on the user's computer at `localhost`. Video files should stay on the user's machine unless the user deliberately chooses an online API.

## 2. Core promise

**Upload a long video → review suggested clips → refine the edit → export polished shorts.**

The AI creates a useful first draft, not an unchangeable final video. Human editing and control are essential.

## 3. Target user

Creators and editors who turn podcasts, interviews, talking-head videos, educational videos, and similar long-form content into short vertical clips.

## 4. Product principles

1. **Local-first:** keep source videos, project files, and rendering local by default.
2. **Lightweight:** install only dependencies required for the current milestone. Do not add Docker, databases, large UI frameworks, or extra models without a demonstrated need.
3. **Useful before clever:** get one complete end-to-end workflow working before adding advanced AI.
4. **Editable output:** all automatic decisions should be represented as editable timeline/project data.
5. **Graceful fallback:** if an API key, network connection, model, or optional library is unavailable, core editing should still work where possible.
6. **Honest AI:** label clip scores as estimates, not guarantees of virality.
7. **Safe and legal media use:** use only media the user owns, has permission to use, or can lawfully use under the relevant platform's terms. Respect API terms, attribution requirements, rate limits, and licenses.

## 5. MVP scope

The first working version should support this complete path:

1. Import one local video file.
2. Read video metadata and create a project.
3. Transcribe the audio locally, with timestamps.
4. Suggest a small number of candidate clips from transcript segments.
5. Let the user select and adjust each clip's start/end time.
6. Render a vertical 9:16 short with captions.
7. Optionally add a manually selected local B-roll asset and a local sound effect.
8. Export MP4 and save the project data.

Do not make every advanced feature a prerequisite for the first successful export.

## 6. Later features

After the MVP works reliably, add in this order:

- Editable multi-track timeline and preview.
- Better clip scoring and clip overlap/duplicate detection.
- Styled captions and word-level highlighting.
- B-roll search using an approved stock API, starting with Pexels if its API access and terms fit.
- Suggested B-roll timing based on transcript meaning.
- Local sound-effect library and optional online sound search.
- Transitions at selected cuts and B-roll boundaries.
- Batch export, project reopening, and improved rendering settings.
- Optional image-generation API for visual gaps, only if time remains and the hackathon theme benefits from it.

## 7. Out of scope for the hackathon MVP

- Replacing Premiere Pro, DaVinci Resolve, or CapCut.
- A professional-grade nonlinear editor with every editing operation.
- Training a new speech or generative model.
- Training a LoRA.
- Guaranteed virality prediction.
- Automatic downloading from arbitrary social-media URLs.
- A mandatory cloud account, cloud database, or paid service.
- Installing a full local LLM or large generative model just to make the MVP work.

## 8. Definition of done

The MVP is successful when a new user can:

- Start the app using documented commands.
- Open the local web interface.
- Import a supported video.
- Generate a timestamped transcript.
- See suggested clip candidates.
- Adjust a clip's in/out points.
- Generate readable captions.
- Render and play/download a short MP4.
- Reopen or inspect the saved project JSON.
- See understandable errors when a step fails.

A smaller reliable workflow is better than many unfinished buttons.

## 9. Operating assumptions

- Initial target: Windows desktop, with code kept portable where practical.
- UI: HTML, CSS, and vanilla JavaScript.
- Backend: Python and FastAPI.
- Rendering: FFmpeg.
- Local transcription: faster-whisper, initially with a small model suitable for limited VRAM.
- Online services are optional adapters, not hard dependencies.
- Export targets: 9:16 vertical, 1080 × 1920 by default; 2K output may be offered only when the input, hardware, and render time make it practical. Do not promise 2K if the source or machine cannot handle it.

## 10. First instruction to the coding agent

Read `PROJECT.md`, `FEATURES.md`, `DESIGN.md`, and `TECH_STACK.md` before changing code. Implement the project in the milestone order defined in `FEATURES.md`. After each milestone, run the relevant tests, update the README, and create a Git commit. Never install unnecessary dependencies or attempt to implement all features at once.
