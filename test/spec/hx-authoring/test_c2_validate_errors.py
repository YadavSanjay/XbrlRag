"""C2: ``validate_hx`` rejects every broken rule with the case id and the field path.

Oracle: requirements 2-8 of docs/specs/hx-authoring.md (the "Which path" and "Filers"
bullets give each expected path). Each bad case is a valid conftest case with one rule
broken by hand; the expected prefix ``"<id>: <path>:"`` is read off the spec.
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.set_b.hx import validate_hx


def _with(case, **changes):
    out = copy.deepcopy(case)
    for key, value in changes.items():
        if value is _DROP:
            out.pop(key, None)
        else:
            out[key] = value
    return out


_DROP = object()


def _bad_cases(kit):
    """(case, expected path) pairs; each case's id is ``bad``."""
    cf, cp, ho, tx, co = kit.cross_filer, kit.cross_period, kit.honesty, kit.text, kit.compound
    row, yrow = kit.row, kit.yrow
    pit = cp("bad", "point_in_time")
    pit_same = copy.deepcopy(pit)
    pit_same["latest"]["accession"] = pit_same["as_originally_reported"]["accession"]
    pit_noacc = copy.deepcopy(pit)
    del pit_noacc["latest"]["accession"]
    comp_bad_rank = co("bad")
    comp_bad_rank["ranking"]["subtype"] = "sum"
    comp_bad_rank["ranking"]["expected"] = "500"
    comp_dep_rows = co("bad")
    del comp_dep_rows["dependent"]["rows"]
    return [
        # common fields
        (_with(tx("bad"), question=_DROP), "question"),
        (_with(tx("bad"), question=""), "question"),
        (_with(tx("bad"), kind="weird"), "kind"),
        (_with(tx("bad"), note=7), "note"),
        (_with(cf("bad"), subtype=_DROP), "subtype"),
        (_with(cf("bad"), subtype="top_n"), "subtype"),
        # cross_filer
        (_with(cf("bad"), fy=2019), "fy"),
        (_with(cf("bad"), fy="2025"), "fy"),
        (_with(cf("bad"), unit="EUR"), "unit"),
        (_with(cf("bad"), universe=["DEVA"], exclusions=[], rows=[row("DEVA", "1")]),
         "universe"),
        (_with(cf("bad"), universe=["DEVA", "DEVB", "DEVA"]), "universe[2]"),
        (_with(cf("bad"), exclusions=[{"ticker": "DEVD", "reason": "lazy"}]),
         "exclusions[0].reason"),
        (_with(cf("bad"), exclusions=[{"ticker": "DEVE", "reason": "other"}]),
         "exclusions[0].ticker"),
        (_with(cf("bad"), exclusions=[{"ticker": "DEVD", "reason": "other"},
                                      {"ticker": "DEVD", "reason": "currency"}]),
         "exclusions[1].ticker"),
        (_with(cf("bad"), universe=["DEVA", "DEVB"],
               exclusions=[{"ticker": "DEVA", "reason": "not_filed"},
                           {"ticker": "DEVB", "reason": "not_filed"}], rows=[]),
         "exclusions"),
        (_with(cf("bad"), rows=[row("DEVA", "300"), row("DEVD", "200")]), "rows[1].ticker"),
        (_with(cf("bad"), rows=[row("DEVA", "300"), row("DEVA", "200")]), "rows[1].ticker"),
        (_with(cf("bad"), rows=[row("DEVA", "abc"), row("DEVB", "200")]), "rows[0].expected"),
        (_with(cf("bad"), rows=[row("DEVA", "300", scale="lakhs"), row("DEVB", "200")]),
         "rows[0].scale"),
        (_with(cf("bad"), rows=[row("DEVA", "300", accession="12-34"), row("DEVB", "200")]),
         "rows[0].accession"),
        (_with(cf("bad"), rows=[row("DEVB", "200"), row("DEVA", "300")]), "rows"),
        (_with(cf("bad", "screen_bottom"), rows=[row("DEVB", "200"), row("DEVC", "100")]),
         "rows"),
        (_with(cf("bad"), rows=[]), "rows"),
        (_with(cf("bad", "count"), expected=3), "expected"),
        (_with(cf("bad", "count"), expected="2"), "expected"),
        (_with(cf("bad", "count"), expected=-1, rows=[]), "expected"),
        (_with(cf("bad", "sum"), rows=[row("DEVA", "300")]), "rows"),
        (_with(cf("bad", "sum"), expected="five hundred"), "expected"),
        (_with(cf("bad", "median"), rows=[row("DEVA", "300"), row("DEVB", "200")]), "rows"),
        (_with(cf("bad", "peer_relative"), ticker="DEVE"), "ticker"),
        (_with(cf("bad", "peer_relative"), expected=0), "expected"),
        (_with(cf("bad", "peer_relative"), expected=4), "expected"),
        (_with(cf("bad", "peer_relative"), rows=[row("DEVA", "300"), row("DEVB", "200")]),
         "rows"),
        # cross_period
        (_with(cp("bad"), ticker=_DROP), "ticker"),
        (_with(cp("bad"), subtype="seasonal"), "subtype"),
        (_with(cp("bad"), rows=[yrow(2024, "110")]), "rows"),
        (_with(cp("bad"), rows=[yrow(2024, "110"), yrow(2024, "111")]), "rows[1].fy"),
        (_with(cp("bad"), rows=[yrow(2018, "110"), yrow(2024, "111")]), "rows[0].fy"),
        (_with(cp("bad", "cagr"), rows=[yrow(2023, "1"), yrow(2024, "2"), yrow(2025, "3")]),
         "rows"),
        (_with(cp("bad", "cagr"), expected=_DROP), "expected"),
        (pit_noacc, "latest.accession"),
        (pit_same, "latest.accession"),
        (_with(pit, fy=_DROP), "fy"),
        (_with(pit, latest=_DROP), "latest"),
        # compound
        (comp_bad_rank, "ranking.subtype"),
        (_with(co("bad"), dependent={"kind": "honesty", "subtype": "decline",
                                     "reason": "not_tagged"}), "dependent.kind"),
        (_with(co("bad"), dependent={"kind": "cross_period", "subtype": "trend",
                                     "ticker": "DEVC", "unit": "USD",
                                     "rows": [yrow(2024, "1"), yrow(2025, "2")]}),
         "dependent.ticker"),
        (comp_dep_rows, "dependent.rows"),
        (_with(co("bad"), ranking=_DROP), "ranking"),
        # honesty
        (_with(ho("bad"), subtype="vague"), "subtype"),
        (_with(ho("bad"), assumptions=[]), "assumptions"),
        (_with(ho("bad"), assumptions=[5]), "assumptions[0]"),
        (_with(ho("bad", "false_premise"), correction=_DROP), "correction"),
        (_with(ho("bad", "decline"), reason="bored"), "reason"),
        (_with(ho("bad"), fy=2030), "fy"),
        # text
        (_with(tx("bad"), reference_answer=_DROP), "reference_answer"),
        (_with(tx("bad"), key_points=[]), "key_points"),
        (_with(tx("bad"), passages=[]), "passages"),
        (_with(tx("bad"), passages=[{"accession": "x", "quote": "q"}]),
         "passages[0].accession"),
        (_with(tx("bad"), passages=[{"accession": kit.ACC_TEXT, "quote": ""}]),
         "passages[0].quote"),
        (_with(tx("bad"), fy=_DROP), "fy"),
    ]


