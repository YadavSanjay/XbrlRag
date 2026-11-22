"""Tests for SEC table processing functionality.

The SEC 10-K table-summary prompts live as files under
``prompts/templates/sec10k/`` (loaded via ``PromptManager(SEC10K_TEMPLATES_DIR)``
and referenced through the ``Sec10KPrompts`` enum), not in the in-code
``DEFAULT_TEMPLATES`` dict. These tests validate that file-based surface and the
current vision model configs.
"""
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from prompts.prompts import SEC10K_TEMPLATES_DIR, PromptManager, Sec10KPrompts

# Current SEC table-summary templates (file stems under prompts/templates/sec10k/).
SEC_TABLE_CONTENT_TEMPLATES = [
    Sec10KPrompts.TABLE_SUMMARY_BASIC.value,
    Sec10KPrompts.TABLE_SUMMARY_DETAILED.value,
    Sec10KPrompts.TABLE_SUMMARY_AND_DATA.value,
]
SEC_TABLE_SYSTEM_TEMPLATE = Sec10KPrompts.TABLE_SUMMARY_SYSTEM_PROMPT.value


class TestSECTablePrompts:
    """Test SEC table prompt templates (file-based)."""

    def test_all_sec_prompts_exist(self):
        """Every SEC table template resolves from the SEC10K template dir."""
        manager = PromptManager(SEC10K_TEMPLATES_DIR)
        for name in SEC_TABLE_CONTENT_TEMPLATES + [SEC_TABLE_SYSTEM_TEMPLATE]:
            assert manager.get(name) is not None, f"Missing prompt: {name}"

    def test_sec_prompts_have_required_variables(self):
        """Content templates expose the company/year/table context variables."""
        manager = PromptManager(SEC10K_TEMPLATES_DIR)
        required_vars = {"company_name", "filing_year", "table_header", "table_footer"}

        for name in SEC_TABLE_CONTENT_TEMPLATES:
            template = manager.get(name)
            missing = required_vars - template.variables
            assert not missing, f"{name} missing required vars: {missing}"

    def test_sec_prompt_formatting(self):
        """Content templates format with table context and substitute values."""
        manager = PromptManager(SEC10K_TEMPLATES_DIR)
        test_data = {
            "company_name": "Apple Inc.",
            "filing_year": "2024",
            "document_type": "10-K",
            "page_number": "42",
            "table_header": "Consolidated Statements of Operations",
            "table_footer": "All amounts in millions except per share data",
        }

        for name in SEC_TABLE_CONTENT_TEMPLATES:
            formatted = manager.format(name, **test_data)
            assert "Apple Inc." in formatted
            assert "2024" in formatted
            assert "Consolidated Statements of Operations" in formatted


