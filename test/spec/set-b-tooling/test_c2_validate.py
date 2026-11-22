"""C2: ``validate`` — errors, warnings and the mix.

Oracle: requirement 4 of docs/specs/set-b-tooling.md and the P set of pre-registration v0
section 3 (``("AAPL", 2026)``). Each bad case is a valid case from the conftest builders
with one field broken by hand, so the expected error is read straight off the spec's list.
"""

from __future__ import annotations

import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.set_b.core import validate

QUESTION = "Zqx unmistakable question wording 7741?"
NOTE = "Wvk private author note 5512"


def _make(kit, kind, cid, ticker, fy):
    return {"lookup": kit.lookup, "dimensional": kit.dimensional, "compute": kit.compute,
            "breakdown": kit.breakdown}[kind](cid, ticker=ticker, fy=fy)


def _valid_set(kit, n, kinds=None, tickers=None):
    """n valid cases cycling kinds, tickers and fiscal years 2025/2026 (never AAPL 2026)."""
    kinds = kinds or kit.KINDS
    tickers = tickers or kit.FILERS
    out = []
    for i in range(n):
        ticker = tickers[i % len(tickers)]
        fy = 2025 if (ticker == "AAPL" or i % 2 == 0) else 2026
        out.append(_make(kit, kinds[i % len(kinds)], f"v{i:03d}", ticker, fy))
    return out


def _errors_for(result, cid):
    return [m for m in result["errors"] if m.startswith(cid)]


def _assert_error(cases, cid, field):
    result = validate(cases)
    hits = [m for m in _errors_for(result, cid) if field in m]
    assert hits, f"no error for {cid} naming {field}: {result['errors']}"


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("field", ["question", "kind", "ticker", "fy", "expected", "unit"])
def test_missing_required_lookup_field(kit, field):
    # oracle: requirement 4 — a missing required field is an error that starts with the
    # id and names the field (requirement 1 lists these as required for a lookup)
    case = kit.lookup("bad1")
    del case[field]
    _assert_error([case], "bad1", field)


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("field,value", [
    ("fy", "2025"),          # str instead of int
    ("question", 17),        # int instead of str
    ("ticker", ["MSFT"]),    # list instead of str
])
def test_wrong_type(kit, field, value):
    # oracle: requirement 4 — a wrong type is an error naming the field
    case = kit.lookup("bad2")
    case[field] = value
    _assert_error([case], "bad2", field)


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("builder,field,value", [
    ("lookup", "kind", "screen"),
    ("compute", "formula", "cagr"),
    ("lookup", "unit", "EUR"),
    ("lookup", "scale", "lakhs"),
])
def test_unknown_enum_values(kit, builder, field, value):
    # oracle: requirement 4 — an unknown kind, formula, unit or scale is an error naming it
    case = getattr(kit, builder)("bad3")
    case[field] = value
    _assert_error([case], "bad3", field)


@pytest.mark.criterion("C2")
def test_duplicate_id(kit):
    # oracle: requirement 4 — a duplicate id is an error; the message starts with that id
    _assert_error([kit.lookup("dup"), kit.breakdown("dup")], "dup", "id")


@pytest.mark.criterion("C2")
def test_missing_id_uses_index_prefix(kit):
    # oracle: requirement 4 — a message for a case with no id starts with "#<index>"
    case = kit.lookup("x")
    del case["id"]
    result = validate([kit.lookup("ok1"), case])
    hits = [m for m in result["errors"] if re.match(r"#\d+", m) and "id" in m]
    assert hits, result["errors"]
    assert not _errors_for(result, "ok1")


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("ticker", ["IBM", "EXAMPLE", "msftx"])
def test_ticker_outside_the_suite_filers(kit, ticker):
    # oracle: requirement 4 and spec decisions — only the 12 suite filers are allowed
    _assert_error([kit.lookup("bad4", ticker=ticker)], "bad4", "ticker")


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("fy", [2023, 2027])
def test_fy_outside_range(kit, fy):
    # oracle: requirement 4 — fy outside 2024-2026 is an error
    _assert_error([kit.lookup("bad5", fy=fy)], "bad5", "fy")


