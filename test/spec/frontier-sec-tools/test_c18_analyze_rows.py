"""C18: computing over earlier tool results, with no copying and no code."""

from __future__ import annotations

import json
import math
import statistics

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sec_payloads import HOLD_CIK, frame_url, json_body

from src.eval.core.judge_wiring import answer_claims
from src.eval.sec_tools.toolbox import ToolOutcome, analyze_rows

REVENUES = {"taxonomy": "us-gaap", "tag": "Revenues"}
FRAME = {**REVENUES, "unit": "USD", "period": "CY2023"}
URL = "https://data.sec.gov/x.json"


def _source(rows: list[dict], *, total: int | None = None) -> ToolOutcome:
    count = len(rows) if total is None else total
    return ToolOutcome({"rows": rows[:1], "total": count}, [URL], rows=rows)


def _run(rows: list[dict], **arguments):
    return analyze_rows({"sources": ["S1"], **arguments}, {"S1": _source(rows)})


VALUES = [{"val": v} for v in (10, 40, 20, 30)]
FILED = [
    {"cik": 1, "fy": 2022, "form": "10-K", "end": "2022-12-31", "val": 100},
    {"cik": 1, "fy": 2023, "form": "10-K", "end": "2023-12-31", "val": 120},
    {"cik": 1, "fy": 2023, "form": "10-Q", "end": "2023-09-30", "val": 90},
    {"cik": 2, "fy": 2022, "form": "10-K", "end": "2022-12-31", "val": 50},
    {"cik": 2, "fy": 2023, "form": "10-K", "end": "2023-12-31", "val": 40},
    {"cik": 3, "fy": 2023, "form": "10-K", "end": "2023-12-31", "val": 7},
]
CURRENT = {"where": [{"column": "fy", "op": "eq", "value": 2023}]}
BASE = {"where": [{"column": "fy", "op": "eq", "value": 2022}]}
TENK = [{"column": "form", "op": "eq", "value": "10-K"}]


def _sec_with_frame(fake_sec, values: list[int]):
    data = [
        {"accn": f"a-{i}", "cik": 100 + i, "entityName": f"Filer {i}", "loc": "US-NY",
         "start": "2023-01-01", "end": "2023-12-31", "val": value}
        for i, value in enumerate(values)
    ]
    sec = fake_sec()
    sec.pages[frame_url("Revenues", "USD", "CY2023")] = (200, json_body({
        "taxonomy": "us-gaap", "tag": "Revenues", "ccp": "CY2023", "uom": "USD",
        "label": "Revenues", "description": "d", "pts": len(data), "data": data,
    }))
    return sec


@pytest.mark.criterion("C18")
@pytest.mark.parametrize(
    ("op", "expected"),
    [("sum", 100.0), ("mean", 25.0), ("median", 25.0), ("min", 10.0), ("max", 40.0)],
)
def test_each_aggregate(op, expected):
    # oracle: 10, 40, 20 and 30, by hand
    outcome = _run(VALUES, op=op, column="val")
    assert outcome.error_code is None
    # tolerance: float arithmetic on four small integers
    assert outcome.payload["value"] == pytest.approx(expected, rel=1e-12)
    assert outcome.payload["count"] == 4
    assert outcome.derived_from == ["S1"]


@pytest.mark.criterion("C18")
def test_count_needs_no_column():
    # oracle: requirement 33 — five of FILED's six rows are 10-K, counted by hand
    outcome = _run(FILED, op="count", where=TENK)
    assert outcome.payload["value"] == 5


@pytest.mark.criterion("C18")
def test_a_median_over_more_rows_than_a_model_could_copy(make_toolbox, fake_sec):
    # oracle: the values are 1..5001 by construction, so the median is 2501. The model saw one
    toolbox, sec = make_toolbox(_sec_with_frame(fake_sec, list(range(1, 5002))))
    frame = toolbox.execute("get_frame", {**FRAME, "limit": 1})
    assert len(frame.payload["rows"]) == 1
    assert frame.payload["total"] == 5001
    before = len(sec.calls)
    outcome = toolbox.execute(
        "analyze_rows", {"sources": ["S1"], "op": "median", "column": "val"},
        results={"S1": frame},
    )
    # tolerance: an exact middle value as a float
    assert outcome.payload["value"] == pytest.approx(2501.0, abs=1e-9)
    assert outcome.payload["count"] == 5001
    assert outcome.payload["inputs"] == [
        {"source_id": "S1", "rows_total": 5001, "rows_selected": 5001, "complete": True}]
    assert [r["value"] for r in outcome.payload["row_refs"]] == [2501]
    assert len(sec.calls) == before


