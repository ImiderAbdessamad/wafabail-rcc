/* Panneau gauche : document original, zones extraites, pièces & import. */

import { useMemo } from "react";
import * as api from "../../lib/api.js";
import { CODE_ORDER } from "../../lib/fields.js";
import { formatAmount } from "../../lib/format.js";
import Icon, { ICONS } from "../Icon.jsx";
import ImportPanel from "../ImportPanel.jsx";
import { Badge, EmptyState } from "../States.jsx";
import Banner from "./Banner.jsx";
import PdfEvidenceViewer from "./PdfEvidenceViewer.jsx";

const TABS = [
  { key: "doc", label: "Document original" },
  { key: "zones", label: "Zones extraites" },
  { key: "import", label: "Pièces & import" },
];

export default function ViewerPane({
  dossier,
  tab,
  onTabChange,
  activeCode,
  activeEvidencePage,
  onFocusField,
  onAttached,
}) {
  const hasDocument = dossier.has_document;
  const effectiveTab = hasDocument ? tab : "import";

  return (
    <div className="split-viewer">
      <div className="pane-tabs" role="tablist" aria-label="Pièces du dossier">
        {TABS.map((item) => {
          const disabled = item.key !== "import" && !hasDocument;
          return (
            <button
              key={item.key}
              type="button"
              role="tab"
              className={`pane-tab${disabled ? " hint" : ""}`}
              aria-selected={effectiveTab === item.key}
              disabled={disabled}
              data-hint={disabled ? "Aucune liasse rattachée à ce dossier" : undefined}
              onClick={() => onTabChange(item.key)}
            >
              {item.label}
              {item.key === "import" && !hasDocument ? (
                <span className="tab-badge" aria-label="action requise">!</span>
              ) : null}
            </button>
          );
        })}
      </div>

      <div className="pane-body is-entering" role="tabpanel" key={effectiveTab}>
        {effectiveTab === "doc" ? (
          <DocumentTab dossier={dossier} activeCode={activeCode} activeEvidencePage={activeEvidencePage} onFocusField={onFocusField} />
        ) : effectiveTab === "zones" ? (
          <ZonesTab dossier={dossier} activeCode={activeCode} onFocusField={onFocusField} />
        ) : (
          <ImportTab dossier={dossier} onAttached={onAttached} />
        )}
      </div>
    </div>
  );
}

/* --------------------------------------------------------------- document --- */

function DocumentTab({ dossier, activeCode, activeEvidencePage, onFocusField }) {
  const fileUrl = api.dossiers.fileUrl(dossier.id);

  return (
    <div className="pane-body">
      <PdfEvidenceViewer
        fileUrl={fileUrl}
        filename={dossier.filename || dossier.id}
        fields={dossier.result?.fields ?? []}
        activeCode={activeCode}
        activeEvidencePage={activeEvidencePage}
        onFocusField={onFocusField}
      />
    </div>
  );
}

/* ------------------------------------------------------------------ zones --- */

