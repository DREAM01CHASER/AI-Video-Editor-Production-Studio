/**
 * Frontend for the AI Video Production Studio.
 *
 * Import, the project list, and local transcription talk to the FastAPI backend
 * (Milestones 2 and 3). Transcription runs as a background job: the page starts
 * it, polls /api/jobs/{id} for real progress, and then loads the saved transcript
 * from /api/projects/{id}/transcript so a reload restores it without re-running
 * the model.
 *
 * Milestone 4 added transcript-based clip suggestions, and Milestone 5 added
 * captions and a real export: the page asks the backend for the caption cues of
 * the selected clip, then starts a render job and polls it exactly like
 * transcription. Anything that is not implemented yet stays disabled with a
 * reason in its title, and the processing panel labels anything that is a demo.
 */

const DEMO_PROJECT = {
  id: "demo-episode-12",
  name: "Demo — Podcast Episode 12",
  status: "Ready",
  source: {
    duration: 782.4,
    width: 1920,
    height: 1080,
    fps: 30,
    sizeBytes: 1_281_638_400,
  },
  candidates: [
    {
      id: "clip-1",
      title: "The five-minute editing rule",
      reason: "Self-contained tip with a clear payoff that opens on a complete sentence.",
      start: 42.5,
      end: 88.1,
      score: 0.87,
    },
    {
      id: "clip-2",
      title: "Why retention drops at 30 seconds",
      reason: "High information density and a specific number that hooks the viewer early.",
      start: 138.0,
      end: 172.4,
      score: 0.83,
    },
    {
      id: "clip-3",
      title: "The worst advice for new creators",
      reason: "Contrarian framing with a strong emotional beat; overlaps the previous clip's topic.",
      start: 150.0,
      end: 196.7,
      score: 0.79,
      overlapWith: "clip-2",
    },
    {
      id: "clip-4",
      title: "One habit that doubled output",
      reason: "Concrete before/after story that closes cleanly on a resolution.",
      start: 505.2,
      end: 548.9,
      score: 0.76,
    },
  ],
};

const EMPTY_PROJECT = {
  id: null,
  name: "No project open",
  status: "Idle",
  source: null,
  candidates: [],
};

/** Steps that genuinely happen during Milestone 2 ingest. */
const INGEST_STEPS = ["Validate file", "Store in local workspace", "Read metadata"];

/** Steps of the future pipeline, shown by the layout demo only. */
const DEMO_STEPS = [
  "Import and validate file",
  "Read video metadata",
  "Transcribe audio",
  "Suggest short clips",
  "Render export",
];

const SUPPORTED_EXTENSIONS = [".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"];

/** How often the page asks the backend for transcription progress. */
const TRANSCRIBE_POLL_MS = 800;

/** Rendering reports progress far more often, so it is polled a little slower. */
const RENDER_POLL_MS = 700;

const els = {
  projectName: document.getElementById("project-name"),
  projectId: document.getElementById("project-id-value"),
  projectStatus: document.getElementById("project-status-value"),
  healthStatus: document.getElementById("health-status"),
  healthMessage: document.getElementById("health-message"),
  dataSourceBadge: document.getElementById("data-source-badge"),
  btnImport: document.getElementById("btn-import"),
  btnNewProject: document.getElementById("btn-new-project"),
  btnRefreshProjects: document.getElementById("btn-refresh-projects"),
  btnRunDemo: document.getElementById("btn-run-demo"),
  btnExport: document.getElementById("btn-export"),
  projectList: document.getElementById("project-list"),
  assetList: document.getElementById("asset-list"),
  processingPanel: document.getElementById("processing-panel"),
  processingMessage: document.getElementById("processing-message"),
  progressBar: document.getElementById("processing-progress"),
  progressFill: document.getElementById("progress-fill"),
  processingSteps: document.getElementById("processing-steps"),
  processingError: document.getElementById("processing-error"),
  processingLog: document.getElementById("processing-log"),
  transcriptBadge: document.getElementById("transcript-badge"),
  btnTranscribe: document.getElementById("btn-transcribe"),
  btnTranscribeCancel: document.getElementById("btn-transcribe-cancel"),
  btnTranscribeRetry: document.getElementById("btn-transcribe-retry"),
  transcriptSummary: document.getElementById("transcript-summary"),
  transcribeProgress: document.getElementById("transcribe-progress"),
  transcribeProgressFill: document.getElementById("transcribe-progress-fill"),
  transcriptStatus: document.getElementById("transcript-status"),
  transcriptError: document.getElementById("transcript-error"),
  transcriptList: document.getElementById("transcript-list"),
  transcriptEmpty: document.getElementById("transcript-empty"),
  video: document.getElementById("video-element"),
  previewEmpty: document.getElementById("preview-empty"),
  previewEmptyHint: document.getElementById("preview-empty-hint"),
  previewCurrentTime: document.getElementById("preview-current-time"),
  previewDuration: document.getElementById("preview-duration"),
  previewClipLabel: document.getElementById("preview-clip-label"),
  dropZone: document.getElementById("drop-zone"),
  dropZoneHint: document.getElementById("drop-zone-hint"),
  fileInput: document.getElementById("file-input"),
  candidatesList: document.getElementById("candidates-list"),
  candidatesCount: document.getElementById("candidates-count"),
  candidatesEmpty: document.getElementById("candidates-empty"),
  candidatesStatus: document.getElementById("candidates-status"),
  candidatesError: document.getElementById("candidates-error"),
  candidatesDisclaimer: document.getElementById("candidates-disclaimer"),
  btnSuggestClips: document.getElementById("btn-suggest-clips"),
  metadataDuration: document.getElementById("metadata-duration"),
  metadataResolution: document.getElementById("metadata-resolution"),
  metadataFps: document.getElementById("metadata-fps"),
  metadataSize: document.getElementById("metadata-size"),
  selectedClipName: document.getElementById("selected-clip-name"),
  clipStart: document.getElementById("clip-start"),
  clipEnd: document.getElementById("clip-end"),
  clipDuration: document.getElementById("clip-duration"),
  timelineRuler: document.getElementById("timeline-ruler"),
  timelineTracks: document.getElementById("timeline-tracks"),
  captionStyle: document.getElementById("caption-style"),
  fontSize: document.getElementById("font-size"),
  captionPlacement: document.getElementById("caption-placement"),
  highlightMode: document.getElementById("highlight-mode"),
  lineLength: document.getElementById("line-length"),
  highContrast: document.getElementById("high-contrast"),
  captionPreview: document.getElementById("caption-preview"),
  captionPreviewStatus: document.getElementById("caption-preview-status"),
  aspectRatio: document.getElementById("aspect-ratio"),
  outputResolution: document.getElementById("output-resolution"),
  exportFormat: document.getElementById("export-format"),
  exportCaptionMode: document.getElementById("export-caption-mode"),
  exportWriteSrt: document.getElementById("export-write-srt"),
  btnExportCancel: document.getElementById("btn-export-cancel"),
  exportProgress: document.getElementById("export-progress"),
  exportProgressFill: document.getElementById("export-progress-fill"),
  exportStatus: document.getElementById("export-status"),
  exportError: document.getElementById("export-error"),
  exportNotes: document.getElementById("export-notes"),
  exportResult: document.getElementById("export-result"),
  exportResultBadge: document.getElementById("export-result-badge"),
  exportVideo: document.getElementById("export-video"),
  exportResultSummary: document.getElementById("export-result-summary"),
  exportResultNotes: document.getElementById("export-result-notes"),
  exportDownload: document.getElementById("export-download"),
  exportSrtLink: document.getElementById("export-srt-link"),
};

