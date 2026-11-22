"""Tests for XbrlFactstoreLookup (design doc §2.2 lookup branch).

Two groups:
- TestPureHelpers: dimension/period/label parsing — no database.
- Test*Lookup (requires_db): run the resolver against the *live* xbrl_db and
  assert the values returned by scripts/xbrl/analyst_queries.sql. The ground-
  truth numbers below were captured from the current xbrl_db (12 covered filers,
  FY2022–FY2026); they validate the resolver end-to-end against real iXBRL facts.

Integration tests skip automatically if xbrl_db is unreachable.
"""

import pytest

from src.retriever.xbrl.xbrl_factstore_lookup import (
    LookupStatus,
    XbrlFactstoreLookup,
    _local_name,
    _norm_label,
)

NVDA = "0001045810"
AAPL = "0000320193"
MSFT = "0000789019"


def _db_available() -> bool:
    try:
        lk = XbrlFactstoreLookup.from_env()
        with lk._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM mv_financial_facts LIMIT 1")
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(not _db_available(), reason="xbrl_db not reachable")


@pytest.fixture(scope="module")
def factstore() -> XbrlFactstoreLookup:
    return XbrlFactstoreLookup.from_env()


def _step(
    step_id, cik, fy, concept_family, dimensions="NONE", section_hint=None, **extra
):
    return {
        "id": step_id,
        "op": "lookup",
        "cik": cik,
        "fy": fy,
        "concept_family": concept_family,
        "dimensions": dimensions,
        "section_hint": section_hint,
        **extra,
    }


# ---------------------------------------------------------------------------
# Pure helpers (no DB)
# ---------------------------------------------------------------------------


