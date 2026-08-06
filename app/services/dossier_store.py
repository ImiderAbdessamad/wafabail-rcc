"""CRUD dossiers RCC, corrections analystes et piste d'audit (SQLite)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from app.config import PDF_STORAGE_DIR
from app.schemas.dossier import (
    STATUS_LABELS,
    AuditEntry,
    DossierDetail,
    DossierSummary,
    FieldOverride,
)
from app.schemas.rcc import RCC_ELEMENTS, RccAnalysisResult

logger = logging.getLogger(__name__)

FIELD_LABELS: dict[str, str] = {code: label for _, code, label, _ in RCC_ELEMENTS}

ACTION_LABELS: dict[str, str] = {
    "ingestion": "Ingestion",
    "correction": "Correction",
    "validated": "Validation",
    "rejected": "Rejet",
    "escalated": "Arbitrage",
    "pending": "Remise en file",
    "document_attached": "Pièce rattachée",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Stockage PDF -----------------------------------------------------------


def pdf_path_for_job(job_id: str) -> Path:
    return Path(PDF_STORAGE_DIR) / f"{job_id}.pdf"


def store_pdf(job_id: str, content: bytes) -> Path:
    """Conserve le PDF pour la visionneuse — le job store le libère après analyse."""
    path = pdf_path_for_job(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


# --- Identifiants métier ----------------------------------------------------


def _next_dossier_id(cur: Any) -> str:
    """Numérotation lisible RCC-AAAA-NNNN, séquentielle par année."""
    year = datetime.now(timezone.utc).year
    prefix = f"RCC-{year}-"
    cur.execute(
        "SELECT id FROM dossiers WHERE id LIKE ? ORDER BY id DESC LIMIT 1",
        (prefix + "%",),
    )
    row = cur.fetchone()
    seq = 1
    if row is not None:
        try:
            seq = int(str(row["id"]).rsplit("-", 1)[-1]) + 1
        except ValueError:
            seq = 1
    return f"{prefix}{seq:04d}"


# --- Sérialisation ----------------------------------------------------------


def _row_to_summary(row: Any, override_count: int = 0) -> DossierSummary:
    return DossierSummary(
        id=row["id"],
        job_id=row["job_id"],
        client_name=row["client_name"] or "",
        ice=row["ice"],
        credit_amount=row["credit_amount"],
        exercice_date=row["exercice_date"],
        status=row["status"],
        status_label=STATUS_LABELS.get(row["status"], row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        sector=row["sector"],
        filename=row["filename"],
        has_document=bool(row["pdf_path"]) and Path(row["pdf_path"]).exists(),
        completeness_pct=row["completeness_pct"] or 0.0,
        override_count=override_count,
        motif=row["motif"],
        comment=row["comment"],
        decided_by=row["decided_by"],
        decided_at=row["decided_at"],
    )


def _parse_result(raw: str | None) -> RccAnalysisResult | None:
    if not raw:
        return None
    try:
        return RccAnalysisResult.model_validate_json(raw)
    except Exception:  # noqa: BLE001
        logger.warning("Instantané RCC illisible, ignoré.")
        return None


# --- Écriture ---------------------------------------------------------------


def create_dossier(
    *,
    job_id: str | None,
    result: RccAnalysisResult | None,
    client_name: str | None = None,
    ice: str | None = None,
    credit_amount: float | None = None,
    exercice_date: str | None = None,
    sector: str | None = None,
    actor: str = "Moteur OCR",
) -> DossierDetail:
    """Crée un dossier à partir d'un job d'extraction terminé.

    Les métadonnées non fournies sont reprises de l'identification du document
    (raison sociale, ICE, exercice) extraite par le pipeline.
    """
    from app.db import cursor  # import tardif : DB initialisée au démarrage

    doc = result.document if result else None
    company = doc.company if doc else None
    exercise = doc.exercise if doc else None

    name = client_name or (company.raison_sociale if company else None) or "Client à identifier"
    resolved_ice = ice or (company.ice if company else None)
    resolved_exercice = (
        exercice_date
        or (exercise.fin if exercise else None)
        or (exercise.label if exercise else None)
    )
    resolved_sector = sector or (company.ville if company else None)
    filename = doc.filename if doc else None

    pdf_path = None
    if job_id:
        candidate = pdf_path_for_job(job_id)
        if candidate.exists():
            pdf_path = str(candidate)

    now = _now_iso()
    with cursor(commit=True) as cur:
        dossier_id = _next_dossier_id(cur)
        cur.execute(
            """
            INSERT INTO dossiers (
                id, job_id, client_name, ice, credit_amount, exercice_date,
                status, created_at, updated_at, sector, filename, pdf_path,
                result_json, completeness_pct
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dossier_id,
                job_id,
                name,
                resolved_ice,
                credit_amount,
                resolved_exercice,
                now,
                now,
                resolved_sector,
                filename,
                pdf_path,
                result.model_dump_json() if result else None,
                result.completeness_pct if result else 0.0,
            ),
        )
        detail = (
            f"{len([f for f in result.fields if f.value is not None])}/"
            f"{len(RCC_ELEMENTS)} champs lus"
            if result
            else "dossier créé sans extraction"
        )
        cur.execute(
            "INSERT INTO dossier_events (dossier_id, action, detail, actor, created_at)"
            " VALUES (?, 'ingestion', ?, ?, ?)",
            (dossier_id, detail, actor, now),
        )

    created = get_dossier(dossier_id)
    assert created is not None
    return created


