"""C2, C3: the segment screen ranks one business-axis, single-dimension member per filer, and
every filer it does not rank carries the true reason.

docs/specs/segment-lookup-screens.md, Requirements 2-4. `XbrlFactstoreScreen.screen` runs for
real over the fake store of conftest.py (the DB boundary).
"""

from __future__ import annotations

import pytest
import sls_rules as rules
from hypothesis import example, given
from hypothesis import strategies as st
from sls_store import synthetic_record

FY = 2024

# Ranked rows per hint: (ticker, value, member_match). Values from the FSDS num lines quoted
# in the fixture (checked by _fsds_value below); member_match from the member labels.
RANKED = {
    "other": [("COST", 49_969_000_000, "exact"), ("AMZN", 5_425_000_000, "contains"),
              ("GOOGL", 1_648_000_000, "contains")],
    "cloud": [("GOOGL", 43_229_000_000, "contains")],
    "international": [("AMZN", 142_906_000_000, "exact"), ("WMT", 114_641_000_000, "contains"),
                      ("UPS", 17_960_000_000, "contains")],
}
# (accession, FSDS tag, FSDS segments) of each ranked value.
RFC = "RevenueFromContractWithCustomerExcludingAssessedTax"
FSDS_OF = {
    ("other", "COST"): ("0000909832-24-000049", RFC, "ProductOrService=Other;"),
    ("other", "AMZN"): ("0001018724-25-000004", RFC, "ProductOrService=OtherServices;"),
    ("other", "GOOGL"): ("0001652044-25-000014", RFC, "BusinessSegments=AllOtherSegments;"),
    ("cloud", "GOOGL"): ("0001652044-25-000014", RFC, "BusinessSegments=GoogleCloud;"),
    ("international", "AMZN"): ("0001018724-25-000004", RFC,
                                "BusinessSegments=InternationalSegment;"),
    ("international", "WMT"): ("0000104169-24-000056", RFC,
                               "BusinessSegments=WalmartInternational;"),
    ("international", "UPS"): ("0001090727-25-000019", RFC,
                               "BusinessSegments=InternationalPackage;"),
}
# Facts the old screen ranked that are not the filer's segments (FSDS shows their axes).
NOT_SEGMENTS = {
    "other": {"KO", "NFLX"},  # equity-method investees 99,043M; AOCI reclassification 124M
    "cloud": set(),
    "international": {"LMT"},  # MajorCustomersAxis
}
# Exclusions per hint: ticker -> (reason, labels or None). Labels as the filers name them.
EXCLUDED = {
    "cloud": {
        "MSFT": ("ambiguous_member", ["Dynamics Products And Cloud Services", "Intelligent Cloud",
                                      "Office Products And Cloud Services",
                                      "Server Products And Cloud Services"]),
        "ORCL": ("ambiguous_member", ["Applications Cloud Services And License Support",
                                      "Cloud Services", "Cloud Services And License Support",
                                      "Infrastructure Cloud Services And License Support"]),
        "CRM": ("ambiguous_member",
                ["Marketing and Commerce Cloud", "Sales Cloud", "Service Cloud"]),
        **{t: ("member_not_found", None)
           for t in ("AMZN", "COST", "KO", "LMT", "MCD", "NFLX", "UPS", "WMT")},
    },
    "other": {
        "MSFT": ("ambiguous_member", ["Other Products And Services", "Service, Other"]),
        "CRM": ("ambiguous_member", ["Professional Services and Other",
                                     "Salesforce Platform and Other"]),
        "UPS": ("multi_dimension_only", None),
        "WMT": ("multi_dimension_only", None),
        **{t: ("member_not_found", None) for t in ("KO", "LMT", "MCD", "NFLX", "ORCL")},
    },
    "international": {
        "MCD": ("ambiguous_member", ["International Developmental Licensed Markets and Corporate",
                                     "International Operated Markets"]),
        **{t: ("member_not_found", None)
           for t in ("COST", "CRM", "GOOGL", "KO", "LMT", "MSFT", "NFLX", "ORCL")},
    },
}


