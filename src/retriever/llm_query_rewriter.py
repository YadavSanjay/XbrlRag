
import json
import os
import sys
import time
from typing import List, Literal

from prompts.prompts import SEC10K_TEMPLATES_DIR, PromptManager, Sec10KPrompts
from src.llm.config import SEC_TABLE_MODEL_CONFIGS
from src.logging_config import get_logger

logger = get_logger(__name__)


QUERY_DECOMP_SCHEMA = {
  "type": "object",
  "additionalProperties": "false",
  "properties": {
    "subquestions": {
      "type": "array",
      "minItems": 2,
      "maxItems": 5,
      "items": {
        "type": "object",
        "additionalProperties": "false",
        "properties": {
          "id": { "type": "string" },
          "question": { "type": "string" },
          "weight": { "type": "number" },
          "section_targets": {
            "type": "array",
            "minItems": 1,
            "maxItems": 2,
            "items": { "type": "string" }
          },
        },
        "required": ["id", "question", "weight", "section_targets"]
      }
    }
  },
  "required": ["subquestions"]
}



class QueryRewriter:

    def __init__(self,
            api_key: str | None = None,
            model: Literal["gpt-4o", "gpt-4o-mini", "gpt-5-mini", "gpt-5-nano"] = "gpt-5-mini",
            query_decomp_system_prompt: str = Sec10KPrompts.QUERY_DECOMPOSE_SYSTEM.value,
            query_decomp_user_prompt: str = Sec10KPrompts.QUERY_DECOMPOSE_USER.value,
            query_paraphrase_system_prompt: str = Sec10KPrompts.QUERY_PARAPHRASE_SYSTEM.value,
            query_paraphrase_user_prompt: str = Sec10KPrompts.QUERY_PARAPHRASE_USER.value,):

        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key required via param or OPENAI_API_KEY env var")

        self.model = model
        self.model_config = SEC_TABLE_MODEL_CONFIGS[model]
        self.query_decomp_system_prompt = query_decomp_system_prompt
        self.query_decomp_user_prompt = query_decomp_user_prompt
        self.query_paraphrase_system_prompt = query_paraphrase_system_prompt
        self.query_paraphrase_user_prompt = query_paraphrase_user_prompt

        # Initialize prompt manager with SEC templates
        self.prompts = PromptManager(SEC10K_TEMPLATES_DIR)

        logger.info(f"Initialized SEC Query Re-writer with model={model}")


    def decompose_paraphrase_query(self, query: str) -> list[tuple[str, float]]:
            start_time = time.perf_counter()
            # Decompose query into sub-queries
            decomp_query_messages = self._get_query_decompose_messages(query)
            decomp_query_response = self._do_llm_call(decomp_query_messages)
            if not decomp_query_response:
                raise ValueError("Empty response from query decomposition LLM call")
            try:
                decomp_data = json.loads(decomp_query_response)
            except json.JSONDecodeError as e:
                logger.error(f"JSON decode error in query decomposition response: {e}")
                raise
            subquestions = decomp_data.get("subquestions", [])
            if not subquestions:
                raise ValueError("No subquestions found in decomposition response")
            rewritten_queries = []
            for subq in subquestions:
                logger.debug(f"Processing sub-question: {subq}")
                sub_query_text = subq.get("question", "").strip()
                sub_query_weight = subq.get("weight", 1.0)
                sub_query_section_targets = subq.get("section_targets", [])
                query_sections = ", ".join(sub_query_section_targets) if sub_query_section_targets else "general"
                if not sub_query_text:
                    logger.warning(f"Skipping empty sub-question in decomposition response: {subq}")
                    continue
                rewritten_queries.append((sub_query_text, sub_query_weight))

                ## Paraphrase sub-query - decomposition already provides focused queries
                # paraphrase_messages = self._get_query_paraphrase_messages(sub_query_text, query_sections)
                # paraphrased_response = self._do_llm_call(paraphrase_messages)
                # if not paraphrased_response:
                #     logger.warning(f"Empty paraphrase response for sub-question: {sub_query_text}. Using original.")
                #     rewritten_queries.append((sub_query_text, sub_query_weight))
                #     continue
                # rewritten_queries.append((paraphrased_response.strip(), sub_query_weight))

            end_time = time.perf_counter()
            total_time = end_time - start_time
            logger.info(f"\n Total time for query rewriting: {total_time:.2f} seconds\n")
            self._log_query_and_rewritten_queries(query, rewritten_queries)
            return rewritten_queries


    def paraphrase_query(self, sub_query: str, query_sections: str = "general") -> str:
        paraphrase_messages = self._get_query_paraphrase_messages(sub_query, query_sections)
        paraphrased_response = self._do_llm_call(paraphrase_messages)
        if not paraphrased_response:
            logger.warning(f"Empty paraphrase response for sub-question: {sub_query}. Using original.")
            return sub_query
        logger.info(f"Original query: {sub_query}\n")
        logger.info(f"Paraphrased query: {paraphrased_response.strip()}\n")
        return paraphrased_response.strip()


    def _get_query_decompose_messages(self, query: str) -> list[dict]:
        system_prompt_text = self.prompts.format(Sec10KPrompts.QUERY_DECOMPOSE_SYSTEM.value, decomposed_queries_json=QUERY_DECOMP_SCHEMA)
        user_prompt_text = self.prompts.format(Sec10KPrompts.QUERY_DECOMPOSE_USER.value,user_query=query)
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


    def _get_query_paraphrase_messages(self, sub_query: str, query_sections: str = "general") -> list[dict]:
        system_prompt_text = self.prompts.format(Sec10KPrompts.QUERY_PARAPHRASE_SYSTEM.value)
        user_prompt_text = self.prompts.format(Sec10KPrompts.QUERY_PARAPHRASE_USER.value,query=sub_query, query_sections=query_sections)
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

            llm_response = getattr(choice.message, "content", None)
            if not llm_response or not str(llm_response).strip():
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

            return llm_response
        except Exception as e:
            logger.error(f"exception seen during query router - API error: {e}")
            raise


    def _log_query_and_rewritten_queries(self, original_query: str, rewritten_queries: list[tuple[float,str]]) -> None:
        logger.info(f"Original Query: {original_query}\n")
        for idx, rq in enumerate(rewritten_queries, start=1):
            logger.info(f"Rewritten Query {idx}: {rq[0]}: {rq[1]}")


if __name__ == "__main__":
    query_rewriter = QueryRewriter()
    test_query = "Using the 2024 disclosures for revenues by geography and long-lived assets by geographic area, what were the approximate U.S. shares of (a) total revenues and (b) total long-lived assets in 2024, and by how many percentage points was the U.S. asset share higher than the U.S. revenue share?"
    query_rewriter.decompose_paraphrase_query(test_query)
