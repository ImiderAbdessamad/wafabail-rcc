"""Jeu de démonstration : dossiers RCC réalistes sans appeler le pipeline GLM.

Utile pour recetter l'interface (états vides, incohérences, liasse manquante)
quand Ollama n'est pas disponible.

    python scripts/seed_demo.py            # ajoute les dossiers de démo
    python scripts/seed_demo.py --reset    # vide la base avant d'insérer
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz  # PyMuPDF — génère un PDF lisible pour la visionneuse

from app.db import cursor, init_db
from app.schemas.direct_financial_extraction import (
    CompanyInfo,
    DocumentSummary,
    ExerciseInfo,
    ExtractionSummary,
)
from app.schemas.rcc import (
    RCC_ELEMENTS,
    AccountingControlView,
    FieldEvidence,
    RccAnalysisResult,
    RccField,
)
from app.services import dossier_store
from app.services.rcc_mapper import CONTROL_LABELS

# (code, valeur, confiance, page, libellé source)
BASE_VALUES: dict[str, tuple[float | None, float, int, str]] = {
    "ACTIFS_IMMOBILISES": (8_420_000, 0.96, 1, "TOTAL ACTIF IMMOBILISE"),
    "TOTAL_BILAN": (22_220_000, 0.97, 1, "TOTAL GENERAL ACTIF"),
    "CHIFFRE_AFFAIRES": (34_980_000, 0.97, 3, "Chiffre d'affaires"),
    "CA_EXPORT": (421_000, 0.58, 3, "dont a l'export"),
    "DETTES_BANCAIRES_MLT": (3_250_000, 0.96, 2, "Dettes de financement"),
    "DETTES_BANCAIRES_CT": (2_210_000, 0.92, 2, "Credits de tresorerie"),
    "PASSIF_CIRCULANT": (9_940_000, 0.71, 2, "TOTAL PASSIF CIRCULANT"),
    "DETTES_FOURNISSEURS": (5_120_000, 0.94, 2, "Fournisseurs et comptes rattaches"),
    "COMPTE_COURANT_ASSOCIES": (1_450_000, 0.95, 2, "Comptes courants des associes"),
    "TRESORERIE_PASSIF": (2_210_000, 0.92, 2, "TRESORERIE PASSIF"),
    "ACTIF_CIRCULANT": (12_655_000, 0.93, 1, "TOTAL ACTIF CIRCULANT"),
    "CREANCES_CLIENTS": (6_120_000, 0.74, 1, "Clients et comptes rattaches"),
    "TRESORERIE_ACTIF": (1_145_000, 0.97, 1, "TRESORERIE ACTIF"),
    "CAISSE": (85_000, 0.95, 1, "Caisses, regies d'avances"),
    "ACHATS_REVENDUS": (21_340_000, 0.96, 3, "Achats revendus de marchandises"),
    "ACHATS_CONSOMMES": (4_180_000, 0.91, 3, "Achats consommes de matieres"),
    "AUTRES_CHARGES_EXTERNES": (5_680_000, 0.93, 3, "Autres charges externes"),
    "CHARGES_INTERETS": (640_000, 0.66, 4, "Charges d'interets"),
    "RESULTAT_NET": (1_148_000, 0.95, 4, "RESULTAT NET"),
}

N1_VALUES = {
    "CHIFFRE_AFFAIRES": 30_140_000,
    "TOTAL_BILAN": 19_760_000,
    "RESULTAT_NET": 892_000,
    "DETTES_BANCAIRES_MLT": 2_480_000,
}


def build_result(
    *,
    filename: str,
    company: CompanyInfo,
    exercice_fin: str,
    missing: set[str] = frozenset(),
    conflicting: set[str] = frozenset(),
    balanced: bool = True,
) -> RccAnalysisResult:
    fields: list[RccField] = []
    found = 0

    for num, code, label, source in RCC_ELEMENTS:
        if code == "TYPE_RESULTAT":
            net = BASE_VALUES["RESULTAT_NET"][0]
            note = "Bénéficiaire" if net and net > 0 else "Déficitaire"
            fields.append(
                RccField(number=num, code=code, label=label, source=source,
                         status="derived", note=note, confidence=0.95)
            )
            found += 1
            continue

        value, confidence, page, raw_label = BASE_VALUES[code]

        if code in missing:
            fields.append(RccField(number=num, code=code, label=label, source=source,
                                   status="missing", confidence=0.0))
            continue
        if code in conflicting:
            fields.append(
                RccField(
                    number=num, code=code, label=label, source=source,
                    status="conflicting", confidence=0.0,
                    note="Valeur invalidée par un contrôle comptable",
                    evidence=[FieldEvidence(page_number=page, raw_label=raw_label,
                                            raw_value=f"{value:,.0f}".replace(",", " "),
                                            column_name="NET N", page_type="BILAN_PASSIF",
                                            confidence=confidence)],
                )
            )
            continue

        found += 1
        fields.append(
            RccField(
                number=num, code=code, label=label, source=source,
                value=value, status="confirmed", confidence=confidence,
                value_n1=N1_VALUES.get(code),
                evidence=[
                    FieldEvidence(
                        page_number=page, raw_label=raw_label,
                        raw_value=f"{value:,.0f}".replace(",", " "),
                        column_name="NET N",
                        page_type="BILAN_ACTIF" if page == 1 else "BILAN_PASSIF" if page == 2 else "CPC",
                        confidence=confidence,
                        source_excerpt=f"{raw_label} .... {value:,.0f}".replace(",", " "),
                    )
                ],
            )
        )

    total_actif = 22_220_000.0
    total_passif = total_actif if balanced else total_actif - 340_000
    controls = [
        AccountingControlView(
            code="bilan_equilibre",
            status="passed" if balanced else "failed",
            label=CONTROL_LABELS["bilan_equilibre"],
            expected=total_actif, observed=total_passif,
            difference=total_passif - total_actif, tolerance=2222.0,
            affected_fields=["TOTAL_BILAN", "PASSIF_CIRCULANT"],
            message="Actif ≈ Passif." if balanced else "Déséquilibre actif / passif au-delà de la tolérance.",
        ),
        AccountingControlView(
            code="tresorerie_nette", status="passed", label=CONTROL_LABELS["tresorerie_nette"],
            expected=-1_065_000.0, observed=-1_065_000.0, difference=0.0,
            affected_fields=["TRESORERIE_ACTIF", "TRESORERIE_PASSIF"],
            message="Trésorerie nette cohérente.",
        ),
        AccountingControlView(
            code="resultat_net", status="passed", label=CONTROL_LABELS["resultat_net"],
            expected=1_148_000.0, observed=1_148_000.0, difference=0.0,
            affected_fields=["RESULTAT_NET"], message="Résultat net cohérent.",
        ),
        AccountingControlView(
            code="resultat_exploitation", status="not_testable",
            label=CONTROL_LABELS["resultat_exploitation"],
            affected_fields=["RESULTAT_EXPLOITATION"],
            message="Données insuffisantes pour le contrôle.",
        ),
    ]

    return RccAnalysisResult(
        document=DocumentSummary(
            filename=filename, pages_total=4, pages_processed=4,
            pages_skipped=0, pages_failed=0, company=company,
            exercise=ExerciseInfo(debut="01/01/2025", fin=exercice_fin,
                                  label=f"01/01/2025 → {exercice_fin}"),
        ),
        extraction=ExtractionSummary(model="glm4v", warnings=[]),
        fields=fields,
        completeness_pct=round(100.0 * found / len(RCC_ELEMENTS), 1),
        warnings=[] if balanced else ["Contrôle comptable : bilan_equilibre — Déséquilibre actif / passif."],
        controls=controls,
    )


def make_pdf(path: Path, company: CompanyInfo, result: RccAnalysisResult) -> None:
    """Liasse de démonstration à 4 pages, lisible dans la visionneuse."""
    doc = fitz.open()
    sections = [
        ("BILAN — ACTIF", [f for f in result.fields if f.source == "Bilan Actif"]),
        ("BILAN — PASSIF", [f for f in result.fields if f.source in {"Bilan Passif", "Bilan"}]),
        ("COMPTE DE PRODUITS ET CHARGES", [f for f in result.fields if f.source == "CPC"][:4]),
        ("COMPTE DE PRODUITS ET CHARGES (suite)", [f for f in result.fields if f.source == "CPC"][4:]),
    ]
    for index, (title, fields) in enumerate(sections, start=1):
        page = doc.new_page()
        page.insert_text((56, 60), company.raison_sociale or "", fontsize=13, fontname="hebo")
        page.insert_text((56, 76), f"ICE {company.ice or '—'} · {company.ville or ''}", fontsize=8)
        page.insert_text((360, 60), title, fontsize=10, fontname="hebo")
        page.insert_text((360, 74), "Exercice du 01/01/2025 au 31/12/2025", fontsize=8)
        page.insert_text((360, 86), "Montants en dirhams", fontsize=8)
        page.draw_line(fitz.Point(56, 96), fitz.Point(540, 96))

        y = 126
        for field in fields:
            page.insert_text((56, y), field.label[:52], fontsize=9)
            amount = "—" if field.value is None else f"{field.value:,.0f}".replace(",", " ")
            page.insert_text((470, y), amount, fontsize=9, fontname="hebo")
            page.draw_line(fitz.Point(56, y + 5), fitz.Point(540, y + 5), color=(.85, .87, .9))
            y += 24

        page.insert_text((56, 790), f"Page {index}/4 — liasse fiscale modèle normal", fontsize=7)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    doc.close()


DEMO = [
    dict(
        company=CompanyInfo(raison_sociale="SOTRAMEX INDUSTRIE SARL", ice="001874552000073",
                            ville="Casablanca", identifiant_fiscal="40218755"),
        filename="Bilan_SOTRAMEX_2025.pdf", credit=4_200_000,
        sector="Industrie métallurgique · Casablanca", exercice="31/12/2025",
        balanced=False, conflicting={"PASSIF_CIRCULANT"}, missing=set(), with_pdf=True,
    ),
    dict(
        company=CompanyInfo(raison_sociale="TRANSPORT ATLAS LOGISTIQUE SA", ice="001996304000058",
                            ville="Tanger", identifiant_fiscal="15330214"),
        filename="Bilan_ATLAS_2025.pdf", credit=2_340_000,
        sector="Transport & logistique · Tanger", exercice="31/12/2025",
        balanced=True, conflicting=set(), missing=set(), with_pdf=True,
    ),
    dict(
        company=CompanyInfo(raison_sociale="GROUPE ALMADINA DISTRIBUTION", ice="002145889000041",
                            ville="Rabat", identifiant_fiscal="22087441"),
        filename="Liasse_ALMADINA_2025.pdf", credit=7_850_000,
        sector="Distribution alimentaire · Rabat", exercice="31/12/2025",
        balanced=True, conflicting=set(), missing={"CA_EXPORT", "CHARGES_INTERETS"}, with_pdf=True,
    ),
    dict(
        company=CompanyInfo(raison_sociale="AGRIVERT SEMENCES SARL", ice="002310774000012",
                            ville="Meknès", identifiant_fiscal="33901287"),
        filename=None, credit=1_780_000,
        sector="Agro-industrie · Meknès", exercice="30/06/2025",
        balanced=True, conflicting=set(), missing=set(), with_pdf=False,  # liasse manquante
    ),
]


def reset() -> None:
    with cursor(commit=True) as cur:
        cur.execute("DELETE FROM field_overrides")
        cur.execute("DELETE FROM dossier_events")
        cur.execute("DELETE FROM dossiers")
    print("Base vidée.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="vide les dossiers existants")
    args = parser.parse_args()

    init_db()
    if args.reset:
        reset()

    for index, spec in enumerate(DEMO):
        if not spec["with_pdf"]:
            # Dossier sans liasse : état « Liasse manquante »
            detail = dossier_store.create_dossier(
                job_id=None, result=None,
                client_name=spec["company"].raison_sociale, ice=spec["company"].ice,
                credit_amount=spec["credit"], exercice_date=spec["exercice"],
                sector=spec["sector"], actor="Import EKIP",
            )
            print(f"  {detail.id}  {detail.client_name}  (liasse manquante)")
            continue

        result = build_result(
            filename=spec["filename"], company=spec["company"],
            exercice_fin=spec["exercice"], missing=spec["missing"],
            conflicting=spec["conflicting"], balanced=spec["balanced"],
        )
        job_id = f"demo{index:04d}{datetime.now(timezone.utc):%H%M%S}"
        make_pdf(dossier_store.pdf_path_for_job(job_id), spec["company"], result)

        detail = dossier_store.create_dossier(
            job_id=job_id, result=result, credit_amount=spec["credit"],
            sector=spec["sector"], actor="Moteur OCR",
        )
        print(f"  {detail.id}  {detail.client_name}  complétude {detail.completeness_pct} %")

    print("\nJeu de démonstration créé. Lancez :")
    print("  uvicorn main:app --reload --port 8001")


if __name__ == "__main__":
    main()
