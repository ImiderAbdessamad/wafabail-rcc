/* UI Wafabail RCC — upload PDF, job SSE, affichage des 20 postes EKIP */

(function () {
  "use strict";

  const API_JOBS = "/api/v1/rcc/jobs";
  const MAX_BYTES = 50 * 1024 * 1024;

  const dropzone = document.getElementById("dropzone");
  const dropzoneEmpty = document.getElementById("dropzoneEmpty");
  const previewFile = document.getElementById("previewFile");
  const previewFileName = document.getElementById("previewFileName");
  const previewFileSize = document.getElementById("previewFileSize");
  const fileInput = document.getElementById("fileInput");
  const removeFile = document.getElementById("removeFile");
  const analyzeBtn = document.getElementById("analyzeBtn");
  const analyzeBtnLabel = document.getElementById("analyzeBtnLabel");
  const resetBtn = document.getElementById("resetBtn");
  const errorBanner = document.getElementById("errorBanner");
  const warningBanner = document.getElementById("warningBanner");

  const stateIdle = document.getElementById("stateIdle");
  const stateLoading = document.getElementById("stateLoading");
  const stateResult = document.getElementById("stateResult");
  const loadingText = document.getElementById("loadingText");
  const loadingHint = document.getElementById("loadingHint");
  const progressFill = document.getElementById("progressFill");
  const progressBar = document.getElementById("progressBar");
  const progressPct = document.getElementById("progressPct");
  const progressPage = document.getElementById("progressPage");

  const fieldsContainer = document.getElementById("fieldsContainer");
  const companyBadge = document.getElementById("companyBadge");
  const completenessBadge = document.getElementById("completenessBadge");
  const modelBadge = document.getElementById("modelBadge");
  const docMeta = document.getElementById("docMeta");
  const copyJsonBtn = document.getElementById("copyJsonBtn");

  let selectedFile = null;
  let lastResult = null;
  let eventSource = null;
  let analyzing = false;

  function showError(message) {
    errorBanner.textContent = message;
    errorBanner.hidden = false;
  }

  function clearError() {
    errorBanner.hidden = true;
    errorBanner.textContent = "";
  }

  function setResultState(state) {
    stateIdle.hidden = state !== "idle";
    stateLoading.hidden = state !== "loading";
    stateResult.hidden = state !== "result";
  }

  function formatFileSize(bytes) {
    if (bytes < 1024) return `${bytes} o`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} Ko`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} Mo`;
  }

  function formatAmount(value) {
    if (value == null || value === "") return "—";
    try {
      return new Intl.NumberFormat("fr-FR", {
        maximumFractionDigits: 2,
      }).format(Number(value));
    } catch {
      return String(value);
    }
  }

  function statusClass(status) {
    const s = (status || "missing").toLowerCase();
    if (s === "confirmed" || s === "derived") return "status-ok";
    if (s === "ambiguous") return "status-warn";
    if (s === "conflicting" || s === "invalid") return "status-bad";
    return "status-miss";
  }

  function statusLabel(status) {
    const map = {
      confirmed: "confirmé",
      derived: "dérivé",
      ambiguous: "ambigu",
      conflicting: "conflit",
      invalid: "invalide",
      missing: "manquant",
    };
    return map[(status || "").toLowerCase()] || status || "—";
  }

  function closeStream() {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
  }

  function setAnalyzing(active) {
    analyzing = active;
    analyzeBtn.disabled = active || !selectedFile;
    analyzeBtnLabel.textContent = active
      ? "Extraction en cours…"
      : "Extraire les champs RCC";
    resetBtn.hidden = !active && !lastResult && !selectedFile;
  }

  function updateProgress(payload) {
    const pct = Math.max(0, Math.min(100, Number(payload.progress_pct || 0)));
    progressFill.style.width = `${pct}%`;
    progressBar.setAttribute("aria-valuenow", String(pct));
    progressPct.textContent = `${pct} %`;
    loadingHint.textContent = payload.message || payload.current_step || "…";
    if (payload.current_page && payload.pages_total) {
      progressPage.textContent = `Page ${payload.current_page} / ${payload.pages_total}`;
    } else if (payload.pages_total) {
      progressPage.textContent = `${payload.pages_total} page(s)`;
    } else {
      progressPage.textContent = "—";
    }
  }

  function resetAll() {
    closeStream();
    selectedFile = null;
    lastResult = null;
    analyzing = false;
    fileInput.value = "";
    previewFile.hidden = true;
    dropzoneEmpty.hidden = false;
    removeFile.hidden = true;
    analyzeBtn.disabled = true;
    analyzeBtnLabel.textContent = "Extraire les champs RCC";
    resetBtn.hidden = true;
    clearError();
    warningBanner.hidden = true;
    fieldsContainer.innerHTML = "";
    companyBadge.hidden = true;
    docMeta.hidden = true;
    progressFill.style.width = "0%";
    setResultState("idle");
  }

  function handleFile(file) {
    clearError();
    if (!file) return;

    const name = (file.name || "").toLowerCase();
    if (!name.endsWith(".pdf") && file.type !== "application/pdf") {
      showError("Seuls les fichiers PDF sont acceptés.");
      return;
    }
    if (file.size > MAX_BYTES) {
      showError(`Fichier trop volumineux (max ${formatFileSize(MAX_BYTES)}).`);
      return;
    }

    selectedFile = file;
    dropzoneEmpty.hidden = true;
    previewFileName.textContent = file.name;
    previewFileSize.textContent = formatFileSize(file.size);
    previewFile.hidden = false;
    removeFile.hidden = false;
    analyzeBtn.disabled = false;
    resetBtn.hidden = false;
  }

  function renderResult(result) {
    lastResult = result;
    fieldsContainer.innerHTML = "";

    const company = result.document?.company?.raison_sociale;
    if (company) {
      companyBadge.textContent = company;
      companyBadge.hidden = false;
    } else {
      companyBadge.hidden = true;
    }

    const pct = result.completeness_pct ?? 0;
    completenessBadge.textContent = `Complétude ${pct} %`;
    modelBadge.textContent = result.extraction?.model
      ? result.extraction.model.split("/").pop()
      : "GLM";

    const doc = result.document || {};
    const exercise = doc.exercise?.label || doc.exercise?.fin || "";
    const parts = [
      doc.filename,
      exercise ? `Exercice ${exercise}` : null,
      `${doc.pages_processed || 0}/${doc.pages_total || 0} pages`,
    ].filter(Boolean);
    docMeta.textContent = parts.join(" · ");
    docMeta.hidden = parts.length === 0;

    const warnings = result.warnings || [];
    if (warnings.length) {
      warningBanner.textContent = warnings.slice(0, 4).join(" · ");
      warningBanner.hidden = false;
    } else {
      warningBanner.hidden = true;
    }

    (result.fields || []).forEach((field) => {
      const el = document.createElement("div");
      el.className = `field ${statusClass(field.status)}`;
      if (field.code === "TYPE_RESULTAT") {
        el.classList.add("field-wide");
      }

      const displayValue =
        field.code === "TYPE_RESULTAT"
          ? field.note || "—"
          : formatAmount(field.value);

      el.innerHTML = `
        <div class="field-top">
          <span class="field-label">${field.number}. ${escapeHtml(field.label)}</span>
          <span class="field-status">${escapeHtml(statusLabel(field.status))}</span>
        </div>
        <span class="field-value field-mono">${escapeHtml(displayValue)}</span>
        ${
          field.note && field.code !== "TYPE_RESULTAT"
            ? `<span class="field-note">${escapeHtml(field.note)}</span>`
            : ""
        }
      `;
      fieldsContainer.appendChild(el);
    });

    setResultState("result");
    setAnalyzing(false);
    resetBtn.hidden = false;
  }

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function fetchResult(jobId) {
    const res = await fetch(`${API_JOBS}/${jobId}/result`);
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `Erreur HTTP ${res.status}`);
    }
    return res.json();
  }

  function listenJob(jobId, streamUrl) {
    closeStream();
    const url = streamUrl || `${API_JOBS}/${jobId}/stream`;
    eventSource = new EventSource(url);

    const onProgress = (event) => {
      try {
        const data = JSON.parse(event.data);
        updateProgress(data);
      } catch {
        /* ignore */
      }
    };

    eventSource.addEventListener("job_status", onProgress);
    eventSource.addEventListener("page_extracted", onProgress);
    eventSource.addEventListener("page_skipped", onProgress);
    eventSource.addEventListener("page_failed", onProgress);
    eventSource.addEventListener("resolving_fields", onProgress);
    eventSource.addEventListener("running_controls", onProgress);
    eventSource.onmessage = onProgress;

    eventSource.addEventListener("result_ready", async () => {
      closeStream();
      loadingText.textContent = "Finalisation…";
      try {
        const result = await fetchResult(jobId);
        renderResult(result);
      } catch (err) {
        showError(err.message || "Impossible de récupérer le résultat.");
        setAnalyzing(false);
        setResultState("idle");
      }
    });

    eventSource.addEventListener("job_failed", (event) => {
      closeStream();
      let message = "Le job a échoué.";
      try {
        const data = JSON.parse(event.data);
        message = data.error || data.message || message;
      } catch {
        /* ignore */
      }
      showError(message);
      setAnalyzing(false);
      setResultState("idle");
    });

    eventSource.onerror = () => {
      // EventSource se reconnecte ; on poll le statut si fermé
      if (!eventSource || eventSource.readyState === EventSource.CLOSED) {
        pollUntilDone(jobId);
      }
    };
  }

  async function pollUntilDone(jobId) {
    closeStream();
    for (let i = 0; i < 120 && analyzing; i += 1) {
      try {
        const res = await fetch(`${API_JOBS}/${jobId}`);
        if (!res.ok) break;
        const progress = await res.json();
        updateProgress(progress);
        if (progress.status === "completed") {
          const result = await fetchResult(jobId);
          renderResult(result);
          return;
        }
        if (progress.status === "failed") {
          showError(progress.error || "Le job a échoué.");
          setAnalyzing(false);
          setResultState("idle");
          return;
        }
      } catch {
        /* retry */
      }
      await new Promise((r) => setTimeout(r, 2000));
    }
  }

  async function startExtraction() {
    if (!selectedFile || analyzing) return;
    clearError();
    setAnalyzing(true);
    setResultState("loading");
    loadingText.textContent = "Extraction en cours…";
    updateProgress({ progress_pct: 2, message: "Création du job…", pages_total: null });

    const form = new FormData();
    form.append("file", selectedFile);

    try {
      const res = await fetch(API_JOBS, { method: "POST", body: form });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(
          typeof detail.detail === "string"
            ? detail.detail
            : `Erreur HTTP ${res.status}`
        );
      }
      const data = await res.json();
      listenJob(data.job_id, data.stream_url);
    } catch (err) {
      showError(err.message || "Échec de la création du job.");
      setAnalyzing(false);
      setResultState("idle");
    }
  }

  // --- Events ---
  dropzone.addEventListener("click", () => {
    if (!analyzing) fileInput.click();
  });
  dropzone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (!analyzing) fileInput.click();
    }
  });
  fileInput.addEventListener("change", () => {
    if (fileInput.files?.[0]) handleFile(fileInput.files[0]);
  });

  ["dragenter", "dragover"].forEach((evt) => {
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.add("drag-active");
    });
  });
  ["dragleave", "drop"].forEach((evt) => {
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.remove("drag-active");
    });
  });
  dropzone.addEventListener("drop", (e) => {
    const file = e.dataTransfer?.files?.[0];
    if (file) handleFile(file);
  });

  removeFile.addEventListener("click", (e) => {
    e.stopPropagation();
    if (!analyzing) resetAll();
  });
  analyzeBtn.addEventListener("click", startExtraction);
  resetBtn.addEventListener("click", resetAll);

  copyJsonBtn.addEventListener("click", async () => {
    if (!lastResult) return;
    const flat = {};
    (lastResult.fields || []).forEach((f) => {
      flat[f.code] = f.code === "TYPE_RESULTAT" ? f.note : f.value;
    });
    try {
      await navigator.clipboard.writeText(JSON.stringify(flat, null, 2));
      copyJsonBtn.textContent = "Copié !";
      setTimeout(() => {
        copyJsonBtn.textContent = "Copier en JSON";
      }, 1600);
    } catch {
      showError("Impossible de copier dans le presse-papiers.");
    }
  });
})();
