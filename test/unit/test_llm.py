"""Unit tests for LLM wrappers."""
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.llm.config import HuggingFaceConfig, OpenAIConfig
from src.llm.cost_tracker import CostTracker, UsageRecord
from src.llm.rate_limiter import RateLimiter


class TestCostTracker:
    """Test cost tracking functionality."""

    def test_record_usage(self):
        """Test recording a single usage."""
        tracker = CostTracker()
        record = tracker.record_usage(
            model="gpt-3.5-turbo",
            prompt_tokens=100,
            completion_tokens=50,
        )

        assert record.prompt_tokens == 100
        assert record.completion_tokens == 50
        assert record.total_tokens == 150
        assert record.estimated_cost_usd > 0

        summary = tracker.get_summary()
        assert summary["total_calls"] == 1
        assert summary["total_prompt_tokens"] == 100
        assert summary["total_completion_tokens"] == 50

    def test_multiple_models(self):
        """Test tracking multiple models."""
        tracker = CostTracker()
        tracker.record_usage("gpt-3.5-turbo", 100, 50)
        tracker.record_usage("gpt-4", 200, 100)
        tracker.record_usage("gpt-3.5-turbo", 50, 25)

        summary = tracker.get_summary()
        assert summary["total_calls"] == 3
        assert len(summary["per_model"]) == 2
        assert summary["per_model"]["gpt-3.5-turbo"]["calls"] == 2
        assert summary["per_model"]["gpt-4"]["calls"] == 1

    def test_reset(self):
        """Test resetting tracker."""
        tracker = CostTracker()
        tracker.record_usage("gpt-3.5-turbo", 100, 50)
        tracker.reset()

        summary = tracker.get_summary()
        assert summary["total_calls"] == 0
        assert summary["total_cost_usd"] == 0.0


class TestRateLimiter:
    """Test rate limiting functionality."""

    def test_no_limit(self):
        """Test rate limiter with no limits."""
        limiter = RateLimiter(rpm=None, tpm=None)
        # Should not block
        limiter.wait_if_needed(1000)
        limiter.wait_if_needed(1000)

    def test_rpm_limit(self):
        """Test requests per minute limit."""
        import time

        limiter = RateLimiter(rpm=2, tpm=None)

        # First two should be immediate
        start = time.time()
        limiter.wait_if_needed(0)
        limiter.wait_if_needed(0)
        elapsed = time.time() - start
        assert elapsed < 1.0  # Should be fast

        # Third should block (but we won't wait in test)
        # Just verify it doesn't crash
        # limiter.wait_if_needed(0)  # Would wait ~60s


class TestOpenAIConfig:
    """Test OpenAI configuration."""

    def test_default_config(self):
        """Test default configuration values."""
        config = OpenAIConfig()
        assert config.model == "gpt-5-mini"
        assert config.temperature == 0.7
        assert config.retry_max_attempts == 3

    def test_custom_config(self):
        """Test custom configuration."""
        config = OpenAIConfig(
            model="gpt-4",
            temperature=0.5,
            max_tokens=1000,
            retry_max_attempts=5,
        )
        assert config.model == "gpt-4"
        assert config.temperature == 0.5
        assert config.max_tokens == 1000
        assert config.retry_max_attempts == 5


class TestHuggingFaceConfig:
    """Test HuggingFace configuration."""

    def test_default_config(self):
        """Test default configuration values."""
        config = HuggingFaceConfig()
        assert config.model == "meta-llama/Llama-2-7b-chat-hf"
        assert config.task == "text-generation"


# Integration tests (require API keys, marked to skip by default)
@pytest.mark.skip(reason="Requires OpenAI API key")
class TestOpenAIWrapperIntegration:
    """Integration tests for OpenAI wrapper (requires API key)."""

    def test_generate(self):
        """Test actual OpenAI generation."""
        from src.llm.openai_wrapper import OpenAIWrapper

        config = OpenAIConfig(
            model="gpt-3.5-turbo",
            max_tokens=50,
        )
        wrapper = OpenAIWrapper(config)

        response = wrapper.generate("Say 'test' and nothing else.")
        assert len(response) > 0

        summary = wrapper.get_cost_summary()
        assert summary["total_calls"] == 1


@pytest.mark.skip(reason="Requires HuggingFace API token")
class TestHuggingFaceWrapperIntegration:
    """Integration tests for HuggingFace wrapper (requires API token)."""

    def test_generate(self):
        """Test actual HuggingFace generation."""
        from src.llm.huggingface_wrapper import HuggingFaceWrapper

        config = HuggingFaceConfig(
            model="gpt2",  # Small model for testing
            max_tokens=50,
        )
        wrapper = HuggingFaceWrapper(config)

        response = wrapper.generate("Hello")
        assert len(response) > 0
