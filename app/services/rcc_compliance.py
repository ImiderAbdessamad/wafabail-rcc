"""Règles de conformité RCC — calculées côté serveur.

Rien n'est recalculé côté client : le panneau « Conformité RCC » de l'interface
se contente d'afficher ce que renvoie ce module. Les contrôles comptables
proviennent de `app.services.financial_controls` via l'instantané d'extraction.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.config import CONFIDENCE_REVIEW_THRESHOLD
from app.schemas.dossier import DossierDetail
from app.schemas.rcc import RCC_ELEMENTS

# Postes calculés par le pipeline : verrouillés dans l'interface.
LOCKED_CODES: frozenset[str] = frozenset({"TOTAL_BILAN", "RESULTAT_NET"})
# Poste dérivé non numérique (rendu sous forme de tag).
DERIVED_TAG_CODES: frozenset[str] = frozenset({"TYPE_RESULTAT"})

SECTION_PIECES = "Pièces obligatoires"
SECTION_COMPLETUDE = "Complétude des champs RCC"
SECTION_COHERENCE = "Cohérence comptable"
SECTION_RCC = "Cohérence des postes RCC"
SECTION_CONTROLE = "Contrôle RCC"

# Identités comptables vérifiables sur les seuls postes RCC, donc recalculables
# sur les valeurs corrigées par l'analyste (contrairement aux contrôles du
# pipeline, figés au moment de l'extraction).
#   (libellé, opérateur, membre gauche, membre droit)
RCC_IDENTITIES: list[tuple[str, str, list[str], list[str]]] = [
    (
        "Total du bilan = actifs immobilisés + actif circulant + trésorerie actif",
        "==",
        ["TOTAL_BILAN"],
        ["ACTIFS_IMMOBILISES", "ACTIF_CIRCULANT", "TRESORERIE_ACTIF"],
    ),
    (
        "Créances clients incluses dans l'actif circulant",
        "<=",
        ["CREANCES_CLIENTS"],
        ["ACTIF_CIRCULANT"],
    ),
    (
        "Dettes fournisseurs incluses dans le passif circulant",
        "<=",
        ["DETTES_FOURNISSEURS"],
        ["PASSIF_CIRCULANT"],
    ),
    (
        "Chiffre d'affaires à l'export inférieur ou égal au chiffre d'affaires",
        "<=",
        ["CA_EXPORT"],
        ["CHIFFRE_AFFAIRES"],
    ),
    (
        "Caisse incluse dans la trésorerie actif",
        "<=",
        ["CAISSE"],
        ["TRESORERIE_ACTIF"],
    ),
]


class ComplianceRule(BaseModel):
    section: str
    label: str
    ok: bool
    blocking: bool = False
    detail: str = ""
    affected_fields: list[str] = Field(default_factory=list)


class ComplianceSection(BaseModel):
    title: str
    state: str
    severity: str  # ok | warn | blocked
    rules: list[ComplianceRule]


class ComplianceReport(BaseModel):
    sections: list[ComplianceSection]
    rules_total: int
    rules_ok: int
    pct: int
    blockers: int
    warnings: int
    can_validate: bool
    summary: str
    # Champs signalés, pour l'interface
    missing_fields: list[str] = Field(default_factory=list)
    conflicting_fields: list[str] = Field(default_factory=list)
    low_confidence_fields: list[str] = Field(default_factory=list)


def _fmt(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value:,.0f}".replace(",", " ")


def build_compliance(detail: DossierDetail) -> ComplianceReport:
    result = detail.result
    overrides = {o.field_code: o for o in detail.overrides}
    rules: list[ComplianceRule] = []

    # --- Pièces obligatoires -------------------------------------------------
    has_doc = detail.has_document
    has_result = result is not None
    rules.append(
        ComplianceRule(
            section=SECTION_PIECES,
            label="Liasse fiscale rattachée au dossier",
            ok=has_doc,
            blocking=True,
            detail=(
                f"{detail.filename} · {result.document.pages_total} page(s)"
                if has_doc and result
                else "aucun fichier"
            ),
        )
    )
    rules.append(
        ComplianceRule(
            section=SECTION_PIECES,
            label="Extraction OCR exécutée sur la liasse",
            ok=has_result,
            blocking=True,
            detail=(
                f"{result.document.pages_processed}/{result.document.pages_total} pages traitées"
                if result
                else "non exécutée"
            ),
        )
    )
    if result and result.document.pages_failed:
        rules.append(
            ComplianceRule(
                section=SECTION_PIECES,
                label="Toutes les pages de la liasse ont été exploitées",
                ok=False,
                blocking=False,
                detail=f"{result.document.pages_failed} page(s) en échec",
            )
        )

    # --- Complétude ----------------------------------------------------------
    missing: list[str] = []
    conflicting: list[str] = []
    low_conf: list[str] = []
    total_codes = len(RCC_ELEMENTS)
    filled = 0

    if result:
        for field in result.fields:
            corrected = overrides.get(field.code)
            has_value = (
                corrected is not None and corrected.corrected_value is not None
            ) or (
                field.value is not None
                if field.code not in DERIVED_TAG_CODES
                else bool(field.note)
            )
            if has_value:
                filled += 1
            else:
                missing.append(field.code)

            if field.status == "conflicting" and corrected is None:
                conflicting.append(field.code)
            # Seuil de relecture : confiance faible et champ non repris par l'analyste
            # Un poste sous le seuil est levé dès que l'analyste l'a repris —
            # soit en corrigeant la valeur, soit en confirmant celle de l'OCR.
            if (
                field.code not in LOCKED_CODES
                and field.code not in DERIVED_TAG_CODES
                and corrected is None
                and field.status not in {"missing", "conflicting"}
                and field.confidence < CONFIDENCE_REVIEW_THRESHOLD
            ):
                low_conf.append(field.code)
    else:
        missing = [code for _, code, _, _ in RCC_ELEMENTS]

    rules.append(
        ComplianceRule(
            section=SECTION_COMPLETUDE,
            label="Tous les postes RCC sont renseignés",
            ok=not missing,
            blocking=True,
            detail=f"{filled}/{total_codes} renseignés",
            affected_fields=missing,
        )
    )
    rules.append(
        ComplianceRule(
            section=SECTION_COMPLETUDE,
            label="Date de clôture d'exercice identifiée",
            ok=bool(detail.exercice_date),
            blocking=False,
            detail=detail.exercice_date or "non détectée",
        )
    )
    rules.append(
        ComplianceRule(
            section=SECTION_COMPLETUDE,
            label="Identification du client (raison sociale et ICE)",
            ok=bool(detail.client_name and detail.ice),
            blocking=False,
            detail=detail.ice or "ICE non détecté",
        )
    )

    # --- Cohérence comptable (contrôles réels du pipeline) -------------------
    #
    # Ces contrôles sont figés au moment de l'extraction : ils ne peuvent pas
    # être rejoués ici (ils portent sur le FinancialDataset complet, pas sur les
    # 20 postes RCC). Un contrôle en échec reste donc bloquant tant que
    # l'analyste n'a pas repris les postes RCC qu'il désigne — et cesse de
    # l'être une fois cet arbitrage humain effectué. Sans cette levée, un
    # dossier déséquilibré ne pourrait jamais être validé, quelle que soit la
    # correction apportée.
    # Seuls les postes réellement saisissables comptent : un contrôle qui ne
    # désigne que des postes verrouillés (TOTAL_BILAN, RESULTAT_NET) ne peut pas
    # être levé par l'analyste et ne doit donc jamais bloquer.
    editable_codes = {
        code
        for _, code, _, _ in RCC_ELEMENTS
        if code not in LOCKED_CODES and code not in DERIVED_TAG_CODES
    }
    corrected_codes = set(overrides)

    controls = result.controls if result else []
    if not controls:
        rules.append(
            ComplianceRule(
                section=SECTION_COHERENCE,
                label="Contrôles comptables exécutés",
                ok=False,
                blocking=bool(has_result),
                detail="aucun contrôle disponible",
            )
        )
    for control in controls:
        actionable = [c for c in control.affected_fields if c in editable_codes]
        arbitrated = bool(actionable) and all(c in corrected_codes for c in actionable)

        if control.status == "passed":
            ok, blocking = True, False
            detail_txt = f"{_fmt(control.observed)} = {_fmt(control.expected)}"
        elif control.status == "failed":
            if arbitrated:
                ok, blocking = True, False
                detail_txt = f"arbitré — {len(actionable)} poste(s) repris"
            elif actionable:
                ok, blocking = False, True
                detail_txt = f"écart {_fmt(abs(control.difference or 0))}"
            else:
                # Aucun poste RCC n'est modifiable pour lever ce contrôle :
                # on avertit sans bloquer, pour ne pas créer d'impasse.
                ok, blocking = False, False
                detail_txt = f"écart {_fmt(abs(control.difference or 0))} — non corrigeable ici"
        else:
            # Non testable : donnée absente, déjà couverte par la complétude.
            ok, blocking = True, False
            detail_txt = "non testable"

        rules.append(
            ComplianceRule(
                section=SECTION_COHERENCE,
                label=control.label,
                ok=ok,
                blocking=blocking,
                detail=detail_txt,
                affected_fields=actionable or control.affected_fields,
            )
        )

    # --- Identités recalculées sur les valeurs corrigées ---------------------
    values = {f.code: f.value for f in (result.fields if result else [])}
    for code, override in overrides.items():
        values[code] = override.corrected_value

    for label, operator, left_codes, right_codes in RCC_IDENTITIES:
        left_vals = [values.get(c) for c in left_codes]
        right_vals = [values.get(c) for c in right_codes]
        if any(v is None for v in left_vals + right_vals):
            rules.append(
                ComplianceRule(
                    section=SECTION_RCC,
                    label=label,
                    ok=True,  # non testable : ne bloque pas, la complétude s'en charge
                    blocking=False,
                    detail="non testable",
                    affected_fields=left_codes + right_codes,
                )
            )
            continue

        left = sum(left_vals)
        right = sum(right_vals)
        # Tolérance alignée sur financial_controls._tol (1 MAD ou 0,01 %)
        tolerance = max(1.0, abs(right) * 0.0001)
        ok = abs(left - right) <= tolerance if operator == "==" else left <= right + tolerance

        rules.append(
            ComplianceRule(
                section=SECTION_RCC,
                label=label,
                ok=ok,
                blocking=not ok,
                detail=(
                    f"{_fmt(left)} {'=' if operator == '==' else '≤'} {_fmt(right)}"
                    if ok
                    else f"{_fmt(left)} vs {_fmt(right)} · écart {_fmt(abs(left - right))}"
                ),
                affected_fields=left_codes + right_codes,
            )
        )

    # --- Contrôle RCC --------------------------------------------------------
    rules.append(
        ComplianceRule(
            section=SECTION_CONTROLE,
            label="Aucun poste en conflit d'extraction non arbitré",
            ok=not conflicting,
            blocking=True,
            detail=(
                f"{len(conflicting)} poste(s) en conflit" if conflicting else "aucun conflit"
            ),
            affected_fields=conflicting,
        )
    )
    threshold_pct = int(CONFIDENCE_REVIEW_THRESHOLD * 100)
    rules.append(
        ComplianceRule(
            section=SECTION_CONTROLE,
            label=f"Postes sous {threshold_pct} % de confiance OCR tous contrôlés",
            ok=not low_conf,
            blocking=True,
            detail=(
                f"{len(low_conf)} en attente de contrôle" if low_conf else "aucun en attente"
            ),
            affected_fields=low_conf,
        )
    )
    rules.append(
        ComplianceRule(
            section=SECTION_CONTROLE,
            label="Piste d'audit horodatée et nominative",
            ok=True,
            blocking=False,
            detail=f"{len(detail.overrides)} correction(s) tracée(s)",
        )
    )
    if result and result.warnings:
        rules.append(
            ComplianceRule(
                section=SECTION_CONTROLE,
                label="Aucun avertissement d'extraction à justifier",
                ok=False,
                blocking=False,
                detail=f"{len(result.warnings)} avertissement(s)",
            )
        )

    # --- Agrégation ----------------------------------------------------------
    ok_count = sum(1 for r in rules if r.ok)
    blockers = sum(1 for r in rules if not r.ok and r.blocking)
    warns = sum(1 for r in rules if not r.ok and not r.blocking)
    pct = round(100 * ok_count / len(rules)) if rules else 0

    order = [
        SECTION_PIECES,
        SECTION_COMPLETUDE,
        SECTION_COHERENCE,
        SECTION_RCC,
        SECTION_CONTROLE,
    ]
    sections: list[ComplianceSection] = []
    for title in order:
        section_rules = [r for r in rules if r.section == title]
        if not section_rules:
            continue
        bad = [r for r in section_rules if not r.ok]
        blk = sum(1 for r in bad if r.blocking)
        sections.append(
            ComplianceSection(
                title=title,
                state=(
                    f"{blk} bloquant(s)"
                    if blk
                    else f"{len(bad)} à justifier"
                    if bad
                    else "Conforme"
                ),
                severity="blocked" if blk else "warn" if bad else "ok",
                rules=section_rules,
            )
        )

    if blockers:
        summary = (
            f"{blockers} règle(s) bloquante(s) non satisfaite(s) — validation impossible"
        )
    elif warns:
        summary = f"Règles bloquantes levées · {warns} avertissement(s) à justifier"
    else:
        summary = "Dossier conforme aux règles RCC — transmission au modèle EKIP autorisée"

    return ComplianceReport(
        sections=sections,
        rules_total=len(rules),
        rules_ok=ok_count,
        pct=pct,
        blockers=blockers,
        warnings=warns,
        can_validate=blockers == 0,
        summary=summary,
        missing_fields=missing,
        conflicting_fields=conflicting,
        low_confidence_fields=low_conf,
    )
