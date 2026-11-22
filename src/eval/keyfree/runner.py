# The key-free checks over a run: the metamorphic pass and the perturbation runner.
#
# metamorphic_pass pairs a run's own answers (a breakdown with its lookup total, a growth
# compute with two lookups, a screen with the individual lookups) and applies the checks of
# metamorphic.py. perturbation_twins builds ticker and year twins of a suite's cases;
# run_perturbation_checks answers each pair and applies the plan and answer perturbation
# checks. No answer key is needed. Spec: docs/specs/judge-runner-wiring.md.

from __future__ import annotations

import random
import re
import threading
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from src.eval.core.case import (
    KIND_BREAKDOWN,
    KIND_COMPUTE,
    KIND_LOOKUP,
    KIND_SCREEN,
    EvalCase,
)
from src.eval.core.system import SystemAnswer, SystemUnderTest, exception_text
from src.eval.keyfree.metamorphic import (
    check_breakdown_sum,
    check_growth,
    check_screen_order,
)
from src.eval.keyfree.perturbation import (
    check_answer_perturbation,
    check_plan_perturbation,
    perturb_ticker,
    perturb_year,
)
from src.llm.run_budget import RunHalt
from src.logging_config import get_logger

logger = get_logger(__name__)

METAMORPHIC_CHECKS = ("breakdown_sum", "growth", "screen_order")
PERTURBATION_CHECKS = ("plan_perturbation", "answer_perturbation")
_VERDICTS = ("consistent", "inconsistent", "not_applicable")
_GROWTH_TAG = "growth"
_WHITESPACE = re.compile(r"\s+")


# ---------------------------------------------------------------------------- shape


def _shape(check_names: Sequence[str], verdicts: list[dict]) -> dict:
    """The common report shape (requirement 9) of a pass's verdicts.

    Args:
        check_names: Every check of the pass, present even when it never applied.
        verdicts: The verdict dicts, each with ``check``, ``verdict`` and ``case_id``.

    Returns:
        ``checks``, ``counts`` and ``verdicts``.
    """
    checks: dict[str, dict[str, list[str]]] = {
        name: {v: [] for v in _VERDICTS} for name in check_names
    }
    for v in verdicts:
        checks[v["check"]][v["verdict"]].append(v["case_id"])
    for by_verdict in checks.values():
        for ids in by_verdict.values():
            ids.sort()
    counts = {
        name: {v: len(ids) for v, ids in by_verdict.items()}
        for name, by_verdict in checks.items()
    }
    return {"checks": checks, "counts": counts, "verdicts": verdicts}


# ---------------------------------------------------------------------------- amounts


def _field(obj: Any, key: str) -> Any:
    """Reads *key* from a dict, or the attribute of that name from any other object."""
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def _dict_steps(container: Any) -> list[dict]:
    steps = _field(container, "steps")
    return [s for s in steps if isinstance(s, dict)] if isinstance(steps, list) else []


def _exec_steps(answer: SystemAnswer) -> list[dict]:
    """The answer's execution steps; none when it has an error."""
    if answer.error:
        return []
    return _dict_steps(_field(answer.raw, "execution_result"))


def _plan_steps(answer: SystemAnswer) -> list[dict]:
    """The answer's plan steps; none when it has an error."""
    if answer.error:
        return []
    return _dict_steps(_field(answer.raw, "plan"))


def _plan_of(answer: SystemAnswer) -> dict:
    """The answer's plan as a dict, ``{}`` when it has none."""
    plan = None if answer.error else _field(answer.raw, "plan")
    return dict(plan) if isinstance(plan, Mapping) else {}


def _first_ok(answer: SystemAnswer, op: str) -> dict | None:
    for step in _exec_steps(answer):
        if step.get("op") == op and step.get("status") == "OK":
            return step
    return None


def _missing() -> dict:
    return {"value": None, "unit": None}


def _lookup_amount(answer: SystemAnswer) -> dict:
    """The first OK lookup step's ``{"value", "unit"}``; the missing amount otherwise."""
    step = _first_ok(answer, "lookup")
    if step is None:
        return _missing()
    return {"value": step.get("value"), "unit": step.get("unit")}


