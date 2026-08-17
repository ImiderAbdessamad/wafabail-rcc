"""API jobs d'extraction bilancielle RCC (moteur v10 → champs EKIP)."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app.config import DIRECT_FINANCIAL_MAX_PAGES, MAX_UPLOAD_BYTES
from app.schemas.dossier import SessionUser
from app.schemas.rcc import RccAnalysisResult, RccJobCreateResponse, RccJobProgress
from app.services.auth import require_analyst
from app.services.rcc_lab_pipeline import run_financial_job
from app.services.dossier_store import store_pdf
from app.services.financial_job_store import job_store

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/rcc",
    tags=["RCC bilanciel (EKIP)"],
)

_PIPELINE_LOCK = asyncio.Lock()


async def _read_pdf_upload(file: UploadFile) -> tuple[bytes, str]:
    filename = file.filename or "document.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="Seuls les fichiers PDF sont acceptés.")
    content_type = (file.content_type or "").lower()
    if content_type and content_type not in {
        "application/pdf",
        "application/octet-stream",
        "",
    }:
        raise HTTPException(status_code=422, detail=f"MIME non supporté : {content_type}")

    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Fichier trop volumineux (max {MAX_UPLOAD_BYTES // (1024 * 1024)} Mo).",
            )
        chunks.append(chunk)
    content = b"".join(chunks)
    if not content.startswith(b"%PDF"):
        raise HTTPException(status_code=422, detail="Signature PDF invalide.")
    return content, filename


async def _run_job_sequential(job_id: str) -> None:
    async with _PIPELINE_LOCK:
        await run_financial_job(job_id, store=job_store)


@router.post("/jobs", response_model=RccJobCreateResponse)
async def create_rcc_job(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="PDF de liasse fiscale"),
    max_pages: Optional[int] = Form(None),
    _user: SessionUser = Depends(require_analyst),
) -> RccJobCreateResponse:
    """Crée un job d'extraction RCC asynchrone (progression via SSE)."""
    if max_pages is not None and max_pages < 1:
        raise HTTPException(status_code=422, detail="max_pages doit être >= 1.")
    if max_pages is not None and max_pages > DIRECT_FINANCIAL_MAX_PAGES:
        raise HTTPException(
            status_code=422,
            detail=f"max_pages ne peut pas dépasser {DIRECT_FINANCIAL_MAX_PAGES}.",
        )

    content, filename = await _read_pdf_upload(file)
    job = job_store.create(
        pdf_bytes=content,
        filename=filename,
        include_markdown=False,
        max_pages=max_pages,
    )
    # Le job store libère `pdf_bytes` en fin d'analyse : on garde une copie sur
    # disque pour la visionneuse de l'écran de validation.
    try:
        store_pdf(job.job_id, content)
    except OSError:
        logger.exception("Impossible d'archiver le PDF du job %s", job.job_id)

    background_tasks.add_task(_run_job_sequential, job.job_id)
    return RccJobCreateResponse(
        job_id=job.job_id,
        status="queued",
        stream_url=f"/api/v1/rcc/jobs/{job.job_id}/stream",
        result_url=f"/api/v1/rcc/jobs/{job.job_id}/result",
    )


@router.get("/jobs/{job_id}", response_model=RccJobProgress)
async def get_rcc_job(
    job_id: str,
    _user: SessionUser = Depends(require_analyst),
) -> RccJobProgress:
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job introuvable ou expiré.")
    return job_store.to_progress(job)


@router.get("/jobs/{job_id}/stream")
async def stream_rcc_job(
    job_id: str,
    _user: SessionUser = Depends(require_analyst),
) -> StreamingResponse:
    """Server-Sent Events pour la progression du job."""
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job introuvable ou expiré.")

    queue = job_store.subscribe(job_id)
    if queue is None:
        raise HTTPException(status_code=404, detail="Job introuvable.")

    async def event_generator() -> AsyncIterator[str]:
        try:
            progress = job_store.to_progress(job)
            yield (
                "event: job_status\n"
                f"data: {progress.model_dump_json()}\n\n"
            )
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=25.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    current = job_store.get(job_id)
                    if current is None or current.status in {"completed", "failed"}:
                        break
                    continue

                event_type = event.get("event", "message")
                payload = json.dumps(event, ensure_ascii=False, default=str)
                yield f"event: {event_type}\ndata: {payload}\n\n"

                if event_type in {"result_ready", "job_failed"}:
                    break
        finally:
            job_store.unsubscribe(job_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/jobs/{job_id}/result",
    response_model=RccAnalysisResult,
)
async def get_rcc_result(
    job_id: str,
    _user: SessionUser = Depends(require_analyst),
) -> RccAnalysisResult:
    """Résultat RCC : uniquement les postes bilanciels EKIP."""
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job introuvable ou expiré.")
    if job.status == "failed":
        raise HTTPException(
            status_code=422,
            detail=job.error or "Le job a échoué.",
        )
    if job.status != "completed" or job.result is None:
        raise HTTPException(
            status_code=409,
            detail="Le résultat n'est pas encore disponible.",
        )
    return job.result
