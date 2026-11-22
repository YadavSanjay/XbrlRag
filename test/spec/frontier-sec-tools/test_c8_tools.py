"""C8: the eleven tools, each on hand-built SEC payloads."""

from __future__ import annotations

import hashlib
import json
from urllib.parse import parse_qs, urlparse

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sec_payloads import (
    AAA_10K,
    AAA_10Q,
    AAA_CIK,
    AAA_FILING_LINES,
    AAA_OLD,
    ARCHIVES,
    HOLD_CIK,
    REVENUE_2022_ORIGINAL,
    REVENUE_2022_RESTATED,
    REVENUE_2023,
    facts_url,
    submissions_url,
)

from src.eval.sec_tools.toolbox import calculate, tool_specs, tools_fingerprint

NAMES = [
    "find_company",
    "list_filings",
    "list_concepts",
    "get_concept",
    "get_frame",
    "search_filings_text",
    "list_filing_documents",
    "read_filing",
    "search_filing",
    "calculate",
    "analyze_rows",
]
REVENUES = {"taxonomy": "us-gaap", "tag": "Revenues"}
FRAME = {**REVENUES, "unit": "USD", "period": "CY2023"}


@pytest.mark.criterion("C8")
def test_the_set_is_the_eleven_tools_in_order():
    # oracle: requirement 12's table
    specs = tool_specs()
    assert [s.name for s in specs] == NAMES
    for spec in specs:
        assert spec.description.strip()
        assert spec.parameters["type"] == "object"
        json.dumps(spec.parameters)


@pytest.mark.criterion("C8")
def test_find_company_by_ticker_first(make_toolbox):
    # oracle: requirement 12 — hand-built tickers file; the CIK comes back padded
    toolbox, _ = make_toolbox()
    payload = toolbox.execute("find_company", {"query": "aaa"}).payload
    assert payload["companies"][0] == {"cik": AAA_CIK, "ticker": "AAA", "name": "Alpha Holdings"}


@pytest.mark.criterion("C8")
def test_find_company_by_name(make_toolbox):
    # oracle: requirement 12 — names containing the query, case-insensitive
    toolbox, _ = make_toolbox()
    payload = toolbox.execute("find_company", {"query": "INDUSTRIES"}).payload
    assert [c["ticker"] for c in payload["companies"]] == ["BBB"]


@pytest.mark.criterion("C8")
def test_find_company_by_a_list_of_tickers(make_toolbox):
    # oracle: requirement 12 — a universe resolves in one call
    toolbox, _ = make_toolbox()
    payload = toolbox.execute("find_company", {"tickers": ["BBB", "aaa", "ZZZ"]}).payload
    assert sorted(c["ticker"] for c in payload["companies"]) == ["AAA", "BBB"]
    assert payload["not_found"] == ["ZZZ"]


@pytest.mark.criterion("C8")
def test_list_filings_reads_older_pages(make_toolbox):
    # oracle: requirement 12 — a filer's 10-K can sit in an older submissions file; newest first
    toolbox, _ = make_toolbox()
    outcome = toolbox.execute("list_filings", {"cik": "1", "forms": ["10-K"]})
    assert outcome.payload["rows"] == [
        {"accession": AAA_10K, "form": "10-K", "filing_date": "2024-02-01",
         "report_date": "2023-12-31", "primary_document": "aaa-2023.htm"},
        {"accession": AAA_OLD, "form": "10-K", "filing_date": "2019-02-01",
         "report_date": "2018-12-31", "primary_document": "aaa-2018.htm"},
    ]
    assert submissions_url(AAA_CIK) in outcome.urls


@pytest.mark.criterion("C8")
def test_list_filings_by_date(make_toolbox):
    # oracle: requirement 12
    toolbox, _ = make_toolbox()
    rows = toolbox.execute(
        "list_filings", {"cik": "1", "filed_from": "2024-03-01", "filed_to": "2024-06-01"}
    ).payload["rows"]
    assert [r["accession"] for r in rows] == [AAA_10Q]


@pytest.mark.criterion("C8")
def test_list_concepts(make_toolbox):
    # oracle: requirement 12 — counted by hand: three Revenues facts by the date
    toolbox, _ = make_toolbox()
    outcome = toolbox.execute("list_concepts", {"cik": "1", "query": "revenue"})
    assert outcome.payload["rows"] == [{
        "taxonomy": "us-gaap", "tag": "Revenues", "label": "Revenues", "units": ["USD"],
        "first_end": "2022-12-31", "last_end": "2023-12-31", "facts": 3,
    }]
    assert outcome.urls == [facts_url(AAA_CIK)]


