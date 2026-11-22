"""Fixtures for the witness-rules-r2-1 spec tests.

Spec: docs/specs/witness-rules-r2-1.md. ``fixtures/oracle.yaml`` is the locked judging oracle
(r2's with three added elements); ``fixtures/taxonomy_excerpt.json`` holds the us-gaap 2025
standard label and definition of every oracle element, with the source files' SHA-256;
``fixtures/synthetic/`` is witness-rules-r2's constructed four-filing sample (byte copy),
every outcome derived by hand in that spec's Notes. Nothing is read outside this directory
except the live ``configs/eval/v2`` files, the accepted r2 rules copy and r2 oracle in
``test/spec/witness-rules-r2/fixtures/``, and key-definitions' locked Debt/DeferredRevenue
rules.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
SYNTH = FIXTURES / "synthetic"
ORACLE = FIXTURES / "oracle.yaml"
EXCERPT = FIXTURES / "taxonomy_excerpt.json"
REPO = Path(__file__).resolve().parents[3]
LIVE_RULES = REPO / "configs" / "eval" / "v2" / "statement_lines.yaml"
RECORD = REPO / "configs" / "eval" / "v2" / "statement_lines.acceptance_r2_1.json"
# The byte copy of the r2.1 rules the judging read ran on (written with the record).
ACCEPTED_RULES = REPO / "configs" / "eval" / "v2" / "statement_lines.accepted_r2_1.yaml"
R2_FIXTURES = REPO / "test" / "spec" / "witness-rules-r2" / "fixtures"
R2_RULES = R2_FIXTURES / "statement_lines_r2.yaml"
R2_ORACLE = R2_FIXTURES / "oracle.yaml"
# The accepted r2 rules: the rules_sha256 of configs/eval/v2/statement_lines.acceptance.json.
R2_RULES_SHA256 = "947ba5c6ac81628c96edcfe9661a1de86fd3dd314e0331947fab70fa67ee4a9b"
KD_RULES = REPO / "test" / "spec" / "key-definitions" / "fixtures" / "statement_lines_kd.yaml"
# debt-ifrs-elements' locked Debt/DeferredRevenue entries (Debt gains the IFRS elements).
DEBT_RULES = (REPO / "test" / "spec" / "debt-ifrs-elements" / "fixtures"
              / "statement_lines_debt.yaml")

# Families whose rules r2.1 does not touch (definitions owned by key-definitions).
UNTOUCHED = ("Debt", "DeferredRevenue")

OUTCOMES = ("hit", "none", "ambiguous", "wrong", "oracle_ambiguous", "no_oracle",
            "no_oracle_selected")


def sha256(path: Path) -> str:
    """SHA-256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def synth_hashes() -> dict[str, str]:
    """The synthetic sample's four table hashes."""
    return {t: sha256(SYNTH / f"{t}.tsv") for t in ("sub", "pre", "num", "tag")}


def excerpt() -> dict:
    """The committed us-gaap 2025 excerpt."""
    return json.loads(EXCERPT.read_text(encoding="utf-8"))


def labels() -> dict[str, str | None]:
    """Element local name -> standard label; None for an element us-gaap 2025 lacks."""
    ex = excerpt()
    out: dict[str, str | None] = {k.split(":", 1)[1]: v["label"]
                                  for k, v in ex["elements"].items()}
    out.update({k.split(":", 1)[1]: None for k in ex["absent"]})
    return out


def counts(**kw: int) -> dict[str, int]:
    """A full seven-outcome count map, zeros where not given."""
    return {o: kw.get(o, 0) for o in OUTCOMES}


def pairs(family_counts: dict[str, dict[str, int]]) -> list[tuple[str, str]]:
    """Expands family -> outcome counts into ``(family, outcome)`` pairs."""
    return [(f, o) for f, c in family_counts.items() for o, n in c.items() for _ in range(n)]


def score(families: dict[str, dict[str, int]]) -> dict:
    """A ``Score`` built by hand from family counts (witness-rules-r2 requirement 4)."""
    totals = {o: sum(c[o] for c in families.values()) for o in OUTCOMES}
    ev = totals["hit"] + totals["none"] + totals["ambiguous"] + totals["wrong"]
    dec = totals["hit"] + totals["wrong"]
    return {"families": families, "totals": totals, "evaluable": ev,
            "recall": totals["hit"] / ev if ev else None,
            "precision": totals["hit"] / dec if dec else None,
            "ambiguity": totals["ambiguous"] / ev if ev else None}


@pytest.fixture(scope="session")
def kit() -> SimpleNamespace:
    """Paths, helpers and the excerpt labels."""
    return SimpleNamespace(
        fixtures=FIXTURES, synth=SYNTH, oracle=ORACLE, excerpt_path=EXCERPT, repo=REPO,
        live_rules=LIVE_RULES, record=RECORD, accepted_rules=ACCEPTED_RULES,
        r2_rules=R2_RULES, r2_oracle=R2_ORACLE, r2_sha=R2_RULES_SHA256, kd_rules=KD_RULES,
        debt_rules=DEBT_RULES,
        untouched=UNTOUCHED, outcomes=OUTCOMES,
        sha=sha256, synth_hashes=synth_hashes, excerpt=excerpt, labels=labels, counts=counts,
        pairs=pairs, score=score)
