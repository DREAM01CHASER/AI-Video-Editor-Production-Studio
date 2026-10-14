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
  assetCount: document.getElementById("asset-count"),
  assetStatus: document.getElementById("asset-status"),
  assetError: document.getElementById("asset-error"),
  assetSearch: document.getElementById("asset-search"),
  assetKindFilter: document.getElementById("asset-kind-filter"),
  assetFileInput: document.getElementById("asset-file-input"),
  btnImportBroll: document.getElementById("btn-import-broll"),
  btnImportSfx: document.getElementById("btn-import-sfx"),
  btnAddBroll: document.getElementById("btn-add-broll"),
  btnAddSfx: document.getElementById("btn-add-sfx"),
  stockPanel: document.getElementById("stock-panel"),
  stockQuery: document.getElementById("stock-query"),
  stockOrientation: document.getElementById("stock-orientation"),
  stockCountSelect: document.getElementById("stock-count-select"),
  btnStockSearch: document.getElementById("btn-stock-search"),
  stockStatus: document.getElementById("stock-status"),
  stockError: document.getElementById("stock-error"),
  stockResults: document.getElementById("stock-results"),
  stockCount: document.getElementById("stock-count"),
  timelinePlacement: document.getElementById("timeline-placement"),
  itemPanel: document.getElementById("item-panel"),
  itemName: document.getElementById("item-name"),
  itemStatus: document.getElementById("item-status"),
  itemFields: document.getElementById("item-fields"),
  itemStart: document.getElementById("item-start"),
  itemEnd: document.getElementById("item-end"),
  itemSourceField: document.getElementById("item-source-field"),
  itemSourceStart: document.getElementById("item-source-start"),
  itemPlacementField: document.getElementById("item-placement-field"),
  itemPlacement: document.getElementById("item-placement"),
  itemFitField: document.getElementById("item-fit-field"),
  itemFit: document.getElementById("item-fit"),
  itemGainField: document.getElementById("item-gain-field"),
  itemGain: document.getElementById("item-gain"),
  itemFadeField: document.getElementById("item-fade-field"),
  itemFade: document.getElementById("item-fade"),
  itemTransitionKindField: document.getElementById("item-transition-kind-field"),
  itemTransitionKind: document.getElementById("item-transition-kind"),
  itemTransitionAtField: document.getElementById("item-transition-at-field"),
  itemTransitionAt: document.getElementById("item-transition-at"),
  itemTransitionDurationField: document.getElementById("item-transition-duration-field"),
  itemTransitionDuration: document.getElementById("item-transition-duration"),
  btnAddTransition: document.getElementById("btn-add-transition"),
  soundDesignEnabled: document.getElementById("sound-design-enabled"),
  soundDesignKindField: document.getElementById("sound-design-kind-field"),
  soundDesignKind: document.getElementById("sound-design-kind"),
  soundDesignCountField: document.getElementById("sound-design-count-field"),
  soundDesignCount: document.getElementById("sound-design-count"),
  soundDesignState: document.getElementById("sound-design-state"),
  soundDesignStatus: document.getElementById("sound-design-status"),
  btnItemDelete: document.getElementById("btn-item-delete"),
  itemError: document.getElementById("item-error"),
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
  timelineSummary: document.getElementById("timeline-summary"),
  timelineStatus: document.getElementById("timeline-status"),
  timelineError: document.getElementById("timeline-error"),
  timelineSaveState: document.getElementById("timeline-save-state"),
  btnTimelineIn: document.getElementById("btn-timeline-in"),
  btnTimelineOut: document.getElementById("btn-timeline-out"),
  outputFps: document.getElementById("output-fps"),
  captionStyle: document.getElementById("caption-style"),
  fontSize: document.getElementById("font-size"),
  captionPlacement: document.getElementById("caption-placement"),
  highlightMode: document.getElementById("highlight-mode"),
  lineLength: document.getElementById("line-length"),
  captionPosition: document.getElementById("caption-position"),
  captionPositionValue: document.getElementById("caption-position-value"),
  brollMode: document.getElementById("broll-mode"),
  brollMax: document.getElementById("broll-max"),
  btnBrollAuto: document.getElementById("btn-broll-auto"),
  btnBrollSuggest: document.getElementById("btn-broll-suggest"),
  brollStatus: document.getElementById("broll-status"),
  brollError: document.getElementById("broll-error"),
  brollProposals: document.getElementById("broll-proposals"),
  brollCount: document.getElementById("broll-count"),
  captionFontFamily: document.getElementById("caption-font-family"),
  captionAnimation: document.getElementById("caption-animation"),
  captionHighlighting: document.getElementById("caption-highlighting"),
  captionUppercase: document.getElementById("caption-uppercase"),
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
  "outputFps",
  "exportFormat",
  "exportCaptionMode",
  "exportWriteSrt",
  "captionStyle",
  "fontSize",
  "captionPlacement",
  "highlightMode",
  "lineLength",
  "captionPosition",
  "captionFontFamily",
  "captionAnimation",
  "captionHighlighting",
  "captionUppercase",
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
  //: Automatic B-roll proposals for the selected clip, and where they came from.
  broll: {
    proposals: [],
    payload: null,
    //: The inserts a one-click pass actually downloaded and placed, with their
    //: credit, so the panel can show what went into the edit and from where.
    selected: [],
    busy: false,
  },
  //: The composed edit for the selected clip: in/out points, captions, render
  //: settings, and the item tracks. The project JSON stays the source of truth.
  timeline: null,
  timelineSaving: false,
  //: Timeline saves run in order, so a slow response cannot overwrite a newer edit.
  timelineQueue: Promise.resolve(),
  //: The local asset library, and which asset the next placement will use.
  assets: [],
  activeAssetId: null,
  assetBusy: false,
  //: Optional stock search: whether it is configured, and the last results.
  stock: {
    status: null,
    results: [],
    keywords: [],
    placement: null,
    busy: false,
  },
  //: The timeline item being edited in the inspector, as {track, id}.
  selectedItem: null,
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
    // Which device transcription can really use, shown before any job runs: a
    // visible GPU whose CUDA runtime is missing is a CPU run, and that has to be
    // stated rather than discovered from a slow transcription.
    const compute = data.transcription?.compute;
    if (compute) {
      const device = compute.usable ? "GPU (CUDA)" : "CPU";
      els.healthMessage.textContent +=
        ` Transcription: ${device} — ${compute.model || "model"}` +
        (compute.usable ? ` on ${compute.device_count} CUDA device.` : `/int8.`) +
        ` ${compute.summary}`;
      if (compute.remedy) els.healthMessage.textContent += ` ${compute.remedy}`;
    }
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

/* ------------------------------------------------------- local assets */

function showAssetError(message) {
  els.assetError.textContent = message;
  els.assetError.classList.remove("is-hidden");
}

function hideAssetError() {
  els.assetError.textContent = "";
  els.assetError.classList.add("is-hidden");
}

function formatAssetDuration(seconds) {
  const value = Number(seconds) || 0;
  return value >= 60 ? formatTimecode(value) : `${value.toFixed(1)}s`;
}

/** The asset the next “add at playhead” click will place. */
function activeAsset() {
  return state.assets.find((asset) => asset.id === state.activeAssetId) || null;
}

function updateAssetControls() {
  const real = isRealProject();
  const reason = "Import a video first — assets belong to a project.";

  for (const button of [els.btnImportBroll, els.btnImportSfx]) {
    button.disabled = !real || state.assetBusy;
    button.title = real ? button.dataset.title || button.title : reason;
  }
}

