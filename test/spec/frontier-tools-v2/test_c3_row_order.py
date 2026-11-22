"""C3: get_concept rows come newest first, on every page."""

from __future__ import annotations

import json

import pytest
from ftv2_payloads import AAA_CIK, BBB_CIK, companyfacts, fact, facts_url, json_body, revenues
from hypothesis import example, given, settings
from hypothesis import strategies as st

REVENUES = {"taxonomy": "us-gaap", "tag": "Revenues"}
# AAA and BBB's known Revenues rows, written out by hand in requirement 3's order: end
# descending, then cik ascending, then filed descending, then accn descending.
EXPECTED = [
    ("2023-12-31", AAA_CIK, 120),  # filed 2024-02-01, accn ...-24-000001
    ("2023-12-31", AAA_CIK, 119),  # filed 2024-02-01, accn ...-24-000000
    ("2023-12-31", BBB_CIK, 40),
    ("2023-09-30", BBB_CIK, 30),
    ("2022-12-31", AAA_CIK, 101),  # filed 2024-02-01
    ("2022-12-31", AAA_CIK, 100),  # filed 2023-02-01
    ("2022-12-31", BBB_CIK, 50),
    ("2021-12-31", BBB_CIK, 35),
]


def _walk(toolbox, arguments: dict, limit: int) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    for _ in range(1000):
        outcome = toolbox.execute("get_concept", {**arguments, "offset": offset, "limit": limit})
        assert outcome.error_code is None, outcome.payload
        rows.extend(outcome.payload["rows"])
        if outcome.payload["next_offset"] is None:
            return rows
        assert outcome.payload["next_offset"] > offset
        offset = outcome.payload["next_offset"]
    raise AssertionError("paging did not end")


def _text(value) -> str:
    return "" if value is None else str(value)


def _in_order(first: dict, second: dict) -> bool:
    """Whether *first* may precede *second* under requirement 3's key; missing is ""."""
    pairs = [
        (_text(second.get("end")), _text(first.get("end"))),       # descending
        (_text(first.get("cik")), _text(second.get("cik"))),       # ascending
        (_text(second.get("filed")), _text(first.get("filed"))),   # descending
        (_text(second.get("accn")), _text(first.get("accn"))),     # descending
    ]
    for low, high in pairs:
        if low != high:
            return low < high
    return True


@pytest.mark.criterion("C3")
def test_two_filers_newest_first(make_toolbox):
    # oracle: EXPECTED, written by hand from the fixture
    toolbox, _ = make_toolbox()
    outcome = toolbox.execute("get_concept", {"ciks": ["2", "1"], **REVENUES})
    assert outcome.error_code is None, outcome.payload
    assert [(r["end"], r["cik"], r["val"]) for r in outcome.payload["rows"]] == EXPECTED


@pytest.mark.criterion("C3")
def test_one_filer_newest_first(make_toolbox):
    # oracle: AAA's four known rows by hand: 2023 (same day, higher accn first), then the
    # 2022 restatement before the original
    toolbox, _ = make_toolbox()
    outcome = toolbox.execute("get_concept", {"cik": "1", **REVENUES})
    assert outcome.error_code is None
    assert [r["val"] for r in outcome.payload["rows"]] == [120, 119, 101, 100]


@pytest.mark.criterion("C3")
def test_every_page_follows_the_order(make_toolbox):
    # oracle: EXPECTED, cut into pages of three by hand
    toolbox, _ = make_toolbox()
    pages = []
    for offset in (0, 3, 6):
        outcome = toolbox.execute("get_concept", {"ciks": ["1", "2"], **REVENUES,
                                                  "offset": offset, "limit": 3})
        assert outcome.error_code is None, outcome.payload
        pages.append([r["val"] for r in outcome.payload["rows"]])
    assert pages == [[120, 119, 40], [30, 101, 100], [50, 35]]


_ENDS = st.sampled_from(["2021-12-31", "2022-12-31", "2023-12-31"])
_FILED = st.one_of(st.none(), st.sampled_from(["2022-03-01", "2023-03-01", "2024-03-01"]))
_ACCN = st.one_of(st.none(), st.sampled_from(["a-1", "a-2", "a-3"]))


def _sec_with(fake_sec, entries_by_cik: dict[str, list[dict]]):
    sec = fake_sec()
    for cik, entries in entries_by_cik.items():
        sec.pages[facts_url(cik)] = (200, json_body(
            companyfacts(int(cik), f"Filer {cik}", revenues({"USD": entries}))))
    return sec


def _entry(end: str, filed: str | None, accn: str | None, val: int) -> dict:
    entry = fact(end, val, accn or "", filed or "", fy=2023)
    for key, value in (("filed", filed), ("accn", accn)):
        if value is None:
            del entry[key]
    return entry


_FACTS = st.lists(st.tuples(_ENDS, _FILED, _ACCN), min_size=1, max_size=25)


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@settings(max_examples=40)
@given(first=_FACTS, second=_FACTS, limit=st.integers(1, 7))
@example(first=[("2021-12-31", "2022-03-01", "a-1"), ("2023-12-31", "2024-03-01", "a-3")],
         second=[("2022-12-31", "2023-03-01", "a-2")], limit=1)
def test_pages_concatenate_to_the_sorted_unpaged_list(make_toolbox, fake_sec, first, second,
                                                      limit):
    # oracle: metamorphic — walking next_offset gives the unpaged list, which is sorted under
    # requirement 3's key (a missing filed or accn sorts as ""); vals number the facts
    entries = {AAA_CIK: [], BBB_CIK: []}
    val = 0
    for cik, facts in ((AAA_CIK, first), (BBB_CIK, second)):
        for end, filed, accn in facts:
            entries[cik].append(_entry(end, filed, accn, val))
            val += 1
    toolbox, _ = make_toolbox(_sec_with(fake_sec, entries), as_of="2030-01-01")
    arguments = {"ciks": [BBB_CIK, AAA_CIK], **REVENUES}
    whole = toolbox.execute("get_concept", {**arguments, "limit": 1000})
    assert whole.error_code is None, whole.payload
    rows = whole.payload["rows"]
    assert sorted(r["val"] for r in rows) == list(range(val))
    for before, after in zip(rows, rows[1:], strict=False):
        assert _in_order(before, after), (before, after)
    paged = _walk(toolbox, arguments, limit)
    assert json.dumps(paged) == json.dumps(rows)
