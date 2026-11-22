"""Provider-neutral LLM gateway (docs/design/XbrlRag_LLM_Harness.md).

Public surface:
    LLMGateway         — instrumented, provider-agnostic completion gateway
    LLMResult          — result of one (possibly retried) call (alias LLMCallResult)
    ModelRef           — resolved (provider, series, tier) reference to a model
    resolve_model_ref  — resolve a model id / tier alias to a ModelRef
    ProviderAdapter    — adapter interface; OpenAI/OpenAICompat/Anthropic impls
"""
from src.llm.gateway.adapters import (
    AnthropicAdapter,
    LLMEmptyReplyError,
    LLMRefusalError,
    OpenAIAdapter,
    OpenAICompatAdapter,
    ParsedResponse,
    ProviderAdapter,
    cache_point_count,
    classify_transient,
)
from src.llm.gateway.gateway import (
    LLMCallResult,
    LLMGateway,
    LLMResult,
)
from src.llm.gateway.json_utils import extract_json
from src.llm.gateway.model_ref import (
    ModelRef,
    call_params,
    derive_series,
    infer_provider,
    resolve_model_ref,
    stage_call_params,
)

__all__ = [
    "LLMGateway",
    "LLMResult",
    "LLMCallResult",
    "ModelRef",
    "resolve_model_ref",
    "call_params",
    "stage_call_params",
    "extract_json",
    "infer_provider",
    "derive_series",
    "ProviderAdapter",
    "ParsedResponse",
    "classify_transient",
    "cache_point_count",
    "LLMEmptyReplyError",
    "LLMRefusalError",
    "OpenAIAdapter",
    "OpenAICompatAdapter",
    "AnthropicAdapter",
]
