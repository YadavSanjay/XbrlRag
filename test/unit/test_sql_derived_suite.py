"""WS-9 / E2 — unit tests for the SQL-derived suite builder.

No live Postgres: a fake connection replays canned result sets, so the builder's
skip / label / render logic is testable in CI. The committed suite file is also
checked structurally — it is the ground truth every future run is scored against, so
a defect in it is far more expensive than a defect in ordinary code. Each of the four
bugs the Phase-1 validation gate caught has a regression test here.
"""

from __future__ import annotations

import json
import os
import re
from decimal import Decimal

import pytest
import yaml

from src.eval.suites import sql_derived
from src.eval.suites.sql_derived import (
    SqlDerivedBuilder,
    case_key,
    clean_member_label,
    collapse_hierarchy,
    drop_aggregate_rows,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SPEC_PATH = os.path.join(REPO_ROOT, "configs", "eval", "sql_derived_templates.yaml")
SUITE_PATH = os.path.join(REPO_ROOT, "test", "eval", "suites", "sql_derived_v1.json")


# ---------------------------------------------------------------------------
# Fake DB
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, script):
        self._script = script
        self.description = None
        self._rows = []

    def execute(self, sql, params=None):
        cols, rows = self._script(sql, params or {})
        self.description = [(c,) for c in cols]
        self._rows = rows

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn:
    """Replays canned results. ``script(sql, params) -> (cols, rows)``."""

    def __init__(self, script):
        self._script = script
        self.rolled_back = 0

    def cursor(self):
        return _FakeCursor(self._script)

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        pass


def _spec(**template_overrides):
    template = {
        "id": "t-lookup",
        "source": "analyst_queries.sql#1a",
        "kind": "lookup",
        "nl": [
            "What was {company}'s revenue in fiscal {fy}?",
            "{company} FY{fy} revenue?",
        ],
        "params": {"ticker": "corpus.tickers", "fy": "corpus.fiscal_years"},
        # Fixtures must scope the period like real templates, or the build guard rejects them.
        "sql": (
            "SELECT value, fact_id, unit FROM mv "
            "WHERE t=%(ticker)s AND y=%(fy)s AND fiscal_period = 'FY'"
        ),
        "expect": {"value": "value", "fact_ids": "fact_id", "unit": "unit"},
        "rel_tol": 1e-4,
    }
    template.update(template_overrides)
    return {
        "version": "v1",
        "suite_name": "sql_derived",
        "corpus": {
            "tickers": ["NVDA"],
            "fiscal_years": [2026],
            "company_names": {"NVDA": "NVIDIA"},
        },
        "tiers": {"standard": {"paraphrases": 1, "max_cases_per_template": 50}},
        "templates": [template],
    }


def _one_row(cols, row):
    def script(sql, params):
        if "entity_master" in sql:
            return ["trading_symbol", "company_name"], [("NVDA", "NVIDIA CORP")]
        return cols, [row]

    return script


# ---------------------------------------------------------------------------
# Core build behaviour
# ---------------------------------------------------------------------------


