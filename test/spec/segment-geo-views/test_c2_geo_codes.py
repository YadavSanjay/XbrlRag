"""C2: geography codes come from the geography_normalization row of the identical QName (J1).

docs/specs/segment-geo-views.md, Requirement 2. Point cases: every geographic member of Meta
FY2024 (0001326801-25-000017), TSMC FY2024 (0001193125-25-083423) and Tesla FY2023
(0001628280-24-002390), resolved against the committed table snapshot. Properties: row order,
unrelated rows and duplicates never change a result.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.sec_edgar.xbrl.segment_geo import resolve_geography

GEO_AXES = {"srt:StatementGeographicalAxis", "ifrs-full:GeographicalAreasAxis"}
RESULT_KEYS = {"geo_code", "geo_name", "geo_type"}

# oracle: ISO 3166-1 alpha-2 (the SEC country taxonomy's local names are these codes).
ISO_3166 = {"CN": "China", "DE": "Germany", "JP": "Japan", "TW": "Taiwan", "US": "United States"}

FB = "http://www.facebook.com/20241231:"
TSMC = "http://www.tsmc.com/20241231:"
TSLA = "http://www.tesla.com/20231231:"

# oracle: each member's own label (in the geo_members fixture) read against the
# geography_normalization row of the identical QName, by hand; countries by ISO 3166.
EXPECTED = {
    "meta_fy2024_geo_members": {
        "country:US": "US",
        "country:CN": "CN",
        FB + "USCanadaMember": "US-CA",        # "US & Canada [Member]"
        FB + "RestOfWorldMember": "ROW",       # "Rest Of World [Member]"
        "srt:EuropeMember": "EU",              # "Europe [Member]"
        "srt:AsiaPacificMember": "APAC",       # "Asia Pacific [Member]"
        "us-gaap:NonUsMember": "NON-US",       # "Non-US [Member]"
    },
    "tsm_fy2024_geo_members": {
        "country:TW": "TW",
        "country:US": "US",
        "country:CN": "CN",
        "country:JP": "JP",
        TSMC + "EmeaMember": "EMEA",                        # "EMEA [member]"
        TSMC + "EuropeMiddleEastAndAfricaMember": "EMEA",   # "Europe Middle East and Africa"
        TSMC + "OtherCountryMember": "ROW",                 # "Other country [member]"
        TSMC + "OthersMember": "ROW",                       # "Others [member]"
        "ifrs-full:CountryOfDomicileMember": None,          # no row: unmapped
        "ifrs-full:ForeignCountriesMember": None,           # no row: unmapped
    },
    "tsla_fy2023_geo_members": {
        "country:US": "US",
        "country:CN": "CN",
        "country:DE": "DE",
        TSLA + "OtherInternationalMember": "ROW",   # "Other International [Member]"
        TSLA + "ShanghaiChinaMember": "CN",         # "Shanghai, China [Member]"
    },
}


def _resolve(member: dict, rows: list[dict]) -> dict:
    return resolve_geography(member["segment_member"], member["segment_label"], rows)


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_geographic_member_gets_its_exact_code(geo_members, normalization, name):
    # oracle: EXPECTED above (labels and ISO 3166 by hand), not computed by the code.
    members = geo_members(name)["members"]
    assert {m["segment_axis"] for m in members} <= GEO_AXES
    assert {m["segment_member"] for m in members} == set(EXPECTED[name])
    for m in members:
        result = _resolve(m, normalization)
        assert set(result) == RESULT_KEYS
        assert result["geo_code"] == EXPECTED[name][m["segment_member"]], m["segment_member"]
        if result["geo_code"] is None:
            assert result["geo_name"] == m["segment_label"]
            assert result["geo_type"] is None


@pytest.mark.criterion("C2")
def test_country_members_get_iso_3166_codes_and_names(geo_members, normalization):
    # oracle: ISO 3166-1 alpha-2 codes and English short names (ISO_3166 above).
    seen = 0
    for name in EXPECTED:
        for m in geo_members(name)["members"]:
            if not m["segment_member"].startswith("country:"):
                continue
            code = m["segment_member"].split(":", 1)[1]
            result = _resolve(m, normalization)
            assert result == {"geo_code": code, "geo_name": ISO_3166[code], "geo_type": "country"}
            seen += 1
    assert seen == 9  # META 2, TSMC 4, Tesla 3


@pytest.mark.criterion("C2")
def test_tesla_china_screen_member_is_china_not_other_international(
        filing, geo_members, normalization, tsla_filing):
    # oracle: Tesla's 10-K (tsla-20231231.htm, copied in tsla_fy2023_filing_excerpt.json):
    # f-1645 is 21,745 (scale 6) in context c-369 = srt:StatementGeographicalAxis country:CN,
    # row "China"; f-1648 is 29,793 in c-372 = tsla:OtherInternationalMember, row
    # "Other international"; f-1651 Total 96,773.
    ix = {f["id"]: f for f in tsla_filing["facts"]}
    assert ix["f-1645"]["table_row_text"].startswith("China | 21,745")
    assert "<xbrldi:explicitMember dimension=\"srt:StatementGeographicalAxis\">country:CN<" in (
        tsla_filing["contexts"][ix["f-1645"]["context_ref"]])
    assert ix["f-1648"]["table_row_text"].startswith("Other international | 29,793")
    assert "tsla:OtherInternationalMember" in tsla_filing["contexts"][ix["f-1648"]["context_ref"]]

    facts = [f for f in filing("tsla_fy2023_revenues").current()
             if f["view"]["segment_axis"] == "srt:StatementGeographicalAxis"]
    codes = {f["fact_id"]: resolve_geography(f["view"]["segment_member"],
                                             f["view"]["segment_label"], normalization)["geo_code"]
             for f in facts}
    china = [fid for fid, code in codes.items() if code == "CN"]
    assert china == [78189]
    assert filing("tsla_fy2023_revenues").by_id(78189)["value"] == 21745 * 10**6
    assert codes[78192] == "ROW"
    assert codes[78186] == "US"


@pytest.mark.criterion("C2")
def test_unmapped_member_gets_no_code_never_a_uri_fragment(normalization):
    # oracle: Requirement 2. Tesla's FY2024 member http://www.tesla.com/20241231:OtherCountriesMember
    # (label "Other Countries [Member]", xbrl_db 2026-11-07) has no table row (the snapshot holds
    # only the 20221231 one); the investigation's view gave it '//www.tesla.com/20241231'.
    member = "http://www.tesla.com/20241231:OtherCountriesMember"
    assert all(r["member_qname"] != member for r in normalization)
    got = resolve_geography(member, "Other Countries [Member]", normalization)
    assert got == {"geo_code": None, "geo_name": "Other Countries [Member]", "geo_type": None}

    older = resolve_geography("http://www.tesla.com/20221231:OtherCountriesMember",
                              "Other Countries [Member]", normalization)
    assert older == {"geo_code": "ROW", "geo_name": "Other Countries (TSLA)", "geo_type": "region"}

    # The SRT group members carry an empty iso_code in the table: no code, the row's name.
    group = resolve_geography("srt:SegmentGeographicalGroupsOfCountriesGroupOneMember",
                              "Group One [Member]", normalization)
    assert group == {"geo_code": None, "geo_name": "Geographic Group 1", "geo_type": "segment"}


@pytest.mark.criterion("C2")
def test_matching_rows_that_disagree_leave_the_member_unmapped():
    # oracle: Requirement 2 by hand: two taxonomy years of one standard member agreeing give that
    # row; disagreeing copies give no code; a row's name falls back to the label when empty.
    rows = [
        {"member_qname": "http://fasb.org/srt/2023:EuropeMember", "iso_code": "EU",
         "geo_name": "Europe", "geo_type": "region"},
        {"member_qname": "http://fasb.org/srt/2024:EuropeMember", "iso_code": "EU",
         "geo_name": "Europe", "geo_type": "region"},
        {"member_qname": "http://xbrl.sec.gov/country/2023:GB", "iso_code": "GB",
         "geo_name": "United Kingdom", "geo_type": "country"},
        {"member_qname": "http://xbrl.sec.gov/country/2024:GB", "iso_code": "UK",
         "geo_name": "United Kingdom", "geo_type": "country"},
        {"member_qname": "http://x.example/20241231:NorthMember", "iso_code": "NA",
         "geo_name": "", "geo_type": "region"},
    ]
    assert resolve_geography("srt:EuropeMember", "Europe [Member]", rows) == {
        "geo_code": "EU", "geo_name": "Europe", "geo_type": "region"}
    assert resolve_geography("country:GB", "UNITED KINGDOM", rows) == {
        "geo_code": None, "geo_name": "UNITED KINGDOM", "geo_type": None}
    assert resolve_geography("http://x.example/20241231:NorthMember", "North [Member]", rows) == {
        "geo_code": "NA", "geo_name": "North [Member]", "geo_type": "region"}
    # The prefix form is not the URI form: a member written as a URI matches no standard row.
    as_uri = resolve_geography("http://fasb.org/srt/2024:EuropeMember", "Europe", rows)
    assert as_uri["geo_code"] is None


def _all_members(geo_members) -> list[dict]:
    return [m for name in EXPECTED for m in geo_members(name)["members"]]


_local = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz", min_size=1,
                 max_size=12)


@pytest.mark.criterion("C2")
@given(seed=st.randoms(use_true_random=False),
       locals_=st.lists(_local, max_size=6),
       codes=st.lists(st.sampled_from(["CN", "US", "ROW", "EMEA", "XX"]), min_size=6, max_size=6))
def test_order_and_unrelated_rows_never_change_a_result(geo_members, normalization, seed,
                                                        locals_, codes):
    # oracle: Requirement 2 (exact key match): rows are shuffled, and rows are added in the
    # members' own namespaces under local names that no fixture member uses, so no expected
    # code (EXPECTED) may move.
    members = _all_members(geo_members)
    used = {m["segment_member"] for m in members}
    extra = []
    for i, local in enumerate(locals_):
        for ns in ("http://www.tesla.com/20231231", "http://www.tsmc.com/20241231",
                   "http://xbrl.sec.gov/country/2024", "http://fasb.org/srt/2024"):
            qname = f"{ns}:Zz{local}Member"
            if qname not in used:
                extra.append({"member_qname": qname, "iso_code": codes[i], "geo_name": "x",
                              "geo_type": "region"})
    rows = list(normalization) + extra + [dict(r) for r in normalization[:50]]
    seed.shuffle(rows)
    expected = {member: code for e in EXPECTED.values() for member, code in e.items()}
    for m in members:
        assert _resolve(m, rows)["geo_code"] == expected[m["segment_member"]], m["segment_member"]


@pytest.mark.criterion("C2")
@given(ns=st.sampled_from(["http://www.tesla.com/20231231", "http://www.facebook.com/20241231",
                           "http://www.walmart.com/20240131", "http://www.amd.com/20251227"]),
       local=_local, label=st.text(max_size=20))
def test_a_member_without_a_row_never_gets_a_code(normalization, ns, local, label):
    # oracle: Requirement 2: no row with the identical key means geo_code None and the member's
    # own label, whatever the namespace (the old view returned the namespace fragment).
    member = f"{ns}:Qq{local}Member"
    got = resolve_geography(member, label, normalization)
    assert got == {"geo_code": None, "geo_name": label, "geo_type": None}
    known = resolve_geography(f"{TSLA}OtherInternationalMember", label, normalization)
    assert known["geo_code"] == "ROW"
