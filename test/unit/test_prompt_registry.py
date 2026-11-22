"""Unit tests for the provider/series/version prompt registry."""
import pytest

from prompts.prompt_registry import PromptRegistry, get_xbrl_registry


@pytest.fixture
def reg():
    return get_xbrl_registry()


# Default prompt version per stage: plan_generator v2 adds the `breakdown` op
# (docs/specs/breakdown-op.md), v3 keeps several qualifiers in one lookup, v4 lists
# the valid derived metric names (docs/specs/screen-metric-names.md), v5 puts only
# the members a question names into hints, one member per hint, v6 adds the screen
# `universe` (docs/specs/screen-universe.md), v7 the `aggregate` op and the router's
# UNIVERSE / AGGREGATE hints (docs/specs/screen-aggregates.md);
# query_decomposer v2 / intent_router v2 keep one metric with several qualifiers
# as one need / one concept phrase; intent_router v3 routes a total/median over a named
# group to a screen and never lifts a group as a company; synthesizer v4 states an
# aggregate step's value, v5 a defined total's disclosure and parts
# (docs/specs/s4-lookup-deferred-revenue-debt.md); frontier_sec_tools v2
# (docs/specs/frontier-tools-v2.md); plan_generator v8 / synthesizer v6 /
# query_decomposer v3 the cagr op and ratio cues (docs/specs/plan-cagr-and-ratios.md);
# plan_generator v9 reads an unqualified share count as shares outstanding; intent_router v4
# keeps a range of years as one period mention (docs/specs/fiscal-year-range-split.md).
# synthesizer v7 states a compute or cagr year-length note verbatim
# (docs/specs/period-year-length-answers.md), v8 a Q4 lookup's note
# (docs/specs/q4-lookup.md). faithfulness_verifier v2 shows a screen's rank,
# period_end and screen summary as evidence (docs/specs/verifier-derived-disclosures.md), v3 a
# lookup, compute or aggregate step's period (docs/specs/verifier-period-evidence.md).
# plan_generator v10 names a nested ranking with `error_code`, and query_decomposer v4 splits
# it on PLANNER_FEEDBACK "nested_ranking" (the pipeline's one re-decomposition on that refusal).
# plan_generator v11 sets a screen over named companies to their ticker list
# (docs/specs/named-company-screen.md).
# query_decomposer v5, intent_router v5 and plan_generator v12 read the "TICKER (#k)"
# rank labels of a <<TOP:id:N>> substitution (docs/specs/ranked-top-injection.md).
# plan_generator v13 applies the label rule only to a question about one position.
DEFAULT_VERSIONS = {
    "plan_generator": "v13", "query_decomposer": "v5", "intent_router": "v5", "synthesizer": "v8",
    "long_context": "v2", "long_context_multi": "v1", "frontier_sec_tools": "v2",
    "faithfulness_verifier": "v3",
}


def test_all_stages_resolve_to_base_for_openai_gpt5(reg):
    for stage in reg.stages():
        p = reg.resolve(stage, provider="openai", series="gpt-5")
        assert p.ref == f"_base/{stage}/{DEFAULT_VERSIONS.get(stage, 'v1')}"
        assert p.system.template.strip()
        assert p.user.template.strip()


def test_unknown_provider_series_inherits_base(reg):
    # No anthropic/claude-4 override dir exists yet -> falls back to _base.
    p = reg.resolve("intent_router", provider="anthropic", series="claude-4")
    assert p.ref == f"_base/intent_router/{DEFAULT_VERSIONS['intent_router']}"
    assert p.provider == "anthropic"  # the ref carries the requested provider


def test_content_hash_is_stable_and_content_based(reg):
    a = reg.resolve("synthesizer", provider="openai", series="gpt-5")
    b = reg.resolve("synthesizer", provider="anthropic", series="claude-4")
    # Same underlying _base templates -> identical content hash regardless of caller.
    assert a.content_hash == b.content_hash
    assert len(a.content_hash) == 12


def test_signature_shape(reg):
    p = reg.resolve("intent_router", provider="openai", series="gpt-5")
    sig = p.signature()
    assert sig["version"] == p.content_hash
    v = DEFAULT_VERSIONS["intent_router"]
    assert sig["prompt_ref"] == f"_base/intent_router/{v}"
    assert sig["templates"] == [f"_base/intent_router/{v}/system", f"_base/intent_router/{v}/user"]


def test_unknown_stage_raises(reg):
    with pytest.raises(KeyError):
        reg.resolve("does_not_exist")


def test_missing_version_raises(reg):
    with pytest.raises(FileNotFoundError):
        reg.resolve("intent_router", provider="openai", series="gpt-5", version="v999")


def test_list_variants_finds_base(reg):
    variants = reg.list_variants("synthesizer")
    assert {"provider": "_base", "series": None, "version": "v1", "location": "_base"} in variants


def test_resolve_is_cached(reg):
    a = reg.resolve("plan_generator", provider="openai", series="gpt-5")
    b = reg.resolve("plan_generator", provider="openai", series="gpt-5")
    assert a is b
