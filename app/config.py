"""Configuration — extraction bilancielle RCC (GLM Vision / Ollama)."""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
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
