"""C10: a held-out build reports counts only, on the console and in the logs.

Oracle: requirement 9 of docs/specs/hf-suite-build.md, restated in conftest (``leaks``): no
fixture ticker, CIK (padded or not), company name, concept, intent or case id, question, no
decimal number and no run of seven digits outside a ``sha256:`` token.
"""

from __future__ import annotations

import logging

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.suites.heldout_build import heldout_summary


def _secrets(build) -> list[str]:
    """The questions, case ids and keyed values of a build: none may be printed or logged."""
    cases = build["heldout_filers"]["cases"]
    out = [c["question"] for c in cases] + [c["id"] for c in cases]
    for c in cases:
        if c.get("expected_value") is not None:
            v = c["expected_value"]
            out += [repr(v), repr(float(v))]
    return out


def _builds(kit):
    sec = kit.pinned_sec(kit.companyfacts_docs())
    w = kit.witness("none")
    return [kit.hf_build(["DEVD"]), kit.hf_compound(["DEVD"]),
            kit.hf_build(["DEVB"], quotas={"lookup": 100}, wit=w, sec=sec),
            kit.hf_compound(["DEVB"], quotas={"compound.leader_growth": 100}, wit=w, sec=sec)]


@pytest.mark.criterion("C10")
def test_summary_states_counts_and_nothing_else(kit):
    # oracle: requirement 9, restated in conftest; the counts and hash read off each build.
    for build in _builds(kit):
        lines = heldout_summary(build)
        assert lines and all(isinstance(line, str) for line in lines)
        text = "\n".join(lines)
        assert kit.leaks(lines, extra=_secrets(build)) == []
        assert str(build["heldout_filers"]["n_cases"]) in text
        assert build["heldout_filers"]["content_hash"] in text
        for shape, n in build["manifest"]["intents"]["selected"].items():
            if n:
                assert shape in text
        if "key_resolutions" in build["manifest"]:
            assert "unused" in text


@pytest.mark.criterion("C10")
def test_logs_name_no_filer_value_or_question(kit, caplog):
    # oracle: requirement 9: nothing logged at any level during the builds and their
    # refusals names a ticker, CIK, company, value or question.
    caplog.set_level(logging.DEBUG)
    builds = _builds(kit)
    errors = []
    for bad in ([], ["DEVD", "DEVZ"]):
        with pytest.raises(ValueError) as info:
            kit.hf_build(bad)
        errors.append(str(info.value))
    messages = [r.getMessage() for r in caplog.records] + errors
    secrets = [s for b in builds for s in _secrets(b)] + ["DEVZ"]
    forbidden = [*kit.TICKERS, *kit.CIK.values(), *kit.NAMES.values(), *secrets]
    assert [f for m in messages for f in forbidden if f in m] == []
    assert all(b["heldout_filers"]["n_cases"] > 0 for b in builds)


@pytest.mark.criterion("C10")
@settings(max_examples=10, deadline=None)
@given(heldout=st.sets(st.sampled_from(("DEVA", "DEVB", "DEVC", "DEVD", "DEVE")),
                       min_size=1))
def test_any_heldout_set_summary_is_counts_only(kit, heldout):
    # oracle: requirement 9, restated in conftest, for any held-out set.
    quotas = {"lookup": 2, "screen.top": 2, "peer_relative": 2, "compute.ratio": 2}
    build = kit.hf_build(sorted(heldout), quotas=quotas)
    lines = heldout_summary(build)
    assert lines
    assert kit.leaks(lines, extra=_secrets(build)) == []
