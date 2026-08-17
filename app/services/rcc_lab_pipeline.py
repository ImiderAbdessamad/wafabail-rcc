"""Pipeline RCC — adaptateur API du moteur `ocr_lab_core_v10`.

Le moteur (copie vendorisée du script de laboratoire v10 « robust ») fait tout
le travail d'extraction : rendu, orientation, classification, lecture guidée par
la grille, consensus multi-modèles, résolution des 20 postes RCC et contrôles
arithmétiques. Ce module ne fait que deux choses :

1. l'exécuter dans un thread en relayant sa progression vers les évènements SSE ;
2. projeter ses quatre DataFrames sur le schéma `RccAnalysisResult` attendu par
   l'API, les écrans de validation et la conformité.

Aucune valeur n'est recalculée ici : les montants, statuts et contrôles sont
repris tels que le moteur les a produits.
"""
from __future__ import annotations

import asyncio
import logging
import queue
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd

from app import config
from app.schemas.direct_financial_extraction import (
    CompanyInfo,
    DocumentSummary,
    ExerciseInfo,
    ExtractionSummary,
    FinancialPageAudit,
)
from app.schemas.rcc import (
    RCC_ELEMENTS,
    AccountingControlView,
    FieldEvidence,
    RccAnalysisResult,
    RccField,
)
from app.services import ocr_lab_core_v10 as engine

logger = logging.getLogger(__name__)

ProgressEmitter = Callable[[str, dict[str, Any]], None]

# --- Correspondances moteur → API -------------------------------------------

# Codes d'évidence internes du moteur alimentant chaque poste RCC
# (miroir de `resolve_rcc` / `_propagate_validation_status`).
_EVIDENCE_CODES: dict[str, list[str]] = {
    "ACTIFS_IMMOBILISES": ["ACTIFS_IMMOBILISES"],
    "TOTAL_BILAN": ["TOTAL_ACTIF"],
    "CHIFFRE_AFFAIRES": ["CHIFFRE_AFFAIRES", "VENTES_BIENS_SERVICES", "VENTES_MARCHANDISES"],
    "CA_EXPORT": ["EXPORT_MARCHANDISES", "EXPORT_BIENS", "EXPORT_SERVICES"],
    "DETTES_BANCAIRES_MLT": ["DETTES_FINANCEMENT"],
    "DETTES_BANCAIRES_CT": ["DETTES_BANCAIRES_CT"],
    "PASSIF_CIRCULANT": ["PASSIF_CIRCULANT"],
    "DETTES_FOURNISSEURS": ["FOURNISSEURS"],
    "COMPTE_COURANT_ASSOCIES": ["COMPTE_COURANT_ASSOCIES"],
    "TRESORERIE_PASSIF": ["TRESORERIE_PASSIF"],
    "ACTIF_CIRCULANT": ["ACTIF_CIRCULANT"],
    "CREANCES_CLIENTS": ["CLIENTS"],
    "TRESORERIE_ACTIF": ["TRESORERIE_ACTIF"],
    "CAISSE": ["CAISSE"],
    "ACHATS_REVENDUS": ["ACHATS_REVENDUS", "ACHATS_REVENDUS_TOTAL"],
    "ACHATS_CONSOMMES": ["ACHATS_CONSOMMES", "ACHATS_CONSOMMES_TOTAL"],
    "AUTRES_CHARGES_EXTERNES": ["AUTRES_CHARGES_EXTERNES", "AUTRES_CHARGES_EXTERNES_TOTAL"],
    "CHARGES_INTERETS": ["CHARGES_INTERETS"],
    "RESULTAT_NET": ["RESULTAT_NET"],
    "TYPE_RESULTAT": [],
}

_EVIDENCE_TO_RCC: dict[str, str] = {
    code: rcc_code
    for rcc_code, codes in _EVIDENCE_CODES.items()
    for code in codes
}
_EVIDENCE_TO_RCC["TOTAL_PASSIF"] = "TOTAL_BILAN"
_EVIDENCE_TO_RCC["PASSIF_TOTAL_I"] = "PASSIF_CIRCULANT"

