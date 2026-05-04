const state = {
  imageId: null,
  currentStages: {},
  runs: [],
  selectedRuns: new Set(),
};

const stageOrder = ["original", "offset", "mask_overlay", "inpainted", "final", "2x2", "3x3"];
const stageLabels = {
  original: "Original",
  offset: "Offset",
  mask_overlay: "Mask overlay",
  inpainted: "Inpainted",
  final: "Final",
  "2x2": "2x2",
  "3x3": "3x3",
};

const els = {
  status: document.querySelector("#status"),
  dropZone: document.querySelector("#dropZone"),
  fileInput: document.querySelector("#fileInput"),
  stageGrid: document.querySelector("#stageGrid"),
  maskWidth: document.querySelector("#maskWidth"),
  maskWidthValue: document.querySelector("#maskWidthValue"),
  strength: document.querySelector("#strength"),
  strengthValue: document.querySelector("#strengthValue"),
  prompt: document.querySelector("#prompt"),
  runButton: document.querySelector("#runButton"),
  refreshHistory: document.querySelector("#refreshHistory"),
  historyList: document.querySelector("#historyList"),
  compareGrid: document.querySelector("#compareGrid"),
  lightbox: document.querySelector("#lightbox"),
  lightboxImage: document.querySelector("#lightboxImage"),
  lightboxCaption: document.querySelector("#lightboxCaption"),
  closeLightbox: document.querySelector("#closeLightbox"),
};

function setStatus(message, isError = false) {
  els.status.textContent = message;
  els.status.style.color = isError ? "var(--danger)" : "var(--muted)";
}

function selectedModel() {
  return document.querySelector("input[name='model']:checked").value;
}

function updateSliderLabels() {
  els.maskWidthValue.value = `${els.maskWidth.value}px`;
  els.strengthValue.value = Number(els.strength.value).toFixed(2);
}

async function uploadFile(file) {
  const data = new FormData();
  data.append("file", file);
  setStatus("Uploading image...");
  const response = await fetch("/api/upload", { method: "POST", body: data });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.detail || "Upload failed");
  }
  state.imageId = payload.image_id;
  state.currentStages = { original: `/uploads/${payload.image_id}.png` };
  els.runButton.disabled = false;
  renderStages();
  setStatus(`Loaded ${payload.size[0]} x ${payload.size[1]} image`);
}

async function runPipeline() {
  if (!state.imageId) return;
  els.runButton.disabled = true;
  setStatus("Running pipeline...");
  const response = await fetch("/api/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      image_id: state.imageId,
      mask_width: Number(els.maskWidth.value),
      model: selectedModel(),
      prompt: els.prompt.value,
      strength: Number(els.strength.value),
    }),
  });
  const payload = await response.json();
  if (!response.ok) {
    els.runButton.disabled = false;
    throw new Error(payload.detail || "Pipeline failed");
  }
  state.currentStages = payload.stages;
  renderStages();
  await loadHistory();
  setStatus(`Run ${payload.run_id}`);
  els.runButton.disabled = false;
}

async function loadHistory() {
  const response = await fetch("/api/runs");
  state.runs = await response.json();
  renderHistory();
  renderCompare();
}

function renderStages() {
  els.stageGrid.innerHTML = "";
  for (const name of stageOrder) {
    const src = state.currentStages[name];
    if (!src) continue;
    const figure = document.createElement("figure");
    figure.className = "stage";
    figure.innerHTML = `
      <button type="button" aria-label="Open ${stageLabels[name]}">
        <img src="${src}" alt="${stageLabels[name]} stage" />
      </button>
      <figcaption><span>${stageLabels[name]}</span><span>${name}</span></figcaption>
    `;
    figure.querySelector("button").addEventListener("click", () => openLightbox(src, stageLabels[name]));
    els.stageGrid.append(figure);
  }
}

function renderHistory() {
  els.historyList.innerHTML = "";
  if (state.runs.length === 0) {
    els.historyList.textContent = "No runs yet";
    return;
  }
  for (const run of state.runs) {
    const row = document.createElement("label");
    row.className = "history-row";
    row.innerHTML = `
      <input type="checkbox" ${state.selectedRuns.has(run.run_id) ? "checked" : ""} />
      <span>
        <strong>${run.run_id}</strong>
        mask=${run.params.mask_width}px strength=${Number(run.params.strength).toFixed(2)}
      </span>
    `;
    row.querySelector("input").addEventListener("change", (event) => {
      if (event.target.checked) {
        state.selectedRuns.add(run.run_id);
      } else {
        state.selectedRuns.delete(run.run_id);
      }
      renderCompare();
    });
    els.historyList.append(row);
  }
}

function renderCompare() {
  els.compareGrid.innerHTML = "";
  const selected = state.runs.filter((run) => state.selectedRuns.has(run.run_id));
  if (selected.length === 0) {
    els.compareGrid.textContent = "Select runs from history";
    return;
  }
  for (const run of selected) {
    const src = run.thumbnails["2x2"] || run.thumbnails.final;
    const item = document.createElement("figure");
    item.className = "compare-item";
    item.innerHTML = `
      <img src="${src}" alt="${run.run_id} preview" />
      <figcaption>
        <span>${run.run_id}</span>
        <span>${run.params.mask_width}px / ${Number(run.params.strength).toFixed(2)}</span>
      </figcaption>
    `;
    item.querySelector("img").addEventListener("click", () => openLightbox(src, run.run_id));
    els.compareGrid.append(item);
  }
}

function openLightbox(src, caption) {
  els.lightboxImage.src = src;
  els.lightboxCaption.textContent = caption;
  els.lightbox.showModal();
}

els.dropZone.addEventListener("click", () => els.fileInput.click());
els.dropZone.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") els.fileInput.click();
});
els.dropZone.addEventListener("dragover", (event) => {
  event.preventDefault();
  els.dropZone.classList.add("is-dragging");
});
els.dropZone.addEventListener("dragleave", () => els.dropZone.classList.remove("is-dragging"));
els.dropZone.addEventListener("drop", async (event) => {
  event.preventDefault();
  els.dropZone.classList.remove("is-dragging");
  const file = event.dataTransfer.files[0];
  if (file) await handleAction(() => uploadFile(file));
});
els.fileInput.addEventListener("change", async () => {
  const file = els.fileInput.files[0];
  if (file) await handleAction(() => uploadFile(file));
});
els.maskWidth.addEventListener("input", updateSliderLabels);
els.strength.addEventListener("input", updateSliderLabels);
els.runButton.addEventListener("click", () => handleAction(runPipeline));
els.refreshHistory.addEventListener("click", () => handleAction(loadHistory));
els.closeLightbox.addEventListener("click", () => els.lightbox.close());

async function handleAction(action) {
  try {
    await action();
  } catch (error) {
    setStatus(error.message, true);
  }
}

updateSliderLabels();
loadHistory();
