"""Stub minimal — dépendance du parseur Markdown (non utilisé par le pipeline GLM)."""
from __future__ import annotations

from pydantic import BaseModel


class PdfPageExtraction(BaseModel):
    page_number: int = 1
    markdown: str = ""