@pytest.mark.criterion("C8")
def test_get_concept_keeps_the_secs_rows_and_restatements(make_toolbox):
    # oracle: requirement 12 — rows unchanged, with unit; no deduplication, so the restated
    # FY2022 stays beside the original. Re-spec (frontier-tools-v2 requirements 1 and 3): each
    # row also names its cik, taxonomy and tag, and rows run newest first (end, then filed,
    # descending)
    toolbox, _ = make_toolbox()
    payload = toolbox.execute("get_concept", {"cik": "1", **REVENUES}).payload
    assert payload["label"] == "Revenues"
    assert payload["description"] == "Amount of revenue recognized."
    assert payload["units"] == ["USD"]
    named = {"unit": "USD", "cik": AAA_CIK, **REVENUES}
    assert payload["rows"] == [
        {**REVENUE_2023, **named},
        {**REVENUE_2022_RESTATED, **named},
        {**REVENUE_2022_ORIGINAL, **named},
    ]


@pytest.mark.criterion("C8")
@pytest.mark.parametrize(
    ("extra", "values"),
    [
        ({"fiscal_year": 2022}, [100]),
        ({"form": "10-Q"}, []),
        ({"end_from": "2023-01-01"}, [120]),
        ({"end_to": "2022-12-31"}, [101, 100]),
        ({"unit": "USD"}, [120, 101, 100]),
        ({"unit": "EUR"}, []),
    ],
)
def test_get_concept_filters(make_toolbox, extra, values):
    # oracle: requirement 12 — read off the hand-built facts; re-spec (frontier-tools-v2
    # requirement 3): newest first
    toolbox, _ = make_toolbox()
    outcome = toolbox.execute("get_concept", {"cik": "1", **REVENUES, **extra})
    assert outcome.error_code is None
    assert [r["val"] for r in outcome.payload["rows"]] == values


@pytest.mark.criterion("C8")
def test_get_frame_sorts_and_filters(make_toolbox):
    # oracle: requirement 12 — descending by default, rows unchanged
    toolbox, _ = make_toolbox()
    payload = toolbox.execute("get_frame", FRAME).payload
    assert payload["label"] == "Revenues"
    assert [r["val"] for r in payload["rows"]] == [999, 300, 120, 80]
    assert payload["rows"][1]["entityName"] == "Beta Industries"
    assert payload["rows"][1]["accn"] == "0000000002-24-000001"
    ascending = toolbox.execute("get_frame", {**FRAME, "order": "asc"}).payload
    assert [r["val"] for r in ascending["rows"]] == [80, 120, 300, 999]
    chosen = toolbox.execute("get_frame", {**FRAME, "ciks": ["1", "0000000003"]}).payload
    assert [r["val"] for r in chosen["rows"]] == [120, 80]
    assert chosen["total"] == 2


@pytest.mark.criterion("C8")
def test_a_per_share_unit_takes_the_frames_form(make_toolbox):
    # oracle: requirement 12 and the SEC frames API, checked live on 2026-11-02:
    # .../EarningsPerShareBasic/USD-per-shares/CY2023.json answers, .../USD%2Fshares/... is 404
    toolbox, sec = make_toolbox()
    frame = {"taxonomy": "us-gaap", "tag": "EarningsPerShareBasic", "ccp": "CY2023",
             "uom": "USD/shares", "label": "EPS, basic", "description": "", "pts": 1,
             "data": [{"accn": AAA_10K, "cik": 1, "entityName": "Alpha Holdings", "loc": "US-NY",
                       "start": "2023-01-01", "end": "2023-12-31", "val": 1.5}]}
    url = ("https://data.sec.gov/api/xbrl/frames/us-gaap/EarningsPerShareBasic/USD-per-shares/"
           "CY2023.json")
    sec.pages[url] = (200, json.dumps(frame).encode("utf-8"))
    payload = toolbox.execute("get_frame", {"taxonomy": "us-gaap", "tag": "EarningsPerShareBasic",
                                            "unit": "USD/shares", "period": "CY2023"}).payload
    assert sec.urls[-1] == url
    assert [r["val"] for r in payload["rows"]] == [1.5]


