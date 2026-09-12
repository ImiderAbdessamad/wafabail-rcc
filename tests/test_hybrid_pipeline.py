"""Tests unitaires du routeur OCR local, sans Ollama ni modèles téléchargés."""
from __future__ import annotations

import io
import sys
import unittest
from unittest.mock import AsyncMock, patch
from pathlib import Path

import fitz
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.docling_financial_adapter import candidates_from_docling_markdown
from app.services.financial_document_router import (
    LocalOcrObservation,
    assess_orientation,
    classify_document_source,
    normalized_pages_to_pdf,
    prepare_financial_page,
)
from app.schemas.direct_financial_extraction import (
    DirectFinancialCandidate,
    DirectFinancialEvidence,
)
from app.services.direct_financial_resolver import build_dataset_from_direct_candidates


def png_bytes(*, inverted: bool = False) -> bytes:
    background = "black" if inverted else "white"
    foreground = "white" if inverted else "black"
    image = Image.new("RGB", (900, 1250), background)
    draw = ImageDraw.Draw(image)
    draw.text((70, 80), "BILAN ACTIF EXERCICE N N-1", fill=foreground)
    for y in range(160, 1050, 55):
        draw.line((70, y, 830, y), fill=foreground, width=3)
    for x in (70, 480, 650, 830):
        draw.line((x, 160, x, 1050), fill=foreground, width=3)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class HybridRouterTests(unittest.TestCase):
    def test_document_source_routing(self) -> None:
        native = "Bilan actif exercice clients immobilisations " * 4
        self.assertEqual(classify_document_source([native] * 4), "born_digital")
        self.assertEqual(classify_document_source(["", "", ""]), "image_only")
        self.assertEqual(classify_document_source([native, "", ""]), "hybrid")
        self.assertEqual(classify_document_source(["%$#@!" * 30] * 4), "image_only")

    def test_native_page_does_not_reapply_pdf_rotation(self) -> None:
        result = assess_orientation(png_bytes(), native_text="bilan actif " * 20)
        self.assertEqual(result.selected_angle, 0)
        self.assertEqual(result.method, "native_text_rendered_orientation")

    def test_semantic_ocr_distinguishes_90_from_270(self) -> None:
        def fake_runner(_image: bytes, angle: int) -> LocalOcrObservation:
            if angle == 90:
                return LocalOcrObservation(
                    angle=angle,
                    text="Bilan actif total exercice clients",
                    confidence=0.96,
                    word_count=15,
                    numeric_count=8,
                    keyword_hits=5,
                    score=45.0,
                    status="success",
                )
            return LocalOcrObservation(
                angle=angle,
                text="x",
                confidence=0.2,
                word_count=1,
                score=1.0,
                status="success",
            )

        result = assess_orientation(png_bytes(), ocr_runner=fake_runner)
        self.assertEqual(result.selected_angle, 90)
        self.assertEqual(result.method, "rapidocr_semantic_cascade")
        self.assertGreater(result.confidence, 0.9)

    def test_inverted_page_is_corrected_and_audited(self) -> None:
        luminance_seen_by_orientation: list[float] = []

        def corrected_runner(image_bytes: bytes, angle: int) -> LocalOcrObservation:
            with Image.open(io.BytesIO(image_bytes)) as sample:
                gray = Image.Image.convert(sample, "L").resize((1, 1))
                luminance_seen_by_orientation.append(float(gray.getpixel((0, 0))))
            return LocalOcrObservation(
                angle=angle,
                text="bilan actif" if angle == 0 else "",
                confidence=0.9 if angle == 0 else 0.1,
                word_count=8 if angle == 0 else 0,
                keyword_hits=2 if angle == 0 else 0,
                score=20.0 if angle == 0 else 0.0,
                status="success",
            )

        prepared = prepare_financial_page(
            page_number=1,
            image_bytes=png_bytes(inverted=True),
            native_text="",
            source_kind="image_only",
            ocr_runner=corrected_runner,
        )
        self.assertTrue(prepared.visual.inverted)
        self.assertTrue(all(value > 125 for value in luminance_seen_by_orientation))
        self.assertTrue(any("Polarit" in warning for warning in prepared.warnings))
        with Image.open(io.BytesIO(prepared.extraction_image)) as image:
            self.assertGreater(sum(Image.Image.convert(image, "L").getextrema()), 200)

    def test_final_normalized_ocr_replaces_sparse_orientation_text(self) -> None:
        prepared = prepare_financial_page(
            page_number=1,
            image_bytes=png_bytes(),
            native_text="",
            source_kind="image_only",
            ocr_runner=lambda _image, angle: LocalOcrObservation(
                angle=angle,
                text="bilan" if angle == 0 else "",
                confidence=0.55,
                word_count=8 if angle == 0 else 0,
                keyword_hits=1 if angle == 0 else 0,
                score=8.0 if angle == 0 else 0.0,
                status="success",
            ),
            text_ocr_runner=lambda _image: LocalOcrObservation(
                angle=0,
                text="Bilan actif total exercice clients 1 200,00",
                confidence=0.93,
                word_count=9,
                numeric_count=1,
                keyword_hits=5,
                score=40.0,
                status="success",
            ),
        )
        self.assertIn("clients", prepared.local_ocr_text.lower())
        self.assertEqual(prepared.local_ocr_confidence, 0.93)
        self.assertEqual(prepared.local_ocr_status, "success")

    def test_normalized_pages_create_valid_pdf(self) -> None:
        prepared = prepare_financial_page(
            page_number=1,
            image_bytes=png_bytes(),
            native_text="native " * 30,
            source_kind="born_digital",
        )
        pdf = normalized_pages_to_pdf([prepared, prepared])
        document = fitz.open(stream=pdf, filetype="pdf")
        try:
            self.assertEqual(document.page_count, 2)
        finally:
            document.close()


