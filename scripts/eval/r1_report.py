"""Build the pre-registered R1 comparison table from stored eval runs (docs/specs/r1-report.md).

Read-only and $0: no LLM call, no pipeline, no write to any database. Each ``--arm`` names a
stored run in the ``eval`` schema under a label; every arm must be on the same suite and
split. The report pairs the arms on case id and gives, per kind, value accuracy,
confident-wrong rate, grounding precision (the reference only), mean cost and latency, the
exact McNemar test of the reference against every other arm, and the success-bar lines
(pre-registration §3, Amendment 1 R1). A held-out report carries aggregates only.

    conda run -n py312 python scripts/eval/r1_report.py `
        --arm xbrlrag=eval-aaaaaaaaaaaa --arm frontier=eval-bbbbbbbbbbbb `
        --arm oracle=eval-cccccccccccc `
        --reference xbrlrag --bar-agent frontier --oracle oracle `
        --out-json data/eval/r1/r1.json --out-md data/eval/r1/r1.md
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.eval.core.r1_report import (  # noqa: E402
    R1Refused,
    build_r1,
    load_arm,
    render_markdown,
)


class PostgresR1Reader:
    """Reads ``eval.eval_run`` and ``eval.eval_case_result`` in a read-only session."""

    _RUN = "SELECT run_id, suite, system, split, config FROM eval.eval_run WHERE run_id = %s"
    _CASES = (
        "SELECT case_id, tags, value_correct, confident_wrong, grounding_precision, "
        "cost_usd, latency_ms, error FROM eval.eval_case_result WHERE run_id = %s "
        "ORDER BY case_id"
    )

    def __init__(self) -> None:
        self._conn: Any = None

    def _rows(self, sql: str, param: Any) -> list[dict[str, Any]]:
        if self._conn is None:
            from src.db.conn import connect  # lazy: the module imports without a driver

            self._conn = connect(options="-c default_transaction_read_only=on")
            self._conn.set_session(readonly=True)
        with self._conn.cursor() as cur:
            cur.execute(sql, (param,))
            columns = [d[0] for d in cur.description or ()]
            return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]

    def run_row(self, run_id: str) -> Mapping[str, Any] | None:
        """The run row, or None."""
        rows = self._rows(self._RUN, run_id)
        return rows[0] if rows else None

    def case_rows(self, run_id: str) -> Sequence[Mapping[str, Any]]:
        """The run's case rows."""
        return self._rows(self._CASES, run_id)

    def close(self) -> None:
        """Closes the connection, if one was opened."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def _parse_arm(text: str) -> tuple[str, str]:
    label, sep, run_id = text.partition("=")
    if not sep or not label.strip() or not run_id.strip():
        raise argparse.ArgumentTypeError(f"expected <label>=<run_id>, got {text!r}")
    return label.strip(), run_id.strip()


def _json_default(value: Any) -> Any:
    # Decimal columns that survive into config values.
    return float(value)


def main(argv: list[str] | None = None, reader: Any = None) -> int:
    """Runs the CLI.

    Args:
        argv: Command-line arguments; None reads ``sys.argv``.
        reader: A run reader; None opens a read-only Postgres session.

    Returns:
        0, or 2 when the arms are refused.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--arm", action="append", type=_parse_arm, required=True,
                        metavar="LABEL=RUN_ID", help="one stored run per arm (repeat)")
    parser.add_argument("--reference", required=True, help="the label of XbrlRag's arm")
    parser.add_argument("--bar-agent", required=True,
                        help="the label of the tools agent the bar is read against")
    parser.add_argument("--oracle", default=None,
                        help="the label of long_context_oracle's arm, if it ran")
    parser.add_argument("--out-json", required=True, help="the JSON file to write")
    parser.add_argument("--out-md", required=True, help="the Markdown file to write")
    args = parser.parse_args(argv)

    own_reader = reader is None
    reader = reader or PostgresR1Reader()
    try:
        arms = [load_arm(label, run_id, reader) for label, run_id in args.arm]
        r1 = build_r1(arms, args.reference, args.bar_agent, oracle=args.oracle)
    except R1Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    finally:
        if own_reader:
            reader.close()
    md = render_markdown(r1)
    for path, text in ((args.out_json, json.dumps(r1, indent=2, allow_nan=False,
                                                  default=_json_default)),
                       (args.out_md, md)):
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