class TestPureHelpers:
    def test_period_type_duration_vs_instant(self):
        assert XbrlFactstoreLookup._derive_period_type("Revenue") == "duration"
        assert XbrlFactstoreLookup._derive_period_type("NetIncome") == "duration"
        assert XbrlFactstoreLookup._derive_period_type("TotalAssets") == "instant"
        assert (
            XbrlFactstoreLookup._derive_period_type("CashAndEquivalents") == "instant"
        )

    def test_derive_duration_kind(self):
        d = XbrlFactstoreLookup._derive_duration_kind
        assert d("FY", False) == "annual"
        assert d("Q1", False) == "quarter"
        assert d("Q1", True) == "quarter"  # Q1 YTD == the quarter
        assert d("Q2", False) == "quarter"  # discrete quarter (default)
        assert d("Q2", True) == "half"  # year-to-date
        assert d("Q3", False) == "quarter"
        assert d("Q3", True) == "nine_month"
        assert d("Q4", False) == "quarter"

    def test_local_axis_from_fuzzy_hint(self):
        assert XbrlFactstoreLookup._local_axis("product") == "ProductOrServiceAxis"
        assert (
            XbrlFactstoreLookup._local_axis("geography") == "StatementGeographicalAxis"
        )
        assert XbrlFactstoreLookup._local_axis("customer") == "MajorCustomersAxis"

    def test_local_axis_from_qname(self):
        assert (
            XbrlFactstoreLookup._local_axis("srt:ProductOrServiceAxis")
            == "ProductOrServiceAxis"
        )
        assert (
            XbrlFactstoreLookup._local_axis("us-gaap:MajorCustomersAxis")
            == "MajorCustomersAxis"
        )

    def test_local_axis_unknown_hint(self):
        assert XbrlFactstoreLookup._local_axis("flavor") is None

    def test_parse_dimensions_none(self):
        lk = XbrlFactstoreLookup()
        specs, err = lk._parse_dimensions("NONE", [])
        assert specs == [] and err is None

    def test_parse_dimensions_single(self):
        lk = XbrlFactstoreLookup()
        specs, err = lk._parse_dimensions({"product": "Data Center"}, [])
        assert err is None and len(specs) == 1
        assert specs[0].local_axis == "ProductOrServiceAxis"
        assert specs[0].member_hint == "data center"
        assert specs[0].member_qname is None

    def test_parse_dimensions_member_qname(self):
        lk = XbrlFactstoreLookup()
        specs, _ = lk._parse_dimensions(
            {"srt:ProductOrServiceAxis": "nvda:DataCenterMember"}, []
        )
        assert specs[0].member_qname == "nvda:DataCenterMember"

    def test_parse_dimensions_invalid(self):
        lk = XbrlFactstoreLookup()
        specs, err = lk._parse_dimensions("TOTAL", [])
        assert err == LookupStatus.UNRESOLVED_DIMENSION

    def test_norm_label_strips_member_suffix(self):
        assert _norm_label("Data Center [Member]") == "data center"
        assert _norm_label("UNITED STATES") == "united states"
        assert _norm_label("Customer One [Member]") == "customer one"

    def test_local_name_helper(self):
        assert (
            _local_name("http://fasb.org/srt/2025:ProductOrServiceAxis")
            == "ProductOrServiceAxis"
        )
        assert _local_name("country:US") == "US"

    def test_multidim_row_matches(self):
        from src.retriever.xbrl.xbrl_factstore_lookup import _DimSpec

        labels = {
            "http://fasb.org/srt/2025:MajorCustomersAxis": "Customer One [Member]",
            "http://fasb.org/us-gaap/2025:ConcentrationRiskByBenchmarkAxis": "Revenue Benchmark [Member]",
        }
        specs = [
            _DimSpec("customer", "MajorCustomersAxis", "customer one", None),
            _DimSpec("benchmark", "ConcentrationRiskByBenchmarkAxis", "revenue", None),
        ]
        assert XbrlFactstoreLookup._multidim_row_matches(labels, specs)
        # a requested member not present -> no match
        specs[0] = _DimSpec("customer", "MajorCustomersAxis", "customer nine", None)
        assert not XbrlFactstoreLookup._multidim_row_matches(labels, specs)

    def test_section_consistency(self):
        # 'statement' is a substring of 'statements' in the path -> consistent
        assert XbrlFactstoreLookup._section_consistent(
            "Income Statement", "Statement - CONSOLIDATED STATEMENTS OF OPERATIONS"
        )
        assert XbrlFactstoreLookup._section_consistent(
            "Balance Sheet", "Statement - CONSOLIDATED BALANCE SHEETS"
        )
        # no shared significant word -> not consistent
        assert not XbrlFactstoreLookup._section_consistent(
            "Cash Flow", "Statement - CONSOLIDATED BALANCE SHEETS"
        )

    def test_non_lookup_op_errors_without_db(self):
        lk = XbrlFactstoreLookup()
        result = lk.lookup({"id": "c1", "op": "compute", "expr": "a+b"})
        assert result.status == LookupStatus.ERROR

    def test_missing_required_fields_errors_without_db(self):
        lk = XbrlFactstoreLookup()
        result = lk.lookup(
            {"id": "x", "op": "lookup", "cik": NVDA}
        )  # no fy/concept_family
        assert result.status == LookupStatus.ERROR


# ---------------------------------------------------------------------------
# Undimensioned lookups vs analyst_queries.sql §1 / §5
# ---------------------------------------------------------------------------


