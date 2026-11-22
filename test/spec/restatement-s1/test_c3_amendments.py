"""C3: a Part III-only 10-K/A is never the key or the answer; a full-statement one wins.

docs/specs/restatement-s1.md, requirement 6. The four corpus amendments carry only the cover
facts (companyfacts lists 2 entries each, FSDS has none); TSLA's 2012 10-K/A re-filed the
statements. The restating amendment of the scenario is synthetic (see conftest).
"""

from __future__ import annotations

import datetime as dt

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.core import carries_financial_data, fiscal_year_key, latest_filed_key
from src.retriever.xbrl.restatement import current_column, latest_restatement

SHARES = "dei:EntityCommonStockSharesOutstanding"
FLOAT = "dei:EntityPublicFloat"

# ticker, cover concept, unit, end, the 10-K the key must cite (the 10-K/A it must not)
COVER = [
    ("AMD", SHARES, "shares", "2026-01-30", "0000002488-26-000018"),
    ("AMD", FLOAT, "USD", "2024-06-28", "0000002488-26-000018"),
    ("MRK", SHARES, "shares", "2024-01-31", "0001628280-24-006850"),
    ("MRK", FLOAT, "USD", "2023-06-30", "0001628280-24-006850"),
    ("TSLA", SHARES, "shares", "2025-01-22", "0001628280-25-003063"),
    ("TSLA", FLOAT, "USD", "2024-06-28", "0001628280-25-003063"),
    ("TSLA", SHARES, "shares", "2026-01-23", "0001628280-26-003952"),
    ("TSLA", FLOAT, "USD", "2025-06-30", "0001628280-26-003952"),
]
# ticker, fiscal year, the amendment, the 10-K it amends
AMENDED = [
    ("AMD", 2025, "0000002488-26-000021", "0000002488-26-000018"),
    ("MRK", 2023, "0001193125-24-093194", "0001628280-24-006850"),
    ("TSLA", 2024, "0001104659-25-042659", "0001628280-25-003063"),
    ("TSLA", 2025, "0001104659-26-053166", "0001628280-26-003952"),
]


# ---------------------------------------------------------------------------------- points
@pytest.mark.criterion("C3")
def test_only_full_statement_filings_carry_primary_statements(kit):
    # oracle: companyfacts — each Part III amendment has only the two dei cover entries; the
    # 10-Ks and TSLA's 2012 10-K/A (538 entries) also tag us-gaap Assets and NetIncomeLoss
    for ticker, _fy, amendment, tenk in AMENDED:
        doc = kit.companyfacts(ticker)
        assert carries_financial_data(doc, amendment) is False, amendment
        assert carries_financial_data(doc, tenk) is True, tenk
    tsla = kit.companyfacts("TSLA")
    assert carries_financial_data(tsla, kit.TSLA_2012_10KA) is True
    assert carries_financial_data(tsla, kit.TSLA_2012_10K) is True
    assert carries_financial_data(tsla, "0009999999-26-000009") is False   # no entries at all


@pytest.mark.criterion("C3")
@given(amended=st.sampled_from(AMENDED), tag=st.sampled_from(["Assets", "NetIncomeLoss"]),
       nil=st.booleans())
def test_one_non_cover_entry_makes_an_amendment_financial(kit, amended, tag, nil):
    # oracle: requirement 6's definition — one valued us-gaap entry of the amendment is
    # enough; a nil one is not
    ticker, _fy, amendment, tenk = amended
    doc = kit.companyfacts(ticker)
    assert carries_financial_data(doc, amendment) is False
    es = next(iter(doc["facts"]["us-gaap"][tag]["units"].values()))
    src = next(e for e in es if e["accn"] == tenk)
    es.append(dict(src, accn=amendment, form="10-K/A", val=None if nil else src["val"]))
    assert carries_financial_data(doc, amendment) is (not nil)


