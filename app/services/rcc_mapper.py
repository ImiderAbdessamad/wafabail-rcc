"""Projection FinancialDataset → champs RCC EKIP (+ règles dérivées)."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.schemas.direct_financial_extraction import (
    DocumentSummary,
    ExtractionSummary,
)
from app.schemas.financial_analysis import (
    AccountingControlResult,
    FinancialDataset,
    FinancialValue,
)
from app.schemas.rcc import (
    RCC_ELEMENTS,
    AccountingControlView,
    FieldEvidence,
    RccAnalysisResult,
    RccField,
)

# Libellés métier des contrôles de app.services.financial_controls
CONTROL_LABELS: dict[str, str] = {
    "bilan_equilibre": "Total Actif = Total Passif",
    "tresorerie_nette": "Trésorerie nette = trésorerie actif − trésorerie passif",
    "resultat_exploitation": "Produits d'exploitation − charges = résultat d'exploitation",
    "resultat_financier": "Produits financiers − charges financières = résultat financier",
    "resultat_courant": "Résultat exploitation + financier = résultat courant",
    "resultat_non_courant": "Produits non courants − charges = résultat non courant",
    "resultat_avant_impot": "Résultat courant + non courant = résultat avant impôt",
    "resultat_net": "Résultat avant impôt − IS = résultat net",
}

# Postes RCC pour lesquels le pipeline extrait réellement un exercice N-1.
# Aucune dérivation : si l'attribut est absent ou inutilisable, pas de N-1.
_N1_ATTRS: dict[str, str] = {
    "CHIFFRE_AFFAIRES": "chiffre_affaires_n1",
    "RESULTAT_NET": "resultat_net_n1",
    "TOTAL_BILAN": "total_bilan_n1",
    "DETTES_BANCAIRES_MLT": "dettes_financieres_n1",
}


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
) -> tuple[float | None, str, float, FinancialValue | None]:
    """Retourne (value, status, confidence, source) depuis le 1ᵉʳ attr utilisable.

    La `FinancialValue` retenue est renvoyée telle quelle pour que l'appelant
    puisse en extraire la provenance (panneau « Zones extraites »).
    """
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
            return (
                _decimal_to_float(fv.value if fv else None),
                fv.status if fv else "missing",
                conf,
                fv,
            )
        if fv is not None and fv.status in {"conflicting", "invalid"}:
            # Champ invalidé par un contrôle comptable : on garde la provenance
            # pour que l'analyste voie d'où venait la valeur rejetée.
            return None, fv.status, 0.0, fv
    return None, "missing", 0.0, None


def _evidence(fv: FinancialValue | None, *, limit: int = 6) -> list[FieldEvidence]:
    """Provenance → évidences API (dédupliquées, les plus sûres d'abord)."""
    if fv is None or not fv.provenance:
        return []
    seen: set[tuple] = set()
    items: list[tuple[float, FieldEvidence]] = []
    for p in fv.provenance:
        key = (p.page_number, p.raw_label, p.raw_value, p.column_name)
        if key in seen:
            continue
        seen.add(key)
        conf = float(p.confidence) if p.confidence is not None else 0.0
        items.append(
            (
                conf,
                FieldEvidence(
                    page_number=p.page_number,
                    raw_label=p.raw_label,
                    raw_value=p.raw_value,
                    column_name=p.column_name,
                    page_type=p.page_type,
                    confidence=conf or None,
                    source_excerpt=p.source_excerpt,
                ),
            )
        )
    items.sort(key=lambda pair: pair[0], reverse=True)
    return [ev for _, ev in items[:limit]]


def _n1_value(dataset: FinancialDataset, code: str) -> float | None:
    attr = _N1_ATTRS.get(code)
    if attr is None:
        return None
    fv = _fv(dataset, attr)
    if not _usable(fv):
        return None
    return _decimal_to_float(fv.value if fv else None)


def _control_views(
    checks: list[AccountingControlResult] | None,
) -> list[AccountingControlView]:
    return [
        AccountingControlView(
            code=c.code,
            status=c.status,
            label=CONTROL_LABELS.get(c.code, c.code),
            expected=_decimal_to_float(c.expected),
            observed=_decimal_to_float(c.observed),
            difference=_decimal_to_float(c.difference),
            tolerance=_decimal_to_float(c.tolerance),
            affected_fields=list(c.affected_fields),
            message=c.message,
        )
        for c in (checks or [])
    ]


def build_rcc_result(
    *,
    document: DocumentSummary,
    extraction: ExtractionSummary,
    dataset: FinancialDataset,
    warnings: list[str] | None = None,
    accounting_checks: list[AccountingControlResult] | None = None,
) -> RccAnalysisResult:
    """Construit la réponse RCC (uniquement les postes EKIP)."""
    resolved: dict[
        str, tuple[float | None, str, float, str | None, FinancialValue | None]
    ] = {}

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
        value, status, conf, source = _pick(dataset, *attrs)
        note = None
        if code == "DETTES_BANCAIRES_MLT" and value is not None:
            mlt_fv = _fv(dataset, "dettes_bancaires_mlt")
            if not _usable(mlt_fv) and _usable(_fv(dataset, "dettes_financieres")):
                note = "Alias dettes de financement (MLT)"
        if code == "CHARGES_INTERETS" and value is not None:
            ff = _fv(dataset, "frais_financiers")
            if not _usable(ff) and _usable(_fv(dataset, "charges_financieres")):
                note = "Proxy charges financières (TOTAL V)"
        if status == "conflicting" and not note:
            note = "Valeur invalidée par un contrôle comptable"
        resolved[code] = (value, status, conf, note, source)

    # TYPE_RESULTAT dérivé du résultat net
    rn_val, rn_status, rn_conf, _, _ = resolved.get(
        "RESULTAT_NET", (None, "missing", 0.0, None, None)
    )
    if rn_val is not None and rn_status in {"confirmed", "derived", "ambiguous"}:
        if rn_val > 0:
            type_note = "Bénéficiaire"
        elif rn_val < 0:
            type_note = "Déficitaire"
        else:
            type_note = "Nul"
        resolved["TYPE_RESULTAT"] = (None, "derived", max(rn_conf, 0.95), type_note, None)
    else:
        resolved["TYPE_RESULTAT"] = (None, "missing", 0.0, None, None)

    fields: list[RccField] = []
    found = 0
    for num, code, label, source_section in RCC_ELEMENTS:
        value, status, conf, note, fv = resolved.get(
            code, (None, "missing", 0.0, None, None)
        )
        if code == "TYPE_RESULTAT":
            if note:
                found += 1
            fields.append(
                RccField(
                    number=num,
                    code=code,
                    label=label,
                    value=None,
                    source=source_section,
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
                source=source_section,
                status=status,
                note=note,
                confidence=conf,
                value_n1=_n1_value(dataset, code),
                evidence=_evidence(fv),
            )
        )

    completeness = round(100.0 * found / len(RCC_ELEMENTS), 1)
    return RccAnalysisResult(
        document=document,
        extraction=extraction,
        fields=fields,
        completeness_pct=completeness,
        warnings=list(warnings or []),
        controls=_control_views(accounting_checks),
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
