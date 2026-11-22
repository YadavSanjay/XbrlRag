"""WS-9 / E1 — the run orchestrator.

Three operational constraints shape this (design doc §6.4):

1. **Per-kind concurrency.** SQL-backed ``lookup``/``compute``/``screen`` cases are
   cheap and parallelise freely. ``narrative`` reranks on CPU, has taken 90–145 s per
   query, and an unbounded reranker batch has previously segfaulted — so narrative
   runs **serialized** by default. Ignoring this makes a full run take hours and
   risks destabilising the machine.
2. **Hard cost budget.** ``max_cost_usd`` aborts cleanly and records partial results
   rather than overrunning. The run is marked ``ABORTED`` and ``n_skipped`` is always
   reported, so a truncated run can never be mistaken for full coverage.
3. **Resume.** A prior report's results are carried forward and their cases skipped,
   so an aborted or interrupted run continues instead of restarting.
4. **Cache groups.** With ``cache_groups`` set, cases that share a key (``"filing"``: the
   case's own ticker and fiscal year) run one after another in one task, largest group
   first, while groups run in parallel. The first call on a filing writes the provider's
   prompt cache and the rest read it: Anthropic serves a hit only once the writing
   response has started, and OpenAI (30 minutes) and Anthropic (5 minutes) keep an entry
   only so long after its last read. The key decides when a case runs, never what the
   system is given. :meth:`EvalRunner.run_many` puts several systems in one schedule,
   each answering a case in turn, so arms that send the same filing prefix (the oracle
   and resolved long-context arms) share one cache entry.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Dict, Optional

from src.eval.core.case import KIND_NARRATIVE, EvalCase, EvalSuite
from src.eval.core.graders.dispatch import grade_case
from src.eval.core.report import CaseResult, RunReport
from src.eval.core.run_columns import run_split
from src.eval.core.system import SystemAnswer, SystemUnderTest, exception_text
from src.llm.request_options import openai_service_tier
from src.llm.run_budget import RunHalt, RunStopScope
from src.logging_config import get_logger
from src.observability.service_tiers import TierCounts, merge_counts

logger = get_logger(__name__)

# Kinds that must not run concurrently with themselves, and why.
DEFAULT_KIND_CONCURRENCY: dict[str, int] = {KIND_NARRATIVE: 1}


def filing_group(case: EvalCase) -> str | None:
    """The case's own filing as ``TICKER:FY``, the ``"filing"`` cache-group key.

    Args:
        case: The case.

    Returns:
        The key, or None when the case names no single ticker and fiscal year.
    """
    if not case.ticker or case.fy is None:
        return None
    return f"{case.ticker.upper()}:{case.fy}"


# Cache-group name -> the key it runs cases together by (RunnerConfig.cache_groups).
CACHE_GROUPS: dict[str, Callable[[EvalCase], str | None]] = {"filing": filing_group}


@dataclass
class RunnerConfig:
    max_workers: int = 8
    kind_concurrency: dict[str, int] = field(
        default_factory=lambda: dict(DEFAULT_KIND_CONCURRENCY)
    )
    max_cost_usd: float | None = None
    progress_every: int = 25
    tier: str | None = None
    cache_groups: str | None = None

    def to_dict(self) -> dict:
        return {
            "max_workers": self.max_workers,
            "kind_concurrency": self.kind_concurrency,
            "max_cost_usd": self.max_cost_usd,
            "tier": self.tier,
            "cache_groups": self.cache_groups,
        }


def schedule(cases: Sequence[EvalCase], cache_groups: str | None) -> list[list[EvalCase]]:
    """The run's tasks: each a list of cases answered one after another.

    Args:
        cases: The cases to run, in suite order.
        cache_groups: A :data:`CACHE_GROUPS` name, or None for one task per case.

    Returns:
        With no grouping, one task per case in suite order. Otherwise the groups, largest
        first (ties in order of first appearance), each in suite order, then one task per
        case without a key.

    Raises:
        ValueError: If ``cache_groups`` is not a known name.
    """
    if cache_groups is None:
        return [[case] for case in cases]
    if cache_groups not in CACHE_GROUPS:
        raise ValueError(
            f"unknown cache_groups {cache_groups!r}; known: {sorted(CACHE_GROUPS)}"
        )
    key_of = CACHE_GROUPS[cache_groups]
    groups: dict[str, list[EvalCase]] = {}
    singles: list[list[EvalCase]] = []
    for case in cases:
        key = key_of(case)
        if key is None:
            singles.append([case])
        else:
            groups.setdefault(key, []).append(case)
    return sorted(groups.values(), key=len, reverse=True) + singles


class _KindGate:
    """Per-kind semaphores, so one slow/unsafe kind cannot saturate the pool."""

    def __init__(self, limits: dict[str, int], default: int):
        self._default = max(1, default)
        self._sems: dict[str, threading.Semaphore] = {
            kind: threading.Semaphore(max(1, limit)) for kind, limit in limits.items()
        }
        self._lock = threading.Lock()

    def _sem(self, kind: str) -> threading.Semaphore:
        with self._lock:
            if kind not in self._sems:
                self._sems[kind] = threading.Semaphore(self._default)
            return self._sems[kind]

    def acquire(self, kind: str) -> threading.Semaphore:
        sem = self._sem(kind)
        sem.acquire()
        return sem


def _served_union(
    results: Sequence[CaseResult], prior: RunReport | None
) -> dict[str, list[str]]:
    """Requested model id -> sorted distinct served ids over a run's cases.

    Args:
        results: The run's graded cases.
        prior: The report a resumed run continues, whose rolled-up value is merged in.

    Returns:
        The union; empty when no case reported a served id.
    """
    served: dict[str, set[str]] = {}
    sources = [r.served_models for r in results]
    if prior is not None:
        sources.append(prior.config.get("served_models") or {})
    for mapping in sources:
        for model, ids in mapping.items():
            served.setdefault(model, set()).update(ids)
    return {model: sorted(ids) for model, ids in sorted(served.items())}


def service_tier_calls(results: Sequence[CaseResult]) -> TierCounts:
    """The run's LLM calls by requested and served service tier, per provider and stage.

    The sum of the cases' own counts; a resumed run's restored cases carry theirs, so no
    prior rollup is added (that would count them twice).

    Args:
        results: The run's graded cases.

    Returns:
        ``"<provider>/<stage>"`` -> requested tier -> served tier -> calls
        (src/observability/service_tiers.py); empty when no case made a call.
    """
    return merge_counts(r.service_tiers for r in results)


class EvalRunner:
    """Runs a suite against one or more :class:`SystemUnderTest` and grades every case."""

    def __init__(self, config: RunnerConfig | None = None):
        self.config = config or RunnerConfig()

    # ------------------------------------------------------------------
    def run(
        self,
        cases: Sequence[EvalCase],
        system: SystemUnderTest,
        *,
        suite: str = "",
        suite_version: str = "",
        corpus_fingerprint: str | None = None,
        git_sha: str | None = None,
        prior: RunReport | None = None,
        run_id: str | None = None,
        on_result: Callable[[CaseResult], None] | None = None,
    ) -> RunReport:
        return self.run_many(
            cases,
            [system],
            suite=suite,
            suite_version=suite_version,
            corpus_fingerprint=corpus_fingerprint,
            git_sha=git_sha,
            prior=prior,
            run_ids=[run_id],
            on_result=on_result,
        )[0]

    def run_many(
        self,
        cases: Sequence[EvalCase],
        systems: Sequence[SystemUnderTest],
        *,
        suite: str = "",
        suite_version: str = "",
        corpus_fingerprint: str | None = None,
        git_sha: str | None = None,
        prior: RunReport | None = None,
        run_ids: Sequence[str | None] | None = None,
        on_result: Callable[[CaseResult], None] | None = None,
    ) -> list[RunReport]:
        """Runs the cases against several systems in one schedule, one report each.

        Each task answers its cases one after another, every system in turn on a case
        before the next case. With ``cache_groups`` a filing's cases then reach every
        system back to back, so systems that send the same filing prefix (the oracle and
        resolved long-context arms) share one prompt-cache entry. ``max_cost_usd`` caps
        the systems together.

        Args:
            cases: The cases, in suite order.
            systems: The systems; distinct names.
            suite: The suite's name, for each report.
            suite_version: The suite's version, for each report.
            corpus_fingerprint: The suite's corpus fingerprint, for each report.
            git_sha: The code version, for each report.
            prior: A report to resume; one system only.
            run_ids: A run id per system; None or a None entry mints one.
            on_result: Called with each graded result as soon as it is graded, in the
                case's own task; one system only.

        Returns:
            One report per system, in the order of ``systems``.

        Raises:
            ValueError: No system, repeated names, ``prior`` or ``on_result`` with several
                systems, a mixed split, or an unknown ``cache_groups``; all before any
                case is answered.
            RunHalt: When the run's budget or circuit breaker stops a system's answer or
                a grading hook; ``run_checkpointed`` keeps the partial reports instead.
        """
        cfg = self.config
        names = [getattr(s, "name", type(s).__name__) for s in systems]
        if not names or len(set(names)) != len(names):
            raise ValueError(f"run_many needs distinct systems, got {names}")
        if len(names) > 1 and (prior is not None or on_result is not None):
            raise ValueError("prior and on_result take one system")
        ids = list(run_ids) if run_ids is not None else [None] * len(names)
        if len(ids) != len(names):
            raise ValueError("run_ids needs one entry per system")
        reports = self.new_reports(
            cases,
            names,
            ids,
            suite=suite,
            suite_version=suite_version,
            corpus_fingerprint=corpus_fingerprint,
            git_shas=[git_sha] * len(names),
        )

        todo = {c.id for c in cases}
        if prior is not None:
            done = {r.case.id for r in prior.results}
            reports[0].results.extend(prior.results)
            todo -= done
            logger.info("resume: %d/%d cases already graded", len(done), len(cases))

        budget = _Budget(cfg.max_cost_usd)
        hook: Callable[[int, CaseResult], None] | None = None
        if on_result is not None:
            callback = on_result

            def _hook(_index: int, result: CaseResult) -> None:
                callback(result)

            hook = _hook
        self.execute(
            cases, systems, reports, todo=[set(todo) for _ in names], budget=budget,
            on_result=hook,
        )

        for report in reports:
            report.finished_at = _now()
            report.status = "ABORTED" if budget.exhausted else "OK"
            report.config["served_models"] = _served_union(report.results, prior)
            report.config["service_tier_calls"] = service_tier_calls(report.results)
            if budget.exhausted:
                logger.warning(
                    "eval %s aborted on cost budget: spent $%.4f of $%.4f; %d case(s) not "
                    "attempted",
                    report.run_id,
                    budget.spent,
                    cfg.max_cost_usd or 0.0,
                    report.n_skipped,
                )
        return reports

    def new_reports(
        self,
        cases: Sequence[EvalCase],
        names: Sequence[str],
        run_ids: Sequence[str | None],
        *,
        suite: str,
        suite_version: str,
        corpus_fingerprint: str | None,
        git_shas: Sequence[str | None],
    ) -> list[RunReport]:
        """One ``RUNNING`` report per system, each with the run's config.

        Reads the run's split and OpenAI service tier once, so a mixed split or an
        unknown tier raises here, before anything is answered or spent
        (docs/specs/eval-run-columns.md, docs/specs/openai-6-1-profile.md).

        Args:
            cases: The run's cases.
            names: The systems' names, distinct.
            run_ids: A run id per system; a None entry mints one.
            suite: The suite's name.
            suite_version: The suite's version.
            corpus_fingerprint: The suite's corpus fingerprint.
            git_shas: The code version, per system.

        Returns:
            The reports, in the order of ``names``.

        Raises:
            ValueError: A mixed split.
            LlmConfigError: An unknown service tier.
        """
        cfg = self.config
        split = run_split(cases)
        service_tier = openai_service_tier()
        reports = []
        for name, rid, sha in zip(names, run_ids, git_shas, strict=True):
            report = RunReport(
                run_id=rid or f"eval-{uuid.uuid4().hex[:12]}",
                suite=suite,
                suite_version=suite_version,
                system=name,
                tier=cfg.tier,
                corpus_fingerprint=corpus_fingerprint,
                git_sha=sha,
                n_planned=len(cases),
                config=cfg.to_dict(),
            )
            report.config["split"] = split
            report.config["service_tier"] = service_tier
            if len(names) > 1:
                report.config["scheduled_with"] = [n for n in names if n != name]
            reports.append(report)
        return reports

    def execute(
        self,
        cases: Sequence[EvalCase],
        systems: Sequence[SystemUnderTest],
        reports: Sequence[RunReport],
        *,
        todo: Sequence[set[str]],
        budget: _Budget,
        on_result: Callable[[int, CaseResult], None] | None = None,
    ) -> None:
        """Answers and grades each system's outstanding cases in one schedule.

        When exactly one system is scheduled, its optional ``plan_cases`` is called once
        with its outstanding cases in suite order, before any case is answered
        (docs/specs/long-context-cache-reuse.md, requirement 1).

        Each graded result is appended to its system's report and passed to
        ``on_result`` in the case's own task, as soon as it is graded and before that
        task answers its next case, so a checkpoint never waits for a whole cache group
        (docs/specs/eval-run-checkpoint.md, requirement 4). ``on_result`` calls are
        serialised.

        Args:
            cases: The run's cases, in suite order.
            systems: The systems, in the order of ``reports``.
            reports: One report per system; results are appended to them.
            todo: Per system, the ids of the cases it still has to answer.
            budget: The run's cost budget, possibly already charged.
            on_result: Called with ``(system index, result)`` for each graded result.

        Raises:
            ValueError: An unknown ``cache_groups``, before anything is answered.
            BaseException: Whatever stopped a task (a ``RunHalt``, or a crash). The other
                tasks stop before their next answer and are waited for, so every result
                graded before the stop is in ``reports`` and was passed to ``on_result``.
        """
        cfg = self.config
        pending = [c for c in cases if any(c.id in t for t in todo)]
        if not pending:
            return
        tasks = schedule(pending, cfg.cache_groups)  # raises before anything is answered
        if len(systems) == 1:
            _plan_cases(systems[0], [c for c in cases if c.id in todo[0]])
        gate = _KindGate(cfg.kind_concurrency, cfg.max_workers)
        stop = threading.Event()
        lock = threading.Lock()
        hook_lock = threading.Lock()
        planned = sum(1 for c in pending for t in todo if c.id in t)
        completed = 0
        label = ", ".join(r.run_id for r in reports)

        def record(index: int, result: CaseResult) -> None:
            nonlocal completed
            with lock:
                reports[index].results.append(result)
                completed += 1
                done = completed
            if on_result is not None:
                with hook_lock:
                    on_result(index, result)
            if cfg.progress_every and done % cfg.progress_every == 0:
                logger.info(
                    "eval %s: %d/%d graded, spent $%.4f", label, done, planned, budget.spent
                )

        failure: BaseException | None = None
        with ThreadPoolExecutor(max_workers=max(1, cfg.max_workers)) as pool:
            futures = [
                pool.submit(self._run_task, task, systems, todo, gate, budget, stop, record)
                for task in tasks
            ]
            for future in as_completed(futures):
                try:
                    # Only a run halt or a crash gets here as an error.
                    future.result()
                except BaseException as exc:
                    if failure is None:
                        failure = exc
                        stop.set()
                        for other in futures:
                            other.cancel()
        if failure is not None:
            raise failure

    def run_suite(
        self, suite: EvalSuite, system: SystemUnderTest, **kwargs
    ) -> RunReport:
        """Convenience wrapper carrying the suite's identity into the report."""
        kwargs.setdefault("suite", suite.name)
        kwargs.setdefault("suite_version", suite.version)
        kwargs.setdefault("corpus_fingerprint", suite.corpus_fingerprint)
        return self.run(suite.cases, system, **kwargs)

    # ------------------------------------------------------------------
    def _run_task(
        self,
        task: Sequence[EvalCase],
        systems: Sequence[SystemUnderTest],
        todo: Sequence[set[str]],
        gate: _KindGate,
        budget: _Budget,
        stop: threading.Event,
        record: Callable[[int, CaseResult], None],
    ) -> None:
        """Runs a task's cases one after another, every system in turn on each case.

        A system skips a case it has nothing outstanding for. Each result is recorded as
        soon as it is graded; see :meth:`_run_one`.
        """
        for case in task:
            for index, system in enumerate(systems):
                if case.id not in todo[index]:
                    continue
                result = self._run_one(case, system, gate, budget, stop)
                if result is not None:
                    record(index, result)

    def _run_one(
        self,
        case: EvalCase,
        system: SystemUnderTest,
        gate: _KindGate,
        budget: _Budget,
        stop: threading.Event,
    ) -> CaseResult | None:
        """Answer + grade one case. Returns None when the budget or a stop skipped it.

        A system that blows up yields ``SystemAnswer(error=exception_text(exc))``, which
        grades as ``ERROR``: one bad case must not abort a multi-hundred-case run. A
        ``RunHalt`` (the run's budget or circuit breaker) is not the case's failure: it
        propagates and stops the run (docs/specs/eval-run-checkpoint.md, requirement 11),
        as does one raised by :meth:`_after_grade`.
        """
        if budget.exhausted or stop.is_set():
            return None

        sem = gate.acquire(case.kind)
        try:
            if budget.exhausted or stop.is_set():  # re-check: may have tripped while queued
                return None
            started = time.perf_counter()
            try:
                # The scope lets a Flex wait inside the answer see this run's stop.
                with RunStopScope(stop):
                    answer = system.answer(case)
            except RunHalt:
                raise
            except Exception as exc:
                logger.exception(
                    "system %r raised on case %s", getattr(system, "name", "?"), case.id
                )
                answer = SystemAnswer(error=exception_text(exc))
            if not answer.latency_ms:
                answer.latency_ms = (time.perf_counter() - started) * 1000.0
        finally:
            sem.release()

        budget.charge(answer.cost_usd)
        result = CaseResult.build(case, answer, grade_case(case, answer))
        self._after_grade(case, answer, result, budget, system)
        return result

    def _after_grade(
        self,
        case: EvalCase,
        answer: SystemAnswer,
        result: CaseResult,
        budget: _Budget,
        system: SystemUnderTest,
    ) -> None:
        """Hook run in the case's own task after it is graded; a no-op here.

        A subclass may add to the grade (the claim judge, docs/specs/judge-runner-wiring.md)
        and charge its own cost to ``budget``. A ``RunHalt`` it raises stops the run.

        Args:
            case: The case.
            answer: The system's answer.
            result: The graded result, which the hook may update in place.
            budget: The run's cost budget.
            system: The system that answered.
        """
        del case, answer, result, budget, system


