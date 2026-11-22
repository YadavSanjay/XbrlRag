"""C3: a key the statement disagrees with blocks the build unless a reviewed resolution covers it.

Oracle: the fixtures (statement lines hand-checked, companyfacts entries as SEC serves them)
and the committed resolution file. The rebuilt keys below are the 2026-11-07 rebuild's dev key
inputs (spec, "The defect").

Since docs/specs/reviewed-resolutions.md (requirement 2) the locked list is the *original*
list: it only shrinks, and an entry outside it is admitted only with a complete review record.
Every entry these tests construct carries no review, so each is refused exactly as before.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.statement_witness import (
    WitnessBlocked,
    check_resolutions,
    load_resolutions,
    resolve,
    select_key,
    statement_witness,
)

# tolerance: filed integer amounts; rel 1e-12 absorbs binary floating point only.
REL = 1e-12
V23 = ("2023-01-01", "2023-12-31")
M24 = ("2024-01-01", "2024-12-31")
REV = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
CWCL = "us-gaap:ContractWithCustomerLiability"
PRETAX_INCL_EQUITY = ("us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxes"
                      "ExtraordinaryItemsNoncontrollingInterest")
AAPL_Q = "0000320193-26-000020"  # AAPL 10-Q, FY2026 Q3 (2025-09-27 comparative)


def _w(kit, family, k, annual, annual_value=None):
    e = kit.env()
    return statement_witness(e.tables, e.rules, e.notes, family, k, annual, annual_value)


def _orcl(kit):
    return kit.key("ORCL", CWCL, kit.ORCL, "2024-05-31", None, 2024)


def _vst(kit, concept):
    return kit.key("VST", concept, kit.VST, V23[1], V23[0], 2023)


@pytest.mark.criterion("C3")
def test_vst_rebuilt_revenue_disagrees(kit):
    # oracle: VST FY2025 10-K income statements "Operating revenues" 2023: 14,779 (Revenues);
    # the rebuilt key RevenueFromContractWithCustomerExcludingAssessedTax 13,804 is the note's
    # "Total revenue from contracts with customers", on no statement
    w = _w(kit, "Revenue", _vst(kit, REV), kit.VST)
    assert w["status"] == "disagree"
    assert w["value"] == pytest.approx(14_779e6, rel=REL)
    assert [ln["concept"] for ln in w["lines"]] == ["us-gaap:Revenues"]
    assert w["key_value"] == pytest.approx(13_804e6, rel=REL)


@pytest.mark.criterion("C3")
def test_mcd_income_statement_sub_line_disagrees(kit):
    # oracle: MCD FY2025 10-K 2024: cash flows "Depreciation and amortization" 2,097; the
    # income statement sub-line of the same label 447 (DepreciationDepletionAndAmortization)
    k = kit.key("MCD", "us-gaap:DepreciationDepletionAndAmortization", kit.MCD, M24[1], M24[0],
                2024)
    w = _w(kit, "DepreciationAndAmortization", k, kit.MCD)
    assert (w["status"], [ln["stmt"] for ln in w["lines"]]) == ("disagree", ["CF"])
    assert w["value"] == pytest.approx(2_097e6, rel=REL)


@pytest.mark.criterion("C3")
def test_aapl_noncurrent_debt_disagrees_with_the_total(kit):
    # oracle: AAPL FY2025 10-K balance sheet "Term debt" 12,350 + 78,328 = 90,678 (the debt
    # note's "Total term debt" 90,678); the rebuilt key LongTermDebtNoncurrent 78,328 comes
    # from a later 10-Q whose 2025-09-27 column equals the 10-K's (no recast)
    concept = "us-gaap:LongTermDebtNoncurrent"
    k = kit.key("AAPL", concept, AAPL_Q, "2025-09-27", None, 2025)
    annual = kit.cf_value("AAPL", concept, kit.AAPL, "2025-09-27", None)
    w = _w(kit, "Debt", k, kit.AAPL, annual)
    assert (w["status"], w["adsh"]) == ("disagree", kit.AAPL)
    assert w["value"] == pytest.approx(12_350e6 + 78_328e6, rel=REL)


@pytest.mark.criterion("C3")
def test_amd_pretax_including_equity_income_disagrees(kit):
    # oracle: AMD FY2025 10-K 2024: income statement "... before income taxes and equity
    # income" 1,989; the tax note's "Total pre-tax income including equity income in investee"
    # 2,022
    k = kit.key("AMD", PRETAX_INCL_EQUITY, kit.AMD, "2024-12-28", "2023-12-31", 2024)
    w = _w(kit, "PretaxIncome", k, kit.AMD)
    assert w["status"] == "disagree"
    assert w["value"] == pytest.approx(1_989e6, rel=REL)


@pytest.mark.criterion("C3")
@pytest.mark.parametrize(
    ("ticker", "family", "concept", "accession", "end", "unit", "plabel"),
    [
        # oracle: KO FY2025 balance sheet "Accounts payable and accrued expenses" 14,813, which
        # the AccountsPayable rule rejects (accrued); the rebuilt key is that line, from a 10-Q
        ("KO", "AccountsPayable", "us-gaap:AccountsPayableAndAccruedLiabilitiesCurrent",
         "0001628280-26-067844", "2025-12-31", "USD", "Accounts payable and accrued expenses"),
        # oracle: PG FY2026 10-K parenthetical "Common stock, issued (in shares)" 4,009.2M; the
        # rebuilt SharesOutstanding key is that issued count
        ("PG", "SharesOutstanding", "us-gaap:CommonStockSharesIssued", "0000080424-26-000103",
         "2025-06-30", "shares", "Common stock, issued (in shares)"),
    ],
)
def test_rebuilt_keys_on_rejected_lines(kit, ticker, family, concept, accession, end, unit,
                                        plabel):
    # oracle: the rendered line named in the parametrize comment, checked by hand
    k = kit.key(ticker, concept, accession, end, None, 2025, unit=unit)
    annual_adsh = getattr(kit, ticker)
    annual = kit.cf_value(ticker, concept, annual_adsh, end, None, unit)
    w = _w(kit, family, k, annual_adsh, annual)
    assert (w["status"], w["reason"], w["lines"]) == ("rejected_line", plabel, [])


@pytest.mark.criterion("C3")
def test_select_key_blocks_unless_resolved(kit):
    # oracle: the committed resolution file (ORCL FY2024 total contract liability 10,546,
    # reading key); ORCL FY2025 10-K balance sheets "Deferred revenues" 2024-05-31: 9,313
    e = kit.env()
    k = _orcl(kit)
    vals = {CWCL: 10_546e6}
    with pytest.raises(WitnessBlocked):
        select_key(e.tables, e.rules, e.notes, [], "DeferredRevenue", [k], kit.ORCL, vals)
    got = select_key(e.tables, e.rules, e.notes, e.resolutions, "DeferredRevenue", [k],
                     kit.ORCL, vals)
    assert got["status"] == "disagree"
    assert got["resolution"]["reading"] == "key"
    assert got["resolution"]["concept"] == CWCL
    assert got["key_value"] == pytest.approx(10_546e6, rel=REL)
    assert got["value"] == pytest.approx(9_313e6, rel=REL)
    vst = _vst(kit, REV)
    with pytest.raises(WitnessBlocked):
        select_key(e.tables, e.rules, e.notes, e.resolutions, "Revenue", [vst], kit.VST, {})
    with pytest.raises(WitnessBlocked):
        select_key(e.tables, e.rules, e.notes, e.resolutions, "Revenue", [], kit.VST, {})
    assert issubclass(WitnessBlocked, ValueError)


@pytest.mark.criterion("C3")
def test_a_moved_key_is_not_covered(kit):
    # oracle: requirement 3, resolve matches family, cik, fiscal year, concept and value exactly
    e = kit.env()
    w = statement_witness(e.tables, e.rules, e.notes, "DeferredRevenue", _orcl(kit), kit.ORCL)
    assert resolve(w, e.resolutions)["resolution"]["value"] == pytest.approx(10_546e6, rel=REL)
    moved_value = dict(w, key_value=10_733e6)
    assert resolve(moved_value, e.resolutions)["resolution"] is None
    moved_concept = dict(w, key_concept="us-gaap:DeferredRevenue")
    assert resolve(moved_concept, e.resolutions)["resolution"] is None
    other_year = dict(w, fiscal_year=2025)
    assert resolve(other_year, e.resolutions)["resolution"] is None
    accepted = dict(w, status="agree")
    assert resolve(accepted, e.resolutions)["resolution"] is None
    assert w["resolution"] is None  # resolve returns a copy


@pytest.mark.criterion("C3")
def test_live_resolutions_only_shrink(kit):
    # oracle: requirement 3 against the locked (original) list of one entry: the original list
    # only shrinks; an unreviewed entry outside it is refused (reviewed-resolutions
    # requirement 2; a reviewed live entry passes)
    locked = load_resolutions(kit.resolutions_path)
    assert len(locked) == 1
    live = kit.live / "key_resolutions.yaml"
    assert live.exists(), "configs/eval/v2/key_resolutions.yaml is missing"
    n = check_resolutions(load_resolutions(live), locked)
    assert n == len(load_resolutions(live))
    assert check_resolutions(locked, locked) == 1
    assert check_resolutions([], locked) == 0
    grown = [*locked, dict(locked[0], fiscal_year=2025)]
    with pytest.raises(ValueError):
        check_resolutions(grown, locked)
    with pytest.raises(ValueError):
        check_resolutions([dict(locked[0], reason="another reason")], locked)


ENTRY = "family: X, cik: '0000000001', fiscal_year: 2024, concept: 'us-gaap:A'"
BAD = [
    "version: 2\nresolutions: []\n",
    f"version: 1\nresolutions:\n  - {{{ENTRY}, value: 1, reading: maybe, reason: r}}\n",
    f"version: 1\nresolutions:\n  - {{{ENTRY}, value: 1, reading: key, reason: ''}}\n",
    f"version: 1\nresolutions:\n  - {{{ENTRY}, reading: key, reason: r}}\n",
    f"version: 1\nresolutions:\n  - {{{ENTRY}, value: 1, reading: key, reason: r, by: me}}\n",
    (f"version: 1\nresolutions:\n  - {{{ENTRY}, value: 1, reading: key, reason: r}}\n"
     f"  - {{{ENTRY}, value: 2, reading: drop, reason: s}}\n"),
]


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("text", BAD)
def test_malformed_resolutions_are_refused(tmp_path: Path, text):
    # oracle: requirement 3, load_resolutions' ValueError cases
    p = tmp_path / "res.yaml"
    p.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        load_resolutions(p)


@pytest.mark.criterion("C3")
@given(year=st.integers(min_value=2000, max_value=2040),
       value=st.integers(min_value=1, max_value=10**13))
def test_any_entry_outside_the_locked_list_is_growth(kit, year, value):
    # oracle: requirement 3: an unreviewed entry differing from every locked entry in any
    # field is refused (reviewed-resolutions requirement 2); a subset of the locked list is
    # accepted
    locked = load_resolutions(kit.resolutions_path)
    new = dict(locked[0], fiscal_year=year, value=value)
    if year == 2024 and value == 10_546_000_000:
        assert check_resolutions([new], locked) == 1
    else:
        with pytest.raises(ValueError):
            check_resolutions([new], locked)


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
def test_resolve_never_changes_the_status(kit):
    # oracle: requirement 3, "It never changes status"
    e = kit.env()
    keys = [
        ("DeferredRevenue", _orcl(kit), kit.ORCL),
        ("Revenue", _vst(kit, "us-gaap:Revenues"), kit.VST),
        ("Revenue", _vst(kit, REV), kit.VST),
    ]
    statuses = []
    for family, k, adsh in keys:
        w = statement_witness(e.tables, e.rules, e.notes, family, k, adsh)
        r = resolve(w, e.resolutions)
        assert {x: y for x, y in r.items() if x != "resolution"} == {
            x: y for x, y in w.items() if x != "resolution"}
        statuses.append(r["status"])
    assert statuses == ["disagree", "agree", "disagree"]