def _fsds_value(fixture: dict, accession: str, tag: str, segments: str) -> float:
    for block in fixture["fsds"]:
        header = block["header"].split("\t")
        for line in block["lines"]:
            f = dict(zip(header, line.split("\t"), strict=True))
            if (f["adsh"], f["tag"], f["segments"], f["qtrs"]) == (accession, tag, segments, "4"):
                return float(f["value"])
    raise AssertionError(f"no FSDS line {accession} {tag} {segments!r} in the fixture")


def _roster(records):
    return sorted({r["ticker"] for r in records})


def _exclusions(result) -> dict[str, dict]:
    return {e["ticker"]: e for e in (result.disclosure or {}).get("exclusions", [])}


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("hint", ["other", "cloud", "international"])
def test_segment_screen_ranks_the_named_member(run_screen, segment_fixture, hint):
    # oracle: FSDS num lines quoted in the fixture (each ranked value is checked against its
    # line here) and the filers' member labels (exact vs containment, by hand)
    for ticker, value, _ in RANKED[hint]:
        assert _fsds_value(segment_fixture, *FSDS_OF[(hint, ticker)]) == pytest.approx(
            value, rel=1e-12)  # the expected table is the FSDS value, exactly
    records = segment_fixture["records"]
    result = run_screen(records, _roster(records), {"fy": FY, "dimension": {"segment": hint}})
    got = [(r["ticker"], r["value"], r.get("member_match")) for r in result.rows]
    assert [g[0] for g in got] == [e[0] for e in RANKED[hint]]
    assert [g[1] for g in got] == pytest.approx([float(e[1]) for e in RANKED[hint]],
                                                rel=1e-12)  # exact stored integers
    assert [g[2] for g in got] == [e[2] for e in RANKED[hint]]
    assert not NOT_SEGMENTS[hint] & {g[0] for g in got}
    for row in result.rows:
        if row.get("member_match") == "contains":
            label = rules.bare(row["label"])
            notes = result.resolution_notes
            assert any(row["ticker"] in n and label in n.lower() for n in notes), (
                row["ticker"], label, notes)


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("hint", ["cloud", "other", "international"])
def test_segment_screen_exclusions_carry_the_reason(run_screen, segment_fixture, hint):
    # oracle: the filers' member labels in the fixture, classified by hand per Requirement 4
    records = segment_fixture["records"]
    result = run_screen(records, _roster(records), {"fy": FY, "dimension": {"segment": hint}})
    excl = _exclusions(result)
    assert set(excl) == set(EXCLUDED[hint])
    for ticker, (reason, labels) in EXCLUDED[hint].items():
        assert excl[ticker]["reason"] == reason, (ticker, excl[ticker])
        if labels is not None:
            assert sorted(rules.bare(x) for x in excl[ticker]["labels"]) == sorted(
                x.lower() for x in labels)
    ranked = {r["ticker"] for r in result.rows}
    assert not ranked & set(excl)


# ------------------------------------------------------------------ properties
SEG = "http://fasb.org/us-gaap/2024:StatementBusinessSegmentsAxis"
PROD = "http://fasb.org/srt/2024:ProductOrServiceAxis"
GEO = "http://fasb.org/srt/2024:StatementGeographicalAxis"
OFF_AXES = [GEO, "http://fasb.org/srt/2024:MajorCustomersAxis",
            "http://fasb.org/us-gaap/2024:EquityMethodInvestmentNonconsolidatedInvesteeAxis",
            "http://www.syn.example/20241231:SecondaryCategorizationAxis"]
HINT = "cloud"
EXACT_LABELS = ["Cloud [Member]", "Cloud Segment [Member]", "CLOUD"]
CONTAINS_LABELS = ["Google Cloud [Member]", "Cloud Services [Member]", "Intelligent Cloud [Member]",
                   "Sales Cloud [Member]"]
