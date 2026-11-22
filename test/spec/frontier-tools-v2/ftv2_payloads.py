"""Hand-built SEC payloads for the frontier-tools-v2 spec tests (docs/specs/frontier-tools-v2.md).

The shapes are the SEC's published JSON shapes (as in test/spec/frontier-sec-tools/sec_payloads.py).
Every filer is synthetic:

- AAA (CIK 1) reports us-gaap:Revenues in USD, with a restated FY2022, two same-day FY2023
  filings, and one fact filed after the as-of date; and dei shares outstanding.
- BBB (CIK 2) reports us-gaap:Revenues in USD and EUR, sharing period ends with AAA, under its
  own label.
- CCC (CIK 3) reports us-gaap:Assets only, never Revenues.
- DDD (CIK 4) reports Revenues entries that carry their own ``cik``, ``taxonomy`` and ``tag``.
- HOLD (CIK 9) plays a withheld filer and reports Revenues.

The module has a distinct name so it never collides with another spec directory's helpers.
"""

from __future__ import annotations

import json
from typing import Any

MODEL = "gpt-5.6-sol"
AS_OF = "2024-06-30"

AAA_CIK = "0000000001"
BBB_CIK = "0000000002"
CCC_CIK = "0000000003"
DDD_CIK = "0000000004"
HOLD_CIK = "0000000009"
AAA_10K = "0000000001-24-000001"

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
AAA_DOCUMENT_URL = f"{ARCHIVES}/1/000000000124000001/aaa-2023.htm"


def submissions_url(cik: str) -> str:
    return f"https://data.sec.gov/submissions/CIK{cik}.json"


def facts_url(cik: str) -> str:
    return f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"


def fact(end: str, val: float, accn: str, filed: str, *, fy: int, form: str = "10-K",
         fp: str = "FY", start: str = "") -> dict[str, Any]:
    """One SEC companyfacts entry."""
    entry: dict[str, Any] = {"end": end, "val": val, "accn": accn, "fy": fy, "fp": fp,
                             "form": form, "filed": filed}
    if start:
        entry["start"] = start
    return entry


# AAA: us-gaap:Revenues, USD.
AAA_2022_ORIGINAL = fact("2022-12-31", 100, "0000000001-23-000001", "2023-02-01", fy=2022,
                         start="2022-01-01")
AAA_2022_RESTATED = fact("2022-12-31", 101, AAA_10K, "2024-02-01", fy=2023, start="2022-01-01")
AAA_2023 = fact("2023-12-31", 120, AAA_10K, "2024-02-01", fy=2023, start="2023-01-01")
# Filed the same day as AAA_2023 under a lower accession number.
AAA_2023_SAME_DAY = fact("2023-12-31", 119, "0000000001-24-000000", "2024-02-01", fy=2023,
                         start="2023-01-01")
# Filed after AS_OF: never shown.
AAA_2024_LATE = fact("2024-06-30", 150, "0000000001-24-000003", "2024-08-01", fy=2024,
                     start="2023-07-01")
AAA_REVENUES = [AAA_2022_ORIGINAL, AAA_2022_RESTATED, AAA_2023, AAA_2023_SAME_DAY, AAA_2024_LATE]
AAA_SHARES = fact("2024-01-15", 5000, AAA_10K, "2024-02-01", fy=2023)

# BBB: us-gaap:Revenues, USD and EUR.
BBB_2022 = fact("2022-12-31", 50, "0000000002-23-000001", "2023-03-01", fy=2022,
                start="2022-01-01")
BBB_2023_Q3 = fact("2023-09-30", 30, "0000000002-23-000005", "2023-11-01", fy=2023, form="10-Q",
                   fp="Q3", start="2023-07-01")
BBB_2023 = fact("2023-12-31", 40, "0000000002-24-000001", "2024-03-01", fy=2023,
                start="2023-01-01")
BBB_2021_EUR = fact("2021-12-31", 35, "0000000002-22-000001", "2022-03-01", fy=2021,
                    start="2021-01-01")

# HOLD: us-gaap:Revenues, USD.
HOLD_2023 = fact("2023-12-31", 999, "0000000009-24-000001", "2024-02-02", fy=2023,
                 start="2023-01-01")

