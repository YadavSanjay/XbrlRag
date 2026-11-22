"""C7: a stored run loads as a re-grade source, and partial or payload-less runs refuse."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.regrade import RegradeRefused, load_source, regrade_report


def _regrade(source, cases):
    return regrade_report(
        source, cases, suite="xbrl_v2_dev", suite_version="2", corpus_fingerprint="pm:new"
    )


@pytest.fixture()
def stored(lookup_case, answer, build_report):
    cases = [lookup_case("l1", 100.0), lookup_case("l2", 200.0), lookup_case("l3", 300.0)]
    report = build_report(
        [(cases[0], answer([100.0])), (cases[1], answer([250.0])),
         (cases[2], answer([], refused=True, cited=False))],
        n_planned=4,
    )
    return cases, report


@pytest.mark.criterion("C7")
def test_a_stored_run_loads(stored, store_with):
    # oracle: requirement 9 — the row's fields and the stored payloads, as stored
    cases, report = stored
    source = load_source(store_with(report), report.run_id)
    assert source["run_id"] == "eval-aaaaaaaaaaaa"
    assert source["split"] == "dev"
    assert source["n_planned"] == 4
    assert source["system"] == "xbrlrag_full"
    assert source["status"] == "OK"
    assert source["config"]["resume_identity"]["suite_hash"] == "sha256:" + "ab" * 32
    assert sorted(r["case_id"] for r in source["results"]) == ["l1", "l2", "l3"]
    assert {r["case_id"]: r["grade"] for r in source["results"]} == {
        "l1": "CORRECT", "l2": "INCORRECT", "l3": "REFUSED",
    }


@pytest.mark.criterion("C7")
def test_split_falls_back_to_the_config(stored, store_with):
    # oracle: requirement 9 — no split column: the config's split
    _, report = stored
    assert load_source(store_with(report, row_split=False), report.run_id)["split"] == "dev"


@pytest.mark.criterion("C7")
def test_unknown_running_and_payloadless_runs_refuse(stored, store_with):
    # oracle: requirement 9's three refusals
    _, report = stored
    with pytest.raises(RegradeRefused, match="eval-cccccccccccc"):
        load_source(store_with(report), "eval-cccccccccccc")
    with pytest.raises(RegradeRefused, match="RUNNING"):
        load_source(store_with(report, status="RUNNING"), report.run_id)
    with pytest.raises(RegradeRefused, match="result_payload"):
        load_source(store_with(report, payloads=False), report.run_id)


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
def test_store_and_json_sources_regrade_alike(stored, store_with, as_json_source, rekey):
    # oracle: requirement 9 — the store's source and the JSON report's give the same re-grade
    cases, report = stored
    new_cases = [cases[0], rekey(cases[1], 250.0), cases[2]]
    a = _regrade(load_source(store_with(report), report.run_id), new_cases)
    b = _regrade(as_json_source(report), new_cases)
    assert [r.grade.to_dict() for r in a.results] == [r.grade.to_dict() for r in b.results]
    assert a.metrics.to_dict() == b.metrics.to_dict()
    assert a.n_planned == b.n_planned == 4
    assert {r.case.id: r.grade.grade.value for r in a.results}["l2"] == "CORRECT"


@pytest.mark.criterion("C7")
@given(
    status=st.sampled_from(["OK", "ABORTED", "ERROR"]),
    n=st.integers(min_value=1, max_value=6),
)
def test_finished_runs_of_any_status_load(lookup_case, answer, build_report, store_with,
                                          status, n):
    # oracle: requirement 9 — only RUNNING refuses; the stored status and every payload kept
    report = build_report(
        [(lookup_case(f"f{i}", 10.0 + i), answer([10.0 + i])) for i in range(n)]
    )
    source = load_source(store_with(report, status=status), report.run_id)
    assert source["status"] == status
    assert sorted(r["case_id"] for r in source["results"]) == sorted(f"f{i}" for i in range(n))
    assert source["n_planned"] == n
