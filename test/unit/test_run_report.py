"""Unit tests for scripts/eval/run_report.py (synthetic reports, log lines and DB rows).

Covers the join of each case to its trace by query_id (audit rows first, then logs), the
cost recomputation against hand-computed values, escaping of hostile text, the body cap,
and the held-out refusal before any case is read. No DB, network or LLM.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "run_report_cli", REPO / "scripts" / "eval" / "run_report.py")
assert _spec is not None and _spec.loader is not None
rr = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = rr  # dataclasses resolve their module through sys.modules
_spec.loader.exec_module(rr)

# USD per 1,000 tokens, as in configs/llm/pricing.yaml.
PRICING = {
    "m-tiered": {
        "input": 0.003, "output": 0.015, "cached_input": 0.0003, "cache_write": 0.00375,
        "tiers": [{"above_prompt_tokens": 200_000, "input": 0.006, "output": 0.0225,
                   "cached_input": 0.0006, "cache_write": 0.0075}],
    },
    "m-plain": {"input": 0.002, "output": 0.008},
}


def _call(qid: str, model: str = "m-tiered", stage: str = "synthesizer", **kw) -> dict:
    ev = {"kind": "llm_call", "query_id": qid, "stage": stage, "model": model,
          "prompt_tokens": 10_000, "completion_tokens": 500, "cached_prompt_tokens": 4_000,
          "cache_write_tokens": 2_000, "cost_usd": 0.0282, "latency_ms": 1200.0,
          "system": "SYS", "user": "USER", "response": "RESP"}
    ev.update(kw)
    return ev


def _log_line(ev: dict | None = None, **raw) -> str:
    if ev is not None:
        return json.dumps({"ts": "2026-11-04 22:13:40,241", "level": "INFO",
                           "logger": "xbrlrag.events", "query_id": ev["query_id"],
                           "stage": ev.get("stage", "-"), "message": json.dumps(ev)})
    return json.dumps({"ts": "2026-11-04 22:13:41,000", "level": "INFO", **raw})


def _case(case_id: str, qid: str | None, grade: str = "CORRECT", **kw) -> dict:
    c = {"case_id": case_id, "query_id": qid, "grade": grade, "passed": grade == "CORRECT",
         "question": f"question {case_id}", "kind": "lookup", "ticker": "AAA", "fy": 2024,
         "reason": "ok", "expected": {"value": 1.0}, "cost_usd": 0.0282,
         "produced": {"text": f"answer {case_id}", "fact_ids": [1], "chunk_ids": []},
         "tags": ["split:dev"], "latency_ms": 1000.0}
    c.update(kw)
    return c


def _report(run_id: str, system: str, cases: list[dict], **kw) -> dict:
    r = {"run_id": run_id, "system": system, "suite": "xbrl_v2_dev", "split": "dev",
         "started_at": "2026-11-05T03:00:00+00:00", "finished_at": "2026-11-05T03:01:00+00:00",
         "status": "OK", "metrics": {"total_cost_usd": 0.0282 * len(cases)},
         "config": {"split": "dev"}, "results": cases}
    r.update(kw)
    return r


class FakeReader:
    """Stands in for the Postgres store: records which reads happened."""

    def __init__(self, run: dict | None = None, cases: list[dict] | None = None,
                 summaries: dict | None = None, events: dict | None = None) -> None:
        self.run, self.cases = run, cases or []
        self.summaries, self.events = summaries or {}, events or {}
        self.case_reads = 0
        self.audit_ids: list[str] = []

    def run_row(self, run_id):
        return self.run

    def case_rows(self, run_id):
        self.case_reads += 1
        return self.cases

    def audit(self, query_ids):
        self.audit_ids = list(query_ids)
        return ({q: s for q, s in self.summaries.items() if q in query_ids},
                {q: e for q, e in self.events.items() if q in query_ids})


# ---------------------------------------------------------------------------- join


def test_log_events_join_cases_by_query_id():
    index = rr.LogIndex()
    lines = [
        _log_line(_call("q1")),
        _log_line({"kind": "sql_query", "query_id": "q1", "stage": "plan_exec",
                   "source": "factstore_lookup", "sql": "SELECT 1", "n_rows": 1}),
        _log_line(_call("q2", stage="plan_generator")),
        _log_line(_call("q-other")),
        _log_line(logger="httpx", query_id="q1", message="HTTP Request: POST ..."),
        _log_line(logger="src.x", query_id="-", message="process-level"),
        "not json at all",
    ]
    rr.parse_log_lines(lines, "xbrlrag_run_suite_1.log", index)
    run_a = rr.run_from_report(_report("eval-a", "sys_a", [_case("c1", "q1")]), "a.json")
    run_b = rr.run_from_report(
        _report("eval-b", "sys_b", [_case("c2", "q2"), _case("c3", "q-missing")]), "b.json")

    rr.attach_events([run_a, run_b], index, None)

    c1 = run_a.cases[0]
    assert [e.get("kind") for e in c1["_events"]] == ["llm_call", "sql_query", None]
    assert c1["_events"][2]["_raw"] and c1["_events"][2]["logger"] == "httpx"
    assert c1["_event_source"] == "xbrlrag_run_suite_1.log"
    assert [e["stage"] for e in rr.llm_calls(run_b.cases[0])] == ["plan_generator"]
    assert run_b.cases[1]["_events"] == [] and run_b.cases[1]["_event_source"] == "none"
    assert run_a.log_files == {"xbrlrag_run_suite_1.log": 1}
    assert run_b.log_files == {"xbrlrag_run_suite_1.log": 1}


def test_audit_rows_win_over_logs_and_carry_the_answer():
    index = rr.LogIndex()
    rr.parse_log_lines([_log_line(_call("q1", stage="from_log"))], "x.log", index)
    answer = {"plan": {"steps": [{"id": "s1", "op": "lookup", "ticker": "AAA"}]},
              "execution_result": {"steps": [{"step_id": "s1", "status": "OK", "value": 5.0,
                                              "citation": {"fact_id": 77}}]},
              "answer_details": [{"statement": "AAA was 5.", "fact_ids": ["77"],
                                  "chunk_ids": []}],
              "answer_summary": "AAA was 5."}
    reader = FakeReader(
        summaries={"q1": {"query_id": "q1", "branch": "PLAN_GENERATOR", "answer": answer}},
        events={"q1": [_call("q1", stage="from_audit")]})
    run = rr.run_from_report(_report("eval-a", "sys_a", [_case("c1", "q1")]), "a.json")

    rr.attach_events([run], index, reader)

    case = run.cases[0]
    assert case["_event_source"] == "audit"
    assert [e["stage"] for e in rr.llm_calls(case)] == ["from_audit"]
    assert case["_answer"]["answer_summary"] == "AAA was 5."
    assert reader.audit_ids == ["q1"]
    doc = rr.render_report([run], PRICING)
    assert "Plan and step results" in doc and "Statements and citations" in doc
    assert "PLAN_GENERATOR" in doc


# ---------------------------------------------------------------------------- cost


def test_recompute_cost_against_hand_computed_values():
    # 4,000 uncached x 0.003 + 2,000 written x 0.00375 + 4,000 read x 0.0003
    # + 500 out x 0.015 = 12 + 7.5 + 1.2 + 7.5 = 28.2 per 1,000 -> $0.0282
    cc = rr.recompute_cost(_call("q"), PRICING)
    assert (cc.uncached_in, cc.cache_write, cc.cache_read, cc.output) == (4000, 2000, 4000, 500)
    assert cc.total == pytest.approx(0.0282, abs=1e-12)
    assert cc.parts == pytest.approx(
        {"uncached_in": 0.012, "cache_write": 0.0075, "cache_read": 0.0012, "output": 0.0075})
    assert not cc.differs


def test_recompute_cost_uses_the_prompt_size_tier():
    # Above 200k prompt tokens: 200,000 uncached x 0.006 + 100,000 read x 0.0006
    # + 1,000 out x 0.0225 = 1200 + 60 + 22.5 = 1282.5 per 1,000 -> $1.2825
    call = _call("q", prompt_tokens=300_000, cached_prompt_tokens=100_000,
                 cache_write_tokens=0, completion_tokens=1_000, cost_usd=1.2825)
    cc = rr.recompute_cost(call, PRICING)
    assert cc.total == pytest.approx(1.2825, abs=1e-12)
    assert cc.parts["cache_read"] == pytest.approx(0.06)
    assert sum(cc.parts.values()) == pytest.approx(cc.total)


def test_recompute_cost_cache_rates_fall_back_to_input_and_strings_are_read():
    # Log events may carry numbers as strings. 1,000 prompt (500 cached, billed at input)
    # x 0.002 + 250 out x 0.008 = 2 + 2 = 4 per 1,000 -> $0.004.
    call = _call("q", model="m-plain", prompt_tokens="1000", cached_prompt_tokens="500",
                 cache_write_tokens="0", completion_tokens="250", cost_usd="0.004")
    cc = rr.recompute_cost(call, PRICING)
    assert cc.total == pytest.approx(0.004, abs=1e-12)
    assert not cc.differs


def test_stored_cost_difference_and_unpriced_model_are_flagged():
    off = rr.recompute_cost(_call("q", cost_usd=0.05), PRICING)
    assert off.differs and off.total == pytest.approx(0.0282)
    unknown = rr.recompute_cost(_call("q", model="no-such-model"), PRICING)
    assert unknown.total is None and unknown.differs and "no pricing row" in unknown.error
    run = rr.run_from_report(_report("eval-a", "s", [_case("c1", "q1")]), "a.json")
    run.cases[0]["_events"] = [_call("q1", cost_usd=0.05)]
    doc = rr.render_report([run], PRICING)
    assert "<td class='n bad'>0.028200</td>" in doc
    assert "Σ stored call cost 0.050000 ≠ case cost 0.028200" in doc


# ---------------------------------------------------------------------------- escaping


def test_hostile_text_is_escaped_everywhere():
    hostile = "<script>alert(1)</script>"
    attr = "x' onmouseover='alert(2)"
    case = _case("c<1>", "q1", grade="INCORRECT", question=hostile,
                 reason="<img src=x onerror=alert(3)>",
                 produced={"text": hostile, "fact_ids": [], "chunk_ids": []})
    run = rr.run_from_report(_report("eval-<x>", attr, [case]), "a.json")
    run.cases[0]["_events"] = [
        _call("q1", model=attr, system=hostile, response="</pre><b>bold</b>"),
        {"kind": "sql_query", "query_id": "q1", "sql": "SELECT '<svg onload=alert(4)>'"},
        {"_raw": True, "query_id": "q1", "logger": "x", "level": "INFO", "message": hostile},
    ]
    doc = rr.render_report([run], PRICING)
    for raw in (hostile, "<img src=x", "<svg onload", "</pre><b>bold", "' onmouseover='",
                "<1>", "eval-<x>"):
        assert raw not in doc
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in doc
    assert "data-system='x&#x27; onmouseover=&#x27;alert(2)'" in doc
    assert doc.count("<script>") == 1  # the page's own filter script only


def test_bodies_are_capped_and_source_truncation_is_noted():
    run = rr.run_from_report(_report("eval-a", "s", [_case("c1", "q1")]), "a.json")
    run.cases[0]["_events"] = [_call("q1", user="U" * 5000,
                                     response="R" * 10 + "\n… [truncated 99 chars]")]
    doc = rr.render_report([run], PRICING, max_body_chars=1000)
    assert "U" * 1000 in doc and "U" * 1001 not in doc
    assert "shown 1,000 of 5,000 chars" in doc
    assert "already truncated at the source" in doc


# ---------------------------------------------------------------------------- held-out


@pytest.mark.parametrize("fields", [
    {"split": "heldout_filers", "config": {"split": "heldout_filers"}},
    {"split": "external", "config": {}},
    {"split": "legacy", "config": {"split": "temporal"}},
    {"split": "legacy", "suite": "heldout_b", "config": {}},
    {"split": "dev", "suite": "heldout_x", "config": {}},
])
def test_heldout_report_is_refused_and_nothing_is_written(tmp_path, fields, capsys):
    src = tmp_path / "run.json"
    src.write_text(json.dumps(_report("eval-h", "s", [_case("c1", "q1")], **fields)),
                   encoding="utf-8")
    out = tmp_path / "out.html"

    code = rr.main([str(src), "--out", str(out), "--no-audit"])

    assert code == 2
    assert not out.exists()
    assert "refused" in capsys.readouterr().err


@pytest.mark.parametrize("case_kw", [
    {"tags": ["split:heldout_filers"]},
    {"tags": ["set-b", "lookup"]},
    {"split": "external", "tags": []},
])
def test_heldout_case_inside_a_run_is_refused(case_kw):
    with pytest.raises(rr.HeldOutRefused):
        rr.run_from_report(_report("eval-a", "s", [_case("c1", "q1", **case_kw)]), "a.json")


def test_stored_heldout_run_is_refused_before_cases_are_read():
    reader = FakeReader(run={"run_id": "eval-h", "system": "s", "suite": "heldout_b",
                             "split": "legacy", "config": None},
                        cases=[_case("c1", "q1")])
    with pytest.raises(rr.HeldOutRefused):
        rr.run_from_db("eval-h", reader)
    assert reader.case_reads == 0


def test_stored_dev_run_renders_from_audit_rows(tmp_path):
    reader = FakeReader(run={"run_id": "eval-d", "system": "xbrlrag_full", "suite": "x",
                             "split": "dev", "config": '{"split": "dev"}',
                             "total_cost_usd": 0.0282},
                        cases=[_case("c1", "q1"), _case("c2", "q2", grade="REFUSED")],
                        events={"q1": [_call("q1")]})
    runs, got = rr.load_runs(["eval-d"], reader_factory=lambda: reader)
    rr.attach_events(runs, None, got)
    doc = rr.render_report(runs, PRICING)
    assert "data-grade='REFUSED'" in doc and "data-grade='CORRECT'" in doc
    assert runs[0].cases[0]["_event_source"] == "audit"
    assert runs[0].cases[1]["_event_source"] == "none"
