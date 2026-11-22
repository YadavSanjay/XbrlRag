"""Record what the blog v1 freeze pins: models, prompts, grader, corpus and services.

Pre-registration v1 (blog plan step 1). Run once at the freeze, from a clean checkout of
the code commit being frozen, with the local Postgres, Qdrant and RagInference up:

    conda run -n py312 python docs/eval/blog_v1/freeze_record.py \
        --raginference-repo ../RagInference --out docs/eval/blog_v1/freeze_record.json

For every LLM profile it lists each stage's requested model and prompt version. It then
makes one closed-book call per distinct model (about $0.10 in all) and records the model
id the provider reports serving. That is the snapshot the alias pointed to at the freeze
(docs/specs/served-model-id.md). Every eval run records its own served ids in
``config.served_models``, so a later run can be checked against this record.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from prompts.prompt_registry import get_xbrl_registry  # noqa: E402
from src.eval.core.fingerprint import compute_fingerprint  # noqa: E402
from src.eval.core.graders.honesty import grader_version  # noqa: E402
from src.llm.gateway import resolve_model_ref  # noqa: E402
from src.llm.profiles import XBRL_STAGE_TIERS, load_committed_settings  # noqa: E402

LONG_CONTEXT_MODELS = ("gpt-5.6-sol", "claude-opus-5-5")
PACKAGES = ("openai", "anthropic", "httpx", "pydantic", "psycopg2", "qdrant-client",
            "arelle-release", "hypothesis", "pytest")
SUITE = REPO_ROOT / "test" / "eval" / "suites" / "sql_derived_v1.json"
# A main-suite question; its answer is not graded, only the reply's model id is kept.
PROBE_QUESTION = "What was Apple's revenue in fiscal 2024?"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def _prompt(stage: str, model: str) -> dict:
    ref = resolve_model_ref(model)
    sig = get_xbrl_registry().resolve(stage, provider=ref.provider,
                                      series=ref.series).signature()
    return {"model": model, "provider": ref.provider, "series": ref.series,
            "prompt_ref": sig["prompt_ref"], "prompt_version": sig["version"]}


def profiles() -> dict:
    """Each profile's tier models, and per stage its requested model and prompt version.

    A stage escalates up its profile's tiers, so the frontier model can answer although
    no stage starts on it.
    """
    out: dict = {}
    for name in load_committed_settings().profile_names:
        settings = load_committed_settings(profile=name)
        out[name] = {
            "tiers": settings.tier_models(),
            "stages": {stage: _prompt(stage, settings.model_for_stage(stage))
                       for stage in XBRL_STAGE_TIERS},
        }
    out["long_context"] = {"stages": {m: _prompt("long_context", m)
                                      for m in LONG_CONTEXT_MODELS}}
    return out


def served_ids(models: list[str]) -> dict:
    """One closed-book call per model; the provider-reported model id of each."""
    from src.eval.systems._stage_llm import StageLLM
    from src.observability.trace_context import trace_query

    result = {}
    for model in models:
        llm = StageLLM("closed_book", model=model)
        with trace_query(f"freeze probe {model}") as trace:
            llm.complete(llm.prompt.user.format(question=PROBE_QUESTION))
        result[model] = {"served": trace.served_models.get(model, []),
                         "cost_usd": round(trace.total_cost_usd, 6)}
    return result


def services(raginference_repo: Path | None) -> dict:
    """Corpus fingerprint, Postgres, Qdrant and RagInference versions."""
    import httpx

    from src.db.conn import connect
    from src.retriever.endpoints import resolve_endpoint

    conn = connect(connect_timeout=10)
    try:
        conn.set_session(readonly=True, autocommit=True)
        fingerprint = compute_fingerprint(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT version()")
            postgres = cur.fetchone()[0]
    finally:
        conn.close()

    scheme = "https" if os.environ.get("QDRANT_HTTPS", "").lower() in ("1", "true") else "http"
    qdrant_url = (f"{scheme}://{os.environ.get('QDRANT_HOST', 'localhost')}:"
                  f"{os.environ.get('QDRANT_PORT', '6333')}/")
    qdrant = httpx.get(qdrant_url, timeout=10).json()
    health = httpx.get(resolve_endpoint("/healthz"), timeout=10).json()
    rag = {"embedder": health.get("embedder"), "reranker": health.get("reranker")}
    if raginference_repo is not None:
        rag["git_commit"] = _git(raginference_repo, "rev-parse", "HEAD")
        rag["git_dirty"] = bool(_git(raginference_repo, "status", "--porcelain"))
    return {
        "corpus_fingerprint": fingerprint,
        "postgres": postgres,
        "qdrant": {"version": qdrant.get("version"), "commit": qdrant.get("commit")},
        "raginference": rag,
    }


def main() -> int:
    """Collects the record, writes it and prints its SHA-256."""
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--raginference-repo", type=Path, default=None)
    args = p.parse_args()

    suite = json.loads(SUITE.read_bytes())
    prof = profiles()
    models = sorted({row["model"] for p in prof.values() for row in p["stages"].values()}
                    | {m for p in prof.values() for m in p.get("tiers", {}).values()})
    record = {
        "recorded_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "code_commit": _git(REPO_ROOT, "rev-parse", "HEAD"),
        "code_dirty": bool(_git(REPO_ROOT, "status", "--porcelain", "--untracked-files=no")),
        "grader_version": grader_version(),
        "suite": {"path": "test/eval/suites/sql_derived_v1.json",
                  "sha256": hashlib.sha256(SUITE.read_bytes()).hexdigest(),
                  "n_cases": suite["n_cases"],
                  "corpus_fingerprint": suite["corpus_fingerprint"]},
        "stage_tiers": dict(XBRL_STAGE_TIERS),
        "profiles": prof,
        "served_models": served_ids(models),
        "services": services(args.raginference_repo),
        "python": platform.python_version(),
        "packages": {name: _version(name) for name in PACKAGES},
    }
    data = (json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    args.out.write_bytes(data)
    print(json.dumps({k: record[k] for k in ("code_commit", "code_dirty", "grader_version",
                                             "served_models")}, indent=2))
    print(json.dumps(record["services"], indent=2))
    print(f"{hashlib.sha256(data).hexdigest()}  {args.out.name}")
    return 0


def _version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
