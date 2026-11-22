"""Spec tests C6-C8 for eval-intents: the phrasing renderer (docs/specs/eval-intents.md)."""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.suites.intents import Intent, Phrasing, load_lexicon, load_phrasebook, render


def _one(kind, ticker, year, metric, **extra):
    return dict(kind=kind, tickers=[ticker], fiscal_years=[year], metrics=[metric], **extra)


def _ph(template_id, company="ticker", metric="label", period="fiscal"):
    return dict(template_id=template_id, company_form=company, metric_form=metric,
                period_form=period)


def _screen(op, universe, year, metric, n=None):
    return dict(kind="screen", operation=op, universe=universe, n=n, fiscal_years=[year],
                metrics=[metric])


# (intent kwargs, phrasing kwargs, expected question), each expected string written by hand
# from the fixture lexicon and phrasebook and the spec's surface rules.
POINTS = [
    (_one("lookup", "AAPL", 2024, "revenue"),
     _ph("lookup-conv", "short", "synonym:0", "year_ended"),
     "What was Apple's net sales in the fiscal year ended September 28, 2024?"),
    (_one("lookup", "msft", 2009, "revenue"),
     _ph("lookup-terse", "ticker", "label", "fy_short"),
     "MSFT Revenues FY09"),
    (_one("lookup", "NVDA", 2024, "operating_income"),
     _ph("lookup-formal", "legal", "shorthand:1", "fiscal_year"),
     "Please state the op income reported by NVIDIA Corporation for fiscal year 2024."),
    (_one("dimensional", "NVDA", 2025, "revenue", dimension="data_center"),
     _ph("dimensional-conv", "short", "label", "fiscal"),
     "What was Nvidia's Revenues from the Data Center segment in fiscal 2025?"),
    (_one("breakdown", "AAPL", 2023, "revenue", dimension="segment"),
     _ph("breakdown-formal", "ticker", "synonym:1", "year_ended"),
     "Break down AAPL's total revenue by business segment for the fiscal year ended "
     "September 30, 2023."),
    (dict(kind="compute", operation="yoy_growth", tickers=["AAPL"], fiscal_years=[2024, 2023],
          metrics=["revenue"]),
     _ph("yoy-conv", "short", "label", "fy_short"),
     "How much did Apple's Revenues grow in FY24?"),
    (dict(kind="compute", operation="cagr", tickers=["AAPL"], fiscal_years=[2022, 2024],
          metrics=["revenue"]),
     _ph("cagr-formal", "legal", "shorthand:0", "year_ended"),
     "What was the compound annual growth rate of Apple Inc.'s top line from the fiscal year "
     "ended September 24, 2022 to the fiscal year ended September 28, 2024?"),
    (dict(kind="compute", operation="difference", tickers=["MSFT"], fiscal_years=[2024, 2020],
          metrics=["revenue"]),
     _ph("span-terse", "legal", "label", "fy_short"),
     "Microsoft Corporation Revenues, FY20 to FY24"),
    (dict(kind="trend", tickers=["MSFT"], fiscal_years=[2023, 2022, 2024], metrics=["revenue"]),
     _ph("span-terse", "ticker", "synonym:0", "fiscal"),
     "MSFT net sales, fiscal 2022 to fiscal 2024"),
    (dict(kind="compute", operation="ratio", tickers=["NVDA"], fiscal_years=[2024],
          metrics=["gross_profit", "revenue"]),
     _ph("ratio-formal", "short", "synonym:0", "fiscal_year"),
     "State Nvidia's gross margin dollars as a share of net sales for fiscal year 2024."),
    (_one("point_in_time", "AAPL", 2022, "revenue", filing_year=2024),
     _ph("pit-formal", "short", "label", "fy_short"),
     "As reported in its FY24 annual report, what was Apple's Revenues for FY22?"),
    (_screen("top", "big_tech", 2024, "revenue", n=3),
     _ph("screen-top-conv", "none", "label", "fiscal"),
     "Which 3 of big tech had the highest Revenues in fiscal 2024?"),
    (_screen("bottom", "covered", 2022, "rd_expense", n=5),
     _ph("screen-bottom-conv", "none", "label", "fiscal_year"),
     "Which 5 of the covered companies had the lowest Research and Development Expense in "
     "fiscal year 2022?"),
    (_screen("sum", "big_tech", 2024, "gross_profit"),
     _ph("screen-sum-terse", "none", "label", "fiscal"),
     "total Gross Profit of big tech, fiscal 2024"),
    (_screen("median", "covered", 2023, "operating_income"),
     _ph("screen-median-formal", "none", "synonym:0", "fiscal_year"),
     "What was the median operating profit across the covered companies for fiscal year 2023?"),
    (_one("peer_relative", "AAPL", 2024, "rd_expense", universe="covered"),
     _ph("peer-conv", "legal", "label", "fy_short"),
     "How does Apple Inc.'s Research and Development Expense in FY24 compare with the covered "
     "companies?"),
]


