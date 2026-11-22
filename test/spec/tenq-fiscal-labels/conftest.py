"""Fixtures for the tenq-fiscal-labels spec tests.

Spec: docs/specs/tenq-fiscal-labels.md. Everything under ``fixtures/`` is public SEC data copied
from local caches (companyfacts, FSDS ``sub.txt``, EDGAR instances and documents, the corpus
manifests), each with its URL, SHA-256 and retrieval date; nothing is fetched. Synthetic
calendars and instances are built here by hand. The EDGAR company index is the only fake, at the
download seam. Everything is exposed through one session-scoped ``kit`` so hypothesis tests can
use it.

The loader checks the oracles against each other when it starts (spec Notes): a fixture that
broke one of those checks fails here, before any test runs.
"""

from __future__ import annotations

import calendar
import json
import re
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.ingest.sec_edgar.ingest.layer0_populator import ManifestRecord

FIXTURES = Path(__file__).parent / "fixtures"

ORCL_Q1_FY2027 = "0001193125-26-389274"
ORCL_Q1_FY2026 = "0001193125-25-200095"
NVDA_FY2025_10K = "0001045810-25-000023"
MISMATCHED = (
    "0000320193-26-000013", "0000320193-26-000020", "0001193125-26-191507",
    "0000723125-26-000006", "0000723125-26-000015", "0001045810-26-000052",
    "0001045810-26-000075", "0001193125-26-101045", ORCL_Q1_FY2027,
)
# ORCL's first 10-Q of fiscal 2022: the start of the "15 earlier ORCL 10-Qs" (report G2).
ORCL_FIRST_NEGATIVE = "0001564590-21-047772"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _d(text: str) -> date:
    return date.fromisoformat(text)


# --------------------------------------------------------------------- companyfacts filings

CF = _load("companyfacts_filings.json")["filers"]
FILINGS = {
    ticker: [{**row, "period_end": _d(row["period_end"]), "filed": _d(row["filed"])}
             for row in filer["filings"]]
    for ticker, filer in CF.items()
}
CIK = {ticker: filer["cik"] for ticker, filer in CF.items()}
BY_ACCESSION = {row["accession_no"]: row for rows in FILINGS.values() for row in rows}

# ORCL's own text names 0001193125-26-389274 fiscal 2027, the quarter ended 2026-08-31.
TEXT = _load("orcl_q1_fy2027_text.json")
_years_in_text = {int(y) for s in TEXT["sentences"]
                  for y in re.findall(r"first quarter of fiscal (\d{4})", s)}
assert _years_in_text == {2027}, _years_in_text
assert TEXT["cover"] == "For the quarterly period ended August 31, 2026", TEXT["cover"]
assert BY_ACCESSION[ORCL_Q1_FY2027]["period_end"] == date(2026, 8, 31)

# The filer's own fiscal year and quarter of every filing: SEC's fy/fp, except the one filing
# whose DEI (and so SEC's fy) contradicts its own text.
OWN_FY = {acc: row["fy"] for acc, row in BY_ACCESSION.items()}
OWN_FY[ORCL_Q1_FY2027] = 2027
OWN_QUARTER = {acc: int(row["fp"][1]) for acc, row in BY_ACCESSION.items() if row["form"] == "10-Q"}

# All five filers name a fiscal year after the calendar year it ends in (spec Notes).
for _acc, _row in BY_ACCESSION.items():
    if _row["form"] == "10-K":
        assert _row["fp"] == "FY" and _row["fy"] == _row["period_end"].year, (_acc, _row)


def _year_ending_after(ticker: str, q: date) -> int:
    """The calendar year of the first 10-K period end after ``q``, else the last one's + 1."""
    ends = sorted(r["period_end"] for r in FILINGS[ticker] if r["form"] == "10-K")
    later = [e for e in ends if e > q]
    return later[0].year if later else ends[-1].year + 1


# So the year of every 10-Q is the year its fiscal year ends in: SEC's fy for all but ORCL's.
for _ticker, _rows in FILINGS.items():
    for _row in _rows:
        if _row["form"] == "10-Q":
            assert OWN_FY[_row["accession_no"]] == _year_ending_after(_ticker, _row["period_end"])


