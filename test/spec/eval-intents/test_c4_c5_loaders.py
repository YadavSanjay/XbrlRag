"""Spec tests C4-C5 for eval-intents: the phrasebook and lexicon loaders
(docs/specs/eval-intents.md)."""

from __future__ import annotations

import copy
import datetime as dt
import tempfile

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.suites.intents import load_lexicon, load_phrasebook

GOOD_T = {"id": "t1", "register": "terse", "shapes": ["lookup"],
          "text": "{company} {metric} {period}"}


def _pb(*templates):
    return {"templates": [copy.deepcopy(t) for t in templates]}


def _t(**over):
    return {**copy.deepcopy(GOOD_T), **over}


# --------------------------------------------------------------------------- C4
@pytest.mark.criterion("C4")
def test_c4_fixture_loads_point(kit):
    # oracle: the committed fixture phrasebook.yaml, read by hand.
    pb = load_phrasebook(kit.phrasebook)
    ids = [t.id for t in pb.templates]
    assert ids == ["lookup-terse", "lookup-conv", "lookup-formal", "dimensional-conv",
                   "breakdown-formal", "yoy-conv", "span-terse", "cagr-formal", "ratio-formal",
                   "pit-formal", "screen-top-conv", "screen-bottom-conv", "screen-sum-terse",
                   "screen-median-formal", "peer-conv"]
    span = pb.templates[6]
    assert span.register == "terse"
    assert tuple(span.shapes) == ("compute.difference", "compute.cagr", "trend")
    assert isinstance(span.shapes, tuple)
    assert span.text == "{company} {metric}, {first_period} to {last_period}"
    assert pb.templates[1].text == "What was {company}'s {metric} in {period}?"
    assert pb.templates[1].register == "conversational"


BAD_PHRASEBOOKS = [
    ("duplicate id", _pb(GOOD_T, GOOD_T)),
    ("empty id", _pb(_t(id=""))),
    ("unknown register", _pb(_t(register="casual"))),
    ("empty shapes", _pb(_t(shapes=[]))),
    ("unknown shape", _pb(_t(shapes=["lookup.top"]))),
    ("bare compute is not a shape", _pb(_t(shapes=["compute"]))),
    ("unknown placeholder", _pb(_t(text="{ticker} {metric} {period}"))),
    ("stray closing brace", _pb(_t(text="{company} {metric} {period} }"))),
    ("stray opening brace", _pb(_t(text="{company} {metric} { {period}"))),
    ("placeholder the shape cannot fill", _pb(_t(text="{company} {metric} {period} {dimension}"))),
    ("placeholder one listed shape cannot fill",
     _pb(_t(shapes=["peer_relative", "lookup"], text="{company} {metric} {period} {universe}"))),
    ("required placeholder missing", _pb(_t(text="{company} {metric}"))),
    ("yoy without last_period",
     _pb(_t(shapes=["compute.yoy_growth"], text="{company} {metric} {first_period}"))),
    ("screen.top without n",
     _pb(_t(shapes=["screen.top"], text="top {metric} of {universe} in {period}"))),
]


@pytest.mark.criterion("C4")
def test_c4_bad_phrasebooks_raise(kit, tmp_path):
    # oracle: the C4 rules; each file breaks exactly one, so loading it must raise ValueError.
    good = kit.write_yaml(tmp_path, "good.yaml", _pb(GOOD_T, _t(
        id="t2", shapes=["compute.yoy_growth"], text="{company} {metric} growth {last_period}")))
    assert [t.id for t in load_phrasebook(good).templates] == ["t1", "t2"]
    for name, doc in BAD_PHRASEBOOKS:
        path = kit.write_yaml(tmp_path, "bad.yaml", doc)
        with pytest.raises(ValueError):
            load_phrasebook(path)
        assert name


@pytest.mark.criterion("C4")
@given(shape=st.sampled_from(sorted(
    ["lookup", "dimensional", "breakdown", "compute.yoy_growth", "compute.difference",
     "compute.cagr", "compute.ratio", "trend", "point_in_time", "screen.top", "screen.bottom",
     "screen.sum", "screen.median", "peer_relative"])),
       extra=st.sampled_from(["company", "metric", "metric_2", "dimension", "period",
                              "first_period", "last_period", "filing_period", "universe", "n"]))
def test_c4_fillable_property(kit, shape, extra):
    # oracle: the spec's placeholder table — the required placeholders plus one more load iff
    # the extra one is fillable for the shape.
    fillable, required = kit.fill[shape]
    text = " ".join("{" + p + "}" for p in sorted(required | {extra}))
    with tempfile.TemporaryDirectory() as d:
        path = kit.write_yaml(d, "pb.yaml", _pb(_t(id="x", shapes=[shape], text=text)))
        if extra in fillable:
            pb = load_phrasebook(path)
            got = [(t.id, tuple(t.shapes), t.text) for t in pb.templates]
            assert got == [("x", (shape,), text)]
        else:
            with pytest.raises(ValueError):
                load_phrasebook(path)


