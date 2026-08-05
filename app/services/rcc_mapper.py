"""Projection FinancialDataset → champs RCC EKIP (+ règles dérivées)."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.schemas.direct_financial_extraction import (
    DocumentSummary,
    ExtractionSummary,
)
from app.schemas.financial_analysis import FinancialDataset, FinancialValue
from app.schemas.rcc import RCC_ELEMENTS, RccAnalysisResult, RccField


def _decimal_to_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value)


def _fv(dataset: FinancialDataset, attr: str) -> FinancialValue | None:
    return getattr(dataset, attr, None)


def _usable(fv: FinancialValue | None) -> bool:
    if fv is None or fv.value is None:
        return False
    return fv.status in {"confirmed", "derived", "ambiguous"}


def _pick(
    dataset: FinancialDataset,
    *attrs: str,
) -> tuple[float | None, str, float]:
    """Retourne (value, status, confidence) depuis le premier attr utilisable."""
    for attr in attrs:
        fv = _fv(dataset, attr)
        if _usable(fv):
            conf = 0.0
            if fv and fv.provenance:
                for p in fv.provenance:
                    if p.confidence is not None:
                        conf = max(conf, float(p.confidence))
            if conf == 0.0 and fv and fv.status == "derived":
                conf = 0.85
            elif conf == 0.0 and fv and fv.status == "confirmed":
                conf = 0.9
            return _decimal_to_float(fv.value if fv else None), fv.status if fv else "missing", conf
        if fv is not None and fv.status in {"conflicting", "invalid"}:
            return None, fv.status, 0.0
    return None, "missing", 0.0


def build_rcc_result(
    *,
    document: DocumentSummary,
    extraction: ExtractionSummary,
    dataset: FinancialDataset,
    warnings: list[str] | None = None,
) -> RccAnalysisResult:
    """Construit la réponse RCC (uniquement les postes EKIP)."""
    resolved: dict[str, tuple[float | None, str, float, str | None]] = {}

    # Mapping dataset → codes RCC
    mapping: dict[str, tuple[str, ...]] = {
        "ACTIFS_IMMOBILISES": ("actifs_immobilises",),
        "TOTAL_BILAN": ("total_bilan", "total_actif", "total_passif"),
        "CHIFFRE_AFFAIRES": ("chiffre_affaires",),
        "CA_EXPORT": ("ca_export",),
        # MLT = dettes de financement ; fallback sur dettes_financieres
        "DETTES_BANCAIRES_MLT": ("dettes_bancaires_mlt", "dettes_financieres"),
        "DETTES_BANCAIRES_CT": ("dettes_bancaires_ct",),
        "PASSIF_CIRCULANT": ("passif_circulant",),
        "DETTES_FOURNISSEURS": ("fournisseurs",),
        "COMPTE_COURANT_ASSOCIES": ("compte_courant_associes",),
        "TRESORERIE_PASSIF": ("tresorerie_passif",),
        "ACTIF_CIRCULANT": ("actif_circulant",),
        "CREANCES_CLIENTS": ("clients",),
        "TRESORERIE_ACTIF": ("tresorerie_actif",),
        "CAISSE": ("caisse",),
        "ACHATS_REVENDUS": ("achats_revendus", "achats"),
        "ACHATS_CONSOMMES": ("achats_consommes",),
        "AUTRES_CHARGES_EXTERNES": ("autres_charges_externes",),
        "CHARGES_INTERETS": ("frais_financiers", "charges_financieres"),
        "RESULTAT_NET": ("resultat_net",),
    }

    for code, attrs in mapping.items():
        value, status, conf = _pick(dataset, *attrs)
        note = None
        if code == "DETTES_BANCAIRES_MLT" and value is not None:
            mlt_fv = _fv(dataset, "dettes_bancaires_mlt")
            if not _usable(mlt_fv) and _usable(_fv(dataset, "dettes_financieres")):
                note = "Alias dettes de financement (MLT)"
        if code == "CHARGES_INTERETS" and value is not None:
            ff = _fv(dataset, "frais_financiers")
            if not _usable(ff) and _usable(_fv(dataset, "charges_financieres")):
                note = "Proxy charges financières (TOTAL V)"
        resolved[code] = (value, status, conf, note)

    # TYPE_RESULTAT dérivé du résultat net
    rn_val, rn_status, rn_conf, _ = resolved.get(
        "RESULTAT_NET", (None, "missing", 0.0, None)
    )
    if rn_val is not None and rn_status in {"confirmed", "derived", "ambiguous"}:
        if rn_val > 0:
            type_note = "Bénéficiaire"
        elif rn_val < 0:
            type_note = "Déficitaire"
        else:
            type_note = "Nul"
        resolved["TYPE_RESULTAT"] = (None, "derived", max(rn_conf, 0.95), type_note)
    else:
        resolved["TYPE_RESULTAT"] = (None, "missing", 0.0, None)

    fields: list[RccField] = []
    found = 0
    for num, code, label, source in RCC_ELEMENTS:
        value, status, conf, note = resolved.get(code, (None, "missing", 0.0, None))
        if code == "TYPE_RESULTAT":
            if note:
                found += 1
            fields.append(
                RccField(
                    number=num,
                    code=code,
                    label=label,
                    value=None,
                    source=source,
                    status=status,
                    note=note,
                    confidence=conf,
                )
            )
            continue

        if value is not None and status in {"confirmed", "derived", "ambiguous"}:
            found += 1
        fields.append(
            RccField(
                number=num,
                code=code,
                label=label,
                value=value,
                source=source,
                status=status,
                note=note,
                confidence=conf,
            )
        )

    completeness = round(100.0 * found / len(RCC_ELEMENTS), 1)
    return RccAnalysisResult(
        document=document,
        extraction=extraction,
        fields=fields,
        completeness_pct=completeness,
        warnings=list(warnings or []),
    )


def rcc_fields_as_dict(result: RccAnalysisResult) -> dict[str, Any]:
    """Vue plate code → valeur (TYPE_RESULTAT via note)."""
    out: dict[str, Any] = {}
    for f in result.fields:
        if f.code == "TYPE_RESULTAT":
            out[f.code] = f.note
        else:
            out[f.code] = f.value
    return out