@requires_db
class TestUndimensionedLookup:
    def test_nvda_total_revenue_fy26(self, factstore):
        """analyst_queries.sql 1a — segment_axis IS NULL total revenue."""
        result = factstore.lookup(
            _step("tot26", NVDA, 2026, "Revenue", "NONE", "Income Statement")
        )
        assert result.status == LookupStatus.OK
        assert result.value == pytest.approx(215938000000.0)
        assert result.unit == "USD"
        assert result.citation.fact_id is not None
        assert result.confidence >= factstore.min_confidence

    def test_aapl_total_revenue_fy24(self, factstore):
        """analyst_queries.sql 1b-style — different filer/period."""
        result = factstore.lookup(
            _step("aaplrev", AAPL, 2024, "Revenue", "NONE", "Income Statement")
        )
        assert result.status == LookupStatus.OK
        assert result.value == pytest.approx(391035000000.0)

    def test_nvda_net_income_fy26(self, factstore):
        result = factstore.lookup(
            _step("ni26", NVDA, 2026, "NetIncome", "NONE", "Income Statement")
        )
        assert result.status == LookupStatus.OK
        assert result.value == pytest.approx(120067000000.0)

    def test_aapl_total_assets_fy24_uses_instant(self, factstore):
        """analyst_queries.sql 5b — balance-sheet item must use period_type='instant'."""
        result = factstore.lookup(
            _step("ta24", AAPL, 2024, "TotalAssets", "NONE", "Balance Sheet")
        )
        assert result.status == LookupStatus.OK
        assert result.value == pytest.approx(364980000000.0)

    def test_year_with_no_filing_is_not_found(self, factstore):
        result = factstore.lookup(
            _step("none", NVDA, 2019, "Revenue", "NONE", "Income Statement")
        )
        assert result.status == LookupStatus.NOT_FOUND
        assert result.value is None


# ---------------------------------------------------------------------------
# Period-aware lookups (10-Q support): fiscal_period + ytd
# ---------------------------------------------------------------------------


@requires_db
class TestPeriodAwareLookup:
    def test_fy_default_is_annual(self, factstore):
        """No fiscal_period => 'FY' => the annual figure (unchanged behavior)."""
        result = factstore.lookup(_step("fy", NVDA, 2026, "Revenue", "NONE"))
        assert result.status == LookupStatus.OK
        assert result.value == pytest.approx(215938000000.0)

    def test_aapl_q2_discrete_quarter(self, factstore):
        """AAPL FY2026 Q2 discrete-quarter revenue (~3 months)."""
        result = factstore.lookup(
            _step("q2", AAPL, 2026, "Revenue", "NONE", fiscal_period="Q2")
        )
        assert result.status == LookupStatus.OK
        assert result.value == pytest.approx(111184000000.0)
        assert result.candidates[0].period_type == "duration"

    def test_aapl_q2_ytd_is_cumulative(self, factstore):
        """Same quarter, ytd=True => the cumulative (6-month) figure, distinct
        from and larger than the discrete quarter."""
        result = factstore.lookup(
            _step("q2ytd", AAPL, 2026, "Revenue", "NONE", fiscal_period="Q2", ytd=True)
        )
        assert result.status == LookupStatus.OK
        assert result.value == pytest.approx(254940000000.0)
        assert result.value > 111184000000.0

    def test_quarter_and_annual_differ(self, factstore):
        """A quarter lookup must not collapse to the annual figure."""
        q = factstore.lookup(
            _step("q", MSFT, 2026, "Revenue", "NONE", fiscal_period="Q3")
        )
        assert q.status == LookupStatus.OK
        assert q.value == pytest.approx(82886000000.0)


# ---------------------------------------------------------------------------
# Point-in-time (as_of / as_originally_reported)
# ---------------------------------------------------------------------------

AMD = "0000002488"  # has a FY2024 10-K filed 2025-02-05


@requires_db
class TestPointInTime:
    def test_as_of_before_filing_is_not_found(self, factstore):
        """No look-ahead: the FY2024 value can't be read before its 10-K exists."""
        r = factstore.lookup(
            _step("aof", AMD, 2024, "Revenue", "NONE", as_of="2024-06-01")
        )
        assert r.status == LookupStatus.NOT_FOUND

    def test_as_of_after_filing_resolves(self, factstore):
        r = factstore.lookup(
            _step("aof2", AMD, 2024, "Revenue", "NONE", as_of="2025-03-01")
        )
        assert r.status == LookupStatus.OK
        assert r.value == pytest.approx(25785000000.0)

    def test_as_of_matches_latest_when_no_restatement(self, factstore):
        latest = factstore.lookup(_step("l", AMD, 2024, "Revenue", "NONE"))
        original = factstore.lookup(
            _step("o", AMD, 2024, "Revenue", "NONE", as_originally_reported=True)
        )
        assert latest.status == original.status == LookupStatus.OK
        assert latest.value == original.value == pytest.approx(25785000000.0)


