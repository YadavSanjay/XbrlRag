# ``frontier_sec_tools``: a frontier model with tools over the public SEC APIs.
#
# The real competitor for cross-filer and cross-period questions. It is built to be beaten
# fairly: a fixed tool set, a real agent prompt, and no read of the fact store, Qdrant or
# any ``xbrl_*`` data.
#
# One case is one bounded conversation: the system prompt, one user message (as-of date,
# scope, question), then tool turns through ``LLMGateway.complete_tools``. Each turn's
# assistant message goes back unchanged, every call is answered in order (a successful
# result as ``{"source_id", "tool", "result"}``), and a status message that says what is
# left carries the request's one cache point. Four budgets stop the tools, never the
# answer: a hit budget leads to one final call with ``tool_choice="none"``.
#
# Spec: [docs/specs/frontier-sec-tools.md](../../../docs/specs/frontier-sec-tools.md); the
# window, output budget and tool fixes:
# [docs/specs/frontier-tools-v2.md](../../../docs/specs/frontier-tools-v2.md).

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from prompts.prompt_registry import get_xbrl_registry
from src.eval.core.answer_parsing import parse_structured_answer
from src.eval.core.case import EvalCase
from src.eval.core.system import SystemAnswer
from src.eval.sec_tools.toolbox import (
    SecToolbox,
    ToolOutcome,
    error_outcome,
    load_universes,
    tool_specs,
    tools_fingerprint,
)
from src.eval.systems._stage_llm import answer_in_trace
from src.llm.config import MODEL_PRICING
from src.llm.gateway import LLMGateway, call_params, resolve_model_ref
from src.llm.gateway.tools import ToolCall, tool_result_message
from src.llm.stage_models import frontier_model
from src.llm.xbrl_gateway import build_xbrl_gateway
from src.logging_config import get_logger
from src.observability.trace_context import current_trace, log_event

logger = get_logger(__name__)

STAGE = "frontier_sec_tools"
# The default model is the active profile's frontier tier (frontier_model(),
# docs/specs/arm-model-from-profile.md); --model overrides it.
# Reasoning shares the output budget, and a tool turn that runs out of it loses the call, so
# each turn asks for the model's own ceiling: call_params caps this at llm.yaml's
# max_output_tokens (docs/specs/frontier-tools-v2.md requirement 8).
MAX_COMPLETION_TOKENS = 16000
TEMPERATURE = 0.1
# Keeps prompts below the 272K-token point at which the frontier model's price rises
# (long-context-arms requirement 15); pricing.yaml still prices a prompt past that point.
MAX_WINDOW_TOKENS = 250_000
# The lowest characters-per-token ratio of the 2026-11-02 pilot: text not yet measured by the
# provider is estimated at this rate, an upper bound (frontier-tools-v2 requirement 6).
CHARS_PER_TOKEN = 1.75
# Room kept in the window for the status message that follows the tool results.
_STATUS_RESERVE_CHARS = 600
HELDOUT_TAG = "split:heldout_filers"
STOPS = ("answered", "steps", "tool_calls", "cost", "window")

_ROOT = Path(__file__).resolve().parents[3]
_GROUPS_PATH = _ROOT / "configs" / "eval" / "v2" / "groups.yaml"
_SECTORS_PATH = _ROOT / "configs" / "entities" / "sectors.yaml"


def _message_chars(message: Mapping[str, Any]) -> int:
    """A message's size as JSON, ``provider_state`` included."""
    return len(json.dumps(message, ensure_ascii=False, default=str))


def _reply_object(text: str) -> dict[str, Any] | None:
    """The first JSON object in a reply, bare, fenced or within prose."""
    decoder = json.JSONDecoder()
    for start in (i for i, ch in enumerate(text) if ch == "{"):
        try:
            value, _ = decoder.raw_decode(text, start)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


class _Case:
    """The mutable state of one case's conversation."""

    def __init__(self, record: dict[str, Any]) -> None:
        self.record = record
        self.results: dict[str, ToolOutcome] = {}
        self.contents: dict[str, str] = {}
        self.chars = 0
        # The provider's prompt_tokens for the last turn (0: none reported), and the
        # message characters that turn sent.
        self.measured_tokens = 0
        self.measured_chars = 0
        self.status_index: int | None = None
        self.window_hit = False