/** Every control that shapes a render, so one loop can disable them together. */
const RENDER_CONTROLS = [
  "aspectRatio",
  "outputResolution",
  "exportFormat",
  "exportCaptionMode",
  "exportWriteSrt",
  "captionStyle",
  "fontSize",
  "captionPlacement",
  "highlightMode",
  "lineLength",
  "highContrast",
];

const state = {
  project: { ...EMPTY_PROJECT },
  selectedId: null,
  objectUrl: null,
  demoRunning: false,
  busy: false,
  projects: [],
  transcript: {
    document: null,
    segments: [],
    jobId: null,
    timer: null,
    failed: false,
    lastMessage: "",
  },
  clips: {
    analysis: null,
    stale: false,
    busy: false,
  },
  captions: {
    clipId: null,
    busy: false,
  },
  export: {
    jobId: null,
    timer: null,
    busy: false,
    result: null,
  },
};

/* ----------------------------------------------------------- formatting */

function formatTimecode(seconds) {
  const safe = Math.max(0, Math.round(seconds || 0));
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const secs = safe % 60;
  const pad = (value) => String(value).padStart(2, "0");
  return hours > 0 ? `${hours}:${pad(minutes)}:${pad(secs)}` : `${pad(minutes)}:${pad(secs)}`;
}