@pytest.mark.criterion("C8")
def test_search_filings_text(make_toolbox):
    # oracle: requirement 12 and the EDGAR full-text search query parameters
    toolbox, sec = make_toolbox()
    payload = toolbox.execute(
        "search_filings_text",
        {"query": '"order backlog"', "forms": ["10-K"], "ciks": ["1"],
         "filed_from": "2024-01-01", "page": 2},
    ).payload
    query = parse_qs(urlparse(sec.urls[-1]).query)
    assert query["q"] == ['"order backlog"']
    assert query["forms"] == ["10-K"]
    assert query["ciks"] == [AAA_CIK]
    assert query["dateRange"] == ["custom"]
    assert query["startdt"] == ["2024-01-01"]
    assert query["from"] == ["100"]
    assert payload["total"] == 3
    assert payload["rows"][0] == {
        "accession": AAA_10K, "form": "10-K", "filing_date": "2024-02-01",
        "period_ending": "2023-12-31", "ciks": [AAA_CIK],
        "companies": ["Alpha Holdings  (AAA)"], "document": "aaa-2023.htm",
    }


@pytest.mark.criterion("C8")
def test_list_filing_documents(make_toolbox):
    # oracle: requirement 12
    toolbox, _ = make_toolbox()
    outcome = toolbox.execute("list_filing_documents", {"cik": "1", "accession": AAA_10K})
    assert outcome.payload["rows"] == [
        {"name": "aaa-2023.htm", "size": "2048"}, {"name": "ex21.htm", "size": "512"}]


@pytest.mark.criterion("C8")
def test_read_filing_defaults_to_the_primary_document(make_toolbox):
    # oracle: requirements 12, 13 — filing_text gives one paragraph per line
    toolbox, _ = make_toolbox()
    outcome = toolbox.execute("read_filing", {"cik": "0000000001", "accession": AAA_10K})
    payload = outcome.payload
    text = "\n".join(AAA_FILING_LINES)
    assert payload["text"] == text
    assert payload["total_chars"] == len(text)
    assert payload["offset"] == 0
    assert payload["next_offset"] is None
    assert payload["document"] == "aaa-2023.htm"
    assert payload["form"] == "10-K"
    assert payload["filing_date"] == "2024-02-01"
    assert f"{ARCHIVES}/1/000000000124000001/aaa-2023.htm" in outcome.urls


@pytest.mark.criterion("C8")
def test_read_filing_takes_a_document_and_an_undashed_accession(make_toolbox):
    # oracle: requirements 12, 17
    toolbox, _ = make_toolbox()
    payload = toolbox.execute(
        "read_filing", {"cik": "1", "accession": "000000000124000001", "document": "ex21.htm"}
    ).payload
    assert payload["text"] == "Subsidiaries of Alpha Holdings."
    assert payload["document"] == "ex21.htm"


@pytest.mark.criterion("C8")
def test_search_filing_is_a_literal_case_insensitive_search(make_toolbox):
    # oracle: requirement 12 — one hand-placed match; a regex character is literal
    toolbox, _ = make_toolbox()
    args = {"cik": "1", "accession": AAA_10K}
    payload = toolbox.execute("search_filing", {**args, "query": "BACKLOG"}).payload
    assert payload["total"] == 1
    assert "42 units" in payload["rows"][0]["snippet"]
    assert toolbox.execute("search_filing", {**args, "query": "Back.og"}).payload["total"] == 0


@pytest.mark.criterion("C8")
def test_the_calculator_tool(make_toolbox):
    # oracle: (120 - 100) / 100 by hand; the tool reads nothing
    toolbox, sec = make_toolbox()
    outcome = toolbox.execute("calculate", {"expression": "(120 - 100) / 100"})
    # tolerance: one float division
    assert outcome.payload["value"] == pytest.approx(0.2, rel=1e-12)
    assert outcome.urls == []
    assert sec.calls == []
    assert calculate("1 + 1") == pytest.approx(2.0, abs=1e-12)


@pytest.mark.criterion("C8")
def test_a_missing_filer_is_not_found(make_toolbox):
    # oracle: requirement 16 — a 404 on the hand-built SEC
    toolbox, _ = make_toolbox()
    outcome = toolbox.execute("list_filings", {"cik": HOLD_CIK})
    assert outcome.error_code == "not_found"
    assert outcome.payload["error"] == "not_found"


@pytest.mark.criterion("C8")
@given(st.sampled_from(NAMES))
def test_the_fingerprint_is_the_hash_of_the_definitions(name):
    # oracle: requirement 12 — computed here from the definitions
    specs = tool_specs()
    definitions = [
        {"name": s.name, "description": s.description, "parameters": s.parameters} for s in specs
    ]
    expected = hashlib.sha256(json.dumps(definitions, sort_keys=True).encode("utf-8")).hexdigest()
    assert tools_fingerprint() == expected
    assert name in [s.name for s in specs]