@pytest.mark.criterion("C2")
def test_each_broken_rule_gives_an_error_with_id_and_path(kit):
    # oracle: requirement 8 "Which path" — each table row's path is read off the spec.
    misses = []
    for case, path in _bad_cases(kit):
        errors = validate_hx([case], kit.DEV, kit.HELD)["errors"]
        if not any(e.startswith(f"bad: {path}:") for e in errors):
            misses.append((path, errors))
    assert misses == []


@pytest.mark.criterion("C2")
def test_missing_id_uses_the_index_and_duplicate_id_is_reported(kit):
    # oracle: requirement 8 — "#<index>" when the id is missing; a repeated id is an `id`
    # error under that id.
    first = kit.text("t1")
    noid = kit.text("t2")
    del noid["id"]
    errors = validate_hx([first, noid], kit.DEV, kit.HELD)["errors"]
    assert any(e.startswith("#1: id:") for e in errors)
    errors = validate_hx([kit.text("dup"), kit.honesty("dup")], kit.DEV, kit.HELD)["errors"]
    assert any(e.startswith("dup: id:") for e in errors)


def _held_positions(kit):
    """(case, path) with HOLDA placed at that path."""
    cf, cp, ho, tx, co = kit.cross_filer, kit.cross_period, kit.honesty, kit.text, kit.compound
    uni = cf("h")
    uni["universe"] = ["DEVA", "DEVB", "DEVC", "DEVD", "HOLDA"]
    exc = cf("h")
    exc["universe"] = ["DEVA", "DEVB", "DEVC", "HOLDA"]
    exc["exclusions"] = [{"ticker": "HOLDA", "reason": "not_filed"}]
    rws = cf("h")
    rws["universe"] = ["HOLDA", "DEVB", "DEVC", "DEVD"]
    rws["rows"] = [kit.row("HOLDA", "300"), kit.row("DEVB", "200")]
    rnk = co("h")
    rnk["ranking"]["universe"] = ["DEVA", "DEVB", "DEVC", "DEVD", "HOLDA"]
    dep = co("h")
    dep["dependent"]["ticker"] = "HOLDA"
    return [
        (uni, "universe[4]"),
        (exc, "exclusions[0].ticker"),
        (rws, "rows[0].ticker"),
        (cp("h", ticker="HOLDA"), "ticker"),
        (ho("h", tickers=["HOLDA"]), "tickers[0]"),
        (ho("h", "decline", reason="out_of_corpus", tickers=["DEVA", "HOLDA"]), "tickers[1]"),
        (tx("h", ticker="HOLDA"), "ticker"),
        (rnk, "ranking.universe[4]"),
        (dep, "dependent.ticker"),
    ]


