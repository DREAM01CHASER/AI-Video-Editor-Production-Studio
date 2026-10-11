/**
 * Frontend shell for the AI Video Production Studio.
 *
 * Import and the project list talk to the local FastAPI backend (Milestone 2).
 * Transcription, clip suggestions, captions, and rendering are still layout
 * only: their controls are disabled or clearly marked as a demo, and the
 * processing panel labels anything that is not real.
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
  progressBar: document.querySelector(".progress-bar"),
  progressFill: document.getElementById("progress-fill"),
  processingSteps: document.getElementById("processing-steps"),
  processingError: document.getElementById("processing-error"),
  processingLog: document.getElementById("processing-log"),
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
  busy: false,
  projects: [],
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
    meta.textContent = project.duration
      ? `${formatTimecode(project.duration)} · ${project.clip_count} clips`
      : "Imported";

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
    candidates: (payload.clips || []).map((clip, index) => ({
      id: clip.id || `clip-${index + 1}`,
      title: clip.title || `Clip ${index + 1}`,
      reason: clip.reason || "",
      start: Number(clip.source_start) || 0,
      end: Number(clip.source_end) || 0,
      score: Number(clip.score) || 0,
    })),
  };
  state.selectedId = null;

  els.dataSourceBadge.textContent = "Imported";
  els.dataSourceBadge.className = "badge badge-demo";
  els.dropZoneHint.textContent = `${state.project.name} · saved in the local workspace.`;
  els.previewEmptyHint.textContent = "Source video loaded from the local workspace.";

  renderProjectList();
  renderProject();
  setPreviewSource(`/api/projects/${payload.project_id}/source`);
  selectCandidate(null);
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
  state.selectedId = state.project.candidates[0]?.id ?? null;
  els.dataSourceBadge.textContent = "Demo data";
  els.dataSourceBadge.className = "badge badge-demo";
  els.dropZoneHint.textContent =
    "Supported: MP4, MOV, M4V, AVI, MKV, WebM. Files stay on this machine.";
  els.previewEmptyHint.textContent =
    "Sample project loaded — import a video to preview real footage.";
  resetPreview();
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

// Export stays disabled until the render milestone; the title explains why.
els.btnExport.title =
  "Rendering is not implemented yet; it arrives in the captions and export milestone.";

function resetToEmptyProject() {
  state.project = { ...EMPTY_PROJECT };
  state.selectedId = null;
  els.dataSourceBadge.textContent = "No project";
  els.dataSourceBadge.className = "badge badge-muted";
  resetPreview();
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