class TestBuild:
    def test_emits_a_labelled_case(self):
        conn = FakeConn(
            _one_row(
                ["value", "fact_id", "unit"], (Decimal("215938000000"), 1224, "USD")
            )
        )
        suite = SqlDerivedBuilder(_spec(), conn).build("standard")
        assert len(suite) == 1
        case = suite.cases[0]
        assert case.expected_value == 215938000000.0
        assert case.expected_fact_ids == [1224]
        assert case.unit == "USD"
        assert case.kind == "lookup"
        assert case.origin == "analyst_queries.sql#1a"

    def test_question_uses_spec_company_name_not_edgar_legal_name(self):
        """Asking about "NVIDIA CORP" tests string matching; "NVIDIA" tests entity
        resolution — which is the thing under test."""
        conn = FakeConn(
            _one_row(["value", "fact_id", "unit"], (Decimal("1"), 5, "USD"))
        )
        suite = SqlDerivedBuilder(_spec(), conn).build("standard")
        assert suite.cases[0].question == "What was NVIDIA's revenue in fiscal 2026?"

    def test_falls_back_to_entity_master_name(self):
        spec = _spec()
        del spec["corpus"]["company_names"]
        conn = FakeConn(
            _one_row(["value", "fact_id", "unit"], (Decimal("1"), 5, "USD"))
        )
        suite = SqlDerivedBuilder(spec, conn).build("standard")
        assert "NVIDIA CORP" in suite.cases[0].question

    def test_empty_result_is_skipped(self):
        def script(sql, params):
            if "entity_master" in sql:
                return ["trading_symbol", "company_name"], []
            return ["value", "fact_id", "unit"], []

        builder = SqlDerivedBuilder(_spec(), FakeConn(script))
        assert len(builder.build("standard")) == 0
        assert builder.stats.skipped_empty == 1

    def test_missing_fact_id_is_skipped(self):
        """Every case must carry grounding truth; an unlabelled case is dropped."""
        conn = FakeConn(
            _one_row(["value", "fact_id", "unit"], (Decimal("1"), None, "USD"))
        )
        builder = SqlDerivedBuilder(_spec(), conn)
        assert len(builder.build("standard")) == 0
        assert builder.stats.skipped_no_facts == 1

    def test_zero_value_is_skipped(self):
        """close_enough() matches a 0 target with an absolute band, so a zero-valued
        case is close to unfalsifiable and would inflate accuracy."""
        conn = FakeConn(
            _one_row(["value", "fact_id", "unit"], (Decimal("0"), 7, "USD"))
        )
        builder = SqlDerivedBuilder(_spec(), conn)
        assert len(builder.build("standard")) == 0
        assert builder.stats.skipped_zero_value == 1

    def test_allow_zero_opts_back_in(self):
        conn = FakeConn(
            _one_row(["value", "fact_id", "unit"], (Decimal("0"), 7, "USD"))
        )
        suite = SqlDerivedBuilder(_spec(allow_zero=True), conn).build("standard")
        assert len(suite) == 1

    def test_ambiguous_result_is_skipped_when_unique_required(self):
        """Ambiguous ground truth is worse than none."""

        def script(sql, params):
            if "entity_master" in sql:
                return ["trading_symbol", "company_name"], [("NVDA", "NVIDIA")]
            return ["value", "fact_id", "unit"], [
                (Decimal("1"), 1, "USD"),
                (Decimal("2"), 2, "USD"),
            ]

        builder = SqlDerivedBuilder(_spec(require_unique=True), FakeConn(script))
        assert len(builder.build("standard")) == 0
        assert builder.stats.skipped_ambiguous == 1

    def test_sql_error_is_counted_and_rolled_back(self):
        def script(sql, params):
            if "entity_master" in sql:
                return ["trading_symbol", "company_name"], [("NVDA", "NVIDIA")]
            raise RuntimeError("relation does not exist")

        conn = FakeConn(script)
        builder = SqlDerivedBuilder(_spec(), conn)
        assert len(builder.build("standard")) == 0
        assert builder.stats.sql_errors == 1
        assert conn.rolled_back >= 1

    def test_alt_scale_accepts_percentage_form(self):
        conn = FakeConn(
            _one_row(["value", "fact_id", "unit"], (Decimal("0.71"), 9, None))
        )
        suite = SqlDerivedBuilder(_spec(alt_scale=[100]), conn).build("standard")
        assert suite.cases[0].alt_values == [71.0]

    def test_paraphrases_share_a_group_and_truth(self):
        spec = _spec()
        spec["tiers"]["standard"]["paraphrases"] = 2
        conn = FakeConn(
            _one_row(["value", "fact_id", "unit"], (Decimal("5"), 1, "USD"))
        )
        suite = SqlDerivedBuilder(spec, conn).build("standard")
        assert len(suite) == 2
        assert len({c.paraphrase_group for c in suite.cases}) == 1
        assert len({c.id for c in suite.cases}) == 2
        assert {c.expected_value for c in suite.cases} == {5.0}

    def test_cap_limits_cases_per_template(self):
        spec = _spec()
        spec["corpus"]["fiscal_years"] = [2022, 2023, 2024, 2025, 2026]
        spec["tiers"]["standard"]["max_cases_per_template"] = 2
        conn = FakeConn(
            _one_row(["value", "fact_id", "unit"], (Decimal("5"), 1, "USD"))
        )
        builder = SqlDerivedBuilder(spec, conn)
        assert len(builder.build("standard")) == 2
        assert builder.stats.skipped_capped == 1


class TestScreenCases:
    def _screen_spec(self, **kw):
        return _spec(
            id="t-screen",
            kind="screen",
            nl=["Rank the covered filers by revenue for FY{fy}."],
            params={"fy": "corpus.fiscal_years"},
            sql="SELECT ticker, value, fact_id FROM mv WHERE y=%(fy)s AND fiscal_period = 'FY'",
            expect={
                "rows": {"entity": "ticker", "value": "value"},
                "fact_ids": "fact_id",
            },
            post_sort={"by": "value", "order": "desc"},
            min_rows=2,
            top_n=3,
            **kw,
        )

    def _script(self, rows):
        def script(sql, params):
            if "entity_master" in sql:
                return ["trading_symbol", "company_name"], [("NVDA", "NVIDIA")]
            return ["ticker", "value", "fact_id"], rows

        return script

    def test_rows_are_ranked_by_value_not_source_order(self):
        """The reference SQL orders by ticker for readability; a ranked case must be
        ordered by the metric being ranked."""
        conn = FakeConn(
            self._script(
                [
                    ("AAPL", Decimal("10"), 1),
                    ("NVDA", Decimal("30"), 2),
                    ("MSFT", Decimal("20"), 3),
                ]
            )
        )
        suite = SqlDerivedBuilder(self._screen_spec(), conn).build("standard")
        rows = suite.cases[0].expected_rows
        assert [r["entity"] for r in rows] == ["NVDA", "MSFT", "AAPL"]
        # Collected in ranked order; grading compares them as a set.
        assert suite.cases[0].expected_fact_ids == [2, 3, 1]

    def test_top_n_truncates_expected_rows(self):
        conn = FakeConn(
            self._script([(f"T{i}", Decimal(str(10 - i)), i) for i in range(6)])
        )
        suite = SqlDerivedBuilder(self._screen_spec(), conn).build("standard")
        assert len(suite.cases[0].expected_rows) == 3

    def test_fact_ids_cover_only_the_rows_that_survive_top_n(self):
        """Ground truth is the top-N rows, so citations are only required for those.
        Collecting ids from all 11 filers marked a correct top-5 answer as
        'missing 6 facts'."""
        conn = FakeConn(
            self._script([(f"T{i}", Decimal(str(100 - i)), i) for i in range(6)])
        )
        suite = SqlDerivedBuilder(self._screen_spec(), conn).build("standard")
        case = suite.cases[0]
        assert len(case.expected_rows) == 3  # top_n on this fixture
        assert case.expected_fact_ids == [0, 1, 2]  # not [0..5]

    def test_too_few_rows_is_skipped(self):
        conn = FakeConn(self._script([("NVDA", Decimal("1"), 1)]))
        builder = SqlDerivedBuilder(self._screen_spec(), conn)
        assert len(builder.build("standard")) == 0
        assert builder.stats.skipped_too_few_rows == 1

    def test_member_suffix_stripped_from_row_entities(self):
        def script(sql, params):
            if "entity_master" in sql:
                return ["trading_symbol", "company_name"], [("NVDA", "NVIDIA")]
            return ["ticker", "value", "fact_id"], [
                ("Data Center [Member]", Decimal("30"), 1),
                ("Gaming [Member]", Decimal("10"), 2),
            ]

        suite = SqlDerivedBuilder(self._screen_spec(), FakeConn(script)).build(
            "standard"
        )
        assert [r["entity"] for r in suite.cases[0].expected_rows] == [
            "Data Center",
            "Gaming",
        ]


