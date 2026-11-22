"""Hand-built SEC payloads for the frontier-sec-tools spec tests, in the SEC's published JSON
shapes. AAA, BBB, CCC and HOLD are synthetic; HOLD plays a held-out filer.
"""

from __future__ import annotations

import json
from typing import Any

MODEL = "gpt-5.6-sol"
AS_OF = "2024-06-30"
IDENTITY = "Spec Tester spec@example.com"

AAA_CIK = "0000000001"
BBB_CIK = "0000000002"
CCC_CIK = "0000000003"
HOLD_CIK = "0000000009"
AAA_10K = "0000000001-24-000001"
AAA_10Q = "0000000001-24-000002"
AAA_LATE = "0000000001-24-000003"
AAA_OLD = "0000000001-19-000001"
HOLD_10K = "0000000009-24-000001"

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FTS_URL = "https://efts.sec.gov/LATEST/search-index"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"


def submissions_url(cik: str) -> str:
    return f"https://data.sec.gov/submissions/CIK{cik}.json"


def facts_url(cik: str) -> str:
    return f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"


def frame_url(tag: str, unit: str, period: str) -> str:
    return f"https://data.sec.gov/api/xbrl/frames/us-gaap/{tag}/{unit}/{period}.json"


def _columns(rows: list[tuple[str, str, str, str, str]]) -> dict[str, list[str]]:
    return {
        "accessionNumber": [r[0] for r in rows],
        "form": [r[1] for r in rows],
        "filingDate": [r[2] for r in rows],
        "reportDate": [r[3] for r in rows],
        "primaryDocument": [r[4] for r in rows],
    }


def fact(end: str, val: float, accn: str, filed: str, *, fy: int, start: str = "") -> dict:
    entry = {"end": end, "val": val, "accn": accn, "fy": fy, "fp": "FY", "form": "10-K",
             "filed": filed}
    if start:
        entry["start"] = start
    return entry


REVENUE_2022_ORIGINAL = fact("2022-12-31", 100, "0000000001-23-000001", "2023-02-01",
                             fy=2022, start="2022-01-01")
REVENUE_2022_RESTATED = fact("2022-12-31", 101, AAA_10K, "2024-02-01", fy=2023,
                             start="2022-01-01")
REVENUE_2023 = fact("2023-12-31", 120, AAA_10K, "2024-02-01", fy=2023, start="2023-01-01")
REVENUE_2024_LATE = fact("2024-06-30", 150, AAA_LATE, "2024-08-01", fy=2024,
                         start="2023-07-01")


def html_doc(*paragraphs: str) -> str:
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return f'<html xmlns="http://www.w3.org/1999/xhtml"><head></head><body>{body}</body></html>'


AAA_FILING_LINES = (
    "Alpha Holdings annual report.",
    "Backlog was 42 units at year end.",
    "Revenue rose because of pricing.",
)


def json_body(value: Any) -> bytes:
    return json.dumps(value).encode("utf-8")


