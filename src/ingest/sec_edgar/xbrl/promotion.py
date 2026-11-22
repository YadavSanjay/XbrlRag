"""WS-1 / B5 — per-filing publish gate.

After a filing's facts are loaded and the gate MVs are refreshed
(``scripts/rebuild_mvs.py``), this module evaluates each ingested filing and
decides whether it is visible to queries. It is the **final ingest step**;
WS-5's ingest DAG will call :meth:`PublishGate.evaluate` after the MV refresh.

Gates
-----
**GATE-A (structural, HARD)** — ``mv_anchor_coverage.xmlid_coverage_pct >=
``min_anchor`` (default 0.95), and no fact holding ``(ixTransformValueError)``,
which Arelle stores when the SEC's transforms were not loaded
(docs/specs/ixt-sec-transforms.md). A low value means many facts loaded with
``fact.xml_id`` NULL, which silently breaks their citation anchors. (Loading the
extracted ``*_htm.xml`` does not cause this: EDGAR's instance keeps the ids, and
a multi-document filing loads it, docs/specs/ingest-multidoc-ixbrl.md.) This is
the failure the gate exists to catch, so it always blocks.

**GATE-B (semantic, ADVISORY by default)** — completeness
(``mv_filing_completeness.coverage_pct >= ``min_completeness``, default 0.65) AND
no *new* strict calculation failure in the filing itself. ``mv_sum_of_parts_checks``
runs XBRL Calculations 1.1 per filing (docs/specs/calc-check-c11.md); its strict
failures (status ``fail``, no dimensions) are counted **per accession** through
:func:`~src.ingest.sec_edgar.xbrl.calc_check.calc_gate_decision` against the
committed, shrink-only exception list ``configs/ingest/calc_c11_exceptions.csv``
(known filer errors). ``partial``, dimensional and ``inapplicable`` results are
recorded only. ``sum_of_parts_failures`` on the manifest is the number of *new*
(unlisted) strict failures. These signals are recorded on every manifest row, but
they only *hold a filing back* when ``gate_b_enforce=True``
(``XBRLRAG_PUBLISH_GATE_B_ENFORCE``); completeness is not yet calibrated for a
clean binary gate — see ``scripts/ddl/mv/41_*``.

Output
------
One :class:`publish_manifest` row per evaluated filing (``is_published`` = passed
the enforced gates). Held-back filings' facts remain physically present in the
fact store / MVs; the factstore read paths
(``xbrl_factstore_lookup`` / ``xbrl_factstore_screen``) exclude them via the
read-time deny-list (see ``publish_gate_clause``).

Filings are enumerated per real accession from ``mv_anchor_coverage`` joined to
``filing_registry`` (the authoritative form-type / fiscal-period source). A
manifest row's ``fiscal_year`` is the filing's label, the same one both fact MVs
carry (docs/specs/fiscal-label-alignment.md), so the read-time deny-list matches
the filing it names; re-run the gate after a relabelling so the rows follow.

Connection params come from the same ``XBRL_DB_*`` env vars as the factstore.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from src import PROJECT_ROOT
from src.ingest.sec_edgar.xbrl.calc_check import calc_gate_decision, load_calc_exceptions
from src.logging_config import get_logger
from src.observability.trace_context import log_event
from src.pipelines.xbrl.query_cache import bump_cache_epoch

logger = get_logger(__name__)

# Gate thresholds (overridable via constructor or env).
DEFAULT_MIN_ANCHOR = 0.95          # GATE-A: xml-id coverage floor
DEFAULT_MIN_COMPLETENESS = 0.65    # GATE-B: expected-concept coverage floor (advisory)
DEFAULT_MAX_SOP_FAILURES = 0       # GATE-B: new strict calc-failure ceiling (advisory)
# Known strict Calculations 1.1 failures (filer errors), shrink-only (calc-check-c11).
CALC_EXCEPTIONS_PATH = Path(PROJECT_ROOT) / "configs" / "ingest" / "calc_c11_exceptions.csv"


def _env_flag(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class FilingGateResult:
    """The gate decision for a single filing."""

    accession_no: str
    cik: str
    fiscal_year: int | None
    fiscal_period: str | None
    form_type: str | None
    anchor_coverage_pct: float | None
    completeness_pct: float | None
    sum_of_parts_failures: int | None
    gate_a_passed: bool
    gate_b_passed: bool          # the GATE-B *signal* outcome (independent of enforcement)
    is_published: bool
    held_back_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "accession_no": self.accession_no,
            "cik": self.cik,
            "fiscal_year": self.fiscal_year,
            "fiscal_period": self.fiscal_period,
            "form_type": self.form_type,
            "anchor_coverage_pct": self.anchor_coverage_pct,
            "completeness_pct": self.completeness_pct,
            "sum_of_parts_failures": self.sum_of_parts_failures,
            "gate_a_passed": self.gate_a_passed,
            "gate_b_passed": self.gate_b_passed,
            "is_published": self.is_published,
            "held_back_reasons": self.held_back_reasons,
        }


@dataclass
class PublishReport:
    """Outcome of one publish-gate run."""

    evaluated: int = 0
    published: int = 0
    held_back: int = 0
    gate_b_enforced: bool = False
    results: list[FilingGateResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "evaluated": self.evaluated,
            "published": self.published,
            "held_back": self.held_back,
            "gate_b_enforced": self.gate_b_enforced,
            "held_back_filings": [r.to_dict() for r in self.results if not r.is_published],
        }


class PublishGate:
    """Evaluates ingested filings against GATE-A/GATE-B and writes publish_manifest."""

    def __init__(
        self,
        db_conn_params: dict | None = None,
        *,
        min_anchor: float = DEFAULT_MIN_ANCHOR,
        min_completeness: float = DEFAULT_MIN_COMPLETENESS,
        max_sop_failures: int = DEFAULT_MAX_SOP_FAILURES,
        gate_b_enforce: bool = False,
        calc_exceptions: Collection[tuple] | None = None,
    ):
        self._db_conn_params = db_conn_params or {}
        self.min_anchor = min_anchor
        self.min_completeness = min_completeness
        self.max_sop_failures = max_sop_failures
        self.gate_b_enforce = gate_b_enforce
        # None = the committed list, read when the gate first needs it.
        self._calc_exceptions = calc_exceptions

    @property
    def calc_exceptions(self) -> Collection[tuple]:
        """The known strict calculation failures excused per accession."""
        if self._calc_exceptions is None:
            self._calc_exceptions = load_calc_exceptions(CALC_EXCEPTIONS_PATH)
        return self._calc_exceptions

    @classmethod
    def from_env(cls, dotenv_path: str | None = None, **kwargs) -> PublishGate:
        load_dotenv(dotenv_path)
        conn_params = {
            "host": os.environ.get("XBRL_DB_HOST", "localhost"),
            "port": int(os.environ.get("XBRL_DB_PORT", "5432")),
            "dbname": os.environ.get("XBRL_DB_NAME", "xbrl_db"),
            "user": os.environ.get("XBRL_DB_USER", "postgres"),
            "password": os.environ.get("XBRL_DB_PASSWORD", "postgres"),
        }
        kwargs.setdefault("gate_b_enforce", _env_flag("XBRLRAG_PUBLISH_GATE_B_ENFORCE", False))
        return cls(db_conn_params=conn_params, **kwargs)

    def _connect(self):
        return psycopg2.connect(**self._db_conn_params)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(
        self,
        accessions: Iterable[str] | None = None,
        run_id: str | None = None,
    ) -> PublishReport:
        """Evaluate the given accessions (or all ingested filings) and upsert the
        publish manifest. Returns a :class:`PublishReport`."""
        acc_list = list(accessions) if accessions is not None else None
        with self._connect() as conn:
            rows = self._fetch_candidates(conn, acc_list)
            results = [self._decide(r) for r in rows]
            if results:
                self._write_manifest(conn, results, run_id)
                # WS-4 / B1: publishing changes what queries may see, so flush the
                # (cross-process) semantic query cache by bumping the shared epoch.
                bump_cache_epoch(conn)
            conn.commit()

        report = PublishReport(
            evaluated=len(results),
            published=sum(1 for r in results if r.is_published),
            held_back=sum(1 for r in results if not r.is_published),
            gate_b_enforced=self.gate_b_enforce,
            results=results,
        )
        logger.info(
            "publish gate: evaluated=%d published=%d held_back=%d gate_b_enforce=%s",
            report.evaluated, report.published, report.held_back, self.gate_b_enforce,
        )
        for r in results:
            if not r.is_published:
                logger.warning("HELD BACK %s (%s FY%s %s): %s",
                               r.accession_no, r.cik, r.fiscal_year, r.fiscal_period,
                               "; ".join(r.held_back_reasons))
        try:
            log_event("publish_gate", source="promotion", **report.to_dict())
        except Exception:  # pragma: no cover - tracing must never break ingest
            pass
        return report

    # ------------------------------------------------------------------
    # Decision (pure)
    # ------------------------------------------------------------------

    def _decide(self, r: dict) -> FilingGateResult:
        """Apply GATE-A/GATE-B to one candidate row. Pure — no I/O."""
        reasons: list[str] = []

        anchor = r.get("anchor_coverage_pct")
        gate_a_passed = anchor is not None and anchor >= self.min_anchor
        if not gate_a_passed:
            shown = f"{anchor:.3f}" if anchor is not None else "n/a"
            reasons.append(
                f"GATE-A: xml-id anchor coverage {shown} < {self.min_anchor:.2f} "
                f"(likely a wrong-source-file load — citations would be broken)"
            )
        # A canonical filing without the primary statements (e.g. a Part III-only 10-K/A
        # left canonical) would serve an empty period; a non-canonical one is never held
        # back here, since the read-time gate is per period and would hide the original.
        if r.get("is_canonical") is True and r.get("has_primary_statements") is False:
            gate_a_passed = False
            reasons.append(
                "GATE-A: canonical filing carries no primary statements "
                "(no TotalAssets with Revenue or NetIncome)"
            )

        transform_errors = r.get("transform_error_facts") or 0
        if transform_errors > 0:
            gate_a_passed = False
            reasons.append(
                f"GATE-A: {transform_errors} fact(s) hold (ixTransformValueError) "
                "(was the EDGAR/transform plugin loaded?)"
            )

        completeness = r.get("completeness_pct")
        sop_failures = r.get("sum_of_parts_failures")
        # A missing completeness row (None) is not counted against the filing.
        completeness_ok = completeness is None or completeness >= self.min_completeness
        sop_ok = sop_failures is None or sop_failures <= self.max_sop_failures
        gate_b_passed = completeness_ok and sop_ok

        advisory = "" if self.gate_b_enforce else " (advisory — not enforced)"
        if not completeness_ok:
            reasons.append(
                f"GATE-B: expected-concept completeness {completeness:.3f} "
                f"< {self.min_completeness:.2f}{advisory}"
            )
        if not sop_ok:
            decision = r.get("calc_decision")
            excepted = (
                f", {decision['excepted_failures']} more on the exception list"
                if decision else ""
            )
            reasons.append(
                f"GATE-B: {sop_failures} new strict sum-of-parts failure(s) "
                f"(> {self.max_sop_failures}){excepted}{advisory}"
            )

        # GATE-B only blocks publication when enforcement is enabled.
        is_published = gate_a_passed and (gate_b_passed or not self.gate_b_enforce)

        return FilingGateResult(
            accession_no=r["accession_no"],
            cik=r["cik"],
            fiscal_year=r.get("fiscal_year"),
            fiscal_period=r.get("fiscal_period"),
            form_type=r.get("form_type"),
            anchor_coverage_pct=anchor,
            completeness_pct=completeness,
            sum_of_parts_failures=sop_failures,
            gate_a_passed=gate_a_passed,
            gate_b_passed=gate_b_passed,
            is_published=is_published,
            held_back_reasons=reasons,
        )

    # ------------------------------------------------------------------
    # SQL
    # ------------------------------------------------------------------

    _CANDIDATE_SQL = """
        SELECT
            ac.accession_no,
            ac.cik,
            fr.fiscal_year,
            fr.fiscal_period,
            fr.form_type,
            ac.xmlid_coverage_pct                        AS anchor_coverage_pct,
            fc.coverage_pct                              AS completeness_pct,
            (fr.superseded_by IS NULL)                   AS is_canonical,
            (SELECT COUNT(*)
               FROM fact f
               JOIN accession a ON a.accession_id = f.accession_id
              WHERE a.filing_accession_number = fr.accession_no
                AND f.fact_value = '(ixTransformValueError)')
                                                         AS transform_error_facts,
            -- layer0_populator.has_primary_statements, as SQL: the balance sheet plus
            -- an income-statement total (docs/specs/annual-amendment-handling.md).
            (EXISTS (SELECT 1 FROM mv_financial_facts m
                      WHERE m.accession_no = fr.accession_no
                        AND m.concept_family = 'TotalAssets')
             AND EXISTS (SELECT 1 FROM mv_financial_facts m
                          WHERE m.accession_no = fr.accession_no
                            AND m.concept_family IN ('Revenue', 'NetIncome')))
                                                         AS has_primary_statements
        FROM mv_anchor_coverage ac
        JOIN filing_registry fr
              ON fr.accession_no = ac.accession_no
             AND fr.cik          = ac.cik
        LEFT JOIN mv_filing_completeness fc
              ON fc.cik = ac.cik AND fc.fiscal_year = fr.fiscal_year
        {accession_filter}
        ORDER BY ac.cik, fr.fiscal_year, fr.fiscal_period
    """

    # Only non-pass rows can change a calc_gate_decision; the gate reads them per filing.
    _CALC_RESULTS_SQL = """
        SELECT accession_no, link_role_uri, parent_concept, period_start, period_end,
               unit, status, dimensional
          FROM mv_sum_of_parts_checks
         WHERE status <> 'pass'
           AND accession_no = ANY(%(accessions)s)
    """

    def _fetch_candidates(self, conn, accessions: list[str] | None) -> list[dict]:
        params: dict[str, Any] = {}
        accession_filter = ""
        if accessions is not None:
            accession_filter = "WHERE ac.accession_no = ANY(%(accessions)s)"
            params["accessions"] = list(accessions)
        sql = self._CANDIDATE_SQL.format(accession_filter=accession_filter)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
            calc_results = self._fetch_calc_results(cur, [r["accession_no"] for r in rows])
        # Normalize float/int types out of Decimal.
        for r in rows:
            if r.get("anchor_coverage_pct") is not None:
                r["anchor_coverage_pct"] = float(r["anchor_coverage_pct"])
            if r.get("completeness_pct") is not None:
                r["completeness_pct"] = float(r["completeness_pct"])
            self._attach_calc_decision(r, calc_results.get(r["accession_no"], []))
        return rows

    def _fetch_calc_results(self, cur, accessions: list[str]) -> dict[str, list[dict]]:
        """Non-pass mv_sum_of_parts_checks rows per accession, as calc_check results."""
        if not accessions:
            return {}
        cur.execute(self._CALC_RESULTS_SQL, {"accessions": accessions})
        by_acc: dict[str, list[dict]] = defaultdict(list)
        for row in cur.fetchall():
            start = row["period_start"]
            by_acc[row["accession_no"]].append({
                "link_role": row["link_role_uri"],
                "parent": row["parent_concept"],
                "period_start": None if start is None else start.isoformat(),
                "period_end": row["period_end"].isoformat(),
                "unit": row["unit"],
                "status": row["status"],
                "dimensional": row["dimensional"],
            })
        return by_acc

    def _attach_calc_decision(self, r: dict, calc_results: list[dict]) -> None:
        """Count one filing's strict calc failures against the exception list (pure)."""
        decision = calc_gate_decision(r["accession_no"], calc_results, self.calc_exceptions)
        r["calc_decision"] = decision
        r["sum_of_parts_failures"] = len(decision["new_failures"])

    _UPSERT_SQL = """
        INSERT INTO publish_manifest (
            accession_no, cik, fiscal_year, fiscal_period, form_type,
            is_published, gate_a_passed, gate_b_passed,
            anchor_coverage_pct, completeness_pct, sum_of_parts_failures,
            held_back_reasons, run_id, evaluated_at
        ) VALUES (
            %(accession_no)s, %(cik)s, %(fiscal_year)s, %(fiscal_period)s, %(form_type)s,
            %(is_published)s, %(gate_a_passed)s, %(gate_b_passed)s,
            %(anchor_coverage_pct)s, %(completeness_pct)s, %(sum_of_parts_failures)s,
            %(held_back_reasons)s, %(run_id)s, now()
        )
        ON CONFLICT (accession_no) DO UPDATE SET
            cik                   = EXCLUDED.cik,
            fiscal_year           = EXCLUDED.fiscal_year,
            fiscal_period         = EXCLUDED.fiscal_period,
            form_type             = EXCLUDED.form_type,
            is_published          = EXCLUDED.is_published,
            gate_a_passed         = EXCLUDED.gate_a_passed,
            gate_b_passed         = EXCLUDED.gate_b_passed,
            anchor_coverage_pct   = EXCLUDED.anchor_coverage_pct,
            completeness_pct      = EXCLUDED.completeness_pct,
            sum_of_parts_failures = EXCLUDED.sum_of_parts_failures,
            held_back_reasons     = EXCLUDED.held_back_reasons,
            run_id                = EXCLUDED.run_id,
            evaluated_at          = now()
    """

    def _write_manifest(self, conn, results: list[FilingGateResult], run_id: str | None) -> None:
        with conn.cursor() as cur:
            for r in results:
                cur.execute(self._UPSERT_SQL, {
                    "accession_no": r.accession_no,
                    "cik": r.cik,
                    "fiscal_year": r.fiscal_year,
                    "fiscal_period": r.fiscal_period,
                    "form_type": r.form_type,
                    "is_published": r.is_published,
                    "gate_a_passed": r.gate_a_passed,
                    "gate_b_passed": r.gate_b_passed,
                    "anchor_coverage_pct": r.anchor_coverage_pct,
                    "completeness_pct": r.completeness_pct,
                    "sum_of_parts_failures": r.sum_of_parts_failures,
                    "held_back_reasons": psycopg2.extras.Json(r.held_back_reasons),
                    "run_id": run_id,
                })


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="WS-1/B5 per-filing publish gate.")
    parser.add_argument("--enforce-gate-b", action="store_true",
                        help="Hold back filings that fail the (advisory) GATE-B signals too.")
    parser.add_argument("--accession", action="append", default=None,
                        help="Restrict to specific accession number(s); repeatable.")
    parser.add_argument("--run-id", default=None, help="audit.ingestion_run.run_id to stamp.")
    args = parser.parse_args()

    gate = PublishGate.from_env(gate_b_enforce=args.enforce_gate_b)
    report = gate.evaluate(accessions=args.accession, run_id=args.run_id)
    print(json.dumps(report.to_dict(), indent=2, default=str))
