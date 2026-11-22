"""C1: the committed files load, and every invalid document raises LlmConfigError.

Oracle: requirement 1 (and requirement 5 for the pricing rows). The documents are the
hand-built ones in conftest.py; each case below breaks exactly one rule.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.profiles import LlmConfigError, load_committed_settings, load_llm_settings

Docs = tuple[dict[str, Any], dict[str, Any]]
ALLOWED_EFFORTS = ("low", "medium", "high", "xhigh", "max")


def _set(path: tuple[str, ...], value: Any) -> Callable[[Docs], None]:
    """A mutator setting ``doc[path] = value`` (path[0] == "pricing" targets the rate card)."""

    def mutate(docs: Docs) -> None:
        doc, pricing = docs
        target = pricing if path[0] == "pricing" else doc
        keys = path[1:] if path[0] == "pricing" else path
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value

    return mutate


def _delete(path: tuple[str, ...]) -> Callable[[Docs], None]:
    """A mutator removing ``doc[path]`` (path[0] == "pricing" targets the rate card)."""

    def mutate(docs: Docs) -> None:
        doc, pricing = docs
        target = pricing if path[0] == "pricing" else doc
        keys = path[1:] if path[0] == "pricing" else path
        for key in keys[:-1]:
            target = target[key]
        del target[keys[-1]]

    return mutate


# One rule broken per case; requirement 1 / 5 says each raises LlmConfigError.
INVALID: dict[str, Callable[[Docs], None]] = {
    # an unknown key at each level
    "unknown top-level key": _set(("budgets",), {"max": 1}),
    "unknown provider key": _set(("providers", "openai", "timeout_s"), 30.0),
    "unknown model key": _set(("models", "gpt-5", "json_mode"), "json_object"),
    "unknown profile key": _set(("profiles", "openai", "huge"), "gpt-5"),
    "unknown pricing key": _set(("pricing", "gpt-5", "batch_input"), 0.001),
    "unknown pricing tier key": _set(
        ("pricing", "claude-haiku-5-5", "tiers", 0, "flex"), 0.001
    ),
    # a missing tier
    "missing small tier": _delete(("profiles", "anthropic", "small")),
    "missing frontier tier": _delete(("profiles", "openai", "frontier")),
    # an unknown provider
    # deepseek-provider: deepseek is now an allowed provider name, so the forbidden name is
    # mistral (still outside the allowed set before and after that change)
    "provider name not allowed": _set(
        ("providers", "mistral"), {"api_key_env": "MISTRAL_API_KEY"}
    ),
    # deepseek-provider: mistral, as above
    "model on an undeclared provider": _set(("models", "gpt-5", "provider"), "mistral"),
    # a profile model not in models
    "profile model not in models": _set(("profiles", "openai", "small"), "gpt-zz-invented-9"),
    # effort rules
    "effort on an openai model": _set(("models", "gpt-5", "effort"), "high"),
    "effort outside the allowed values": _set(("models", "claude-sonnet-5-5", "effort"), "extreme"),
    # rates
    "infinite rate": _set(("pricing", "gpt-5", "input"), math.inf),
    "nan rate": _set(("pricing", "gpt-5", "output"), math.nan),
    "negative rate": _set(("pricing", "gpt-5", "cached_input"), -0.0001),
    "negative cache-write rate": _set(("pricing", "claude-opus-5-5", "cache_write"), -0.005),
    "negative tier rate": _set(("pricing", "claude-haiku-5-5", "tiers", 0, "input"), -0.0005),
    "tier threshold zero": _set(
        ("pricing", "claude-haiku-5-5", "tiers", 0, "above_prompt_tokens"), 0
    ),
    # max_output_tokens <= 0
    "max_output_tokens zero": _set(("models", "gpt-5-mini", "max_output_tokens"), 0),
    "max_output_tokens negative": _set(("models", "claude-opus-5-5", "max_output_tokens"), -1),
    # wrong types
    "max_output_tokens as a string": _set(("models", "gpt-5", "max_output_tokens"), "8000"),
    "temperature as a string": _set(("models", "gpt-5", "temperature"), "no"),
    "api_key_env not a string": _set(("providers", "anthropic", "api_key_env"), 5),
    "profile tier not a string": _set(("profiles", "anthropic", "medium"), 5),
    "rate as a string": _set(("pricing", "gpt-5", "input"), "0.00125"),
}

# Every required key of the hand-built documents (requirements 1 and 5).
REQUIRED_PATHS: list[tuple[str, ...]] = [
    ("active_profile",),
    ("providers",),
    ("models",),
    ("profiles",),
    ("providers", "openai", "api_key_env"),
    ("providers", "anthropic", "api_key_env"),
    *[("models", m, "provider") for m in ("gpt-5-nano", "gpt-5.6-sol", "claude-haiku-5-5")],
    *[("models", m, "max_output_tokens") for m in ("gpt-5", "gpt-5.6-luna", "claude-opus-5-5")],
    *[("profiles", p, t) for p in ("openai", "openai-5.6", "anthropic", "mixed")
      for t in ("small", "medium", "frontier")],
    *[("pricing", m, k) for m in ("gpt-5-mini", "gpt-5.6-terra", "claude-sonnet-5-5")
      for k in ("input", "output")],
    *[("pricing", "claude-haiku-5-5", "tiers", 0, k)
      for k in ("above_prompt_tokens", "input", "output")],
]


@pytest.mark.criterion("C1")
def test_committed_files_load(llm_env):
    # oracle: requirement 1 — the committed llm.yaml / pricing.yaml load into settings for
    # every committed profile; with no profile chosen anywhere the active one is "openai"
    # (requirement 3, row 4)
    for name in ("openai", "openai-5.6", "anthropic"):
        assert load_committed_settings(profile=name, environ={}).active_profile == name
    # arm-model-from-profile moves row 4 from openai to openai-6.1 and pins it in its own C3.
    assert load_committed_settings(environ={}).active_profile in ("openai", "openai-6.1")


@pytest.mark.criterion("C1")
def test_hand_built_documents_are_valid(make_doc, make_pricing):
    # oracle: requirement 1 — the conftest documents satisfy every rule, so each invalid case
    # below differs from a valid document by exactly one change
    settings = load_llm_settings(make_doc(), make_pricing(), profile="mixed", environ={})
    assert settings.active_profile == "mixed"
    assert settings.model_for_tier("medium") == "claude-sonnet-5-5"


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("case", sorted(INVALID))
def test_invalid_document_raises(make_doc, make_pricing, case):
    # oracle: requirement 1 / 5 — the named rule is broken, so loading raises LlmConfigError
    docs = (make_doc(), make_pricing())
    INVALID[case](docs)
    with pytest.raises(LlmConfigError):
        load_llm_settings(docs[0], docs[1], profile="openai", environ={})


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("model", ["claude-opus-5-5", "gpt-5.6-luna"])
def test_unpriced_profile_model_raises_naming_it(make_doc, make_pricing, model):
    # oracle: requirement 5 — a model a profile uses with no pricing row raises, naming it
    pricing = make_pricing()
    del pricing[model]
    with pytest.raises(LlmConfigError) as excinfo:
        load_llm_settings(make_doc(), pricing, profile="openai", environ={})
    assert model in str(excinfo.value)


@pytest.mark.criterion("C1")
@given(path=st.sampled_from(REQUIRED_PATHS))
def test_removing_any_required_key_raises(make_doc, make_pricing, path):
    # oracle: C1's property — removing any one required key from a valid document raises,
    # whichever profile is active (the whole document is validated)
    docs = (make_doc(), make_pricing())
    _delete(path)(docs)
    with pytest.raises(LlmConfigError):
        load_llm_settings(docs[0], docs[1], profile="anthropic", environ={})


@pytest.mark.criterion("C1")
@given(
    model=st.sampled_from(["gpt-5", "gpt-5-mini", "claude-haiku-5-5", "claude-opus-5-5"]),
    max_output_tokens=st.integers(max_value=0),
)
def test_any_non_positive_max_output_tokens_raises(make_doc, make_pricing, model,
                                                   max_output_tokens):
    # oracle: requirement 1 — max_output_tokens is an int > 0
    doc = make_doc()
    doc["models"][model]["max_output_tokens"] = max_output_tokens
    with pytest.raises(LlmConfigError):
        load_llm_settings(doc, make_pricing(), profile="openai", environ={})


@pytest.mark.criterion("C1")
@given(
    model=st.sampled_from(["gpt-5", "claude-sonnet-5-5", "gpt-5.6-sol"]),
    key=st.sampled_from(["input", "output", "cached_input", "cache_write"]),
    rate=st.one_of(
        st.floats(max_value=-1e-12, allow_nan=False, allow_infinity=True),
        st.sampled_from([math.inf, math.nan]),
    ),
)
def test_any_negative_or_non_finite_rate_raises(make_doc, make_pricing, model, key, rate):
    # oracle: requirement 5 — rates are finite and >= 0
    pricing = make_pricing()
    pricing[model][key] = rate
    with pytest.raises(LlmConfigError):
        load_llm_settings(make_doc(), pricing, profile="openai", environ={})


@pytest.mark.criterion("C1")
@given(effort=st.text(max_size=12).filter(lambda s: s not in ALLOWED_EFFORTS))
def test_any_effort_outside_the_allowed_values_raises(make_doc, make_pricing, effort):
    # oracle: requirement 1 — effort is one of low, medium, high, xhigh, max
    doc = make_doc()
    doc["models"]["claude-opus-5-5"]["effort"] = effort
    with pytest.raises(LlmConfigError):
        load_llm_settings(doc, make_pricing(), profile="anthropic", environ={})


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
def test_every_allowed_effort_loads_on_anthropic_only(make_doc, make_pricing):
    # oracle: requirement 1 — each allowed effort is accepted on an anthropic model and the
    # same value is rejected on an openai model
    for effort in ALLOWED_EFFORTS:
        doc = make_doc()
        doc["models"]["claude-opus-5-5"]["effort"] = effort
        settings = load_llm_settings(doc, make_pricing(), profile="anthropic", environ={})
        assert settings.effort_for("claude-opus-5-5") == effort
        doc["models"]["gpt-5"]["effort"] = effort
        with pytest.raises(LlmConfigError):
            load_llm_settings(doc, make_pricing(), profile="anthropic", environ={})
