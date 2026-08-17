/* Écran de validation — visionneuse à gauche, formulaire RCC à droite. */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import * as api from "../lib/api.js";
import { exportDossierJson, exportDossierPdf, exportDossierWorkbook } from "../lib/dossierExport.js";
import { STATUS_META } from "../lib/fields.js";
import { formatAmount, formatAmountMad, formatDate, pluralize } from "../lib/format.js";
import Icon, { ICONS } from "../components/Icon.jsx";
import { Badge, ErrorState, SkeletonRows } from "../components/States.jsx";
import Banner from "../components/detail/Banner.jsx";
import CompliancePanel from "../components/detail/CompliancePanel.jsx";
import FieldGroups from "../components/detail/FieldGroups.jsx";
import ViewerPane from "../components/detail/ViewerPane.jsx";
import { RejectModal, ValidatedModal } from "../components/detail/DetailModals.jsx";
import { useSession } from "../hooks/useSession.jsx";
import { useToasts } from "../hooks/useToasts.jsx";

const SAVE_DEBOUNCE = 650;

export default function DossierDetail({ onDossierChanged }) {
  const { dossierId } = useParams();
  const navigate = useNavigate();
  const { toast, announce } = useToasts();
  const { user } = useSession();

  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [paneTab, setPaneTab] = useState("doc");
  const [activeCode, setActiveCode] = useState(null);
  const [activeEvidencePage, setActiveEvidencePage] = useState(null);
  const [targetCode, setTargetCode] = useState(null);
  const [savingCodes, setSavingCodes] = useState(new Set());
  const [formOnly, setFormOnly] = useState(false);
  const [modal, setModal] = useState(null); // "reject" | "validated"
  const [busyAction, setBusyAction] = useState(null);

  const pendingEdits = useRef(new Map()); // code → valeur en attente
  const timers = useRef(new Map());
  const rowNodes = useRef(new Map());

  const registerRow = useCallback((code, node) => {
    if (node) rowNodes.current.set(code, node);
    else rowNodes.current.delete(code);
  }, []);

  /* ------------------------------------------------------------ chargement --- */

  const load = useCallback(
    async (id, { silent = false } = {}) => {
      if (!silent) {
        setLoading(true);
        setError(null);
      }
      try {
        const payload = await api.dossiers.get(id);
        setData(payload);
        if (!payload.dossier.has_document) setPaneTab("import");
        setError(null);
      } catch (err) {
        if (err.isAuth) return;
        setError(err.message);
      } finally {
        setLoading(false);
      }
    },
    []
  );

  useEffect(() => {
    setActiveCode(null);
    setActiveEvidencePage(null);
    setPaneTab("doc");
    setFormOnly(false);
    load(dossierId);
  }, [dossierId, load]);

  // Les corrections en attente partent avant de quitter l'écran ou l'onglet.
  const flushAll = useCallback(() => {
    for (const [code, timer] of timers.current) {
      clearTimeout(timer);
      timers.current.delete(code);
      if (pendingEdits.current.has(code)) saveFieldRef.current(code);
    }
  }, []);

  const saveFieldRef = useRef(() => {});

  useEffect(() => {
    const onHide = () => {
      if (document.visibilityState === "hidden") flushAll();
    };
    window.addEventListener("beforeunload", flushAll);
    document.addEventListener("visibilitychange", onHide);
    return () => {
      window.removeEventListener("beforeunload", flushAll);
      document.removeEventListener("visibilitychange", onHide);
      flushAll();
    };
  }, [flushAll]);

  /* ------------------------------------------------------ enregistrement --- */

  const markSaving = useCallback((code, on) => {
    setSavingCodes((current) => {
      const next = new Set(current);
      if (on) next.add(code);
      else next.delete(code);
      return next;
    });
  }, []);

  const saveField = useCallback(
    async (code) => {
      if (!pendingEdits.current.has(code)) return;
      const value = pendingEdits.current.get(code);
      pendingEdits.current.delete(code);
      markSaving(code, true);

      try {
        const updated = await api.dossiers.saveOverrides(dossierId, [
          { field_code: code, corrected_value: value },
        ]);
        setData(updated);
        onDossierChanged?.(updated.dossier);
        announce(`${code} enregistré.`);
      } catch (err) {
        if (!err.isAuth) {
          toast(err.message, { title: "Correction non enregistrée", type: "bad" });
          // Retour arrière : on recharge l'état serveur qui fait autorité.
          load(dossierId, { silent: true });
        }
      } finally {
        markSaving(code, false);
      }
    },
    [announce, dossierId, load, markSaving, onDossierChanged, toast]
  );

  saveFieldRef.current = saveField;

  const onEdit = useCallback(
    (code, value, { cancel = false } = {}) => {
      const timer = timers.current.get(code);
      if (timer) clearTimeout(timer);

      if (cancel) {
        pendingEdits.current.delete(code);
        timers.current.delete(code);
        return;
      }

      pendingEdits.current.set(code, value);
      timers.current.set(
        code,
        setTimeout(() => {
          timers.current.delete(code);
          saveField(code);
        }, SAVE_DEBOUNCE)
      );
    },
    [saveField]
  );

  const onCommit = useCallback(
    (code) => {
      const timer = timers.current.get(code);
      if (timer) {
        clearTimeout(timer);
        timers.current.delete(code);
      }
      if (pendingEdits.current.has(code)) saveField(code);
    },
    [saveField]
  );

  const onVerify = useCallback(
    async (code) => {
      markSaving(code, true);
      try {
        const updated = await api.dossiers.saveOverrides(dossierId, [
          { field_code: code, corrected_value: null, verified: true },
        ]);
        setData(updated);
        onDossierChanged?.(updated.dossier);
        announce(`${code} marqué comme vérifié.`);
      } catch (err) {
        if (!err.isAuth) toast(err.message, { title: "Vérification non enregistrée", type: "bad" });
      } finally {
        markSaving(code, false);
      }
    },
    [announce, dossierId, markSaving, onDossierChanged, toast]
  );

  /* -------------------------------------------------------------- actions --- */

  const focusField = useCallback(
    (code, { openDoc = false, scroll = true, pageNumber = null } = {}) => {
      setActiveCode(code);
      setActiveEvidencePage(pageNumber);
      if (openDoc && data?.dossier.has_document) setPaneTab("doc");
      if (!scroll) return;

      const row = rowNodes.current.get(code);
      if (row) {
        row.scrollIntoView({ behavior: "smooth", block: "center" });
        setTargetCode(code);
        setTimeout(() => setTargetCode(null), 1300);
        row.querySelector("input:not([readonly])")?.focus();
      }
    },
    [data?.dossier.has_document]
  );

  const patchDossier = useCallback(
    async (payload, { successTitle, successText }) => {
      try {
        const updated = await api.dossiers.patch(dossierId, payload);
        setData(updated);
        onDossierChanged?.(updated.dossier);
        toast(successText, { title: successTitle, type: "ok" });
        return updated;
      } catch (err) {
        if (!err.isAuth) toast(err.message, { title: "Action impossible", type: "bad" });
        return null;
      }
    },
    [dossierId, onDossierChanged, toast]
  );

  async function onValidate() {
    setBusyAction("validate");
    const updated = await patchDossier(
      { status: "validated" },
      { successTitle: "Dossier validé", successText: `${dossierId} a été transmis au modèle EKIP.` }
    );
    setBusyAction(null);
    if (updated) setModal("validated");
  }

  async function onExport(kind) {
    flushAll();
    const statusLabel = (STATUS_META[data.dossier.status] || STATUS_META.pending).label;
    try {
      if (kind === "excel") await exportDossierWorkbook({ ...data, statusLabel });
      else if (kind === "json") {
        exportDossierJson({
          ...data,
          statusLabel,
          exportedBy: user?.display_name || user?.username || null,
        });
      } else await exportDossierPdf({ ...data, statusLabel });
      toast(
        kind === "excel"
          ? "Le classeur contient une synthèse, les données RCC, les zones extraites et les contrôles."
          : kind === "json"
            ? "Les 20 postes RCC, les valeurs effectives, les preuves et les contrôles sont dans le fichier."
            : "Le rapport analyste est prêt à être partagé ou archivé.",
        {
          title:
            kind === "excel" ? "Classeur Excel téléchargé"
              : kind === "json" ? "Fichier JSON téléchargé"
                : "Rapport PDF téléchargé",
          type: "ok",
        }
      );
    } catch (err) {
      toast(err.message || "L'export n'a pas pu être généré.", { title: "Export impossible", type: "bad" });
    }
  }

  /* ---------------------------------------------------------------- rendu --- */

  if (loading) {
    return (
      <section className="view view-detail is-entering">
        <header className="topbar topbar-detail">
          <div className="skeleton sk-title" style={{ width: 220 }} />
        </header>
        <div className="split">
          <div className="split-viewer">
            <div className="pane-scroll">
              <SkeletonRows count={4} widths={[140, 200, 90]} />
            </div>
          </div>
          <div className="split-form scroll">
            <div className="panel">
              <SkeletonRows count={7} widths={[180, 260, 120, 140]} />
            </div>
          </div>
        </div>
      </section>
    );
  }

  if (error || !data) {
    return (
      <section className="view view-detail is-entering">
        <header className="topbar topbar-detail">
          <button
            type="button"
            className="btn-icon detail-back"
            aria-label="Retour à la liste des dossiers"
            onClick={() => navigate("/dossiers")}
          >
            <Icon paths={ICONS.chevronLeft} size={15} />
          </button>
          <div className="detail-name">Dossier indisponible</div>
        </header>
        <div className="scroll">
          <ErrorState title="Dossier introuvable" message={error} onRetry={() => load(dossierId)}>
            <button type="button" className="btn btn-ghost" onClick={() => navigate("/dossiers")}>
              Retour à la liste
            </button>
          </ErrorState>
        </div>
      </section>
    );
  }

  const { dossier, compliance } = data;
  const overrides = new Map(dossier.overrides.map((o) => [o.field_code, o]));
  const meta = STATUS_META[dossier.status] || STATUS_META.pending;
  const balance = dossier.result?.controls.find((c) => c.code === "bilan_equilibre");

  return (
    <section className="view view-detail is-entering">
      <header className="topbar topbar-detail">
        <button
          type="button"
          className="btn-icon detail-back"
          aria-label="Retour à la liste des dossiers"
          onClick={() => { flushAll(); navigate("/dossiers"); }}
        >
          <Icon paths={ICONS.chevronLeft} size={15} />
        </button>

        <div>
          <div className="detail-head-top">
            <span className="detail-id">{dossier.id}</span>
            <h1 className="detail-name">{dossier.client_name || "Client à identifier"}</h1>
            <Badge tone={meta.cls.replace("badge-", "")} dot>{meta.label}</Badge>
            {dossier.overrides.length ? (
              <Badge>{pluralize(dossier.overrides.length, "poste contrôlé", "postes contrôlés")}</Badge>
            ) : null}
          </div>

          <div className="detail-head-meta">
            <span>Exercice clos le <b>{formatDate(dossier.exercice_date)}</b></span>
            <span>Crédit demandé <b>{formatAmountMad(dossier.credit_amount)}</b></span>
            <span>ICE <b>{dossier.ice || "non détecté"}</b></span>
            <span className={dossier.has_document ? "conf-ok" : "conf-bad"} style={{ fontWeight: 600 }}>
              {dossier.has_document
                ? `Extraction OCR · complétude ${formatAmount(dossier.completeness_pct, { decimals: true })} %`
                : "Liasse non rattachée"}
            </span>
          </div>
        </div>

        <div className="detail-actions">
          <div className="detail-action-group" aria-label="Exporter le dossier">
            <button type="button" className="btn btn-ghost" onClick={() => onExport("json")}>
              <Icon paths={ICONS.file} size={14} width={1.9} />
              Exporter JSON
            </button>
            <button type="button" className="btn btn-ghost" onClick={() => onExport("excel")}>
              <Icon paths={ICONS.file} size={14} width={1.9} />
              Exporter Excel
            </button>
            <button type="button" className="btn btn-dark" onClick={() => onExport("pdf")}>
              <Icon paths={ICONS.file} size={14} width={1.9} />
              Rapport PDF
            </button>
          </div>
          <span className="detail-action-divider" aria-hidden="true" />
          <div className="detail-action-group" aria-label="Décision analyste">
            <button
              type="button"
              className="btn btn-danger"
              disabled={dossier.status === "rejected"}
              onClick={() => setModal("reject")}
            >
              Rejeter
            </button>
            <button
              type="button"
              className={`btn ${compliance.can_validate ? "btn-ok" : "btn-ghost"} hint${busyAction === "validate" ? " is-busy" : ""}`}
              disabled={!compliance.can_validate || dossier.status === "validated" || busyAction === "validate"}
              data-hint={
                dossier.status === "validated"
                  ? "Ce dossier est déjà validé"
                  : compliance.can_validate
                    ? "Transmettre les postes RCC au modèle EKIP"
                    : `${pluralize(compliance.blockers, "règle")} de conformité bloquante(s) à lever avant validation`
              }
              onClick={onValidate}
            >
              <Icon paths={ICONS.check} size={14} width={2.4} />
              <span className="btn-label">Valider le dossier</span>
              <span className="btn-spinner" aria-hidden="true" />
            </button>
          </div>
        </div>
      </header>

      <div className={`split${formOnly ? " is-form-only" : ""}`}>
        <ViewerPane
          dossier={dossier}
          tab={paneTab}
          onTabChange={setPaneTab}
          activeCode={activeCode}
          activeEvidencePage={activeEvidencePage}
          onFocusField={focusField}
          onAttached={async (jobId) => {
            try {
              await api.dossiers.attach(dossier.id, { job_id: jobId });
              toast("Extraction rattachée au dossier.", { title: "Liasse importée", type: "ok" });
              setPaneTab("doc");
              await load(dossier.id, { silent: true });
              onDossierChanged?.(dossier);
            } catch (err) {
              if (!err.isAuth) toast(err.message, { title: "Rattachement impossible", type: "bad" });
            }
          }}
        />

        <button
          type="button"
          className="split-toggle"
          aria-expanded={!formOnly}
          onClick={() => setFormOnly((current) => !current)}
        >
          <span className="split-toggle-label">
            {formOnly ? "Voir le document" : "Voir le formulaire"}
          </span>
        </button>

        <div className="split-form scroll">
          {balance?.status === "failed" ? (
            <Banner
              tone="bad"
              title="Incohérence comptable détectée — Total Actif ≠ Total Passif"
              text={`Actif ${formatAmount(balance.expected)} · Passif ${formatAmount(balance.observed)} · écart de ${formatAmount(Math.abs(balance.difference ?? 0))} MAD. La validation est bloquée tant que l'équilibre n'est pas rétabli.`}
            >
              <button
                type="button"
                className="btn btn-danger-solid btn-sm"
                onClick={() => focusField(balance.affected_fields?.[0] || "TOTAL_BILAN", { openDoc: true })}
              >
                Voir le poste concerné
              </button>
            </Banner>
          ) : balance?.status === "passed" ? (
            <Banner
              tone="ok"
              title="Équilibre comptable vérifié."
              text={`Total Actif = Total Passif = ${formatAmount(balance.observed)} MAD.`}
            />
          ) : !dossier.has_document ? (
            <Banner
              tone="bad"
              title="Liasse manquante."
              text="Aucune extraction n'est rattachée à ce dossier : la validation reste bloquée jusqu'à l'import du bilan scanné."
            >
              <button type="button" className="btn btn-danger-solid btn-sm" onClick={() => setPaneTab("import")}>
                Importer la liasse
              </button>
            </Banner>
          ) : (
            <Banner
              tone="neutral"
              title="Équilibre actif / passif non testable."
              text={balance?.message || "Les totaux actif et passif n'ont pas pu être isolés dans la liasse."}
            />
          )}

          <CompliancePanel compliance={compliance} onFocusField={focusField} />

          <div className="legend">
            <h2>Données financières extraites</h2>
            <div className="legend-keys">
              <LegendKey label="Confiance OCR élevée" bg="var(--ok-soft)" border="var(--ok)" />
              <LegendKey label="À vérifier (< 80 %)" bg="var(--warn-soft)" border="var(--warn)" />
              <LegendKey label="Incohérence / non lu" bg="var(--bad-soft)" border="var(--bad)" />
              <LegendKey label="Calculé — verrouillé" bg="#F1F5F9" border="#CBD5E1" />
            </div>
            <span className="legend-note">
              {`${pluralize(dossier.overrides.length, "poste")} contrôlé(s) · 20 postes RCC`}
            </span>
          </div>

          <FieldGroups
            fields={dossier.result?.fields ?? []}
            overrides={overrides}
            activeCode={activeCode}
            savingCodes={savingCodes}
            targetCode={targetCode}
            onEdit={onEdit}
            onCommit={onCommit}
            onVerify={onVerify}
            onFocusField={focusField}
            registerRow={registerRow}
          />

          <ControlsPanel controls={dossier.result?.controls ?? []} />

          <ExtractionWarnings warnings={dossier.result?.warnings ?? []} />
        </div>
      </div>

      <RejectModal
        open={modal === "reject"}
        dossier={dossier}
        onClose={() => setModal(null)}
        onConfirm={async ({ motif, comment }) => {
          const updated = await patchDossier(
            { status: "rejected", motif, comment },
            { successTitle: "Dossier rejeté", successText: `${dossier.id} retourne au gestionnaire.` }
          );
          if (updated) navigate("/dossiers");
          return Boolean(updated);
        }}
      />

      <ValidatedModal
        open={modal === "validated"}
        dossier={dossier}
        compliance={compliance}
        onClose={() => { setModal(null); navigate("/dossiers"); }}
      />
    </section>
  );
}

