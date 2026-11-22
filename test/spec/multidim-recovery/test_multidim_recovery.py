"""Spec tests for multidim-recovery: a neutral consolidation member and an intersection
fallback for split sibling lookups (docs/specs/multidim-recovery.md).

Pure: the matchers, `_build_result` driven with rows as the multi-dimension query returns
them, `intersection_steps`, and `PlanExecutor.execute` over a fake fact store (the DB
boundary) that answers from the hints it is given.
"""

from __future__ import annotations

import itertools
import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.plan_executor import (
    PlanExecutor,
    StepResult,
    StepStatus,
    intersection_steps,
)
from src.retriever.xbrl.xbrl_factstore_lookup import (
    Citation,
    FactCandidate,
    FactLookupResult,
    LookupStatus,
    XbrlFactstoreLookup,
    dimensions_fully_matched,
    dimensions_match_ignoring_neutral,
    is_neutral_dimension,
)

CONS_2024 = "http://fasb.org/srt/2024:ConsolidationItemsAxis"
SEG_2024 = "http://fasb.org/us-gaap/2024:StatementBusinessSegmentsAxis"
CONS_2025 = "http://fasb.org/srt/2025:ConsolidationItemsAxis"
SEG_2025 = "http://fasb.org/us-gaap/2025:StatementBusinessSegmentsAxis"

# NVIDIA FY2025 OperatingIncomeLoss, fact 65496 (2026-10-14 corpus).
NVDA_LABELS = {CONS_2024: "Operating Segments [Member]", SEG_2024: "Graphics Segment [Member]"}
# AMD FY2025 OperatingIncomeLoss, fact 115042: raw local names, no label linkbase entry.
AMD_LABELS = {CONS_2025: "OperatingSegmentsMember", SEG_2025: "ClientAndGamingMember"}
# Apple FY2024 CashAndCashEquivalentsAtCarryingValue, fact 13665.
AAPL_LABELS = {
    "http://fasb.org/us-gaap/2024:FinancialInstrumentAxis": "Corporate Debt Securities [Member]",
    "http://fasb.org/us-gaap/2024:FairValueByFairValueHierarchyLevelAxis":
        "Fair Value, Inputs, Level 2 [Member]",
}
# Broadcom FY2024 revenue, fact 25183.
AVGO_LABELS = {
    "http://fasb.org/srt/2024:ProductOrServiceAxis": "Subscriptions and Services [Member]",
    "http://fasb.org/srt/2024:StatementGeographicalAxis": "Asia Pacific [Member]",
}
NOT_NEUTRAL_CONS = [
    "Intersegment Elimination [Member]",
    "Corporate, Non-Segment [Member]",
    "Segment Reconciling Items [Member]",
]