function ZonesTab({ dossier, activeCode, onFocusField }) {
  const rows = useMemo(() => {
    const out = [];
    for (const field of dossier.result?.fields ?? []) {
      for (const evidence of field.evidence ?? []) out.push({ field, evidence });
    }
    out.sort(
      (a, b) =>
        (a.evidence.page_number ?? 99) - (b.evidence.page_number ?? 99) ||
        (CODE_ORDER.get(a.field.code) ?? 999) - (CODE_ORDER.get(b.field.code) ?? 999)
    );
    return out;
  }, [dossier.result]);

  if (!rows.length) {
    return (
      <div className="pane-scroll">
        <EmptyState
          icon={ICONS.highlight}
          title="Aucune zone d'extraction"
          text="Le moteur n'a associé aucune ligne source aux postes de ce dossier. Consultez le document original pour un contrôle manuel."
        />
      </div>
    );
  }

  return (
    <div className="pane-scroll">
      <div className="panel">
        <div className="panel-head">
          <div>
            <h2>Zones d'extraction reconnues</h2>
            <p>Ligne du document identifiée par le moteur pour chaque poste. Cliquez pour cadrer la page.</p>
          </div>
        </div>

        {rows.map(({ field, evidence }, index) => {
          const confidence = evidence.confidence ?? field.confidence ?? 0;
          const dot = confidence >= 0.8 ? "var(--ok)" : confidence >= 0.6 ? "var(--warn)" : "var(--bad)";
          const toneClass = confidence >= 0.8 ? "conf-ok" : confidence >= 0.6 ? "conf-low" : "conf-bad";
          const meta = [
            evidence.page_number ? `page ${evidence.page_number}` : null,
            evidence.raw_label,
            evidence.column_name,
          ].filter(Boolean).join(" · ");

          return (
            <button
              key={`${field.code}-${index}`}
              type="button"
              className={`zone${activeCode === field.code ? " is-active" : ""}`}
              onClick={() => onFocusField(field.code, { openDoc: true, pageNumber: evidence.page_number })}
            >
              <span className="zone-dot" style={{ background: dot }} aria-hidden="true" />
              <span className="zone-main">
                <span className="zone-label">{field.label}</span>
                <span className="zone-meta" title={evidence.source_excerpt || ""}>{meta}</span>
              </span>
              <span className="zone-val">{evidence.raw_value || formatAmount(field.value)}</span>
              <span className={`zone-conf ${toneClass}`}>{`${Math.round(confidence * 100)} %`}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------- import --- */

function ImportTab({ dossier, onAttached }) {
  const result = dossier.result;
  const pieces = [
    {
      label: "Liasse fiscale — bilan actif / passif",
      meta: dossier.has_document
        ? `${dossier.filename} · ${result?.document.pages_total ?? "?"} pages`
        : "Aucun fichier rattaché",
      ok: dossier.has_document,
    },
    {
      label: "Compte de produits et charges (CPC)",
      meta: result
        ? `${result.document.pages_processed} page(s) exploitée(s) par le moteur`
        : "Extraction non exécutée",
      ok: Boolean(result?.document.pages_processed),
    },
    {
      label: "Postes RCC extraits",
      meta: result
        ? `complétude ${formatAmount(result.completeness_pct, { decimals: true })} %`
        : "aucun poste extrait",
      ok: (result?.completeness_pct ?? 0) >= 100,
    },
  ];

  return (
    <div className="pane-scroll">
      {dossier.has_document ? (
        <Banner
          tone="ok"
          title="Liasse déjà rattachée et extraite."
          text="L'import ci-dessous remplace la liasse par une version corrigée et relance l'extraction."
        />
      ) : (
        <Banner
          tone="bad"
          title="Aucune liasse rattachée à ce dossier."
          text="Importez le bilan scanné pour lancer l'extraction OCR et lever le blocage de conformité."
        />
      )}

      <ImportPanel
        compact
        title={`Déposer une liasse pour ${dossier.id}`}
        onCompleted={onAttached}
      />

      <p className="comp-section-title" style={{ margin: "18px 0 8px" }}>
        Pièces attendues au dossier
      </p>

      {pieces.map((piece) => (
        <div className="piece" key={piece.label}>
          <span
            className="piece-mark"
            style={
              piece.ok
                ? { background: "var(--ok-soft)", color: "var(--ok-text)" }
                : { background: "var(--bad-soft)", color: "var(--bad-text)" }
            }
          >
            {piece.ok ? "✓" : "!"}
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <p className="piece-label">{piece.label}</p>
            <p className="piece-meta">{piece.meta}</p>
          </div>
          <span className={`piece-state ${piece.ok ? "conf-ok" : "conf-bad"}`}>
            {piece.ok ? "Conforme" : "Manquante"}
          </span>
        </div>
      ))}
    </div>
  );
}
