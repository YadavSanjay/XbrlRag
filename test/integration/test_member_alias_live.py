"""Live-DB integration tests for WS-1 / A2 member reclassification handling.

Skip automatically if xbrl_db / member_aliases is unreachable. They assert:
- the namespace-insensitive YoY member match resolves dimensional YoY across a
  member's date-stamped-namespace drift (the dominant fix), e.g. MSFT's
  Intelligent Cloud segment revenue resolves both sides year-over-year;
- the detector produces only directional, differing-local-name renames
  (no Class-A -> Class-B style false positives);
- genuine renames are bridged in mv_yoy_comparison via member_aliases.
"""
import os

import psycopg2
import pytest
from dotenv import load_dotenv

from src.ingest.sec_edgar.xbrl.member_alias_detector import MemberAliasDetector, _local

MSFT = "0000789019"


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
                cur.execute("SELECT to_regclass('public.member_aliases')")
                if cur.fetchone()[0] is None:
                    return False
                cur.execute("SELECT 1 FROM mv_yoy_comparison LIMIT 1")
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not _db_available(), reason="xbrl_db / member_aliases not reachable")


@pytest.fixture()
def conn():
    c = psycopg2.connect(**_conn_params())
    c.autocommit = True
    yield c
    c.close()


@requires_db
class TestNamespaceInsensitiveYoY:
    def test_msft_segment_revenue_yoy_resolves_both_sides(self, conn):
        """Intelligent Cloud is re-declared under a new date-stamped namespace
        each year; the local-name match must still line up current vs prior."""
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT fy_current, value_current, value_prior
                FROM mv_yoy_comparison
                WHERE cik = %s AND concept_family = 'Revenue' AND fiscal_period = 'FY'
                  AND lower(segment_label) LIKE '%%intelligent cloud%%'
                  AND value_current IS NOT NULL AND value_prior IS NOT NULL
                """,
                (MSFT,))
            rows = cur.fetchall()
        assert rows, "Intelligent Cloud segment revenue YoY did not resolve both sides"
        # Both sides are real dollar figures for at least one year.
        assert all(vc is not None and vp is not None for _, vc, vp in rows)

    def test_dimensional_yoy_has_substantial_resolved_coverage(self, conn):
        """Regression guard: the local-name match keeps dimensional YoY coverage
        well above the full-QName-match floor."""
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM mv_yoy_comparison "
                "WHERE segment_member IS NOT NULL "
                "AND value_current IS NOT NULL AND value_prior IS NOT NULL")
            assert cur.fetchone()[0] > 20000


@requires_db
class TestDetector:
    def test_detected_aliases_are_directional_local_name_renames(self, conn):
        """Every persisted alias must be a genuine local-name rename, and no
        symmetric Class-A -> Class-B style collision may survive."""
        with conn.cursor() as cur:
            cur.execute(
                "SELECT cik, axis_qname, old_member_qname, new_member_qname, "
                "support_count FROM member_aliases")
            rows = cur.fetchall()
        assert rows, "expected the detector to have populated member_aliases"
        for cik, axis, old, new, support in rows:
            assert _local(old) != _local(new), f"same local name: {old} vs {new}"
            assert support >= 1
        # No Class-A -> Class-B share-class confusion.
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM member_aliases "
                "WHERE old_member_qname ILIKE '%ClassA%' AND new_member_qname ILIKE '%ClassB%'")
            assert cur.fetchone()[0] == 0

    def test_detect_is_idempotent(self, conn):
        """Re-running the detector upserts to the same set (no duplication)."""
        detector = MemberAliasDetector.from_env()
        before = detector.detect().detected
        after = detector.detect().detected
        assert before == after

    def test_a_genuine_rename_is_bridged_in_yoy(self, conn):
        """At least one detected rename actually reconciles a YoY row (both the
        old-member prior value and new-member current value present)."""
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*)
                FROM mv_yoy_comparison y
                JOIN member_aliases ma
                  ON ma.cik = y.cik
                 AND SUBSTRING(ma.new_member_qname FROM '[^:]+$')
                       = SUBSTRING(y.segment_member FROM '[^:]+$')
                WHERE y.value_current IS NOT NULL AND y.value_prior IS NOT NULL
                """)
            # The bridge mechanism is exercised; >=0 is always true, but we assert
            # the join runs and (given the corpus) finds at least one bridged row.
            assert cur.fetchone()[0] >= 1