def _render(kit, intent_kw, phrasing_kw):
    lex = load_lexicon(kit.lexicon)
    pb = load_phrasebook(kit.phrasebook)
    return render(Intent(**intent_kw), Phrasing(**phrasing_kw), lex, pb)


# --------------------------------------------------------------------------- C6
@pytest.mark.criterion("C6")
def test_c6_render_points(kit):
    # oracle: hand-written expected questions (POINTS) from the fixtures and the surface rules.
    for intent_kw, phrasing_kw, want in POINTS:
        assert _render(kit, intent_kw, phrasing_kw) == want


@pytest.mark.criterion("C6")
@given(ticker=st.sampled_from(["AAPL", "MSFT", "NVDA"]),
       year=st.integers(min_value=2000, max_value=2039),
       metric=st.sampled_from(["revenue", "operating_income", "gross_profit", "rd_expense"]),
       cform=st.sampled_from(["ticker", "short", "legal"]),
       pform=st.sampled_from(["fy_short", "fiscal", "fiscal_year"]), data=st.data())
def test_c6_lookup_property(kit, ticker, year, metric, cform, pform, data):
    # oracle: the surface rules restated over the raw fixture YAML (kit.raw_lexicon).
    raw = kit.raw_lexicon()
    m = raw["metrics"][metric]
    mforms = (["label"] + [f"synonym:{i}" for i in range(len(m.get("synonyms", [])))]
              + [f"shorthand:{i}" for i in range(len(m.get("shorthand", [])))])
    mform = data.draw(st.sampled_from(mforms))
    company = ticker if cform == "ticker" else raw["companies"][ticker][cform]
    kind, _, idx = mform.partition(":")
    if kind == "label":
        surface = m["label"]
    else:
        surface = m["synonyms" if kind == "synonym" else "shorthand"][int(idx)]
    want = f"What was {company}'s {surface} in {kit.period_text(pform, year, ticker, raw)}?"
    got = _render(kit, _one("lookup", ticker, year, metric),
                  _ph("lookup-conv", cform, mform, pform))
    assert got == want


# --------------------------------------------------------------------------- C7
LOOKUP = _one("lookup", "AAPL", 2024, "revenue")
SCREEN = _screen("top", "big_tech", 2024, "revenue", n=3)
RATIO = dict(kind="compute", operation="ratio", tickers=["NVDA"], fiscal_years=[2024],
             metrics=["revenue", "gross_profit"])
FAILS = [
    (LOOKUP, _ph("no-such-template")),
    (LOOKUP, _ph("peer-conv")),                                    # shape not listed
    (LOOKUP, _ph("lookup-conv", company="nickname")),              # unknown form
    (LOOKUP, _ph("lookup-conv", metric="synonym")),                # no index
    (LOOKUP, _ph("lookup-conv", period="calendar")),
    (LOOKUP, _ph("lookup-conv", company="none")),                  # none with a ticker
    (SCREEN, _ph("screen-top-conv", company="ticker")),            # a form without a ticker
    (SCREEN, _ph("screen-top-conv", company="none", period="year_ended")),
    (LOOKUP, _ph("lookup-conv", metric="synonym:2")),              # out of range
    (LOOKUP, _ph("lookup-conv", metric="shorthand:1")),
    (LOOKUP, _ph("lookup-conv", metric="synonym:-1")),
    ({**LOOKUP, "metrics": ["rd_expense"]}, _ph("lookup-conv", metric="synonym:0")),
    ({**LOOKUP, "fiscal_years": [2021]}, _ph("lookup-conv", period="year_ended")),
    ({**LOOKUP, "tickers": ["NVDA"]}, _ph("lookup-conv", period="year_ended")),
    (_one("point_in_time", "AAPL", 2024, "revenue", filing_year=2025),
     _ph("pit-formal", period="year_ended")),                      # no end for the filing year
    (RATIO, _ph("ratio-formal", metric="synonym:1")),              # valid for revenue only
    ({**LOOKUP, "tickers": ["TSLA"]}, _ph("lookup-conv", company="short")),
    ({**LOOKUP, "tickers": ["TSLA"]}, _ph("lookup-conv", company="ticker")),
    ({**LOOKUP, "metrics": ["capex"]}, _ph("lookup-conv")),
    (_one("dimensional", "NVDA", 2025, "revenue", dimension="gaming"), _ph("dimensional-conv")),
    ({**SCREEN, "universe": "sp100"}, _ph("screen-top-conv", company="none")),
]