function formatSize(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(unit >= 2 ? 1 : 0)} ${units[unit]}`;
}

/** Read the backend's structured error envelope, falling back to a plain message. */
function apiErrorMessage(payload, fallback) {
  return payload?.error?.message || fallback;
}

/** True when the open project is a real imported one that has a source file. */
function isRealProject() {
  return Boolean(state.project.id) && state.project.id !== DEMO_PROJECT.id;
}

/* ---------------------------------------------------------------- health */

async function checkHealth() {
  try {
    const response = await fetch("/api/health", { headers: { Accept: "application/json" } });
    if (!response.ok) {
      throw new Error(`Backend returned HTTP ${response.status}`);
    }
    const data = await response.json();
    els.healthStatus.textContent = "Backend ready";
    els.healthStatus.className = "status status-ready";
    els.healthMessage.textContent = `${data.service} is running locally. Workspace: ${data.workspace}.`;
  } catch (error) {
    els.healthStatus.textContent = "Backend unavailable";
    els.healthStatus.className = "status status-error";
    els.healthMessage.textContent =
      "The local backend could not be reached. Start it with the command in README.md.";
    console.error(error);
  }
}

/* --------------------------------------------------------- project list */

function renderProjectList() {
  els.projectList.replaceChildren();

  for (const project of state.projects) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "list__item";
    if (project.project_id === state.project.id) button.classList.add("is-active");

    const name = document.createElement("span");
    name.textContent = project.name || project.project_id;
    const meta = document.createElement("span");
    meta.className = "list__meta";
    const parts = [];
    if (project.duration) parts.push(formatTimecode(project.duration));
    parts.push(project.has_transcript ? "transcribed" : "not transcribed");
    meta.textContent = parts.join(" · ");

    button.append(name, meta);
    button.addEventListener("click", () => openProject(project.project_id));
    item.append(button);
    els.projectList.append(item);
  }

  const demoItem = document.createElement("li");
  const demoButton = document.createElement("button");
  demoButton.type = "button";
  demoButton.className = "list__item";
  if (state.project.id === DEMO_PROJECT.id) demoButton.classList.add("is-active");
  const demoName = document.createElement("span");
  demoName.textContent = DEMO_PROJECT.name;
  const demoMeta = document.createElement("span");
  demoMeta.className = "list__meta";
  demoMeta.textContent = "Sample";
  demoButton.append(demoName, demoMeta);
  demoButton.addEventListener("click", loadDemoProject);
  demoItem.append(demoButton);
  els.projectList.append(demoItem);

  if (state.projects.length === 0) {
    const emptyItem = document.createElement("li");
    const hint = document.createElement("p");
    hint.className = "panel__hint";
    hint.textContent = "No imported projects yet. Use Import video or the drop zone.";
    emptyItem.append(hint);
    els.projectList.append(emptyItem);
  }
}

async function fetchProjects() {
  try {
    const response = await fetch("/api/projects", { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    state.projects = Array.isArray(data.projects) ? data.projects : [];
  } catch (error) {
    console.warn("Could not list projects:", error);
    state.projects = [];
  }
  renderProjectList();
  return state.projects;
}

function renderAssetList() {
  els.assetList.replaceChildren();
  for (const label of ["B-roll clips", "Sound effects", "Music"]) {
    const item = document.createElement("li");
    const row = document.createElement("div");
    row.className = "list__item";
    const name = document.createElement("span");
    name.textContent = label;
    const meta = document.createElement("span");
    meta.className = "list__meta";
    meta.textContent = "0 files";
    row.append(name, meta);
    item.append(row);
    els.assetList.append(item);
  }
}

/* ------------------------------------------------------------- rendering */

function renderProject() {
  const { project } = state;
  els.projectName.textContent = project.name;
  els.projectId.textContent = project.id || "—";
  els.projectStatus.textContent = project.status;

  renderMetadata(project.source);
  renderCandidates();
  renderTimeline();
  updateTranscribeControls();
  updateSuggestControls();
  updateExportControls();
}

function renderMetadata(source) {
  if (!source) {
    els.metadataDuration.textContent = "—";
    els.metadataResolution.textContent = "—";
    els.metadataFps.textContent = "—";
    els.metadataSize.textContent = "—";
    return;
  }
  els.metadataDuration.textContent = formatTimecode(source.duration);
  els.metadataResolution.textContent = `${source.width} × ${source.height}`;
  els.metadataFps.textContent = source.fps ? `${source.fps} fps` : "unknown";
  els.metadataSize.textContent = formatSize(source.sizeBytes);
}

function renderCandidates() {
  const { candidates } = state.project;
  els.candidatesList.replaceChildren();
  els.candidatesCount.textContent = String(candidates.length);
  els.candidatesEmpty.classList.toggle("is-hidden", candidates.length > 0);

  for (const candidate of candidates) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "candidate";
    button.dataset.id = candidate.id;
    if (candidate.id === state.selectedId) button.classList.add("is-active");

    const top = document.createElement("div");
    top.className = "candidate__top";
    const title = document.createElement("span");
    title.className = "candidate__title";
    title.textContent = candidate.title;
    const score = document.createElement("span");
    score.className = "candidate__score";
    const basis = candidate.scoreLabel === "llm" ? "LLM estimate" : "estimate";
    score.textContent = `Score ${Number(candidate.score || 0).toFixed(2)} (${basis})`;
    top.append(title, score);

    const reason = document.createElement("p");
    reason.className = "candidate__reason";
    reason.textContent = candidate.reason;

    const footer = document.createElement("div");
    footer.className = "candidate__footer";
    const range = document.createElement("span");
    range.textContent = `${formatTimecode(candidate.start)} – ${formatTimecode(candidate.end)}`;
    const length = document.createElement("span");
    length.textContent = `${(candidate.end - candidate.start).toFixed(1)}s`;
    footer.append(range, length);

    const flags = document.createElement("div");
    flags.className = "candidate__flags";

    if (candidate.edited) {
      const tag = document.createElement("span");
      tag.className = "tag tag--edited";
      tag.textContent = "edited";
      flags.append(tag);
    }

    // Older demo data uses a single overlapWith id; real data uses a list.
    const overlaps = candidate.overlapsWith || (candidate.overlapWith ? [candidate.overlapWith] : []);
    if (overlaps.length > 0) {
      const tag = document.createElement("span");
      tag.className = "tag tag--overlap";
      tag.textContent = `overlaps ${overlaps.join(", ")}`;
      flags.append(tag);
    }
    if (candidate.nearDuplicateOf) {
      const tag = document.createElement("span");
      tag.className = "tag tag--overlap";
      tag.textContent = `near-duplicate of ${candidate.nearDuplicateOf}`;
      flags.append(tag);
    }
    if (flags.childElementCount > 0) footer.append(flags);

    button.append(top, reason, footer);
    button.addEventListener("click", () => selectCandidate(candidate.id));
    item.append(button);
    els.candidatesList.append(item);
  }
}

function selectCandidate(id) {
  state.selectedId = id;
  const candidate = state.project.candidates.find((item) => item.id === id) || null;

  for (const button of els.candidatesList.querySelectorAll(".candidate")) {
    button.classList.toggle("is-active", button.dataset.id === id);
  }

  if (!candidate) {
    els.selectedClipName.textContent = "None";
    els.previewClipLabel.textContent = "No clip selected";
    els.clipStart.value = "0";
    els.clipEnd.value = "0";
  } else {
    els.selectedClipName.textContent = candidate.title;
    els.previewClipLabel.textContent = `${candidate.title} · ${(candidate.end - candidate.start).toFixed(1)}s`;
    els.clipStart.value = candidate.start.toFixed(1);
    els.clipEnd.value = candidate.end.toFixed(1);
    if (els.video.src) {
      els.video.currentTime = candidate.start;
    }
  }

  updateClipDuration();
  renderTimeline();
  updateExportControls();
  // Captions belong to the selected clip, so they follow the selection.
  loadCaptionPreview();
}

/* --------------------------------------------------------- clip inspector */

function updateClipDuration() {
  const start = Number.parseFloat(els.clipStart.value) || 0;
  const end = Number.parseFloat(els.clipEnd.value) || 0;
  const duration = Math.max(0, end - start);
  els.clipDuration.textContent = `${duration.toFixed(1)}s`;
}

async function applyClipEdit() {
  const candidate = state.project.candidates.find((item) => item.id === state.selectedId);
  if (!candidate) return;
  candidate.start = Math.max(0, Number.parseFloat(els.clipStart.value) || 0);
  candidate.end = Math.max(candidate.start, Number.parseFloat(els.clipEnd.value) || 0);
  updateClipDuration();
  renderCandidates();
  renderTimeline();
  await persistClipEdits(candidate);
}

els.clipStart.addEventListener("input", updateClipDuration);
els.clipEnd.addEventListener("input", updateClipDuration);
els.clipStart.addEventListener("change", applyClipEdit);
els.clipEnd.addEventListener("change", applyClipEdit);

/* ------------------------------------------------------------- timeline */

function renderTimeline() {
  const source = state.project.source;
  if (!source) {
    els.timelineRuler.replaceChildren();
    els.timelineTracks.replaceChildren();
    return;
  }

  const duration = source.duration;
  const pct = (seconds) => `${(seconds / duration) * 100}%`;

  els.timelineRuler.replaceChildren();
  const tickCount = 6;
  for (let i = 0; i <= tickCount; i += 1) {
    const tick = document.createElement("span");
    tick.className = "timeline__tick";
    tick.style.left = `${(i / tickCount) * 100}%`;
    tick.textContent = formatTimecode((i / tickCount) * duration);
    els.timelineRuler.append(tick);
  }

  const selected = state.project.candidates.find((item) => item.id === state.selectedId) || null;

  const tracks = [
    { kind: "video", block: { start: 0, end: duration } },
    { kind: "caption", block: selected ? { start: selected.start, end: selected.end } : null },
    { kind: "broll", block: null },
    { kind: "sfx", block: null },
  ];

  els.timelineTracks.replaceChildren();
  for (const track of tracks) {
    const row = document.createElement("div");
    row.className = "timeline__track";
    row.title = `${track.kind[0].toUpperCase()}${track.kind.slice(1)} track`;

    if (track.block) {
      const clip = document.createElement("div");
      clip.className = `timeline__clip timeline__clip--${track.kind}`;
      clip.style.left = pct(track.block.start);
      clip.style.width = pct(track.block.end - track.block.start);
      row.append(clip);
    }

    if (track.kind === "video" && selected) {
      const playhead = document.createElement("div");
      playhead.className = "timeline__playhead";
      playhead.style.left = pct(selected.start);
      row.append(playhead);
    }

    els.timelineTracks.append(row);
  }
}

/* -------------------------------------------------------- preview handling */

function resetPreview() {
  if (state.objectUrl) {
    URL.revokeObjectURL(state.objectUrl);
    state.objectUrl = null;
  }
  els.video.pause();
  els.video.removeAttribute("src");
  els.video.classList.add("is-hidden");
  els.previewEmpty.classList.remove("is-hidden");
  els.previewCurrentTime.textContent = "00:00";
  els.previewDuration.textContent = "00:00";
}

function setPreviewSource(url, { objectUrl = false } = {}) {
  resetPreview();
  if (objectUrl) state.objectUrl = url;
  els.video.src = url;
  els.video.classList.remove("is-hidden");
  els.previewEmpty.classList.add("is-hidden");
}

els.video.addEventListener("timeupdate", () => {
  els.previewCurrentTime.textContent = formatTimecode(els.video.currentTime);
  els.previewDuration.textContent = formatTimecode(els.video.duration || 0);
});

els.video.addEventListener("error", () => {
  if (!els.video.currentSrc) return;
  showError("This browser could not play the source video. The file is still saved to the project.");
});

/* -------------------------------------------------------- transcription */

function setTranscribeProgress(percent) {
  const value = Math.min(100, Math.max(0, Number(percent) || 0));
  els.transcribeProgressFill.style.width = `${value}%`;
  els.transcribeProgress.setAttribute("aria-valuenow", String(Math.round(value)));
}

function setTranscriptBadge(text, className) {
  els.transcriptBadge.textContent = text;
  els.transcriptBadge.className = className;
}

function showTranscriptError(message) {
  els.transcriptError.textContent = message;
  els.transcriptError.classList.remove("is-hidden");
}

function hideTranscriptError() {
  els.transcriptError.textContent = "";
  els.transcriptError.classList.add("is-hidden");
}

/**
 * Enable transcription only for a real imported project, and only when no job is
 * already running. Every disabled state has a reason in the button's title.
 */
function updateTranscribeControls() {
  const real = isRealProject();
  const running = Boolean(state.transcript.jobId);

  els.btnTranscribe.disabled = !real || running || state.busy;
  els.btnTranscribe.title = real
    ? "Transcribe the source audio locally with faster-whisper."
    : "Import a video first — transcription runs on the imported source.";

  els.btnTranscribeCancel.classList.toggle("is-hidden", !running);

  const canRetry =
    real && !running && state.transcript.failed && state.transcript.segments.length === 0;
  els.btnTranscribeRetry.classList.toggle("is-hidden", !canRetry);
}

/** Clear the transcript panel to its "nothing transcribed yet" state. */
function resetTranscript(
  statusText = "Import a video, then transcribe it locally.",
  badgeText = "Not transcribed"
) {
  clearTimeout(state.transcript.timer);
  state.transcript.timer = null;
  state.transcript.jobId = null;
  state.transcript.failed = false;
  state.transcript.document = null;
  state.transcript.segments = [];
  state.transcript.lastMessage = "";

  els.transcriptList.replaceChildren();
  els.transcriptSummary.textContent = "";
  els.transcriptEmpty.textContent = "No transcript yet.";
  els.transcriptEmpty.classList.remove("is-hidden");
  els.transcriptStatus.textContent = statusText;
  setTranscriptBadge(badgeText, "badge badge-muted");
  setTranscribeProgress(0);
  hideTranscriptError();
  updateTranscribeControls();
}

/** Render a saved transcript document and describe how it was produced. */
function renderTranscript(document_) {
  const segments = Array.isArray(document_?.segments) ? document_.segments : [];
  state.transcript.document = document_ || null;
  state.transcript.segments = segments;
  state.transcript.failed = false;
  state.transcript.lastMessage = "";

  els.transcriptList.replaceChildren();

  for (const [index, segment] of segments.entries()) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "transcript__item";
    button.dataset.index = String(index);

    const time = document.createElement("span");
    time.className = "transcript__time";
    time.textContent = formatTimecode(segment.start);

    const text = document.createElement("span");
    text.className = "transcript__text";
    text.textContent = segment.text || "(no speech)";

    button.append(time, text);
    button.addEventListener("click", () => seekToSegment(index));
    item.append(button);
    els.transcriptList.append(item);
  }

  const empty = segments.length === 0;
  els.transcriptEmpty.classList.toggle("is-hidden", !empty);
  if (empty) {
    els.transcriptEmpty.textContent =
      "The model found no speech in this file. Check that the video has an audible audio track.";
  }

  const bits = [document_?.model, document_?.language, `${segments.length} segments`];
  if (document_?.device) bits.push(String(document_.device).toUpperCase());
  if (document_?.elapsed_seconds) bits.push(`${Number(document_.elapsed_seconds).toFixed(1)}s`);
  if (document_?.has_word_timestamps) bits.push("word timings");
  els.transcriptSummary.textContent = bits.filter(Boolean).join(" · ");

  const notes = Array.isArray(document_?.notes) ? document_.notes : [];
  els.transcriptStatus.textContent = notes.length
    ? notes.join(" ")
    : `Saved transcript for ${segments.length} segments.`;

  setTranscriptBadge(`${segments.length} segments`, "badge status-ready");
  setTranscribeProgress(100);
  hideTranscriptError();
  updateTranscribeControls();
}

/** Jump the preview to a transcript segment so timings can be checked by ear. */
function seekToSegment(index) {
  const segment = state.transcript.segments[index];
  if (!segment) return;

  for (const button of els.transcriptList.querySelectorAll(".transcript__item")) {
    button.classList.toggle("is-active", button.dataset.index === String(index));
  }

  if (els.video.src) {
    els.video.currentTime = Number(segment.start) || 0;
  }
}

/** Load a saved transcript, returning null when there is nothing to show yet. */
async function loadTranscript(projectId) {
  try {
    const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/transcript`, {
      headers: { Accept: "application/json" },
    });

    // Both "project not found" and "no transcript yet" mean the same thing here.
    if (response.status === 404) {
      resetTranscript("This project has not been transcribed yet.");
      return null;
    }

    const document_ = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(
        apiErrorMessage(document_, `Could not read the transcript (HTTP ${response.status}).`)
      );
    }
    return document_;
  } catch (error) {
    resetTranscript("The saved transcript could not be read.");
    showTranscriptError(error.message);
    return null;
  }
}

