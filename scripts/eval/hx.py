"""Author, check, build and seal the hand-written external set H-X.

v2 evaluation (docs/specs/hx-authoring.md). H-X's numeric answer keys are confirmed against
our fact store by SQL only: this script never runs the retrieval pipeline, never calls an
LLM, and never prints a question, note, answer, passage, amount, accession or ticker; its
output is case ids, field paths, kinds, sub-types, statuses and counts. Keep the authoring
file and the built suite outside the repo (for example under
``<vault>\\h_x\\``) until the unseal step.

    # $X = the vault folder, outside the repo; FILERS = the filer-sets YAML
    conda run -n py312 python scripts/eval/hx.py validate   $X/hx.yaml --filers FILERS
    conda run -n py312 python scripts/eval/hx.py check-keys $X/hx.yaml --filers FILERS
    conda run -n py312 python scripts/eval/hx.py build      $X/hx.yaml --filers FILERS \
        --out $X/hx.json
    conda run -n py312 python scripts/eval/hx.py seal       $X/hx.json
    conda run -n py312 python scripts/eval/hx.py verify     $X/hx.json <sha256>

The filer-sets file is YAML with two lists of tickers, ``dev_filers`` (the development
filers H-X may name) and ``heldout_filers`` (never allowed). ``check-keys`` and ``build``
need the local Postgres ``xbrl_db`` (read-only). Costs $0.
Guide: docs/guides/xbrl-flow/12-heldout-set-b.md (section 12.4).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.eval.set_b.core import canonical_bytes, load_authoring, sha256_hex  # noqa: E402
from src.eval.set_b.hx import (  # noqa: E402
    build_hx_suite,
    render_hx_report,
    resolve_hx_case,
    validate_hx,
)
from src.eval.set_b.hx_base import HxKeyFactStore  # noqa: E402
from src.logging_config import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)


class InputError(Exception):
    """A file could not be read; the message carries no file content."""


def is_inside(path: Path, root: Path) -> bool:
    """Whether ``path`` resolves to ``root`` or somewhere below it.

    Args:
        path: The path to test (need not exist).
        root: The directory.

    Returns:
        True when the path lies inside ``root``.
    """
    return path.resolve().is_relative_to(root.resolve())


def parse_filer_sets(text: str) -> tuple[frozenset[str], frozenset[str]]:
    """Parses the filer-sets YAML: ``dev_filers`` and ``heldout_filers`` lists.

    Args:
        text: The YAML text.

    Returns:
        ``(dev_filers, heldout_filers)``.

    Raises:
        InputError: If the document is not a mapping with two lists of non-empty
            strings, or ``dev_filers`` is empty. The message names keys only.
    """
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise InputError("the filer-sets file is not valid YAML") from exc
    if not isinstance(doc, dict):
        raise InputError("the filer-sets file must be a mapping with dev_filers and "
                         "heldout_filers")
    sets = []
    for key in ("dev_filers", "heldout_filers"):
        value = doc.get(key)
        if not isinstance(value, list) or not all(
                isinstance(t, str) and t.strip() for t in value):
            raise InputError(f"filer-sets: {key} must be a list of tickers")
        sets.append(frozenset(t.strip() for t in value))
    if not sets[0]:
        raise InputError("filer-sets: dev_filers is empty")
    overlap = sets[0] & sets[1]
    if overlap:
        raise InputError(f"filer-sets: {len(overlap)} ticker(s) are in both lists")
    return sets[0], sets[1]


def _read_filers(path: Path) -> tuple[frozenset[str], frozenset[str]]:
    return parse_filer_sets(path.read_text(encoding="utf-8"))


def _read_cases(path: Path) -> list[dict]:
    """Loads the authoring file; a YAML error reports its position only."""
    if is_inside(path, REPO_ROOT):
        print(f"warning: {path.name} is inside the repo working tree; keep H-X outside it")
    try:
        return load_authoring(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
        raise InputError(f"the authoring file is not valid YAML{where}") from None
    except ValueError as exc:  # load_authoring: no `cases:` list (names keys only)
        raise InputError(str(exc)) from None


def render_validation(result: dict) -> str:
    """Renders ``validate_hx``'s result: errors, warnings, then the mix.

    Args:
        result: ``{"errors", "warnings", "mix"}`` from ``validate_hx``; its messages carry
            ids, field paths and counts only.

    Returns:
        The text to print.
    """
    lines = [f"errors: {len(result['errors'])}"]
    lines += [f"  {m}" for m in result["errors"]]
    lines.append(f"warnings: {len(result['warnings'])}")
    lines += [f"  {m}" for m in result["warnings"]]
    mix = result["mix"]
    lines.append(f"mix: {mix['total']} cases, {mix['filers']} distinct filers")
    for axis in ("kind", "subtype"):
        counts = ", ".join(f"{k} {v}" for k, v in mix[axis].items())
        lines.append(f"  {axis}: {counts or '-'}")
    return "\n".join(lines) + "\n"


def _validated(path: Path, filers: Path) -> list[dict] | None:
    """Reads and validates; prints the validation and returns None on errors."""
    dev, held = _read_filers(filers)
    cases = _read_cases(path)
    result = validate_hx(cases, dev, held)
    if result["errors"]:
        print(render_validation(result), end="")
        return None
    return cases


def _postgres_store() -> HxKeyFactStore:
    from src.eval.set_b.hx_pg_store import PostgresHxKeyFactStore

    return PostgresHxKeyFactStore()


def cmd_validate(path: Path, filers: Path) -> int:
    """``validate FILE``: structure and filers; exit 1 on errors."""
    dev, held = _read_filers(filers)
    result = validate_hx(_read_cases(path), dev, held)
    print(render_validation(result), end="")
    return 1 if result["errors"] else 0


def cmd_check_keys(path: Path, filers: Path, store: HxKeyFactStore) -> int:
    """``check-keys FILE``: every numeric key against the fact store.

    Exit 1 unless every case is verified or skipped.
    """
    cases = _validated(path, filers)
    if cases is None:
        return 1
    resolutions = [resolve_hx_case(case, store) for case in cases]
    print(render_hx_report(resolutions), end="")
    return 0 if all(r["status"] in ("verified", "skipped") for r in resolutions) else 1


def cmd_build(path: Path, filers: Path, out: Path, store: HxKeyFactStore, *,
              allow_unverified: bool, force_in_repo: bool) -> int:
    """``build FILE --out OUT``: validate, resolve, write the canonical suite, print its hash."""
    if is_inside(out, REPO_ROOT) and not force_in_repo:
        print(f"refusing to write {out.name}: it is inside the repo working tree. Keep H-X "
              "outside the repo until the unseal step (or pass --force-in-repo).")
        return 1
    cases = _validated(path, filers)
    if cases is None:
        return 1
    resolutions = [resolve_hx_case(case, store) for case in cases]
    print(render_hx_report(resolutions), end="")
    try:
        suite = build_hx_suite(cases, resolutions, allow_unverified=allow_unverified)
    except ValueError as exc:
        print(f"not built: {exc} (fix them, or pass --allow-unverified to leave them out)")
        return 1
    data = canonical_bytes(suite)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    if suite.get("excluded"):
        print(f"excluded (unverified): {', '.join(suite['excluded'])}")
    print(f"wrote {out.name} ({suite['n_cases']} cases)")
    print(f"sha256 {sha256_hex(data)}")
    return 0


def cmd_seal(out: Path) -> int:
    """``seal OUT``: print ``<sha256>  <basename>``."""
    print(f"{sha256_hex(out.read_bytes())}  {out.name}")
    return 0


def cmd_verify(out: Path, expected_hash: str) -> int:
    """``verify OUT HASH``: exit 1 when the file's SHA-256 differs from HASH."""
    actual = sha256_hex(out.read_bytes())
    want = expected_hash.strip().lower()
    if actual == want:
        print(f"OK {actual}  {out.name}")
        return 0
    print(f"MISMATCH {out.name}: expected {want}, got {actual}")
    return 1


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    for name, text in (("validate", "check structure and filers (no database)"),
                       ("check-keys", "confirm every numeric answer key against xbrl_db"),
                       ("build", "validate, resolve and write the seal-ready suite")):
        s = sub.add_parser(name, help=text)
        s.add_argument("file", type=Path, help="the YAML authoring file")
        s.add_argument("--filers", type=Path, required=True,
                       help="YAML with dev_filers and heldout_filers lists")
        if name == "build":
            s.add_argument("--out", type=Path, required=True, help="the suite JSON to write")
            s.add_argument("--allow-unverified", action="store_true",
                           help="leave unverified cases out (listed in 'excluded')")
            s.add_argument("--force-in-repo", action="store_true",
                           help="allow --out inside the repo (only at the unseal step)")
    s = sub.add_parser("seal", help="print '<sha256>  <basename>' of a built suite")
    s.add_argument("out", type=Path, help="the built suite JSON")
    s = sub.add_parser("verify", help="exit 1 unless a built suite matches a hash")
    s.add_argument("out", type=Path, help="the built suite JSON")
    s.add_argument("hash", help="the recorded sha256")
    return p


def main(argv: list[str] | None = None, store: HxKeyFactStore | None = None) -> int:
    """Parses the arguments and runs one subcommand.

    Args:
        argv: The command line, without the program name; ``sys.argv[1:]`` when None.
        store: The fact store for ``check-keys`` and ``build``; the read-only Postgres
            store when None.

    Returns:
        The exit code (2 for an unreadable input file).
    """
    args = _parser().parse_args(argv)
    configure_logging()
    if args.command == "seal":
        return cmd_seal(args.out)
    if args.command == "verify":
        return cmd_verify(args.out, args.hash)
    try:
        if args.command == "validate":
            return cmd_validate(args.file, args.filers)
        owned = store is None
        the_store = store if store is not None else _postgres_store()
        try:
            if args.command == "check-keys":
                return cmd_check_keys(args.file, args.filers, the_store)
            return cmd_build(args.file, args.filers, args.out, the_store,
                             allow_unverified=args.allow_unverified,
                             force_in_repo=args.force_in_repo)
        finally:
            close = getattr(the_store, "close", None)
            if owned and callable(close):
                close()
    except InputError as exc:
        # Key names and positions only, never file content.
        print(f"error: {exc}")
        return 2


if __name__ == "__main__":
    # Never fail on a Windows console code page.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
