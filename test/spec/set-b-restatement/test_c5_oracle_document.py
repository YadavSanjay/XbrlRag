"""C5: the long-context oracle document is the cited filing (requirement 3).

Under S1, NVDA's FY2023 diluted EPS is cited from the FY2025 10-K 0001045810-25-000023 (0.17,
after the split; restatement-s1 C1), while the 10-K labelled FY2023 (0001045810-23-000017)
states 1.74. AMD's receivables at 2025-12-27 are cited from the Q2 FY2026 10-Q
0000002488-26-000123; 0000002488-26-000021 is AMD's Part III-only 10-K/A (superseded by its
10-K). Forms are EDGAR's (``dei.json``). Store keys follow the normalized tree's layout.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.systems.long_context import ANNUAL_FORMS, cited_filing_key

KEY_FY2023 = "NVDA/2023/10-K/canonical.htm"
KEY_FY2025 = "NVDA/2025/10-K/canonical.htm"
MARK_FY2023 = "ORIGINAL-FY2023-10K diluted EPS 1.74"
MARK_FY2025 = "RESTATING-FY2025-10K diluted EPS 0.17"


def _filing(accn, form, canonical, key):
    return {"accession_no": accn, "form_type": form, "canonical": canonical, "key": key}


def _nvda_ids(kit):
    """The fact ids of NVDA's FY2023 diluted EPS in the restating FY2025 10-K."""
    ids = [r["fact_id"] for r in kit.rows_of(kit.store_rows("NVDA"), "EarningsPerShareDiluted",
                                             "0001045810-25-000023", "2023-01-29")]
    assert ids
    return ids


def _filings(kit):
    """fact id -> filing record, for the facts the tests cite."""
    out = {i: _filing("0001045810-25-000023", "10-K", True, KEY_FY2025) for i in _nvda_ids(kit)}
    amd = kit.store_rows("AMD")
    for r in kit.rows_of(amd, "AccountsReceivableNetCurrent", "0000002488-26-000123",
                         "2025-12-27"):
        out[r["fact_id"]] = _filing("0000002488-26-000123", "10-Q", True, "AMD/2026/10-Q/c.htm")
    for r in kit.rows_of(amd, "EntityCommonStockSharesOutstanding", "0000002488-26-000021"):
        out[r["fact_id"]] = _filing("0000002488-26-000021", "10-K/A", False,
                                    "AMD/2025/10-K_A/canonical.htm")
    for r in kit.rows_of(kit.store_rows("NVDA"), "EarningsPerShareDiluted",
                         "0001045810-23-000017", "2023-01-29"):
        out[r["fact_id"]] = _filing("0001045810-23-000017", "10-K", True, KEY_FY2023)
    return out


def _ids_of(filings, accn):
    ids = sorted(i for i, f in filings.items() if f["accession_no"] == accn)
    assert ids
    return ids


# ---------------------------------------------------------------------------------- points
@pytest.mark.criterion("C5")
def test_the_key_is_the_one_canonical_annual_filing_holding_the_facts(kit):
    # oracle: S1 by hand (NVDA FY2023 EPS cited from 0001045810-25-000023, a canonical 10-K) and
    # requirement 3's conditions for every other set: two filings, a 10-Q, a non-canonical
    # 10-K/A, an unknown id and no ids give no key
    filings = _filings(kit)
    nvda = _ids_of(filings, "0001045810-25-000023")
    assert cited_filing_key(nvda, filings) == KEY_FY2025
    original = _ids_of(filings, "0001045810-23-000017")
    assert cited_filing_key(original, filings) == KEY_FY2023
    assert cited_filing_key(nvda + original, filings) is None
    assert cited_filing_key(_ids_of(filings, "0000002488-26-000123"), filings) is None
    assert cited_filing_key(_ids_of(filings, "0000002488-26-000021"), filings) is None
    assert cited_filing_key(nvda + [999_999_999], filings) is None
    assert cited_filing_key([], filings) is None
    keyless = {i: dict(f, key=None) for i, f in filings.items()}
    assert cited_filing_key(nvda, keyless) is None


