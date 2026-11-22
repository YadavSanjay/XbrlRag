"""C3: TSM in TWD (docs/specs/suite-templates-s1.md, requirement 1).

TSM's 20-Fs tag net income in TWD for every year and, as a convenience translation, in USD
for the current year only. The pick answers in the fy filing's reporting currency.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.suites.sql_derived import s1_lookup_pick

TOL = 5e4  # half of TWD 0.1M: TSM tags in TWD thousands, values quoted to 0.1M

# fy, TWD value (FSDS), the answering accession
POINTS = [
    (2022, 993_294_700_000.0, "0001193125-25-083423"),
    (2023, 851_027_700_000.0, "0001628280-26-025362"),
    (2024, 1_157_523_900_000.0, "0001628280-26-025362"),
]


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("case", POINTS, ids=lambda c: f"FY{c[0]}")
def test_the_pick_is_the_twd_restatement(kit, case):
    # oracle: FSDS num (TWD and USD rows of each 20-F); the 2026 20-F covers 2023-2025 only,
    # so FY2022's latest report is the 2025 20-F
    fy, value, accession = case
    rows = kit.store_rows("TSM", ["NetIncome"])
    assert {r["unit"] for r in rows} == {"TWD", "USD"}
    pick = s1_lookup_pick(rows, fy)
    assert pick is not None
    assert pick["unit"] == "TWD"
    assert pick["accession_no"] == accession
    assert pick["value"] == pytest.approx(value, abs=TOL)  # quoted to TWD 0.1M
    assert kit.iso(pick["period_end"]) == kit.own_end("TSM", fy)


@pytest.mark.criterion("C3")
def test_never_the_usd_convenience_translation(kit):
    # oracle: FSDS — 0001193125-24-099840 tags FY2023 net income USD 27,793.2M as well
    pick = s1_lookup_pick(kit.store_rows("TSM", ["NetIncome"]), 2023)
    assert pick is not None
    assert pick["value"] != pytest.approx(27_793_200_000.0, abs=TOL)
    assert pick["unit"] != "USD"


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@settings(max_examples=60)
@given(st.sampled_from([2022, 2023, 2024, 2025]), st.data())
def test_usd_rows_never_change_the_pick(kit, fy, data):
    # oracle: the reporting currency is the fy filing's TWD; USD rows are a translation
    rows = kit.store_rows("TSM", ["NetIncome"])
    twd = [r for r in rows if r["unit"] == "TWD"]
    usd = [r for r in rows if r["unit"] == "USD"]
    base = s1_lookup_pick(twd, fy)
    assert base is not None and base["unit"] == "TWD"
    keep = data.draw(st.lists(st.sampled_from(usd), unique_by=lambda r: r["fact_id"]))
    pick = s1_lookup_pick([*twd, *keep], fy)
    assert pick is not None
    assert pick["fact_id"] == base["fact_id"]
