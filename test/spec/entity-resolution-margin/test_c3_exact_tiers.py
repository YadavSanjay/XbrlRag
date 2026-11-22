"""C3: exact tiers — ticker, alias, exact name (docs/specs/entity-resolution-margin.md, R3-R4)."""

from __future__ import annotations

import pytest
from erm_data import CORPUS, CORPUS_CIKS, CORPUS_SEC_ROWS, SEC_CIK, cik_of
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.entity_resolution import company_name_key, resolve_company

# Mention -> (SEC ticker whose CIK is expected, expected status). The CIK comes from SEC's
# company_tickers.json (the excerpt), never from our resolver or entity_master.
EXACT = {
    # tickers (R3 tier 1; matched case-insensitively after normalize_company_mention)
    "AAPL": ("AAPL", "exact_ticker"),
    "googl": ("GOOGL", "exact_ticker"),
    "Meta": ("META", "exact_ticker"),
    "TSM": ("TSM", "exact_ticker"),
    "UPS": ("UPS", "exact_ticker"),
    # committed aliases (R3 tier 2): existing ones and R4's additions
    "TSMC": ("TSM", "alias"),
    "Taiwan Semiconductor": ("TSM", "alias"),
    "Google": ("GOOGL", "alias"),
    "Facebook": ("META", "alias"),
    "P&G": ("PG", "alias"),
    "Coke": ("KO", "alias"),
    "TI": ("TXN", "alias"),
    "Lilly": ("LLY", "alias"),
    "Costco": ("COST", "alias"),
    "GOOG": ("GOOGL", "alias"),
    "Abbott": ("ABT", "alias"),
    "Amazon": ("AMZN", "alias"),
    "Cisco": ("CSCO", "alias"),
    "Lockheed": ("LMT", "alias"),
    "Micron": ("MU", "alias"),
    # names (R3 tier 3: key equality with the entity_master name)
    "Apple": ("AAPL", "exact_name"),
    "Alphabet": ("GOOGL", "exact_name"),
    "Microsoft": ("MSFT", "exact_name"),
    "Home Depot": ("HD", "exact_name"),
    "The Home Depot": ("HD", "exact_name"),
    "Procter & Gamble": ("PG", "exact_name"),
    "Procter and Gamble": ("PG", "exact_name"),
    "Coca-Cola": ("KO", "exact_name"),
    "The Coca-Cola Company": ("KO", "exact_name"),
    "McDonald's": ("MCD", "exact_name"),
    "McDonalds": ("MCD", "exact_name"),
    "Eli Lilly": ("LLY", "exact_name"),
    "Merck": ("MRK", "exact_name"),
    "Merck & Co": ("MRK", "exact_name"),
    "Union Pacific": ("UNP", "exact_name"),
    "United Parcel Service": ("UPS", "exact_name"),
    "Texas Instruments": ("TXN", "exact_name"),
    "Constellation Energy": ("CEG", "exact_name"),
    "Advanced Micro Devices": ("AMD", "exact_name"),
    "Lockheed Martin": ("LMT", "exact_name"),
    "Micron Technology": ("MU", "exact_name"),
    "Amazon.com": ("AMZN", "exact_name"),
    "Abbott Laboratories": ("ABT", "exact_name"),
    "Cisco Systems": ("CSCO", "exact_name"),
    "Costco Wholesale": ("COST", "exact_name"),
    "Oracle": ("ORCL", "exact_name"),
    "Nvidia": ("NVDA", "exact_name"),
    "Qualcomm": ("QCOM", "exact_name"),
    "Taiwan Semiconductor Manufacturing": ("TSM", "alias"),  # a committed alias too
    "Walmart": ("WMT", "exact_name"),
}


def _check_exact(m, ticker: str, status: str) -> None:
    assert m.status == status
    assert m.cik == SEC_CIK[ticker]
    assert m.score == pytest.approx(1.0, abs=1e-12)  # exact tiers score exactly 1.0
    assert m.candidates == ()


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("mention", sorted(EXACT))
def test_exact_mentions_resolve_to_secs_cik(mention, committed_aliases):
    # oracle: SEC company_tickers.json (ticker -> CIK) for the company each mention names;
    # the tier is R3's order (ticker, alias, exact name)
    ticker, status = EXACT[mention]
    _check_exact(resolve_company(mention, CORPUS, committed_aliases), ticker, status)


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("row", CORPUS_SEC_ROWS, ids=lambda r: f"{r['ticker']}:{r['title']}")
def test_every_sec_title_of_a_corpus_filer_resolves_to_its_cik(row, committed_aliases):
    # oracle: SEC company_tickers.json — the row's own CIK; SEC's spelling ("ELI LILLY & Co",
    # "AMAZON COM INC", "MCDONALDS CORP") keys to the same name as entity_master's (R1)
    m = resolve_company(row["title"], CORPUS, committed_aliases)
    assert m.status == "exact_name"
    assert m.cik == cik_of(row)
    assert m.score == pytest.approx(1.0, abs=1e-12)  # exact tiers score exactly 1.0
    assert m.candidates == ()


