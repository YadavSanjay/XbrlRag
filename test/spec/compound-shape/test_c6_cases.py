"""C6: a compound EvalCase and its record carry requirement 5's fields: the ranking at the
top level, ``parts`` in order, the tags, origin, paraphrase group and key provenance.

Oracle: requirements 4-5 of docs/specs/compound-shape.md; values hand-keyed from the
conftest fixture table.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.core.case import EvalCase

PREFIXES = ["split", "intent", "family", "template", "key_source", "key_status", "shape"]
DISCLOSURE_KEYS = {"disclosures", "universe", "expected_exclusions"}


def _tags_ok(rec, split, iid, shape, status="verified"):
    tags = rec["tags"]
    assert [t.partition(":")[0] for t in tags[:7]] == PREFIXES
    assert tags[7:] == ["compound"]
    assert tags[0] == f"split:{split}"
    assert tags[1] == f"intent:{iid}"
    assert tags[4] == "key_source:sec_companyfacts"
    assert tags[5] == f"key_status:{status}"
    assert tags[6] == f"shape:{shape}"


@pytest.mark.criterion("C6")
def test_rank_by_ratio_case(kit):
    # oracle: fixture — top 3 by FY2024 revenue A, B, C; winner DEVB at 120/400 = 0.3.
    # key inputs: the screen's 4 ranked revenue keys (A, B, C, D from their FY2024 10-Ks),
    # then for A, B, C in ranking order operating income then revenue
    kw = kit.rbr(2024, "Revenue", "operating_margin")
    iid = kit.oracle_id(kw)
    rec = kit.case_of(kit.build(), kw)
    _tags_ok(rec, "dev", iid, kit.RBR)
    assert rec["suite"] == "xbrl_v2_dev_compound"
    assert rec["origin"] == "v2:compound.rank_by_ratio"
    assert rec["paraphrase_group"] == iid
    assert rec["kind"] == "screen" and rec["top_n"] == 3 and rec["unit"] == "USD"
    assert "ticker" not in rec and "cik" not in rec
    ranking, dependent = rec["parts"]
    assert ranking["part"] == "ranking" and dependent["part"] == "dependent"
    assert ranking["expected_rows"] == rec["expected_rows"]
    assert ranking["disclosures"] == rec["disclosures"]
    assert dependent["ticker"] == "DEVB"
    assert dependent["expected_value"] == pytest.approx(0.3, rel=1e-12)  # 120/400, rounding
    assert not DISCLOSURE_KEYS & set(dependent)
    key = rec["key"]
    assert key["status"] == "verified"
    acc = {"DEVA": kit.A24, "DEVB": kit.B24, "DEVC": kit.C24, "DEVD": kit.D24}
    rev, oil = "us-gaap:Revenues", "us-gaap:OperatingIncomeLoss"
    want = [(acc[t], rev) for t in ("DEVA", "DEVB", "DEVC", "DEVD")]
    for t in ("DEVA", "DEVB", "DEVC"):
        want += [(acc[t], oil), (acc[t], rev)]
    assert [(k["accession"], k["concept"]) for k in key["inputs"]] == want
    assert {"S3", "S4", "S5"} <= set(key["policies"])
    assert EvalCase.from_dict(rec).parts == rec["parts"]


@pytest.mark.criterion("C6")
def test_leader_growth_case(kit):
    # oracle: fixture — revenue leader DEVA; net income FY2023 50, FY2024 60 (both in its
    # FY2024 10-K): growth 0.2, alt 20. Inputs: 4 ranked revenue keys, then the two growth
    # inputs, FY2023 then FY2024
    kw = kit.lg(2024, "Revenue", "NetIncome")
    iid = kit.oracle_id(kw)
    rec = kit.case_of(kit.build(), kw)
    _tags_ok(rec, "dev", iid, kit.LG)
    assert rec["origin"] == "v2:compound.leader_growth"
    assert rec["paraphrase_group"] == iid
    dependent = rec["parts"][1]
    assert dependent["kind"] == "compute" and dependent["ticker"] == "DEVA"
    assert dependent["expected_value"] == pytest.approx(0.2, rel=1e-12)  # 10/50, rounding
    assert dependent["alt_values"] == [pytest.approx(20.0, rel=1e-12)]  # same
    inputs = rec["key"]["inputs"]
    assert len(inputs) == 6
    assert [(k["concept"], k["period_end"], k["accession"]) for k in inputs[4:]] == [
        ("us-gaap:NetIncomeLoss", "2023-12-31", kit.A24),
        ("us-gaap:NetIncomeLoss", "2024-12-31", kit.A24)]
    assert {"S3", "S4", "S5"} <= set(rec["key"]["policies"])


@pytest.mark.criterion("C6")
def test_status_is_verified_only_when_every_input_is(kit):
    # oracle: requirement 4 — the fact store lacks DEVB's FY2024 operating income (a
    # dependent input), then DEVD's FY2024 revenue (a ranking input of a filer outside the
    # top 3): either makes the case single-source
    kw = kit.rbr(2024, "Revenue", "operating_margin")
    iid = kit.oracle_id(kw)
    s, e = kit.Q24
    for gap in ((kit.CIK["DEVB"], "us-gaap:OperatingIncomeLoss", s, e, kit.B24),
                (kit.CIK["DEVD"], "us-gaap:Revenues", s, e, kit.D24)):
        rec = kit.case_of(kit.build(missing=(gap,)), kw)
        assert rec["key"]["status"] == "single-source"
        _tags_ok(rec, "dev", iid, kit.RBR, status="single-source")


@pytest.mark.criterion("C6")
@settings(max_examples=6)
@given(share=st.floats(min_value=0.0, max_value=1.0), salt=st.text(min_size=1, max_size=6))
def test_every_record_is_well_formed(kit, share, salt):
    # oracle: requirement 5 — for every case: ranking at the top level equals the ranking
    # part; parts ranking then dependent; tags, origin and paraphrase group agree; status
    # verified iff every input is; policies include S3, S4, S5
    result = kit.build(share=share, salt=salt)
    pairs = kit.records(result)
    assert len(pairs) == 15
    for split, rec in pairs:
        shape = rec["tags"][6].partition(":")[2]
        iid = rec["tags"][1].partition(":")[2]
        _tags_ok(rec, split, iid, shape, status=rec["key"]["status"])
        assert shape in (kit.RBR, kit.LG)
        assert rec["origin"] == f"v2:{shape}"
        assert rec["suite"] == f"xbrl_v2_{split}_compound"
        assert rec["paraphrase_group"] == iid
        assert [p["part"] for p in rec["parts"]] == ["ranking", "dependent"]
        assert rec["parts"][0]["expected_rows"] == rec["expected_rows"]
        assert rec["kind"] == "screen" and rec["top_n"] == 3
        statuses = {k["status"] for k in rec["key"]["inputs"]}
        assert rec["key"]["status"] == ("verified" if statuses == {"verified"}
                                        else "single-source")
        assert {"S3", "S4", "S5"} <= set(rec["key"]["policies"])
