import json
import os
import time
from typing import List, Literal, Tuple

from pydantic import BaseModel

from prompts.prompts import SEC10K_TEMPLATES_DIR, PromptManager, Sec10KPrompts
from src.llm.config import SEC_TABLE_MODEL_CONFIGS
from src.logging_config import get_logger

logger = get_logger(__name__)


LLM_ANSWER_RESPONSE_FORMAT = {
  "answer_summary": "string - a concise, human-readable overall answer in 3 to 4 sentences, limited ONLY to what is supported by the context. If the question is not fully answerable, clearly say which parts are unknown.",
  "answer_details": [
    {
      "statement": "string - one specific factual statement or conclusion you are making.",
      "chunk_ids": "array of strings - list of chunk IDs from which this statement was derived."
    }
  ],
  "unanswered_parts": [
    {
      "question_fragment": "string - the part of the user query you could NOT answer from the context.",
      "reason": "string - short explanation, e.g., 'No numeric data found in context for 2024 capex.'"
    }
  ],
  "metadata": {
    "fully_answered": "true" or "false",
    "notes": "string - any important caveats about data quality, ambiguity, or cross-chunk inconsistencies."
  }
}


class RagResponse(BaseModel):
    query: str
    answer_summary: str
    answer_details: list[dict[str, str | list[str]]]
    retrieved_chunks: list[tuple[str,str]]


class RAGResultGenerator:

    def __init__(self,
                api_key: str | None = None,
                model: Literal["gpt-4o", "gpt-4o-mini", "gpt-5-mini", "gpt-5-nano"] = "gpt-5-mini",
                rag_result_system_prompt: str = Sec10KPrompts.RAG_RESULT_SYSTEM.value,
                rag_result_user_prompt: str = Sec10KPrompts.RAG_RESULT_USER.value,):

        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key required via param or OPENAI_API_KEY env var")

        self.model = model
        self.model_config = SEC_TABLE_MODEL_CONFIGS[model]
        self.rag_result_system_prompt = rag_result_system_prompt
        self.rag_result_user_prompt = rag_result_user_prompt

        # Initialize prompt manager with SEC templates
        self.prompts = PromptManager(SEC10K_TEMPLATES_DIR)

        logger.info(f"Initialized RAG Query Result Generator with model={model}")


    def generate_answer(self, query: str, retrieved_chunks: list[tuple[str,str]]) -> RagResponse:
        rag_query_messages = self._get_rag_query_messages(query, retrieved_chunks)
        logger.debug(f"\n\n RAG Query Messages prepared for LLM call: {rag_query_messages}\n\n")
        llm_response = self._do_llm_call(rag_query_messages)
        logger.info(f"RAG LLM Response: {llm_response}")
        try:
            llm_response = json.loads(llm_response)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse RAG LLM response as JSON: {e}")
            raise
        rag_answer_response = self._extract_rag_answer(query, retrieved_chunks, llm_response)
        return rag_answer_response


    def _get_rag_query_messages(self, query: str, retrieved_chunks: list[tuple[str,str]]) -> list[dict]:
        system_prompt_text = self.prompts.format(Sec10KPrompts.RAG_RESULT_SYSTEM.value, llm_answer_format=LLM_ANSWER_RESPONSE_FORMAT)

        context_text_list = []
        [context_text_list.append(f"{chunk_id} : {content}") for chunk_id, content in retrieved_chunks]
        user_prompt_text = self.prompts.format(Sec10KPrompts.RAG_RESULT_USER.value, user_query=query, context_chunks=context_text_list,)
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


    def _extract_rag_answer(self, query: str, retrieved_chunks: list[tuple[str, str]], llm_response: dict) -> RagResponse:
        try:
            response_json = llm_response
            # self._do_log_rag_answer(query, response_json)
            answer_summary = response_json.get("answer_summary", "")
            answer_details = response_json.get("answer_details", [])
            rag_reponse = RagResponse(
                query=query,
                answer_summary=answer_summary,
                answer_details=answer_details,
                retrieved_chunks=retrieved_chunks
            )
            return rag_reponse
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            raise


    def _do_log_rag_answer(self, query: str, response_json: dict):
        logger.info(f"query={query}")
        answer_summary = response_json.get("answer_summary", "")
        logger.info(f"answer_summary={answer_summary}")
        answer_details = response_json.get("answer_details", [])
        for detail in answer_details:
            statement = detail.get("statement", "")
            chunk_ids = detail.get("chunk_ids", [])
            logger.info(f"answer_details: statement={statement}, chunk_ids={chunk_ids}")
        unanswered_parts = response_json.get("unanswered_parts", [])
        for part in unanswered_parts:
            question_fragment = part.get("question_fragment", "")
            reason = part.get("reason", "")
            logger.info(f"unanswered part: question_fragment={question_fragment}, reason={reason}")
        metadata = response_json.get("metadata", {})
        fully_answered = metadata.get("fully_answered", "")
        notes = metadata.get("notes", "")
        logger.info(f"fully_answered={fully_answered}, notes={notes}")
