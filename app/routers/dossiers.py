"""API dossiers RCC — file de validation, corrections, piste d'audit."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.schemas.dossier import (
    AuditResponse,
    DossierCreateRequest,
    DossierDetail,
    DossierListResponse,
    DossierPatchRequest,
    FieldOverride,
    FieldOverrideBatchRequest,
    SessionUser,
)
from app.services import dossier_store
from app.services.auth import require_analyst
from app.services.financial_job_store import job_store
from app.services.rcc_compliance import ComplianceReport, build_compliance

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rcc/dossiers", tags=["Dossiers RCC"])


class DossierDetailResponse(BaseModel):
    """Dossier + conformité calculée serveur (jamais recalculée côté client)."""

    dossier: DossierDetail
    compliance: ComplianceReport
    effective_values: dict[str, Optional[float]]


def _detail_or_404(dossier_id: str) -> DossierDetail:
    detail = dossier_store.get_dossier(dossier_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Dossier introuvable.")
    return detail


def _to_response(detail: DossierDetail) -> DossierDetailResponse:
    return DossierDetailResponse(
        dossier=detail,
        compliance=build_compliance(detail),
        effective_values=dossier_store.effective_values(detail),
    )


@router.get("", response_model=DossierListResponse)
async def list_dossiers(
    status: Optional[str] = Query(
        default=None,
        pattern="^(all|pending|validated|rejected|escalated)$",
        description="Filtre de statut ; « all » ou absent = tous.",
    ),
    search: Optional[str] = Query(default=None, max_length=120),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    _user: SessionUser = Depends(require_analyst),
) -> DossierListResponse:
    items, total, counts = dossier_store.list_dossiers(
        status=status, search=search, limit=limit, offset=offset
    )
    return DossierListResponse(items=items, total=total, counts=counts)


@router.post("", response_model=DossierDetailResponse, status_code=201)
async def create_dossier(
    payload: DossierCreateRequest,
    user: SessionUser = Depends(require_analyst),
) -> DossierDetailResponse:
    """Crée le dossier une fois le job d'extraction terminé (`result_ready`)."""
    existing = dossier_store.find_by_job(payload.job_id)
    if existing is not None:
        return _to_response(existing)

    job = job_store.get(payload.job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job introuvable ou expiré.")
    if job.status == "failed":
        raise HTTPException(status_code=422, detail=job.error or "Le job a échoué.")
    if job.status != "completed" or job.result is None:
        raise HTTPException(
            status_code=409, detail="Le résultat d'extraction n'est pas encore disponible."
        )

    detail = dossier_store.create_dossier(
        job_id=payload.job_id,
        result=job.result,
        client_name=payload.client_name,
        ice=payload.ice,
        credit_amount=payload.credit_amount,
        exercice_date=payload.exercice_date,
        sector=payload.sector,
        actor=user.display_name,
    )
    return _to_response(detail)


@router.get("/{dossier_id}", response_model=DossierDetailResponse)
async def get_dossier(
    dossier_id: str,
    _user: SessionUser = Depends(require_analyst),
) -> DossierDetailResponse:
    return _to_response(_detail_or_404(dossier_id))


@router.patch("/{dossier_id}", response_model=DossierDetailResponse)
async def patch_dossier(
    dossier_id: str,
    payload: DossierPatchRequest,
    user: SessionUser = Depends(require_analyst),
) -> DossierDetailResponse:
    """Validation / rejet / arbitrage — le rejet exige un motif."""
    current = _detail_or_404(dossier_id)

    if payload.status == "rejected" and not (payload.motif or current.motif):
        raise HTTPException(
            status_code=422, detail="Un motif est obligatoire pour rejeter un dossier."
        )
    if payload.status == "validated":
        report = build_compliance(current)
        if not report.can_validate:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{report.blockers} règle(s) de conformité bloquante(s) "
                    "doivent être levées avant validation."
                ),
            )

    updated = dossier_store.update_dossier(
        dossier_id,
        changes=payload.model_dump(exclude_unset=True),
        actor=user.display_name,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Dossier introuvable.")
    return _to_response(updated)


@router.put("/{dossier_id}/overrides", response_model=DossierDetailResponse)
async def put_overrides(
    dossier_id: str,
    payload: FieldOverrideBatchRequest,
    user: SessionUser = Depends(require_analyst),
) -> DossierDetailResponse:
    """Enregistre les corrections analystes (valeur OCR jamais écrasée)."""
    _detail_or_404(dossier_id)
    saved = dossier_store.save_overrides(
        dossier_id,
        entries=[
            (o.field_code, o.corrected_value, o.verified) for o in payload.overrides
        ],
        actor=user.display_name,
    )
    if saved is None:
        raise HTTPException(status_code=404, detail="Dossier introuvable.")
    return _to_response(_detail_or_404(dossier_id))


@router.get("/{dossier_id}/overrides", response_model=list[FieldOverride])
async def get_overrides(
    dossier_id: str,
    _user: SessionUser = Depends(require_analyst),
) -> list[FieldOverride]:
    _detail_or_404(dossier_id)
    return dossier_store.list_overrides(dossier_id)


@router.get("/{dossier_id}/audit", response_model=AuditResponse)
async def get_dossier_audit(
    dossier_id: str,
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    _user: SessionUser = Depends(require_analyst),
) -> AuditResponse:
    _detail_or_404(dossier_id)
    items, total = dossier_store.list_audit(
        dossier_id=dossier_id, limit=limit, offset=offset
    )
    return AuditResponse(items=items, total=total)


@router.get("/{dossier_id}/file")
async def get_dossier_file(
    dossier_id: str,
    _user: SessionUser = Depends(require_analyst),
) -> FileResponse:
    """PDF d'origine, pour la visionneuse de l'écran de validation."""
    detail = _detail_or_404(dossier_id)
    path = dossier_store.get_dossier_pdf_path(dossier_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Aucune liasse rattachée à ce dossier.")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=detail.filename or f"{dossier_id}.pdf",
        headers={"Content-Disposition": f'inline; filename="{dossier_id}.pdf"'},
    )


@router.post("/{dossier_id}/attach", response_model=DossierDetailResponse)
async def attach_job(
    dossier_id: str,
    payload: DossierCreateRequest,
    user: SessionUser = Depends(require_analyst),
) -> DossierDetailResponse:
    """Rattache une extraction terminée à un dossier existant (« liasse manquante »)."""
    _detail_or_404(dossier_id)
    job = job_store.get(payload.job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job introuvable ou expiré.")
    if job.status != "completed" or job.result is None:
        raise HTTPException(
            status_code=409, detail="Le résultat d'extraction n'est pas encore disponible."
        )
    updated = dossier_store.attach_result(
        dossier_id, job_id=payload.job_id, result=job.result, actor=user.display_name
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Dossier introuvable.")
    return _to_response(updated)


audit_router = APIRouter(prefix="/rcc/audit", tags=["Dossiers RCC"])


@audit_router.get("", response_model=AuditResponse)
async def get_global_audit(
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    _user: SessionUser = Depends(require_analyst),
) -> AuditResponse:
    """Journal global — écran « Historique & piste d'audit »."""
    items, total = dossier_store.list_audit(limit=limit, offset=offset)
    return AuditResponse(items=items, total=total)