# ---------------------------------------------------------------------------
# Single-dimension lookups vs analyst_queries.sql §3 / §4
# ---------------------------------------------------------------------------


@requires_db
class TestDimensionalLookup:
    def test_nvda_data_center_revenue_fy26(self, factstore):
        """analyst_queries.sql 3a/3d — product/service dimension via fuzzy hint."""
        result = factstore.lookup(
            _step(
                "dc26",
                NVDA,
                2026,
                "Revenue",
                {"product": "data center"},
                "Disaggregated Revenue",
            )
        )
        assert result.status == LookupStatus.OK
        assert result.value == pytest.approx(193737000000.0)

    def test_nvda_gaming_revenue_fy26(self, factstore):
        result = factstore.lookup(
            _step(
                "gm26",
                NVDA,
                2026,
                "Revenue",
                {"product": "gaming"},
                "Disaggregated Revenue",
            )
        )
        assert result.status == LookupStatus.OK
        assert result.value == pytest.approx(16042000000.0)

    def test_nvda_us_geographic_revenue_fy26(self, factstore):
        """analyst_queries.sql 4a — StatementGeographicalAxis, country:US member."""
        result = factstore.lookup(
            _step(
                "us26",
                NVDA,
                2026,
                "Revenue",
                {"geography": "united states"},
                "Geographic Revenue",
            )
        )
        assert result.status == LookupStatus.OK
        assert result.value == pytest.approx(149617000000.0)

    def test_data_center_is_year_correct_for_prior_fy(self, factstore):
        """The FY2025 Data Center fact comes from the FY2025 filing, not the
        FY2026 comparative column — i.e. de-dup picks the as-originally-reported row."""
        fy26 = factstore.lookup(
            _step("dc26", NVDA, 2026, "Revenue", {"product": "data center"})
        )
        fy25 = factstore.lookup(
            _step("dc25", NVDA, 2025, "Revenue", {"product": "data center"})
        )
        assert fy26.status == LookupStatus.OK and fy25.status == LookupStatus.OK
        assert fy26.value == pytest.approx(193737000000.0)
        assert fy25.value == pytest.approx(115186000000.0)
        assert fy26.citation.fact_id != fy25.citation.fact_id

    def test_total_and_dimension_select_different_facts(self, factstore):
        """The segment_axis IS NULL vs LIKE predicate is the key invariant (doc §2/§3.4):
        total revenue and Data Center revenue must resolve to different facts."""
        tot = factstore.lookup(
            _step("tot26", NVDA, 2026, "Revenue", "NONE", "Income Statement")
        )
        dc = factstore.lookup(
            _step("dc26", NVDA, 2026, "Revenue", {"product": "data center"})
        )
        assert tot.value != dc.value
        assert tot.citation.fact_id != dc.citation.fact_id

    def test_unresolved_member_returns_unresolved_dimension(self, factstore):
        result = factstore.lookup(
            _step(
                "x",
                NVDA,
                2026,
                "Revenue",
                {"product": "quantum teleportation"},
                "Disaggregated Revenue",
            )
        )
        assert result.status == LookupStatus.UNRESOLVED_DIMENSION

    def test_broad_member_hint_is_ambiguous(self, factstore):
        """A hint that matches multiple product members yields several distinct
        values at the latest period -> AMBIGUOUS with a candidate list (§3.6).

        "ing" is inside "Gaming" and "Networking" and names neither exactly. (The old
        hint "member" matched every label only through the literal "[Member]" marker,
        which label matching now ignores: docs/specs/segment-label-match.md.)"""
        result = factstore.lookup(
            _step(
                "amb",
                NVDA,
                2026,
                "Revenue",
                {"product": "ing"},
                "Disaggregated Revenue",
            )
        )
        assert result.status == LookupStatus.AMBIGUOUS
        assert len(result.candidates) > 1