async function startTranscription() {
  const projectId = state.project.id;
  if (!projectId || state.transcript.jobId) return;

  hideTranscriptError();
  state.transcript.failed = false;
  state.transcript.lastMessage = "";
  setTranscriptBadge("Starting…", "badge badge-demo");
  els.transcriptStatus.textContent = "Starting local transcription…";
  setTranscribeProgress(0);
  els.processingLog.textContent = "";
  log(`Starting local transcription for ${projectId}.`);

  try {
    const response = await fetch(
      `/api/projects/${encodeURIComponent(projectId)}/transcribe`,
      { method: "POST" }
    );
    const job = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(
        apiErrorMessage(job, `Could not start transcription (HTTP ${response.status}).`)
      );
    }

    state.transcript.jobId = job.job_id;
    updateTranscribeControls();
    pollTranscription(job.job_id, projectId);
  } catch (error) {
    state.transcript.failed = true;
    setTranscriptBadge("Failed", "badge status-error");
    els.transcriptStatus.textContent = "Transcription could not start.";
    showTranscriptError(error.message);
    updateTranscribeControls();
  }
}

/**
 * Poll a transcription job until it finishes.
 *
 * If the user opens another project while the job runs, progress keeps being
 * fetched but is not painted onto the wrong project's panel.
 */
