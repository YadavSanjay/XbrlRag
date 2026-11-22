"""Wiring of a fourth-quarter lookup (docs/specs/q4-lookup.md, Wiring 1-4).

The lookup runs over a fake connection serving synthetic, corpus-shaped rows of the flat
S1 query; the expected answers follow from the spec's requirements by hand.
"""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.retriever.xbrl.citation_integrity import evidence_index
from src.retriever.xbrl.plan_executor import PlanExecutor, StepStatus, q4_fact_ids
from src.retriever.xbrl.xbrl_factstore_lookup import (
    Q4_DIMENSIONAL_REASON,
    Q4_INSTANT_NOTE,
    LookupStatus,
    XbrlFactstoreLookup,
)

M = 1_000_000
CIK = "0000000001"
START = dt.date(2023, 7, 1)
K24, Q24, K25 = "0000000001-24-000010", "0000000001-24-000007", "0000000001-25-000010"


def _row(fact_id, label, fp, start, days, value, accession, filed, *, own=True,
         unit="USD", concept="http://fasb.org/us-gaap/2024:Revenues", period_type="duration"):
    """A stored row as the flat S1 query returns it (stored end = start + days)."""
    end = start + dt.timedelta(days=days)
    dpe = end - dt.timedelta(days=1) if own else end + dt.timedelta(days=364)
    return {
        "fact_id": fact_id, "value": Decimal(value), "unit": unit,
        "period_type": period_type,
        "period_start": None if period_type == "instant" else start,
        "period_end": end, "concept": concept, "concept_family": "Revenue",
        "concept_pref_rank": 1, "concept_role": "primary", "substitution_code": None,
        "substitution_note": None, "presented": True, "segment_axis": None,
        "segment_member": None, "segment_label": None, "has_multiple_dimensions": False,
        "html_anchor": f"#f{fact_id}", "page_num": None, "section_path": None,
        "accession_no": accession, "linked_chunk_ids": [], "decimals": -6,
        "fiscal_year": label, "fiscal_period": fp, "filed_date": filed,
        "is_amendment": False, "is_canonical_filing": True, "document_period_end": dpe,
    }


def _base_rows() -> list[dict]:
    """FY2024: year 1,000M, nine months 700M, and the 10-Q's discrete third quarter."""
    return [
        _row(1, 2024, "FY", START, 366, 1_000 * M, K24, dt.date(2024, 7, 30)),
        _row(2, 2024, "Q3", START, 275, 700 * M, Q24, dt.date(2024, 4, 30)),
        _row(3, 2024, "Q3", dt.date(2024, 1, 1), 91, 240 * M, Q24, dt.date(2024, 4, 30)),
    ]


class FakeDb:
    """Serves the flat query's rows and records every read's parameters."""

    def __init__(self, rows):
        self.rows = rows
        self.calls: list[dict] = []

    def fetch(self, _conn, sql: str, params: dict) -> list[dict]:
        self.calls.append(params)
        assert "mv_financial_facts" in sql
        return [r for r in self.rows if r["period_type"] == params["period_type"]]


def _lookup(db: FakeDb, monkeypatch) -> XbrlFactstoreLookup:
    lk = XbrlFactstoreLookup()

    @contextmanager
    def connect():
        yield None

    monkeypatch.setattr(lk, "_connect", connect)
    monkeypatch.setattr(lk, "_fetch", db.fetch)
    return lk


def _step(**kw):
    return {"id": "s1", "op": "lookup", "cik": CIK, "fy": 2024, "concept_family": "Revenue",
            "fiscal_period": "Q4", **kw}


def test_a_q4_duration_is_derived_without_a_duration_kind_pin(monkeypatch):
    db = FakeDb(_base_rows())
    r = _lookup(db, monkeypatch).lookup(_step())
    assert r.status == LookupStatus.OK
    assert r.value == pytest.approx(300 * M, rel=1e-12)  # 1,000M - 700M
    assert r.unit == "USD"
    assert r.citation.fact_id == 1
    assert r.q4["status"] == "derived" and r.q4["fact_ids"] == [1, 2]
    assert (r.q4["period_start"], r.q4["period_end"], r.q4["days"]) == (
        "2024-04-01", "2024-06-30", 91)
    assert any("derived" in n for n in r.resolution_notes)
    assert all("duration_kind" not in c for c in db.calls)
    assert r.to_dict()["q4"]["basis"] == "latest"


def test_ytd_is_ignored_for_q4(monkeypatch):
    r = _lookup(FakeDb(_base_rows()), monkeypatch).lookup(_step(ytd=True))
    assert r.status == LookupStatus.OK
    assert r.value == pytest.approx(300 * M, rel=1e-12)


