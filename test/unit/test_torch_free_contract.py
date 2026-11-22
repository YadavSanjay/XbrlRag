"""The ingest and serving images are torch-free — assert it, don't assume it.

`Dockerfile.ingest` and `requirements-ingest.txt` both state the contract: ingest
embeds over HTTP via `RAGINFER_URL` (`RemoteEmbedder`) so it never installs torch.
Nothing enforced it, and an import chain quietly broke it —

    RemoteEmbedder -> remote_reranker.resolve_endpoint -> BaseReranker
                   -> retriever_util -> torch

so the class whose entire purpose is avoiding torch required it, and the vector
load died with `ModuleNotFoundError: No module named 'torch'` on the AWS runner,
after the embedding batch had already been prepared.

The same leak then bit the **serving** image, because this file only ever listed
the *ingest* entry points: the first real query on AWS died with

    xbrl_narrative_retriever -> jina_reranker -> base_reranker
                             -> retriever_util -> torch

at `/api/query/stream` — the reranker is chosen lazily and correctly (remote when
`RAGINFER_URL` is set), but the *module-level* import of the local one was
unconditional. Hence `SERVING_SLIM_MODULES` below, blocked against every heavy
lib `requirements-serve.txt` deliberately omits, not just torch.

These tests import the real entry points with those libs blocked, which is
exhaustive by construction: a new leak anywhere in the transitive graph fails
here rather than in a container three stages into a pipeline run.
"""

from __future__ import annotations

import builtins
import sys
from contextlib import contextmanager

import pytest

# Every module the torch-free images must be able to import. Add to this list
# when a new entry point joins the ingest or serving path.
TORCH_FREE_MODULES = [
    "src.embeddings.remote_embedder",
    "src.retriever.endpoints",
    "src.pipelines.xbrl.load_to_vectordb_pipeline",
    "src.pipelines.html10k.load_to_vectordb_pipeline",
    "src.eval.systems.html_rag",
    "src.chunking.xbrl_html_base_chunking",
    "src.ingest.sec_edgar.xbrl.html_canonicalizer",
    "src.ingest.sec_edgar.xbrl.dom_position_populator",
    "src.ingest.sec_edgar.ingest.arelle_xbrl_ingestor",
    "src.ingest.sec_edgar.ingest.layer0_populator",
    "src.ingest.sec_edgar.ingest.concept_labels_populator",
    "src.ingest.sec_edgar.downloader.sec_filing_downloader",
    "src.ingest.util.generate_geography_normalization",
]

# The heavy libraries `requirements-serve.txt` deliberately does not install —
# the serving image reaches BGE-M3 / Jina over HTTP via RAGINFER_URL instead.
SERVING_BANNED_LIBS = (
    "torch",
    "transformers",
    "FlagEmbedding",
    "sentence_transformers",
    "faiss",
)

# Entry points the slim serving image imports: the BFF, the MCP server, and the
# XBRL query pipeline they both drive (down to the narrative retriever, whose
# reranker must resolve over HTTP without importing the local one).
SERVING_SLIM_MODULES = [
    "src.api.main",
    "src.api.pipelines",
    "src.api.streaming",
    "src.mcp_server.server",
    "src.pipelines.xbrl.retrieval_pipeline",
    "src.retriever.xbrl.xbrl_narrative_retriever",
    "src.retriever.remote_reranker",
    "src.retriever.base_reranker",
    "src.retriever.retriever_util",
]


_MISSING = object()


@contextmanager
def _modules_blocked(*banned: str):
    """Make `import <banned>` raise, as it does inside the slim images.

    Also evicts already-imported banned and `src.*` modules so the import graph
    is re-walked rather than served from `sys.modules` (other tests in the same
    session will have imported torch transitively).
    """
    real_import = builtins.__import__
    saved = dict(sys.modules)
    saved_attrs = {}
    for name in saved:
        parent_name, _, child = name.rpartition(".")
        if parent_name in saved:
            parent = saved[parent_name]
            saved_attrs[(parent, child)] = getattr(parent, child, _MISSING)

    def _is_banned(name: str) -> bool:
        return any(name == b or name.startswith(b + ".") for b in banned)

    for name in list(sys.modules):
        if _is_banned(name) or name.startswith("src."):
            del sys.modules[name]

    def _blocked_import(name, *args, **kwargs):
        if _is_banned(name):
            raise ModuleNotFoundError(f"No module named '{name}'")
        return real_import(name, *args, **kwargs)

    builtins.__import__ = _blocked_import
    try:
        yield
    finally:
        builtins.__import__ = real_import
        sys.modules.clear()
        sys.modules.update(saved)
        # The re-import re-bound submodule attributes on packages that stayed in
        # sys.modules (`src.ingest` on `src`). Put back what those attributes held,
        # or `import a.b as x` and `from a.b import y` see different copies and a
        # later monkeypatch hits the wrong class.
        for (parent, child), value in saved_attrs.items():
            if value is _MISSING:
                if hasattr(parent, child):
                    delattr(parent, child)
            else:
                setattr(parent, child, value)


def _torch_blocked():
    return _modules_blocked("torch")


def test_the_blocker_actually_blocks():
    """Meta-test: prove _torch_blocked() is not a no-op.

    Without this, every test below could pass because the blocking never took
    effect — a green guard that guards nothing. `jina_reranker` loads the model
    in-process and imports torch at module scope, so it must fail.
    """
    with _torch_blocked():
        with pytest.raises(ModuleNotFoundError):
            __import__("src.retriever.jina_reranker")


@pytest.mark.parametrize("module", TORCH_FREE_MODULES)
def test_module_imports_without_torch(module):
    with _torch_blocked():
        __import__(module)


@pytest.mark.parametrize("module", SERVING_SLIM_MODULES)
def test_serving_module_imports_without_heavy_libs(module):
    """The serving image installs none of `SERVING_BANNED_LIBS`."""
    with _modules_blocked(*SERVING_BANNED_LIBS):
        __import__(module)


def test_narrative_reranker_resolves_remotely_without_heavy_libs(monkeypatch):
    """The specific serving regression: with RAGINFER_URL set, the narrative
    retriever's reranker is built over HTTP and never touches the local model."""
    monkeypatch.setenv("RAGINFER_URL", "http://raginference:8799")
    with _modules_blocked(*SERVING_BANNED_LIBS):
        from src.retriever.xbrl.xbrl_narrative_retriever import XbrlNarrativeRetriever

        reranker = XbrlNarrativeRetriever._build_reranker()
        assert type(reranker).__name__ == "RemoteReranker"
        assert reranker.url == "http://raginference:8799/rerank"


def test_remote_embedder_is_constructible_without_torch(monkeypatch):
    """The specific regression: RemoteEmbedder is the torch-free embedding path."""
    monkeypatch.setenv("RAGINFER_URL", "http://raginference:8799")
    with _torch_blocked():
        from src.embeddings.remote_embedder import RemoteEmbedder

        embedder = RemoteEmbedder()
        assert embedder.url == "http://raginference:8799/embed"


def test_resolve_endpoint_still_importable_from_remote_reranker():
    """Back-compat: the helper moved, but the old import path must keep working.

    This one is allowed to pull torch — remote_reranker legitimately depends on
    BaseReranker. It exists so the re-export is not silently dropped later.
    """
    pytest.importorskip("torch")
    from src.retriever.remote_reranker import resolve_endpoint

    assert resolve_endpoint("/rerank", "http://explicit") == "http://explicit"
