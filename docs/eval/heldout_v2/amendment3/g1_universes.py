"""Write the held-out corpus's universes for the H-F runs of the look (Amendment 3.1).

The H-F keys put a held-out filer into a named group by the committed rule (the group's
``definitions`` in ``configs/eval/v2/groups.yaml`` applied to the committed sector tags plus
the held-out tags; docs/specs/hf-suite-build.md section 4). This script applies the same
function, ``heldout_groups``, and writes two files outside the repository:

- ``--groups-out``: a groups file in the committed file's format (same ``as_of``,
  ``sectors_file`` and ``definitions``; ``groups`` over the held-out corpus). It shows whether
  the committed groups already hold for the held-out corpus. On 2026-11-18 they did (no
  held-out filer joins a group), so the look uses the committed file unchanged (Amendment
  3.1). The file is never committed: in general it can name held-out filers.
- ``--universes-out``: the ``--sec-universes`` file of the ``frontier_sec_tools`` H-F runs,
  ``{covered: [{ticker, cik}], groups, as_of}``. ``covered`` is every filer of the H-F
  ``covered`` universe: the committed lexicon's companies plus the overlay's, with their
  CIKs from the committed and held-out sector tags.

Run by the maintainer from the repository root (no database, no LLM, $0):

    $HO = "D:\\Tools\\AI\\XbrlRag_heldout\\data\\v2"
    conda run -n py312 python docs/eval/heldout_v2/amendment3/g1_universes.py `
        --heldout-tags "$HO\\sectors_heldout.yaml" --overlay "$HO\\overlay.yaml" `
        --groups-out "$HO\\look\\groups_heldout.yaml" `
        --universes-out "$HO\\look\\sec_universes_heldout.yaml"

It prints counts and SHA-256s only, never a ticker, CIK or name. Exit codes: 0 written,
1 a check failed (nothing written), 2 refused (an output inside the repository).
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from src.eval.suites.heldout_build import (  # noqa: E402
    heldout_groups,
    merge_lexicon_overlay,
    require_outside,
)
from src.eval.suites.intents import load_lexicon  # noqa: E402
from src.retriever.xbrl.entity_sectors import (  # noqa: E402
    load_sector_tags,
    load_universe_groups,
)

COMMITTED_GROUPS = REPO_ROOT / "configs" / "eval" / "v2" / "groups.yaml"
COMMITTED_LEXICON = REPO_ROOT / "configs" / "eval" / "v2" / "lexicon.yaml"


def _dump(doc: dict[str, Any], path: Path) -> str:
    """Writes ``doc`` as YAML and returns the file's SHA-256.

    Args:
        doc: The document.
        path: Where to write it (its folder is created).

    Returns:
        The hex SHA-256 of the written bytes.
    """
    data = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def build(heldout_tags: Path, overlay: Path) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Builds the groups file and the universes file, with a counts-only report.

    Args:
        heldout_tags: The held-out sector tags (the committed sectors file's format).
        overlay: The H-F lexicon overlay (held-out companies).

    Returns:
        ``(groups_doc, universes_doc, report_lines)``.

    Raises:
        ValueError: A filer in both tag sets, stale committed groups, or a covered filer
            with no CIK in either tag set, or a tagged held-out filer not in the overlay.
    """
    committed = load_universe_groups(COMMITTED_GROUPS)
    dev_tags = load_sector_tags(REPO_ROOT / committed["sectors_file"])
    held_tags = load_sector_tags(heldout_tags)
    groups = heldout_groups(committed, dev_tags, held_tags)

    held_tickers = {f["ticker"] for f in held_tags["filers"].values()}
    cik_of = {f["ticker"]: cik for tags in (dev_tags, held_tags)
              for cik, f in tags["filers"].items()}
    base = load_lexicon(COMMITTED_LEXICON)
    lexicon = merge_lexicon_overlay(base, overlay)
    covered_tickers = sorted(lexicon.companies)
    overlay_tickers = set(lexicon.companies) - set(base.companies)
    missing = [t for t in covered_tickers if t not in cik_of]
    if missing:
        raise ValueError(f"{len(missing)} covered filer(s) have no CIK in either tag set")
    if overlay_tickers != held_tickers:
        raise ValueError(
            f"the overlay's companies and the held-out tags differ: "
            f"{len(overlay_tickers - held_tickers)} only in the overlay, "
            f"{len(held_tickers - overlay_tickers)} only in the tags")

    groups_doc = {"as_of": committed["as_of"], "sectors_file": committed["sectors_file"],
                  "definitions": committed["definitions"], "groups": groups}
    universes_doc = {"covered": [{"ticker": t, "cik": cik_of[t]} for t in covered_tickers],
                     "groups": groups, "as_of": committed["as_of"]}

    lines = [f"covered: {len(covered_tickers)} filers "
             f"({len(covered_tickers) - len(overlay_tickers)} development, "
             f"{len(overlay_tickers)} held-out)"]
    for gid, members in groups.items():
        n_held = sum(1 for t in members if t in held_tickers)
        lines.append(f"group {gid}: {len(members)} members "
                     f"({len(committed['groups'][gid])} committed, {n_held} held-out)")
    return groups_doc, universes_doc, lines


def main(argv: list[str] | None = None) -> int:
    """Parses the arguments, builds, checks and writes both files, and prints counts.

    Args:
        argv: The arguments; ``sys.argv[1:]`` when None.

    Returns:
        The exit code: 0 written, 1 a check failed, 2 refused.
    """
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--heldout-tags", type=Path, required=True)
    p.add_argument("--overlay", type=Path, required=True)
    p.add_argument("--groups-out", type=Path, required=True)
    p.add_argument("--universes-out", type=Path, required=True)
    p.add_argument("--show-error", action="store_true",
                   help="print a failed check's message (it can quote a tag value)")
    args = p.parse_args(argv)
    try:
        groups_out = require_outside(args.groups_out, REPO_ROOT)
        universes_out = require_outside(args.universes_out, REPO_ROOT)
    except ValueError:
        print("refused: both outputs must lie outside the repository", file=sys.stderr)
        return 2
    try:
        groups_doc, universes_doc, lines = build(args.heldout_tags, args.overlay)
    except (ValueError, OSError) as exc:
        # A loader's message can quote a tag value, so it is shown only on request.
        detail = f": {exc}" if args.show_error else " (rerun with --show-error to see why)"
        print(f"check failed, nothing written: {type(exc).__name__}{detail}", file=sys.stderr)
        return 1
    print("\n".join(lines))
    g_hash = _dump(groups_doc, groups_out)
    u_hash = _dump(universes_doc, universes_out)
    reloaded = load_universe_groups(groups_out)  # the router's own loader must accept it
    if reloaded["groups"] != groups_doc["groups"]:
        print("check failed: the written groups file does not reload unchanged",
              file=sys.stderr)
        return 1
    print(f"{g_hash}  {groups_out.name}")
    print(f"{u_hash}  {universes_out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