function renderAssetList() {
  els.assetList.replaceChildren();

  const query = els.assetSearch.value.trim().toLowerCase();
  const kind = els.assetKindFilter.value;
  const matches = state.assets.filter((asset) => {
    if (kind && asset.kind !== kind) return false;
    if (!query) return true;
    const haystack = [asset.label, asset.original_name, ...(asset.tags || [])]
      .join(" ")
      .toLowerCase();
    return haystack.includes(query);
  });

  els.assetCount.textContent = String(state.assets.length);

  for (const asset of matches) {
    const item = document.createElement("li");
    const card = document.createElement("div");
    card.className = "asset";
    if (asset.id === state.activeAssetId) card.classList.add("is-active");

    const top = document.createElement("div");
    top.className = "asset__top";
    const label = document.createElement("span");
    label.className = "asset__label";
    label.textContent = asset.label || asset.original_name || asset.id;
    const tag = document.createElement("span");
    tag.className = `tag tag--${asset.kind}`;
    tag.textContent = asset.kind === "broll" ? "B-roll" : "SFX";
    top.append(label, tag);

    const meta = document.createElement("span");
    meta.className = "asset__meta";
    const bits = [formatAssetDuration(asset.duration), formatSize(asset.size_bytes)];
    if (asset.kind === "broll" && asset.width) bits.push(`${asset.width}×${asset.height}`);
    meta.textContent = bits.join(" · ");

    card.append(top, meta);

    if ((asset.tags || []).length > 0) {
      const tags = document.createElement("div");
      tags.className = "asset__tags";
      for (const name of asset.tags) {
        const chip = document.createElement("span");
        chip.className = "tag";
        chip.textContent = name;
        tags.append(chip);
      }
      card.append(tags);
    }

    const actions = document.createElement("div");
    actions.className = "asset__actions";

    const use = document.createElement("button");
    use.type = "button";
    use.className = "btn btn-ghost";
    use.textContent = asset.id === state.activeAssetId ? "Selected" : "Select";
    use.title = "Use this asset when adding a timeline item.";
    use.addEventListener("click", (event) => {
      event.stopPropagation();
      state.activeAssetId = asset.id;
      renderAssetList();
      updateTimelinePlacement();
    });

    const preview = document.createElement("button");
    preview.type = "button";
    preview.className = "btn btn-ghost";
    preview.textContent = "Preview";
    preview.title = "Play this asset in the preview area.";
    preview.addEventListener("click", (event) => {
      event.stopPropagation();
      setPreviewSource(asset.url);
      log(`Previewing asset ${asset.label || asset.id}.`);
    });

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "btn btn-ghost";
    // Deleting asks once more in the same place, so nothing is lost by a misclick.
    remove.dataset.confirming = "false";
    remove.textContent = "Delete";
    remove.title = "Remove this asset from the project.";
    remove.addEventListener("click", (event) => {
      event.stopPropagation();
      if (remove.dataset.confirming === "true") {
        deleteAsset(asset);
        return;
      }
      remove.dataset.confirming = "true";
      remove.textContent = "Confirm delete";
    });

    actions.append(use, preview, remove);
    card.append(actions);
    item.append(card);
    els.assetList.append(item);
  }

  if (matches.length === 0) {
    const item = document.createElement("li");
    const hint = document.createElement("p");
    hint.className = "panel__hint";
    hint.textContent = state.assets.length
      ? "No asset matches that search."
      : isRealProject()
        ? "No assets yet. Import your own B-roll or sound effect above."
        : "Open or import a project to import assets into it.";
    item.append(hint);
    els.assetList.append(item);
  }

  updateAssetControls();
  updateTimelinePlacement();
}

