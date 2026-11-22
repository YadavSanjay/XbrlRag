"""RagInference endpoint resolution — deliberately dependency-free.

This lives apart from :mod:`src.retriever.remote_reranker` because of the
**torch-free contract**: the ingest and serving images embed and rerank over HTTP
(``RAGINFER_URL``) precisely so they never install torch. ``resolve_endpoint``
originally sat in ``remote_reranker``, which imports ``BaseReranker`` ->
``retriever_util`` -> ``torch`` — so ``RemoteEmbedder``, the class whose entire
purpose is avoiding torch, transitively required it and blew up at run time with
``ModuleNotFoundError: No module named 'torch'`` inside the ingest container.

Keep this module importable with nothing but the standard library.
"""

from __future__ import annotations

import os
from typing import Optional


def resolve_endpoint(path: str, explicit: str | None = None) -> str:
    """Resolve a service endpoint: an explicit full URL wins, else
    ``RAGINFER_URL`` base + *path* (e.g. ``'/rerank'``)."""
    if explicit:
        return explicit
    base = os.getenv("RAGINFER_URL", "").strip().rstrip("/")
    return f"{base}{path}" if base else ""
