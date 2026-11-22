"""External test statistics for one question set (docs/specs/blog-stats.md).

Pre-registration v1 §5: each proportion with a Wilson 95% interval, a system run three
times as the mean and range of its runs, and XbrlRag against each baseline with a
two-sided exact McNemar test on the 2-of-3 outcomes of the cases both were given. A case
passes when its stored ``value_correct`` is true (v1 §3), as graded in the run itself, so
no suite file is needed.

The config names one set; sets are never pooled::

    {"set": "main", "reference": "xbrlrag_full",
     "systems": {"xbrlrag_full": ["eval-...", "eval-...", "eval-..."],
                 "long_context_sol": ["eval-..."]},
     "strata": {"TICKER": "S", "OTHER": "D"}}

``strata`` is optional (A′ only). Output is counts and rates only: no case id, question,
answer or ticker, so set B's author can run it on B and share what it prints::

    conda run -n py312 python scripts/eval/eval_stats.py --config b.json --out b_stats.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.eval.core.stats import majority_outcomes, mcnemar_exact, wilson_interval

HEADLINE_KINDS = ("lookup", "compute")
KIND_ORDER = ("lookup", "compute", "dimensional", "breakdown", "screen", "narrative")

# One case row: (case_id, kind, ticker, value_correct). Never printed.
Row = tuple[str, str, str, bool]


def load_runs(run_ids: Sequence[str]) -> tuple[dict[str, list[Row]], list[dict[str, Any]]]:
    """Every case row of each run, and the runs' own run-level fields.

    Args:
        run_ids: Stored ``eval`` run ids.

    Returns:
        Rows by run id, and per run its ``run_id``, ``system``, ``git_sha`` and
        ``started_at`` (ISO).

    Raises:
        SystemExit: A run id is unknown or has no case rows.
    """
    from src.db.conn import connect

    conn = connect(connect_timeout=10)
    conn.set_session(readonly=True, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "select run_id, system, git_sha, started_at from eval.eval_run "
                "where run_id = any(%s)",
                (list(run_ids),),
            )
            meta = {r[0]: r for r in cur.fetchall()}
            rows: dict[str, list[Row]] = {}
            for run_id in run_ids:
                if run_id not in meta:
                    raise SystemExit(f"unknown run id {run_id!r}")
                cur.execute(
                    "select case_id, coalesce(kind, ''), coalesce(ticker, ''), "
                    "coalesce(value_correct, false) from eval.eval_case_result "
                    "where run_id = %s",
                    (run_id,),
                )
                rows[run_id] = [tuple(r) for r in cur.fetchall()]
                if not rows[run_id]:
                    raise SystemExit(f"run {run_id} has no case rows")
    finally:
        conn.close()
    runs = [
        {
            "run_id": m[0],
            "system": m[1],
            "git_sha": m[2],
            "started_at": m[3].astimezone(UTC).isoformat(),
        }
        for m in (meta[r] for r in run_ids)
    ]
    return rows, runs


def _slices(kinds: set[str], strata: Mapping[str, str]) -> list[tuple[str, Any]]:
    """(name, row filter) for the headline, each kind, all cases, and each stratum."""
    out: list[tuple[str, Any]] = [
        ("lookup+compute", lambda r: r[1] in HEADLINE_KINDS),
    ]
    for kind in [k for k in KIND_ORDER if k in kinds] + sorted(kinds - set(KIND_ORDER)):
        out.append((kind, lambda r, k=kind: r[1] == k))
    out.append(("all", lambda r: True))
    for stratum in sorted(set(strata.values())):
        tickers = {t for t, s in strata.items() if s == stratum}
        out.append((f"stratum {stratum}, lookup+compute",
                    lambda r, t=tickers: r[2] in t and r[1] in HEADLINE_KINDS))
        out.append((f"stratum {stratum}, all", lambda r, t=tickers: r[2] in t))
    return out


def analyse(config: Mapping[str, Any], rows: Mapping[str, list[Row]]) -> dict[str, Any]:
    """The per-slice statistics for one set.

    Args:
        config: The parsed config (see the module docstring).
        rows: Case rows by run id, from :func:`load_runs`.

    Returns:
        By slice: per system its per-run ``k``/``n`` with Wilson intervals, the mean and
        range of its rates, and the McNemar result against the reference system.
    """
    systems: Mapping[str, Sequence[str]] = config["systems"]
    reference = config["reference"]
    if reference not in systems:
        raise SystemExit(f"reference system {reference!r} is not in systems")
    kinds = {r[1] for run_rows in rows.values() for r in run_rows}
    result: dict[str, Any] = {}
    for name, keep in _slices(kinds, config.get("strata") or {}):
        outcomes: dict[str, list[dict[str, bool]]] = {
            system: [{r[0]: r[3] for r in rows[run_id] if keep(r)} for run_id in run_ids]
            for system, run_ids in systems.items()
        }
        if not any(o for runs in outcomes.values() for o in runs):
            continue
        per_system: dict[str, Any] = {}
        ref_majority = majority_outcomes(outcomes[reference])
        for system, runs in outcomes.items():
            if not runs[0]:
                continue
            per_run = []
            for run in runs:
                k, n = sum(run.values()), len(run)
                lower, upper = wilson_interval(k, n)
                per_run.append({"k": k, "n": n, "rate": k / n, "lower": lower, "upper": upper})
            rates = [r["rate"] for r in per_run]
            entry: dict[str, Any] = {
                "runs": per_run,
                "mean": sum(rates) / len(rates),
                "min": min(rates),
                "max": max(rates),
            }
            if system != reference and ref_majority:
                entry["vs_reference"] = mcnemar_exact(ref_majority, majority_outcomes(runs))
            per_system[system] = entry
        result[name] = per_system
    return result


def _print(set_name: str, reference: str, result: Mapping[str, Any]) -> None:
    print(f"set {set_name}: value accuracy, Wilson 95%; McNemar vs {reference} "
          f"(ref-only / other-only, exact two-sided p)")
    for name, per_system in result.items():
        print(f"\n{name}")
        for system, e in per_system.items():
            runs = "  ".join(
                f"{r['k']}/{r['n']} {r['rate']:.1%} [{r['lower']:.1%}, {r['upper']:.1%}]"
                for r in e["runs"]
            )
            spread = (f"  mean {e['mean']:.1%} ({e['min']:.1%}-{e['max']:.1%})"
                      if len(e["runs"]) > 1 else "")
            line = f"  {system:24s} {runs}{spread}"
            if "vs_reference" in e:
                m = e["vs_reference"]
                line += (f"  | n={m['n']} {m['a_only']}/{m['b_only']} "
                         f"p={m['p_value']:.3g}")
            print(line)


def main(argv: list[str] | None = None) -> int:
    """Run the CLI.

    Args:
        argv: Command-line arguments; ``None`` reads ``sys.argv``.

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--config", required=True, help="JSON naming one set's runs")
    parser.add_argument("--out", default=None, help="also write the result as JSON here")
    args = parser.parse_args(argv)

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    run_ids = [r for ids in config["systems"].values() for r in ids]
    rows, runs = load_runs(run_ids)
    for system, ids in config["systems"].items():
        if len(ids) % 2 == 0:
            raise SystemExit(f"{system}: need 1 or 3 runs (v1 §5), got {len(ids)}")

    print("runs:")
    for r in runs:
        print(f"  {r['run_id']}  {r['system']:20s} {r['git_sha']}  {r['started_at']}")
    shas = {r["git_sha"] for r in runs}
    days = {r["started_at"][:10] for r in runs}
    if len(shas) > 1 or len(days) > 1:
        print(f"WARNING: runs span git_sha {sorted(shas)} and days {sorted(days)} "
              f"(v1 same-day rule)")
    result = analyse(config, rows)
    _print(config.get("set", "?"), config["reference"], result)
    if args.out:
        Path(args.out).write_text(
            json.dumps({"set": config.get("set"), "runs": runs, "slices": result}, indent=2),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
