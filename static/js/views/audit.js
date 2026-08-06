/* Écran « Historique & piste d'audit » — journal virtualisé. */

import * as api from "../api.js";
import {
  $, ICONS, announce, el, formatDateTime, icon,
  pluralize, replaceChildren, schedule, skeletonRows, toast,
} from "../util.js";

const ROW_HEIGHT = 45;   // hauteur fixe d'une ligne (doit suivre le CSS)
const OVERSCAN = 8;      // lignes rendues hors écran de part et d'autre

const ACTION_TONE = {
  Validation: { color: "var(--ok-text)", bg: "var(--ok-soft)" },
  Rejet: { color: "var(--bad-text)", bg: "var(--bad-soft)" },
  Ingestion: { color: "var(--ink-4)", bg: "#F1F5F9" },
  Correction: { color: "var(--warn-text)", bg: "var(--warn-soft-2)" },
  Arbitrage: { color: "#fff", bg: "var(--dark)" },
};

export function createAuditView({ onOpenDossier }) {
  const pane = $("#auditPane");
  const exportBtn = $("#auditExport");

  let items = [];
  let viewport = null;
  let rowsHost = null;
  let spacer = null;
  let lastRange = { start: -1, end: -1 };

  /* --------------------------------------------------------- rendu ligne --- */

  function buildRow(entry) {
    const tone = ACTION_TONE[entry.action] || ACTION_TONE.Ingestion;
    return el("div", { class: "audit-row", role: "row", style: `height:${ROW_HEIGHT}px` }, [
      el("span", { class: "a-time", role: "cell", text: formatDateTime(entry.timestamp) }),
      el("span", { class: "a-user", role: "cell", title: entry.actor, text: entry.actor }),
      el("span", { class: "a-dossier", role: "cell" }, [
        el("button", {
          type: "button",
          class: "a-dossier",
          style: "background:none;text-decoration:underline;text-underline-offset:2px",
          text: entry.dossier_id,
          title: entry.client_name,
          onClick: () => onOpenDossier(entry.dossier_id),
        }),
      ]),
      el("span", {
        class: "a-field", role: "cell",
        title: entry.client_name,
        text: entry.field_label || entry.field_code || "—",
      }),
      el("span", { class: "a-before", role: "cell", text: entry.before ?? "—" }),
      el("span", { class: "a-after", role: "cell", title: entry.after ?? "", text: entry.after ?? "—" }),
      el("span", { class: "a-action", role: "cell" }, [
        el("span", {
          class: "badge badge-xs",
          style: `color:${tone.color};background:${tone.bg}`,
          text: entry.action,
        }),
      ]),
    ]);
  }

  /* ------------------------------------------------------ virtualisation --- */

  function renderWindow() {
    if (!viewport) return;
    const scrollTop = viewport.scrollTop;
    const height = viewport.clientHeight || 480;
    const start = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN);
    const end = Math.min(items.length, Math.ceil((scrollTop + height) / ROW_HEIGHT) + OVERSCAN);

    if (start === lastRange.start && end === lastRange.end) return;
    lastRange = { start, end };

    const fragment = document.createDocumentFragment();
    for (let i = start; i < end; i += 1) fragment.appendChild(buildRow(items[i]));
    rowsHost.replaceChildren(fragment);
    rowsHost.style.transform = `translateY(${start * ROW_HEIGHT}px)`;
  }

  function renderTable() {
    spacer = el("div", { class: "audit-spacer", style: `height:${items.length * ROW_HEIGHT}px` });
    rowsHost = el("div", { class: "audit-rows", role: "rowgroup" });
    spacer.appendChild(rowsHost);

    viewport = el("div", {
      class: "audit-viewport scroll",
      onScroll: () => schedule(renderWindow),
    }, [spacer]);

    const table = el("div", { class: "audit-table", role: "table", "aria-label": "Journal d'audit" }, [
      el("div", { class: "audit-head", role: "row" }, [
        el("span", { class: "a-time", role: "columnheader", text: "Horodatage" }),
        el("span", { class: "a-user", role: "columnheader", text: "Utilisateur" }),
        el("span", { class: "a-dossier", role: "columnheader", text: "Dossier" }),
        el("span", { class: "a-field", role: "columnheader", text: "Poste" }),
        el("span", { class: "a-before", role: "columnheader", text: "Avant" }),
        el("span", { class: "a-after", role: "columnheader", text: "Après" }),
        el("span", { class: "a-action", role: "columnheader", text: "Action" }),
      ]),
      viewport,
    ]);

    replaceChildren(pane, [
      el("div", { class: "panel", style: "overflow-x:auto" }, [
        el("div", { class: "panel-head" }, [
          el("div", {}, [
            el("h2", { text: "Journal des actions" }),
            el("p", { text: "Corrections de postes, ingestions OCR et décisions, tous dossiers confondus." }),
          ]),
          el("span", { class: "table-count", text: pluralize(items.length, "entrée") }),
        ]),
        table,
      ]),
    ]);

    lastRange = { start: -1, end: -1 };
    renderWindow();
  }

  function renderEmpty() {
    replaceChildren(pane, [
      el("div", { class: "panel" }, [
        el("div", { class: "empty" }, [
          el("div", { class: "empty-icon" }, [icon(ICONS.clock, { size: 20, stroke: "#94A3B8", width: 1.6 })]),
          el("h3", { text: "Journal vide" }),
          el("p", { text: "Aucune correction ni décision n'a encore été enregistrée. Le journal se remplit dès la première liasse importée." }),
        ]),
      ]),
    ]);
  }

  function renderError(message, retry) {
    replaceChildren(pane, [
      el("div", { class: "panel" }, [
        el("div", { class: "empty empty-bad" }, [
          el("div", { class: "empty-icon" }, [icon(ICONS.warning, { size: 20, stroke: "#DC2626", width: 2 })]),
          el("h3", { text: "Journal indisponible" }),
          el("p", { text: message }),
          el("button", { type: "button", class: "btn btn-primary", text: "Réessayer", onClick: retry }),
        ]),
      ]),
    ]);
  }

  /* -------------------------------------------------------------- export --- */

  function toCsv() {
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
    return `﻿${lines.join("\r\n")}`;
  }

  exportBtn.addEventListener("click", () => {
    if (!items.length) {
      toast("Le journal est vide — rien à exporter.", { title: "Export", type: "warn" });
      return;
    }
    const blob = new Blob([toCsv()], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = el("a", {
      href: url,
      download: `piste-audit-rcc-${new Date().toISOString().slice(0, 10)}.csv`,
    });
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast(`${pluralize(items.length, "entrée")} exportée(s).`, { title: "Export CSV", type: "ok" });
  });

  window.addEventListener("resize", () => schedule(renderWindow));

  /* ------------------------------------------------------------ chargement --- */

  async function load() {
    replaceChildren(pane, [el("div", { class: "panel" }, skeletonRows(8, [130, 110, 110, 200, 100, 110]))]);
    try {
      const data = await api.audit.all();
      items = data.items;
      exportBtn.disabled = !items.length;
      if (!items.length) renderEmpty();
      else {
        renderTable();
        announce(`${pluralize(items.length, "entrée")} dans le journal d'audit.`);
      }
    } catch (error) {
      if (error.isAuth) return;
      renderError(error.message, load);
    }
  }

  return { show: load };
}
