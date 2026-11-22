"""WS-2 / A3 — regenerate the static XBRL golden set from the live fact store.

Snapshots the *expected* numeric answer for each labelled query out of
``mv_financial_facts`` (anchored to ``fiscal_calendar`` exactly as the integration
suite's ground-truth fixtures do), and writes them to
``data/eval/xbrl_golden_v1.json`` so the golden set is static and committed. Grading
then needs no DB for the labels — only to run the pipeline.

Run once (and again when the corpus changes):

    conda run -n py312 python scripts/eval/build_golden_set.py

Requires the XBRL_DB_* env vars (same as the factstore).
"""
from __future__ import annotations

import json
import os
from typing import Optional

import psycopg2
from dotenv import load_dotenv

OUT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "test", "eval", "golden", "xbrl_golden_v1.json",
)

# Flat (company-level, non-dimensional) lookups: (id, query, ticker, fy, concept, period_type, tags)
FLAT_SPECS = [
    ("nvda-rev-2026", "What was NVIDIA's revenue in fiscal 2026?", "NVDA", 2026, "Revenue", "duration", ["revenue"]),
    ("nvda-netincome-2026", "What was NVIDIA's net income in fiscal 2026?", "NVDA", 2026, "NetIncome", "duration", ["net-income"]),
    ("nvda-assets-2026", "What were NVIDIA's total assets at the end of fiscal 2026?", "NVDA", 2026, "TotalAssets", "instant", ["balance-sheet"]),
    ("aapl-rev-2024", "What was Apple's revenue in fiscal 2024?", "AAPL", 2024, "Revenue", "duration", ["revenue"]),
    ("aapl-netincome-2024", "What was Apple's net income in fiscal 2024?", "AAPL", 2024, "NetIncome", "duration", ["net-income"]),
    ("aapl-assets-2024", "What were Apple's total assets at the end of fiscal 2024?", "AAPL", 2024, "TotalAssets", "instant", ["balance-sheet"]),
    ("msft-rev-2025", "What was Microsoft's revenue in fiscal 2025?", "MSFT", 2025, "Revenue", "duration", ["revenue"]),
    ("googl-rev-2023", "What was Alphabet's revenue in fiscal 2023?", "GOOGL", 2023, "Revenue", "duration", ["revenue"]),
    ("googl-rd-2023", "What was Alphabet's research and development expense in fiscal 2023?", "GOOGL", 2023, "ResearchAndDevelopment", "duration", ["rd"]),
    ("amd-rev-2024", "What was AMD's revenue in fiscal 2024?", "AMD", 2024, "Revenue", "duration", ["revenue"]),
]

# Dimensional lookups: (id, query, ticker, fy, concept, axis_like, member_like, tags)
DIM_SPECS = [
    ("nvda-datacenter-rev-2026", "What was NVIDIA's Data Center revenue in fiscal 2026?",
     "NVDA", 2026, "Revenue", "%ProductOrServiceAxis", "%data center%", ["dimensional", "revenue"]),
]

# Compute cases: (id, query, kind, fn(gt) -> (value, alt_values), rel_tol, tags)
# fn receives the ground-truth resolver and returns the expected computed value.
COMPUTE_SPECS = [
    ("nvda-rev-yoy-2526",
     "What was the year-over-year growth in NVIDIA's revenue from fiscal 2025 to fiscal 2026?",
     lambda gt: _yoy(gt("NVDA", 2025, "Revenue"), gt("NVDA", 2026, "Revenue")),
     1e-2, ["compute", "yoy"]),
    ("aapl-net-margin-2024",
     "What was Apple's net profit margin in fiscal 2024?",
     lambda gt: _ratio(gt("AAPL", 2024, "NetIncome"), gt("AAPL", 2024, "Revenue")),
     1e-2, ["compute", "margin"]),
]


def _yoy(prev, curr):
    if prev in (None, 0) or curr is None:
        return None
    val = (curr - prev) / prev
    return val, [val * 100.0]        # accept fraction or percentage form


def _ratio(num, den):
    if den in (None, 0) or num is None:
        return None
    val = num / den
    return val, [val * 100.0]


