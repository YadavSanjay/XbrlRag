"""C6: the explicit model, the call's stage, the v1 prompt and system selection.

The tier-mapping check runs the arm in a child Python process per switch value, because
``src.llm.config`` reads ``XBRLRAG_USE_REPLACEMENT_MODELS`` once, at import: setting the
variable in this process after import would change nothing, and patching the config
module is forbidden in spec tests. The child imports the config fresh under the switch
(it reports the switch it saw and that set's frontier model, so the toggle is shown to
have taken effect) and builds the arm with this directory's fakes. The expected model
is the literal ``"gpt-5.6-sol"`` from requirement 4, never a value read from config.

arm-model-from-profile replaces requirement 4's literal default with the active profile's
frontier model, built when the arm is (docs/specs/arm-model-from-profile.md), and pins
that in its own C1. The default-model checks here hold on both sides: the default is a
priced model, and under the legacy switch it is either the old literal or that set's
frontier model. An explicit model is still used as given.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from prompts.prompt_registry import get_xbrl_registry
from src.eval.core.case import EvalCase
from src.eval.core.system import SystemUnderTest
from src.eval.systems import build_system, system_names
from src.eval.systems.long_context import LongContextSystem
from src.llm.config import MODEL_PRICING
from src.llm.gateway import resolve_model_ref

NAMES = ("xbrlrag_full", "closed_book", "naive_rag_plain", "naive_rag_filtered", "long_context")
OTHER_NAMES = tuple(n for n in NAMES if n != "long_context")
# long-context-arms: the arm is stored as long_context_oracle; long_context still builds it.
LONG_CONTEXT_NAMES = ("long_context", "long_context_oracle")
# Arms added by later specs: long-context-arms (oracle, resolved, multi), html-rag-arm and
# frontier-sec-tools. Each is a valid system name, so none may be drawn as an unknown one.
LATER_ARMS = (
    "long_context_oracle",
    "long_context_resolved",
    "long_context_multi",
    "html_rag",
    "frontier_sec_tools",
)
DEFAULT_MODEL = "gpt-5.6-sol"
# A priced model other than the default (MODEL_PRICING has an entry for it).
OTHER_PRICED_MODEL = "gpt-5"
# llm-provider-profiles prices claude-opus-5-5, so both examples are invented IDs.
UNPRICED_MODELS = ("claude-zz-invented-9", "gpt-zz-invented-9")

SPEC_DIR = Path(__file__).resolve().parent
REPO_ROOT = SPEC_DIR.parents[2]
_RESULT_TAG = "LCRESULT "


def _case() -> EvalCase:
    return EvalCase(
        id="c6", question="What was AAA total revenue in fiscal 2024?", kind="lookup",
        ticker="AAA", fy=2024,
    )


@pytest.mark.criterion("C6")
def test_system_names_exact():
    # oracle: C6's literal tuple — long_context appended last. long-context-arms renames it and
    # adds two, and pins the exact tuple in its own C1; this holds on both sides.
    assert system_names()[:4] == NAMES[:4]
    assert system_names()[4] in LONG_CONTEXT_NAMES


@pytest.mark.criterion("C6")
def test_build_long_context_offline(offline_env):
    # oracle: requirement 8 — every endpoint points at a closed local port, so a build
    # that reached a backend would fail here; answer() is never called
    assert system_names()[4] in LONG_CONTEXT_NAMES
    for system in (build_system("long_context"),
                   build_system("long_context", model=OTHER_PRICED_MODEL)):
        assert isinstance(system, SystemUnderTest)
        assert system.name in LONG_CONTEXT_NAMES


@pytest.mark.criterion("C6")
def test_constructor_name_offline(offline_env):
    # oracle: the Interface section — the arm's name; constructing does no I/O
    assert LongContextSystem().name in LONG_CONTEXT_NAMES
    assert LongContextSystem(model=OTHER_PRICED_MODEL).name in LONG_CONTEXT_NAMES


@pytest.mark.criterion("C6")
def test_default_call_is_the_arms_model_on_the_stage(make_arm):
    # oracle: requirement 4 — stage long_context, on the arm's own (priced) model; which model
    # is the default is arm-model-from-profile's C1
    arm, adapter, _, _ = make_arm()
    assert arm.answer(_case()).error is None
    assert adapter.calls == 1
    assert adapter.stages == ["long_context"]
    assert arm.model in MODEL_PRICING
    assert adapter.params[0]["model"] == arm.model


@pytest.mark.criterion("C6")
def test_explicit_model_passes_through(make_arm):
    # oracle: requirement 4 — the arm calls the model it was built with
    assert OTHER_PRICED_MODEL in MODEL_PRICING  # precondition of the oracle
    arm, adapter, _, _ = make_arm(model=OTHER_PRICED_MODEL)
    assert arm.answer(_case()).error is None
    assert adapter.calls == 1
    assert adapter.stages == ["long_context"]
    assert adapter.params[0]["model"] == OTHER_PRICED_MODEL


@pytest.mark.criterion("C6")
@given(model=st.sampled_from(sorted(MODEL_PRICING)))
def test_any_priced_model_passes_through(make_arm, model):
    # oracle: C6 — "the given model when one is passed (any model with a MODEL_PRICING
    # entry)"; the call's model is exactly the id given
    arm, adapter, _, _ = make_arm(model=model)
    assert arm.answer(_case()).error is None
    assert adapter.calls == 1
    assert adapter.stages == ["long_context"]
    assert adapter.params[0]["model"] == model


@pytest.mark.criterion("C6")
@pytest.mark.parametrize("model", UNPRICED_MODELS)
def test_unpriced_model_is_rejected_at_construction(make_gateway, model):
    # oracle: requirement 4 — no MODEL_PRICING entry means ValueError when the arm is
    # built, before any provider call
    assert model not in MODEL_PRICING  # precondition of the oracle
    gateway, adapter = make_gateway()
    with pytest.raises(ValueError):
        LongContextSystem(model=model, gateway=gateway)
    assert adapter.calls == 0


@pytest.mark.criterion("C6")
@given(
    model=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-./", min_size=0, max_size=30)
    .filter(lambda s: s not in MODEL_PRICING)
)
def test_any_unpriced_id_is_rejected(make_gateway, model):
    # oracle: requirement 4 — any id outside MODEL_PRICING is rejected at construction
    gateway, adapter = make_gateway()
    with pytest.raises(ValueError):
        LongContextSystem(model=model, gateway=gateway)
    assert adapter.calls == 0


_CHILD = r"""
import json, sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from conftest import DEFAULT_KEY, PROVIDERS, FakeAdapter, FakeResolver, MemoryStore, _xhtml
from src.eval.core.case import EvalCase
from src.eval.systems.long_context import LongContextSystem
from src.llm import config
from src.llm.cost_tracker import CostTracker
from src.llm.gateway import LLMGateway
from src.llm.run_budget import RunBudget
from src.observability.trace_context import set_audit_sink

