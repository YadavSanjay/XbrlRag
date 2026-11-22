"""C6: long_context_multi sends the k resolved filings, in one fixed order."""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from prompts.prompt_registry import get_xbrl_registry
from src.eval.systems.long_context import filing_text, split_user_template
from src.llm.gateway import resolve_model_ref

A23 = "AAA/2023/10-K/canonical.htm"
A24 = "AAA/2024/10-K/canonical.htm"
B24 = "BBB/2024/10-K/canonical.htm"
PAIR_KEYS = {("AAA", 2024): A24, ("BBB", 2024): B24}


def _entry(ticker: str, fiscal_year: int, html: str) -> str:
    # oracle: requirement 9's format, written out here
    return json.dumps(
        {"ticker": ticker, "fiscal_year": fiscal_year, "text": filing_text(html)},
        ensure_ascii=False,
    )


@pytest.mark.criterion("C6")
def test_pair_of_filers_sends_both_in_target_order(make_multi, make_case, doc, router_reply):
    # oracle: requirement 9, hand-built filings — AAA sorts before BBB, whatever the question says
    a_html, b_html = doc("Zqalpha revenue 7"), doc("Zqbeta revenue 9")
    arm, _, adapter, resolver, store = make_multi(
        router_text=router_reply(["BBB", "AAA"], ["fiscal 2024"]),
        keys=PAIR_KEYS,
        objects={A24: a_html, B24: b_html},
    )
    case = make_case("Which had more revenue in 2024, BBB or AAA?", ticker=None, fy=None)
    answer = arm.answer(case)

    assert answer.error is None
    assert adapter.calls == 1
    assert resolver.calls == [("AAA", 2024), ("BBB", 2024)]
    assert store.reads == [A24, B24]
    filing_message, question_message = adapter.messages()[1:3]
    text = filing_message["content"]
    assert filing_message.get("cache_point") is True
    assert not question_message.get("cache_point")
    assert case.question in question_message["content"]
    assert case.question not in text
    first, second = _entry("AAA", 2024, a_html), _entry("BBB", 2024, b_html)
    assert first in text
    assert second in text
    assert text.index(first) < text.index(second)
    assert answer.numeric_values == [7.0]
    assert answer.fact_ids == []
    assert answer.chunk_ids == []


@pytest.mark.criterion("C6")
def test_cross_year_sends_each_year(make_multi, make_case, doc, router_reply):
    # oracle: requirement 9 — one filer, two years, earlier first
    a23, a24 = doc("Zqearlier"), doc("Zqlater")
    arm, _, adapter, _, _ = make_multi(
        router_text=router_reply(["AAA"], ["fiscal 2024", "fiscal 2023"]),
        keys={("AAA", 2023): A23, ("AAA", 2024): A24},
        objects={A23: a23, A24: a24},
    )
    answer = arm.answer(make_case())
    assert answer.error is None
    text = adapter.messages()[1]["content"]
    assert text.index(_entry("AAA", 2023, a23)) < text.index(_entry("AAA", 2024, a24))
    assert answer.selection["k"] == 2


@pytest.mark.criterion("C6")
def test_one_filing_is_still_a_list(make_multi, make_case, doc, router_reply):
    # oracle: requirement 9 — k = 1 is the same format, not a special case
    html = doc("Zqonly")
    arm, _, adapter, _, _ = make_multi(
        router_text=router_reply(["AAA"], ["fiscal 2024"]), objects={A24: html}
    )
    assert arm.answer(make_case()).error is None
    assert _entry("AAA", 2024, html) in adapter.messages()[1]["content"]


@pytest.mark.criterion("C6")
def test_its_own_stage_and_prompt(make_multi, make_case):
    # oracle: requirement 9 — stage long_context_multi, a v1 template with one cache point; the
    # oracle's stage stays at its own version
    arm, _, adapter, _, _ = make_multi()
    arm.answer(make_case())
    assert adapter.stages == ["long_context_multi"]
    ref = resolve_model_ref("gpt-5.6-sol")
    registry = get_xbrl_registry()
    assert "long_context_multi" in registry.stages()
    resolved = registry.resolve("long_context_multi", provider=ref.provider, series=ref.series)
    assert resolved.version == "v1"
    assert adapter.system_text() == resolved.system.format()
    assert resolved.system_text.strip() != ""
    filing, question = split_user_template(resolved.user)
    assert filing.variables == {"filing"}
    assert question.variables == {"question"}


@pytest.mark.criterion("C6")
@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"kind": "screen", "ticker": None, "fy": 2024}, id="screen"),
        pytest.param({"universe": ["AAA", "BBB"]}, id="universe"),
    ],
)
def test_a_case_ranging_over_filers_is_excluded_before_the_router(make_multi, make_case, kwargs):
    # oracle: requirement 10 — the filers are not named, so there are no k filings to send
    arm, router_adapter, adapter, _, _ = make_multi()
    answer = arm.answer(make_case(**kwargs))
    assert answer.error.startswith("excluded: ")
    assert router_adapter.calls == 0
    assert adapter.calls == 0