OTHER_LABELS = ["Hardware [Member]", "Services [Member]", "Advertising [Member]"]
EXTRA = ("http://fasb.org/us-gaap/2024:StatementGeographicalAxis",
         "http://www.syn.example/20241231:EastMember", "East [Member]")


def _rec(fact_id, ticker, i, fy, value, axis, label, multi=False, unit="USD"):
    dims = [(axis, f"http://www.syn.example/20241231:M{fact_id}", label)]
    if multi:
        dims.append(EXTRA)
    return synthetic_record(fact_id=fact_id, ticker=ticker, cik=f"{i + 1:010d}", fiscal_year=fy,
                            value=float(value), dims=dims, unit=unit)


row_specs = st.tuples(
    st.sampled_from([SEG, PROD]),
    st.sampled_from(EXACT_LABELS + CONTAINS_LABELS + OTHER_LABELS),
    st.booleans(),
)


@st.composite
def segment_stores(draw):
    """Filers with eligible-axis rows of random labels; values distinct across filers."""
    n = draw(st.integers(min_value=1, max_value=6))
    values = draw(st.lists(st.integers(min_value=1, max_value=900), min_size=n * 4,
                           max_size=n * 4, unique=True))
    records, fid = [], 1000
    for i in range(n):
        specs = draw(st.lists(row_specs, min_size=1, max_size=4))
        for j, (axis, label, multi) in enumerate(specs):
            records.append(_rec(fid, f"F{i:02d}", i, FY, values[i * 4 + j] * 1_000_000, axis,
                                label, multi))
            fid += 1
    return records


@st.composite
def noise(draw, tickers):
    """Rows Requirement 2 excludes, larger than every eligible value."""
    out, fid = [], 9000
    for i, t in enumerate(tickers):
        for _ in range(draw(st.integers(min_value=0, max_value=3))):
            kind = draw(st.sampled_from(["axis", "multi", "label"]))
            value = draw(st.integers(min_value=1000, max_value=99_999)) * 1_000_000
            label = draw(st.sampled_from(EXACT_LABELS + CONTAINS_LABELS))
            if kind == "axis":
                out.append(_rec(fid, t, i, FY, value, draw(st.sampled_from(OFF_AXES)), label))
            elif kind == "multi":
                out.append(_rec(fid, t, i, FY, value, draw(st.sampled_from([SEG, PROD])), label,
                                multi=True))
            else:
                out.append(_rec(fid, t, i, FY, value, SEG, draw(st.sampled_from(OTHER_LABELS))))
            fid += 1
    return out


@st.composite
def stores_with_noise(draw):
    records = draw(segment_stores())
    return records, draw(noise(sorted({r["ticker"] for r in records})))


def _ranking(result):
    return [(r["ticker"], r["value"], r.get("member_match")) for r in result.rows]


# A KO-like filer: its one segment "Cloud" (50M) and an investee-axis row (5,000M) that
# contains the hint; and a CRM-like filer with an exact member next to a larger one.
_KO_LIKE = (
    [_rec(1, "F00", 0, FY, 50_000_000, SEG, "Cloud [Member]"),
     _rec(2, "F01", 1, FY, 70_000_000, PROD, "Cloud Segment [Member]"),
     _rec(3, "F01", 1, FY, 90_000_000, PROD, "Sales Cloud [Member]")],
    [_rec(9000, "F00", 0, FY, 5_000_000_000, OFF_AXES[2], "Google Cloud [Member]"),
     _rec(9001, "F01", 1, FY, 6_000_000_000, PROD, "Cloud [Member]", multi=True)],
)


