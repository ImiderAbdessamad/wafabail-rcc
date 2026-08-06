/* Écran « Historique & piste d'audit » — journal virtualisé. */

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import * as api from "../lib/api.js";
import { formatDateTime, pluralize } from "../lib/format.js";
import { TopBar } from "../components/AppShell.jsx";
import { ICONS } from "../components/Icon.jsx";
import { EmptyState, ErrorState, SkeletonRows } from "../components/States.jsx";
import { useVirtualRows } from "../hooks/index.js";
import { useToasts } from "../hooks/useToasts.jsx";

const ROW_HEIGHT = 45; // doit suivre la hauteur CSS de .audit-row

const ACTION_TONE = {
  Validation: { color: "var(--ok-text)", background: "var(--ok-soft)" },
  Rejet: { color: "var(--bad-text)", background: "var(--bad-soft)" },
  Ingestion: { color: "var(--ink-4)", background: "#F1F5F9" },
  Correction: { color: "var(--warn-text)", background: "var(--warn-soft-2)" },
  Vérification: { color: "var(--ok-text)", background: "var(--ok-soft)" },
  Arbitrage: { color: "#fff", background: "var(--dark)" },
};

export default function Audit() {
  const navigate = useNavigate();
  const { toast, announce } = useToasts();

  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const payload = await api.audit.all();
      setItems(payload.items);
      announce(`${pluralize(payload.items.length, "entrée")} dans le journal d'audit.`);
    } catch (err) {
      if (!err.isAuth) setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [announce]);

  useEffect(() => {
    load();
  }, [load]);

  function exportCsv() {
    if (!items.length) {
      toast("Le journal est vide — rien à exporter.", { title: "Export", type: "warn" });
      return;
    }
    const header = ["Horodatage", "Utilisateur", "Dossier", "Client", "Poste", "Avant", "Apres", "Action"];
    const escape = (value) => `"${String(value ?? "").replace(/"/g, '""')}"`;
    const lines = [header.map(escape).join(";")];
    for (const entry of items) {
      lines.push([
        formatDateTime(entry.timestamp), entry.actor, entry.dossier_id, entry.client_name,
        entry.field_label || entry.field_code || "", entry.before, entry.after, entry.action,
      ].map(escape).join(";"));
    }
    // BOM : Excel (fr) ouvre correctement l'UTF-8
    const blob = new Blob([`﻿${lines.join("\r\n")}`], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `piste-audit-rcc-${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast(`${pluralize(items.length, "entrée")} exportée(s).`, { title: "Export CSV", type: "ok" });
  }

  return (
    <section className="view is-entering">
      <TopBar
        title="Historique & piste d'audit"
        subtitle="Toute correction, validation ou rejet est horodatée et nominative."
      >
        <button type="button" className="btn btn-ghost" onClick={exportCsv} disabled={!items.length}>
          Exporter le journal (CSV)
        </button>
      </TopBar>

      <div className="scroll">
        <div className="wrap">
          {loading ? (
            <div className="panel">
              <SkeletonRows count={8} widths={[130, 110, 110, 200, 100, 110]} />
            </div>
          ) : error ? (
            <div className="panel">
              <ErrorState title="Journal indisponible" message={error} onRetry={load} />
            </div>
          ) : items.length === 0 ? (
            <div className="panel">
              <EmptyState
                icon={ICONS.clock}
                title="Journal vide"
                text="Aucune correction ni décision n'a encore été enregistrée. Le journal se remplit dès la première liasse importée."
              />
            </div>
          ) : (
            <AuditTable items={items} onOpenDossier={(id) => navigate(`/validation/${encodeURIComponent(id)}`)} />
          )}
        </div>
      </div>
    </section>
  );
}

function AuditTable({ items, onOpenDossier }) {
  const { ref, start, end, offsetY, totalHeight } = useVirtualRows(items.length, ROW_HEIGHT);
  const visible = items.slice(start, end);

  return (
    <div className="panel" style={{ overflowX: "auto" }}>
      <div className="panel-head">
        <div>
          <h2>Journal des actions</h2>
          <p>Corrections de postes, ingestions OCR et décisions, tous dossiers confondus.</p>
        </div>
        <span className="table-count">{pluralize(items.length, "entrée")}</span>
      </div>

      <div className="audit-table" role="table" aria-label="Journal d'audit">
        <div className="audit-head" role="row">
          <span className="a-time" role="columnheader">Horodatage</span>
          <span className="a-user" role="columnheader">Utilisateur</span>
          <span className="a-dossier" role="columnheader">Dossier</span>
          <span className="a-field" role="columnheader">Poste</span>
          <span className="a-before" role="columnheader">Avant</span>
          <span className="a-after" role="columnheader">Après</span>
          <span className="a-action" role="columnheader">Action</span>
        </div>

        <div className="audit-viewport scroll" ref={ref}>
          <div className="audit-spacer" style={{ height: totalHeight }}>
            <div className="audit-rows" role="rowgroup" style={{ transform: `translateY(${offsetY}px)` }}>
              {visible.map((entry, index) => {
                const tone = ACTION_TONE[entry.action] || ACTION_TONE.Ingestion;
                return (
                  <div
                    className="audit-row"
                    role="row"
                    style={{ height: ROW_HEIGHT }}
                    key={`${start + index}-${entry.timestamp}-${entry.field_code ?? entry.action}`}
                  >
                    <span className="a-time" role="cell">{formatDateTime(entry.timestamp)}</span>
                    <span className="a-user" role="cell" title={entry.actor}>{entry.actor}</span>
                    <span className="a-dossier" role="cell">
                      <button
                        type="button"
                        className="a-dossier"
                        style={{ background: "none", textDecoration: "underline", textUnderlineOffset: 2 }}
                        title={entry.client_name}
                        onClick={() => onOpenDossier(entry.dossier_id)}
                      >
                        {entry.dossier_id}
                      </button>
                    </span>
                    <span className="a-field" role="cell" title={entry.client_name}>
                      {entry.field_label || entry.field_code || "—"}
                    </span>
                    <span className="a-before" role="cell">{entry.before ?? "—"}</span>
                    <span className="a-after" role="cell" title={entry.after ?? ""}>
                      {entry.after ?? "—"}
                    </span>
                    <span className="a-action" role="cell">
                      <span className="badge badge-xs" style={tone}>{entry.action}</span>
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
