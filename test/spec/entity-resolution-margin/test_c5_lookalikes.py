"""C5: lookalikes and companies outside the corpus never resolve to a corpus filer
(docs/specs/entity-resolution-margin.md)."""

from __future__ import annotations

from collections import defaultdict

import pytest
from erm_data import CORPUS, CORPUS_CIKS, OTHER_SEC_ROWS, SEC_CIK, SEC_ROWS, SEC_UNIVERSE, cik_of
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.entity_resolution import company_name_key, resolve_company

# Real lookalikes by SEC ticker; each names a company outside the corpus.
HARD = ["MCHP", "STZ", "TPL", "CCEP", "COKE", "KOF", "AMAT", "ABBV", "JNJ", "UNB", "UELMO",
        "AMR", "MTTAF", "APLE", "RIOT", "GJR", "GJO", "CSTAF", "MIND", "TXRH", "JCI"]

# COKE (Coca-Cola Consolidated) is the one ticker that equals a committed alias ("Coke" ->
# The Coca-Cola Company); the alias wins by R3's order, a known edge the spec leaves open.
TICKER_EXCLUDED = {"COKE"}


def _not_a_corpus_filer(m) -> None:
    assert m.status in ("ambiguous", "unresolved")
    assert m.cik is None
    assert m.ticker is None


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("ticker", HARD)
def test_named_lookalikes_do_not_resolve(ticker, committed_aliases):
    # oracle: SEC company_tickers.json — the title belongs to a CIK outside the corpus
    row = next(r for r in SEC_ROWS if r["ticker"] == ticker)
    assert cik_of(row) not in CORPUS_CIKS
    _not_a_corpus_filer(resolve_company(row["title"], CORPUS, committed_aliases))


@pytest.mark.criterion("C5")
def test_every_other_sec_title_and_ticker_stays_unresolved(committed_aliases):
    # oracle: SEC company_tickers.json — every named/near row's CIK is outside the corpus
    # (the near rows are every SEC name scoring >= 0.30, i.e. those today's rule can take)
    wrong = []
    for row in OTHER_SEC_ROWS:
        assert cik_of(row) not in CORPUS_CIKS
        mentions = [row["title"]]
        if row["ticker"] not in TICKER_EXCLUDED:
            mentions.append(row["ticker"])
        for mention in mentions:
            m = resolve_company(mention, CORPUS, committed_aliases)
            if m.cik is not None or m.status not in ("ambiguous", "unresolved"):
                wrong.append((mention, m.status, m.cik))
    assert len(OTHER_SEC_ROWS) > 400
    assert wrong == []


@pytest.mark.criterion("C5")
def test_lookalikes_name_the_filer_they_resemble(committed_aliases):
    # oracle: SEC company_tickers.json for the CIKs; C2 — "microchip technology" scores
    # 16/23 against Micron's key and "constellation brands" shares "constellation" with
    # Constellation Energy's key (14 of 28 trigrams = 0.5 >= 0.30), so each is ambiguous
    # and names the filer it resembles, for the pipeline to ask about
    micro = resolve_company("Microchip Technology", CORPUS, committed_aliases)
    assert (micro.status, micro.cik) == ("ambiguous", None)
    assert micro.candidates[0] == SEC_CIK["MU"]
    assert micro.score == pytest.approx(16 / 23, abs=1e-9)  # exact ratio, float rounding

    brands = resolve_company("Constellation Brands", CORPUS, committed_aliases)
    assert (brands.status, brands.cik) == ("ambiguous", None)
    assert brands.candidates == (SEC_CIK["CEG"],)


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("mention", ["J&J", "Johnson & Johnson", "Applied Materials",
                                     "AbbVie", "Constellation Brands", "Texas Pacific Land",
                                     "Coca-Cola Europacific Partners", "Union Bankshares",
                                     "Meta Materials", "Alpha Metallurgical Resources"])
def test_common_spellings_of_non_corpus_companies_do_not_resolve(mention, committed_aliases):
    # oracle: SEC company_tickers.json — JNJ (200406), AMAT (6951), ABBV (1551152), STZ
    # (16918), TPL (1811074), CCEP (1650107), UNB (706863), AMR (1704715) are not corpus
    # CIKs; Meta Materials (MMAT) is no longer listed at all
    _not_a_corpus_filer(resolve_company(mention, CORPUS, committed_aliases))


# ---------------------------------------------------------------- metamorphic
def _ciks_by_key() -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for cik, _ticker, title in SEC_UNIVERSE:
        out[company_name_key(title)].add(cik)
    return out


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
def test_with_the_whole_excerpt_as_universe_every_title_resolves_to_itself():
    # oracle: SEC company_tickers.json — each row's own CIK. Growing the universe from the
    # corpus to every excerpted company turns "Constellation Brands" from ambiguous into
    # STZ, and leaves every corpus title on its own CIK; a key shared by several CIKs is
    # ambiguous among exactly those CIKs (R3 tier 3)
    by_key = _ciks_by_key()
    wrong = []
    for cik, _ticker, title in SEC_UNIVERSE:
        m = resolve_company(title, SEC_UNIVERSE, {})
        owners = by_key[company_name_key(title)]
        if len(owners) == 1:
            ok = m.status == "exact_name" and m.cik == cik
        else:
            ok = m.status == "ambiguous" and m.cik is None and set(m.candidates) == owners
        if not ok:
            wrong.append((title, m.status, m.cik))
    assert wrong == []
    assert resolve_company("Constellation Brands", SEC_UNIVERSE, {}).cik == SEC_CIK["STZ"]


@st.composite
def respelled_other(draw):
    row = draw(st.sampled_from(OTHER_SEC_ROWS))
    title = row["title"]
    case = draw(st.sampled_from([str.lower, str.upper, str.title, lambda s: s]))
    words = case(title).split(" ")
    gaps = [" " * draw(st.integers(min_value=1, max_value=3)) for _ in words[1:]]
    body = words[0] + "".join(g + w for g, w in zip(gaps, words[1:], strict=True))
    left = draw(st.sampled_from(["", " ", '"', "("]))
    right = draw(st.sampled_from(["", " ", '"', ")", "?"]))
    return f"{left}{body}{right}", row


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(case=respelled_other())
def test_respelling_a_non_corpus_name_never_makes_it_resolve(case, committed_aliases):
    # oracle: SEC company_tickers.json — the row's CIK is outside the corpus; case,
    # whitespace and surrounding punctuation never change a key (R1), so never the outcome
    spelled, row = case
    assert cik_of(row) not in CORPUS_CIKS
    _not_a_corpus_filer(resolve_company(spelled, CORPUS, committed_aliases))