class _Budget:
    """Thread-safe cumulative cost guard."""

    def __init__(self, limit: float | None):
        self._limit = limit
        self._spent = 0.0
        self._lock = threading.Lock()

    @property
    def spent(self) -> float:
        with self._lock:
            return self._spent

    @property
    def exhausted(self) -> bool:
        if self._limit is None:
            return False
        with self._lock:
            return self._spent >= self._limit

    def charge(self, amount: float) -> None:
        if not amount:
            return
        with self._lock:
            self._spent += amount


def _plan_cases(system: SystemUnderTest, cases: list[EvalCase]) -> None:
    """Calls a single scheduled system's optional ``plan_cases`` hook.

    A joint schedule never plans (docs/specs/long-context-cache-reuse.md, requirement 1),
    so the caller passes only a run's one system. A system without the method is not
    called. A failure other than a ``RunHalt`` is logged and the run proceeds unplanned.

    Args:
        system: The run's only system.
        cases: Its outstanding cases, in suite order.

    Raises:
        RunHalt: The hook's run halt, before any case is answered.
    """
    plan = getattr(system, "plan_cases", None)
    if plan is None:
        return
    try:
        plan(cases)
    except RunHalt:
        raise
    except Exception as exc:  # isolation point: a failed plan leaves the run unplanned
        logger.warning("eval: %s.plan_cases failed, running unplanned: %s",
                       getattr(system, "name", type(system).__name__), exception_text(exc))


def _now() -> str:
    import datetime

    return datetime.datetime.now(datetime.UTC).isoformat()


def run_suite(
    suite: EvalSuite,
    system: SystemUnderTest,
    *,
    config: RunnerConfig | None = None,
    **kwargs,
) -> RunReport:
    """Module-level shorthand for the common one-shot call."""
    return EvalRunner(config).run_suite(suite, system, **kwargs)
