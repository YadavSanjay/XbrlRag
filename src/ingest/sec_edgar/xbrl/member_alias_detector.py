"""WS-1 / A2 — dimensional member reclassification detector.

Populates the ``member_aliases`` table (see
``scripts/ddl/layer_0/member_aliases.sql``) with genuine member RENAMES so that
``mv_yoy_comparison`` can bridge them and a YoY dimensional query does not return
one value + one NULL (Plan-Extractor §6.1, the highest-risk silent-wrong-answer
class).

Detection walks each filing's prior-year COMPARATIVE column: a fact value the
prior filing reported under member ``M_old`` that reappears — same local concept,
same period_end, same value — under a **different-local-name** member ``M_new`` in
the current filing's comparative column is a reclassification signal.

Namespace-version drift (the SAME local name re-declared under a new date-stamped
extension namespace) is deliberately EXCLUDED here — it is handled directly by
``mv_yoy_comparison`` matching members on local name. Only genuine local-name
renames are recorded. A single coincidental equal-value collision is filtered out
by requiring the rename to be supported by ``min_support_concepts`` distinct
concepts (a real reclassification migrates every fact under the member together).

This is an ingest step; run it after the MV rebuild, then rebuild
``mv_yoy_comparison`` so new aliases take effect. Connection params come from the
same ``XBRL_DB_*`` env vars as the factstore.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from src.logging_config import get_logger

logger = get_logger(__name__)

DEFAULT_MIN_SUPPORT_CONCEPTS = 2   # distinct concepts backing the rename (filters flukes)


def _local(qname: str | None) -> str | None:
    return qname.split(":")[-1] if qname else qname


@dataclass
class MemberAlias:
    cik: str
    axis_qname: str
    old_member_qname: str
    new_member_qname: str
    detected_in_fy: int | None
    support_concepts: int
    support_facts: int
    confidence: float

    def to_dict(self) -> dict:
        return {
            "cik": self.cik,
            "axis": _local(self.axis_qname),
            "old_member": _local(self.old_member_qname),
            "new_member": _local(self.new_member_qname),
            "detected_in_fy": self.detected_in_fy,
            "support_concepts": self.support_concepts,
            "support_facts": self.support_facts,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class AliasReport:
    detected: int = 0
    aliases: list[MemberAlias] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"detected": self.detected, "aliases": [a.to_dict() for a in self.aliases]}


class MemberAliasDetector:
    """Detects member reclassifications and upserts them into member_aliases."""

    def __init__(
        self,
        db_conn_params: dict | None = None,
        *,
        min_support_concepts: int = DEFAULT_MIN_SUPPORT_CONCEPTS,
    ):
        self._db_conn_params = db_conn_params or {}
        self.min_support_concepts = min_support_concepts

    @classmethod
    def from_env(cls, dotenv_path: str | None = None, **kwargs) -> MemberAliasDetector:
        load_dotenv(dotenv_path)
        conn_params = {
            "host": os.environ.get("XBRL_DB_HOST", "localhost"),
            "port": int(os.environ.get("XBRL_DB_PORT", "5432")),
            "dbname": os.environ.get("XBRL_DB_NAME", "xbrl_db"),
            "user": os.environ.get("XBRL_DB_USER", "postgres"),
            "password": os.environ.get("XBRL_DB_PASSWORD", "postgres"),
        }
        return cls(db_conn_params=conn_params, **kwargs)

    def _connect(self):
        return psycopg2.connect(**self._db_conn_params)

    @staticmethod
    def _confidence(support_concepts: int) -> float:
        """Support-based heuristic: a real reclassification migrates every fact
        under the member, so more distinct backing concepts => higher confidence.
        Saturates at 5 concepts."""
        return min(1.0, support_concepts / 5.0)

    # ------------------------------------------------------------------

    def detect(
        self,
        accessions: Iterable[str] | None = None,
        run_id: str | None = None,
    ) -> AliasReport:
        """Detect reclassifications (optionally restricted to accessions) and
        upsert them into member_aliases. Returns an :class:`AliasReport`."""
        acc_list = list(accessions) if accessions is not None else None
        with self._connect() as conn:
            rows = self._find_candidates(conn, acc_list)
            aliases = [
                MemberAlias(
                    cik=r["cik"],
                    axis_qname=r["axis_qname"],
                    old_member_qname=r["old_member_qname"],
                    new_member_qname=r["new_member_qname"],
                    detected_in_fy=r.get("detected_in_fy"),
                    support_concepts=int(r["support_concepts"]),
                    support_facts=int(r["support_facts"]),
                    confidence=self._confidence(int(r["support_concepts"])),
                )
                for r in rows
            ]
            if aliases:
                self._upsert(conn, aliases)
            conn.commit()

        report = AliasReport(detected=len(aliases), aliases=aliases)
        logger.info("member-alias detector: detected=%d (min_support_concepts=%d)",
                    report.detected, self.min_support_concepts)
        for a in aliases:
            logger.info("  reclassification %s [%s]: %s -> %s (support=%d concepts, conf=%.2f)",
                        a.cik, _local(a.axis_qname), _local(a.old_member_qname),
                        _local(a.new_member_qname), a.support_concepts, a.confidence)
        return report

    # A rename is DIRECTIONAL and MUTUALLY EXCLUSIVE across the two filings:
    #   * comp  = the new filing's prior-year COMPARATIVE column (M_new, restating last year)
    #   * prior = the old filing's CURRENT column (M_old, as originally reported)
    #   * M_old must be ABSENT from the new filing's own current column, and
    #   * M_new must be ABSENT from the old filing's own current column.
    # Without this, coincidental equal-value collisions between coexisting members
    # (equity components that share a balance, Class A vs Class B stock, …) are
    # mis-detected as renames — and those are symmetric (A->B and B->A both appear),
    # the tell-tale of a false positive. The two NOT EXISTS clauses require the old
    # and new members to never coexist, which is what an actual reclassification looks like.
    _DETECT_SQL = """
        SELECT
            comp.cik,
            comp.segment_axis     AS axis_qname,
            prior.segment_member  AS old_member_qname,
            comp.segment_member   AS new_member_qname,
            COUNT(DISTINCT comp.concept) AS support_concepts,
            COUNT(*)                     AS support_facts,
            MIN(comp.fiscal_year)        AS detected_in_fy
        FROM mv_financial_facts comp
        JOIN mv_financial_facts prior
          ON prior.cik = comp.cik
         AND SUBSTRING(prior.concept      FROM '[^:]+$') = SUBSTRING(comp.concept      FROM '[^:]+$')
         AND SUBSTRING(prior.segment_axis FROM '[^:]+$') = SUBSTRING(comp.segment_axis FROM '[^:]+$')
         AND prior.period_end = comp.period_end
         AND prior.value      = comp.value
         -- TRUE rename: the member's LOCAL name differs (namespace drift excluded).
         AND SUBSTRING(prior.segment_member FROM '[^:]+$') <> SUBSTRING(comp.segment_member FROM '[^:]+$')
         AND prior.is_current_period   = TRUE
         AND prior.is_canonical_filing = TRUE
        WHERE comp.is_current_period   = FALSE
          AND comp.is_canonical_filing = TRUE
          AND comp.segment_axis   IS NOT NULL
          AND comp.segment_member IS NOT NULL
          AND comp.value <> 0
          AND comp.accession_no <> prior.accession_no
          -- M_old absent from the NEW filing's current column
          AND NOT EXISTS (
              SELECT 1 FROM mv_financial_facts x
               WHERE x.accession_no = comp.accession_no
                 AND x.is_current_period = TRUE
                 AND SUBSTRING(x.segment_axis   FROM '[^:]+$') = SUBSTRING(comp.segment_axis   FROM '[^:]+$')
                 AND SUBSTRING(x.segment_member FROM '[^:]+$') = SUBSTRING(prior.segment_member FROM '[^:]+$')
          )
          -- M_new absent from the OLD filing's current column
          AND NOT EXISTS (
              SELECT 1 FROM mv_financial_facts y
               WHERE y.accession_no = prior.accession_no
                 AND y.is_current_period = TRUE
                 AND SUBSTRING(y.segment_axis   FROM '[^:]+$') = SUBSTRING(comp.segment_axis   FROM '[^:]+$')
                 AND SUBSTRING(y.segment_member FROM '[^:]+$') = SUBSTRING(comp.segment_member FROM '[^:]+$')
          )
          {accession_filter}
        GROUP BY comp.cik, comp.segment_axis, prior.segment_member, comp.segment_member
        HAVING COUNT(DISTINCT comp.concept) >= %(min_support_concepts)s
        ORDER BY COUNT(*) DESC
    """

    def _find_candidates(self, conn, accessions: list[str] | None) -> list[dict]:
        params: dict[str, Any] = {"min_support_concepts": self.min_support_concepts}
        accession_filter = ""
        if accessions is not None:
            accession_filter = "AND comp.accession_no = ANY(%(accessions)s)"
            params["accessions"] = list(accessions)
        sql = self._DETECT_SQL.format(accession_filter=accession_filter)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    _UPSERT_SQL = """
        INSERT INTO member_aliases (
            cik, axis_qname, old_member_qname, new_member_qname,
            detected_in_fy, support_count, confidence, updated_at
        ) VALUES (
            %(cik)s, %(axis_qname)s, %(old_member_qname)s, %(new_member_qname)s,
            %(detected_in_fy)s, %(support_facts)s, %(confidence)s, now()
        )
        ON CONFLICT (cik, axis_qname, old_member_qname, new_member_qname) DO UPDATE SET
            detected_in_fy = LEAST(member_aliases.detected_in_fy, EXCLUDED.detected_in_fy),
            support_count  = EXCLUDED.support_count,
            confidence     = EXCLUDED.confidence,
            updated_at     = now()
    """

    def _upsert(self, conn, aliases: list[MemberAlias]) -> None:
        with conn.cursor() as cur:
            for a in aliases:
                cur.execute(self._UPSERT_SQL, {
                    "cik": a.cik,
                    "axis_qname": a.axis_qname,
                    "old_member_qname": a.old_member_qname,
                    "new_member_qname": a.new_member_qname,
                    "detected_in_fy": a.detected_in_fy,
                    "support_facts": a.support_facts,
                    "confidence": a.confidence,
                })


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="WS-1/A2 member reclassification detector.")
    parser.add_argument("--min-support-concepts", type=int, default=DEFAULT_MIN_SUPPORT_CONCEPTS,
                        help="Distinct concepts required to back a rename (default 2).")
    parser.add_argument("--accession", action="append", default=None,
                        help="Restrict to specific accession number(s); repeatable.")
    args = parser.parse_args()

    detector = MemberAliasDetector.from_env(min_support_concepts=args.min_support_concepts)
    report = detector.detect(accessions=args.accession)
    print(json.dumps(report.to_dict(), indent=2, default=str))
