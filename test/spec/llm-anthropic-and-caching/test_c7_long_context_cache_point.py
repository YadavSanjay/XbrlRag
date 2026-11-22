"""C7: the long-context arm uses a cache point (requirement 9).

The arm, ``filing_text``, the gateway and the prompt registry are real; the provider
adapter, the filing resolver and the filing store are fakes (conftest.py). Filing lines
use only ASCII letters, digits, spaces and ``|``, so ``json.dumps`` of the filing text is
the same with or without ``ensure_ascii``; the expected filing text is ``"\\n".join(lines)``
(long-context-arm's C1 rule for one paragraph per line). Filing lines and questions use
disjoint marker words, so neither is a substring of the other.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from prompts.prompt_registry import get_xbrl_registry

REPO_ROOT = Path(__file__).resolve().parents[3]


def _html(xhtml, lines: list[str]) -> str:
    return xhtml("".join(f"<p>{line}</p>" for line in lines))


def _assert_layout(messages: list[dict], lines: list[str], question: str) -> None:
    """Requirement 9's three-message layout for one call."""
    filing = json.dumps("\n".join(lines))
    assert [m.get("role") for m in messages] == ["system", "user", "user"]
    assert messages[1].get("cache_point") is True
    assert not messages[0].get("cache_point")
    assert not messages[2].get("cache_point")
    system, prefix, tail = (m.get("content") for m in messages)
    assert isinstance(prefix, str) and isinstance(tail, str)
    assert filing in prefix
    assert prefix.rstrip().endswith(filing)  # everything up to and including the filing
    assert question in tail
    assert question not in str(system)
    assert question not in prefix
    for line in lines:
        assert line not in str(system)
        assert line not in tail


@pytest.mark.criterion("C7")
def test_three_messages_with_the_filing_marked(offline_env, make_arm, make_case, xhtml):
    # oracle: requirement 9, hand-built filing — system, then a cache-point user message
    # ending with the json.dumps filing text, then a user message with the question
    lines = ["Zorblax Item 7 overview", "Zorblax revenue | 391,035"]
    arm, adapter = make_arm(_html(xhtml, lines))
    question = "What was Qvx total revenue in fiscal 2024?"
    answer = arm.answer(make_case(question))
    assert answer.error is None
    assert adapter.calls == 1
    _assert_layout(adapter.messages(0), lines, question)


@pytest.mark.criterion("C7")
def test_registry_resolves_long_context_at_v2():
    # oracle: requirement 9 — the long_context prompt moves to v2 and is the registry default
    manifest = yaml.safe_load(
        (REPO_ROOT / "prompts" / "templates" / "xbrl" / "registry.yaml").read_text(
            encoding="utf-8")
    )
    assert manifest["stages"]["long_context"]["default"]["version"] == "v2"
    assert get_xbrl_registry().resolve("long_context").version == "v2"
    assert get_xbrl_registry().resolve("long_context", provider="openai",
                                       series="gpt-5.6").version == "v2"


_WORD = st.text(
    alphabet=st.sampled_from(list("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")),
    min_size=1,
    max_size=10,
)
_LINE = st.lists(_WORD, min_size=1, max_size=5).map(lambda ws: "Zqx " + " ".join(ws))


@pytest.mark.criterion("C7")
@given(
    lines=st.lists(_LINE, min_size=1, max_size=6),
    tails=st.lists(_WORD, min_size=2, max_size=2, unique=True),
)
def test_two_questions_on_one_filing_share_the_cached_prefix(make_arm, make_case, xhtml,
                                                              lines, tails):
    # oracle: requirement 9 shared-prefix property — two cases on the same filing with
    # different questions (and ids) send byte-identical system and cache-point messages
    arm, adapter = make_arm(_html(xhtml, lines))
    q1 = f"What was the Wibble revenue in fiscal 2024 {tails[0]}?"
    q2 = f"What was the Wibble revenue in fiscal 2024 {tails[1]}?"
    assert arm.answer(make_case(q1, case_id="first")).error is None
    assert arm.answer(make_case(q2, case_id="second")).error is None
    assert adapter.calls == 2
    first, second = adapter.messages(0), adapter.messages(1)
    _assert_layout(first, lines, q1)
    _assert_layout(second, lines, q2)
    assert first[0] == second[0]
    assert first[1] == second[1]
    assert first[1]["content"].encode("utf-8") == second[1]["content"].encode("utf-8")
    assert first[2] != second[2]