def attach_result(
    dossier_id: str,
    *,
    job_id: str,
    result: RccAnalysisResult,
    actor: str,
) -> DossierDetail | None:
    """Rattache une extraction à un dossier existant (ré-import / liasse manquante)."""
    from app.db import cursor

    pdf_path = pdf_path_for_job(job_id)
    now = _now_iso()
    with cursor(commit=True) as cur:
        cur.execute("SELECT id FROM dossiers WHERE id = ?", (dossier_id,))
        if cur.fetchone() is None:
            return None
        cur.execute(
            """
            UPDATE dossiers
               SET job_id = ?, result_json = ?, completeness_pct = ?,
                   filename = ?, pdf_path = ?, updated_at = ?
             WHERE id = ?
            """,
            (
                job_id,
                result.model_dump_json(),
                result.completeness_pct,
                result.document.filename,
                str(pdf_path) if pdf_path.exists() else None,
                now,
                dossier_id,
            ),
        )
        cur.execute(
            "INSERT INTO dossier_events (dossier_id, action, detail, actor, created_at)"
            " VALUES (?, 'document_attached', ?, ?, ?)",
            (dossier_id, result.document.filename, actor, now),
        )
    return get_dossier(dossier_id)


def update_dossier(
    dossier_id: str,
    *,
    changes: dict[str, Any],
    actor: str,
) -> DossierDetail | None:
    """Met à jour statut / métadonnées et trace l'évènement si le statut change."""
    from app.db import cursor

    allowed = {
        "status",
        "motif",
        "comment",
        "client_name",
        "sector",
        "credit_amount",
        "exercice_date",
    }
    updates = {k: v for k, v in changes.items() if k in allowed and v is not None}
    if not updates:
        return get_dossier(dossier_id)

    now = _now_iso()
    with cursor(commit=True) as cur:
        cur.execute("SELECT status FROM dossiers WHERE id = ?", (dossier_id,))
        row = cur.fetchone()
        if row is None:
            return None
        previous_status = row["status"]

        new_status = updates.get("status")
        if new_status and new_status != previous_status:
            updates["decided_by"] = actor
            updates["decided_at"] = now

        assignments = ", ".join(f"{key} = ?" for key in updates)
        cur.execute(
            f"UPDATE dossiers SET {assignments}, updated_at = ? WHERE id = ?",
            (*updates.values(), now, dossier_id),
        )

        if new_status and new_status != previous_status:
            detail_parts = [p for p in (updates.get("motif"), updates.get("comment")) if p]
            cur.execute(
                "INSERT INTO dossier_events (dossier_id, action, detail, actor, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    dossier_id,
                    new_status,
                    " · ".join(detail_parts) or None,
                    actor,
                    now,
                ),
            )
    return get_dossier(dossier_id)