@pytest.mark.criterion("C18")
def test_a_median_of_an_even_count_cites_its_middle_rows():
    # oracle: requirement 35 — 20 and 30 are the middle pair, at indexes 2 and 3
    refs = _run(VALUES, op="median", column="val").payload["row_refs"]
    assert sorted(r["index"] for r in refs) == [2, 3]
    assert all(r["source_id"] == "S1" for r in refs)


@pytest.mark.criterion("C18")
def test_min_and_max_cite_their_row():
    # oracle: requirement 35 — 40 is at index 1
    refs = _run(VALUES, op="max", column="val").payload["row_refs"]
    assert refs == [{"source_id": "S1", "index": 1, "value": 40}]


@pytest.mark.criterion("C18")
def test_a_sum_caps_its_row_refs_and_says_so():
    # oracle: requirement 35 — the first 100, and the true count
    payload = _run([{"val": i} for i in range(250)], op="sum", column="val").payload
    assert len(payload["row_refs"]) == 100
    assert payload["row_refs_total"] == 250


@pytest.mark.criterion("C18")
def test_sort_keeps_the_rows_and_their_origin():
    # oracle: requirement 33 — by hand
    payload = _run(VALUES, op="sort", column="val", order="asc").payload
    assert [(r["val"], r["index"]) for r in payload["rows"]] == [(10, 0), (20, 2), (30, 3), (40, 1)]
    assert payload["rows"][0]["source_id"] == "S1"
    assert payload["total"] == 4


@pytest.mark.criterion("C18")
def test_rank_handles_ties():
    # oracle: requirement 33 — 1, 1, 3, with ties in source order
    rows = [{"val": 5, "n": "a"}, {"val": 9, "n": "b"}, {"val": 9, "n": "c"}]
    ranked = _run(rows, op="rank", column="val").payload["rows"]
    assert [(r["rank"], r["n"]) for r in ranked] == [(1, "b"), (1, "c"), (3, "a")]


@pytest.mark.criterion("C18")
def test_percent_change():
    # oracle: (120 - 100) / 100 is 20 percent, with the two cited rows
    payload = _run(
        FILED, op="percent_change", column="val", where=TENK + [
            {"column": "cik", "op": "eq", "value": 1}], current=CURRENT, base=BASE,
    ).payload
    # tolerance: one float division
    assert payload["value"] == pytest.approx(20.0, rel=1e-12)
    assert sorted(r["value"] for r in payload["row_refs"]) == [100, 120]


@pytest.mark.criterion("C18")
def test_ratio():
    # oracle: 120 / 50, across filers
    payload = _run(
        FILED, op="ratio", column="val", where=TENK,
        numerator={"where": [{"column": "val", "op": "eq", "value": 120}]},
        denominator={"where": [{"column": "val", "op": "eq", "value": 50}]},
    ).payload
    # tolerance: one float division
    assert payload["value"] == pytest.approx(2.4, rel=1e-12)


@pytest.mark.criterion("C18")
@pytest.mark.parametrize(
    ("where", "count"),
    [
        ([{"column": "fy", "op": "eq", "value": 2023}], 4),
        ([{"column": "form", "op": "ne", "value": "10-K"}], 1),
        ([{"column": "cik", "op": "in", "value": [1, 3]}], 4),
        ([{"column": "cik", "op": "eq", "value": "0000000002"}], 2),
        ([{"column": "end", "op": "ge", "value": "2023-10-01"}], 3),
        ([{"column": "end", "op": "lt", "value": "2023-01-01"}], 2),
        ([{"column": "val", "op": "gt", "value": 90}], 2),
        ([{"column": "val", "op": "le", "value": 7}], 1),
        ([{"column": "form", "op": "contains", "value": "-q"}], 1),
        ([{"column": "val", "op": "is_number"}], 6),
        ([{"column": "fy", "op": "eq", "value": 2023}, {"column": "form", "op": "eq",
                                                        "value": "10-K"}], 3),
    ],
)
def test_filters(where, count):
    # oracle: counted by hand on FILED
    assert _run(FILED, op="count", where=where).payload["value"] == count


