# Product Design and UX

## 1. Design goal

Create a polished, calm, dark desktop-style editing workspace that feels like a focused creator tool—not a generic chatbot dashboard. The interface should make the next action obvious and expose progress/errors clearly.

## 2. Core user journey

1. Open the app at localhost.
2. Import a video.
3. Review metadata and start transcription.
4. Review transcript and candidate clips.
5. Select a candidate and adjust its start/end.
6. Choose caption style and output format.
7. Optionally add B-roll/SFX.
8. Preview and render.
9. Download/open the exported MP4 and save the project.

Do not force the user to configure APIs before trying local features.

## 3. Main layout

Use a desktop-first three-zone workspace, adapting to smaller screens:

### Left sidebar — Projects and assets
- Current project and project list.
- Import video button.
- Local B-roll and SFX library.
- Clear asset type icons and filenames.

### Center — Preview and clip candidates
- Video preview with play/pause and current time.
- Candidate clip cards showing title, duration, start/end, and why suggested.
- Selected clip should be visually obvious.
- Preview should show the chosen aspect ratio/caption style when feasible.

### Right inspector — Selected item settings
- Clip in/out time.
- Aspect ratio and export resolution.
- Caption preset, font size, placement, and highlight mode.
- B-roll source/timing/fit.
- SFX volume/timing.
- Transition type/duration where implemented.

### Bottom — Basic timeline
- Main video track.
- Caption track.
- Optional B-roll track.
- Optional SFX/audio track.
- Playhead and time ruler.
- Simple trim/move controls are sufficient for the MVP. Do not imitate a complete professional editor prematurely.

## 4. Visual style

- Dark neutral background with readable contrast.
- One restrained accent color for primary actions and selected states.
- Clear typography and consistent spacing.
- Rounded panels used sparingly.
- Avoid excessive gradients, glass effects, animated decorations, and oversized empty hero sections.
- Use clear status labels: Waiting, Processing, Completed, Failed.
- Show progress for long tasks; never leave a button spinning indefinitely.

## 5. Interaction rules

- Every visible button must work or be clearly disabled with a reason.
- Confirm destructive actions such as deleting a project or asset.
- Keep the original source file untouched.
- Display processing steps and useful errors in plain language.
- Keep user edits when switching between clips.
- Allow undo for timeline changes if simple to implement; otherwise avoid destructive auto-edits.
- Use keyboard shortcuts only when documented and conflict-free.
- Make loading, empty, error, and success states part of the design—not afterthoughts.

## 6. Timeline/project model

Use a serializable JSON project as the source of truth. Suggested conceptual structure:

```json
{
  "project_id": "generated-id",
  "source_video": "relative/path/to/source.mp4",
  "source_metadata": {
    "duration": 600.0,
    "width": 1920,
    "height": 1080,
    "fps": 30
  },
  "clips": [
    {
      "id": "clip-1",
      "title": "Candidate title",
      "source_start": 42.0,
      "source_end": 84.0,
      "output_aspect": "9:16",
      "captions": {
        "enabled": true,
        "preset": "bold-highlight"
      },
      "broll": [],
      "sfx": [],
      "transitions": []
    }
  ]
}
```

This is an illustrative schema, not a requirement to copy it verbatim. Validate all timestamps and paths on the backend. Use relative project paths where possible. Do not store API keys in project JSON.

## 7. Rendering UX

- Show the selected clip duration and output resolution before render.
- Show render progress when possible; otherwise show a clear processing state and log output.
- Keep the UI responsive while processing.
- Save output to a predictable `exports/` directory and display the exact path.
- Never claim a render completed until the backend confirms success and the output file exists.
- If render fails, show a concise error and a way to view technical details.

## 8. Accessibility and quality

- Ensure text and controls have strong contrast.
- Use labels for inputs and accessible names for icon-only controls.
- Do not rely on color alone to communicate status.
- Ensure subtitles remain inside safe areas for vertical social-video formats.
- Avoid placing captions under common platform UI overlays.
