from __future__ import annotations

import os
from collections.abc import Sequence

from openai import OpenAI
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.embeddings.base import BaseEmbeddingGenerator
from src.logging_config import get_logger

logger = get_logger(__name__)


class OpenAIEmbeddingGenerator(BaseEmbeddingGenerator):

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key: str | None = None,
        embedding_type: str | None = None,
    ):
        self.model_name = model
        resolved_key = api_key or os.getenv("OPENAI_API_KEY")
        if not resolved_key:
            raise ValueError("OpenAI API key must be set via OPENAI_API_KEY or constructor.")

        self.client = OpenAI(api_key=resolved_key)

        super().__init__(embedding_type or "openai")
        logger.info("Initialized OpenAIEmbeddingGenerator with model=%s", model)

    def dense_embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []

        logger.debug("Requesting OpenAI embeddings for %s texts", len(texts))
        response = self._embed_batch_with_retry(list(texts))

        vectors = [item.embedding for item in response.data]
        logger.debug("Received %s OpenAI embeddings", len(vectors))
        return vectors

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    def _embed_batch_with_retry(self, texts: list[str]):
        logger.debug("Calling OpenAI embeddings API (batch=%s)", len(texts))
        return self.client.embeddings.create(model=self.model_name, input=texts)
