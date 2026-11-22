"""C4: ``resolve_case`` for lookup and dimensional cases.

Oracle: requirement 7 of docs/specs/set-b-tooling.md. One hand-built filer-year
(``_world``) gives each status; the expected classes are read off it with requirement 6.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.set_b.core import resolve_case

M = 1e6
QUESTION = "Kjq distinctive lookup wording 3309?"


def _world(kit):
    """MSFT FY2025 candidates (synthetic)."""
    c = kit.cand
    return [
        c(11, "Revenues", 245122 * M, family="Revenue"),                  # unique flat amount
        c(21, "OperatingIncomeLoss", 88523 * M, family="OperatingIncome"),  # 88,523 twice:
        c(22, "NetIncomeLoss", 88523 * M, family="NetIncome"),            # -> ambiguous
        c(31, "Revenues", 105362 * M, member="Intelligent Cloud"),        # 105,362: dimensional
        c(32, "Revenues", 105362 * M, member="More Personal Computing"),  # two members, and
        c(33, "GrossProfit", 105362 * M),                                 # a flat decoy
        c(34, "Revenues", 105362 * M, member="Intelligent Cloud", unit="TWD"),
    ]


def _store(kit, cands=None):
    return kit.FakeStore({("MSFT", 2025): _world(kit) if cands is None else cands})


@pytest.mark.criterion("C4")
def test_lookup_verified(kit):
    # oracle: requirement 7 — "245,122" millions USD matches only fact 11: one class ->
    # verified, classes [[11]]; facts_for called once for (MSFT, 2025)
    store = _store(kit)
    got = resolve_case(kit.lookup("k1", expected="245,122", question=QUESTION), store)
    assert got["id"] == "k1"
    assert got["status"] == "verified"
    assert got["classes"] == [[11]]
    assert store.calls == [("MSFT", 2025)]


@pytest.mark.criterion("C4")
def test_lookup_ambiguous_with_hint(kit):
    # oracle: requirement 7 — "88,523" matches facts 21 and 22 (two concepts): two classes
    # -> ambiguous; the hint names each class's local name, never the question or amount
    store = _store(kit)
    got = resolve_case(kit.lookup("k2", expected="88,523", question=QUESTION), store)
    assert got["status"] == "ambiguous"
    assert got["classes"] == [[21], [22]]
    hint = got["candidates_hint"]
    assert len(hint) == 2
    text = str(hint)
    assert "OperatingIncomeLoss" in text
    assert "NetIncomeLoss" in text
    assert QUESTION not in text
    assert "Kjq" not in text
    assert "88523" not in text
    assert "88,523" not in text
    assert store.calls == [("MSFT", 2025)]


@pytest.mark.criterion("C4")
def test_concept_hint_disambiguates(kit):
    # oracle: requirement 7 — the case's concept hint goes to find_classes: "NetIncome"
    # equals fact 22's family only -> verified [[22]]
    got = resolve_case(kit.lookup("k3", expected="88,523", concept="NetIncome"), _store(kit))
    assert got["status"] == "verified"
    assert got["classes"] == [[22]]


@pytest.mark.criterion("C4")
def test_lookup_not_found(kit):
    # oracle: requirement 7 — "245,123" is 1e6 from fact 11 (half_unit 0.5e6): no class
    store = _store(kit)
    got = resolve_case(kit.lookup("k4", expected="245,123"), store)
    assert got["id"] == "k4"
    assert got["status"] == "not_found"
    assert got["classes"] == []
    assert store.calls == [("MSFT", 2025)]


@pytest.mark.criterion("C4")
def test_lookup_uses_only_flat_candidates(kit):
    # oracle: requirement 7 — a lookup has dimensional=False: "105,362" matches only the
    # flat GrossProfit fact 33, not the dimensional 31/32
    got = resolve_case(kit.lookup("k5", expected="105,362"), _store(kit))
    assert got["status"] == "verified"
    assert got["classes"] == [[33]]


@pytest.mark.criterion("C4")
def test_dimensional_uses_dimensional_candidates_filtered_by_member(kit):
    # oracle: requirement 7 — a dimensional case has dimensional=True and passes `member`:
    # "cloud" keeps fact 31 (Intelligent Cloud, USD); 32 is another member, 33 is flat,
    # 34 is TWD
    store = _store(kit)
    got = resolve_case(kit.dimensional("k6", expected="105,362", member="cloud"), store)
    assert got["status"] == "verified"
    assert got["classes"] == [[31]]
    assert store.calls == [("MSFT", 2025)]


@pytest.mark.criterion("C4")
def test_dimensional_member_that_matches_two_is_ambiguous(kit):
    # oracle: requirement 7 — member "o" is a substring of both member labels: two classes
    got = resolve_case(kit.dimensional("k7", expected="105,362", member="o"), _store(kit))
    assert got["status"] == "ambiguous"
    assert got["classes"] == [[31], [32]]
    text = str(got["candidates_hint"])
    assert "Intelligent Cloud" in text
    assert "More Personal Computing" in text


@pytest.mark.criterion("C4")
def test_reads_the_cases_ticker_and_year(kit):
    # oracle: requirement 7 — facts_for(ticker, fy): NVDA FY2024 has its own fact 41; the
    # MSFT world is not read
    c = kit.cand
    store = kit.FakeStore({("MSFT", 2025): _world(kit),
                           ("NVDA", 2024): [c(41, "Revenues", 60922 * M, fy=2024)]})
    got = resolve_case(kit.lookup("k8", ticker="NVDA", fy=2024, expected="60,922"), store)
    assert got["status"] == "verified"
    assert got["classes"] == [[41]]
    assert store.calls == [("NVDA", 2024)]


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(data=st.data(), which=st.sampled_from(["k2", "k7", "k1"]))
def test_shuffling_candidates_keeps_the_result(kit, data, which):
    # oracle: requirement 7 — classes are sorted (requirement 6), so the candidate order
    # cannot change the result; the unshuffled result is pinned to the hand-derived status
    cases = {"k1": (kit.lookup("k1", expected="245,122"), "verified"),
             "k2": (kit.lookup("k2", expected="88,523"), "ambiguous"),
             "k7": (kit.dimensional("k7", expected="105,362", member="o"), "ambiguous")}
    case, status = cases[which]
    base = resolve_case(case, _store(kit))
    assert base["status"] == status
    shuffled = data.draw(st.permutations(_world(kit)))
    got = resolve_case(case, _store(kit, list(shuffled)))
    assert got == base