@pytest.mark.criterion("C18")
def test_a_filter_on_another_type_does_not_match():
    # oracle: requirement 32 — a date and a number do not compare
    assert _run([{"val": 1, "end": 5}, {"val": 2, "end": "2023-12-31"}], op="count", where=[
        {"column": "end", "op": "ge", "value": "2023-01-01"}]).payload["value"] == 1


@pytest.mark.criterion("C18")
def test_latest_by_is_s1s_dedupe_on_real_tool_rows(make_toolbox):
    # oracle: S1 — FY2022 was restated from 100 to 101, so 101 + 120
    toolbox, _ = make_toolbox()
    concept = toolbox.execute("get_concept", {"cik": "1", **REVENUES})
    payload = analyze_rows(
        {"sources": ["S1"], "op": "sum", "column": "val", "latest_by": ["start", "end"]},
        {"S1": concept},
    ).payload
    # tolerance: a float sum of two integers
    assert payload["value"] == pytest.approx(221.0, abs=1e-9)
    assert payload["count"] == 2


@pytest.mark.criterion("C18")
def test_by_gives_one_row_per_group():
    # oracle: 10-K rows per cik, by hand: 220, 90 and 7, largest first
    payload = _run(FILED, op="sum", column="val", where=TENK, by="cik").payload
    assert [(r["key"], r["value"], r["count"]) for r in payload["rows"]] == [
        (1, 220, 2), (2, 90, 2), (3, 7, 1)]


@pytest.mark.criterion("C18")
def test_percent_change_by_filer_ranks_and_names_the_unmatched():
    # oracle: AAA +20%, BBB -20%, and CCC has no base, so S3 can name it
    payload = _run(
        FILED, op="percent_change", column="val", where=TENK, by="cik", current=CURRENT, base=BASE,
    ).payload
    assert [r["key"] for r in payload["rows"]] == [1, 2]
    # tolerance: one float division
    assert payload["rows"][0]["value"] == pytest.approx(20.0, rel=1e-12)
    assert payload["rows"][1]["value"] == pytest.approx(-20.0, rel=1e-12)
    assert (payload["rows"][0]["current"], payload["rows"][0]["base"]) == (120, 100)
    assert [u["key"] for u in payload["unmatched"]] == [3]
    assert payload["unmatched"][0]["reason"]
    assert payload["unmatched_total"] == 1


@pytest.mark.criterion("C18")
def test_several_sources_are_read_together():
    # oracle: requirement 31 — pages or frames together, a repeated id once
    results = {"S1": _source(VALUES), "S2": _source([{"val": 100}])}
    payload = analyze_rows(
        {"sources": ["S1", "S2", "S1"], "op": "sum", "column": "val"}, results
    ).payload
    # tolerance: a float sum of small integers
    assert payload["value"] == pytest.approx(200.0, abs=1e-9)
    assert [i["source_id"] for i in payload["inputs"]] == ["S1", "S2"]


@pytest.mark.criterion("C18")
def test_an_incomplete_source_says_so():
    # oracle: requirement 33 — a search page of 1 of 3 hits
    outcome = analyze_rows({"sources": ["S1"], "op": "count"},
                           {"S1": _source([{"val": 1}], total=3)})
    assert outcome.payload["inputs"][0]["complete"] is False


@pytest.mark.criterion("C18")
def test_a_result_feeds_another():
    # oracle: requirement 31 — the top two of a ranking sum to 70
    ranked = _run(VALUES, op="rank", column="val")
    outcome = analyze_rows(
        {"sources": ["S2"], "op": "sum", "column": "val",
         "where": [{"column": "rank", "op": "le", "value": 2}]},
        {"S2": ranked},
    )
    # tolerance: a float sum of two integers
    assert outcome.payload["value"] == pytest.approx(70.0, abs=1e-9)