async function pollTranscription(jobId, projectId) {
  const stillOpen = state.project.id === projectId;

  try {
    const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`, {
      headers: { Accept: "application/json" },
    });
    const job = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(
        apiErrorMessage(job, `Lost track of the transcription job (HTTP ${response.status}).`)
      );
    }

    if (stillOpen) {
      setTranscribeProgress(job.progress);
      els.transcriptStatus.textContent = job.message || job.status;
      if (job.message && job.message !== state.transcript.lastMessage) {
        state.transcript.lastMessage = job.message;
        log(`Transcription ${Math.round(Number(job.progress) || 0)}% — ${job.message}`);
      }
    }

    if (job.status === "succeeded") {
      await onTranscriptionSucceeded(job, projectId, stillOpen);
      return;
    }
    if (job.status === "failed") {
      onTranscriptionFailed(job.error?.message || "Transcription failed.", stillOpen);
      return;
    }
    if (job.status === "cancelled") {
      onTranscriptionCancelled(stillOpen);
      return;
    }

    state.transcript.timer = setTimeout(
      () => pollTranscription(jobId, projectId),
      TRANSCRIBE_POLL_MS
    );
  } catch (error) {
    onTranscriptionFailed(error.message, stillOpen);
  }
}

async function onTranscriptionSucceeded(job, projectId, stillOpen) {
  state.transcript.jobId = null;
  clearTimeout(state.transcript.timer);
  state.transcript.timer = null;

  const result = job.result || {};
  log(
    `Transcript saved: ${result.segment_count ?? 0} segments, language ${result.language}, ` +
      `${result.model} on ${result.device}.`
  );

  if (!stillOpen) {
    updateTranscribeControls();
    await fetchProjects();
    return;
  }

  setTranscriptBadge("Transcribed", "badge status-ready");
  // A fresh transcript enables clip suggestion and makes old clips stale.
  state.project.hasTranscript = true;
  if (state.project.candidates.length === 0) {
    els.candidatesStatus.textContent =
      "Transcript ready — press Suggest clips to find candidate moments.";
  }
  const document_ = await loadTranscript(projectId);
  if (document_) {
    renderTranscript(document_);
  }
  updateTranscribeControls();
  updateSuggestControls();
  await fetchProjects();
}

function onTranscriptionFailed(message, stillOpen = true) {
  state.transcript.jobId = null;
  state.transcript.failed = true;
  clearTimeout(state.transcript.timer);
  state.transcript.timer = null;
  if (!stillOpen) return;

  setTranscriptBadge("Failed", "badge status-error");
  els.transcriptStatus.textContent = "Transcription failed.";
  showTranscriptError(message);
  log(`Error: ${message}`);
  updateTranscribeControls();
}

function onTranscriptionCancelled(stillOpen = true) {
  state.transcript.jobId = null;
  state.transcript.failed = true;
  clearTimeout(state.transcript.timer);
  state.transcript.timer = null;
  if (!stillOpen) return;

  setTranscriptBadge("Cancelled", "badge badge-muted");
  els.transcriptStatus.textContent = "Transcription cancelled. Retry when ready.";
  setTranscribeProgress(0);
  log("Transcription cancelled.");
  updateTranscribeControls();
}

async function cancelTranscription() {
  const jobId = state.transcript.jobId;
  if (!jobId) return;

  els.btnTranscribeCancel.disabled = true;
  try {
    const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {
      method: "POST",
    });
    const job = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(apiErrorMessage(job, `Could not cancel (HTTP ${response.status}).`));
    }
    els.transcriptStatus.textContent = "Cancelling…";
  } catch (error) {
    showTranscriptError(error.message);
  } finally {
    els.btnTranscribeCancel.disabled = false;
  }
}

/* --------------------------------------------------------- clip candidates */

/** Map a clip from the API into the shape the interface renders. */
function candidateFromServer(clip, index = 0) {
  return {
    id: clip.id || `clip-${index + 1}`,
    title: clip.title || `Clip ${index + 1}`,
    reason: clip.reason || "",
    start: Number(clip.source_start) || 0,
    end: Number(clip.source_end) || 0,
    score: Number(clip.score) || 0,
    scoreLabel: clip.score_label || "estimate",
    overlapsWith: Array.isArray(clip.overlaps_with) ? clip.overlaps_with : [],
    nearDuplicateOf: clip.near_duplicate_of || null,
    edited: Boolean(clip.edited),
  };
}

/** Clips suggested from an older transcript are out of date. */
function clipAnalysisIsStale(payload) {
  const basedOn = payload?.clip_analysis?.based_on_transcript_generated_at;
  const current = payload?.transcript?.generated_at;
  return Boolean(basedOn && current && basedOn !== current);
}

function showCandidateError(message) {
  els.candidatesError.textContent = message;
  els.candidatesError.classList.remove("is-hidden");
}

function hideCandidateError() {
  els.candidatesError.textContent = "";
  els.candidatesError.classList.add("is-hidden");
}

/** Describe how the current candidates were produced, and how much to trust them. */
function applyClipAnalysis(analysis, stale) {
  state.clips.analysis = analysis || null;
  state.clips.stale = Boolean(stale);

  if (!analysis) {
    els.candidatesDisclaimer.textContent = "";
    return;
  }

  const source = analysis.generator === "llm" ? "an optional LLM pass" : "a transparent heuristic";
  const parts = [
    `Suggested by ${source}.`,
    analysis.score_meaning || "",
    ...(analysis.notes || []),
  ];
  if (stale) {
    parts.push("These are stale: the transcript changed since they were suggested, so re-run it.");
  }
  els.candidatesDisclaimer.textContent = parts.filter(Boolean).join(" ");
}

/** Suggestions need a real project that already has a transcript. */
function updateSuggestControls() {
  const real = isRealProject();
  const hasTranscript = real && Boolean(state.project.hasTranscript);

  els.btnSuggestClips.disabled = !hasTranscript || state.clips.busy;
  els.btnSuggestClips.title = !real
    ? "Import a video first — suggestions come from its transcript."
    : hasTranscript
      ? "Detect candidate clips from the saved transcript."
      : "Transcribe this project first — suggestions come from the transcript.";
}

async function suggestClips() {
  const projectId = state.project.id;
  if (!projectId || state.clips.busy) return;

  state.clips.busy = true;
  updateSuggestControls();
  hideCandidateError();
  els.candidatesStatus.textContent = "Analysing the transcript for strong moments…";

  try {
    const response = await fetch(
      `/api/projects/${encodeURIComponent(projectId)}/suggest-clips`,
      { method: "POST" }
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(
        apiErrorMessage(payload, `Could not suggest clips (HTTP ${response.status}).`)
      );
    }

    if (state.project.id !== projectId) return;

    state.project.candidates = (payload.clips || []).map(candidateFromServer);
    state.selectedId = null;
    applyClipAnalysis(payload.analysis, false);
    renderCandidates();
    selectCandidate(null);
    log(`Suggested ${state.project.candidates.length} candidate clips.`);
  } catch (error) {
    showCandidateError(error.message);
    els.candidatesStatus.textContent = "Clip suggestion failed.";
    log(`Error: ${error.message}`);
  } finally {
    state.clips.busy = false;
    updateSuggestControls();
  }
}

/** Save a clip's new in/out points. The backend owns the validation. */
async function persistClipEdits(candidate) {
  if (!isRealProject()) {
    els.candidatesStatus.textContent = "Sample data — edits to the sample project are not saved.";
    return;
  }

  try {
    const response = await fetch(`/api/projects/${encodeURIComponent(state.project.id)}/clips`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify({
        clips: [
          { id: candidate.id, source_start: candidate.start, source_end: candidate.end },
        ],
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(
        apiErrorMessage(payload, `Could not save the clip (HTTP ${response.status}).`)
      );
    }

    hideCandidateError();
    // Adopt the values the backend validated rather than trusting local numbers.
    const saved = (payload.clips || []).find((clip) => clip.id === candidate.id);
    if (saved) Object.assign(candidate, candidateFromServer(saved));
    renderCandidates();
    log(`Saved clip ${candidate.id}: ${candidate.start.toFixed(1)}s – ${candidate.end.toFixed(1)}s`);
  } catch (error) {
    showCandidateError(error.message);
    log(`Error: ${error.message}`);
  }
}

/* --------------------------------------------------- captions and export */

/** The caption choices currently in the inspector. */
function captionStyleFromControls() {
  return {
    preset: els.captionStyle.value,
    font_size: els.fontSize.value,
    placement: els.captionPlacement.value,
    highlight_mode: els.highlightMode.value,
    line_length: els.lineLength.value,
    high_contrast: els.highContrast.checked,
  };
}

/** The export choices currently in the inspector. */
function exportSettingsFromControls() {
  return {
    aspect_ratio: els.aspectRatio.value,
    resolution: els.outputResolution.value,
    format: els.exportFormat.value,
    caption_mode: els.exportCaptionMode.value,
    write_srt: els.exportWriteSrt.checked,
  };
}

function applyStyleToControls(style) {
  const value = style || {};
  if (value.preset) els.captionStyle.value = value.preset;
  if (value.font_size) els.fontSize.value = value.font_size;
  if (value.placement) els.captionPlacement.value = value.placement;
  if (value.highlight_mode) els.highlightMode.value = value.highlight_mode;
  if (value.line_length) els.lineLength.value = value.line_length;
  els.highContrast.checked = Boolean(value.high_contrast);
}

function applyExportSettingsToControls(settings) {
  const value = settings || {};
  if (value.aspect_ratio) els.aspectRatio.value = value.aspect_ratio;
  if (value.format) els.exportFormat.value = value.format;
  if (value.caption_mode) els.exportCaptionMode.value = value.caption_mode;
  if (typeof value.write_srt === "boolean") els.exportWriteSrt.checked = value.write_srt;
  syncResolutionOptions(value.resolution);
}

/**
 * Only offer resolutions that belong to the chosen aspect ratio.
 *
 * A 1920 × 1080 size on a 9:16 export would be stretched, which is never what
 * anyone means, so the mismatch is prevented here and rejected by the backend.
 */
function syncResolutionOptions(preferred) {
  const aspect = els.aspectRatio.value;
  let firstMatch = null;

  for (const option of els.outputResolution.options) {
    const matches = !option.dataset.aspect || option.dataset.aspect === aspect;
    option.disabled = !matches;
    option.hidden = !matches;
    if (matches && !firstMatch) firstMatch = option;
  }

  if (preferred) {
    const wanted = Array.from(els.outputResolution.options).find(
      (option) => option.value === preferred && !option.disabled
    );
    if (wanted) {
      els.outputResolution.value = wanted.value;
      return;
    }
  }

  const current = els.outputResolution.selectedOptions[0];
  if ((!current || current.disabled) && firstMatch) {
    els.outputResolution.value = firstMatch.value;
  }
}

function showExportError(message) {
  els.exportError.textContent = message;
  els.exportError.classList.remove("is-hidden");
}

function hideExportError() {
  els.exportError.textContent = "";
  els.exportError.classList.add("is-hidden");
}

function setExportProgress(percent) {
  const value = Math.min(100, Math.max(0, Number(percent) || 0));
  els.exportProgressFill.style.width = `${value}%`;
  els.exportProgress.setAttribute("aria-valuenow", String(Math.round(value)));
}

/** Render the caption cues of the selected clip, which is what the export burns in. */
function renderCaptionPreview(payload, message) {
  els.captionPreview.replaceChildren();

  if (!payload) {
    els.captionPreviewStatus.textContent = message || "Select a clip to preview its captions.";
    return;
  }

  const cues = Array.isArray(payload.cues) ? payload.cues : [];
  if (cues.length === 0) {
    els.captionPreviewStatus.textContent =
      payload.summary?.note ||
      "This clip has no transcribed speech, so a captioned export would show no captions.";
    return;
  }

  const geometry = payload.geometry || {};
  const bits = [`${cues.length} caption(s)`];
  if (geometry.max_line_chars) bits.push(`up to ${geometry.max_line_chars} characters per line`);
  if (geometry.font_size) bits.push(`${geometry.font_size}px text`);
  els.captionPreviewStatus.textContent = `${bits.join(" · ")}.`;

  for (const cue of cues) {
    const item = document.createElement("li");
    item.className = "caption-preview__item";

    const time = document.createElement("span");
    time.className = "caption-preview__time";
    // Clip-relative seconds, so the first cue visibly starts at 0.0s.
    time.textContent = `${Number(cue.start).toFixed(1)}s → ${Number(cue.end).toFixed(1)}s`;

    const lines = document.createElement("span");
    lines.className = "caption-preview__lines";
    for (const line of cue.lines || [cue.text]) {
      const span = document.createElement("span");
      span.textContent = line;
      lines.append(span);
    }

    item.append(time, lines);
    els.captionPreview.append(item);
  }
}

async function loadCaptionPreview() {
  const clipId = state.selectedId;

  if (!isRealProject()) {
    renderCaptionPreview(
      null,
      "Import a video, transcribe it, and suggest clips to preview captions."
    );
    return;
  }

  if (!clipId) {
    renderCaptionPreview(null, "Select a clip to preview its captions.");
    return;
  }

  if (state.captions.busy) return;
  state.captions.busy = true;
  els.captionPreviewStatus.textContent = "Building captions from the saved transcript…";

  try {
    const response = await fetch(
      `/api/projects/${encodeURIComponent(state.project.id)}/captions` +
        `?clip_id=${encodeURIComponent(clipId)}`,
      { headers: { Accept: "application/json" } }
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(
        apiErrorMessage(payload, `Could not build captions (HTTP ${response.status}).`)
      );
    }
    if (state.selectedId !== clipId) return;
    state.captions.clipId = clipId;
    renderCaptionPreview(payload);
  } catch (error) {
    renderCaptionPreview(null, error.message);
  } finally {
    state.captions.busy = false;
  }
}

/**
 * Save the caption and export settings the next render will use.
 *
 * The backend validates both halves and returns the plan, so the upscale and
 * 2K warnings shown here come from the same code that builds the FFmpeg command.
 */
async function saveProjectSettings({ reloadCaptions = false } = {}) {
  if (!isRealProject()) {
    if (reloadCaptions) renderCaptionPreview(null, "Sample project — settings are not saved.");
    return;
  }

  try {
    const response = await fetch(
      `/api/projects/${encodeURIComponent(state.project.id)}/settings`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          caption_style: captionStyleFromControls(),
          export: exportSettingsFromControls(),
        }),
      }
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(
        apiErrorMessage(payload, `Could not save the settings (HTTP ${response.status}).`)
      );
    }

    hideExportError();
    const notes = payload.plan?.notes || [];
    els.exportNotes.textContent = notes.join(" ");
    log(`Saved settings: ${els.captionStyle.value} captions, ${els.outputResolution.value}.`);

    if (reloadCaptions) await loadCaptionPreview();
  } catch (error) {
    showExportError(error.message);
  }
}

/** Export needs a real project and a selected clip; every disabled state is explained. */
function updateExportControls() {
  const real = isRealProject();
  const clip = state.project.candidates.find((item) => item.id === state.selectedId) || null;
  const running = Boolean(state.export.jobId);

  els.btnExport.disabled = !real || !clip || running || state.export.busy;
  els.btnExport.title = !real
    ? "Import a video first — the export renders an imported project's clip."
    : !clip
      ? "Select a clip first — the export renders the selected clip and its captions."
      : running
        ? "An export is already running for this project."
        : "Render the selected clip with captions into an MP4 file.";

  els.btnExportCancel.classList.toggle("is-hidden", !running);

  for (const name of RENDER_CONTROLS) {
    els[name].disabled = !real || running;
  }
}

function showExportResult(result, available = true) {
  if (!result) {
    els.exportResult.classList.add("is-hidden");
    return;
  }

  els.exportResult.classList.remove("is-hidden");
  els.exportResultBadge.textContent = available ? "Ready" : "File missing";
  els.exportResultBadge.className = available ? "badge status-ready" : "badge status-error";

  els.exportVideo.src = result.url;
  els.exportDownload.href = result.url;
  els.exportDownload.setAttribute("download", result.file || "export.mp4");
  els.exportDownload.textContent = available ? "Download MP4" : "Download MP4 (file missing)";

  if (result.srt_url) {
    els.exportSrtLink.href = result.srt_url;
    els.exportSrtLink.setAttribute("download", result.srt_file || "captions.srt");
    els.exportSrtLink.classList.remove("is-hidden");
  } else {
    els.exportSrtLink.classList.add("is-hidden");
  }

  const captions =
    result.caption_mode === "none"
      ? "captions off"
      : `${result.cue_count} caption(s), ${result.caption_preset} preset`;
  els.exportResultSummary.textContent = [
    result.clip_title ? `Clip: ${result.clip_title}` : null,
    `${result.width} × ${result.height}`,
    `${Number(result.duration).toFixed(1)}s`,
    formatSize(result.size_bytes),
    captions,
    result.render_seconds ? `rendered in ${Number(result.render_seconds).toFixed(1)}s` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  const notes = [...(result.notes || [])];
  if ((result.font_warnings || []).length > 0) {
    notes.push(
      "This machine was missing a font for some characters, so they may not appear " +
        "in the burned-in captions."
    );
  }
  els.exportResultNotes.textContent = notes.join(" ");
}

/** Show the newest render already saved for this project, if there is one. */
async function loadExports(projectId) {
  try {
    const response = await fetch(
      `/api/projects/${encodeURIComponent(projectId)}/exports`,
      { headers: { Accept: "application/json" } }
    );
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    if (state.project.id !== projectId) return;

    const latest = payload.latest;
    if (payload.export_settings) applyExportSettingsToControls(payload.export_settings);
    if (payload.caption_style) applyStyleToControls(payload.caption_style);

    if (payload.exports?.length) {
      const available = latest?.available !== false;
      els.exportStatus.textContent = available
        ? `${payload.exports.length} saved export(s) for this project.`
        : "The newest export's file is missing from the exports folder.";
      showExportResult(latest, available);
    } else {
      showExportResult(null);
      els.exportStatus.textContent =
        "Nothing rendered yet for this project. Select a clip, then export.";
    }
  } catch (error) {
    console.warn("Could not list exports:", error);
  }
}

async function startExport() {
  const projectId = state.project.id;
  const clipId = state.selectedId;
  if (!isRealProject() || !clipId || state.export.jobId) return;

  hideExportError();
  state.export.busy = true;
  setExportProgress(0);
  els.processingLog.textContent = "";
  els.exportStatus.textContent = "Starting the export…";
  log(
    `Exporting ${clipId} at ${els.outputResolution.value} with ` +
      `${els.exportCaptionMode.value === "burn" ? els.captionStyle.value + " captions" : "no captions"}.`
  );
  updateExportControls();

  try {
    const response = await fetch(
      `/api/projects/${encodeURIComponent(projectId)}/render`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          clip_id: clipId,
          caption_style: captionStyleFromControls(),
          export: exportSettingsFromControls(),
        }),
      }
    );
    const job = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(apiErrorMessage(job, `Could not start the export (HTTP ${response.status}).`));
    }

    state.export.jobId = job.job_id;
    updateExportControls();
    pollRender(job.job_id, projectId);
  } catch (error) {
    state.export.busy = false;
    showExportError(error.message);
    els.exportStatus.textContent = "The export could not start.";
    log(`Error: ${error.message}`);
    updateExportControls();
  }
}

async function pollRender(jobId, projectId) {
  const stillOpen = state.project.id === projectId;

  try {
    const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`, {
      headers: { Accept: "application/json" },
    });
    const job = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(apiErrorMessage(job, `Lost track of the export (HTTP ${response.status}).`));
    }

    if (stillOpen) {
      setExportProgress(job.progress);
      els.exportStatus.textContent = job.message || job.status;
    }

    if (job.status === "succeeded") {
      onRenderSucceeded(job, stillOpen);
      return;
    }
    if (job.status === "failed") {
      onRenderFinished("failed", job.error?.message || "The export failed.", stillOpen);
      return;
    }
    if (job.status === "cancelled") {
      onRenderFinished("cancelled", "Export cancelled. No file was kept.", stillOpen);
      return;
    }

    state.export.timer = setTimeout(() => pollRender(jobId, projectId), RENDER_POLL_MS);
  } catch (error) {
    onRenderFinished("failed", error.message, stillOpen);
  }
}

