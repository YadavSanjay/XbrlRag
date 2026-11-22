# The settings of the HTML 10-K flow's retrieval and load.
#
# Spec: docs/specs/html-rag-arm.md.

from __future__ import annotations

import os
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src import PROJECT_ROOT
from src.llm.profiles import expand_placeholders

_DEFAULT_PATH = Path(PROJECT_ROOT) / "configs" / "pipelines" / "html10k" / "rag.yaml"


class Html10kRagConfig(BaseModel):
    """The validated settings. No field has a default: the committed file holds the values.

    Attributes:
        rrf_k: The RRF constant.
        dense_weight: The RRF weight of the dense search.
        sparse_weight: The RRF weight of the sparse search.
        prefetch_limit: Candidates per vector before fusion.
        rerank_candidates: Fused candidates sent to the reranker.
        context_top_k: Reranked chunks kept, per search.
        reranker_model: Sent to RagInference; part of the rerank cache's key.
        expand_neighbors: Whether a well-scored chunk brings its neighbours and table parts.
        expansion_score_threshold: The rerank score from which a chunk expands, on jina's scale.
        expansion_max_chunks: The most chunks expansion may add to one retrieval.
        max_filer_searches: With more filers than this, one search covers them all.
        table_max_chars: Passed to ``chunk_filing`` by the load.
        embed_batch_size: Texts per embedding request in the load.
        rerank_cache_path: The rerank cache's file.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    rrf_k: int = Field(ge=1)
    dense_weight: float = Field(gt=0, allow_inf_nan=False)
    sparse_weight: float = Field(gt=0, allow_inf_nan=False)
    prefetch_limit: int = Field(ge=1)
    rerank_candidates: int = Field(ge=1)
    context_top_k: int = Field(ge=1)
    reranker_model: str = Field(min_length=1)
    expand_neighbors: bool
    expansion_score_threshold: float = Field(allow_inf_nan=False)
    expansion_max_chunks: int = Field(ge=0)
    max_filer_searches: int = Field(ge=1)
    table_max_chars: int = Field(ge=1)
    embed_batch_size: int = Field(ge=1)
    rerank_cache_path: Path

    @field_validator("rerank_cache_path", mode="before")
    @classmethod
    def _resolve_cache_path(cls, value: object) -> Path:
        """Expand ``${VAR:-default}``; a relative path is under ``PROJECT_ROOT``."""
        path = Path(expand_placeholders(str(value), os.environ))
        return path if path.is_absolute() else Path(PROJECT_ROOT) / path

    @model_validator(mode="after")
    def _check_funnel(self) -> Self:
        """Each stage must narrow what the one before it passes on."""
        if self.rerank_candidates > self.prefetch_limit:
            raise ValueError(
                f"rerank_candidates {self.rerank_candidates} is over prefetch_limit "
                f"{self.prefetch_limit}"
            )
        if self.context_top_k > self.rerank_candidates:
            raise ValueError(
                f"context_top_k {self.context_top_k} is over rerank_candidates "
                f"{self.rerank_candidates}"
            )
        return self


def load_html10k_config(path: Path | None = None) -> Html10kRagConfig:
    """Load and validate the settings.

    Args:
        path: The YAML file; ``configs/pipelines/html10k/rag.yaml`` when omitted.

    Returns:
        The settings.

    Raises:
        ValueError: A key is missing or unknown, or a value is out of range.
    """
    source = _DEFAULT_PATH if path is None else path
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{source} does not hold a mapping")
    return Html10kRagConfig.model_validate(raw)
