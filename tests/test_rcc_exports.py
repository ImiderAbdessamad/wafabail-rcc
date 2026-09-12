"""Regression tests for the auditable RCC CSV/PDF exports."""
from __future__ import annotations

import csv
import io
import unittest

import fitz

from app.schemas.direct_financial_extraction import (
    CompanyInfo,
    DocumentSummary,
    ExerciseInfo,
    ExtractionSummary,
    FinancialPageAudit,
)
from app.schemas.dossier import DossierDetail, FieldOverride
from app.schemas.financial_analysis import RatioResult
from app.schemas.rcc import (
    AccountingControlView,
    FieldEvidence,
    RCC_ELEMENTS,
    RccAnalysisResult,
    RccField,
)
from app.schemas.scoring import ScoringSummary
from app.services.rcc_compliance import build_compliance
from app.services.rcc_export import build_rcc_csv, safe_export_stem
from app.services.rcc_pdf_export import build_rcc_pdf


def sample_dossier() -> DossierDetail:
    fields: list[RccField] = []
    for number, code, label, source in RCC_ELEMENTS:
        if code == "TYPE_RESULTAT":
            fields.append(
                RccField(
                    number=number,
                    code=code,
                    label=label,
                    source=source,
                    status="derived",
                    note="Bénéficiaire",
                    confidence=1,
                )
            )
            continue
        fields.append(
            RccField(
                number=number,
                code=code,
                label=label,
                source=source,
                value=float(number * 1000),
                value_n1=float(number * 900) if code in {"TOTAL_BILAN", "CHIFFRE_AFFAIRES"} else None,
                status="confirmed",
                confidence=0.91,
                evidence=[
                    FieldEvidence(
                        page_number=1 + (number % 2),
                        raw_label=label,
                        raw_value=str(number * 1000),
                        column_name="NET_N",
                        page_type="BILAN_ACTIF",
                        confidence=0.91,
                        extraction_method="docling_table",
                        engine="docling",
                    )
                ],
            )
        )

    ratio = RatioResult(
        code="rentabilite_commerciale",
        label="Rentabilité commerciale",
        formula="Résultat net / chiffre d'affaires x 100",
        value=5.25,
        unit="%",
        status="informatif",
    )
    result = RccAnalysisResult(
        document=DocumentSummary(
            filename="bilan-test.pdf",
            pages_total=2,
            pages_processed=2,
            pages_skipped=0,
            pages_failed=0,
            company=CompanyInfo(raison_sociale="SOCIÉTÉ TEST", ice="001234567000089"),
            exercise=ExerciseInfo(fin="31/12/2025"),
        ),
        extraction=ExtractionSummary(
            model="glm-ocr",
            pipeline="hybrid_financial_v2",
            source_kind="image_only",
            engines=["rapidocr", "docling", "glm-ocr"],
            page_audit=[
                FinancialPageAudit(
                    page_number=1,
                    detected_type="BILAN_ACTIF",
                    orientation=90,
                    extraction_status="processed",
                    extraction_strategy="scan_local_ocr_glm",
                    route="scan_local_ocr_glm",
                    source_kind="scan",
                    local_ocr_chars=1800,
                    local_ocr_confidence=0.88,
                    local_ocr_status="success",
                    docling_status="success",
                )
            ],
        ),
        fields=fields,
        completeness_pct=100,
        controls=[
            AccountingControlView(
                code="bilan_equilibre",
                status="passed",
                label="Total Actif = Total Passif",
                expected=2000,
                observed=2000,
                difference=0,
                message="Équilibre vérifié",
            )
        ],
        scoring=ScoringSummary(
            ratios=[ratio],
            calculable_ratio_count=1,
            total_ratio_count=12,
        ),
    )
    return DossierDetail(
        id="RCC-2026-0099",
        job_id="job-export-test",
        client_name="SOCIÉTÉ TEST",
        ice="001234567000089",
        credit_amount=1_250_000,
        exercice_date="31/12/2025",
        status="pending",
        status_label="À réviser",
        created_at="2026-08-16T10:00:00+00:00",
        updated_at="2026-08-16T10:00:00+00:00",
        filename="bilan-test.pdf",
        has_document=True,
        completeness_pct=100,
        result=result,
        overrides=[
            FieldOverride(
                field_code="CHIFFRE_AFFAIRES",
                original_value=3000,
                corrected_value=7777,
                edited_by="Analyste RCC",
                edited_at="2026-08-16T10:05:00+00:00",
            ),
            # Legacy verification record: NULL must retain the source value.
            FieldOverride(
                field_code="CA_EXPORT",
                original_value=4000,
                corrected_value=None,
                verified=True,
                edited_by="Analyste RCC",
                edited_at="2026-08-16T10:06:00+00:00",
            ),
        ],
    )


class RccExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dossier = sample_dossier()
        self.compliance = build_compliance(self.dossier)

    def test_csv_is_rectangular_auditable_and_uses_effective_values(self) -> None:
        payload = build_rcc_csv(self.dossier, self.compliance)
        text = payload.decode("utf-8-sig")
        lines = text.splitlines()
        self.assertEqual(lines[0], "sep=;")
        rows = list(csv.DictReader(io.StringIO("\n".join(lines[1:])), delimiter=";"))
        self.assertTrue(rows)
        self.assertTrue(all(set(row) for row in rows))

        revenue = next(
            row for row in rows
            if row["record_type"] == "rcc_field"
            and row["code"] == "CHIFFRE_AFFAIRES"
            and row["period"] == "N"
        )
        self.assertEqual(float(revenue["value"]), 7777)
        self.assertEqual(revenue["review_status"], "corrected")

        verified = next(
            row for row in rows
            if row["record_type"] == "rcc_field"
            and row["code"] == "CA_EXPORT"
            and row["period"] == "N"
        )
        self.assertEqual(float(verified["value"]), 4000)
        self.assertEqual(verified["review_status"], "verified")
        self.assertTrue(any(row["record_type"] == "financial_ratio" for row in rows))
        self.assertTrue(any(row["record_type"] == "page_audit" for row in rows))

    def test_pdf_is_valid_paginated_and_contains_all_report_sections(self) -> None:
        payload = build_rcc_pdf(self.dossier, self.compliance)
        self.assertTrue(payload.startswith(b"%PDF"))
        document = fitz.open(stream=payload, filetype="pdf")
        self.assertGreaterEqual(document.page_count, 2)
        text = "\n".join(page.get_text() for page in document)
        document.close()
        # MuPDF may encode common letter pairs as visual ligatures in the
        # searchable text layer; assert stable section wording around them.
        self.assertIn("Rapport d'extraction", text)
        self.assertIn("RCC-2026-0099", text)
        self.assertIn("Postes RCC", text)
        self.assertIn("Ratios de scoring", text)
        self.assertIn("Audit du pipeline", text)
        self.assertIn("Page 1 /", text)

    def test_filename_is_portable(self) -> None:
        self.assertEqual(safe_export_stem(self.dossier), "RCC-RCC-2026-0099-SOCI-T-TEST")


if __name__ == "__main__":
    unittest.main()
