"""C2: which candidate owns a rank expression (docs/specs/peer-rank-grading.md, req. 1).

The real texts are dev-split xbrlrag_full answers from the stored runs eval-eefe3508621a and
eval-5e602d782e6f, shortened. Their first rank for the subject was checked by hand against
the case key (WMT 2, MSFT 20, META 3, UNP 17, KO 20).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.v2 import stated_ranks

LEX = {
    "AAPL": ["Apple", "Apple Inc."], "AMD": ["Advanced Micro Devices"],
    "AMZN": ["Amazon", "Amazon.com, Inc."], "AVGO": ["Broadcom", "Broadcom Inc."],
    "COST": ["Costco"], "GOOGL": ["Alphabet"], "HD": ["Home Depot"], "INTC": ["Intel"],
    "KO": ["Coca-Cola"], "META": ["Meta"], "MRK": ["Merck"], "MSFT": ["Microsoft"],
    "MU": ["Micron"], "NFLX": ["Netflix"], "TXN": ["Texas Instruments"],
    "UNP": ["Union Pacific"], "WMT": ["Walmart"],
}


@pytest.mark.criterion("C2")
def test_nearest_mention_owns_the_rank():
    # oracle: owner rule 3 — "13th" is 8 characters after AMD and 15 before Broadcom
    text = "AMD ranked 13th just ahead of Broadcom."
    assert stated_ranks(text, "AMD", LEX) == [13]
    assert stated_ranks(text, "AVGO", LEX) == []


@pytest.mark.criterion("C2")
def test_forward_binding_ordinal():
    # oracle: owner rule 1 — "12th-ranked" and "13th-ranked" bind to the mention after them,
    # even though MRK ends nearer to "13th" than AMD starts
    text = ("Microsoft's value is, however, below those of the 12th-ranked (MRK) and "
            "13th-ranked (AMD) companies.")
    assert stated_ranks(text, "MSFT", LEX) == []
    assert stated_ranks(text, "MRK", LEX) == [12]
    assert stated_ranks(text, "AMD", LEX) == [13]


@pytest.mark.criterion("C2")
def test_parenthetical_binds_back():
    # oracle: owner rule 2 — "the 1st" is inside DEVB's parenthesis; DEVC (gap 12) is nearer
    # than DEVB (gap 15), but the parenthesis gives it to DEVB
    names = {"DEVA": [], "DEVB": [], "DEVC": []}
    text = "DEVA ranks 2nd, just below DEVB (420 USD, the 1st) and above DEVC."
    assert stated_ranks(text, "DEVB", names) == [1]
    assert stated_ranks(text, "DEVC", names) == []
    assert stated_ranks(text, "DEVA", names) == [2]


@pytest.mark.criterion("C2")
def test_carry_over_and_default():
    # oracle: owner rules 4 and 5 — "That ranks 20th" has no mention and follows a Microsoft
    # sentence; a text naming no candidate gives its rank to the subject
    carried = ("Microsoft's Inventory, Net for the fiscal year ended June 30, 2024 was "
               "1,246,000,000 USD. That ranks 20th of the 24 covered companies.")
    assert stated_ranks(carried, "MSFT", LEX) == [20]
    assert stated_ranks(carried, "AAPL", LEX) == []
    assert stated_ranks("Ranks 2nd of the 4 retailers.", "WMT", LEX) == [2]


@pytest.mark.criterion("C2")
def test_ticker_case_sensitive_names_case_insensitive():
    # oracle: "Mentions" — "meta" is the name Meta (case-insensitive), so META owns 3rd;
    # "aapl" is not the ticker AAPL, so its sentence has no mention and 5th carries over to
    # the last mention before it (Microsoft)
    assert stated_ranks("meta ranks 3rd.", "META", LEX) == [3]
    text = "Microsoft leads the group. aapl ranks 5th."
    assert stated_ranks(text, "MSFT", LEX) == [5]
    assert stated_ranks(text, "AAPL", LEX) == []


@pytest.mark.criterion("C2")
def test_overlapping_mentions_longest_wins():
    # oracle: "Mentions" — synthetic: one candidate's name ("Broadcom Inc.") contains
    # another's ("Broadcom"); the overlap starting first and longest wins, and the "." of
    # "Inc." inside the mention is no sentence end
    names = {"AVGO": ["Broadcom Inc."], "BRCM": ["Broadcom"]}
    text = "Broadcom Inc. ranked 14th of 14."
    assert stated_ranks(text, "AVGO", names) == [14]
    assert stated_ranks(text, "BRCM", names) == []


WMT_SUMMARY = (
    "Walmart's accounts payable for fiscal 2023 was 53742000000.0 USD, which ranks 2nd of "
    "the 4 retailers in the screen. The screen ranks Amazon first, Walmart second, Costco "
    "third and Home Depot fourth.")
MSFT_SUMMARY = (
    "Microsoft's Inventory, Net for the fiscal year ended June 30, 2024 was 1,246,000,000 "
    "USD. That ranks 20th of the 24 covered companies with a ranked FY2024 inventory value. "
    "Microsoft's value is, however, below those of the 12th-ranked (MRK) and 13th-ranked "
    "(AMD) companies, which are the two middle positions of the 24 ranked.")
META_SUMMARY = (
    "META's fiscal year 2022 capital expenditures were 31186000000.0 USD, which ranks 3rd "
    "among the 14 covered companies with a tagged and ranked Capex figure. Only AMZN "
    "(63645000000.0 USD) and GOOGL (31485000000.0 USD) were higher.")
UNP_SUMMARY = (
    "UNP ranks 17th of the 32 ranked covered companies on comprehensive income for fiscal "
    "year 2023, at 6,347,000,000 USD (period ended 2023-12-31). AAPL ranked first at "
    "96,652,000,000 USD, and MU ranked last at -5,585,000,000 USD.\n"
    "TXN ranked immediately above UNP (rank 16) with comprehensive income of "
    "6,316,000,000 USD.")
KO_SUMMARY = (
    "Coca-Cola (KO) reported revenue of 47,941,000,000 USD for fiscal year 2025. In the "
    "cross-filer revenue screen for FY2025, KO ranks 20th among the covered companies, "
    "sitting just below Intel (52,853,000,000 USD, rank 19) and just above Netflix "
    "(45,183,036,000 USD, rank 21).")


@pytest.mark.criterion("C2")
def test_real_answers_first_rank_is_the_keys():
    # oracle: the dev keys (WMT 2, MSFT 20, META 3, UNP 17, KO 20), read by hand from each
    # text: the first rank owned by the subject
    assert stated_ranks(WMT_SUMMARY, "WMT", LEX) == [2, 2]
    assert stated_ranks(WMT_SUMMARY, "AMZN", LEX) == [1]
    assert stated_ranks(WMT_SUMMARY, "COST", LEX) == [3]
    assert stated_ranks(WMT_SUMMARY, "HD", LEX) == [4]
    assert stated_ranks(MSFT_SUMMARY, "MSFT", LEX) == [20]
    assert stated_ranks(META_SUMMARY, "META", LEX) == [3]
    assert stated_ranks(UNP_SUMMARY, "UNP", LEX)[0] == 17
    assert stated_ranks(UNP_SUMMARY, "AAPL", LEX) == [1]
    assert stated_ranks(KO_SUMMARY, "KO", LEX) == [20]
    assert stated_ranks(KO_SUMMARY, "INTC", LEX) == [19]
    assert stated_ranks(KO_SUMMARY, "NFLX", LEX) == [21]


POOL = ["DEVA", "DEVB", "DEVC", "DEVD", "DEVE", "DEVF"]


@st.composite
def _claims(draw):
    tickers = draw(st.lists(st.sampled_from(POOL), min_size=1, max_size=6, unique=True))
    return [(t, draw(st.integers(min_value=1, max_value=40))) for t in tickers]


def _text(claims, rename=None):
    rename = rename or {}
    return " ".join(f"{rename.get(t, t)} ranks #{k} on revenue." for t, k in claims)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(claims=_claims(), data=st.data())
def test_sentence_order_never_moves_a_rank(claims, data):
    # oracle: owner rule 3 — each sentence names one entity, so each owns exactly its own k
    # wherever its sentence stands
    names = {t: [] for t in POOL}
    shuffled = data.draw(st.permutations(claims))
    for t, k in claims:
        assert stated_ranks(_text(claims), t, names) == [k]
        assert stated_ranks(_text(shuffled), t, names) == [k]


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(claims=_claims(), data=st.data())
def test_consistent_renaming_is_invariant(claims, data):
    # oracle: requirement 1 depends on mentions only — renaming every ticker (and its names)
    # through a bijection keeps every entity's ranks
    targets = data.draw(st.permutations(["QQA", "QQB", "QQC", "QQD", "QQE", "QQF"]))
    rename = dict(zip(POOL, targets, strict=True))
    names = {t: [f"{t.capitalize()} Holdings"] for t in POOL}
    renamed_names = {rename[t]: [f"{rename[t].capitalize()} Holdings"] for t in POOL}
    for t, k in claims:
        assert stated_ranks(_text(claims), t, names) == [k]
        assert stated_ranks(_text(claims, rename), rename[t], renamed_names) == [k]
