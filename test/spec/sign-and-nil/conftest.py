"""Fixtures for sign-and-nil (docs/specs/sign-and-nil.md).

One I/O boundary is faked, nothing else: the database under `XbrlFactstoreScreen`
(`psycopg2.connect`). The fake answers the publish-manifest probe with "absent", an
`entity_master` query with the roster, a `mv_segment_matrix` / `mv_geographic_facts` query
with the committed rows (one per filer, as the per-filer `DISTINCT ON` returns them today),
and any other query with no rows. The lookup is the product's own
`XbrlFactstoreLookup._build_result` over committed rows.

Stored rows were read from xbrl_db (read-only) on 2026-11-07; the SEC excerpts are in
`fixtures/` with the SHA-256 of their source files.
"""

from __future__ import annotations

import copy
import json
import types
from pathlib import Path

import psycopg2
import pytest

from src.retriever.xbrl.xbrl_factstore_lookup import XbrlFactstoreLookup
from src.retriever.xbrl.xbrl_factstore_screen import XbrlFactstoreScreen

FIXTURES = Path(__file__).parent / "fixtures"

VST_CIK = "0001692819"
VST25 = "0001692819-26-000006"  # VST FY2025 10-K (vistra-20251231.htm)
G25 = "http://fasb.org/us-gaap/2025:"
OPSEG = ("srt:ConsolidationItemsAxis", "us-gaap:OperatingSegmentsMember",
         "Operating Segments [Member]")


def fact_row(fid, concept, value, decimals, start, end, family, rank, *, dims=None,
             presented=True, anchor=None, accession=VST25, role="primary"):
    """A row as the flat lookup SQL returns it (mv_financial_facts columns)."""
    axis, member, label = dims if dims else (None, None, None)
    return {
        "fact_id": fid, "value": value, "unit": "USD", "decimals": decimals,
        "period_type": "duration", "period_start": start, "period_end": end,
        "concept": concept, "concept_family": family, "concept_pref_rank": rank,
        "concept_role": role, "substitution_code": None, "substitution_note": None,
        "presented": presented, "segment_axis": axis, "segment_member": member,
        "segment_label": label, "dimension_labels": None, "html_anchor": anchor,
        "page_num": None, "section_path": None, "accession_no": accession,
        "linked_chunk_ids": [],
    }


# VST FY2025 10-K, stored rows (period_end is Arelle's next-midnight date). A nil fact is
# stored with value NULL and decimals NULL: `<ix:nonFraction ... xsi:nil="true" ...>`.
VST_TAX_OPSEG = [
    # f-3006: FY2025 tax expense, Operating Segments column, filed nil
    fact_row(146227, G25 + "IncomeTaxExpenseBenefit", None, None, "2025-01-01",
             "2026-01-01", "TaxExpense", 1, dims=OPSEG, anchor="#f-3006"),
    # f-3132: FY2023 comparative, filed nil
    fact_row(146353, G25 + "IncomeTaxExpenseBenefit", None, None, "2023-01-01",
             "2024-01-01", "TaxExpense", 1, dims=OPSEG, anchor="#f-3132"),
]
VST_INTEREST_OPSEG = [
    # f-3000: FY2025 interest expense, Operating Segments column, filed nil
    fact_row(146221, G25 + "InterestExpenseNonoperating", None, None, "2025-01-01",
             "2026-01-01", "InterestExpense", 2, dims=OPSEG, anchor="#f-3000"),
    # f-3066: FY2024 comparative, filed nil
    fact_row(146287, G25 + "InterestExpenseNonoperating", None, None, "2024-01-01",
             "2025-01-01", "InterestExpense", 2, dims=OPSEG, anchor="#f-3066"),
]
# f-87: FY2025 consolidated income tax expense, printed "(179)", tagged +179,000,000.
VST_TAX_FY2025 = fact_row(143329, G25 + "IncomeTaxExpenseBenefit", 179000000.0, -6,
                          "2025-01-01", "2026-01-01", "TaxExpense", 1, anchor="#f-87")
# f-79: FY2024 consolidated interest expense 900,000,000 (undimensioned).
VST_INTEREST_FY2024 = fact_row(143321, G25 + "InterestExpenseNonoperating", 900000000.0, -6,
                               "2024-01-01", "2025-01-01", "InterestExpense", 2,
                               anchor="#f-79")