@pytest.mark.criterion("C2")
def test_p_set_rejected_for_lookup_accepted_one_year_earlier(kit):
    # oracle: pre-registration v0 section 3 — ("AAPL", 2026) is in P and rejected;
    # ("AAPL", 2025) is not in P and gives no error
    bad = validate([kit.lookup("p1", ticker="AAPL", fy=2026)])
    hits = [m for m in _errors_for(bad, "p1") if "fy" in m or "ticker" in m]
    assert hits, bad["errors"]
    good = validate([kit.lookup("p2", ticker="AAPL", fy=2025)])
    assert good["errors"] == []


@pytest.mark.criterion("C2")
def test_p_set_rejected_as_compute_input_fy(kit):
    # oracle: requirement 4 — the P rule also applies to a compute input's fy: an AAPL
    # FY2025 case whose `current` input sits at fy 2026 is rejected; the same case with
    # the input at 2025 is accepted
    bad_inputs = [kit.inp("current", "1,125", fy=2026), kit.inp("prior", "1,000", fy=2024)]
    bad = validate([kit.compute("p3", ticker="AAPL", fy=2025, inputs=bad_inputs)])
    assert [m for m in _errors_for(bad, "p3") if "fy" in m], bad["errors"]
    ok_inputs = [kit.inp("current", "1,125", fy=2025), kit.inp("prior", "1,000", fy=2024)]
    ok = validate([kit.compute("p4", ticker="AAPL", fy=2025, inputs=ok_inputs)])
    assert ok["errors"] == []


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("raw", ["abc", "12..5", "5%%"])
def test_rejected_amount(kit, raw):
    # oracle: requirements 2 and 4 — an amount parse_amount rejects is an error naming
    # the field (`expected`)
    _assert_error([kit.lookup("bad6", expected=raw)], "bad6", "expected")


@pytest.mark.criterion("C2")
def test_percent_with_scale_is_a_rejected_amount(kit):
    # oracle: requirements 2 and 4 — "%" with a scale is rejected by parse_amount
    _assert_error([kit.lookup("bad7", expected="5%", scale="millions")], "bad7", "expected")


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("formula,names", [
    ("yoy", ["current"]),                    # only one input
    ("yoy", ["current", "previous"]),        # wrong name
    ("yoy", ["current", "prior", "prior"]),  # three inputs
    ("ratio", ["numerator", "bottom"]),
    ("difference", ["a", "c"]),
    ("sum", ["x"]),                          # fewer than 2 inputs
])
def test_formula_input_shapes(kit, formula, names):
    # oracle: requirement 4 — yoy needs exactly current+prior; ratio numerator+denominator;
    # difference a+b; sum at least 2 inputs; the error names `inputs`
    inputs = [kit.inp(n, "100") for n in names]
    case = kit.compute("bad8", formula=formula, expected="1", inputs=inputs)
    _assert_error([case], "bad8", "inputs")


@pytest.mark.criterion("C2")
def test_breakdown_with_one_row(kit):
    # oracle: requirement 4 — a breakdown with fewer than 2 rows is an error naming `rows`
    case = kit.breakdown("bad9", rows=[{"member": "Alpha", "expected": "10",
                                        "scale": "millions"}])
    _assert_error([case], "bad9", "rows")


@pytest.mark.criterion("C2")
def test_dimensional_without_member(kit):
    # oracle: requirement 4 — a dimensional case without `member` is an error naming it
    case = kit.dimensional("bad10")
    del case["member"]
    _assert_error([case], "bad10", "member")


@pytest.mark.criterion("C2")
def test_balanced_forty_cases_have_no_errors_or_warnings_and_mix_counts(kit):
    # oracle: requirement 4 — 40 cases, 10 per kind, tickers cycle over 12 filers (at most
    # 4 each, 10%), fy alternates: no errors, no warnings. The mix is counted by hand from
    # _valid_set: kind 10 each; ticker i % 12 -> the first 4 filers get 4, the rest 3;
    # fy: even i -> 2025 (20 cases); odd i -> 2026 except AAPL (i=12, 24, 36 are even
    # anyway), so 2026 gets 20
    cases = _valid_set(kit, 40)
    result = validate(cases)
    assert result["errors"] == []
    assert result["warnings"] == []
    mix = result["mix"]
    assert mix["total"] == 40
    assert {k: v for k, v in mix["kind"].items() if v} == {k: 10 for k in kit.KINDS}
    expected_tickers = {t: (4 if i < 4 else 3) for i, t in enumerate(kit.FILERS)}
    assert {k: v for k, v in mix["ticker"].items() if v} == expected_tickers
    assert {k: v for k, v in mix["fy"].items() if v} == {2025: 20, 2026: 20}


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("n", [39, 61])
def test_count_outside_forty_to_sixty_warns(kit, n):
    # oracle: requirement 4 — a total outside 40-60 is a warning; the set is otherwise
    # balanced (all kinds present, no ticker above 40%), so it is the only warning
    result = validate(_valid_set(kit, n))
    assert result["errors"] == []
    assert len(result["warnings"]) == 1
    assert result["mix"]["total"] == n


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("n", [40, 60])
def test_count_at_the_bounds_does_not_warn(kit, n):
    # oracle: requirement 4 — 40 and 60 are inside 40-60
    result = validate(_valid_set(kit, n))
    assert result["errors"] == []
    assert result["warnings"] == []
    assert result["mix"]["total"] == n


