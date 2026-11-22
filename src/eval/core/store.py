"""WS-9 / E3 — Postgres persistence for evaluation runs.

Persists a completed :class:`~src.eval.core.report.RunReport` to ``eval.eval_run``
(one scorecard row) + ``eval.eval_case_result`` (one row per graded case), the schema
in ``scripts/ddl/eval/eval_runs.sql``.

Until now every run's output died with the terminal — in a repo where *query-time*
telemetry gets a full `audit` schema, analyst views, Prometheus export and a React
Trace Explorer. This is what makes a run comparable to last week's instead of a
number someone remembers.

Writes are **best-effort**, matching the audit sink's contract: a DB outage degrades
to "results still printed and available as JSON", never a crashed eval run. Losing
persistence must not cost you the two hours of LLM calls that produced the results.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import src.eval.core.report as eval_report
from src.db.scrub import scrub_nul
from src.eval.core.checkpoint.base import RunStore
from src.eval.core.report import RunReport, run_stability
from src.eval.core.run_columns import (
    JUDGE_COLUMNS,
    LEGACY_SPLIT,
    SPLITS,
    case_columns,
    run_columns,
    slice_fields_from_tags,
)
from src.logging_config import get_logger

logger = get_logger(__name__)


# Run by start_run before the RUNNING row: fails on a schema without result_payload.
_PROBE_CASE_SCHEMA = "SELECT result_payload FROM eval.eval_case_result LIMIT 0"

_INSERT_RUN = """
    INSERT INTO eval.eval_run (
        run_id, suite, suite_version, system, tier, git_sha, corpus_fingerprint,
        started_at, finished_at, status, n_planned, n_cases, n_skipped,
        n_correct, n_partial, n_incorrect, n_refused, n_error,
        n_hedged_miss, n_needs_review, n_confident_wrong, n_imprecise,
        accuracy, value_accuracy, citation_rate, grounding_precision, grounding_recall,
        silent_wrong_rate, confident_wrong_rate, refusal_rate, hedged_miss_rate,
        imprecise_rate, decline_rate, error_rate, paraphrase_stability,
        total_cost_usd, latency_p50_ms, latency_p95_ms, by_kind, by_tag, config, notes,
        grader_version, split, judge_version
    ) VALUES (
        %(run_id)s, %(suite)s, %(suite_version)s, %(system)s, %(tier)s, %(git_sha)s,
        %(corpus_fingerprint)s, %(started_at)s, %(finished_at)s, %(status)s,
        %(n_planned)s, %(n_cases)s, %(n_skipped)s,
        %(n_correct)s, %(n_partial)s, %(n_incorrect)s, %(n_refused)s, %(n_error)s,
        %(n_hedged_miss)s, %(n_needs_review)s, %(n_confident_wrong)s, %(n_imprecise)s,
        %(accuracy)s, %(value_accuracy)s, %(citation_rate)s, %(grounding_precision)s,
        %(grounding_recall)s,
        %(silent_wrong_rate)s, %(confident_wrong_rate)s, %(refusal_rate)s,
        %(hedged_miss_rate)s, %(imprecise_rate)s, %(decline_rate)s,
        %(error_rate)s, %(paraphrase_stability)s,
        %(total_cost_usd)s, %(latency_p50_ms)s, %(latency_p95_ms)s,
        %(by_kind)s, %(by_tag)s, %(config)s, %(notes)s,
        %(grader_version)s, %(split)s, %(judge_version)s
    )
    ON CONFLICT (run_id) DO UPDATE SET
        finished_at          = EXCLUDED.finished_at,
        status               = EXCLUDED.status,
        n_cases              = EXCLUDED.n_cases,
        n_skipped            = EXCLUDED.n_skipped,
        n_correct            = EXCLUDED.n_correct,
        n_partial            = EXCLUDED.n_partial,
        n_incorrect          = EXCLUDED.n_incorrect,
        n_refused            = EXCLUDED.n_refused,
        n_error              = EXCLUDED.n_error,
        n_hedged_miss        = EXCLUDED.n_hedged_miss,
        n_needs_review       = EXCLUDED.n_needs_review,
        n_confident_wrong    = EXCLUDED.n_confident_wrong,
        n_imprecise          = EXCLUDED.n_imprecise,
        accuracy             = EXCLUDED.accuracy,
        value_accuracy       = EXCLUDED.value_accuracy,
        citation_rate        = EXCLUDED.citation_rate,
        grounding_precision  = EXCLUDED.grounding_precision,
        grounding_recall     = EXCLUDED.grounding_recall,
        silent_wrong_rate    = EXCLUDED.silent_wrong_rate,
        confident_wrong_rate = EXCLUDED.confident_wrong_rate,
        refusal_rate        = EXCLUDED.refusal_rate,
        hedged_miss_rate     = EXCLUDED.hedged_miss_rate,
        imprecise_rate       = EXCLUDED.imprecise_rate,
        decline_rate         = EXCLUDED.decline_rate,
        error_rate           = EXCLUDED.error_rate,
        paraphrase_stability = EXCLUDED.paraphrase_stability,
        total_cost_usd       = EXCLUDED.total_cost_usd,
        latency_p50_ms       = EXCLUDED.latency_p50_ms,
        latency_p95_ms       = EXCLUDED.latency_p95_ms,
        by_kind              = EXCLUDED.by_kind,
        by_tag               = EXCLUDED.by_tag,
        config               = EXCLUDED.config,
        notes                = EXCLUDED.notes,
        grader_version       = EXCLUDED.grader_version,
        judge_version        = EXCLUDED.judge_version
