# Checkpointed runs: payload, resume identity, error origin, and the run itself.
# Spec: docs/specs/eval-run-checkpoint.md.
#
# run_checkpointed writes a RUNNING row per system before any case is answered, one case
# row as each case is graded, and the final scorecard at the end. A resume continues the
# same run ids: it checks the stored identity, restores every stored case and answers
# only the cases with no row (and, with retry_errors="transport", the restored transport
# errors). Each invocation appends one entry to config["segments"], with the OpenAI
# service tier it ran under, which may differ between segments.

from __future__ import annotations

import datetime
import hashlib
import json
import re
import threading
from collections.abc import Mapping, Sequence
from typing import Any

from src.eval.core.case import EvalCase
from src.eval.core.checkpoint.base import RunStore
from src.eval.core.graders.base import Grade
from src.eval.core.graders.judge import CalibrationReport, ClaimJudge
from src.eval.core.judge_wiring import check_judge, finish_graded, graded_runner
from src.eval.core.report import CaseResult, RunReport
from src.eval.core.runner import (
    RunnerConfig,
    _Budget,
    _now,
    _served_union,
    schedule,
    service_tier_calls,
)
from src.eval.core.system import SystemUnderTest, exception_text
from src.llm.gateway.adapters.base import TRANSIENT_EXC_NAMES, TRANSIENT_STATUS
from src.llm.run_budget import RunHalt
from src.logging_config import get_logger
from src.observability.service_tiers import merge_counts

logger = get_logger(__name__)

# The two error-origin tags (requirement 13).
TRANSPORT = "transport"
ARM = "arm"
# The values retry_errors accepts besides None (requirement 14).
RETRY_CHOICES = (TRANSPORT,)

# The fields a resume must match (requirement 7), and those that must be known to match.
IDENTITY_FIELDS = ("suite_hash", "system", "model", "llm_profile", "git_sha", "settings")
_PROVABLE_FIELDS = ("suite_hash", "git_sha")

# An error text of the form Name(<args>), as repr() and error_text write it.
_ERROR_TEXT = re.compile(r"\A([A-Za-z_][\w.]*)\((.*)\)\s*\Z", re.DOTALL)
# The status forms a quoted message may start with: the provider SDKs; httpx's
# HTTPStatusError (RagInference's clients); requests' HTTPError; Qdrant's UnexpectedResponse.
_STATUS_FORMS = tuple(
    re.compile(pattern)
    for pattern in (
        r"Error code: (\d{3})",
        r"(?:Server|Client) error '(\d{3})",
        r"(\d{3}) (?:Server|Client) Error",
        r"Unexpected Response: (\d{3})",
    )
)
# Transport classes, recognised by name when the text carries no status: the gateway's
# own, Python's connection errors, the run halts, httpx's connection errors (RagInference
# and Qdrant unreachable), and Qdrant's wrapper for a failed connection.
_TRANSPORT_NAMES = TRANSIENT_EXC_NAMES | {
    "TimeoutError",
    "ConnectionError",
    "ConnectionResetError",
    "ConnectionAbortedError",
    "ConnectionRefusedError",
    "BrokenPipeError",
    "CircuitBreakerOpen",
    "RunBudgetExceeded",
    "FlexWaitExceeded",
    "RunStopped",
    "ConnectError",
    "ReadError",
    "WriteError",
    "RemoteProtocolError",
    "PoolTimeout",
    "WriteTimeout",
    "ResponseHandlingException",
}
# A lost Postgres connection: these classes with one of these messages (ignoring case).
_PG_CONNECTION_NAMES = frozenset({"OperationalError", "InterfaceError"})
_PG_CONNECTION_LOST = (
    "server closed the connection unexpectedly",
    "connection already closed",
    "could not connect to server",
    "connection to server at",
    "terminating connection",
    "ssl connection has been closed unexpectedly",
    "connection refused",
    "connection timed out",
)


class ResumeRefused(ValueError):  # noqa: N818 - the name the locked spec tests import
    """A resume that cannot prove it continues the same run."""


