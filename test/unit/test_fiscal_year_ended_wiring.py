"""EntityExtractor wiring of "the fiscal year ended <date>".

Spec: docs/specs/fiscal-year-ended-date.md requirement 5.

The DB is the I/O boundary: a fake connection answers the two fiscal_calendar reads (the
filer's whole calendar, and one year's row) from memory; the resolver runs for real. Home
Depot's period ends and labels are public (its 10-K covers; docs/specs/fiscal-label-alignment.md).
"""

from __future__ import annotations

from datetime import date

from src.observability.trace_context import trace_query
from src.retriever.xbrl.entity_extractor import EntityExtractor, ResolvedCompany

HD_CIK = "0000354950"
HD_ROWS = [
    {"cik": HD_CIK, "fiscal_year": 2023, "fiscal_period": "FY",
     "period_start": date(2023, 1, 30), "period_end": date(2024, 1, 28)},
    {"cik": HD_CIK, "fiscal_year": 2024, "fiscal_period": "FY",
     "period_start": date(2024, 1, 29), "period_end": date(2025, 2, 2)},
    {"cik": HD_CIK, "fiscal_year": 2025, "fiscal_period": "FY",
     "period_start": date(2025, 2, 3), "period_end": date(2026, 2, 1)},
]
HD = ResolvedCompany(mention="HD", cik=HD_CIK, ticker="HD", fye_month=2, fye_day=1,
                     last_seen_fy=2025, resolution="exact_ticker", similarity=1.0)


class _FakeCursor:
    def __init__(self, rows: list[dict], log: list[tuple]):
        self._rows = rows
        self._log = log
        self._result: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql: str, params: tuple = ()) -> None:
        self._log.append(params)
        mine = [r for r in self._rows if r["cik"] == params[0]]
        if "fiscal_year = %s" in sql:
            cik, fy, period = params
            hits = [r for r in mine if r["fiscal_year"] == fy]
            hits.sort(key=lambda r: (r["fiscal_period"] != period, r["fiscal_period"] != "FY"))
            self._result = hits[:1]
        else:
            self._result = [{k: r[k] for k in ("fiscal_year", "fiscal_period", "period_end")}
                            for r in mine]

    def fetchone(self) -> dict | None:
        return self._result[0] if self._result else None

    def fetchall(self) -> list[dict]:
        return list(self._result)


class _FakeConn:
    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.log: list[tuple] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self, cursor_factory=None):
        return _FakeCursor(self.rows, self.log)


class _FakeDbExtractor(EntityExtractor):
    def __init__(self, rows: list[dict]):
        super().__init__(db_conn_params={}, company_aliases={})
        self.conn = _FakeConn(rows)

    def _connect(self):
        return self.conn


def _years(periods):
    return [(p.fiscal_year, p.fiscal_period, p.ambiguous) for p in periods]


def test_a_dated_mention_takes_the_filers_label_and_logs_a_decision():
    # oracle: Home Depot's 10-K — the fiscal year ended 2025-02-02 is fiscal 2024
    with trace_query("q") as tc:
        periods = _FakeDbExtractor(HD_ROWS).resolve_periods(
            ["the fiscal year ended February 2, 2025"], [HD])
    assert _years(periods) == [(2024, "FY", False)]
    assert periods[0].period_end == "2025-02-02"
    decisions = [e for e in tc.events if e["kind"] == "decision"]
    assert decisions == [{**decisions[0], "point": "period_resolution",
                          "decision": "fiscal_year_ended_date", "date": "2025-02-02",
                          "fiscal_year": 2024,
                          "mention": "the fiscal year ended February 2, 2025"}]


def test_a_dated_range_resolves_every_labelled_year():
    # oracle: by hand — fiscal 2023 (ended 2024-01-28) through fiscal 2025 (ended 2026-02-01)
    mention = ("between the fiscal year ended January 28, 2024 and the fiscal year ended "
               "February 1, 2026")
    periods = _FakeDbExtractor(HD_ROWS).resolve_periods([mention], [HD])
    assert _years(periods) == [(2023, "FY", False), (2024, "FY", False), (2025, "FY", False)]


def test_an_unmatched_date_is_ambiguous_never_the_digits():
    periods = _FakeDbExtractor(HD_ROWS).resolve_periods(
        ["the fiscal year ended March 15, 2025"], [HD])
    assert len(periods) == 1 and periods[0].ambiguous
    assert periods[0].fiscal_year is None and "2025-03-15" in (periods[0].reason or "")


def test_no_calendar_no_company_or_no_date_keep_todays_reading():
    # no calendar rows for the cik: the digits, as before (maintainer decision (b))
    assert _years(_FakeDbExtractor([]).resolve_periods(
        ["the fiscal year ended February 2, 2025"], [HD])) == [(2025, "FY", False)]
    # no company: a cross-filer mention is unaffected, and no calendar is read
    ext = _FakeDbExtractor(HD_ROWS)
    assert _years(ext.resolve_periods(["the fiscal year ended December 31, 2024"])) == \
        [(2024, "FY", False)]
    assert ext.conn.log == []
    # no full date: today's reading
    assert _years(_FakeDbExtractor(HD_ROWS).resolve_periods(["fiscal 2024"], [HD])) == \
        [(2024, "FY", False)]


def test_a_label_equal_to_the_end_year_logs_nothing():
    rows = [{"cik": HD_CIK, "fiscal_year": 2024, "fiscal_period": "FY",
             "period_start": date(2024, 1, 1), "period_end": date(2024, 12, 31)}]
    with trace_query("q") as tc:
        periods = _FakeDbExtractor(rows).resolve_periods(
            ["the fiscal year ended December 31, 2024"], [HD])
    assert _years(periods) == [(2024, "FY", False)]
    assert not [e for e in tc.events if e["kind"] == "decision"]