"""

_INSERT_CASE = """
    INSERT INTO eval.eval_case_result (
        run_id, case_id, kind, grade, passed, silent_wrong, needs_review,
        confident_wrong, value_correct, question,
        expected, produced, matched_value, has_citation,
        grounding_precision, grounding_recall, screen_metrics, reason, error,
        latency_ms, cost_usd, query_id, paraphrase_group, origin, ticker, fy, tags,
        intent_id, phrasing_family, template_id, key_source, key_status,
        judge_correctness, judge_completeness, judge_citation_support,
        judge_unsupported_claims, result_payload
    ) VALUES (
        %(run_id)s, %(case_id)s, %(kind)s, %(grade)s, %(passed)s, %(silent_wrong)s,
        %(needs_review)s, %(confident_wrong)s, %(value_correct)s,
        %(question)s, %(expected)s, %(produced)s, %(matched_value)s, %(has_citation)s,
        %(grounding_precision)s, %(grounding_recall)s, %(screen_metrics)s,
        %(reason)s, %(error)s, %(latency_ms)s, %(cost_usd)s, %(query_id)s,
        %(paraphrase_group)s, %(origin)s, %(ticker)s, %(fy)s, %(tags)s,
        %(intent_id)s, %(phrasing_family)s, %(template_id)s, %(key_source)s,
        %(key_status)s, %(judge_correctness)s, %(judge_completeness)s,
        %(judge_citation_support)s, %(judge_unsupported_claims)s, %(result_payload)s
    )
    ON CONFLICT (run_id, case_id) DO UPDATE SET
        grade               = EXCLUDED.grade,
        passed              = EXCLUDED.passed,
        silent_wrong        = EXCLUDED.silent_wrong,
        needs_review        = EXCLUDED.needs_review,
        confident_wrong     = EXCLUDED.confident_wrong,
        value_correct       = EXCLUDED.value_correct,
        expected           = EXCLUDED.expected,
        produced            = EXCLUDED.produced,
        matched_value       = EXCLUDED.matched_value,
        has_citation        = EXCLUDED.has_citation,
        grounding_precision = EXCLUDED.grounding_precision,
        grounding_recall    = EXCLUDED.grounding_recall,
        screen_metrics      = EXCLUDED.screen_metrics,
        reason              = EXCLUDED.reason,
        error               = EXCLUDED.error,
        latency_ms          = EXCLUDED.latency_ms,
        cost_usd            = EXCLUDED.cost_usd,
        query_id            = EXCLUDED.query_id,
        judge_correctness        = EXCLUDED.judge_correctness,
        judge_completeness       = EXCLUDED.judge_completeness,
        judge_citation_support   = EXCLUDED.judge_citation_support,
        judge_unsupported_claims = EXCLUDED.judge_unsupported_claims,
        result_payload           = EXCLUDED.result_payload
"""

_SELECT_RUN = """
    SELECT run_id, suite, suite_version, system, tier, git_sha, status, started_at,
           finished_at, config, n_planned, split, grader_version, corpus_fingerprint
      FROM eval.eval_run
     WHERE run_id = %s