def _rows_of(step: dict | None) -> list[dict]:
    rows = step.get("rows") if step is not None else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _breakdown_rows(answer: SystemAnswer) -> list[dict]:
    """The first OK breakdown step's rows as amounts; the step's unit fills a missing one."""
    step = _first_ok(answer, "breakdown")
    if step is None:
        return []
    return [
        {"value": r.get("value"),
         "unit": r.get("unit") if r.get("unit") is not None else step.get("unit")}
        for r in _rows_of(step)
    ]


def _growth_amount(answer: SystemAnswer) -> dict:
    """The first OK compute step's value, as a fraction when its expr multiplies by 100."""
    step = _first_ok(answer, "compute")
    if step is None:
        return _missing()
    value = step.get("value")
    for plan_step in _plan_steps(answer):
        if plan_step.get("id") == step.get("step_id"):
            expr = plan_step.get("expr")
            if (value is not None and isinstance(expr, str)
                    and "*100" in _WHITESPACE.sub("", expr)):
                value = float(value) / 100.0
            break
    return {"value": value, "unit": step.get("unit")}


def _first_plan_screen(answer: SystemAnswer) -> dict:
    for step in _plan_steps(answer):
        if step.get("op") == "screen":
            return step
    return {}


def _screen_order(plan_step: dict) -> str:
    order = plan_step.get("order")
    if isinstance(order, str) and order.lower() in ("asc", "desc"):
        return order.lower()
    return "desc"


def _undimensioned_value_screen(plan_step: dict) -> bool:
    """Whether a plan screen step has no dimensions, no axis and a plain-value metric."""
    dims = plan_step.get("dimensions")
    no_dims = (dims is None or dims == {}
               or (isinstance(dims, str) and dims.strip().upper() == "NONE"))
    return no_dims and not plan_step.get("axis") and plan_step.get("metric_kind") in (
        None, "value")


# ---------------------------------------------------------------------------- pass


def _upper(ticker: str | None) -> str | None:
    return ticker.upper() if isinstance(ticker, str) and ticker else None


class _Lookups:
    """The run's lookup cases, for finding a subject's partner lookups."""

    def __init__(self, pairs: Sequence[tuple[EvalCase, SystemAnswer]]) -> None:
        self._pairs = [(c, a) for c, a in pairs if c.kind == KIND_LOOKUP]

    def _matching(
        self, concept: str | None, fy: int | None, fiscal_period: str, ticker: str | None
    ) -> list[tuple[EvalCase, SystemAnswer]]:
        if concept is None or fy is None:
            return []
        return [
            (c, a) for c, a in self._pairs
            if c.concept is not None and c.concept == concept and c.fy == fy
            and c.fiscal_period == fiscal_period
            and _upper(c.ticker) is not None
            and (ticker is None or _upper(c.ticker) == ticker)
        ]

    @staticmethod
    def _pick(found: list[tuple[EvalCase, SystemAnswer]]) -> tuple[EvalCase, dict] | None:
        """The first match whose amount has a value, else the first match."""
        if not found:
            return None
        for case, answer in found:
            amount = _lookup_amount(answer)
            if amount["value"] is not None:
                return case, amount
        return found[0][0], _lookup_amount(found[0][1])

    def partner(self, subject: EvalCase, fy: int | None) -> tuple[EvalCase, dict] | None:
        """The partner lookup of *subject* at *fy*: same ticker, concept and period."""
        ticker = _upper(subject.ticker)
        if ticker is None:
            return None
        return self._pick(self._matching(subject.concept, fy, subject.fiscal_period, ticker))

    def by_ticker(self, subject: EvalCase) -> dict[str, tuple[EvalCase, dict]]:
        """Per upper-cased ticker, the partner lookup at the subject's concept and fy."""
        found = self._matching(subject.concept, subject.fy, subject.fiscal_period, None)
        out: dict[str, tuple[EvalCase, dict]] = {}
        for ticker in dict.fromkeys(_upper(c.ticker) for c, _ in found):
            if ticker is None:
                continue
            picked = self._pick([(c, a) for c, a in found if _upper(c.ticker) == ticker])
            if picked is not None:
                out[ticker] = picked
        return out


def _with_ids(verdict: dict, case_id: str, related: list[str]) -> dict:
    return {**verdict, "case_id": case_id, "related_case_ids": list(related)}


