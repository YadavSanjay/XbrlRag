"""C6: the window estimate is the provider's count plus the text appended since, at 1.75."""

from __future__ import annotations

import json

import pytest
from ftv2_payloads import AAA_10K, AAA_DOCUMENT_URL, html_doc
from hypothesis import example, given, settings
from hypothesis import strategies as st

from src.eval.systems import frontier_sec_tools

READ = [("read_filing", {"cik": "1", "accession": AAA_10K})]
FIND = [("find_company", {"query": "AAA"})]
# read_filing returns 20,000 characters of the page by default: the tool result holds at least
# that many characters, and its envelope, the assistant message and any reserve add well under
# SLACK characters.
TEXT = 20_000
SLACK = 8_000
RATE = 1.75


def _sec(fake_sec):
    sec = fake_sec()
    sec.pages[AAA_DOCUMENT_URL] = (200, html_doc("MARKER" + "x" * 30_000).encode("utf-8"))
    return sec


def _refused(adapter, call: int) -> bool:
    """Whether the read_filing result sent on *call* is the window error."""
    results = [t for t in adapter.texts(call, "tool") if "read_filing" in t or "window" in t]
    assert results, adapter.texts(call, "tool")
    shown = results[-1]
    if "MARKER" in shown:
        assert json.loads(shown)["source_id"]
        return False
    assert json.loads(shown)["error"] == "window"
    return True


def _run(make_arm, make_case, fake_sec, script, usage, max_input_tokens):
    arm, adapter, _ = make_arm(script, transport=_sec(fake_sec), usage=usage,
                               max_input_tokens=max_input_tokens)
    answer = arm.answer(make_case())
    return answer, adapter


@pytest.mark.criterion("C6")
def test_the_rate_is_the_pilots_lowest():
    # oracle: requirement 6 — 1.75 characters per token, the pilot's lowest ratio
    # tolerance: a constant, compared exactly up to float representation
    assert frontier_sec_tools.CHARS_PER_TOKEN == pytest.approx(1.75, abs=1e-12)


@pytest.mark.criterion("C6")
def test_after_a_turn_with_usage(make_arm, make_case, fake_sec):
    # oracle: prompt_tokens + appended_chars / 1.75 — the turn reports 40,000 tokens and then
    # at least 20,000 and at most 28,000 characters are appended, so the estimate lies between
    # 40,000 + 11,428.6 and 40,000 + 16,000. A window 1 token short of the low end refuses the
    # result; one at the high end accepts it. Characters / 4 over the whole conversation
    # (about 8,000) would accept both.
    prompt = 40_000
    low = prompt + int(TEXT / RATE) - 1
    high = prompt + int((TEXT + SLACK) / RATE) + 1
    answer, adapter = _run(make_arm, make_case, fake_sec, [READ], [prompt], low)
    assert _refused(adapter, 1) is True
    assert answer.agent["stop"] == "window"
    assert adapter.params[1]["tool_choice"] == "none"
    answer, adapter = _run(make_arm, make_case, fake_sec, [READ], [prompt], high)
    assert _refused(adapter, 1) is False
    assert answer.agent["stop"] == "answered"


@pytest.mark.criterion("C6")
def test_the_latest_turns_count_replaces_the_earlier_ones(make_arm, make_case, fake_sec):
    # oracle: prompt_tokens + appended_chars / 1.75 with the second turn's 40,000 — the first
    # turn's 1,000 plus everything since it (under 30,000 characters, 17,143 tokens) would
    # accept the result at the low window
    prompt = 40_000
    low = prompt + int(TEXT / RATE) - 1
    high = prompt + int((TEXT + SLACK) / RATE) + 1
    answer, adapter = _run(make_arm, make_case, fake_sec, [FIND, READ], [1000, prompt], low)
    assert _refused(adapter, 2) is True
    assert answer.agent["stop"] == "window"
    answer, adapter = _run(make_arm, make_case, fake_sec, [FIND, READ], [1000, prompt], high)
    assert _refused(adapter, 2) is False
    assert answer.agent["stop"] == "answered"


@pytest.mark.criterion("C6")
def test_without_usage_all_characters_count_as_before_the_first_turn(make_arm, make_case,
                                                                     fake_sec):
    # oracle: all message characters / 1.75 — the turn reports 0 tokens, so the estimate is the
    # whole conversation at 1.75: at least (first prompt's content + 20,000) / 1.75, at most
    # SLACK characters more. Characters / 4 would accept the result at the low window.
    probe, probe_adapter, _ = make_arm()
    probe.answer(make_case())
    first = sum(len(str(m.get("content", ""))) for m in probe_adapter.messages(0))
    low = int((first + TEXT) / RATE) - 1
    high = int((first + TEXT + SLACK) / RATE) + 1
    answer, adapter = _run(make_arm, make_case, fake_sec, [READ], [0], low)
    assert _refused(adapter, 1) is True
    assert answer.agent["stop"] == "window"
    answer, adapter = _run(make_arm, make_case, fake_sec, [READ], [0], high)
    assert _refused(adapter, 1) is False
    assert answer.agent["stop"] == "answered"


WINDOW = 100_000


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
@settings(max_examples=15)
@given(first=st.integers(1000, WINDOW), second=st.integers(1000, WINDOW))
@example(first=1000, second=WINDOW - 1000)
def test_more_reported_tokens_never_turn_a_refusal_into_acceptance(make_arm, make_case,
                                                                   fake_sec, first, second):
    # oracle: metamorphic — with the window fixed, a refusal at the lower reported count stays a
    # refusal at the higher one; and by prompt_tokens + appended_chars / 1.75, a count above
    # WINDOW - 20,000 / 1.75 is refused and one at or below WINDOW - 28,000 / 1.75 accepted
    lower, higher = sorted((first, second))
    decisions = {}
    for prompt in (lower, higher):
        _, adapter = _run(make_arm, make_case, fake_sec, [READ], [prompt], WINDOW)
        decisions[prompt] = _refused(adapter, 1)
        if prompt > WINDOW - TEXT / RATE:
            assert decisions[prompt] is True, prompt
        if prompt <= WINDOW - (TEXT + SLACK) / RATE:
            assert decisions[prompt] is False, prompt
    if decisions[lower]:
        assert decisions[higher] is True