# Colonne portant la valeur de l'exercice courant, par type de page
# (identique à `engine.value_from_row`).
_VALUE_COLUMNS: dict[str, list[str]] = {
    "BILAN_ACTIF": ["NET_N"],
    "BILAN_PASSIF": ["EXERCICE_N"],
    "CPC": ["TOTAL_N", "OP_N"],
    "DETAIL_CPC": ["EXERCICE_N"],
}
_N1_COLUMN: dict[str, str] = {
    "BILAN_ACTIF": "NET_N1",
    "BILAN_PASSIF": "EXERCICE_N1",
    "CPC": "TOTAL_N1",
    "DETAIL_CPC": "EXERCICE_N1",
}
# Postes pour lesquels l'exercice N-1 est lu sur la même ligne de la liasse.
_N1_CODES = frozenset({"CHIFFRE_AFFAIRES", "RESULTAT_NET", "TOTAL_BILAN", "DETTES_BANCAIRES_MLT"})

# Statuts du moteur → statuts de l'API (confirmed|derived|ambiguous|conflicting|missing).
_STATUS_MAP: dict[str, str] = {
    "confirmed": "confirmed",
    "cross_validated": "confirmed",
    "low_confidence": "ambiguous",
    "needs_review": "ambiguous",
    "derived": "derived",
    "partial": "derived",
    "proxy": "derived",
    "blank_on_form": "missing",
    "missing": "missing",
    "conflicting": "conflicting",
    "conflicting_blank_vs_value": "conflicting",
}

# Statut moteur → mention affichée en badge sur la ligne du poste. Volontairement
# court : le détail chiffré part dans les avertissements d'extraction.
_NOTE_FR: dict[str, str] = {
    "cross_validated": "Recoupé sur 2 pages",
    "low_confidence": "Lecture à confirmer",
    "needs_review": "Contrôle non levé",
    "derived": "Calculé depuis les lignes imprimées",
    "partial": "Somme partielle",
    "blank_on_form": "Ligne vide sur la liasse",
    "conflicting": "Valeurs divergentes",
    "conflicting_blank_vs_value": "Cellule vide contre montant",
}

_PROXY_NOTES: dict[str, str] = {
    "DETTES_BANCAIRES_MLT": "Proxy : dettes de financement",
    "CHIFFRE_AFFAIRES": "Proxy : ligne de ventes",
}

_CONTROL_LABELS: dict[str, str] = {
    "ACTIF_ROW_BRUT_MINUS_AMORT_EQUALS_NET": "Bilan actif : Brut − Amortissements = Net",
    "CPC_OPS_SUM_TO_TOTAL_N": "CPC : opérations de l'exercice + exercices antérieurs = total",
    "BILAN_ACTIF_I_PLUS_II_PLUS_III_EQUALS_TOTAL": "Bilan actif : immobilisé + circulant + trésorerie = total actif",
    "BILAN_PASSIF_I_PLUS_II_PLUS_III_EQUALS_TOTAL": "Bilan passif : permanent + circulant + trésorerie = total passif",
    "CPC_VENTES_SUM_EQUALS_CHIFFRE_AFFAIRES": "CPC : ventes marchandises + ventes biens et services = chiffre d'affaires",
    "TOTAL_ACTIF_EQUALS_TOTAL_PASSIF": "Total Actif = Total Passif",
    "RESULTAT_NET_RESOLVED_EQUALS_PASSIF": "Résultat net du CPC = résultat net inscrit au passif",
}
# L'écran de validation cherche ce code pour la bannière d'équilibre du bilan.
_CONTROL_CODES: dict[str, str] = {
    "TOTAL_ACTIF_EQUALS_TOTAL_PASSIF": "bilan_equilibre",
    "ACTIF_ROW_BRUT_MINUS_AMORT_EQUALS_NET": "actif_brut_amort_net",
    "CPC_OPS_SUM_TO_TOTAL_N": "cpc_operations_total",
    "BILAN_ACTIF_I_PLUS_II_PLUS_III_EQUALS_TOTAL": "bilan_actif_totaux",
    "BILAN_PASSIF_I_PLUS_II_PLUS_III_EQUALS_TOTAL": "bilan_passif_totaux",
    "CPC_VENTES_SUM_EQUALS_CHIFFRE_AFFAIRES": "cpc_ventes_chiffre_affaires",
    "RESULTAT_NET_RESOLVED_EQUALS_PASSIF": "resultat_net",
}
# Contrôles produits ligne par ligne : agrégés en une seule vue lisible.
_ROW_LEVEL_CHECKS = frozenset(
    {"ACTIF_ROW_BRUT_MINUS_AMORT_EQUALS_NET", "CPC_OPS_SUM_TO_TOTAL_N"}
)

