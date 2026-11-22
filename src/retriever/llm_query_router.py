
import json
import os
from enum import Enum
from typing import List, Literal

from prompts.prompts import SEC10K_TEMPLATES_DIR, PromptManager, Sec10KPrompts
from src.llm.config import SEC_TABLE_MODEL_CONFIGS
from src.logging_config import get_logger
from src.preprocess.sec10k.sec10k_toc import TENK_SECTIONS

logger = get_logger(__name__)


class ROUTER_DECISION(Enum):
    DIRECT = "DIRECT"
    PARAPHRASE = "PARAPHRASE"
    DECOMPOSE = "DECOMPOSE"
    DECOMPOSE_AND_PARAPHRASE = "DECOMPOSE_AND_PARAPHRASE"


ROUTER_RESPONSE_SCHEMA = {
  "type": "object",
  "additionalProperties": "false",
  "properties": {
    "route": { "type": "string", "enum": ["DIRECT", "PARAPHRASE", "DECOMPOSE", "DECOMPOSE_AND_PARAPHRASE"] },
    "reasons": { "type": "array", "items": { "type": "string" }, "minItems": 1, "maxItems": 5 },
    "estimated_subquestions": { "type": "integer", "minimum": 0, "maximum": 5 },
    "risk_flags": {
      "type": "array",
      "items": { "type": "string", "enum": ["multi_intent", "multi_entity", "multi_time_period", "needs_multi_sections", "metric_comparison", "ambiguous_terms"] }
    }
  },
  "required": ["route", "reasons", "estimated_subquestions", "risk_flags"]
}

ROUTER_DOC_SCOPE_INSTRUCTIONS = " for company mentioned in 10-K filings use 2024 if no year is specified, otherwise use the years mentioned "


class QueryRouter:

    def __init__(
        self,
        api_key: str | None = None,
        model: Literal["gpt-4o", "gpt-4o-mini", "gpt-5-mini", "gpt-5-nano"] = "gpt-5-mini",
        system_prompt_template: str = Sec10KPrompts.QUERY_ROUTER_SYSTEM.value,
        user_prompt_template: str = Sec10KPrompts.QUERY_DECOMPOSE_USER.value,
    ):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key required via param or OPENAI_API_KEY env var")

        self.model = model
        self.model_config = SEC_TABLE_MODEL_CONFIGS[model]
        self.sec10k_sections = "\n".join([f"- {section}" for section in TENK_SECTIONS])
        self.system_prompt_template = system_prompt_template
        self.user_prompt_template = user_prompt_template

        # Initialize prompt manager with SEC templates
        self.prompts = PromptManager(SEC10K_TEMPLATES_DIR)

        logger.info(f"Initialized SEC Query Router with model={model}")


    def route_query(self, query: str, **kwargs) -> ROUTER_DECISION:
        system_prompt_text = self.prompts.format(Sec10KPrompts.QUERY_ROUTER_SYSTEM.value, response_schema_json=ROUTER_RESPONSE_SCHEMA)
        user_prompt_text = self.prompts.format(Sec10KPrompts.QUERY_DECOMPOSE_USER.value,user_query=query, document_scope=ROUTER_DOC_SCOPE_INSTRUCTIONS)
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

        llm_response = self._do_llm_call(messages, **kwargs)
        router_decision = self._extract_routing_decision(llm_response)
        return router_decision


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


    def _extract_routing_decision(self, response_content: str) -> ROUTER_DECISION:
        try:
            response_json = json.loads(response_content)
            route_str = response_json.get("route")
            routing_factors = response_json.get("reasons", [])
            logger.info(f"routing decision: {route_str} based on factors:\n\n {routing_factors} \n\n")
            return ROUTER_DECISION(route_str)
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.error(f"Error parsing routing decision: {e}")
            return ROUTER_DECISION.DIRECT


if __name__ == "__main__":
    # Example usage
    router = QueryRouter(model="gpt-5-mini")
    query1 = "What are the risk factors mentioned by Google?"
    decision1 = router.route_query(query1)
    print(f"Routing decision: {decision1}")
    query2 = "Using the 2024 disclosures for revenues by geography and long-lived assets by geographic area, what were the approximate U.S. shares of (a) total revenues and (b) total long-lived assets in 2024, and by how many percentage points was the U.S. asset share higher than the U.S. revenue share?"
    decision2 = router.route_query(query2)
    print(f"Routing decision: {decision2}")