def _answer_error(check: str) -> dict:
    return {"check": check, "verdict": "not_applicable", "reason": "answer_error"}


def metamorphic_pass(pairs: Sequence[tuple[EvalCase, SystemAnswer]]) -> dict:
    """Applies the breakdown, growth and screen checks to a run's own answers.

    Args:
        pairs: The run's ``(case, answer)`` pairs.

    Returns:
        ``checks`` (case ids per verdict), ``counts`` and ``verdicts``.
    """
    lookups = _Lookups(pairs)
    verdicts: list[dict] = []
    for case, answer in pairs:
        if case.kind == KIND_BREAKDOWN:
            partner = lookups.partner(case, case.fy)
            if partner is None:
                continue
            related = [partner[0].id]
            verdict = (_answer_error("breakdown_sum") if answer.error
                       else check_breakdown_sum(_breakdown_rows(answer), partner[1]))
            verdicts.append(_with_ids(verdict, case.id, related))
        elif case.kind == KIND_COMPUTE and _GROWTH_TAG in case.tags:
            if case.fy is None:
                continue
            current = lookups.partner(case, case.fy)
            prior = lookups.partner(case, case.fy - 1)
            if current is None or prior is None:
                continue
            related = [current[0].id, prior[0].id]
            verdict = (_answer_error("growth") if answer.error
                       else check_growth(_growth_amount(answer), current[1], prior[1]))
            verdicts.append(_with_ids(verdict, case.id, related))
        elif case.kind == KIND_SCREEN:
            plan_step = _first_plan_screen(answer)
            if not _undimensioned_value_screen(plan_step):
                continue
            partners = lookups.by_ticker(case)
            if not partners:
                continue
            related = sorted(c.id for c, _ in partners.values())
            if answer.error:
                verdict = _answer_error("screen_order")
            else:
                amounts = {t: amount for t, (_, amount) in partners.items()}
                rows = _rows_of(_first_ok(answer, "screen"))
                verdict = check_screen_order(rows, amounts, _screen_order(plan_step))
            verdicts.append(_with_ids(verdict, case.id, related))
    return _shape(METAMORPHIC_CHECKS, verdicts)


# ---------------------------------------------------------------------------- twins


def _twin_case(
    case: EvalCase,
    twin_id: str,
    question: str,
    *,
    ticker: str | None,
    cik: int | None,
    fy: int | None,
) -> EvalCase:
    """A perturbed copy of *case* without an answer key."""
    return EvalCase(
        id=twin_id,
        question=question,
        suite=case.suite,
        kind=case.kind,
        origin=case.origin,
        ticker=ticker,
        cik=cik,
        fy=fy,
        fiscal_period=case.fiscal_period,
        concept=case.concept,
        unit=case.unit,
        tags=list(case.tags),
    )


def perturbation_twins(
    cases: Sequence[EvalCase],
    *,
    seed: int,
    names: Mapping[str, list[str]],
    max_twins: int,
) -> list[dict]:
    """Builds ticker and year twins of a suite's cases, deterministically for a seed.

    Args:
        cases: The suite's cases.
        seed: The seed choosing each ticker twin's new ticker.
        names: Ticker to company surface forms, as ``perturb_ticker`` takes.
        max_twins: The most twins returned.

    Returns:
        ``{"original", "twin", "perturbation"}`` per twin.

    Raises:
        ValueError: ``max_twins`` is negative.
    """
    if max_twins < 0:
        raise ValueError(f"max_twins must not be negative, got {max_twins}")
    universe = sorted({t for t in (_upper(c.ticker) for c in cases) if t is not None})
    name_map = dict(names)
    twins: list[dict] = []
    for case in cases:
        if len(twins) >= max_twins:
            break
        own = _upper(case.ticker)
        others = [t for t in universe if t != own]
        if own is not None and others:
            to = random.Random(f"{seed}:{case.id}").choice(others)
            p = perturb_ticker(case.question, own, to, name_map)
            if p is not None:
                twin = _twin_case(case, f"{case.id}~ticker:{to}", p["question"],
                                  ticker=to, cik=None, fy=case.fy)
                twins.append({"original": case, "twin": twin,
                              "perturbation": p["perturbation"]})
        if case.fy is not None:
            p = perturb_year(case.question, case.fy, case.fy - 1)
            if p is not None:
                twin = _twin_case(case, f"{case.id}~year:{case.fy - 1}", p["question"],
                                  ticker=case.ticker, cik=case.cik, fy=case.fy - 1)
                twins.append({"original": case, "twin": twin,
                              "perturbation": p["perturbation"]})
    return twins[:max_twins]


