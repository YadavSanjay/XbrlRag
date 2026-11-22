"""C8: the summary counts transitions and never lists a held-out run's cases."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.regrade import regrade_report, regrade_summary

# H-F, H-X and set B (external), H-T (temporal): requirement 10.
HELDOUT = ("heldout_filers", "external", "temporal")


def _regrade(source, cases):
    return regrade_report(
        source, cases, suite="s", suite_version="2", corpus_fingerprint=None
    )


def _run(split, lookup_case, answer, build_report, as_json_source, rekey):
    # Before: m1 CORRECT, m2 INCORRECT, m3 INCORRECT, m4 CORRECT, m5 REFUSED.
    # Keys of m2 and m3 move onto the stated values, m4's moves off: after m1 CORRECT,
    # m2 CORRECT, m3 CORRECT, m4 INCORRECT, m5 REFUSED.
    cases = [lookup_case(f"m{i}", 100.0 * i, split=split) for i in range(1, 6)]
    stated = [[100.0], [999.0], [777.0], [400.0], []]
    pairs = [
        (c, answer(v, refused=not v, cited=bool(v))) for c, v in zip(cases, stated, strict=True)
    ]
    source = as_json_source(build_report(pairs, split=split))
    new_cases = [cases[0], rekey(cases[1], 999.0), rekey(cases[2], 777.0),
                 rekey(cases[3], 401.0), cases[4]]
    return source, _regrade(source, new_cases)


@pytest.mark.criterion("C8")
@pytest.mark.parametrize("split", HELDOUT)
def test_heldout_summary_has_aggregates_only(lookup_case, answer, build_report,
                                             as_json_source, rekey, split):
    # oracle: requirement 10 — the hand count of _run, with no per-case list
    source, out = _run(split, lookup_case, answer, build_report, as_json_source, rekey)
    summary = regrade_summary(source, out)
    assert summary["split"] == split
    assert summary["n"] == 5
    assert summary["transitions"] == {"INCORRECT -> CORRECT": 2, "CORRECT -> INCORRECT": 1}
    assert "changed" in summary and summary["changed"] is None


@pytest.mark.criterion("C8")
def test_dev_summary_hand_count(lookup_case, answer, build_report, as_json_source, rekey):
    # oracle: hand count of the five cases in _run
    source, out = _run("dev", lookup_case, answer, build_report, as_json_source, rekey)
    summary = regrade_summary(source, out)
    assert summary["split"] == "dev"
    assert summary["n"] == 5
    assert summary["transitions"] == {"INCORRECT -> CORRECT": 2, "CORRECT -> INCORRECT": 1}
    assert summary["changed"] == [
        {"case_id": "m2", "kind": "lookup", "before": "INCORRECT", "after": "CORRECT"},
        {"case_id": "m3", "kind": "lookup", "before": "INCORRECT", "after": "CORRECT"},
        {"case_id": "m4", "kind": "lookup", "before": "CORRECT", "after": "INCORRECT"},
    ]


@pytest.mark.criterion("C8")
def test_missing_stored_grade_reads_none(lookup_case, answer, build_report, as_json_source):
    # oracle: requirement 10 — a result with no stored grade counts as "(none)" -> after
    case = lookup_case("g1", 5.0)
    source = as_json_source(build_report([(case, answer([5.0]))]))
    del source["results"][0]["grade"]
    out = _regrade(source, [case])
    summary = regrade_summary(source, out)
    assert summary["transitions"] == {"(none) -> CORRECT": 1}
    assert out.config["regrade"]["n_grade_changed"] == 1


@pytest.mark.criterion("C8")
@given(split=st.sampled_from(["dev", "validation", "legacy", *HELDOUT]))
def test_per_case_list_hidden_exactly_for_heldout(lookup_case, answer, build_report,
                                                  as_json_source, rekey, split):
    # oracle: requirement 10 — aggregates always; per-case only outside the held-out splits
    source, out = _run(split, lookup_case, answer, build_report, as_json_source, rekey)
    summary = regrade_summary(source, out)
    assert summary["split"] == split
    assert summary["transitions"] == {"INCORRECT -> CORRECT": 2, "CORRECT -> INCORRECT": 1}
    if split in ("heldout_filers", "external", "temporal"):
        assert summary["changed"] is None
    else:
        assert [c["case_id"] for c in summary["changed"]] == ["m2", "m3", "m4"]