@pytest.mark.criterion("C18")
@pytest.mark.parametrize(
    ("arguments", "code"),
    [
        ({"sources": ["S9"], "op": "sum", "column": "val"}, "bad_reference"),
        ({"sources": ["S2"], "op": "sum", "column": "val"}, "bad_reference"),
        ({"sources": [], "op": "sum", "column": "val"}, "bad_arguments"),
        ({"sources": ["S1"] * 51, "op": "count"}, "bad_arguments"),
        ({"sources": "S1", "op": "sum", "column": "val"}, "bad_arguments"),
        ({"sources": ["S1"], "op": "exec", "column": "val"}, "bad_arguments"),
        ({"sources": ["S1"], "op": "sum"}, "bad_arguments"),
        ({"sources": ["S1"], "op": "sum", "column": "nope"}, "unknown_column"),
        ({"sources": ["S1"], "op": "count", "where": [{"column": "nope", "op": "eq", "value": 1}]},
         "unknown_column"),
        ({"sources": ["S1"], "op": "count", "where": [{"column": "val", "op": "like", "value": 1}]},
         "bad_arguments"),
        ({"sources": ["S1"], "op": "sum", "column": "name"}, "non_numeric"),
        ({"sources": ["S1"], "op": "count", "where": [{"column": "val", "op": "gt", "value": 99}]},
         "empty_selection"),
    ],
)
def test_a_failure_is_an_outcome_with_a_code(arguments, code):
    # oracle: requirement 34
    results = {
        "S1": _source([{"val": 1, "name": "a"}, {"val": 2, "name": "b"}]),
        "S2": ToolOutcome({"text": "a filing"}, [URL]),
    }
    outcome = analyze_rows(arguments, results)
    assert outcome.error_code == code
    assert outcome.payload["error"] == code
    assert outcome.payload["message"]
    assert "value" not in outcome.payload


@pytest.mark.criterion("C18")
def test_no_arguments_is_an_outcome():
    # oracle: requirement 34
    assert analyze_rows(None, {}).error_code == "bad_arguments"


@pytest.mark.criterion("C18")
@pytest.mark.parametrize("bad", [None, "12", True, float("nan"), float("inf")])
def test_a_non_numeric_value_is_never_skipped_silently(bad):
    # oracle: requirement 34 — the failure names the row, and is_number is how to go on
    rows = [{"val": 1}, {"val": bad}, {"val": 3}]
    outcome = _run(rows, op="sum", column="val")
    assert outcome.error_code == "non_numeric"
    assert outcome.payload["first_index"] == 1
    assert outcome.payload["first_source_id"] == "S1"
    assert outcome.payload["rows"] == 1
    kept = _run(rows, op="sum", column="val",
                where=[{"column": "val", "op": "is_number"}])
    # tolerance: a float sum of two integers
    assert kept.payload["value"] == pytest.approx(4.0, abs=1e-9)


@pytest.mark.criterion("C18")
def test_a_missing_value_is_non_numeric():
    # oracle: requirement 34
    assert _run([{"val": 1}, {"other": 2}], op="sum", column="val").error_code == "non_numeric"


@pytest.mark.criterion("C18")
def test_a_side_with_two_rows_is_ambiguous():
    # oracle: requirement 34 — two filers' fiscal 2023 is not one number
    outcome = _run(FILED, op="percent_change", column="val", current=CURRENT, base=BASE)
    assert outcome.error_code == "ambiguous_selection"
    assert outcome.payload["rows"] >= 2


@pytest.mark.criterion("C18")
@pytest.mark.parametrize(
    "rows",
    [[{"y": 1, "val": 5}, {"y": 2, "val": 0}], [{"y": 1, "val": 5}, {"y": 2, "val": -1}]],
)
def test_a_base_at_or_below_zero_is_not_meaningful(rows):
    # oracle: S4 — growth is not meaningful when prior <= 0
    outcome = _run(
        rows, op="percent_change", column="val",
        current={"where": [{"column": "y", "op": "eq", "value": 1}]},
        base={"where": [{"column": "y", "op": "eq", "value": 2}]},
    )
    assert outcome.error_code == "not_meaningful"