_RCC_LABELS: dict[str, str] = {code: label for _, code, label, _ in RCC_ELEMENTS}


# --- Utilitaires -------------------------------------------------------------


def _clean(value: Any) -> Any:
    """Neutralise les NaN/NaT que pandas produit sur les colonnes creuses."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _to_float(value: Any) -> float | None:
    value = _clean(value)
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    parsed = engine.parse_amount(str(value))
    return float(parsed) if parsed is not None else None


def _first_page(value: Any) -> int | None:
    """Le moteur peut renvoyer « 3 » ou « 3,4 » pour un poste multi-pages."""
    value = _clean(value)
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    for chunk in str(value).replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk.isdigit():
            return int(chunk)
    return None


def _fmt(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f}".replace(",", " ")


def _records(frame: Any) -> list[dict[str, Any]]:
    if frame is None or getattr(frame, "empty", True):
        return []
    return [
        {key: _clean(val) for key, val in record.items()}
        for record in frame.to_dict("records")
    ]


def _row_value(row: engine.EvidenceRow) -> tuple[str | None, str | None]:
    """Cellule (valeur brute, nom de colonne) portant l'exercice courant."""
    for column in _VALUE_COLUMNS.get(row.page_type, []):
        raw = row.cells.get(column)
        if raw and engine.parse_amount(raw) is not None:
            return raw, column
    return row.cells.get("TEXT"), None


# --- Client moteur -----------------------------------------------------------


def build_client() -> engine.OllamaClient:
    """Client Ollama du moteur v10, paramétré par la configuration de l'app."""
    engine.REQUEST_TIMEOUT = config.RCC_REQUEST_TIMEOUT_SECONDS
    engine.KEEP_ALIVE = config.RCC_KEEP_ALIVE
    engine.RENDER_DPI = config.RCC_RENDER_DPI
    engine.EXTRACT_MAX_SIDE = config.RCC_EXTRACT_MAX_SIDE
    return engine.OllamaClient(
        base_url=config.RCC_OLLAMA_URL,
        model=config.RCC_VISION_MODEL,
        mapper_model=config.RCC_MAPPER_MODEL,
        adjudicator_model=config.RCC_ADJUDICATOR_MODEL,
        ocr_model=config.RCC_OCR_MODEL,
        verify_model=config.RCC_VERIFY_MODEL,
    )


# --- Projection moteur → RccAnalysisResult -----------------------------------


def _identification(evidence: list[engine.EvidenceRow]) -> tuple[CompanyInfo, ExerciseInfo]:
    values: dict[str, str] = {}
    for row in evidence:
        if row.page_type != "IDENTIFICATION":
            continue
        raw = (row.cells.get("TEXT") or "").strip()
        if raw and row.field_code not in values:
            values[row.field_code] = raw

    debut = values.get("EXERCICE_DEBUT")
    fin = values.get("EXERCICE_FIN")
    label = None
    if debut and fin:
        label = f"Du {debut} au {fin}"
    elif fin:
        label = f"Clôture au {fin}"

    return (
        CompanyInfo(
            raison_sociale=values.get("RAISON_SOCIALE"),
            identifiant_fiscal=values.get("IDENTIFIANT_FISCAL"),
            ice=values.get("ICE"),
            adresse=values.get("ADRESSE"),
            ville=values.get("VILLE"),
        ),
        ExerciseInfo(debut=debut, fin=fin, label=label),
    )


