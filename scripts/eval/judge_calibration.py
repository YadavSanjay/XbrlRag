"""Calibrate the text judge against human labels (docs/specs/judge-calibration.md).

The maintainer's workflow, all files under ``docs/eval/judge_calibration/`` by default
(dev material only; held-out cases are refused):

    # 1. answer the dev text cases with each arm (paid: the arms' calls, hard cap), and
    #    write answers.json (the calibration set), labels.csv (blank) and context.md
    conda run -n py312 python scripts/eval/judge_calibration.py export \\
        --suite <dev text suite.json> --llm-profile deepseek \\
        --system xbrlrag_full --system naive_rag_filtered --system closed_book \\
        --max-cost-usd 3

    # 2. fill the label column of labels.csv by hand (y/n), reading context.md

    # 3. each candidate judge labels the same answers (paid: one call per answer; hard
    #    cap; re-running continues a file the cap stopped)
    conda run -n py312 python scripts/eval/judge_calibration.py judge \\
        --judge-model claude-haiku-5-5 --judge-model gpt-6-luna --max-cost-usd 2

    # 4. per-dimension agreement for every candidate (kappa, or observed agreement for a
    #    dimension whose human labels are >= 99% one class), the cheapest passing judge
    #    for the generator profile, and the hashed record calibration.json (no calls)
    conda run -n py312 python scripts/eval/judge_calibration.py calibrate \\
        --generator-profile deepseek --min-answers 68 --min-items 100

    # 5. print a committed record and check its hash
    conda run -n py312 python scripts/eval/judge_calibration.py show

``run_suite.py --judge`` loads calibration.json, so a judge version the record shows as
passed has its means reported. Commit answers.json, labels.csv, context.md, the
judge_labels/ files and calibration.json together so the record is reproducible.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.eval.core.case import EvalSuite  # noqa: E402
from src.eval.core.graders.judge import (  # noqa: E402
    Claim,
    ClaimJudge,
    load_judge_settings,
)
from src.eval.core.graders.judge_calibration import (  # noqa: E402
    CalibrationAnswer,
    CalibrationResult,
    JudgeLabels,
    answer_set_sha256,
    build_label_sheet,
    calibrate,
    calibration_answer,
    calibration_file_text,
    calibration_record,
    choose_judge,
    judge_answer_labels,
    load_calibration_reports,
    parse_label_sheet,
    record_sha256,
    render_label_context,
)
from src.eval.core.judge_wiring import check_judge_family, is_text_graded  # noqa: E402
from src.eval.core.run_columns import run_split  # noqa: E402
from src.llm.profiles import load_committed_settings  # noqa: E402
from src.observability.trace_context import trace_query  # noqa: E402

DEFAULT_DIR = REPO_ROOT / "docs" / "eval" / "judge_calibration"
ANSWERS_SCHEMA = "judge-calibration-answers/1"
JUDGE_LABELS_SCHEMA = "judge-calibration-judge-labels/1"
_PROFILE_ENV = "XBRLRAG_LLM_PROFILE"


# ---------------------------------------------------------------------------- answers


def answers_to_json(answers: Sequence[CalibrationAnswer]) -> str:
    """The answers file: the calibration answers and their set hash.

    Args:
        answers: The calibration answers, in export order.

    Returns:
        Indented JSON text with a trailing newline.
    """
    doc = {
        "schema": ANSWERS_SCHEMA,
        "answer_set_sha256": answer_set_sha256(answers),
        "answers": [
            {
                "answer_id": a.answer_id,
                "case_id": a.case_id,
                "split": a.split,
                "generator_model": a.generator_model,
                "question": a.question,
                "reference_answer": a.reference_answer,
                "key_points": list(a.key_points),
                "claims": [
                    {
                        "claim_id": c.claim_id,
                        "text": c.text,
                        "cited_passages": list(c.cited_passages),
                    }
                    for c in a.claims
                ],
            }
            for a in answers
        ],
    }
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def answers_from_json(text: str) -> list[CalibrationAnswer]:
    """Reads an answers file back, checking its schema and set hash.

    Args:
        text: The file's text.

    Returns:
        The calibration answers, in file order.

    Raises:
        ValueError: A wrong schema, an invalid answer, or a hash that does not match.
    """
    doc = json.loads(text)
    if not isinstance(doc, dict) or doc.get("schema") != ANSWERS_SCHEMA:
        raise ValueError(f"not a calibration answers file (schema {ANSWERS_SCHEMA!r})")
    answers = [
        CalibrationAnswer(
            answer_id=a["answer_id"],
            case_id=a["case_id"],
            split=a["split"],
            generator_model=a["generator_model"],
            question=a["question"],
            reference_answer=a["reference_answer"],
            key_points=a["key_points"],
            claims=[
                Claim(c["claim_id"], c["text"], c["cited_passages"]) for c in a["claims"]
            ],
        )
        for a in doc["answers"]
    ]
    if answer_set_sha256(answers) != doc.get("answer_set_sha256"):
        raise ValueError("answers file does not match its answer_set_sha256 (edited?)")
    return answers


# ------------------------------------------------------------------------ judge labels


def _version_file_name(judge_version: str) -> str:
    """A file name for a judge version (``v1:claude-haiku-5-5`` -> ``v1_claude-haiku-5-5``)."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", judge_version) + ".json"