class DoclingCandidateTests(unittest.TestCase):
    def test_actif_table_uses_net_and_preserves_n1(self) -> None:
        markdown = """
| ACTIF | Libellé | Brut exercice | Amortissements et provisions | Net exercice | Net exercice précédent |
|---|---|---:|---:|---:|---:|
|  | TOTAL I (A+B+C+D+E) | 1 200,00 | 200,00 | 1 000,00 | 900,00 |
|  | Clients et comptes rattachés | 500,00 | 0,00 | 500,00 | 450,00 |
|  | TOTAL GENERAL I+II+III | 2 500,00 | 200,00 | 2 300,00 | 2 100,00 |
"""
        candidates = candidates_from_docling_markdown(
            {2: markdown},
            page_types={2: "BILAN_ACTIF"},
            scanned=False,
        )
        values = {(item.field_code, item.period, item.raw_value) for item in candidates}
        self.assertIn(("ACTIFS_IMMOBILISES", "N", "1 000,00"), values)
        self.assertIn(("ACTIFS_IMMOBILISES", "N_MINUS_1", "900,00"), values)
        self.assertIn(("CLIENTS", "N", "500,00"), values)
        self.assertIn(("TOTAL_ACTIF", "N", "2 300,00"), values)
        self.assertTrue(all(item.evidence.extraction_method == "docling_table" for item in candidates))

    def test_passif_table_maps_rcc_rows(self) -> None:
        markdown = """
| Section | PASSIF | EXERCICE | EXERCICE PRECEDENT |
|---|---|---:|---:|
| CAPITAUX PROPRES | Total des capitaux propres | 16 974 102,47 | 17 215 221,59 |
| DETTES DU PASSIF CIRCULANT | Fournisseurs et comptes rattachés | 12 000,00 | 10 000,00 |
|  | Comptes d'associés | 3 380 040,43 | 9 213,92 |
| DETTES DU PASSIF CIRCULANT | Etat | 53 363,05 | 437 693,00 |
|  | TOTAL GENERAL I+II+III | 20 509 380,37 | 17 662 128,51 |
"""
        candidates = candidates_from_docling_markdown(
            {4: markdown},
            page_types={4: "BILAN_PASSIF"},
        )
        codes = {item.field_code for item in candidates if item.period == "N"}
        self.assertTrue(
            {"FONDS_PROPRES", "FOURNISSEURS", "COMPTE_COURANT_ASSOCIES", "TOTAL_PASSIF"}
            <= codes
        )
        self.assertFalse(
            any(item.field_code == "PASSIF_CIRCULANT" and item.raw_value == "53 363,05" for item in candidates)
        )

    def test_ragged_docling_row_is_rejected_instead_of_shifting_periods(self) -> None:
        markdown = """
| ACTIF | Libellé | Brut exercice | Amortissements et provisions | Net exercice | Net exercice précédent |
|---|---|---:|---:|---:|---:|
|  | Clients et comptes rattachés | 500,00 | 0,00 | 500,00 | 450,00 | note décalée |
"""
        candidates = candidates_from_docling_markdown(
            {1: markdown},
            page_types={1: "BILAN_ACTIF"},
        )
        self.assertEqual(candidates, [])

    def test_consensus_preserves_both_engine_provenances(self) -> None:
        base = DirectFinancialCandidate(
            field_code="CLIENTS",
            raw_value="500,00",
            period="N",
            nature="DETAIL",
            confidence=0.86,
            evidence=DirectFinancialEvidence(
                page_number=2,
                page_type="BILAN_ACTIF",
                raw_label="Clients et comptes rattachés",
                column_name="Net exercice",
                column_role="NET_N",
                source_excerpt="Clients et comptes rattachés|500,00",
                extraction_method="glm_direct_vision",
                engine="glm-test",
            ),
        )
        docling = base.model_copy(
            update={
                "evidence": base.evidence.model_copy(
                    update={
                        "extraction_method": "docling_table",
                        "engine": "docling+tableformer",
                    }
                )
            }
        )
        dataset = build_dataset_from_direct_candidates([base, docling])
        self.assertEqual(dataset.clients.status, "confirmed")
        self.assertEqual(dataset.clients.value, 500)
        self.assertEqual(
            {item.extraction_method for item in dataset.clients.provenance},
            {"glm_direct_vision", "docling_table"},
        )
        self.assertTrue(any("Consensus" in warning for warning in dataset.clients.warnings))

    def test_equivalent_engine_disagreement_is_conflicting(self) -> None:
        first = DirectFinancialCandidate(
            field_code="CLIENTS",
            raw_value="500,00",
            period="N",
            nature="DETAIL",
            confidence=0.86,
            evidence=DirectFinancialEvidence(
                page_number=2,
                page_type="BILAN_ACTIF",
                raw_label="Clients et comptes rattachés",
                column_name="Net exercice",
                column_role="NET_N",
                source_excerpt="Clients|500,00",
                extraction_method="glm_direct_vision",
                engine="glm-test",
            ),
        )
        second = first.model_copy(
            update={
                "raw_value": "550,00",
                "evidence": first.evidence.model_copy(
                    update={
                        "source_excerpt": "Clients|550,00",
                        "extraction_method": "docling_table",
                        "engine": "docling+tableformer",
                    }
                ),
            }
        )
        dataset = build_dataset_from_direct_candidates([first, second])
        self.assertEqual(dataset.clients.status, "conflicting")
        self.assertIsNone(dataset.clients.value)


class HybridPipelineIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_analyze_routes_native_page_without_network(self) -> None:
        document = fitz.open()
        page = document.new_page(width=595, height=842)
        page.insert_text((70, 80), "BILAN ACTIF")
        page.insert_text((70, 120), "TOTAL GENERAL I+II+III 2 300,00")
        page.insert_text((70, 160), "IMMOBILISATIONS CORPORELLES")
        page.insert_text((70, 200), "CREANCES DE L ACTIF CIRCULANT TRESORERIE ACTIF")
        pdf_bytes = document.tobytes()
        document.close()

        candidate = DirectFinancialCandidate(
            field_code="TOTAL_ACTIF",
            raw_value="2 300,00",
            period="N",
            nature="GRAND_TOTAL",
            confidence=0.9,
            evidence=DirectFinancialEvidence(
                page_number=1,
                page_type="BILAN_ACTIF",
                raw_label="TOTAL GENERAL I+II+III",
                column_name="Net exercice",
                column_role="NET_N",
                source_excerpt="TOTAL GENERAL I+II+III|2 300,00",
                engine="test_glm",
            ),
        )

        from app.services import direct_financial_extraction_pipeline as pipeline

        with (
            patch.object(pipeline, "warmup_direct_financial_model", new=AsyncMock()),
            patch.object(
                pipeline,
                "_extract_once",
                new=AsyncMock(return_value=([candidate], 12, "full_page", None)),
            ),
            patch.object(pipeline, "DOCLING_ENABLED", False),
            patch.object(pipeline, "DIRECT_FINANCIAL_PAGE_DELAY_SECONDS", 0),
        ):
            result = await pipeline.analyze_financial_document(
                pdf_bytes,
                "synthetic_native.pdf",
                max_pages=1,
            )

        self.assertEqual(result.extraction.pipeline, "hybrid_local_evidence_v1")
        self.assertEqual(result.extraction.source_kind, "born_digital")
        self.assertEqual(result.extraction.page_audit[0].route, "native_docling_glm")
        self.assertEqual(result.extraction.page_audit[0].orientation, 0)
        self.assertEqual(
            result.extraction.page_audit[0].orientation_method,
            "native_text_rendered_orientation",
        )
        total_bilan = next(field for field in result.fields if field.code == "TOTAL_BILAN")
        self.assertEqual(total_bilan.value, 2300.0)
        self.assertEqual(total_bilan.evidence[0].extraction_method, "glm_direct_vision")
        self.assertIsNotNone(result.scoring)
        self.assertEqual(result.scoring.score_status, "not_computed_policy_unapproved")


if __name__ == "__main__":
    unittest.main(verbosity=2)