def save_overrides(
    dossier_id: str,
    *,
    entries: Iterable[tuple[str, float | None, bool]],
    actor: str,
) -> list[FieldOverride] | None:
    """Enregistre corrections et confirmations analystes.

    `original_value` reste toujours la valeur OCR : la surcharge se superpose,
    elle n'écrase jamais l'extraction.

    Trois cas par entrée `(code, valeur, verified)` :
      * `verified=True`  → confirmation de la valeur OCR telle quelle ;
      * valeur différente de l'OCR → correction ;
      * valeur nulle ou identique à l'OCR → retour à la source (surcharge levée).
    """
    from app.db import cursor

    now = _now_iso()
    with cursor(commit=True) as cur:
        cur.execute("SELECT result_json FROM dossiers WHERE id = ?", (dossier_id,))
        row = cur.fetchone()
        if row is None:
            return None
        result = _parse_result(row["result_json"])
        ocr_values: dict[str, float | None] = {}
        if result:
            ocr_values = {f.code: f.value for f in result.fields}

        for field_code, corrected, verified in entries:
            if field_code not in FIELD_LABELS:
                continue
            original = ocr_values.get(field_code)

            # Une seule surcharge courante par poste : on remplace.
            cur.execute(
                "DELETE FROM field_overrides WHERE dossier_id = ? AND field_code = ?",
                (dossier_id, field_code),
            )

            if verified:
                value = corrected if corrected is not None else original
            elif corrected is None or (
                original is not None and abs(corrected - original) < 1e-9
            ):
                continue  # retour à la valeur OCR : plus de surcharge
            else:
                value = corrected

            cur.execute(
                """
                INSERT INTO field_overrides (
                    dossier_id, field_code, original_value, corrected_value,
                    edited_by, edited_at, verified
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (dossier_id, field_code, original, value, actor, now, 1 if verified else 0),
            )
        cur.execute(
            "UPDATE dossiers SET updated_at = ? WHERE id = ?", (now, dossier_id)
        )
    return list_overrides(dossier_id)


# --- Lecture ----------------------------------------------------------------


def list_overrides(dossier_id: str) -> list[FieldOverride]:
    from app.db import cursor

    with cursor() as cur:
        cur.execute(
            "SELECT field_code, original_value, corrected_value, edited_by, edited_at,"
            " verified FROM field_overrides WHERE dossier_id = ? ORDER BY field_code",
            (dossier_id,),
        )
        rows = cur.fetchall()
    return [
        FieldOverride(
            field_code=r["field_code"],
            original_value=r["original_value"],
            corrected_value=r["corrected_value"],
            edited_by=r["edited_by"],
            edited_at=r["edited_at"],
            verified=bool(r["verified"]),
        )
        for r in rows
    ]


def get_dossier(dossier_id: str) -> DossierDetail | None:
    from app.db import cursor

    with cursor() as cur:
        cur.execute("SELECT * FROM dossiers WHERE id = ?", (dossier_id,))
        row = cur.fetchone()
        if row is None:
            return None
        cur.execute(
            "SELECT COUNT(*) AS n FROM field_overrides WHERE dossier_id = ?",
            (dossier_id,),
        )
        count = cur.fetchone()["n"]

    summary = _row_to_summary(row, override_count=count)
    return DossierDetail(
        **summary.model_dump(),
        result=_parse_result(row["result_json"]),
        overrides=list_overrides(dossier_id),
    )


def get_dossier_pdf_path(dossier_id: str) -> Path | None:
    from app.db import cursor

    with cursor() as cur:
        cur.execute("SELECT pdf_path FROM dossiers WHERE id = ?", (dossier_id,))
        row = cur.fetchone()
    if row is None or not row["pdf_path"]:
        return None
    path = Path(row["pdf_path"])
    return path if path.exists() else None


def find_by_job(job_id: str) -> DossierDetail | None:
    from app.db import cursor

    with cursor() as cur:
        cur.execute("SELECT id FROM dossiers WHERE job_id = ?", (job_id,))
        row = cur.fetchone()
    return get_dossier(row["id"]) if row else None


def list_dossiers(
    *,
    status: str | None = None,
    search: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> tuple[list[DossierSummary], int, dict[str, int]]:
    from app.db import cursor

    where: list[str] = []
    params: list[Any] = []
    if status and status != "all":
        where.append("d.status = ?")
        params.append(status)
    if search:
        where.append("(LOWER(d.id) LIKE ? OR LOWER(d.client_name) LIKE ? OR LOWER(COALESCE(d.ice,'')) LIKE ?)")
        needle = f"%{search.strip().lower()}%"
        params.extend([needle, needle, needle])
    clause = f" WHERE {' AND '.join(where)}" if where else ""

    with cursor() as cur:
        cur.execute(
            f"""
            SELECT d.*, (
                SELECT COUNT(*) FROM field_overrides o WHERE o.dossier_id = d.id
            ) AS override_count
            FROM dossiers d{clause}
            ORDER BY d.created_at DESC, d.id DESC
            LIMIT ? OFFSET ?
            """,
            (*params, limit, offset),
        )
        rows = cur.fetchall()
        cur.execute(f"SELECT COUNT(*) AS n FROM dossiers d{clause}", tuple(params))
        total = cur.fetchone()["n"]
        cur.execute("SELECT status, COUNT(*) AS n FROM dossiers GROUP BY status")
        counts = {r["status"]: r["n"] for r in cur.fetchall()}

    counts.setdefault("pending", 0)
    counts.setdefault("validated", 0)
    counts.setdefault("rejected", 0)
    counts.setdefault("escalated", 0)
    counts["all"] = sum(
        counts[k] for k in ("pending", "validated", "rejected", "escalated")
    )
    items = [_row_to_summary(r, override_count=r["override_count"]) for r in rows]
    return items, total, counts


def _fmt_amount(value: float | None) -> str | None:
    if value is None:
        return None
    return f"{value:,.0f}".replace(",", " ")


def list_audit(
    *,
    dossier_id: str | None = None,
    limit: int = 500,
    offset: int = 0,
) -> tuple[list[AuditEntry], int]:
    """Corrections + évènements, joints au contexte dossier, antéchronologiques."""
    from app.db import cursor

    where_o = " WHERE o.dossier_id = ?" if dossier_id else ""
    where_e = " WHERE e.dossier_id = ?" if dossier_id else ""
    params_o = (dossier_id,) if dossier_id else ()

    with cursor() as cur:
        cur.execute(
            f"""
            SELECT o.field_code, o.original_value, o.corrected_value, o.verified,
                   o.edited_by, o.edited_at, d.id AS dossier_id, d.client_name
              FROM field_overrides o
              JOIN dossiers d ON d.id = o.dossier_id{where_o}
            """,
            params_o,
        )
        corrections = cur.fetchall()
        cur.execute(
            f"""
            SELECT e.action, e.detail, e.actor, e.created_at,
                   d.id AS dossier_id, d.client_name
              FROM dossier_events e
              JOIN dossiers d ON d.id = e.dossier_id{where_e}
            """,
            params_o,
        )
        events = cur.fetchall()

    entries: list[AuditEntry] = []
    for r in corrections:
        is_verified = bool(r["verified"])
        entries.append(
            AuditEntry(
                kind="correction",
                timestamp=r["edited_at"],
                actor=r["edited_by"],
                dossier_id=r["dossier_id"],
                client_name=r["client_name"] or "",
                field_code=r["field_code"],
                field_label=FIELD_LABELS.get(r["field_code"], r["field_code"]),
                before=_fmt_amount(r["original_value"]) or "—",
                after=(
                    "valeur OCR confirmée"
                    if is_verified
                    else _fmt_amount(r["corrected_value"]) or "—"
                ),
                action="Vérification" if is_verified else "Correction",
            )
        )
    for r in events:
        entries.append(
            AuditEntry(
                kind="event",
                timestamp=r["created_at"],
                actor=r["actor"],
                dossier_id=r["dossier_id"],
                client_name=r["client_name"] or "",
                field_label="Dossier complet",
                before="—",
                after=r["detail"] or STATUS_LABELS.get(r["action"], r["action"]),
                action=ACTION_LABELS.get(r["action"], r["action"]),
            )
        )

    entries.sort(key=lambda e: e.timestamp, reverse=True)
    total = len(entries)
    return entries[offset : offset + limit], total


def effective_values(detail: DossierDetail) -> dict[str, float | None]:
    """Valeurs OCR avec les corrections analystes superposées."""
    values: dict[str, float | None] = {}
    if detail.result:
        values = {f.code: f.value for f in detail.result.fields}
    for override in detail.overrides:
        values[override.field_code] = override.corrected_value
    return values