def judge_labels_to_json(
    judge_version: str, answer_hash: str, labels: Sequence[JudgeLabels]
) -> str:
    """One candidate's judge-labels file.

    Args:
        judge_version: The candidate's version stamp.
        answer_hash: The answer set the labels are for.
        labels: The labels so far, one per answer judged.

    Returns:
        Indented JSON text with a trailing newline.
    """
    doc = {
        "schema": JUDGE_LABELS_SCHEMA,
        "judge_version": judge_version,
        "answer_set_sha256": answer_hash,
        "answers": [
            {
                "answer_id": jl.answer_id,
                "error": jl.error,
                "cost_usd": jl.cost_usd,
                "labels": [[a, d, i, v] for (a, d, i), v in sorted(jl.labels.items())],
            }
            for jl in labels
        ],
    }
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def judge_labels_from_json(text: str, answer_hash: str) -> tuple[str, list[JudgeLabels]]:
    """Reads one candidate's judge-labels file.

    Args:
        text: The file's text.
        answer_hash: The answer set the labels must be for.

    Returns:
        ``(judge_version, labels)``.

    Raises:
        ValueError: A wrong schema, or labels made for another answer set.
    """
    doc = json.loads(text)
    if not isinstance(doc, dict) or doc.get("schema") != JUDGE_LABELS_SCHEMA:
        raise ValueError(f"not a judge-labels file (schema {JUDGE_LABELS_SCHEMA!r})")
    if doc.get("answer_set_sha256") != answer_hash:
        raise ValueError(
            f"judge labels of {doc.get('judge_version')!r} were made for another answer set"
        )
    version = str(doc["judge_version"])
    labels = [
        JudgeLabels(
            answer_id=entry["answer_id"],
            judge_version=version,
            labels={(a, d, i): bool(v) for a, d, i, v in entry["labels"]},
            error=entry["error"],
            cost_usd=float(entry["cost_usd"]),
        )
        for entry in doc["answers"]
    ]
    return version, labels


# --------------------------------------------------------------------------- commands


def _write_new(path: Path, text: str, *, force: bool) -> None:
    """Writes *text* to *path*, refusing to overwrite unless *force*."""
    if path.exists() and not force:
        raise FileExistsError(f"{path} exists; pass --force to overwrite it")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _generator_model(system: Any, llm_profile: str) -> str:
    """The model a system generates with: its own, else the profile's frontier model."""
    own = getattr(system, "model", None)
    if isinstance(own, str) and own:
        return own
    return load_committed_settings(profile=llm_profile).model_for_tier("frontier")


