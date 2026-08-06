/* Écran de validation — visionneuse PDF à gauche, formulaire RCC à droite. */

import * as api from "../api.js";
import {
  CODE_ORDER, GROUPS, FORMULAS, LOCKED_CODES, REJECT_MOTIFS,
  STATUS_META, TAG_CODES, fieldState,
} from "../fields.js";
import { openModal } from "../modal.js";
import {
  $, ICONS, announce, debounce, el, formatAmount, formatAmountMad,
  formatDate, formatDateTime, icon, parseAmount, pluralize,
  replaceChildren, skeletonRows, toast,
} from "../util.js";
import { createImportPanel } from "./import.js";

const SAVE_DEBOUNCE = 650;

export function createDetailView({ onBack, onDossierChanged }) {
  const barNode = $("#detailBar");
  const viewerNode = $("#viewerPane");
  const formNode = $("#formPane");
  const splitNode = $("#detailSplit");
  const splitToggle = $("#splitToggle");

  /** @type {{id, data, loading, error, paneTab, activeCode, pendingSaves, savers}} */
  let state = {
    id: null,
    data: null,
    loading: false,
    error: null,
    paneTab: "doc",
    activeCode: null,
    dirty: new Map(),   // code -> valeur en cours d'enregistrement
    savers: new Map(),  // code -> fonction debounce
  };

  let importPanel = null;

  splitToggle.addEventListener("click", () => {
    const formOnly = splitNode.classList.toggle("is-form-only");
    splitToggle.querySelector(".split-toggle-label").textContent =
      formOnly ? "Voir le document" : "Voir le formulaire";
    splitToggle.setAttribute("aria-expanded", String(!formOnly));
  });

  /* =========================================================== chargement === */

  async function load(dossierId, { silent = false } = {}) {
    state.id = dossierId;
    if (!silent) {
      state.loading = true;
      state.error = null;
      renderAll();
    }
    try {
      const data = await api.dossiers.get(dossierId);
      state.data = data;
      state.loading = false;
      state.error = null;
      // Un dossier sans liasse ouvre directement l'onglet d'import.
      if (!data.dossier.has_document) state.paneTab = "import";
      renderAll();
    } catch (error) {
      if (error.isAuth) return;
      state.loading = false;
      state.error = error.message;
      renderAll();
    }
  }

  /* ============================================================== en-tête === */

  function renderBar() {
    if (state.loading || !state.data) {
      replaceChildren(barNode, [
        el("div", { class: "skeleton sk-title", style: "width:220px" }),
      ]);
      return;
    }
    if (state.error) {
      replaceChildren(barNode, [
        el("button", {
          type: "button", class: "btn-icon detail-back", "aria-label": "Retour à la liste",
          onClick: onBack,
        }, [icon(ICONS.chevronLeft, { size: 15, stroke: "#475569" })]),
        el("div", { class: "detail-name", text: "Dossier indisponible" }),
      ]);
      return;
    }

    const { dossier, compliance } = state.data;
    const meta = STATUS_META[dossier.status] || STATUS_META.pending;
    const editCount = dossier.overrides.length;

    const validateBtn = el("button", {
      type: "button",
      class: `btn ${compliance.can_validate ? "btn-ok" : "btn-ghost"} hint`,
      disabled: !compliance.can_validate || dossier.status === "validated",
      "data-hint": dossier.status === "validated"
        ? "Ce dossier est déjà validé"
        : compliance.can_validate
          ? "Transmettre les postes RCC au modèle EKIP"
          : `${pluralize(compliance.blockers, "règle")} de conformité bloquante(s) à lever avant validation`,
      onClick: onValidate,
    }, [
      icon(ICONS.check, { size: 14, stroke: "currentColor", width: 2.4 }),
      el("span", { class: "btn-label", text: "Valider le dossier" }),
      el("span", { class: "btn-spinner", "aria-hidden": "true" }),
    ]);

    replaceChildren(barNode, [
      el("button", {
        type: "button", class: "btn-icon detail-back", "aria-label": "Retour à la liste des dossiers",
        onClick: onBack,
      }, [icon(ICONS.chevronLeft, { size: 15, stroke: "#475569" })]),

      el("div", {}, [
        el("div", { class: "detail-head-top" }, [
          el("span", { class: "detail-id", text: dossier.id }),
          el("h1", { class: "detail-name", text: dossier.client_name || "Client à identifier" }),
          el("span", { class: `badge ${meta.cls}` }, [
            el("span", { class: "badge-dot", "aria-hidden": "true" }), meta.label,
          ]),
          editCount
            ? el("span", { class: "badge badge-neutral", text: `${pluralize(editCount, "correction")}` })
            : null,
        ]),
        el("div", { class: "detail-head-meta" }, [
          el("span", {}, ["Exercice clos le ", el("b", { text: formatDate(dossier.exercice_date) })]),
          el("span", {}, ["Crédit demandé ", el("b", { text: formatAmountMad(dossier.credit_amount) })]),
          el("span", {}, ["ICE ", el("b", { text: dossier.ice || "non détecté" })]),
          el("span", {
            class: dossier.has_document ? "conf-ok" : "conf-bad",
            style: "font-weight:600",
            text: dossier.has_document
              ? `Extraction OCR · complétude ${formatAmount(dossier.completeness_pct, { decimals: true })} %`
              : "Liasse non rattachée",
          }),
        ]),
      ]),

      el("div", { class: "detail-actions" }, [
        el("button", {
          type: "button", class: "btn btn-ghost", text: "Demander un arbitrage",
          disabled: dossier.status === "escalated",
          onClick: onEscalate,
        }),
        el("button", {
          type: "button", class: "btn btn-danger", text: "Rejeter",
          disabled: dossier.status === "rejected",
          onClick: onReject,
        }),
        validateBtn,
      ]),
    ]);
  }

  /* ============================================================ panneau G === */

  const PANE_TABS = [
    { key: "doc", label: "Document original" },
    { key: "zones", label: "Zones extraites" },
    { key: "import", label: "Pièces & import" },
  ];

  function setPaneTab(key) {
    state.paneTab = key;
    renderViewer();
  }

  function renderViewer() {
    if (state.loading || !state.data) {
      replaceChildren(viewerNode, [el("div", { class: "pane-scroll" }, skeletonRows(4, [140, 200, 90]))]);
      return;
    }
    if (state.error) {
      replaceChildren(viewerNode, []);
      return;
    }

    const { dossier } = state.data;
    const tabs = el("div", { class: "pane-tabs", role: "tablist", "aria-label": "Pièces du dossier" },
      PANE_TABS.map((tab) => {
        const disabled = tab.key !== "import" && !dossier.has_document;
        return el("button", {
          type: "button",
          role: "tab",
          class: `pane-tab${disabled ? " hint" : ""}`,
          "aria-selected": String(state.paneTab === tab.key),
          disabled,
          "data-hint": disabled ? "Aucune liasse rattachée à ce dossier" : null,
          onClick: () => setPaneTab(tab.key),
        }, [
          tab.label,
          tab.key === "import" && !dossier.has_document
            ? el("span", { class: "tab-badge", text: "!", "aria-label": "action requise" })
            : null,
        ]);
      })
    );

    const active = !dossier.has_document ? "import" : state.paneTab;
    const body = el("div", { class: "pane-body is-entering", role: "tabpanel" }, [
      active === "doc" ? renderDocument() : active === "zones" ? renderZones() : renderImport(),
    ]);

    replaceChildren(viewerNode, [tabs, body]);
  }

  /* -- onglet Document : visionneuse PDF native du navigateur -------------- */

  function renderDocument() {
    const { dossier } = state.data;
    const page = pageForCode(state.activeCode);
    const src = `${api.dossiers.fileUrl(dossier.id)}#page=${page}&view=FitH`;

    const frame = el("iframe", {
      class: "viewer-frame",
      title: `Liasse fiscale ${dossier.filename || dossier.id}`,
      src,
    });

    return el("div", { class: "pane-body" }, [
      el("div", { class: "viewer-toolbar" }, [
        el("span", { class: "viewer-file", title: dossier.filename, text: dossier.filename || "Liasse" }),
        state.activeCode
          ? el("span", { class: "badge badge-warn badge-xs", text: `page ${page}` })
          : el("span", { class: "muted", text: "Sélectionnez un poste pour cadrer sa page" }),
        el("span", { style: "flex:1" }),
        el("a", {
          class: "btn btn-ghost btn-sm",
          href: api.dossiers.fileUrl(dossier.id),
          target: "_blank",
          rel: "noopener",
          text: "Ouvrir dans un onglet",
        }),
      ]),
      frame,
    ]);
  }

  /** Page du PDF portant la meilleure preuve pour ce poste. */
  function pageForCode(code) {
    if (!code || !state.data?.dossier.result) return 1;
    const field = state.data.dossier.result.fields.find((f) => f.code === code);
    return field?.evidence?.[0]?.page_number || 1;
  }

  /* -- onglet Zones extraites : provenance GLM réelle ---------------------- */

  function renderZones() {
    const result = state.data.dossier.result;
    const rows = [];

    for (const field of result?.fields ?? []) {
      for (const ev of field.evidence ?? []) {
        rows.push({ field, ev });
      }
    }
    rows.sort(
      (a, b) =>
        (a.ev.page_number ?? 99) - (b.ev.page_number ?? 99) ||
        (CODE_ORDER.get(a.field.code) ?? 999) - (CODE_ORDER.get(b.field.code) ?? 999)
    );

    if (!rows.length) {
      return el("div", { class: "pane-scroll" }, [
        el("div", { class: "empty" }, [
          el("div", { class: "empty-icon" }, [icon(ICONS.highlight, { size: 20, stroke: "#94A3B8", width: 1.6 })]),
          el("h3", { text: "Aucune zone d'extraction" }),
          el("p", {
            text: "Le moteur n'a associé aucune ligne source aux postes de ce dossier. Consultez le document original pour un contrôle manuel.",
          }),
        ]),
      ]);
    }

    return el("div", { class: "pane-scroll" }, [
      el("div", { class: "panel" }, [
        el("div", { class: "panel-head" }, [
          el("div", {}, [
            el("h2", { text: "Zones d'extraction reconnues" }),
            el("p", { text: "Ligne du document identifiée par le moteur pour chaque poste. Cliquez pour cadrer la page." }),
          ]),
        ]),
        ...rows.map(({ field, ev }) => {
          const conf = ev.confidence ?? field.confidence ?? 0;
          const tone = conf >= 0.8 ? "var(--ok)" : conf >= 0.6 ? "var(--warn)" : "var(--bad)";
          const toneClass = conf >= 0.8 ? "conf-ok" : conf >= 0.6 ? "conf-low" : "conf-bad";
          return el("button", {
            type: "button",
            class: `zone${state.activeCode === field.code ? " is-active" : ""}`,
            onClick: () => focusField(field.code, { openDoc: true }),
          }, [
            el("span", { class: "zone-dot", style: `background:${tone}`, "aria-hidden": "true" }),
            el("span", { class: "zone-main" }, [
              el("span", { class: "zone-label", text: field.label }),
              el("span", {
                class: "zone-meta",
                title: ev.source_excerpt || "",
                text: [
                  ev.page_number ? `page ${ev.page_number}` : null,
                  ev.raw_label,
                  ev.column_name,
                ].filter(Boolean).join(" · "),
              }),
            ]),
            el("span", { class: "zone-val", text: ev.raw_value || formatAmount(field.value) }),
            el("span", { class: `zone-conf ${toneClass}`, text: `${Math.round(conf * 100)} %` }),
          ]);
        }),
      ]),
    ]);
  }

  /* -- onglet Pièces & import --------------------------------------------- */

  function renderImport() {
    const { dossier } = state.data;
    const container = el("div", { class: "pane-scroll" });

    container.appendChild(
      dossier.has_document
        ? banner("ok", "Liasse déjà rattachée et extraite.",
            "L'import ci-dessous remplace la liasse par une version corrigée et relance l'extraction.")
        : banner("bad", "Aucune liasse rattachée à ce dossier.",
            "Importez le bilan scanné pour lancer l'extraction OCR et lever le blocage de conformité.")
    );

    // Le panneau est reconstruit à chaque rendu : on coupe les suivis SSE du
    // précédent pour ne pas laisser d'EventSource orphelines.
    importPanel?.destroy();
    importPanel = createImportPanel({
      compact: true,
      title: `Déposer une liasse pour ${dossier.id}`,
      onCompleted: async (jobId) => {
        try {
          await api.dossiers.attach(dossier.id, { job_id: jobId });
          toast("Extraction rattachée au dossier.", { title: "Liasse importée", type: "ok" });
          state.paneTab = "doc";
          await load(dossier.id, { silent: true });
          onDossierChanged?.(state.data.dossier);
        } catch (error) {
          toast(error.message, { title: "Rattachement impossible", type: "bad" });
        }
      },
    });
    container.appendChild(importPanel.node);

    container.appendChild(
      el("p", { class: "comp-section-title", style: "margin:18px 0 8px", text: "Pièces attendues au dossier" })
    );
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
    for (const piece of pieces) {
      container.appendChild(
        el("div", { class: "piece" }, [
          el("span", {
            class: "piece-mark",
            style: piece.ok
              ? "background:var(--ok-soft);color:var(--ok-text)"
              : "background:var(--bad-soft);color:var(--bad-text)",
            text: piece.ok ? "✓" : "!",
          }),
          el("div", { style: "flex:1;min-width:0" }, [
            el("p", { class: "piece-label", text: piece.label }),
            el("p", { class: "piece-meta", text: piece.meta }),
          ]),
          el("span", {
            class: `piece-state ${piece.ok ? "conf-ok" : "conf-bad"}`,
            text: piece.ok ? "Conforme" : "Manquante",
          }),
        ])
      );
    }
    return container;
  }

  /* ============================================================ formulaire === */

  function banner(tone, title, text, action) {
    const toneIcon = tone === "ok" ? ICONS.check : tone === "bad" ? ICONS.warning : ICONS.info;
    return el("div", { class: `banner banner-${tone}` }, [
      icon(toneIcon, { size: 17, stroke: "currentColor", width: 2 }),
      el("div", { class: "banner-body" }, [
        el("p", { class: "banner-title", text: title }),
        text ? el("p", { class: "banner-text", text }) : null,
      ]),
      action || null,
    ]);
  }

  function renderForm() {
    if (state.loading) {
      replaceChildren(formNode, [
        el("div", { class: "panel" }, skeletonRows(7, [180, 260, 120, 140])),
      ]);
      return;
    }
    if (state.error) {
      replaceChildren(formNode, [
        el("div", { class: "empty empty-bad" }, [
          el("div", { class: "empty-icon" }, [icon(ICONS.warning, { size: 20, stroke: "#DC2626", width: 2 })]),
          el("h3", { text: "Dossier introuvable" }),
          el("p", { text: state.error }),
          el("div", { style: "display:flex;gap:9px" }, [
            el("button", { type: "button", class: "btn btn-ghost", text: "Retour à la liste", onClick: onBack }),
            el("button", { type: "button", class: "btn btn-primary", text: "Réessayer", onClick: () => load(state.id) }),
          ]),
        ]),
      ]);
      return;
    }

    const { dossier, compliance } = state.data;
    const nodes = [];

    // Bannière d'équilibre : issue du contrôle comptable réel, pas d'un recalcul.
    const balance = dossier.result?.controls.find((c) => c.code === "bilan_equilibre");
    if (balance?.status === "failed") {
      nodes.push(
        banner(
          "bad",
          "Incohérence comptable détectée — Total Actif ≠ Total Passif",
          `Actif ${formatAmount(balance.expected)} · Passif ${formatAmount(balance.observed)} · écart de ${formatAmount(Math.abs(balance.difference ?? 0))} MAD. La validation est bloquée tant que l'équilibre n'est pas rétabli.`,
          el("button", {
            type: "button", class: "btn btn-danger-solid btn-sm", text: "Voir le poste concerné",
            onClick: () => focusField(balance.affected_fields?.[0] || "TOTAL_BILAN", { openDoc: true }),
          })
        )
      );
    } else if (balance?.status === "passed") {
      nodes.push(banner("ok", "Équilibre comptable vérifié.",
        `Total Actif = Total Passif = ${formatAmount(balance.observed)} MAD.`));
    } else if (!dossier.has_document) {
      nodes.push(banner("bad", "Liasse manquante.",
        "Aucune extraction n'est rattachée à ce dossier : la validation reste bloquée jusqu'à l'import du bilan scanné.",
        el("button", {
          type: "button", class: "btn btn-danger-solid btn-sm", text: "Importer la liasse",
          onClick: () => setPaneTab("import"),
        })));
    } else {
      nodes.push(banner("neutral", "Équilibre actif / passif non testable.",
        balance?.message || "Les totaux actif et passif n'ont pas pu être isolés dans la liasse."));
    }

    nodes.push(renderCompliance(compliance));
    nodes.push(renderLegend(dossier));
    for (const group of GROUPS) nodes.push(renderGroup(group));
    nodes.push(renderControls(dossier.result?.controls ?? []));

    if (dossier.result?.warnings?.length) {
      nodes.push(
        el("div", { class: "panel panel-pad", style: "margin-top:14px" }, [
          el("h2", { style: "font-size:12.5px;font-weight:700;margin-bottom:9px", text: "Avertissements d'extraction" }),
          el("ul", { style: "display:flex;flex-direction:column;gap:6px;list-style:none" },
            dossier.result.warnings.map((warning) =>
              el("li", { style: "font-size:11.5px;color:var(--muted);line-height:1.5", text: `· ${warning}` })
            )
          ),
        ])
      );
    }

    replaceChildren(formNode, nodes);
  }

  function renderCompliance(compliance) {
    const tone = compliance.blockers ? "bad" : compliance.warnings ? "low" : "ok";
    const colorVar = tone === "bad" ? "var(--bad-text)" : tone === "low" ? "var(--warn-text)" : "var(--ok-text)";

    return el("div", { class: "panel panel-pad", style: "margin-bottom:14px" }, [
      el("div", { class: "comp-head" }, [
        el("div", {}, [
          el("h2", { class: "comp-title", text: "Conformité RCC du dossier" }),
          el("p", { class: "comp-sub", text: compliance.summary }),
        ]),
        el("div", { class: "comp-score" }, [
          el("div", { class: "comp-pct", style: `color:${colorVar}`, text: `${compliance.pct} %` }),
          el("div", { class: "comp-ratio", text: `${compliance.rules_ok}/${compliance.rules_total} règles conformes` }),
        ]),
      ]),
      ...compliance.sections.map((section) =>
        el("div", { class: "comp-section" }, [
          el("div", { class: "comp-section-head" }, [
            el("span", { class: "comp-section-title", text: section.title }),
            el("span", { class: "comp-section-line", "aria-hidden": "true" }),
            el("span", {
              class: "comp-section-state",
              style: `color:${section.severity === "blocked" ? "var(--bad-text)" : section.severity === "warn" ? "var(--warn-text)" : "var(--ok-text)"}`,
              text: section.state,
            }),
          ]),
          ...section.rules.map((rule) => {
            const canFocus = !rule.ok && rule.affected_fields?.length;
            return el("div", { class: "comp-rule" }, [
              el("span", {
                class: "comp-mark",
                style: rule.ok
                  ? "background:var(--ok-soft);color:var(--ok-text)"
                  : rule.blocking
                    ? "background:var(--bad-soft);color:var(--bad-text)"
                    : "background:var(--warn-soft-2);color:var(--warn-text)",
                text: rule.ok ? "✓" : rule.blocking ? "!" : "~",
              }),
              canFocus
                ? el("button", {
                    type: "button", class: "comp-label",
                    style: "text-align:left;text-decoration:underline;text-underline-offset:2px",
                    text: rule.label,
                    onClick: () => focusField(rule.affected_fields[0], { openDoc: true }),
                  })
                : el("span", { class: "comp-label", text: rule.label }),
              !rule.ok && rule.blocking ? el("span", { class: "comp-blocking", text: "bloquant" }) : null,
              el("span", { class: "comp-detail", title: rule.detail, text: rule.detail }),
            ]);
          }),
        ])
      ),
    ]);
  }

  function renderLegend(dossier) {
    return el("div", { class: "legend" }, [
      el("h2", { text: "Données financières extraites" }),
      el("div", { class: "legend-keys" }, [
        legendKey("Confiance OCR élevée", "var(--ok-soft)", "var(--ok)"),
        legendKey("À vérifier (< 80 %)", "var(--warn-soft)", "var(--warn)"),
        legendKey("Incohérence / non lu", "var(--bad-soft)", "var(--bad)"),
        legendKey("Calculé — verrouillé", "#F1F5F9", "#CBD5E1"),
      ]),
      el("span", {
        class: "legend-note",
        text: `${pluralize(dossier.overrides.length, "poste")} corrigé(s) · 20 postes RCC`,
      }),
    ]);
  }

  function legendKey(label, bg, border) {
    return el("span", { class: "legend-key" }, [
      el("span", { class: "legend-swatch", style: `background:${bg};border-color:${border}` }),
      label,
    ]);
  }

  /* -- groupes de champs ---------------------------------------------------- */

  const rowNodes = new Map();

  function renderGroup(group) {
    const { dossier } = state.data;
    const byCode = new Map((dossier.result?.fields ?? []).map((f) => [f.code, f]));
    const overrides = new Map(dossier.overrides.map((o) => [o.field_code, o]));

    let toCheck = 0;
    let blocked = 0;
    const rows = [];

    for (const code of group.codes) {
      const field = byCode.get(code) || {
        code, label: code, value: null, status: "missing", confidence: 0, evidence: [],
      };
      const override = overrides.get(code) || null;
      const meta = fieldState(field, override);
      if (meta.state === "low") toCheck += 1;
      if (meta.state === "conflict" || meta.state === "missing") blocked += 1;
      rows.push(buildFieldRow(field, override, meta));
    }

    const stateLabel = blocked
      ? `${blocked} poste(s) à reprendre`
      : toCheck
        ? `${toCheck} poste(s) à vérifier`
        : "Contrôlé";
    const stateStyle = blocked
      ? "color:var(--bad-text);background:var(--bad-soft)"
      : toCheck
        ? "color:var(--warn-text);background:var(--warn-soft-2)"
        : "color:var(--ok-text);background:var(--ok-soft)";

    return el("section", { class: "group", "aria-label": group.title }, [
      el("div", { class: "group-head" }, [
        el("span", { class: "group-letter", "aria-hidden": "true", text: group.letter }),
        el("h3", { class: "group-title", text: group.title }),
        el("span", { class: "group-subtitle", text: group.subtitle }),
        el("span", { class: "group-state", style: stateStyle, text: stateLabel }),
      ]),
      el("div", { class: "group-body" }, rows),
    ]);
  }

  function buildFieldRow(field, override, meta) {
    const code = field.code;
    const inputId = `f-${code}`;
    const isTag = TAG_CODES.has(code);
    const locked = LOCKED_CODES.has(code);
    const displayValue = override ? override.corrected_value : field.value;

    const row = el("div", {
      class: "frow",
      dataset: { code, state: meta.state },
      onMouseenter: () => showAuditTip(row, field, override),
      onMouseleave: () => hideAuditTip(row),
    });

    // ---- libellé + drapeaux
    const head = el("div", { class: "frow-head" }, [
      el("label", { class: "frow-label", for: isTag ? null : inputId, text: field.label }),
      override
        ? el("span", { class: "frow-flag frow-flag-edit hint", "data-hint": `Corrigé par ${override.edited_by}` },
            [icon(ICONS.pencil, { size: 11, width: 2.2 })])
        : null,
      locked
        ? el("span", { class: "frow-flag frow-flag-lock hint", "data-hint": "Calculé par le pipeline — non modifiable" },
            [icon(ICONS.lock, { size: 11, width: 2 })])
        : null,
      field.evidence?.length
        ? el("button", {
            type: "button",
            class: `locate${state.activeCode === code ? " is-active" : ""}`,
            "aria-label": `Localiser ${field.label} dans le document`,
            onClick: () => focusField(code, { openDoc: true }),
          }, [icon(ICONS.highlight, { size: 9, width: 2.4 }), "voir"])
        : null,
      // Confirmation d'un poste sous le seuil de confiance dont la valeur OCR
      // est correcte : sans cela, la règle de conformité serait insoluble.
      meta.state === "low"
        ? el("button", {
            type: "button",
            class: "locate hint",
            "data-hint": "Confirmer la valeur lue par l'OCR sans la modifier",
            "aria-label": `Marquer ${field.label} comme vérifié`,
            onClick: (event) => { event.preventDefault(); verifyField(code); },
          }, [icon(ICONS.check, { size: 9, width: 2.6 }), "marquer vérifié"])
        : null,
      field.note ? el("span", { class: "badge badge-neutral badge-xs", text: field.note }) : null,
    ]);

    row.appendChild(
      el("div", { class: "frow-main" }, [head, el("p", { class: "frow-formula", text: FORMULAS[code] || "" })])
    );

    // ---- exercice N-1 (uniquement si réellement extrait)
    if (field.value_n1 != null) {
      const current = displayValue;
      const variation = current != null && field.value_n1 !== 0
        ? ((current - field.value_n1) / Math.abs(field.value_n1)) * 100
        : null;
      const varColor = variation == null ? "var(--muted-2)"
        : Math.abs(variation) > 40 ? "var(--bad-text)"
        : Math.abs(variation) > 20 ? "var(--warn-text)"
        : variation >= 0 ? "var(--ok-text)" : "var(--muted)";
      row.appendChild(
        el("div", { class: "frow-prev hint", "data-hint": "Exercice N-1 extrait de la liasse" }, [
          el("div", { class: "frow-prev-val", text: formatAmount(field.value_n1) }),
          el("div", {
            class: "frow-prev-var", style: `color:${varColor}`,
            text: variation == null ? "" : `${variation >= 0 ? "+" : ""}${variation.toFixed(1).replace(".", ",")} %`,
          }),
        ])
      );
    }

    // ---- valeur
    if (isTag) {
      const tagValue = field.note || "Non déterminé";
      const cls = tagValue === "Bénéficiaire" ? "badge-ok"
        : tagValue === "Déficitaire" ? "badge-bad" : "badge-neutral";
      row.appendChild(
        el("div", { class: "frow-input", style: "justify-content:flex-end" }, [
          el("span", { class: `badge ${cls}`, style: "font-size:12.5px;padding:7px 16px", text: tagValue }),
        ])
      );
    } else {
      const input = el("input", {
        id: inputId,
        type: "text",
        inputmode: "decimal",
        autocomplete: "off",
        value: displayValue != null ? formatAmount(displayValue) : "",
        placeholder: meta.state === "missing" ? "non lu" : "",
        readonly: locked,
        "aria-describedby": `${inputId}-conf`,
        "aria-invalid": meta.state === "conflict" || meta.state === "missing" ? "true" : null,
      });

      if (!locked) {
        input.addEventListener("input", () => onFieldInput(code, input, row));
        input.addEventListener("focus", () => focusField(code, { openDoc: false, scroll: false }));
        input.addEventListener("blur", () => {
          const parsed = parseAmount(input.value);
          if (parsed != null) input.value = formatAmount(parsed);
          flushSave(code);
        });
        input.addEventListener("keydown", (event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            flushSave(code);
            input.blur();
          } else if (event.key === "Escape") {
            event.preventDefault();
            input.value = displayValue != null ? formatAmount(displayValue) : "";
            state.savers.get(code)?.cancel();
            state.dirty.delete(code);
            setRowState(row, meta.state);
          }
        });
      }

      row.appendChild(
        el("div", { class: "frow-input" }, [
          el("span", { class: "frow-save-spinner", style: "display:none", "aria-hidden": "true" }),
          el("div", { class: "frow-input-wrap" }, [
            input,
            el("span", { class: "frow-unit", "aria-hidden": "true", text: "MAD" }),
          ]),
          el("span", { id: `${inputId}-conf`, class: `frow-conf ${meta.confClass}`, text: meta.confLabel }),
        ])
      );
    }

    rowNodes.set(code, row);
    return row;
  }

  function setRowState(row, next) {
    row.dataset.state = next;
  }

  /* -- info-bulle piste d'audit -------------------------------------------- */

  function showAuditTip(row, field, override) {
    if (!override || row.querySelector(".audit-tip")) return;
    row.appendChild(
      el("div", { class: "audit-tip", role: "note" }, [
        el("p", { class: "audit-tip-head", text: "Piste d'audit" }),
        el("p", {}, ["Valeur OCR d'origine : ", el("b", { text: `${formatAmount(override.original_value)} MAD` })]),
        el("p", { class: "audit-tip-meta", text: `Corrigé par ${override.edited_by} le ${formatDateTime(override.edited_at)}` }),
      ])
    );
  }

  function hideAuditTip(row) {
    row.querySelector(".audit-tip")?.remove();
  }

  /* -- contrôles comptables bruts ------------------------------------------ */

  function renderControls(controls) {
    if (!controls.length) {
      return el("div", { class: "panel panel-pad" }, [
        el("h2", { style: "font-size:12.5px;font-weight:700", text: "Contrôles de cohérence" }),
        el("p", { style: "font-size:11.5px;color:var(--muted);margin-top:6px",
          text: "Aucun contrôle comptable n'a pu être exécuté sur ce dossier." }),
      ]);
    }
    return el("div", { class: "panel panel-pad" }, [
      el("h2", { style: "font-size:12.5px;font-weight:700;margin-bottom:11px", text: "Contrôles de cohérence" }),
      ...controls.map((control) => {
        const tone = control.status === "passed" ? "conf-ok"
          : control.status === "failed" ? "conf-bad" : "conf-lock";
        const dot = control.status === "passed" ? "var(--ok)"
          : control.status === "failed" ? "var(--bad)" : "var(--muted-2)";
        const verdict = control.status === "passed" ? "Conforme"
          : control.status === "failed" ? "Écart" : "Non testable";
        return el("div", { style: "display:flex;align-items:center;gap:11px;padding:8px 0;border-bottom:1px solid #F4F6F9" }, [
          el("span", { style: `width:8px;height:8px;border-radius:50%;background:${dot};flex:0 0 8px`, "aria-hidden": "true" }),
          el("span", { style: "flex:1;font-size:12px;color:var(--ink-3)", text: control.label }),
          el("span", { class: "comp-detail", title: control.message, text: control.message }),
          el("span", { class: `${tone}`, style: "flex:0 0 86px;text-align:right;font-size:11.5px;font-weight:700", text: verdict }),
        ]);
      }),
    ]);
  }

  /* ========================================================= enregistrement === */

  function onFieldInput(code, input, row) {
    const parsed = parseAmount(input.value);
    state.dirty.set(code, parsed);
    setRowState(row, "saving");
    row.querySelector(".frow-save-spinner").style.display = "block";

    if (!state.savers.has(code)) {
      state.savers.set(code, debounce(() => saveField(code), SAVE_DEBOUNCE));
    }
    state.savers.get(code)();
  }

  function flushSave(code) {
    const saver = state.savers.get(code);
    if (saver && state.dirty.has(code)) saver.flush();
  }

  /** Enregistrement optimiste d'un poste, avec retour arrière en cas d'échec. */
  async function saveField(code) {
    if (!state.dirty.has(code)) return;
    const value = state.dirty.get(code);
    state.dirty.delete(code);

    const row = rowNodes.get(code);
    const input = row?.querySelector("input");
    const spinner = row?.querySelector(".frow-save-spinner");
    const previous = state.data.dossier.overrides.find((o) => o.field_code === code);
    const ocrValue = state.data.dossier.result?.fields.find((f) => f.code === code)?.value ?? null;

    // Optimiste : le poste passe immédiatement en « corrigé ».
    if (row) setRowState(row, value == null ? "missing" : value === ocrValue ? "ok" : "edited");

    try {
      const updated = await api.dossiers.saveOverrides(state.data.dossier.id, [
        { field_code: code, corrected_value: value },
      ]);
      state.data = updated;
      if (spinner) spinner.style.display = "none";
      // Le panneau de conformité dépend de la nouvelle valeur : on le régénère,
      // sans reconstruire les champs qui n'ont pas bougé.
      refreshDerived(code);
      onDossierChanged?.(updated.dossier);
      announce(`${code} enregistré.`);
    } catch (error) {
      if (spinner) spinner.style.display = "none";
      if (error.isAuth) return;
      // Retour arrière : on restaure la valeur connue du serveur.
      const fallback = previous ? previous.corrected_value : ocrValue;
      if (input) input.value = fallback != null ? formatAmount(fallback) : "";
      if (row) {
        const field = state.data.dossier.result?.fields.find((f) => f.code === code);
        setRowState(row, field ? fieldState(field, previous).state : "missing");
      }
      toast(error.message, { title: "Correction non enregistrée", type: "bad" });
    }
  }

  /**
   * Régénère les blocs dérivés (conformité, contrôles, en-tête, états de groupe)
   * sans toucher au champ en cours d'édition ni perdre le focus.
   */
  function refreshDerived(editedCode) {
    const focused = document.activeElement;
    const focusedCode = focused?.id?.startsWith("f-") ? focused.id.slice(2) : null;
    const selectionStart = focused?.selectionStart;

    rowNodes.clear();
    renderForm();
    renderBar();

    if (focusedCode) {
      const input = formNode.querySelector(`#f-${CSS.escape(focusedCode)}`);
      if (input) {
        input.focus();
        try { input.setSelectionRange(selectionStart, selectionStart); } catch { /* champ non textuel */ }
      }
    } else if (editedCode) {
      rowNodes.get(editedCode)?.classList.add("is-flash");
    }
  }

  /** Confirme la valeur OCR d'un poste à faible confiance (sans la modifier). */
  async function verifyField(code) {
    const row = rowNodes.get(code);
    const previousState = row?.dataset.state;
    if (row) setRowState(row, "verified"); // optimiste

    try {
      const updated = await api.dossiers.saveOverrides(state.data.dossier.id, [
        { field_code: code, corrected_value: null, verified: true },
      ]);
      state.data = updated;
      refreshDerived(code);
      onDossierChanged?.(updated.dossier);
      announce(`${code} marqué comme vérifié.`);
    } catch (error) {
      if (error.isAuth) return;
      if (row && previousState) setRowState(row, previousState);
      toast(error.message, { title: "Vérification non enregistrée", type: "bad" });
    }
  }

  /* =============================================================== actions === */

  function focusField(code, { openDoc = false, scroll = true } = {}) {
    state.activeCode = code;
    if (openDoc && state.data?.dossier.has_document) {
      if (state.paneTab !== "doc") state.paneTab = "doc";
      renderViewer();
    } else {
      // Met à jour uniquement les pastilles « voir » actives
      for (const [rowCode, row] of rowNodes) {
        row.querySelector(".locate")?.classList.toggle("is-active", rowCode === code);
      }
      if (state.paneTab === "zones") renderViewer();
    }
    if (!scroll) return;
    const row = rowNodes.get(code);
    if (row) {
      row.scrollIntoView({ behavior: "smooth", block: "center" });
      row.classList.remove("is-target");
      void row.offsetWidth; // relance l'animation
      row.classList.add("is-target");
      row.querySelector("input:not([readonly])")?.focus();
    }
  }

  async function patchDossier(payload, { successTitle, successText }) {
    const previous = state.data;
    try {
      const updated = await api.dossiers.patch(state.data.dossier.id, payload);
      state.data = updated;
      renderAll();
      onDossierChanged?.(updated.dossier);
      toast(successText, { title: successTitle, type: "ok" });
      return updated;
    } catch (error) {
      if (error.isAuth) return null;
      state.data = previous;
      renderAll();
      toast(error.message, { title: "Action impossible", type: "bad" });
      return null;
    }
  }

  async function onValidate() {
    const { dossier, compliance } = state.data;
    if (!compliance.can_validate) return;

    const button = barNode.querySelector(".btn-ok");
    button?.classList.add("is-busy");
    const updated = await patchDossier(
      { status: "validated" },
      { successTitle: "Dossier validé", successText: `${dossier.id} a été transmis au modèle EKIP.` }
    );
    button?.classList.remove("is-busy");
    if (!updated) return;

    openModal(
      (close) =>
        el("div", { class: "modal-center" }, [
          el("div", { class: "modal-check" }, [icon(ICONS.check, { size: 26, stroke: "var(--ok)", width: 2.6 })]),
          el("h2", { class: "modal-title", id: "validatedTitle", text: "Dossier validé" }),
          el("p", { class: "modal-sub" }, [
            "Les postes RCC contrôlés du dossier ",
            el("b", { class: "mono", text: dossier.id }),
            " ont été transmis au modèle EKIP. La piste d'audit des corrections est archivée.",
          ]),
          el("dl", { class: "modal-stats" }, [
            el("div", {}, [
              el("dt", { text: String(updated.dossier.overrides.length) }),
              el("dd", { text: "postes corrigés" }),
            ]),
            el("div", {}, [
              el("dt", { style: "color:var(--ok-text)", text: `${updated.compliance.rules_ok}/${updated.compliance.rules_total}` }),
              el("dd", { text: "règles conformes" }),
            ]),
            el("div", {}, [
              el("dt", { style: "font-size:13px", text: updated.dossier.decided_by || "—" }),
              el("dd", { text: "valideur" }),
            ]),
          ]),
          el("button", {
            type: "button", class: "btn btn-primary btn-block", autofocus: true,
            style: "margin-top:18px", text: "Retour aux dossiers",
            onClick: () => { close(); onBack(); },
          }),
        ]),
      { labelledBy: "validatedTitle" }
    );
  }

  function onReject() {
    const { dossier } = state.data;
    let motif = REJECT_MOTIFS[0];

    openModal(
      (close) => {
        const commentField = el("textarea", {
          placeholder: "Commentaire à l'attention du gestionnaire (facultatif)…",
          "aria-label": "Commentaire de rejet",
        });
        const confirmBtn = el("button", {
          type: "button", class: "btn btn-danger-solid",
          onClick: async () => {
            confirmBtn.classList.add("is-busy");
            const updated = await patchDossier(
              { status: "rejected", motif, comment: commentField.value.trim() || null },
              { successTitle: "Dossier rejeté", successText: `${dossier.id} retourne au gestionnaire.` }
            );
            confirmBtn.classList.remove("is-busy");
            if (updated) { close(); onBack(); }
          },
        }, [
          el("span", { class: "btn-label", text: "Confirmer le rejet" }),
          el("span", { class: "btn-spinner", "aria-hidden": "true" }),
        ]);

        return el("div", {}, [
          el("h2", { class: "modal-title", id: "rejectTitle", text: `Rejeter le dossier ${dossier.id}` }),
          el("p", { class: "modal-sub", text: "Le dossier retourne au gestionnaire avec le motif choisi. Aucune donnée n'est transmise au modèle EKIP." }),
          el("fieldset", { class: "radio-list" },
            REJECT_MOTIFS.map((label, index) =>
              el("label", { class: `radio-item${index === 0 ? " is-checked" : ""}` }, [
                el("input", {
                  type: "radio", name: "motif", value: label, checked: index === 0,
                  autofocus: index === 0,
                  onChange: (event) => {
                    motif = label;
                    // Repli pour les navigateurs sans `:has()`
                    const list = event.target.closest(".radio-list");
                    for (const item of list.querySelectorAll(".radio-item")) {
                      item.classList.toggle("is-checked", item.contains(event.target));
                    }
                  },
                }),
                label,
              ])
            )
          ),
          commentField,
          el("div", { class: "modal-actions" }, [
            el("button", { type: "button", class: "btn btn-ghost", text: "Annuler", onClick: close }),
            confirmBtn,
          ]),
        ]);
      },
      { labelledBy: "rejectTitle" }
    );
  }

  function onEscalate() {
    const { dossier, compliance } = state.data;

    openModal(
      (close) => {
        const commentField = el("textarea", {
          placeholder: "Question posée au superviseur…",
          "aria-label": "Question au superviseur",
          autofocus: true,
        });
        const confirmBtn = el("button", {
          type: "button", class: "btn btn-dark",
          onClick: async () => {
            confirmBtn.classList.add("is-busy");
            const updated = await patchDossier(
              {
                status: "escalated",
                motif: "Arbitrage superviseur demandé",
                comment: commentField.value.trim() || null,
              },
              { successTitle: "Arbitrage demandé", successText: `${dossier.id} sort de votre file.` }
            );
            confirmBtn.classList.remove("is-busy");
            if (updated) close();
          },
        }, [
          el("span", { class: "btn-label", text: "Transmettre au superviseur" }),
          el("span", { class: "btn-spinner", "aria-hidden": "true" }),
        ]);

        return el("div", {}, [
          el("h2", { class: "modal-title", id: "escalateTitle", text: "Demander un arbitrage superviseur" }),
          el("p", { class: "modal-sub", text: "Le dossier reste en attente et sort de votre file. Un superviseur RCC tranche sur les points signalés." }),
          el("div", { class: "modal-recap" }, [
            el("p", { class: "modal-recap-title", text: "Points transmis automatiquement" }),
            el("ul", {}, [
              el("li", { text: `· Règles de conformité bloquantes : ${compliance.blockers}` }),
              el("li", { text: `· Postes en incohérence d'extraction : ${compliance.conflicting_fields.length}` }),
              el("li", { text: `· Postes sous le seuil de confiance : ${compliance.low_confidence_fields.length}` }),
              el("li", { text: `· Postes non lus : ${compliance.missing_fields.length}` }),
            ]),
          ]),
          commentField,
          el("div", { class: "modal-actions" }, [
            el("button", { type: "button", class: "btn btn-ghost", text: "Annuler", onClick: close }),
            confirmBtn,
          ]),
        ]);
      },
      { labelledBy: "escalateTitle" }
    );
  }

  /* =============================================================== rendu === */

  function renderAll() {
    rowNodes.clear();
    renderBar();
    renderViewer();
    renderForm();
    splitToggle.hidden = !state.data;
  }

  return {
    async open(dossierId) {
      state.activeCode = null;
      state.paneTab = "doc";
      splitNode.classList.remove("is-form-only");
      splitToggle.querySelector(".split-toggle-label").textContent = "Voir le formulaire";
      await load(dossierId);
    },
    get currentId() { return state.id; },
    /** Enregistre les saisies en attente avant de quitter l'écran. */
    flushPending() {
      for (const code of [...state.dirty.keys()]) flushSave(code);
    },
    /** Libère les suivis d'extraction (déconnexion). */
    destroy() {
      importPanel?.destroy();
      importPanel = null;
    },
  };
}
