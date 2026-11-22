"""Fixtures for the witness-rules-r2 spec tests.

Spec: docs/specs/witness-rules-r2.md. ``fixtures/oracle.yaml`` is the locked judging oracle;
``fixtures/synthetic/`` is a constructed four-filing FSDS sample with synthetic rules and
oracle, every outcome derived by hand (spec Notes). Nothing is read outside this directory
except the live ``configs/eval/v2`` files and the retired key-statement-witness r1 copy the
spec names.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
SYNTH = FIXTURES / "synthetic"
ORACLE = FIXTURES / "oracle.yaml"
REPO = Path(__file__).resolve().parents[3]
LIVE_RULES = REPO / "configs" / "eval" / "v2" / "statement_lines.yaml"
RECORD = REPO / "configs" / "eval" / "v2" / "statement_lines.acceptance.json"
# Byte copy of the accepted r2 rules (the record's rules_sha256); the live file moved on to
# r2.1 (docs/specs/witness-rules-r2-1.md).
R2_RULES = FIXTURES / "statement_lines_r2.yaml"
R1_RULES = REPO / "test" / "spec" / "key-statement-witness" / "fixtures" / "statement_lines.yaml"
R1_SHA256 = "661bed5dc7d7d458e0402cb4d8e043f0979f474bfe4d4a113a625b0aa2eff295"

# Synthetic filings (spec Notes, "Synthetic sample").
S1 = "0000000001-24-000001"
S2 = "0000000002-24-000002"
S3 = "0000000003-24-000003"
S4 = "0000000004-24-000004"

OUTCOMES = ("hit", "none", "ambiguous", "wrong", "oracle_ambiguous", "no_oracle",
            "no_oracle_selected")


def sha256(path: Path) -> str:
    """SHA-256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def synth_hashes() -> dict[str, str]:
    """The synthetic sample's four table hashes."""
    return {t: sha256(SYNTH / f"{t}.tsv") for t in ("sub", "pre", "num", "tag")}


def counts(**kw: int) -> dict[str, int]:
    """A full seven-outcome count map, zeros where not given."""
    return {o: kw.get(o, 0) for o in OUTCOMES}


def pairs(family_counts: dict[str, dict[str, int]]) -> list[tuple[str, str]]:
    """Expands family -> outcome counts into ``(family, outcome)`` pairs."""
    return [(f, o) for f, c in family_counts.items() for o, n in c.items() for _ in range(n)]


def score(families: dict[str, dict[str, int]]) -> dict:
    """A ``Score`` built by hand from family counts (requirement 4), for acceptance tests."""
    totals = {o: sum(c[o] for c in families.values()) for o in OUTCOMES}
    ev = totals["hit"] + totals["none"] + totals["ambiguous"] + totals["wrong"]
    dec = totals["hit"] + totals["wrong"]
    return {"families": families, "totals": totals, "evaluable": ev,
            "recall": totals["hit"] / ev if ev else None,
            "precision": totals["hit"] / dec if dec else None,
            "ambiguity": totals["ambiguous"] / ev if ev else None}


@pytest.fixture(scope="session")
def kit() -> SimpleNamespace:
    """Paths, synthetic accessions and helpers."""
    return SimpleNamespace(
        fixtures=FIXTURES, synth=SYNTH, oracle=ORACLE, repo=REPO, live_rules=LIVE_RULES,
        record=RECORD, r2_rules=R2_RULES, r1_rules=R1_RULES, r1_sha=R1_SHA256, S1=S1, S2=S2,
        S3=S3, S4=S4, outcomes=OUTCOMES, sha=sha256, synth_hashes=synth_hashes, counts=counts,
        pairs=pairs, score=score)