async function fetchAssets(projectId) {
  if (!isRealProject()) {
    state.assets = [];
    state.activeAssetId = null;
    renderAssetList();
    return [];
  }

  try {
    const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/assets`, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    if (state.project.id !== projectId) return [];

    state.assets = Array.isArray(payload.assets) ? payload.assets : [];
    if (!state.assets.some((asset) => asset.id === state.activeAssetId)) {
      state.activeAssetId = state.assets[0]?.id ?? null;
    }
    hideAssetError();
  } catch (error) {
    state.assets = [];
    showAssetError(`Could not list the asset library: ${error.message}`);
  }

  renderAssetList();
  return state.assets;
}

async function importAsset(file, kind) {
  if (!file || !isRealProject() || state.assetBusy) return;

  state.assetBusy = true;
  hideAssetError();
  els.assetStatus.textContent = `Importing ${file.name}…`;
  updateAssetControls();

  const formData = new FormData();
  formData.append("file", file);
  formData.append("kind", kind);
  formData.append("label", file.name.replace(/\.[^.]+$/, ""));

  try {
    const response = await fetch(
      `/api/projects/${encodeURIComponent(state.project.id)}/assets`,
      { method: "POST", body: formData }
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(apiErrorMessage(payload, `Import failed (HTTP ${response.status}).`));
    }

    state.activeAssetId = payload.id;
    els.assetStatus.textContent = `Imported ${payload.label} (${formatAssetDuration(payload.duration)}).`;
    log(`Imported ${kind} asset ${payload.label}.`);
    await fetchAssets(state.project.id);
  } catch (error) {
    showAssetError(error.message);
    els.assetStatus.textContent = "The asset was not imported.";
    log(`Error: ${error.message}`);
  } finally {
    state.assetBusy = false;
    els.assetFileInput.value = "";
    updateAssetControls();
  }
}

async function deleteAsset(asset) {
  if (!isRealProject()) return;

  hideAssetError();
  try {
    const response = await fetch(
      `/api/projects/${encodeURIComponent(state.project.id)}/assets/${encodeURIComponent(asset.id)}`,
      { method: "DELETE", headers: { Accept: "application/json" } }
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(apiErrorMessage(payload, `Could not delete it (HTTP ${response.status}).`));
    }
    if (state.activeAssetId === asset.id) state.activeAssetId = null;
    els.assetStatus.textContent = `Deleted ${asset.label || asset.id}.`;
    log(`Deleted asset ${asset.label || asset.id}.`);
    await fetchAssets(state.project.id);
  } catch (error) {
    showAssetError(error.message);
  }
}

/* --------------------------------------------------------- stock search */

function showStockError(message) {
  els.stockError.textContent = message;
  els.stockError.classList.remove("is-hidden");
}

function hideStockError() {
  els.stockError.textContent = "";
  els.stockError.classList.add("is-hidden");
}

/** The clip whose transcript should drive an empty search box. */
function activeClipId() {
  return state.selectedId || null;
}

function updateStockControls() {
  const real = isRealProject();
  const configured = state.stock.status?.configured !== false;
  const busy = state.stock.busy;

  els.btnStockSearch.disabled = !real || !configured || busy;
  els.btnStockSearch.title = !real
    ? "Import a video first — stock search attaches to a project."
    : !configured
      ? "Set PEXELS_API_KEY in .env to enable stock search."
      : busy
        ? "A search is already running."
        : "Search Pexels for stock B-roll to overlay on the selected clip.";

  els.stockQuery.disabled = !real || !configured || busy;
  els.stockOrientation.disabled = !real || !configured || busy;
  els.stockCountSelect.disabled = !real || !configured || busy;
}

/** Ask the backend whether stock search is configured, and show why not if not. */
async function loadStockStatus(projectId) {
  if (!isRealProject()) return;

  try {
    const response = await fetch(
      `/api/projects/${encodeURIComponent(projectId)}/stock`,
      { headers: { Accept: "application/json" } }
    );
    const payload = await response.json().catch(() => ({}));
    if (state.project.id !== projectId) return;
    if (!response.ok) throw new Error(apiErrorMessage(payload, `HTTP ${response.status}`));

    state.stock.status = payload;
    els.stockStatus.textContent = payload.note || "";
  } catch (error) {
    state.stock.status = { configured: false, note: error.message };
    els.stockStatus.textContent = error.message;
  }

  updateStockControls();
}

/** Reset the stock panel when a different project is open. */
function resetStock(note) {
  state.stock.results = [];
  state.stock.keywords = [];
  state.stock.placement = null;
  state.stock.busy = false;
  state.stock.status = null;
  els.stockResults.replaceChildren();
  els.stockCount.textContent = "0";
  els.stockQuery.value = "";
  hideStockError();
  els.stockStatus.textContent = note || "";
  updateStockControls();
}

function renderStockResults() {
  els.stockResults.replaceChildren();
  const results = state.stock.results;
  els.stockCount.textContent = String(results.length);

  for (const result of results) {
    const item = document.createElement("li");
    const card = document.createElement("div");
    card.className = "stock-card";

    if (result.preview_url) {
      const thumb = document.createElement("img");
      thumb.className = "stock-card__thumb";
      thumb.src = result.preview_url;
      thumb.alt = "";
      thumb.loading = "lazy";
      card.append(thumb);
    }

    const body = document.createElement("div");
    body.className = "stock-card__body";

    const title = document.createElement("span");
    title.className = "stock-card__title";
    title.textContent =
      `${result.width || "?"}×${result.height || "?"} · ` +
      (result.media_type === "photo" ? "still image" : formatAssetDuration(result.duration));

    const credit = document.createElement("span");
    credit.className = "stock-card__credit";
    if (result.page_url) {
      const link = document.createElement("a");
      link.href = result.page_url;
      link.target = "_blank";
      link.rel = "noreferrer noopener";
      link.textContent = result.photographer || "View on Pexels";
      credit.append(link, document.createTextNode(" · Media provided by Pexels"));
    } else {
      credit.textContent = "Media provided by Pexels";
    }

    body.append(title, credit);
    card.append(body);

    const actions = document.createElement("div");
    actions.className = "stock-card__actions";

    const add = document.createElement("button");
    add.type = "button";
    add.className = "btn btn-ghost";
    add.textContent = "Add as B-roll";
    add.title = "Download this clip into the project and place it on the timeline.";
    add.addEventListener("click", (event) => {
      event.stopPropagation();
      importStockResult(result);
    });
    actions.append(add);
    card.append(actions);

    item.append(card);
    els.stockResults.append(item);
  }

  if (results.length === 0) {
    const item = document.createElement("li");
    const hint = document.createElement("p");
    hint.className = "panel__hint";
    hint.textContent = state.stock.status?.configured
      ? "Search Pexels for stock B-roll. Leave the box empty to use the clip's own words."
      : "Stock search is off. Set PEXELS_API_KEY in .env to enable it — your local " +
        "library keeps working either way.";
    item.append(hint);
    els.stockResults.append(item);
  }

  updateStockControls();
}

async function searchStock() {
  if (!isRealProject() || state.stock.busy) return;

  state.stock.busy = true;
  hideStockError();
  els.stockStatus.textContent = "Searching Pexels…";
  updateStockControls();

  const parameters = new URLSearchParams({
    per_page: els.stockCountSelect.value || "12",
  });
  const query = els.stockQuery.value.trim();
  if (query) parameters.set("q", query);
  else if (activeClipId()) parameters.set("clip_id", activeClipId());
  const orientation = els.stockOrientation.value;
  if (orientation && orientation !== "all") parameters.set("orientation", orientation);

  try {
    const response = await fetch(
      `/api/projects/${encodeURIComponent(state.project.id)}/stock/search?${parameters}`,
      { headers: { Accept: "application/json" } }
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(apiErrorMessage(payload, `The search failed (HTTP ${response.status}).`));
    }

    state.stock.results = Array.isArray(payload.results) ? payload.results : [];
    state.stock.keywords = Array.isArray(payload.keywords) ? payload.keywords : [];
    state.stock.placement = payload.suggested_placement || null;
    els.stockStatus.textContent = payload.query
      ? `“${payload.query}” — ${state.stock.results.length} result(s). Preview first; nothing is downloaded until you add one.`
      : "No results.";
    renderStockResults();
    log(`Stock search “${payload.query}” returned ${state.stock.results.length} result(s).`);
  } catch (error) {
    showStockError(error.message);
    els.stockStatus.textContent = "The search did not finish.";
    state.stock.results = [];
    renderStockResults();
  } finally {
    state.stock.busy = false;
    updateStockControls();
  }
}

async function importStockResult(result) {
  if (!isRealProject() || state.assetBusy) return;

  state.assetBusy = true;
  hideStockError();
  els.stockStatus.textContent = "Downloading the chosen clip into the project…";
  updateAssetControls();
  updateStockControls();

  try {
    const response = await fetch(
      `/api/projects/${encodeURIComponent(state.project.id)}/stock/import`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(
          result.media_type === "photo"
            ? { media_type: "photo", photo_id: result.id }
            : { media_type: "video", video_id: result.id }
        ),
      }
    );
    const asset = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(apiErrorMessage(asset, `Could not import it (HTTP ${response.status}).`));
    }

    state.activeAssetId = asset.id;
    els.stockStatus.textContent = `Added ${asset.label} to the library — media provided by Pexels.`;
    log(`Imported stock clip ${asset.id} from Pexels.`);
    await fetchAssets(state.project.id);

    // Place it on the timeline so the search leads to a real edit, not just a file.
    await placeStockAssetOnTimeline(asset, result);
  } catch (error) {
    showStockError(error.message);
    els.stockStatus.textContent = "The clip was not imported.";
  } finally {
    state.assetBusy = false;
    updateAssetControls();
    updateStockControls();
  }
}

/** Draft a B-roll placement for a freshly imported stock clip.
 *
 * The placement is only a suggestion taken from the transcript, so it is applied
 * through the same validated PATCH the timeline uses — the backend may refuse it
 * and the timeline stays exactly as it was.
 */
async function placeStockAssetOnTimeline(asset, result) {
  if (!state.timeline || !state.timeline.clip_id) {
    els.stockStatus.textContent =
      `Added ${asset.label} to the library. Select a clip to place it on the timeline.`;
    return;
  }

  const placement = state.stock.placement;
  const clipDuration = Number(state.timeline.duration) || 0;
  if (!clipDuration) return;

  const start = Math.min(Math.max(0, Number(placement?.start) || 0), clipDuration - 0.5);
  const end = Math.min(
    Math.max(start + 0.5, Number(placement?.end) || start + 2.0),
    clipDuration
  );

  await sendTimelinePatch({
    broll: [
      ...(state.timeline.broll || []),
      {
        source: asset.id,
        start: round3(start),
        end: round3(end),
        placement: "full",
        // A still fills the frame; a clip keeps its own framing.
        fit: result?.media_type === "photo" ? "cover" : "contain",
      },
    ],
  });

  els.stockStatus.textContent = `Added ${asset.label} and placed it at ${start.toFixed(1)}s — ` +
    "adjust or remove it on the timeline.";
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
  updateStockControls();
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
  // Captions and the timeline both belong to the selected clip.
  loadCaptionPreview();
  loadTimeline(state.project.id, id);
}

/* --------------------------------------------------------- clip inspector */

function updateClipDuration() {
  const start = Number.parseFloat(els.clipStart.value) || 0;
  const end = Number.parseFloat(els.clipEnd.value) || 0;
  const duration = Math.max(0, end - start);
  els.clipDuration.textContent = `${duration.toFixed(1)}s`;
}

/**
 * Save the in/out fields typed into the inspector.
 *
 * They go through the same timeline endpoint the drag handles use, so there is
 * one write path for a clip's times and one place that validates them.
 */
async function applyClipEdit() {
  const candidate = state.project.candidates.find((item) => item.id === state.selectedId);
  if (!candidate) return;

  if (!isRealProject() || !state.timeline) {
    els.candidatesStatus.textContent =
      "Sample data — edits to the sample project are not saved.";
    return;
  }

  const start = Math.max(0, Number.parseFloat(els.clipStart.value) || 0);
  const end = Math.max(start, Number.parseFloat(els.clipEnd.value) || 0);
  await saveTimeline({ video: { source_start: round3(start), source_end: round3(end) } }, {
    reloadCaptions: true,
  });
}

els.clipStart.addEventListener("input", updateClipDuration);
els.clipEnd.addEventListener("input", updateClipDuration);
els.clipStart.addEventListener("change", applyClipEdit);
els.clipEnd.addEventListener("change", applyClipEdit);

/* ------------------------------------------------------------- timeline */

/** The shortest clip that is still worth exporting, in seconds. */
const MIN_CLIP_SECONDS = 1.0;

function setTimelineSaveState(text, className) {
  els.timelineSaveState.textContent = text;
  els.timelineSaveState.className = className;
}

function showTimelineError(message) {
  els.timelineError.textContent = message;
  els.timelineError.classList.remove("is-hidden");
}

function hideTimelineError() {
  els.timelineError.textContent = "";
  els.timelineError.classList.add("is-hidden");
}

/** The edit currently loaded from the backend, or null for sample data. */
function currentTimeline() {
  return isRealProject() ? state.timeline : null;
}

function round3(value) {
  return Math.round(Number(value) * 1000) / 1000;
}

/**
 * Paint the timeline from the loaded edit.
 *
 * Every block is positioned from the project data rather than from a local
 * guess, so an edit that the backend rejected cannot leave the picture showing
 * something that was never saved.
 */
function renderTimeline() {
  const source = state.project.source;
  const timeline = currentTimeline();

  els.timelineRuler.replaceChildren();
  els.timelineTracks.replaceChildren();

  if (!source || !source.duration) {
    els.timelineSummary.textContent = "No clip selected.";
    els.timelineStatus.textContent = "Import a video to edit a timeline.";
    updateTimelineControls();
    return;
  }

  const duration = source.duration;
  const pct = (seconds) => `${Math.min(100, (seconds / duration) * 100)}%`;

  for (let i = 0; i <= 6; i += 1) {
    const tick = document.createElement("span");
    tick.className = "timeline__tick";
    tick.style.left = `${(i / 6) * 100}%`;
    tick.textContent = formatTimecode((i / 6) * duration);
    els.timelineRuler.append(tick);
  }

  const lanes = [
    { kind: "video", label: "Video", title: "Selected clip on the source timeline" },
    { kind: "caption", label: "Captions", title: "Burned-in caption track" },
    { kind: "broll", label: "B-roll", title: "B-roll overlays" },
    { kind: "sfx", label: "SFX", title: "Sound effects you placed by hand" },
    { kind: "transition", label: "Transitions", title: "How the clip arrives or leaves" },
  ];

  for (const lane of lanes) {
    const label = document.createElement("div");
    label.className = "timeline__track-label";
    label.textContent = lane.label;

    const track = document.createElement("div");
    track.className = "timeline__lane";
    track.dataset.kind = lane.kind;
    track.title = lane.title;

    if (lane.kind === "video") {
      paintVideoTrack(track, timeline, duration, pct);
    } else if (lane.kind === "caption") {
      paintCaptionTrack(track, timeline, pct);
    } else if (lane.kind === "broll") {
      paintItems(track, timeline?.broll, "broll", pct, {
        trackName: "broll",
        empty: timeline
          ? "No B-roll yet — import a clip and press “+ B-roll at playhead”"
          : "Select a real clip to edit this track",
      });
    } else if (lane.kind === "sfx") {
      paintItems(track, timeline?.sfx, "sfx", pct, {
        trackName: "sfx",
        empty: timeline
          ? "No sound effects yet — import one and press “+ SFX at playhead”"
          : "Select a real clip to edit this track",
      });
    } else {
      // A transition is anchored to the clip's start or end, so it is drawn as a
      // marker at that edge rather than as a block with a width of its own.
      const drawn = paintTransitions(track, timeline, pct);
      if (timeline && drawn === 0) {
        appendTimelineHint(
          track,
          "No transitions — press “+ Transition” to fade, slide, or wipe the clip in or out"
        );
      } else if (!timeline) {
        appendTimelineHint(track, "Select a real clip to edit this track");
      }
    }

    if (!timeline && lane.kind !== "video") {
      appendTimelineHint(track, "Select a real clip to edit this track");
    }

    els.timelineTracks.append(label, track);
  }

  paintTimelineStatus(timeline, duration);
  renderSoundDesign(timeline);
  updateTimelineControls();
}

function paintVideoTrack(track, timeline, duration, pct) {
  if (!timeline) {
    appendTimelineHint(track, "No clip selected");
    return;
  }

  const { source_start: start, source_end: end } = timeline.video;
  const block = document.createElement("div");
  block.className = "timeline__clip timeline__clip--video";
  block.dataset.role = "clip-block";
  block.style.left = pct(start);
  block.style.width = pct(Math.max(0, end - start));
  block.title = `${formatTimecode(start)} → ${formatTimecode(end)} — drag to move, drag the edges to trim`;

  const label = document.createElement("span");
  label.className = "timeline__clip-label";
  label.textContent = `${(end - start).toFixed(1)}s`;
  block.append(label);

  for (const [mode, side] of [["in", "--in"], ["out", "--out"]]) {
    const handle = document.createElement("span");
    handle.className = `timeline__handle timeline__handle${side}`;
    handle.dataset.role = `handle-${mode}`;
    handle.title = mode === "in" ? "Drag to change the in point" : "Drag to change the out point";
    block.append(handle);
  }

  // Which edge is being grabbed is decided from the pointer's position rather
  // than from which element was hit: on a short clip the two handles overlap, and
  // then the whole block has to stay draggable.
  block.addEventListener("pointerdown", (event) => {
    const rect = block.getBoundingClientRect();
    const offset = event.clientX - rect.left;
    const edge = Math.min(12, Math.max(4, rect.width * 0.25));

    if (rect.width < 28) {
      beginClipDrag(event, "move");
    } else if (offset <= edge) {
      beginClipDrag(event, "in");
    } else if (offset >= rect.width - edge) {
      beginClipDrag(event, "out");
    } else {
      beginClipDrag(event, "move");
    }
  });

  track.append(block, playheadElement(duration, pct));
}

function paintCaptionTrack(track, timeline, pct) {
  if (!timeline) return;
  if (!timeline.captions?.enabled) {
    appendTimelineHint(track, "Captions off for this export");
    return;
  }

  const block = document.createElement("div");
  block.className = "timeline__clip timeline__clip--caption";
  block.style.left = pct(timeline.video.source_start);
  block.style.width = pct(Math.max(0, timeline.video.source_end - timeline.video.source_start));
  block.title = `${timeline.captions.preset} captions, drawn from the saved transcript`;
  track.append(block, playheadElement(state.project.source.duration, pct));
}

function appendTimelineHint(track, text) {
  const hint = document.createElement("span");
  hint.className = "timeline__hint";
  hint.textContent = text;
  track.append(hint);
}

/**
 * Draw a track's items and return how many were drawn.
 *
 * Returning the count lets a caller decide whether an empty hint is needed, so
 * lanes that hold two kinds of item cannot end up with two hints.
 */
function paintItems(track, items, kind, pct, { marker = false, empty = null, trackName = null } = {}) {
  const list = items || [];

  for (const item of list) {
    const start = Number(item.start) || 0;
    const end = Number(item.end) || start;
    const element = document.createElement("div");
    if (marker) {
      element.className = "timeline__marker";
      element.style.left = pct(start);
      element.title = `${item.kind} transition at ${formatTimecode(start)}`;
    } else {
      element.className = `timeline__clip timeline__clip--${kind} timeline__clip--item`;
      element.dataset.itemId = String(item.id);
      element.style.left = pct(start);
      element.style.width = pct(Math.max(0.2, end - start));
      element.title = `${kind} item — click to edit ${formatTimecode(start)} → ${formatTimecode(end)}`;
      if (state.selectedItem?.track === trackName && state.selectedItem.id === item.id) {
        element.classList.add("is-active");
      }
      if (trackName) {
        element.addEventListener("click", (event) => {
          event.stopPropagation();
          selectTimelineItem(trackName, String(item.id));
        });
      }
    }
    track.append(element);
  }

  if (list.length === 0 && empty) appendTimelineHint(track, empty);
  return list.length;
}

/**
 * Draw the transition markers and return how many were drawn.
 *
 * A transition belongs to an edge of the clip rather than to a span inside it, so
 * each marker is drawn as a short block sitting at that edge. Its width is the
 * transition's own length, which makes the size of the effect readable at a glance
 * rather than having to open the item.
 */
function paintTransitions(track, timeline, pct) {
  const list = timeline?.transitions || [];
  const duration = Number(timeline?.duration) || 0;

  for (const item of list) {
    const length = Number(item.duration) || 0;
    const atEnd = String(item.at || "start") === "end";
    const start = atEnd ? Math.max(0, duration - length) : 0;

    const element = document.createElement("div");
    element.className = "timeline__marker timeline__marker--transition";
    element.dataset.itemId = String(item.id);
    element.style.left = pct(start);
    element.style.width = pct(Math.max(0.05, length));
    element.title =
      `${transitionLabel(item.kind)} ${atEnd ? "out" : "in"} — ` +
      `${formatTimecode(start)} → ${formatTimecode(start + length)}; click to edit`;

    if (state.selectedItem?.track === "transitions" && state.selectedItem.id === item.id) {
      element.classList.add("is-active");
    }

    element.addEventListener("click", (event) => {
      event.stopPropagation();
      selectTimelineItem("transitions", String(item.id));
    });

    track.append(element);
  }

  return list.length;
}

/** A readable name for a transition kind, matching the inspector's own labels. */
function transitionLabel(kind) {
  return (
    { fade: "Fade", "slide-left": "Slide", "wipe-left": "Wipe" }[String(kind)] || String(kind)
  );
}

function playheadElement(duration, pct) {
  const playhead = document.createElement("div");
  playhead.className = "timeline__playhead";
  playhead.dataset.role = "playhead";
  const seconds = Number(els.video.currentTime) || 0;
  playhead.style.left = pct(Math.min(seconds, duration));
  return playhead;
}

/* ------------------------------------------------- timeline items */

function showItemError(message) {
  els.itemError.textContent = message;
  els.itemError.classList.remove("is-hidden");
}

function hideItemError() {
  els.itemError.textContent = "";
  els.itemError.classList.add("is-hidden");
}

/** Where the playhead sits inside the selected clip, in clip-relative seconds. */
function playheadInClip() {
  const timeline = currentTimeline();
  if (!timeline) return 0;
  const source = Number(els.video.currentTime) || 0;
  return Math.min(
    Math.max(0, source - timeline.video.source_start),
    Math.max(0, timeline.duration)
  );
}

function selectTimelineItem(trackName, itemId) {
  state.selectedItem = { track: trackName, id: itemId };
  renderTimeline();
  renderItemPanel();
}

function selectedTimelineItem() {
  const selection = state.selectedItem;
  const timeline = currentTimeline();
  if (!selection || !timeline) return null;

  const item = (timeline[selection.track] || []).find(
    (entry) => String(entry.id) === String(selection.id)
  );
  return item ? { track: selection.track, item } : null;
}

function assetLabelFor(id) {
  const asset = state.assets.find((entry) => entry.id === id);
  if (asset) return asset.label || asset.original_name || asset.id;
  return String(id) === "source" || String(id) === "video"
    ? "this project's own video"
    : String(id);
}

function renderItemPanel() {
  const found = selectedTimelineItem();

  if (!found) {
    els.itemFields.classList.add("is-hidden");
    els.itemName.textContent = "None";
    els.itemName.className = "badge badge-muted";
    els.itemStatus.textContent =
      "Click a B-roll, sound-effect, or transition block on the timeline to edit it here.";
    hideItemError();
    return;
  }

  const { track, item } = found;
  const isBroll = track === "broll";
  const isTransition = track === "transitions";

  els.itemFields.classList.remove("is-hidden");
  els.itemName.textContent = isTransition
    ? `Transition ${item.id}`
    : isBroll
      ? `B-roll ${item.id}`
      : `SFX ${item.id}`;
  els.itemName.className = isTransition
    ? "badge tag--transition"
    : isBroll
      ? "badge tag--broll"
      : "badge tag--sfx";

  els.itemStart.value = Number(item.start || 0).toFixed(1);
  els.itemEnd.value = Number(item.end || 0).toFixed(1);
  els.itemSourceStart.value = Number(item.source_start || 0).toFixed(1);
  els.itemPlacement.value = item.placement || "full";
  els.itemFit.value = item.fit || "cover";
  els.itemGain.value = String(Number(item.gain_db ?? -12));
  els.itemFade.value = Number(item.fade_seconds || 0).toFixed(2);
  els.itemTransitionKind.value = item.kind || "fade";
  els.itemTransitionAt.value = item.at || "start";
  els.itemTransitionDuration.value = Number(item.duration ?? 0.5).toFixed(1);

  for (const [element, visible] of [
    [els.itemSourceField, isBroll],
    [els.itemPlacementField, isBroll],
    [els.itemFitField, isBroll],
    [els.itemGainField, !isBroll && !isTransition],
    [els.itemFadeField, !isBroll && !isTransition],
    [els.itemTransitionKindField, isTransition],
    [els.itemTransitionAtField, isTransition],
    [els.itemTransitionDurationField, isTransition],
  ]) {
    element.classList.toggle("is-hidden", !visible);
  }

  // A transition lives at an edge of the clip, so it has no in/out of its own.
  els.itemStart.closest(".field").classList.toggle("is-hidden", isTransition);
  els.itemEnd.closest(".field").classList.toggle("is-hidden", isTransition);

  els.itemStatus.textContent = isTransition
    ? `${transitionLabel(item.kind)} ${item.at === "end" ? "out of" : "into"} the clip ` +
      `over ${Number(item.duration ?? 0).toFixed(1)}s.`
    : isBroll
      ? `Overlaying ${assetLabelFor(item.source)} from ${Number(item.source_start || 0).toFixed(1)}s, ${item.placement || "full"} frame, ${item.fit || "cover"}.`
      : `Mixing ${assetLabelFor(item.asset_id)} at ${Number(item.gain_db ?? -12)} dB with a ${Number(item.fade_seconds || 0).toFixed(2)}s fade.`;
  hideItemError();
}

/** Save one field of the selected item, keeping the rest of the track as it is. */
async function updateSelectedItem(changes) {
  const found = selectedTimelineItem();
  const timeline = currentTimeline();
  if (!found || !timeline) return;

  const { track, item } = found;
  const updated = timeline[track].map((entry) =>
    String(entry.id) === String(item.id) ? { ...entry, ...changes } : entry
  );

  await saveTimeline({ [track]: updated });
  renderItemPanel();
}

async function removeSelectedItem() {
  const found = selectedTimelineItem();
  const timeline = currentTimeline();
  if (!found || !timeline) return;

  const { track, item } = found;
  state.selectedItem = null;
  await saveTimeline({
    [track]: timeline[track].filter((entry) => String(entry.id) !== String(item.id)),
  });
  renderItemPanel();
}

/* ------------------------------------------------ automatic b-roll */

function showBrollError(message) {
  els.brollError.textContent = message;
  els.brollError.classList.remove("is-hidden");
}

function hideBrollError() {
  els.brollError.textContent = "";
  els.brollError.classList.add("is-hidden");
}

function updateBrollControls() {
  const ready = isRealProject() && Boolean(state.timeline) && !state.broll.busy;
  els.btnBrollSuggest.disabled = !ready;
  els.btnBrollAuto.disabled = !ready;
  els.btnBrollAuto.title = !isRealProject()
    ? "Import a video first — B-roll follows a clip's transcript."
    : !state.timeline
      ? "Select a clip first — the search follows its transcript."
      : state.broll.busy
        ? "A B-roll pass is already running."
        : "Search stock videos and photos for this clip and place them on the timeline.";
}

function resetBroll(note) {
  state.broll = { proposals: [], payload: null, selected: [], busy: false };
  els.brollProposals.replaceChildren();
  els.brollCount.textContent = "0";
  hideBrollError();
  els.brollStatus.textContent = note || "";
  updateBrollControls();
}

/** Render the proposed insert times and where each one would come from. */
function renderBrollProposals() {
  els.brollProposals.replaceChildren();
  const placed = state.broll.selected || [];
  const proposals = state.broll.proposals || [];
  els.brollCount.textContent = String(placed.length || proposals.length);

  for (const item of placed) {
    const entry = document.createElement("li");
    entry.className = "list__item";

    const time = document.createElement("p");
    time.className = "list__title";
    time.textContent = `${Number(item.start).toFixed(1)}s to ${Number(item.end).toFixed(1)}s · ${item.media_type === "photo" ? "still image" : "video"}`;
    entry.append(time);

    const detail = document.createElement("p");
    detail.className = "panel__hint";
    detail.textContent = `${item.label || "Stock media"} — ${item.reason || ""}`;
    entry.append(detail);

    // Pexels' terms require the credit to appear where the media is shown.
    const credit = document.createElement("p");
    credit.className = "panel__hint";
    if (item.page_url) {
      const link = document.createElement("a");
      link.href = item.page_url;
      link.target = "_blank";
      link.rel = "noreferrer noopener";
      link.textContent = item.photographer || "View on Pexels";
      credit.append(link, document.createTextNode(" · Media provided by Pexels"));
    } else {
      credit.textContent = "From your own library";
    }
    entry.append(credit);

    els.brollProposals.append(entry);
  }

  for (const proposal of proposals) {
    const item = document.createElement("li");
    item.className = "list__item";

    const time = document.createElement("p");
    time.className = "list__title";
    time.textContent = `${Number(proposal.start).toFixed(1)}s to ${Number(proposal.end).toFixed(1)}s · ${proposal.duration}s`;
    item.append(time);

    const reason = document.createElement("p");
    reason.className = "panel__hint";
    reason.textContent = `${proposal.source?.label || "No source"} — ${proposal.reason}`;
    item.append(reason);

    const source = proposal.source || {};
    if (source.type === "local") {
      const button = document.createElement("button");
      button.className = "btn btn-secondary";
      button.type = "button";
      button.textContent = "Add this insert";
      button.addEventListener("click", () => applyBrollProposal(proposal));
      item.append(button);
    } else if (source.type === "online") {
      const button = document.createElement("button");
      button.className = "btn btn-ghost";
      button.type = "button";
      button.textContent = `Find stock for “${proposal.query}”`;
      button.addEventListener("click", () => {
        els.stockQuery.value = proposal.query;
        searchStock();
      });
      item.append(button);
    }

    els.brollProposals.append(item);
  }
}

/**
 * Add B-Rolls: search the clip's transcript, download the best match for each
 * moment, and place them on the timeline in one pass.
 *
 * This is the whole workflow the button promises — analysis, stock search,
 * download, and timeline insertion — so the export uses the result rather than
 * only the panel showing it.
 */
async function addBrolls() {
  if (!isRealProject() || !state.timeline || state.broll.busy) return;

  state.broll.busy = true;
  hideBrollError();
  els.brollStatus.textContent =
    "Reading the clip's transcript, searching stock video and photos, and downloading the best match for each moment…";
  updateBrollControls();

  try {
    const response = await fetch(
      `/api/projects/${encodeURIComponent(state.project.id)}/broll/auto`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          clip_id: state.timeline.clip_id,
          mode: els.brollMode.value,
          max_items: Number(els.brollMax.value) || 3,
        }),
      }
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(
        apiErrorMessage(payload, `Could not add B-roll (HTTP ${response.status}).`)
      );
    }

    const summary = [payload.note, payload.stock_note].filter(Boolean).join(" ");
    log(
      `Add B-Rolls: ${payload.added ?? 0} insert(s) placed, ` +
        `${payload.downloaded ?? 0} downloaded, ${payload.reused ?? 0} reused.`
    );

    // The timeline is what the renderer reads, so reload it from the server
    // rather than assuming the local copy matches — and show the result *after*
    // that reload, because loading a timeline resets this panel.
    await loadTimeline(state.project.id, state.timeline.clip_id);
    await fetchAssets(state.project.id);

    state.broll.selected = Array.isArray(payload.selected) ? payload.selected : [];
    // A placed set supersedes the earlier draft proposals.
    state.broll.proposals = [];
    state.broll.payload = payload;
    renderBrollProposals();
    els.brollStatus.textContent = summary;
  } catch (error) {
    showBrollError(error.message);
  } finally {
    state.broll.busy = false;
    updateBrollControls();
  }
}

/** Ask the backend where B-roll should go, for how long, and from where. */
async function suggestBroll() {
  if (!isRealProject() || !state.timeline) return;
  hideBrollError();
  els.btnBrollSuggest.disabled = true;
  els.brollStatus.textContent = "Reading the transcript…";

  try {
    const response = await fetch(
      `/api/projects/${encodeURIComponent(state.project.id)}/broll/propose`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          clip_id: state.timeline.clip_id,
          mode: els.brollMode.value,
          max_items: Number(els.brollMax.value) || 3,
        }),
      }
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(apiErrorMessage(payload, `Could not suggest B-roll (HTTP ${response.status}).`));
    }

    state.broll = { proposals: payload.items || [], payload, selected: [], busy: false };
    renderBrollProposals();
    els.brollStatus.textContent = [payload.note, payload.stock_note]
      .filter(Boolean)
      .join(" ");
  } catch (error) {
    showBrollError(error.message);
    els.brollStatus.textContent = "";
  } finally {
    updateBrollControls();
  }
}

/** Place one accepted proposal through the validated timeline path. */
async function applyBrollProposal(proposal) {
  if (!isRealProject() || !state.timeline) return;
  hideBrollError();
  els.btnBrollSuggest.disabled = true;

  try {
    const response = await fetch(
      `/api/projects/${encodeURIComponent(state.project.id)}/broll/apply`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          clip_id: state.timeline.clip_id,
          items: [{
            asset_id: proposal.source?.asset_id,
            start: proposal.start,
            end: proposal.end,
          }],
        }),
      }
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(apiErrorMessage(payload, `Could not place B-roll (HTTP ${response.status}).`));
    }

    els.brollStatus.textContent = payload.note || "Placed.";
    await loadTimeline(state.project.id, state.timeline.clip_id);
  } catch (error) {
    showBrollError(error.message);
  } finally {
    updateBrollControls();
  }
}

/** Place the selected asset on the B-roll track, starting at the playhead. */
async function addBrollAtPlayhead() {
  const timeline = currentTimeline();
  if (!timeline) return;

  const asset = activeAsset();
  if (!asset || asset.kind !== "broll") {
    showItemError("Select a B-roll asset in the local asset library first.");
    return;
  }

  const start = round3(playheadInClip());
  const room = round3(Math.max(0, timeline.duration - start));
  const length = round3(Math.min(3, Number(asset.duration) || 3, room));

  if (length < 0.5) {
    showItemError(
      "There is no room left in the clip for an insert. Move the playhead earlier or trim the clip."
    );
    return;
  }

  const item = {
    source: asset.id,
    source_start: 0,
    source_end: length,
    start,
    end: round3(start + length),
    placement: "full",
    fit: "cover",
  };

  const saved = await saveTimeline({ broll: [...timeline.broll, item] });
  if (saved) {
    const added = saved.broll[saved.broll.length - 1];
    selectTimelineItem("broll", added.id);
    log(`Placed B-roll '${asset.label || asset.id}' at ${start.toFixed(1)}s.`);
  }
}

/** Place the selected asset on the sound-effect track, starting at the playhead. */
async function addSfxAtPlayhead() {
  const timeline = currentTimeline();
  if (!timeline) return;

  const asset = activeAsset();
  if (!asset || asset.kind !== "sfx") {
    showItemError("Select a sound-effect asset in the local asset library first.");
    return;
  }

  const start = round3(playheadInClip());
  const room = round3(Math.max(0, timeline.duration - start));
  const length = round3(Math.min(2, Number(asset.duration) || 2, room));

  if (length < 0.2) {
    showItemError(
      "There is no room left in the clip for a sound effect. Move the playhead earlier or trim the clip."
    );
    return;
  }

  const item = {
    asset_id: asset.id,
    start,
    end: round3(start + length),
    // Default under the voice, which is why the ceiling is below full scale.
    gain_db: -12,
    fade_seconds: 0.15,
  };

  const saved = await saveTimeline({ sfx: [...timeline.sfx, item] });
  if (saved) {
    const added = saved.sfx[saved.sfx.length - 1];
    selectTimelineItem("sfx", added.id);
    log(`Placed sound effect '${asset.label || asset.id}' at ${start.toFixed(1)}s.`);
  }
}

/**
 * Add a transition to the selected clip.
 *
 * A transition belongs to an edge rather than a point, so there is nothing for the
 * playhead to decide here: the kind comes from the inspector's last choice, the
 * edge from where the playhead sits (early in the clip means "in"), and the length
 * is half a second unless the clip is too short to hold it. The backend validates
 * the result, so an over-long request comes back with an explanation rather than
 * being silently clamped here and stored as something the user did not ask for.
 */
async function addTransition() {
  const timeline = currentTimeline();
  if (!timeline) return;

  const duration = Number(timeline.duration) || 0;
  if (duration < 0.6) {
    showItemError(
      `A transition needs more than a moment to read. This clip is ${duration.toFixed(1)}s long.`
    );
    return;
  }

  const kind = els.itemTransitionKind.value || "fade";
  const at = playheadInClip() <= duration / 2 ? "start" : "end";
  const length = round3(Math.min(0.5, duration / 2));

  const saved = await saveTimeline({ transitions: [...timeline.transitions, { kind, at, duration: length }] });
  if (saved) {
    const added = saved.transitions[saved.transitions.length - 1];
    selectTimelineItem("transitions", added.id);
    log(`Added a ${transitionLabel(kind)} ${added.at === "end" ? "out of" : "into"} the clip over ${added.duration}s.`);
  }
}

/* ------------------------------------------------------ automatic sound design */

/**
 * Mirror the saved sound-design settings into the panel.
 *
 * The proposals the backend computed are shown as text rather than as stored
 * items, so an automatic effect is never a surprise: the user can see exactly what
 * the next render would add, and switch it off.
 */
function renderSoundDesign(timeline) {
  const settings = timeline?.sound_design;
  const on = Boolean(settings?.enabled);

  els.soundDesignEnabled.value = on ? "true" : "false";
  els.soundDesignKind.value = settings?.kind || "whoosh";
  els.soundDesignCount.value = String(settings?.max_effects ?? 2);

  els.soundDesignKindField.classList.toggle("is-hidden", !on);
  els.soundDesignCountField.classList.toggle("is-hidden", !on);

  els.soundDesignState.textContent = on ? "On" : "Off";
  els.soundDesignState.className = on ? "badge badge-ok" : "badge badge-muted";

  const proposals = settings?.proposed || [];
  if (!timeline) {
    els.soundDesignStatus.textContent = "Select a clip to see what automatic sound design would add.";
  } else if (!on) {
    els.soundDesignStatus.textContent =
      "No automatic effects will be added. The clip exports exactly as it is on screen.";
  } else if (proposals.length === 0) {
    els.soundDesignStatus.textContent =
      "Nothing to mark yet — automatic effects are only placed where the picture already changes, so add a transition or B-roll first.";
  } else {
    els.soundDesignStatus.textContent =
      `The next export adds ${proposals.length} ` +
      `${proposals[0].kind === "pop" ? "pop" : "whoosh"} effect(s) at ` +
      `${proposals.map((effect) => `${Number(effect.start).toFixed(1)}s`).join(", ")}, ` +
      "mixed under the speech.";
  }
}

/** Save one sound-design setting through the same validated path as any edit. */
async function saveSoundDesign(changes) {
  const timeline = currentTimeline();
  if (!timeline) return;

  const saved = await saveTimeline({
    sound_design: { ...(timeline.sound_design || {}), ...changes, proposed: undefined },
  });
  if (saved) {
    log(
      changes.enabled === false
        ? "Automatic sound design switched off."
        : `Automatic sound design saved: up to ${changes.max_effects ?? saved.sound_design.max_effects} ${
            saved.sound_design.kind
          } effect(s).`
    );
  }
}

/** Explain what the two placement buttons will use, and why they may be off. */
function updateTimelinePlacement() {
  const timeline = currentTimeline();
  const asset = activeAsset();
  const clip = Boolean(timeline) && !state.timelineSaving && !state.export.jobId;

  const brollAsset = asset?.kind === "broll" ? asset : null;
  const sfxAsset = asset?.kind === "sfx" ? asset : null;

  els.btnAddBroll.disabled = !clip || !brollAsset;
  els.btnAddBroll.title = !clip
    ? "Select a clip first — items are placed on the selected clip."
    : brollAsset
      ? `Add '${brollAsset.label || brollAsset.id}' at the playhead.`
      : "Select a B-roll asset in the local asset library first.";

  els.btnAddSfx.disabled = !clip || !sfxAsset;
  els.btnAddSfx.title = !clip
    ? "Select a clip first — items are placed on the selected clip."
    : sfxAsset
      ? `Add '${sfxAsset.label || sfxAsset.id}' at the playhead.`
      : "Select a sound-effect asset in the local asset library first.";

  els.btnAddTransition.disabled = !clip;
  els.btnAddTransition.title = !clip
    ? "Select a clip first — a transition belongs to the selected clip."
    : `Add a ${transitionLabel(els.itemTransitionKind.value || "fade").toLowerCase()} at the ` +
      `${playheadInClip() <= (Number(timeline?.duration) || 0) / 2 ? "start" : "end"}.`;

  if (!isRealProject()) {
    els.timelinePlacement.textContent =
      "Open an imported project to place B-roll and sound effects.";
  } else if (!timeline) {
    els.timelinePlacement.textContent = "Select a clip to place items on it.";
  } else if (asset) {
    els.timelinePlacement.textContent =
      `Next placement uses ${asset.label || asset.id} (${asset.kind === "broll" ? "B-roll" : "SFX"}).`;
  } else {
    els.timelinePlacement.textContent =
      "Select an asset in the library to place it at the playhead.";
  }
}

function paintTimelineStatus(timeline, duration) {
  if (!timeline) {
    els.timelineSummary.textContent = "No clip selected.";
    els.timelineStatus.textContent = isRealProject()
      ? "Select a clip above to load its edit."
      : "Sample project — the timeline shows layout only.";
    return;
  }

  els.timelineSummary.textContent =
    `${formatTimecode(timeline.video.source_start)} → ${formatTimecode(timeline.video.source_end)} ` +
    `(${timeline.duration.toFixed(1)}s of ${formatTimecode(duration)}) · ${timeline.render.fps} fps · ` +
    `${timeline.render.width} × ${timeline.render.height}`;

  const notes = [];
  if (timeline.captions?.enabled) notes.push(`${timeline.captions.preset} captions`);
  else notes.push("captions off");
  notes.push(`${timeline.broll.length} B-roll item(s)`);
  notes.push(`${timeline.sfx.length} effect(s)`);
  if (timeline.transitions.length) {
    notes.push(
      `${timeline.transitions.length} transition(s): ` +
        timeline.transitions.map((item) => `${transitionLabel(item.kind).toLowerCase()} ${
          item.at === "end" ? "out" : "in"
        }`).join(", ")
    );
  }
  if (timeline.sound_design?.enabled) {
    notes.push(`up to ${timeline.sound_design.max_effects} automatic effect(s)`);
  }
  els.timelineStatus.textContent = `Editing ${timeline.clip_title || timeline.clip_id}: ${notes.join(", ")}.`;
}

/** Move the playhead markers to match the preview video. */
function paintPlayheads() {
  const source = state.project.source;
  if (!source?.duration) return;
  const ratio = Math.min(1, Math.max(0, (Number(els.video.currentTime) || 0) / source.duration));
  for (const playhead of els.timelineTracks.querySelectorAll('[data-role="playhead"]')) {
    playhead.style.left = `${ratio * 100}%`;
  }
}

function updateTimelineControls() {
  const editable = Boolean(currentTimeline()) && !state.timelineSaving && !state.export.jobId;
  const reason = !isRealProject()
    ? "Import a video and select a clip first — the timeline edits a real project."
    : !state.selectedId
      ? "Select a clip first — the in point sits inside the selected clip."
      : "Move the selected clip's in point to the current playhead position.";

  for (const [button, title] of [
    [els.btnTimelineIn, reason],
    [els.btnTimelineOut, reason.replace("in point", "out point")],
  ]) {
    button.disabled = !editable;
    button.title = editable ? title : reason;
  }

  updateTimelinePlacement();
  renderItemPanel();
}

/* --------------------------------------------------------- timeline edits */

async function loadTimeline(projectId, clipId, { quiet = false } = {}) {
  if (!isRealProject() || !clipId) {
    state.timeline = null;
    renderTimeline();
    return null;
  }

  if (!quiet) {
    els.timelineStatus.textContent = "Loading the saved edit…";
    hideTimelineError();
  }


  try {
    const response = await fetch(
      `/api/projects/${encodeURIComponent(projectId)}/timeline?clip_id=${encodeURIComponent(clipId)}`,
      { headers: { Accept: "application/json" } }
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(apiErrorMessage(payload, `Could not load the timeline (HTTP ${response.status}).`));
    }
    if (state.project.id !== projectId || state.selectedId !== clipId) return null;

    // A different clip brings different items, so nothing stays selected.
    if (state.selectedItem && state.timeline?.clip_id !== payload.clip_id) {
      state.selectedItem = null;
    }
    state.timeline = payload;
    renderTimeline();
    setTimelineSaveState("Saved edit", "badge status-ready");
    resetBroll("Suggest B-roll follows what this clip's transcript says.");
    return payload;
  } catch (error) {
    state.timeline = null;
    renderTimeline();
    showTimelineError(error.message);
    setTimelineSaveState("Unavailable", "badge status-error");
    return null;
  }
}

/**
 * Save an edit, one at a time.
 *
 * Drag, the inspector fields, and the caption controls can all fire quickly, and
 * two overlapping requests could otherwise be applied out of order — which would
 * leave the picture showing an edit that was not the most recent one. Chaining
 * them keeps the saved edit and the interface in the same order.
 */
function saveTimeline(patch, options) {
  const next = state.timelineQueue.then(
    () => sendTimelinePatch(patch, options),
    () => sendTimelinePatch(patch, options)
  );
  state.timelineQueue = next.then(
    () => undefined,
    () => undefined
  );
  return next;
}

/**
 * Send one edit and adopt what the backend stored.
 *
 * The response is the same document the next render reads, so the interface
 * shows the saved edit rather than the attempt.
 */
async function sendTimelinePatch(patch, { reloadCaptions = false } = {}) {
  const projectId = state.project.id;
  if (!isRealProject() || !state.timeline) return null;

  state.timelineSaving = true;
  setTimelineSaveState("Saving…", "badge badge-demo");
  updateTimelineControls();

  try {
    const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/timeline`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ clip_id: state.timeline.clip_id, ...patch }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(apiErrorMessage(payload, `Could not save the edit (HTTP ${response.status}).`));
    }
    if (state.project.id !== projectId) return null;

    state.timeline = payload;
    hideTimelineError();
    setTimelineSaveState("Saved edit", "badge status-ready");

    // The edit changed the clip, so the clip list and inspector follow it.
    const candidate = state.project.candidates.find((item) => item.id === payload.clip_id);
    if (candidate) {
      candidate.start = payload.video.source_start;
      candidate.end = payload.video.source_end;
      candidate.edited = true;
      renderCandidates();
    }

    els.clipStart.value = payload.video.source_start.toFixed(1);
    els.clipEnd.value = payload.video.source_end.toFixed(1);
    updateClipDuration();
    applyExportSettingsToControls(payload.render);
    if (payload.captions) applyStyleToControls(payload.captions);
    els.exportNotes.textContent = (payload.notes || []).join(" ");

    renderTimeline();
    updateExportControls();
    log(
      `Saved timeline: ${payload.video.source_start.toFixed(1)}s → ` +
        `${payload.video.source_end.toFixed(1)}s at ${payload.render.fps} fps.`
    );

    if (reloadCaptions) await loadCaptionPreview();
    return payload;
  } catch (error) {
    log(`Error: ${error.message}`);
    setTimelineSaveState("Not saved", "badge status-error");
    // Re-read the stored edit so the timeline never shows a rejected change, but
    // quietly: the explanation of what was refused has to stay on screen.
    const clipId = state.timeline?.clip_id;
    if (clipId) await loadTimeline(projectId, clipId, { quiet: true });
    showTimelineError(error.message);
    return null;
  } finally {
    state.timelineSaving = false;
    updateTimelineControls();
  }
}

