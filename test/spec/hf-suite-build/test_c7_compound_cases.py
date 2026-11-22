"""C7: the held-out compound build: one suite ``heldout_hf_compound``.

Oracle: requirement 7 of docs/specs/hf-suite-build.md; the fixture's companyfacts table
(FY2024 revenue ranking over ``covered``: DEVA 500 > DEVB 400 > DEVC 300 > DEVD 200, DEVE
EUR excluded; DEVA's FY2024 10-K reports operating income 100 and FY2023 90); and the
metamorphic comparison with the existing ``build_compound_suites`` (share 0).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.eval.core.case import EvalCase
from src.eval.core.run_columns import run_split


@pytest.mark.criterion("C7")
def test_compound_suite_and_a_hand_keyed_case(kit):
    # oracle: requirement 7; the leader_growth intent (covered, FY2024, Revenue ranking,
    # operating income growth) has leader DEVA and growth (100 - 90) / 90 (S1: both from the
    # FY2024 10-K).
    build = kit.hf_compound(["DEVD"])
    assert set(build) == {"heldout_filers", "manifest", "intents"}
    suite = build["heldout_filers"]
    assert suite["name"] == "heldout_hf_compound"
    assert suite["split"] == "heldout_filers"
    cases = suite["cases"]
    assert cases and suite["n_cases"] == len(cases)
    assert all(c["suite"] == "heldout_hf_compound" for c in cases)
    assert all(c["tags"][0] == "split:heldout_filers" and c["tags"][-1] == "compound"
               for c in cases)
    assert run_split([EvalCase.from_dict(c) for c in cases]) == "heldout_filers"
    assert suite["content_hash"] == kit.sha256_hash(cases)
    assert {o["universe"] for o in build["intents"]} == {"covered"}
    assert build["manifest"]["phrasing"] == {"per_intent": 2, "share": 0, "salt": kit.SALT}
    assert build["manifest"]["split"] == "heldout_filers"

    lg = kit.oracle_id({"kind": "compound", "operation": "leader_growth", "universe": "covered",
                        "n": 3, "fiscal_years": [2023, 2024],
                        "metrics": ["Revenue", "OperatingIncome"]})
    found = [c for c in cases if f"intent:{lg}" in c["tags"]]
    assert found
    for c in found:
        dependent = c["parts"][1]
        assert dependent["ticker"] == "DEVA"
        assert dependent["expected_value"] == pytest.approx((100 - 90) / 90, rel=1e-12)
        assert [r["ticker"] for r in c["expected_rows"]] == ["DEVA", "DEVB", "DEVC"]


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
def test_compound_records_are_the_dev_records_relabelled(kit):
    # oracle: requirement 7: each held-out record is the development compound record of the
    # same case id with suite and split tag replaced, for exactly the involved intents.
    heldout = ["DEVB"]
    dev = kit.dev_compound(share=0.0)
    dev_records = {c["id"]: c for c in dev["dev"]["cases"]}
    objs = {kit.oracle_id(o): o for o in dev["intents"]}
    uni = kit.universes()
    want = {cid for cid, c in dev_records.items() if kit.oracle_involves(
        SimpleNamespace(tickers=[], universe=objs[c["paraphrase_group"]]["universe"]),
        uni, heldout)}
    build = kit.hf_compound(heldout)
    got = {c["id"]: c for c in build["heldout_filers"]["cases"]}
    assert got and set(got) == want
    for cid, record in got.items():
        assert kit.as_dev(record, "xbrl_v2_dev_compound") == dev_records[cid]
