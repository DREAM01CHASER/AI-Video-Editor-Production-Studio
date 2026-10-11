/**
 * Milestone 1 frontend shell.
 *
 * This module drives the layout with illustrative sample data only. It talks to
 * the backend for the health check and nothing else: importing a file creates a
 * local browser preview, and the processing panel is a layout demo. Real ingest,
 * transcription, clip suggestions, and rendering arrive in later milestones.
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

const PROCESSING_STEPS = [
  "Import and validate file",
  "Read video metadata",
  "Transcribe audio",
  "Suggest short clips",
  "Render export",
];

const SUPPORTED_EXTENSIONS = [".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"];

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
  btnExport: document.getElementById("btn-export"),
  projectList: document.getElementById("project-list"),
  assetList: document.getElementById("asset-list"),
  processingPanel: document.getElementById("processing-panel"),
  processingMessage: document.getElementById("processing-message"),
  btnRunDemo: document.getElementById("btn-run-demo"),
  progressBar: document.querySelector(".progress-bar"),
  progressFill: document.getElementById("progress-fill"),
  processingSteps: document.getElementById("processing-steps"),
  processingError: document.getElementById("processing-error"),
  processingLog: document.getElementById("processing-log"),
  previewFrame: document.getElementById("preview-frame"),
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
};

const state = {
  project: { ...EMPTY_PROJECT },
  selectedId: null,
  objectUrl: null,
  demoRunning: false,
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

/* ------------------------------------------------------- project + lists */

function renderProjectList() {
  els.projectList.replaceChildren();
  const projects = [
    { id: DEMO_PROJECT.id, name: DEMO_PROJECT.name, meta: "Sample" },
    { id: "—", name: "Your projects appear here", meta: "Milestone 2" },
  ];

  for (const project of projects) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "list__item";
    if (project.id === state.project.id) button.classList.add("is-active");

    const name = document.createElement("span");
    name.textContent = project.name;
    const meta = document.createElement("span");
    meta.className = "list__meta";
    meta.textContent = project.meta;

    button.append(name, meta);
    if (project.id === DEMO_PROJECT.id) {
      button.addEventListener("click", loadDemoProject);
    } else {
      button.disabled = true;
      button.title = "Listing local projects is implemented in the ingest milestone.";
    }
    item.append(button);
    els.projectList.append(item);
  }
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
  els.metadataFps.textContent = `${source.fps} fps`;
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
    score.textContent = `Score ${candidate.score.toFixed(2)} (estimate)`;
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
    if (candidate.overlapWith) {
      const tag = document.createElement("span");
      tag.className = "tag tag--overlap";
      tag.textContent = `overlaps ${candidate.overlapWith}`;
      footer.append(tag);
    }

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
}

/* --------------------------------------------------------- clip inspector */

function updateClipDuration() {
  const start = Number.parseFloat(els.clipStart.value) || 0;
  const end = Number.parseFloat(els.clipEnd.value) || 0;
  const duration = Math.max(0, end - start);
  els.clipDuration.textContent = `${duration.toFixed(1)}s`;
}