def _page_audit(
    audit_rows: list[dict[str, Any]],
    evidence: list[engine.EvidenceRow],
) -> list[FinancialPageAudit]:
    rows_by_page: dict[int, int] = {}
    for row in evidence:
        rows_by_page[row.page] = rows_by_page.get(row.page, 0) + 1

    pages: list[FinancialPageAudit] = []
    for record in audit_rows:
        page_no = int(record.get("page") or 0)
        page_type = str(record.get("page_type") or "AUTRE")
        if page_type not in engine.ALL_PAGE_TYPES:
            page_type = "AUTRE"
        error = record.get("extraction_error")
        candidates = rows_by_page.get(page_no, 0)

        if error:
            status = "failed"
        elif page_type not in engine.RELEVANT_PAGE_TYPES:
            status = "skipped"
        elif candidates:
            status = "processed"
        else:
            status = "empty"

        rotation = record.get("rotation")
        orientation = int(rotation) % 360 if rotation is not None else 0
        if orientation not in {0, 90, 180, 270}:
            orientation = 0

        strategy = str(record.get("mode") or "scan_glm")
        mode = record.get("scan_extraction_mode")
        if mode:
            strategy = f"{strategy}:{mode}"

        pages.append(
            FinancialPageAudit(
                page_number=page_no,
                detected_type=page_type,  # type: ignore[arg-type]
                orientation=orientation,  # type: ignore[arg-type]
                extraction_status=status,  # type: ignore[arg-type]
                extraction_strategy=strategy,
                candidates_count=candidates,
                error=str(error) if error else None,
            )
        )
    return pages


def _field_evidence(
    code: str,
    evidence: list[engine.EvidenceRow],
    *,
    limit: int = 6,
) -> list[FieldEvidence]:
    wanted = set(_EVIDENCE_CODES.get(code, []))
    rows = [row for row in evidence if row.field_code in wanted]
    rows.sort(key=lambda r: (-(r.confidence or 0.0), r.page))

    items: list[FieldEvidence] = []
    seen: set[tuple[Any, ...]] = set()
    for row in rows[: limit * 2]:
        raw_value, column = _row_value(row)
        key = (row.page, row.raw_label, raw_value, column)
        if key in seen:
            continue
        seen.add(key)
        items.append(
            FieldEvidence(
                page_number=row.page,
                raw_label=row.raw_label,
                raw_value=raw_value,
                column_name=column,
                page_type=row.page_type,
                confidence=row.confidence,
                source_excerpt=row.mapping_note or row.source,
            )
        )
        if len(items) >= limit:
            break
    return items


def _value_n1(code: str, value: float | None, evidence: list[engine.EvidenceRow]) -> float | None:
    if code not in _N1_CODES:
        return None
    wanted = set(_EVIDENCE_CODES.get(code, []))
    candidates = [row for row in evidence if row.field_code in wanted]
    if value is not None:
        matched = [
            row
            for row in candidates
            if (v := engine.value_from_row(row)) is not None and abs(float(v) - value) < 0.01
        ]
        candidates = matched or candidates
    for row in sorted(candidates, key=lambda r: (-(r.confidence or 0.0), r.page)):
        column = _N1_COLUMN.get(row.page_type)
        if not column:
            continue
        parsed = engine.parse_amount(row.cells.get(column))
        if parsed is not None:
            return float(parsed)
    return None


def _note_fr(code: str, engine_status: str) -> str | None:
    if engine_status == "proxy":
        return _PROXY_NOTES.get(code, "Valeur approchée")
    return _NOTE_FR.get(engine_status)