def cmd_export(args: argparse.Namespace) -> int:
    """Answers the dev text cases with each system; writes the answers, sheet and context.

    A cap that stops the export still writes the answers paid for, and exits 3.
    """
    from src.eval.systems import build_system  # heavy; only this subcommand needs it

    suite = EvalSuite.load(args.suite)
    cases = [c for c in suite.cases if is_text_graded(c)]
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        print(f"{args.suite}: no text-graded cases (expected_text + key_points)")
        return 2
    split = run_split(cases)  # a mixed suite raises here, before any call
    if split != "dev":
        print(f"{args.suite}: split {split!r}; calibration answers come from dev cases only")
        return 2
    out_dir = Path(args.out_dir)
    targets = [out_dir / n for n in ("answers.json", "labels.csv", "context.md")]
    for target in targets:
        if target.exists() and not args.force:
            print(f"{target} exists; pass --force to overwrite it")
            return 2

    llm_profile = load_committed_settings(profile=args.llm_profile).active_profile
    previous = os.environ.get(_PROFILE_ENV)
    os.environ[_PROFILE_ENV] = llm_profile
    try:
        answers: list[CalibrationAnswer] = []
        spent = 0.0
        skipped: list[str] = []
        capped = False
        for name in args.system:
            system = build_system(name)
            generator = _generator_model(system, llm_profile)
            for case in cases:
                if spent >= args.max_cost_usd:
                    capped = True
                    break
                with trace_query(f"[judge calibration export] {name} {case.id}") as trace:
                    answer = system.answer(case)
                spent += max(float(trace.total_cost_usd), float(answer.cost_usd or 0.0))
                answer_id = f"{case.id}~{name}"
                try:
                    answers.append(
                        calibration_answer(
                            case, answer, answer_id=answer_id, generator_model=generator
                        )
                    )
                except ValueError as exc:
                    skipped.append(f"{answer_id}: {exc}")
            if capped:
                print(f"--max-cost-usd {args.max_cost_usd} reached; the export is partial")
                break
    finally:
        if previous is None:
            os.environ.pop(_PROFILE_ENV, None)
        else:
            os.environ[_PROFILE_ENV] = previous

    _write_new(targets[0], answers_to_json(answers), force=args.force)
    _write_new(targets[1], build_label_sheet(answers), force=args.force)
    _write_new(targets[2], render_label_context(answers), force=args.force)
    n_labels = build_label_sheet(answers).count("\n") - 1
    print(f"exported {len(answers)} answers ({n_labels} labels) to {out_dir}; "
          f"cost ${spent:.4f}")
    for line in skipped:
        print(f"  skipped {line}")
    return 3 if capped else 0


def cmd_judge(args: argparse.Namespace) -> int:
    """Runs each candidate judge over the answers, one call per answer, under a hard cap."""
    out_dir = Path(args.out_dir)
    answers = answers_from_json((out_dir / "answers.json").read_text(encoding="utf-8"))
    answer_hash = answer_set_sha256(answers)
    prompt_version = args.prompt_version or load_judge_settings().prompt_version
    labels_dir = out_dir / "judge_labels"
    generators = sorted({a.generator_model for a in answers})
    spent = 0.0
    for model in args.judge_model:
        # The judge checks one generator; judge_answer_labels re-checks every answer's.
        judge = ClaimJudge(
            model=model, generator_model=generators[0], prompt_version=prompt_version
        )
        path = labels_dir / _version_file_name(judge.judge_version)
        done: list[JudgeLabels] = []
        if path.exists():
            version, done = judge_labels_from_json(path.read_text(encoding="utf-8"), answer_hash)
            if version != judge.judge_version:
                raise ValueError(f"{path} holds {version!r}, not {judge.judge_version!r}")
        done_ids = {jl.answer_id for jl in done}
        todo = [a for a in answers if a.answer_id not in done_ids]
        print(f"{judge.judge_version}: {len(done)} done, {len(todo)} to judge")
        halted = False
        try:
            for answer in todo:
                if spent >= args.max_cost_usd:
                    print(f"--max-cost-usd {args.max_cost_usd} reached; re-run to continue")
                    halted = True
                    break
                result = judge_answer_labels(judge, answer)
                spent += result.cost_usd
                done.append(result)
        finally:
            # Keep every paid label, also when a RunHalt or an error stops the loop.
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(judge_labels_to_json(judge.judge_version, answer_hash, done))
        n_errors = sum(1 for jl in done if jl.error is not None)
        print(f"  wrote {path} ({len(done)}/{len(answers)} answers, {n_errors} judge_errors)")
        if halted:
            return 3
    print(f"judge cost ${spent:.4f}")
    return 0