@pytest.mark.criterion("C3")
def test_the_key_cites_the_10k_never_a_part_iii_amendment(kit):
    # oracle: companyfacts entries and their EDGAR forms; requirement 6 — the 10-K/A's entries
    # are as if absent, so each of the 8 cover periods answers with its 10-K's accession and
    # value (today 7 of 8 cite the amendment)
    cited = {}
    for ticker, concept, unit, end, _tenk in COVER:
        key = latest_filed_key(kit.companyfacts(ticker), concept, unit, None, end)
        cited[(ticker, concept, end)] = (key["accession"], key["form"], key["value"])
    expected = {
        (ticker, concept, end): (tenk, "10-K",
                                 pytest.approx(kit.entry_value(ticker, concept.split(":")[1],
                                                               tenk, end, unit), rel=1e-12))
        for ticker, concept, unit, end, tenk in COVER
    }
    assert cited == expected


@pytest.mark.criterion("C3")
def test_a_restating_amendment_wins_and_the_original_stays_available(kit):
    # oracle: the synthetic scenario's construction — the 10-K/A re-files TSLA FY2025 net income
    # changed by -$100M after the Part III 10-K/A; S1 takes it, as originally reported is the
    # 10-K's companyfacts value
    doc = kit.companyfacts("TSLA", scenario="restating_10ka")
    tenk = "0001628280-26-003952"
    original = kit.entry_value("TSLA", "NetIncomeLoss", tenk, "2025-12-31")
    key = fiscal_year_key(doc, "us-gaap:NetIncomeLoss", "USD", 2025)
    assert key["accession"] == kit.SYNTH_10KA
    assert key["value"] == pytest.approx(original + kit.SYNTH_NET_INCOME_DELTA, abs=0.5)
    cover = latest_filed_key(doc, SHARES, "shares", None, "2026-01-23")
    assert cover["accession"] == kit.SYNTH_10KA   # never the Part III 0001104659-26-053166
    rows = kit.store_rows("TSLA", ["NetIncomeLoss"], scenario="restating_10ka")
    answer = latest_restatement(current_column(rows, 2025), rows)
    assert kit.accessions(answer) == {kit.SYNTH_10KA}
    assert [v for v in kit.values(answer)] == [
        pytest.approx(original + kit.SYNTH_NET_INCOME_DELTA, abs=0.5)]
    as_filed = current_column(rows, 2025, as_originally_reported=True)
    assert kit.accessions(as_filed) == {tenk}
    assert [v for v in kit.values(as_filed)] == [pytest.approx(original, abs=0.5)]
    looked = kit.run_lookup(kit.store_rows("TSLA", scenario="restating_10ka"), "TSLA", 2025,
                            "NetIncome", registry=kit.registry_rows(["TSLA"], "restating_10ka"))
    assert looked.status.value == "OK", looked.resolution_notes
    assert looked.citation.accession_no == kit.SYNTH_10KA


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("amended", AMENDED, ids=[f"{a[0]}-FY{a[1]}" for a in AMENDED])
def test_the_product_never_answers_from_a_part_iii_amendment(kit, amended):
    # oracle: annual-amendment-handling C3 — the amendment is superseded_by its 10-K, so the
    # cover-page share count of the year is the 10-K's (companyfacts value)
    ticker, fy, amendment, tenk = amended
    rows = kit.store_rows(ticker, ["EntityCommonStockSharesOutstanding"])
    answer = latest_restatement(current_column(rows, fy), rows)
    assert kit.accessions(answer) == {tenk}
    looked = kit.run_lookup(kit.store_rows(ticker), ticker, fy, "SharesOutstanding")
    assert looked.status.value == "OK", looked.resolution_notes
    assert looked.citation.accession_no == tenk
    assert looked.citation.accession_no != amendment


# ------------------------------------------------------------------------------ properties
def _part_iii_copy(doc: dict, tenk: str, accn: str, filed: str) -> None:
    """Adds a Part III-only amendment: copies of the 10-K's two cover entries."""
    for tag in ("EntityCommonStockSharesOutstanding", "EntityPublicFloat"):
        for es in doc["facts"]["dei"][tag]["units"].values():
            es.extend([dict(e, accn=accn, form="10-K/A", filed=filed)
                       for e in list(es) if e["accn"] == tenk])


