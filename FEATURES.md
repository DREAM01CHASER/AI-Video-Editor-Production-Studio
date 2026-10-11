# Features and Build Milestones

This file is the implementation plan. **Complete and test one milestone before starting the next.** Commit to Git after every milestone.

## Priority legend

- **P0:** required for a working MVP.
- **P1:** important enhancement after the MVP.
- **P2:** stretch feature; skip if time is short.

## Milestone 0 — Repository and safe foundation (P0)

- Create a clean repository and `.gitignore`.
- Add README with Windows setup and run instructions.
- Add `requirements.txt` or `pyproject.toml` with only initial dependencies.
- Add `.env.example`; never commit API keys.
- Create a local workspace folder for projects, uploads, previews, and exports.
- Validate upload type and file size; use safe generated filenames rather than trusting user filenames.
- Keep temporary files isolated per project and clean them up safely.
- **Git checkpoint:** `chore: establish project foundation`.

**Acceptance:** backend starts, frontend loads, health endpoint works, no secrets or media files are committed.

## Milestone 1 — Frontend shell (P0)

Build a clean dark desktop-first interface with:
- Project header and clear status.
- Video import drop zone and file picker.
- Video metadata display (duration, resolution, frame rate, size).
- Main preview area.
- Processing steps/status with progress and error messages.
- Clip-candidate list.
- Basic caption style controls.
- Export settings panel.

Use sample/mock data only for layout work. Clearly label mock functionality and do not pretend a button works if it does not.

**Acceptance:** responsive interface loads and can show a sample project without requiring AI models or API keys.

**Git checkpoint:** `feat: add frontend shell`.

## Milestone 2 — Local backend and video ingest (P0)

- Build a FastAPI app with `/api/health`.
- Add a local upload endpoint and project creation.
- Use FFprobe to read metadata and FFmpeg for safe media operations.
- Store files in a project-specific local directory.
- Return structured JSON errors.
- Do not load an entire large video into RAM; use file paths and streaming/chunk-aware operations.

**Acceptance:** import a short sample video, see correct metadata, and receive a project ID.

**Git checkpoint:** `feat: add local video ingest`.

## Milestone 3 — Local transcription (P0)

- Integrate `faster-whisper`.
- Start with a small model and CPU/int8 fallback; provide an explicit model setting.
- Use word timestamps if supported and useful; fall back to segment timestamps.
- Save transcript segments as JSON with `start`, `end`, `text`, and optional word timings.
- Show transcription progress and allow retry/cancel where practical.
- Download models only when needed, explain that first use may download a model, and store them in the normal model cache.
- Do not add speaker diarization in the MVP.

**Acceptance:** a sample clip produces a readable transcript with usable timestamps.

**Git checkpoint:** `feat: add local transcription`.

## Milestone 4 — Candidate clip detection (P0)

- Use transcript segments to propose 3–5 candidate clips.
- For hackathon MVP, use a small optional LLM API if configured; otherwise use a transparent heuristic fallback.
- Consider hook strength, self-contained meaning, context, information density, and duration.
- Preserve enough context so a clip does not begin or end mid-sentence where avoidable.
- Do not claim a clip is guaranteed to go viral.
- Each candidate must have a title/reason, start/end time, and editable score/reasoning.
- Detect and flag overlapping or near-duplicate candidates.

**Acceptance:** transcript can produce candidates and the user can adjust start/end times.

**Git checkpoint:** `feat: suggest short-form clips`.

## Milestone 5 — Captions and first export (P0)

- Generate subtitles from the transcript for the selected clip.
- Correctly shift transcript timestamps to clip-relative time.
- Implement 2–3 readable caption presets, such as Clean, Bold Highlight, and Minimal.
- Support font size, safe-area placement, line length, and high contrast.
- Use ASS subtitles for styled/burned-in captions; SRT may be offered as a sidecar export.
- Render via FFmpeg and provide progress, logs, cancel/error handling if practical.
- Default to 1080 × 1920 vertical output; preserve aspect ratio and crop/scale sensibly.
- Do not upscale to 2K by default.

**Acceptance:** user can export and play a captioned vertical MP4.

**Git checkpoint:** `feat: render captioned vertical clips`.

## Milestone 6 — Simple editable timeline (P0/P1)

Represent the edit as project JSON, not as hidden FFmpeg command state. Include:
- Source video and clip in/out points.
- Output dimensions and frame rate.
- Caption style and caption track.
- B-roll items with source, start, end, placement, and fit mode.
- SFX items with source, start, end, gain, and fade.
- Transition items/type/duration.
- Render settings.

Provide a basic timeline/editor where the user can modify clip start/end, caption preset, B-roll selection, and SFX timing. A full professional NLE is not required.

**Acceptance:** changes in the UI update the project JSON and the next render uses those changes.

**Git checkpoint:** `feat: add editable project timeline`.

## Milestone 7 — Local B-roll and sound effects (P1)

- Let users import or choose local B-roll clips and SFX from an asset library.
- Add a searchable local asset list with tags.
- Allow manual placement and timing first.
- Mix audio without unexpectedly overpowering speech; support gain and fade.
- Never automatically use arbitrary files from outside the user's selected assets.
- Add online search only through a documented API and only after the local workflow works.

**Acceptance:** user can overlay one B-roll clip and one SFX asset and export successfully.

**Git checkpoint:** `feat: add local b-roll and sfx`.

## Milestone 8 — Stock search and semantic suggestions (P1/P2)

- Add a provider adapter for Pexels video search if API access is available.
- Search using concise keywords derived from transcript context.
- Show preview, source, attribution/credit, and license/terms information where required.
- Do not silently download or use assets if provider terms prohibit the intended use.
- Let the user choose results; automated placement can be a suggested draft.
- If network/API access fails, keep local assets usable.

**Acceptance:** user can search, preview, select, and place a stock clip with provenance shown.

**Git checkpoint:** `feat: search stock b-roll`.

## Milestone 9 — Transitions and automatic sound design (P2)

- Use a small set of bundled or user-supplied transition assets, only if licensing allows.
- Add transitions only where they improve the edit; do not force a transition at every cut.
- Add SFX at selected cuts and B-roll entrances/exits with adjustable volume and timing.
- Include a toggle to disable all automatic SFX/transitions.
- Avoid visual/audio overload.

**Acceptance:** transition and SFX choices are editable and export without broken timing.

**Git checkpoint:** `feat: add transitions and sound design`.

## Milestone 10 — Final reliability and demo (P0 before submission)

- Test a short sample video from import to export.
- Test missing FFmpeg, unavailable API, invalid file, and failed render messages.
- Confirm output path and project persistence.
- Add a sample project or short royalty-cleared sample media only if licensing permits.
- Record a demo of the working path.
- Update README with features that genuinely work, setup steps, known limitations, and AI tools used.
- Do not advertise unfinished features.

**Git checkpoint:** `release: hackathon mvp`.

## Time-box rules

1. If transcription is not working, pause B-roll work and fix transcription.
2. If rendering is not working, stop adding features until one export succeeds.
3. If the timeline becomes too complex, edit clip points and settings in a simple inspector first.
4. Do not add React, a database, Docker, Celery, Redis, or a local LLM unless a concrete blocker proves it necessary.
5. Keep each milestone small enough to test and revert.
