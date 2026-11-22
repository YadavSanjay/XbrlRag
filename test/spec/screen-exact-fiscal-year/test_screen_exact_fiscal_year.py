"""Spec tests for screen-exact-fiscal-year: a screen for fiscal year N ranks only
fiscal-year-N figures (docs/specs/screen-exact-fiscal-year.md).

`select_fiscal_year` is pure. The `screen()` tests replace `psycopg2.connect` (the DB is
an I/O boundary) with a fake store that answers each view's query the way the current
SQL does: per filer, the latest fiscal year <= the requested one.
"""

from __future__ import annotations

import psycopg2
import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.xbrl_factstore_screen import (
    XbrlFactstoreScreen,
    select_fiscal_year,
)

# Total assets at each filer's latest fiscal year <= 2026, from the flat screen's own SQL
# on the 2026-10-14 corpus (USD).
TOTAL_ASSETS_FY2026 = {
    "AMZN": (2025, 818_042_000_000.0),
    "MSFT": (2026, 758_376_000_000.0),
    "GOOGL": (2025, 595_281_000_000.0),
    "META": (2025, 366_021_000_000.0),
    "AAPL": (2025, 359_241_000_000.0),
    "ORCL": (2026, 261_759_000_000.0),
    "TSM": (2025, 252_879_900_000.0),
    "NVDA": (2026, 206_803_000_000.0),
    "AVGO": (2025, 171_092_000_000.0),
    "TSLA": (2025, 137_806_000_000.0),
    "MU": (2025, 82_798_000_000.0),
    "AMD": (2025, 76_926_000_000.0),
}

VIEWS = {
    "flat": {"dimension": "NONE", "metric_kind": "value", "concept_family": "TotalAssets"},
    "geographic": {"dimension": {"geography": "China"}, "metric_kind": "value",
                   "concept_family": "Revenue"},
    "segment": {"dimension": {"segment": "cloud"}, "metric_kind": "value",
                "concept_family": "Revenue"},
    "derived": {"dimension": "NONE", "metric_kind": "derived:OperatingMarginPct",
                "concept_family": "OperatingIncome"},
}