@pytest.mark.criterion("C18")
def test_a_zero_denominator_is_not_meaningful():
    # oracle: requirement 33
    outcome = _run(
        [{"y": 1, "val": 5}, {"y": 2, "val": 0}], op="ratio", column="val",
        numerator={"where": [{"column": "y", "op": "eq", "value": 1}]},
        denominator={"where": [{"column": "y", "op": "eq", "value": 2}]},
    )
    assert outcome.error_code == "not_meaningful"


@pytest.mark.criterion("C18")
def test_too_many_rows_is_refused():
    # oracle: requirement 31's bound
    outcome = _run([{"val": 1}] * 100_001, op="count")
    assert outcome.error_code == "bad_arguments"


@pytest.mark.criterion("C18")
def test_rows_after_the_date_never_enter(make_toolbox):
    # oracle: requirements 11, 31 — 150 was filed after the date
    toolbox, _ = make_toolbox()
    concept = toolbox.execute("get_concept", {"cik": "1", **REVENUES})
    assert analyze_rows({"sources": ["S1"], "op": "max", "column": "val"},
                        {"S1": concept}).payload["value"] == 120


@pytest.mark.criterion("C18")
def test_withheld_rows_never_enter(make_toolbox):
    # oracle: requirements 28, 31 — HOLD's 999 tops the frame
    toolbox, _ = make_toolbox(withheld=(HOLD_CIK,))
    frame = toolbox.execute("get_frame", FRAME)
    payload = analyze_rows({"sources": ["S1"], "op": "max", "column": "val"},
                           {"S1": frame}).payload
    assert payload["value"] == 300
    assert "Held Corp" not in json.dumps(payload)


@pytest.mark.criterion("C18")
def test_through_the_arm_nothing_is_copied_and_the_citation_resolves(make_arm, make_case, reply,
                                                                     fake_sec):
    # oracle: requirements 31, 35 — the model saw one of 5,001 rows and cited the computation
    final = reply(2501, statements=[{"statement": "The median was 2501.", "sources": ["S2"]}])
    arm, adapter, _ = make_arm(
        [[("get_frame", {**FRAME, "limit": 1})],
         [("analyze_rows", {"sources": ["S1"], "op": "median", "column": "val"})]],
        final=final, transport=_sec_with_frame(fake_sec, list(range(1, 5002))),
    )
    answer = arm.answer(make_case())
    computed = json.loads(adapter.texts(2, "tool")[1])
    assert computed["source_id"] == "S2"
    assert computed["tool"] == "analyze_rows"
    # tolerance: an exact middle value as a float
    assert computed["result"]["value"] == pytest.approx(2501.0, abs=1e-9)
    assert sum(len(t) for t in adapter.texts(2, "tool")) < 5000
    assert answer.chunk_ids == ["S2", "S1"]
    assert answer.agent["unresolved_citations"] == []
    assert answer.agent["by_tool"] == {"get_frame": 1, "analyze_rows": 1}
    derived = answer.agent["sources"][1]
    assert derived["derived_from"] == ["S1"]
    assert derived["urls"] == answer.agent["sources"][0]["urls"]
    assert answer.agent["sources"][0]["urls"]
    claim = answer_claims(answer)[0]
    assert len(claim.cited_passages) == 2
    assert json.loads(claim.cited_passages[0])["source_id"] == "S2"
    assert json.loads(claim.cited_passages[1])["source_id"] == "S1"


@pytest.mark.criterion("C18")
def test_a_chain_resolves_transitively(make_arm, make_case, reply):
    # oracle: requirement 35 — S3 reads S2, which reads S1
    arm, _, _ = make_arm(
        [[("get_frame", FRAME)],
         [("analyze_rows", {"sources": ["S1"], "op": "rank", "column": "val"})],
         [("analyze_rows", {"sources": ["S2"], "op": "count"})]],
        final=reply(4, cited=["S3"]),
    )
    assert arm.answer(make_case()).chunk_ids == ["S3", "S2", "S1"]


