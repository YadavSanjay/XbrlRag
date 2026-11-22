"""The closed-book baseline arm: the model alone, with no documents.

A control for how often the model states a wrong figure from its own knowledge, not a
benchmark. One LLM call per case (stage ``closed_book``, on the synthesizer's tier); the
model sees only the question and is told it may say it doesn't know. Its JSON reply is
read by ``parse_structured_answer``, so it is graded by the rules XbrlRag is. A
closed-book answer never cites anything.

Spec: [docs/specs/baseline-arms.md](../../../docs/specs/baseline-arms.md).
"""

from __future__ import annotations

from src.eval.core.answer_parsing import parse_structured_answer
from src.eval.core.case import EvalCase
from src.eval.core.system import SystemAnswer
from src.eval.systems._stage_llm import StageLLM, answer_in_trace
from src.llm.gateway import LLMGateway

STAGE = "closed_book"


class ClosedBookSystem:
    """Answers each case from the model's own knowledge, in one LLM call."""

    name = "closed_book"

    def __init__(self, gateway: LLMGateway | None = None) -> None:
        """Builds the arm.

        Args:
            gateway: The gateway for the ``closed_book`` stage; built on first use when
                omitted.
        """
        self._llm = StageLLM(STAGE, gateway)

    def answer(self, case: EvalCase) -> SystemAnswer:
        """Answers one case without retrieving anything.

        Args:
            case: The eval case; only its question reaches the model.

        Returns:
            The parsed answer, with its trace id, cost and latency.

        Raises:
            RunHalt: The run's budget or circuit breaker stopped the call.
        """
        return answer_in_trace(case.question, lambda: self._answer(case.question))

    def _answer(self, question: str) -> SystemAnswer:
        reply = self._llm.complete(self._llm.prompt.user.format(question=question))
        answer = parse_structured_answer(reply)
        answer.fact_ids = []
        answer.chunk_ids = []
        return answer
