"""Unit tests for PlanValidator (design doc §2.2.3, point 3).

Plan fixtures are modeled on the natural-language "Purpose" comments in
scripts/xbrl/analyst_queries.sql (1a, 2c, 3a/3d, 6b, 6a+9a) so the validator
is exercised against plans an LLM would plausibly emit for those queries.
"""
import pytest

from src.retriever.xbrl.plan_validator import KNOWN_CONCEPT_FAMILIES, PlanValidator

NVDA_CIK = "0001045810"


@pytest.fixture
def validator() -> PlanValidator:
    return PlanValidator()


# ---------------------------------------------------------------------------
# Valid plans, modeled on scripts/xbrl/analyst_queries.sql
# ---------------------------------------------------------------------------

class TestValidPlans:
    def test_single_lookup_no_dimensions(self, validator):
        """analyst_queries.sql 1a: 'What was NVDA's revenue in FY2026?'"""
        plan = {
            "steps": [
                {
                    "id": "rev26",
                    "op": "lookup",
                    "cik": NVDA_CIK,
                    "ticker": "NVDA",
                    "fy": 2026,
                    "concept_family": "Revenue",
                    "dimensions": "NONE",
                    "section_hint": "Income Statement",
                }
            ]
        }
        assert validator.validate(plan) == []

    def test_yoy_growth_compute(self, validator):
        """analyst_queries.sql 2c: YoY revenue growth for NVDA FY2025 -> FY2026."""
        plan = {
            "steps": [
                {
                    "id": "rev25", "op": "lookup", "cik": NVDA_CIK, "ticker": "NVDA",
                    "fy": 2025, "concept_family": "Revenue", "dimensions": "NONE",
                    "section_hint": "Income Statement",
                },
                {
                    "id": "rev26", "op": "lookup", "cik": NVDA_CIK, "ticker": "NVDA",
                    "fy": 2026, "concept_family": "Revenue", "dimensions": "NONE",
                    "section_hint": "Income Statement",
                },
                {"id": "growth", "op": "compute", "expr": "(rev26 - rev25) / rev25"},
            ]
        }
        assert validator.validate(plan) == []

    def test_segment_comparison_with_dimensions(self, validator):
        """analyst_queries.sql 3a/3d: NVDA Data Center vs Gaming revenue, FY2026."""
        plan = {
            "steps": [
                {
                    "id": "dc26", "op": "lookup", "cik": NVDA_CIK, "ticker": "NVDA",
                    "fy": 2026, "concept_family": "Revenue",
                    "dimensions": {"product": "data center"},
                    "section_hint": "Disaggregated Revenue",
                },
                {
                    "id": "gm26", "op": "lookup", "cik": NVDA_CIK, "ticker": "NVDA",
                    "fy": 2026, "concept_family": "Revenue",
                    "dimensions": {"product": "gaming"},
                    "section_hint": "Disaggregated Revenue",
                },
                {"id": "diff", "op": "compute", "expr": "dc26 - gm26"},
            ]
        }
        assert validator.validate(plan) == []

    def test_capex_intensity_ratio(self, validator):
        """analyst_queries.sql 6b: capex / revenue for NVDA FY2024."""
        plan = {
            "steps": [
                {
                    "id": "capex24", "op": "lookup", "cik": NVDA_CIK, "ticker": "NVDA",
                    "fy": 2024, "concept_family": "Capex", "dimensions": "NONE",
                    "section_hint": "Cash Flow",
                },
                {
                    "id": "rev24", "op": "lookup", "cik": NVDA_CIK, "ticker": "NVDA",
                    "fy": 2024, "concept_family": "Revenue", "dimensions": "NONE",
                    "section_hint": "Income Statement",
                },
                {"id": "intensity", "op": "compute", "expr": "capex24 / rev24"},
            ]
        }
        assert validator.validate(plan) == []

    def test_free_cash_flow_plus_narrative(self, validator):
        """analyst_queries.sql 6a + 9a: FCF = OCF - Capex, with narrative context."""
        plan = {
            "steps": [
                {
                    "id": "ocf26", "op": "lookup", "cik": NVDA_CIK, "ticker": "NVDA",
                    "fy": 2026, "concept_family": "CashFromOperations", "dimensions": "NONE",
                    "section_hint": "Cash Flow",
                },
                {
                    "id": "capex26", "op": "lookup", "cik": NVDA_CIK, "ticker": "NVDA",
                    "fy": 2026, "concept_family": "Capex", "dimensions": "NONE",
                    "section_hint": "Cash Flow",
                },
                {"id": "fcf", "op": "compute", "expr": "ocf26 - capex26"},
                {"id": "ctx", "op": "narrative", "section_hint": "Management's Discussion and Analysis"},
            ]
        }
        assert validator.validate(plan) == []

    def test_error_object_passthrough(self, validator):
        """The prompt's own {"error": ...} contract is not this validator's concern."""
        plan = {
            "error": "Period mismatch: query asks for calendar 2024 but filer's FYE is Jan 25.",
            "user_query": "...",
            "reason": "...",
            "suggestion": "...",
        }
        assert validator.validate(plan) == []


