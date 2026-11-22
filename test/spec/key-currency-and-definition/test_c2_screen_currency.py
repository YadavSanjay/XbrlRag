"""C2: every member path of a cross-filer screen requires a USD reporting currency (K-R1).

Oracle: requirement 2 of docs/specs/key-currency-and-definition.md, S6 of
docs/design/XbrlRag_Eval_Semantics.md ("a filer that does not report it in USD is excluded
under S3 and named"), and the conftest's FACTS table, summed and ranked by hand.

Paths in the fixture: USDA and TWDK are keyed by the inventory (TWDK on its USD row); USDB and
TWDC have no key, so DebtIssued reaches them through the statement-line path and Debt through
the defined-family path (s4:debt_total).
"""

from __future__ import annotations

import pytest

from src.eval.suites.intents import Intent, intent_id
from src.eval.suites.v2_suite import build_v2_suites

# tolerance: whole-million fixture amounts; rel 1e-12 absorbs only binary floating point
REL = 1e-12
M = 1_000_000
QUOTAS = {"screen.sum": 10, "screen.top": 10, "peer_relative": 10}


def _build(kit, sec=None, universes=None):
    return build_v2_suites(kit.inventory(), universes or kit.UNIVERSES, sec or kit.FakeSec(),
                           kit.NoStore(), kit.phrasebook(), kit.lexicon(), quotas=QUOTAS,
                           per_intent=1, share=0.5, salt="kcd", witness=kit.witness())


def _case(build, op, metric, universe="semis"):
    n = 3 if op == "top" else None
    iid = intent_id(Intent("screen", operation=op, universe=universe, fiscal_years=[2023],
                           metrics=[metric], n=n))
    found = [c for s in ("dev", "validation") for c in build[s]["cases"]
             if f"intent:{iid}" in c["tags"]]
    assert len(found) == 1, f"{op} {metric}: {len(found)} cases"
    return found[0]


CURRENCY = [{"ticker": "TWDC", "reason": "currency"}, {"ticker": "TWDK", "reason": "currency"}]
UNTAGGED_TWDK = [{"ticker": "TWDC", "reason": "currency"},
                 {"ticker": "TWDK", "reason": "metric_not_tagged"}]


@pytest.mark.criterion("C2")
def test_debt_issued_sum_ranks_only_usd_reporters(kit):
    # oracle: FY2023 proceeds USDA 1,000 (keyed) + USDB 500 (statement line) = 1,500; TWDC
    # (statement line, USD 2,799 is a convenience translation) and TWDK (keyed on its USD
    # row, 80) report in TWD and are excluded for currency
    case = _case(_build(kit), "sum", "DebtIssued")
    assert case["expected_value"] == pytest.approx(1_500 * M, rel=REL)
    assert case["disclosures"]["ranked"] == ["USDA", "USDB"]
    assert case["disclosures"]["excluded"] == CURRENCY


@pytest.mark.criterion("C2")
def test_debt_sum_excludes_the_defined_path_too(kit):
    # oracle: FY2023 total debt USDA 200 + 3,000 = 3,200 (keyed), USDB 1,500 (defined path):
    # 4,700. TWDC's balance-sheet lines in USD (326 + 3,260) are a translation: currency.
    # TWDK presents no borrowing line, so no path ranks it and its reason is the path's own
    case = _case(_build(kit), "sum", "Debt")
    assert case["expected_value"] == pytest.approx(4_700 * M, rel=REL)
    assert case["disclosures"]["ranked"] == ["USDA", "USDB"]
    assert case["disclosures"]["excluded"] == UNTAGGED_TWDK


@pytest.mark.criterion("C2")
def test_top_rows_and_peer_rank(kit):
    # oracle: hand ranking of the USD reporters, descending: USDA 1,000, USDB 500. TWDK's own
    # peer-relative intent cannot rank its subject: dropped as subject_not_ranked
    build = _build(kit)
    rows = _case(build, "top", "DebtIssued")["expected_rows"]
    assert [(r["ticker"], r["value"]) for r in rows] == [
        ("USDA", pytest.approx(1_000 * M, rel=REL)), ("USDB", pytest.approx(500 * M, rel=REL))]
    dropped = build["manifest"]["intents"]["dropped_by_shape"]
    assert dropped.get("peer_relative") == {"subject_not_ranked": 1}


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
def test_a_usd_only_document_returns_the_member_to_its_path(kit):
    # oracle: requirement 2 — with TWDK's TWD facts removed its reporting currency is USD, so
    # the keyed path ranks it on its USD row (80): 1,580. TWDC and every Debt member are
    # unchanged (TWDK's Debt reason was its path's own already)
    build = _build(kit, sec=kit.FakeSec({"TWDK": frozenset({"TWD"})}))
    issued = _case(build, "sum", "DebtIssued")
    assert issued["expected_value"] == pytest.approx(1_580 * M, rel=REL)
    assert issued["disclosures"]["ranked"] == ["USDA", "USDB", "TWDK"]
    assert issued["disclosures"]["excluded"] == [{"ticker": "TWDC", "reason": "currency"}]
    debt = _case(build, "sum", "Debt")
    assert debt["expected_value"] == pytest.approx(4_700 * M, rel=REL)
    assert debt["disclosures"]["excluded"] == UNTAGGED_TWDK


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
def test_usd_reporters_are_untouched(kit):
    # oracle: requirement 2 — the check removes only the non-USD reporters: a universe of the
    # USD reporters alone gives the same values and ranking
    full = _build(kit)
    usd = _build(kit, universes={"semis": ["USDA", "USDB"]})
    for metric in ("DebtIssued", "Debt"):
        a, b = _case(full, "sum", metric), _case(usd, "sum", metric)
        assert a["expected_value"] == pytest.approx(b["expected_value"], rel=REL)
        assert a["disclosures"]["ranked"] == b["disclosures"]["ranked"]
        assert b["disclosures"]["excluded"] == []
