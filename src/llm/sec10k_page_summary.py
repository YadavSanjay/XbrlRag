
import os
from typing import Any, Literal

from prompts.prompts import SEC10K_TEMPLATES_DIR, PromptManager, Sec10KPrompts
from src import BASE_DATA_DIR
from src.llm.config import SEC_TABLE_MODEL_CONFIGS
from src.logging_config import get_logger
from src.preprocess.sec10k.sec10k_toc import TENK_SECTIONS

logger = get_logger(__name__)



class SEC10KPageSummarizer:
    """Summarizer for SEC 10-K pages using LLMs."""

    def __init__(
        self,
        api_key: str | None = None,
        model: Literal["gpt-4o", "gpt-4o-mini", "gpt-5-mini", "gpt-5-nano"] = "gpt-5-nano",
        prompt_template: str = Sec10KPrompts.PAGE_SUMMARY_BASIC.value,
    ):
        """Initialize SEC 10-K page summarizer.
        
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
        self.sec10k_secions = "\n".join([f"- {section}" for section in TENK_SECTIONS])
        self.prompt_template = prompt_template

        # Initialize prompt manager with SEC templates
        self.prompts = PromptManager(SEC10K_TEMPLATES_DIR)

        logger.info(f"Initialized SEC Page Summarizer with model={model}")


    def summarize_sec10k_page(self, page_text: str, **kwargs: Any,) -> dict:
        """
        Generate summary, keywords, and section classification for a SEC 10-K page.
        
        Args:
                page_text: The main text content of the page
                title: The page title (optional)
                header: The page header (optional)
                llm_client: LLM client instance for making API calls
                
        Returns:
                dict with keys: summary, keywords, section
        """

        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("Install openai package: pip install openai>=1.0.0")

        client = OpenAI(api_key=self.api_key)

        prompt_text = self.prompts.format(Sec10KPrompts.PAGE_SUMMARY_BASIC.value,
                                     sections=self.sec10k_secions,
                                     text=page_text[:4000]  # Limit text length for token efficiency
        )

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt_text,
                    }
                ]
            }
        ]

        # Merge config with kwargs
        api_params = {
            "model": self.model_config["model"],
            "messages": messages,
            "max_completion_tokens": self.model_config.get("max_completion_tokens", 2000),
            "temperature": self.model_config.get("temperature", 0.1),
            **kwargs,
        }
        logger.debug(f"Calling {self.model} with max_completion_tokens={api_params['max_completion_tokens']}")
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
                    f"Text Summary API call: {usage.prompt_tokens} prompt + "
                    f"{usage.completion_tokens} completion = {usage.total_tokens} tokens"
                )
            else:
                logger.warning("No usage metrics returned from LLM response")

            return summary

        except Exception as e:
            logger.error(f"Text Summary API error: {e}")
            raise


if __name__ == "__main__":
    # Example usage
    summarizer = SEC10KPageSummarizer()
    from unstructured.partition.pdf import partition_pdf
    UNSTRUCTURED_HIRES_MODEL_NAME = "yolox"

    year = "2024"
    foldername = f"{year}-10K-COMMUNICATIONSERVICES-GOOGL"
    page = 103
    file_path = BASE_DATA_DIR / year / foldername / "pages" / f"{foldername}_page_{page}.pdf"

    elements = partition_pdf(filename=file_path,languages=["eng"], strategy="hi_res", hi_res_model_name=UNSTRUCTURED_HIRES_MODEL_NAME)
    page_text = ""
    for element in elements:
        if element.category != "Table":
            if element.category == "Header":
                page_text += f"<header> {element.text}\n"
            elif element.category == "Title":
                page_text += f"<title> {element.text}\n"
            else:
                page_text += f"{element.text}\n"
    print(f"len(page_text)={len(page_text)} - page_text={page_text}")
    result = summarizer.summarize_sec10k_page(page_text)
    print("result:", result)