def _canonical(value: Any) -> str:
    """The canonical JSON text of *value* (requirement 7's form)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def suite_hash(cases: Sequence[EvalCase]) -> str:
    """``"sha256:"`` plus the digest of the cases' canonical JSON, in case-id order.

    Args:
        cases: The selected cases.

    Returns:
        The hash.
    """
    docs = [case.to_dict() for case in sorted(cases, key=lambda c: c.id)]
    return "sha256:" + hashlib.sha256(_canonical(docs).encode("utf-8")).hexdigest()


def checkpoint_payload(result: CaseResult) -> dict[str, Any]:
    """The JSON payload a case is stored and restored with.

    Args:
        result: The graded case.

    Returns:
        ``result.to_dict()`` with exact ``latency_ms`` / ``cost_usd``, ``served_models``,
        ``service_tiers``,
        ``judge_scores``, ``incomplete`` and the judge's error, cost and skip reason.
    """
    payload = result.to_dict()
    payload.update(
        {
            "latency_ms": result.latency_ms,
            "cost_usd": result.cost_usd,
            "served_models": {model: list(ids) for model, ids in result.served_models.items()},
            "service_tiers": merge_counts([result.service_tiers]),
            "judge_scores": (
                dict(result.grade.judge_scores)
                if result.grade.judge_scores is not None else None
            ),
            "incomplete": result.grade.incomplete,
            "judge_error": result.judge_error,
            "judge_cost_usd": result.judge_cost_usd,
            "judge_skipped": result.judge_skipped,
        }
    )
    return payload


def error_origin(error: str | None) -> str | None:
    """Whether a stored error text is a transport failure or the arm's own.

    Args:
        error: The grade's error text (an arm's ``repr(exc)``), or None.

    Returns:
        ``"transport"``, ``"arm"``, or None when there is no error.
    """
    if not error:
        return None
    match = _ERROR_TEXT.match(error)
    if match is None:
        return ARM  # not Name(<message>): doubtful, so the arm's own
    name = match.group(1).rsplit(".", 1)[-1]
    args = match.group(2)
    if args[:1] in ("'", '"'):
        message = args[1:]
        for form in _STATUS_FORMS:
            status = form.match(message)
            if status is not None:
                code = int(status.group(1))
                # A status decides, as in classify_transient.
                return TRANSPORT if code in TRANSIENT_STATUS or code >= 500 else ARM
    if name in _TRANSPORT_NAMES:
        return TRANSPORT
    if name in _PG_CONNECTION_NAMES:
        lowered = args.lower()
        if any(phrase in lowered for phrase in _PG_CONNECTION_LOST):
            return TRANSPORT
    return ARM


def error_text(exc: BaseException) -> str:
    """The error text a failed case stores: ``repr(exc)``, or ``Name(str(exc)!r)`` when the
    repr drops the message (no ``args`` but a non-empty ``str``, as Qdrant's
    ``UnexpectedResponse``).

    Args:
        exc: The exception a system raised.

    Returns:
        The text, which ``error_origin`` classifies.
    """
    return exception_text(exc)


def resume_identity(
    cases: Sequence[EvalCase],
    *,
    system: str,
    model: str | None,
    llm_profile: str | None,
    git_sha: str | None,
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    """What a resume must match: suite hash, system, model, profile, git sha, settings.

    Args:
        cases: The selected cases.
        system: The system's name.
        model: The exact model id, or None.
        llm_profile: The LLM profile.
        git_sha: The code revision.
        settings: The answer-affecting options.

    Returns:
        The identity dict.
    """
    return {
        "suite_hash": suite_hash(cases),
        "system": system,
        "model": model,
        "llm_profile": llm_profile,
        "git_sha": git_sha,
        "settings": dict(settings),
    }


def _as_stored(value: Any) -> str:
    """A value as JSON storage would return it, for comparing a stored and a live value."""
    return json.dumps(value, sort_keys=True, default=str)


def check_resume(stored_config: Mapping[str, Any], identity: Mapping[str, Any]) -> None:
    """Refuses a resume whose identity differs from the stored run's.

    Args:
        stored_config: The stored run's config.
        identity: This invocation's identity.

    Raises:
        ResumeRefused: Naming every differing field.
    """
    stored = stored_config.get("resume_identity")
    if not isinstance(stored, Mapping):
        raise ResumeRefused(
            "the stored run has no resume_identity (it was not run checkpointed), so it "
            "cannot be resumed"
        )
    differing = [
        f for f in IDENTITY_FIELDS if _as_stored(stored.get(f)) != _as_stored(identity.get(f))
    ]
    unknown = [
        f for f in _PROVABLE_FIELDS
        if f not in differing and (stored.get(f) is None or identity.get(f) is None)
    ]
    if differing or unknown:
        problems = [f"{f} differs ({stored.get(f)!r} stored, {identity.get(f)!r} now)"
                    for f in differing]
        problems += [f"{f} is unknown, so the same code and cases cannot be proved"
                     for f in unknown]
        raise ResumeRefused("resume refused: " + "; ".join(problems))


def _started_at(value: Any) -> str:
    """A stored ``started_at`` as the ISO text a report holds."""
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    return str(value)


def _config_of(row: Mapping[str, Any]) -> dict[str, Any]:
    """A stored row's config as a dict (a JSON text is decoded)."""
    config = row.get("config")
    if isinstance(config, str):
        config = json.loads(config)
    return dict(config or {})