def run_lookup(rows, family, dimensions="NONE", fy=2025, cik=VST_CIK):
    """`_build_result` over committed rows, as the lookup runs it after its query."""
    lk = XbrlFactstoreLookup()
    notes: list[str] = []
    specs, status = lk._parse_dimensions(dimensions, notes)
    assert status is None
    step = {"id": "s1", "op": "lookup", "cik": cik, "fy": fy, "concept_family": family,
            "dimensions": dimensions, "fiscal_period": "FY"}
    return lk._build_result("s1", step, cik, fy, family, "duration", specs,
                            copy.deepcopy(rows), notes)


# --------------------------------------------------------------------------- screen fake
def segment_row(ticker, cik, value, fact_id, label="Operating Segments [Member]", fy=2025):
    """A row as `_query_segment`'s SQL returns it (mv_segment_matrix columns)."""
    return {"ticker": ticker, "cik": cik, "fiscal_year": fy, "value": value, "unit": "USD",
            "pct_of_total": None, "segment_label": label,
            "section_path": "Disclosure - Segment Information (Details)", "fact_id": fact_id}


# The current segment SQL's rows for FY2025 "operating segments" (one row per filer):
# only VST matches, and its DISTINCT ON picks the nil fact (NULLS FIRST under DESC).
VST_SEGMENT_INTEREST = [segment_row("VST", VST_CIK, None, 146221)]
VST_SEGMENT_TAX = [segment_row("VST", VST_CIK, None, 146227)]


class _Cursor:
    def __init__(self, rows: list, roster: list, as_dict: bool):
        self._view_rows = rows
        self._roster = roster
        self._as_dict = as_dict
        self._rows: list = []

    def execute(self, sql, params=None):
        if "to_regclass" in sql:  # publish_manifest probe: absent, so the gate is inert
            self._rows = [(False,)]
            return
        if "entity_master" in sql:
            rows = [{"cik": cik, "ticker": t} for t, cik in sorted(self._roster)]
        elif "mv_segment_matrix" in sql or "mv_geographic_facts" in sql:
            rows = [dict(r) for r in copy.deepcopy(self._view_rows)]
        else:
            rows = []
        self._rows = rows if self._as_dict else [tuple(r.values()) for r in rows]

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def mogrify(self, sql, params=None):
        return sql.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Conn:
    def __init__(self, rows: list, roster: list):
        self._rows = rows
        self._roster = roster

    def cursor(self, cursor_factory=None):
        return _Cursor(self._rows, self._roster, as_dict=cursor_factory is not None)

    def get_dsn_parameters(self):
        return {"dbname": "fake_sign_and_nil_db"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


def run_segment_screen(rows, family, member="operating segments", order="desc", fy=2025):
    """A segment screen over the fake database; the roster is the rows' filers."""
    roster = sorted({(r["ticker"], r["cik"]) for r in rows})
    step = {"id": "s1", "op": "screen", "concept_family": family, "fy": fy,
            "fiscal_period": "FY", "dimension": {"segment": member},
            "metric_kind": "value", "order": order, "top_n": None}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(psycopg2, "connect", lambda *a, **kw: _Conn(rows, roster))
        return XbrlFactstoreScreen(db_conn_params={}).screen(step)


# --------------------------------------------------------------------------- SEC excerpts
def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def fx():
    """Everything the tests share, as one namespace (safe with hypothesis)."""
    return types.SimpleNamespace(
        G25=G25, OPSEG=OPSEG, VST_CIK=VST_CIK, fact_row=fact_row, run_lookup=run_lookup,
        VST_TAX_OPSEG=VST_TAX_OPSEG, VST_INTEREST_OPSEG=VST_INTEREST_OPSEG,
        VST_TAX_FY2025=VST_TAX_FY2025, VST_INTEREST_FY2024=VST_INTEREST_FY2024,
        segment_row=segment_row, run_segment_screen=run_segment_screen,
        VST_SEGMENT_INTEREST=VST_SEGMENT_INTEREST, VST_SEGMENT_TAX=VST_SEGMENT_TAX,
        companyfacts=_load("companyfacts_excerpts.json"), fsds=_load("fsds_lines.json"),
    )
