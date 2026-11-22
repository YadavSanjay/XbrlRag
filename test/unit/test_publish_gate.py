"""Unit tests for the WS-1 / B5 publish gate.

Two groups:
- TestGateDecision: the pure GATE-A/GATE-B decision logic (PublishGate._decide) —
  no database. GATE-A (anchor) is a HARD gate; GATE-B (completeness + sum-of-parts)
  is ADVISORY unless gate_b_enforce=True.
- TestPublishGateClause: the read-time deny-list SQL fragment
  (publish_gate_clause) against a fake connection — inert when publish_manifest
  is absent, correlated to the surface table when present.
"""
import src.retriever.xbrl.xbrl_factstore_lookup as fsl
from src.ingest.sec_edgar.xbrl.promotion import PublishGate, PublishReport
from src.retriever.xbrl.xbrl_factstore_lookup import publish_gate_clause


def _row(accession="0000-1", cik="0000320193", fy=2025, fp="FY", form="10-K",
         anchor=1.0, completeness=0.80, sop=0):
    return {
        "accession_no": accession, "cik": cik, "fiscal_year": fy,
        "fiscal_period": fp, "form_type": form,
        "anchor_coverage_pct": anchor, "completeness_pct": completeness,
        "sum_of_parts_failures": sop,
    }


# ---------------------------------------------------------------------------
# Gate decision logic (no DB)
# ---------------------------------------------------------------------------

class TestGateDecision:
    def test_clean_filing_is_published(self):
        gate = PublishGate()
        r = gate._decide(_row(anchor=1.0, completeness=0.90, sop=0))
        assert r.gate_a_passed and r.gate_b_passed and r.is_published
        assert r.held_back_reasons == []

    def test_gate_a_fail_holds_back_even_when_gate_b_clean(self):
        """A wrong-source-file load (low anchor coverage) is always blocked."""
        gate = PublishGate()
        r = gate._decide(_row(anchor=0.10, completeness=0.99, sop=0))
        assert not r.gate_a_passed
        assert not r.is_published
        assert any("GATE-A" in x and "wrong-source-file" in x for x in r.held_back_reasons)

    def test_gate_a_missing_anchor_is_fail(self):
        gate = PublishGate()
        r = gate._decide(_row(anchor=None))
        assert not r.gate_a_passed and not r.is_published

    def test_low_completeness_is_advisory_by_default(self):
        """GATE-B completeness below threshold does NOT hold back when advisory."""
        gate = PublishGate(gate_b_enforce=False)
        r = gate._decide(_row(anchor=1.0, completeness=0.40, sop=0))
        assert r.gate_a_passed
        assert not r.gate_b_passed                 # signal failed
        assert r.is_published                      # ...but advisory, so still published
        assert any("advisory" in x for x in r.held_back_reasons)

    def test_low_completeness_blocks_when_enforced(self):
        gate = PublishGate(gate_b_enforce=True)
        r = gate._decide(_row(anchor=1.0, completeness=0.40, sop=0))
        assert r.gate_a_passed and not r.gate_b_passed
        assert not r.is_published
        assert any("completeness" in x and "advisory" not in x for x in r.held_back_reasons)

    def test_sum_of_parts_failure_advisory_vs_enforced(self):
        advisory = PublishGate(gate_b_enforce=False)._decide(_row(sop=5))
        assert advisory.is_published and not advisory.gate_b_passed

        enforced = PublishGate(gate_b_enforce=True)._decide(_row(sop=5))
        assert not enforced.is_published
        assert any("sum-of-parts" in x for x in enforced.held_back_reasons)

    def test_missing_completeness_not_penalized(self):
        """A filing with no completeness row is judged on GATE-A + sum-of-parts only."""
        gate = PublishGate(gate_b_enforce=True)
        r = gate._decide(_row(anchor=1.0, completeness=None, sop=0))
        assert r.gate_b_passed and r.is_published

    def test_custom_thresholds_respected(self):
        strict = PublishGate(min_anchor=0.99, gate_b_enforce=False)
        r = strict._decide(_row(anchor=0.97))
        assert not r.gate_a_passed and not r.is_published


# ---------------------------------------------------------------------------
# GATE-B sum-of-parts: per-accession strict failures vs the exception list
# (docs/specs/calc-check-c11.md, Requirement 5)
# ---------------------------------------------------------------------------

ROLE = "http://example.com/role/BalanceSheet"
ASSETS = "http://fasb.org/us-gaap/2024:Assets"


def _calc(status, end="2025-01-01", dimensional=False, start=None):
    return {"link_role": ROLE, "parent": ASSETS, "period_start": start, "period_end": end,
            "unit": "USD", "status": status, "dimensional": dimensional}


def _key(acc, end, start=None):
    return (acc, ROLE, ASSETS, start, end, "USD")