# ---------------------------------------------------------------------------
# Confidence threshold + multi-dimension path
# ---------------------------------------------------------------------------


@requires_db
class TestMultidimConfidence:
    """A 2-dimension lookup could never earn the +0.2 exact-dimension credit
    (_dimension_match_exact returned False for len(dim_specs) != 1), capping its
    confidence below the 0.70 threshold — so a correctly-resolved multi-axis fact was
    reported AMBIGUOUS by construction, and an AMBIGUOUS step's value is unusable."""

    @staticmethod
    def _candidate(dimension_labels):
        from src.retriever.xbrl.xbrl_factstore_lookup import Citation, FactCandidate

        return FactCandidate(
            fact_id=1,
            value=1.0,
            unit="USD",
            period_type="instant",
            period_start=None,
            period_end="2024-09-29",
            concept="us-gaap:X",
            concept_family=None,
            concept_pref_rank=1,
            segment_axis=None,
            segment_member=None,
            segment_label=None,
            section_path=None,
            citation=Citation(fact_id=1),
            dimension_labels=dimension_labels,
        )

    @staticmethod
    def _specs(*pairs):
        from src.retriever.xbrl.xbrl_factstore_lookup import _DimSpec

        return [_DimSpec(k, None, v, None) for k, v in pairs]

    def test_two_exact_dimension_matches_earn_the_credit(self):
        cand = self._candidate(
            {
                "u:FinancialInstrumentAxis": "Commercial Paper [Member]",
                "u:FairValueByFairValueHierarchyLevelAxis": "Level 2 [Member]",
            }
        )
        specs = self._specs(("instrument", "commercial paper"), ("level", "level 2"))
        assert XbrlFactstoreLookup._dimension_match_exact(cand, specs) is True

    def test_a_one_to_one_substring_match_on_a_fully_specified_fact_does(self):
        """ "level 2" inside "Fair Value, Inputs, Level 2" is credited when the hints
        cover the fact's dimensions one-to-one (docs/specs/multidim-full-match.md)."""
        cand = self._candidate(
            {
                "u:FinancialInstrumentAxis": "Commercial Paper [Member]",
                "u:FairValueByFairValueHierarchyLevelAxis": "Fair Value, Inputs, Level 2 [Member]",
            }
        )
        specs = self._specs(("instrument", "commercial paper"), ("level", "level 2"))
        assert XbrlFactstoreLookup._dimension_match_exact(cand, specs) is True

    def test_an_under_specified_match_does_not(self):
        """The same hints against a fact with a third, unmentioned dimension are not
        credited: the question did not pin that dimension down."""
        cand = self._candidate(
            {
                "u:FinancialInstrumentAxis": "Commercial Paper [Member]",
                "u:FairValueByFairValueHierarchyLevelAxis": "Fair Value, Inputs, Level 2 [Member]",
                "u:FairValueByAssetClassAxis": "Debt Securities [Member]",
            }
        )
        specs = self._specs(("instrument", "commercial paper"), ("level", "level 2"))
        assert XbrlFactstoreLookup._dimension_match_exact(cand, specs) is False

    def test_a_missing_dimension_is_not_exact(self):
        cand = self._candidate(
            {"u:FinancialInstrumentAxis": "Commercial Paper [Member]"}
        )
        specs = self._specs(("instrument", "commercial paper"), ("level", "level 2"))
        assert XbrlFactstoreLookup._dimension_match_exact(cand, specs) is False

    def test_no_dimension_labels_is_not_exact(self):
        specs = self._specs(("a", "x"), ("b", "y"))
        assert (
            XbrlFactstoreLookup._dimension_match_exact(self._candidate(None), specs)
            is False
        )


