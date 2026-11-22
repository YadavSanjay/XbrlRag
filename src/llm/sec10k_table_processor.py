"""SEC 10K Table Summarization utilities using OpenAI Vision models.

This module provides specialized helpers for processing tables from SEC 10K filings

Example:
    >>> processor = SEC10KTableProcessor(api_key="sk-...")
    >>> summary = processor.summarize_table(
    ...     image_path="table.jpg",
    ...     company_name="Apple Inc.",
    ...     filing_year="2024",
    ...     table_header="Consolidated Statements of Operations",
    ...     table_footer="All amounts in millions except per share",
    ... )
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any, Literal

from prompts.prompts import SEC10K_TEMPLATES_DIR, PromptManager, Sec10KPrompts
from src.llm.config import SEC_TABLE_MODEL_CONFIGS
from src.logging_config import get_logger

logger = get_logger(__name__)


class SEC10KTableProcessor:
    """Process SEC 10K tables using OpenAI Vision models.

    Optimized for extracting structured information from table images
    with context from headers and footers.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: Literal["gpt-4o", "gpt-4o-mini", "gpt-5-mini", "gpt-5-nano"] = "gpt-5-nano",
        system_prompt: str = Sec10KPrompts.TABLE_SUMMARY_SYSTEM_PROMPT.value,
        prompt_template: str = Sec10KPrompts.TABLE_SUMMARY_AND_DATA.value,
    ):
        """Initialize SEC table processor.

        Args:
            api_key: OpenAI API key (reads from env if None)
            model: Vision model to use
            prompt_template: Name of prompt template to use
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key required via param or OPENAI_API_KEY env var")

        self.model = model
        self.model_config = SEC_TABLE_MODEL_CONFIGS[model]
        self.system_prompt = system_prompt
        self.prompt_template = prompt_template

        # Initialize prompt manager with SEC templates
        self.prompts = PromptManager(SEC10K_TEMPLATES_DIR)

        logger.info(f"Initialized SEC Table Processor with model={model}")


    def summarize_table(
        self,
        image_path: str | Path,
        company_name: str,
        filing_year: str,
        table_header: str,
        table_footer: str = "",
        document_type: str = "10-K",
        page_number: str | None = None,
        system_prompt: str | None = None,
        prompt_template: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Summarize a table from SEC filing using vision model.

        Args:
            image_path: Path to table image (JPG, PNG, etc.)
            company_name: Company name (e.g., "Apple Inc.")
            filing_year: Filing year (e.g., "2024")
            table_header: Text from table header/title
            table_footer: Text from table footer/notes
            document_type: Document type (default "10-K")
            page_number: Optional page number   
            system_prompt: Override default system prompt
            prompt_template: Override default prompt template
            **kwargs: Additional kwargs for API call

        Returns:
            Plain text summary of the table

        Raises:
            FileNotFoundError: If image file doesn't exist
            Exception: On API errors
        """
        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

        # Encode image to base64
        image_base64 = self._encode_image(image_path)

        # Format prompt with context
        system_prompt = system_prompt or self.system_prompt
        template_name = prompt_template or self.prompt_template
        prompt_text = self.prompts.format(
            template_name,
            company_name=company_name,
            filing_year=filing_year,
            document_type=document_type,
            table_header=table_header,
            table_footer=table_footer or "None",
            page_number=page_number or "N/A",
        )
        system_prompt_text = self.prompts.format(system_prompt)

        logger.debug(
            f"Processing table for {company_name} ({filing_year}) "
            f"using {self.model} with template '{template_name}'"
        )

        # Call OpenAI Vision API
        summary = self._call_vision_api(system_prompt_text, prompt_text, image_base64, **kwargs)

        return summary

    # TODO: This should be moved to a pipline utility module
    def summarize_table_batch(
        self,
        tables: list[dict[str, Any]],
        prompt_template: str | None = None,
        max_workers: int = 3,
    ) -> list[str]:
        """Process multiple tables in parallel.

        Args:
            tables: List of dicts with keys: image_path, company_name, filing_year,
                    table_header, table_footer, etc.
            prompt_template: Override default prompt template
            max_workers: Max parallel workers

        Returns:
            List of summaries in same order as input
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results = [None] * len(tables)

        logger.info(f"Processing batch of {len(tables)} tables with {max_workers} workers")

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_idx = {
                executor.submit(
                    self.summarize_table,
                    prompt_template=prompt_template,
                    **table_data,
                ): i
                for i, table_data in enumerate(tables)
            }

            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    results[idx] = future.result()
                    logger.debug(f"Completed table {idx + 1}/{len(tables)}")
                except Exception as e:
                    logger.error(f"Error processing table {idx}: {e}")
                    results[idx] = f"ERROR: {e}"

        successes = sum(1 for r in results if not isinstance(r, str) or not r.startswith("ERROR"))
        logger.info(f"Batch complete: {successes}/{len(tables)} succeeded")

        return results

    def _encode_image(self, image_path: Path) -> str:
        """Encode image file to base64 string.

        Args:
            image_path: Path to image file

        Returns:
            Base64 encoded string
        """
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def _call_vision_api(
        self,
        system_prompt_text: str,
        prompt_text: str,
        image_base64: str,
        **kwargs: Any,
    ) -> str:
        """Call OpenAI Vision API with prompt and image.

        Args:
            system_prompt_text: System prompt text
            prompt_text: Text prompt
            image_base64: Base64 encoded image
            **kwargs: Additional API parameters

        Returns:
            Model response text
        """
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("Install openai package: pip install openai>=1.0.0")

        client = OpenAI(api_key=self.api_key)

        # Build messages with vision format
        messages = [
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": system_prompt_text,
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt_text,
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_base64}",
                            "detail": self.model_config.get("detail", "high"),
                        },
                    },
                ],
            }
        ]

        # Merge config with kwargs
        api_params = {
            "model": self.model_config["model"],
            "messages": messages,
            "max_tokens": self.model_config.get("max_tokens", 2000),
            "temperature": self.model_config.get("temperature", 0.1),
            **kwargs,
        }

        logger.debug(f"Calling {self.model} with max_tokens={api_params['max_tokens']}")

        try:
            response = client.chat.completions.create(**api_params)
            if not response or not getattr(response, "choices", None):
                raise ValueError("LLM returned an empty response or no choices")

            choice = response.choices[0]
            finish_reason = getattr(choice, "finish_reason", None)
            if finish_reason == "content_filter":
                raise ValueError("LLM response blocked by content filter")
            if finish_reason == "length":
                logger.warning("LLM response truncated: finish_reason=length")
            elif finish_reason not in (None, "stop", "tool_calls", "function_call"):
                logger.warning(f"Unexpected finish_reason: {finish_reason}")

            summary = getattr(choice.message, "content", None)
            if not summary or not str(summary).strip():
                raise ValueError("LLM returned empty message content")

            # Log token usage
            usage = getattr(response, "usage", None)
            if usage:
                logger.info(
                    f"Vision API call: {usage.prompt_tokens} prompt + "
                    f"{usage.completion_tokens} completion = {usage.total_tokens} tokens"
                )
            else:
                logger.warning("No usage metrics returned from LLM response")

            return summary

        except Exception as e:
            logger.error(f"Vision API error: {e}")
            raise


if __name__ == "__main__":
    # Unit test / example usage
    api_key = os.getenv("OPENAI_API_KEY")
    model = "gpt-4o-mini"

    system_prompt = Sec10KPrompts.TABLE_SUMMARY_SYSTEM_PROMPT.value
    template = Sec10KPrompts.TABLE_SUMMARY_AND_DATA.value

    processor = SEC10KTableProcessor(api_key=api_key, model=model, prompt_template=template, system_prompt=system_prompt)
    folder = "2024-10K-FINANCIALS-JPM"
    page = 135
    table_number = 2
    company_name = "J.P.Morgan Chase & Co."
    filing_year = "2024"
    header = "The table below sets forth wholesale loan maturities and the distribution between fixed and floating interest rates based on the stated terms of the loan agreements by loan class. Refer to Note 12 for further information on loan classes."
    footer = "(a) Includes loans that have an initial fixed interest rate that resets to a variable rate as the variable rate will be the prevailing rate over the life of the loan.. (b) Includes loans held-for-sale, demand loans and overdrafts."
    summary = processor.summarize_table(
        image_path=f"data\\pagebreakdown\\{folder}\\tables\\page_{page}\\table_{table_number}.jpeg",
        company_name=company_name,
        filing_year=filing_year,
        table_header=header,
        table_footer=footer,
        prompt_template=template,
    )

    print(f"Table Summary with {template}:")
    print(summary)

    output_dir = Path("data/temp/llm_test")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"summary_{model}_{folder}_page_{page}_table_{table_number}.txt"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(summary)
    print(f"Summary saved to: {output_file}")
