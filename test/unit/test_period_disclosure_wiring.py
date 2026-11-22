"""The period-disclosure wiring (docs/specs/period-disclosure.md R7.1, R7.4, R7.5).

The views mirror the locked pure functions of ``src/retriever/xbrl/period_disclosure.py``;
these tests keep the committed DDL in step with them (the bands, the year-length columns,
the closing-only formulas) and check the variance bridge's year-length note over a faked
database (``src.api.db``, the I/O boundary). The live checks over every stored row are
``test/integration/test_period_disclosure_views_live.py``.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from src.api.v1 import service
from src.retriever.xbrl.period_disclosure import DURATION_BANDS, duration_class

_MV_DIR = Path(__file__).resolve().parents[2] / "scripts" / "ddl" / "mv"
_BAND = re.compile(
    r"WHEN \(ctx\.period_end - ctx\.period_start\) BETWEEN\s+(\d+) AND (\d+) THEN '(\w+)'")
_ELSE = re.compile(r"ELSE\s+'(\w+)'\s+--[^\n]*\n\s*END\s+AS duration_kind")
_CLOSING = "(closing; opening balance not filed)"
_AVERAGE_METRICS = (
    "ReturnOnAssetsPct", "ReturnOnEquityPct", "ReturnOnInvestedCapitalPct", "AssetTurnover",
    "InventoryTurnover", "DaysSalesOutstanding", "DaysInventoryOutstanding",
)


def _sql(name: str) -> str:
    return (_MV_DIR / name).read_text(encoding="utf-8")


_BASE_VIEWS = ["10_mv_financial_facts.sql", "11_mv_financial_facts_multidim.sql"]


@pytest.mark.parametrize("name", _BASE_VIEWS)
def test_duration_kind_bands_mirror_duration_class(name):
    sql = _sql(name)
    bands = tuple((kind, int(lo), int(hi)) for lo, hi, kind in _BAND.findall(sql))
    assert bands == DURATION_BANDS
    assert _ELSE.findall(sql) == ["other"]


@pytest.mark.parametrize("days,kind", [
    (91, "quarter"), (182, "half"), (274, "nine_month"), (365, "annual"), (371, "annual"),
    (334, "other"), (731, "other"), (31, "other"), (132, "other"), (112, "quarter"),
])
def test_sql_case_classes_the_spec_examples_like_duration_class(days, kind):
    # oracle: the spec's C1 day counts (CEG 334, MCD 731, VST 31) and corpus notes (COST
    # 112, CSCO 132); the SQL CASE evaluated in Python must agree with duration_class.
    sql = _sql("10_mv_financial_facts.sql")
    sql_bands = [(k, int(lo), int(hi)) for lo, hi, k in _BAND.findall(sql)]
    sql_kind = next((k for k, lo, hi in sql_bands if lo <= days <= hi), "other")
    start = dt.date(2024, 1, 1)
    assert sql_kind == duration_class(start, start + dt.timedelta(days=days)) == kind


def test_yoy_view_carries_both_periods_and_their_lengths():
    sql = _sql("33_mv_yoy_comparison.sql")
    for column in ("period_start_current", "period_end_current", "days_current",
                   "period_start_prior", "period_end_prior", "days_prior"):
        assert re.search(rf"\bAS {column}\b", sql), column


def test_every_average_based_metric_says_when_it_used_the_closing_balance():
    sql = _sql("34_mv_derived_metrics.sql")
    blocks = sql.split("UNION ALL")
    for metric in _AVERAGE_METRICS:
        block = next(b for b in blocks if f"'{metric}'" in b)
        assert "closing_only THEN" in block, metric
        assert _CLOSING in block, metric


def test_opening_balance_is_the_same_filings_instant_at_the_year_start():
    sql = _sql("34_mv_derived_metrics.sql")
    lateral = sql[sql.index("bal_avg AS ("):sql.index("assets_avg AS")]
    for predicate in ("ob.accession_no = c.accession_no", "ob.concept = c.concept",
                      "ob.unit IS NOT DISTINCT FROM c.unit", "ob.value IS NOT NULL",
                      "ob.period_end = fs.period_start", "ORDER BY ob.fact_id"):
        assert predicate in lateral, predicate
    # The prior-year filing is no longer the opening balance's source.
    assert "fiscal_year = c.fiscal_year - 1" not in sql


# ---------------------------------------------------------------------------
# The variance bridge's year-length note (R7.4), over a faked src.api.db
# ---------------------------------------------------------------------------

@dataclass
class _Company:
    cik: str | None = "0000320193"
    ticker: str | None = "AAPL"


def _fake_db(monkeypatch, total: dict | None):
    monkeypatch.setattr(service, "resolve_company", lambda c: _Company())
    monkeypatch.setattr(service.db, "query_one", lambda sql, params=None: total)
    monkeypatch.setattr(service.db, "query", lambda sql, params=None: [])


def _total(start_c, end_c, start_p, end_p) -> dict:
    return {"value_current": 2.0, "value_prior": 1.0, "yoy_change": 1.0,
            "fact_id_current": 1, "fact_id_prior": 2,
            "period_start_current": start_c, "period_end_current": end_c,
            "period_start_prior": start_p, "period_end_prior": end_p}


def test_bridge_notes_a_53_week_year(monkeypatch):
    # oracle: spec C3 — AAPL FY2024 (2023-10-01 to stored 2024-09-29, 364 days) against
    # FY2023 (2022-09-25 to stored 2023-10-01, 371 days, a 53-week year).
    _fake_db(monkeypatch, _total("2023-10-01", "2024-09-29", "2022-09-25", "2023-10-01"))
    _, bridge = service.variance_bridge("AAPL", "Revenue", 2024)
    note = bridge["year_length_note"]
    assert note is not None
    assert "364" in note and "371" in note and "53-week" in note


def test_bridge_has_no_note_for_equal_years(monkeypatch):
    # oracle: spec C3 — MSFT FY2026 against FY2025, 365 and 365 days.
    _fake_db(monkeypatch, _total("2025-07-01", "2026-07-01", "2024-07-01", "2025-07-01"))
    _, bridge = service.variance_bridge("MSFT", "Revenue", 2026)
    assert bridge["year_length_note"] is None


def test_bridge_has_no_note_without_periods(monkeypatch):
    _fake_db(monkeypatch, _total(None, None, None, None))
    _, bridge = service.variance_bridge("AAPL", "Revenue", 2024)
    assert bridge["year_length_note"] is None


def test_bridge_route_returns_the_note(monkeypatch):
    from fastapi.testclient import TestClient

    from src.api.main import app

    _fake_db(monkeypatch, _total("2023-10-01", "2024-09-29", "2022-09-25", "2023-10-01"))
    monkeypatch.setattr(service, "resolve_company", lambda c: _ResolvedForRoute())
    resp = TestClient(app).post("/api/v1/facts/bridge",
                                json={"company": "AAPL", "fiscal_year": 2024})
    assert resp.status_code == 200
    assert "53-week" in resp.json()["year_length_note"]


@dataclass
class _ResolvedForRoute:
    cik: str | None = "0000320193"
    ticker: str | None = "AAPL"
    company_name: str | None = "Apple Inc."
    fye_month: int | None = 9
    fye_day: int | None = 28
    last_seen_fy: int | None = 2026
    resolution: str = "exact_ticker"
    similarity: float = 1.0