"""

_SELECT_PAYLOADS = """
    SELECT result_payload
      FROM eval.eval_case_result
     WHERE run_id = %s AND result_payload IS NOT NULL
     ORDER BY case_id
"""

_INSERT_SUITE = """
    INSERT INTO eval.eval_suite (
        suite, version, generated_at, corpus_fingerprint, n_cases,
        by_kind, description, build_stats
    ) VALUES (
        %(suite)s, %(version)s, %(generated_at)s, %(corpus_fingerprint)s, %(n_cases)s,
        %(by_kind)s, %(description)s, %(build_stats)s
    )
    ON CONFLICT (suite, version) DO UPDATE SET
        generated_at       = EXCLUDED.generated_at,
        corpus_fingerprint = EXCLUDED.corpus_fingerprint,
        n_cases            = EXCLUDED.n_cases,
        by_kind            = EXCLUDED.by_kind,
        description        = EXCLUDED.description,
        build_stats        = EXCLUDED.build_stats
"""


def _scrub(value: Any) -> Any:
    """Strips NUL bytes from strings, recursively (see :func:`src.db.scrub.scrub_nul`).

    Every parameter set this module writes also passes through it whole at the write
    boundary (``_run_params``, ``_case_params``, ``persist_suite``,
    ``persist_report_dict``), so a new
    column is scrubbed without a per-field call.
    """
    return scrub_nul(value)


def _case_slice_params(columns: dict[str, Any]) -> dict[str, Any]:
    """The nine new case-row parameters: the case columns minus ``split``.

    ``split`` lives on the run row (docs/specs/eval-run-columns.md, requirement 7).

    Args:
        columns: The ten case columns (``case_columns`` or a re-imported report's).

    Returns:
        The five tag columns (NUL-scrubbed) and the four judge columns.
    """
    return {
        k: _scrub(v) if isinstance(v, str) else v
        for k, v in columns.items()
        if k != "split"
    }


def _json(value: Any) -> str | None:
    """psycopg2 has no adapter for dict/list — serialise for the JSONB columns."""
    if value is None:
        return None
    return json.dumps(_scrub(value), default=str)


class PostgresEvalStore(RunStore):
    """Writes run scorecards + per-case results to the ``eval`` schema.

    As a :class:`RunStore` it checkpoints a run as it goes
    (docs/specs/eval-run-checkpoint.md): ``start_run`` writes the ``RUNNING`` row and
    raises when it cannot, ``write_case`` writes one case row per transaction, and
    ``finish_run`` is :meth:`persist_run`. Every case row carries ``result_payload``,
    the exact checkpoint payload a resume restores the case from.
    """

    def __init__(self, conn_params: dict[str, Any] | None = None):
        self._params = conn_params

    @classmethod
    def from_env(cls) -> PostgresEvalStore:
        return cls()

    def _connect(self):
        import psycopg2  # lazy: the eval core must import without a DB driver

        if self._params is not None:
            return psycopg2.connect(**self._params)
        from src.db.conn import connect

        return connect()

    # ------------------------------------------------------------------
    def start_run(self, report: RunReport) -> None:
        """Writes the run row with status ``RUNNING`` in one transaction (RunStore).

        The same transaction first selects ``result_payload`` with ``LIMIT 0``, so a
        database whose ``eval`` schema predates that column refuses the run here, before
        any answer is paid for, instead of failing every case write afterwards.

        Args:
            report: The run at its start.

        Raises:
            Exception: When the schema lacks ``result_payload`` or the row cannot be
                written.
        """
        conn = self._connect()  # a failure propagates: the run must not start unstored
        try:
            with conn:
                with conn.cursor() as cur:
                    try:
                        cur.execute(_PROBE_CASE_SCHEMA)
                    except Exception as exc:
                        raise RuntimeError(
                            "eval.eval_case_result has no result_payload column; apply "
                            "scripts/ddl/eval/eval_runs.sql (scripts/ddl/eval/_apply.py) "
                            "to this database before a stored run"
                        ) from exc
                    cur.execute(_INSERT_RUN, self._run_params(report))
            logger.info("started eval run %s (%s)", report.run_id, report.status)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def write_case(self, run_id: str, result: eval_report.CaseResult) -> bool:
        """Writes one case row, with ``result_payload``, in its own transaction (RunStore).

        Args:
            run_id: The run.
            result: The graded case.

        Returns:
            Whether it was written; never raises.
        """
        try:
            conn = self._connect()
        except Exception as exc:
            logger.warning("eval store unavailable, case %s of %s not checkpointed: %s",
                           result.case.id, run_id, exc)
            return False
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(_INSERT_CASE, self._case_params(run_id, result))
            return True
        except Exception as exc:
            logger.warning("failed to checkpoint case %s of %s: %s", result.case.id,
                           run_id, exc)
            return False
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def finish_run(self, report: RunReport) -> bool:
        """Finalises the run: ``persist_run`` with ``result_payload`` on every row (RunStore).

        Args:
            report: The final report.

        Returns:
            Whether it was written; never raises.
        """
        return self.persist_run(report)

    def load_run_row(self, run_id: str) -> dict[str, Any] | None:
        """The stored run row as a dict, or None (RunStore).

        Args:
            run_id: The run.

        Raises:
            Exception: When the store cannot be read, so a resume never mistakes an
                outage for an unknown run.
        """
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(_SELECT_RUN, (run_id,))
                row = cur.fetchone()
                columns = [d[0] for d in cur.description or ()]
        finally:
            try:
                conn.close()
            except Exception:
                pass
        if row is None:
            return None
        out = dict(zip(columns, row, strict=True))
        if isinstance(out.get("config"), str):  # a driver without the jsonb adapter
            out["config"] = json.loads(out["config"])
        for key in ("started_at", "finished_at"):
            value = out.get(key)
            if value is not None and hasattr(value, "isoformat"):
                out[key] = value.isoformat()
        return out

    def load_case_payloads(self, run_id: str) -> list[dict[str, Any]]:
        """The run's stored ``result_payload`` values (RunStore).

        Args:
            run_id: The run.

        Raises:
            Exception: When the store cannot be read.
        """
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(_SELECT_PAYLOADS, (run_id,))
                rows = cur.fetchall()
        finally:
            try:
                conn.close()
            except Exception:
                pass
        return [json.loads(r[0]) if isinstance(r[0], str) else dict(r[0]) for r in rows]

    def persist_run(self, report: RunReport) -> bool:
        """Persist a run + all its case results. Returns success; never raises."""
        try:
            conn = self._connect()
        except Exception as exc:
            logger.warning("eval store unavailable, results not persisted: %s", exc)
            return False
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(_INSERT_RUN, self._run_params(report))
                    rows = [self._case_params(report.run_id, r) for r in report.results]
                    if rows:
                        cur.executemany(_INSERT_CASE, rows)
            logger.info(
                "persisted eval run %s (%d cases)", report.run_id, len(report.results)
            )
            return True
        except Exception as exc:
            logger.warning("failed to persist eval run %s: %s", report.run_id, exc)
            return False
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def persist_suite(self, suite: Any, build_stats: dict | None = None) -> bool:
        """Record which suite version was generated, and against which corpus."""
        try:
            conn = self._connect()
        except Exception as exc:
            logger.warning("eval store unavailable, suite not recorded: %s", exc)
            return False
        try:
            by_kind = {k: len(v) for k, v in suite.by_kind().items()}
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        _INSERT_SUITE,
                        _scrub({
                            "suite": suite.name,
                            "version": suite.version,
                            "generated_at": suite.generated_at,
                            "corpus_fingerprint": suite.corpus_fingerprint,
                            "n_cases": len(suite.cases),
                            "by_kind": _json(by_kind),
                            "description": suite.description,
                            "build_stats": _json(build_stats),
                        }),
                    )
            return True
        except Exception as exc:
            logger.warning("failed to record suite %s: %s", suite.qualified_name, exc)
            return False
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    @staticmethod
    def _run_params(report: RunReport) -> dict[str, Any]:
        m = report.metrics
        return _scrub({
            "run_id": report.run_id,
            "suite": report.suite,
            "suite_version": report.suite_version,
            "system": report.system,
            "tier": report.tier,
            "git_sha": report.git_sha,
            "corpus_fingerprint": report.corpus_fingerprint,
            "started_at": report.started_at,
            "finished_at": report.finished_at,
            "status": report.status,
            "n_planned": report.n_planned,
            "n_cases": m.n,
            "n_skipped": report.n_skipped,
            "n_correct": m.n_correct,
            "n_partial": m.n_partial,
            "n_incorrect": m.n_incorrect,
            "n_refused": m.n_refused,
            "n_error": m.n_error,
            "n_hedged_miss": m.n_hedged_miss,
            "n_needs_review": m.n_needs_review,
            "n_confident_wrong": m.n_confident_wrong,
            "n_imprecise": m.n_imprecise,
            "accuracy": m.accuracy,
            "value_accuracy": m.value_accuracy,
            "citation_rate": m.citation_rate,
            "grounding_precision": m.grounding_precision,
            "grounding_recall": m.grounding_recall,
            "silent_wrong_rate": m.silent_wrong_rate,
            "confident_wrong_rate": m.confident_wrong_rate,
            "refusal_rate": m.refusal_rate,
            "hedged_miss_rate": m.hedged_miss_rate,
            "imprecise_rate": m.imprecise_rate,
            "decline_rate": m.decline_rate,
            "error_rate": m.error_rate,
            "paraphrase_stability": run_stability(report.results),
            "total_cost_usd": m.total_cost_usd,
            "latency_p50_ms": m.latency_p50_ms,
            "latency_p95_ms": m.latency_p95_ms,
            "by_kind": _json({k: v.to_dict() for k, v in report.by_kind().items()}),
            "by_tag": _json({k: v.to_dict() for k, v in report.by_tag().items()}),
            "config": _json(report.config),
            "notes": report.notes,
            "grader_version": m.grader_version,
            **run_columns(report),
        })

    @staticmethod
    def _case_params(run_id: str, result: Any) -> dict[str, Any]:
        # Lazy: the checkpoint module imports the runner, which the store must not need.
        from src.eval.core.checkpoint.run import checkpoint_payload

        g = result.grade
        return _scrub({
            "run_id": run_id,
            "case_id": result.case.id,
            "kind": result.case.kind,
            "grade": g.grade.value,
            "passed": g.passed,
            "silent_wrong": g.silent_wrong,
            "needs_review": g.needs_review,
            "confident_wrong": g.confident_wrong,
            "value_correct": g.value_correct,
            # TEXT columns reject NUL exactly as jsonb does — scrub them too.
            "question": _scrub(result.case.question),
            "expected": _json(g.expected),
            "produced": _json(g.produced),
            "matched_value": g.matched_value,
            "has_citation": g.has_citation,
            "grounding_precision": g.grounding_precision,
            "grounding_recall": g.grounding_recall,
            "screen_metrics": _json(g.screen_metrics),
            "reason": _scrub(g.reason),
            "error": _scrub(g.error),
            "latency_ms": result.latency_ms,
            "cost_usd": result.cost_usd,
            "query_id": result.query_id,
            "paraphrase_group": result.case.paraphrase_group,
            "origin": result.case.origin,
            "ticker": result.case.ticker,
            "fy": result.case.fy,
            "tags": list(result.case.tags),
            **_case_slice_params(case_columns(result)),
            "result_payload": _json(checkpoint_payload(result)),
        })


def persist(report: RunReport, store: PostgresEvalStore | None = None) -> bool:
    """Module-level shorthand used by ``scripts/eval/run_suite.py``."""
    return (store or PostgresEvalStore.from_env()).persist_run(report)


def persist_report_dict(
    data: dict[str, Any], store: PostgresEvalStore | None = None
) -> bool:
    """Persist a run from its serialised ``RunReport.to_dict()`` form.

    A run is expensive — hours of LLM calls — so its JSON must be re-importable
    without re-running it: when the store was down at the time, when CI runs the suite
    on a different machine than the one holding the database, or when an older report
    needs backfilling into the schema.
    """
    store = store or PostgresEvalStore.from_env()
    metrics = data.get("metrics") or {}
    run_id = data["run_id"]
    # A report written before eval-run-columns has no split: it is legacy.
    split = data.get("split") or LEGACY_SPLIT
    if split not in SPLITS:
        raise ValueError(f"unknown run split {split!r}; expected one of {SPLITS}")

    run_params: dict[str, Any] = {
        "run_id": run_id,
        "suite": data.get("suite", ""),
        "suite_version": data.get("suite_version"),
        "system": data.get("system", ""),
        "tier": data.get("tier"),
        "git_sha": data.get("git_sha"),
        "corpus_fingerprint": data.get("corpus_fingerprint"),
        "started_at": data.get("started_at"),
        "finished_at": data.get("finished_at"),
        "status": data.get("status", "OK"),
        "n_planned": data.get("n_planned", 0),
        "n_skipped": data.get("n_skipped", 0),
        "paraphrase_stability": data.get("paraphrase_stability"),
        "by_kind": _json(data.get("by_kind")),
        "by_tag": _json(data.get("by_tag")),
        "config": _json(data.get("config")),
        "notes": data.get("notes", ""),
        **{
            k: metrics.get(k)
            for k in (
                "n_correct",
                "n_partial",
                "n_incorrect",
                "n_refused",
                "n_error",
                "accuracy",
                "citation_rate",
                "grounding_precision",
                "grounding_recall",
                "silent_wrong_rate",
                # Absent from a report graded before version 3: stored as NULL, never 0.
                "n_confident_wrong",
                "confident_wrong_rate",
                "value_accuracy",
                # Absent from a report graded before version 4: NULL as well.
                "n_imprecise",
                "imprecise_rate",
                "refusal_rate",
                "hedged_miss_rate",
                "decline_rate",
                "error_rate",
                "total_cost_usd",
                "latency_p50_ms",
                "latency_p95_ms",
            )
        },
        # A report written before grader versioning has none of these: its version
        # stays NULL (pre-versioned), and the NOT NULL counts default to 0.
        "n_hedged_miss": metrics.get("n_hedged_miss", 0),
        "n_needs_review": metrics.get("n_needs_review", 0),
        "grader_version": data.get("grader_version") or metrics.get("grader_version"),
        "split": split,
        "judge_version": data.get("judge_version"),
    }
    run_params["n_cases"] = metrics.get("n", 0)
    run_params = _scrub(run_params)

    case_rows = [
        {
            "run_id": run_id,
            "case_id": c["case_id"],
            "kind": c.get("kind"),
            "grade": c["grade"],
            "passed": c["passed"],
            "silent_wrong": c.get("silent_wrong", False),
            "needs_review": c.get("needs_review", False),
            "confident_wrong": c.get("confident_wrong"),
            "value_correct": c.get("value_correct"),
            "question": _scrub(c.get("question")),
            "expected": _json(c.get("expected")),
            "produced": _json(c.get("produced")),
            "matched_value": c.get("matched_value"),
            "has_citation": c.get("has_citation"),
            "grounding_precision": c.get("grounding_precision"),
            "grounding_recall": c.get("grounding_recall"),
            "screen_metrics": _json(c.get("screen_metrics")),
            "reason": _scrub(c.get("reason")),
            "error": _scrub(c.get("error")),
            "latency_ms": c.get("latency_ms"),
            "cost_usd": c.get("cost_usd"),
            "query_id": c.get("query_id"),
            "paraphrase_group": c.get("paraphrase_group"),
            "origin": c.get("origin"),
            "ticker": c.get("ticker"),
            "fy": c.get("fy"),
            "tags": list(c.get("tags") or []),
            **_case_slice_params(
                {
                    **slice_fields_from_tags(c.get("tags") or []),
                    **{col: c.get(col) for col in JUDGE_COLUMNS.values()},
                }
            ),
            # A report's JSON is rounded and drops the judge's cost: not a payload a
            # resume could restore from, so the case is re-answered on a resume.
            "result_payload": None,
        }
        for c in data.get("results", [])
    ]
    case_rows = [_scrub(row) for row in case_rows]

    try:
        conn = store._connect()
    except Exception as exc:
        logger.warning("eval store unavailable, report not imported: %s", exc)
        return False
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(_INSERT_RUN, run_params)
                if case_rows:
                    cur.executemany(_INSERT_CASE, case_rows)
        logger.info("imported eval run %s (%d cases)", run_id, len(case_rows))
        return True
    except Exception as exc:
        logger.warning("failed to import eval run %s: %s", run_id, exc)
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def load_run(run_id: str, conn: Any = None) -> dict[str, Any] | None:
    """Read one run's scorecard back — used by tests and the drift check."""
    from src.api import db

    return db.query_one("SELECT * FROM eval.eval_run WHERE run_id = %s", (run_id,))


def load_case_results(run_id: str, limit: int = 1000) -> list[dict[str, Any]]:
    from src.api import db

    return db.query(
        "SELECT * FROM eval.eval_case_result WHERE run_id = %s "
        "ORDER BY passed, kind, case_id LIMIT %s",
        (run_id, limit),
    )
