"""C3: with nothing chosen, the active profile is openai-6.1; openai itself is unchanged.

Oracle: requirement 3, with openai-6-1-profile requirement 2 (``PROFILE_61``) and
llm-provider-profiles requirement 2 (``PROFILE_OPENAI``, ``STAGE_TIERS``).

``XBRL_QUERY_MODEL_TIERS`` and ``MODEL_TIERS`` are built when ``src.llm.config`` is
imported, so they are read in a child Python process with neither ``XBRLRAG_LLM_PROFILE`` nor
the legacy switch set: setting a variable here after import would change nothing.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from amp_tables import GPT5_FAMILY, PROFILE_56, PROFILE_61, PROFILE_OPENAI, STAGE_TIERS
from hypothesis import given
from hypothesis import strategies as st

from src.llm.config import openai_model_set
from src.llm.profiles import LlmConfigError, load_committed_settings, load_llm_settings

REPO_ROOT = Path(__file__).resolve().parents[3]
_TAG = "AMPRESULT "
_CHILD = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from src.llm import config
print("AMPRESULT " + json.dumps({"stages": dict(config.XBRL_QUERY_MODEL_TIERS),
                                 "openai_tiers": dict(config.MODEL_TIERS["openai"])}))
"""
_CLEARED = {"XBRLRAG_LLM_PROFILE", "XBRLRAG_USE_REPLACEMENT_MODELS", "ARAG_LLM_PROFILE",
            "ARAG_USE_REPLACEMENT_MODELS"}

# A hand-built document: two OpenAI models, priced, and the profiles each test needs.
_DOC = {
    "active_profile": "${XBRLRAG_LLM_PROFILE:-}",
    "providers": {"openai": {"api_key_env": "OPENAI_API_KEY"}},
    "models": {
        "model-a": {"provider": "openai", "max_output_tokens": 1000},
        "model-b": {"provider": "openai", "max_output_tokens": 2000},
    },
    "profiles": {
        "openai": {"small": "model-a", "medium": "model-a", "frontier": "model-a"},
        "openai-5.6": {"small": "model-a", "medium": "model-b", "frontier": "model-a"},
    },
}
_PRICING = {"model-a": {"input": 0.001, "output": 0.002},
            "model-b": {"input": 0.003, "output": 0.004}}


def _expected_stages(row: dict[str, str]) -> dict[str, str]:
    return {stage: row[tier] for stage, tier in STAGE_TIERS.items()}


@pytest.fixture(scope="module")
def import_time():
    """``XBRL_QUERY_MODEL_TIERS`` and ``MODEL_TIERS["openai"]``, imported with no profile set."""
    environ = {k: v for k, v in os.environ.items() if k not in _CLEARED}
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD, str(REPO_ROOT)],
        cwd=str(REPO_ROOT), env=environ, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith(_TAG)]
    assert len(lines) == 1, proc.stdout[-3000:]
    return json.loads(lines[0][len(_TAG):])


@pytest.mark.criterion("C3")
def test_nothing_chosen_is_openai_6_1():
    # oracle: requirement 3 — the fallback is openai-6.1, with openai-6-1-profile's row
    settings = load_committed_settings(environ={})
    assert settings.active_profile == "openai-6.1"
    assert settings.tier_models() == PROFILE_61


@pytest.mark.criterion("C3")
def test_openai_is_kept_and_the_switch_still_wins_over_the_fallback():
    # oracle: requirement 3 — openai, named, keeps the gpt-5 family; the legacy switch on is
    # still openai-5.6; and neither is what an empty environment gives
    assert load_committed_settings(profile="openai", environ={}).tier_models() == PROFILE_OPENAI
    switched = load_committed_settings(environ={"XBRLRAG_USE_REPLACEMENT_MODELS": "1"})
    assert switched.active_profile == "openai-5.6"
    assert switched.tier_models() == PROFILE_56
    assert load_committed_settings(environ={}).tier_models() not in (PROFILE_OPENAI, PROFILE_56)


@pytest.mark.criterion("C3")
def test_the_legacy_current_set_is_openai_6_1():
    # oracle: requirement 3 — openai_model_set(False) follows the fallback; True is unchanged
    assert openai_model_set(False) == PROFILE_61
    assert openai_model_set(True) == PROFILE_56


@pytest.mark.criterion("C3")
def test_import_time_tables_with_nothing_chosen(import_time):
    # oracle: requirement 3 — every stage on its tier's openai-6.1 model; the legacy OpenAI
    # ladder is the openai-6.1 set
    assert import_time["stages"] == _expected_stages(PROFILE_61)
    assert import_time["openai_tiers"] == PROFILE_61


@pytest.mark.criterion("C3")
def test_a_document_without_openai_6_1_raises():
    # oracle: requirement 3 — the fallback names openai-6.1; a document without it raises
    # rather than choosing another profile
    with pytest.raises(LlmConfigError):
        load_llm_settings(copy.deepcopy(_DOC), copy.deepcopy(_PRICING), environ={})


@pytest.mark.criterion("C3")
def test_a_document_with_openai_6_1_falls_back_to_it():
    # oracle: requirement 3 — with nothing chosen, the document's openai-6.1 profile
    doc = copy.deepcopy(_DOC)
    doc["profiles"]["openai-6.1"] = {"small": "model-b", "medium": "model-b",
                                     "frontier": "model-b"}
    settings = load_llm_settings(doc, copy.deepcopy(_PRICING), environ={})
    assert settings.active_profile == "openai-6.1"
    assert settings.model_for_tier("frontier") == "model-b"


@pytest.mark.criterion("C3")
@given(stage=st.sampled_from(sorted(STAGE_TIERS)))
def test_no_stage_falls_through_to_gpt_5(stage):
    # oracle: requirement 3 — in an empty environment each stage's model is its tier's
    # openai-6.1 model, never a gpt-5-family model
    model = load_committed_settings(environ={}).model_for_stage(stage)
    assert model == PROFILE_61[STAGE_TIERS[stage]]
    assert model not in GPT5_FAMILY
