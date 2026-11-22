# The store a checkpointed run writes to and resumes from (docs/specs/eval-run-checkpoint.md).
# PostgresEvalStore is the production implementation; tests use an in-memory one.

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from src.eval.core.report import CaseResult, RunReport


class RunStore(ABC):
    """Persists a run as it goes: a row at start, one row per case, the scorecard at the end."""

    @abstractmethod
    def start_run(self, report: RunReport) -> None:
        """Inserts (or, on a resume, resets) the run row with the report's status and config.

        Args:
            report: The run, with status ``RUNNING``.

        Raises:
            Exception: When the row cannot be written; the run must not start unstored.
        """
        raise NotImplementedError("tdd:eval-run-checkpoint")

    @abstractmethod
    def write_case(self, run_id: str, result: CaseResult) -> bool:
        """Writes one case result, with its checkpoint payload, in its own transaction.

        Args:
            run_id: The run.
            result: The graded case.

        Returns:
            Whether the row was written; never raises.
        """
        raise NotImplementedError("tdd:eval-run-checkpoint")

    @abstractmethod
    def finish_run(self, report: RunReport) -> bool:
        """Finalises the run row and writes every case row.

        Args:
            report: The final report.

        Returns:
            Whether it was written; never raises.
        """
        raise NotImplementedError("tdd:eval-run-checkpoint")

    @abstractmethod
    def load_run_row(self, run_id: str) -> dict[str, Any] | None:
        """The stored run row (at least ``run_id``, ``system``, ``status``, ``started_at``,
        ``config``), or None when there is none.

        Args:
            run_id: The run.
        """
        raise NotImplementedError("tdd:eval-run-checkpoint")

    @abstractmethod
    def load_case_payloads(self, run_id: str) -> list[dict[str, Any]]:
        """The stored checkpoint payloads of the run's cases.

        Args:
            run_id: The run.
        """
        raise NotImplementedError("tdd:eval-run-checkpoint")


class NullRunStore(RunStore):
    """A store that keeps nothing: for a run the operator asked not to store (``--no-store``).

    Every write succeeds and nothing can be resumed, so a run through
    ``run_checkpointed`` behaves as an unstored run, halts included.
    """

    def start_run(self, report: RunReport) -> None:
        """Keeps nothing."""
        del report

    def write_case(self, run_id: str, result: CaseResult) -> bool:
        """Keeps nothing; always succeeds."""
        del run_id, result
        return True

    def finish_run(self, report: RunReport) -> bool:
        """Keeps nothing; always succeeds."""
        del report
        return True

    def load_run_row(self, run_id: str) -> dict[str, Any] | None:
        """There is never a stored run."""
        del run_id
        return None

    def load_case_payloads(self, run_id: str) -> list[dict[str, Any]]:
        """There are never stored cases."""
        del run_id
        return []
