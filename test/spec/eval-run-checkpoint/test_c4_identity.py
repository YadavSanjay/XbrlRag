"""C4: the suite hash and the resume identity (requirements 7-8)."""

from __future__ import annotations

import hashlib
import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.checkpoint.run import (
    ResumeRefused,
    check_resume,
    resume_identity,
    suite_hash,
)

FIELDS = ("suite_hash", "system", "model", "llm_profile", "git_sha", "settings")


def _oracle_hash(cases) -> str:
    # requirement 7, computed here with hashlib and json alone
    docs = [c.to_dict() for c in sorted(cases, key=lambda c: c.id)]
    text = json.dumps(docs, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cases(kit):
    return [kit.make_case("b", ticker="BBB", fy=2023), kit.make_case("a", ticker="AAA", fy=2024),
            kit.make_case("c")]


@pytest.mark.criterion("C4")
def test_suite_hash_is_sha256_of_the_cases_in_id_order(kit):
    # oracle: requirement 7 — hashlib over the canonical JSON
    cases = _cases(kit)
    assert suite_hash(cases) == _oracle_hash(cases)
    assert suite_hash(cases).startswith("sha256:") and len(suite_hash(cases)) == 7 + 64


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(order=st.permutations([0, 1, 2]), bump=st.sampled_from(["value", "question", "fy"]))
def test_suite_hash_ignores_order_and_sees_any_change(kit, order, bump):
    # oracle: requirement 7 — the selection, not its order; any field change shows
    cases = _cases(kit)
    shuffled = [cases[i] for i in order]
    assert suite_hash(shuffled) == suite_hash(cases)
    changed = [kit.make_case(c.id, ticker=c.ticker, fy=c.fy) for c in cases]
    if bump == "value":
        changed[0].expected_value = 2.0
    elif bump == "question":
        changed[0].question = "another question"
    else:
        changed[0].fy = 2020
    assert suite_hash(changed) != suite_hash(cases)


@pytest.mark.criterion("C4")
def test_resume_identity_has_the_six_fields(kit):
    # oracle: requirement 7 — the dict written out by hand
    cases = _cases(kit)
    got = resume_identity(cases, system="s", model="m", llm_profile="p", git_sha="abc",
                          settings={"cache_groups": "filing"})
    assert got == {"suite_hash": _oracle_hash(cases), "system": "s", "model": "m",
                   "llm_profile": "p", "git_sha": "abc", "settings": {"cache_groups": "filing"}}


@pytest.mark.criterion("C4")
def test_matching_identity_passes_and_each_differing_field_is_named(kit):
    # oracle: requirement 8 — equal passes; one field changed refuses, naming it
    base = kit.identity_for(_cases(kit), "s")
    assert check_resume({"resume_identity": base}, dict(base)) is None
    other = {"suite_hash": "sha256:" + "0" * 64, "system": "t", "model": "model-2",
             "llm_profile": "openai", "git_sha": "fff0000", "settings": {"cache_groups": "filing"}}
    for field in FIELDS:
        changed = {**base, field: other[field]}
        with pytest.raises(ResumeRefused) as err:
            check_resume({"resume_identity": base}, changed)
        assert field in str(err.value)
        assert isinstance(err.value, ValueError)


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(fields=st.sets(st.sampled_from(FIELDS), min_size=1))
def test_every_differing_field_is_named_at_once(kit, fields):
    # oracle: requirement 8 — the message names every differing field
    base = kit.identity_for(_cases(kit), "s")
    changed = {**base, **{f: f"changed-{f}" for f in fields}}
    with pytest.raises(ResumeRefused) as err:
        check_resume({"resume_identity": base}, changed)
    for field in fields:
        assert field in str(err.value)


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("field", ["git_sha", "suite_hash"])
def test_an_unprovable_identity_is_refused_even_when_equal(kit, field):
    # oracle: requirement 8 — None on both sides cannot prove the same code and cases
    ident = {**kit.identity_for(_cases(kit), "s"), field: None}
    with pytest.raises(ResumeRefused):
        check_resume({"resume_identity": ident}, dict(ident))


@pytest.mark.criterion("C4")
def test_a_stored_run_without_identity_is_refused(kit):
    # oracle: requirement 8 — a run stored before this change cannot be resumed
    with pytest.raises(ResumeRefused):
        check_resume({"llm_profile": "deepseek"}, kit.identity_for(_cases(kit), "s"))
