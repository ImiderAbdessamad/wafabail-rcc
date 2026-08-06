/* Import de liasses : dépôt PDF → job d'extraction → suivi SSE → dossier. */

import * as api from "../api.js";
import {
  $, ICONS, announce, el, formatAmount, formatFileSize,
  icon, replaceChildren, toast,
} from "../util.js";

const MAX_BYTES = 50 * 1024 * 1024;

/** Étapes de la barre de progression, alignées sur les évènements du pipeline. */
const STEP_LABELS = {
  queued: "En file d'attente",
  validating: "Validation du PDF",
  rendering: "Rendu des pages",
  classifying: "Classification des pages",
  extracting_page: "Lecture des pages",
  resolving: "Résolution des postes RCC",
  controls: "Contrôles comptables",
  completed: "Extraction terminée",
  failed: "Échec",
};

function validateFile(file) {
  const name = (file.name || "").toLowerCase();
  if (!name.endsWith(".pdf") && file.type !== "application/pdf") {
    return "Seuls les fichiers PDF sont acceptés.";
  }
  if (file.size > MAX_BYTES) {
    return `Fichier trop volumineux (max ${formatFileSize(MAX_BYTES)}).`;
  }
  if (file.size === 0) return "Le fichier est vide.";
  return null;
}

/**
 * Panneau d'import réutilisable (écran Import et onglet « Pièces & import »).
 *
 * @param {{ compact?: boolean, title?: string, onCompleted?: (jobId, result) => any }} options
 */
export function createImportPanel({ compact = false, title, onCompleted } = {}) {
  const node = el("div", { class: "import-panel" });
  const jobs = new Map(); // clé locale -> { file, status, pct, message, follower, node }
  let seq = 0;

  const fileInput = el("input", {
    type: "file",
    accept: "application/pdf,.pdf",
    multiple: true,
    hidden: true,
    onChange: (event) => {
      for (const file of event.target.files) enqueue(file);
      event.target.value = "";
    },
  });

  const dropzone = el("button", {
    type: "button",
    class: "dropzone",
    "aria-label": "Choisir une liasse fiscale au format PDF",
    onClick: () => fileInput.click(),
  }, [
    el("span", { class: "dropzone-icon" }, [icon(ICONS.upload, { size: 20, stroke: "var(--accent)", width: 1.9 })]),
    el("span", { class: "dropzone-title", text: title || "Déposez les liasses fiscales ici" }),
    el("span", {
      class: "dropzone-hint",
      text: `PDF uniquement · ${formatFileSize(MAX_BYTES)} maximum · l'extraction des postes RCC démarre automatiquement`,
    }),
  ]);

  for (const type of ["dragenter", "dragover"]) {
    dropzone.addEventListener(type, (event) => {
      event.preventDefault();
      dropzone.classList.add("is-drag");
    });
  }
  for (const type of ["dragleave", "dragend"]) {
    dropzone.addEventListener(type, () => dropzone.classList.remove("is-drag"));
  }
  dropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    dropzone.classList.remove("is-drag");
    for (const file of event.dataTransfer?.files ?? []) enqueue(file);
  });

  const listPanel = el("div", { class: "panel", style: "margin-top:14px", hidden: true });
  const listHead = el("div", { class: "panel-head" }, [el("h2", { text: "Lot en cours" })]);
  const listBody = el("div", {});
  listPanel.append(listHead, listBody);

  node.append(fileInput, dropzone, listPanel);

  /* ------------------------------------------------------------- rendu --- */

  function renderJob(job) {
    const tone = job.status === "done" ? "ok"
      : job.status === "error" ? "bad"
      : job.status === "running" || job.status === "uploading" ? "warn" : "neutral";
    const color = tone === "ok" ? "var(--ok)" : tone === "bad" ? "var(--bad)" : "var(--accent)";
    const badgeCls = tone === "ok" ? "badge-ok" : tone === "bad" ? "badge-bad"
      : tone === "warn" ? "badge-warn" : "badge-neutral";
    const badgeText = job.status === "done" ? "Prêt"
      : job.status === "error" ? "Échec"
      : job.status === "uploading" ? `Envoi ${job.pct} %`
      : `${job.pct} %`;

    const bar = el("div", { class: "progress" }, [
      el("div", {
        class: `progress-fill${job.status === "done" ? " is-ok" : ""}${job.status === "error" ? " is-bad" : ""}${job.indeterminate ? " is-indeterminate" : ""}`,
        style: `width:${job.status === "error" ? 100 : job.pct}%`,
      }),
    ]);

    const actions = [];
    if (job.status === "error") {
      actions.push(el("button", {
        type: "button", class: "btn btn-ghost btn-sm", text: "Réessayer",
        onClick: () => { jobs.delete(job.key); job.node.remove(); enqueue(job.file); },
      }));
    }
    if (job.status === "running" || job.status === "uploading") {
      actions.push(el("button", {
        type: "button", class: "btn btn-ghost btn-sm", text: "Annuler",
        onClick: () => cancel(job),
      }));
    }
    if (job.status === "done" && job.dossierId && !compact) {
      actions.push(el("button", {
        type: "button", class: "btn btn-soft btn-sm", text: "Contrôler →",
        onClick: () => job.onOpen?.(),
      }));
    }

    const fresh = el("div", { class: "import-item" }, [
      el("span", {
        class: "import-icon",
        style: `background:${tone === "ok" ? "var(--ok-soft)" : tone === "bad" ? "var(--bad-soft)" : "var(--accent-soft)"};border:1px solid ${tone === "ok" ? "var(--ok-border)" : tone === "bad" ? "var(--bad-border)" : "var(--accent-border)"}`,
      }, [icon(ICONS.file, { size: 16, stroke: color, width: 1.9 })]),
      el("div", { class: "import-main" }, [
        el("p", { class: "import-name", title: job.file.name, text: job.file.name }),
        el("p", { class: "import-meta", text: `${formatFileSize(job.file.size)} · ${job.message}` }),
        bar,
      ]),
      el("span", { class: `badge ${badgeCls}`, text: badgeText }),
      ...actions,
    ]);

    if (job.node?.isConnected) job.node.replaceWith(fresh);
    else listBody.appendChild(fresh);
    job.node = fresh;
    listPanel.hidden = jobs.size === 0;
    listHead.firstChild.textContent = `Lot en cours · ${jobs.size} fichier(s)`;
  }

  function update(job, patch) {
    Object.assign(job, patch);
    renderJob(job);
  }

  /* ------------------------------------------------------------ pipeline --- */

  function cancel(job) {
    job.follower?.cancel();
    update(job, { status: "error", message: "Annulé par l'utilisateur.", pct: 0, indeterminate: false });
  }

  async function enqueue(file) {
    const error = validateFile(file);
    if (error) {
      toast(error, { title: file.name, type: "bad" });
      return;
    }

    const key = `job-${seq++}`;
    const job = {
      key, file, status: "uploading", pct: 0,
      message: "Envoi du fichier…", indeterminate: false, node: null,
    };
    jobs.set(key, job);
    renderJob(job);

    let created;
    try {
      created = await api.jobs.create(file, {
        onUploadProgress: (pct) => update(job, { pct, message: `Envoi du fichier… ${pct} %` }),
      });
    } catch (err) {
      update(job, { status: "error", message: err.message, pct: 0, indeterminate: false });
      toast(err.message, { title: `Import de ${file.name}`, type: "bad" });
      return;
    }

    update(job, {
      status: "running", pct: 2, indeterminate: true,
      message: "Extraction en file d'attente…", jobId: created.job_id,
    });
    announce(`Extraction lancée pour ${file.name}.`);

    const follower = api.followJob(created.job_id, {
      onProgress: (progress) => {
        const pct = Math.max(2, Math.min(100, Number(progress.progress_pct || 0)));
        const step = STEP_LABELS[progress.current_step] || progress.current_step || "Traitement";
        const label = progress.message || step;
        // Le message du pipeline mentionne déjà la page en cours pour les
        // évènements de lecture : on n'ajoute le compteur que s'il manque.
        const suffix =
          /page\s*\d+\s*\/\s*\d+/i.test(label)
            ? ""
            : progress.current_page && progress.pages_total
              ? ` · page ${progress.current_page}/${progress.pages_total}`
              : progress.pages_total
                ? ` · ${progress.pages_total} page(s)`
                : "";
        update(job, { pct, indeterminate: pct <= 2, message: `${label}${suffix}` });
      },
    });
    job.follower = follower;

    try {
      const result = await follower.promise;
      update(job, {
        status: "done", pct: 100, indeterminate: false,
        message: `Extraction terminée · complétude ${formatAmount(result.completeness_pct, { decimals: true })} %`,
      });
      announce(`Extraction terminée pour ${file.name}.`);
      const outcome = await onCompleted?.(created.job_id, result);
      if (outcome?.dossierId) {
        update(job, {
          dossierId: outcome.dossierId,
          onOpen: outcome.onOpen,
          message: `Dossier ${outcome.dossierId} créé · complétude ${formatAmount(result.completeness_pct, { decimals: true })} %`,
        });
      }
    } catch (err) {
      if (err.isAuth) return;
      update(job, { status: "error", message: err.message, pct: 100, indeterminate: false });
      toast(err.message, { title: `Extraction de ${file.name}`, type: "bad" });
    }
  }

  return {
    node,
    /** Interrompt tous les suivis en cours (changement d'écran, déconnexion). */
    destroy() {
      for (const job of jobs.values()) job.follower?.cancel();
      jobs.clear();
    },
  };
}