# ---------------------------------------------------------------------------- runner


class _PairRunner:
    """Answers originals once and twins once, under a shared cost cap."""

    def __init__(self, system: SystemUnderTest, max_cost_usd: float | None) -> None:
        self._system = system
        self._max_cost_usd = max_cost_usd
        self._lock = threading.Lock()
        self._originals: dict[str, Future[SystemAnswer]] = {}
        self.spent = 0.0
        self.skipped: list[str] = []
        self.n_pairs = 0

    def _answer(self, case: EvalCase) -> SystemAnswer:
        """Answers one case; any failure but a run halt becomes an errored answer."""
        try:
            answer = self._system.answer(case)
        except RunHalt:
            raise
        except Exception as exc:  # isolation point: one failed answer must not stop the run
            logger.warning("perturbation run: system raised on case %s: %r", case.id, exc)
            answer = SystemAnswer(error=exception_text(exc))
        with self._lock:
            self.spent += float(answer.cost_usd or 0.0)
        return answer

    def _original(self, case: EvalCase) -> SystemAnswer:
        """The original's answer, computed by the first pair that needs it."""
        with self._lock:
            known = self._originals.get(case.id)
            owner = known is None
            future: Future[SystemAnswer] = Future() if known is None else known
            if owner:
                self._originals[case.id] = future
        if owner:
            try:
                future.set_result(self._answer(case))
            except BaseException as exc:
                future.set_exception(exc)
                raise
        return future.result()

    def run(self, item: dict, lookups_equal: Callable[[EvalCase, EvalCase], bool]
            ) -> list[dict] | None:
        """Runs one pair; ``None`` when the cost cap stopped it before it started."""
        original, twin = item["original"], item["twin"]
        with self._lock:
            if self._max_cost_usd is not None and self.spent >= self._max_cost_usd:
                self.skipped.append(twin.id)
                return None
            self.n_pairs += 1
        original_answer = self._original(original)
        twin_answer = self._answer(twin)
        related = [original.id]
        plan = check_plan_perturbation(
            _plan_of(original_answer), _plan_of(twin_answer), item["perturbation"])
        values = check_answer_perturbation(
            list(original_answer.numeric_values), list(twin_answer.numeric_values),
            lookups_equal(original, twin))
        return [_with_ids(plan, twin.id, related), _with_ids(values, twin.id, related)]


def run_perturbation_checks(
    twins: Sequence[dict],
    system: SystemUnderTest,
    *,
    lookups_equal: Callable[[EvalCase, EvalCase], bool],
    max_workers: int = 4,
    max_cost_usd: float | None = None,
) -> dict:
    """Answers each original and twin and applies the perturbation checks.

    Args:
        twins: Items from ``perturbation_twins``.
        system: The system under test.
        lookups_equal: Whether an original's and its twin's underlying lookups are equal.
        max_workers: The most pairs run at once.
        max_cost_usd: The cap on the summed answer cost; ``None`` for none.

    Returns:
        ``checks``, ``counts``, ``verdicts``, ``skipped``, ``n_pairs`` and ``cost_usd``.

    Raises:
        ValueError: ``max_workers`` is below 1.
        RunHalt: When the system's run budget stops it.
    """
    if max_workers < 1:
        raise ValueError(f"max_workers must be at least 1, got {max_workers}")
    runner = _PairRunner(system, max_cost_usd)
    verdicts: list[dict] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(runner.run, item, lookups_equal) for item in twins]
        for future in futures:
            try:
                pair_verdicts = future.result()
            except BaseException:
                for pending in futures:
                    pending.cancel()
                raise
            if pair_verdicts is not None:
                verdicts.extend(pair_verdicts)
    out = _shape(PERTURBATION_CHECKS, verdicts)
    out["skipped"] = sorted(runner.skipped)
    out["n_pairs"] = runner.n_pairs
    out["cost_usd"] = runner.spent
    return out
