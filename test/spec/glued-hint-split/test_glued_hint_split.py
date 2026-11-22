"""Spec tests for glued-hint-split: a NOT_FOUND lookup whose hint glues two members
("Family of Apps and Service, Other") is retried with the hint split in two, and accepted
only on a one-to-one dimension match (docs/specs/glued-hint-split.md).

The fact store is faked at the DB boundary (as in multidim-recovery); the executor, the
calculator and the dimension matcher run for real.
"""

from __future__ import annotations

import copy
import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.plan_executor import PlanExecutor, StepStatus, split_glued_hints
from src.retriever.xbrl.xbrl_factstore_lookup import (
    Citation,
    FactCandidate,
    FactLookupResult,
    LookupStatus,
)

META = "0001326801"
OK, NF = LookupStatus.OK, LookupStatus.NOT_FOUND
AMB, UNRES = LookupStatus.AMBIGUOUS, LookupStatus.UNRESOLVED_DIMENSION
SEG = "http://fasb.org/us-gaap/2025:StatementBusinessSegmentsAxis"
PROD = "http://fasb.org/srt/2025:ProductOrServiceAxis"
GLUED = {"product": "Family of Apps and Service, Other"}
SPLIT = {"product_a": "Family of Apps", "product_b": "Service, Other"}
# Meta FY2025 fact 41640 (replayed through the real lookup, 2026-10-17).
LABELS = {SEG: "Family of Apps [Member]", PROD: "Service, Other [Member]"}
VALUE = 2_584_000_000.0


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
@pytest.mark.parametrize("dims,expected", [
    ({"product": "Advertising and Family of Apps"},
     [{"product_a": "Advertising", "product_b": "Family of Apps"}]),
    (GLUED, [SPLIT]),
    ({"instrument": "certificates of deposit and debt securities", "fair_value_level": "level 2"},
     [{"fair_value_level": "level 2", "instrument_a": "certificates of deposit",
       "instrument_b": "debt securities"}]),
    ({"x": "Research and Development Expense and 2022 Restructuring"},
     [{"x_a": "Research", "x_b": "Development Expense and 2022 Restructuring"},
      {"x_a": "Research and Development Expense", "x_b": "2022 Restructuring"}]),
    ({"x": "Cloud AND Gaming"}, [{"x_a": "Cloud", "x_b": "Gaming"}]),
])
def test_split_point_cases(dims, expected):
    # oracle: Requirement 1 by hand — one variant per usable separator, left to right
    before = copy.deepcopy(dims)
    assert split_glued_hints(dims) == expected
    assert dims == before  # never mutated


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("dims", [
    {"x": "Brand Name"}, {"x": "Operand"}, {"x": "and services"}, {"x": "services and"},
    {"x": "a and b", "x_a": "c"}, "NONE", {}, None,
])
def test_nothing_to_split(dims):
    # oracle: Requirement 1 — no separator, an empty part, a key collision or no dict
    assert split_glued_hints(GLUED) == [SPLIT]  # control
    assert split_glued_hints(dims) == []


_WORD = st.text(alphabet="bcdefghijklmopqrstuvwxyz", min_size=1, max_size=6)


@pytest.mark.criterion("C1")
@given(st.lists(_WORD, min_size=2, max_size=8), st.sampled_from(["and", "AND", "And"]),
       st.sampled_from(["product", "segment", "k"]))
def test_variant_count_and_shape(words, sep, key):
    # oracle: Requirement 1 as properties — min(4, separators) variants, one extra key each,
    # and the two parts rejoin to the hint
    hint = f" {sep} ".join(words)
    dims = {key: hint, "other": "level 2"}
    before = copy.deepcopy(dims)
    out = split_glued_hints(dims)
    assert dims == before
    assert len(out) == min(4, len(words) - 1)
    for i, v in enumerate(out):
        assert set(v) == {"other", f"{key}_a", f"{key}_b"}
        assert v["other"] == "level 2"
        rejoined = re.sub(r"\s+and\s+", " and ", f"{v[key + '_a']} and {v[key + '_b']}",
                          flags=re.IGNORECASE).lower()
        assert rejoined == re.sub(r"\s+and\s+", " and ", hint, flags=re.IGNORECASE).lower()
        assert v[f"{key}_a"].lower() == f" {sep} ".join(words[: i + 1]).lower()