class TestCalcGateWiring:
    def _decide(self, calc_results, exceptions, enforce=True, accession="ACC-1"):
        gate = PublishGate(gate_b_enforce=enforce, calc_exceptions=exceptions)
        row = _row(accession=accession, completeness=0.9)
        gate._attach_calc_decision(row, calc_results)
        return row, gate._decide(row)

    def test_listed_strict_failure_does_not_hold_back(self):
        row, r = self._decide([_calc("fail")], {_key("ACC-1", "2025-01-01")})
        assert row["sum_of_parts_failures"] == 0
        assert row["calc_decision"]["excepted_failures"] == 1
        assert r.gate_b_passed and r.is_published

    def test_new_strict_failure_holds_back_when_enforced(self):
        row, r = self._decide([_calc("fail"), _calc("fail", "2024-01-01")],
                              {_key("ACC-1", "2025-01-01")})
        assert row["sum_of_parts_failures"] == 1
        assert not r.gate_b_passed and not r.is_published
        assert any("1 new strict" in x and "1 more on the exception list" in x
                   for x in r.held_back_reasons)

    def test_exception_of_another_accession_does_not_excuse(self):
        _, r = self._decide([_calc("fail")], {_key("ACC-2", "2025-01-01")})
        assert not r.is_published

    def test_non_strict_results_never_hold_back(self):
        results = [_calc("partial"), _calc("fail", dimensional=True), _calc("inapplicable")]
        row, r = self._decide(results, frozenset())
        assert row["sum_of_parts_failures"] == 0 and r.is_published
        assert row["calc_decision"]["partial"] == 1
        assert row["calc_decision"]["dimensional_failures"] == 1

    def test_advisory_by_default(self):
        _, r = self._decide([_calc("fail")], frozenset(), enforce=False)
        assert not r.gate_b_passed and r.is_published

    def test_committed_exception_list_loads(self):
        exceptions = PublishGate().calc_exceptions
        assert exceptions and all(len(k) == 6 for k in exceptions)


# ---------------------------------------------------------------------------
# Publish report
# ---------------------------------------------------------------------------

class TestPublishReport:
    def test_report_to_dict_lists_only_held_back(self):
        gate = PublishGate(gate_b_enforce=True)
        published = gate._decide(_row(accession="A", anchor=1.0, completeness=0.9))
        held = gate._decide(_row(accession="B", anchor=0.1))
        report = PublishReport(
            evaluated=2, published=1, held_back=1, gate_b_enforced=True,
            results=[published, held],
        )
        d = report.to_dict()
        assert d["evaluated"] == 2 and d["published"] == 1 and d["held_back"] == 1
        accs = [f["accession_no"] for f in d["held_back_filings"]]
        assert accs == ["B"]


# ---------------------------------------------------------------------------
# Read-time deny-list SQL fragment (fake connection)
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, regclass):
        self._regclass = regclass
        self._last = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._last = sql

    def fetchone(self):
        # publish_gate_clause probes: SELECT to_regclass(...) IS NOT NULL
        return (self._regclass is not None,)


class _FakeConn:
    def __init__(self, regclass, dbname="fake_db"):
        self._regclass = regclass
        self._dbname = dbname

    def get_dsn_parameters(self):
        return {"dbname": self._dbname}

    def cursor(self):
        return _FakeCursor(self._regclass)


class TestPublishGateClause:
    def setup_method(self):
        fsl._PUBLISH_MANIFEST_PRESENT.clear()

    def teardown_method(self):
        fsl._PUBLISH_MANIFEST_PRESENT.clear()

    def test_inert_when_manifest_absent(self):
        conn = _FakeConn(regclass=None, dbname="absent_db")
        assert publish_gate_clause(conn, "mv_financial_facts") == ""

    def test_clause_when_manifest_present(self):
        conn = _FakeConn(regclass="publish_manifest", dbname="present_db")
        clause = publish_gate_clause(conn, "mv_financial_facts")
        assert "NOT EXISTS" in clause
        assert "publish_manifest pm" in clause
        assert "pm.cik = mv_financial_facts.cik" in clause
        assert "pm.fiscal_year = mv_financial_facts.fiscal_year" in clause
        assert "pm.fiscal_period = mv_financial_facts.fiscal_period" in clause
        assert "pm.is_published = FALSE" in clause

    def test_clause_correlates_to_the_given_surface(self):
        conn = _FakeConn(regclass="publish_manifest", dbname="present_db2")
        clause = publish_gate_clause(conn, "mv_geographic_facts")
        assert "pm.cik = mv_geographic_facts.cik" in clause

    def test_presence_cached_once_true(self):
        conn = _FakeConn(regclass="publish_manifest", dbname="cache_db")
        assert publish_gate_clause(conn, "mv_financial_facts") != ""
        # Now even a conn that reports absent for the same dbname stays cached True.
        stale = _FakeConn(regclass=None, dbname="cache_db")
        assert publish_gate_clause(stale, "mv_financial_facts") != ""
