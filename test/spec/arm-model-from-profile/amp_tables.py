"""The arm-model-from-profile spec's tables (docs/specs/arm-model-from-profile.md), by hand.

Written from the spec, never read back from the code under test.
"""

from __future__ import annotations

PROFILE_ENV = "XBRLRAG_LLM_PROFILE"
LEGACY_ENV = "XBRLRAG_USE_REPLACEMENT_MODELS"

# Requirement 1's table: active profile -> the arms' default model.
FRONTIER = {
    "openai-6.1": "gpt-6.1-sol",
    "openai-5.6": "gpt-5.6-sol",
    "anthropic": "claude-opus-5-5",
    "deepseek": "deepseek-v4-pro",
    "openai": "gpt-5",
}
# The old literal default; under a profile whose frontier is this the two can't be told apart.
OLD_DEFAULT = "gpt-5.6-sol"
DISTINGUISHING = tuple(p for p, m in FRONTIER.items() if m != OLD_DEFAULT)

# openai-6-1-profile requirement 2, and llm-provider-profiles requirement 2's openai row.
PROFILE_61 = {"small": "gpt-6-luna", "medium": "gpt-6.1-terra", "frontier": "gpt-6.1-sol"}
PROFILE_OPENAI = {"small": "gpt-5-nano", "medium": "gpt-5-mini", "frontier": "gpt-5"}
PROFILE_56 = {"small": "gpt-5.6-luna", "medium": "gpt-5.6-terra", "frontier": "gpt-5.6-sol"}
GPT5_FAMILY = frozenset(PROFILE_OPENAI.values())

# XBRL_STAGE_TIERS as llm-provider-profiles and baseline-arms state it.
STAGE_TIERS = {
    "query_decomposer": "medium",
    "intent_router": "medium",
    "plan_generator": "medium",
    "synthesizer": "medium",
    "faithfulness_verifier": "small",
    "closed_book": "medium",
    "naive_rag": "medium",
}

ARM_NAMES = ("long_context_oracle", "long_context_resolved", "long_context_multi",
             "frontier_sec_tools")
# build_system also takes the oracle arm's former name.
BUILD_NAMES = (*ARM_NAMES, "long_context")

# Every model ID committed in configs/llm/llm.yaml, for the help-text check.
COMMITTED_MODELS = (
    "gpt-5-nano", "gpt-5-mini", "gpt-5", "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol",
    "gpt-6-luna", "gpt-6.1-terra", "gpt-6.1-sol", "claude-haiku-5-5", "claude-sonnet-5-5",
    "claude-opus-5-5", "deepseek-flash", "deepseek-v4-pro",
)