/* ------------------------------------------------------ écran complet --- */

export function createImportView({ onOpenDossier, onDossierCreated }) {
  const pane = $("#importPane");
  let panel = null;

  function mount() {
    panel?.destroy();
    panel = createImportPanel({
      onCompleted: async (jobId) => {
        try {
          const created = await api.dossiers.create({ job_id: jobId });
          const dossierId = created.dossier.id;
          toast(`Dossier ${dossierId} créé et placé dans la file de validation.`, {
            title: "Extraction terminée", type: "ok",
          });
          onDossierCreated?.(created.dossier);
          return { dossierId, onOpen: () => onOpenDossier(dossierId) };
        } catch (error) {
          toast(error.message, { title: "Création du dossier impossible", type: "bad" });
          return null;
        }
      },
    });

    replaceChildren(pane, [
      panel.node,
      el("div", { class: "banner banner-neutral", style: "margin-top:18px" }, [
        icon(ICONS.info, { size: 17, stroke: "currentColor", width: 2 }),
        el("div", { class: "banner-body" }, [
          el("p", { class: "banner-title", text: "Comment se déroule l'extraction" }),
          el("p", { class: "banner-text",
            text: "Chaque page est classée puis lue par le modèle de vision, les postes RCC sont résolus, "
              + "puis les contrôles comptables sont exécutés. Un dossier est créé automatiquement en fin "
              + "de traitement et rejoint la file de validation." }),
        ]),
      ]),
    ]);
  }

  return {
    show() { mount(); },
    hide() { panel?.destroy(); panel = null; },
  };
}