# --------------------------------------------------------------------------- C2 / C3
def _candidate(labels, fact_id=41640, value=VALUE):
    return FactCandidate(
        fact_id=fact_id, value=value, unit="USD", period_type="duration", period_start=None,
        period_end="2025-12-31",
        concept="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        concept_family="Revenue", concept_pref_rank=1, segment_axis=None, segment_member=None,
        segment_label=None, section_path=None, citation=Citation(fact_id=fact_id),
        dimension_labels=labels)


def _answer(status, labels=LABELS, fact_id=41640, value=VALUE, candidates=True):
    return (status, value if status is not NF else None, fact_id,
            [_candidate(labels, fact_id, value)] if candidates and status is not NF else [])


class FakeFactStore:
    """The fact store (DB boundary): answers by the step's dimensions; records each call."""

    def __init__(self, answers: dict):
        self.answers = {self._key(k): v for k, v in answers.items()}
        self.calls: list[dict] = []

    @staticmethod
    def _key(dims):
        return tuple(sorted(dims.items())) if isinstance(dims, dict) else dims

    def lookup(self, step: dict) -> FactLookupResult:
        self.calls.append(copy.deepcopy(step))
        status, value, fid, cands = self.answers.get(self._key(step["dimensions"]),
                                                     (NF, None, None, []))
        return FactLookupResult(
            step_id=step["id"], status=status, value=value,
            unit="USD" if value is not None else None,
            citation=Citation(fact_id=fid) if value is not None else None,
            confidence=0.8 if status is OK else (0.4 if status is AMB else 0.0),
            candidates=cands)


def _step(dims=None, sid="m25", fy=2025):
    return {"id": sid, "op": "lookup", "cik": META, "fy": fy, "fiscal_period": "FY",
            "concept_family": "Revenue", "dimensions": GLUED if dims is None else dims}


PLAN = [_step(), {"id": "x2", "op": "compute", "expr": "m25 * 2"}]


def _answers(split_answer=None, glued=UNRES):
    return {tuple(sorted(GLUED.items())): (glued, None, None, []),
            tuple(sorted(SPLIT.items())): split_answer or _answer(OK)}


def _run(answers, plan=PLAN):
    store = FakeFactStore({})
    store.answers = answers
    return PlanExecutor(factstore_lookup=store).execute({"steps": plan}), store


@pytest.mark.criterion("C2")
def test_meta_glued_hint_resolves_through_the_split():
    # oracle: corpus fact 41640 = 2,584,000,000 USD; x2 = 2 × 2,584M by hand
    result, _ = _run(_answers())
    m25 = result.step("m25")
    assert m25.op == "lookup" and m25.status is StepStatus.OK
    assert m25.value == pytest.approx(VALUE, rel=1e-12)  # the fact's value, unchanged
    assert m25.unit == "USD"
    assert (m25.citation or {}).get("fact_id") == 41640
    assert m25.confidence == pytest.approx(0.8, abs=1e-9)  # the split lookup's confidence
    assert result.bindings["m25"] == pytest.approx(VALUE, rel=1e-12)  # bound
    assert result.step("x2").value == pytest.approx(2 * VALUE, rel=1e-12)  # exact double


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("split_answer", [
    _answer(AMB),
    _answer(NF),
    _answer(OK, candidates=False),
    _answer(OK, labels={SEG: "Family of Apps [Member]"}),
    _answer(OK, labels={**LABELS, "x:LegalEntityAxis": "Meta Platforms Ireland [Member]"}),
])
def test_split_answer_rejected_unless_ok_and_one_to_one(split_answer):
    # oracle: Requirement 2 — OK and a one-to-one match, or the step keeps its NOT_FOUND
    control, _ = _run(_answers())
    assert control.step("m25").status is StepStatus.OK  # the accepted path is reachable
    result, _ = _run(_answers(split_answer))
    m25 = result.step("m25")
    assert m25.status is StepStatus.NOT_FOUND and m25.value is None
    assert "m25" not in result.bindings or result.bindings["m25"] is None


