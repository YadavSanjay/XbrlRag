"""HuggingFace LLM wrapper using LangChain with retry, rate-limit, and cost tracking."""
from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

from langchain_huggingface import HuggingFaceEndpoint
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.llm.base import BaseLLMWrapper
from src.llm.config import HuggingFaceConfig
from src.llm.cost_tracker import CostTracker
from src.llm.rate_limiter import RateLimiter
from src.logging_config import get_logger

logger = get_logger(__name__)


# TODO: Should be invoked from Pipeline module - needs refactoring to handle all document type usecases
class HuggingFaceWrapper(BaseLLMWrapper):
    """LangChain-based HuggingFace wrapper with retry, rate-limit, and cost tracking.

    Supports HuggingFace Inference API (serverless) and Inference Endpoints.

    Example:
        >>> config = HuggingFaceConfig(model="meta-llama/Llama-2-7b-chat-hf")
        >>> wrapper = HuggingFaceWrapper(config)
        >>> response = wrapper.generate("Explain embeddings briefly")
        >>> print(wrapper.get_cost_summary())
    """

    def __init__(
        self,
        config: HuggingFaceConfig,
        cost_tracker: CostTracker | None = None,
    ):
        """Initialize HuggingFace wrapper.

        Args:
            config: HuggingFace configuration
            cost_tracker: Optional shared cost tracker (creates new if None)
        """
        self.config = config
        self.cost_tracker = cost_tracker or CostTracker()
        self.rate_limiter = RateLimiter(
            rpm=config.rate_limit_rpm,
            tpm=config.rate_limit_tpm,
        )

        # Initialize LangChain HuggingFaceEndpoint
        api_token = config.api_key or os.getenv("HUGGINGFACEHUB_API_TOKEN")
        if not api_token:
            raise ValueError(
                "HuggingFace API token must be provided via config or HUGGINGFACEHUB_API_TOKEN env var"
            )

        # Build model kwargs
        model_kwargs = {
            "temperature": config.temperature,
            "max_new_tokens": config.max_tokens,
            "top_p": config.top_p,
        }

        self.llm = HuggingFaceEndpoint(
            repo_id=config.model,
            task=config.task,
            huggingfacehub_api_token=api_token,
            model_kwargs=model_kwargs,
            timeout=config.timeout,
        )

        logger.info(f"Initialized HuggingFace wrapper with model={config.model}")

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
        # Estimate tokens for rate limiter (rough heuristic)
        estimated_input_tokens = len(prompt) // 4
        estimated_total_tokens = estimated_input_tokens + self.config.max_tokens

        # Wait if rate limit would be exceeded
        self.rate_limiter.wait_if_needed(estimated_total_tokens)

        # Call with retry
        response_text = self._generate_with_retry(prompt, **kwargs)

        # Track usage (HF doesn't always return token counts; estimate)
        estimated_prompt_tokens = len(prompt) // 4
        estimated_completion_tokens = len(response_text) // 4
        self.cost_tracker.record_usage(
            model=self.config.model,
            prompt_tokens=estimated_prompt_tokens,
            completion_tokens=estimated_completion_tokens,
            metadata={"prompt_preview": prompt[:100], "estimated": True},
        )

        return response_text

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=60),
        retry=retry_if_exception_type((Exception,)),
        reraise=True,
    )
    def _generate_with_retry(self, prompt: str, **kwargs: Any) -> str:
        """Internal method with tenacity retry decorator.

        Returns:
            Response text
        """
        try:
            # Override retry attempts from config
            self._generate_with_retry.retry.stop = stop_after_attempt(self.config.retry_max_attempts)
            self._generate_with_retry.retry.wait = wait_exponential(
                multiplier=1,
                min=self.config.retry_min_wait,
                max=self.config.retry_max_wait,
            )

            # Invoke LangChain HuggingFaceEndpoint
            response = self.llm.invoke(prompt, **kwargs)

            logger.debug(f"HuggingFace generation successful")
            return response

        except Exception as e:
            logger.warning(f"HuggingFace API error (will retry): {e}")
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
                model=self.config.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                metadata={"prompt_preview": prompt[:100], "streaming": True, "estimated": True},
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

            # Stream from LangChain HuggingFaceEndpoint
            for chunk in self.llm.stream(prompt, **kwargs):
                if chunk:
                    yield chunk

            logger.debug(f"HuggingFace streaming completed")

        except Exception as e:
            logger.warning(f"HuggingFace streaming error (will retry): {e}")
            raise

    def get_cost_summary(self) -> dict[str, Any]:
        """Get cost and usage summary from tracker."""
        return self.cost_tracker.get_summary()
