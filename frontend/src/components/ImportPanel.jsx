/* Dépôt de liasses : upload → job → suivi SSE → callback de fin. */

import { useCallback, useRef, useState } from "react";
import * as api from "../lib/api.js";
import { formatAmount, formatFileSize } from "../lib/format.js";
import Icon, { ICONS } from "./Icon.jsx";
import { Badge } from "./States.jsx";
import { useJobFollower } from "../hooks/index.js";
import { useToasts } from "../hooks/useToasts.jsx";

const MAX_BYTES = 50 * 1024 * 1024;

/** Étapes du pipeline, pour les libellés de repli. */
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
  if (file.size > MAX_BYTES) return `Fichier trop volumineux (max ${formatFileSize(MAX_BYTES)}).`;
  if (file.size === 0) return "Le fichier est vide.";
  return null;
}

let nextKey = 0;

/**
 * @param {object}   props
 * @param {string}   props.title        titre de la zone de dépôt
 * @param {boolean}  props.compact      masque le bouton « Contrôler »
 * @param {Function} props.onCompleted  (jobId, result) → { dossierId, onOpen } | void
 */
export default function ImportPanel({ title, compact = false, onCompleted }) {
  const { toast, announce } = useToasts();
  const follow = useJobFollower();
  const [jobs, setJobs] = useState([]);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef(null);

  const patch = useCallback((key, changes) => {
    setJobs((current) =>
      current.map((job) => (job.key === key ? { ...job, ...changes } : job))
    );
  }, []);

  const enqueue = useCallback(
    async (file) => {
      const invalid = validateFile(file);
      if (invalid) {
        toast(invalid, { title: file.name, type: "bad" });
        return;
      }

      const key = `job-${nextKey++}`;
      setJobs((current) => [
        ...current,
        { key, file, status: "uploading", pct: 0, message: "Envoi du fichier…", indeterminate: false },
      ]);

      let created;
      try {
        created = await api.jobs.create(file, {
          onUploadProgress: (pct) =>
            patch(key, { pct, message: `Envoi du fichier… ${pct} %` }),
        });
      } catch (err) {
        patch(key, { status: "error", message: err.message, pct: 0, indeterminate: false });
        if (!err.isAuth) toast(err.message, { title: `Import de ${file.name}`, type: "bad" });
        return;
      }

      patch(key, {
        status: "running", pct: 2, indeterminate: true,
        message: "Extraction en file d'attente…", jobId: created.job_id,
      });
      announce(`Extraction lancée pour ${file.name}.`);

      try {
        const result = await follow(created.job_id, {
          onProgress: (progress) => {
            const pct = Math.max(2, Math.min(100, Number(progress.progress_pct || 0)));
            const label =
              progress.message || STEP_LABELS[progress.current_step] || progress.current_step || "Traitement";
            // Le message du pipeline mentionne déjà la page pour les évènements
            // de lecture : on n'ajoute le compteur que s'il manque.
            const suffix = /page\s*\d+\s*\/\s*\d+/i.test(label)
              ? ""
              : progress.current_page && progress.pages_total
                ? ` · page ${progress.current_page}/${progress.pages_total}`
                : progress.pages_total
                  ? ` · ${progress.pages_total} page(s)`
                  : "";
            patch(key, { pct, indeterminate: pct <= 2, message: `${label}${suffix}` });
          },
        });

        const completeness = formatAmount(result.completeness_pct, { decimals: true });
        patch(key, {
          status: "done", pct: 100, indeterminate: false,
          message: `Extraction terminée · complétude ${completeness} %`,
        });
        announce(`Extraction terminée pour ${file.name}.`);

        const outcome = await onCompleted?.(created.job_id, result);
        if (outcome?.dossierId) {
          patch(key, {
            dossierId: outcome.dossierId,
            onOpen: outcome.onOpen,
            message: `Dossier ${outcome.dossierId} créé · complétude ${completeness} %`,
          });
        }
      } catch (err) {
        if (err.isAuth) return;
        patch(key, { status: "error", message: err.message, pct: 100, indeterminate: false });
        toast(err.message, { title: `Extraction de ${file.name}`, type: "bad" });
      }
    },
    [announce, follow, onCompleted, patch, toast]
  );

  function onFiles(fileList) {
    for (const file of Array.from(fileList || [])) enqueue(file);
  }

  return (
    <div className="import-panel">
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf,.pdf"
        multiple
        hidden
        onChange={(event) => {
          onFiles(event.target.files);
          event.target.value = "";
        }}
      />

      <button
        type="button"
        className={`dropzone${dragging ? " is-drag" : ""}`}
        aria-label="Choisir une liasse fiscale au format PDF"
        onClick={() => inputRef.current?.click()}
        onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
        onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          onFiles(event.dataTransfer?.files);
        }}
      >
        <span className="dropzone-icon">
          <Icon paths={ICONS.upload} size={20} width={1.9} style={{ stroke: "var(--accent)" }} />
        </span>
        <span className="dropzone-title">{title || "Déposez les liasses fiscales ici"}</span>
        <span className="dropzone-hint">
          {`PDF uniquement · ${formatFileSize(MAX_BYTES)} maximum · l'extraction des postes RCC démarre automatiquement`}
        </span>
      </button>

      {jobs.length ? (
        <div className="panel" style={{ marginTop: 14 }}>
          <div className="panel-head">
            <h2>{`Lot en cours · ${jobs.length} fichier(s)`}</h2>
          </div>
          {jobs.map((job) => (
            <ImportRow
              key={job.key}
              job={job}
              compact={compact}
              onRetry={() => {
                setJobs((current) => current.filter((item) => item.key !== job.key));
                enqueue(job.file);
              }}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function ImportRow({ job, compact, onRetry }) {
  const tone =
    job.status === "done" ? "ok" : job.status === "error" ? "bad" : "warn";
  const color =
    tone === "ok" ? "var(--ok)" : tone === "bad" ? "var(--bad)" : "var(--accent)";
  const badgeText =
    job.status === "done" ? "Prêt"
      : job.status === "error" ? "Échec"
      : job.status === "uploading" ? `Envoi ${job.pct} %`
      : `${job.pct} %`;

  return (
    <div className="import-item">
      <span
        className="import-icon"
        style={{
          background: tone === "ok" ? "var(--ok-soft)" : tone === "bad" ? "var(--bad-soft)" : "var(--accent-soft)",
          border: `1px solid ${tone === "ok" ? "var(--ok-border)" : tone === "bad" ? "var(--bad-border)" : "var(--accent-border)"}`,
        }}
      >
        <Icon paths={ICONS.file} size={16} width={1.9} style={{ stroke: color }} />
      </span>

      <div className="import-main">
        <p className="import-name" title={job.file.name}>{job.file.name}</p>
        <p className="import-meta">{`${formatFileSize(job.file.size)} · ${job.message}`}</p>
        <div className="progress">
          <div
            className={`progress-fill${job.status === "done" ? " is-ok" : ""}${job.status === "error" ? " is-bad" : ""}${job.indeterminate ? " is-indeterminate" : ""}`}
            style={{ width: `${job.status === "error" ? 100 : job.pct}%` }}
          />
        </div>
      </div>

      <Badge tone={tone === "warn" ? "warn" : tone}>{badgeText}</Badge>

      {job.status === "error" ? (
        <button type="button" className="btn btn-ghost btn-sm" onClick={onRetry}>
          Réessayer
        </button>
      ) : null}

      {job.status === "done" && job.dossierId && !compact ? (
        <button type="button" className="btn btn-soft btn-sm" onClick={job.onOpen}>
          Contrôler →
        </button>
      ) : null}
    </div>
  );
}