@requires_db
class TestAxisHintRelaxation:
    """AXIS_HINT_TO_LOCAL pins the planner's axis word to one axis, and the member is
    matched only within it. But "segment" in analyst English means any breakdown
    dimension, and the taxonomy puts most revenue members on ProductOrServiceAxis
    (59) rather than StatementBusinessSegmentsAxis (31). A good member hint was
    therefore missing purely on the axis — the dominant cause of breakdown failures
    (90 of 216 lookup steps NOT_FOUND in the WS-9 baseline)."""

    def test_member_on_a_different_axis_is_found(self, factstore):
        """NVIDIA's Gaming is a ProductOrService member; the planner asked for it as
        a business segment. This returned NOT_FOUND before the fallback."""
        result = factstore.lookup(
            _step("gm24", NVDA, 2024, "Revenue", {"segment": "gaming"})
        )
        assert result.status == LookupStatus.OK
        assert result.value == pytest.approx(10447000000.0)

    def test_the_relaxation_is_recorded_in_notes(self, factstore):
        """A widened match must be visible in the trace, never silent."""
        result = factstore.lookup(
            _step("gm24", NVDA, 2024, "Revenue", {"segment": "gaming"})
        )
        assert any("axis hint relaxed" in n for n in (result.resolution_notes or []))

    def test_axis_description_passed_as_a_member_stays_unresolved(self, factstore):
        """The planner sometimes passes the breakdown dimension through as the member
        ("break down by product or service segment"). Widening the search for one of
        those could only produce a spurious match."""
        result = factstore.lookup(
            _step(
                "seg24",
                "0001018724",
                2024,
                "Revenue",
                {"product": "product or service segment"},
            )
        )
        assert result.status != LookupStatus.OK
        assert not any(
            "axis hint relaxed" in n for n in (result.resolution_notes or [])
        )

    def test_a_hit_on_the_hinted_axis_is_not_widened(self, factstore):
        """The fallback runs only when the hinted axis found nothing, so it can never
        override the axis the planner actually asked for."""
        result = factstore.lookup(
            _step("dc26", NVDA, 2026, "Revenue", {"product": "data center"})
        )
        assert result.status == LookupStatus.OK
        assert result.value == pytest.approx(193737000000.0)
        assert not any(
            "axis hint relaxed" in n for n in (result.resolution_notes or [])
        )

    def test_an_unknown_member_is_still_not_found(self, factstore):
        """Relaxing the axis must not turn a genuine miss into a match."""
        result = factstore.lookup(
            _step(
                "zz24", NVDA, 2024, "Revenue", {"segment": "nonexistent business line"}
            )
        )
        assert result.status != LookupStatus.OK


MU = "0000723125"


@requires_db
class TestNeutralConsolidationMember:
    """A segment figure is also tagged ConsolidationItemsAxis = OperatingSegmentsMember,
    so it is a 2-dimension fact the flat view files under its first dimension. A
    one-hint lookup that omits the neutral member must still find it
    (docs/specs/multidim-recovery.md). Values are the stored facts (2026-10-14 corpus)."""

    def test_segment_without_the_neutral_member_resolves(self, factstore):
        """NVIDIA FY2025 Graphics operating income: NOT_FOUND before. The FY2025 10-K's
        fact 65496 as the latest report restates it (S1, docs/specs/dimensional-s1.md R3b):
        the FY2026 10-K's fact 1136, same member pair and value."""
        result = factstore.lookup(
            _step("gfx25", NVDA, 2025, "OperatingIncome", {"segment": "graphics"})
        )
        assert result.status == LookupStatus.OK
        assert result.value == pytest.approx(5_085_000_000.0)
        assert result.citation.fact_id == 1136
        assert result.citation.accession_no == "0001045810-26-000021"
        original = factstore.lookup(_step(
            "gfx25o", NVDA, 2025, "OperatingIncome", {"segment": "graphics"},
            as_originally_reported=True,
        ))
        assert original.citation.fact_id == 65496
        assert any("neutral consolidation" in n for n in result.resolution_notes)

    def test_raw_local_name_labels_match_spaced_hints(self, factstore):
        """AMD FY2025 labels are raw local names ("DatacenterMember"); the planner
        writes "data center", and on a product axis, which the fallback ignores."""
        result = factstore.lookup(
            _step("dc25", AMD, 2025, "Revenue", {"product": "data center"})
        )
        assert result.status == LookupStatus.OK
        assert result.value == pytest.approx(16_635_000_000.0)

    def test_a_flat_hit_is_not_replaced(self, factstore):
        """The fallback runs only when the flat path and the axis retry found nothing."""
        result = factstore.lookup(
            _step("gm24", NVDA, 2024, "Revenue", {"segment": "gaming"})
        )
        assert result.status == LookupStatus.OK
        assert result.value == pytest.approx(10447000000.0)
        assert not any("neutral consolidation" in n for n in result.resolution_notes)

    def test_ordinary_multidim_path_keeps_its_rows(self, factstore):
        """The SQL hint filter is confined to the fallback: on the ordinary 2-hint path it
        would surface a rank-4 LongTermDebtNoncurrent row with another value, and the
        family-wide distinct-value check would then refuse this resolved fact."""
        result = factstore.lookup(
            _step(
                "tla24", MU, 2024, "Debt",
                {"instrument": "2027 term loan a", "type": "notes payable to banks"},
            )
        )
        assert result.status == LookupStatus.OK
        assert result.value == pytest.approx(1_063_000_000.0)


