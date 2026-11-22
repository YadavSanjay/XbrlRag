"""Spec tests for identity-and-stand-in (docs/specs/identity-and-stand-in.md).

Pure: `select_entity_identity` on DEI cover facts as the populator passes them, and
`refuse_stand_in` on label lists (no DB).
"""

from __future__ import annotations

import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.sec_edgar.ingest.entity_identity import select_entity_identity
from src.retriever.xbrl.xbrl_factstore_lookup import refuse_stand_in

LE = "dei:LegalEntityAxis"
CLASS = "dei:EntityListingsClassOfStockAxis"
EXCH = "dei:SecurityExchangeAxis"
STANDARD = {"us-gaap", "srt", "dei", "exch", "country"}


def _f(name, value, **dims):
    return {"name": name, "value": value, "dims": dict(dims)}


def _sym(value, dims=None):
    return {"name": "TradingSymbol", "value": value, "dims": dict(dims or {})}


# Covers as filed (Notes; read 2026-10-17).
CEG = [
    _f("EntityCentralIndexKey", "0001868275"),
    {"name": "EntityCentralIndexKey", "value": "0001168165",
     "dims": {LE: "ceg:ConstellationEnergyGenerationLLCMember"}},
    _f("EntityRegistrantName", "CONSTELLATION ENERGY CORPORATION"),
    {"name": "EntityRegistrantName", "value": "CONSTELLATION ENERGY GENERATION, LLC",
     "dims": {LE: "ceg:ConstellationEnergyGenerationLLCMember"}},
    _sym("CEG"),
]
GOOGL = [
    _f("EntityCentralIndexKey", "0001652044"),
    _f("EntityRegistrantName", "Alphabet Inc."),
    _sym("GOOGL", {CLASS: "us-gaap:CommonClassAMember"}),
    _sym("GOOG", {CLASS: "goog:CapitalClassCMember"}),
    _sym("GOOGM", {CLASS: "goog:DepositarySharesSeriesAMandatoryConvertiblePreferredStockMember"}),
    _sym("GOOGN", {CLASS: "goog:DepositarySharesSeriesBMandatoryConvertiblePreferredStockMember"}),
]
ORCL = [_f("EntityRegistrantName", "Oracle Corporation"),
        _sym("ORCL", {CLASS: "us-gaap:CommonStockMember"})]
VST = [_f("EntityRegistrantName", "Vistra Corp."), _sym("VST", {EXCH: "exch:XNYS"})]
PRD_ONLY = [_f("EntityRegistrantName", "Oracle Corporation"),
            _sym("ORCL PRD", {CLASS: "us-gaap:CommonStockMember"})]


def _is_primary(dims):
    """Requirement 3.2: no dimension but an exchange, or a standard class member."""
    return all(axis == EXCH or member.split(":")[0] in STANDARD for axis, member in dims.items())


def _symbol_oracle(facts, existing):
    """Requirement 1 and 3 written out."""
    cands = {}
    for f in facts:
        if f["name"] != "TradingSymbol" or LE in f["dims"]:
            continue
        v = f["value"].strip()
        if not v or re.search(r"\s", v):
            continue
        cands[v] = cands.get(v, False) or _is_primary(f["dims"])
    if not cands:
        return None
    stored = (existing or {}).get("trading_symbol")
    if stored in cands:
        return stored
    return min(cands, key=lambda s: (not cands[s], len(s), s))


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_ceg_is_named_for_itself_not_its_co_registrant():
    # oracle: CEG 10-K FY2025 cover; Requirement 1-2 (the LLC facts carry dei:LegalEntityAxis)
    assert select_entity_identity(CEG, None) == {
        "company_name": "CONSTELLATION ENERGY CORPORATION", "trading_symbol": "CEG"}
    stored = {"company_name": "CONSTELLATION ENERGY GENERATION, LLC", "trading_symbol": "CEG"}
    assert select_entity_identity(CEG, stored)["company_name"] == "CONSTELLATION ENERGY CORPORATION"
    no_name = [f for f in CEG if not (f["name"] == "EntityRegistrantName" and not f["dims"])]
    assert select_entity_identity(no_name, stored)["company_name"] is None  # stored name kept


co_registrant_fact = st.builds(
    lambda name, value, member: {"name": name, "value": value, "dims": {LE: f"ceg:{member}Member"}},
    st.sampled_from(["EntityRegistrantName", "TradingSymbol", "EntityCentralIndexKey"]),
    st.text("ABCDEFGHIJKLMNOPQRSTUVWXYZ ,.", min_size=1, max_size=20),
    st.text("ABCDEFGHIJKLMNOPQRSTUVWXYZ", min_size=1, max_size=10),
)


