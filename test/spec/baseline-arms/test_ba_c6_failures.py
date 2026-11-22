"""C6: errors become SystemAnswer(error=...); a run halt propagates."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.systems.closed_book import ClosedBookSystem
from src.eval.systems.naive_rag import NaiveRagSystem
from src.llm.run_budget import RunBudget, RunHalt

ARMS = ["closed_book", "naive_rag_plain", "naive_rag_filtered"]


class ProviderRejectedError(Exception):
    """A non-transient provider error (its name is not in the transient list)."""


@pytest.fixture(scope="session")
def arm_with(make_gateway, make_embedder, make_qdrant, point, reply):
    """Factory: (system, adapter, qdrant) with injectable failures at each boundary."""

    def make(arm, *, provider_error=None, embed_error=None, qdrant_error=None, run_budget=None):
        gateway, adapter = make_gateway(reply(9.0), error=provider_error, run_budget=run_budget)
        if arm == "closed_book":
            return ClosedBookSystem(gateway=gateway), adapter, None
        qdrant = make_qdrant([point("chunk-1", "Revenue was 9 dollars.")], error=qdrant_error)
        system = NaiveRagSystem(
            filtered=(arm == "naive_rag_filtered"),
            gateway=gateway,
            embedder=make_embedder(error=embed_error),
            vector_db_client=qdrant,
        )
        return system, adapter, qdrant

    return make


@pytest.mark.criterion("C6")
@pytest.mark.parametrize("arm", ARMS)
def test_provider_error_becomes_error_answer(arm_with, make_case, arm):
    # oracle: requirement 9 — a permanent provider exception is reported, not raised
    system, adapter, _ = arm_with(arm, provider_error=ProviderRejectedError("400 bad request"))
    ans = system.answer(make_case())
    assert adapter.calls == 1
    assert isinstance(ans.error, str) and ans.error != ""
    assert ans.numeric_values == []


@pytest.mark.criterion("C6")
@pytest.mark.parametrize("arm", ["naive_rag_plain", "naive_rag_filtered"])
def test_embedder_error_becomes_error_answer(arm_with, make_case, arm):
    # oracle: requirement 9 — a retrieval-side error (embedder) is reported, not raised
    system, adapter, _ = arm_with(arm, embed_error=ConnectionRefusedError("raginfer down"))
    ans = system.answer(make_case())
    assert adapter.calls == 0
    assert isinstance(ans.error, str) and ans.error != ""
    assert ans.numeric_values == []


@pytest.mark.criterion("C6")
@pytest.mark.parametrize("arm", ["naive_rag_plain", "naive_rag_filtered"])
def test_qdrant_error_becomes_error_answer(arm_with, make_case, arm):
    # oracle: requirement 9 — a retrieval error (hybrid_query_text) is reported, not raised
    system, adapter, qdrant = arm_with(arm, qdrant_error=TimeoutError("qdrant timed out"))
    ans = system.answer(make_case())
    assert len(qdrant.calls) == 1
    assert adapter.calls == 0
    assert isinstance(ans.error, str) and ans.error != ""
    assert ans.numeric_values == []


@pytest.mark.criterion("C6")
@pytest.mark.parametrize("arm", ARMS)
def test_zero_call_budget_raises_run_halt(arm_with, make_case, arm):
    # oracle: requirement 9 — RunBudget(max_llm_calls=0) trips before the first call, and
    # RunHalt is the one exception that leaves answer()
    system, adapter, _ = arm_with(arm, run_budget=RunBudget(max_llm_calls=0))
    with pytest.raises(RunHalt):
        system.answer(make_case())
    assert adapter.calls == 0


_ERRORS = st.sampled_from(
    [
        ValueError,
        KeyError,
        RuntimeError,
        ProviderRejectedError,
        ConnectionRefusedError,
        TimeoutError,
    ]
)
_BOUNDARY = st.sampled_from(["provider", "embedder", "qdrant"])
_HALT_BUDGETS = st.sampled_from(
    [
        RunBudget(max_llm_calls=0),
        RunBudget(max_cost_usd=0.0),
        RunBudget(max_tokens=0),
        RunBudget(max_consecutive_failures=0),
    ]
)


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
@given(
    arm=st.sampled_from(ARMS),
    boundary=_BOUNDARY,
    exc_type=_ERRORS,
    message=st.text(alphabet="abcdefghij -", min_size=1, max_size=20),
)
def test_any_boundary_error_is_contained(arm_with, make_case, arm, boundary, exc_type, message):
    # oracle: requirement 9 — whichever boundary fails and however, answer() returns an
    # error answer with no numbers (closed book has only the provider boundary)
    if arm == "closed_book":
        boundary = "provider"
    err = exc_type(message)
    system, _, _ = arm_with(
        arm,
        provider_error=err if boundary == "provider" else None,
        embed_error=err if boundary == "embedder" else None,
        qdrant_error=err if boundary == "qdrant" else None,
    )
    ans = system.answer(make_case())
    assert isinstance(ans.error, str) and ans.error != ""
    assert ans.numeric_values == []
    assert ans.rows == []


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
@given(arm=st.sampled_from(ARMS), budget=_HALT_BUDGETS)
def test_any_tripped_budget_propagates(arm_with, make_case, arm, budget):
    # oracle: requirement 9 — every RunBudget threshold at zero trips before the first
    # call (RunBudgetExceeded or CircuitBreakerOpen, both RunHalt), and it propagates
    system, adapter, _ = arm_with(arm, run_budget=budget)
    with pytest.raises(RunHalt):
        system.answer(make_case())
    assert adapter.calls == 0