class TestFactIdExtraction:
    def test_component_fact_ids_json_object(self):
        conn = FakeConn(
            _one_row(
                ["value", "component_fact_ids"],
                (Decimal("0.71"), {"revenue": 77, "cost_of_revenue": 80}),
            )
        )
        spec = _spec(expect={"value": "value", "fact_ids_json": "component_fact_ids"})
        suite = SqlDerivedBuilder(spec, conn).build("standard")
        assert sorted(suite.cases[0].expected_fact_ids) == [77, 80]

    def test_multiple_fact_id_columns(self):
        conn = FakeConn(
            _one_row(
                ["value", "fact_id_current", "fact_id_prior"],
                (Decimal("0.65"), 1224, 65559),
            )
        )
        spec = _spec(
            expect={"value": "value", "fact_ids": ["fact_id_current", "fact_id_prior"]}
        )
        suite = SqlDerivedBuilder(spec, conn).build("standard")
        assert suite.cases[0].expected_fact_ids == [1224, 65559]


class TestAggregateFiltering:
    """A filer may tag an aggregate and its children on the same axis, giving a
    breakdown two equally-correct answers. Ground truth collapses to ONE level —
    by default the level the filer reports (the parent), which is also the level the
    pipeline answers at."""

    @staticmethod
    def _rows(pairs):
        return [{"entity": e, "value": float(v)} for e, v in pairs]

    APPLE = [
        ("Product", 294866),
        ("iPhone", 201183),
        ("Service", 96169),
        ("Wearables", 37005),
        ("Mac", 29984),
        ("iPad", 26694),
    ]
    NVIDIA = [
        ("Data Center", 115186),
        ("Compute", 102196),
        ("Networking", 12990),
        ("Gaming", 11350),
    ]

    def test_reported_level_keeps_the_parent(self):
        """Apple reports Product/Service; iPhone..iPad are its children."""
        kept = {r["entity"] for r in collapse_hierarchy(self._rows(self.APPLE))}
        assert kept == {"Product", "Service"}

    def test_reported_level_keeps_nvidia_data_center(self):
        """Data Center = Compute + Networking, and Data Center is what NVIDIA
        reports — this is the case that made the pipeline look wrong."""
        kept = {r["entity"] for r in collapse_hierarchy(self._rows(self.NVIDIA))}
        assert kept == {"Data Center", "Gaming"}

    def test_leaf_level_keeps_the_children(self):
        kept = {
            r["entity"]
            for r in collapse_hierarchy(self._rows(self.APPLE), level="leaf")
        }
        assert kept == {"iPhone", "Service", "Wearables", "Mac", "iPad"}

    def test_back_compat_alias_is_leaf_level(self):
        kept = {r["entity"] for r in drop_aggregate_rows(self._rows(self.NVIDIA))}
        assert kept == {"Compute", "Networking", "Gaming"}

    def test_nested_hierarchy_collapses_fully(self):
        """A = B + C and B = D + E: keeping only A must not leave D and E behind to
        double-count against it."""
        rows = self._rows([("A", 100), ("B", 60), ("C", 40), ("D", 35), ("E", 25)])
        kept = {r["entity"] for r in collapse_hierarchy(rows)}
        assert kept == {"A"}

    def test_keeps_a_clean_single_level_breakdown(self):
        rows = self._rows([("DRAM", 28578), ("NAND", 8503), ("Other", 297)])
        assert len(drop_aggregate_rows(rows)) == 3

    def test_collapses_duplicate_rows(self):
        """AAPL FY2024 lists Service twice under distinct member QNames."""
        rows = self._rows([("Service", 96169), ("Service", 96169), ("Mac", 29984)])
        assert len(drop_aggregate_rows(rows)) == 2

    def test_a_single_equal_sibling_is_not_treated_as_a_parent(self):
        """Two equal siblings are duplicates/coincidence, not a hierarchy."""
        rows = self._rows([("A", 100), ("B", 100)])
        assert len(drop_aggregate_rows(rows)) == 2