@pytest.mark.criterion("C2")
@given(stores_with_noise())
@example(_KO_LIKE)
def test_excluded_rows_never_move_the_ranking(run_screen, store):
    # oracle: Requirement 3 restated in sls_rules.segment_outcomes; the metamorphic relation
    # (rows Requirement 2 excludes cannot change the ranking)
    records, extra = store
    tickers = sorted({r["ticker"] for r in records})
    expected = rules.segment_outcomes(records, tickers, HINT, FY)
    want = [(t, float(v), expected[t][1]["member_match"]) for t, v in rules.ranked_of(expected)]
    clean = run_screen(records, tickers, {"fy": FY, "dimension": {"segment": HINT}})
    assert _ranking(clean) == want
    noisy = run_screen(records + extra, tickers, {"fy": FY, "dimension": {"segment": HINT}})
    assert _ranking(noisy) == want


# One filer per kind, built to fall in one reason of Requirement 4 (or to rank).
KINDS = ["ranked_exact", "ranked_contains", "currency", "ambiguous", "multi_only", "older_only",
         "other_members", "nothing"]


def _filer(kind: str, ticker: str, i: int, base: int, value: int) -> list[dict]:
    v = value * 1_000_000
    if kind == "ranked_exact":
        return [_rec(base, ticker, i, FY, v, SEG, "Cloud [Member]"),
                _rec(base + 1, ticker, i, FY, v + 7_000_000, PROD, "Google Cloud [Member]")]
    if kind == "ranked_contains":
        return [_rec(base, ticker, i, FY, v, PROD, "Intelligent Cloud [Member]")]
    if kind == "currency":
        return [_rec(base, ticker, i, FY, v, SEG, "Cloud [Member]", unit="EUR")]
    if kind == "ambiguous":
        return [_rec(base, ticker, i, FY, v, PROD, "Sales Cloud [Member]"),
                _rec(base + 1, ticker, i, FY, v + 1_000_000, PROD, "Service Cloud [Member]")]
    if kind == "multi_only":
        return [_rec(base, ticker, i, FY, v, PROD, "Cloud [Member]", multi=True)]
    if kind == "older_only":
        return [_rec(base, ticker, i, FY - 1, v, SEG, "Cloud [Member]")]
    if kind == "other_members":
        return [_rec(base, ticker, i, FY, v, SEG, "Hardware [Member]"),
                _rec(base + 1, ticker, i, FY, v + 1_000_000, OFF_AXES[1], "Cloud [Member]")]
    return []


@st.composite
def reason_stores(draw):
    kinds = draw(st.lists(st.sampled_from(KINDS), min_size=1, max_size=8))
    values = draw(st.lists(st.integers(min_value=10, max_value=990), min_size=len(kinds),
                           max_size=len(kinds), unique=True))
    records = []
    for i, (kind, value) in enumerate(zip(kinds, values, strict=True)):
        records += _filer(kind, f"F{i:02d}", i, 1000 + 10 * i, value)
    return [f"F{i:02d}" for i in range(len(kinds))], records


@pytest.mark.criterion("C3")
@given(reason_stores())
@example((["F00", "F01", "F02"],
          _filer("ambiguous", "F00", 0, 1000, 10) + _filer("multi_only", "F01", 1, 1010, 20)
          + _filer("currency", "F02", 2, 1020, 30)))
def test_every_unranked_filer_has_its_reason(run_screen, store):
    # oracle: each filer is built to fall in one reason; Requirement 4 restated in
    # sls_rules.segment_outcomes decides it
    roster, records = store
    expected = rules.segment_outcomes(records, roster, HINT, FY)
    result = run_screen(records, roster, {"fy": FY, "dimension": {"segment": HINT}})
    excl = _exclusions(result)
    ranked = [r["ticker"] for r in result.rows]
    assert sorted(ranked) == sorted(t for t, (o, _) in expected.items() if o == "ranked")
    assert not set(ranked) & set(excl)
    for ticker, (reason, details) in rules.exclusions_of(expected).items():
        assert excl.get(ticker, {}).get("reason") == reason, (ticker, excl.get(ticker))
        for key, value in details.items():
            assert excl[ticker][key] == value, (ticker, key, excl[ticker])
