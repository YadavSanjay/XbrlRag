from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import List

from src.logging_config import get_logger

logger = get_logger(__name__)


class EmbeddingType(Enum):
    DENSE = "dense"
    SPARSE = "sparse"


@dataclass
class DenseEmbedding:
    values: list[float]


@dataclass
class SparseEmbedding:
    indices: list[int]
    values: list[float]


EmbeddingKey = tuple[str, EmbeddingType]
EmbeddingMap = dict[str, tuple[DenseEmbedding,SparseEmbedding]]


class BaseEmbeddingGenerator(ABC):
    model_name: str

    @abstractmethod
    def dense_embed_texts(self, id_texts: list[tuple[str, str]]) -> list[tuple[str, DenseEmbedding]]:
        raise NotImplementedError

    @abstractmethod
    def sparse_embed_texts(self, id_texts: list[tuple[str, str]]) -> list[tuple[str, SparseEmbedding]]:
        raise NotImplementedError

    @abstractmethod
    def embed_texts(self, id_texts: list[tuple[str, str]]) -> EmbeddingMap:
        raise NotImplementedError

    def dict_to_sparse_list(self, sparse_dict: dict[int, float]) -> SparseEmbedding:
        indices = list(sparse_dict.keys())
        values = [sparse_dict[idx] for idx in indices]
        return SparseEmbedding(indices=indices, values=values)
