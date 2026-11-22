# Cuts SEC's Financial Statement Data Sets quarters down to a CIK list, the rule the
# development cut data/cache/sec_fsds/filtered follows: sub rows of the filers, num and pre
# rows of their filings, tag rows those use; every kept line byte for byte.
# Spec: docs/specs/hf-suite-build.md, requirement 10.

from __future__ import annotations

import re
import zipfile
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path

TABLES = ("sub", "num", "pre", "tag")
_QUARTER = re.compile(r"\d{4}q[1-4]")


def _lines(data: bytes) -> list[bytes]:
    """The non-empty lines of a table, each without its line end."""
    out = []
    for line in data.split(b"\n"):
        if line.endswith(b"\r"):
            line = line[:-1]
        if line:
            out.append(line)
    return out


def _columns(header: bytes, *names: str) -> list[int]:
    """The positions of the named columns in a header line."""
    cols = header.decode("utf-8").split("\t")
    missing = [n for n in names if n not in cols]
    if missing:
        raise ValueError(f"FSDS table header lacks column(s) {missing}")
    return [cols.index(n) for n in names]


def _field(row: list[bytes], i: int) -> bytes:
    return row[i] if i < len(row) else b""


def _cik(value: bytes) -> int | None:
    text = value.decode("utf-8", "replace").strip()
    return int(text) if text.isdigit() else None


def _join(header: bytes, kept: list[bytes]) -> bytes:
    return b"".join(line + b"\n" for line in (header, *kept))


def cut_fsds_tables(raw: Mapping[str, bytes], ciks: Collection[str]) -> dict[str, bytes]:
    """Keeps the rows of the given filers in one quarter's four tables.

    Args:
        raw: ``{"sub", "num", "pre", "tag"}`` -> the bytes of SEC's ``<table>.txt``.
        ciks: The filers' CIKs (padded or not).

    Returns:
        ``{"sub", "num", "pre", "tag"}`` -> the cut bytes (header plus kept lines).

    Raises:
        ValueError: ``ciks`` is empty.
    """
    wanted = {int(str(c).strip()) for c in ciks}
    if not wanted:
        raise ValueError("the FSDS cut needs at least one CIK")
    out: dict[str, bytes] = {}

    sub = _lines(raw["sub"])
    adsh_i, cik_i = _columns(sub[0], "adsh", "cik")
    kept_sub = [ln for ln in sub[1:] if _cik(_field(ln.split(b"\t"), cik_i)) in wanted]
    out["sub"] = _join(sub[0], kept_sub)
    filings = {_field(ln.split(b"\t"), adsh_i) for ln in kept_sub}

    used: set[tuple[bytes, bytes]] = set()
    for name in ("num", "pre"):
        lines = _lines(raw[name])
        a_i, t_i, v_i = _columns(lines[0], "adsh", "tag", "version")
        kept = []
        for ln in lines[1:]:
            row = ln.split(b"\t")
            if _field(row, a_i) in filings:
                kept.append(ln)
                used.add((_field(row, t_i), _field(row, v_i)))
        out[name] = _join(lines[0], kept)

    tag = _lines(raw["tag"])
    t_i, v_i = _columns(tag[0], "tag", "version")
    kept_tag = [ln for ln in tag[1:]
                if (_field(ln.split(b"\t"), t_i), _field(ln.split(b"\t"), v_i)) in used]
    out["tag"] = _join(tag[0], kept_tag)
    return {t: out[t] for t in TABLES}


def cut_fsds(zips: Sequence[str | Path], ciks: Collection[str],
             out_dir: str | Path) -> dict[str, dict[str, int]]:
    """Cuts each quarter zip into ``<out_dir>/<yyyy>q<n>/<table>.tsv``.

    Args:
        zips: Quarter zips named ``<yyyy>q<n>.zip``.
        ciks: The filers' CIKs (padded or not).
        out_dir: The directory to write into.

    Returns:
        quarter -> table -> kept rows (the header not counted).

    Raises:
        ValueError: A zip name is not a quarter, or ``ciks`` is empty.
        FileNotFoundError: A zip lacks one of the four tables.
    """
    paths = [Path(z) for z in zips]
    for path in paths:
        if path.suffix.lower() != ".zip" or not _QUARTER.fullmatch(path.stem.lower()):
            raise ValueError(f"{path.name}: not a quarter zip (<yyyy>q<n>.zip)")
    if not ciks:
        raise ValueError("the FSDS cut needs at least one CIK")
    root = Path(out_dir)
    counts: dict[str, dict[str, int]] = {}
    for path in paths:
        quarter = path.stem.lower()
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            raw: dict[str, bytes] = {}
            for table in TABLES:
                member = f"{table}.txt"
                if member not in names:
                    raise FileNotFoundError(f"{path.name}: no {member}")
                raw[table] = z.read(member)
        cut = cut_fsds_tables(raw, ciks)
        target = root / quarter
        target.mkdir(parents=True, exist_ok=True)
        counts[quarter] = {}
        for table in TABLES:
            (target / f"{table}.tsv").write_bytes(cut[table])
            counts[quarter][table] = len(_lines(cut[table])) - 1
    return counts
