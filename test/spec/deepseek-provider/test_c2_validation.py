"""C2: invalid DeepSeek configurations raise LlmConfigError; valid ones load.

Oracle: requirement 1. The documents are the hand-built ones in conftest.py (three providers,
deepseek with an https base_url). Every test first asserts that the valid document loads, so
each case differs from a loadable document by exactly one change.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.profiles import LlmConfigError, load_llm_settings

Docs = tuple[dict[str, Any], dict[str, Any]]


def _drop_deepseek_base_url(docs: Docs) -> None:
    del docs[0]["providers"]["deepseek"]["base_url"]


def _deepseek_http(docs: Docs) -> None:
    docs[0]["providers"]["deepseek"]["base_url"] = "http://api.deepseek.com"


def _openai_base_url(docs: Docs) -> None:
    docs[0]["providers"]["openai"]["base_url"] = "https://api.openai.com/v1"


def _anthropic_base_url(docs: Docs) -> None:
    docs[0]["providers"]["anthropic"]["base_url"] = "https://api.anthropic.com"


def _mistral_provider(docs: Docs) -> None:
    docs[0]["providers"]["mistral"] = {"api_key_env": "MISTRAL_API_KEY"}


def _model_on_mistral(docs: Docs) -> None:
    docs[0]["models"]["deepseek-v4-pro"]["provider"] = "mistral"


def _effort_on_openai(docs: Docs) -> None:
    docs[0]["models"]["gpt-5-mini"]["effort"] = "high"


# One rule of requirement 1 broken per case; each raises LlmConfigError.
INVALID: dict[str, Callable[[Docs], None]] = {
    "deepseek without base_url": _drop_deepseek_base_url,
    "deepseek with an http base_url": _deepseek_http,
    "base_url on openai": _openai_base_url,
    "base_url on anthropic": _anthropic_base_url,
    "provider named mistral": _mistral_provider,
    "model on mistral": _model_on_mistral,
    "effort on an openai model": _effort_on_openai,
}


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("case", sorted(INVALID))
def test_invalid_deepseek_configuration_raises(spec, case):
    # oracle: requirement 1 — the named rule is broken, so loading raises LlmConfigError
    docs = spec.make_docs()
    assert spec.loads(*docs), "the valid three-provider document does not load"
    INVALID[case](docs)
    with pytest.raises(LlmConfigError):
        load_llm_settings(docs[0], docs[1], profile="openai", environ={})


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_effort_on_a_deepseek_model_loads(spec, effort):
    # oracle: requirement 1 — effort is allowed on deepseek models
    doc, pricing = spec.make_docs()
    doc["models"]["deepseek-flash"]["effort"] = effort
    assert spec.loads(doc, pricing), f"effort {effort!r} on deepseek-flash was rejected"
    doc["models"]["gpt-5-mini"]["effort"] = effort
    assert not spec.loads(doc, pricing), "effort on an openai model loaded"


def _allowed(name: str, base_url: str | None) -> bool:
    """Requirement 1's rule: deepseek needs an https base_url; the others carry none."""
    if name == "deepseek":
        return base_url is not None and base_url.startswith("https://")
    return base_url is None


HOSTS = st.from_regex(r"[a-z][a-z0-9-]{0,15}\.[a-z]{2,6}(/[a-z0-9]{1,8})?", fullmatch=True)


@pytest.mark.criterion("C2")
@given(
    name=st.sampled_from(["openai", "anthropic", "deepseek"]),
    scheme=st.sampled_from([None, "https://", "http://"]),
    host=HOSTS,
)
def test_any_provider_base_url_loads_exactly_when_allowed(spec, name, scheme, host):
    # oracle: C2's property — loading succeeds exactly when requirement 1 allows the
    # (provider, base_url) pair; the other two providers stay valid
    docs = spec.make_docs()
    assert spec.loads(*docs), "the valid three-provider document does not load"
    row = docs[0]["providers"][name]
    base_url = None if scheme is None else scheme + host
    row.pop("base_url", None)
    if base_url is not None:
        row["base_url"] = base_url
    assert spec.loads(*docs) is _allowed(name, base_url)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
def test_renaming_deepseek_to_an_unknown_provider_fails(spec):
    # oracle: requirement 1 — the same rows load under "deepseek" and fail under any name
    # outside the three
    docs = spec.make_docs()
    assert spec.loads(*docs), "the valid three-provider document does not load"
    for other in ("mistral", "deepseek-v2", "DeepSeek", "openai_compat"):
        doc, pricing = spec.make_docs()
        doc["providers"][other] = doc["providers"].pop("deepseek")
        for row in doc["models"].values():
            if row["provider"] == "deepseek":
                row["provider"] = other
        assert not spec.loads(doc, pricing), other