def _build_fields(
    rcc_rows: list[dict[str, Any]],
    evidence: list[engine.EvidenceRow],
) -> list[RccField]:
    by_code = {str(row.get("code")): row for row in rcc_rows}
    fields: list[RccField] = []

    for number, code, label, source in RCC_ELEMENTS:
        record = by_code.get(code, {})
        engine_status = str(record.get("status") or "missing")
        raw_value = _clean(record.get("value"))
        # Un contrôle non levé ne doit pas transformer une cellule vide en
        # « à vérifier » : le poste reste manquant.
        if engine_status == "needs_review" and (code == "TYPE_RESULTAT" or _to_float(raw_value) is None):
            engine_status = "missing"
        status = _STATUS_MAP.get(engine_status, "missing")
        confidence = float(record.get("confidence") or 0.0)

        if code == "TYPE_RESULTAT":
            # Poste non numérique : « Bénéficiaire », « Déficitaire » ou « Nul »
            # est porté par la note, que l'interface rend sous forme de tag.
            fields.append(
                RccField(
                    number=number, code=code, label=label, source=source,
                    value=None, status=status, confidence=confidence,
                    note=str(raw_value) if raw_value else None,
                )
            )
            continue

        value = _to_float(raw_value)
        if engine_status == "needs_review":
            # Un contrôle non levé ne doit pas passer pour une lecture sûre.
            confidence = min(confidence, 0.5)

        fields.append(
            RccField(
                number=number,
                code=code,
                label=label,
                source=source,
                value=value,
                status=status,
                note=_note_fr(code, engine_status),
                confidence=max(0.0, min(1.0, confidence)),
                value_n1=_value_n1(code, value, evidence),
                evidence=_field_evidence(code, evidence),
            )
        )
    return fields


def _aggregate_row_control(check: str, rows: list[dict[str, Any]]) -> AccountingControlView:
    failed = [row for row in rows if str(row.get("status")) == "failed"]
    affected: list[str] = []
    for row in failed:
        rcc_code = _EVIDENCE_TO_RCC.get(str(row.get("field") or ""))
        if rcc_code and rcc_code not in affected:
            affected.append(rcc_code)

    worst = max(
        (abs(_to_float(row.get("difference")) or 0.0) for row in failed),
        default=0.0,
    )
    if failed:
        details = ", ".join(
            f"page {_first_page(row.get('page')) or '?'} · {row.get('field')}"
            for row in failed[:4]
        )
        message = (
            f"{len(failed)} ligne(s) en écart sur {len(rows)} vérifiée(s) — {details}"
        )
    else:
        message = f"{len(rows)} ligne(s) vérifiée(s), toutes cohérentes."

    return AccountingControlView(
        code=_CONTROL_CODES.get(check, check.lower()),
        status="failed" if failed else "passed",
        label=_CONTROL_LABELS.get(check, check),
        difference=worst if failed else 0.0,
        tolerance=float(engine.ROBUST_ARITH_TOL),
        affected_fields=affected,
        message=message,
    )


def _build_controls(control_rows: list[dict[str, Any]]) -> list[AccountingControlView]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in control_rows:
        grouped.setdefault(str(row.get("check")), []).append(row)

    controls: list[AccountingControlView] = []
    for check, rows in grouped.items():
        if check in _ROW_LEVEL_CHECKS:
            controls.append(_aggregate_row_control(check, rows))
            continue

        row = rows[0]
        expected = _to_float(row.get("expected"))
        observed = _to_float(row.get("observed"))
        difference = _to_float(row.get("difference"))
        passed = str(row.get("status")) == "passed"
        rcc_code = _EVIDENCE_TO_RCC.get(str(row.get("field") or ""))
        message = (
            "Contrôle vérifié."
            if passed
            else f"Écart de {_fmt(abs(difference) if difference is not None else None)} MAD "
            f"(attendu {_fmt(expected)}, lu {_fmt(observed)})."
        )
        controls.append(
            AccountingControlView(
                code=_CONTROL_CODES.get(check, check.lower()),
                status="passed" if passed else "failed",
                label=_CONTROL_LABELS.get(check, check),
                expected=expected,
                observed=observed,
                difference=difference,
                tolerance=float(engine.ROBUST_ARITH_TOL),
                affected_fields=[rcc_code] if rcc_code else [],
                message=message,
            )
        )

    controls.sort(key=lambda c: (c.status != "failed", c.label))
    return controls


def _build_warnings(
    pages: Iterable[FinancialPageAudit],
    fields: Iterable[RccField],
    controls: Iterable[AccountingControlView],
) -> list[str]:
    warnings: list[str] = []

    failed_pages = [page.page_number for page in pages if page.extraction_status == "failed"]
    if failed_pages:
        warnings.append(
            "Pages non exploitées : "
            + ", ".join(str(p) for p in failed_pages)
            + " — les postes qu'elles portent peuvent manquer."
        )

    conflicting = [f.label for f in fields if f.status == "conflicting"]
    if conflicting:
        warnings.append("Valeurs divergentes à arbitrer : " + ", ".join(conflicting) + ".")

    to_review = [
        f.label
        for f in fields
        if f.value is not None and f.status == "ambiguous"
    ]
    if to_review:
        warnings.append("Lectures à confirmer : " + ", ".join(to_review) + ".")

    for control in controls:
        if control.status == "failed":
            warnings.append(f"Contrôle comptable en écart — {control.label} : {control.message}")

    return warnings