def annual_periods(ticker: str) -> dict[date, int]:
    """The filer's 10-K period ends → SEC's fy, which is the filer's own label for all five."""
    return {r["period_end"]: r["fy"] for r in FILINGS[ticker] if r["form"] == "10-K"}


def tenqs(ticker: str) -> list[dict]:
    return [r for r in FILINGS[ticker] if r["form"] == "10-Q"]


def label_row(row: dict, dei: int | None = None, keep_dei: bool = True) -> dict:
    """One ``label_filings`` row; its DEI is SEC's fy (SEC repeats DEI) unless given."""
    return {"accession_no": row["accession_no"], "form_type": row["form"],
            "period_end": row["period_end"], "filed_date": row["filed"],
            "dei_fiscal_year": row["fy"] if keep_dei else dei}


ORCL_ROWS = [label_row(r) for r in FILINGS["ORCL"]]
ORCL_TENQS = tenqs("ORCL")
_first = next(i for i, r in enumerate(ORCL_TENQS) if r["accession_no"] == ORCL_FIRST_NEGATIVE)
ORCL_SIXTEEN = ORCL_TENQS[_first:]
assert len(ORCL_SIXTEEN) == 16 and ORCL_SIXTEEN[-1]["accession_no"] == ORCL_Q1_FY2027

# --------------------------------------------------------------------- FSDS sub rows

_FSDS = _load("fsds_sub.json")
_HEADER = _FSDS["header"].split("\t")
SUB = {}
for _r in _FSDS["rows"]:
    _row = dict(zip(_HEADER, _r["raw"].split("\t"), strict=True))
    SUB[_row["adsh"]] = _row


def fsds_label(sub_row: dict) -> tuple[int, int]:
    """Fiscal year and quarter from FSDS ``period`` and ``fye`` alone (spec Notes).

    The year is the calendar year of the first year end on or after ``period``; the quarter
    is the number of whole quarters from the preceding year end. ``sub.fy`` plays no part.
    """
    year, month = int(sub_row["period"][:4]), int(sub_row["period"][4:6])
    fye_month = int(sub_row["fye"][:2])
    end_year = year if month <= fye_month else year + 1
    return end_year, ((month - fye_month) % 12) // 3


for _acc in MISMATCHED:
    _fy, _q = fsds_label(SUB[_acc])
    assert f"Q{_q}" == SUB[_acc]["fp"], (_acc, _q, SUB[_acc]["fp"])
    assert _fy == OWN_FY[_acc] and _q == OWN_QUARTER[_acc], (_acc, _fy, _q)
    # sub.fy agrees except where SEC repeats ORCL's wrong DEI.
    assert (int(SUB[_acc]["fy"]) == _fy) == (_acc != ORCL_Q1_FY2027), _acc
# SEC's known offset: NVIDIA's fiscal 2025 10-K is fy 2024 in FSDS.
assert fsds_label(SUB[NVDA_FY2025_10K]) == (2025, 0) and SUB[NVDA_FY2025_10K]["fy"] == "2024"
assert fsds_label(SUB[ORCL_Q1_FY2026]) == (2026, 1) == (int(SUB[ORCL_Q1_FY2026]["fy"]), 1)

# --------------------------------------------------------------------- manifests


def _record(m: dict) -> ManifestRecord:
    """A parsed manifest, field by field as ``Layer0Populator._parse_manifest`` reads it."""
    return ManifestRecord(
        ticker=m["ticker"], cik=str(m["cik"]).zfill(10), accession_no=m["accession_no"],
        form_type=m["form"].upper(), filing_type=m["filing_type"].upper(),
        is_amendment=bool(m["is_amendment"]), fiscal_year=int(m["fiscal_year"]),
        quarter=None if m["quarter"] is None else int(m["quarter"]),
        filed_date=_d(m["filing_date"]),
        period_start=None if m["period_start"] is None else _d(m["period_start"]),
        period_end=_d(m["period_end"]), fye_month=m["fye_month"], fye_day=m["fye_day"],
        primary_document=m["primary_document"],
    )