function onRenderSucceeded(job, stillOpen) {
  state.export.jobId = null;
  state.export.busy = false;
  clearTimeout(state.export.timer);
  state.export.timer = null;

  if (!stillOpen) {
    updateExportControls();
    return;
  }

  const result = job.result || null;
  state.export.result = result;
  setExportProgress(100);

  if (!result) {
    els.exportStatus.textContent =
      "The render finished but returned no details, so nothing is shown here.";
    updateExportControls();
    return;
  }

  els.exportStatus.textContent = `Exported ${result.file} (${formatSize(result.size_bytes)}).`;
  showExportResult(result, true);
  log(
    `Exported ${result.file}: ${result.width}×${result.height}, ${result.cue_count} caption(s), ` +
      `${Number(result.render_seconds || 0).toFixed(1)}s to render.`
  );
  updateExportControls();
}

function onRenderFinished(status, message, stillOpen) {
  state.export.jobId = null;
  state.export.busy = false;
  clearTimeout(state.export.timer);
  state.export.timer = null;
  if (!stillOpen) return;

  if (status === "cancelled") {
    els.exportStatus.textContent = message;
    setExportProgress(0);
  } else {
    showExportError(message);
    els.exportStatus.textContent = "The export did not finish.";
  }
  log(`Error: ${message}`);
  updateExportControls();
}

