"""Author, check, build and seal the hand-written held-out set B.

External test v1 (docs/specs/set-b-tooling.md). Set B's answer keys are
confirmed against our fact store by SQL only: this script never runs the retrieval
pipeline, never calls an LLM, and never prints a question, a note or an expected amount.
Keep the authoring file and the built suite outside the repo (for example under
``<vault>\\set_b\\``) until the unseal step.

    # $B = the vault folder, outside the repo
    conda run -n py312 python scripts/eval/set_b.py validate   $B/set_b.yaml
    conda run -n py312 python scripts/eval/set_b.py check-keys $B/set_b.yaml
    conda run -n py312 python scripts/eval/set_b.py build      $B/set_b.yaml --out $B/b.json
    conda run -n py312 python scripts/eval/set_b.py seal       $B/b.json
    conda run -n py312 python scripts/eval/set_b.py verify     $B/b.json <sha256>

``check-keys`` and ``build`` need the local Postgres ``xbrl_db`` (read-only). Costs $0.
Guide: docs/guides/xbrl-flow/12-heldout-set-b.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.eval.set_b.base import KeyFactStore  # noqa: E402
from src.eval.set_b.core import (  # noqa: E402
    build_suite,
    canonical_bytes,
    load_authoring,
    render_check_report,
    resolve_case,
    sha256_hex,
    validate,
)
from src.logging_config import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)


class LabelRecordingStore(KeyFactStore):
    """Delegates to a store and keeps every member label it hands out.

    ``build_suite`` needs ``{fact_id: member_label}`` for breakdown rows; the labels
    come from the candidates the check already read, so no second query is made.
    """

    def __init__(self, inner: KeyFactStore) -> None:
        """Wraps a store.

        Args:
            inner: The store that reads the facts.
        """
        self._inner = inner
        self.labels: dict[int, str] = {}

    def facts_for(self, ticker: str, fiscal_year: int) -> list[dict]:
        """Returns the inner store's candidates, recording their member labels.

        Args:
            ticker: The filer's ticker.
            fiscal_year: The fiscal year.

        Returns:
            The candidates.
        """
        return self._record(self._inner.facts_for(ticker, fiscal_year))

    def breakdown_facts_for(self, ticker: str, fiscal_year: int) -> list[dict]:
        """Returns the inner store's breakdown members, recording their member labels.

        Reads the inner store's ``breakdown_facts_for`` (never its ``facts_for``), so a
        breakdown row is labelled with its breakdown member
        (docs/specs/set-b-dimensional-s1.md, requirement 3).

        Args:
            ticker: The filer's ticker.
            fiscal_year: The fiscal year.

        Returns:
            The breakdown candidates.
        """
        return self._record(self._inner.breakdown_facts_for(ticker, fiscal_year))

    def _record(self, facts: list[dict]) -> list[dict]:
        """Records each candidate's member label and returns the candidates."""
        for fact in facts:
            if fact.get("member_label") is not None:
                self.labels[int(fact["fact_id"])] = fact["member_label"]
        return facts


def is_inside(path: Path, root: Path) -> bool:
    """Whether ``path`` resolves to ``root`` or somewhere below it.

    Args:
        path: The path to test (need not exist).
        root: The directory.

    Returns:
        True when the path lies inside ``root``.
    """
    return path.resolve().is_relative_to(root.resolve())


def render_validation(result: dict) -> str:
    """Renders ``validate``'s result: errors, warnings, then the mix.

    Args:
        result: ``{"errors", "warnings", "mix"}`` from ``validate``; its messages carry
            no question or note text.

    Returns:
        The text to print.
    """
    lines = [f"errors: {len(result['errors'])}"]
    lines += [f"  {m}" for m in result["errors"]]
    lines.append(f"warnings: {len(result['warnings'])}")
    lines += [f"  {m}" for m in result["warnings"]]
    mix = result["mix"]
    lines.append(f"mix: {mix['total']} cases")
    for axis in ("kind", "ticker", "fy"):
        counts = ", ".join(f"{k} {v}" for k, v in mix[axis].items())
        lines.append(f"  {axis}: {counts or '-'}")
    return "\n".join(lines) + "\n"


def _read_cases(path: Path) -> list[dict]:
    if is_inside(path, REPO_ROOT):
        print(f"warning: {path} is inside the repo working tree; keep set B outside it")
    return load_authoring(path.read_text(encoding="utf-8"))


def _validated(path: Path) -> list[dict] | None:
    """Reads and validates; prints the validation and returns None on errors."""
    cases = _read_cases(path)
    result = validate(cases)
    if result["errors"]:
        print(render_validation(result), end="")
        return None
    return cases


def _resolve_all(cases: list[dict], store: KeyFactStore) -> list[dict]:
    return [resolve_case(case, store) for case in cases]