_MANIFEST_FILE = _load("manifests.json")
MANIFESTS = {e["manifest"]["accession_no"]: e["manifest"] for e in _MANIFEST_FILE["manifests"]}
# The five filers' manifests plus the INTC and AMZN ones the bounds tests write to disk.
ALL_MANIFESTS = {**MANIFESTS, **{e["manifest"]["accession_no"]: e["manifest"]
                                 for e in _MANIFEST_FILE["others"]}}
RECORDS = [_record(m) for m in MANIFESTS.values()]
assert len(RECORDS) == 33 and set(MISMATCHED) <= set(MANIFESTS)
assert set(MANIFESTS) <= set(BY_ACCESSION), set(MANIFESTS) - set(BY_ACCESSION)
# Today's registry quarter is the calendar quarter of the period end (report G3).
for _acc in MISMATCHED:
    _m = MANIFESTS[_acc]
    assert _m["quarter"] == (_d(_m["period_end"]).month - 1) // 3 + 1, _acc
DEI = {acc: BY_ACCESSION[acc]["fy"] for acc in MANIFESTS}


def orcl_q1_fy2026_record() -> ManifestRecord:
    """ORCL's Q1 FY2026 10-Q as the downloader would write it today (calendar Q3 of 2025)."""
    row = BY_ACCESSION[ORCL_Q1_FY2026]
    return ManifestRecord(
        ticker="ORCL", cik=CIK["ORCL"], accession_no=ORCL_Q1_FY2026, form_type="10-Q",
        filing_type="10-Q", is_amendment=False, fiscal_year=2025, quarter=3,
        filed_date=row["filed"], period_start=date(2025, 6, 1), period_end=row["period_end"],
        fye_month=5, fye_day=31, primary_document=None,
    )


# --------------------------------------------------------------------- EDGAR index (fake)


class IndexFiling:
    """A row of the EDGAR filing index. Touching anything that fetches filing text fails."""

    def __init__(self, form: str, filed: date, report: str, accession: str) -> None:
        self.form = form
        self.filing_date = filed
        self.report_date = report
        self.accession_no = accession

    def __repr__(self) -> str:
        return f"IndexFiling({self.form}, {self.filing_date}, {self.report_date})"

    def _fetches_text(self, *_: object) -> None:
        raise AssertionError(f"filing text fetched during selection: {self!r}")

    period_of_report = property(_fetches_text)
    attachments = property(_fetches_text)
    primary_documents = property(_fetches_text)
    sgml = _fetches_text
    full_text_submission = _fetches_text


class FakeCompany:
    """Stands in for edgartools' Company: the index, in memory, filtered as the library does."""

    def __init__(self, filings: list[IndexFiling]) -> None:
        self.filings = filings

    def get_filings(self, *, form: str, filing_date: str | None = None, **_: object) -> list:
        rows = [f for f in self.filings if f.form == form]
        if filing_date:
            low, _sep, high = filing_date.partition(":")
            if low:
                rows = [f for f in rows if f.filing_date.isoformat() >= low]
            if high:
                rows = [f for f in rows if f.filing_date.isoformat() <= high]
        return rows


def index_of(ticker: str) -> list[IndexFiling]:
    """The filer's 10-K and 10-Q index rows since 2020-06, from companyfacts."""
    return [IndexFiling(r["form"], r["filed"], r["period_end"].isoformat(), r["accession_no"])
            for r in FILINGS[ticker]]


# --------------------------------------------------------------------- instances

INSTANCES = _load("instances.json")["instances"]
PERIODS = _load("companyfacts_periods.json")["periods"]


def instance_bytes(accession: str) -> bytes:
    return (FIXTURES / INSTANCES[accession]["file"]).read_bytes()


