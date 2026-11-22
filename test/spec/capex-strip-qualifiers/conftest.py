"""Fixtures for the capex-strip-qualifiers spec tests.

Spec: docs/specs/capex-strip-qualifiers.md. Committed under ``fixtures/``: TSLA's FY2025 10-K
cash-flow statement as SEC Financial Statement Data Sets rows (byte copies of
statement-rules-by-element's fixture), SHA-256 in ``fixtures/provenance.json``, verified by
the session fixture before any test runs.

Outside this directory the tests read: the live ``configs/eval/v2/statement_lines.yaml``, the
accepted r2.1 copy and its record, the judged copy and record this change adds, and the
retired witness-rules-r2-1 fixtures (its oracle, us-gaap 2025 label excerpt and the synthetic
sample). They never read a judging sample: judge3's hashes are constants here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
FSDS_DIRS = sorted(p for p in (FIXTURES / "fsds").iterdir() if p.is_dir())
REPO = Path(__file__).resolve().parents[3]
V2 = REPO / "configs" / "eval" / "v2"
LIVE_RULES = V2 / "statement_lines.yaml"
ACCEPTED_R2_1 = V2 / "statement_lines.accepted_r2_1.yaml"
# The judged candidate (a byte copy of the live rules) and its one judging record.
ACCEPTED_STRIP = V2 / "statement_lines.accepted_strip.yaml"
RECORD = V2 / "statement_lines.acceptance_strip.json"
R21 = REPO / "test" / "spec" / "witness-rules-r2-1" / "fixtures"
SYNTH = R21 / "synthetic"
KD_RULES = REPO / "test" / "spec" / "key-definitions" / "fixtures" / "statement_lines_kd.yaml"
DEBT_RULES = (REPO / "test" / "spec" / "debt-ifrs-elements" / "fixtures"
              / "statement_lines_debt.yaml")

TSLA = "0001628280-26-003952"   # TSLA 10-K FY2025 (2025-12-31), quarter 2026q1

# judge3 (spec Notes): positions 360-479 of witness-rules-r2's seeded order. Pinned before
# anything read the tables; never opened by a test.
JUDGE3_SHA256 = {
    "sub": "d582e91a63539785cac828cbec470431f3c91f9bdc74f4ad10a2dfc710aa1c5c",
    "pre": "0ff29edfb3c5fcb2e51b8ed48d4f9eb2ca6a1d3478a9f5098a8d2cfb2110b84a",
    "num": "221a3c800db46b2568e253736330827e5d2481540e41907469ccd3c794dcae99",
    "tag": "87e616d50bbb5340d70ef1b68750c322275916a0c5ca6430957d547ddece5f15",
}
# The accepted r2.1 copy and the r2.1 oracle (witness-rules-r2-1's record pins both).
ACCEPTED_R2_1_SHA256 = "1f4612bc4be62c95915ba8e1bc9c48290ac3d026541904ece0bfc35bcc493248"
ORACLE_R2_1_SHA256 = "7cde3f087888f8e029ffd89d27bef452d0cf456a5e2002a77aca9968d76bf9ca"
# Families not judged (definitions owned by key-definitions / debt-ifrs-elements).
UNJUDGED = ("Debt", "DeferredRevenue")
OUTCOMES = ("hit", "none", "ambiguous", "wrong", "oracle_ambiguous", "no_oracle",
            "no_oracle_selected")


def sha256(path: Path) -> str:
    """SHA-256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def table_hashes(directory: Path) -> dict[str, str]:
    """The four table hashes of a sample directory."""
    return {t: sha256(directory / f"{t}.tsv") for t in ("sub", "pre", "num", "tag")}


def labels() -> dict[str, str | None]:
    """us-gaap 2025 standard label per oracle element (None: absent), from r2.1's excerpt."""
    ex = json.loads((R21 / "taxonomy_excerpt.json").read_text(encoding="utf-8"))
    out: dict[str, str | None] = {k.split(":", 1)[1]: v["label"]
                                  for k, v in ex["elements"].items()}
    out.update({k.split(":", 1)[1]: None for k in ex["absent"]})
    return out


def counts(**kw: int) -> dict[str, int]:
    """A full seven-outcome count map, zeros where not given."""
    return {o: kw.get(o, 0) for o in OUTCOMES}


_ENV: list[SimpleNamespace] = []


def loaded() -> SimpleNamespace:
    """Loaded TSLA tables and rules files; called inside a test body (loaders are product code)."""
    if not _ENV:
        from src.eval.keys.statement_witness import load_statement_rules
        from src.eval.sec_reference.fsds import load_tables

        _ENV.append(SimpleNamespace(
            tables=load_tables(FSDS_DIRS),
            live=load_statement_rules(LIVE_RULES),
            accepted=load_statement_rules(ACCEPTED_R2_1),
            kd=load_statement_rules(KD_RULES),
            debt=load_statement_rules(DEBT_RULES),
        ))
    return _ENV[0]


@pytest.fixture(scope="session")
def kit() -> SimpleNamespace:
    """Paths, constants and helpers; verifies every fixture file's SHA-256 first."""
    prov = json.loads((FIXTURES / "provenance.json").read_text(encoding="utf-8"))
    for rel, digest in prov["files"].items():
        assert sha256(FIXTURES / rel) == digest, f"fixture {rel} differs from its provenance"
    return SimpleNamespace(
        loaded=loaded, sha=sha256, table_hashes=table_hashes, labels=labels, counts=counts,
        live_rules=LIVE_RULES, accepted_r2_1=ACCEPTED_R2_1, accepted_strip=ACCEPTED_STRIP,
        record=RECORD, r21=R21, synth=SYNTH, oracle=R21 / "oracle.yaml",
        judge3=JUDGE3_SHA256, accepted_r2_1_sha=ACCEPTED_R2_1_SHA256,
        oracle_sha=ORACLE_R2_1_SHA256, unjudged=UNJUDGED, outcomes=OUTCOMES, TSLA=TSLA,
        provenance=prov,
    )
