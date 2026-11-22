"""C10: witness blocks — a compound candidate that meets a key the statement witness blocks,
with no reviewed resolution covering it, is left out as ``witness_blocked``; the compound
build never raises for it and its blocks never reach a shared witness's report; resolutions
apply as in the main build.

Oracle: requirement 4 ("Witness blocks") and requirement 7 of docs/specs/compound-shape.md,
applied by hand to the conftest's companyfacts table and the synthetic FSDS fixture under
``witness/`` (see its README.txt): every DEVA..DEVD income statement presents Revenues,
Operating income and Net income on the key's own element with the table's values, except
DEVB's two 10-Ks, which present no net income line, so DEVB's FY2023 and FY2024 NetIncomeLoss
keys are ``note_unlisted`` (the note list is empty): blocked. The witness is a real
``KeyWitness``; the only fakes are the conftest's I/O seams (a companyfacts source that hands
out the same document per CIK, and ``FakeFactStore``).

The six ``leader_growth`` candidates (universe ``big``, n = 3, ranking year FY2024), by hand
(rankings FY2024: revenue DEVA 500 > DEVB 400 > DEVC 300; operating income DEVB 120 >
DEVA 100 > DEVC 75; net income DEVA 60 > DEVB 48 > DEVC 30 > DEVD 5):

    (A, B)                       meets a DEVB net income key?        unresolved
    (Revenue, OperatingIncome)   no (leader DEVA)                     kept, 10/90
    (Revenue, NetIncome)         no (leader DEVA)                     kept, 10/50
    (OperatingIncome, Revenue)   no (leader DEVB, growth of revenue)  kept, -20/420
    (OperatingIncome, NetIncome) yes: the leader DEVB's growth        witness_blocked
    (NetIncome, Revenue)         yes: DEVB is a ranking member        witness_blocked
    (NetIncome, OperatingIncome) yes: DEVB is a ranking member        witness_blocked
"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.keys.base import SecSource
from src.eval.keys.statement_witness import (
    WitnessBlocked,
    load_note_concepts,
    load_resolutions,
    load_statement_rules,
)
from src.eval.sec_reference.fsds import load_tables
from src.eval.suites.intents import load_lexicon, load_phrasebook
from src.eval.suites.v2_suite import GENERATED_SHAPES, build_compound_suites, build_v2_suites
from src.eval.suites.v2_witness import KeyWitness

HERE = Path(__file__).resolve().parent
W = HERE / "witness"
REV, OI, NI = "Revenue", "OperatingIncome", "NetIncome"
BLOCKING = ("disagree", "ambiguous", "rejected_line", "note_unlisted")

# (A, B) -> (leader, growth) of the kept candidates, unresolved; growth (B_Y - B_Y-1) / |B_Y-1|
# with both values from the leader's FY2024 10-K (S1; DEVA's FY2024 10-K reports FY2023
# operating income as 90).
KEPT = {
    (REV, OI): ("DEVA", (100 - 90) / 90),
    (REV, NI): ("DEVA", (60 - 50) / 50),
    (OI, REV): ("DEVB", (400 - 420) / 420),
}
BLOCKED = ((OI, NI), (NI, REV), (NI, OI))
PAIRS = (*KEPT, *BLOCKED)


class PinnedSec(SecSource):
    """Serves one fixed companyfacts document per CIK (the same object on every call)."""

    def __init__(self, docs: dict[str, dict]) -> None:
        self.docs = copy.deepcopy(docs)

    def company_facts(self, cik: str) -> dict:
        return self.docs[str(cik).zfill(10)]

    def frames(self, taxonomy: str, tag: str, unit: str, period: str) -> dict:
        raise AssertionError("the build must not read frames")


def _witness(resolutions: str = "none") -> KeyWitness:
    """A real witness over the fixture FSDS quarter, rules, notes and a resolution list."""
    return KeyWitness(load_tables([W / "fsds" / "2025q1"]),
                      load_statement_rules(W / "statement_lines.yaml"),
                      load_note_concepts(W / "note_concepts.yaml"),
                      load_resolutions(W / f"key_resolutions_{resolutions}.yaml"))


def _env(kit):
    docs = kit.companyfacts_docs()
    return PinnedSec(docs), kit.store(docs)


def _compound(kit, witness, env, q_lg: int = 100, inv=None):
    sec, store = env
    return build_compound_suites(
        kit.inventory() if inv is None else inv, kit.universes(), sec, store,
        kit.compound_phrasebook(), kit.lexicon(), quotas={kit.RBR: 0, kit.LG: q_lg},
        per_intent=1, share=0.0, salt=kit.SALT, witness=witness)


def _main(kit, witness, env, inv):
    sec, store = env
    return build_v2_suites(inv, kit.universes(), sec, store,
                           load_phrasebook(HERE / "phrasebook_main.yaml"),
                           load_lexicon(HERE / "lexicon.yaml"),
                           quotas={s: 100 for s in GENERATED_SHAPES}, per_intent=1,
                           share=0.0, salt=kit.SALT, witness=witness)


def _inventory_without_devb_net_income(kit) -> dict:
    """The inventory with DEVB's net income removed: a main build with no block of its own."""
    inv = kit.inventory()
    for year in (2023, 2024):
        del inv["DEVB"]["years"][year][NI]
    return inv