@pytest.mark.criterion("C2")
@given(st.sampled_from([OK, NF, AMB]), st.booleans(), st.sampled_from([UNRES, NF]))
def test_ok_exactly_when_accepted(status, full_labels, glued_status):
    # oracle: Requirement 2 as a truth table over the split answer's status and labels
    labels = LABELS if full_labels else {PROD: "Service, Other [Member]"}
    result, _ = _run(_answers(_answer(status, labels=labels), glued=glued_status))
    m25 = result.step("m25")
    if status is OK and full_labels:
        assert m25.status is StepStatus.OK
        assert m25.value == pytest.approx(VALUE, rel=1e-12)  # the fact's value
    else:
        assert m25.status is StepStatus.NOT_FOUND and m25.value is None
    control, _ = _run(_answers())
    assert control.step("m25").status is StepStatus.OK  # accepted path reachable


@pytest.mark.criterion("C2")
def test_first_accepted_variant_wins():
    # oracle: Requirement 2 — variants tried in order; the first rejected, the second accepted
    glued = {"x": "Research and Development Expense and 2022 Restructuring"}
    v1 = {"x_a": "Research", "x_b": "Development Expense and 2022 Restructuring"}
    v2 = {"x_a": "Research and Development Expense", "x_b": "2022 Restructuring"}
    labels = {"x:IncomeStatementLocationAxis": "Research and Development Expense [Member]",
              "x:RestructuringPlanAxis": "2022 Restructuring [Member]"}
    answers = {tuple(sorted(glued.items())): (UNRES, None, None, []),
               tuple(sorted(v1.items())): _answer(AMB, labels=labels, fact_id=1, value=1.0),
               tuple(sorted(v2.items())): _answer(OK, labels=labels, fact_id=7, value=254e6)}
    result, store = _run(answers, [_step(glued, sid="r24")])
    r24 = result.step("r24")
    assert r24.status is StepStatus.OK
    assert (r24.citation or {}).get("fact_id") == 7
    assert r24.value == pytest.approx(254e6, rel=1e-12)  # the second variant's fact
    assert [c["dimensions"] for c in store.calls] == [glued, v1, v2]


@pytest.mark.criterion("C3")
def test_calls_per_scope():
    # oracle: Requirement 3 — retries only for a NOT_FOUND step with variants, in order,
    # stopping at the first accepted one
    _, store = _run(_answers())
    assert [c["dimensions"] for c in store.calls] == [GLUED, SPLIT]
    assert all(c["id"] == "m25" for c in store.calls)
    glued2 = {"x": "a and b and c"}
    v1, v2 = {"x_a": "a", "x_b": "b and c"}, {"x_a": "a and b", "x_b": "c"}
    _, store = _run({tuple(sorted(glued2.items())): (NF, None, None, [])}, [_step(glued2)])
    assert [c["dimensions"] for c in store.calls] == [glued2, v1, v2]
    for status in (OK, AMB):
        _, store = _run({**_answers(), tuple(sorted(GLUED.items())): _answer(status)})
        assert [c["dimensions"] for c in store.calls] == [GLUED]
    plain = {"product": "Service, Other"}
    _, store = _run({}, [_step(plain)])
    assert [c["dimensions"] for c in store.calls] == [plain]


@pytest.mark.criterion("C3")
def test_at_most_four_retries():
    # oracle: Requirement 3 — the fan-out is bounded at 4 variants
    _, control = _run(_answers())
    assert len(control.calls) == 2  # control: one retry when one variant exists
    glued = {"x": " and ".join("abcdefg")}  # 6 separators
    _, store = _run({}, [_step(glued)])
    assert len(store.calls) == 1 + 4
    assert store.calls[0]["dimensions"] == glued


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(st.sampled_from([
    _step({"product": "Service, Other"}, sid="p1", fy=2024),  # fy 2024: never a sibling
    _step("NONE", sid="p2"),
    _step({"segment": "Reality Labs"}, sid="p3", fy=2024),
]))
def test_adding_a_step_without_separator_adds_one_call(extra):
    # oracle: metamorphic — a step with no variants costs exactly its own lookup
    _, base = _run(_answers())
    _, more = _run(_answers(), [_step(), extra])
    assert [c["dimensions"] for c in base.calls] == [GLUED, SPLIT]
    assert len(more.calls) == len(base.calls) + 1
    assert more.calls[-1]["id"] == extra["id"]