@pytest.mark.criterion("C6")
@pytest.mark.parametrize("ticker, fy", [(None, None), ("ZZZ", 1999)])
def test_the_answer_key_fields_are_not_read(make_multi, make_case, router_reply, ticker, fy):
    # oracle: requirement 10 — a pairwise case has no single ticker, and the fields choose nothing
    arm, _, adapter, resolver, _ = make_multi(router_text=router_reply(["AAA"], ["fiscal 2024"]))
    assert arm.answer(make_case(ticker=ticker, fy=fy)).error is None
    assert resolver.calls == [("AAA", 2024)]
    assert adapter.calls == 1


@pytest.mark.criterion("C6")
def test_a_filing_with_no_key_is_left_out_and_listed(make_multi, make_case, doc, router_reply):
    # oracle: requirement 11 — BBB 2024 has no key; the call goes ahead on AAA alone
    html = doc("Zqalpha")
    arm, _, adapter, _, _ = make_multi(
        router_text=router_reply(["AAA", "BBB"], ["fiscal 2024"]),
        keys={("AAA", 2024): A24},
        objects={A24: html},
    )
    answer = arm.answer(make_case())
    assert answer.error is None
    assert adapter.calls == 1
    assert _entry("AAA", 2024, html) in adapter.messages()[1]["content"]
    assert answer.selection["missing"] == [{"ticker": "BBB", "fiscal_year": 2024}]
    assert answer.selection["k"] == 1


@pytest.mark.criterion("C6")
def test_a_key_not_in_the_store_is_left_out_and_listed(make_multi, make_case, doc, router_reply):
    # oracle: requirement 11 — BBB 2024 has a key but no object
    arm, _, adapter, _, _ = make_multi(
        router_text=router_reply(["AAA", "BBB"], ["fiscal 2024"]),
        keys=PAIR_KEYS,
        objects={A24: doc("Zqalpha")},
    )
    answer = arm.answer(make_case())
    assert answer.error is None
    assert adapter.calls == 1
    assert answer.selection["missing"] == [{"ticker": "BBB", "fiscal_year": 2024}]


@pytest.mark.criterion("C6")
def test_no_key_at_all_is_a_refusal(make_multi, make_case, router_reply):
    # oracle: requirement 11 — no key resolved, so the resolver's years may be the error
    arm, _, adapter, _, _ = make_multi(
        router_text=router_reply(["AAA", "BBB"], ["fiscal 2024"]), keys={}
    )
    answer = arm.answer(make_case())
    assert answer.refused is True
    assert answer.error is None
    assert adapter.calls == 0
    assert answer.selection["status"] == "no_filing"


@pytest.mark.criterion("C6")
def test_keys_but_no_objects_is_excluded(make_multi, make_case, router_reply):
    # oracle: requirement 11 — every key resolved and none is in the store: the corpus's gap
    arm, _, adapter, _, _ = make_multi(
        router_text=router_reply(["AAA", "BBB"], ["fiscal 2024"]), keys=PAIR_KEYS, objects={}
    )
    answer = arm.answer(make_case())
    assert answer.error.startswith("excluded: ")
    assert adapter.calls == 0
    assert answer.selection["status"] == "not_in_store"


@pytest.mark.criterion("C6")
def test_undecided_is_a_refusal(make_multi, make_case, router_reply):
    # oracle: requirement 2 — one unresolved company among two refuses; no partial pair
    arm, _, adapter, resolver, _ = make_multi(
        router_text=router_reply(["AAA", "Nobody Corp"], ["fiscal 2024"])
    )
    answer = arm.answer(make_case())
    assert answer.refused is True
    assert answer.error is None
    assert adapter.calls == 0
    assert resolver.calls == []
    assert answer.selection["status"] == "undecided"


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
@given(
    companies=st.permutations(["AAA", "BBB"]),
    periods=st.permutations(["fiscal 2023", "fiscal 2024"]),
)
def test_mention_order_never_changes_the_filing_message(
    make_multi, make_case, doc, router_reply, companies, periods
):
    # oracle: requirement 9 — the prefix depends only on the set of filings, so cases on one
    # set can share the provider's cache
    keys = {
        ("AAA", 2023): A23, ("AAA", 2024): A24,
        ("BBB", 2023): "BBB/2023/10-K/canonical.htm", ("BBB", 2024): B24,
    }
    objects = {key: doc(f"Zqfiling {key}") for key in keys.values()}
    base, _, base_adapter, _, _ = make_multi(
        router_text=router_reply(["AAA", "BBB"], ["fiscal 2023", "fiscal 2024"]),
        keys=keys, objects=objects,
    )
    arm, _, adapter, _, _ = make_multi(
        router_text=router_reply(list(companies), list(periods)), keys=keys, objects=objects
    )
    base.answer(make_case())
    arm.answer(make_case())
    assert adapter.messages()[:2] == base_adapter.messages()[:2]
