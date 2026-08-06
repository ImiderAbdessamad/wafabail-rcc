"""Schémas des dossiers RCC, corrections analystes et piste d'audit."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.rcc import RccAnalysisResult

DossierStatus = Literal["pending", "validated", "rejected", "escalated"]

STATUS_LABELS: dict[str, str] = {
    "pending": "À réviser",
    "validated": "Validé",
    "rejected": "Rejeté",
    "escalated": "Arbitrage demandé",
}


class DossierSummary(BaseModel):
    """Ligne de la file de validation."""

    id: str
    job_id: Optional[str] = None
    client_name: str = ""
    ice: Optional[str] = None
    credit_amount: Optional[float] = None
    exercice_date: Optional[str] = None
    status: DossierStatus = "pending"
    status_label: str = "À réviser"
    created_at: str
    updated_at: str
    sector: Optional[str] = None
    filename: Optional[str] = None
    has_document: bool = False
    completeness_pct: float = 0.0
    override_count: int = 0
    motif: Optional[str] = None
    comment: Optional[str] = None
    decided_by: Optional[str] = None
    decided_at: Optional[str] = None


class FieldOverride(BaseModel):
    """Correction analyste — la valeur OCR d'origine n'est jamais écrasée."""

    field_code: str
    original_value: Optional[float] = None
    corrected_value: Optional[float] = None
    edited_by: str
    edited_at: str
    #: True lorsque l'analyste confirme la valeur OCR sans la modifier.
    verified: bool = False


class DossierDetail(DossierSummary):
    """Dossier complet : instantané d'extraction + corrections superposées."""

    result: Optional[RccAnalysisResult] = None
    overrides: list[FieldOverride] = Field(default_factory=list)


class DossierListResponse(BaseModel):
    items: list[DossierSummary]
    total: int
    counts: dict[str, int] = Field(default_factory=dict)


class DossierCreateRequest(BaseModel):
    """Créé après le `result_ready` du job d'extraction."""

    job_id: str
    client_name: Optional[str] = None
    ice: Optional[str] = None
    credit_amount: Optional[float] = Field(default=None, ge=0)
    exercice_date: Optional[str] = None
    sector: Optional[str] = None


class DossierPatchRequest(BaseModel):
    """Changement de statut et/ou métadonnées d'instruction."""

    status: Optional[DossierStatus] = None
    motif: Optional[str] = Field(default=None, max_length=200)
    comment: Optional[str] = Field(default=None, max_length=4000)
    client_name: Optional[str] = Field(default=None, max_length=200)
    sector: Optional[str] = Field(default=None, max_length=120)
    credit_amount: Optional[float] = Field(default=None, ge=0)
    exercice_date: Optional[str] = Field(default=None, max_length=40)


class FieldOverrideRequest(BaseModel):
    field_code: str = Field(min_length=1, max_length=60)
    corrected_value: Optional[float] = None
    #: Confirmation de la valeur OCR telle quelle (poste à faible confiance).
    verified: bool = False


class FieldOverrideBatchRequest(BaseModel):
    overrides: list[FieldOverrideRequest] = Field(min_length=1, max_length=40)


class AuditEntry(BaseModel):
    """Entrée du journal — correction de champ ou évènement de cycle de vie."""

    kind: Literal["correction", "event"]
    timestamp: str
    actor: str
    dossier_id: str
    client_name: str = ""
    field_code: Optional[str] = None
    field_label: Optional[str] = None
    before: Optional[str] = None
    after: Optional[str] = None
    action: str


class AuditResponse(BaseModel):
    items: list[AuditEntry]
    total: int


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=200)


class SessionUser(BaseModel):
    username: str
    display_name: str
    role: str = "analyst"
    initials: str = ""
