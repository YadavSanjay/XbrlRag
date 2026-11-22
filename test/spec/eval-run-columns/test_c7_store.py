"""C7: the store writes the new run and case columns (requirements 8 and 9).

The database driver is faked (conftest ``fake_db``); the store, its SQL and the report
are real.
"""

from __future__ import annotations

import pytest

from src.eval.core.store import PostgresEvalStore, persist_report_dict

TAG_COLUMNS = ("intent_id", "phrasing_family", "template_id", "key_source", "key_status")
JUDGE_COLUMNS = ("judge_correctness", "judge_completeness", "judge_citation_support",
                 "judge_unsupported_claims")
NEW_CASE_COLUMNS = TAG_COLUMNS + JUDGE_COLUMNS
FULL_SCORES = {"correctness": 0.9, "completeness": 0.6, "citation_support": 0.8,
               "unsupported_claims": 1}


def _store() -> PostgresEvalStore:
    return PostgresEvalStore(conn_params={"dbname": "fake"})


def _dev_report(make_case, graded, make_report):
    """Four dev cases over two intents: i1 split (one right, one wrong), i2 both right.
    All four share paraphrase group g, which is unstable, so the paraphrase-group value
    (0.0) differs from the intent value (0.5)."""
    base = ["split:dev", "template:t1", "key_source:sec_companyfacts", "key_status:verified"]
    results = [
        graded(make_case("a", [*base, "intent:i1", "family:terse"], paraphrase_group="g"),
               True, FULL_SCORES),
        graded(make_case("b", [*base, "intent:i1", "family:formal"], paraphrase_group="g"),
               False),
        graded(make_case("c", [*base, "intent:i2", "family:terse"], paraphrase_group="g"),
               True),
        graded(make_case("d", [*base, "intent:i2", "family:formal"], paraphrase_group="g"),
               True, None),
    ]
    return make_report(results, config={"split": "dev", "judge_version": "judge-v1"})


# oracle for _dev_report's case rows, written out by hand from the tags and scores above
DEV_CASE_ROWS = {
    "a": {"intent_id": "i1", "phrasing_family": "terse", "judge_correctness": 0.9,
          "judge_completeness": 0.6, "judge_citation_support": 0.8,
          "judge_unsupported_claims": 1},
    "b": {"intent_id": "i1", "phrasing_family": "formal"},
    "c": {"intent_id": "i2", "phrasing_family": "terse"},
    "d": {"intent_id": "i2", "phrasing_family": "formal"},
}


def _expected_case_row(case_id: str) -> dict:
    row = {c: None for c in NEW_CASE_COLUMNS}
    row.update(template_id="t1", key_source="sec_companyfacts", key_status="verified")
    row.update(DEV_CASE_ROWS[case_id])
    return row


def _new_case_values(rows) -> dict[str, dict]:
    return {r["case_id"]: {c: r.get(c) for c in NEW_CASE_COLUMNS} for r in rows}


@pytest.mark.criterion("C7")
def test_persist_run_writes_run_columns(make_case, graded, make_report, fake_db):
    # oracle: requirement 8 — split and judge_version from run_columns; paraphrase
    # stability is the intent stability, hand count 1 of 2 multi-case intents stable
    report = _dev_report(make_case, graded, make_report)
    with fake_db() as rec:
        ok = _store().persist_run(report)
    assert ok is True
    assert {"split", "judge_version"} <= set(rec.insert_columns("eval_run"))
    (row,) = rec.run_rows
    assert row.get("split") == "dev"
    assert row.get("judge_version") == "judge-v1"
    # tolerance: an exact ratio 1/2; abs=1e-12 only absorbs float division
    assert row.get("paraphrase_stability") == pytest.approx(0.5, abs=1e-12)


@pytest.mark.criterion("C7")
def test_persist_run_writes_case_columns(make_case, graded, make_report, fake_db):
    # oracle: requirement 8 — each case row carries its five tag columns and four judge
    # columns (DEV_CASE_ROWS, by hand)
    report = _dev_report(make_case, graded, make_report)
    with fake_db() as rec:
        ok = _store().persist_run(report)
    assert ok is True
    assert set(NEW_CASE_COLUMNS) <= set(rec.insert_columns("eval_case_result"))
    got = _new_case_values(rec.case_rows)
    assert sorted(got) == ["a", "b", "c", "d"]
    for cid, values in got.items():
        expected = _expected_case_row(cid)
        for col in NEW_CASE_COLUMNS:
            if isinstance(expected[col], float):
                # tolerance: copied value; abs=1e-12 is the float-equality idiom
                assert values[col] == pytest.approx(expected[col], abs=1e-12), (cid, col)
            else:
                assert values[col] == expected[col], (cid, col)


