"""Anthropic LLM wrapper using LangChain with retry, rate-limit, and cost tracking."""
from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

from langchain_anthropic import ChatAnthropic
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.llm.base import BaseLLMWrapper
from src.llm.config import ANTHROPIC_MODEL_ALIASES, AnthropicConfig
from src.llm.cost_tracker import CostTracker
from src.llm.rate_limiter import RateLimiter
from src.logging_config import get_logger

logger = get_logger(__name__)


# TODO: Should be invoked from Pipeline module - needs refactoring to handle all document type usecases
class AnthropicWrapper(BaseLLMWrapper):
    """LangChain-based Anthropic wrapper with retry, rate-limit, and cost tracking.

    `config.model` accepts either a tier alias (`"haiku"`, `"sonnet"`, `"opus"`) or a
    full Anthropic model id (e.g. `"claude-sonnet-4-6"`). Aliases are resolved via
    `ANTHROPIC_MODEL_ALIASES`.

    Example:
        >>> config = AnthropicConfig(model="sonnet", temperature=0.2)
        >>> wrapper = AnthropicWrapper(config)
        >>> response = wrapper.generate("Explain RAG in 50 words")
        >>> print(wrapper.get_cost_summary())
    """

    def __init__(
        self,
        config: AnthropicConfig,
        cost_tracker: CostTracker | None = None,
    ):
        """Initialize Anthropic wrapper.

        Args:
            config: Anthropic configuration
            cost_tracker: Optional shared cost tracker (creates new if None)
        """
        self.config = config
        self.cost_tracker = cost_tracker or CostTracker()
        self.rate_limiter = RateLimiter(
            rpm=config.rate_limit_rpm,
            tpm=config.rate_limit_tpm,
        )

        api_key = config.api_key or os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("Anthropic API key must be provided via config or ANTHROPIC_API_KEY env var")

        # Resolve tier alias ("haiku"/"sonnet"/"opus") to a concrete model id.
        self.resolved_model = ANTHROPIC_MODEL_ALIASES.get(config.model, config.model)

        self.llm = ChatAnthropic(
            model=self.resolved_model,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            top_p=config.top_p,
            anthropic_api_key=api_key,
            timeout=config.timeout,
        )

        logger.info(f"Initialized Anthropic wrapper with model={self.resolved_model} (config.model={config.model})")

    def generate(self, prompt: str, **kwargs: Any) -> str:
        """Generate completion with retry and rate-limit logic.

        Args:
            prompt: Input prompt
            **kwargs: Override config params (temperature, max_tokens, etc.)

        Returns:
            Generated text

        Raises:
            Exception: After max retry attempts
        """
        # Estimate tokens for rate limiter (rough heuristic: 1 token ≈ 4 chars)
        estimated_input_tokens = len(prompt) // 4
        estimated_total_tokens = estimated_input_tokens + self.config.max_tokens

        # Wait if rate limit would be exceeded
        self.rate_limiter.wait_if_needed(estimated_total_tokens)

        # Call with retry
        response_text, usage = self._generate_with_retry(prompt, **kwargs)

        # Track cost
        if usage:
            self.cost_tracker.record_usage(
                model=self.resolved_model,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                metadata={"prompt_preview": prompt[:100]},
            )

        return response_text

    @retry(
        stop=stop_after_attempt(3),  # Will be overridden by instance config
        wait=wait_exponential(multiplier=1, min=1, max=60),
        retry=retry_if_exception_type((Exception,)),  # Retry on any exception
        reraise=True,
    )
    def _generate_with_retry(self, prompt: str, **kwargs: Any) -> tuple[str, dict[str, Any] | None]:
        """Internal method with tenacity retry decorator.

        Returns:
            Tuple of (response_text, usage_dict)
        """
        try:
            # Override retry attempts from config
            self._generate_with_retry.retry.stop = stop_after_attempt(self.config.retry_max_attempts)
            self._generate_with_retry.retry.wait = wait_exponential(
                multiplier=1,
                min=self.config.retry_min_wait,
                max=self.config.retry_max_wait,
            )

            # Invoke LangChain ChatAnthropic
            messages = [{"role": "user", "content": prompt}]
            response = self.llm.invoke(messages, **kwargs)

            # Extract response text
            response_text = response.content if hasattr(response, "content") else str(response)

            usage = self._extract_usage(response)

            logger.debug(f"Anthropic generation successful, usage={usage}")
            return response_text, usage

        except Exception as e:
            logger.warning(f"Anthropic API error (will retry): {e}")
            raise

    def stream(self, prompt: str, **kwargs: Any) -> Iterator[str]:
        """Stream completion with retry and rate-limit logic.

        Args:
            prompt: Input prompt
            **kwargs: Override config params (temperature, max_tokens, etc.)

        Yields:
            Text chunks as they are generated

        Raises:
            Exception: After max retry attempts
        """
        # Estimate tokens for rate limiter
        estimated_input_tokens = len(prompt) // 4
        estimated_total_tokens = estimated_input_tokens + self.config.max_tokens

        # Wait if rate limit would be exceeded
        self.rate_limiter.wait_if_needed(estimated_total_tokens)

        # Stream with retry
        total_completion = ""
        prompt_tokens = estimated_input_tokens

        try:
            for chunk in self._stream_with_retry(prompt, **kwargs):
                total_completion += chunk
                yield chunk

            # Track cost after stream completes
            completion_tokens = len(total_completion) // 4
            self.cost_tracker.record_usage(
                model=self.resolved_model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                metadata={"prompt_preview": prompt[:100], "streaming": True},
            )
        except Exception as e:
            logger.error(f"Streaming failed: {e}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=60),
        retry=retry_if_exception_type((Exception,)),
        reraise=True,
    )
    def _stream_with_retry(self, prompt: str, **kwargs: Any) -> Iterator[str]:
        """Internal streaming method with tenacity retry decorator.

        Yields:
            Text chunks
        """
        try:
            # Override retry attempts from config
            self._stream_with_retry.retry.stop = stop_after_attempt(self.config.retry_max_attempts)
            self._stream_with_retry.retry.wait = wait_exponential(
                multiplier=1,
                min=self.config.retry_min_wait,
                max=self.config.retry_max_wait,
            )

            # Stream from LangChain ChatAnthropic
            messages = [{"role": "user", "content": prompt}]

            for chunk in self.llm.stream(messages, **kwargs):
                content = chunk.content if hasattr(chunk, "content") else str(chunk)
                if content:
                    yield content

            logger.debug(f"Anthropic streaming completed")

        except Exception as e:
            logger.warning(f"Anthropic streaming error (will retry): {e}")
            raise

    @staticmethod
    def _extract_usage(response: Any) -> dict[str, Any] | None:
        """Extract a {prompt_tokens, completion_tokens} dict from an AIMessage.

        LangChain standardizes usage on `response.usage_metadata` as
        {input_tokens, output_tokens, total_tokens}. Falls back to the raw
        Anthropic `usage` block in `response_metadata` if needed.
        """
        usage_metadata = getattr(response, "usage_metadata", None)
        if usage_metadata:
            return {
                "prompt_tokens": usage_metadata.get("input_tokens", 0),
                "completion_tokens": usage_metadata.get("output_tokens", 0),
            }

        response_metadata = getattr(response, "response_metadata", None) or {}
        raw_usage = response_metadata.get("usage")
        if raw_usage:
            return {
                "prompt_tokens": raw_usage.get("input_tokens", 0),
                "completion_tokens": raw_usage.get("output_tokens", 0),
            }

        return None

    def get_cost_summary(self) -> dict[str, Any]:
        """Get cost and usage summary from tracker."""
        return self.cost_tracker.get_summary()
