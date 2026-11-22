"""Wiring of a screen's derived facts into the faithfulness verifier (unlocked work of
docs/specs/verifier-derived-disclosures.md, Wiring 5).

``_index_fact_steps`` keeps a screen row's rank, period end and parent screen; the registry
resolves the verifier prompt v2, whose placeholders are the ones ``_build_messages`` passes.
"""

from __future__ import annotations

import string

from prompts.prompt_registry import get_xbrl_registry
from src.retriever.xbrl.llm_xbrl_faithfulness_verifier import XbrlFaithfulnessVerifier

_SCREEN = {"step_id": "s1", "op": "screen", "status": "OK", "rows": [
    {"ticker": "AMZN", "fact_id": "6971", "value": 84_981_000_000.0, "unit": "USD",
     "fiscal_year": 2023, "rank": 1, "period_end": "2023-12-31"},
]}
_BREAKDOWN = {"step_id": "b1", "op": "breakdown", "status": "OK", "rows": [
    {"label": "Americas", "fact_id": "77", "value": 5.0e9, "unit": "USD", "rank": 1,
     "period_end": "2023-12-31"},
]}


def _placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_index_keeps_a_screen_rows_rank_period_end_and_parent():
    _, by_fact = XbrlFaithfulnessVerifier._index_fact_steps({"steps": [_SCREEN, _BREAKDOWN]})
    row = by_fact["6971"]
    assert row["rank"] == 1 and row["period_end"] == "2023-12-31"
    assert row["screen_step_id"] == "s1"
    # a breakdown row's evidence is unchanged (the spec covers screens only)
    assert "rank" not in by_fact["77"] and "period_end" not in by_fact["77"]


def test_step_id_citation_puts_the_summary_on_the_steps_own_entry():
    by_step, by_fact = XbrlFaithfulnessVerifier._index_fact_steps({"steps": [_SCREEN]})
    ev = XbrlFaithfulnessVerifier._resolve_fact_evidence(["s1", "6971"], by_step, by_fact)
    assert [e["step_id"] for e in ev] == ["s1", "s1:6971"]
    assert ev[0]["screen"]["ranked"] == ["AMZN"] and "screen" not in ev[1]


def test_registry_resolves_verifier_v2_with_the_call_sites_placeholders():
    # v3 is the default since verifier-period-evidence; v2 stays resolvable, unchanged
    prompt = get_xbrl_registry().resolve("faithfulness_verifier", provider="openai",
                                         series="gpt-5", version="v2")
    assert prompt.ref == "_base/faithfulness_verifier/v2"
    assert _placeholders(prompt.system.template) == {"llm_answer_format"}
    assert _placeholders(prompt.user.template) == {"statements_json"}
    assert '"screen"' in prompt.system.template and '"rank"' in prompt.system.template
    # v1 stays resolvable, unchanged
    v1 = get_xbrl_registry().resolve("faithfulness_verifier", provider="openai",
                                     series="gpt-5", version="v1")
    assert '"screen"' not in v1.system.template