# --------------------------------------------------------------------------- fake store
class _Cursor:
    def __init__(self, store, as_dict):
        self._store = store
        self._as_dict = as_dict
        self._rows: list = []

    def execute(self, sql, params=None):
        params = params or {}
        if "to_regclass" in sql:  # publish_manifest probe: absent, so the gate is inert
            self._rows = [(False,)]
            return
        fy = params.get("fy")
        rows = []
        for i, (ticker, years) in enumerate(sorted(self._store.items())):
            eligible = [(y, v) for y, v in years if y <= fy]
            if not eligible:
                continue
            y, v = max(eligible)
            rows.append({
                "ticker": ticker, "cik": f"{i:010d}", "fiscal_year": y, "value": v,
                "unit": "USD", "pct_of_total": None, "geo_code": "CN", "geo_name": "China",
                "segment_label": "Cloud", "section_path": None, "fact_id": 1000 + i,
                "period_end": None, "html_anchor": None, "page_num": None,
                "accession_no": None, "metric_name": "OperatingMarginPct",
                "formula": "OperatingIncome / Revenue", "component_fact_ids": [1, 2],
            })
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
    def __init__(self, store):
        self._store = store

    def cursor(self, cursor_factory=None):
        return _Cursor(self._store, as_dict=cursor_factory is not None)

    def get_dsn_parameters(self):
        return {"dbname": "fake_screen_db"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


def _screen(monkeypatch, store, fy, view="flat", top_n=5):
    monkeypatch.setattr(psycopg2, "connect", lambda **kw: _Conn(store))
    step = {"id": "s1", "op": "screen", "fy": fy, "order": "desc", "top_n": top_n,
            **VIEWS[view]}
    return XbrlFactstoreScreen(db_conn_params={}).screen(step)


def _excluded(result) -> list:
    # `excluded` is new in to_dict(); read it as [] when absent so a missing key fails an
    # assertion rather than raising.
    return result.to_dict().get("excluded") or []


def _point_store():
    return {t: [(y, v)] for t, (y, v) in TOTAL_ASSETS_FY2026.items()}


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_select_fiscal_year_point_case():
    # oracle: requirement — the 2026-10-14 FY2026 total-assets rows: three FY2026 filers
    # kept in input order, nine FY2025 filers excluded with their latest year
    rows = [{"ticker": t, "fiscal_year": y, "value": v}
            for t, (y, v) in TOTAL_ASSETS_FY2026.items()]
    kept, excluded = select_fiscal_year(rows, 2026)
    assert [r["ticker"] for r in kept] == ["MSFT", "ORCL", "NVDA"]
    assert excluded == [{"ticker": t, "latest_fiscal_year": 2025}
                        for t in sorted(["AMZN", "GOOGL", "META", "AAPL", "TSM", "AVGO",
                                         "TSLA", "MU", "AMD"])]
    assert select_fiscal_year([], 2026) == ([], [])
    none_rows = [{"ticker": "X", "fiscal_year": None}]
    assert select_fiscal_year(none_rows, 2026) == (
        [], [{"ticker": "X", "latest_fiscal_year": None}])


row_st = st.fixed_dictionaries({
    "ticker": st.sampled_from(["A", "B", "C", "D", "E"]),
    "fiscal_year": st.one_of(st.none(), st.integers(min_value=2022, max_value=2026)),
    "value": st.floats(min_value=0, max_value=1e12, allow_nan=False),
})


@pytest.mark.criterion("C1")
@given(st.lists(row_st, max_size=12), st.integers(min_value=2022, max_value=2026))
def test_select_fiscal_year_rule_for_any_rows(rows, fy):
    # oracle: the C1 rule written out
    kept, excluded = select_fiscal_year(rows, fy)
    assert kept == [r for r in rows if r["fiscal_year"] == fy]
    kept_tickers = {r["ticker"] for r in kept}
    expected = []
    for t in sorted({r["ticker"] for r in rows} - kept_tickers):
        years = [r["fiscal_year"] for r in rows
                 if r["ticker"] == t and r["fiscal_year"] is not None]
        expected.append({"ticker": t, "latest_fiscal_year": max(years) if years else None})
    assert excluded == expected


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
def test_fy2026_total_assets_screen(monkeypatch):
    # oracle: requirement — only MSFT, ORCL, NVDA have FY2026 annual figures; ranked by
    # value (758.4B > 261.8B > 206.8B); the nine FY2025 filers are excluded, not ranked
    result = _screen(monkeypatch, _point_store(), 2026)
    assert result.is_ok
    assert [(r["ticker"], r["fiscal_year"]) for r in result.rows] == [
        ("MSFT", 2026), ("ORCL", 2026), ("NVDA", 2026)]
    assert [r["rank"] for r in result.rows] == [1, 2, 3]
    excluded = _excluded(result)
    assert [e["ticker"] for e in excluded] == sorted(
        ["AMZN", "GOOGL", "META", "AAPL", "TSM", "AVGO", "TSLA", "MU", "AMD"])
    assert {e["latest_fiscal_year"] for e in excluded} == {2025}


@pytest.mark.criterion("C2")
def test_no_filer_has_the_year_is_not_found(monkeypatch):
    # oracle: requirement — nothing for FY2027 yet: NOT_FOUND, every filer listed
    result = _screen(monkeypatch, _point_store(), 2027)
    assert result.status.value == "NOT_FOUND"
    assert result.rows == []
    assert len(_excluded(result)) == 12


years_st = st.lists(
    st.tuples(st.integers(min_value=2022, max_value=2026),
              st.floats(min_value=1.0, max_value=1e12, allow_nan=False)),
    min_size=1, max_size=4, unique_by=lambda t: t[0])
stores = st.dictionaries(st.sampled_from(["AAPL", "AMD", "AMZN", "MSFT", "NVDA", "ORCL"]),
                         years_st, min_size=1, max_size=6)


@pytest.mark.criterion("C2")
@given(stores, st.integers(min_value=2023, max_value=2026),
       st.sampled_from(sorted(VIEWS)), st.integers(min_value=1, max_value=6))
def test_every_view_ranks_only_the_requested_year(store, fy, view, top_n):
    # oracle: the C2 rule written out over the fake store
    with pytest.MonkeyPatch.context() as mp:
        result = _screen(mp, store, fy, view=view, top_n=top_n)
    latest = {t: max((y, v) for y, v in ys if y <= fy)
              for t, ys in store.items() if any(y <= fy for y, _ in ys)}
    # Ranking is a stable descending sort over rows returned in ticker order, so equal
    # values keep ticker order.
    at_fy = sorted(((v, t) for t, (y, v) in latest.items() if y == fy),
                   key=lambda vt: (-vt[0], vt[1]))
    expected_rows = [t for _, t in at_fy][:top_n]
    assert [r["ticker"] for r in result.rows] == expected_rows
    assert all(r["fiscal_year"] == fy for r in result.rows)
    assert _excluded(result) == [
        {"ticker": t, "latest_fiscal_year": y} for t, (y, _) in sorted(latest.items()) if y != fy]
    assert result.status.value == ("OK" if at_fy else "NOT_FOUND")


# --------------------------------------------------------------------------- C3
@pytest.mark.criterion("C3")
def test_adding_an_older_filer_only_grows_excluded(monkeypatch):
    # oracle: metamorphic — a filer with only FY2024 cannot displace FY2026 rows
    base = _screen(monkeypatch, _point_store(), 2026)
    grown_store = {**_point_store(), "ZZZZ": [(2024, 9e12)]}
    grown = _screen(monkeypatch, grown_store, 2026)
    assert grown.rows == base.rows
    assert _excluded(grown) == sorted(
        _excluded(base) + [{"ticker": "ZZZZ", "latest_fiscal_year": 2024}],
        key=lambda e: e["ticker"])


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(stores, st.integers(min_value=2023, max_value=2026), st.sampled_from(sorted(VIEWS)),
       st.integers(min_value=2022, max_value=2025))
def test_older_filer_and_removed_year_for_any_store(store, fy, view, old_year):
    # oracle: metamorphic — adding an older-only filer never changes the ranking; with
    # every fiscal-year-N row removed the screen is NOT_FOUND
    if old_year >= fy:
        old_year = fy - 1
    with pytest.MonkeyPatch.context() as mp:
        base = _screen(mp, store, fy, view=view)
        grown = _screen(mp, {**store, "ZZZZ": [(old_year, 5e11)]}, fy, view=view)
        stripped = {t: [(y, v) for y, v in ys if y != fy] for t, ys in store.items()}
        stripped = {t: ys for t, ys in stripped.items() if ys}
        none_left = _screen(mp, stripped, fy, view=view) if stripped else None
    assert grown.rows == base.rows
    assert {"ticker": "ZZZZ", "latest_fiscal_year": old_year} in _excluded(grown)
    if none_left is not None:
        assert none_left.status.value == "NOT_FOUND" and none_left.rows == []
