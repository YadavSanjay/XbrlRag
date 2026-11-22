"""The lookup's stated substitution and its way to the synthesizer
(docs/specs/concept-family-coverage.md R3). Pure: no DB, no LLM."""

from __future__ import annotations

from prompts.prompt_registry import get_xbrl_registry
from src.retriever.xbrl.plan_executor import StepResult, StepStatus
from src.retriever.xbrl.xbrl_factstore_lookup import XbrlFactstoreLookup

G = "http://fasb.org/us-gaap/2025:"
NOTE = "Depreciation only; amortization is not included."


def _row(fact_id, value, concept, rank, role=None, presented=True, unit="USD", code=None):
    return {"fact_id": fact_id, "value": value, "unit": unit, "period_type": "duration",
            "period_start": None, "period_end": "2026-01-01", "concept": concept,
            "concept_family": "DepreciationAndAmortization", "concept_pref_rank": rank,
            "concept_role": role, "substitution_code": code,
            "substitution_note": NOTE if code else None, "presented": presented,
            "segment_axis": None, "segment_member": None, "segment_label": None,
            "html_anchor": None, "page_num": None, "section_path": None,
            "accession_no": None, "linked_chunk_ids": [], "decimals": -6}


def _result(rows, family="DepreciationAndAmortization"):
    lk = XbrlFactstoreLookup()
    notes: list[str] = []
    specs, _ = lk._parse_dimensions("NONE", notes)
    step = {"id": "s1", "op": "lookup", "dimensions": "NONE", "section_hint": None}
    return lk._build_result("s1", step, "0000000001", 2025, family, "duration", specs,
                            rows, notes)


def test_a_substitute_answer_carries_its_note():
    r = _result([_row(1, 5e9, G + "Depreciation", 50, "substitute", code="depreciation_only"),
                 _row(2, 1e9, G + "AmortizationOfIntangibleAssets", 51, "component")])
    assert r.status.value == "OK"
    assert r.citation.fact_id == 1
    assert r.substitution == {"code": "depreciation_only", "note": NOTE,
                              "concept": G + "Depreciation"}
    assert r.to_dict()["substitution"] == r.substitution


def test_rows_without_roles_never_answer_with_a_component():
    # Before the reload the view has no roles: a rank in 50-99 is a component.
    r = _result([_row(1, 5e9, G + "Depreciation", 50)])
    assert r.status.value == "NOT_FOUND"
    assert r.substitution is None
    assert any("component" in n for n in r.resolution_notes)


def test_a_per_share_unit_is_shown_per_share():
    r = _result([_row(7, 22.95, G + "EarningsPerShareDiluted", 1, "primary",
                      unit="shares*USD")], family="EarningsPerShareDiluted")
    assert (r.status.value, r.unit) == ("OK", "USD/shares")
    assert r.candidates[0].unit == "shares*USD"  # candidates keep the stored unit


def test_the_step_hands_the_substitution_to_the_synthesizer():
    sub = {"code": "depreciation_only", "note": NOTE, "concept": G + "Depreciation"}
    step = StepResult("s1", "lookup", StepStatus.OK, value=5e9, unit="USD", substitution=sub)
    assert step.to_dict()["substitution"] == sub
    assert "substitution" not in StepResult("s2", "lookup", StepStatus.OK).to_dict()


def test_the_synthesizer_prompt_states_the_note():
    prompt = get_xbrl_registry().resolve("synthesizer", provider="openai", series="gpt-5")
    assert '"substitution"' in prompt.system.template and '"note"' in prompt.system.template
    assert "substitution" in prompt.user.template
    prompt.system.format(llm_answer_format="{}")  # placeholders unchanged
    prompt.user.format(user_query="q", plan_json="{}", fact_results="[]", context_chunks="[]")
