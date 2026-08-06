/* Écran « Validation RCC · bilans OCR » — file de validation. */

import * as api from "../api.js";
import { STATUS_META } from "../fields.js";
import {
  $, ICONS, announce, debounce, el, formatAmountMad, formatDate,
  icon, pluralize, replaceChildren, schedule, skeletonRows, toast,
} from "../util.js";

const FILTERS = [
  { key: "pending", label: "À réviser" },
  { key: "validated", label: "Validés" },
  { key: "rejected", label: "Rejetés" },
  { key: "escalated", label: "Arbitrage" },
  { key: "all", label: "Tous" },
];

/** Au-delà de ce nombre de lignes, on rend par lots pour garder l'UI fluide. */
const CHUNK_SIZE = 60;

export function createListView({ onOpenDossier, onNavigate }) {
  const filtersNode = $("#listFilters");
  const rowsNode = $("#listRows");
  const statsNode = $("#listStats");
  const countNode = $("#listCount");
  const searchInput = $("#listSearch");
  const searchBusy = $("#listSearchBusy");

  let state = { status: "pending", search: "", items: [], counts: {}, loading: true, error: null };
  let inflight = null;
  let renderToken = 0;
  let activeId = null;

  /* ----------------------------------------------------------- filtres --- */

  function renderFilters() {
    replaceChildren(
      filtersNode,
      FILTERS.map((filter) => {
        const count = state.counts[filter.key];
        return el("button", {
          type: "button",
          role: "tab",
          "aria-selected": String(state.status === filter.key),
          onClick: () => setStatus(filter.key),
        }, [
          filter.label,
          count != null ? el("span", { class: "seg-count", text: String(count) }) : null,
        ]);
      })
    );
  }

  function renderStats() {
    const cards = [
      { label: "En attente de validation", value: state.counts.pending ?? 0, note: "dossiers", tone: "is-warn" },
      { label: "Validés", value: state.counts.validated ?? 0, note: "transmis EKIP", tone: "is-ok" },
      { label: "Rejetés", value: state.counts.rejected ?? 0, note: "retournés", tone: "is-bad" },
      { label: "En arbitrage", value: state.counts.escalated ?? 0, note: "superviseur", tone: "is-accent" },
    ];
    replaceChildren(
      statsNode,
      cards.map((card) =>
        el("div", { class: "stat" }, [
          el("p", { class: "stat-label", text: card.label }),
          el("div", { class: "stat-row" }, [
            el("span", { class: `stat-value ${card.tone}`, text: String(card.value) }),
            el("span", { class: "stat-note", text: card.note }),
          ]),
        ])
      )
    );
  }

  /* ------------------------------------------------------------ lignes --- */

  function buildRow(dossier) {
    const meta = STATUS_META[dossier.status] || STATUS_META.pending;

    const open = (event) => {
      event?.stopPropagation();
      onOpenDossier(dossier.id);
    };

    const row = el("div", {
      class: `row tr${dossier.id === activeId ? " is-active" : ""}`,
      role: "row",
      tabindex: "0",
      dataset: { id: dossier.id },
      "aria-label": `Dossier ${dossier.id}, ${dossier.client_name}, ${meta.label}`,
      onClick: open,
      onKeydown: (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          open(event);
        }
      },
    }, [
      el("span", { class: "c-id", role: "cell" }, [
        el("span", { class: "row-id", text: dossier.id }),
      ]),
      el("span", { class: "c-name", role: "cell" }, [
        el("span", { class: "row-name", title: dossier.client_name, text: dossier.client_name || "—" }),
        el("span", { class: "row-sub" }, [
          el("span", { class: "row-sector", text: dossier.sector || "Secteur non renseigné" }),
          el("span", {
            class: `badge badge-xs ${dossier.has_document ? "badge-ok" : "badge-bad"}`,
            text: dossier.has_document ? "Liasse OCR ✓" : "Liasse manquante",
          }),
        ]),
      ]),
      el("span", { class: "c-date row-date", role: "cell", text: formatDate(dossier.exercice_date) }),
      el("span", { class: "c-amount row-amount", role: "cell", text: formatAmountMad(dossier.credit_amount) }),
      el("span", { class: "c-status", role: "cell" }, [
        el("span", { class: `badge ${meta.cls}` }, [
          el("span", { class: "badge-dot", "aria-hidden": "true" }),
          meta.label,
        ]),
      ]),
      el("span", { class: "c-actions", role: "cell" }, [
        el("button", {
          type: "button", class: "btn-icon hint",
          "data-hint": "Ouvrir le dossier", "aria-label": `Ouvrir ${dossier.id}`,
          onClick: open,
        }, [icon(ICONS.eye, { size: 14, stroke: "#64748B", width: 1.8 })]),
        el("button", {
          type: "button", class: "btn-icon hint",
          "data-hint": dossier.override_count
            ? `${pluralize(dossier.override_count, "correction")} enregistrée(s)`
            : "Corriger les postes extraits",
          "aria-label": `Corriger ${dossier.id}`,
          onClick: open,
        }, [icon(ICONS.pencil, { size: 13, stroke: "#B45309", width: 1.8 })]),
      ]),
    ]);
    return row;
  }

  /** Rend par lots : la première frame reste sous les 16 ms même à 1000 lignes. */
  function renderRowsChunked(items) {
    const token = ++renderToken;
    rowsNode.replaceChildren();

    const step = (start) => {
      if (token !== renderToken) return;
      const fragment = document.createDocumentFragment();
      const end = Math.min(start + CHUNK_SIZE, items.length);
      for (let i = start; i < end; i += 1) fragment.appendChild(buildRow(items[i]));
      rowsNode.appendChild(fragment);
      if (end < items.length) schedule(() => step(end));
    };
    step(0);
  }

  function renderEmpty() {
    const searching = Boolean(state.search);
    const filterLabel = FILTERS.find((f) => f.key === state.status)?.label ?? "";
    // Le filtre de statut peut masquer un dossier qui existe bel et bien :
    // on propose d'élargir la recherche plutôt que d'annoncer « aucun résultat ».
    const hiddenByFilter = searching && state.status !== "all";

    replaceChildren(rowsNode, [
      el("div", { class: "empty" }, [
        el("div", { class: "empty-icon" }, [icon(ICONS.inbox, { size: 20, stroke: "#94A3B8", width: 1.6 })]),
        el("h3", {
          text: searching ? "Aucun résultat" : `Aucun dossier « ${filterLabel.toLowerCase()} »`,
        }),
        el("p", {
          text: searching
            ? hiddenByFilter
              ? `Aucun dossier « ${filterLabel.toLowerCase()} » ne correspond à « ${state.search} ». Il existe peut-être sous un autre statut.`
              : `Aucun dossier ne correspond à « ${state.search} ». Essayez un autre numéro, une raison sociale ou un ICE.`
            : "Les dossiers apparaissent ici dès qu'une liasse fiscale a été importée et extraite par le moteur OCR.",
        }),
        el("div", { style: "display:flex;gap:9px;flex-wrap:wrap;justify-content:center" }, [
          hiddenByFilter
            ? el("button", {
                type: "button", class: "btn btn-primary", text: "Chercher dans tous les statuts",
                onClick: () => setStatus("all"),
              })
            : null,
          searching
            ? el("button", {
                type: "button", class: "btn btn-ghost", text: "Effacer la recherche",
                onClick: () => { searchInput.value = ""; setSearch(""); },
              })
            : el("button", {
                type: "button", class: "btn btn-primary", text: "Importer une liasse",
                onClick: () => onNavigate("import"),
              }),
        ]),
      ]),
    ]);
  }

  function renderError() {
    replaceChildren(rowsNode, [
      el("div", { class: "empty empty-bad" }, [
        el("div", { class: "empty-icon" }, [icon(ICONS.warning, { size: 20, stroke: "#DC2626", width: 2 })]),
        el("h3", { text: "Chargement impossible" }),
        el("p", { text: state.error }),
        el("button", {
          type: "button", class: "btn btn-primary", text: "Réessayer",
          onClick: () => load(),
        }),
      ]),
    ]);
  }

  function render() {
    renderFilters();
    renderStats();

    if (state.loading) {
      countNode.textContent = "";
      replaceChildren(rowsNode, skeletonRows(6, [96, 210, 110, 130, 120]));
      return;
    }
    if (state.error) {
      countNode.textContent = "";
      renderError();
      return;
    }

    countNode.textContent = pluralize(state.items.length, "dossier");
    if (!state.items.length) renderEmpty();
    else renderRowsChunked(state.items);
  }

  /* ---------------------------------------------------------- chargement --- */

  async function load() {
    inflight?.abort();
    const controller = new AbortController();
    inflight = controller;

    state.loading = state.items.length === 0;
    state.error = null;
    if (state.loading) render();
    searchBusy.hidden = !state.search;

    try {
      const data = await api.dossiers.list({
        status: state.status,
        search: state.search,
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      state = { ...state, items: data.items, counts: data.counts, loading: false, error: null };
      render();
      announce(`${pluralize(data.items.length, "dossier")} affiché(s).`);
    } catch (error) {
      if (error.name === "AbortError" || controller.signal.aborted) return;
      if (error.isAuth) return;
      state = { ...state, loading: false, error: error.message };
      render();
    } finally {
      if (inflight === controller) {
        inflight = null;
        searchBusy.hidden = true;
      }
    }
  }

  function setStatus(status) {
    if (state.status === status) return;
    state.status = status;
    state.items = [];
    render();
    load();
  }

  function setSearch(value) {
    state.search = value.trim();
    load();
  }

  const debouncedSearch = debounce((value) => setSearch(value), 280);
  searchInput.addEventListener("input", (event) => {
    searchBusy.hidden = !event.target.value;
    debouncedSearch(event.target.value);
  });
  searchInput.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.target.value = "";
      debouncedSearch.flush("");
    } else if (event.key === "Enter") {
      event.preventDefault();
      debouncedSearch.flush(event.target.value);
    }
  });

  /* ------------------------------------------------------------ interface --- */

  return {
    /** Appelé à chaque entrée dans l'écran. */
    async show() {
      await load();
    },
    /** Met en évidence le dossier ouvert et rafraîchit ses données. */
    setActive(dossierId) {
      activeId = dossierId;
      for (const row of rowsNode.querySelectorAll(".row")) {
        row.classList.toggle("is-active", row.dataset.id === dossierId);
      }
    },
    /** Applique localement un changement de statut sans recharger (optimiste). */
    patchLocal(dossier) {
      const index = state.items.findIndex((item) => item.id === dossier.id);
      if (index === -1) return;
      state.items[index] = { ...state.items[index], ...dossier };
      // Le dossier peut sortir du filtre courant : rechargement silencieux.
      load();
    },
    refresh: load,
  };
}