# ------------------------------------------------------------------ oracles (the rules)
def _norm(text: str) -> str:
    s = text.lower().replace("[member]", " ")
    s = re.sub(r"[\[\]]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _neutral(axis: str, label: str) -> bool:
    return axis.split(":")[-1] == "ConsolidationItemsAxis" and _norm(label).replace(
        " ", "") in {"operatingsegments", "operatingsegmentsmember"}


def _contained(hint: str, label: str) -> bool:
    h, lbl = _norm(hint), _norm(label)
    return h in lbl or h.replace(" ", "") in lbl.replace(" ", "")


def _oracle(labels, hints) -> bool:
    """The C2 rule written out by brute force over assignments."""
    if not hints:
        return False
    kept = [lbl for ax, lbl in (labels or {}).items()
            if not (_neutral(ax, lbl) and not any(_contained(h, lbl) for h in hints))]
    if len(kept) != len(hints):
        return False
    return any(all(_contained(h, lbl) for h, lbl in zip(hints, perm, strict=True))
               for perm in itertools.permutations(kept))


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_neutral_point_cases():
    # oracle: the definition, by hand on the 2026-10-14 labels
    assert is_neutral_dimension(CONS_2024, "Operating Segments [Member]") is True
    assert is_neutral_dimension(CONS_2025, "OperatingSegmentsMember") is True
    assert is_neutral_dimension("srt:ConsolidationItemsAxis", "operating segments") is True
    for label in NOT_NEUTRAL_CONS:
        assert is_neutral_dimension(CONS_2024, label) is False
    assert is_neutral_dimension(SEG_2024, "Operating Segments [Member]") is False
    assert is_neutral_dimension(CONS_2024, "Other Operating Segment [Member]") is False
    assert is_neutral_dimension(SEG_2024, "Other Operating Segment [Member]") is False


prefixes = st.sampled_from(["srt:", "http://fasb.org/srt/2023:", "http://fasb.org/srt/2025:",
                            "https://example.com/2025:"])
neutral_spellings = st.sampled_from(["Operating Segments [Member]", "OperatingSegmentsMember",
                                     "operating segments", "OPERATING SEGMENTS",
                                     "Operating  Segments", "[Operating Segments]"])


@pytest.mark.criterion("C1")
@given(prefixes, neutral_spellings, st.sampled_from(NOT_NEUTRAL_CONS))
def test_neutral_depends_only_on_axis_local_name_and_label(prefix, label, other):
    # oracle: the definition — any namespace, any spelling of the member; other
    # consolidation members and other axes are never neutral
    assert is_neutral_dimension(prefix + "ConsolidationItemsAxis", label) is True
    assert is_neutral_dimension(prefix + "StatementBusinessSegmentsAxis", label) is False
    assert is_neutral_dimension(prefix + "ConsolidationItemsAxis", other) is False


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
def test_match_point_cases():
    # oracle: the definition, by hand on the 2026-10-14 facts
    assert dimensions_match_ignoring_neutral(NVDA_LABELS, ["graphics"]) is True
    assert dimensions_match_ignoring_neutral(NVDA_LABELS, ["graphics segment"]) is True
    assert dimensions_match_ignoring_neutral(
        NVDA_LABELS, ["graphics", "operating segments"]) is True
    # Space-free containment: "client and gaming" in "ClientAndGamingMember".
    assert dimensions_match_ignoring_neutral(AMD_LABELS, ["client and gaming"]) is True
    # A non-neutral consolidation member is a dimension the hint does not cover.
    elim = {CONS_2024: "Intersegment Elimination [Member]", SEG_2024: "Graphics Segment [Member]"}
    assert dimensions_match_ignoring_neutral(elim, ["graphics"]) is False
    # One hint against a two-dimension fact with no neutral member: under-specified.
    assert dimensions_match_ignoring_neutral(AAPL_LABELS, ["level 2"]) is False
    assert dimensions_match_ignoring_neutral(
        AAPL_LABELS, ["corporate debt securities", "level 2"]) is True
    assert dimensions_match_ignoring_neutral(NVDA_LABELS, []) is False
    assert dimensions_match_ignoring_neutral(None, ["graphics"]) is False
    assert dimensions_match_ignoring_neutral({}, ["graphics"]) is False


words = st.sampled_from(["corporate", "debt", "securities", "level", "fair", "value",
                         "treasury", "agencies", "cash", "graphics", "compute", "client"])
label_words = st.lists(words, min_size=1, max_size=4, unique=True)


@st.composite
def matched_case(draw):
    """Labels plus hints that match them one-to-one by construction (each hint a run of
    consecutive words of its own label), optionally with a neutral dimension added."""
    n = draw(st.integers(min_value=1, max_value=3))
    labels, hints = {}, []
    for i in range(n):
        ws = draw(label_words)
        a = draw(st.integers(min_value=0, max_value=len(ws) - 1))
        b = draw(st.integers(min_value=a + 1, max_value=len(ws)))
        labels[f"x:Axis{i}"] = " ".join(ws) + " [Member]"
        hints.append(" ".join(ws[a:b]))
    if draw(st.booleans()):
        labels[draw(prefixes) + "ConsolidationItemsAxis"] = draw(neutral_spellings)
    return labels, hints


hint_lists = st.lists(st.lists(words, min_size=1, max_size=2).map(" ".join),
                      min_size=0, max_size=3)
plain_dim = label_words.map(lambda w: ("x:Axis", " ".join(w) + " [Member]"))
neutral_dim = neutral_spellings.map(lambda lbl: ("srt:ConsolidationItemsAxis", lbl))
# Axis keys made unique by a per-position namespace; the local name is what counts.
label_maps = st.lists(st.one_of(plain_dim, neutral_dim), max_size=3).map(
    lambda dims: {f"ns{i}/{ax}": lbl for i, (ax, lbl) in enumerate(dims)})


@pytest.mark.criterion("C2")
@given(matched_case(), label_maps, hint_lists)
def test_rule_for_any_labels_and_hints(case, labels, hints):
    # oracle: the C2 rule written out by brute force; the constructed case is a match
    # by construction, the free one may go either way
    c_labels, c_hints = case
    assert dimensions_match_ignoring_neutral(c_labels, c_hints) is True
    assert dimensions_match_ignoring_neutral(labels, hints) is _oracle(labels, hints)


# --------------------------------------------------------------------------- C3
def _row(fact_id, value, labels, concept, pref_rank=1, period_end="2025-01-26"):
    return {
        "fact_id": fact_id, "value": value, "unit": "USD", "period_type": "duration",
        "period_start": None, "period_end": period_end, "concept": concept,
        "concept_family": None, "concept_pref_rank": pref_rank, "segment_axis": None,
        "segment_member": None, "segment_label": None, "html_anchor": None,
        "page_num": None, "section_path": None, "accession_no": None,
        "linked_chunk_ids": [], "dimension_labels": labels,
    }


OPINC = "http://fasb.org/us-gaap/2024:OperatingIncomeLoss"


def _result(dims, rows):
    lk = XbrlFactstoreLookup()
    notes: list[str] = []
    specs, status = lk._parse_dimensions(dims, notes)
    assert status is None
    step = {"id": "s1", "op": "lookup", "dimensions": dims, "section_hint": None}
    return lk._build_result("s1", step, "0001045810", 2025, "OperatingIncome",
                            "duration", specs, rows, notes)


@pytest.mark.criterion("C3")
def test_one_hint_against_neutral_fact_resolves():
    # oracle: _score_confidence by hand — 0.4 (one value) + 0.2 (preferred concept)
    # + 0.2 (dimension matched) = 0.8 >= 0.70 -> OK; without the credit 0.6
    ok = _result({"segment": "graphics"}, [_row(65496, 5_085_000_000.0, NVDA_LABELS, OPINC)])
    assert ok.status.value == "OK"
    assert ok.value == pytest.approx(5_085_000_000.0, rel=1e-12)  # the stored fact, unrounded
    assert ok.confidence == pytest.approx(0.8, abs=1e-9)  # sum of fixed credits
    elim = {CONS_2024: "Intersegment Elimination [Member]", SEG_2024: "Graphics Segment [Member]"}
    no = _result({"segment": "graphics"}, [_row(65496, 5_085_000_000.0, elim, OPINC)])
    assert no.status.value == "AMBIGUOUS"
    assert no.confidence == pytest.approx(0.6, abs=1e-9)  # no dimension credit


@pytest.mark.criterion("C3")
@given(st.sampled_from(["graphics", "Graphics Segment", "graphics segment", "GRAPHICS"]),
       st.sampled_from(["Operating Segments [Member]", "OperatingSegmentsMember"]
                       + NOT_NEUTRAL_CONS))
def test_wording_does_not_matter_but_the_consolidation_member_does(hint, cons_label):
    # oracle: the rule — any contained wording credits the fact when the consolidation
    # member is neutral; any other consolidation member is an uncovered dimension
    labels = {CONS_2024: cons_label, SEG_2024: "Graphics Segment [Member]"}
    r = _result({"segment": hint}, [_row(65496, 5_085_000_000.0, labels, OPINC)])
    neutral = cons_label in ("Operating Segments [Member]", "OperatingSegmentsMember")
    assert r.status.value == ("OK" if neutral else "AMBIGUOUS")


# --------------------------------------------------------------------------- C4
AVGO = "0001730168"


def _lk(step_id, dims, fam="Revenue", cik=AVGO, fy=2024, **extra):
    return {"id": step_id, "op": "lookup", "cik": cik, "fy": fy, "concept_family": fam,
            "dimensions": dims, **extra}


def _res(step_id, status, op="lookup"):
    return StepResult(step_id=step_id, op=op, status=status)


BROADCOM_PLAN = [
    _lk("aprev24", {"geography": "asia pacific"}),
    _lk("subs24", {"product": "subscriptions and services"}),
]
BROADCOM_RESULTS = [_res("aprev24", StepStatus.AMBIGUOUS), _res("subs24", StepStatus.OK)]


@pytest.mark.criterion("C4")
def test_intersection_steps_point_cases():
    # oracle: the definition, by hand on the Broadcom FY2024 traced plan
    merged = intersection_steps(BROADCOM_PLAN, BROADCOM_RESULTS)
    assert merged == [{
        "id": "aprev24+subs24", "op": "lookup", "cik": AVGO, "fy": 2024,
        "concept_family": "Revenue", "fiscal_period": "FY", "ytd": False,
        "dimensions": {"member_1": "asia pacific", "member_2": "subscriptions and services"},
    }]
    # All siblings OK: separate questions, nothing merged.
    all_ok = [_res("aprev24", StepStatus.OK), _res("subs24", StepStatus.OK)]
    assert intersection_steps(BROADCOM_PLAN, all_ok) == []
    # A dimensionless step, a compute step and a different concept are never siblings.
    plan = [
        _lk("a", {"segment": "graphics"}, fam="OperatingIncome"),
        _lk("b", "NONE", fam="OperatingIncome"),
        {"id": "c", "op": "compute", "expr": "a / b"},
        _lk("d", {"segment": "operating segments"}, fam="Revenue"),
    ]
    res = [_res("a", StepStatus.NOT_FOUND), _res("b", StepStatus.OK),
           _res("c", StepStatus.ERROR, op="compute"), _res("d", StepStatus.AMBIGUOUS)]
    assert intersection_steps(plan, res) == []
    # Hints de-duplicated by normalised form; one distinct hint is not an intersection.
    dup = [_lk("a", {"segment": "Graphics"}), _lk("b", {"product": "graphics [Member]"})]
    assert intersection_steps(dup, [_res("a", StepStatus.NOT_FOUND),
                                    _res("b", StepStatus.NOT_FOUND)]) == []
    # Multi-key siblings contribute every value in plan order; period fields carried over.
    msft = [
        _lk("stcp26", {"instrument": "commercial paper"}, fam="ShortTermInvestments",
            fy=2026, fiscal_period="FY", ytd=False),
        _lk("dslev226", {"instrument": "debt securities", "level": "level 2"},
            fam="ShortTermInvestments", fy=2026, fiscal_period="FY", ytd=False),
    ]
    out = intersection_steps(msft, [_res("stcp26", StepStatus.NOT_FOUND),
                                    _res("dslev226", StepStatus.AMBIGUOUS)])
    assert [m["id"] for m in out] == ["stcp26+dslev226"]
    assert out[0]["dimensions"] == {"member_1": "commercial paper",
                                    "member_2": "debt securities", "member_3": "level 2"}
    # Different fiscal periods are different questions.
    q = [_lk("a", {"segment": "graphics"}, fiscal_period="Q2"),
         _lk("b", {"segment": "operating segments"})]
    assert intersection_steps(q, [_res("a", StepStatus.NOT_FOUND),
                                  _res("b", StepStatus.NOT_FOUND)]) == []


unrelated_steps = st.lists(st.sampled_from([
    _lk("u1", {"segment": "gaming"}, fam="OperatingIncome"),
    _lk("u2", "NONE"),
    {"id": "u3", "op": "compute", "expr": "aprev24 / u2"},
    _lk("u4", {"geography": "emea"}, fy=2025),
]), max_size=3, unique_by=lambda s: s["id"])


@pytest.mark.criterion("C4")
@given(unrelated_steps, st.integers(min_value=0, max_value=3),
       st.sampled_from([StepStatus.NOT_FOUND, StepStatus.AMBIGUOUS, StepStatus.OK]))
def test_unrelated_steps_do_not_change_the_merge(extra, pos, extra_status):
    # oracle: the definition — only same cik/fy/family/period dimensional lookups group
    steps = list(BROADCOM_PLAN)
    results = list(BROADCOM_RESULTS)
    for s in extra:
        at = min(pos, len(steps))
        steps.insert(at, s)
        results.insert(at, _res(s["id"], extra_status, op=s["op"]))
    merged = intersection_steps(steps, results)
    assert [m["id"] for m in merged] == ["aprev24+subs24"]
    assert merged[0]["dimensions"] == {"member_1": "asia pacific",
                                       "member_2": "subscriptions and services"}


# --------------------------------------------------------------------------- C5
def _candidate(fact_id, value, labels):
    return FactCandidate(
        fact_id=fact_id, value=value, unit="USD", period_type="duration", period_start=None,
        period_end="2024-11-03",
        concept="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        concept_family="Revenue", concept_pref_rank=1, segment_axis=None, segment_member=None,
        segment_label=None, section_path=None, citation=Citation(fact_id=fact_id),
        dimension_labels=labels)


class FakeFactStore:
    """The fact store (DB boundary): answers each sibling as traced, and a merged lookup
    with `merged_answer`. Records every step it is asked."""

    def __init__(self, sibling_status: dict, merged_answer: FactLookupResult):
        self.sibling_status = sibling_status
        self.merged_answer = merged_answer
        self.calls: list[dict] = []

    def lookup(self, step: dict) -> FactLookupResult:
        self.calls.append(step)
        sid = step["id"]
        if sid in self.sibling_status:
            status, value = self.sibling_status[sid]
            return FactLookupResult(step_id=sid, status=status, value=value, unit="USD",
                                    citation=Citation(fact_id=1), confidence=0.6)
        ans = self.merged_answer
        return FactLookupResult(step_id=sid, status=ans.status, value=ans.value, unit=ans.unit,
                                citation=ans.citation, confidence=ans.confidence,
                                candidates=ans.candidates)


BROADCOM_SIBLINGS = {"aprev24": (LookupStatus.AMBIGUOUS, 28_422_000_000.0),
                     "subs24": (LookupStatus.OK, 21_215_000_000.0)}


def _merged_ok(labels=AVGO_LABELS, status=LookupStatus.OK, candidates=True):
    return FactLookupResult(
        step_id="x", status=status, value=2_203_000_000.0, unit="USD",
        citation=Citation(fact_id=25183), confidence=0.8,
        candidates=[_candidate(25183, 2_203_000_000.0, labels)] if candidates else [])


@pytest.mark.criterion("C5")
def test_executor_appends_an_accepted_intersection():
    # oracle: the definition; 2,203,000,000 is the stored value of Broadcom fact 25183
    store = FakeFactStore(BROADCOM_SIBLINGS, _merged_ok())
    result = PlanExecutor(factstore_lookup=store).execute({"steps": BROADCOM_PLAN})
    assert [s.step_id for s in result.steps] == ["aprev24", "subs24", "aprev24+subs24"]
    added = result.steps[-1]
    assert added.op == "lookup" and added.status is StepStatus.OK
    assert added.value == pytest.approx(2_203_000_000.0, rel=1e-12)  # passed through unrounded
    assert added.citation["fact_id"] == 25183
    d = added.to_dict()
    assert d["derived_from"] == ["aprev24", "subs24"]
    assert d["dimensions"] == {"member_1": "asia pacific",
                               "member_2": "subscriptions and services"}
    assert result.bindings["aprev24+subs24"] == pytest.approx(2_203_000_000.0, rel=1e-12)
    # Siblings untouched.
    assert result.steps[0].status is StepStatus.AMBIGUOUS
    assert result.steps[1].value == pytest.approx(21_215_000_000.0, rel=1e-12)
    assert "derived_from" not in result.steps[1].to_dict()
    merged_calls = [c for c in store.calls if c["id"] == "aprev24+subs24"]
    assert len(merged_calls) == 1


@pytest.mark.criterion("C5")
def test_executor_rejects_or_skips():
    # oracle: the definition — reject unless OK with a one-to-one candidate; skip when
    # every sibling resolved
    baseline_ids = ["aprev24", "subs24"]
    extra = {**AVGO_LABELS, "x:FairValueByAssetClassAxis": "Debt Securities [Member]"}
    for answer in (_merged_ok(status=LookupStatus.AMBIGUOUS), _merged_ok(candidates=False),
                   _merged_ok(labels=extra)):
        store = FakeFactStore(BROADCOM_SIBLINGS, answer)
        result = PlanExecutor(factstore_lookup=store).execute({"steps": BROADCOM_PLAN})
        assert [s.step_id for s in result.steps] == baseline_ids
        assert "aprev24+subs24" not in result.bindings
    all_ok = {"aprev24": (LookupStatus.OK, 1.0), "subs24": (LookupStatus.OK, 2.0)}
    store = FakeFactStore(all_ok, _merged_ok())
    result = PlanExecutor(factstore_lookup=store).execute({"steps": BROADCOM_PLAN})
    assert [c["id"] for c in store.calls] == baseline_ids
    assert result.bindings == {"aprev24": pytest.approx(1.0), "subs24": pytest.approx(2.0)}
    # And the accepted path is reachable from the same fixture.
    store = FakeFactStore(BROADCOM_SIBLINGS, _merged_ok())
    assert len(PlanExecutor(factstore_lookup=store).execute({"steps": BROADCOM_PLAN}).steps) == 3


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(st.booleans(), st.sampled_from([LookupStatus.NOT_FOUND, LookupStatus.AMBIGUOUS]))
def test_executor_result_independent_of_sibling_order(reverse, failed_status):
    # oracle: metamorphic — the accepted fact does not depend on plan order; the merged id
    # and derived_from follow plan order
    plan = list(reversed(BROADCOM_PLAN)) if reverse else list(BROADCOM_PLAN)
    siblings = {**BROADCOM_SIBLINGS, "aprev24": (failed_status, None)}
    store = FakeFactStore(siblings, _merged_ok())
    result = PlanExecutor(factstore_lookup=store).execute({"steps": plan})
    ids = [s["id"] for s in plan]
    added = result.steps[-1]
    assert added.step_id == "+".join(ids)
    assert added.to_dict()["derived_from"] == ids
    assert added.value == pytest.approx(2_203_000_000.0, rel=1e-12)  # passed through unrounded
    assert added.citation["fact_id"] == 25183


# --------------------------------------------------------------------------- C6
@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
@given(matched_case(), st.randoms(use_true_random=False), neutral_spellings, prefixes)
def test_matcher_metamorphic(case, rnd, neutral_label, prefix):
    # oracle: metamorphic — order and case are irrelevant; a neutral dimension the hints do
    # not name changes nothing; an uncovered non-neutral dimension removes the match
    labels, hints = case
    assert dimensions_match_ignoring_neutral(labels, hints) is True
    items = list(labels.items())
    rnd.shuffle(items)
    shuffled = list(hints)
    rnd.shuffle(shuffled)
    assert dimensions_match_ignoring_neutral(dict(items), shuffled) is True
    assert dimensions_match_ignoring_neutral(
        {k: v.upper() for k, v in labels.items()}, [h.upper() for h in hints]) is True
    no_neutral = {k: v for k, v in labels.items() if not _neutral(k, v)}
    with_neutral = {**no_neutral, prefix + "ConsolidationItemsAxis": neutral_label}
    assert dimensions_match_ignoring_neutral(with_neutral, hints) is True
    grown = {**labels, "x:OtherAxis": "Something Else Entirely [Member]"}
    assert dimensions_match_ignoring_neutral(grown, hints) is False


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
@given(st.lists(label_words.map(lambda w: " ".join(w) + " [Member]"), min_size=2, max_size=3),
       st.data())
def test_superset_of_full_match(label_list, data):
    # oracle: metamorphic — every one-to-one full match (multidim-full-match) is also a
    # match once neutral members are set aside
    labels = {f"x:Axis{i}": lbl for i, lbl in enumerate(label_list)}
    hints = [data.draw(st.sampled_from(lbl.replace(" [Member]", "").split()))
             for lbl in label_list]
    full = [lbl.replace(" [Member]", "") for lbl in label_list]
    assert dimensions_fully_matched(labels, full) is True
    assert dimensions_match_ignoring_neutral(labels, full) is True
    if dimensions_fully_matched(labels, hints):
        assert dimensions_match_ignoring_neutral(labels, hints) is True


@pytest.mark.criterion("C6")
def test_metamorphic_point_case():
    # oracle: the definition on NVIDIA 65496 — reordering keeps the match; swapping the
    # neutral member for Intersegment Elimination removes it
    assert dimensions_match_ignoring_neutral(
        dict(reversed(list(NVDA_LABELS.items()))), ["Graphics"]) is True
    assert dimensions_match_ignoring_neutral(
        {**NVDA_LABELS, CONS_2024: "Intersegment Elimination [Member]"}, ["graphics"]) is False