def _kept_pairs(kit, result) -> set:
    ids = {kit.oracle_id(o) for o in result["intents"]}
    return {p for p in PAIRS if kit.oracle_id(kit.lg(2024, *p)) in ids}


def _dependent(kit, result, pair):
    rec = kit.case_of(result, kit.lg(2024, *pair))
    assert [p["part"] for p in rec["parts"]] == ["ranking", "dependent"]
    return rec, rec["parts"][1]


@pytest.mark.criterion("C10")
def test_blocked_candidates_are_left_out_and_the_others_kept(kit):
    # oracle: the module docstring's table — 3 of the 6 leader_growth candidates meet a
    # blocked DEVB net income key and are dropped as witness_blocked; the other 3 keep their
    # hand-computed growth; no rank_by_ratio intent is keyed (quota 0; 24 candidates as C8)
    result = _compound(kit, _witness(), _env(kit))
    intents = result["manifest"]["intents"]
    assert intents["selected"] == {kit.RBR: 0, kit.LG: 3}
    assert intents["candidates"] == {kit.RBR: 24, kit.LG: 6}
    assert intents["dropped"] == {"witness_blocked": 3}
    assert intents["dropped_by_shape"] == {kit.LG: {"witness_blocked": 3}}
    assert _kept_pairs(kit, result) == set(KEPT)
    for pair, (leader, growth) in KEPT.items():
        rec, dep = _dependent(kit, result, pair)
        assert rec["expected_rows"][0]["ticker"] == leader
        assert dep["ticker"] == leader
        assert dep["expected_value"] == pytest.approx(growth, rel=1e-12)  # float rounding only
    assert result["dev"]["n_cases"] == 3 and result["validation"]["n_cases"] == 0


@pytest.mark.criterion("C10")
def test_no_raise_and_no_blocking_witness_record(kit):
    # oracle: requirement 7 — the build returns (no WitnessBlocked); requirement 4 — a blocked
    # key has no witness record, so key_independence counts no blocking status, and the
    # witness reports nothing afterwards
    witness = _witness()
    result = _compound(kit, witness, _env(kit))
    by_status = result["manifest"]["key_independence"]["by_status"]
    assert {s: by_status[s] for s in BLOCKING} == dict.fromkeys(BLOCKING, 0)
    assert result["manifest"]["key_resolutions"]["n"] == 0
    witness.raise_if_blocked()


def _ordered(kit, pairs):
    # one universe, so order_candidates is the order of sha1(intent id)
    return sorted(pairs, key=lambda p: hashlib.sha1(
        kit.oracle_id(kit.lg(2024, *p)).encode("utf-8")).hexdigest())


@pytest.mark.criterion("C10")
@settings(max_examples=8)
@given(quota=st.integers(min_value=0, max_value=7))
def test_quota_is_met_in_candidate_order_past_blocked_candidates(kit, quota):
    # oracle: requirement 3 with requirement 4 — candidates are keyed in order until the quota
    # of kept intents is met; each blocked candidate met on the way counts as witness_blocked
    # and the next candidate is tried
    result = _compound(kit, _witness(), _env(kit), q_lg=quota)
    kept, blocked = [], 0
    for pair in _ordered(kit, PAIRS):
        if len(kept) >= quota:
            break
        if pair in KEPT:
            kept.append(pair)
        else:
            blocked += 1
    assert result["manifest"]["intents"]["selected"][kit.LG] == len(kept) == min(quota, 3)
    assert kit.dropped(result, kit.LG) == ({"witness_blocked": blocked} if blocked else {})
    assert _kept_pairs(kit, result) == set(kept)