def _error_origins(results: Sequence[CaseResult]) -> dict[str, int]:
    """``{"transport": n, "arm": m}`` over the ``ERROR`` results (requirement 15)."""
    counts = {TRANSPORT: 0, ARM: 0}
    for result in results:
        if result.grade.grade is Grade.ERROR:
            counts[error_origin(result.grade.error) or ARM] += 1
    return counts


class _Restored:
    """What a resume carries into one system's run: its row, results and retried cases."""

    def __init__(self, row: Mapping[str, Any], results: list[CaseResult],
                 retried: list[str]) -> None:
        self.row = row
        self.config = _config_of(row)
        self.results = results
        self.retried = retried


def _restore(
    store: RunStore,
    resume: str,
    cases: Sequence[EvalCase],
    names: Sequence[str],
    identities: Sequence[Mapping[str, Any]],
    retry_errors: str | None,
) -> tuple[list[str], list[_Restored]]:
    """Loads, checks and restores every system's stored run.

    Returns:
        The run ids, in the order of ``names``, and what each system carries over.

    Raises:
        ResumeRefused: An unknown run, another schedule, or a refused identity.
    """
    first = store.load_run_row(resume)
    if first is None:
        raise ResumeRefused(f"no stored run {resume!r} to resume")
    schedule_ids = _config_of(first).get("schedule_run_ids")
    if not isinstance(schedule_ids, Mapping) or set(schedule_ids) != set(names):
        stored_names = sorted(schedule_ids) if isinstance(schedule_ids, Mapping) else None
        raise ResumeRefused(
            f"run {resume!r} was scheduled with systems {stored_names}, not {sorted(names)}; "
            "a resume must bring exactly the stored schedule"
        )
    run_ids = [str(schedule_ids[name]) for name in names]
    by_id = {case.id: case for case in cases}
    refusals: list[str] = []
    restored: list[_Restored] = []
    for name, rid, identity in zip(names, run_ids, identities, strict=True):
        row = first if rid == resume else store.load_run_row(rid)
        if row is None:
            refusals.append(f"{name}: no stored run {rid!r}")
            continue
        try:
            check_resume(_config_of(row), identity)
        except ResumeRefused as exc:
            refusals.append(f"{name} ({rid}): {exc}")
            continue
        results: dict[str, CaseResult] = {}
        for payload in store.load_case_payloads(rid):
            case = by_id.get(str(payload.get("case_id")))
            if case is None:
                raise ResumeRefused(
                    f"{name} ({rid}): stored case {payload.get('case_id')!r} is not in the "
                    "selection"
                )
            results[case.id] = CaseResult.from_dict(payload, case)
        retried: list[str] = []
        if retry_errors == TRANSPORT:
            retried = sorted(
                cid for cid, r in results.items()
                if r.grade.grade is Grade.ERROR and error_origin(r.grade.error) == TRANSPORT
            )
            for cid in retried:
                del results[cid]
        ordered = [results[c.id] for c in cases if c.id in results]
        restored.append(_Restored(row, ordered, retried))
    if refusals:
        raise ResumeRefused("; ".join(refusals))
    return run_ids, restored


