"""C5: the ten case columns and the two run columns (requirements 5 and 6)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.run_columns import MixedSplitError, case_columns, run_columns

SPLITS = ["dev", "validation", "heldout_filers", "external", "temporal", "legacy"]
JUDGE_NONE = {"judge_correctness": None, "judge_completeness": None,
              "judge_citation_support": None, "judge_unsupported_claims": None}
TAGS = ["split:validation", "intent:rev-aapl-2024", "family:conversational",
        "template:lookup_v3", "key_source:sec_frames", "key_status:single-source",
        "kind:lookup"]


@pytest.mark.criterion("C5")
def test_case_columns_merge_tags_and_judge_scores(make_case, graded):
    # oracle: requirement 5 — the six tag fields of the case plus the four judge
    # columns of grade.judge_scores, written out by hand
    result = graded(make_case("a", TAGS), True,
                    {"correctness": 0.75, "completeness": 0.5, "citation_support": 0.25,
                     "unsupported_claims": 3})
    out = case_columns(result)
    assert set(out) == {"split", "intent_id", "phrasing_family", "template_id",
                        "key_source", "key_status", *JUDGE_NONE}
    assert {k: out[k] for k in ("split", "intent_id", "phrasing_family", "template_id",
                                "key_source", "key_status")} == {
        "split": "validation", "intent_id": "rev-aapl-2024",
        "phrasing_family": "conversational", "template_id": "lookup_v3",
        "key_source": "sec_frames", "key_status": "single-source"}
    # tolerance: copied values; abs=1e-12 is the float-equality idiom
    assert out["judge_correctness"] == pytest.approx(0.75, abs=1e-12)
    assert out["judge_completeness"] == pytest.approx(0.5, abs=1e-12)
    assert out["judge_citation_support"] == pytest.approx(0.25, abs=1e-12)
    assert out["judge_unsupported_claims"] == 3


@pytest.mark.criterion("C5")
def test_case_columns_without_judge_scores(make_case, graded):
    # oracle: requirement 5 — no judge_scores attribute, or None, gives None judge columns
    for scores in ("absent", None):
        out = case_columns(graded(make_case("a", ["family:terse"]), False, scores))
        assert out == {"split": None, "intent_id": None, "phrasing_family": "terse",
                       "template_id": None, "key_source": None, "key_status": None,
                       **JUDGE_NONE}


@pytest.mark.criterion("C5")
def test_run_columns_split_sources(make_case, graded, make_report):
    # oracle: requirement 6 — config split wins; else the results' split; empty
    # results and no config give legacy
    dev = [graded(make_case("a", ["split:dev"])), graded(make_case("b", ["split:dev"]))]
    assert run_columns(make_report(dev, config={"split": "dev"})) == {
        "split": "dev", "judge_version": None}
    assert run_columns(make_report(dev)) == {"split": "dev", "judge_version": None}
    assert run_columns(make_report([])) == {"split": "legacy", "judge_version": None}
    assert run_columns(make_report([], config={"split": "external"})) == {
        "split": "external", "judge_version": None}


@pytest.mark.criterion("C5")
def test_run_columns_judge_version(make_case, graded, make_report):
    # oracle: requirement 6 — judge_version from config when non-empty, else None
    res = [graded(make_case("a", []))]
    assert run_columns(make_report(res, config={"judge_version": "judge-v2"})) == {
        "split": "legacy", "judge_version": "judge-v2"}
    assert run_columns(make_report(res, config={"judge_version": ""})) == {
        "split": "legacy", "judge_version": None}


@pytest.mark.criterion("C5")
def test_run_columns_rejects_bad_config_split(make_case, graded, make_report):
    # oracle: requirement 6 — an unknown config split is a ValueError; one that
    # disagrees with the results is a MixedSplitError
    dev = [graded(make_case("a", ["split:dev"]))]
    with pytest.raises(ValueError):
        run_columns(make_report(dev, config={"split": "holdout"}))
    with pytest.raises(MixedSplitError):
        run_columns(make_report(dev, config={"split": "validation"}))


@pytest.mark.criterion("C5")
@given(
    split=st.one_of(st.none(), st.sampled_from(SPLITS)),
    intent=st.one_of(st.none(), st.sampled_from(["i1", "i-2", "x:y"])),
    family=st.one_of(st.none(), st.sampled_from(["terse", "formal", "llm_para"])),
    status=st.one_of(st.none(), st.sampled_from(["verified", "single-source"])),
    corr=st.one_of(st.none(), st.floats(0.0, 1.0, allow_nan=False)),
    claims=st.one_of(st.none(), st.integers(0, 9)),
    correct=st.booleans(),
)
def test_case_columns_property(make_case, graded, split, intent, family, status, corr,
                               claims, correct):
    # oracle: requirements 1, 4 and 5 — each rendered tag and score comes back in its
    # own column, everything else None, whatever the grade
    tags = [f"{p}:{v}" for p, v in (("split", split), ("intent", intent),
                                    ("family", family), ("key_status", status)) if v]
    scores = {k: v for k, v in (("correctness", corr), ("unsupported_claims", claims))
              if v is not None}
    out = case_columns(graded(make_case("a", tags), correct, scores))
    assert out["split"] == split
    assert out["intent_id"] == intent
    assert out["phrasing_family"] == family
    assert out["key_status"] == status
    assert out["template_id"] is None
    assert out["key_source"] is None
    if corr is None:
        assert out["judge_correctness"] is None
    else:
        # tolerance: copied value; abs=1e-12 is the float-equality idiom
        assert out["judge_correctness"] == pytest.approx(corr, abs=1e-12)
    assert out["judge_unsupported_claims"] == claims
    assert out["judge_completeness"] is None
    assert out["judge_citation_support"] is None