@pytest.mark.criterion("C3")
def test_two_ciks_sharing_a_key_or_a_ticker_are_ambiguous():
    # oracle: R3 tiers 1 and 3 — a ticker or key held by two CIKs names both, ascending,
    # and resolves to neither
    filers = [("0000000002", "ORX", "ORACLE INC"), ("0000000001", "ORY", "Oracle Corp"),
              ("0000000003", "DUP", "Alpha Widgets"), ("0000000004", "dup", "Beta Gadgets")]
    by_name = resolve_company("Oracle", filers, {})
    assert (by_name.status, by_name.cik) == ("ambiguous", None)
    assert by_name.candidates == ("0000000001", "0000000002")
    by_ticker = resolve_company("DUP", filers, {})
    assert (by_ticker.status, by_ticker.cik) == ("ambiguous", None)
    assert by_ticker.candidates == ("0000000003", "0000000004")


@pytest.mark.criterion("C3")
def test_an_alias_whose_cik_is_not_in_the_universe_is_unresolved(committed_aliases):
    # oracle: R3 tier 2 / docs/specs/company-aliases.md — an alias is authoritative: TSMC's
    # CIK (SEC: TSM 1046179) absent from the universe means unresolved, not a fuzzy guess
    tsm = SEC_CIK["TSM"]
    universe = [row for row in CORPUS if row[0] != tsm]
    m = resolve_company("TSMC", universe, committed_aliases)
    assert (m.status, m.cik, m.candidates) == ("unresolved", None, ())


# ---------------------------------------------------------------- properties
@st.composite
def respelled(draw, mention: str) -> str:
    """Case, inner whitespace runs and surrounding punctuation: none changes the company."""
    case = draw(st.sampled_from([str.lower, str.upper, lambda s: s]))
    words = case(mention).split(" ")
    gaps = [" " * draw(st.integers(min_value=1, max_value=3)) for _ in words[1:]]
    body = words[0] + "".join(g + w for g, w in zip(gaps, words[1:], strict=True))
    left = draw(st.sampled_from(["", " ", '"', "(", "  "]))
    right = draw(st.sampled_from(["", " ", '"', ")", ".", "  ", "?"]))
    return f"{left}{body}{right}"


@st.composite
def exact_case(draw):
    mention = draw(st.sampled_from(sorted(EXACT)))
    return draw(respelled(mention)), mention


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(case=exact_case(), rnd=st.randoms(use_true_random=False))
def test_exact_match_ignores_spelling_and_universe_order(case, rnd, committed_aliases):
    # oracle: SEC company_tickers.json for the expected CIK; R1/R3 — case, whitespace and
    # surrounding punctuation are normalized away, and the result never depends on the
    # order of filers
    spelled, mention = case
    ticker, status = EXACT[mention]
    universe = list(CORPUS)
    rnd.shuffle(universe)
    _check_exact(resolve_company(spelled, universe, committed_aliases), ticker, status)


LETTERS = "bcdfghjklmnpqrstvwxz"  # no vowels: a made-up name never spells a real one


@st.composite
def unrelated_company(draw):
    """A company that shares no CIK, ticker or key with any exact case or corpus filer."""
    words = draw(st.lists(st.text(alphabet=LETTERS, min_size=4, max_size=9), min_size=1,
                          max_size=3))
    name = " ".join(words) + draw(st.sampled_from(["", " Inc.", " Corp", " Holdings"]))
    cik = f"{draw(st.integers(min_value=5_000_000, max_value=9_999_999)):010d}"
    ticker = draw(st.text(alphabet="BCDFGHJKLMNPQRSTVWXZ", min_size=5, max_size=6))
    return (cik, ticker, name)


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(mention=st.sampled_from(sorted(EXACT)),
       extra=st.lists(unrelated_company(), min_size=1, max_size=5),
       rnd=st.randoms(use_true_random=False))
def test_adding_unrelated_companies_never_changes_an_exact_match(mention, extra, rnd,
                                                                committed_aliases):
    # oracle: SEC company_tickers.json for the expected CIK; R3 — tiers 1-3 depend only on
    # rows with the same ticker, alias CIK or key, and an unrelated company has none
    known_keys = {company_name_key(n) for _, _, n in CORPUS}
    extra = [e for e in extra if e[0] not in CORPUS_CIKS
             and company_name_key(e[2]) not in known_keys]
    universe = list(CORPUS) + extra
    rnd.shuffle(universe)
    ticker, status = EXACT[mention]
    _check_exact(resolve_company(mention, universe, committed_aliases), ticker, status)
