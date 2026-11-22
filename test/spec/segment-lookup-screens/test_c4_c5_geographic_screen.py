"""C4, C5: geographic screens give true exclusion reasons, and Europe / EMEA resolve to the
filer's own region members.

docs/specs/segment-lookup-screens.md, Requirements 4-5. `XbrlFactstoreScreen.screen` runs for
real over the fake store of conftest.py (the DB boundary).
"""

from __future__ import annotations

import pytest
import sls_rules as rules
from hypothesis import example, given
from hypothesis import strategies as st
from sls_store import synthetic_record

FY = 2024

CHINA_RANKED = [  # (ticker, value); each checked against its FSDS Geographical=CN line
    ("AAPL", 66_952_000_000), ("META", 18_350_000_000), ("QCOM", 17_826_000_000),
    ("AMD", 6_231_000_000), ("MRK", 5_494_000_000), ("MU", 3_045_000_000),
    ("LLY", 1_660_400_000),
]
CHINA_EXCLUDED = {
    "TSM": ("currency", {"currency": "TWD"}),
    "NVDA": ("different_member", {"labels": ["China Including Hong Kong [Member]"]}),
    "AVGO": ("different_member", {"labels": ["China (including Hong Kong) [Member]"]}),
    **{t: ("member_not_found", {}) for t in ("ADBE", "CRM", "GOOGL", "LMT", "ORCL")},
}
EUROPE_RANKED = [  # FSDS Geographical=Europe lines
    ("META", 38_361_000_000), ("CRM", 8_128_000_000), ("LMT", 7_716_000_000),
    ("LLY", 6_920_700_000), ("AMD", 1_625_000_000), ("MU", 818_000_000),
]
EMEA_RANKED = [  # FSDS Geographical=EMEA lines
    ("GOOGL", 102_127_000_000), ("MRK", 14_041_000_000), ("ORCL", 13_030_000_000),
    ("AVGO", 8_182_000_000), ("ADBE", 5_554_000_000),
]
EUROPE_EXCLUDED = {
    **{t: ("different_member", {"labels": ["EMEA [Member]"]})
       for t in ("ADBE", "AVGO", "GOOGL", "MRK", "ORCL")},
    "TSM": ("different_member", {"labels": ["Europe Middle East and Africa [member]"]}),
    **{t: ("member_not_found", {}) for t in ("AAPL", "NVDA", "QCOM")},
}
EMEA_EXCLUDED = {
    "TSM": ("currency", {"currency": "TWD"}),
    **{t: ("different_member", {"labels": ["Europe [Member]"]})
       for t in ("AMD", "CRM", "LLY", "LMT", "META", "MU")},
    **{t: ("member_not_found", {}) for t in ("AAPL", "NVDA", "QCOM")},
}


def _fsds_geo(fixture: dict, ticker: str, member: str) -> float:
    """The FY value of the fixture's FSDS line `Geographical=<member>;` for `ticker`'s
    FY2024 filing (its only extra dimension may be a concentration-risk pair, as QCOM's)."""
    accession = {r["ticker"]: r["accession_no"] for r in fixture["records"]
                 if r["fiscal_year"] == FY}[ticker]
    found = []
    for block in fixture["fsds"]:
        header = block["header"].split("\t")
        for line in block["lines"]:
            f = dict(zip(header, line.split("\t"), strict=True))
            segs = [s for s in f["segments"].split(";")
                    if s and not s.startswith("ConcentrationRisk")]
            if f["adsh"] == accession and f["qtrs"] == "4" and segs == [f"Geographical={member}"]:
                found.append((f["ddate"], float(f["value"])))
    assert found, (ticker, member)
    return max(found)[1]


def _ixbrl(fixture: dict, ix_id: str) -> dict:
    for excerpt in fixture["ixbrl"]:
        for fact in excerpt["facts"]:
            if fact["id"] == ix_id:
                return fact
    raise AssertionError(ix_id)


def _roster(records):
    return sorted({r["ticker"] for r in records})


def _exclusions(result) -> dict[str, dict]:
    return {e["ticker"]: e for e in (result.disclosure or {}).get("exclusions", [])}


def _check_exclusions(result, expected: dict) -> None:
    excl = _exclusions(result)
    assert set(excl) == set(expected), sorted(excl)
    for ticker, (reason, details) in expected.items():
        assert excl[ticker]["reason"] == reason, (ticker, excl[ticker])
        for key, value in details.items():
            assert excl[ticker][key] == value, (ticker, key, excl[ticker])


def _ranked(result) -> list[tuple[str, float]]:
    return [(r["ticker"], r["value"]) for r in result.rows]