function applyClipEdit() {
  const candidate = state.project.candidates.find((item) => item.id === state.selectedId);
  if (!candidate) return;
  candidate.start = Math.max(0, Number.parseFloat(els.clipStart.value) || 0);
  candidate.end = Math.max(candidate.start, Number.parseFloat(els.clipEnd.value) || 0);
  updateClipDuration();
  renderCandidates();
  renderTimeline();
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

/* -------------------------------------------------------- import + preview */

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

function loadDemoProject() {
  state.project = structuredClone(DEMO_PROJECT);
  state.selectedId = state.project.candidates[0]?.id ?? null;
  els.dataSourceBadge.textContent = "Demo data";
  els.dataSourceBadge.className = "badge badge-demo";
  els.dropZoneHint.textContent = "Supported: MP4, MOV, M4V, AVI, MKV, WebM. Files stay on this machine.";
  els.previewEmptyHint.textContent =
    "Sample project loaded — import a video to preview real footage.";
  resetPreview();
  renderProjectList();
  renderProject();
  selectCandidate(state.selectedId);
}

function resetToEmptyProject() {
  state.project = { ...EMPTY_PROJECT };
  state.selectedId = null;
  els.dataSourceBadge.textContent = "No project";
  els.dataSourceBadge.className = "badge badge-muted";
  resetPreview();
  renderProjectList();
  renderProject();
  selectCandidate(null);
}

function showPreview(videoUrl) {
  resetPreview();
  state.objectUrl = videoUrl;
  els.video.src = videoUrl;
  els.video.classList.remove("is-hidden");
  els.previewEmpty.classList.add("is-hidden");
}

function handleFile(file) {
  if (!file) return;

  const extension = `.${file.name.split(".").pop().toLowerCase()}`;
  if (!SUPPORTED_EXTENSIONS.includes(extension)) {
    showProcessing("Import failed");
    setProgress(0);
    showError(`Unsupported file type "${extension || file.name}". Use MP4, MOV, M4V, AVI, MKV, or WebM.`);
    log(`Rejected ${file.name}`);
    return;
  }

  hideError();
  showProcessing(`Loaded ${file.name} for local preview`);

  state.project = {
    id: "local-preview",
    name: file.name,
    status: "Local preview",
    source: null,
    candidates: [],
  };
  state.selectedId = null;
  els.dataSourceBadge.textContent = "Local file (not uploaded)";
  els.dataSourceBadge.className = "badge badge-muted";

  showPreview(URL.createObjectURL(file));

  els.video.onloadedmetadata = () => {
    const source = {
      duration: els.video.duration,
      width: els.video.videoWidth,
      height: els.video.videoHeight,
      fps: 0,
      sizeBytes: file.size,
    };
    state.project.source = source;
    els.dataSourceBadge.textContent = "Local file";
    renderMetadata(source);
    els.dropZoneHint.textContent = `${file.name} · ${formatSize(file.size)} · preview only, not uploaded.`;
    renderTimeline();
    els.processingMessage.textContent = "Metadata read locally — upload arrives in the ingest milestone.";
  };

  els.video.onerror = () => {
    showError("This browser could not decode the selected file for preview.");
  };

  renderProjectList();
  renderProject();
  selectCandidate(null);
}

els.video.addEventListener("timeupdate", () => {
  els.previewCurrentTime.textContent = formatTimecode(els.video.currentTime);
  els.previewDuration.textContent = formatTimecode(els.video.duration || 0);
});

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

function showProcessing(message) {
  els.processingPanel.classList.remove("is-hidden");
  els.processingMessage.textContent = message;
  renderSteps(PROCESSING_STEPS.map((label) => ({ label, status: "waiting" })));
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

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function runProcessingDemo() {
  if (state.demoRunning) return;
  state.demoRunning = true;
  els.processingLog.textContent = "";
  hideError();
  showProcessing("Layout demo running…");
  log("Demo only — no media is analysed or rendered.");

  const steps = PROCESSING_STEPS.map((label) => ({ label, status: "waiting" }));

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

els.btnImport.addEventListener("click", () => els.fileInput.click());
els.fileInput.addEventListener("change", () => {
  handleFile(els.fileInput.files?.[0]);
  els.fileInput.value = "";
});

els.btnNewProject.addEventListener("click", resetToEmptyProject);
els.btnRefreshProjects.addEventListener("click", () => {
  renderProjectList();
  log("Project list refreshed (demo data).");
});

els.dropZone.addEventListener("click", () => els.fileInput.click());
els.dropZone.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    els.fileInput.click();
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

// Export stays disabled until the render milestone; the title explains why.
els.btnExport.title =
  "Rendering is not implemented yet; it arrives in the captions and export milestone.";

/* ------------------------------------------------------------------ start */

document.addEventListener("DOMContentLoaded", () => {
  renderAssetList();
  renderProjectList();
  loadDemoProject();
  checkHealth();
});
