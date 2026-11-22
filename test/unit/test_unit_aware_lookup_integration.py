"""Integration of unit-aware-lookup (docs/specs/unit-aware-lookup.md) beyond the locked spec
tests: the lookup's unit restriction, the validator's `unit` rule, and the router's fast
path carrying a currency onto the plan. No DB, no LLM.
"""

from __future__ import annotations

import pytest

from src.retriever.xbrl.entity_extractor import ResolvedCompany, ResolvedPeriod
from src.retriever.xbrl.intent_router import XBRL_INTENT, IntentRouter, IntentRouterResult
from src.retriever.xbrl.plan_validator import PlanValidator
from src.retriever.xbrl.xbrl_factstore_lookup import (
    Citation,
    FactCandidate,
    LookupStatus,
    XbrlFactstoreLookup,
)

TSM = "0001046179"


def _cand(fact_id: int, value: float, unit: str | None, period_end: str) -> FactCandidate:
    return FactCandidate(
        fact_id=fact_id, value=value, unit=unit, period_type="duration",
        period_start=None, period_end=period_end, concept="ifrs-full:Revenue",
        concept_family="Revenue", concept_pref_rank=1, segment_axis=None,
        segment_member=None, segment_label=None, section_path=None,
        citation=Citation(fact_id=fact_id),
    )


# TSMC FY2024 revenue: TWD for the year and comparatives, USD convenience for the latest.
TSMC = [
    _cand(87103, 2_894_307_700_000.0, "TWD", "2025-01-01"),
    _cand(87104, 88_268_000_000.0, "USD", "2025-01-01"),
    _cand(87102, 2_161_735_800_000.0, "TWD", "2024-01-01"),
    _cand(87101, 2_263_891_300_000.0, "TWD", "2023-01-01"),
]
restrict = XbrlFactstoreLookup._restrict_to_one_unit


class TestRestrictToOneUnit:
    def test_reporting_currency_is_kept_and_the_translation_set_aside(self):
        notes: list[str] = []
        kept, status = restrict(TSMC, None, notes)
        assert status is None
        assert {c.unit for c in kept} == {"TWD"}
        assert kept[0].fact_id == 87103
        assert "set aside ['USD']" in notes[-1]

    def test_requested_unit_selects_the_translation(self):
        kept, status = restrict(TSMC, "USD", [])
        assert status is None
        assert [c.fact_id for c in kept] == [87104]

    def test_unavailable_requested_unit_is_not_found(self):
        notes: list[str] = []
        _, status = restrict(TSMC, "EUR", notes)
        assert status is LookupStatus.NOT_FOUND
        assert "EUR" in notes[-1]

    def test_two_units_with_comparatives_stay_ambiguous(self):
        both = [
            _cand(1, 10.0, "USD", "2025-01-01"), _cand(2, 9.0, "EUR", "2025-01-01"),
            _cand(3, 8.0, "USD", "2024-01-01"), _cand(4, 7.0, "EUR", "2024-01-01"),
        ]
        _, status = restrict(both, None, [])
        assert status is LookupStatus.AMBIGUOUS

    def test_single_unit_and_unitless_pass_through_untouched(self):
        usd = [_cand(1, 10.0, "USD", "2025-01-01"), _cand(2, 9.0, "USD", "2024-01-01")]
        assert restrict(usd, None, []) == (usd, None)
        unitless = [_cand(1, 0.21, None, "2025-01-01")]
        assert restrict(unitless, None, []) == (unitless, None)


class TestValidatorUnit:
    @staticmethod
    def _plan(**extra):
        step = {"id": "rev24", "op": "lookup", "cik": TSM, "fy": 2024,
                "concept_family": "Revenue", "dimensions": "NONE", "section_hint": None}
        return {"steps": [{**step, **extra}]}

    def test_iso_code_is_accepted_and_absence_is_fine(self):
        validator = PlanValidator()
        assert validator.validate(self._plan(unit="TWD")) == []
        assert validator.validate(self._plan()) == []

    @pytest.mark.parametrize("bad", ["usd", "US Dollar", "US", 12])
    def test_anything_else_is_rejected(self, bad):
        errors = PlanValidator().validate(self._plan(unit=bad))
        assert any("'unit'" in e for e in errors)


class TestFastPathCurrency:
    @staticmethod
    def _plan(concept_phrase: str) -> dict | None:
        router = object.__new__(IntentRouter)  # the fast path needs no LLM client
        result = IntentRouterResult(
            intent=XBRL_INTENT.NUMERIC_LOOKUP,
            companies=[ResolvedCompany(mention="TSMC", cik=TSM, ticker="TSM", resolution="alias",
                                       similarity=1.0)],
            periods=[ResolvedPeriod(mention="2024", fiscal_year=2024)],
            concept_candidates=[concept_phrase],
        )
        return router._try_fast_path_plan(result)

    def test_named_currency_becomes_the_step_unit(self):
        step = self._plan("revenue in New Taiwan dollars")["steps"][0]
        assert step["concept_family"] == "Revenue"
        assert step["unit"] == "TWD"

    def test_no_currency_leaves_unit_unset(self):
        step = self._plan("revenue")["steps"][0]
        assert step["concept_family"] == "Revenue"
        assert "unit" not in step
