"""WS-9 / E1 — corpus-coverage partitioning.

An external suite (Phase 4) asks about filings XbrlRag has never ingested. Scoring only
the answerable subset and reporting that as "accuracy" is how an inflated,
indefensible number gets produced. So coverage is a **first-class, reported**
dimension: every report publishes the score *and* the coverage percentage.

Also used by the Phase-1 SQL-derived builder, where the corpus set falls out of the
fact store directly, so an unanswerable parameter combination is dropped at build
time rather than graded as a failure.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Dict, List, Set, Tuple

from src.eval.core.case import Coverage, EvalCase

# A corpus is the set of (TICKER, fiscal_year) pairs the fact store can answer for.
CorpusKey = tuple[str, int]


@dataclass
class CoverageReport:
    in_corpus: list[EvalCase] = field(default_factory=list)
    out_of_corpus: list[EvalCase] = field(default_factory=list)
    partial: list[EvalCase] = field(default_factory=list)
    unknown: list[EvalCase] = field(default_factory=list)

    @property
    def total(self) -> int:
        return (
            len(self.in_corpus)
            + len(self.out_of_corpus)
            + len(self.partial)
            + len(self.unknown)
        )

    @property
    def coverage_pct(self) -> float:
        """Fraction of cases the corpus can actually answer. Reported alongside every
        score — never used to silently drop cases."""
        if not self.total:
            return 0.0
        return (len(self.in_corpus) + 0.5 * len(self.partial)) / self.total

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "in_corpus": len(self.in_corpus),
            "out_of_corpus": len(self.out_of_corpus),
            "partial": len(self.partial),
            "unknown": len(self.unknown),
            "coverage_pct": round(self.coverage_pct, 4),
        }


def classify(case: EvalCase, corpus: set[CorpusKey]) -> Coverage:
    """Coverage of one case against a ``{(TICKER, fy)}`` corpus set.

    A case naming neither a ticker nor a fiscal year — a cross-filer screen, for
    instance — is ``UNKNOWN``, not ``OUT_OF_CORPUS``: it legitimately names no
    company, exactly as ``NUMERIC_SCREEN`` does in the query pipeline.
    """
    ticker = (case.ticker or "").upper() or None
    fy = case.fy

    if ticker is None and fy is None:
        return Coverage.UNKNOWN
    if ticker is not None and fy is not None:
        return Coverage.IN_CORPUS if (ticker, fy) in corpus else Coverage.OUT_OF_CORPUS
    if ticker is not None:
        return (
            Coverage.PARTIAL
            if any(t == ticker for t, _ in corpus)
            else Coverage.OUT_OF_CORPUS
        )
    return (
        Coverage.PARTIAL if any(y == fy for _, y in corpus) else Coverage.OUT_OF_CORPUS
    )


def partition(cases: Iterable[EvalCase], corpus: set[CorpusKey]) -> CoverageReport:
    """Split cases by coverage, stamping ``case.coverage`` in place."""
    report = CoverageReport()
    bucket: dict[Coverage, list[EvalCase]] = {
        Coverage.IN_CORPUS: report.in_corpus,
        Coverage.OUT_OF_CORPUS: report.out_of_corpus,
        Coverage.PARTIAL: report.partial,
        Coverage.UNKNOWN: report.unknown,
    }
    for case in cases:
        cov = classify(case, corpus)
        case.coverage = cov
        bucket[cov].append(case)
    return report


def corpus_from_pairs(pairs: Sequence[tuple[str, int]]) -> set[CorpusKey]:
    return {(str(t).upper(), int(y)) for t, y in pairs}


def load_corpus(conn: Any = None) -> set[CorpusKey]:
    """The live ``{(ticker, fiscal_year)}`` set from the fact store.

    Returns an empty set when Postgres is unreachable so callers degrade rather than
    crash — the same fail-soft contract the audit/observability layer uses.
    """
    close_after = False
    try:
        if conn is None:
            from src.db.conn import connect

            conn = connect()
            close_after = True
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ticker, fiscal_year
                FROM   mv_financial_facts
                WHERE  ticker IS NOT NULL AND fiscal_year IS NOT NULL
                """
            )
            return {(str(t).upper(), int(y)) for t, y in cur.fetchall()}
    except Exception:  # pragma: no cover - depends on a live backend
        return set()
    finally:
        if close_after and conn is not None:
            try:
                conn.close()
            except Exception:
                pass