@pytest.mark.criterion("C2")
def test_kind_with_no_cases_warns(kit):
    # oracle: requirement 4 — no breakdown case among 42 is one warning, naming the kind
    result = validate(_valid_set(kit, 42, kinds=["lookup", "dimensional", "compute"]))
    assert result["errors"] == []
    assert len(result["warnings"]) == 1
    assert "breakdown" in result["warnings"][0]
    assert {k: v for k, v in result["mix"]["kind"].items() if v} == {
        "lookup": 14, "dimensional": 14, "compute": 14}


@pytest.mark.criterion("C2")
def test_one_ticker_above_forty_percent_warns(kit):
    # oracle: requirement 4 — 17 of 40 on MSFT is 42.5% > 40%: one warning naming MSFT;
    # 16 of 40 is exactly 40%, not more, so no warning
    over = _valid_set(kit, 23, tickers=[t for t in kit.FILERS if t != "MSFT"])
    over += [kit.lookup(f"m{i:02d}", ticker="MSFT") for i in range(17)]
    result = validate(over)
    assert result["errors"] == []
    assert len(result["warnings"]) == 1
    assert "MSFT" in result["warnings"][0]
    assert result["mix"]["ticker"]["MSFT"] == 17

    at = _valid_set(kit, 24, tickers=[t for t in kit.FILERS if t != "MSFT"])
    at += [kit.lookup(f"m{i:02d}", ticker="MSFT") for i in range(16)]
    result = validate(at)
    assert result["errors"] == []
    assert result["warnings"] == []
    assert result["mix"]["ticker"]["MSFT"] == 16


@pytest.mark.criterion("C2")
def test_messages_never_contain_question_or_note(kit):
    # oracle: requirement 4 — no message contains a case's question or note; the cases
    # below produce errors (bad ticker, bad fy, missing member, duplicate id) and warnings
    # (count 4, a ticker above 40%)
    def tag(c):
        c["question"] = QUESTION
        c["note"] = NOTE
        return c

    cases = [tag(kit.lookup("n1", ticker="IBM")), tag(kit.lookup("n2", fy=2030)),
             tag(kit.dimensional("n3")), tag(kit.compute("n1"))]
    del cases[2]["member"]
    result = validate(cases)
    assert result["errors"]
    assert result["warnings"]
    for m in result["errors"] + result["warnings"]:
        assert QUESTION not in m
        assert NOTE not in m
        assert "Zqx" not in m
        assert "Wvk" not in m


_case_spec = st.tuples(
    st.sampled_from(["lookup", "dimensional", "compute", "breakdown"]),
    st.sampled_from(["AAPL", "AMD", "AMZN", "AVGO", "GOOGL", "META", "MSFT", "MU", "NVDA",
                     "ORCL", "TSLA", "TSM"]),
    st.sampled_from([2025, 2026]),
)


@pytest.mark.criterion("C2")
@given(specs=st.lists(_case_spec, min_size=1, max_size=70))
def test_random_valid_cases_have_no_errors(kit, specs):
    # oracle: requirement 4 — every case below is valid by construction (suite filer,
    # fy in range, (AAPL, 2026) moved to 2025, unique ids, kind-specific fields present),
    # so `errors` is empty and mix.total is the case count
    cases = []
    for i, (kind, ticker, fy) in enumerate(specs):
        if ticker == "AAPL" and fy == 2026:
            fy = 2025
        cases.append(_make(kit, kind, f"r{i:03d}", ticker, fy))
    result = validate(cases)
    assert result["errors"] == []
    assert result["mix"]["total"] == len(cases)
