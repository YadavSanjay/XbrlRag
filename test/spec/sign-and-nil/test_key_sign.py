"""Spec tests for sign-and-nil C3: an eval key keeps its element's sign and refuses a
sign flip between filings.

docs/specs/sign-and-nil.md, requirement R3. The keys are built by the product's own
`fiscal_year_key` / `latest_filed_key` over committed SEC companyfacts excerpts
(fixtures/companyfacts_excerpts.json); FSDS `pre`/`num` rows of the same filings
(fixtures/fsds_lines.json) state the tagged value and whether the statement line negates it.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.core import KeyAmbiguousError, fiscal_year_key, latest_filed_key

AAPL, INTC, VST, AMD = "0000320193", "0000050863", "0001692819", "0000002488"

# (cik, tag, fiscal year, FSDS accession, FSDS ddate, tagged value, printed value).
# The printed value is the statement's: -tagged on a negating line (pre.negating = 1).
SIGNED = [
    # Apple FY2024 10-K cash flow statement: negated labels, outflows tagged positive
    (AAPL, "PaymentsForRepurchaseOfCommonStock", 2024, "0000320193-24-000123",
     "2024-09-30", 94949000000, -94949000000),
    (AAPL, "IncreaseDecreaseInAccountsReceivable", 2024, "0000320193-24-000123",
     "2024-09-30", 3788000000, -3788000000),
    (AAPL, "PaymentsToAcquirePropertyPlantAndEquipment", 2024, "0000320193-24-000123",
     "2024-09-30", 9447000000, -9447000000),
    # an increase/decrease item that is negative: inventories fell in FY2022, printed 1,484
    (AAPL, "IncreaseDecreaseInInventories", 2022, "0000320193-24-000123",
     "2022-09-30", -1484000000, 1484000000),
    # Vistra FY2025 income statement: "Interest expense and related charges" printed (1,179)
    (VST, "InterestExpenseNonoperating", 2025, "0001692819-26-000006",
     "2025-12-31", 1179000000, -1179000000),
    # a loss: Intel FY2024 net loss, a negative NetIncomeLoss on a line that does not negate
    (INTC, "NetIncomeLoss", 2024, "0000050863-25-000009", "2024-12-31",
     -18756000000, -18756000000),
]


def _fsds(fx, accession, tag):
    return next(x for x in fx.fsds if x["adsh"] == accession and x["tag"] == tag)


def _doc(fx, cik):
    return fx.companyfacts[cik]["document"]


@pytest.mark.criterion("C3")
def test_keys_keep_the_tagged_sign_and_refuse_amds_sign_flip(fx):
    # oracle: SEC companyfacts and the FSDS num/pre rows of the same filing (committed
    # excerpts, SHA-256 in each file), hand-checked against the statements: AMD's FY2021
    # financing cash flow is -1,895,000,000 in its FY2021 and FY2022 10-Ks and
    # +1,895,000,000 in its FY2023 10-K (fact 18008 of data/eval/calc_check/report.md)
    amd = _doc(fx, AMD)
    concept = "us-gaap:NetCashProvidedByUsedInFinancingActivities"
    entries = [e for e in amd["facts"]["us-gaap"][concept.split(":")[1]]["units"]["USD"]
               if e.get("start") == "2020-12-27" and e["end"] == "2021-12-25"]
    assert sorted((e["accn"], e["val"]) for e in entries) == [
        ("0000002488-22-000016", -1895000000), ("0000002488-23-000047", -1895000000),
        ("0000002488-24-000012", 1895000000)]
    with pytest.raises(KeyAmbiguousError, match="sign"):
        fiscal_year_key(amd, concept, "USD", 2021)
    # the later years have no flip: S1's latest-filed value, with its own sign
    assert fiscal_year_key(amd, concept, "USD", 2022)["value"] == -3264000000
    assert fiscal_year_key(amd, concept, "USD", 2023)["value"] == -1146000000

    for cik, tag, year, accession, ddate, tagged, printed in SIGNED:
        line = _fsds(fx, accession, tag)
        assert [p["negating"] for p in line["pre"]] == [printed != tagged]
        assert [n["value"] for n in line["num"] if n["ddate"] == ddate and n["qtrs"] == 4] == [
            pytest.approx(tagged, abs=0.5)]  # abs=0.5: FSDS writes the integer as a float
        key = fiscal_year_key(_doc(fx, cik), f"us-gaap:{tag}", "USD", year)
        assert key["value"] == tagged  # exact: companyfacts values are integers
        if printed != tagged:
            assert key["value"] != printed


@st.composite
def _period_entries(draw):
    """One period's entries from distinct filings: v, -v and other values (no other pair)."""
    v = draw(st.integers(min_value=1, max_value=10**12))
    others = draw(st.lists(st.integers(min_value=-10**12, max_value=10**12)
                           .filter(lambda x: abs(x) != v),
                           min_size=1, max_size=4, unique_by=abs))
    flip = [v, -v] + draw(st.lists(st.sampled_from([v, -v]), max_size=2))
    values = draw(st.permutations(flip + others))
    entries = []
    for i, val in enumerate(values):
        entries.append({"start": "2024-01-01", "end": "2024-12-31", "val": val,
                        "accn": f"0000000001-25-{i + 1:06d}", "fy": 2024 + i, "fp": "FY",
                        "form": "10-K", "filed": f"{2025 + i}-02-01"})
    return v, entries


def _cf(entries):
    return {"cik": 1, "entityName": "SYNTHETIC", "facts": {"us-gaap": {"Liabilities": {
        "label": "Liabilities", "units": {"USD": entries}}}}}


@pytest.mark.criterion("C3")
@given(_period_entries())
def test_any_sign_flip_is_ambiguous_and_keys_negate_with_their_entries(drawn):
    # oracle: R3 written out — v and -v among a period's entries -> KeyAmbiguousError
    # ("sign"); otherwise the latest filed entry's value, so negating every entry negates it
    v, entries = drawn
    with pytest.raises(KeyAmbiguousError, match="sign"):
        latest_filed_key(_cf(entries), "us-gaap:Liabilities", "USD", "2024-01-01",
                         "2024-12-31")

    kept = [e for e in entries if abs(e["val"]) != v]
    latest = max(kept, key=lambda e: e["filed"])
    key = latest_filed_key(_cf(kept), "us-gaap:Liabilities", "USD", "2024-01-01",
                           "2024-12-31")
    assert key["value"] == latest["val"]  # exact: integers
    negated = [dict(e, val=-e["val"]) for e in kept]
    assert latest_filed_key(_cf(negated), "us-gaap:Liabilities", "USD", "2024-01-01",
                            "2024-12-31")["value"] == -latest["val"]