@pytest.mark.criterion("C5")
def test_the_arm_reads_the_cited_filing_and_falls_back_to_the_fy_filing(kit):
    # oracle: S1 by hand — the key's 0.17 is in the FY2025 10-K, so the oracle document for the
    # FY2023 case is that filing; a case citing no facts keeps the FY filing (canonical_key)
    filings = _filings(kit)
    nvda = _ids_of(filings, "0001045810-25-000023")
    arm, adapter, resolver, store = kit.make_arm(
        {("NVDA", 2023): KEY_FY2023}, filings,
        {KEY_FY2023: kit.xhtml(MARK_FY2023), KEY_FY2025: kit.xhtml(MARK_FY2025)})
    answer = arm.answer(kit.make_case("nvda-eps-23", "NVDA", 2023, nvda))
    assert answer.error is None, answer.error
    assert store.reads == [KEY_FY2025]
    assert MARK_FY2025 in adapter.user_text(0)
    assert MARK_FY2023 not in adapter.user_text(0)
    assert ("fact_filings", tuple(nvda)) in resolver.calls

    plain = arm.answer(kit.make_case("nvda-eps-23-uncited", "NVDA", 2023, []))
    assert plain.error is None, plain.error
    assert store.reads == [KEY_FY2025, KEY_FY2023]
    assert MARK_FY2023 in adapter.user_text(1)

    mixed = _ids_of(filings, "0001045810-23-000017") + nvda
    arm.answer(kit.make_case("nvda-eps-23-mixed", "NVDA", 2023, mixed))
    assert store.reads == [KEY_FY2025, KEY_FY2023, KEY_FY2023]


# ------------------------------------------------------------------------------ properties
@pytest.mark.criterion("C5")
@given(ids=st.lists(st.integers(min_value=1, max_value=10_000), min_size=1, max_size=8),
       form=st.sampled_from(ANNUAL_FORMS), data=st.data())
def test_order_and_repetition_never_matter_and_a_second_filing_voids_the_key(ids, form, data):
    # oracle: requirement 3 by hand — facts all in one canonical annual filing give its key,
    # in any order and with repeats; one fact of another filing makes it None
    filings = {i: _filing("0000000001-25-000001", form, True, "X/2025/F/canonical.htm")
               for i in ids}
    shuffled = data.draw(st.permutations(ids + ids[: len(ids) // 2]))
    assert cited_filing_key(shuffled, filings) == "X/2025/F/canonical.htm"
    other = 20_000
    filings[other] = _filing("0000000001-26-000002", form, True, "X/2026/F/canonical.htm")
    at = data.draw(st.integers(min_value=0, max_value=len(shuffled)))
    assert cited_filing_key(shuffled[:at] + [other] + shuffled[at:], filings) is None


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(fy=st.sampled_from([2022, 2023, 2024, 2025]))
def test_the_arm_reads_the_cited_filing_whatever_the_fiscal_label(kit, fy):
    # oracle: metamorphic — the document follows the citation, not the case's fiscal-year label:
    # the same cited facts give the same document for any labelled year that has its own filing
    filings = _filings(kit)
    nvda = _ids_of(filings, "0001045810-25-000023")
    own = f"NVDA/{fy}/10-K/canonical.htm"
    objects = {KEY_FY2025: kit.xhtml(MARK_FY2025)}
    objects.setdefault(own, kit.xhtml(f"OWN-FY{fy}"))
    arm, adapter, _resolver, store = kit.make_arm({("NVDA", fy): own}, filings, objects)
    answer = arm.answer(kit.make_case(f"nvda-{fy}", "NVDA", fy, nvda))
    assert answer.error is None, answer.error
    assert store.reads == [KEY_FY2025]
    assert MARK_FY2025 in adapter.user_text(0)