/** Trim or move the clip block by dragging it on the timeline. */
function beginClipDrag(event, mode) {
  const timeline = currentTimeline();  
  const source = state.project.source;
  if (!timeline || !source?.duration) return;

  event.preventDefault();
  event.stopPropagation();

  const lane = event.currentTarget.closest(".timeline__lane");
  const secondsPerPixel = source.duration / Math.max(1, lane.getBoundingClientRect().width);
  const startX = event.clientX;
  const original = { in: timeline.video.source_start, out: timeline.video.source_end };
  let pending = { ...original };

  const apply = (clientX) => {
    const delta = (clientX - startX) * secondsPerPixel;
    let inPoint = original.in;
    let outPoint = original.out;

    if (mode === "in") {
      inPoint = Math.min(Math.max(0, original.in + delta), original.out - MIN_CLIP_SECONDS);
    } else if (mode === "out") {
      outPoint = Math.max(Math.min(source.duration, original.out + delta), original.in + MIN_CLIP_SECONDS);
    } else {
      const width = original.out - original.in;
      inPoint = Math.min(Math.max(0, original.in + delta), Math.max(0, source.duration - width));
      outPoint = inPoint + width;
    }

    pending = { in: round3(inPoint), out: round3(outPoint) };
    timeline.video.source_start = pending.in;
    timeline.video.source_end = pending.out;
    timeline.duration = round3(pending.out - pending.in);
    els.clipStart.value = pending.in.toFixed(1);
    els.clipEnd.value = pending.out.toFixed(1);
    updateClipDuration();
    renderTimeline();
    setTimelineSaveState("Dragging…", "badge badge-demo");
  };

  const move = (moveEvent) => apply(moveEvent.clientX);
  const finish = async () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", finish);
    window.removeEventListener("pointercancel", finish);

    if (pending.in === original.in && pending.out === original.out) {
      setTimelineSaveState("Saved edit", "badge status-ready");
      return;
    }

    await saveTimeline(
      { video: { source_start: pending.in, source_end: pending.out } },
      { reloadCaptions: true }
    );
  };

  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", finish);
  window.addEventListener("pointercancel", finish);
}

