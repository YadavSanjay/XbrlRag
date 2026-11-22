"""Record what the v2 look's system freeze pins: code, models, prompts, grader, suites, services.

Pre-registration §6 item 3 (the code commit and freeze record of the look), recorded by the
later amendment (C4). Run once, from a clean checkout of the freeze branch, with the local
Postgres, Qdrant and RagInference up:

    conda run -n py312 python docs/eval/heldout_v2/freeze/freeze_record.py \
        --frozen-commit 66d5575 --out docs/eval/heldout_v2/freeze/freeze_record.json

The branch adds only files under ``docs/``; the script refuses to run unless the tree outside
``docs/`` equals ``--frozen-commit``, so the code of the look commit is that commit's code.

For every committed LLM profile it lists each stage's requested model and prompt version. It
then makes one closed-book call per G1/G2 model (about $0.10 in all) and records the model id
the provider reports serving (docs/specs/served-model-id.md); every eval run records its own
served ids in ``config.served_models``, so a look run can be checked against this record.

It reads no held-out data: H-F's and H-X's hashes come by the later amendment, from their own
sealed records.
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

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from dotenv import find_dotenv, load_dotenv  # noqa: E402

load_dotenv(find_dotenv(usecwd=True))  # the operator's settings, searched upward from the cwd

from prompts.prompt_registry import get_xbrl_registry  # noqa: E402
from src.eval.core.fingerprint import compute_fingerprint  # noqa: E402
from src.eval.core.graders.honesty import grader_version  # noqa: E402
from src.eval.sec_tools.toolbox import tools_fingerprint  # noqa: E402
from src.llm.gateway import resolve_model_ref  # noqa: E402
from src.llm.profiles import XBRL_STAGE_TIERS, load_committed_settings  # noqa: E402

# The arms' own stages, beside the pipeline's XBRL_STAGE_TIERS.
ARM_STAGES = ("long_context", "frontier_sec_tools", "closed_book")
# G1 runs the anthropic profile, G2 openai-6.1, and the second tools agent deepseek-v4-pro
# (Amendment 2, D1'). One served-id probe per model of those.
PROBE_PROFILES = ("anthropic", "openai-6.1")
PROBE_EXTRA = ("deepseek-v4-pro",)
PACKAGES = ("openai", "anthropic", "httpx", "pydantic", "psycopg2", "qdrant-client",
            "arelle-release", "hypothesis", "pytest")
FROZEN_SUITES = ("xbrl_v2_dev.json", "xbrl_v2_validation.json", "xbrl_v2_dev_compound.json",
                 "xbrl_v2_validation_compound.json", "xbrl_v2_dev_sample.json", "intents.json",
                 "intents_compound.json", "xbrl_v2_manifest.json",
                 "xbrl_v2_compound_manifest.json")
# The committed inputs that decide the answer keys and the grading.
KEY_CONFIGS = ("configs/eval/v2/lexicon.yaml", "configs/eval/v2/phrasebook.yaml",
               "configs/eval/v2/phrasebook_compound.yaml", "configs/eval/v2/groups.yaml",
               "configs/eval/v2/statement_lines.yaml", "configs/eval/v2/note_concepts.yaml",
               "configs/eval/v2/key_resolutions.yaml", "configs/eval/v2/reviewers.yaml",
               "configs/eval/judge.yaml", "configs/llm/llm.yaml", "configs/llm/pricing.yaml",
               "prompts/templates/xbrl/registry.yaml")
PROBE_QUESTION = "What was Apple's revenue in fiscal 2024?"


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _prompt(stage: str, model: str) -> dict:
    ref = resolve_model_ref(model)
    sig = get_xbrl_registry().resolve(stage, provider=ref.provider,
                                      series=ref.series).signature()
    return {"model": model, "provider": ref.provider, "series": ref.series,
            "prompt_ref": sig["prompt_ref"], "prompt_version": sig["version"]}


def profiles() -> dict:
    """Each profile's tier models, and per stage its requested model and prompt version."""
    out: dict = {}
    for name in load_committed_settings().profile_names:
        settings = load_committed_settings(profile=name)
        stages = {stage: _prompt(stage, settings.model_for_stage(stage))
                  for stage in XBRL_STAGE_TIERS}
        frontier = settings.tier_models()["frontier"]
        for stage in ARM_STAGES:
            try:
                stages[stage] = _prompt(stage, frontier)
            except Exception as exc:
                stages[stage] = {"model": frontier, "error": type(exc).__name__}
        out[name] = {"tiers": settings.tier_models(), "stages": stages}
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


