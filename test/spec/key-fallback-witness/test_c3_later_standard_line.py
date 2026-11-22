"""C3: ``later_standard_line`` finds the later filing that presents the period on one standard
element, and the value guard decides.

Oracle: requirement 3 of docs/specs/key-fallback-witness.md; the conftest rows (FEXT's FY2024
10-K presents FY2022 capex 976 on us-gaap:PaymentsToAcquireProductiveAssets, its FY2023 10-K
on an extension element; FRST's FY2024 10-K restates FY2022 to 990; FDEF's later line is
"... and businesses", which the Capex rule excludes) and, for the property, the decision rule
restated in the test over generated filings.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.statement_witness import later_standard_line

# tolerance: whole-million fixture amounts; rel 1e-12 absorbs only binary floating point.
REL = 1e-12
PPA = "us-gaap:PaymentsToAcquireProductiveAssets"
ABSENT = "0000810002-26-000099"  # a made-up later report in no table


def _call(kit, adshs, own, year=2022):
    e = kit.env()
    start, end = kit.period(year)
    return later_standard_line(kit.all_tables(), e.rules["Capex"], adshs, start, end, "USD",
                               float(own * kit.M))


@pytest.mark.criterion("C3")
def test_the_later_standard_line_is_found_in_any_order(kit):
    # oracle: FEXT FY2024 10-K, CF report 4 line 1, PaymentsToAcquireProductiveAssets, FY2022
    # column 976; the FY2023 10-K's line is an extension and is passed over, as is a filing
    # the tables do not hold
    want = {"adsh": kit.FE24, "concept": PPA, "value": pytest.approx(976e6, rel=REL)}
    assert _call(kit, [kit.FE24, kit.FE23], 976) == want
    assert _call(kit, [kit.FE23, kit.FE24], 976) == want
    assert _call(kit, [ABSENT, kit.FE23, kit.FE24], 976) == want
    assert _call(kit, [kit.FE24], 1833, year=2023) == {
        "adsh": kit.FE24, "concept": PPA, "value": pytest.approx(1833e6, rel=REL)}


@pytest.mark.criterion("C3")
def test_a_different_value_a_rejected_line_or_no_standard_line_gives_none(kit):
    # oracle: own 975 against the later 976; FRST's later 990 against its own 976 (FY2023:
    # 1,833 both); FDEF's later "Purchases of property and equipment and businesses" is
    # excluded by the rule; FEXT's FY2023 10-K alone holds only the extension line
    assert _call(kit, [kit.FE24, kit.FE23], 975) is None
    assert _call(kit, [kit.FR24, kit.FR23], 976) is None
    assert _call(kit, [kit.FR24], 1833, year=2023) == {
        "adsh": kit.FR24, "concept": PPA, "value": pytest.approx(1833e6, rel=REL)}
    assert _call(kit, [kit.FD24, kit.FD23], 700) is None
    assert _call(kit, [kit.FE23], 976) is None
    assert _call(kit, [], 976) is None


KINDS = ("absent", "noline", "ext", "srt", "two", "us-gaap", "ifrs")
LABEL = "Purchases of property and equipment"


def _filing(i, kind, value):
    """FSDS rows of one generated filing of a made-up filer (CIK 810009)."""
    adsh = f"0000810009-25-{i:06d}"
    sub = [{"adsh": adsh, "cik": "810009", "name": "GEN", "form": "10-K", "period": "20241231",
            "fy": "2024", "fp": "FY", "filed": "20250301"}]
    specs = {"ext": [("CapexExt", adsh, LABEL)],
             "srt": [("CapexSrt", "srt/2024", LABEL)],
             "two": [("PaymentsToAcquireProductiveAssets", "us-gaap/2024", LABEL),
                     ("PaymentsToAcquirePropertyPlantAndEquipment", "us-gaap/2024",
                      "Purchases of property, plant and equipment")],
             "us-gaap": [("PaymentsToAcquireProductiveAssets", "us-gaap/2024", LABEL)],
             "ifrs": [("PurchaseOfPropertyPlantAndEquipment", "ifrs/2024", LABEL)]}.get(kind, [])
    pre = [{"adsh": adsh, "report": "4", "line": str(n), "stmt": "CF", "inpth": "0",
            "rfile": "H", "tag": tag, "version": version, "plabel": label, "negating": "0"}
           for n, (tag, version, label) in enumerate(specs, start=1)]
    num = [{"adsh": adsh, "tag": tag, "version": version, "ddate": "20221231", "qtrs": "4",
            "uom": "USD", "segments": "", "coreg": "", "value": f"{value * 1e6:.4f}",
            "footnote": ""} for tag, version, _label in specs]
    if kind == "absent":
        sub = []
    return adsh, {"sub": sub, "pre": pre, "num": num}


@pytest.mark.criterion("C3")
@given(filings=st.lists(st.tuples(st.sampled_from(KINDS), st.integers(1, 3)), max_size=6),
       own=st.integers(1, 3))
def test_the_first_single_standard_line_decides(kit, filings, own):
    # oracle: requirement 3 restated: pass over every filing that is absent, has no line, an
    # extension or srt line, or two lines; the first us-gaap or ifrs-full single line decides
    # (its value against own); None when none decides
    e = kit.env()
    tables = {"sub": [], "pre": [], "num": [], "tag": []}
    adshs = []
    for i, (kind, value) in enumerate(filings):
        adsh, rows = _filing(i, kind, value)
        adshs.append(adsh)
        for name in ("sub", "pre", "num"):
            tables[name].extend(rows[name])
    want = None
    for i, (kind, value) in enumerate(filings):
        if kind in ("us-gaap", "ifrs"):
            if value == own:
                concept = ("us-gaap:PaymentsToAcquireProductiveAssets" if kind == "us-gaap"
                           else "ifrs-full:PurchaseOfPropertyPlantAndEquipment")
                want = {"adsh": adshs[i], "concept": concept,
                        "value": pytest.approx(value * 1e6, rel=REL)}
            break
    got = later_standard_line(tables, e.rules["Capex"], adshs, "2022-01-01", "2022-12-31",
                              "USD", float(own * 1e6))
    assert got == want
