"""C4: the eval run record carries the union of the answers' served ids.

Spec: docs/specs/served-model-id.md, requirement 5 and criterion C4.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.runner import EvalRunner, RunnerConfig

M = "gpt-5-mini"
M2 = "gpt-5-nano"
SERVED = st.sampled_from(["gpt-5-mini-2025-08-07", "gpt-5-mini-2025-01-01", "snap-a",
                          "Snap-B", "gpt-5-nano-2025-08-07"])
SERVED_MAP = st.dictionaries(st.sampled_from([M, M2, "claude-opus-5-5"]),
                             st.lists(SERVED, min_size=1, max_size=3, unique=True),
                             max_size=3)


def _runner(**kwargs) -> EvalRunner:
    return EvalRunner(RunnerConfig(max_workers=1, progress_every=0, **kwargs))


# ---------------------------------------------------------------------------- point oracles


@pytest.mark.criterion("C4")
def test_config_served_models_is_the_union_of_the_answers(fake_system, make_cases):
    # oracle: the union written out by hand from the fakes' configured values
    system = fake_system({
        "a": {M: ["gpt-5-mini-2025-08-07"]},
        "b": {M: ["gpt-5-mini-2025-01-01", "gpt-5-mini-2025-08-07"],
              M2: ["gpt-5-nano-2025-08-07"]},
        "c": {},
    })
    report = _runner().run(make_cases(["a", "b", "c"]), system)
    assert report.config.get("served_models") == {
        M: ["gpt-5-mini-2025-01-01", "gpt-5-mini-2025-08-07"],
        M2: ["gpt-5-nano-2025-08-07"],
    }
    assert report.config["max_workers"] == 1  # existing config keys kept


@pytest.mark.criterion("C4")
def test_config_served_models_is_an_empty_dict_when_no_case_reported(fake_system,
                                                                      make_cases):
    # oracle: requirement 5: set on every run, {} when no case reported one
    report = _runner().run(make_cases(["a", "b"]), fake_system({}))
    assert "served_models" in report.config
    assert report.config["served_models"] == {}


@pytest.mark.criterion("C4")
def test_resume_merges_prior_served_models(fake_system, make_cases):
    # oracle: prior's {M: [s-08]} merged with the new case's {M: [s-01], M2: [n]},
    # written out by hand
    cases = make_cases(["a", "b"])
    prior = _runner().run(cases[:1], fake_system({"a": {M: ["gpt-5-mini-2025-08-07"]}}))
    prior.config["served_models"] = {M: ["gpt-5-mini-2025-08-07"]}  # what a resumed run read
    system = fake_system({"b": {M: ["gpt-5-mini-2025-01-01"],
                                M2: ["gpt-5-nano-2025-08-07"]}})
    report = _runner().run(cases, system, prior=prior)
    assert system.answered == ["b"]
    assert report.config.get("served_models") == {
        M: ["gpt-5-mini-2025-01-01", "gpt-5-mini-2025-08-07"],
        M2: ["gpt-5-nano-2025-08-07"],
    }


@pytest.mark.criterion("C4")
def test_aborted_run_carries_the_union_of_the_cases_that_ran(fake_system, make_cases):
    # oracle: with one worker and a $0.01 cap, case "a" (cost $1) runs and exhausts the
    # budget, so only "a"'s configured served ids are in the union
    system = fake_system({"a": {M: ["snap-a"]}, "b": {M: ["snap-b"]},
                          "c": {M2: ["snap-c"]}}, cost_usd=1.0)
    report = _runner(max_cost_usd=0.01).run(make_cases(["a", "b", "c"]), system)
    assert report.status == "ABORTED"
    assert system.answered == ["a"]
    assert report.config.get("served_models") == {M: ["snap-a"]}


# ---------------------------------------------------------------------------- properties


@pytest.mark.criterion("C4")
@given(maps=st.lists(SERVED_MAP, min_size=1, max_size=6), prior_map=st.one_of(
    st.none(), SERVED_MAP), n_prior=st.integers(0, 2))
def test_union_over_any_answers_and_prior(maps, prior_map, n_prior, fake_system, make_cases,
                                          union):
    # oracle: the union computed in the test (conftest.union_served) from the fakes'
    # configured served_models and the prior report's config
    n_prior = min(n_prior, len(maps))
    ids = [f"c{i}" for i in range(len(maps))]
    cases = make_cases(ids)
    by_id = dict(zip(ids, maps, strict=True))
    prior = None
    expected_parts = maps[n_prior:]
    if n_prior or prior_map is not None:
        prior = _runner().run(cases[:n_prior], fake_system({}))
        if prior_map is not None:
            prior.config["served_models"] = {k: list(v) for k, v in prior_map.items()}
            expected_parts = [*expected_parts, prior_map]
        else:
            prior.config.pop("served_models", None)  # an older report without the key
    report = _runner().run(cases, fake_system(by_id), prior=prior)
    assert "served_models" in report.config
    assert report.config["served_models"] == union(expected_parts)


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
def test_union_does_not_depend_on_case_order(fake_system, make_cases):
    # oracle: the same answers in reverse order give the same hand-written union
    served = {"a": {M: ["z-snap", "a-snap"]}, "b": {M: ["m-snap"]}, "c": {M2: ["n-snap"]}}
    expected = {M: ["a-snap", "m-snap", "z-snap"], M2: ["n-snap"]}
    forward = _runner().run(make_cases(["a", "b", "c"]), fake_system(served))
    backward = _runner().run(make_cases(["c", "b", "a"]), fake_system(served))
    assert forward.config.get("served_models") == expected
    assert backward.config.get("served_models") == expected
