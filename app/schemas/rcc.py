"""Schémas de réponse RCC — champs bilanciels pour enrichissement EKIP."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.direct_financial_extraction import (
    DocumentSummary,
    ExtractionSummary,
    JobStatus,
)

# Référentiel métier RCC (enrichissement EKIP)
RCC_ELEMENTS: list[tuple[int, str, str, str]] = [
    (1, "ACTIFS_IMMOBILISES", "Actifs immobilisés", "Bilan Actif"),
    (2, "TOTAL_BILAN", "Total bilan", "Bilan Actif"),
    (3, "CHIFFRE_AFFAIRES", "Chiffre d'affaires", "CPC"),
    (4, "CA_EXPORT", "Chiffre d'affaires à l'export", "CPC"),
    (5, "DETTES_BANCAIRES_MLT", "Dettes bancaires MLT", "Bilan Passif"),
    (6, "DETTES_BANCAIRES_CT", "Dettes bancaires CT", "Bilan Passif"),
    (7, "PASSIF_CIRCULANT", "Passif circulant", "Bilan Passif"),
    (8, "DETTES_FOURNISSEURS", "Dettes fournisseurs", "Bilan Passif"),
    (9, "COMPTE_COURANT_ASSOCIES", "Compte courant d'associés", "Bilan"),
    (10, "TRESORERIE_PASSIF", "Trésorerie passif", "Bilan Passif"),
    (11, "ACTIF_CIRCULANT", "Actif circulant", "Bilan Actif"),
    (12, "CREANCES_CLIENTS", "Créances clients", "Bilan Actif"),
    (13, "TRESORERIE_ACTIF", "Trésorerie actif", "Bilan Actif"),
    (14, "CAISSE", "Caisse actif", "Bilan Actif"),
    (15, "ACHATS_REVENDUS", "Achats revendus", "CPC"),
    (16, "ACHATS_CONSOMMES", "Achats consommés", "CPC"),
    (17, "AUTRES_CHARGES_EXTERNES", "Autres charges externes", "CPC"),
    (18, "CHARGES_INTERETS", "Charges d'intérêts", "CPC"),
    (19, "RESULTAT_NET", "Résultat net", "CPC"),
    (20, "TYPE_RESULTAT", "Type de résultat", "Dérivé"),
]


class FieldEvidence(BaseModel):
    """Provenance d'une valeur extraite — alimente le panneau « Zones extraites »."""

    page_number: Optional[int] = None
    raw_label: Optional[str] = None
    raw_value: Optional[str] = None
    column_name: Optional[str] = None
    page_type: Optional[str] = None
    confidence: Optional[float] = None
    source_excerpt: Optional[str] = None


class RccField(BaseModel):
    """Un poste bilanciel / CPC destiné à EKIP."""

    number: int = Field(ge=1, le=20)
    code: str
    label: str
    value: Optional[float] = None
    unit: str = "MAD"
    source: str
    status: str = "missing"
    note: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    # Exercice N-1 : renseigné uniquement pour les postes réellement extraits
    # en N-1 par le pipeline (pas de dérivation côté client).
    value_n1: Optional[float] = None
    evidence: list[FieldEvidence] = Field(default_factory=list)


class AccountingControlView(BaseModel):
    """Vue API d'un contrôle comptable (app.services.financial_controls)."""

    code: str
    status: str  # passed | failed | not_testable
    label: str
    expected: Optional[float] = None
    observed: Optional[float] = None
    difference: Optional[float] = None
    tolerance: Optional[float] = None
    affected_fields: list[str] = Field(default_factory=list)
    message: str = ""


class RccAnalysisResult(BaseModel):
    """Réponse API — uniquement les champs RCC + métadonnées document."""

    document: DocumentSummary
    extraction: ExtractionSummary
    fields: list[RccField]
    completeness_pct: float = 0.0
    warnings: list[str] = Field(default_factory=list)
    controls: list[AccountingControlView] = Field(default_factory=list)


class RccJobCreateResponse(BaseModel):
    job_id: str
    status: JobStatus = "queued"
    stream_url: str
    result_url: str


class RccJobProgress(BaseModel):
    job_id: str
    status: JobStatus
    progress_pct: int = 0
    current_step: str = "queued"
    current_page: int | None = None
    pages_total: int | None = None
    pages_financial: int = 0
    pages_skipped: int = 0
    pages_failed: int = 0
    message: str = ""
    error: str | None = None
    stream_url: str | None = None
    result_url: str | None = None


TypeResultat = Literal["Bénéficiaire", "Déficitaire", "Nul"]
