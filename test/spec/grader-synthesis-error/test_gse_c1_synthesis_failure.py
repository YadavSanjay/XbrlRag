"""C1: the synthesizer's fallback text is recognised exactly (docs/specs/grader-synthesis-error.md,
requirement 1).

E and R are copied from ``XbrlRagSynthesizer._degraded_response``; the contract test checks them
against that method's real output, so an edit of either sentence fails here.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.answer_parsing import synthesis_failure
from src.llm.gateway import LLMRefusalError
from src.retriever.xbrl.llm_xbrl_synthesizer import XbrlRagSynthesizer
from src.retriever.xbrl.plan_executor import ExecutionResult, ExecutionStatus
from src.retriever.xbrl.screen_disclosure import screen_disclosure_text

PROSE = st.text(alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd", "Zs"),
                                       whitelist_characters=".,;:()$%-\n"),
                max_size=200)


@pytest.mark.criterion("C1")
def test_each_fallback_alone_and_together(kit):
    # oracle: requirement 1 — E reads "error", R "refusal", E wins when both are present
    assert synthesis_failure(kit.E) == "error"
    assert synthesis_failure(kit.R) == "refusal"
    assert synthesis_failure(f"{kit.R}\n\n{kit.E}") == "error"
    assert synthesis_failure(kit.META_TEXT) == "error"


@pytest.mark.criterion("C1")
def test_a_failed_sub_query_mid_text(kit):
    # oracle: requirement 1 — a decomposed answer joins "[id] query\nsummary" blocks
    first = "[q1] What was DEVA's revenue?\nDEVA's revenue was $500 million."
    assert synthesis_failure(f"{first}\n\n[q2] And DEVB's?\n{kit.E}") == "error"
    assert synthesis_failure(f"{first}\n\n[q2] And DEVB's?\n{kit.R}") == "refusal"


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("text", [
    "DEVA's revenue was $500 million. (Note: automated fact-checking could not be completed "
    "(LLM_ERROR); treat figures as unverified - see metadata.)",
    "The CAGR computation could not be completed because the compute step returned an error.",
    "This answer could not be completed: the synthesis step returned a response that was not "
    "valid JSON.",
    "this answer could not be completed: the synthesis step returned a response that was not "
    "valid json. the retrieved facts are preserved in execution_result; please retry the query.",
    "This answer could not be completed.",
    "",
])
def test_near_misses_are_not_failures(text):
    # oracle: requirement 1 — exact, case-sensitive substrings only; a prefix, a lower-cased
    # copy and other "could not be completed" notes are not the fallback
    assert synthesis_failure(text) is None


def _degraded(error: Exception) -> str:
    """The real fallback summary for ``error``, with a screen disclosure appended."""
    synthesizer = object.__new__(XbrlRagSynthesizer)
    response = synthesizer._degraded_response(
        "Where does DEVS rank?", {"steps": []}, ExecutionResult(status=ExecutionStatus.OK),
        [], error)
    disclosure = screen_disclosure_text({
        "universe": "covered", "fiscal_year": 2022, "members": 33,
        "ranked": ["DEVA", "DEVS"], "exclusions": [], "period_ends": {},
        "period_ends_differ": False})
    return f"{response.answer_summary} {disclosure}"


@pytest.mark.criterion("C1")
def test_the_synthesizer_output_is_recognised():
    # oracle: the synthesizer's own fallback (requirement 1): an empty reply and an unparsable
    # one are the unusable-reply fallback, a provider refusal the refusal fallback
    assert synthesis_failure(_degraded(ValueError("LLM returned empty message content"))) \
        == "error"
    assert synthesis_failure(_degraded(json.JSONDecodeError("Expecting value", "x", 0))) \
        == "error"
    assert synthesis_failure(_degraded(LLMRefusalError("declined"))) == "refusal"


@pytest.mark.criterion("C1")
@given(before=PROSE, after=PROSE, which=st.sampled_from(["E", "R"]))
def test_inserting_a_fallback_anywhere(kit, before, after, which):
    # oracle: requirement 1 — prose holds neither sentence (its alphabet has no "_"), so it
    # reads None; with one inserted anywhere it reads that one's kind
    assert synthesis_failure(before + after) is None
    want = "error" if which == "E" else "refusal"
    assert synthesis_failure(before + getattr(kit, which) + after) == want