@pytest.mark.criterion("C18")
def test_the_model_can_see_a_failure_and_go_on(make_arm, make_case, reply):
    # oracle: requirements 20, 34 — no source id, no case error; S9 and a filing hold no rows
    arm, adapter, _ = make_arm(
        [[("analyze_rows", {"sources": ["S9"], "op": "count"})],
         [("find_company", {"query": "AAA"})],
         [("analyze_rows", {"sources": ["S1"], "op": "count"})]],
        final=reply(1, cited=["S2"]),
    )
    answer = arm.answer(make_case())
    assert answer.error is None
    failed = json.loads(adapter.texts(1, "tool")[0])
    assert failed["error"] == "bad_reference"
    assert "source_id" not in failed
    assert answer.agent["tool_errors"] == 1
    assert answer.chunk_ids == ["S2", "S1"]


@pytest.mark.criterion("C18")
def test_a_case_cannot_read_another_cases_results(make_arm, make_case):
    # oracle: requirement 31 — a source lives for its case only
    arm, adapter, _ = make_arm(
        [[("find_company", {"query": "AAA"})],
         "first case ends",
         [("analyze_rows", {"sources": ["S1"], "op": "count"})]],
    )
    arm.answer(make_case())
    arm.answer(make_case())
    assert json.loads(adapter.texts(3, "tool")[0])["error"] == "bad_reference"


_VALUES = st.lists(st.integers(-10**9, 10**9), min_size=1, max_size=2000)


@pytest.mark.criterion("C18")
@given(values=_VALUES, seed=st.randoms(use_true_random=False))
def test_aggregates_equal_the_standard_library_in_any_order(values, seed):
    # oracle: math.fsum, statistics.median, min and max on the same integers; shuffling changes
    # nothing
    rows = [{"val": v} for v in values]
    shuffled = list(rows)
    seed.shuffle(shuffled)
    expected = {
        "sum": math.fsum(values),
        "mean": math.fsum(values) / len(values),
        "median": float(statistics.median(values)),
        "min": float(min(values)),
        "max": float(max(values)),
    }
    for op, value in expected.items():
        # tolerance: float rounding of sums of up to 2,000 integers
        assert _run(rows, op=op, column="val").payload["value"] == pytest.approx(
            value, rel=1e-9, abs=1e-6)
        assert _run(shuffled, op=op, column="val").payload["value"] == pytest.approx(
            value, rel=1e-9, abs=1e-6)


@pytest.mark.criterion("C18")
@settings(max_examples=40)
@given(values=st.lists(st.integers(0, 50), min_size=1, max_size=300),
       cap=st.integers(2000, 6000))
def test_walking_a_ranking_gives_each_row_once_within_the_cap(values, cap):
    # oracle: requirement 33 — rows carry their index, and a rank counts the rows above + 1
    rows = [{"val": v} for v in values]
    arguments = {"sources": ["S1"], "op": "rank", "column": "val"}
    seen: list[dict] = []
    offset = 0
    for _ in range(10_000):
        outcome = analyze_rows({**arguments, "offset": offset}, {"S1": _source(rows)},
                               max_result_chars=cap)
        assert len(json.dumps(outcome.payload, ensure_ascii=False)) <= cap
        seen.extend(outcome.payload["rows"])
        if outcome.payload["next_offset"] is None:
            break
        assert outcome.payload["next_offset"] > offset
        offset = outcome.payload["next_offset"]
    assert sorted(r["index"] for r in seen) == list(range(len(values)))
    for row in seen:
        assert row["rank"] == 1 + sum(1 for v in values if v > row["val"])
    assert len(outcome.rows) == len(values)


@pytest.mark.criterion("C18")
@given(values=st.lists(st.integers(-20, 20), min_size=1, max_size=100),
       threshold=st.integers(-25, 25))
def test_a_filter_selects_exactly_the_rows_that_match(values, threshold):
    # oracle: the same comparison in this file; no match is a failure, not zero
    rows = [{"val": v} for v in values]
    outcome = _run(rows, op="count", where=[{"column": "val", "op": "gt", "value": threshold}])
    expected = sum(1 for v in values if v > threshold)
    if expected:
        assert outcome.payload["value"] == expected
    else:
        assert outcome.error_code == "empty_selection"
