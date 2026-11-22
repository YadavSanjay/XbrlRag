"""C5: source_id is a column of every row analyze_rows reads."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.sec_tools.toolbox import analyze_rows

S1_ROWS = [{"val": 10}, {"val": 20}]
S2_ROWS = [{"val": 1}, {"val": 2}, {"val": 4}]


def _ok(arguments: dict, results: dict) -> dict:
    outcome = analyze_rows(arguments, results)
    assert outcome.error_code is None, outcome.payload
    return outcome.payload


@pytest.mark.criterion("C5")
def test_where_on_source_id(source):
    # oracle: hand-built sources — S2's values 1 + 2 + 4 = 7
    results = {"S1": source(S1_ROWS), "S2": source(S2_ROWS)}
    payload = _ok({"sources": ["S1", "S2"], "op": "sum", "column": "val",
                   "where": [{"column": "source_id", "op": "eq", "value": "S2"}]}, results)
    # tolerance: a float sum of three integers
    assert payload["value"] == pytest.approx(7.0, abs=1e-9)
    assert payload["count"] == 3


@pytest.mark.criterion("C5")
def test_by_source_id(source):
    # oracle: hand-built sources — S1 sums to 30 over 2 rows, S2 to 7 over 3, largest first
    results = {"S1": source(S1_ROWS), "S2": source(S2_ROWS)}
    payload = _ok({"sources": ["S1", "S2"], "op": "sum", "column": "val", "by": "source_id"},
                  results)
    assert [(r["key"], r["value"], r["count"]) for r in payload["rows"]] == [
        ("S1", 30, 2), ("S2", 7, 3)]


@pytest.mark.criterion("C5")
def test_latest_by_source_id(source):
    # oracle: hand-built sources — one row kept per source: the greatest (filed, accn), so S1's
    # 20 (filed 2024) and S2's 4 (accn c beats b on the same day): 24
    results = {
        "S1": source([{"val": 10, "filed": "2023-01-01", "accn": "a"},
                      {"val": 20, "filed": "2024-01-01", "accn": "a"}]),
        "S2": source([{"val": 1, "filed": "2022-01-01", "accn": "a"},
                      {"val": 2, "filed": "2024-05-01", "accn": "b"},
                      {"val": 4, "filed": "2024-05-01", "accn": "c"}]),
    }
    payload = _ok({"sources": ["S1", "S2"], "op": "sum", "column": "val",
                   "latest_by": ["source_id"]}, results)
    # tolerance: a float sum of two integers
    assert payload["value"] == pytest.approx(24.0, abs=1e-9)
    assert payload["count"] == 2


@pytest.mark.criterion("C5")
def test_source_id_is_never_an_unknown_column(source):
    # oracle: requirement 5 — every row read has the column, so a where on it selects, never
    # fails; no row here comes from S7, so a where on S7 is an empty selection
    results = {"S1": source([{"val": 3}])}
    payload = _ok({"sources": ["S1"], "op": "count",
                   "where": [{"column": "source_id", "op": "in", "value": ["S1", "S7"]}]},
                  results)
    assert payload["value"] == 1
    outcome = analyze_rows({"sources": ["S1"], "op": "count",
                            "where": [{"column": "source_id", "op": "eq", "value": "S7"}]},
                           results)
    assert outcome.error_code == "empty_selection"


@pytest.mark.criterion("C5")
def test_a_rows_own_source_id_wins(source):
    # oracle: requirement 5 — a sort of S1 and S2 is S3; its rows keep the source_id of the row
    # they came from, so a where on S1 over S3 selects S1's 10 and 20 (sum 30), not nothing
    results = {"S1": source(S1_ROWS), "S2": source(S2_ROWS)}
    ranked = analyze_rows({"sources": ["S1", "S2"], "op": "sort", "column": "val"}, results)
    assert ranked.error_code is None
    results["S3"] = ranked
    payload = _ok({"sources": ["S3"], "op": "sum", "column": "val",
                   "where": [{"column": "source_id", "op": "eq", "value": "S1"}]}, results)
    # tolerance: a float sum of two integers
    assert payload["value"] == pytest.approx(30.0, abs=1e-9)
    grouped = _ok({"sources": ["S3"], "op": "count", "by": "source_id"}, results)
    assert sorted((r["key"], r["value"]) for r in grouped["rows"]) == [("S1", 2), ("S2", 3)]
    plain = _ok({"sources": ["S1"], "op": "count", "by": "source_id"}, results)
    assert [(r["key"], r["value"]) for r in plain["rows"]] == [("S1", 2)]


@pytest.mark.criterion("C5")
@given(sizes=st.lists(st.integers(1, 12), min_size=1, max_size=6))
def test_by_source_id_counts_each_sources_rows(source, sizes):
    # oracle: the drawn sizes — source Sk holds sizes[k-1] rows, so by source_id counts them
    results = {f"S{k}": source([{"val": j} for j in range(n)])
               for k, n in enumerate(sizes, start=1)}
    payload = _ok({"sources": list(results), "op": "count", "by": "source_id",
                   "limit": 1000}, results)
    assert sorted((r["key"], r["value"]) for r in payload["rows"]) == sorted(
        (f"S{k}", n) for k, n in enumerate(sizes, start=1))