@pytest.mark.criterion("C4")
def test_china_exclusions_carry_the_true_reason(run_screen, geographic_fixture):
    # oracle: FSDS num lines quoted in the fixture (CN values; NVDA's and AVGO's
    # ChinaIncludingHongKong members), the TSM 20-F iXBRL fact ixv-111673 (unitRef TWD,
    # country:CN), and the filers' member labels classified by hand per Requirement 4
    for ticker, value in CHINA_RANKED:
        assert _fsds_geo(geographic_fixture, ticker, "CN") == pytest.approx(
            value, rel=1e-12)  # the expected table is the FSDS value, exactly
    tsm = _ixbrl(geographic_fixture, "ixv-111673")
    assert 'unitRef="Unit_TWD"' in tsm["element"] and "country:CN" in tsm["context"]
    records = geographic_fixture["records"]
    result = run_screen(records, _roster(records), {"fy": FY, "dimension": {"geography": "China"}})
    assert [t for t, _ in _ranked(result)] == [t for t, _ in CHINA_RANKED]
    assert [v for _, v in _ranked(result)] == pytest.approx(
        [float(v) for _, v in CHINA_RANKED], rel=1e-12)  # exact stored integers
    _check_exclusions(result, CHINA_EXCLUDED)
    assert all(e["ticker"] != "NVDA" for e in result.excluded)  # not period_not_filed


@pytest.mark.criterion("C5")
def test_europe_and_emea_resolve_by_member_label(run_screen, geographic_fixture):
    # oracle: FSDS num lines quoted in the fixture (Geographical=Europe / EMEA), the TSM
    # 20-F iXBRL fact ixv-111679 (EMEA, unitRef TWD), the filers' member labels and the
    # region table of Requirement 5
    for ticker, value in EUROPE_RANKED:
        assert _fsds_geo(geographic_fixture, ticker, "Europe") == pytest.approx(
            value, rel=1e-12)  # the expected table is the FSDS value, exactly
    for ticker, value in EMEA_RANKED:
        assert _fsds_geo(geographic_fixture, ticker, "EMEA") == pytest.approx(
            value, rel=1e-12)  # the expected table is the FSDS value, exactly
    tsm = _ixbrl(geographic_fixture, "ixv-111679")
    assert 'unitRef="Unit_TWD"' in tsm["element"] and "EuropeMiddleEastAndAfrica" in tsm["context"]
    records = geographic_fixture["records"]
    roster = _roster(records)
    for request, ranked, excluded in (("Europe", EUROPE_RANKED, EUROPE_EXCLUDED),
                                      ("EMEA", EMEA_RANKED, EMEA_EXCLUDED)):
        result = run_screen(records, roster, {"fy": FY, "dimension": {"geography": request}})
        assert [t for t, _ in _ranked(result)] == [t for t, _ in ranked], request
        assert [v for _, v in _ranked(result)] == pytest.approx(
            [float(v) for _, v in ranked], rel=1e-12)  # exact stored integers
        assert all(r.get("member_match") == "exact" for r in result.rows)
        _check_exclusions(result, excluded)


# ------------------------------------------------------------------ properties
GEO = "http://fasb.org/srt/2024:StatementGeographicalAxis"
SEG = "http://fasb.org/us-gaap/2024:StatementBusinessSegmentsAxis"
GEO_KINDS = ["usd", "usd_cells", "foreign", "related", "older_only", "other_countries", "nothing"]


def _geo(fact_id, ticker, i, fy, value, member, label, code, unit="USD", cell=False):
    dims = [(GEO, member, label)]
    if cell:
        dims.append((SEG, f"http://www.syn.example/20241231:Seg{fact_id}Member", "Retail [Member]"))
    return synthetic_record(fact_id=fact_id, ticker=ticker, cik=f"{i + 1:010d}", fiscal_year=fy,
                            value=float(value), dims=dims, unit=unit, geographic=True,
                            geo_code=code, geo_name=label)


CN = "http://xbrl.sec.gov/country/2024:CN"
JP = "http://xbrl.sec.gov/country/2024:JP"
TW = "http://xbrl.sec.gov/country/2024:TW"


