"""Rewrite the resolved ``groups`` of configs/eval/v2/groups.yaml from its ``definitions``.

The named universe groups (docs/specs/sector-tags.md) are derived from the committed sector
tags: each definition (``sectors``, ``industries`` or ``tickers``) is resolved by
``build_groups`` and written back as sorted tickers. The file is then checked with
``check_groups`` against the v2 lexicon's ``universes`` and the corpus: by default the live
``xbrl_db`` (read-only, ``entity_master.trading_symbol``), or with ``--corpus-from-tags``
the filers of the sectors file (offline).

    conda run -n py312 python scripts/eval/build_universe_groups.py              # rewrite + check
    conda run -n py312 python scripts/eval/build_universe_groups.py --check      # check only
    conda run -n py312 python scripts/eval/build_universe_groups.py --corpus-from-tags

Exit code 1 when the check reports a problem (or, with ``--check``, when the committed
groups are stale). Costs $0: no LLM call.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.eval.suites.intents import load_lexicon  # noqa: E402
from src.retriever.xbrl.entity_sectors import (  # noqa: E402
    DEFAULT_GROUPS_PATH,
    build_groups,
    check_groups,
    load_sector_tags,
    load_universe_groups,
)

DEFAULT_LEXICON = REPO_ROOT / "configs" / "eval" / "v2" / "lexicon.yaml"

_HEADER = """\
# Named universe groups for cross-filer questions (S3; docs/specs/sector-tags.md).
# Loaded and validated by src/retriever/xbrl/entity_sectors.py (load_universe_groups).
#
# `definitions` is the source of truth: group id -> exactly one rule (sectors, industries
# or tickers) over the tags in `sectors_file`. `groups` is GENERATED from it by
#     conda run -n py312 python scripts/eval/build_universe_groups.py
# never edit it by hand. Each group id needs phrases in configs/eval/v2/lexicon.yaml
# `universes`; `covered` is the corpus-wide universe and never a group here.
"""


def _flow_list(values: list[str]) -> str:
    return "[" + ", ".join(values) + "]"


def render_groups_file(doc: dict[str, Any], groups: dict[str, list[str]]) -> str:
    """Renders the groups file with the given resolved groups.

    Args:
        doc: As returned by ``load_universe_groups`` (its as-of, sectors file, definitions).
        groups: The resolved groups to write.

    Returns:
        The YAML text.
    """
    lines = [_HEADER.rstrip("\n"), "", f'as_of: "{doc["as_of"]}"',
             f"sectors_file: {doc['sectors_file']}", "definitions:"]
    for gid, rule in doc["definitions"].items():
        kind, values = next(iter(rule.items()))
        lines.append(f"  {gid}: {{{kind}: {_flow_list(list(values))}}}")
    lines.append("groups:")
    for gid in doc["definitions"]:
        lines.append(f"  {gid}: {_flow_list(groups[gid])}")
    return "\n".join(lines) + "\n"


def _live_corpus_tickers() -> set[str]:
    from src.eval.suites.v2_sources import connect_readonly

    conn = connect_readonly()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT trading_symbol FROM entity_master "
                        "WHERE trading_symbol IS NOT NULL")
            return {str(row[0]).strip() for row in cur.fetchall()}
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    """Parses the arguments, rewrites (or checks) the groups file and prints any problem.

    Args:
        argv: The arguments; ``sys.argv[1:]`` when None.

    Returns:
        The exit code.
    """
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--groups", type=Path, default=DEFAULT_GROUPS_PATH,
                   help="groups YAML (default: configs/eval/v2/groups.yaml)")
    p.add_argument("--lexicon", type=Path, default=DEFAULT_LEXICON,
                   help="v2 lexicon YAML (default: configs/eval/v2/lexicon.yaml)")
    p.add_argument("--check", action="store_true",
                   help="write nothing; fail when the committed groups are stale")
    p.add_argument("--corpus-from-tags", action="store_true",
                   help="use the sectors file's filers as the corpus (no database)")
    args = p.parse_args(argv)

    doc = load_universe_groups(args.groups)
    sectors_path = Path(doc["sectors_file"])
    if not sectors_path.is_absolute():
        sectors_path = REPO_ROOT / sectors_path
    tags = load_sector_tags(sectors_path)
    built = build_groups(tags, doc["definitions"])

    stale = built != doc["groups"]
    if stale and not args.check:
        args.groups.write_text(render_groups_file(doc, built), encoding="utf-8")
        print(f"rewrote {args.groups}")
    elif stale:
        print(f"{args.groups}: committed groups are stale; re-run without --check")
    doc = {**doc, "groups": built}

    if args.corpus_from_tags:
        corpus = {f["ticker"] for f in tags["filers"].values()}
    else:
        corpus = _live_corpus_tickers()
    universes = load_lexicon(args.lexicon).universes
    problems = check_groups(doc, tags, corpus, universes)
    for problem in problems:
        print(f"problem: {problem}")
    for gid, members in built.items():
        print(f"{gid}: {', '.join(members)}")
    return 1 if problems or (stale and args.check) else 0


if __name__ == "__main__":
    raise SystemExit(main())