class TestPeriodScoping:
    """A question phrased "in fiscal YYYY" means the ANNUAL figure. Without a
    fiscal_period predicate the SQL returns a 10-Q quarterly/YTD row and labels it
    annual — which mis-measured 75 cases (18% of the suite) in the first baseline."""

    def test_build_refuses_a_template_that_does_not_scope_the_period(self):
        spec = _spec(sql="SELECT value, fact_id, unit FROM mv WHERE t=%(ticker)s")
        with pytest.raises(ValueError, match="fiscal_period"):
            SqlDerivedBuilder(spec, FakeConn(lambda s, p: ([], []))).build("standard")

    def test_build_accepts_a_period_scoped_template(self):
        conn = FakeConn(
            _one_row(["value", "fact_id", "unit"], (Decimal("1"), 5, "USD"))
        )
        spec = _spec(
            sql="SELECT value, fact_id, unit FROM mv "
            "WHERE t=%(ticker)s AND fiscal_period = 'FY'"
        )
        assert len(SqlDerivedBuilder(spec, conn).build("standard")) == 1

    def test_every_committed_template_scopes_the_period(self):
        with open(SPEC_PATH, encoding="utf-8") as f:
            spec = yaml.safe_load(f)
        missing = [
            t["id"] for t in spec["templates"]
            if not t.get("pick") and "fiscal_period" not in t["sql"]
        ]
        assert not missing, f"templates without a fiscal_period predicate: {missing}"

    def test_a_pick_template_is_scoped_by_its_pick(self):
        # s1_lookup_pick reads the filing labelled fy/FY; the SQL returns every filing.
        conn = FakeConn(lambda s, p: ([], []))
        spec = _spec(sql="SELECT value, fact_id, unit FROM mv WHERE t=%(ticker)s")
        spec["templates"][0]["pick"] = "s1_lookup"
        SqlDerivedBuilder(spec, conn).build("standard")  # does not raise

    @pytest.mark.parametrize("predicate", [
        "fiscal_year = %(fy)s", "f.fiscal_period = 'FY'", "fiscal_year IN (2024)",
    ])
    def test_a_pick_template_refuses_a_year_predicate(self, predicate):
        # A year filter would cut the later filings' comparatives that restate the year.
        spec = _spec(sql=f"SELECT value, fact_id, unit FROM mv WHERE {predicate}")
        spec["templates"][0]["pick"] = "s1_lookup"
        with pytest.raises(ValueError, match="must not restrict"):
            SqlDerivedBuilder(spec, FakeConn(lambda s, p: ([], [])))

    def test_an_unknown_pick_is_refused(self):
        spec = _spec(sql="SELECT value FROM mv WHERE fiscal_period = 'FY'")
        spec["templates"][0]["pick"] = "nope"
        with pytest.raises(ValueError, match="unknown pick"):
            SqlDerivedBuilder(spec, FakeConn(lambda s, p: ([], [])))

    def test_no_committed_pick_template_restricts_the_year(self):
        with open(SPEC_PATH, encoding="utf-8") as f:
            spec = yaml.safe_load(f)
        picks = [t for t in spec["templates"] if t.get("pick")]
        assert {t["id"] for t in picks} == (
            set(S1_PICK_TEMPLATES) | set(DIMENSIONAL_PICKS) | YOY_PICK_TEMPLATES
        )
        for t in picks:
            assert not re.search(r"fiscal_(year|period)\s*(=|IN\b)", t["sql"], re.I), t["id"]


class TestFactEquivalence:
    def test_classes_are_looked_up_and_attached_to_the_case(self):
        def script(sql, params):
            if "entity_master" in sql:
                return ["trading_symbol", "company_name"], [("NVDA", "NVIDIA")]
            if "array_agg" in sql:  # the equivalence query
                return ["anchor", "members"], [(5, [5, 77, 99])]
            return ["value", "fact_id", "unit"], [(Decimal("1"), 5, "USD")]

        suite = SqlDerivedBuilder(_spec(), FakeConn(script)).build("standard")
        case = suite.cases[0]
        assert case.expected_fact_ids == [5]
        assert case.grounding_classes() == [[5, 77, 99]]

    def test_alternative_inputs_become_one_class(self):
        """GrossMarginPct cites revenue plus EITHER cost of revenue OR gross profit
        (docs/specs/alternative-inputs-grounding.md): two classes, not three."""

        def script(sql, params):
            if "entity_master" in sql:
                return ["trading_symbol", "company_name"], [("NVDA", "NVIDIA")]
            if "array_agg" in sql:  # the equivalence query
                return ["anchor", "members"], [(80, [80, 81])]
            return ["value", "component_fact_ids"], [
                (Decimal("0.49"), {"revenue": 77, "cost_of_revenue": [80, 90]})
            ]

        spec = _spec(expect={"value": "value", "fact_ids_json": "component_fact_ids"})
        case = SqlDerivedBuilder(spec, FakeConn(script)).build("standard").cases[0]
        assert case.expected_fact_ids == [77, 80, 90]
        assert case.grounding_classes() == [[77], [80, 81, 90]]

    def test_unresolvable_fact_falls_back_to_a_singleton(self):
        """A fact the equivalence query cannot resolve must still grade, strictly."""

        def script(sql, params):
            if "entity_master" in sql:
                return ["trading_symbol", "company_name"], [("NVDA", "NVIDIA")]
            if "array_agg" in sql:
                return ["anchor", "members"], []  # no rows
            return ["value", "fact_id", "unit"], [(Decimal("1"), 5, "USD")]

        suite = SqlDerivedBuilder(_spec(), FakeConn(script)).build("standard")
        assert suite.cases[0].grounding_classes() == [[5]]

    def test_equivalence_failure_does_not_abort_the_build(self):
        def script(sql, params):
            if "entity_master" in sql:
                return ["trading_symbol", "company_name"], [("NVDA", "NVIDIA")]
            if "array_agg" in sql:
                raise RuntimeError("view missing")
            return ["value", "fact_id", "unit"], [(Decimal("1"), 5, "USD")]

        suite = SqlDerivedBuilder(_spec(), FakeConn(script)).build("standard")
        assert len(suite) == 1 and suite.cases[0].grounding_classes() == [[5]]