def _geo_filer(kind: str, ticker: str, i: int, base: int, value: int) -> list[dict]:
    v = value * 1_000_000
    jp = _geo(base + 5, ticker, i, FY, v + 3_000_000, JP, "JAPAN", "JP")
    if kind == "usd":
        return [_geo(base, ticker, i, FY, v, CN, "CHINA", "CN"), jp]
    if kind == "usd_cells":
        return [_geo(base, ticker, i, FY, v, CN, "CHINA", "CN"),
                _geo(base + 1, ticker, i, FY, v // 2, CN, "CHINA", "CN", cell=True), jp]
    if kind == "foreign":
        return [_geo(base, ticker, i, FY, v, CN, "CHINA", "CN", unit="TWD"),
                _geo(base + 5, ticker, i, FY, v, JP, "JAPAN", "JP", unit="TWD")]
    if kind == "related":
        # An extension member naming China; its stored code is another filer's (J1).
        return [_geo(base, ticker, i, FY - 2, v, CN, "CHINA", "CN"),
                _geo(base + 1, ticker, i, FY, v, f"http://www.syn{i}.example/2024:GreaterChinaMember",
                     "Greater China [Member]", "GCN"),
                _geo(base + 2, ticker, i, FY, v + 1_000_000, TW, "TAIWAN, PROVINCE OF CHINA", "TW")]
    if kind == "older_only":
        return [_geo(base, ticker, i, FY - 1, v, CN, "CHINA", "CN"), jp]
    if kind == "other_countries":
        return [jp, _geo(base + 6, ticker, i, FY, v + 2_000_000, TW, "TAIWAN", "TW")]
    return []


@st.composite
def geo_stores(draw):
    kinds = draw(st.lists(st.sampled_from(GEO_KINDS), min_size=1, max_size=8))
    values = draw(st.lists(st.integers(min_value=10, max_value=990), min_size=len(kinds),
                           max_size=len(kinds), unique=True))
    records = []
    for i, (kind, value) in enumerate(zip(kinds, values, strict=True)):
        records += _geo_filer(kind, f"G{i:02d}", i, 1000 + 10 * i, value)
    return [f"G{i:02d}" for i in range(len(kinds))], records


@pytest.mark.criterion("C4")
@given(geo_stores())
@example((["G00", "G01", "G02"], _geo_filer("foreign", "G00", 0, 1000, 10)
          + _geo_filer("related", "G01", 1, 1010, 20) + _geo_filer("usd", "G02", 2, 1020, 30)))
def test_country_screen_reasons_follow_the_order(run_screen, store):
    # oracle: each filer is built to fall in one outcome; Requirements 4-5 restated in
    # sls_rules.country_outcomes decide it
    roster, records = store
    expected = rules.country_outcomes(records, roster, "CN", "China", FY)
    result = run_screen(records, roster, {"fy": FY, "dimension": {"geography": "China"}})
    want = [(t, float(v)) for t, v in rules.ranked_of(expected)]
    assert _ranked(result) == want
    _check_exclusions(result, rules.exclusions_of(expected))


_CODES = st.sampled_from(["EU", "EuropeMember", "EMEAMember", "CN", "ROW", "GCN", None,
                          "//www.example.com/20241231"])
_SPELLINGS = {"europe": ["Europe", "EUROPE", "europe"],
              "emea": ["EMEA", "emea", "Europe, Middle East and Africa",
                       "Europe Middle East & Africa"]}


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(st.data())
def test_regions_ignore_geo_codes_and_spelling(run_screen, geographic_fixture, data):
    # oracle: the region table of Requirement 5 restated in sls_rules.region_outcomes; the
    # metamorphic relations (geo_code / geo_name never decide a region; spellings with one
    # member key are one request)
    codes = data.draw(st.lists(_CODES, min_size=7, max_size=7))
    names = data.draw(st.lists(st.sampled_from(["Europe", "EMEA (X)", "Other", "CHINA"]),
                               min_size=5, max_size=5))
    records = [dict(r) for r in geographic_fixture["records"]]
    for i, r in enumerate(records):
        r["geo_code"] = codes[i % len(codes)]
        r["geo_name"] = names[i % len(names)]
    roster = _roster(records)
    seen = {}
    for region, spellings in _SPELLINGS.items():
        request = data.draw(st.sampled_from(spellings))
        expected = rules.region_outcomes(records, roster, request, FY)
        result = run_screen(records, roster, {"fy": FY, "dimension": {"geography": request}})
        assert _ranked(result) == [(t, float(v)) for t, v in rules.ranked_of(expected)]
        _check_exclusions(result, rules.exclusions_of(expected))
        seen[region] = ({r["ticker"] for r in result.rows},
                        {t: e["reason"] for t, e in _exclusions(result).items()})
    # A filer ranked under one region and not the other is different_member there.
    for a, b in (("europe", "emea"), ("emea", "europe")):
        for ticker in seen[a][0] - seen[b][0]:
            assert seen[b][1].get(ticker) == "different_member", (b, ticker)