def run_checkpointed(
    cases: Sequence[EvalCase],
    systems: Sequence[SystemUnderTest],
    *,
    store: RunStore,
    identities: Sequence[Mapping[str, Any]],
    resume: str | None = None,
    retry_errors: str | None = None,
    run_ids: Sequence[str] | None = None,
    config: RunnerConfig | None = None,
    judge: ClaimJudge | None = None,
    generator_profile: str | None = None,
    calibration: Sequence[CalibrationReport] = (),
    metamorphic: bool = False,
    suite: str = "",
    suite_version: str = "",
    corpus_fingerprint: str | None = None,
) -> list[RunReport]:
    """Runs (or resumes) a graded run, storing each case as it is graded.

    Args:
        cases: The selected cases.
        systems: The systems, in one schedule.
        store: Where the run is checkpointed.
        identities: One ``resume_identity`` per system.
        resume: The first system's run id to resume, or None for a new run.
        retry_errors: ``"transport"`` to re-run restored transport errors, or None.
        run_ids: Run ids for a new run, one per system; None mints them.
        config: The runner configuration.
        judge: The claim judge, or None.
        generator_profile: The systems' LLM profile; required with a judge.
        calibration: Calibration reports for the judge summary (``scores_reportable``).
        metamorphic: Whether to run the key-free pass.
        suite: The suite's name.
        suite_version: The suite's version.
        corpus_fingerprint: The suite's corpus fingerprint.

    Returns:
        One report per system, finalised in the store.

    Raises:
        ValueError: A refused combination, before any call.
        ResumeRefused: A resume that does not match the stored run, before any call.
    """
    cfg = config or RunnerConfig()
    names = [getattr(s, "name", type(s).__name__) for s in systems]
    # Requirement 16: refused combinations, before anything is read, written or called.
    if not names or len(set(names)) != len(names):
        raise ValueError(f"run_checkpointed needs distinct systems, got {names}")
    if len(identities) != len(systems):
        raise ValueError(
            f"identities needs one entry per system: {len(identities)} for {len(systems)}"
        )
    if retry_errors is not None and retry_errors not in RETRY_CHOICES:
        raise ValueError(f"retry_errors must be None or one of {RETRY_CHOICES}, "
                         f"got {retry_errors!r}")
    if retry_errors is not None and resume is None:
        raise ValueError("retry_errors re-runs restored cases, so it needs resume")
    if resume is not None and run_ids is not None:
        raise ValueError("a resume keeps the stored run ids; run_ids is for a new run")
    if resume is not None and metamorphic:
        raise ValueError("the key-free pass needs every case's answer in memory; a resumed "
                         "run's restored cases have none")
    if run_ids is not None and len(run_ids) != len(systems):
        raise ValueError("run_ids needs one entry per system")
    check_judge(judge, generator_profile)
    schedule(cases, cfg.cache_groups)  # an unknown cache_groups raises here

    restored: list[_Restored] | None = None
    ids: list[str | None]
    if resume is not None:
        stored_ids, restored = _restore(store, resume, cases, names, identities, retry_errors)
        ids = list(stored_ids)
    else:
        ids = list(run_ids) if run_ids is not None else [None] * len(names)

    runner = graded_runner(cfg, judge)
    reports = runner.new_reports(
        cases, names, ids, suite=suite, suite_version=suite_version,
        corpus_fingerprint=corpus_fingerprint,
        git_shas=[identity.get("git_sha") for identity in identities],
    )
    schedule_run_ids = {report.system: report.run_id for report in reports}
    segment_started = _now()
    for index, (report, identity) in enumerate(zip(reports, identities, strict=True)):
        prior = restored[index] if restored is not None else None
        segments: list[Any] = list(prior.config.get("segments") or []) if prior else []
        if prior is not None:
            report.started_at = _started_at(prior.row.get("started_at") or report.started_at)
            report.results.extend(prior.results)
        segments.append(
            {
                "started_at": segment_started if prior is not None else report.started_at,
                "n_restored": len(prior.results) if prior is not None else 0,
                "retried": list(prior.retried) if prior is not None else [],
                "max_cost_usd": cfg.max_cost_usd,
                "max_workers": cfg.max_workers,
                "service_tier": report.config.get("service_tier"),
            }
        )
        report.config["resume_identity"] = dict(identity)
        report.config["schedule_run_ids"] = dict(schedule_run_ids)
        report.config["segments"] = segments

    # Requirement 3: a row per system before any system is called; a failure propagates.
    for report in reports:
        store.start_run(report)

    budget = _Budget(cfg.max_cost_usd)
    for report in reports:  # the cap covers the whole run, restored spend included
        budget.charge(sum(r.cost_usd + r.judge_cost_usd for r in report.results))
    todo = [{c.id for c in cases} - {r.case.id for r in report.results} for report in reports]
    failures = [0] * len(reports)
    failures_lock = threading.Lock()

    def write(index: int, result: CaseResult) -> None:
        if not store.write_case(reports[index].run_id, result):
            logger.warning("checkpoint write failed for %s case %s; a resume re-runs it",
                           reports[index].run_id, result.case.id)
            with failures_lock:
                failures[index] += 1

    halt: RunHalt | None = None
    try:
        runner.execute(cases, systems, reports, todo=todo, budget=budget, on_result=write)
    except RunHalt as exc:  # requirement 11: a halt stops the run and keeps it
        halt = exc
        logger.warning("eval %s halted: %r; the partial run is kept",
                       ", ".join(r.run_id for r in reports), exc)

    for index, report in enumerate(reports):
        report.finished_at = _now()
        report.status = "ABORTED" if halt is not None or budget.exhausted else "OK"
        report.config["served_models"] = _served_union(report.results, None)
        report.config["service_tier_calls"] = service_tier_calls(report.results)
        report.config["checkpoint_write_failures"] = failures[index]
        report.config["error_origins"] = _error_origins(report.results)
        if halt is not None:
            report.config["halt"] = {"type": type(halt).__name__, "detail": str(halt)}
        if budget.exhausted:
            logger.warning(
                "eval %s aborted on cost budget: spent $%.4f of $%.4f; %d case(s) not "
                "attempted", report.run_id, budget.spent, cfg.max_cost_usd or 0.0,
                report.n_skipped,
            )
    finish_graded(reports, runner, cases, judge=judge, calibration=calibration,
                  metamorphic=metamorphic)
    for report in reports:
        if not store.finish_run(report):
            logger.warning("eval run %s was not finalised in the store; its case rows "
                           "written so far remain resumable", report.run_id)
    return reports