# --------------------------------------------------------------------------- C5
@pytest.mark.criterion("C5")
def test_c5_fixture_loads_point(kit):
    # oracle: the committed fixture lexicon.yaml, read by hand.
    lex = load_lexicon(kit.lexicon)
    assert set(lex.companies) == {"AAPL", "MSFT", "NVDA"}  # "msft" upper-cased
    assert lex.companies["MSFT"] == {"short": "Microsoft", "legal": "Microsoft Corporation"}
    assert lex.metrics["revenue"] == {"label": "Revenues",
                                      "synonyms": ("net sales", "total revenue"),
                                      "shorthand": ("top line",)}
    assert lex.metrics["gross_profit"] == {"label": "Gross Profit",
                                           "synonyms": ("gross margin dollars",), "shorthand": ()}
    assert lex.metrics["rd_expense"] == {"label": "Research and Development Expense",
                                         "synonyms": (), "shorthand": ()}
    assert lex.dimensions["data_center"] == ("Data Center segment", "data center business")
    assert lex.universes["covered"] == ("the covered companies",)
    assert lex.period_ends["AAPL"] == {2022: dt.date(2022, 9, 24), 2023: dt.date(2023, 9, 30),
                                       2024: dt.date(2024, 9, 28)}
    assert lex.period_ends["MSFT"] == {2024: dt.date(2024, 6, 30)}


GOOD_LEX = {
    "companies": {"AAPL": {"short": "Apple", "legal": "Apple Inc."}},
    "metrics": {"revenue": {"label": "Revenues", "synonyms": ["net sales"]}},
    "dimensions": {"segment": ["business segment"]},
    "universes": {"covered": ["the covered companies"]},
    "period_ends": {"AAPL": {2024: "2024-09-28"}},
}


def _lex(path_keys, value):
    doc = copy.deepcopy(GOOD_LEX)
    node = doc
    for k in path_keys[:-1]:
        node = node[k]
    if value is _DEL:
        del node[path_keys[-1]]
    else:
        node[path_keys[-1]] = value
    return doc


_DEL = object()

BAD_LEXICONS = [
    (("companys",), {"AAPL": {"short": "Apple", "legal": "Apple Inc."}}),   # unknown top-level key
    (("companies", "AAPL", "short"), _DEL),
    (("companies", "AAPL", "legal"), _DEL),
    (("companies", "AAPL", "short"), ""),
    (("metrics", "revenue", "label"), _DEL),
    (("metrics", "revenue", "label"), "Rev{enue}"),
    (("metrics", "revenue", "synonyms"), ["net sales", ""]),
    (("metrics", "revenue", "synonyms"), ["net } sales"]),
    (("dimensions", "segment"), []),
    (("dimensions", "segment"), ["{segment}"]),
    (("universes", "covered"), []),
    (("period_ends", "AAPL", 2024), "2024-13-01"),
    (("period_ends", "AAPL", 2024), "September 28, 2024"),
]


@pytest.mark.criterion("C5")
def test_c5_bad_lexicons_raise(kit, tmp_path):
    # oracle: the C5 rules; each file breaks exactly one, so loading it must raise ValueError.
    lex = load_lexicon(kit.write_yaml(tmp_path, "good.yaml", GOOD_LEX))
    assert lex.metrics["revenue"]["shorthand"] == ()
    sparse = load_lexicon(kit.write_yaml(tmp_path, "sparse.yaml", {"metrics": GOOD_LEX["metrics"]}))
    empties = (sparse.companies, sparse.dimensions, sparse.universes, sparse.period_ends)
    assert empties == ({}, {}, {}, {})
    for keys, value in BAD_LEXICONS:
        path = kit.write_yaml(tmp_path, "bad.yaml", _lex(keys, value))
        with pytest.raises(ValueError):
            load_lexicon(path)


@pytest.mark.criterion("C5")
@given(day=st.dates(min_value=dt.date(1990, 1, 1), max_value=dt.date(2040, 12, 31)),
       ticker=st.sampled_from(["aapl", "Aapl", "AAPL", "aaPL"]))
def test_c5_period_end_property(kit, day, ticker):
    # oracle: C5 — an ISO date string loads as that datetime.date, under an upper-cased ticker.
    doc = copy.deepcopy(GOOD_LEX)
    doc["companies"] = {ticker: {"short": "Apple", "legal": "Apple Inc."}}
    doc["period_ends"] = {ticker: {day.year: day.isoformat()}}
    with tempfile.TemporaryDirectory() as d:
        lex = load_lexicon(kit.write_yaml(d, "lex.yaml", doc))
    assert lex.period_ends == {"AAPL": {day.year: day}}
    assert list(lex.companies) == ["AAPL"]
