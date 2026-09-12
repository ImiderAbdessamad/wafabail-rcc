"""Scoring facade reusing the single RCC financial extraction job.

No second OCR call is performed: the scoring view is calculated from the same
resolved dataset, provenance, and accounting controls as the RCC result.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile

from app.routers.rcc import (
    _stream_job_response,
    create_rcc_job,
    get_rcc_job,
    get_rcc_result,
)
from app.schemas.dossier import SessionUser
from app.schemas.rcc import RccJobProgress
from app.schemas.scoring import (
    ScoringJobCreateResponse,
    ScoringJobResult,
)
from app.services.auth import require_analyst


router = APIRouter(prefix="/scoring", tags=["Scoring financier"])


@router.post("/jobs", response_model=ScoringJobCreateResponse)
async def create_scoring_job(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="PDF de liasse fiscale"),
    max_pages: Optional[int] = Form(None),
    _user: SessionUser = Depends(require_analyst),
) -> ScoringJobCreateResponse:
    created = await create_rcc_job(
        background_tasks=background_tasks,
        file=file,
        max_pages=max_pages,
        _user=_user,
    )
    return ScoringJobCreateResponse(
        job_id=created.job_id,
        status=created.status,
        stream_url=f"/api/v1/scoring/jobs/{created.job_id}/stream",
        result_url=f"/api/v1/scoring/jobs/{created.job_id}/result",
    )


@router.get("/jobs/{job_id}", response_model=RccJobProgress)
async def get_scoring_job(
    job_id: str,
    _user: SessionUser = Depends(require_analyst),
) -> RccJobProgress:
    progress = await get_rcc_job(job_id=job_id, _user=_user)
    return progress.model_copy(
        update={
            "stream_url": f"/api/v1/scoring/jobs/{job_id}/stream",
            "result_url": f"/api/v1/scoring/jobs/{job_id}/result",
        }
    )


@router.get("/jobs/{job_id}/stream")
async def stream_scoring_job(
    job_id: str,
    _user: SessionUser = Depends(require_analyst),
):
    return _stream_job_response(job_id, route_prefix="/api/v1/scoring")


@router.get("/jobs/{job_id}/result", response_model=ScoringJobResult)
async def get_scoring_result(
    job_id: str,
    _user: SessionUser = Depends(require_analyst),
) -> ScoringJobResult:
    result = await get_rcc_result(job_id=job_id, _user=_user)
    if result.scoring is None:
        raise HTTPException(
            status_code=409,
            detail="La vue scoring n'est pas disponible pour cette extraction.",
        )
    return ScoringJobResult(
        document=result.document,
        extraction=result.extraction,
        scoring=result.scoring,
        controls=[control.model_dump() for control in result.controls],
        warnings=list(result.warnings),
    )
