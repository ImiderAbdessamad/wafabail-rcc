from __future__ import annotations

import unittest
from decimal import Decimal

from app.schemas.financial_analysis import FinancialValue, ValueProvenance
from app.services.financial_dataset_builder import empty_dataset
from app.services.scoring_analysis import build_scoring_summary


def confirmed(code: str, value: str) -> FinancialValue:
    return FinancialValue(
        code=code,
        label=code.replace("_", " ").title(),
        value=Decimal(value),
        status="confirmed",
        provenance=[
            ValueProvenance(
                page_number=2,
                raw_label=code,
                raw_value=value,
                extraction_method="test",
                mapping_model="test_engine",
            )
        ],
    )


class ScoringAnalysisTests(unittest.TestCase):
    def populated_dataset(self):
        dataset = empty_dataset()
        dataset.chiffre_affaires = confirmed("CHIFFRE_AFFAIRES", "1000")
        dataset.chiffre_affaires_n1 = confirmed("CHIFFRE_AFFAIRES_N1", "900")
        dataset.resultat_net = confirmed("RESULTAT_NET", "80")
        dataset.total_bilan = confirmed("TOTAL_BILAN", "800")
        dataset.fonds_propres = confirmed("FONDS_PROPRES", "200")
        dataset.dettes_financieres = confirmed("DETTES_FINANCIERES", "300")
        dataset.caf = confirmed("CAF", "100")
        dataset.fdr = confirmed("FDR", "50")
        dataset.tresorerie_nette = confirmed("TRESORERIE_NETTE", "20")
        dataset.clients = confirmed("CLIENTS", "250")
        dataset.fournisseurs = confirmed("FOURNISSEURS", "200")
        dataset.achats = confirmed("ACHATS", "600")
        return dataset

    def test_reference_ratios_are_deterministic_and_audited(self) -> None:
        summary = build_scoring_summary(self.populated_dataset())
        ratios = {ratio.code: ratio for ratio in summary.ratios}

        self.assertEqual(summary.total_ratio_count, 12)
        self.assertEqual(summary.calculable_ratio_count, 12)
        self.assertIsNone(summary.score)
        self.assertEqual(summary.policy_status, "unapproved_reference")
        self.assertEqual(ratios["autonomie_financiere"].value, Decimal("25.00"))
        self.assertEqual(ratios["ratio_endettement"].value, Decimal("1.50"))
        self.assertEqual(ratios["delai_clients"].value, Decimal("90.0"))
        self.assertEqual(ratios["delai_fournisseurs"].value, Decimal("120.0"))
        self.assertEqual(ratios["croissance_ca"].value, Decimal("11.11"))
        self.assertEqual(ratios["rentabilite_economique"].status, "informatif")
        self.assertEqual(summary.inputs[0].provenance[0].mapping_model, "test_engine")

    def test_zero_denominator_is_non_calculable_not_zero(self) -> None:
        dataset = self.populated_dataset()
        dataset.chiffre_affaires = confirmed("CHIFFRE_AFFAIRES", "0")
        summary = build_scoring_summary(dataset)
        ratios = {ratio.code: ratio for ratio in summary.ratios}

        self.assertIsNone(ratios["caf_sur_ca"].value)
        self.assertEqual(ratios["caf_sur_ca"].status, "non_calculable")
        self.assertTrue(
            any("dénominateur nul" in item for item in ratios["caf_sur_ca"].warnings)
        )

    def test_missing_or_conflicting_source_is_not_used(self) -> None:
        dataset = self.populated_dataset()
        dataset.fonds_propres.status = "conflicting"
        dataset.fonds_propres.value = None
        summary = build_scoring_summary(dataset)
        ratios = {ratio.code: ratio for ratio in summary.ratios}

        self.assertEqual(ratios["autonomie_financiere"].status, "non_calculable")
        self.assertEqual(ratios["ratio_endettement"].status, "non_calculable")


if __name__ == "__main__":
    unittest.main(verbosity=2)