function LegendKey({ label, bg, border }) {
  return (
    <span className="legend-key">
      <span className="legend-swatch" style={{ background: bg, borderColor: border }} />
      {label}
    </span>
  );
}

function ControlsPanel({ controls }) {
  if (!controls.length) {
    return (
      <div className="panel panel-pad">
        <h2 style={{ fontSize: "12.5px", fontWeight: 700 }}>Contrôles de cohérence</h2>
        <p style={{ fontSize: "11.5px", color: "var(--muted)", marginTop: 6 }}>
          Aucun contrôle comptable n'a pu être exécuté sur ce dossier.
        </p>
      </div>
    );
  }

  return (
    <section className="panel panel-pad controls-panel">
      <div className="controls-head">
        <div>
          <h2>Contrôles de cohérence</h2>
          <p>Vérifications automatiques déterminantes pour la validation.</p>
        </div>
        <Badge>{`${controls.filter((control) => control.status === "passed").length}/${controls.length} conformes`}</Badge>
      </div>
      {controls.map((control) => {
        const tone = control.status === "passed" ? "conf-ok"
          : control.status === "failed" ? "conf-bad" : "conf-lock";
        const dot = control.status === "passed" ? "var(--ok)"
          : control.status === "failed" ? "var(--bad)" : "var(--muted-2)";
        const verdict = control.status === "passed" ? "Conforme"
          : control.status === "failed" ? "Écart" : "Non testable";

        return (
          <div key={control.code} className="control-row">
            <span className="control-dot" style={{ background: dot }} aria-hidden="true" />
            <span className="control-label">{control.label}</span>
            <span className="comp-detail" title={control.message}>{control.message}</span>
            <span className={`control-verdict ${tone}`}>{verdict}</span>
          </div>
        );
      })}
    </section>
  );
}

function ExtractionWarnings({ warnings }) {
  if (!warnings.length) return null;

  return (
    <section className="panel extraction-warnings">
      <div className="extraction-warnings-summary">
        <span className="extraction-warning-icon" aria-hidden="true">!</span>
        <div>
          <h2>Points d'attention de l'extraction</h2>
          <p>{`${warnings.length} signalement${warnings.length > 1 ? "s" : ""} technique${warnings.length > 1 ? "s" : ""} conservé${warnings.length > 1 ? "s" : ""} pour l'audit.`}</p>
        </div>
      </div>
      <details>
        <summary>Consulter le journal technique</summary>
        <ul>
          {warnings.map((warning) => <li key={warning}>{humanizeWarning(warning)}</li>)}
        </ul>
      </details>
    </section>
  );
}

function humanizeWarning(warning) {
  return String(warning)
    .replace(/_/g, " ")
    .replace(/\bconflicting\b/gi, "présente des valeurs divergentes")
    .replace(/\bexclus\b/gi, "écartés")
    .replace(/\binvalidé\b/gi, "invalidé")
    .replace(/\s+/g, " ")
    .trim();
}
