"""Tests for the concept_mapping ranking data and the per-filer override helpers
(docs/specs/concept-rank-integrity.md). Pure: no DB.

The locked spec tests cover `family_ranking` and `filer_ranking`; these cover the
family lists they are fed and the populator helpers around them.
"""

from __future__ import annotations

from src.ingest.sec_edgar.ingest.layer0_populator import Layer0Populator, filer_ranking
from src.ingest.util.generate_concept_mapping import (
    COMPONENT_RANK_BASE,
    FAMILY_ANCHOR_QNAMES,
    FAMILY_COMPONENT_QNAMES,
    family_ranking,
)


class TestFamilyLists:
    def test_components_belong_to_a_declared_family(self):
        assert set(FAMILY_COMPONENT_QNAMES) <= set(FAMILY_ANCHOR_QNAMES)

    def test_a_component_is_never_also_a_primary(self):
        for family, components in FAMILY_COMPONENT_QNAMES.items():
            assert not set(components) & set(FAMILY_ANCHOR_QNAMES[family]), family

    def test_every_family_fits_below_the_component_band(self):
        for family, primaries in FAMILY_ANCHOR_QNAMES.items():
            assert len(primaries) < COMPONENT_RANK_BASE, family

    def test_reviewed_rank_ones(self):
        """The rank-1 decisions reviewed in the spec's table."""
        expected = {
            "DepreciationAndAmortization": "us-gaap:DepreciationDepletionAndAmortization",
            "Debt": "us-gaap:LongTermDebt",
            "SellingAndMarketing": "us-gaap:SellingAndMarketingExpense",
            "ComprehensiveIncome": "us-gaap:ComprehensiveIncomeNetOfTax",
            "IntangibleAssets": "us-gaap:IntangibleAssetsNetExcludingGoodwill",
            "ShareBasedCompensation": "us-gaap:ShareBasedCompensation",
            "NetIncome": "us-gaap:NetIncomeLoss",
        }
        for family, qname in expected.items():
            ranking = family_ranking(
                FAMILY_ANCHOR_QNAMES[family], FAMILY_COMPONENT_QNAMES.get(family, []), []
            )
            assert ranking[0] == (qname, 1), family

    def test_da_components_rank_below_every_total(self):
        ranking = dict(family_ranking(
            FAMILY_ANCHOR_QNAMES["DepreciationAndAmortization"],
            FAMILY_COMPONENT_QNAMES["DepreciationAndAmortization"], []))
        assert ranking["us-gaap:Depreciation"] >= COMPONENT_RANK_BASE
        assert ranking["us-gaap:AmortizationOfIntangibleAssets"] >= COMPONENT_RANK_BASE
        # Not a us-gaap element (concept-family-coverage D4): MSFT's extension, cik-scoped.
        assert "us-gaap:DepreciationAmortizationAndOther" not in ranking


class TestOverrideHelpers:
    def test_short_form_qname_collects_years_across_dated_namespaces(self):
        present = {
            ("ProfitLoss", "https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full"): {2024},
            ("NetIncomeLoss", "http://fasb.org/us-gaap/2024"): {2023, 2024},
            ("NetIncomeLoss", "http://fasb.org/us-gaap/2025"): {2025},
        }
        years = Layer0Populator._qname_years
        assert years("us-gaap:NetIncomeLoss", present) == {2023, 2024, 2025}
        assert years("ifrs-full:ProfitLoss", present) == {2024}
        assert years("us-gaap:ProfitLoss", present) == set()
        # Full-URI and extension QNames are never matched here.
        assert years("http://fasb.org/us-gaap/2025:NetIncomeLoss", present) == set()
        assert years("amd:Foo", {("Foo", "http://amd.com/2025"): {2025}}) == set()

    def test_finding_actions(self):
        ranking = [("us-gaap:LongTermDebt", 1), ("us-gaap:LongTermDebtNoncurrent", 2),
                   ("us-gaap:ShortTermBorrowings", 51)]
        reported = {"us-gaap:LongTermDebtNoncurrent", "us-gaap:ShortTermBorrowings"}
        filer = filer_ranking(ranking, reported)
        found = Layer0Populator._override_finding(
            "Debt", "0000002488", [2024, 2025], ranking, reported, filer)
        assert found["action"] == "override"
        assert found["winner_qname"] == "us-gaap:LongTermDebtNoncurrent"
        assert found["winner_rank"] == 2
        assert found["fiscal_year"] == "2024-2025"
        assert found["ranking"] == filer

        optimal = Layer0Populator._override_finding(
            "Debt", "x", [2025], ranking, {"us-gaap:LongTermDebt"}, None)
        assert optimal["action"] == "optimal" and optimal["winner_qname"] is None
        none = Layer0Populator._override_finding("Debt", "x", [2025], ranking, set(), None)
        assert none["action"] == "no_facts"

    def test_a_filer_reporting_only_components_gets_no_override(self):
        """Alphabet tags only Depreciation: it must not become Alphabet's D&A."""
        ranking = family_ranking(
            FAMILY_ANCHOR_QNAMES["DepreciationAndAmortization"],
            FAMILY_COMPONENT_QNAMES["DepreciationAndAmortization"], [])
        assert filer_ranking(ranking, {"us-gaap:Depreciation"}) is None
