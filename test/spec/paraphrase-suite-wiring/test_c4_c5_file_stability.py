"""Spec tests C4-C5 for paraphrase-suite-wiring: hashing, order independence, the file, and
paraphrase stability over a run (docs/specs/paraphrase-suite-wiring.md)."""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.case import EvalSuite
from src.eval.core.graders.dispatch import grade_case
from src.eval.core.report import CaseResult, run_stability
from src.eval.core.run_columns import intent_stability, run_split
from src.eval.suites.paraphrase_suite import build_paraphrase_suite, write_paraphrase_suite


def _build(kit, split):
    return build_paraphrase_suite(split, kit.suites(), kit.sets())


# --------------------------------------------------------------------------- C4
@pytest.mark.criterion("C4")
def test_c4_content_hash_is_the_case_list_hash(kit):
    # oracle: v2-suite-build rule 15 computed with hashlib/json in the conftest.
    for split in ("dev", "validation"):
        doc = _build(kit, split)
        assert doc["content_hash"] == kit.suite_hash(doc["cases"])
        assert doc["n_cases"] == len(doc["cases"])


@pytest.mark.criterion("C4")
@given(suites_rev=st.booleans(), sets_rev=st.booleans(),
       split=st.sampled_from(["dev", "validation"]))
def test_c4_property_input_order_irrelevant(kit, suites_rev, sets_rev, split):
    # oracle: the document rule: nothing depends on the order of suites or sets.
    suites, sets = kit.suites(), kit.sets()
    if suites_rev:
        suites.reverse()
    if sets_rev:
        sets.reverse()
    assert build_paraphrase_suite(split, suites, sets) == _build(kit, split)


@pytest.mark.criterion("C4")
def test_c4_write_and_load(kit, tmp_path):
    # oracle: the file rule (json.dumps with sort_keys, indent 2, trailing newline, UTF-8) and
    # EvalSuite.load / run_split as the run reads the file.
    for split in ("dev", "validation"):
        doc = _build(kit, split)
        path = tmp_path / f"{split}.json"
        assert write_paraphrase_suite(doc, path) == path
        expected = json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False,
                              allow_nan=False) + "\n"
        assert path.read_bytes() == expected.encode("utf-8")
        suite = EvalSuite.load(str(path))
        assert [c.id for c in suite.cases] == [c["id"] for c in doc["cases"]]
        assert [c.question for c in suite.cases] == [c["question"] for c in doc["cases"]]
        for case, record in zip(suite.cases, doc["cases"], strict=True):
            assert case.expected_value == record.get("expected_value")
            assert case.expected_rows == record.get("expected_rows")
            assert case.paraphrase_group == record["paraphrase_group"]
        assert run_split(suite.cases) == split


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
def test_c4_metamorphic_tampered_doc_refused(kit, tmp_path):
    # oracle: the write rule: a changed case without a new hash, or a wrong count, raises and
    # writes nothing.
    doc = _build(kit, "dev")
    doc["cases"][0]["question"] = "a question edited after the build"
    with pytest.raises(ValueError):
        write_paraphrase_suite(doc, tmp_path / "tampered.json")
    doc = _build(kit, "dev")
    doc["n_cases"] += 1
    with pytest.raises(ValueError):
        write_paraphrase_suite(doc, tmp_path / "count.json")
    assert not (tmp_path / "tampered.json").exists()
    assert not (tmp_path / "count.json").exists()


# --------------------------------------------------------------------------- C5
def _results(kit, doc, wrong=()):
    results = []
    for case in EvalSuite.from_dict(doc).cases:
        source = kit.source(case.id.split(":")[0])
        ans = kit.wrong_answer(source) if case.id in wrong else kit.right_answer(source)
        results.append(CaseResult.build(case, ans, grade_case(case, ans)))
    return results


@pytest.mark.criterion("C5")
def test_c5_stability_spans_template_and_paraphrase(kit):
    # oracle: plan §3.2 by hand. Dev intents: int-aaa (4 phrasings), int-bbb (2), int-ccc (2).
    dev = _build(kit, "dev")
    all_right = _results(kit, dev)
    assert all(r.passed for r in all_right)
    assert intent_stability(all_right) == pytest.approx(1.0, abs=0)  # 3 of 3, exact
    one_para = _results(kit, dev, wrong={"int-aaa-0002:buried_ask"})
    assert intent_stability(one_para) == pytest.approx(2 / 3, rel=1e-12)  # 2 of 3 stable
    assert run_stability(one_para) == pytest.approx(2 / 3, rel=1e-12)  # the run column
    both = _results(kit, dev, wrong={"int-bbb-0001", "int-bbb-0001:typo_prone"})
    assert intent_stability(both) == pytest.approx(1.0, abs=0)  # both phrasings agree
    val = _build(kit, "validation")
    # validation: int-aaa (3: one dev-case paraphrase, a template, its paraphrase), int-vvv (3)
    assert intent_stability(_results(kit, val)) == pytest.approx(1.0, abs=0)
    flip = _results(kit, val, wrong={"int-aaa-0001:sell_side_shorthand"})
    assert intent_stability(flip) == pytest.approx(1 / 2, rel=1e-12)  # 1 of 2 stable


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(pick=st.sampled_from(["int-aaa-0001:terse_pm", "int-aaa-0002:buried_ask",
                             "int-bbb-0001:typo_prone", "int-ccc-0001:conversational_retail"]))
def test_c5_metamorphic_one_flip_one_unstable_intent(kit, pick):
    # oracle: the definition: flipping one paraphrase of an all-right intent makes exactly that
    # intent unstable: 3 of 3 becomes 2 of 3.
    dev = _build(kit, "dev")
    assert intent_stability(_results(kit, dev)) == pytest.approx(1.0, abs=0)  # exact count
    assert intent_stability(_results(kit, dev, wrong={pick})) == pytest.approx(
        2 / 3, rel=1e-12)  # a ratio of small counts