class TestHelpers:
    def test_member_marker_stripped_everywhere_not_just_trailing(self):
        """Aggregated dimension labels carry the marker mid-string."""
        assert clean_member_label("Data Center [Member]") == "Data Center"
        assert clean_member_label("Commercial Paper [Member] and Level 2 [Member]") == (
            "Commercial Paper and Level 2"
        )

    def test_case_key_is_stable_and_collision_free_for_long_values(self):
        """Truncating long combos silently collapsed distinct cases onto one id."""
        a = case_key({"combo": "X" * 200 + "A", "ticker": "AAPL"})
        b = case_key({"combo": "X" * 200 + "B", "ticker": "AAPL"})
        assert a != b
        assert a == case_key({"combo": "X" * 200 + "A", "ticker": "AAPL"})
        assert len(a) <= 64

    def test_case_key_handles_no_params(self):
        assert case_key({}) == "all"


# ---------------------------------------------------------------------------
# The committed spec + suite file
# ---------------------------------------------------------------------------


class TestSpecFile:
    @pytest.fixture(scope="class")
    def spec(self):
        with open(SPEC_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f)

    def test_spec_parses_and_every_template_is_well_formed(self, spec):
        from src.eval.suites.sql_derived import Template

        for raw in spec["templates"]:
            t = Template.parse(raw)
            assert t.nl, f"{t.id}: no nl phrasings"
            assert t.sql.strip(), f"{t.id}: no sql"
            assert t.source.startswith("analyst_queries.sql#"), f"{t.id}: no source"
            if t.kind in ("screen", "breakdown"):
                assert (t.expect.get("rows") or {}).get(
                    "entity"
                ), f"{t.id}: no row entity"
            else:
                assert t.expect.get("value"), f"{t.id}: no value column"
            assert t.expect.get("fact_ids") or t.expect.get(
                "fact_ids_json"
            ), f"{t.id}: no fact-id source — cases would carry no grounding truth"

    def test_every_analyst_section_1_to_8_and_10_is_represented(self, spec):
        sections = {
            re.sub(r"[a-z]$", "", t["source"].split("#")[-1]) for t in spec["templates"]
        }
        for required in ["1", "2", "3", "4", "5", "6", "7", "8", "10"]:
            assert (
                required in sections
            ), f"analyst_queries.sql section {required} not covered"

    def test_template_ids_unique(self, spec):
        ids = [t["id"] for t in spec["templates"]]
        assert len(ids) == len(set(ids))

    def test_tiers_declare_paraphrase_and_cap(self, spec):
        for name, tier in spec["tiers"].items():
            caps = {"max_cases_per_template", "max_cases_per_stratum"} & set(tier)
            assert "paraphrases" in tier and len(caps) == 1, name


