"""WS-9 — run an eval suite against a system and report the scorecard.

A full 382-case run costs roughly $2 and 50 minutes, and is only needed for a
**comparable headline number**. While iterating on a fix, run the affected slice —
it is an order of magnitude cheaper and answers "did my change work?" directly.

    # FAST LOOP: only the cases that did not pass last time (~130 cases, ~$0.80)
    conda run -n py312 python scripts/eval/run_suite.py --rerun-failed last

    # narrower still: only the refusals from a specific run
    conda run -n py312 python scripts/eval/run_suite.py \
        --rerun-failed eval-9208cc232bb7 --rerun-grade REFUSED

    # one area (~54 cases, ~$0.30)
    conda run -n py312 python scripts/eval/run_suite.py --kind breakdown

    # CHECKPOINT: the whole suite, for a number that is comparable run-to-run
    conda run -n py312 python scripts/eval/run_suite.py --max-cost-usd 5

    # the WS-2 golden set through the same harness
    conda run -n py312 python scripts/eval/run_suite.py --suite golden

    # the same slice on another LLM profile (configs/llm/llm.yaml)
    conda run -n py312 python scripts/eval/run_suite.py --kind breakdown --llm-profile anthropic

Any narrowed run is stored with tier='partial' and a note recording the selection:
its accuracy is over a deliberately-biased subset (usually cases that were already
failing), so it must not be read as a baseline. A targeted re-run also cannot detect
a regression outside the cases it ran — take a full run before trusting a new number.

Narrative cases run **serialized** regardless of ``--workers`` (the reranker is
CPU-bound and has segfaulted under unbounded batching). ``--max-cost-usd`` is a hard
ceiling: the run aborts cleanly, keeps partial results, and reports what it skipped.

Optional passes, all off by default (docs/specs/judge-runner-wiring.md):

    # judge each text-graded case (key points + reference) with the claim judge from
    # configs/eval/judge.yaml; refused when the judge shares the generator's family
    conda run -n py312 python scripts/eval/run_suite.py --judge --max-cost-usd 5

    # the key-free metamorphic pass over the run's own answers (no extra calls)
    conda run -n py312 python scripts/eval/run_suite.py --keyfree

    # up to 20 ticker/year perturbation twins, answered after the run, under the same cap
    conda run -n py312 python scripts/eval/run_suite.py --perturb-twins 20 --max-cost-usd 5

The judge's cost counts against ``--max-cost-usd`` but not against the cases' cost;
its means are withheld until its version passes calibration: the committed record
``docs/eval/judge_calibration/calibration.json`` (``--judge-calibration`` to read another;
built by scripts/eval/judge_calibration.py, docs/specs/judge-calibration.md) is loaded and
a version it records as passed has its means shown.

A stored run is checkpointed as it goes (docs/specs/eval-run-checkpoint.md): its row is
written before the first answer and each case as it is graded, so a crash, a restart or a
budget/breaker halt loses nothing graded. Continue it under the same run id, with the same
selection, code, profile and model (a higher ``--max-cost-usd`` is fine; the cap covers
the whole run, restored spend included):

    conda run -n py312 python scripts/eval/run_suite.py --system closed_book \
        --resume eval-0123456789ab --max-cost-usd 10

    # also re-answer the restored cases that failed on transport (429/5xx, timeouts,
    # RagInference / Qdrant / Postgres connection loss); the arm's own errors are kept
    conda run -n py312 python scripts/eval/run_suite.py --system closed_book \
        --resume eval-0123456789ab --retry-errors transport --max-cost-usd 10

``--resume`` does not combine with ``--no-store``, ``--rerun-failed``, ``--keyfree`` or
``--perturb-twins``. With ``--with-system``, pass the first arm's run id.

The ``frontier_sec_tools`` arm (docs/specs/frontier-sec-tools.md) reads the public SEC APIs
as of a date the operator sets to the corpus's, and needs ``SEC_EDGAR_IDENTITY``:

    conda run -n py312 python scripts/eval/run_suite.py --system frontier_sec_tools \
        --sec-as-of 2026-10-29 --sample 20 --max-cost-usd 40

Its per-case budgets (frontier-sec-tools requirement 22) are ``--max-steps`` (tool turns,
default 20), ``--max-tool-calls`` (default 60), ``--max-case-cost-usd`` (default 2.00) and
``--max-input-tokens`` (default 250,000); they apply only to this arm, and a resume must
bring the same values. The report's ``config.frontier`` records the arm's model, prompt, tool
fingerprint, budgets, SEC snapshot and agent summary (docs/eval/frontier_sec_tools.md).

Results print to stdout, optionally write JSON (``--out``), and are persisted to the
``eval`` schema unless ``--no-store``; browse them at ``/tech/eval``. Operator guide:
docs/guides/xbrl-flow/11-evaluation.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from src.eval.core import EvalCase, EvalSuite, Grade, RunnerConfig
from src.eval.core.case import KIND_NARRATIVE
from src.eval.core.checkpoint.base import NullRunStore, RunStore
from src.eval.core.checkpoint.run import (
    RETRY_CHOICES,
    ResumeRefused,
    resume_identity,
    run_checkpointed,
)
from src.eval.core.fingerprint import git_sha
from src.eval.core.graders.grounding_v2 import ground_results
from src.eval.core.graders.judge import CalibrationReport, ClaimJudge, load_judge_settings
from src.eval.core.graders.judge_calibration import load_calibration_reports
from src.eval.core.judge_wiring import check_judge_family
from src.eval.keyfree.runner import perturbation_twins, run_perturbation_checks
from src.eval.sec_check.base import FactStore
from src.eval.sec_tools.client import check_date
from src.eval.suites.intents import load_lexicon
from src.eval.systems import (
    LONG_CONTEXT_NAMES,
    build_system,
    stored_system_names,
    system_names,
)
from src.eval.systems.frontier_sec_tools import (
    MAX_WINDOW_TOKENS,
    FrontierSecToolsSystem,
    agent_summary,
)
from src.eval.systems.long_context_arms import fit_summary
from src.llm.profiles import LlmConfigError, load_committed_settings
from src.llm.request_options import (
    SERVICE_TIER_ENV,
    SERVICE_TIERS,
    flex_retry_policy,
    openai_service_tier,
)
from src.llm.run_budget import RunHalt
from src.observability.audit_store import enable_audit_persistence

# The variable the XBRL stages read their profile from when they are built.
_PROFILE_ENV = "XBRLRAG_LLM_PROFILE"
# The as-of date the frontier_sec_tools arm's toolbox reads (docs/specs/frontier-sec-tools.md).
_SEC_AS_OF_ENV = "XBRLRAG_SEC_AS_OF"
_FRONTIER = "frontier_sec_tools"
# The systems --model applies to: the long-context arms and the tool-calling arm.
_MODEL_SYSTEMS = (*LONG_CONTEXT_NAMES, _FRONTIER)
# The arms whose answer call is the same filing prefix on the same stage and prompt, so
# one schedule lets them share a prompt-cache entry (--with-system).
_SHARED_PREFIX_SYSTEMS = ("long_context_oracle", "long_context_resolved")

DEFAULT_SUITES = {
    "sql_derived": os.path.join("test", "eval", "suites", "sql_derived_v1.json"),
    "golden": None,  # loaded via the adapter, not a suite file
}


def _load_suite(name: str, path: str | None) -> EvalSuite:
    if path:
        return EvalSuite.load(path)
    if name == "golden":
        from src.eval.suites.golden_xbrl import load

        return load()
    return EvalSuite.load(DEFAULT_SUITES[name])


def _failed_case_ids(run_id, suite, system, grades):
    """Case ids that did not pass in a prior run. ``run_id='last'`` resolves to the
    most recent completed run of the same suite+system, under any name that system's
    runs were stored with.

    Returns None (rather than an empty set) when the store is unreachable or the run
    is unknown, so the caller can refuse instead of silently running everything.
    """
    from src.api import db

    if run_id == "last":
        row = db.query_one(
            "SELECT run_id FROM eval.eval_run WHERE suite = %s AND system = ANY(%s) "
            "AND status <> 'RUNNING' ORDER BY created_at DESC LIMIT 1",
            (suite, list(stored_system_names(system))),
        )
        if not row:
            return None
        run_id = row["run_id"]
        print(f"--rerun-failed=last resolved to {run_id}")

    sql = "SELECT case_id FROM eval.eval_case_result WHERE run_id = %s AND NOT passed"
    params = [run_id]
    if grades:
        sql += " AND grade = ANY(%s)"
        params.append([g.upper() for g in grades])
    rows = db.query(sql, tuple(params))
    if not rows:
        # Distinguish "run has no failures" from "run does not exist".
        exists = db.query_one(
            "SELECT 1 AS ok FROM eval.eval_run WHERE run_id = %s", (run_id,)
        )
        return set() if exists else None
    return {r["case_id"] for r in rows}


def _selection_note(args, n_selected: int) -> str:
    """Describe any narrowing applied, or '' for a full-suite run."""
    bits = []
    if args.rerun_failed:
        bits.append(f"--rerun-failed={args.rerun_failed}")
    for name in ("kind", "tag", "ticker"):
        val = getattr(args, name, None)
        if val:
            bits.append(f"--{name}={','.join(val)}")
    if args.sample:
        bits.append(f"--sample={args.sample}")
    if args.limit:
        bits.append(f"--limit={args.limit}")
    return f"subset of {n_selected} case(s) via {' '.join(bits)}" if bits else ""


def _build_judge(
    judge_model: str | None, llm_profile: str, system_model: str | None
) -> ClaimJudge:
    """The claim judge for ``--judge``, from configs/eval/judge.yaml.

    Args:
        judge_model: ``--judge-model``; the config's development model when None.
        llm_profile: The profile the system generates with.
        system_model: The system's own model (a long-context arm's), if any.

    Returns:
        The judge; its gateway is built on its first call.

    Raises:
        ValueError: The model is not a configured judge, or shares the generator's family.
    """
    settings = load_judge_settings()
    allowed = (settings.development_model, *settings.milestone_models)
    model = judge_model or settings.development_model
    if model not in allowed:
        raise ValueError(f"{model!r} is not a judge in configs/eval/judge.yaml {allowed}")
    check_judge_family(model, llm_profile)
    generator = system_model or load_committed_settings(
        profile=llm_profile).model_for_tier("frontier")
    return ClaimJudge(
        model=model, generator_model=generator, prompt_version=settings.prompt_version
    )


def _lookups_differ(original: EvalCase, twin: EvalCase) -> bool:
    """The perturbation pass's ``lookups_equal``: another filer or year is assumed to differ.

    No store is read (docs/specs/judge-runner-wiring.md leaves the SQL check out), so an
    unchanged answer is flagged ``unchanged`` even in the rare case the values truly tie.
    """
    del original, twin
    return False


_LEXICON = Path(__file__).resolve().parents[2] / "configs" / "eval" / "v2" / "lexicon.yaml"
_CALIBRATION_FILE = (
    Path(__file__).resolve().parents[2] / "docs" / "eval" / "judge_calibration"
    / "calibration.json"
)


def _judge_calibration(path: str | None) -> list[CalibrationReport]:
    """The judge calibration reports for ``--judge`` (docs/specs/judge-calibration.md).

    Args:
        path: ``--judge-calibration``; the committed record file when None.

    Returns:
        One report per calibrated judge version; empty when the default file does not
        exist yet (every judge version is then reported uncalibrated).

    Raises:
        ValueError: The file was edited (its hash does not match) or is not a record.
        OSError: An explicitly given file cannot be read.
    """
    file = Path(path) if path is not None else _CALIBRATION_FILE
    if path is None and not file.exists():
        return []
    return load_calibration_reports(file.read_text(encoding="utf-8"))


def _company_names(path: Path = _LEXICON) -> dict[str, list[str]]:
    """Ticker to company surface forms (short name first, then legal name) from the lexicon.

    Most suite questions name the company rather than its ticker ("What was Apple's
    revenue ..."), so without these a ticker twin can only be made from the few
    ticker-literal questions.
    """
    names: dict[str, list[str]] = {}
    for ticker, entry in load_lexicon(path).companies.items():
        forms = [entry.get("short"), entry.get("legal")]
        names[ticker] = list(dict.fromkeys(f for f in forms if f))
    return names


def _perturbation_pass(cases, system, report, args) -> dict:
    """Answers up to ``--perturb-twins`` twins under what is left of ``--max-cost-usd``.

    A run halt here (budget, circuit breaker) stops only this optional pass: the graded
    report is already paid for, so it is recorded as halted and the run still prints,
    persists and writes its report.
    """
    spent = report.metrics.total_cost_usd + float(report.config.get("judge_cost_usd") or 0.0)
    remaining = max(0.0, args.max_cost_usd - spent)
    # Narrative cases are left out: the runner here has no per-kind gate, and the
    # reranker must not run concurrently (DEFAULT_KIND_CONCURRENCY).
    eligible = [c for c in cases if c.kind != KIND_NARRATIVE]
    twins = perturbation_twins(eligible, seed=args.seed, names=_company_names(),
                               max_twins=args.perturb_twins)
    n_ticker = sum(1 for t in twins if "~ticker:" in t["twin"].id)
    n_year = len(twins) - n_ticker
    print(f"\nperturbation: {len(twins)} twin(s) ({n_ticker} ticker, {n_year} year), "
          f"budget ${remaining:.4f}")
    try:
        out = run_perturbation_checks(
            twins, system, lookups_equal=_lookups_differ, max_workers=args.workers,
            max_cost_usd=remaining,
        )
    except RunHalt as halt:
        print(f"perturbation pass halted: {halt!r}; the graded report is kept")
        out = {"halted": repr(halt)}
    out["n_twins"] = len(twins)
    out["n_ticker_twins"] = n_ticker
    out["n_year_twins"] = n_year
    out["max_cost_usd"] = remaining
    return out


def _print_extras(report) -> None:
    """Prints the judge summary, key-free counts and fit table, when the run has them."""
    fit_by_k = report.config.get("fit_by_k")
    if fit_by_k:
        print("\n  filings per case (k):")
        for k, row in fit_by_k.items():
            print(f"    k={k:<3d} sent={row['sent']}  over_window={row['over_window']}")
    summary = report.config.get("judge_summary") or {}
    if summary.get("status", "none") != "none":
        print(
            f"\n  judge            {summary['judge_version']}   status={summary['status']}   "
            f"judged={summary['n_judged']}   judge_errors={summary['n_judge_error']}   "
            f"cost=${float(report.config.get('judge_cost_usd') or 0.0):.4f}"
        )
        if summary.get("means"):
            for key, value in summary["means"].items():
                print(f"    {key:18s} {'-' if value is None else f'{value:.3f}'}")
        else:
            print("    means withheld: this judge version has not passed calibration")
    for name in ("keyfree", "perturbation"):
        block = report.config.get(name)
        if not block:
            continue
        print(f"\n  {name}:")
        if "halted" in block:
            print(f"    halted: {block['halted']}  (twins={block.get('n_twins', 0)})")
            continue
        for check, counts in block["counts"].items():
            print(
                f"    {check:20s} consistent={counts['consistent']}  "
                f"inconsistent={counts['inconsistent']}  "
                f"n/a={counts['not_applicable']}"
            )
        if name == "perturbation":
            print(
                f"    pairs={block['n_pairs']}  skipped={len(block['skipped'])}  "
                f"cost=${block['cost_usd']:.4f}"
            )


def _print_report(report, top_failures: int) -> None:
    m = report.metrics
    print(f"\n{'=' * 72}")
    print(
        f"  {report.suite}_{report.suite_version}   system={report.system}   "
        f"status={report.status}"
    )
    print(f"  corpus={report.corpus_fingerprint}   git={report.git_sha}")
    print(f"{'=' * 72}")
    print(
        f"  cases            {m.n}"
        + (f"  (+{report.n_skipped} not attempted)" if report.n_skipped else "")
    )
    print(
        f"  accuracy         {m.accuracy:.1%}   "
        f"({m.n_correct} correct, {m.n_partial} partial, {m.n_incorrect} incorrect)"
    )
    print(f"  value accuracy   {m.value_accuracy:.1%}   (right numbers, citations aside)")
    print(f"  citation rate    {m.citation_rate:.1%}")
    if m.grounding_precision is not None:
        print(
            f"  grounding P/R    {m.grounding_precision:.1%} / {m.grounding_recall:.1%}"
        )
    print(
        f"  SILENT-WRONG     {m.silent_wrong_rate:.1%}   ({len(report.silent_wrongs())} case(s))"
    )
    print(
        f"  confident-wrong  {m.confident_wrong_rate:.1%}   "
        f"({m.n_confident_wrong} case(s), cited or not)"
    )
    print(f"  refusal rate     {m.refusal_rate:.1%}")
    print(f"  hedged misses    {m.hedged_miss_rate:.1%}   ({m.n_hedged_miss} case(s))")
    print(f"  decline rate     {m.decline_rate:.1%}   (refused + hedged misses)")
    if m.n_needs_review:
        print(f"  needs review     {m.n_needs_review} breakdown(s) with unexplained amounts")
    print(f"  error rate       {m.error_rate:.1%}")
    print(f"  grader version   {m.grader_version}")
    from src.eval.core.report import paraphrase_stability

    stability = paraphrase_stability(report.results)
    if stability is not None:
        print(f"  paraphrase stab. {stability:.1%}")
    print(f"  cost             ${m.total_cost_usd:.4f}")
    if m.latency_p50_ms is not None:
        print(f"  latency p50/p95  {m.latency_p50_ms:.0f} / {m.latency_p95_ms:.0f} ms")

    print("\n  by kind:")
    for kind, km in report.by_kind().items():
        print(f"    {kind:14s} {km.accuracy:6.1%}  ({km.n_correct}/{km.n})")

    silent = report.silent_wrongs()
    if silent:
        print(f"\n  !! SILENT-WRONG ({len(silent)}) — confident, cited, wrong:")
        for r in silent[:top_failures]:
            print(f"    {r.case.id}\n      {r.case.question}\n      {r.grade.reason}")

    failures = [r for r in report.failures() if r.grade.grade is not Grade.CORRECT]
    if failures:
        print(
            f"\n  failures ({len(failures)}), first {min(top_failures, len(failures))}:"
        )
        for r in failures[:top_failures]:
            print(f"    [{r.grade.grade.value}] {r.case.id}")
            print(f"      Q: {r.case.question}")
            print(f"      {r.grade.reason}")
            if r.query_id:
                print(f"      trace: /tech/traces/{r.query_id}")


def _cache_groups(choice: str | None, system: str) -> str | None:
    """The runner's ``cache_groups`` for ``--cache-groups`` and ``--system``.

    Args:
        choice: The flag's value, None when it was not given.
        system: The system name.

    Returns:
        ``"filing"`` or None. Unset, the long-context systems, whose calls share a filing
        prefix, get ``"filing"``.
    """
    if choice is None:
        return "filing" if system in LONG_CONTEXT_NAMES else None
    return None if choice == "none" else choice


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--suite", default="sql_derived", choices=sorted(DEFAULT_SUITES))
    p.add_argument("--suite-path", default=None, help="explicit suite JSON path")
    # long_context is the oracle arm's former name; a new run is stored under the new one.
    p.add_argument(
        "--system", default="xbrlrag_full", choices=[*system_names(), "long_context"]
    )
    p.add_argument(
        "--model",
        default=None,
        help="exact model ID for a long-context --system or frontier_sec_tools (default: the "
        "active profile's frontier model, see --llm-profile); it must have a MODEL_PRICING "
        "entry. Recorded in the run's config. Not accepted for other systems.",
    )
    p.add_argument(
        "--sec-as-of",
        default=None,
        metavar="YYYY-MM-DD",
        help="frontier_sec_tools only, and required for it: the date its SEC tools treat as "
        "today, set to the corpus's date (sets XBRLRAG_SEC_AS_OF for the run)",
    )
    p.add_argument(
        "--sec-universes",
        default=None,
        metavar="YAML",
        help="frontier_sec_tools only: a YAML file of the universes the arm is told "
        "({covered: [{ticker, cik}], groups: {id: [ticker]}, as_of}); default: the "
        "committed sectors.yaml and groups.yaml, the development universe",
    )
    p.add_argument(
        "--max-input-tokens",
        type=int,
        default=None,
        metavar="N",
        help="frontier_sec_tools only: the arm's prompt window, estimated as characters / 4 "
        "(default 250,000, the most it accepts). JSON tool results run about 2 characters a "
        "token on DeepSeek, so the estimate reads about half the real count. Recorded in "
        "config.frontier.",
    )
    p.add_argument(
        "--max-steps",
        type=int,
        default=None,
        metavar="N",
        help="frontier_sec_tools only: the most tool turns in one case (default 20); a case "
        "makes at most N + 1 model calls. Recorded in config.frontier.",
    )
    p.add_argument(
        "--max-tool-calls",
        type=int,
        default=None,
        metavar="N",
        help="frontier_sec_tools only: the most tool calls in one case (default 60). "
        "Recorded in config.frontier.",
    )
    p.add_argument(
        "--max-case-cost-usd",
        type=float,
        default=None,
        metavar="USD",
        help="frontier_sec_tools only: the spend at which one case stops calling tools "
        "(default 2.00; a case can overshoot by one turn and the final call). Not the run's "
        "--max-cost-usd. Recorded in config.frontier.",
    )
    p.add_argument(
        "--llm-profile",
        default=None,
        help="LLM profile from configs/llm/llm.yaml (e.g. openai, openai-5.6, anthropic) "
        "for every stage of this run; wins over XBRLRAG_LLM_PROFILE. Recorded in the "
        "run's config.",
    )
    p.add_argument(
        "--service-tier",
        choices=list(SERVICE_TIERS),
        default=None,
        help="OpenAI service tier for every OpenAI call of this run (e.g. flex for the "
        "looks); sets XBRLRAG_OPENAI_SERVICE_TIER for the run. Default: the variable's "
        "value, or none (the provider's default). Prices stay at Standard. Recorded as "
        "config.service_tier.",
    )
    p.add_argument("--workers", type=int, default=6)
    p.add_argument(
        "--with-system",
        action="append",
        choices=list(_SHARED_PREFIX_SYSTEMS),
        help="another long-context arm that sends the same filing prefix, run in one "
        "schedule with --system: each case goes to every arm in turn, so the arms share "
        "one prompt-cache entry per filing. Each arm is stored as its own run; "
        "--max-cost-usd covers them together. Repeatable.",
    )
    p.add_argument(
        "--cache-groups",
        choices=["filing", "none"],
        default=None,
        help="run cases that share a filing (the case's ticker and fiscal year) one after "
        "another, so later calls read the prompt cache the first one wrote; groups still "
        "run in parallel. Default: filing for the long-context systems, none otherwise. "
        "Recorded as config.cache_groups.",
    )
    p.add_argument("--max-cost-usd", type=float, default=None)
    p.add_argument("--kind", action="append", help="restrict to case kind(s)")
    p.add_argument("--tag", action="append", help="restrict to tag(s)")
    p.add_argument("--ticker", action="append", help="restrict to ticker(s)")
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="take the first N cases (suite is grouped by template, so this "
        "biases toward one template — prefer --sample)",
    )
    p.add_argument(
        "--sample",
        type=int,
        default=None,
        help="randomly sample N cases across all templates (seeded, so the "
        "same N is reproducible run to run)",
    )
    p.add_argument("--seed", type=int, default=1729)
    p.add_argument(
        "--rerun-failed",
        metavar="RUN_ID",
        default=None,
        help="run only the cases that did NOT pass in RUN_ID (use 'last' for the most "
        "recent run of this suite+system). The fast loop when validating a fix: a "
        "full suite is only needed for a comparable headline number.",
    )
    p.add_argument(
        "--rerun-grade",
        action="append",
        help="with --rerun-failed, restrict to specific prior grades "
        "(e.g. --rerun-grade REFUSED --rerun-grade INCORRECT)",
    )
    p.add_argument("--out", default=None, help="write the full report JSON here")
    p.add_argument("--top-failures", type=int, default=10)
    p.add_argument(
        "--persist-audit",
        action="store_true",
        help="also write each query to audit.query_audit (off by default so "
        "an eval run does not skew production dashboards). Required for the UI's "
        "per-case trace links to resolve.",
    )
    p.add_argument(
        "--no-store",
        action="store_true",
        help="skip writing the run to the `eval` schema",
    )
    p.add_argument(
        "--judge",
        action="store_true",
        help="judge each text-graded case (expected_text + key_points) with the claim "
        "judge; its calls count against --max-cost-usd. Off by default.",
    )
    p.add_argument(
        "--judge-model",
        default=None,
        help="with --judge, the judge model: configs/eval/judge.yaml's development_model "
        "(the default) or one of its milestone_models",
    )
    p.add_argument(
        "--judge-calibration",
        default=None,
        metavar="PATH",
        help="with --judge, the calibration record file whose passed judge versions have "
        "their means reported (default: docs/eval/judge_calibration/calibration.json, "
        "when it exists)",
    )
    p.add_argument(
        "--keyfree",
        action="store_true",
        help="run the key-free metamorphic pass over the run's own answers (no extra "
        "calls); recorded as config.keyfree. Off by default.",
    )
    p.add_argument(
        "--perturb-twins",
        type=int,
        default=0,
        metavar="N",
        help="after the run, answer up to N ticker/year perturbation twins of the selected "
        "cases (and their originals) and check them; needs --max-cost-usd, whose "
        "remainder caps it. Recorded as config.perturbation. Off (0) by default.",
    )
    p.add_argument(
        "--resume",
        metavar="RUN_ID",
        default=None,
        help="continue the stored run RUN_ID (with --with-system, the first arm's): its "
        "graded cases are restored and only the cases with no row are answered. Refused "
        "unless the selection, code, system, model, profile and settings are the run's. "
        "A new segment is recorded in config.segments.",
    )
    p.add_argument(
        "--retry-errors",
        choices=list(RETRY_CHOICES),
        default=None,
        help="with --resume: also re-answer the restored ERROR cases whose error is a "
        "transport failure (429/5xx, timeouts, connection loss); the arm's own errors "
        "are kept",
    )
    args = p.parse_args(argv)
    # docs/specs/eval-run-checkpoint.md, requirement 17: refused before anything runs.
    if args.retry_errors is not None and args.resume is None:
        p.error("--retry-errors needs --resume: it re-answers a stored run's transport "
                "errors")
    if args.resume is not None:
        clashes = [
            flag for flag, given in (
                ("--no-store", args.no_store),
                ("--rerun-failed", args.rerun_failed),
                ("--keyfree", args.keyfree),
                ("--perturb-twins", args.perturb_twins),
            ) if given
        ]
        if clashes:
            p.error(f"--resume does not combine with {', '.join(clashes)}")
    if args.model is not None and args.system not in _MODEL_SYSTEMS:
        p.error(f"--model applies only to --system {' / '.join(_MODEL_SYSTEMS)}")
    if args.system == _FRONTIER:
        if args.sec_as_of is None:
            p.error("--system frontier_sec_tools needs --sec-as-of (the corpus's date)")
        # The toolbox's own rule, so a date it would refuse fails here, before any cost.
        try:
            check_date(args.sec_as_of, "--sec-as-of")
        except ValueError as exc:
            p.error(str(exc))
        if args.sec_universes is not None and not os.path.isfile(args.sec_universes):
            p.error(f"--sec-universes: no such file {args.sec_universes!r}")
        if args.max_input_tokens is not None and not (
            0 < args.max_input_tokens <= MAX_WINDOW_TOKENS
        ):
            p.error(f"--max-input-tokens must be between 1 and {MAX_WINDOW_TOKENS:,}")
        for flag, value in (("--max-steps", args.max_steps),
                            ("--max-tool-calls", args.max_tool_calls),
                            ("--max-case-cost-usd", args.max_case_cost_usd)):
            if value is not None and not (math.isfinite(value) and value > 0):
                p.error(f"{flag} must be positive, got {value}")
    elif any(value is not None for value in (
            args.sec_as_of, args.sec_universes, *_frontier_budgets(args).values())):
        p.error("--sec-as-of, --sec-universes, --max-input-tokens, --max-steps, "
                "--max-tool-calls and --max-case-cost-usd apply only to "
                "--system frontier_sec_tools")
    if args.with_system:
        if args.system not in _SHARED_PREFIX_SYSTEMS:
            p.error(f"--with-system needs --system {' / '.join(_SHARED_PREFIX_SYSTEMS)}")
        names = [args.system, *args.with_system]
        if len(set(names)) != len(names):
            p.error("--with-system must name other arms than --system, each once")
        if args.perturb_twins or args.rerun_failed:
            p.error("--with-system does not combine with --perturb-twins or --rerun-failed")
    if args.judge_model is not None and not args.judge:
        p.error("--judge-model applies only with --judge")
    if args.judge_calibration is not None and not args.judge:
        p.error("--judge-calibration applies only with --judge")
    if args.perturb_twins < 0:
        p.error("--perturb-twins must not be negative")
    if args.perturb_twins and args.max_cost_usd is None:
        p.error("--perturb-twins needs --max-cost-usd: its extra answers are paid calls")
    try:
        llm_profile = load_committed_settings(profile=args.llm_profile).active_profile
    except LlmConfigError as exc:
        p.error(f"--llm-profile: {exc}")

    # The stages read the profile from the environment when they are constructed, so
    # the chosen one is set for the duration of this run (and restored afterwards, for
    # in-process callers such as tests).
    overrides = {_PROFILE_ENV: llm_profile}
    if args.service_tier is not None:
        overrides[SERVICE_TIER_ENV] = args.service_tier
    else:
        try:  # an unknown tier in the environment fails here, before any cost
            openai_service_tier()
        except LlmConfigError as exc:
            p.error(f"{SERVICE_TIER_ENV}: {exc}")
    try:  # the gateway reads the Flex retry policy at a call's first Flex 429; check it now
        flex_retry_policy()
    except LlmConfigError as exc:
        p.error(f"Flex retry policy: {exc}")
    if args.sec_as_of is not None:
        overrides[_SEC_AS_OF_ENV] = args.sec_as_of
    previous = {name: os.environ.get(name) for name in overrides}
    os.environ.update(overrides)
    try:
        return _run(args, llm_profile)
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _load_sec_universes(path: str) -> dict:
    """Reads a ``--sec-universes`` YAML file: ``{covered, groups, as_of}``.

    Raises:
        ValueError: If the file is not a mapping with a ``covered`` list.
    """
    import yaml

    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("covered"), list):
        raise ValueError(f"{path} must be a mapping with a 'covered' list")
    return data


def _frontier_budgets(args: argparse.Namespace) -> dict[str, int | float | None]:
    """The frontier_sec_tools budget flags, keyed by the arm's argument names.

    None for a flag not given, which leaves the arm's default (requirement 22).
    """
    return {
        "max_steps": args.max_steps,
        "max_tool_calls": args.max_tool_calls,
        "max_cost_usd": args.max_case_cost_usd,
        "max_input_tokens": args.max_input_tokens,
    }


def _build(args: argparse.Namespace):
    """The system under test; frontier_sec_tools takes --sec-universes and its budgets."""
    budgets = {name: value for name, value in _frontier_budgets(args).items()
               if value is not None}
    if args.system == _FRONTIER and (args.sec_universes is not None or budgets):
        kwargs: dict = {}
        if args.model is not None:
            kwargs["model"] = args.model
        if args.sec_universes is not None:
            kwargs["universes"] = _load_sec_universes(args.sec_universes)
        kwargs.update(budgets)
        system = FrontierSecToolsSystem(**kwargs)
        # build_system registers the audit sink for every arm but XbrlRag; this branch
        # bypasses it, so it registers the sink itself.
        if args.persist_audit:
            enable_audit_persistence()
        return system
    return build_system(args.system, persist_audit=args.persist_audit, model=args.model)


def _print_frontier(block: dict) -> None:
    """Prints the frontier_sec_tools arm's run config and agent summary."""
    summary = block.get("agent_summary") or {}
    snapshot = block.get("sec_snapshot") or {}
    print("\n  frontier_sec_tools:")
    print(f"    model={block.get('model')}  prompt={block.get('prompt_version')}  "
          f"tools={str(block.get('tools_fingerprint'))[:12]}")
    print(f"    budgets: steps={block.get('max_steps')}  tool_calls={block.get('max_tool_calls')}"
          f"  cost=${block.get('max_cost_usd')}  window={block.get('max_input_tokens')}")
    print(f"    snapshot: as_of={snapshot.get('as_of')}  id={str(snapshot.get('snapshot_id'))[:12]}"
          f"  urls={snapshot.get('urls')}  fetched={snapshot.get('fetched')}"
          f"  frozen={snapshot.get('frozen')}")
    print(f"    cases={summary.get('cases')}  answered={summary.get('answered')}  "
          f"budget_hit={summary.get('budget_hit')}  tool_calls={summary.get('tool_calls')}  "
          f"tool_errors={summary.get('tool_errors')}  "
          f"snapshot_misses={summary.get('snapshot_misses')}  "
          f"unresolved_citations={summary.get('unresolved_citations')}  "
          f"steps_max={summary.get('steps_max')}")


def _run(args: argparse.Namespace, llm_profile: str) -> int:
    """Runs the selected suite; ``main`` has parsed the arguments and set the profile."""

    suite = _load_suite(args.suite, args.suite_path)
    suite = suite.filter(kinds=args.kind, tags=args.tag, tickers=args.ticker)

    if args.rerun_failed:
        wanted = _failed_case_ids(
            args.rerun_failed, args.suite, args.system, args.rerun_grade
        )
        if wanted is None:
            print("could not read prior run from the eval store")
            return 1
        before = len(suite.cases)
        suite.cases = [c for c in suite.cases if c.id in wanted]
        print(
            f"--rerun-failed: {len(suite.cases)} of {before} cases did not pass "
            f"in {args.rerun_failed}"
            + (f" (grades {args.rerun_grade})" if args.rerun_grade else "")
        )
        print(
            "  NB: a targeted re-run cannot detect a regression outside these "
            "cases — take a full run before trusting a new headline number."
        )
    if args.sample and args.sample < len(suite.cases):
        rng = random.Random(args.seed)
        suite.cases = sorted(rng.sample(suite.cases, args.sample), key=lambda c: c.id)
    if args.limit:
        suite.cases = suite.cases[: args.limit]

    if not suite.cases:
        print("no cases selected")
        return 1

    print(
        f"running {len(suite)} case(s) from {suite.qualified_name} "
        f"against {args.system} (workers={args.workers}, "
        f"budget={args.max_cost_usd if args.max_cost_usd else 'unbounded'}, "
        f"service_tier={openai_service_tier() or 'provider default'})"
    )
    if args.resume:
        print(f"  resuming {args.resume}"
              + (" and re-answering its transport errors" if args.retry_errors else ""))

    system = _build(args)
    companions = [
        build_system(name, persist_audit=args.persist_audit, model=args.model)
        for name in args.with_system or []
    ]
    if companions:
        print(f"  in one schedule with {', '.join(args.with_system)}; the budget covers all")
    judge = None
    calibration: list[CalibrationReport] = []
    if args.judge:
        try:
            judge = _build_judge(args.judge_model, llm_profile, getattr(system, "model", None))
            calibration = _judge_calibration(args.judge_calibration)
        except (ValueError, OSError) as exc:
            print(f"--judge: {exc}")
            return 2
        calibrated = any(
            r.judge_version == judge.judge_version and r.passed for r in calibration
        )
        print(f"judging text-graded cases with {judge.judge_version} "
              f"({'calibrated' if calibrated else 'uncalibrated: means withheld'})")
    systems = [system, *companions]
    cache_groups = _cache_groups(args.cache_groups, args.system)
    sha = git_sha()
    settings = _resume_settings(args, cache_groups, judge)
    identities = [
        resume_identity(suite.cases, system=s.name, model=getattr(s, "model", None),
                        llm_profile=llm_profile, git_sha=sha, settings=settings)
        for s in systems
    ]
    store = _store(args)
    try:
        reports = run_checkpointed(
            suite.cases,
            systems,
            store=store,
            identities=identities,
            resume=args.resume,
            retry_errors=args.retry_errors,
            judge=judge,
            generator_profile=llm_profile if judge is not None else None,
            calibration=calibration,
            metamorphic=args.keyfree,
            config=RunnerConfig(
                max_workers=args.workers,
                max_cost_usd=args.max_cost_usd,
                cache_groups=cache_groups,
                # Set before the run: the checkpointed row is written at its start.
                tier="partial" if _selection_note(args, len(suite)) else None,
            ),
            suite=suite.name,
            suite_version=suite.version,
            corpus_fingerprint=suite.corpus_fingerprint,
        )
    except ResumeRefused as exc:
        print(f"--resume {args.resume}: {exc}")
        return 2
    for each_system, report in zip(systems, reports, strict=True):
        _finish(report, each_system, suite, args, llm_profile, store, out=_out_path(
            args.out, report.system if each_system is not system else None))
    return 0


def _store(args: argparse.Namespace) -> RunStore:
    """The run's checkpoint store: the ``eval`` schema, or nothing with ``--no-store``."""
    if args.no_store:
        return NullRunStore()
    from src.eval.core.store import PostgresEvalStore

    return PostgresEvalStore.from_env()


def _fact_store() -> FactStore:
    """The fact store cited fact ids resolve against (read-only ``xbrl_db``)."""
    from src.eval.sec_check.pg_store import PostgresFactStore

    return PostgresFactStore()


def _attach_grounding(report, fact_store: Callable[[], FactStore] | None = None) -> None:
    """Sets v2 grounding on a run whose answers cite fact ids (docs/specs/v2-grounding.md).

    A live run's fact ids come from the store it ran against, so no fingerprint check.
    Records the outcome in ``config["grounding_v2"]``; without a store the fields stay None.
    """
    if not any(r.grade.produced.get("fact_ids") for r in report.results):
        return  # an arm that cites nothing: grounding stays "not applicable"
    outcome = ground_results(report.results, fact_store or _fact_store)
    if outcome["status"] == "not_applicable":
        return
    report.config["grounding_v2"] = outcome
    if outcome["status"] == "attached":
        print(f"v2 grounding attached to {outcome['n_cases']} case(s): "
              f"{outcome['n_citations']} citation(s), "
              f"{outcome['n_unresolved']} unresolved fact id(s)")
    else:
        print(f"v2 grounding NOT attached ({outcome['status']}): {outcome['reason']}")


def _file_sha256(path: str | None) -> str | None:
    """``sha256:<hex>`` of a file's bytes, or None when no file is given."""
    if path is None:
        return None
    with open(path, "rb") as handle:
        return "sha256:" + hashlib.sha256(handle.read()).hexdigest()


def _resume_settings(args: argparse.Namespace, cache_groups: str | None,
                     judge: ClaimJudge | None) -> dict:
    """The answer-affecting options a resume must match (eval-run-checkpoint req. 7).

    Not the cost cap or the workers (raising the cap is what a resume is for), and not
    the OpenAI service tier, which each segment records for itself (config.segments).
    """
    return {
        "cache_groups": cache_groups,
        "judge_model": judge.model if judge is not None else None,
        "judge_version": judge.judge_version if judge is not None else None,
        "sec_as_of": args.sec_as_of,
        "sec_universes_sha256": _file_sha256(args.sec_universes),
        "max_input_tokens": args.max_input_tokens,
        "max_steps": args.max_steps,
        "max_tool_calls": args.max_tool_calls,
        "max_case_cost_usd": args.max_case_cost_usd,
        "with_system": list(args.with_system or []),
    }


def _out_path(out: str | None, companion: str | None) -> str | None:
    """``--out``, with ``.<system>`` before the extension for a ``--with-system`` report."""
    if out is None or companion is None:
        return out
    stem, ext = os.path.splitext(out)
    return f"{stem}.{companion}{ext}"


def _finish(report, system, suite, args: argparse.Namespace, llm_profile: str,
            store: RunStore, out: str | None) -> None:
    """Adds the run's extras to one system's report, prints, stores and writes it.

    The checkpointed run has already finalised its row; it is finalised again here so
    the extras (model, profile, fit table, frontier block, perturbation, notes) are
    stored with it.
    """
    if args.perturb_twins:
        report.config["perturbation"] = _perturbation_pass(
            suite.cases, system, report, args)
    model = getattr(system, "model", None)
    if model is not None:
        report.config["model"] = model  # the exact model a long-context run tested
    report.config["llm_profile"] = llm_profile
    if system.name == "long_context_multi":
        report.config["fit_by_k"] = fit_summary(
            r.grade.produced.get("selection", {}) for r in report.results
        )
    if system.name == _FRONTIER and hasattr(system, "run_config"):
        report.config["frontier"] = {
            **system.run_config(),
            "agent_summary": agent_summary(
                r.grade.produced.get("agent", {}) for r in report.results
            ),
        }

    # A partial run must never be mistaken for a baseline. Its accuracy is over a
    # deliberately-biased subset (usually the cases that were already failing), so the
    # UI's run list and trend would read it as a collapse against a full run.
    selection = _selection_note(args, len(suite))
    if selection:
        report.tier = "partial"
        report.notes = selection
        print(f"\nPARTIAL RUN — {selection}")
        print("  Accuracy here is over a biased subset; it is not a baseline.")

    _attach_grounding(report)
    _print_report(report, args.top_failures)
    _print_extras(report)
    if report.config.get("frontier"):
        _print_frontier(report.config["frontier"])

    if not args.no_store:
        ok = store.finish_run(report)
        print(
            f"\n{'stored' if ok else 'NOT stored'} as eval run {report.run_id}"
            + ("" if ok else " (eval schema unreachable — results above still valid)")
        )

    if out:
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(report.to_dict(), f, indent=2)
            f.write("\n")
        print(f"\nwrote {out}")


if __name__ == "__main__":
    raise SystemExit(main())
