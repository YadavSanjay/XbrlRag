"""Base abstract class for LLM wrappers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any


class BaseLLMWrapper(ABC):
    """Abstract base for LLM wrappers with retry, rate-limit, and cost tracking."""

    @abstractmethod
    def generate(self, prompt: str, **kwargs: Any) -> str:
        """Generate a completion from the LLM.

        Args:
            prompt: Input prompt text
            **kwargs: Additional parameters (temperature, max_tokens, etc.)

        Returns:
            Generated text response
        """
        pass

    @abstractmethod
    def stream(self, prompt: str, **kwargs: Any) -> Iterator[str]:
        """Stream a completion from the LLM token by token.

        Args:
            prompt: Input prompt text
            **kwargs: Additional parameters (temperature, max_tokens, etc.)

        Yields:
            Text chunks as they are generated
        """
        pass

    @abstractmethod
    def get_cost_summary(self) -> dict[str, Any]:
        """Return summary of token usage and estimated costs.

        Returns:
            Dictionary with token counts, cost estimates, etc.
        """
        pass