def build_result(
    *,
    filename: str,
    audit_frame: Any,
    rcc_frame: Any,
    controls_frame: Any,
    evidence: list[engine.EvidenceRow],
    pages_total: int,
) -> RccAnalysisResult:
    """Projette la sortie du moteur v10 sur le contrat de l'API."""
    audit_rows = _records(audit_frame)
    pages = _page_audit(audit_rows, evidence)
    fields = _build_fields(_records(rcc_frame), evidence)
    controls = _build_controls(_records(controls_frame))
    company, exercise = _identification(evidence)
    warnings = _build_warnings(pages, fields, controls)

    found = sum(
        1
        for field in fields
        if (field.note if field.code == "TYPE_RESULTAT" else field.value) is not None
        and field.status != "missing"
    )

    document = DocumentSummary(
        filename=filename,
        pages_total=pages_total or len(pages),
        pages_processed=sum(1 for p in pages if p.extraction_status == "processed"),
        pages_skipped=sum(1 for p in pages if p.extraction_status in {"skipped", "empty"}),
        pages_failed=sum(1 for p in pages if p.extraction_status == "failed"),
        company=company,
        exercise=exercise,
    )
    extraction = ExtractionSummary(
        model=f"{engine.PIPELINE_VERSION} · {config.RCC_VISION_MODEL}",
        page_audit=pages,
        warnings=warnings,
    )

    return RccAnalysisResult(
        document=document,
        extraction=extraction,
        fields=fields,
        completeness_pct=round(100.0 * found / len(RCC_ELEMENTS), 1),
        warnings=warnings,
        controls=controls,
    )


# --- Exécution ---------------------------------------------------------------


def _run_engine(
    pdf_path: Path,
    *,
    max_pages: int | None,
    progress: Callable[[str, dict[str, Any]], None],
) -> tuple[Any, Any, Any, list[engine.EvidenceRow]]:
    """Exécution synchrone du moteur — appelée dans un thread dédié."""
    client = build_client()
    audit, _rows, rcc, controls, evidence = engine.analyze_pdf(
        pdf_path,
        client=client,
        max_pages=max_pages,
        use_glm_verification=config.RCC_USE_GLM_VERIFICATION,
        use_reasoning_mapper=config.RCC_USE_REASONING_MAPPER,
        use_adjudicator=config.RCC_USE_ADJUDICATOR,
        progress=progress,
    )
    return audit, rcc, controls, evidence


async def analyze_rcc_document(
    pdf_bytes: bytes,
    filename: str,
    *,
    max_pages: int | None = None,
    emit: ProgressEmitter | None = None,
) -> RccAnalysisResult:
    """Analyse une liasse fiscale et renvoie les 20 postes RCC.

    Le moteur est bloquant (appels HTTP séquentiels vers Ollama) : il tourne
    dans un thread, sa progression transitant par une file lue depuis la boucle
    d'évènements pour que les évènements SSE restent émis côté asyncio.
    """
    def publish(event: str, data: dict[str, Any]) -> None:
        if emit is not None:
            emit(event, data)

    publish("pdf_validated", {"filename": filename})

    events: "queue.Queue[tuple[str, dict[str, Any]]]" = queue.Queue()
    pages_total = 0

    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    try:
        tmp.write(pdf_bytes)
        tmp.close()
        pdf_path = Path(tmp.name)

        task = asyncio.create_task(
            asyncio.to_thread(
                _run_engine,
                pdf_path,
                max_pages=max_pages,
                progress=lambda event, data: events.put((event, data)),
            )
        )

        def drain() -> int:
            seen = 0
            while True:
                try:
                    event, data = events.get_nowait()
                except queue.Empty:
                    return seen
                if event == "pages_rendered":
                    seen = int(data.get("pages_total") or data.get("count") or 0)
                publish(event, data)

        while not task.done():
            pages_total = drain() or pages_total
            await asyncio.sleep(0.2)
        pages_total = drain() or pages_total

        audit, rcc, controls, evidence = await task
    finally:
        try:
            Path(tmp.name).unlink(missing_ok=True)
        except OSError:
            logger.warning("PDF temporaire non supprimé : %s", tmp.name)

    result = build_result(
        filename=filename,
        audit_frame=audit,
        rcc_frame=rcc,
        controls_frame=controls,
        evidence=evidence,
        pages_total=pages_total,
    )
    publish(
        "result_ready",
        {
            "pages_processed": result.document.pages_processed,
            "completeness_pct": result.completeness_pct,
        },
    )
    return result


