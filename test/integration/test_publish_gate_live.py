"""Live-DB integration tests for the WS-1 / B5 publish gate.

Run against the local xbrl_db (skip automatically if unreachable). They exercise:
- the four A1 gate MVs exist and are queryable;
- PublishGate.evaluate() populates publish_manifest (advisory -> everything with
  good anchor coverage is published);
- GATE-B enforcement holds back a known-sparse filer (per-accession, restored after);
- the read-time deny-list actually removes a held-back filing from lookup results.

The read-time test inserts a SYNTHETIC held-back manifest row (its own accession
number) keyed on a real filing's (cik, fiscal_year, fiscal_period), asserts the
lookup is excluded, then deletes only that synthetic row — the real promotion
rows are never mutated.
"""
import os

import psycopg2
import pytest
from dotenv import load_dotenv

import src.retriever.xbrl.xbrl_factstore_lookup as fsl
from src.ingest.sec_edgar.xbrl.promotion import PublishGate
from src.retriever.xbrl.xbrl_factstore_lookup import LookupStatus, XbrlFactstoreLookup

NVDA = "0001045810"
TSM = "0001046179"   # completeness ~0.33 across years -> fails GATE-B when enforced

GATE_MVS = [
    "mv_anchor_coverage", "mv_calculation_network",
    "mv_sum_of_parts_checks", "mv_filing_completeness",
]
SYNTHETIC_ACC = "TEST-HELDBACK-NVDA-2026"


def _conn_params():
    load_dotenv()
    return {
        "host": os.environ.get("XBRL_DB_HOST", "localhost"),
        "port": int(os.environ.get("XBRL_DB_PORT", "5432")),
        "dbname": os.environ.get("XBRL_DB_NAME", "xbrl_db"),
        "user": os.environ.get("XBRL_DB_USER", "postgres"),
        "password": os.environ.get("XBRL_DB_PASSWORD", "postgres"),
    }


def _db_available() -> bool:
    try:
        with psycopg2.connect(**_conn_params()) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.publish_manifest')")
                if cur.fetchone()[0] is None:
                    return False
                cur.execute("SELECT 1 FROM mv_anchor_coverage LIMIT 1")
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not _db_available(), reason="xbrl_db / publish_manifest not reachable")


@pytest.fixture()
def conn():
    c = psycopg2.connect(**_conn_params())
    c.autocommit = True
    yield c
    c.close()


@requires_db
class TestGateMaterializedViews:
    def test_all_four_gate_mvs_exist(self, conn):
        with conn.cursor() as cur:
            for mv in GATE_MVS:
                cur.execute("SELECT to_regclass(%s)", (mv,))
                assert cur.fetchone()[0] is not None, f"{mv} missing"

    def test_anchor_coverage_is_populated(self, conn):
        with conn.cursor() as cur:
            cur.execute("SELECT count(*), min(xmlid_coverage_pct) FROM mv_anchor_coverage")
            n, lo = cur.fetchone()
            assert n > 0
            assert lo is not None and 0.0 <= float(lo) <= 1.0

    def test_completeness_counts_instant_and_duration(self, conn):
        """The WS-1 fix: a healthy filer should exceed ~0.6, not cap near 0.5."""
        with conn.cursor() as cur:
            cur.execute(
                "SELECT max(coverage_pct) FROM mv_filing_completeness WHERE cik = %s", (NVDA,))
            hi = cur.fetchone()[0]
            assert hi is not None and float(hi) > 0.6


@requires_db
class TestPublishGateEvaluate:
    def test_advisory_publishes_all_good_anchor_filings(self, conn):
        gate = PublishGate.from_env(gate_b_enforce=False)
        report = gate.evaluate()
        assert report.evaluated > 0
        # Every filing has 1.0 anchor coverage in this corpus, so advisory -> all published.
        assert report.held_back == 0
        assert report.published == report.evaluated
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM publish_manifest WHERE is_published")
            assert cur.fetchone()[0] >= report.published

    def test_enforce_holds_back_sparse_filer_then_restore(self, conn):
        """GATE-B enforcement holds back TSM (low completeness). Restored after."""
        with conn.cursor() as cur:
            cur.execute(
                "SELECT accession_no FROM filing_registry WHERE cik = %s "
                "AND fiscal_period = 'FY' ORDER BY fiscal_year DESC LIMIT 1", (TSM,))
            row = cur.fetchone()
        if not row:
            pytest.skip("no TSM annual filing in registry")
        tsm_acc = row[0]
        try:
            enforced = PublishGate.from_env(gate_b_enforce=True)
            report = enforced.evaluate(accessions=[tsm_acc])
            assert report.evaluated == 1
            res = report.results[0]
            assert not res.gate_b_passed          # completeness below threshold
            assert not res.is_published           # ...and enforced -> held back
            assert any("completeness" in r for r in res.held_back_reasons)
        finally:
            # Restore: advisory re-evaluation republishes it (anchor is fine).
            PublishGate.from_env(gate_b_enforce=False).evaluate(accessions=[tsm_acc])
        with conn.cursor() as cur:
            cur.execute("SELECT is_published FROM publish_manifest WHERE accession_no = %s", (tsm_acc,))
            assert cur.fetchone()[0] is True


@requires_db
class TestReadTimeDenyList:
    def test_held_back_filing_is_excluded_from_lookup(self, conn):
        fsl._PUBLISH_MANIFEST_PRESENT.clear()
        lk = XbrlFactstoreLookup.from_env()
        step = {"id": "t", "op": "lookup", "cik": NVDA, "fy": 2026,
                "concept_family": "Revenue", "dimensions": "NONE",
                "section_hint": "Income Statement"}

        # Baseline: NVDA FY2026 revenue resolves.
        assert lk.lookup(step).status == LookupStatus.OK

        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO publish_manifest
                       (accession_no, cik, fiscal_year, fiscal_period, form_type,
                        is_published, gate_a_passed, gate_b_passed)
                       VALUES (%s, %s, 2026, 'FY', '10-K', FALSE, FALSE, FALSE)
                       ON CONFLICT (accession_no) DO UPDATE SET is_published = FALSE""",
                    (SYNTHETIC_ACC, NVDA))
            # With a held-back deny-list row for (NVDA, 2026, FY), the fact vanishes.
            assert lk.lookup(step).status == LookupStatus.NOT_FOUND
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM publish_manifest WHERE accession_no = %s", (SYNTHETIC_ACC,))

        # Restored.
        assert lk.lookup(step).status == LookupStatus.OK