async function cancelExport() {
  const jobId = state.export.jobId;
  if (!jobId) return;

  els.btnExportCancel.disabled = true;
  try {
    const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {
      method: "POST",
    });
    const job = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(apiErrorMessage(job, `Could not cancel (HTTP ${response.status}).`));
    }
    els.exportStatus.textContent = "Cancelling…";
  } catch (error) {
    showExportError(error.message);
  } finally {
    els.btnExportCancel.disabled = false;
  }
}

/* -------------------------------------------------- projects from backend */

function projectFromServer(payload) {
  const metadata = payload.source_metadata || {};
  state.project = {
    id: payload.project_id,
    name: payload.name || payload.project_id,
    status: "Imported",
    source: {
      duration: Number(metadata.duration) || 0,
      width: Number(metadata.width) || 0,
      height: Number(metadata.height) || 0,
      fps: Number(metadata.fps) || 0,
      sizeBytes: Number(metadata.size_bytes) || 0,
    },
    candidates: (payload.clips || []).map(candidateFromServer),
    hasTranscript: Boolean(payload.transcript),
  };
  state.selectedId = null;
  applyClipAnalysis(payload.clip_analysis || null, clipAnalysisIsStale(payload));
  if ((payload.clips || []).length === 0) {
    els.candidatesStatus.textContent = payload.transcript
      ? "Transcript ready — press Suggest clips to find candidate moments."
      : "Transcribe this project to suggest clips.";
  } else {
    els.candidatesStatus.textContent = `${(payload.clips || []).length} saved candidate clip(s).`;
  }
  hideCandidateError();
  // Settings saved with the project are the ones the next render uses.
  applyStyleToControls(payload.caption_style);
  applyExportSettingsToControls(payload.export);
  showExportResult(null);
  els.exportNotes.textContent = "";
  els.exportStatus.textContent = "Loading saved exports…";

  els.dataSourceBadge.textContent = "Imported";
  els.dataSourceBadge.className = "badge badge-demo";
  els.dropZoneHint.textContent = `${state.project.name} · saved in the local workspace.`;
  els.previewEmptyHint.textContent = "Source video loaded from the local workspace.";

  resetTranscript("This project has not been transcribed yet.");
  renderProjectList();
  renderProject();
  setPreviewSource(`/api/projects/${payload.project_id}/source`);
  selectCandidate(null);

  // Restore an existing transcript without re-running the model.
  if (payload.transcript) {
    els.transcriptStatus.textContent = "Loading the saved transcript…";
    loadTranscript(payload.project_id).then((document_) => {
      if (document_ && state.project.id === payload.project_id) {
        renderTranscript(document_);
      }
    });
  }

  loadExports(payload.project_id);
}

