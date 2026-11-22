"""Fixtures for the debt-ifrs-elements spec tests.

Spec: docs/specs/debt-ifrs-elements.md. Committed under ``fixtures/``: TSM's FY2024 20-F
balance sheet as SEC Financial Statement Data Sets rows (byte copies of
statement-rules-by-element's fixture), TSM's companyfacts entries of the three borrowing
elements and the lease element for that filing, the IFRS taxonomy excerpt (standard label,
documentation and schema attributes of every element the spec lists or leaves out) and the
locked rules copy. Each file's SHA-256 is in ``fixtures/provenance.json``; the session fixture
verifies them before any test runs.

Outside this directory the tests read only: the live ``configs/eval/v2/statement_lines.yaml``
(the file the implementation changes), the committed concept mapping (its stated substitutes),
and key-definitions' locked copy and FSDS fixture rows (us-gaap filings, for the metamorphic
check that no us-gaap filing moves). No network, no database, no ``data/``.

Synthetic IFRS filings are built here from hand-written rows (made-up accessions
``0009999999-24-0000NN``).
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
FSDS_DIRS = sorted(p for p in (FIXTURES / "fsds").iterdir() if p.is_dir())
REPO = Path(__file__).resolve().parents[3]
LIVE_RULES = REPO / "configs" / "eval" / "v2" / "statement_lines.yaml"
MAPPING = REPO / "configs" / "concept_mapping" / "concept_family_coverage.yaml"
KD = REPO / "test" / "spec" / "key-definitions" / "fixtures"
LOCKED = FIXTURES / "statement_lines_debt.yaml"

TSM = "0001193125-25-083423"     # TSM 20-F FY2024 (2024-12-31), quarter 2025q2
TSM_CIK = "0001046179"
# key-definitions' us-gaap fixture filings (all 10-Ks, USD): ticker -> accession.
KD_FILINGS = {
    "AAPL": ("0000320193-25-000079", "2025-09-27"),
    "KO": ("0001628280-26-010047", "2025-12-31"),
    "WMT": ("0000104169-25-000021", "2025-01-31"),
    "TSLA": ("0001628280-26-003952", "2025-12-31"),
    "MCD": ("0000063908-26-000035", "2025-12-31"),
    "LLY": ("0000059478-26-000013", "2025-12-31"),
    "AMZN": ("0001018724-26-000004", "2025-12-31"),
    "GOOGL": ("0001652044-26-000018", "2025-12-31"),
}
SUB_HEADER = ("adsh cik name sic countryba stprba cityba zipba bas1 bas2 baph countryma stprma "
              "cityma zipma mas1 mas2 countryinc stprinc ein former changed afs wksi fye form "
              "period fy fp filed accepted prevrpt detail instance nciks aciks").split()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


_ENV: list[SimpleNamespace] = []


def env() -> SimpleNamespace:
    """Loaded tables and rules; called inside a test body (the loaders are product code)."""
    if not _ENV:
        from src.eval.keys.statement_witness import load_statement_rules
        from src.eval.sec_reference.fsds import load_tables

        _ENV.append(SimpleNamespace(
            tables=load_tables(FSDS_DIRS),
            kd_tables=load_tables(sorted(p for p in (KD / "fsds").iterdir() if p.is_dir())),
            locked=load_statement_rules(LOCKED),
            kd=load_statement_rules(KD / "statement_lines_kd.yaml"),
            live=load_statement_rules(LIVE_RULES),
        ))
    return _ENV[0]


def tables_copy() -> dict:
    """A deep copy of the TSM fixture tables, for metamorphic edits."""
    return {k: [dict(r) for r in v] for k, v in env().tables.items()}


def set_pre(tables: dict, adsh: str, line: int, **fields: str) -> None:
    """Edits fields of one ``pre`` row of ``adsh`` (exactly one row must match)."""
    rows = [r for r in tables["pre"] if r["adsh"] == adsh and r["line"] == str(line)]
    assert len(rows) == 1, (adsh, line)
    rows[0].update(fields)


def companyfacts() -> dict:
    """TSM's companyfacts document (SEC's shape), holding the fixture filing's entries."""
    return json.loads((FIXTURES / "companyfacts_excerpt.json").read_text(encoding="utf-8"))


def synthetic(n: int, lines: list[tuple[str, str, str, float]], uom: str = "EUR",
              stmt: str = "BS") -> tuple[str, dict]:
    """A synthetic IFRS 20-F (period 2024-12-31) presenting ``lines`` in order.

    Args:
        n: Distinguishes the made-up accession ``0009999999-24-0000NN``.
        lines: ``(tag, version, plabel, value)``; version ``"ext"`` means the filer's
            extension (the accession itself).
        uom: The unit of every value.
        stmt: The statement code of every line.

    Returns:
        ``(adsh, tables)``.
    """
    adsh = f"0009999999-24-{n:06d}"
    sub = dict.fromkeys(SUB_HEADER, "")
    sub.update(adsh=adsh, cik="9999999", name=f"SYNTHETIC IFRS FILER {n}", form="20-F",
               period="20241231", fy="2024", fp="FY", filed="20250401", prevrpt="0")
    pre, num, tag = [], [], []
    for i, (t, version, plabel, value) in enumerate(lines, start=3):
        v = adsh if version == "ext" else version
        pre.append({"adsh": adsh, "report": "2", "line": str(i), "stmt": stmt, "inpth": "0",
                    "rfile": "H", "tag": t, "version": v, "plabel": plabel, "negating": "0"})
        num.append({"adsh": adsh, "tag": t, "version": v, "ddate": "20241231", "qtrs": "0",
                    "uom": uom, "segments": "", "coreg": "", "value": repr(value),
                    "footnote": ""})
        tag.append({"tag": t, "version": v, "custom": "1" if version == "ext" else "0",
                    "abstract": "0", "datatype": "monetary", "iord": "I", "crdr": "C",
                    "tlabel": "", "doc": ""})
    return adsh, {"sub": [sub], "pre": pre, "num": num, "tag": tag}


@pytest.fixture(scope="session")
def kit():
    """Fixture paths, helpers and the excerpt; verifies every fixture file's SHA-256 first."""
    prov = json.loads((FIXTURES / "provenance.json").read_text(encoding="utf-8"))
    for rel, digest in prov["files"].items():
        assert _sha(FIXTURES / rel) == digest, f"fixture {rel} differs from its provenance"
    excerpt = json.loads((FIXTURES / "taxonomy_excerpt_ifrs.json").read_text(encoding="utf-8"))
    return SimpleNamespace(
        env=env, tables_copy=tables_copy, set_pre=set_pre, companyfacts=companyfacts,
        synthetic=synthetic, copy=copy.deepcopy, sha=_sha, provenance=prov, excerpt=excerpt,
        listed=["ifrs-full:" + n for n in excerpt["listed"]],
        excluded=["ifrs-full:" + n for n in excerpt["excluded"]],
        live_rules=LIVE_RULES, locked_path=LOCKED, mapping=MAPPING, kd=KD,
        TSM=TSM, TSM_CIK=TSM_CIK, KD_FILINGS=KD_FILINGS,
    )
