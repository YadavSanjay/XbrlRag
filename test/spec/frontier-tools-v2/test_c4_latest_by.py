"""C4: analyze_rows latest_by keeps one row per filer and key, never merging filers."""

from __future__ import annotations

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from src.eval.sec_tools.toolbox import analyze_rows

# Two filers whose restated facts share period ends, as get_concept rows name them.
AAA = "0000000001"
BBB = "0000000002"
RESTATED = [
    {"cik": AAA, "end": "2022-12-31", "val": 100, "filed": "2023-02-01", "accn": "a-1"},
    {"cik": AAA, "end": "2022-12-31", "val": 101, "filed": "2024-02-01", "accn": "a-2"},
    {"cik": AAA, "end": "2023-12-31", "val": 120, "filed": "2024-02-01", "accn": "a-2"},
    {"cik": BBB, "end": "2022-12-31", "val": 50, "filed": "2023-03-01", "accn": "b-1"},
    {"cik": BBB, "end": "2022-12-31", "val": 55, "filed": "2024-03-01", "accn": "b-2"},
    {"cik": BBB, "end": "2023-12-31", "val": 40, "filed": "2024-03-01", "accn": "b-2"},
]


def _sum(rows: list[dict], latest_by: list[str], source) -> dict:
    outcome = analyze_rows({"sources": ["S1"], "op": "sum", "column": "val",
                            "latest_by": latest_by}, {"S1": source(rows)})
    assert outcome.error_code is None, outcome.payload
    return outcome.payload


@pytest.mark.criterion("C4")
def test_two_filers_sharing_period_ends_are_never_merged(source):
    # oracle: by hand — per filer and end, the latest filing: AAA 101 + 120, BBB 55 + 40 = 316
    payload = _sum(RESTATED, ["end"], source)
    # tolerance: a float sum of four integers
    assert payload["value"] == pytest.approx(316.0, abs=1e-9)
    assert payload["count"] == 4


@pytest.mark.criterion("C4")
def test_naming_cik_gives_the_same_result(source):
    # oracle: requirement 4 — cik may still be named; the same 316 from four rows
    payload = _sum(RESTATED, ["cik", "end"], source)
    without = _sum(RESTATED, ["end"], source)
    # tolerance: a float sum of four integers
    assert payload["value"] == pytest.approx(316.0, abs=1e-9)
    assert (payload["count"], without["count"]) == (4, 4)


@pytest.mark.criterion("C4")
def test_spellings_of_one_cik_are_one_filer(source):
    # oracle: requirement 4 — "0000320193", "320193" and 320193 are one filer, so of three
    # filings of one period end only the latest (filed 2024-02-01, val 3) is kept; the other
    # filer's 10 stands alone: 3 + 10 = 13
    rows = [
        {"cik": "0000320193", "end": "2023-12-31", "val": 1, "filed": "2023-02-01", "accn": "x"},
        {"cik": "320193", "end": "2023-12-31", "val": 3, "filed": "2024-02-01", "accn": "x"},
        {"cik": 320193, "end": "2023-12-31", "val": 2, "filed": "2023-06-01", "accn": "x"},
        {"cik": 789019, "end": "2023-12-31", "val": 10, "filed": "2023-01-01", "accn": "y"},
    ]
    payload = _sum(rows, ["end"], source)
    # tolerance: a float sum of two integers
    assert payload["value"] == pytest.approx(13.0, abs=1e-9)
    assert payload["count"] == 2


@pytest.mark.criterion("C4")
def test_rows_without_a_cik_are_keyed_by_the_columns_alone(source):
    # oracle: requirement 4 — rows without a cik, or with one that is not a CIK, share one key
    # per end, so the latest (filed, accn) wins among them: 7 (2023-12-31), 9 (2022-12-31); the
    # filer's row stands apart: 7 + 9 + 120 = 136
    rows = [
        {"end": "2023-12-31", "val": 5, "filed": "2024-01-01", "accn": "n-1"},
        {"cik": "not-a-cik", "end": "2023-12-31", "val": 7, "filed": "2024-01-01",
         "accn": "n-2"},
        {"end": "2022-12-31", "val": 9, "filed": "2023-01-01", "accn": "n-3"},
        {"cik": AAA, "end": "2023-12-31", "val": 120, "filed": "2020-01-01", "accn": "a-0"},
    ]
    payload = _sum(rows, ["end"], source)
    # tolerance: a float sum of three integers
    assert payload["value"] == pytest.approx(136.0, abs=1e-9)
    assert payload["count"] == 3


_SPELLINGS = {1: ["0000000001", "1", 1], 2: ["0000000002", "2", 2], 3: ["0000000003", "3", 3]}
_ROW = st.tuples(
    st.sampled_from([1, 2, 3]),
    st.integers(0, 2),
    st.sampled_from(["2021-12-31", "2022-12-31", "2023-12-31"]),
    st.sampled_from(["2022-03-01", "2023-03-01", "2024-03-01"]),
)


@pytest.mark.criterion("C4")
@given(drawn=st.lists(_ROW, min_size=1, max_size=40))
@example(drawn=[(1, 0, "2023-12-31", "2024-03-01"), (2, 1, "2023-12-31", "2023-03-01")])
def test_one_row_kept_per_filer_and_key(source, drawn):
    # oracle: the count kept equals the number of distinct (filer, end) pairs, and each kept row
    # is its pair's greatest (filed, accn) — computed in this test from the drawn rows; accn is
    # unique (the row's number), so the greatest is one row
    rows = [{"cik": _SPELLINGS[filer][spelling], "end": end, "filed": filed,
             "accn": f"{i:04d}", "val": i}
            for i, (filer, spelling, end, filed) in enumerate(drawn)]
    best: dict[tuple[int, str], tuple[str, str, int]] = {}
    for i, (filer, _, end, filed) in enumerate(drawn):
        candidate = (filed, f"{i:04d}", i)
        if (filer, end) not in best or candidate > best[(filer, end)]:
            best[(filer, end)] = candidate
    count = analyze_rows({"sources": ["S1"], "op": "count", "latest_by": ["end"]},
                         {"S1": source(rows)})
    assert count.error_code is None
    assert count.payload["value"] == len(best)
    total = _sum(rows, ["end"], source)
    # tolerance: a float sum of at most 40 small integers
    assert total["value"] == pytest.approx(float(sum(v[2] for v in best.values())), abs=1e-9)