async function openProject(projectId) {
  try {
    const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}`, {
      headers: { Accept: "application/json" },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(apiErrorMessage(payload, `Could not open the project (HTTP ${response.status}).`));
    }
    hideError();
    projectFromServer(payload);
    log(`Opened project ${projectId}`);
  } catch (error) {
    showError(error.message);
  }
}

function loadDemoProject() {
  state.project = structuredClone(DEMO_PROJECT);
  state.project.hasTranscript = false;
  state.selectedId = state.project.candidates[0]?.id ?? null;
  applyClipAnalysis(null, false);
  els.candidatesStatus.textContent = "Sample candidates — illustrative, not generated from a transcript.";
  hideCandidateError();
  els.dataSourceBadge.textContent = "Demo data";
  els.dataSourceBadge.className = "badge badge-demo";
  els.dropZoneHint.textContent =
    "Supported: MP4, MOV, M4V, AVI, MKV, WebM. Files stay on this machine.";
  els.previewEmptyHint.textContent =
    "Sample project loaded — import a video to preview real footage.";
  resetPreview();
  resetTranscript("Import a video to transcribe it — the sample project has no media.");
  showExportResult(null);
  els.exportNotes.textContent = "";
  els.exportStatus.textContent =
    "Nothing rendered yet. Rendering runs FFmpeg on this machine and writes an MP4 " +
    "into the project's exports folder.";
  renderCaptionPreview(null, "Sample project — import a video to preview real captions.");
  setExportProgress(0);
  hideExportError();
  renderProjectList();
  renderProject();
  selectCandidate(state.selectedId);
}

/* --------------------------------------------------------------- import */

function handleFile(file) {
  if (!file || state.busy) return;

  const extension = `.${file.name.split(".").pop().toLowerCase()}`;
  if (!SUPPORTED_EXTENSIONS.includes(extension)) {
    showProcessing("Import failed", INGEST_STEPS);
    markSteps(INGEST_STEPS, "failed", 0);
    setProgress(0);
    showError(`Unsupported file type "${extension || file.name}". Use MP4, MOV, M4V, AVI, MKV, or WebM.`);
    log(`Rejected ${file.name}`);
    return;
  }

  uploadFile(file);
}

async function uploadFile(file) {
  state.busy = true;
  els.btnImport.disabled = true;
  updateTranscribeControls();
  hideError();
  showProcessing(`Uploading ${file.name}…`, INGEST_STEPS);
  setProgress(10);
  log(`Uploading ${file.name} (${formatSize(file.size)})`);

  const formData = new FormData();
  formData.append("file", file);
  formData.append("name", file.name.replace(/\.[^.]+$/, ""));

  try {
    const response = await fetch("/api/projects", { method: "POST", body: formData });
    const payload = await response.json().catch(() => ({}));

    if (!response.ok) {
      markSteps(INGEST_STEPS, "failed", 2);
      throw new Error(apiErrorMessage(payload, `Upload failed (HTTP ${response.status}).`));
    }

    markSteps(INGEST_STEPS, "done");
    setProgress(100);
    els.processingMessage.textContent = `Imported and read metadata for ${file.name}.`;
    log(`Created project ${payload.project_id} in the local workspace.`);

    projectFromServer(payload);
    await fetchProjects();
  } catch (error) {
    setProgress(0);
    showError(error.message);
    log(`Error: ${error.message}`);
  } finally {
    state.busy = false;
    els.btnImport.disabled = false;
    els.fileInput.value = "";
    updateTranscribeControls();
  }
}

/* ------------------------------------------------------------ processing */

function setProgress(percent) {
  const value = Math.min(100, Math.max(0, percent));
  els.progressFill.style.width = `${value}%`;
  els.progressBar.setAttribute("aria-valuenow", String(Math.round(value)));
}

function log(message) {
  const stamp = new Date().toLocaleTimeString();
  els.processingLog.textContent += `${stamp} — ${message}\n`;
}

function showError(message) {
  els.processingError.textContent = message;
  els.processingError.classList.remove("is-hidden");
  els.processingPanel.classList.remove("is-hidden");
}

function hideError() {
  els.processingError.textContent = "";
  els.processingError.classList.add("is-hidden");
}

function renderSteps(steps) {
  els.processingSteps.replaceChildren();
  for (const step of steps) {
    const item = document.createElement("li");
    item.className = `steps__item steps__item--${step.status}`;
    const dot = document.createElement("span");
    dot.className = "steps__dot";
    dot.setAttribute("aria-hidden", "true");
    const label = document.createElement("span");
    label.textContent = step.label;
    const status = document.createElement("span");
    status.className = "steps__status";
    status.textContent = step.status;
    item.append(dot, label, status);
    els.processingSteps.append(item);
  }
}

function showProcessing(message, labels) {
  els.processingPanel.classList.remove("is-hidden");
  els.processingMessage.textContent = message;
  renderSteps(labels.map((label) => ({ label, status: "waiting" })));
}

function markSteps(labels, status, runningIndex = -1) {
  renderSteps(
    labels.map((label, index) => ({
      label,
      status: index === runningIndex ? "running" : status,
    }))
  );
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** Animate the future pipeline. Layout demonstration only — nothing is processed. */
async function runProcessingDemo() {
  if (state.demoRunning) return;
  state.demoRunning = true;
  els.processingLog.textContent = "";
  hideError();
  showProcessing("Layout demo running…", DEMO_STEPS);
  log("Demo only — no media is analysed or rendered.");

  const steps = DEMO_STEPS.map((label) => ({ label, status: "waiting" }));

  for (let index = 0; index < steps.length; index += 1) {
    steps[index].status = "running";
    renderSteps(steps);
    els.processingMessage.textContent = steps[index].label;
    setProgress((index / steps.length) * 100);
    log(`Running: ${steps[index].label}`);
    await sleep(420);
    steps[index].status = "done";
    renderSteps(steps);
  }

  setProgress(100);
  els.processingMessage.textContent = "Demo complete — real processing arrives in later milestones.";
  log("Demo complete.");
  state.demoRunning = false;
}

/* ------------------------------------------------------------ event wiring */

els.btnImport.addEventListener("click", () => {
  if (state.busy) return;
  els.fileInput.click();
});

els.fileInput.addEventListener("change", () => handleFile(els.fileInput.files?.[0]));

els.btnNewProject.addEventListener("click", async () => {
  resetToEmptyProject();
  await fetchProjects();
});

els.btnRefreshProjects.addEventListener("click", async () => {
  await fetchProjects();
  log("Project list refreshed.");
});

els.dropZone.addEventListener("click", () => {
  if (!state.busy) els.fileInput.click();
});

els.dropZone.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    if (!state.busy) els.fileInput.click();
  }
});

for (const type of ["dragenter", "dragover"]) {
  els.dropZone.addEventListener(type, (event) => {
    event.preventDefault();
    els.dropZone.classList.add("is-dragover");
  });
}

for (const type of ["dragleave", "drop"]) {
  els.dropZone.addEventListener(type, (event) => {
    event.preventDefault();
    els.dropZone.classList.remove("is-dragover");
  });
}

els.dropZone.addEventListener("drop", (event) => {
  handleFile(event.dataTransfer?.files?.[0]);
});

els.btnRunDemo.addEventListener("click", runProcessingDemo);

els.btnSuggestClips.addEventListener("click", suggestClips);

els.btnTranscribe.addEventListener("click", startTranscription);
els.btnTranscribeRetry.addEventListener("click", startTranscription);
els.btnTranscribeCancel.addEventListener("click", cancelTranscription);

els.btnExport.addEventListener("click", startExport);
els.btnExportCancel.addEventListener("click", cancelExport);

// Caption controls change the burned-in text, so the preview is rebuilt from the
// backend-validated cues instead of guessing locally what will be rendered.
for (const name of ["captionStyle", "fontSize", "captionPlacement", "highlightMode", "lineLength", "highContrast"]) {
  els[name].addEventListener("change", async () => {
    if (!isRealProject()) {
      renderCaptionPreview(null, "Sample project — settings are not saved.");
      return;
    }
    await saveProjectSettings({ reloadCaptions: true });
  });
}

els.aspectRatio.addEventListener("change", async () => {
  // Keep the resolution list consistent with the chosen ratio before saving.
  syncResolutionOptions();
  await saveProjectSettings();
  updateExportControls();
});

for (const name of ["outputResolution", "exportFormat", "exportCaptionMode", "exportWriteSrt"]) {
  els[name].addEventListener("change", async () => {
    await saveProjectSettings();
    updateExportControls();
  });
}

function resetToEmptyProject() {
  state.project = { ...EMPTY_PROJECT };
  state.selectedId = null;
  els.dataSourceBadge.textContent = "No project";
  els.dataSourceBadge.className = "badge badge-muted";
  resetPreview();
  resetTranscript();
  applyClipAnalysis(null, false);
  els.candidatesStatus.textContent = "No candidates yet.";
  hideCandidateError();
  showExportResult(null);
  setExportProgress(0);
  hideExportError();
  renderCaptionPreview(null, "Import a video to preview captions.");
  renderProject();
  renderProjectList();
  selectCandidate(null);
}

/* ------------------------------------------------------------------ start */

document.addEventListener("DOMContentLoaded", async () => {
  renderAssetList();
  renderProjectList();
  loadDemoProject();
  checkHealth();

  const projects = await fetchProjects();
  if (projects.length > 0) {
    await openProject(projects[0].project_id);
  }
});
