"""C5: ``/metrics`` exports refusals (requirement 5).

The exporter is real; its row source is the fake ``FakeReader`` (conftest.py). Samples are
parsed from the Prometheus text exposition. The metric namespace prefix is whatever the
exporter already puts on ``llm_errors_total``, so the test does not restate it.
"""

from __future__ import annotations

import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.observability.metrics_exporter import render_exposition

_SAMPLE = re.compile(r"^(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)(?:\{(?P<labels>[^}]*)\})? "
                     r"(?P<value>\S+)$")
_LABEL = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


def _samples(text: str, suffix: str) -> list[tuple[str, dict[str, str], str]]:
    """Every sample line whose metric name ends with ``_<suffix>`` (or is it)."""
    out = []
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        m = _SAMPLE.match(line)
        if m and (m["name"] == suffix or m["name"].endswith("_" + suffix)):
            out.append((m["name"], dict(_LABEL.findall(m["labels"] or "")), m["value"]))
    return out


def _by_stage_model(samples) -> dict[tuple[str, str], str]:
    return {(labels.get("stage"), labels.get("model")): value for _n, labels, value in samples}


def _row(stage: str, model: str, *, calls: int, errors: int, refusals: int) -> dict:
    return {"stage": stage, "provider": "fake", "model": model, "model_series": None,
            "model_tier": None, "prompt_version": None, "calls": calls, "errors": errors,
            "refusals": refusals, "retries": 0, "avg_latency_ms": 10, "p95_latency_ms": 20,
            "prompt_tokens": 100, "completion_tokens": 10, "cost_usd": 0}


@pytest.mark.criterion("C5")
def test_refusals_counter_family_and_samples(fake_reader):
    # oracle: requirement 5 — one counter family llm_refusals_total with labels stage and
    # model, one sample per v_llm_calls row equal to its refusals column (3 and 0); the
    # errors samples are still the errors column (1 and 2); model_cost_usd help text says
    # "billed calls"
    reader = fake_reader({
        "v_llm_calls": [
            _row("synthesizer", "claude-opus-5-5", calls=10, errors=1, refusals=3),
            _row("plan_generator", "gpt-5-mini", calls=7, errors=2, refusals=0),
        ],
        "v_model_cost": [{"model": "claude-opus-5-5", "calls": 10, "prompt_tokens": 100,
                          "completion_tokens": 10, "cost_usd": 1.25}],
    })
    text = render_exposition(reader)

    errors = _samples(text, "llm_errors_total")
    assert errors, "the exporter lost llm_errors_total"
    prefix = errors[0][0][: -len("llm_errors_total")]
    assert f"# TYPE {prefix}llm_refusals_total counter" in text.splitlines()
    refusals = _samples(text, "llm_refusals_total")
    assert all(name == f"{prefix}llm_refusals_total" for name, _l, _v in refusals)
    assert len(refusals) == 2
    assert all(set(labels) == {"stage", "model"} for _n, labels, _v in refusals)
    assert _by_stage_model(refusals) == {
        ("synthesizer", "claude-opus-5-5"): "3",
        ("plan_generator", "gpt-5-mini"): "0",
    }
    assert _by_stage_model(errors) == {
        ("synthesizer", "claude-opus-5-5"): "1",
        ("plan_generator", "gpt-5-mini"): "2",
    }
    help_lines = [line for line in text.splitlines()
                  if line.startswith(f"# HELP {prefix}model_cost_usd ")]
    assert len(help_lines) == 1
    assert "billed calls" in help_lines[0]
    assert "successful calls" not in help_lines[0]


_NAME = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_.-", min_size=1, max_size=16)
_COUNT = st.integers(min_value=0, max_value=1_000_000)


@pytest.mark.criterion("C5")
@given(rows=st.lists(st.tuples(_NAME, _NAME, _COUNT, _COUNT), min_size=1, max_size=8,
                     unique_by=lambda r: (r[0], r[1])))
def test_one_refusals_sample_per_row(fake_reader, rows):
    # oracle: requirement 5 — exactly one llm_refusals_total sample per v_llm_calls row,
    # its value the row's refusals (an integer renders without a decimal point), and
    # llm_errors_total still one sample per row equal to errors
    reader = fake_reader({"v_llm_calls": [
        _row(stage, model, calls=errors + refusals + 1, errors=errors, refusals=refusals)
        for stage, model, errors, refusals in rows
    ]})
    text = render_exposition(reader)
    refusals = _samples(text, "llm_refusals_total")
    errors = _samples(text, "llm_errors_total")
    assert len(refusals) == len(rows)
    assert _by_stage_model(refusals) == {(s, m): str(r) for s, m, _e, r in rows}
    assert len(errors) == len(rows)
    assert _by_stage_model(errors) == {(s, m): str(e) for s, m, e, _r in rows}
    type_lines = [line for line in text.splitlines()
                  if line.startswith("# TYPE ") and line.split()[2].endswith("llm_refusals_total")]
    assert len(type_lines) == 1
    assert type_lines[0].endswith(" counter")