def statement_period(accession: str, form: str) -> tuple[date, date]:
    """The year- (annual form) or quarter-length revenue period companyfacts lists."""
    low, high = (350, 380) if form == "10-K" else (80, 100)
    spans = {(_d(e["start"]), _d(e["end"])) for e in PERIODS[accession]["entries"]}
    found = [s for s in spans if low <= (s[1] - s[0]).days + 1 <= high]
    assert len(found) == 1, (accession, spans)
    return found[0]


for _acc in (ORCL_Q1_FY2027, "0001193125-26-101045"):
    _text = instance_bytes(_acc).decode("utf-8")
    assert f">{BY_ACCESSION[_acc]['fy']}</dei:DocumentFiscalYearFocus>" in _text, _acc

# --------------------------------------------------------------------- synthetic calendars


def last_day(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def add_months(d: date, months: int) -> date:
    """The last day of the month ``months`` after ``d``'s month (``d`` is a month end)."""
    index = d.year * 12 + d.month - 1 + months
    return last_day(index // 12, index % 12 + 1)


def last_saturday(year: int, month: int) -> date:
    d = last_day(year, month)
    return d - timedelta(days=(d.weekday() - 5) % 7)


def build_calendar(kind: str, fye_month: int, first_year: int, years: int, label_offset: int,
                   long_quarter: int) -> tuple[dict[date, int], list[tuple[date, int, int]]]:
    """A synthetic filer: annual ends → labels, and each quarter's (end, label, quarter).

    ``kind`` is ``month`` (year ends on the last day of ``fye_month``, quarters on month ends)
    or ``week`` (52/53-week years ending on the last Saturday of ``fye_month``; quarters of
    13 weeks, the 53rd week added to quarter ``long_quarter`` of a 371-day year). A year's
    label is the calendar year it ends in plus ``label_offset``.
    """
    end_of = last_day if kind == "month" else last_saturday
    ends = [end_of(first_year + i, fye_month) for i in range(years + 1)]
    labels = {e: e.year + label_offset for e in ends}
    quarters = []
    for prior, end in zip(ends, ends[1:], strict=False):
        for n in (1, 2, 3):
            if kind == "month":
                q = add_months(prior, 3 * n)
            else:
                weeks = 13 * n + (1 if (end - prior).days == 371 and n >= long_quarter else 0)
                q = prior + timedelta(weeks=weeks)
            quarters.append((q, labels[end], n))
    return labels, quarters


# --------------------------------------------------------------------- synthetic instances

_HEAD = ('<?xml version="1.0" encoding="utf-8"?>\n<xbrl xmlns="http://www.xbrl.org/2003/instance"'
         ' xmlns:dei="http://xbrl.sec.gov/dei/2024" xmlns:us-gaap="http://fasb.org/us-gaap/2024"'
         ' xmlns:xbrldi="http://xbrl.org/2006/xbrldi">\n')


def context_xml(cid: str, start: date | None, end: date, dimensional: bool = False) -> str:
    seg = ('<segment><xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis">'
           'us-gaap:OtherMember</xbrldi:explicitMember></segment>') if dimensional else ""
    period = (f"<instant>{end}</instant>" if start is None
              else f"<startDate>{start}</startDate><endDate>{end}</endDate>")
    return (f'<context id="{cid}"><entity><identifier scheme="http://www.sec.gov/CIK">'
            f"0000900400</identifier>{seg}</entity><period>{period}</period></context>\n")


def instance_xml(contexts: list[str], dei_context: str | None, form: str, end: date) -> bytes:
    """An instance of the given contexts plus DEI cover facts on ``dei_context``."""
    dei = "" if dei_context is None else (
        f'<dei:DocumentType contextRef="{dei_context}">{form}</dei:DocumentType>\n'
        f'<dei:DocumentPeriodEndDate contextRef="{dei_context}">{end}'
        "</dei:DocumentPeriodEndDate>\n")
    return (_HEAD + "".join(contexts) + dei + "</xbrl>\n").encode("utf-8")


@pytest.fixture(scope="session")
def kit() -> SimpleNamespace:
    return SimpleNamespace(**{k: v for k, v in globals().items() if not k.startswith("_")})