def _postgres_store() -> KeyFactStore:
    from src.eval.set_b.pg_store import PostgresKeyFactStore

    return PostgresKeyFactStore()


def cmd_validate(path: Path) -> int:
    """``validate FILE``: structure and scope; exit 1 on errors."""
    result = validate(_read_cases(path))
    print(render_validation(result), end="")
    return 1 if result["errors"] else 0


def cmd_check_keys(path: Path, store: KeyFactStore) -> int:
    """``check-keys FILE``: every answer key against the fact store; exit 1 unless all verify."""
    cases = _validated(path)
    if cases is None:
        return 1
    resolutions = _resolve_all(cases, store)
    print(render_check_report(resolutions), end="")
    return 0 if all(r["status"] == "verified" for r in resolutions) else 1


def cmd_build(path: Path, out: Path, store: KeyFactStore, *, allow_unverified: bool,
              force_in_repo: bool) -> int:
    """``build FILE --out OUT``: validate, resolve, write the canonical suite, print its hash."""
    if is_inside(out, REPO_ROOT) and not force_in_repo:
        print(f"refusing to write {out}: it is inside the repo working tree. Keep set B "
              "outside the repo until the unseal step (or pass --force-in-repo).")
        return 1
    cases = _validated(path)
    if cases is None:
        return 1
    recorder = LabelRecordingStore(store)
    resolutions = _resolve_all(cases, recorder)
    print(render_check_report(resolutions), end="")
    try:
        suite = build_suite(cases, resolutions, recorder.labels,
                            allow_unverified=allow_unverified)
    except ValueError as exc:
        print(f"not built: {exc} (fix them, or pass --allow-unverified to leave them out)")
        return 1
    data = canonical_bytes(suite)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    if suite.get("excluded"):
        print(f"excluded (unverified): {', '.join(suite['excluded'])}")
    print(f"wrote {out} ({suite['n_cases']} cases)")
    print(f"sha256 {sha256_hex(data)}")
    return 0


def cmd_seal(out: Path) -> int:
    """``seal OUT``: print ``<sha256>  <basename>``."""
    print(f"{sha256_hex(out.read_bytes())}  {out.name}")
    return 0


def cmd_verify(out: Path, expected_hash: str) -> int:
    """``verify OUT HASH``: exit 1 when the file's SHA-256 differs from HASH."""
    actual = sha256_hex(out.read_bytes())
    if actual == expected_hash.strip().lower():
        print(f"OK {actual}  {out.name}")
        return 0
    print(f"MISMATCH {out.name}: expected {expected_hash.strip().lower()}, got {actual}")
    return 1


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("validate", help="check structure and scope (no database)")
    s.add_argument("file", type=Path, help="the YAML authoring file")
    s = sub.add_parser("check-keys", help="confirm every answer key against xbrl_db")
    s.add_argument("file", type=Path, help="the YAML authoring file")
    s = sub.add_parser("build", help="validate, resolve and write the sealed-ready suite")
    s.add_argument("file", type=Path, help="the YAML authoring file")
    s.add_argument("--out", type=Path, required=True, help="the suite JSON to write")
    s.add_argument("--allow-unverified", action="store_true",
                   help="leave unverified cases out (listed in 'excluded') instead of failing")
    s.add_argument("--force-in-repo", action="store_true",
                   help="allow --out inside the repo working tree (only at the unseal step)")
    s = sub.add_parser("seal", help="print '<sha256>  <basename>' of a built suite")
    s.add_argument("out", type=Path, help="the built suite JSON")
    s = sub.add_parser("verify", help="exit 1 unless a built suite matches a hash")
    s.add_argument("out", type=Path, help="the built suite JSON")
    s.add_argument("hash", help="the recorded sha256")
    return p


def main(argv: list[str] | None = None, store: KeyFactStore | None = None) -> int:
    """Parses the arguments and runs one subcommand.

    Args:
        argv: The command line, without the program name; ``sys.argv[1:]`` when None.
        store: The fact store for ``check-keys`` and ``build``; the read-only Postgres
            store when None.

    Returns:
        The exit code.
    """
    args = _parser().parse_args(argv)
    configure_logging()
    if args.command == "validate":
        return cmd_validate(args.file)
    if args.command == "seal":
        return cmd_seal(args.out)
    if args.command == "verify":
        return cmd_verify(args.out, args.hash)

    owned = store is None
    the_store = store if store is not None else _postgres_store()
    try:
        if args.command == "check-keys":
            return cmd_check_keys(args.file, the_store)
        return cmd_build(args.file, args.out, the_store,
                         allow_unverified=args.allow_unverified,
                         force_in_repo=args.force_in_repo)
    finally:
        close = getattr(the_store, "close", None)
        if owned and callable(close):
            close()


if __name__ == "__main__":
    # Member labels may be non-ASCII; never fail on a Windows console code page.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
