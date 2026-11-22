"""C3: the TSM lookups of the dev keys answer in TWD (docs/specs/lookup-reporting-currency.md).

Expected values are the frozen v2 dev keys' inputs (SEC companyfacts, unit TWD, decimals -5),
quoted in the spec's C3 table; the store rows are the fixture, behind a fake database.
"""

from __future__ import annotations

import pytest

# decimals -5: the key is exact to 100,000, so half of that either way.
HALF_QUANTUM = 50_000

# family, fy, key value (TWD), key case
KEY_CASES = [
    ("CashAndEquivalents", 2022, 1_342_814_100_000, "int-1f34faa1da08"),
    ("RetainedEarnings", 2022, 2_594_891_200_000, "int-37433c7a5c06"),
    ("PropertyPlantAndEquipment", 2024, 3_234_980_100_000, "int-8fe6a6b1e355"),
    ("GrossProfit", 2022, 1_348_354_800_000, "int-5c685209cc7a"),
    ("GrossProfit", 2023, 1_175_110_600_000, "int-5c685209cc7a"),
    ("GrossProfit", 2024, 1_624_353_600_000, "int-5c685209cc7a"),
    ("ResearchAndDevelopment", 2022, 163_262_200_000, "int-e4e6c46945f0"),
    ("ResearchAndDevelopment", 2023, 182_370_200_000, "int-a967bc81f5f3"),
    ("Revenue", 2022, 2_263_891_300_000, "int-e4e6c46945f0"),
    ("Revenue", 2023, 2_161_735_800_000, "int-a967bc81f5f3"),
]
IDS = [f"{c[0]}-FY{c[1]}" for c in KEY_CASES]


def _fact(kit, fact_id):
    rows = [r for r in kit.tsm_rows() if r["fact_id"] == fact_id]
    assert len(rows) == 1, fact_id
    return rows[0]


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("case", KEY_CASES, ids=IDS)
def test_lookup_answers_the_key_in_twd(kit, case):
    # oracle: the frozen v2 dev key named in the case (SEC companyfacts, unit TWD); S6 — a
    # single-filer answer is in the reporting currency
    family, fy, value, _key = case
    result = kit.run_tsm(fy, family)
    assert result.status.value == "OK", result.resolution_notes
    assert result.unit == "TWD"
    assert result.value == pytest.approx(value, abs=HALF_QUANTUM)
    cited = _fact(kit, result.citation.fact_id)
    assert cited["unit"] == "TWD"
    assert cited["value"] == pytest.approx(value, abs=HALF_QUANTUM)
    notes = " ".join(result.resolution_notes)
    assert "TWD" in notes and "reporting currency" in notes and "USD" in notes


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@pytest.mark.parametrize("case", KEY_CASES, ids=IDS)
def test_the_convenience_rows_never_change_the_answer(kit, case):
    # oracle: Requirement 3: the USD translation is set aside, so deleting it changes neither
    # value, unit nor citation; the key value (as above) must hold both ways
    family, fy, value, _key = case
    with_usd = kit.run_tsm(fy, family)
    without = kit.run_tsm(fy, family, rows=[r for r in kit.tsm_rows() if r["unit"] != "USD"])
    assert without.status.value == "OK", without.resolution_notes
    assert with_usd.status.value == "OK", with_usd.resolution_notes
    assert with_usd.value == pytest.approx(without.value, abs=0.5)  # same fact, same value
    assert with_usd.value == pytest.approx(value, abs=HALF_QUANTUM)
    assert with_usd.unit == without.unit == "TWD"
    assert with_usd.citation.fact_id == without.citation.fact_id


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@pytest.mark.parametrize("case", KEY_CASES[:1] + KEY_CASES[3:4], ids=IDS[:1] + IDS[3:4])
def test_a_requested_usd_still_gets_the_translation(kit, case):
    # oracle: Requirement 3 — a requested unit is handled as today; the USD value is the
    # fixture's USD row at the answer's period, and the unrequested answer is the TWD key
    family, fy, value, _key = case
    twd = kit.run_tsm(fy, family)
    assert twd.status.value == "OK", twd.resolution_notes
    assert twd.unit == "TWD"
    assert twd.value == pytest.approx(value, abs=HALF_QUANTUM)
    usd = kit.run_tsm(fy, family, unit="USD")
    assert usd.status.value == "OK", usd.resolution_notes
    assert usd.unit == "USD"
    cited = _fact(kit, usd.citation.fact_id)
    assert cited["unit"] == "USD"
    assert cited["period_end"] == _fact(kit, twd.citation.fact_id)["period_end"]
    assert usd.value < twd.value / 10   # a translation at ~30 TWD per USD, never the TWD figure