# ---------------------------------------------------------------------------
# Invalid plans
# ---------------------------------------------------------------------------

class TestInvalidPlans:
    def test_not_a_dict(self, validator):
        assert validator.validate(["steps"]) != []

    def test_missing_steps_key(self, validator):
        errors = validator.validate({"plan": []})
        assert any("steps" in e for e in errors)

    def test_empty_steps(self, validator):
        errors = validator.validate({"steps": []})
        assert any("non-empty" in e for e in errors)

    def test_duplicate_step_ids(self, validator):
        plan = {
            "steps": [
                {
                    "id": "rev26", "op": "lookup", "cik": NVDA_CIK, "ticker": "NVDA",
                    "fy": 2026, "concept_family": "Revenue", "dimensions": "NONE",
                    "section_hint": "Income Statement",
                },
                {
                    "id": "rev26", "op": "lookup", "cik": NVDA_CIK, "ticker": "NVDA",
                    "fy": 2025, "concept_family": "Revenue", "dimensions": "NONE",
                    "section_hint": "Income Statement",
                },
            ]
        }
        errors = validator.validate(plan)
        assert any("not unique" in e for e in errors)

    def test_compute_forward_reference(self, validator):
        """compute step references an id that is defined LATER in the plan."""
        plan = {
            "steps": [
                {"id": "growth", "op": "compute", "expr": "(rev26 - rev25) / rev25"},
                {
                    "id": "rev25", "op": "lookup", "cik": NVDA_CIK, "ticker": "NVDA",
                    "fy": 2025, "concept_family": "Revenue", "dimensions": "NONE",
                    "section_hint": "Income Statement",
                },
                {
                    "id": "rev26", "op": "lookup", "cik": NVDA_CIK, "ticker": "NVDA",
                    "fy": 2026, "concept_family": "Revenue", "dimensions": "NONE",
                    "section_hint": "Income Statement",
                },
            ]
        }
        errors = validator.validate(plan)
        assert any("forward-referenced" in e or "undefined" in e for e in errors)

    def test_compute_undefined_reference(self, validator):
        plan = {
            "steps": [
                {
                    "id": "rev26", "op": "lookup", "cik": NVDA_CIK, "ticker": "NVDA",
                    "fy": 2026, "concept_family": "Revenue", "dimensions": "NONE",
                    "section_hint": "Income Statement",
                },
                {"id": "growth", "op": "compute", "expr": "(rev26 - revenue_prior_year) / revenue_prior_year"},
            ]
        }
        errors = validator.validate(plan)
        assert any("revenue_prior_year" in e for e in errors)

    def test_compute_disallowed_characters(self, validator):
        plan = {
            "steps": [
                {
                    "id": "rev26", "op": "lookup", "cik": NVDA_CIK, "ticker": "NVDA",
                    "fy": 2026, "concept_family": "Revenue", "dimensions": "NONE",
                    "section_hint": "Income Statement",
                },
                {"id": "hacky", "op": "compute", "expr": "__import__('os').system('rm -rf /')"},
            ]
        }
        errors = validator.validate(plan)
        assert any("invalid" in e.lower() or "characters" in e.lower() for e in errors)

    def test_bad_dimensions_type(self, validator):
        plan = {
            "steps": [
                {
                    "id": "dc26", "op": "lookup", "cik": NVDA_CIK, "ticker": "NVDA",
                    "fy": 2026, "concept_family": "Revenue",
                    "dimensions": {"product": "DataCenterMember"},  # final QName, not a hint - still a dict though
                    "section_hint": "Disaggregated Revenue",
                },
            ]
        }
        # a dict is structurally fine for the validator (it only checks shape, not
        # whether the LLM emitted a fuzzy hint vs a final QName)
        assert validator.validate(plan) == []

        plan["steps"][0]["dimensions"] = "TOTAL"  # neither "NONE" nor a dict
        errors = validator.validate(plan)
        assert any("dimensions" in e for e in errors)

    def test_unknown_concept_family(self, validator):
        plan = {
            "steps": [
                {
                    "id": "rev26", "op": "lookup", "cik": NVDA_CIK, "ticker": "NVDA",
                    "fy": 2026, "concept_family": "TotalRevenueGAAP",  # hallucinated
                    "dimensions": "NONE", "section_hint": "Income Statement",
                },
            ]
        }
        errors = validator.validate(plan)
        assert any("TotalRevenueGAAP" in e for e in errors)

    def test_invalid_op(self, validator):
        plan = {"steps": [{"id": "agg1", "op": "aggregate", "expr": "sum(rev26)"}]}
        errors = validator.validate(plan)
        assert any("op" in e for e in errors)

    def test_lookup_missing_required_field(self, validator):
        plan = {
            "steps": [
                {"id": "rev26", "op": "lookup", "cik": NVDA_CIK, "ticker": "NVDA", "fy": 2026}
                # missing concept_family, dimensions, section_hint
            ]
        }
        errors = validator.validate(plan)
        assert any("concept_family" in e for e in errors)
        assert any("dimensions" in e for e in errors)
        assert any("section_hint" in e for e in errors)

    def test_narrative_missing_section_hint(self, validator):
        plan = {"steps": [{"id": "ctx", "op": "narrative"}]}
        errors = validator.validate(plan)
        assert any("section_hint" in e for e in errors)

    def test_step_id_not_lowercase(self, validator):
        plan = {
            "steps": [
                {
                    "id": "Rev26", "op": "lookup", "cik": NVDA_CIK, "ticker": "NVDA",
                    "fy": 2026, "concept_family": "Revenue", "dimensions": "NONE",
                    "section_hint": "Income Statement",
                }
            ]
        }
        errors = validator.validate(plan)
        assert any("Rev26" in e for e in errors)

    def test_too_many_steps(self, validator):
        steps = [
            {
                "id": f"f{i}", "op": "lookup", "cik": NVDA_CIK, "ticker": "NVDA",
                "fy": 2020 + (i % 5), "concept_family": "Revenue", "dimensions": "NONE",
                "section_hint": "Income Statement",
            }
            for i in range(51)
        ]
        errors = validator.validate({"steps": steps})
        assert any("exceeds max" in e for e in errors)

    def test_fy_must_be_int(self, validator):
        plan = {
            "steps": [
                {
                    "id": "rev26", "op": "lookup", "cik": NVDA_CIK, "ticker": "NVDA",
                    "fy": "2026", "concept_family": "Revenue", "dimensions": "NONE",
                    "section_hint": "Income Statement",
                }
            ]
        }
        errors = validator.validate(plan)
        assert any("'fy'" in e for e in errors)

    def test_valid_fiscal_period_accepted(self, validator):
        plan = {
            "steps": [
                {
                    "id": "q2", "op": "lookup", "cik": NVDA_CIK, "ticker": "NVDA",
                    "fy": 2026, "fiscal_period": "Q2", "ytd": True,
                    "concept_family": "Revenue", "dimensions": "NONE",
                    "section_hint": "Income Statement",
                }
            ]
        }
        assert validator.validate(plan) == []

    def test_invalid_fiscal_period_rejected(self, validator):
        plan = {
            "steps": [
                {
                    "id": "bad", "op": "lookup", "cik": NVDA_CIK, "ticker": "NVDA",
                    "fy": 2026, "fiscal_period": "Q9",
                    "concept_family": "Revenue", "dimensions": "NONE",
                    "section_hint": "Income Statement",
                }
            ]
        }
        assert any("fiscal_period" in e for e in validator.validate(plan))

    def test_ytd_must_be_bool(self, validator):
        plan = {
            "steps": [
                {
                    "id": "bad", "op": "lookup", "cik": NVDA_CIK, "ticker": "NVDA",
                    "fy": 2026, "fiscal_period": "Q2", "ytd": "yes",
                    "concept_family": "Revenue", "dimensions": "NONE",
                    "section_hint": "Income Statement",
                }
            ]
        }
        assert any("'ytd'" in e for e in validator.validate(plan))

    def test_point_in_time_fields_accepted(self, validator):
        plan = {
            "steps": [
                {
                    "id": "pit", "op": "lookup", "cik": NVDA_CIK, "ticker": "NVDA",
                    "fy": 2024, "as_of": "2025-03-01", "as_originally_reported": True,
                    "concept_family": "Revenue", "dimensions": "NONE",
                    "section_hint": "Income Statement",
                }
            ]
        }
        assert validator.validate(plan) == []

    def test_bad_as_of_rejected(self, validator):
        plan = {
            "steps": [
                {
                    "id": "pit", "op": "lookup", "cik": NVDA_CIK, "ticker": "NVDA",
                    "fy": 2024, "as_of": "March 2025",
                    "concept_family": "Revenue", "dimensions": "NONE",
                    "section_hint": "Income Statement",
                }
            ]
        }
        assert any("as_of" in e for e in validator.validate(plan))


def test_known_concept_families_cover_analyst_query_families():
    """Sanity check: the concept families used across analyst_queries.sql
    scenarios are all present in KNOWN_CONCEPT_FAMILIES."""
    used = {
        "Revenue", "CostOfRevenue", "GrossProfit", "ResearchAndDevelopment",
        "SellingGeneralAndAdministrative", "OperatingExpenses", "OperatingIncome",
        "InterestExpense", "InterestIncome", "PretaxIncome", "TaxExpense", "NetIncome",
        "TotalAssets", "CashAndEquivalents", "Debt", "StockholdersEquity",
        "CashFromOperations", "CashFromInvesting", "CashFromFinancing", "Capex",
        "DividendsPaid", "StockRepurchases", "ShareBasedCompensation", "FreeCashFlow",
    }
    assert used <= KNOWN_CONCEPT_FAMILIES