@pytest.mark.criterion("C7")
def test_c7_render_failures_point(kit):
    # oracle: the C7 rules; each case breaks one, so render must raise ValueError (fail loud).
    want = "What was AAPL's Revenues in fiscal 2024?"
    assert _render(kit, LOOKUP, _ph("lookup-conv")) == want
    for intent_kw, phrasing_kw in FAILS:
        with pytest.raises(ValueError):
            _render(kit, copy.deepcopy(intent_kw), phrasing_kw)


@pytest.mark.criterion("C7")
@given(year=st.integers(min_value=1990, max_value=2040),
       idx=st.integers(min_value=0, max_value=5),
       kind=st.sampled_from(["synonym", "shorthand"]))
def test_c7_index_and_period_end_property(kit, year, idx, kind):
    # oracle: C7 — an index is valid iff below the list length; year_ended needs a period end.
    raw = kit.raw_lexicon()
    n_forms = len(raw["metrics"]["revenue"]["synonyms" if kind == "synonym" else "shorthand"])
    phrasing = _ph("lookup-terse", metric=f"{kind}:{idx}")
    intent = _one("lookup", "AAPL", year, "revenue")
    if idx < n_forms:
        assert _render(kit, intent, phrasing).startswith("AAPL ")
    else:
        with pytest.raises(ValueError):
            _render(kit, intent, phrasing)
    pe = _ph("lookup-terse", period="year_ended")
    if year in raw["period_ends"]["AAPL"]:
        assert _render(kit, intent, pe).startswith("AAPL Revenues the fiscal year ended ")
    else:
        with pytest.raises(ValueError):
            _render(kit, intent, pe)


# --------------------------------------------------------------------------- C8
@pytest.mark.criterion("C8")
def test_c8_company_swap_point(kit):
    # oracle: the substitution rule — only the company surface differs (hand-written strings).
    kw = POINTS[0][0]
    outs = [_render(kit, kw, _ph("lookup-conv", f, "synonym:0", "year_ended"))
            for f in ("ticker", "short", "legal")]
    tail = "net sales in the fiscal year ended September 28, 2024?"
    assert outs == [f"What was AAPL's {tail}", f"What was Apple's {tail}",
                    f"What was Apple Inc.'s {tail}"]


def _swap_check(kit, raw, lex, pb, kw, cand, out):
    tid, cform, mform, pform = cand
    intent = Intent(**kw)
    if cform == "ticker" and kw.get("tickers"):
        t = kw["tickers"][0].upper()
        for other in ("short", "legal"):
            alt = render(intent, Phrasing(**_ph(tid, other, mform, pform)), lex, pb)
            assert alt == out.replace(t, raw["companies"][t][other])
    if pform == "fy_short":
        years = sorted(set(kw["fiscal_years"]))
        years += [kw["filing_year"]] if kw.get("filing_year") else []
        for other in ("fiscal", "fiscal_year"):
            alt = render(intent, Phrasing(**_ph(tid, cform, mform, other)), lex, pb)
            want = out
            for y in years:
                want = want.replace(f"FY{y % 100:02d}", kit.period_text(other, y, None, raw))
            assert alt == want


@pytest.mark.criterion("C8")
@pytest.mark.metamorphic
def test_c8_no_braces_and_form_swaps_metamorphic(kit):
    # oracle: substitution — every placeholder is replaced; swapping one form swaps only its
    # surface. Ticker strings and "FYnn" strings occur nowhere else in the fixture texts.
    raw = kit.raw_lexicon()
    templates = kit.raw_phrasebook()
    lex = load_lexicon(kit.lexicon)
    pb = load_phrasebook(kit.phrasebook)
    checked = 0
    for kw in kit.examples.values():
        intent = Intent(**kw)
        for cand in kit.candidates(kw, templates, raw):
            out = render(intent, Phrasing(**_ph(*cand)), lex, pb)
            assert "{" not in out and "}" not in out
            _swap_check(kit, raw, lex, pb, kw, cand, out)
            checked += 1
    assert checked > 100