def _connect():
    load_dotenv()
    return psycopg2.connect(
        host=os.environ.get("XBRL_DB_HOST", "localhost"),
        port=int(os.environ.get("XBRL_DB_PORT", "5432")),
        dbname=os.environ.get("XBRL_DB_NAME", "xbrl_db"),
        user=os.environ.get("XBRL_DB_USER", "postgres"),
        password=os.environ.get("XBRL_DB_PASSWORD", "postgres"),
    )


def _ground_truth(cur, ticker, fy, concept, period_type="duration") -> float | None:
    guard = ("AND (f.period_start IS NULL OR (f.period_end - f.period_start) > 300)"
             if period_type == "duration" else "")
    cur.execute(
        f"""
        WITH cal AS (SELECT cik, period_end FROM fiscal_calendar
                     WHERE ticker=%s AND fiscal_year=%s AND fiscal_period='FY' LIMIT 1)
        SELECT f.value FROM mv_financial_facts f JOIN cal ON f.cik = cal.cik
        WHERE f.concept_family=%s AND f.period_type=%s
          AND f.has_multiple_dimensions IS NOT TRUE AND f.segment_member IS NULL
          AND f.is_canonical_filing AND f.concept_pref_rank=1 {guard}
          AND abs(f.period_end - cal.period_end) <= 2
        ORDER BY abs(f.period_end - cal.period_end) LIMIT 1
        """, (ticker, fy, concept, period_type))
    row = cur.fetchone()
    return float(row[0]) if row else None


def _dim_ground_truth(cur, ticker, fy, concept, axis_like, member_like) -> float | None:
    cur.execute(
        """
        WITH cal AS (SELECT cik, period_end FROM fiscal_calendar
                     WHERE ticker=%s AND fiscal_year=%s AND fiscal_period='FY' LIMIT 1)
        SELECT f.value FROM mv_financial_facts f JOIN cal ON f.cik = cal.cik
        WHERE f.concept_family=%s AND f.segment_axis LIKE %s AND f.segment_label ILIKE %s
          AND f.is_canonical_filing
          AND (f.period_start IS NULL OR (f.period_end - f.period_start) > 300)
          AND abs(f.period_end - cal.period_end) <= 2
        ORDER BY abs(f.period_end - cal.period_end), f.value DESC LIMIT 1
        """, (ticker, fy, concept, axis_like, member_like))
    row = cur.fetchone()
    return float(row[0]) if row else None


def main() -> None:
    conn = _connect()
    cur = conn.cursor()
    cases = []
    skipped = []

    for cid, query, ticker, fy, concept, ptype, tags in FLAT_SPECS:
        val = _ground_truth(cur, ticker, fy, concept, ptype)
        if val is None:
            skipped.append(cid); continue
        cases.append({
            "id": cid, "query": query, "kind": "lookup", "expected_value": val,
            "unit": "USD", "rel_tol": 1e-4, "require_citation": True,
            "ticker": ticker, "fy": fy, "concept": concept, "tags": tags,
        })

    for cid, query, ticker, fy, concept, axis, member, tags in DIM_SPECS:
        val = _dim_ground_truth(cur, ticker, fy, concept, axis, member)
        if val is None:
            skipped.append(cid); continue
        cases.append({
            "id": cid, "query": query, "kind": "dimensional", "expected_value": val,
            "unit": "USD", "rel_tol": 1e-4, "require_citation": True,
            "ticker": ticker, "fy": fy, "concept": concept, "tags": tags,
        })

    gt = lambda t, y, c, p="duration": _ground_truth(cur, t, y, c, p)  # noqa: E731
    for cid, query, fn, rel_tol, tags in COMPUTE_SPECS:
        out = fn(gt)
        if out is None:
            skipped.append(cid); continue
        val, alts = out
        cases.append({
            "id": cid, "query": query, "kind": "compute", "expected_value": val,
            "rel_tol": rel_tol, "alt_values": alts, "require_citation": True, "tags": tags,
        })

    conn.close()
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    payload = {
        "version": "xbrl_golden_v1",
        "generated_from": "mv_financial_facts + fiscal_calendar (local 12-filer corpus)",
        "n_cases": len(cases),
        "cases": cases,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"wrote {len(cases)} cases to {OUT_PATH}")
    if skipped:
        print(f"skipped (no ground-truth value resolved): {skipped}")


if __name__ == "__main__":
    main()