async def run_financial_job(job_id: str, *, store: Any) -> None:
    """Exécute un job stocké (séquentiel : un seul passage moteur à la fois)."""
    job = store.get(job_id)
    if job is None or job.pdf_bytes is None:
        return

    store.update(
        job_id,
        status="processing",
        current_step="validating",
        message="Validation du PDF…",
        progress_pct=2,
    )
    store.emit(job_id, "job_started", {"filename": job.filename})

    started = time.time()

    def emit(event: str, data: dict[str, Any]) -> None:
        step_map = {
            "pdf_validated": ("validating", 5, "PDF validé"),
            "pages_rendered": ("rendering", 10, "Pages analysées"),
            "page_classified": ("classifying", None, None),
            "page_extracted": ("extracting_page", None, None),
            "page_skipped": ("extracting_page", None, None),
            "page_failed": ("extracting_page", None, None),
            "resolving_fields": ("resolving", 88, "Résolution des postes RCC"),
            "running_controls": ("controls", 94, "Contrôles comptables"),
            "result_ready": ("completed", 100, "Extraction RCC terminée"),
        }
        step, pct, message = step_map.get(event, (None, None, None))
        updates: dict[str, Any] = {}
        if step:
            updates["current_step"] = step
        if pct is not None:
            updates["progress_pct"] = pct
        if message:
            updates["message"] = message
        if "page" in data:
            updates["current_page"] = data["page"]
            pages_total = job.pages_total or data.get("pages_total")
            if pages_total:
                updates["progress_pct"] = min(
                    85,
                    10 + int(75 * int(data["page"]) / max(int(pages_total), 1)),
                )
                updates["message"] = (
                    f"Page {data['page']}/{pages_total} — {data.get('page_type', '')}"
                )
        if event == "pages_rendered":
            total = data.get("pages_total") or data.get("count")
            updates["pages_total"] = total
            job.pages_total = total
        if event == "page_extracted":
            updates["pages_financial"] = (job.pages_financial or 0) + 1
            job.pages_financial = updates["pages_financial"]
        if event == "page_skipped":
            updates["pages_skipped"] = (job.pages_skipped or 0) + 1
            job.pages_skipped = updates["pages_skipped"]
        if event == "page_failed":
            updates["pages_failed"] = (job.pages_failed or 0) + 1
            job.pages_failed = updates["pages_failed"]
        if updates:
            store.update(job_id, **updates)
        store.emit(job_id, event, data)

    try:
        result = await analyze_rcc_document(
            job.pdf_bytes,
            job.filename,
            max_pages=job.max_pages,
            emit=emit,
        )
        logger.info(
            "Job RCC %s terminé en %.0f s — %s%% de complétude",
            job_id,
            time.time() - started,
            result.completeness_pct,
        )
        store.update(
            job_id,
            status="completed",
            progress_pct=100,
            current_step="completed",
            message="Analyse terminée",
            result=result,
            pdf_bytes=None,  # libère la mémoire
        )
        store.emit(job_id, "result_ready", {"status": "completed"})
    except Exception as exc:  # noqa: BLE001
        logger.exception("Job RCC %s échoué", job_id)
        store.update(
            job_id,
            status="failed",
            current_step="failed",
            message="Échec de l'analyse",
            error=str(exc),
            pdf_bytes=None,
        )
        store.emit(job_id, "job_failed", {"error": str(exc)})
