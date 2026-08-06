/* Écran « Validation RCC · bilans OCR » — file de validation. */

import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import * as api from "../lib/api.js";
import { STATUS_META } from "../lib/fields.js";
import { formatAmountMad, formatDate, pluralize } from "../lib/format.js";
import Icon, { ICONS } from "../components/Icon.jsx";
import { Badge, EmptyState, ErrorState, SkeletonRows } from "../components/States.jsx";
import { TopBar } from "../components/AppShell.jsx";
import { useDebouncedValue } from "../hooks/index.js";
import { useToasts } from "../hooks/useToasts.jsx";

const FILTERS = [
  { key: "pending", label: "À réviser" },
  { key: "validated", label: "Validés" },
  { key: "rejected", label: "Rejetés" },
  { key: "escalated", label: "Arbitrage" },
  { key: "all", label: "Tous" },
];

export default function DossierList({ activeId }) {
  const navigate = useNavigate();
  const { announce } = useToasts();
  const [params, setParams] = useSearchParams();

  const status = params.get("statut") || "pending";
  const [search, setSearch] = useState(params.get("q") || "");
  const debouncedSearch = useDebouncedValue(search, 280);

  const [data, setData] = useState({ items: [], counts: {} });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const abortRef = useRef(null);

  const load = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setError(null);

    try {
      const payload = await api.dossiers.list({
        status,
        search: debouncedSearch.trim(),
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      setData(payload);
      announce(`${pluralize(payload.items.length, "dossier")} affiché(s).`);
    } catch (err) {
      if (err.name === "AbortError" || controller.signal.aborted || err.isAuth) return;
      setError(err.message);
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
        setLoading(false);
      }
    }
  }, [status, debouncedSearch, announce]);

  useEffect(() => {
    setLoading(true);
    load();
    return () => abortRef.current?.abort();
  }, [load]);

  // L'URL porte le filtre et la recherche : un lien reste reproductible.
  useEffect(() => {
    const next = new URLSearchParams();
    if (status !== "pending") next.set("statut", status);
    if (debouncedSearch.trim()) next.set("q", debouncedSearch.trim());
    setParams(next, { replace: true });
  }, [status, debouncedSearch, setParams]);

  function setStatus(nextStatus) {
    const next = new URLSearchParams(params);
    if (nextStatus === "pending") next.delete("statut");
    else next.set("statut", nextStatus);
    setParams(next, { replace: true });
  }

  const counts = data.counts || {};
  const searching = Boolean(debouncedSearch.trim());
  const hiddenByFilter = searching && status !== "all";
  const filterLabel = FILTERS.find((f) => f.key === status)?.label ?? "";

  const stats = [
    { label: "En attente de validation", value: counts.pending ?? 0, note: "dossiers", tone: "is-warn" },
    { label: "Validés", value: counts.validated ?? 0, note: "transmis EKIP", tone: "is-ok" },
    { label: "Rejetés", value: counts.rejected ?? 0, note: "retournés", tone: "is-bad" },
    { label: "En arbitrage", value: counts.escalated ?? 0, note: "superviseur", tone: "is-accent" },
  ];

  return (
    <section className="view is-entering">
      <TopBar
        title="Validation RCC · bilans OCR"
        subtitle="Bilans extraits par OCR en attente de contrôle humain avant enrichissement du modèle EKIP."
      >
        <button type="button" className="btn btn-ghost" onClick={() => navigate("/import")}>
          Importer une liasse
        </button>
      </TopBar>

      <div className="scroll">
        <div className="wrap">
          <div className="stats">
            {stats.map((card) => (
              <div className="stat" key={card.label}>
                <p className="stat-label">{card.label}</p>
                <div className="stat-row">
                  <span className={`stat-value ${card.tone}`}>{card.value}</span>
                  <span className="stat-note">{card.note}</span>
                </div>
              </div>
            ))}
          </div>

          <div className="panel">
            <div className="table-toolbar">
              <div className="segmented" role="tablist" aria-label="Filtrer par statut">
                {FILTERS.map((filter) => (
                  <button
                    key={filter.key}
                    type="button"
                    role="tab"
                    aria-selected={status === filter.key}
                    onClick={() => setStatus(filter.key)}
                  >
                    {filter.label}
                    {counts[filter.key] != null ? (
                      <span className="seg-count">{counts[filter.key]}</span>
                    ) : null}
                  </button>
                ))}
              </div>

              <div className="search">
                <Icon paths={ICONS.search} size={14} width={2} />
                <label className="sr-only" htmlFor="listSearch">
                  Rechercher un dossier
                </label>
                <input
                  id="listSearch"
                  type="search"
                  autoComplete="off"
                  placeholder="N° de dossier, raison sociale ou ICE…"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Escape") setSearch("");
                  }}
                />
                {search && search !== debouncedSearch ? (
                  <span className="search-spinner" aria-hidden="true" />
                ) : null}
              </div>

              <span className="table-count" aria-live="polite">
                {loading ? "" : pluralize(data.items.length, "dossier")}
              </span>
            </div>

            <div className="table-scroll">
              <div className="table" role="table" aria-label="Dossiers RCC">
                <div className="tr th" role="row">
                  <span role="columnheader" className="c-id">N° de dossier</span>
                  <span role="columnheader" className="c-name">Client / Raison sociale</span>
                  <span role="columnheader" className="c-date">Date d'exercice</span>
                  <span role="columnheader" className="c-amount">Crédit demandé</span>
                  <span role="columnheader" className="c-status">Statut du dossier</span>
                  <span role="columnheader" className="c-actions">Actions</span>
                </div>

                <div role="rowgroup">
                  {loading ? (
                    <SkeletonRows count={6} widths={[96, 210, 110, 130, 120]} />
                  ) : error ? (
                    <ErrorState message={error} onRetry={load} />
                  ) : data.items.length === 0 ? (
                    <EmptyState
                      title={searching ? "Aucun résultat" : `Aucun dossier « ${filterLabel.toLowerCase()} »`}
                      text={
                        searching
                          ? hiddenByFilter
                            ? `Aucun dossier « ${filterLabel.toLowerCase()} » ne correspond à « ${debouncedSearch.trim()} ». Il existe peut-être sous un autre statut.`
                            : `Aucun dossier ne correspond à « ${debouncedSearch.trim()} ». Essayez un autre numéro, une raison sociale ou un ICE.`
                          : "Les dossiers apparaissent ici dès qu'une liasse fiscale a été importée et extraite par le moteur OCR."
                      }
                    >
                      {hiddenByFilter ? (
                        <button type="button" className="btn btn-primary" onClick={() => setStatus("all")}>
                          Chercher dans tous les statuts
                        </button>
                      ) : null}
                      {searching ? (
                        <button type="button" className="btn btn-ghost" onClick={() => setSearch("")}>
                          Effacer la recherche
                        </button>
                      ) : (
                        <button type="button" className="btn btn-primary" onClick={() => navigate("/import")}>
                          Importer une liasse
                        </button>
                      )}
                    </EmptyState>
                  ) : (
                    data.items.map((dossier) => (
                      <DossierRow
                        key={dossier.id}
                        dossier={dossier}
                        active={dossier.id === activeId}
                        onOpen={() => navigate(`/validation/${encodeURIComponent(dossier.id)}`)}
                      />
                    ))
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

function DossierRow({ dossier, active, onOpen }) {
  const meta = STATUS_META[dossier.status] || STATUS_META.pending;
  const tone = meta.cls.replace("badge-", "");

  return (
    <div
      className={`row tr${active ? " is-active" : ""}`}
      role="row"
      tabIndex={0}
      aria-label={`Dossier ${dossier.id}, ${dossier.client_name}, ${meta.label}`}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen();
        }
      }}
    >
      <span className="c-id" role="cell">
        <span className="row-id">{dossier.id}</span>
      </span>

      <span className="c-name" role="cell">
        <span className="row-name" title={dossier.client_name}>
          {dossier.client_name || "—"}
        </span>
        <span className="row-sub">
          <span className="row-sector">{dossier.sector || "Secteur non renseigné"}</span>
          <Badge tone={dossier.has_document ? "ok" : "bad"} className="badge-xs">
            {dossier.has_document ? "Liasse OCR ✓" : "Liasse manquante"}
          </Badge>
        </span>
      </span>

      <span className="c-date row-date" role="cell">{formatDate(dossier.exercice_date)}</span>
      <span className="c-amount row-amount" role="cell">{formatAmountMad(dossier.credit_amount)}</span>

      <span className="c-status" role="cell">
        <Badge tone={tone} dot>{meta.label}</Badge>
      </span>

      <span className="c-actions" role="cell">
        <button
          type="button"
          className="btn-icon hint"
          data-hint="Ouvrir le dossier"
          aria-label={`Ouvrir ${dossier.id}`}
          onClick={(event) => { event.stopPropagation(); onOpen(); }}
        >
          <Icon paths={ICONS.eye} size={14} width={1.8} style={{ stroke: "#64748B" }} />
        </button>
        <button
          type="button"
          className="btn-icon hint"
          data-hint={
            dossier.override_count
              ? `${pluralize(dossier.override_count, "correction")} enregistrée(s)`
              : "Corriger les postes extraits"
          }
          aria-label={`Corriger ${dossier.id}`}
          onClick={(event) => { event.stopPropagation(); onOpen(); }}
        >
          <Icon paths={ICONS.pencil} size={13} width={1.8} style={{ stroke: "#B45309" }} />
        </button>
      </span>
    </div>
  );
}
