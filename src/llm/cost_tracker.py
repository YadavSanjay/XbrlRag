"""Token usage and cost tracking for LLM calls."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any

from src.llm.config import MODEL_PRICING
from src.llm.profiles import cost_usd
from src.logging_config import get_logger

logger = get_logger(__name__)

# TODO: Needs validation, tests, and integration with pipeline module.

@dataclass
class UsageRecord:
    """Single LLM call usage record."""

    timestamp: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    cached_prompt_tokens: int = 0   # B2: prompt tokens served from cache (⊆ prompt_tokens)
    cache_write_tokens: int = 0     # prompt tokens written to the cache (⊆ prompt_tokens)
    metadata: dict[str, Any] = field(default_factory=dict)


class CostTracker:
    """Track token usage and costs across LLM calls.

    Can log to file and maintain running totals.
    """

    def __init__(self, log_file: str | Path | None = None):
        """Initialize cost tracker.

        Args:
            log_file: Optional path to JSON log file for usage records
        """
        self.log_file = Path(log_file) if log_file else None
        self._records: list[UsageRecord] = []
        self._lock = Lock()
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0
        self._total_cached_prompt_tokens = 0
        self._total_cache_write_tokens = 0
        self._total_cost_usd = 0.0

    def record_usage(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        metadata: dict[str, Any] | None = None,
        cached_prompt_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> UsageRecord:
        """Record a single LLM call.

        Args:
            model: Model name
            prompt_tokens: Input tokens (includes any cached tokens as a subset)
            completion_tokens: Output tokens
            metadata: Optional metadata (user_id, request_id, etc.)
            cached_prompt_tokens: Prompt tokens served from the provider's cache (B2);
                billed at the model's cheaper cached rate.
            cache_write_tokens: Prompt tokens written to the provider's cache; billed at
                the model's cache-write rate.

        Returns:
            UsageRecord with computed cost

        Raises:
            ValueError: If the model has no pricing row (an unpriced call is never
                recorded at $0, which a run's cost cap would miss) or the counts are
                inconsistent.
        """
        import datetime

        total_tokens = prompt_tokens + completion_tokens
        cost = self._estimate_cost(
            model, prompt_tokens, completion_tokens, cached_prompt_tokens, cache_write_tokens
        )

        record = UsageRecord(
            timestamp=datetime.datetime.now(datetime.UTC).isoformat(),
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=cost,
            cached_prompt_tokens=cached_prompt_tokens,
            cache_write_tokens=cache_write_tokens,
            metadata=metadata or {},
        )

        with self._lock:
            self._records.append(record)
            self._total_prompt_tokens += prompt_tokens
            self._total_completion_tokens += completion_tokens
            self._total_cached_prompt_tokens += cached_prompt_tokens
            self._total_cache_write_tokens += cache_write_tokens
            self._total_cost_usd += cost

            logger.info(
                f"LLM usage: model={model}, prompt_tokens={prompt_tokens}, "
                f"cached_prompt_tokens={cached_prompt_tokens}, "
                f"cache_write_tokens={cache_write_tokens}, "
                f"completion_tokens={completion_tokens}, cost=${cost:.6f}"
            )

            if self.log_file:
                self._append_to_log(record)

        return record

    def get_summary(self) -> dict[str, Any]:
        """Get summary of all tracked usage.

        Returns:
            Dictionary with totals and per-model breakdowns
        """
        with self._lock:
            per_model: dict[str, dict[str, Any]] = {}
            for rec in self._records:
                if rec.model not in per_model:
                    per_model[rec.model] = {
                        "calls": 0,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                        "cost_usd": 0.0,
                    }
                per_model[rec.model]["calls"] += 1
                per_model[rec.model]["prompt_tokens"] += rec.prompt_tokens
                per_model[rec.model]["completion_tokens"] += rec.completion_tokens
                per_model[rec.model]["total_tokens"] += rec.total_tokens
                per_model[rec.model]["cost_usd"] += rec.estimated_cost_usd

            return {
                "total_calls": len(self._records),
                "total_prompt_tokens": self._total_prompt_tokens,
                "total_completion_tokens": self._total_completion_tokens,
                "total_cached_prompt_tokens": self._total_cached_prompt_tokens,
                "total_cache_write_tokens": self._total_cache_write_tokens,
                "total_tokens": self._total_prompt_tokens + self._total_completion_tokens,
                "total_cost_usd": self._total_cost_usd,
                "per_model": per_model,
            }

    def reset(self) -> None:
        """Clear all tracked usage."""
        with self._lock:
            self._records.clear()
            self._total_prompt_tokens = 0
            self._total_completion_tokens = 0
            self._total_cached_prompt_tokens = 0
            self._total_cache_write_tokens = 0
            self._total_cost_usd = 0.0

    @staticmethod
    def _estimate_cost(
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cached_prompt_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> float:
        """Cost in USD of one call, from the model's row in configs/llm/pricing.yaml.

        Cached and written prompt tokens (subsets of ``prompt_tokens``) are billed at the
        row's ``cached_input`` / ``cache_write`` rates, each falling back to ``input``.

        Raises:
            ValueError: If the model has no pricing row, or the counts are inconsistent.
        """
        pricing = MODEL_PRICING.get(model)
        if pricing is None:
            raise ValueError(
                f"no pricing row for model {model!r} in configs/llm/pricing.yaml; add one "
                "before calling it (an unpriced call would be recorded at $0)"
            )
        return cost_usd(
            pricing,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_prompt_tokens=cached_prompt_tokens,
            cache_write_tokens=cache_write_tokens,
        )

    def _append_to_log(self, record: UsageRecord) -> None:
        """Append usage record to JSON log file (one JSON object per line)."""
        if not self.log_file:
            return
        try:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(record)) + "\n")
        except Exception as e:
            logger.error(f"Failed to write cost log: {e}")