def sec_pages() -> dict[str, tuple[int, bytes]]:
    """The hand-built SEC: every URL the tools read, by exact URL."""
    tickers = {
        "0": {"cik_str": 1, "ticker": "AAA", "title": "Alpha Holdings"},
        "1": {"cik_str": 2, "ticker": "BBB", "title": "Beta Industries"},
        "2": {"cik_str": 3, "ticker": "CCC", "title": "Gamma Systems"},
        "3": {"cik_str": 9, "ticker": "HOLD", "title": "Held Corp"},
    }
    submissions = {
        "cik": "1",
        "name": "Alpha Holdings",
        "tickers": ["AAA"],
        "filings": {
            "recent": _columns([
                (AAA_LATE, "10-K", "2024-08-01", "2024-06-30", "aaa-late.htm"),
                (AAA_10Q, "10-Q", "2024-05-01", "2024-03-31", "aaa-q1.htm"),
                (AAA_10K, "10-K", "2024-02-01", "2023-12-31", "aaa-2023.htm"),
            ]),
            "files": [{"name": "CIK0000000001-submissions-001.json", "filingCount": 1}],
        },
    }
    older = _columns([(AAA_OLD, "10-K", "2019-02-01", "2018-12-31", "aaa-2018.htm")])
    companyfacts = {
        "cik": 1,
        "entityName": "Alpha Holdings",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "label": "Revenues",
                    "description": "Amount of revenue recognized.",
                    "units": {"USD": [REVENUE_2022_ORIGINAL, REVENUE_2022_RESTATED, REVENUE_2023,
                                      REVENUE_2024_LATE]},
                },
                "Backlog": {
                    "label": "Order backlog",
                    "description": "Late concept.",
                    "units": {"USD": [REVENUE_2024_LATE]},
                },
            },
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "label": "Shares outstanding",
                    "description": "Cover page.",
                    "units": {"shares": [fact("2024-01-15", 5000, AAA_10K, "2024-02-01", fy=2023)]},
                },
            },
        },
    }
    frame = {
        "taxonomy": "us-gaap", "tag": "Revenues", "ccp": "CY2023", "uom": "USD",
        "label": "Revenues", "description": "Amount of revenue recognized.", "pts": 4,
        "data": [
            {"accn": AAA_10K, "cik": 1, "entityName": "Alpha Holdings", "loc": "US-NY",
             "start": "2023-01-01", "end": "2023-12-31", "val": 120},
            {"accn": HOLD_10K, "cik": 9, "entityName": "Held Corp", "loc": "US-CA",
             "start": "2023-01-01", "end": "2023-12-31", "val": 999},
            {"accn": "0000000003-24-000001", "cik": 3, "entityName": "Gamma Systems",
             "loc": "US-TX", "start": "2023-01-01", "end": "2023-12-31", "val": 80},
            {"accn": "0000000002-24-000001", "cik": 2, "entityName": "Beta Industries",
             "loc": "US-WA", "start": "2023-01-01", "end": "2023-12-31", "val": 300},
        ],
    }
    folder = f"{ARCHIVES}/1/000000000124000001"
    index = {"directory": {"name": "000000000124000001", "item": [
        {"name": "aaa-2023.htm", "size": "2048", "last-modified": "2024-02-01 16:00:00"},
        {"name": "ex21.htm", "size": "512", "last-modified": "2024-02-01 16:00:00"},
    ]}}
    late_folder = f"{ARCHIVES}/1/000000000124000003"
    return {
        TICKERS_URL: (200, json_body(tickers)),
        submissions_url(AAA_CIK): (200, json_body(submissions)),
        "https://data.sec.gov/submissions/CIK0000000001-submissions-001.json":
            (200, json_body(older)),
        facts_url(AAA_CIK): (200, json_body(companyfacts)),
        frame_url("Revenues", "USD", "CY2023"): (200, json_body(frame)),
        f"{folder}/index.json": (200, json_body(index)),
        f"{folder}/aaa-2023.htm": (200, html_doc(*AAA_FILING_LINES).encode("utf-8")),
        f"{folder}/ex21.htm": (200, html_doc("Subsidiaries of Alpha Holdings.").encode("utf-8")),
        f"{late_folder}/aaa-late.htm": (200, html_doc("Filed after the date.").encode("utf-8")),
        f"{late_folder}/index.json": (200, json_body({"directory": {"item": []}})),
    }


def fts_body() -> bytes:
    def hit(accession: str, document: str, filed: str, cik: str, name: str) -> dict:
        return {
            "_id": f"{accession}:{document}",
            "_source": {
                "ciks": [cik], "display_names": [name], "file_date": filed, "form": "10-K",
                "adsh": accession, "period_ending": "2023-12-31", "root_forms": ["10-K"],
            },
        }

    return json_body({"hits": {"total": {"value": 3, "relation": "eq"}, "hits": [
        hit(AAA_10K, "aaa-2023.htm", "2024-02-01", AAA_CIK, "Alpha Holdings  (AAA)"),
        hit(HOLD_10K, "hold-2023.htm", "2024-02-02", HOLD_CIK, "Held Corp  (HOLD)"),
        hit(AAA_LATE, "aaa-late.htm", "2024-08-01", AAA_CIK, "Alpha Holdings  (AAA)"),
    ]}})