# DDD: an entry that carries fields named like the added ones.
DDD_2023 = {**fact("2023-12-31", 7, "0000000004-24-000001", "2024-02-05", fy=2023,
                   start="2023-01-01"),
            "cik": 12345, "taxonomy": "not-the-taxonomy", "tag": "NotTheTag"}

REVENUES_LABEL = "Revenues"
REVENUES_DESCRIPTION = "Amount of revenue recognized."
BBB_LABEL = "Revenue from contracts (Beta)"
BBB_DESCRIPTION = "Beta's revenue."


def json_body(value: Any) -> bytes:
    return json.dumps(value).encode("utf-8")


def companyfacts(cik: int, name: str, facts: dict[str, Any]) -> dict[str, Any]:
    return {"cik": cik, "entityName": name, "facts": facts}


def revenues(units: dict[str, list[dict[str, Any]]], *, label: str = REVENUES_LABEL,
             description: str = REVENUES_DESCRIPTION) -> dict[str, Any]:
    return {"us-gaap": {"Revenues": {"label": label, "description": description,
                                     "units": units}}}


def html_doc(*paragraphs: str) -> str:
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return f'<html xmlns="http://www.w3.org/1999/xhtml"><head></head><body>{body}</body></html>'


def _columns(rows: list[tuple[str, str, str, str, str]]) -> dict[str, list[str]]:
    return {
        "accessionNumber": [r[0] for r in rows],
        "form": [r[1] for r in rows],
        "filingDate": [r[2] for r in rows],
        "reportDate": [r[3] for r in rows],
        "primaryDocument": [r[4] for r in rows],
    }


def sec_pages() -> dict[str, tuple[int, bytes]]:
    """The hand-built SEC: every URL the tests read, by exact URL."""
    tickers = {
        "0": {"cik_str": 1, "ticker": "AAA", "title": "Alpha Holdings"},
        "1": {"cik_str": 2, "ticker": "BBB", "title": "Beta Industries"},
        "2": {"cik_str": 3, "ticker": "CCC", "title": "Gamma Systems"},
        "3": {"cik_str": 4, "ticker": "DDD", "title": "Delta Labs"},
        "4": {"cik_str": 9, "ticker": "HOLD", "title": "Held Corp"},
    }
    aaa = companyfacts(1, "Alpha Holdings", {
        **revenues({"USD": AAA_REVENUES}),
        "dei": {"EntityCommonStockSharesOutstanding": {
            "label": "Shares outstanding", "description": "Cover page.",
            "units": {"shares": [AAA_SHARES]}}},
    })
    bbb = companyfacts(2, "Beta Industries", revenues(
        {"USD": [BBB_2022, BBB_2023_Q3, BBB_2023], "EUR": [BBB_2021_EUR]},
        label=BBB_LABEL, description=BBB_DESCRIPTION))
    ccc = companyfacts(3, "Gamma Systems", {"us-gaap": {"Assets": {
        "label": "Assets", "description": "Total assets.",
        "units": {"USD": [fact("2023-12-31", 900, "0000000003-24-000001", "2024-02-03",
                               fy=2023)]}}}})
    ddd = companyfacts(4, "Delta Labs", revenues({"USD": [DDD_2023]}))
    hold = companyfacts(9, "Held Corp", revenues({"USD": [HOLD_2023]}))
    submissions = {
        "cik": "1", "name": "Alpha Holdings", "tickers": ["AAA"],
        "filings": {"recent": _columns([
            (AAA_10K, "10-K", "2024-02-01", "2023-12-31", "aaa-2023.htm"),
        ]), "files": []},
    }
    return {
        TICKERS_URL: (200, json_body(tickers)),
        facts_url(AAA_CIK): (200, json_body(aaa)),
        facts_url(BBB_CIK): (200, json_body(bbb)),
        facts_url(CCC_CIK): (200, json_body(ccc)),
        facts_url(DDD_CIK): (200, json_body(ddd)),
        facts_url(HOLD_CIK): (200, json_body(hold)),
        submissions_url(AAA_CIK): (200, json_body(submissions)),
        AAA_DOCUMENT_URL: (200, html_doc("Alpha Holdings annual report.").encode("utf-8")),
    }