class FrontierSecToolsSystem:
    """Answers a case with a bounded tool-calling loop over the SEC APIs."""

    name = "frontier_sec_tools"

    def __init__(
        self,
        *,
        model: str | None = None,
        gateway: LLMGateway | None = None,
        toolbox: SecToolbox | None = None,
        universes: Mapping[str, Any] | None = None,
        max_steps: int = 20,
        max_tool_calls: int = 60,
        max_cost_usd: float = 2.0,
        max_input_tokens: int = 250000,
        reasoning_effort: str | None = None,
    ) -> None:
        """Builds the arm; touches no network, database or file.

        Args:
            model: The exact model ID.
            gateway: The gateway; built on first use when omitted.
            toolbox: The tools; :meth:`SecToolbox.from_env` on first use when omitted.
            universes: What :func:`load_universes` returns; the committed configs when
                omitted.
            max_steps: The most model turns that may call tools.
            max_tool_calls: The most tool calls in one case.
            max_cost_usd: Spend at which a case stops calling tools.
            max_input_tokens: The largest estimated prompt.
            reasoning_effort: The requested reasoning effort; the model's own when None.

        Raises:
            ValueError: If the model has no price, a limit is not positive, or the window
                is over 250,000 tokens.
        """
        if model is None:
            model = frontier_model()
        if model not in MODEL_PRICING:
            raise ValueError(
                f"{self.name}: model {model!r} has no MODEL_PRICING entry; the run's cost "
                "cap and max_cost_usd need a price"
            )
        for label, value in (("max_steps", max_steps), ("max_tool_calls", max_tool_calls),
                             ("max_cost_usd", max_cost_usd),
                             ("max_input_tokens", max_input_tokens)):
            if isinstance(value, bool) or not value > 0:
                raise ValueError(f"{self.name}: {label} must be positive, got {value!r}")
        if max_input_tokens > MAX_WINDOW_TOKENS:
            raise ValueError(
                f"{self.name}: max_input_tokens {max_input_tokens:,} is over "
                f"{MAX_WINDOW_TOKENS:,}, near the 272K tokens at which the frontier model's "
                "price rises"
            )
        self.model = model
        self.max_steps = int(max_steps)
        self.max_tool_calls = int(max_tool_calls)
        self.max_cost_usd = float(max_cost_usd)
        self.max_input_tokens = int(max_input_tokens)
        self.reasoning_effort = reasoning_effort
        self._ref = resolve_model_ref(model)
        self.prompt = get_xbrl_registry().resolve(
            STAGE, provider=self._ref.provider, series=self._ref.series
        )
        self._signature = self.prompt.signature()
        self._system = self.prompt.system.format()
        params = call_params(self._ref.config, max_completion_tokens=MAX_COMPLETION_TOKENS,
                             temperature=TEMPERATURE)
        self._max_completion_tokens = int(params["max_completion_tokens"])
        temperature = params["temperature"]
        self._temperature = None if temperature is None else float(temperature)
        self._specs = tool_specs()
        self._gateway = gateway
        self._toolbox = toolbox
        self._universes = dict(universes) if universes is not None else None
        self._lock = threading.Lock()

    # -- lazily built parts --------------------------------------------------------------

    def _get_gateway(self) -> LLMGateway:
        if self._gateway is None:
            with self._lock:
                if self._gateway is None:
                    self._gateway = build_xbrl_gateway(STAGE)
        return self._gateway

    def _get_toolbox(self) -> SecToolbox:
        if self._toolbox is None:
            with self._lock:
                if self._toolbox is None:
                    self._toolbox = SecToolbox.from_env()
        return self._toolbox

    def _get_universes(self) -> dict[str, Any]:
        if self._universes is None:
            with self._lock:
                if self._universes is None:
                    self._universes = load_universes(_GROUPS_PATH, _SECTORS_PATH)
        return self._universes

    # -- the case ---------------------------------------------------------------------------

    def answer(self, case: EvalCase) -> SystemAnswer:
        """Answers one case.

        Args:
            case: The eval case.

        Returns:
            The answer, with its ``agent`` record, trace id, cost and latency.

        Raises:
            RunHalt: The run's budget or circuit breaker stopped a call.
        """
        record: dict[str, Any] = {
            "steps": 0, "tool_calls": 0, "tool_errors": 0, "snapshot_misses": 0,
            "by_tool": {}, "tool_ms": 0.0, "withheld": 0, "stop": None, "budget_hit": False,
            "budget": None, "sources": [], "unresolved_citations": [], "prompt_tokens_max": 0,
        }
        answer = answer_in_trace(case.question, lambda: self._run(case, record))
        if record["stop"] is None:
            # A provider failure ended the case: no answer. A budget hit before it (the
            # final no-tools turn failed) is still in ``budget``.
            record["stop"] = "error"
        record["budget_hit"] = record["budget"] is not None
        record["tool_ms"] = round(float(record["tool_ms"]), 1)
        answer.agent = record
        return answer

    def _first_messages(self, case: EvalCase, toolbox: SecToolbox) -> list[dict[str, Any]]:
        # The arm reads question, universe, tags and id from the case, and nothing else.
        if case.universe:
            scope = json.dumps({"universe": list(case.universe)})
        else:
            scope = json.dumps(self._get_universes())
        user = self.prompt.user.format(as_of=toolbox.as_of, scope=scope,
                                       question=case.question)
        return [{"role": "system", "content": self._system},
                {"role": "user", "content": user}]

    def _turn(self, gateway: LLMGateway, messages: list[dict[str, Any]], tool_choice: str
              ) -> Any:
        return gateway.complete_tools(
            messages=messages,
            tools=self._specs,
            model_ref=self._ref,
            stage=STAGE,
            tool_choice=tool_choice,
            max_completion_tokens=self._max_completion_tokens,
            temperature=self._temperature,
            reasoning_effort=self.reasoning_effort,
            prompt_templates=self._signature["templates"],
            prompt_version=self._signature["version"],
        )

    def _run(self, case: EvalCase, record: dict[str, Any]) -> SystemAnswer:
        toolbox = self._get_toolbox()
        gateway = self._get_gateway()
        lift = HELDOUT_TAG in (case.tags or [])
        state = _Case(record)
        messages = self._first_messages(case, toolbox)
        state.chars = sum(_message_chars(m) for m in messages)
        stop: str | None = None
        tool_turns = 0
        while True:
            final = stop is not None
            sent_chars = state.chars
            result = self._turn(gateway, messages, "none" if final else "auto")
            record["steps"] += 1
            self._measure(state, result, sent_chars)
            if final:
                record["stop"] = stop
                if result.tool_calls:
                    names = ", ".join(sorted({c.name for c in result.tool_calls}))
                    return SystemAnswer(
                        text=(f"No answer: the tool budget ({stop}) was used up, and the "
                              f"model asked for more tools ({names}) on the final turn."),
                        refused=True,
                    )
                return self._finish(result.content, state)
            if not result.tool_calls:
                record["stop"] = "answered"
                return self._finish(result.content, state)
            tool_turns += 1
            if state.measured_tokens > self.max_input_tokens:
                # The prompt already crossed the window: the calls get window errors.
                state.window_hit = True
            self._append(messages, state, self._assistant(result))
            for call in result.tool_calls:
                self._answer_call(call, toolbox, lift, messages, state)
            if state.window_hit:
                stop = "window"
            elif record["tool_calls"] >= self.max_tool_calls:
                stop = "tool_calls"
            elif self._case_cost() >= self.max_cost_usd:
                stop = "cost"
            elif tool_turns >= self.max_steps:
                stop = "steps"
            if stop is not None:
                # Recorded before the final turn, so a failed final call keeps the hit.
                record["budget"] = stop
            self._status(messages, state, tool_turns, stop)

    @staticmethod
    def _assistant(result: Any) -> dict[str, Any]:
        if result.assistant_message is not None:
            return result.assistant_message
        return {"role": "assistant", "content": result.content,
                "tool_calls": [{"id": c.call_id, "name": c.name, "arguments": c.arguments}
                               for c in result.tool_calls]}

    @staticmethod
    def _case_cost() -> float:
        trace = current_trace()
        return float(trace.total_cost_usd) if trace is not None else 0.0

    @staticmethod
    def _append(messages: list[dict[str, Any]], state: _Case, message: dict[str, Any]) -> None:
        messages.append(message)
        state.chars += _message_chars(message)

    @staticmethod
    def _measure(state: _Case, result: Any, sent_chars: int) -> None:
        """Takes a turn's reported ``prompt_tokens`` as the size of what it sent."""
        try:
            prompt_tokens = int(getattr(result, "prompt_tokens", 0) or 0)
        except (TypeError, ValueError):
            prompt_tokens = 0
        state.measured_tokens = max(0, prompt_tokens)
        state.measured_chars = sent_chars
        record = state.record
        record["prompt_tokens_max"] = max(int(record["prompt_tokens_max"]),
                                          state.measured_tokens)

    @staticmethod
    def _estimate(state: _Case, extra_chars: int = 0) -> float:
        """The estimated prompt tokens of the conversation plus *extra_chars*.

        The last turn's reported count plus the characters appended since, at
        ``CHARS_PER_TOKEN``; every character at that rate before the first turn or when the
        provider reported no usage.
        """
        if state.measured_tokens > 0:
            appended = state.chars - state.measured_chars + extra_chars
            return state.measured_tokens + appended / CHARS_PER_TOKEN
        return (state.chars + extra_chars) / CHARS_PER_TOKEN

    def _fits(self, state: _Case, message: dict[str, Any]) -> bool:
        extra = _message_chars(message) + _STATUS_RESERVE_CHARS
        return self._estimate(state, extra) <= self.max_input_tokens

    def _answer_call(self, call: ToolCall, toolbox: SecToolbox, lift: bool,
                     messages: list[dict[str, Any]], state: _Case) -> None:
        """Runs one call, or answers it with a budget error, and appends its result."""
        record = state.record
        if state.window_hit or record["tool_calls"] >= self.max_tool_calls:
            reason = "window" if state.window_hit else "budget"
            text = ("not run: the context window for this case is full" if state.window_hit
                    else "not run: the tool budget for this case is used up")
            payload = {"error": reason, "message": text}
            self._append(messages, state,
                         tool_result_message(call.call_id, json.dumps(payload)))
            return
        started = time.perf_counter()
        if call.arguments is None and call.name in {s.name for s in self._specs}:
            outcome = error_outcome(
                "bad_arguments", call.arguments_error or "the arguments are not a JSON object",
                [])
        else:
            outcome = toolbox.execute(call.name, call.arguments, lift_withheld=lift,
                                      results=state.results)
        latency_ms = (time.perf_counter() - started) * 1000.0
        record["tool_calls"] += 1
        record["by_tool"][call.name] = record["by_tool"].get(call.name, 0) + 1
        record["tool_ms"] += latency_ms
        source_id: str | None = None
        if outcome.error_code is not None:
            record["tool_errors"] += 1
            if outcome.error_code == "not_in_snapshot":
                record["snapshot_misses"] += 1
            if outcome.error_code == "withheld":
                record["withheld"] += 1
            content = json.dumps(outcome.payload, ensure_ascii=False)
        else:
            source_id = f"S{len(state.results) + 1}"
            content = json.dumps({"source_id": source_id, "tool": call.name,
                                  "result": outcome.payload}, ensure_ascii=False)
        message = tool_result_message(call.call_id, content)
        status = outcome.error_code or "ok"
        if not self._fits(state, message):
            state.window_hit = True
            status = "window"
            source_id = None
            content = json.dumps({
                "error": "window",
                "message": "the result is too large for this case's context window; it was "
                           "not shown, and the tools are now closed",
            })
            message = tool_result_message(call.call_id, content)
        elif source_id is not None:
            hidden = outcome.payload.get("withheld")
            if isinstance(hidden, int) and not isinstance(hidden, bool):
                record["withheld"] += hidden
            state.results[source_id] = outcome
            state.contents[source_id] = content
            source: dict[str, Any] = {"id": source_id, "tool": call.name,
                                      "arguments": call.arguments, "urls": list(outcome.urls),
                                      "chars": len(content)}
            if outcome.derived_from is not None:
                source["derived_from"] = list(outcome.derived_from)
            record["sources"].append(source)
        self._append(messages, state, message)
        log_event("tool_call", tool=call.name, arguments=call.arguments, status=status,
                  chars=len(content), latency_ms=round(latency_ms, 1), source_id=source_id)

    def _status(self, messages: list[dict[str, Any]], state: _Case, tool_turns: int,
                stop: str | None) -> None:
        """Appends the status message, the request's one cache point."""
        if state.status_index is not None:
            messages[state.status_index].pop("cache_point", None)
        steps_left = max(0, self.max_steps - tool_turns)
        calls_left = max(0, self.max_tool_calls - state.record["tool_calls"])
        if stop is None:
            text = (f"Status: {steps_left} tool steps and {calls_left} tool calls left in this "
                    f"case (of {self.max_steps} and {self.max_tool_calls}). Continue, or reply "
                    "with the JSON answer when you have what you need.")
        else:
            reasons = {
                "steps": "the tool steps are used up",
                "tool_calls": "the tool calls are used up",
                "cost": "the cost budget for this case is spent",
                "window": "the context window is full",
            }
            text = (f"Status: no more tools ({reasons[stop]}): 0 tool steps left. Reply now "
                    "with the JSON answer from the results above; hedge or decline if they do "
                    "not support an answer.")
        state.status_index = len(messages)
        self._append(messages, state, {"role": "user", "content": text, "cache_point": True})

    # -- the answer --------------------------------------------------------------------------

    def _closure(self, source_id: str, state: _Case) -> list[str]:
        """The id and, transitively, the sources it was derived from, each once."""
        out: list[str] = []
        stack = [source_id]
        while stack:
            current = stack.pop(0)
            if current in out or current not in state.results:
                continue
            out.append(current)
            stack.extend(state.results[current].derived_from or [])
        return out

    def _finish(self, text: str, state: _Case) -> SystemAnswer:
        answer = parse_structured_answer(text)
        reply = _reply_object(text) or {}
        resolved: list[str] = []
        unresolved: list[str] = []

        def resolve(ids: Any) -> list[str]:
            found: list[str] = []
            for raw in ids if isinstance(ids, list) else []:
                key = str(raw)
                if key in state.results:
                    for source_id in self._closure(key, state):
                        if source_id not in found:
                            found.append(source_id)
                        if source_id not in resolved:
                            resolved.append(source_id)
                elif key not in unresolved:
                    unresolved.append(key)
            return found

        details: list[dict[str, Any]] = []
        statements = reply.get("statements")
        for entry in statements if isinstance(statements, list) else []:
            if not isinstance(entry, dict):
                continue
            ids = resolve(entry.get("sources"))
            details.append({"statement": str(entry.get("statement") or ""), "fact_ids": [],
                            "chunk_ids": ids})
        cited = resolve(reply.get("cited_chunks"))
        if not details:
            words = reply.get("text") if isinstance(reply.get("text"), str) else text
            details.append({"statement": words, "fact_ids": [], "chunk_ids": cited})
        answer.chunk_ids = resolved
        answer.fact_ids = []
        answer.raw = {
            "answer_details": details,
            "narrative_chunks": [{"chunk_id": s, "text": state.contents[s]} for s in resolved],
        }
        state.record["unresolved_citations"] = unresolved
        return answer

    # -- reporting ----------------------------------------------------------------------------

    def run_config(self) -> dict[str, Any]:
        """What a run records about the arm, published with the results.

        Returns:
            ``model``, ``prompt_version``, ``tools_fingerprint``, the four budgets,
            ``chars_per_token``, ``max_completion_tokens`` (the per-call value sent) and
            ``sec_snapshot``.
        """
        return {
            "model": self.model,
            "prompt_version": self.prompt.version,
            "prompt_ref": self._signature.get("prompt_ref"),
            "tools_fingerprint": tools_fingerprint(),
            "max_steps": self.max_steps,
            "max_tool_calls": self.max_tool_calls,
            "max_cost_usd": self.max_cost_usd,
            "max_input_tokens": self.max_input_tokens,
            "reasoning_effort": self.reasoning_effort,
            "chars_per_token": CHARS_PER_TOKEN,
            "max_completion_tokens": self._max_completion_tokens,
            "sec_snapshot": self._get_toolbox().provenance(),
        }


