"""Tests for streaming, batch processing, and prompt templates."""
from unittest.mock import MagicMock, Mock

import pytest

from prompts.prompts import DEFAULT_TEMPLATES, PromptManager, PromptTemplate
from src.llm.batch import BatchProcessor, chunk_prompts


class TestPromptTemplate:
    """Test prompt template functionality."""

    def test_simple_template(self):
        """Test basic template formatting."""
        template = PromptTemplate("Hello {name}!")
        result = template.format(name="World")
        assert result == "Hello World!"

    def test_multiple_variables(self):
        """Test template with multiple variables."""
        template = PromptTemplate("Question: {question}\nContext: {context}")
        result = template.format(question="What?", context="Some context")
        assert "What?" in result
        assert "Some context" in result

    def test_missing_variable_error(self):
        """Test error when variable is missing."""
        template = PromptTemplate("Hello {name}!")
        with pytest.raises(ValueError, match="Missing required variables"):
            template.format()

    def test_extract_variables(self):
        """Test variable extraction."""
        template = PromptTemplate("Hello {name}, your {item} is ready!")
        assert template.variables == {"name", "item"}

    def test_template_name(self):
        """Test template naming."""
        template = PromptTemplate("Hello", name="greeting")
        assert template.name == "greeting"


class TestPromptManager:
    """Test prompt manager functionality."""

    def test_add_get_template(self):
        """Test adding and retrieving templates."""
        manager = PromptManager()
        manager.add("test", "Hello {name}")

        template = manager.get("test")
        assert template.name == "test"
        assert template.variables == {"name"}

    def test_format_template(self):
        """Test formatting via manager."""
        manager = PromptManager()
        manager.add("greet", "Hello {name}!")

        result = manager.format("greet", name="Alice")
        assert result == "Hello Alice!"

    def test_list_templates(self):
        """Test listing templates."""
        manager = PromptManager()
        manager.add("t1", "Template 1")
        manager.add("t2", "Template 2")

        templates = manager.list_templates()
        assert "t1" in templates
        assert "t2" in templates

    def test_get_nonexistent_template(self):
        """Test error when getting nonexistent template."""
        manager = PromptManager()
        with pytest.raises(KeyError):
            manager.get("nonexistent")

    def test_default_templates(self):
        """Test that default templates are valid."""
        for name, template_str in DEFAULT_TEMPLATES.items():
            template = PromptTemplate(template_str, name=name)
            assert len(template.variables) > 0


class TestBatchProcessing:
    """Test batch processing utilities."""

    def test_chunk_prompts(self):
        """Test chunking prompts."""
        prompts = ["p1", "p2", "p3", "p4", "p5"]
        chunks = list(chunk_prompts(prompts, chunk_size=2))

        assert len(chunks) == 3
        assert chunks[0] == ["p1", "p2"]
        assert chunks[1] == ["p3", "p4"]
        assert chunks[2] == ["p5"]

    def test_chunk_prompts_exact_fit(self):
        """Test chunking when prompts fit exactly."""
        prompts = ["p1", "p2", "p3", "p4"]
        chunks = list(chunk_prompts(prompts, chunk_size=2))

        assert len(chunks) == 2
        assert chunks[0] == ["p1", "p2"]
        assert chunks[1] == ["p3", "p4"]

    def test_batch_processor_init(self):
        """Test batch processor initialization."""
        mock_wrapper = Mock()
        processor = BatchProcessor(mock_wrapper, max_workers=3)

        assert processor.wrapper == mock_wrapper
        assert processor.max_workers == 3
        assert processor.fail_fast == False

    def test_batch_processor_success(self):
        """Test successful batch processing."""
        mock_wrapper = Mock()
        mock_wrapper.generate = Mock(side_effect=lambda p: f"Response to: {p}")

        processor = BatchProcessor(mock_wrapper, max_workers=2)
        prompts = ["Q1", "Q2", "Q3"]

        results = processor.process_batch(prompts)

        assert len(results) == 3
        assert all("Response to:" in r for r in results)

    def test_batch_processor_with_callback(self):
        """Test batch processing with callback."""
        mock_wrapper = Mock()
        mock_wrapper.generate = Mock(return_value="Response")

        callback_calls = []
        def callback(idx, result):
            callback_calls.append((idx, result))

        processor = BatchProcessor(mock_wrapper, max_workers=2)
        prompts = ["Q1", "Q2"]

        processor.process_batch(prompts, callback=callback)

        assert len(callback_calls) == 2

    def test_batch_processor_error_handling(self):
        """Test error handling in batch processing."""
        mock_wrapper = Mock()
        mock_wrapper.generate = Mock(side_effect=Exception("API Error"))

        processor = BatchProcessor(mock_wrapper, max_workers=2, fail_fast=False)
        prompts = ["Q1", "Q2"]

        results = processor.process_batch(prompts)

        assert len(results) == 2
        assert all(isinstance(r, Exception) for r in results)


class TestStreaming:
    """Test streaming functionality (mock-based)."""

    def test_stream_method_exists(self):
        """Test that wrappers have stream method."""
        from src.llm.config import OpenAIConfig
        from src.llm.openai_wrapper import OpenAIWrapper

        # We can't test without API key, but we can verify method exists
        assert hasattr(OpenAIWrapper, 'stream')

    def test_base_wrapper_stream_abstract(self):
        """Test that base wrapper has abstract stream method."""
        import inspect

        from src.llm.base import BaseLLMWrapper

        assert 'stream' in dir(BaseLLMWrapper)
        # Verify it's abstract
        assert inspect.isabstract(BaseLLMWrapper)


# Integration tests (require API keys)
@pytest.mark.skip(reason="Requires API keys")
class TestStreamingIntegration:
    """Integration tests for streaming (requires API key)."""

    def test_openai_streaming(self):
        """Test actual OpenAI streaming."""
        from src.llm.config import OpenAIConfig
        from src.llm.openai_wrapper import OpenAIWrapper

        config = OpenAIConfig(model="gpt-3.5-turbo", max_tokens=50)
        wrapper = OpenAIWrapper(config)

        chunks = []
        for chunk in wrapper.stream("Say hello"):
            chunks.append(chunk)
            assert isinstance(chunk, str)

        full_response = "".join(chunks)
        assert len(full_response) > 0


@pytest.mark.skip(reason="Requires API keys")
class TestBatchIntegration:
    """Integration tests for batch processing (requires API key)."""

    def test_batch_processing_real(self):
        """Test real batch processing."""
        from src.llm.batch import BatchProcessor
        from src.llm.config import OpenAIConfig
        from src.llm.openai_wrapper import OpenAIWrapper

        config = OpenAIConfig(model="gpt-3.5-turbo", max_tokens=20)
        wrapper = OpenAIWrapper(config)
        processor = BatchProcessor(wrapper, max_workers=2)

        prompts = ["Say hi", "Say hello", "Say hey"]
        results = processor.process_batch(prompts)

        assert len(results) == 3
        assert all(isinstance(r, str) for r in results)