@pytest.mark.criterion("C10")
@pytest.mark.metamorphic
def test_shared_witness_main_build_without_blocks_passes_before_and_after(kit):
    # oracle: requirement 4 — with one witness shared, a main build with no block of its own
    # passes before the compound build, the compound build leaves its 3 candidates out without
    # raising, the witness then reports nothing, and the main build run again passes with the
    # same suites and intents
    witness, env = _witness(), _env(kit)
    inv = _inventory_without_devb_net_income(kit)
    first = _main(kit, witness, env, inv)
    compound = _compound(kit, witness, env)
    assert compound["manifest"]["intents"]["dropped"] == {"witness_blocked": 3}
    assert _kept_pairs(kit, compound) == set(KEPT)
    witness.raise_if_blocked()
    again = _main(kit, witness, env, inv)
    for key in ("dev", "validation", "intents"):
        assert kit.canonical(again[key]) == kit.canonical(first[key])


@pytest.mark.criterion("C10")
@pytest.mark.metamorphic
def test_main_build_after_the_compound_build_raises_as_on_a_fresh_witness(kit):
    # oracle: requirement 4 — a key the compound build met blocked still blocks the main build
    # exactly as on a fresh witness: the full-inventory main build (which keys DEVB's net
    # income) raises the same WitnessBlocked with or without the compound build before it
    with pytest.raises(WitnessBlocked) as alone:
        _main(kit, _witness(), _env(kit), kit.inventory())
    witness, env = _witness(), _env(kit)
    compound = _compound(kit, witness, env)
    assert compound["manifest"]["intents"]["dropped"] == {"witness_blocked": 3}
    with pytest.raises(WitnessBlocked) as after:
        _main(kit, witness, env, kit.inventory())
    assert str(after.value) == str(alone.value)


@pytest.mark.criterion("C10")
def test_a_key_resolved_key_is_used(kit):
    # oracle: requirement 4 — DEVB's net income keys resolved "key" (FY2023 40M, FY2024 48M)
    # are used: all 6 candidates kept; (OI, NI): leader DEVB, (48 - 40) / 40; (NI, Rev):
    # ranking DEVA 60, DEVB 48, DEVC 30, leader DEVA, (500 - 450) / 450
    result = _compound(kit, _witness("key"), _env(kit))
    assert result["manifest"]["intents"]["selected"][kit.LG] == 6
    assert kit.dropped(result, kit.LG) == {}
    _rec, dep = _dependent(kit, result, (OI, NI))
    assert dep["ticker"] == "DEVB"
    assert dep["expected_value"] == pytest.approx((48 - 40) / 40, rel=1e-12)  # float rounding
    rec, dep = _dependent(kit, result, (NI, REV))
    assert [(r["ticker"], r["value"]) for r in rec["expected_rows"]] == [
        ("DEVA", 60 * kit.M), ("DEVB", 48 * kit.M), ("DEVC", 30 * kit.M)]
    assert dep["ticker"] == "DEVA"
    assert dep["expected_value"] == pytest.approx((500 - 450) / 450, rel=1e-12)  # same
    assert result["manifest"]["key_resolutions"]["n"] == 2


@pytest.mark.criterion("C10")
def test_a_key_resolved_drop_follows_the_main_build(kit):
    # oracle: requirement 4 — resolved "drop" is not a block: DEVB's growth drops (OI, NI) as
    # dependent:resolved_drop; the net income ranking excludes DEVB as resolved_drop and
    # ranks DEVA 60, DEVC 30, DEVD 5, so (NI, Rev) and (NI, OI) are kept with leader DEVA
    result = _compound(kit, _witness("drop"), _env(kit))
    assert result["manifest"]["intents"]["selected"][kit.LG] == 5
    assert kit.dropped(result, kit.LG) == {"dependent:resolved_drop": 1}
    assert _kept_pairs(kit, result) == set(PAIRS) - {(OI, NI)}
    for b, growth in ((REV, (500 - 450) / 450), (OI, (100 - 90) / 90)):
        rec, dep = _dependent(kit, result, (NI, b))
        assert [(r["ticker"], r["value"]) for r in rec["expected_rows"]] == [
            ("DEVA", 60 * kit.M), ("DEVC", 30 * kit.M), ("DEVD", 5 * kit.M)]
        # grading-dont-care-and-ties requirement 2: a resolved_drop exclusion may carry the
        # reviewed resolution's value (the fixture resolution's 48M)
        devb = [e for e in rec["parts"][0]["disclosures"]["excluded"]
                if e.get("ticker") == "DEVB" and e.get("reason") == "resolved_drop"]
        assert len(devb) == 1
        assert set(devb[0]) <= {"ticker", "reason", "value"}
        assert devb[0].get("value", 48 * kit.M) == pytest.approx(48 * kit.M, rel=1e-12)
        assert dep["ticker"] == "DEVA"
        assert dep["expected_value"] == pytest.approx(growth, rel=1e-12)  # float rounding only
