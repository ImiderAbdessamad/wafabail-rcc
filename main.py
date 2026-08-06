"""Point d'entrée FastAPI — extraction bilancielle RCC pour EKIP.

Lancement :
    uvicorn main:app --reload --host 127.0.0.1 --port 8001
"""

from __future__ import annotations

import logging

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import ALLOWED_ORIGINS, STATIC_DIR
from app.db import close_db, init_db
from app.routers import auth, dossiers, rcc

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Wafabail RCC API",
    description=(
        "Extraction des données bilancielles d'une liasse fiscale "
        "et calcul des champs manquants pour enrichir le modèle EKIP. "
        "Réponse limitée aux postes RCC métier."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS != ["*"] else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/v1")
app.include_router(rcc.router, prefix="/api/v1")
app.include_router(dossiers.router, prefix="/api/v1")
app.include_router(dossiers.audit_router, prefix="/api/v1")


@app.on_event("startup")
async def on_startup() -> None:
    init_db()
    logger.info("Base de données RCC initialisée.")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    close_db()


@app.get("/health", tags=["Système"])
async def health() -> dict:
    return {"status": "ok", "service": "wafabail-rcc"}


@app.get("/", tags=["Système"], include_in_schema=False)
async def ui_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/", StaticFiles(directory=str(STATIC_DIR)), name="static")