/** Seek the preview by clicking the ruler. */
async function seekFromRuler(event) {
  const source = state.project.source;
  if (!source?.duration || !els.video.src) return;

  const rect = els.timelineRuler.getBoundingClientRect();
  const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / Math.max(1, rect.width)));
  els.video.currentTime = ratio * source.duration;
  paintPlayheads();
}

/** Move one edge of the clip to wherever the playhead is. */
async function setClipEdgeFromPlayhead(edge) {
  const timeline = currentTimeline();
  const source = state.project.source;
  if (!timeline || !source?.duration) return;

  const playhead = Math.min(source.duration, Math.max(0, Number(els.video.currentTime) || 0));
  const patch = {};

  if (edge === "in") {
    if (playhead >= timeline.video.source_end - MIN_CLIP_SECONDS) {
      showTimelineError(
        `The in point must stay ${MIN_CLIP_SECONDS}s before the out point. Move the playhead earlier.`
      );
      return;
    }
    patch.video = { source_start: round3(playhead) };
  } else {
    if (playhead <= timeline.video.source_start + MIN_CLIP_SECONDS) {
      showTimelineError(
        `The out point must stay ${MIN_CLIP_SECONDS}s after the in point. Move the playhead later.`
      );
      return;
    }
    patch.video = { source_end: round3(playhead) };
  }

  await saveTimeline(patch, { reloadCaptions: true });
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
  // The timeline playhead follows the preview, so trimming happens where the
  // user is actually looking.
  paintPlayheads();
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
    // The user can move the captions; the backend clamps it into the safe area.
    position: Number(els.captionPosition.value),
    font_family: els.captionFontFamily.value,
    animation: els.captionAnimation.value,
    highlighting: els.captionHighlighting.checked,
    uppercase: els.captionUppercase.checked,
  };
}

