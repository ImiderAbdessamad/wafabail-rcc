"""Deterministic scoring ratios built from the shared RCC extraction dataset.

This module deliberately does not invent a final credit score. The reference
workbooks define ratios and indicative thresholds, but not the approved
ratio-to-axis scoring scale or veto policy required for a defensible score.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from decimal import Decimal

from app.schemas.financial_analysis import (
    FinancialDataset,
    FinancialValue,
    RatioComponent,
    RatioResult,
)
from app.schemas.scoring import ScoringInputField, ScoringSummary
from app.services.financial_dataset_builder import usable
from app.services.financial_normalizer import quantize_ratio, safe_divide


_INPUTS: tuple[tuple[str, str], ...] = (
    ("chiffre_affaires", "CHIFFRE_AFFAIRES"),
    ("chiffre_affaires_n1", "CHIFFRE_AFFAIRES_N1"),
    ("resultat_net", "RESULTAT_NET"),
    ("total_bilan", "TOTAL_BILAN"),
    ("fonds_propres", "FONDS_PROPRES"),
    ("dettes_financieres", "DETTES_FINANCIERES"),
    ("caf", "CAF"),
    ("fdr", "FDR"),
    ("tresorerie_nette", "TRESORERIE_NETTE"),
    ("clients", "CLIENTS"),
    ("fournisseurs", "FOURNISSEURS"),
    ("achats", "ACHATS"),
)


def _component(field: FinancialValue) -> RatioComponent:
    return RatioComponent(
        code=field.code,
        label=field.label,
        value=field.value,
        status=field.status,
    )


def _input(field: FinancialValue, canonical_code: str) -> ScoringInputField:
    return ScoringInputField(
        code=canonical_code,
        label=field.label,
        value=field.value,
        unit=field.unit,
        status=field.status,
        provenance=list(field.provenance),
        warnings=list(field.warnings),
    )


def _ratio(
    *,
    code: str,
    label: str,
    formula: str,
    unit: str,
    fields: Sequence[FinancialValue],
    calculate: Callable[[], Decimal | None],
    threshold: str | None,
    classify: Callable[[Decimal], str] | None,
    decimals: str = "0.01",
) -> RatioResult:
    missing = [field.code for field in fields if not usable(field)]
    if missing:
        return RatioResult(
            code=code,
            label=label,
            formula=formula,
            unit=unit,
            components=[_component(field) for field in fields],
            threshold=threshold,
            status="non_calculable",
            warnings=[
                "Ratio non calculable: source absente, conflictuelle ou invalide: "
                + ", ".join(missing)
            ],
        )
    value = quantize_ratio(calculate(), decimals)
    if value is None:
        return RatioResult(
            code=code,
            label=label,
            formula=formula,
            unit=unit,
            components=[_component(field) for field in fields],
            threshold=threshold,
            status="non_calculable",
            warnings=["Ratio non calculable: dénominateur nul."],
        )
    return RatioResult(
        code=code,
        label=label,
        formula=formula,
        value=value,
        unit=unit,
        components=[_component(field) for field in fields],
        threshold=threshold,
        status=classify(value) if classify is not None else "informatif",
    )


def _pct(numerator: FinancialValue, denominator: FinancialValue) -> Decimal | None:
    value = safe_divide(numerator.value, denominator.value)
    return value * Decimal("100") if value is not None else None


def _multiple(numerator: FinancialValue, denominator: FinancialValue) -> Decimal | None:
    return safe_divide(numerator.value, denominator.value)


def _days(numerator: FinancialValue, denominator: FinancialValue) -> Decimal | None:
    value = safe_divide(numerator.value, denominator.value)
    return value * Decimal("360") if value is not None else None


def _economic_return(
    result: FinancialValue,
    equity: FinancialValue,
    debt: FinancialValue,
) -> Decimal | None:
    if equity.value is None or debt.value is None:
        return None
    value = safe_divide(result.value, equity.value + debt.value)
    return value * Decimal("100") if value is not None else None


def _growth(current: FinancialValue, prior: FinancialValue) -> Decimal | None:
    if current.value is None or prior.value is None:
        return None
    value = safe_divide(current.value - prior.value, prior.value)
    return value * Decimal("100") if value is not None else None


def build_scoring_summary(dataset: FinancialDataset) -> ScoringSummary:
    ca = dataset.chiffre_affaires
    ca_n1 = dataset.chiffre_affaires_n1
    rn = dataset.resultat_net
    total = dataset.total_bilan
    fp = dataset.fonds_propres
    debt = dataset.dettes_financieres
    caf = dataset.caf
    fdr = dataset.fdr
    treasury = dataset.tresorerie_nette
    clients = dataset.clients
    suppliers = dataset.fournisseurs
    purchases = dataset.achats

    ratios: list[RatioResult] = [
        _ratio(
            code="autonomie_financiere",
            label="Autonomie financière",
            formula="Fonds propres / Total bilan × 100",
            unit="%",
            fields=[fp, total],
            calculate=lambda: _pct(fp, total),
            threshold="Indicatif: ≥ 20 %",
            classify=lambda value: "conforme" if value >= 20 else "a_surveiller" if value >= 10 else "non_conforme",
        ),
        _ratio(
            code="ratio_endettement",
            label="Ratio d'endettement",
            formula="Dettes financières / Fonds propres",
            unit="x",
            fields=[debt, fp],
            calculate=lambda: _multiple(debt, fp),
            threshold="Indicatif: ≤ 1,5x",
            classify=lambda value: "conforme" if value <= Decimal("1.5") else "a_surveiller" if value <= Decimal("2.5") else "non_conforme",
        ),
        _ratio(
            code="capacite_remboursement",
            label="Capacité de remboursement",
            formula="Dettes financières / CAF",
            unit="années",
            fields=[debt, caf],
            calculate=lambda: _multiple(debt, caf),
            threshold="Indicatif: ≤ 3 ans",
            classify=lambda value: "conforme" if value <= 3 else "a_surveiller" if value <= 5 else "non_conforme",
        ),
        _ratio(
            code="caf_sur_ca",
            label="CAF / chiffre d'affaires",
            formula="CAF / Chiffre d'affaires × 100",
            unit="%",
            fields=[caf, ca],
            calculate=lambda: _pct(caf, ca),
            threshold="Indicatif: ≥ 5 %",
            classify=lambda value: "conforme" if value >= 5 else "a_surveiller" if value >= 0 else "non_conforme",
        ),
        _ratio(
            code="rentabilite_commerciale",
            label="Rentabilité commerciale",
            formula="Résultat net / Chiffre d'affaires × 100",
            unit="%",
            fields=[rn, ca],
            calculate=lambda: _pct(rn, ca),
            threshold="Indicatif: ≥ 5 %",
            classify=lambda value: "conforme" if value >= 5 else "a_surveiller" if value >= 0 else "non_conforme",
        ),
        _ratio(
            code="rentabilite_financiere",
            label="Rentabilité financière",
            formula="Résultat net / Fonds propres × 100",
            unit="%",
            fields=[rn, fp],
            calculate=lambda: _pct(rn, fp),
            threshold="Indicatif: > 10 %",
            classify=lambda value: "conforme" if value > 10 else "a_surveiller" if value >= 0 else "non_conforme",
        ),
        _ratio(
            code="rentabilite_economique",
            label="Rentabilité économique",
            formula="Résultat net / (Fonds propres + Dettes financières) × 100",
            unit="%",
            fields=[rn, fp, debt],
            calculate=lambda: _economic_return(rn, fp, debt),
            threshold="Informatif: comparaison au coût moyen de la dette non configurée",
            classify=None,
        ),
        _ratio(
            code="fdr_sur_ca",
            label="Fonds de roulement / chiffre d'affaires",
            formula="FDR / Chiffre d'affaires × 100",
            unit="%",
            fields=[fdr, ca],
            calculate=lambda: _pct(fdr, ca),
            threshold="Indicatif: > 0 %",
            classify=lambda value: "conforme" if value > 0 else "a_surveiller" if value == 0 else "non_conforme",
        ),
        _ratio(
            code="tresorerie_jours_ca",
            label="Trésorerie en jours de chiffre d'affaires",
            formula="Trésorerie nette / Chiffre d'affaires × 360",
            unit="jours",
            fields=[treasury, ca],
            calculate=lambda: _days(treasury, ca),
            threshold="Indicatif: > 0 jour",
            classify=lambda value: "conforme" if value > 0 else "a_surveiller" if value >= -30 else "non_conforme",
            decimals="0.1",
        ),
        _ratio(
            code="delai_clients",
            label="Délai clients",
            formula="Clients / Chiffre d'affaires × 360",
            unit="jours",
            fields=[clients, ca],
            calculate=lambda: _days(clients, ca),
            threshold="Indicatif: 60 à 90 jours; un délai inférieur n'est pas pénalisé",
            classify=lambda value: "non_conforme" if value < 0 or value > 120 else "a_surveiller" if value > 90 else "conforme",
            decimals="0.1",
        ),
    ]

    customer_days = next(
        ratio.value for ratio in ratios if ratio.code == "delai_clients"
    )
    ratios.append(
        _ratio(
            code="delai_fournisseurs",
            label="Délai fournisseurs",
            formula="Fournisseurs / Achats × 360",
            unit="jours",
            fields=[suppliers, purchases],
            calculate=lambda: _days(suppliers, purchases),
            threshold="Indicatif: ≥ délai clients",
            classify=lambda value: (
                "non_conforme"
                if value < 0
                else "informatif"
                if customer_days is None
                else "conforme"
                if value >= customer_days
                else "a_surveiller"
            ),
            decimals="0.1",
        )
    )
    ratios.append(
        _ratio(
            code="croissance_ca",
            label="Croissance du chiffre d'affaires",
            formula="(CA N - CA N-1) / CA N-1 × 100",
            unit="%",
            fields=[ca, ca_n1],
            calculate=lambda: _growth(ca, ca_n1),
            threshold="Informatif: aucun seuil officiel configuré",
            classify=None,
        )
    )

    inputs = [
        _input(getattr(dataset, attribute), code)
        for attribute, code in _INPUTS
    ]
    calculable = sum(1 for ratio in ratios if ratio.value is not None)
    warnings = [
        "Ratios issus des classeurs de référence; seuils indicatifs et non approuvés.",
        "Score final non calculé: la grille ratio→axes, les pondérations officielles "
        "et les critères bloquants ne sont pas figés.",
        "L'agrégat ACHATS utilise la dérivation achats revendus + achats consommés "
        "lorsque les deux postes sont disponibles.",
    ]
    return ScoringSummary(
        inputs=inputs,
        ratios=ratios,
        calculable_ratio_count=calculable,
        total_ratio_count=len(ratios),
        warnings=warnings,
    )
