"""Unit tests for the statement witness in the v2 suite build (src/eval/suites/v2_witness.py).

Spec: docs/specs/key-statement-witness.md, "Not locked here": the builder chooses every key
input with ``select_key``, admits every screen member with ``universe_status`` and stores
``witness_manifest`` as ``manifest["key_independence"]``. The companyfacts documents and
FSDS rows are built here, so every expected value is read off them; the committed
statement-line rules are the ones under test.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.eval.keys.base import SecSource
from src.eval.keys.statement_witness import (
    WitnessBlocked,
    load_note_concepts,
    load_resolutions,
    load_statement_rules,
)
from src.eval.suites.intents import Intent
from src.eval.suites.v2_sources import candidate_concepts_from_rows
from src.eval.suites.v2_suite import (
    FactStoreSource,
    IntentDroppedError,
    _Keyer,
    build_v2_suites,
)
from src.eval.suites.v2_witness import KeyWitness

REPO = Path(__file__).resolve().parents[2]
V2 = REPO / "configs" / "eval" / "v2"
REV = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
FY = ("2024-01-01", "2024-12-31")
CIKS = {"AAA": "0000900101", "BBB": "0000900102", "CCC": "0000900103", "DDD": "0000900104",
        "EEE": "0000900105"}


def _adsh(ticker: str) -> str:
    return f"{CIKS[ticker]}-25-000001"


# ticker -> [(concept, value, plabel or None when no statement line presents it)]
FACTS: dict[str, list[tuple[str, float, str | None]]] = {
    "AAA": [("us-gaap:Revenues", 1000.0, "Total revenues"), (REV, 900.0, None),
            ("us-gaap:OperatingExpenses", 300.0, "Total operating expenses")],
    "BBB": [("us-gaap:CostsAndExpenses", 5000.0, "Total costs and expenses")],
    "CCC": [("us-gaap:OperatingExpenses", 400.0, "Total operating expenses")],
    "DDD": [("us-gaap:OperatingExpenses", 200.0, "Total operating expenses")],
    "EEE": [("us-gaap:Revenues", 1000.0, "Total revenues"), (REV, 900.0, None)],
}


def _companyfacts(ticker: str) -> dict:
    facts: dict[str, Any] = {}
    for concept, value, _label in FACTS[ticker]:
        tax, tag = concept.split(":")
        facts.setdefault(tax, {})[tag] = {"units": {"USD": [{
            "start": FY[0], "end": FY[1], "val": value, "accn": _adsh(ticker), "fy": 2024,
            "fp": "FY", "form": "10-K", "filed": "2025-02-01"}]}}
    return {"cik": int(CIKS[ticker]), "entityName": ticker, "facts": facts}


def _tables() -> dict[str, list[dict[str, str]]]:
    sub, pre, num = [], [], []
    for ticker, facts in FACTS.items():
        adsh = _adsh(ticker)
        sub.append({"adsh": adsh, "cik": str(int(CIKS[ticker])), "name": ticker, "form": "10-K",
                    "period": "20241231", "fy": "2024", "fp": "FY", "filed": "20250201"})
        for line, (concept, value, label) in enumerate(facts, start=1):
            tag = concept.split(":")[1]
            num.append({"adsh": adsh, "tag": tag, "version": "us-gaap/2024", "ddate": "20241231",
                        "qtrs": "4", "uom": "USD", "segments": "", "coreg": "",
                        "value": f"{value:.4f}"})
            if label is not None:
                pre.append({"adsh": adsh, "report": "2", "line": str(line), "stmt": "IS",
                            "inpth": "0", "rfile": "H", "tag": tag, "version": "us-gaap/2024",
                            "plabel": label, "negating": "0"})
    return {"sub": sub, "pre": pre, "num": num, "tag": []}


class _Sec(SecSource):
    def company_facts(self, cik: str) -> dict:
        ticker = next(t for t, c in CIKS.items() if c == str(cik).zfill(10))
        return _companyfacts(ticker)

    def frames(self, taxonomy: str, tag: str, unit: str, period: str) -> dict:
        raise AssertionError("frames are not read")


class _NoStore(FactStoreSource):
    def value(self, cik: str, concept: str, unit: str, period_start: str | None,
              period_end: str, accession: str) -> None:
        return None


def _inv(metrics: dict[str, dict[str, str]]) -> dict[str, Any]:
    """ticker -> family -> concept (the mapping's choice), as the inventory carries it."""
    return {t: {"cik": CIKS[t], "years": {2024: {
        family: {"concept": concept, "unit": "USD"} for family, concept in fams.items()}}}
        for t, fams in metrics.items()}


def _witness(candidates: dict[tuple[str, int, str], list[str]] | None = None,
             resolutions: list[dict[str, Any]] | None = None) -> KeyWitness:
    if resolutions is None:
        resolutions = load_resolutions(V2 / "key_resolutions.yaml")
    return KeyWitness(_tables(), load_statement_rules(V2 / "statement_lines.yaml"),  # type: ignore[arg-type]  # rows built here
                      load_note_concepts(V2 / "note_concepts.yaml"), resolutions, candidates)


def _keyer(inv: dict[str, Any], witness: KeyWitness, unis: dict | None = None) -> _Keyer:
    return _Keyer(inv, unis or {}, _Sec(), _NoStore(), witness)


def test_the_statement_line_chooses_among_offered_concepts():
    # the mapping's concept is the unpresented component (900); the statement presents
    # "Total revenues" 1,000 on Revenues, which the mapping also offers
    inv = _inv({"AAA": {"Revenue": REV}})
    w = _witness({(CIKS["AAA"], 2024, "Revenue"): [REV, "us-gaap:Revenues"]})
    keyed = _keyer(inv, w).key(Intent("lookup", tickers=["AAA"], fiscal_years=[2024],
                                      metrics=["Revenue"]))
    assert keyed["value"] == 1000.0
    assert keyed["inputs"][0]["concept"] == "us-gaap:Revenues"
    assert w.manifest()["by_status"]["agree"] == 1


def test_a_disagreeing_key_blocks_the_build():
    # EEE's mapping offers only the component 900 against the presented 1,000: no resolution
    inv = _inv({"EEE": {"Revenue": REV}})
    with pytest.raises(WitnessBlocked, match="Revenue cik 0000900105 FY2024"):
        build_v2_suites(inv, {"covered": ["EEE"]}, _Sec(), _NoStore(), None, None,  # type: ignore[arg-type]  # nothing is phrased
                        quotas={"lookup": 1}, per_intent=1, share=0.5, salt="s",
                        witness=_witness())


def test_without_a_witness_the_build_is_unchanged():
    inv = _inv({"EEE": {"Revenue": REV}})
    keyed = _Keyer(inv, {}, _Sec(), _NoStore()).key(
        Intent("lookup", tickers=["EEE"], fiscal_years=[2024], metrics=["Revenue"]))
    assert (keyed["value"], keyed["inputs"][0]["concept"]) == (900.0, REV)


def test_a_screen_excludes_a_substitute_and_ranks_a_presented_unmapped_line():
    # BBB's "Total costs and expenses" (CostsAndExpenses) is a stated substitute of
    # OperatingExpenses; DDD has no inventory entry but presents "Total operating expenses" 200
    inv = _inv({"AAA": {"OperatingExpenses": "us-gaap:OperatingExpenses"},
                "BBB": {"OperatingExpenses": "us-gaap:CostsAndExpenses"},
                "CCC": {"OperatingExpenses": "us-gaap:OperatingExpenses"},
                "DDD": {"Revenue": "us-gaap:Revenues"}})
    w = _witness()
    unis = {"covered": ("AAA", "BBB", "CCC", "DDD")}
    keyed = _keyer(inv, w, unis).key(Intent("screen", operation="top", n=5, universe="covered",
                                            fiscal_years=[2024],
                                            metrics=["OperatingExpenses"]))
    assert [(r["ticker"], r["value"]) for r in keyed["rows"]] == [
        ("CCC", 400.0), ("AAA", 300.0), ("DDD", 200.0)]
    assert keyed["disclosures"]["excluded"] == [
        {"ticker": "BBB", "reason": "substitute:includes_cost_of_revenue"}]
    m = w.manifest()
    assert m["n"] == 4 and m["by_status"]["agree"] == 4


def test_a_held_key_drops_the_intent_and_is_listed():
    # a 10-Q key whose 10-K value differs is value_recast: held, dropped, and in the manifest
    inv = _inv({"CCC": {"OperatingExpenses": "us-gaap:OperatingExpenses"}})
    sec = _Sec()
    original = sec.company_facts

    def recast(cik: str) -> dict:
        doc = original(cik)
        entries = doc["facts"]["us-gaap"]["OperatingExpenses"]["units"]["USD"]
        entries.append({**entries[0], "val": 450.0, "accn": "0000900103-25-000009",
                        "fp": "Q1", "form": "10-Q", "filed": "2025-05-01"})
        return doc

    sec.company_facts = recast  # type: ignore[method-assign]  # a later 10-Q recasts the year
    w = _witness()
    keyer = _Keyer(inv, {}, sec, _NoStore(), w)
    with pytest.raises(IntentDroppedError) as err:
        keyer.key(Intent("lookup", tickers=["CCC"], fiscal_years=[2024],
                         metrics=["OperatingExpenses"]))
    assert err.value.args == ("value_recast",)
    assert w.manifest()["held"] == [{"family": "OperatingExpenses", "cik": CIKS["CCC"],
                                     "fiscal_year": 2024,
                                     "key_concept": "us-gaap:OperatingExpenses",
                                     "status": "value_recast"}]


def test_the_build_stores_key_independence():
    inv = _inv({"AAA": {"Revenue": "us-gaap:Revenues"}})
    build = build_v2_suites(inv, {"covered": ["AAA"]}, _Sec(), _NoStore(), None, None,  # type: ignore[arg-type]  # nothing is phrased
                            quotas={}, per_intent=1, share=0.5, salt="s",
                            witness=_witness(resolutions=[]))
    assert build["manifest"]["key_independence"]["n"] == 0
    assert build["manifest"]["key_resolutions"] == {
        "n": 0, "original": 0, "reviewed": 0,
        "by_bucket": {"rule_gap": 0, "combined_line": 0, "definition": 0, "note_unlisted": 0,
                      "by_design": 0}}
    plain = build_v2_suites(inv, {"covered": ["AAA"]}, _Sec(), _NoStore(), None, None,  # type: ignore[arg-type]  # nothing is phrased
                            quotas={}, per_intent=1, share=0.5, salt="s")
    assert "key_independence" not in plain["manifest"]


def test_candidate_concepts_keep_every_rank_in_order():
    us = "http://fasb.org/us-gaap/2024"
    rows = [("AAA", 900101, 2024, "Revenue", f"{us}:Revenues", "USD", 10),
            ("AAA", 900101, 2024, "Revenue", f"{us}:RevenueFromContractWithCustomer"
             "ExcludingAssessedTax", "USD", 50),
            ("AAA", 900101, 2024, "Revenue", "http://example.com/ext:Sales", "USD", 5),
            ("AAA", 900101, 2024, "Revenue", f"{us}:Revenues", "USD", 10)]
    assert candidate_concepts_from_rows(rows) == {
        ("0000900101", 2024, "Revenue"): ["us-gaap:Revenues", REV]}


# --------------- the runtime original-or-reviewed check (reviewed-resolutions requirement 2)
# The original list may only shrink; an entry outside it needs a complete review record.

SPEC_LOCKED = REPO / "test" / "spec" / "key-statement-witness" / "fixtures" / "key_resolutions.yaml"
GROWN = """\
version: 1
resolutions:
  - family: Revenue
    cik: '0000900105'
    fiscal_year: 2024
    concept: us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax
    value: 900
    reading: key
    reason: an entry outside the original list with no review record
"""


def _write_fsds(root: Path) -> Path:
    """Writes the FSDS rows built here as one quarter directory of tsv files."""
    root.mkdir()
    for name, rows in _tables().items():
        header = list(rows[0]) if rows else ["adsh"]
        lines = ["\t".join(header)] + ["\t".join(r[c] for c in header) for r in rows]
        (root / f"{name}.tsv").write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    return root


def test_the_pinned_locked_list_equals_the_spec_lock():
    assert load_resolutions(V2 / "key_resolutions.locked.yaml") == load_resolutions(SPEC_LOCKED)


def test_a_grown_resolution_list_fails_before_any_key(tmp_path):
    grown = tmp_path / "key_resolutions.yaml"
    grown.write_text(GROWN, encoding="utf-8")
    # the FSDS directory does not exist: the check fails before it is read or a key chosen
    with pytest.raises(ValueError, match="not in the locked list"):
        KeyWitness.from_paths([tmp_path / "no_fsds"], resolutions=grown)


def test_the_committed_resolutions_pass_the_runtime_check(tmp_path):
    w = KeyWitness.from_paths([_write_fsds(tmp_path / "2025q1")])
    assert w.resolutions == load_resolutions(V2 / "key_resolutions.yaml")
    # the witness loaded from files keys as the in-memory one does
    keyed = _keyer(_inv({"AAA": {"Revenue": "us-gaap:Revenues"}}), w).key(
        Intent("lookup", tickers=["AAA"], fiscal_years=[2024], metrics=["Revenue"]))
    assert keyed["value"] == 1000.0
    assert w.manifest()["by_status"]["agree"] == 1


# --------------- every resolution covers a kept blocked key (reviewed-resolutions req. 3)

# A reviewed resolution of EEE's disagreeing Revenue key (900 against the presented 1,000),
# its evidence EEE's own "Total revenues" line as built in _tables().
EEE_RESOLUTION: dict[str, Any] = {
    "family": "Revenue", "cik": CIKS["EEE"], "fiscal_year": 2024, "concept": REV,
    "value": 900.0, "reading": "key", "reason": "the key is the component the filer tags",
    "review": {"by": "Sanjay", "date": "2026-11-10", "bucket": "definition",
               "evidence": {"kind": "statement", "adsh": _adsh("EEE"), "report": 2,
                            "line": 1, "plabel": "Total revenues"}}}


def _key_eee(w: KeyWitness) -> dict[str, Any]:
    return _keyer(_inv({"EEE": {"Revenue": REV}}), w).key(
        Intent("lookup", tickers=["EEE"], fiscal_years=[2024], metrics=["Revenue"]))


def test_a_reviewed_resolution_covering_a_kept_key_is_counted():
    w = _witness(resolutions=[EEE_RESOLUTION])
    assert _key_eee(w)["value"] == 900.0
    w.raise_if_blocked()
    assert w.resolution_use() == {
        "n": 1, "original": 0, "reviewed": 1,
        "by_bucket": {"rule_gap": 0, "combined_line": 0, "definition": 1, "note_unlisted": 0,
                      "by_design": 0}}


def test_evidence_on_a_line_the_filing_does_not_hold_fails():
    review = EEE_RESOLUTION["review"]
    bad = {**EEE_RESOLUTION,
           "review": {**review, "evidence": {**review["evidence"], "plabel": "Revenues"}}}
    w = _witness(resolutions=[bad])
    _key_eee(w)
    with pytest.raises(ValueError, match="holds no primary-statement line"):
        w.resolution_use()


def test_a_resolution_no_kept_key_uses_fails_the_build():
    # EEE's resolution covers nothing when the build keys only AAA
    inv = _inv({"AAA": {"Revenue": "us-gaap:Revenues"}})
    with pytest.raises(ValueError, match="covers no blocked key"):
        build_v2_suites(inv, {"covered": ["AAA"]}, _Sec(), _NoStore(), None, None,  # type: ignore[arg-type]  # nothing is phrased
                        quotas={}, per_intent=1, share=0.5, salt="s",
                        witness=_witness(resolutions=[EEE_RESOLUTION]))