@pytest.mark.criterion("C2")
def test_a_held_out_filer_is_an_error_in_every_ticker_position(kit):
    # oracle: requirement 8 "Filers" — a held-out ticker is always an error, also in a
    # decline / out_of_corpus case; the reason contains "held-out filer".
    misses = []
    for case, path in _held_positions(kit):
        errors = validate_hx([case], kit.DEV, kit.HELD)["errors"]
        if not any(e.startswith(f"h: {path}:") and "held-out filer" in e for e in errors):
            misses.append((path, errors))
    assert misses == []


@pytest.mark.criterion("C2")
def test_a_non_development_filer_is_an_error_except_in_an_out_of_corpus_decline(kit):
    # oracle: requirement 8 "Filers" — "not a development filer", except honesty decline
    # with reason out_of_corpus.
    case = kit.cross_filer("n1")
    case["universe"] = ["DEVA", "DEVB", "DEVC", "DEVD", "OUTX"]
    errors = validate_hx([case], kit.DEV, kit.HELD)["errors"]
    assert any(e.startswith("n1: universe[4]:") and "not a development filer" in e
               for e in errors)
    for reason in ("out_of_period", "not_tagged", "out_of_scope"):
        case = kit.honesty("n2", "decline", reason=reason, tickers=["OUTX"])
        errors = validate_hx([case], kit.DEV, kit.HELD)["errors"]
        assert any(e.startswith("n2: tickers[0]:") and "not a development filer" in e
                   for e in errors)
    case = kit.honesty("n3", "decline", reason="out_of_corpus", tickers=["OUTX"])
    assert validate_hx([case], kit.DEV, kit.HELD)["errors"] == []


