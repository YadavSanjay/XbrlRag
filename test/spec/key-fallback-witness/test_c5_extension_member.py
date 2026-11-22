"""C5: a screen member whose own report presents the line on an extension element is keyed
from a later report's standard line.

Oracle: requirement 3 of docs/specs/key-fallback-witness.md and the conftest rows: FEXT's own
FY2022 and FY2023 10-Ks present capex on an extension element (976, 1,833); its FY2024 10-K
presents both periods on us-gaap:PaymentsToAcquireProductiveAssets with the same values, which
companyfacts carries from that 10-K. FRST restates FY2022 to 990 there; FDEF's later line is
"... and businesses", which the Capex rule excludes.
"""

from __future__ import annotations

import pytest

# tolerance: whole-million fixture amounts; rel 1e-12 absorbs only binary floating point.
REL = 1e-12
PPA = "us-gaap:PaymentsToAcquireProductiveAssets"


def _member(w, kit, ticker, year):
    return w.unkeyed_member(kit.companyfacts(ticker), kit.labels(ticker), kit.CIK[ticker],
                            "Capex", year, "USD")


@pytest.mark.criterion("C5")
def test_fext_fy2022_capex_is_keyed_from_the_fy2024_10k(kit):
    # oracle: FEXT FY2022 own line 976 (extension, FY2022 10-K); FY2024 10-K CF line 1,
    # PaymentsToAcquireProductiveAssets, FY2022 column 976; companyfacts entry 976 from the
    # FY2024 10-K for 2022-01-01..2022-12-31
    w = kit.witness()
    reason, key = _member(w, kit, "FEXT", 2022)
    assert reason is None
    assert (key["concept"], key["accession"], key["fiscal_year"], key["form"]) == (
        PPA, kit.FE24, 2022, "10-K")
    assert (key["period_start"], key["period_end"]) == kit.period(2022)
    assert key["value"] == pytest.approx(976e6, rel=REL)
    m = w.manifest()
    assert (m["n"], m["by_status"]["agree"]) == (1, 1)
    rec = next(iter(w.witnesses.values()))
    assert (rec["adsh"], rec["key_accession"], rec["family"], rec["fiscal_year"]) == (
        kit.FE24, kit.FE24, "Capex", 2022)


@pytest.mark.criterion("C5")
def test_other_years_and_filers_follow_the_value_guard_and_the_rule(kit):
    # oracle: FEXT FY2023 1,833 = 1,833 (keyed); FRST FY2022 own 976 against the later 990
    # (not keyed), FRST FY2023 1,833 = 1,833 (keyed); FDEF's later line is excluded by the rule
    w = kit.witness()
    reason, key = _member(w, kit, "FEXT", 2023)
    assert reason is None and key["value"] == pytest.approx(1833e6, rel=REL)
    assert (key["accession"], key["period_end"]) == (kit.FE24, "2023-12-31")
    assert _member(w, kit, "FRST", 2022) == ("presented_line_not_keyed", None)
    reason, key = _member(w, kit, "FRST", 2023)
    assert reason is None and key["value"] == pytest.approx(1833e6, rel=REL)
    assert key["accession"] == kit.FR24
    assert _member(w, kit, "FDEF", 2022) == ("presented_line_not_keyed", None)
    assert _member(w, kit, "FDEF", 2023) == ("presented_line_not_keyed", None)


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
def test_without_the_later_quarter_the_member_stays_unkeyed(kit):
    # oracle: requirement 3 step 2: without the 2025q1 quarter no later report presents a
    # standard line, so the result is today's presented_line_not_keyed; restoring the quarter
    # keys the member again (976)
    short = kit.witness(quarters=("2023q1", "2024q1"))
    assert _member(short, kit, "FEXT", 2022) == ("presented_line_not_keyed", None)
    assert _member(short, kit, "FEXT", 2023) == ("presented_line_not_keyed", None)
    assert short.manifest()["n"] == 0
    full = kit.witness()
    reason, key = _member(full, kit, "FEXT", 2022)
    assert reason is None and key["value"] == pytest.approx(976e6, rel=REL)