@pytest.mark.skipif(not os.path.exists(SUITE_PATH), reason="suite not generated")
class TestCommittedSuite:
    """The generated suite is ground truth for every future run — check it hard."""

    @pytest.fixture(scope="class")
    def suite(self):
        return sql_derived.load(SUITE_PATH)

    def test_meets_the_ws9_e2_acceptance_size(self, suite):
        assert len(suite) >= 250, f"only {len(suite)} cases (WS9-E2 requires >= 250)"

    def test_every_case_carries_grounding_truth(self, suite):
        missing = [c.id for c in suite.cases if not c.expected_fact_ids]
        assert not missing, f"{len(missing)} case(s) without expected_fact_ids"

    def test_case_ids_are_unique(self, suite):
        ids = [c.id for c in suite.cases]
        assert len(ids) == len(set(ids))

    def test_questions_are_fully_rendered(self, suite):
        bad = [c.id for c in suite.cases if re.search(r"\{[a-z_]+\}", c.question)]
        assert not bad, f"unrendered placeholders in {bad[:5]}"

    def test_no_xbrl_member_markup_leaks_into_questions(self, suite):
        bad = [c.id for c in suite.cases if "[Member]" in c.question]
        assert not bad, f"raw [Member] markup in {bad[:5]}"

    def test_scalar_cases_have_a_nonzero_expected_value(self, suite):
        bad = [
            c.id
            for c in suite.cases
            if c.kind not in ("screen", "breakdown")
            and (c.expected_value is None or c.expected_value == 0)
        ]
        assert not bad, f"unusable scalar ground truth in {bad[:5]}"

    def test_multi_row_cases_have_at_least_two_rows(self, suite):
        bad = [
            c.id
            for c in suite.cases
            if c.kind in ("screen", "breakdown") and len(c.expected_rows or []) < 2
        ]
        assert not bad, f"ungradable multi-row cases: {bad[:5]}"

    def test_records_the_corpus_fingerprint_it_was_built_against(self, suite):
        assert suite.corpus_fingerprint and suite.corpus_fingerprint != "unknown"

    def test_covers_every_case_kind(self, suite):
        assert set(suite.by_kind()) == {
            "lookup",
            "compute",
            "dimensional",
            "screen",
            "breakdown",
        }

    def test_suite_file_is_valid_json_with_expected_header(self):
        with open(SUITE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        assert data["name"] == "sql_derived"
        assert data["n_cases"] == len(data["cases"])


# ---------------------------------------------------------------------------
# The single-fact pick follows the lookup's candidate order
# ---------------------------------------------------------------------------

# Single-fact lookups keyed by s1_lookup_pick (docs/specs/suite-templates-s1.md): the
# pick, not SQL, orders the candidates by LOOKUP_ORDER_KEYS.
S1_PICK_TEMPLATES = (
    "aq-1a-revenue-lookup",
    "aq-1c-pnl-line-lookup",
    "aq-5a-balance-sheet-lookup",
    "aq-6a-cash-flow-lookup",
    "aq-10a-tsm-twd-lookup",
)
# The dimensional templates and their S1 picks (docs/specs/dimensional-s1.md R2-R5).
DIMENSIONAL_PICKS = {
    "aq-3d-segment-member-lookup": "s1_dimensional",
    "aq-8b-multidim-lookup": "s1_dimensional",
    "aq-3a-product-segment-breakdown": "s1_breakdown",
    "aq-3b-business-segment-breakdown": "s1_breakdown",
    "aq-4a-geographic-breakdown": "s1_breakdown",
    "aq-10b-tsm-geo-breakdown": "s1_breakdown",
}

# Templates whose expected fact is one lookup's answer picked in SQL, and the alias of
# the fact row. None today: every single-fact template picks in Python.
SINGLE_FACT_TEMPLATES: dict[str, str] = {}

# Templates whose growth is two S1 lookups' answers (docs/specs/yoy-growth-s1.md).
YOY_PICK_TEMPLATES = {"aq-2c-yoy-growth"}


class TestLookupOrder:
    """`@LOOKUP_ORDER` is `family_resolution.candidate_order` with the currency tie-break."""

    @pytest.fixture(scope="class")
    def templates(self):
        from src.eval.suites.sql_derived import Template

        with open(SPEC_PATH, encoding="utf-8") as f:
            spec = yaml.safe_load(f)
        return {t["id"]: (t, Template.parse(t)) for t in spec["templates"]}

    def test_keys_are_the_candidate_order_with_the_currency_after_the_period(self):
        names = [name for name, _ in sql_derived.LOOKUP_ORDER_KEYS]
        # docs/specs/concept-family-coverage.md R2, then unit-aware-lookup.md's tie-break.
        assert names == ["role", "period", "reporting_currency", "presented", "rank",
                         "valued", "precision", "fact_id"]

    def test_one_sql_key_per_candidate_order_key(self):
        from src.retriever.xbrl.family_resolution import _order_key

        row = {"concept_role": "primary", "period_end": "2025-01-01", "presented": True,
               "concept_pref_rank": 1, "value": 1.0, "decimals": -6, "fact_id": 1}
        # Every key of the lookup's order has its SQL term; the currency is the extra one.
        assert len(_order_key(row)) == len(sql_derived.LOOKUP_ORDER_KEYS) - 1

    def test_expansion_orders_best_candidate_first(self):
        terms = [t.strip() for t in sql_derived.lookup_order_sql("g").split(",\n")]
        assert terms[0].startswith("CASE WHEN COALESCE(NULLIF(g.concept_role, ''),")
        assert "BETWEEN 50 AND 99 THEN 'component'" in terms[0]  # row_role's rank band
        assert terms[0].endswith("= 'primary' THEN 0 ELSE 1 END")
        assert terms[1] == "g.period_end DESC NULLS LAST"
        assert terms[2].startswith("NOT EXISTS (") and "p.unit = g.unit" in terms[2]
        assert terms[3] == "NOT COALESCE(g.presented, FALSE)"
        assert terms[4] == "g.concept_pref_rank NULLS LAST"
        assert terms[5] == "g.value IS NULL"
        # NULL is an exact fact (INF or no attribute): first, then finer decimals.
        assert terms[6].endswith("WHERE x.fact_id = g.fact_id) DESC NULLS FIRST")
        assert terms[7] == "g.fact_id DESC"

    def test_answerable_leaves_out_components(self):
        assert sql_derived.answerable_sql("g").endswith(" <> 'component'")

    def test_every_single_fact_template_uses_the_lookup_order(self, templates):
        for tid, alias in SINGLE_FACT_TEMPLATES.items():
            raw, parsed = templates[tid]
            assert f"@LOOKUP_ORDER({alias})" in raw["sql"], tid
            assert f"@ANSWERABLE({alias})" in raw["sql"], tid
            assert sql_derived.lookup_order_sql(alias) in parsed.sql, tid
            assert sql_derived.answerable_sql(alias) in parsed.sql, tid
        for tid in YOY_PICK_TEMPLATES:
            raw, parsed = templates[tid]
            # Both legs are picked in Python by the lookup order (docs/specs/yoy-growth-s1.md).
            assert parsed.pick == sql_derived.PICK_S1_YOY, tid
            assert "@LOOKUP_ORDER" not in raw["sql"], tid

    def test_the_five_lookups_use_the_s1_pick(self, templates):
        for tid in S1_PICK_TEMPLATES:
            raw, parsed = templates[tid]
            assert parsed.pick == sql_derived.PICK_S1_LOOKUP, tid
            assert "@LOOKUP_ORDER" not in raw["sql"], tid  # the pick orders, not SQL

    def test_every_single_fact_lookup_template_is_listed(self, templates):
        # A new single-fact lookup over mv_financial_facts must use the lookup order too.
        lookups = {
            tid for tid, (raw, _) in templates.items()
            if raw.get("kind") == "lookup" and "mv_financial_facts" in raw["sql"]
        }
        assert lookups <= set(SINGLE_FACT_TEMPLATES) | set(S1_PICK_TEMPLATES)

    def test_the_pick_orders_by_the_lookup_order_keys(self):
        # The pick's order, key by key, against LOOKUP_ORDER_KEYS (rows differ in one key).
        base = {"segment_axis": None, "concept_role": None, "concept_pref_rank": 1,
                "presented": False, "decimals": None, "value": 1.0, "unit": "USD",
                "concept_family": "Revenue", "concept": "us-gaap:Revenues",
                "segment_member": None, "fiscal_year": 2024, "fiscal_period": "FY",
                "accession_no": "a-24", "filed_date": "2025-02-01", "is_amendment": False,
                "is_canonical_filing": True, "document_period_end": "2024-12-31",
                "period_start": "2024-01-01", "period_end": "2025-01-01"}

        def row(fact_id, **kw):
            return {**base, "fact_id": fact_id, "concept": f"us-gaap:C{fact_id}", **kw}

        pick = sql_derived.s1_lookup_pick
        # role: a substitute loses to the primary; a component never answers
        assert pick([row(2, concept_role="substitute"), row(1)], 2024)["fact_id"] == 1
        assert pick([row(1, concept_role="component")], 2024) is None
        # presented, then rank, then precision (exact first), then larger fact id
        assert pick([row(2), row(1, presented=True)], 2024)["fact_id"] == 1
        assert pick([row(2, concept_pref_rank=3), row(1, concept_pref_rank=2)],
                    2024)["fact_id"] == 1
        assert pick([row(2, decimals=-6), row(1)], 2024)["fact_id"] == 1
        assert pick([row(2, decimals=-6), row(1, decimals=-3)], 2024)["fact_id"] == 1
        assert pick([row(1), row(2)], 2024)["fact_id"] == 2
        # nil and segmented rows never answer; no own column, no pick
        assert pick([row(1, value=None)], 2024) is None
        assert pick([row(1, segment_axis="srt:ProductOrServiceAxis")], 2024) is None
        assert pick([row(1)], 2023) is None
        # the reporting currency (also tagged for an earlier period) beats a translation
        prior = row(9, unit="TWD", period_start="2023-01-01", period_end="2024-01-01")
        rows = [row(20, unit="USD"), row(1, unit="TWD"), prior]
        assert pick(rows, 2024)["unit"] == "TWD"

    def test_no_macro_survives_parsing(self, templates):
        for tid, (_, parsed) in templates.items():
            assert "@" not in parsed.sql, tid


class TestDimensionalS1Picks:
    """The dimensional templates key what the pipeline answers under S1
    (docs/specs/dimensional-s1.md R2-R5). Synthetic rows: two 10-Ks of one filer, the
    later one restating the earlier year."""

    OWN = {"fiscal_year": 2022, "fiscal_period": "FY", "accession_no": "a-22",
           "filed_date": "2023-02-01", "is_amendment": False, "is_canonical_filing": True,
           "document_period_end": "2022-12-31", "unit": "USD", "concept": "us-gaap:Revenues",
           "period_start": "2022-01-01", "period_end": "2023-01-01",
           "has_multiple_dimensions": False}
    LATER = {**OWN, "fiscal_year": 2023, "accession_no": "a-23", "filed_date": "2024-02-01",
             "document_period_end": "2023-12-31"}

    @staticmethod
    def _member(base, fact_id, member, value, **kw):
        return {**base, "fact_id": fact_id, "value": value,
                "segment_axis": "us-gaap:StatementBusinessSegmentsAxis",
                "segment_member": f"x:{member}Member", **kw}

    def test_committed_templates_use_their_picks(self):
        from src.eval.suites.sql_derived import Template

        with open(SPEC_PATH, encoding="utf-8") as f:
            spec = yaml.safe_load(f)
        templates = {t["id"]: (t, Template.parse(t)) for t in spec["templates"]}
        for tid, pick in DIMENSIONAL_PICKS.items():
            raw, parsed = templates[tid]
            assert parsed.pick == pick, tid
            if pick == sql_derived.PICK_S1_BREAKDOWN:
                assert parsed.axes and "%(total_fact_id)s" in raw["sql"], tid
                assert "%(axes)s" in raw["sql"], tid
            else:
                assert " AS matches" in raw["sql"], tid

    def test_breakdown_pick_needs_axes(self):
        spec = _spec(pick="s1_breakdown", kind="breakdown", sql="SELECT 1")
        with pytest.raises(ValueError, match="axes"):
            SqlDerivedBuilder(spec, FakeConn(lambda s, p: ([], [])))

    def test_dimensional_pick_takes_the_latest_report_of_the_matched_member(self):
        own_a = self._member(self.OWN, 1, "A", 100.0, matches=True)
        own_b = self._member(self.OWN, 2, "B", 50.0, matches=False)
        later_a = self._member(self.LATER, 3, "A", 110.0, matches=True)
        # another member name (a rename) is another fact: it restates nothing
        later_renamed = self._member(self.LATER, 4, "ASegment", 120.0, matches=True)
        pick = sql_derived.s1_dimensional_pick
        got = pick([own_a, own_b, later_a, later_renamed], 2022)
        assert [r["fact_id"] for r in got] == [3]
        assert [r["fact_id"] for r in pick([own_a, own_b], 2022)] == [1]
        # a member only the later filing reports is never added; no own column, no pick
        assert pick([own_b, later_a], 2022) == []
        assert pick([own_a], 2023) == []

    def test_dimensional_pick_on_multidim_rows(self):
        dims = {"http://fasb.org/us-gaap/2022:StatementBusinessSegmentsAxis": "x:AMember",
                "http://fasb.org/srt/2022:ProductOrServiceAxis": "x:PMember"}
        later_dims = {"http://fasb.org/us-gaap/2023:StatementBusinessSegmentsAxis":
                      "x:AMember",
                      "http://fasb.org/srt/2023:ProductOrServiceAxis": "x:PMember"}
        own = {**self.OWN, "fact_id": 1, "value": 7.0, "dimensions": dims, "matches": True}
        later = {**self.LATER, "fact_id": 2, "value": 8.0, "dimensions": later_dims,
                 "matches": False}
        got = sql_derived.s1_dimensional_pick([own, later], 2022)
        assert [r["fact_id"] for r in got] == [2]

    def _breakdown_script(self, filer_rows, members_by_total):
        cols = ["segment_name", "value", "fact_id", "total", "member", "parents", "is_line"]
        filer_cols = sorted({k for r in filer_rows for k in r})

        def script(sql, params):
            if "entity_master" in sql:
                return ["trading_symbol", "company_name"], [("NVDA", "NVIDIA")]
            if "total_fact_id" in sql:
                assert params["axes"] == ["StatementBusinessSegmentsAxis"]
                return cols, members_by_total.get(params["total_fact_id"], [])
            if "array_agg" in sql:
                return ["anchor", "members"], []
            return filer_cols, [tuple(r.get(c) for c in filer_cols) for r in filer_rows]

        return script

    def _breakdown_spec(self):
        return _spec(
            id="t-bd", kind="breakdown", pick="s1_breakdown", concept="Revenue",
            axes=["StatementBusinessSegmentsAxis"],
            params={"ticker": "corpus.tickers", "fy": [2022]},
            sql="SELECT * FROM x WHERE fact_id = %(total_fact_id)s AND a = ANY(%(axes)s)",
            expect={"rows": {"entity": "segment_name", "value": "value"},
                    "fact_ids": "fact_id", "total": "total", "line": "is_line",
                    "member": "member", "parents": "parents"},
            min_rows=2,
        )

    def _filer_rows(self):
        def total(base, fact_id):
            return {**base, "fact_id": fact_id, "value": 30.0, "segment_axis": None,
                    "segment_member": None, "concept_pref_rank": 1}
        return [total(self.OWN, 10), self._member(self.OWN, 11, "A", 20.0),
                self._member(self.OWN, 12, "B", 10.0),
                total(self.LATER, 20), self._member(self.LATER, 21, "A", 18.0),
                self._member(self.LATER, 22, "B", 12.0)]

    def test_breakdown_keys_the_latest_filings_split(self):
        members = {
            10: [("A", 20.0, 11, 30.0, "AMember", [], False),
                 ("B", 10.0, 12, 30.0, "BMember", [], False)],
            20: [("A", 18.0, 21, 30.0, "AMember", [], False),
                 ("B", 12.0, 22, 30.0, "BMember", [], False)],
        }
        script = self._breakdown_script(self._filer_rows(), members)
        suite = SqlDerivedBuilder(self._breakdown_spec(), FakeConn(script)).build("standard")
        assert [sorted(c.expected_fact_ids) for c in suite.cases] == [[21, 22]]

    def test_breakdown_falls_back_when_the_later_filing_does_not_split(self):
        members = {
            10: [("A", 20.0, 11, 30.0, "AMember", [], False),
                 ("B", 10.0, 12, 30.0, "BMember", [], False)],
            # the later filing's members do not make its total
            20: [("A", 18.0, 21, 30.0, "AMember", [], False),
                 ("B", 2.0, 22, 30.0, "BMember", [], False)],
        }
        script = self._breakdown_script(self._filer_rows(), members)
        suite = SqlDerivedBuilder(self._breakdown_spec(), FakeConn(script)).build("standard")
        assert [sorted(c.expected_fact_ids) for c in suite.cases] == [[11, 12]]
