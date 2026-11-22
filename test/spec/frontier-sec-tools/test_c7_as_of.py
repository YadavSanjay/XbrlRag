"""C7: every tool shows the world as of the run's date, where the SEC gives a date."""

from __future__ import annotations

from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sec_payloads import AAA_10K, AAA_LATE, FTS_URL, REVENUE_2023, REVENUE_2024_LATE


@pytest.mark.criterion("C7")
def test_get_concept_drops_later_facts(make_toolbox):
    # oracle: requirement 11 — the hand-built fact filed 2024-08-01 is after 2024-06-30;
    # re-spec (frontier-tools-v2 requirement 3): newest first
    toolbox, _ = make_toolbox()
    rows = toolbox.execute(
        "get_concept", {"cik": "1", "taxonomy": "us-gaap", "tag": "Revenues"}
    ).payload["rows"]
    assert [r["val"] for r in rows] == [120, 101, 100]
    assert REVENUE_2024_LATE["val"] not in [r["val"] for r in rows]


@pytest.mark.criterion("C7")
def test_list_concepts_drops_concepts_with_only_later_facts(make_toolbox):
    # oracle: requirement 11 — Backlog's one fact is after the date
    toolbox, _ = make_toolbox()
    tags = [r["tag"] for r in toolbox.execute("list_concepts", {"cik": "1"}).payload["rows"]]
    assert "Backlog" not in tags
    assert "Revenues" in tags


@pytest.mark.criterion("C7")
def test_list_filings_drops_later_filings(make_toolbox):
    # oracle: requirement 11
    toolbox, _ = make_toolbox()
    accessions = [r["accession"] for r in
                  toolbox.execute("list_filings", {"cik": "1"}).payload["rows"]]
    assert AAA_LATE not in accessions
    assert AAA_10K in accessions


@pytest.mark.criterion("C7")
def test_full_text_search_stops_at_the_date(make_toolbox):
    # oracle: requirement 11 — the end date is at most as_of, and a later hit is dropped anyway
    toolbox, sec = make_toolbox()
    payload = toolbox.execute(
        "search_filings_text", {"query": "backlog", "filed_to": "2030-01-01"}
    ).payload
    assert AAA_LATE not in [r["accession"] for r in payload["rows"]]
    query = parse_qs(urlparse(sec.urls[-1]).query)
    assert sec.urls[-1].startswith(FTS_URL)
    assert query["enddt"] == ["2024-06-30"]


@pytest.mark.criterion("C7")
@pytest.mark.parametrize(
    ("tool", "extra"),
    [("read_filing", {}), ("search_filing", {"query": "x"}), ("list_filing_documents", {})],
)
def test_a_later_filing_cannot_be_read(make_toolbox, tool, extra):
    # oracle: requirement 11 — a filing after the date does not exist yet
    toolbox, sec = make_toolbox()
    outcome = toolbox.execute(tool, {"cik": "1", "accession": AAA_LATE, **extra})
    assert outcome.error_code == "not_yet_filed"
    assert "Filed after the date" not in str(outcome.payload)
    assert not any("000000000124000003" in url for url in sec.urls)


@pytest.mark.criterion("C7")
def test_a_filing_before_the_date_can_be_read(make_toolbox):
    # oracle: requirement 11
    toolbox, _ = make_toolbox()
    outcome = toolbox.execute("read_filing", {"cik": "1", "accession": AAA_10K})
    assert outcome.error_code is None
    assert "Backlog was 42 units" in outcome.payload["text"]


@pytest.mark.criterion("C7")
def test_a_frame_says_it_was_not_filtered(make_toolbox):
    # oracle: requirement 11 — the frames API gives no filing date
    toolbox, _ = make_toolbox()
    payload = toolbox.execute(
        "get_frame", {"taxonomy": "us-gaap", "tag": "Revenues", "unit": "USD", "period": "CY2023"}
    ).payload
    assert payload["as_of_filtered"] is False
    assert payload["rows"]


@pytest.mark.criterion("C7")
def test_the_later_date_shows_the_later_fact(make_toolbox):
    # oracle: requirement 11 — the same SEC, a date after the filing; re-spec
    # (frontier-tools-v2 requirement 3): newest first
    toolbox, _ = make_toolbox(as_of="2024-12-31")
    rows = toolbox.execute(
        "get_concept", {"cik": "1", "taxonomy": "us-gaap", "tag": "Revenues"}
    ).payload["rows"]
    assert [r["val"] for r in rows] == [REVENUE_2024_LATE["val"], REVENUE_2023["val"], 101, 100]


_DATES = st.dates(min_value=date(2020, 1, 1), max_value=date(2030, 1, 1))


@pytest.mark.criterion("C7")
@given(early=_DATES, late=_DATES)
def test_a_later_date_never_removes_a_row(make_toolbox, early, late):
    # oracle: requirement 11 — the view grows with the date
    first, second = sorted([early, late])

    def rows(as_of: str) -> list[int]:
        toolbox, _ = make_toolbox(as_of=as_of)
        payload = toolbox.execute(
            "get_concept", {"cik": "1", "taxonomy": "us-gaap", "tag": "Revenues"}
        ).payload
        return [r["val"] for r in payload.get("rows", [])]

    assert set(rows(first.isoformat())) <= set(rows(second.isoformat()))
    # re-spec (frontier-tools-v2 requirement 3): newest first
    assert rows("2030-01-01") == [150, 120, 101, 100]
