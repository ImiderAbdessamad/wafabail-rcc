"""Configuration — extraction bilancielle RCC (GLM Vision / Ollama)."""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

OLLAMA_URL = os.getenv(
    "OLLAMA_URL", "https://ollama-lnhh4y-11434.svc-usw2.nicegpu.com"
).rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "glm4v")
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", OLLAMA_MODEL)
OLLAMA_TIMEOUT_SECONDS = float(os.getenv("OLLAMA_TIMEOUT", "300"))
REQUEST_TIMEOUT_SECONDS = OLLAMA_TIMEOUT_SECONDS

# Alias conservé (importé par financial_candidate_resolver)
OLLAMA_MAPPING_MODEL = os.getenv("OLLAMA_MAPPING_MODEL", "qwen3:8b")

ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "*").split(",")
    if origin.strip()
]

MAX_UPLOAD_MB = float(os.getenv("MAX_UPLOAD_MB", "50"))
MAX_UPLOAD_BYTES = int(MAX_UPLOAD_MB * 1024 * 1024)

DIRECT_FINANCIAL_MODEL = os.getenv(
    "DIRECT_FINANCIAL_MODEL",
    os.getenv("OLLAMA_VISION_MODEL", OLLAMA_MODEL),
)
DIRECT_FINANCIAL_TIMEOUT_SECONDS = float(
    os.getenv("DIRECT_FINANCIAL_TIMEOUT_SECONDS", "300")
)
DIRECT_FINANCIAL_NUM_CTX = int(os.getenv("DIRECT_FINANCIAL_NUM_CTX", "8192"))
DIRECT_FINANCIAL_NUM_PREDICT = int(os.getenv("DIRECT_FINANCIAL_NUM_PREDICT", "4096"))
DIRECT_FINANCIAL_KEEP_ALIVE = os.getenv("DIRECT_FINANCIAL_KEEP_ALIVE", "10m")
DIRECT_FINANCIAL_MAX_ATTEMPTS = int(os.getenv("DIRECT_FINANCIAL_MAX_ATTEMPTS", "2"))
DIRECT_FINANCIAL_RENDER_DPI = int(os.getenv("DIRECT_FINANCIAL_RENDER_DPI", "180"))
DIRECT_FINANCIAL_MAX_PAGES = int(os.getenv("DIRECT_FINANCIAL_MAX_PAGES", "60"))
DIRECT_FINANCIAL_PAGE_DELAY_SECONDS = float(
    os.getenv("DIRECT_FINANCIAL_PAGE_DELAY_SECONDS", "0.5")
)
DIRECT_FINANCIAL_MAX_IMAGE_DIMENSION = int(
    os.getenv("DIRECT_FINANCIAL_MAX_IMAGE_DIMENSION", "1600")
)
DIRECT_FINANCIAL_REGION_FALLBACK = os.getenv(
    "DIRECT_FINANCIAL_REGION_FALLBACK", "true"
).lower() in {"1", "true", "yes"}
DIRECT_FINANCIAL_JOB_TTL_MINUTES = int(
    os.getenv("DIRECT_FINANCIAL_JOB_TTL_MINUTES", "60")
)
DIRECT_FINANCIAL_CLASSIFY_DPI = int(os.getenv("DIRECT_FINANCIAL_CLASSIFY_DPI", "72"))

STATIC_DIR = BASE_DIR / "static"


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# --- Moteur d'extraction RCC (app/services/ocr_lab_core_v10.py) -------------
# Le moteur v10 orchestre cinq modèles aux rôles distincts. Les valeurs par
# défaut reprennent celles du script de laboratoire de référence.
RCC_OLLAMA_URL = os.getenv(
    "RCC_OLLAMA_URL",
    os.getenv("OLLAMA_URL", "https://ollama-lnhh4y-11434.svc-usw2.nicegpu.com"),
).rstrip("/")
# Orientation, classification de page et arbitrage visuel.
RCC_VISION_MODEL = os.getenv(
    "RCC_VISION_MODEL", "hf.co/unsloth/GLM-4.6V-Flash-GGUF:Q4_K_M"
)
# Lecture OCR des cellules isolées.
RCC_OCR_MODEL = os.getenv("RCC_OCR_MODEL", "glm-ocr:q8_0")
# Relecture indépendante des cellules (consensus multi-modèles).
RCC_VERIFY_MODEL = os.getenv("RCC_VERIFY_MODEL", "qwen3-vl:30b")
# Mapping sémantique des libellés — ne voit jamais les montants.
RCC_MAPPER_MODEL = os.getenv("RCC_MAPPER_MODEL", "qwen3.5:9b")
# Arbitrage des libellés ambigus — ne voit jamais les montants non plus.
RCC_ADJUDICATOR_MODEL = os.getenv("RCC_ADJUDICATOR_MODEL", "gemma4:latest")

RCC_USE_GLM_VERIFICATION = _flag("RCC_USE_GLM_VERIFICATION", True)
RCC_USE_REASONING_MAPPER = _flag("RCC_USE_REASONING_MAPPER", True)
RCC_USE_ADJUDICATOR = _flag("RCC_USE_ADJUDICATOR", True)

RCC_REQUEST_TIMEOUT_SECONDS = int(os.getenv("RCC_REQUEST_TIMEOUT_SECONDS", "600"))
RCC_KEEP_ALIVE = os.getenv("RCC_KEEP_ALIVE", "20m")
RCC_RENDER_DPI = int(os.getenv("RCC_RENDER_DPI", "220"))
RCC_EXTRACT_MAX_SIDE = int(os.getenv("RCC_EXTRACT_MAX_SIDE", "2400"))

# --- Persistance (dossiers RCC, corrections analystes, sessions) -------------
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
DB_PATH = os.getenv("DB_PATH", str(DATA_DIR / "wafabail_rcc.sqlite3"))
PDF_STORAGE_DIR = Path(os.getenv("PDF_STORAGE_DIR", str(DATA_DIR / "pdfs")))

# --- Authentification analyste (rôle unique) --------------------------------
# Un seul rôle « valideur RCC » à ce stade — pas de RBAC.
ANALYST_USERNAME = os.getenv("ANALYST_USERNAME", "analyste@wafabail.ma")
ANALYST_PASSWORD = os.getenv("ANALYST_PASSWORD", "wafabail2026")
ANALYST_DISPLAY_NAME = os.getenv("ANALYST_DISPLAY_NAME", "Analyste RCC")
SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "wb_rcc_session")
SESSION_TTL_HOURS = int(os.getenv("SESSION_TTL_HOURS", "12"))
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() in {
    "1",
    "true",
    "yes",
}

# Seuil de confiance sous lequel un champ est signalé « à vérifier ».
CONFIDENCE_REVIEW_THRESHOLD = float(os.getenv("CONFIDENCE_REVIEW_THRESHOLD", "0.8"))