@requires_db
class TestConfidenceAndMultidim:
    def test_high_confidence_threshold_flags_ambiguous(self):
        """A clean single-fact lookup (confidence 0.9) is flagged AMBIGUOUS when
        the caller demands >0.9 confidence."""
        strict = XbrlFactstoreLookup.from_env(min_confidence=0.95)
        result = strict.lookup(
            _step("tot26", NVDA, 2026, "Revenue", "NONE", "Income Statement")
        )
        assert result.status == LookupStatus.AMBIGUOUS
        assert result.value == pytest.approx(215938000000.0)  # value still surfaced

    def test_customer_concentration_uses_multidim_path(self, factstore):
        """A 2-axis customer-concentration request routes to
        mv_financial_facts_multidim and returns a percentage with candidates
        (this data set tags the same customer under several benchmarks/segments,
        so the resolver correctly reports it as AMBIGUOUS — design §3.6 / §6.3)."""
        result = factstore.lookup(
            _step(
                "cust1",
                NVDA,
                2026,
                "ConcentrationRiskPercentage",
                {"customer": "customer one", "benchmark": "accounts receivable"},
                "Concentration of Credit Risk",
            )
        )
        assert result.status in (LookupStatus.OK, LookupStatus.AMBIGUOUS)
        assert result.value is not None
        assert (
            0.0 < result.value <= 1.0
        )  # a concentration percentage, not a dollar figure
        assert len(result.candidates) >= 1


ORCL = "0001341439"
TSLA = "0001318605"


@requires_db
class TestWithinConceptConfidence:
    """The "one value" credit is judged within the winning concept, with a tie guard and
    a Debt component guard (docs/specs/within-concept-confidence.md). Values are the
    stored facts (2026-10-15 corpus, after the concept_mapping reload)."""

    def test_a_lower_ranked_neighbour_no_longer_blocks(self, factstore):
        """Oracle FY2024: NetIncomeLoss (rank 1) vs ProfitLoss (rank 2) — was AMBIGUOUS."""
        result = factstore.lookup(_step("ni24", ORCL, 2024, "NetIncome"))
        assert result.status == LookupStatus.OK
        assert result.value == pytest.approx(10_467_000_000.0)

    def test_debt_components_still_block(self, factstore):
        """Tesla FY2024 "debt current": rank 1 is LongTermDebt (434M) but the question is
        DebtCurrent (54M); the component guard keeps it refused, not confidently wrong."""
        result = factstore.lookup(
            _step("dc24", TSLA, 2024, "Debt",
                  {"a": "energy asset backed notes", "b": "nonrecourse debt"})
        )
        assert result.status != LookupStatus.OK