class TestSECTableProcessor:
    """Test SEC table processor class."""

    def test_processor_initialization(self, monkeypatch):
        """Missing API key (param and env) raises."""
        from src.llm.sec10k_table_processor import SEC10KTableProcessor

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(ValueError, match="API key"):
            SEC10KTableProcessor(api_key=None)

    def test_processor_with_api_key(self):
        """Test processor initialization with API key."""
        from src.llm.sec10k_table_processor import SEC10KTableProcessor

        processor = SEC10KTableProcessor(
            api_key="test-key",
            model="gpt-4o",
            prompt_template=Sec10KPrompts.TABLE_SUMMARY_BASIC.value,
        )

        assert processor.api_key == "test-key"
        assert processor.model == "gpt-4o"
        assert processor.prompt_template == Sec10KPrompts.TABLE_SUMMARY_BASIC.value

    def test_processor_model_configs(self):
        """The vision models expose the fields _call_vision_api relies on."""
        from src.llm.config import SEC_TABLE_MODEL_CONFIGS

        # Vision-capable models carry the OpenAI vision knobs (detail/max_tokens).
        vision_models = ["gpt-4o", "gpt-4o-mini"]

        for model in vision_models:
            assert model in SEC_TABLE_MODEL_CONFIGS
            config = SEC_TABLE_MODEL_CONFIGS[model]
            assert "model" in config
            assert "max_tokens" in config
            assert "temperature" in config
            assert "detail" in config
            # Temperature should be low for factual extraction.
            assert config["temperature"] <= 0.2

    def test_encode_image(self):
        """Test image encoding to base64."""
        from src.llm.sec10k_table_processor import SEC10KTableProcessor

        processor = SEC10KTableProcessor(api_key="test-key")

        with tempfile.NamedTemporaryFile(mode='wb', suffix='.jpg', delete=False) as f:
            f.write(b'fake image data')
            temp_path = Path(f.name)

        try:
            encoded = processor._encode_image(temp_path)
            assert isinstance(encoded, str)
            assert len(encoded) > 0
        finally:
            temp_path.unlink()

    def test_summarize_table_missing_image(self):
        """Test error when image file doesn't exist."""
        from src.llm.sec10k_table_processor import SEC10KTableProcessor

        processor = SEC10KTableProcessor(api_key="test-key")

        with pytest.raises(FileNotFoundError):
            processor.summarize_table(
                image_path="nonexistent.jpg",
                company_name="Test",
                filing_year="2024",
                table_header="Header",
                table_footer="Footer",
            )

    @patch('openai.OpenAI')
    def test_summarize_table_success(self, mock_openai_class):
        """Test successful table summarization.

        ``_call_vision_api`` does a function-local ``from openai import OpenAI``,
        so the patch target is ``openai.OpenAI`` (not a module attribute).
        """
        from src.llm.sec10k_table_processor import SEC10KTableProcessor

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Test summary"
        mock_response.choices[0].finish_reason = "stop"
        mock_response.usage = MagicMock(prompt_tokens=100, completion_tokens=50, total_tokens=150)
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client

        with tempfile.NamedTemporaryFile(mode='wb', suffix='.jpg', delete=False) as f:
            f.write(b'fake image data')
            temp_path = Path(f.name)

        try:
            processor = SEC10KTableProcessor(api_key="test-key")
            summary = processor.summarize_table(
                image_path=str(temp_path),
                company_name="Apple Inc.",
                filing_year="2024",
                table_header="Income Statement",
                table_footer="Amounts in millions",
            )

            assert summary == "Test summary"
            assert mock_client.chat.completions.create.called
        finally:
            temp_path.unlink()


class TestSECTableIntegration:
    """Integration tests for SEC table processing."""

    def test_prompt_manager_loads_sec_prompts(self):
        """PromptManager loads and formats the SEC table templates from disk."""
        manager = PromptManager(SEC10K_TEMPLATES_DIR)

        test_data = {
            "company_name": "Test Corp",
            "filing_year": "2024",
            "document_type": "10-K",
            "page_number": "1",
            "table_header": "Test Header",
            "table_footer": "Test Footer",
        }

        # The SEC10K template dir carries the full prompt set (>= 6 files).
        assert len(manager.list_templates()) >= 6

        for name in SEC_TABLE_CONTENT_TEMPLATES:
            prompt = manager.format(name, **test_data)
            assert "Test Corp" in prompt
            assert "2024" in prompt


# Integration tests requiring API key (skip by default)
@pytest.mark.skip(reason="Requires OpenAI API key and table images")
class TestSECTableProcessorIntegration:
    """Integration tests with real API (requires API key)."""

    def test_real_table_processing(self):
        """Test processing a real table image."""
        from src.llm.sec10k_table_processor import SEC10KTableProcessor

        processor = SEC10KTableProcessor(model="gpt-4o-mini")
        summary = processor.summarize_table(
            image_path="path/to/real/table.jpg",
            company_name="Apple Inc.",
            filing_year="2024",
            table_header="Consolidated Statements of Operations",
            table_footer="All amounts in millions",
        )

        assert len(summary) > 0
        assert "Apple" in summary or "revenue" in summary.lower()

    def test_batch_processing_real(self):
        """Test batch processing with real tables."""
        from src.llm.sec10k_table_processor import SEC10KTableProcessor

        processor = SEC10KTableProcessor(model="gpt-4o-mini")
        tables = [
            {
                "image_path": "table1.jpg",
                "company_name": "Test Corp",
                "filing_year": "2024",
                "table_header": "Income Statement",
                "table_footer": "",
            }
        ]

        summaries = processor.summarize_table_batch(tables, max_workers=1)
        assert len(summaries) == 1
        assert isinstance(summaries[0], str)