@pytest.mark.criterion("C3")
@given(cover=st.sampled_from(COVER), days=st.integers(min_value=0, max_value=400))
def test_a_part_iii_amendment_filed_any_day_never_changes_a_key(kit, cover, days):
    # oracle: metamorphic — requirement 6: one more Part III-only amendment, filed on the
    # 10-K's day or up to 400 days later, leaves the cover key on the 10-K
    ticker, concept, unit, end, tenk = cover
    doc = kit.companyfacts(ticker)
    tenk_filed = next(e["filed"] for e in kit.entries(doc, concept.split(":")[1], unit, "dei")
                      if e["accn"] == tenk)
    filed = (dt.date.fromisoformat(tenk_filed) + dt.timedelta(days=days)).isoformat()
    _part_iii_copy(doc, tenk, "0009999999-26-000001", filed)
    key = latest_filed_key(doc, concept, unit, None, end)
    assert key["accession"] == tenk


@pytest.mark.criterion("C3")
@given(days=st.integers(min_value=1, max_value=300),
       delta=st.integers(min_value=-5_000, max_value=5_000).filter(lambda d: d != 0))
def test_a_full_statement_amendment_filed_last_is_always_cited(kit, days, delta):
    # oracle: metamorphic — S1 with requirement 6: a 10-K/A that re-files TSLA FY2025 Assets
    # and NetIncomeLoss (net income changed by delta $M) after every other filing becomes the
    # key's and the product's accession, and the original stays the 10-K's
    tenk = "0001628280-26-003952"
    accn = "0009999999-26-000002"
    filed = (dt.date(2026, 7, 23) + dt.timedelta(days=days)).isoformat()  # after the last 10-Q
    doc = kit.companyfacts("TSLA")
    for tag in ("Assets", "NetIncomeLoss"):
        for es in doc["facts"]["us-gaap"][tag]["units"].values():
            es.extend([dict(e, accn=accn, form="10-K/A", filed=filed,
                            val=e["val"] + delta * 1_000_000
                            if tag == "NetIncomeLoss" and e.get("start") == "2025-01-01"
                            and e["end"] == "2025-12-31" else e["val"])
                       for e in list(es) if e["accn"] == tenk])
    key = fiscal_year_key(doc, "us-gaap:NetIncomeLoss", "USD", 2025)
    assert key["accession"] == accn
    rows = kit.store_rows("TSLA", ["NetIncomeLoss", "Assets"])
    restated = []
    for r in rows:
        if r["accession_no"] == tenk:
            bump = (kit.local(r["concept"]) == "NetIncomeLoss"
                    and r["period_start"] == "2025-01-01" and r["period_end"] == "2026-01-01")
            restated.append(dict(r, accession_no=accn, filed_date=filed, is_amendment=True,
                                 form_type="10-K/A", fact_id=r["fact_id"] + 7_000_000,
                                 value=r["value"] + delta * 1_000_000 if bump else r["value"]))
            r["is_canonical_filing"] = False
    answer = latest_restatement(current_column(rows + restated, 2025), rows + restated)
    assert kit.accessions(kit.only(answer, "NetIncomeLoss")) == {accn}
    original = current_column(rows + restated, 2025, as_originally_reported=True)
    assert kit.accessions(original) == {tenk}


@pytest.mark.criterion("C3")
@given(amended=st.sampled_from(AMENDED), data=st.data())
def test_part_iii_rows_never_change_the_product_answer(kit, amended, data):
    # oracle: metamorphic — rows of a non-canonical Part III amendment, in any order and with
    # any values, leave the year's answer on the 10-K
    ticker, fy, amendment, tenk = amended
    rows = kit.store_rows(ticker, ["EntityCommonStockSharesOutstanding", "NetIncomeLoss"])
    scale = data.draw(st.integers(min_value=2, max_value=9))
    noisy = [dict(r, value=r["value"] * scale) if r["accession_no"] == amendment else r
             for r in rows]
    noisy = data.draw(st.permutations(noisy))
    answer = latest_restatement(current_column(noisy, fy), noisy)
    assert answer, "the year has an answer"
    assert amendment not in kit.accessions(answer)
    assert kit.accessions(kit.only(answer, "EntityCommonStockSharesOutstanding")) == {tenk}