def test_a_filed_fourth_quarter_answers(monkeypatch):
    rows = [*_base_rows(),
            _row(4, 2024, "FY", dt.date(2024, 4, 1), 91, 301 * M, K24, dt.date(2024, 7, 30))]
    r = _lookup(FakeDb(rows), monkeypatch).lookup(_step())
    assert r.status == LookupStatus.OK
    assert r.value == pytest.approx(301 * M, rel=1e-12)
    assert r.citation.fact_id == 4
    assert r.q4["status"] == "filed" and r.q4["fact_ids"] == [4]
    assert "300,000,000" in r.q4["note"]  # the derivation that differs is named


def test_no_nine_months_is_not_found_with_the_reason(monkeypatch):
    rows = [r for r in _base_rows() if r["fact_id"] != 2]
    r = _lookup(FakeDb(rows), monkeypatch).lookup(_step())
    assert r.status == LookupStatus.NOT_FOUND
    assert "nine-month" in r.error
    assert r.error in r.resolution_notes[-1]
    assert r.citation is None and r.q4 is None


def test_a_dimensional_q4_is_not_found_without_a_read(monkeypatch):
    db = FakeDb(_base_rows())
    r = _lookup(db, monkeypatch).lookup(_step(dimensions={"product": "cloud"}))
    assert r.status == LookupStatus.NOT_FOUND
    assert r.error == Q4_DIMENSIONAL_REASON
    assert db.calls == []


def test_an_instant_q4_is_the_fy_end_balance(monkeypatch):
    rows = [_row(9, 2024, "FY", START, 366, 5_000 * M, K24, dt.date(2024, 7, 30),
                 period_type="instant")]
    for r in rows:
        r["concept_family"] = "TotalAssets"
    db = FakeDb(rows)
    r = _lookup(db, monkeypatch).lookup(_step(concept_family="TotalAssets"))
    assert r.status == LookupStatus.OK
    assert r.value == pytest.approx(5_000 * M, rel=1e-12)
    assert r.citation.fact_id == 9
    assert r.resolution_notes[-1] == Q4_INSTANT_NOTE
    assert r.q4 is None


def test_as_of_keeps_rows_filed_on_or_before_it(monkeypatch):
    # A FY2025 10-K restates the year alone: today that is the as-filed basis; as of a date
    # before it, the restatement is not yet filed and both sides are the latest.
    rows = [*_base_rows(),
            _row(11, 2025, "FY", START, 366, 1_030 * M, K25, dt.date(2025, 7, 30), own=False)]
    now = _lookup(FakeDb(rows), monkeypatch).lookup(_step())
    assert now.q4["basis"] == "as_filed" and now.value == pytest.approx(300 * M, rel=1e-12)
    then = _lookup(FakeDb(rows), monkeypatch).lookup(_step(as_of="2025-01-01"))
    assert then.q4["basis"] == "latest" and then.q4["fact_ids"] == [1, 2]
    early = _lookup(FakeDb(rows), monkeypatch).lookup(_step(as_of="2024-06-01"))
    assert early.status == LookupStatus.NOT_FOUND  # the 10-K is not yet filed


def test_a_currency_code_names_the_per_share_unit(monkeypatch):
    rows = [dict(r, unit="shares*USD", value=r["value"] / M / 100) for r in _base_rows()]
    r = _lookup(FakeDb(rows), monkeypatch).lookup(_step(unit="USD"))
    assert r.status == LookupStatus.OK
    assert r.unit == "USD/shares"
    assert r.value == pytest.approx(3.0, abs=1e-12)  # 10.00 - 7.00


def test_without_a_unit_the_reporting_currency_answers(monkeypatch):
    # A EUR convenience translation of the year beside USD: USD is reported at every
    # period, EUR only at the year end, so USD is the reporting currency.
    eur = dict(_base_rows()[0], fact_id=21, unit="EUR", value=Decimal(900 * M))
    r = _lookup(FakeDb([*_base_rows(), eur]), monkeypatch).lookup(_step())
    assert r.status == LookupStatus.OK and r.unit == "USD"
    assert r.value == pytest.approx(300 * M, rel=1e-12)


def test_the_executor_carries_both_ids_and_the_q4_period(monkeypatch):
    lk = _lookup(FakeDb(_base_rows()), monkeypatch)
    factstore = MagicMock()
    factstore.lookup.side_effect = lk.lookup
    result = PlanExecutor(factstore_lookup=factstore).execute({"steps": [_step()]})
    (step,) = result.steps
    assert step.status is StepStatus.OK
    assert step.component_fact_ids == {"q4_year": 1, "q4_nine_month": 2}
    assert step.period["period_start"] == "2024-04-01"
    assert step.period["period_end"] == "2024-06-30"  # stated: the quarter's last day
    d = step.to_dict()
    assert d["q4"]["status"] == "derived"
    index = evidence_index(result.to_dict())
    assert {"1", "2", "s1"} <= set(index)


def test_q4_fact_ids_only_for_a_derived_q4():
    assert q4_fact_ids(None) is None
    assert q4_fact_ids({"status": "filed", "fact_ids": [4]}) is None
    assert q4_fact_ids({"status": "derived", "fact_ids": [1, 2]}) == {
        "q4_year": 1, "q4_nine_month": 2}