def agent_summary(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Reduces a run's ``agent`` records to its reported totals.

    Args:
        records: The ``agent`` record of each answer.

    Returns:
        ``cases``, ``answered``, ``budget_hit`` (per budget hit), ``tool_calls``,
        ``tool_errors``, ``snapshot_misses``, ``unresolved_citations``, ``steps_max`` and
        ``prompt_tokens_max`` (a record without the field read as 0).
    """
    summary: dict[str, Any] = {
        "cases": 0, "answered": 0, "budget_hit": {}, "tool_calls": 0, "tool_errors": 0,
        "snapshot_misses": 0, "unresolved_citations": 0, "steps_max": 0,
        "prompt_tokens_max": 0,
    }
    for record in records:
        summary["cases"] += 1
        stop = record.get("stop")
        if stop == "answered":
            summary["answered"] += 1
        if record.get("budget_hit"):
            # Keyed by the budget that was hit; a record without ``budget`` (older runs)
            # by its stop reason.
            hit = record.get("budget") or stop
            summary["budget_hit"][hit] = summary["budget_hit"].get(hit, 0) + 1
        for key in ("tool_calls", "tool_errors", "snapshot_misses"):
            summary[key] += int(record.get(key) or 0)
        summary["unresolved_citations"] += len(record.get("unresolved_citations") or [])
        summary["steps_max"] = max(summary["steps_max"], int(record.get("steps") or 0))
        summary["prompt_tokens_max"] = max(summary["prompt_tokens_max"],
                                           int(record.get("prompt_tokens_max") or 0))
    return summary
