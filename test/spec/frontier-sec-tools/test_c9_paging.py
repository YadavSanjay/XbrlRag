"""C9: paging, never silent truncation."""

from __future__ import annotations

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sec_payloads import AAA_10K, AAA_CIK, ARCHIVES, fact, facts_url, html_doc, json_body

REVENUES = {"taxonomy": "us-gaap", "tag": "Revenues"}
FRAME = {**REVENUES, "unit": "USD", "period": "CY2023"}


def _sec_with_facts(fake_sec, n: int):
    sec = fake_sec()
    entries = [fact(f"20{10 + i % 12}-12-{1 + i % 28:02d}", i, f"accn-{i:04d}", "2020-01-01",
                    fy=2020) for i in range(n)]
    document = {"cik": 1, "entityName": "Alpha Holdings", "facts": {"us-gaap": {
        "Revenues": {"label": "Revenues", "description": "d", "units": {"USD": entries}}}}}
    sec.pages[facts_url(AAA_CIK)] = (200, json_body(document))
    return sec


def _walk(toolbox, tool: str, args: dict, *, limit: int | None = None) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    for _ in range(10_000):
        call = {**args, "offset": offset}
        if limit is not None:
            call["limit"] = limit
        payload = toolbox.execute(tool, call).payload
        rows.extend(payload["rows"])
        if payload["next_offset"] is None:
            return rows
        assert payload["next_offset"] > offset
        offset = payload["next_offset"]
    raise AssertionError("paging did not end")


@pytest.mark.criterion("C9")
@pytest.mark.parametrize(
    ("tool", "args", "total"),
    [
        ("list_filings", {"cik": "1"}, 3),
        ("list_concepts", {"cik": "1"}, 2),
        ("get_concept", {"cik": "1", **REVENUES}, 3),
        ("get_frame", FRAME, 4),
    ],
)
def test_the_envelope(make_toolbox, tool, args, total):
    # oracle: requirement 14 — counted by hand on the fixtures
    toolbox, _ = make_toolbox()
    first = toolbox.execute(tool, {**args, "limit": 1}).payload
    assert first["total"] == total
    assert first["offset"] == 0
    assert len(first["rows"]) == 1
    assert first["next_offset"] == 1
    assert first["truncated"] is True
    last = toolbox.execute(tool, {**args, "limit": 1, "offset": total - 1}).payload
    assert last["next_offset"] is None
    assert last["truncated"] is False


@pytest.mark.criterion("C9")
def test_limit_has_a_ceiling(make_toolbox, fake_sec):
    # oracle: requirement 14 — at most 1000. Re-lock: 1000 rows exceed the default 40,000
    # characters, so the character cap is raised here to test the row ceiling alone.
    toolbox, _ = make_toolbox(_sec_with_facts(fake_sec, 1200), max_result_chars=10**7)
    payload = toolbox.execute("get_concept", {"cik": "1", **REVENUES, "limit": 5000}).payload
    assert len(payload["rows"]) == 1000
    assert payload["next_offset"] == 1000


@pytest.mark.criterion("C9")
def test_a_long_filing_is_read_in_pages(make_toolbox, fake_sec):
    # oracle: requirement 14 — the default page, then the rest from next_offset
    sec = fake_sec()
    line = "x" * 60000
    sec.pages[f"{ARCHIVES}/1/000000000124000001/aaa-2023.htm"] = (
        200, html_doc(line).encode("utf-8"))
    toolbox, _ = make_toolbox(sec)
    args = {"cik": "1", "accession": AAA_10K}
    first = toolbox.execute("read_filing", args).payload
    assert len(first["text"]) == 20000
    assert first["next_offset"] == 20000
    assert first["total_chars"] == 60000
    assert toolbox.execute("read_filing", {**args, "offset": 59990}).payload["text"] == "x" * 10


@pytest.mark.criterion("C9")
def test_a_position_starts_the_match_in_read_filing(make_toolbox):
    # oracle: requirement 12 — position is an offset read_filing accepts
    toolbox, _ = make_toolbox()
    args = {"cik": "1", "accession": AAA_10K}
    row = toolbox.execute("search_filing", {**args, "query": "backlog"}).payload["rows"][0]
    text = toolbox.execute("read_filing", {**args, "offset": row["position"]}).payload["text"]
    assert text.lower().startswith("backlog")


@pytest.mark.criterion("C9")
@settings(max_examples=25)
@given(n=st.integers(1, 120), cap=st.integers(1500, 6000))
def test_walking_gives_every_row_once_within_the_cap(make_toolbox, fake_sec, n, cap):
    # oracle: requirement 14 — the rows are numbered 0..n-1 by construction
    toolbox, _ = make_toolbox(_sec_with_facts(fake_sec, n), max_result_chars=cap)
    args = {"cik": "1", **REVENUES}
    assert sorted(r["val"] for r in _walk(toolbox, "get_concept", args)) == list(range(n))
    assert len(json.dumps(toolbox.execute("get_concept", args).payload)) <= cap


@pytest.mark.criterion("C9")
@settings(max_examples=25)
@given(lines=st.lists(st.text(alphabet='ab"\\é', min_size=1, max_size=300), min_size=1,
                      max_size=30),
       cap=st.integers(1000, 4000))
def test_walking_a_filing_gives_every_character_once_within_the_cap(
    make_toolbox, fake_sec, lines, cap
):
    # oracle: requirement 14 — quotes and backslashes grow under JSON, and the cap still holds
    sec = fake_sec()
    escaped = [line.replace("&", "&amp;") for line in lines]
    sec.pages[f"{ARCHIVES}/1/000000000124000001/aaa-2023.htm"] = (
        200, html_doc(*escaped).encode("utf-8"))
    toolbox, _ = make_toolbox(sec, max_result_chars=cap)
    args = {"cik": "1", "accession": AAA_10K}
    whole = "\n".join(lines)
    text, offset = "", 0
    for _ in range(10_000):
        payload = toolbox.execute("read_filing", {**args, "offset": offset}).payload
        assert len(json.dumps(payload, ensure_ascii=False)) <= cap
        text += payload["text"]
        if payload["next_offset"] is None:
            break
        assert payload["next_offset"] > offset
        offset = payload["next_offset"]
    assert text == whole
