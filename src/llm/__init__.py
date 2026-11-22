"""LLM module with LangChain wrappers for OpenAI, Anthropic, and HuggingFace.

Provides:
- OpenAIWrapper: LangChain-based OpenAI client with retry/rate-limit/cost tracking
- AnthropicWrapper: LangChain-based Anthropic client with retry/rate-limit/cost tracking
- HuggingFaceWrapper: LangChain-based HuggingFace Inference API client
- CostTracker: Token usage and cost logging
- PromptManager: Prompt template management
- BatchProcessor: Batch processing utilities
- SECTableProcessor: SEC 10K table summarization with vision models
"""
import logging as _logging

from prompts.prompts import DEFAULT_TEMPLATES, PromptManager, PromptTemplate

# Lightweight, dependency-free building blocks — always importable. The XBRL
# flow only needs these (config/cost_tracker), so they must not be gated behind
# optional provider SDKs.
from src.llm.cost_tracker import CostTracker

# Provider wrappers depend on optional SDKs (langchain_openai, langchain_anthropic,
# vision deps, …). Import them best-effort so that a missing optional dependency
# doesn't make the whole `src.llm` package — and everything that imports
# `src.llm.config` or `src.llm.cost_tracker` — unimportable.
try:
    from src.llm.anthropic_wrapper import AnthropicWrapper
    from src.llm.batch import BatchProcessor, batch_with_retry, chunk_prompts
    from src.llm.huggingface_wrapper import HuggingFaceWrapper
    from src.llm.openai_wrapper import OpenAIWrapper
    from src.llm.sec10k_table_processor import SEC10KTableProcessor
except ImportError as _exc:  # pragma: no cover - depends on which extras are installed
    _logging.getLogger(__name__).debug(
        "Optional LLM provider wrappers unavailable (%s); "
        "src.llm.config / cost_tracker remain usable.", _exc
    )

__all__ = [
    "OpenAIWrapper",
    "AnthropicWrapper",
    "HuggingFaceWrapper",
    "CostTracker",
    "PromptManager",
    "PromptTemplate",
    "DEFAULT_TEMPLATES",
    "BatchProcessor",
    "batch_with_retry",
    "chunk_prompts",
    "SEC10KTableProcessor",
]
