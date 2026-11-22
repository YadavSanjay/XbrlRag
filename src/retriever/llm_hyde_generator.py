
import json
import os
import time
from typing import List, Literal

from prompts.prompts import SEC10K_TEMPLATES_DIR, PromptManager, Sec10KPrompts
from src.llm.config import SEC_TABLE_MODEL_CONFIGS
from src.logging_config import get_logger

logger = get_logger(__name__)


HYDE_ANSWER_RESPONSE_SCHEMA = {
  "subquestion": "{subquestion_text}",
  "variants": [
    "<Variant 1 text here>",
    "<Variant 2 text here>"
  ]
}


class HyDEAnswerGenerator:

    def __init__(self,
            api_key: str | None = None,
            model: Literal["gpt-4o", "gpt-4o-mini", "gpt-5-mini", "gpt-5-nano"] = "gpt-5-mini",
            hyde_generator_system_prompt: str = Sec10KPrompts.HYDE_GENERATOR_SYSTEM.value,
            hyde_generator_user_prompt: str = Sec10KPrompts.HYDE_GENERATOR_USER.value,):

        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key required via param or OPENAI_API_KEY env var")

        self.model = model
        self.model_config = SEC_TABLE_MODEL_CONFIGS[model]
        self.hyde_generator_system_prompt = hyde_generator_system_prompt
        self.hyde_generator_user_prompt = hyde_generator_user_prompt

        # Initialize prompt manager with SEC templates
        self.prompts = PromptManager(SEC10K_TEMPLATES_DIR)

        logger.info(f"Initialized SEC HyDE Answer Generator with model={model}")


    def generate_hyde_answer(self, query: str) -> list[str]:
        start_time = time.perf_counter()
        hyde_query_messages = self._get_hyde_generator_messages(query)
        hyde_answer_response = self._do_llm_call(hyde_query_messages)
        logger.debug(f"HyDE LLM Response: {hyde_answer_response}")
        try:
            llm_response = json.loads(hyde_answer_response)
            hyde_answers = llm_response.get("variants", [])
            total_time = time.perf_counter() - start_time
            logger.info(f"\nTotal time for HyDE answer generation: {total_time:.2f} seconds\n")
            self._do_log_hyde_answers(query, hyde_answers)
            return hyde_answers
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse HyDE LLM response as JSON: {e}")
            raise


    def _get_hyde_generator_messages(self, query: str) -> list[dict]:
        system_prompt_text = self.prompts.format(Sec10KPrompts.HYDE_GENERATOR_SYSTEM.value)
        user_prompt_text = self.prompts.format(Sec10KPrompts.HYDE_GENERATOR_USER.value,
                                               answer_response_json=HYDE_ANSWER_RESPONSE_SCHEMA,
                                               subquestion_text=query, n_variants=2)
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
                        "text": user_prompt_text,
                    },
                ],
            }
        ]
        return messages


    def _do_llm_call(self, messages: list[dict], **kwargs) -> str:
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("Install openai package: pip install openai>=1.0.0")

        client = OpenAI(api_key=self.api_key)

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

            llm_reponse = getattr(choice.message, "content", None)
            if not llm_reponse or not str(llm_reponse).strip():
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

            return llm_reponse
        except Exception as e:
            logger.error(f"exception seen during query router - API error: {e}")
            raise


    def _do_log_hyde_answers(self, query: str, hyde_answers: list[str]) -> None:
        logger.info(f"\n query={query}\n")
        for idx, answer in enumerate(hyde_answers, start=1):
            logger.info(f"{idx}. {answer}")


if __name__ == "__main__":
    hyde_generator = HyDEAnswerGenerator()
    test_query = "Using the 2024 disclosures for revenues by geography and long-lived assets by geographic area, what were the approximate U.S. shares of (a) total revenues and (b) total long-lived assets in 2024, and by how many percentage points was the U.S. asset share higher than the U.S. revenue share?"
    #test_query = "What are the risk factors mentioned by Google?"
    hyde_generator.generate_hyde_answer(test_query)