def _print_dimensions(cand: dict[str, Any]) -> None:
    """A record candidate's floors, then per dimension its gate, share, statistic and pass.

    A kappa-gated dimension shows kappa with its interval, an agreement-gated one its
    observed agreement p_o (docs/specs/judge-calibration-one-class.md).
    """
    print(f"    min_answers={cand['min_answers']} min_items={cand['min_items']} "
          f"threshold={cand['threshold']} agreement_bound={cand['agreement_bound']} "
          f"one_class_share={cand['one_class_share']}")
    for dim, d in cand["dimensions"].items():
        share = d["human_majority_share"]
        share_text = "n/a" if share is None else f"{share:.3f}"
        if d["gate"] == "agreement":
            stat = "p_o undefined" if d["p_o"] is None else f"p_o={d['p_o']:.3f}"
        elif d["kappa"] is None:
            stat = "kappa undefined"
        else:
            stat = f"kappa={d['kappa']:.3f} [{d['ci_low']:.3f}, {d['ci_high']:.3f}]"
        print(f"    {dim:10s} gate={d['gate']:9s} human_share={share_text} "
              f"n={d['n']:<5d} {stat}  {'pass' if d['passed'] else 'FAIL'}")


def _print_result(result: CalibrationResult, cand: dict[str, Any]) -> None:
    """One candidate's row: version, verdict, cost, then its dimensions from the record."""
    print(f"  {result.judge_version:32s} {'PASS' if result.passed else 'fail'}  "
          f"answers={result.n_answers} judge_errors={result.n_judge_errors} "
          f"cost=${result.cost_usd:.4f}")
    _print_dimensions(cand)