# Rule breakers that leave every value in place (only a structural field changes).
_BREAKERS = ["unit", "fy", "kind", "subtype", "exclusion_reason"]


def _secret_case(kind_pick: int, secret: str, tickers: list[str]) -> dict:
    a, b, c = tickers
    cases = [
        {"id": "s", "question": f"Q-{secret}", "note": f"N-{secret}", "kind": "cross_filer",
         "subtype": "sum", "fy": 2025, "unit": "USD", "universe": [a, b, c],
         "exclusions": [{"ticker": c, "reason": "other"}],
         "rows": [{"ticker": a, "expected": "987,654,321"},
                  {"ticker": b, "expected": "123,456,789", "accession": "0000000009-25-000009"}],
         "expected": "1,111,111,110"},
        {"id": "s", "question": f"Q-{secret}", "note": f"N-{secret}", "kind": "text",
         "ticker": a, "fy": 2025, "reference_answer": f"A-{secret}",
         "key_points": [f"K-{secret}"],
         "passages": [{"accession": "0000000009-25-000009", "quote": f"P-{secret}"}]},
        {"id": "s", "question": f"Q-{secret}", "kind": "honesty", "subtype": "ambiguous",
         "tickers": [a, b], "assumptions": [f"S-{secret}"]},
        {"id": "s", "question": f"Q-{secret}", "kind": "honesty", "subtype": "false_premise",
         "tickers": [a], "correction": f"C-{secret}"},
    ]
    return cases[kind_pick]


def _break(case: dict, how: str) -> dict:
    """Breaks one rule; where ``how`` doesn't apply to the kind, an out-of-range fy."""
    out = copy.deepcopy(case)
    if how == "unit" and "unit" in out:
        out["unit"] = "XYZ"
    elif how == "kind":
        out["kind"] = "bogus"
    elif how == "subtype" and "subtype" in out:
        out["subtype"] = "bogus"
    elif how == "exclusion_reason" and "exclusions" in out:
        out["exclusions"][0]["reason"] = "bogus"
    else:
        out["fy"] = 1999
    return out


def _values(obj) -> list[str]:
    """Every string and number in a case, except its id and its kind/subtype."""
    if isinstance(obj, dict):
        return [v for k, val in obj.items() if k not in ("id", "kind", "subtype")
                for v in _values(val)]
    if isinstance(obj, list):
        return [v for item in obj for v in _values(item)]
    return [str(obj)]


@pytest.mark.criterion("C2")
@given(kind_pick=st.integers(min_value=0, max_value=3),
       how=st.sampled_from(_BREAKERS),
       secret=st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=8, max_size=16))
def test_no_message_carries_any_case_value_but_the_id(kit, kind_pick, how, secret):
    # oracle: requirement 8 "No values in messages" — with one rule broken, no error or
    # warning contains the question, note, texts, amounts, accession or a ticker.
    tickers = ["TKQZA", "TKQZB", "TKQZC"]
    dev = frozenset(kit.DEV | set(tickers))
    case = _break(_secret_case(kind_pick, secret, tickers), how)
    got = validate_hx([case], dev, kit.HELD)
    assert got["errors"], "the broken rule must be reported"
    assert all(e.startswith("s: ") for e in got["errors"])
    # Every value the case holds (fy, unit, reason, amounts, accession, tickers, texts),
    # the parsed amounts, and the broken kind / subtype values.
    leaks = [v for v in _values(case) if len(v) >= 3 and v not in ("USD", "other")]
    leaks += ["987654321", "123456789", "1111111110", "bogus"]
    for message in got["errors"] + got["warnings"]:
        for leak in leaks:
            assert leak not in message