@pytest.mark.criterion("C1")
@given(st.lists(co_registrant_fact, max_size=6), st.randoms(use_true_random=False))
def test_co_registrant_facts_never_change_the_result(extra, rnd):
    # oracle: Requirement 1 — facts on dei:LegalEntityAxis are ignored for every field
    facts = CEG + extra
    rnd.shuffle(facts)
    assert select_entity_identity(facts, None) == {
        "company_name": "CONSTELLATION ENERGY CORPORATION", "trading_symbol": "CEG"}


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
def test_primary_listing_wins_and_a_listed_stored_symbol_survives():
    # oracle: Requirement 3 on the Alphabet, Oracle and Vistra covers (Notes)
    assert select_entity_identity(GOOGL, None)["trading_symbol"] == "GOOGL"
    assert select_entity_identity(GOOGL, {"trading_symbol": "GOOGL"})["trading_symbol"] == "GOOGL"
    assert select_entity_identity(GOOGL, {"trading_symbol": "GOOG"})["trading_symbol"] == "GOOG"
    assert select_entity_identity(GOOGL, {"trading_symbol": "GOOGX"})["trading_symbol"] == "GOOGL"
    assert select_entity_identity(GOOGL, None)["company_name"] == "Alphabet Inc."
    assert select_entity_identity(ORCL, None) == {"company_name": "Oracle Corporation",
                                                  "trading_symbol": "ORCL"}
    assert select_entity_identity(VST, None) == {"company_name": "Vistra Corp.",
                                                 "trading_symbol": "VST"}
    assert select_entity_identity(PRD_ONLY, None) == {"company_name": "Oracle Corporation",
                                                      "trading_symbol": None}


symbol_fact = st.builds(
    lambda value, dims: _sym(value, dims),
    st.text("ABCDEFG", min_size=1, max_size=5),
    st.one_of(
        st.just({}),
        st.just({EXCH: "exch:XNAS"}),
        st.sampled_from(["us-gaap:CommonStockMember", "us-gaap:CommonClassAMember",
                         "zz:ClassCMember", "zz:PreferredMember"]).map(lambda m: {CLASS: m}),
        st.just({LE: "zz:SubsidiaryMember"}),
    ),
)


@pytest.mark.criterion("C2")
@given(st.lists(symbol_fact, min_size=1, max_size=8), st.text("ABCDEFG", min_size=1, max_size=5),
       st.randoms(use_true_random=False))
def test_symbol_choice_matches_the_rule_for_any_cover(facts, stored, rnd):
    # oracle: _symbol_oracle — Requirement 3 written out independently; order-free (Req. 4)
    existing = {"company_name": "N", "trading_symbol": stored}
    want = _symbol_oracle(facts, existing)
    shuffled = list(facts)
    rnd.shuffle(shuffled)
    assert select_entity_identity(shuffled, existing)["trading_symbol"] == want
    assert select_entity_identity(facts, None)["trading_symbol"] == _symbol_oracle(facts, None)
    assert select_entity_identity(GOOGL, None)["trading_symbol"] == "GOOGL"   # pinned


# --------------------------------------------------------------------------- C3
INTC_CATALOG = [f"{m} [Member]" for m in (
    "Altera", "Client Computing Group",
    "Client Computing Group, Datacenter And AI And Network And Edge", "Datacenter and AI",
    "Intel Foundry", "Mobileye", "Network and Edge", "Other Operating Segment")]
COMBINED = "Client Computing Group, Datacenter And AI And Network And Edge [Member]"


def _key(text):
    """member_label_key as docs/specs/segment-label-match.md Requirement 1 defines it."""
    words = re.findall(r"[a-z0-9]+", (text or "").lower().replace("[member]", " ")
                       .replace("&", " and "))
    if len(words) > 1 and words[-1] in {"segment", "segments", "member"}:
        words = words[:-1]
    return "".join(words)


def _refuse_oracle(hint, matched, catalog):
    """Requirement 5 written out."""
    k = _key(hint)
    return all(_key(m) != k for m in matched) and any(_key(c) == k for c in catalog)


@pytest.mark.criterion("C3")
def test_intel_combined_member_does_not_stand_in_for_network_and_edge():
    # oracle: INTC FY2025 catalog (Notes); Requirement 5-6 by hand
    assert refuse_stand_in("network and edge", [COMBINED], INTC_CATALOG) is True
    assert refuse_stand_in("Network and Edge", [COMBINED], INTC_CATALOG) is True
    assert refuse_stand_in("network and edge", [COMBINED, "Network and Edge [Member]"],
                           INTC_CATALOG) is False
    assert refuse_stand_in("license support", ["Cloud Services And License Support [Member]"],
                           ["Cloud Services And License Support [Member]",
                            "Hardware [Member]"]) is False
    assert refuse_stand_in("intel foundry", ["Intel Foundry [Member]"], INTC_CATALOG) is False


words = st.lists(st.sampled_from(["edge", "network", "and", "client", "ai", "segment"]),
                 min_size=1, max_size=4).map(" ".join)


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(words, st.lists(words, max_size=4), st.lists(words, max_size=5))
def test_refusal_follows_the_rule_and_an_exact_match_always_clears_it(hint, matched, catalog):
    # oracle: _refuse_oracle (Requirement 5 independently); adding the hint's own label clears it
    labels = [f"{w} [Member]" for w in matched]
    cat = [f"{w} [Member]" for w in catalog]
    assert refuse_stand_in(hint, labels, cat) is _refuse_oracle(hint, labels, cat)
    assert refuse_stand_in(hint, [*labels, f"{hint} [Member]"], cat) is False
    assert refuse_stand_in("network and edge", [COMBINED], INTC_CATALOG) is True   # pinned