def cmd_calibrate(args: argparse.Namespace) -> int:
    """Calibrates every candidate on the labelled set and writes the hashed record."""
    out_dir = Path(args.out_dir)
    answers = answers_from_json((out_dir / "answers.json").read_text(encoding="utf-8"))
    answer_hash = answer_set_sha256(answers)
    human = parse_label_sheet(
        (out_dir / "labels.csv").read_text(encoding="utf-8-sig"), answers
    )
    files = (
        [Path(p) for p in args.judge_labels]
        if args.judge_labels
        else sorted((out_dir / "judge_labels").glob("*.json"))
    )
    if not files:
        print(f"no judge-labels files under {out_dir / 'judge_labels'}; run `judge` first")
        return 2
    results = []
    for path in files:
        _, labels = judge_labels_from_json(path.read_text(encoding="utf-8"), answer_hash)
        results.append(
            calibrate(answers, human, labels, threshold=args.threshold,
                      min_answers=args.min_answers, min_items=args.min_items)
        )
    record = calibration_record(results, answers=answers, human_labels=human)
    out = Path(args.out) if args.out else out_dir / "calibration.json"
    _write_new(out, calibration_file_text(record), force=args.force)
    print(f"calibrated {len(results)} candidate(s) on {len(answers)} answers, "
          f"{len(human)} labels")
    by_version = {c["judge_version"]: c for c in record["candidates"]}
    for result in sorted(results, key=lambda r: r.judge_version):
        _print_result(result, by_version[result.judge_version])
    if args.generator_profile:
        check = load_committed_settings(profile=args.generator_profile).active_profile
        chosen = choose_judge(results, generator_profile=check)
        print(f"cheapest passing judge for profile {check!r}: "
              f"{chosen.judge_version if chosen else 'none'}")
    print(f"wrote {out}  record_sha256={record_sha256(record)}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    """Prints a record file's candidates after checking its hash."""
    path = Path(args.file) if args.file else Path(args.out_dir) / "calibration.json"
    text = path.read_text(encoding="utf-8")
    reports = load_calibration_reports(text)  # refuses an edited record
    doc = json.loads(text)
    record = doc["record"]
    print(f"{path}: record_sha256={doc['record_sha256']} (verified)")
    print(f"  answers={record['n_answers']} labels={record['n_labels']} "
          f"answer_set={record['answer_set_sha256'][:12]} "
          f"label_set={record['label_set_sha256'][:12]}")
    for cand, report in zip(record["candidates"], reports, strict=True):
        kappa = "undefined" if math.isnan(report.kappa) else f"{report.kappa:.3f}"
        print(f"  {cand['judge_version']:32s} {'PASS' if report.passed else 'fail'}  "
              f"min kappa (kappa-gated)={kappa} answers={cand['n_answers']} "
              f"judge_errors={cand['n_judge_errors']} cost=${cand['cost_usd']:.4f}")
        _print_dimensions(cand)
        if args.profile:
            try:
                check_judge_family(cand["model"], args.profile)
                print(f"    usable with generator profile {args.profile!r}")
            except ValueError as exc:
                print(f"    not usable with {args.profile!r}: {exc}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """The command-line parser."""
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out-dir", default=str(DEFAULT_DIR),
                   help="the calibration folder (default docs/eval/judge_calibration)")
    sub = p.add_subparsers(dest="command", required=True)

    ex = sub.add_parser("export", help="answer the dev text cases; write answers, sheet, context")
    ex.add_argument("--suite", required=True, help="a dev suite JSON with text cases")
    ex.add_argument("--system", action="append", required=True,
                    help="an arm to answer with (repeatable)")
    ex.add_argument("--llm-profile", default=None, help="the generator profile")
    ex.add_argument("--max-cost-usd", type=float, required=True,
                    help="hard cap on the arms' cost")
    ex.add_argument("--limit", type=int, default=0, help="only the first N text cases")
    ex.add_argument("--force", action="store_true", help="overwrite existing files")
    ex.set_defaults(func=cmd_export)

    jd = sub.add_parser("judge", help="label the answers with candidate judges (paid)")
    jd.add_argument("--judge-model", action="append", required=True,
                    help="a candidate judge model (repeatable)")
    jd.add_argument("--prompt-version", default=None,
                    help="the eval_judge prompt version (default: configs/eval/judge.yaml)")
    jd.add_argument("--max-cost-usd", type=float, required=True,
                    help="hard cap on the judges' cost over this invocation")
    jd.set_defaults(func=cmd_judge)

    ca = sub.add_parser("calibrate", help="kappa per candidate; write the record (no calls)")
    ca.add_argument("--judge-labels", nargs="*", default=None,
                    help="judge-labels files (default: every file under judge_labels/)")
    ca.add_argument("--threshold", type=float, default=0.7)
    ca.add_argument("--min-answers", type=int, default=100,
                    help="the minimum number of answers the judge labels without error")
    ca.add_argument("--min-items", type=int, default=100,
                    help="the minimum number of labels per dimension (pre-registered 100)")
    ca.add_argument("--generator-profile", default=None,
                    help="also print the cheapest passing judge for this profile")
    ca.add_argument("--out", default=None, help="the record file (default calibration.json)")
    ca.add_argument("--force", action="store_true", help="overwrite an existing record")
    ca.set_defaults(func=cmd_calibrate)

    sh = sub.add_parser("show", help="print a record and verify its hash")
    sh.add_argument("--file", default=None, help="the record (default calibration.json)")
    sh.add_argument("--profile", default=None,
                    help="also say which candidates a generator profile may use")
    sh.set_defaults(func=cmd_show)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """Runs one subcommand; returns the exit code."""
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (ValueError, FileExistsError, FileNotFoundError) as exc:
        print(f"{args.command}: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