set_audit_sink(None)
reply = json.dumps({"value": 1, "unit": "USD", "rows": [], "refused": False,
                    "hedged": False, "cited_chunks": []})
out = {"switch": config.replacement_models_enabled(),
       "frontier": config.MODEL_TIERS["openai"]["frontier"]}
for label, kwargs in (("default", {}), ("explicit", {"model": sys.argv[3]})):
    adapter = FakeAdapter(reply)
    gateway = LLMGateway(stage="long_context", cost_tracker=CostTracker(), rate_limiter=None,
                         run_budget=RunBudget(), max_attempts=1, sleep_fn=lambda _s: None,
                         adapters={p: adapter for p in PROVIDERS})
    arm = LongContextSystem(gateway=gateway, resolver=FakeResolver({("AAA", 2024): DEFAULT_KEY}),
                            store=MemoryStore({DEFAULT_KEY: _xhtml("<p>Revenue was 7</p>")}),
                            **kwargs)
    ans = arm.answer(EvalCase(id="x", question="What was AAA revenue in fiscal 2024?",
                              kind="lookup", ticker="AAA", fy=2024))
    out[label] = [ans.error, [p.get("model") for p in adapter.params]]
print("LCRESULT " + json.dumps(out))
"""


def _run_child(switch: str, explicit: str) -> dict:
    env = dict(os.environ)
    env["XBRLRAG_USE_REPLACEMENT_MODELS"] = switch
    # A profile set in the caller's environment would win over the switch.
    for name in ("XBRLRAG_LLM_PROFILE", "ARAG_LLM_PROFILE"):
        env.pop(name, None)
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD, str(REPO_ROOT), str(SPEC_DIR), explicit],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith(_RESULT_TAG)]
    assert len(lines) == 1, proc.stdout[-3000:]
    return json.loads(lines[0][len(_RESULT_TAG):])


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
@pytest.mark.parametrize(("switch", "replacement_on"), [("false", False), ("true", True)])
def test_explicit_model_is_used_under_either_switch(offline_env, switch, replacement_on):
    # oracle: requirement 4 — with XBRLRAG_USE_REPLACEMENT_MODELS on or off (read at
    # import, so set in a fresh child process), an explicit model is used as given. The
    # default call's model is the literal "gpt-5.6-sol" (requirement 4) or, after
    # arm-model-from-profile, the selected set's frontier model
    explicit = "gpt-5-mini"
    out = _run_child(switch, explicit)
    assert out["switch"] is replacement_on
    # The toggle took effect: the frontier tier differs between the two sets, and with
    # the switch off it is not the old literal default.
    assert (out["frontier"] == DEFAULT_MODEL) is replacement_on
    assert out["default"][0] is None
    assert out["default"][1] in ([DEFAULT_MODEL], [out["frontier"]])
    assert out["explicit"] == [None, [explicit]]


@pytest.mark.criterion("C6")
@pytest.mark.parametrize("name", OTHER_NAMES)
def test_model_for_another_system_raises(offline_env, name):
    # oracle: requirement 8 — giving a model for any system but long_context is a
    # ValueError (the guard keeps a missing keyword from failing for the wrong reason)
    assert system_names()[4] in LONG_CONTEXT_NAMES
    with pytest.raises(ValueError):
        build_system(name, model=OTHER_PRICED_MODEL)


@pytest.mark.criterion("C6")
@given(
    name=st.sampled_from(OTHER_NAMES),
    model=st.sampled_from(sorted(MODEL_PRICING)),
)
def test_any_model_for_any_other_system_raises(name, model):
    # oracle: requirement 8 — for every other name and any model, build_system raises
    # ValueError (raised before anything is built, so no backend is reached)
    assert system_names()[4] in LONG_CONTEXT_NAMES
    with pytest.raises(ValueError):
        build_system(name, model=model)


@pytest.mark.criterion("C6")
def test_prompt_registry_resolves_stage_at_v1_for_the_default_model():
    # oracle: requirement 4 — the prompt is a versioned file, v1, in the XBRL registry,
    # resolved for gpt-5.6-sol's provider and series
    registry = get_xbrl_registry()
    assert "long_context" in registry.stages()
    ref = resolve_model_ref(DEFAULT_MODEL)
    resolved = registry.resolve("long_context", provider=ref.provider, series=ref.series)
    assert resolved.stage == "long_context"
    # llm-anthropic-and-caching moves the default to v2 (the cache-point message layout)
    assert resolved.version == "v2"
    assert resolved.system_text.strip() != ""
    assert resolved.user_text.strip() != ""


@pytest.mark.criterion("C6")
@given(name=st.text(min_size=0, max_size=30).filter(lambda s: s not in NAMES + LATER_ARMS))
def test_any_other_name_still_raises(name):
    # oracle: requirement 8 — the accepted set is exactly NAMES; long_context is in it.
    # Later specs added LATER_ARMS, which build too; hypothesis can draw them, because it
    # mines string constants from the code under test.
    assert system_names()[4] in LONG_CONTEXT_NAMES
    with pytest.raises(ValueError):
        build_system(name)
