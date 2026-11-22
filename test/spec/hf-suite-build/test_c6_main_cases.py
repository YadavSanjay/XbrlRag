"""C6: the held-out main build: one suite ``heldout_hf``, every case ``split:heldout_filers``.

Oracle: requirement 6 of docs/specs/hf-suite-build.md; the fixture's companyfacts table (DEVD
FY2024 revenue 200 million); the content hash restated with ``hashlib`` and ``json`` in
conftest; and the metamorphic comparison with the existing ``build_v2_suites`` (share 0,
quotas above every count), whose record of the same keyed intent and phrasing must differ only
in its suite name and split tag.
"""

from __future__ import annotations

from collections import Counter
from types import SimpleNamespace

import pytest

from src.eval.core.case import EvalCase
from src.eval.core.run_columns import run_split


@pytest.mark.criterion("C6")
def test_suite_cases_and_manifest(kit):
    # oracle: requirement 6 and the fixture (DEVD FY2024 revenue = 200e6).
    build = kit.hf_build(["DEVD"])
    assert set(build) == {"heldout_filers", "manifest", "intents"}
    suite = build["heldout_filers"]
    assert suite["name"] == "heldout_hf"
    assert suite["split"] == "heldout_filers"
    assert suite["version"] == "1"
    cases = suite["cases"]
    assert cases and suite["n_cases"] == len(cases)
    assert [c["id"] for c in cases] == sorted(c["id"] for c in cases)
    assert all(c["suite"] == "heldout_hf" for c in cases)
    assert all(c["tags"][0] == "split:heldout_filers" for c in cases)
    assert run_split([EvalCase.from_dict(c) for c in cases]) == "heldout_filers"
    assert suite["content_hash"] == kit.sha256_hash(cases)

    rev = kit.oracle_id({"kind": "lookup", "tickers": ["DEVD"], "fiscal_years": [2024],
                         "metrics": ["Revenue"]})
    found = [c for c in cases if f"intent:{rev}" in c["tags"]]
    assert found
    assert all(c["expected_value"] == pytest.approx(200e6, rel=1e-12) for c in found)

    manifest = build["manifest"]
    assert manifest["split"] == "heldout_filers"
    assert manifest["phrasing"] == {"per_intent": 2, "share": 0, "salt": kit.SALT}
    assert manifest["heldout"] == {"filers": 1}
    assert manifest["content_hashes"] == {"heldout_filers": suite["content_hash"]}
    shapes = Counter(c["origin"].removeprefix("v2:") for c in cases)
    statuses = Counter(c["key"]["status"] for c in cases)
    assert manifest["cases"] == {"heldout_filers": {
        "total": len(cases), "by_shape": dict(shapes), "by_key_status": dict(statuses)}}
    assert manifest["intents_hash"] == kit.sha256_hash(build["intents"])


@pytest.mark.criterion("C6")
def test_empty_or_unknown_heldout_refused(kit):
    # oracle: requirement 6.1: an empty list, and a filer outside the inventory, are
    # refused; the message names no ticker.
    with pytest.raises(ValueError):
        kit.hf_build([])
    with pytest.raises(ValueError) as info:
        kit.hf_build(["DEVD", "DEVZ"])
    assert "DEVZ" not in str(info.value)


def _intent_ns(obj: dict) -> SimpleNamespace:
    return SimpleNamespace(tickers=list(obj["tickers"]), universe=obj["universe"])


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
def test_records_are_the_dev_records_relabelled(kit):
    # oracle: requirement 6.5: the held-out record equals the development build's record of
    # the same case id with suite and split tag replaced; the held-out cases are exactly the
    # development cases whose intent involves a held-out filer (requirement 5, restated).
    heldout = ["DEVB", "DEVD"]
    dev = kit.dev_build(share=0.0)
    assert dev["validation"]["n_cases"] == 0
    dev_records = {c["id"]: c for c in dev["dev"]["cases"]}
    objs = {kit.oracle_id(o): o for o in dev["intents"]}
    uni = kit.universes()
    want_ids = {cid for cid, c in dev_records.items()
                if kit.oracle_involves(_intent_ns(objs[c["paraphrase_group"]]), uni, heldout)}
    build = kit.hf_build(heldout)
    got = {c["id"]: c for c in build["heldout_filers"]["cases"]}
    assert set(got) == want_ids
    for cid, record in got.items():
        assert kit.as_dev(record, "xbrl_v2_dev") == dev_records[cid]


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
def test_input_order_changes_nothing(kit):
    # oracle: requirement 6.6: nothing depends on the order of the inventory, universes,
    # members or held-out list.
    one = kit.hf_build(["DEVB", "DEVD"])
    inv = dict(reversed(list(kit.inventory().items())))
    uni = {u: list(reversed(m)) for u, m in reversed(list(kit.universes().items()))}
    two = kit.hf_build(["DEVD", "DEVB"], inv=inv, uni=uni)
    assert kit.canonical(one) == kit.canonical(two)
    assert one["heldout_filers"]["n_cases"] > 0