/** Show the chosen caption position as human-readable text. */
function updateCaptionPositionLabel() {
  const value = Number(els.captionPosition.value) || 0;
  const edge = els.captionPlacement.value === "top" ? "the top" : "the bottom";
  els.captionPositionValue.textContent = `${value}% from ${edge}`;
}

/** The export choices currently in the inspector. */
function exportSettingsFromControls() {
  return {
    aspect_ratio: els.aspectRatio.value,
    resolution: els.outputResolution.value,
    format: els.exportFormat.value,
    fps: Number(els.outputFps.value) || 30,
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
  if (value.position !== undefined && value.position !== null) {
    els.captionPosition.value = String(value.position);
  }
  if (value.font_family) els.captionFontFamily.value = value.font_family;
  if (value.animation) els.captionAnimation.value = value.animation;
  els.captionHighlighting.checked = value.highlighting !== false;
  els.captionUppercase.checked = Boolean(value.uppercase);
  els.highContrast.checked = Boolean(value.high_contrast);
  updateCaptionPositionLabel();
}

function applyExportSettingsToControls(settings) {
  const value = settings || {};
  if (value.aspect_ratio) els.aspectRatio.value = value.aspect_ratio;
  if (value.format) els.exportFormat.value = value.format;
  if (value.fps) els.outputFps.value = String(value.fps);
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
  if (geometry.position_percent) bits.push(`${geometry.position_percent}% from the edge`);
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

  // With a clip selected these settings are part of that clip's timeline, so one
  // save keeps the timeline, the inspector, and the next render in agreement.
  if (state.timeline) {
    await saveTimeline(
      {
        captions: captionStyleFromControls(),
        render: exportSettingsFromControls(),
      },
      { reloadCaptions }
    );
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

  // A running render also locks the timeline, so the edit cannot change under it.
  updateTimelineControls();
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
  state.timeline = null;
  state.selectedItem = null;
  state.activeAssetId = null;
  fetchAssets(payload.project_id);
  loadStockStatus(payload.project_id);
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
  state.timeline = null;
  state.selectedItem = null;
  state.assets = [];
  state.activeAssetId = null;
  renderAssetList();
  resetStock("Open or import a project to search stock B-roll.");
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
  setTimelineSaveState("Sample data", "badge badge-demo");
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

els.btnTimelineIn.addEventListener("click", () => setClipEdgeFromPlayhead("in"));
els.btnTimelineOut.addEventListener("click", () => setClipEdgeFromPlayhead("out"));
els.btnAddBroll.addEventListener("click", addBrollAtPlayhead);
els.btnBrollSuggest.addEventListener("click", suggestBroll);
els.btnBrollAuto.addEventListener("click", addBrolls);
els.btnAddSfx.addEventListener("click", addSfxAtPlayhead);
els.btnAddTransition.addEventListener("click", addTransition);

// The transition fields edit the *selected* transition, so changing the kind is
// also how the "+ Transition" button decides what kind to add next.
for (const [name, field, parse] of [
  ["itemTransitionKind", "kind", String],
  ["itemTransitionAt", "at", String],
  ["itemTransitionDuration", "duration", Number],
]) {
  els[name].addEventListener("change", () => {
    const raw = els[name].value;
    const value = parse === Number ? Number.parseFloat(raw) : raw;
    if (parse === Number && !Number.isFinite(value)) {
      showItemError(`${field} must be a number.`);
      renderItemPanel();
      return;
    }
    updateSelectedItem({ [field]: value });
  });
}

els.itemTransitionKind.addEventListener("change", updateTimelinePlacement);

els.soundDesignEnabled.addEventListener("change", () => {
  saveSoundDesign({ enabled: els.soundDesignEnabled.value === "true" });
});
els.soundDesignKind.addEventListener("change", () => {
  saveSoundDesign({ kind: els.soundDesignKind.value });
});
els.soundDesignCount.addEventListener("change", () => {
  saveSoundDesign({ max_effects: Number(els.soundDesignCount.value) });
});
els.btnItemDelete.addEventListener("click", removeSelectedItem);

// Item fields only send what changed, so the rest of the item is left as stored.
for (const [name, field, parse] of [
  ["itemStart", "start", Number],
  ["itemEnd", "end", Number],
  ["itemSourceStart", "source_start", Number],
  ["itemGain", "gain_db", Number],
  ["itemFade", "fade_seconds", Number],
  ["itemPlacement", "placement", String],
  ["itemFit", "fit", String],
]) {
  els[name].addEventListener("change", () => {
    const raw = els[name].value;
    const value = parse === Number ? Number.parseFloat(raw) : raw;
    if (parse === Number && !Number.isFinite(value)) {
      showItemError(`${field} must be a number.`);
      renderItemPanel();
      return;
    }
    updateSelectedItem({ [field]: value });
  });
}

/* --------------------------------------------------------- asset panel */

els.btnImportBroll.addEventListener("click", () => {
  if (!isRealProject() || state.assetBusy) return;
  state.pendingAssetKind = "broll";
  els.assetFileInput.accept =
    "video/mp4,video/quicktime,video/x-m4v,video/x-msvideo,video/x-matroska,video/webm";
  els.assetFileInput.click();
});

els.btnImportSfx.addEventListener("click", () => {
  if (!isRealProject() || state.assetBusy) return;
  state.pendingAssetKind = "sfx";
  els.assetFileInput.accept = "audio/mpeg,audio/wav,audio/mp4,audio/aac,audio/ogg,audio/flac,.mp3,.wav,.m4a,.aac,.ogg,.flac";
  els.assetFileInput.click();
});

els.assetFileInput.addEventListener("change", () => {
  const file = els.assetFileInput.files?.[0];
  importAsset(file, state.pendingAssetKind || "broll");
});

els.assetSearch.addEventListener("input", renderAssetList);
els.assetKindFilter.addEventListener("change", renderAssetList);
els.btnStockSearch.addEventListener("click", searchStock);
els.stockQuery.addEventListener("keydown", (event) => {
  if (event.key === "Enter") searchStock();
});
els.timelineRuler.addEventListener("click", seekFromRuler);
els.timelineRuler.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") seekFromRuler(event);
});

// Caption controls change the burned-in text, so the preview is rebuilt from the
// backend-validated cues instead of guessing locally what will be rendered.
for (const name of ["captionStyle", "fontSize", "captionPlacement", "highlightMode", "lineLength", "captionPosition", "captionFontFamily", "captionAnimation", "captionHighlighting", "captionUppercase", "highContrast"]) {
  els[name].addEventListener("change", async () => {
    updateCaptionPositionLabel();
    if (!isRealProject()) {
      renderCaptionPreview(null, "Sample project — settings are not saved.");
      return;
    }
    await saveProjectSettings({ reloadCaptions: true });
  });
}

// The range input updates its readout while the handle is still being dragged.
els.captionPosition.addEventListener("input", updateCaptionPositionLabel);
els.captionPlacement.addEventListener("change", updateCaptionPositionLabel);

els.aspectRatio.addEventListener("change", async () => {
  // Keep the resolution list consistent with the chosen ratio before saving.
  syncResolutionOptions();
  await saveProjectSettings();
  updateExportControls();
});

for (const name of ["outputResolution", "outputFps", "exportFormat", "exportCaptionMode", "exportWriteSrt"]) {
  els[name].addEventListener("change", async () => {
    await saveProjectSettings();
    updateExportControls();
  });
}

function resetToEmptyProject() {
  state.project = { ...EMPTY_PROJECT };
  state.selectedId = null;
  state.timeline = null;
  state.selectedItem = null;
  state.assets = [];
  state.activeAssetId = null;
  renderAssetList();
  resetStock("Import a video first — stock search needs a project.");
  resetBroll("Import a video first — B-roll follows a clip's transcript.");
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
  setTimelineSaveState("No edit", "badge badge-muted");
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