@pytest.mark.criterion("C7")
def test_legacy_run_keeps_paraphrase_group_stability(make_case, graded, make_report,
                                                     fake_db):
    # oracle: requirement 8 — no intent tags, so paraphrase_stability falls back to the
    # paraphrase-group value: groups g1 (right, right), g2 (right, wrong), g3 (wrong,
    # wrong) give 2 stable of 3; the split is legacy and judge_version None
    outcomes = {"g1": (True, True), "g2": (True, False), "g3": (False, False)}
    results = [graded(make_case(f"{g}-{i}", ["kind:lookup"], paraphrase_group=g), ok)
               for g, pair in outcomes.items() for i, ok in enumerate(pair)]
    with fake_db() as rec:
        ok = _store().persist_run(make_report(results))
    assert ok is True
    (row,) = rec.run_rows
    assert row.get("split") == "legacy"
    assert "judge_version" in row and row["judge_version"] is None
    # tolerance: an exact ratio 2/3; abs=1e-12 only absorbs float division
    assert row.get("paraphrase_stability") == pytest.approx(2 / 3, abs=1e-12)


def _v1_report_dict(**extra) -> dict:
    """A report JSON as written before this change: no split, no judge keys."""
    return {
        "run_id": "old-run", "suite": "sql_derived", "suite_version": "v1",
        "system": "xbrlrag_full", "status": "OK", "n_planned": 2,
        "started_at": "2026-01-01T00:00:00+00:00",
        "finished_at": "2026-01-01T01:00:00+00:00",
        "metrics": {"n": 2, "n_correct": 1, "n_incorrect": 1, "accuracy": 0.5},
        "results": [
            {"case_id": "x", "kind": "lookup", "grade": "CORRECT", "passed": True,
             "tags": ["lookup", "family:terse", "key_source:fact_store"]},
            {"case_id": "y", "kind": "lookup", "grade": "INCORRECT", "passed": False,
             "tags": []},
        ],
        **extra,
    }


@pytest.mark.criterion("C7")
def test_old_report_json_imports_as_legacy(fake_db):
    # oracle: requirement 8 — absent split is legacy, judge_version None, tag columns
    # from each result's tags, judge columns None
    with fake_db() as rec:
        ok = persist_report_dict(_v1_report_dict(), store=_store())
    assert ok is True
    (row,) = rec.run_rows
    assert row.get("split") == "legacy"
    assert "judge_version" in row and row["judge_version"] is None
    got = _new_case_values(rec.case_rows)
    assert set(NEW_CASE_COLUMNS) <= set(rec.insert_columns("eval_case_result"))
    assert got["x"] == {**{c: None for c in NEW_CASE_COLUMNS},
                        "phrasing_family": "terse", "key_source": "fact_store"}
    assert got["y"] == {c: None for c in NEW_CASE_COLUMNS}


@pytest.mark.criterion("C7")
def test_report_json_with_split_and_bad_split(fake_db):
    # oracle: requirement 8 — a stated split is written as is; an unknown one raises
    # ValueError before any connection is opened
    with fake_db() as rec:
        ok = persist_report_dict(_v1_report_dict(split="validation", judge_version="j3"),
                                 store=_store())
    assert ok is True
    (row,) = rec.run_rows
    assert row.get("split") == "validation"
    assert row.get("judge_version") == "j3"
    with fake_db() as rec:
        with pytest.raises(ValueError):
            persist_report_dict(_v1_report_dict(split="holdout"), store=_store())
    assert rec.connects == 0


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
def test_reimported_json_writes_the_same_new_columns(make_case, graded, make_report,
                                                     fake_db):
    # oracle: requirement 9 — persist_report_dict(report.to_dict()) writes what
    # persist_run(report) writes for the new columns, and both match the hand values
    report = _dev_report(make_case, graded, make_report)
    with fake_db() as direct:
        assert _store().persist_run(report) is True
    with fake_db() as reimported:
        assert persist_report_dict(report.to_dict(), store=_store()) is True
    for rec in (direct, reimported):
        (row,) = rec.run_rows
        assert row.get("split") == "dev"
        assert row.get("judge_version") == "judge-v1"
        # tolerance: an exact ratio 1/2; abs=1e-12 only absorbs float division
        assert row.get("paraphrase_stability") == pytest.approx(0.5, abs=1e-12)
    a = _new_case_values(direct.case_rows)
    b = _new_case_values(reimported.case_rows)
    assert a == b
    assert a["b"] == _expected_case_row("b")