def suites(frozen_dir: Path) -> dict:
    """File SHA-256 of each K7 file, and the content hashes its manifests state."""
    files = {name: _sha256(frozen_dir / name) for name in FROZEN_SUITES}
    content = {}
    for manifest in ("xbrl_v2_manifest.json", "xbrl_v2_compound_manifest.json"):
        doc = json.loads((frozen_dir / manifest).read_text(encoding="utf-8"))
        content[manifest] = doc.get("content_hashes")
    sample = json.loads((frozen_dir / "xbrl_v2_dev_sample.json").read_text(encoding="utf-8"))
    content["xbrl_v2_dev_sample.json"] = sample.get("content_hash")
    return {"dir": "data/eval/v2_frozen (K7, Amendment 2)", "file_sha256": files,
            "content_hashes": content}


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
            cur.execute("SELECT current_database(), version()")
            database, postgres = cur.fetchone()
    finally:
        conn.close()
    scheme = "https" if os.environ.get("QDRANT_HTTPS", "").lower() in ("1", "true") else "http"
    qdrant_url = (f"{scheme}://{os.environ.get('QDRANT_HOST', 'localhost')}:"
                  f"{os.environ.get('QDRANT_PORT', '6333')}/")
    qdrant = httpx.get(qdrant_url, timeout=10).json()
    health = httpx.get(resolve_endpoint("/healthz"), timeout=10).json()
    rag = {"embedder": health.get("embedder"), "reranker": health.get("reranker")}
    if raginference_repo is not None:
        rag["git_commit"] = subprocess.run(["git", "-C", str(raginference_repo), "rev-parse",
                                            "HEAD"], capture_output=True, text=True).stdout.strip()
    return {"database": database, "corpus_fingerprint": fingerprint, "postgres": postgres,
            "qdrant": {"version": qdrant.get("version"), "commit": qdrant.get("commit")},
            "raginference": rag}


def main() -> int:
    """Collects the record, writes it and prints its SHA-256."""
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--frozen-commit", required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--frozen-dir", type=Path, default=REPO_ROOT / "data" / "eval" / "v2_frozen")
    p.add_argument("--raginference-repo", type=Path, default=None)
    p.add_argument("--no-probes", action="store_true", help="skip the served-id calls ($0)")
    args = p.parse_args()

    outside_docs = _git("diff", "--name-only", args.frozen_commit, "HEAD", "--", ".", ":!docs")
    dirty = _git("status", "--porcelain", "--untracked-files=no", "--", ".", ":!docs")
    if outside_docs or dirty:
        print(f"refused: the tree outside docs/ differs from {args.frozen_commit}:\n"
              f"{outside_docs}\n{dirty}", file=sys.stderr)
        return 2

    prof = profiles()
    models = sorted({m for name in PROBE_PROFILES for m in prof[name]["tiers"].values()}
                    | set(PROBE_EXTRA))
    record = {
        "recorded_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "frozen_commit": _git("rev-parse", args.frozen_commit),
        "record_commit": _git("rev-parse", "HEAD"),
        "code_outside_docs_equals_frozen_commit": True,
        "grader_version": grader_version(),
        "frontier_sec_tools": {"tools_fingerprint": tools_fingerprint(),
                               "budgets": {"max_steps": 13, "max_tool_calls": 22,
                                           "max_cost_usd": 0.23,
                                           "source": "Amendment 2 item 2 (run flags)"}},
        "suites": suites(args.frozen_dir),
        "configs_sha256": {c: _sha256(REPO_ROOT / c) for c in KEY_CONFIGS},
        "stage_tiers": dict(XBRL_STAGE_TIERS),
        "profiles": prof,
        "served_models": {} if args.no_probes else served_ids(models),
        "services": services(args.raginference_repo),
        "python": platform.python_version(),
        "packages": {name: _version(name) for name in PACKAGES},
    }
    data = (json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    args.out.write_bytes(data)
    print(json.dumps({k: record[k] for k in ("frozen_commit", "record_commit", "grader_version",
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
