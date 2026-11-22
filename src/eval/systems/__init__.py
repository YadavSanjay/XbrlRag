"""WS-9 — systems under test.

- ``xbrlrag_full``: the production plan-based pipeline.
- ``closed_book``: the model alone, no documents; a control for how often it states a
  wrong figure on its own, not a benchmark.
- ``naive_rag_plain`` / ``naive_rag_filtered``: top-10 hybrid Qdrant search over the
  same collection, then one LLM call; the filtered variant restricts retrieval to the
  case's ticker and fiscal year. They show what the plan and fact store buy.
- ``long_context_oracle``: the case's own canonical annual filing, whole, in one call to
  an explicit frontier model (by default the active profile's frontier tier,
  docs/specs/arm-model-from-profile.md). It is handed the right filing,
  which favours it; a case with no single filing is reported as ``excluded:``. Formerly
  ``long_context``, which still builds it; a new run is stored under the new name.
- ``long_context_resolved``: the same call on the one filing XbrlRag's own resolver
  chooses from the question.
- ``long_context_multi``: the k filings that resolver names, in one call.
- ``html_rag``: a competent RAG over the filings' HTML with no XBRL in it (its own index,
  rerank, cited cells and passages, the calculator for every number): the baseline XbrlRag
  has to beat.
- ``frontier_sec_tools``: a frontier model with a fixed set of tools over the public SEC APIs
  (EDGAR submissions, companyfacts, frames, full-text search, filing documents), dated by a
  snapshot; the real competitor on cross-filer and cross-period questions. It reads none of
  XbrlRag's data.

The baseline arms answer with a JSON object read by
``src.eval.core.answer_parsing.parse_structured_answer``, so every system is graded by
the same rules. Specs: docs/specs/baseline-arms.md, docs/specs/long-context-arm.md,
docs/specs/long-context-arms.md, docs/specs/html-rag-arm.md,
docs/specs/frontier-sec-tools.md; design doc §5.2 / §9.
"""

from collections.abc import Callable

from src.eval.core.system import SystemUnderTest
from src.eval.systems.closed_book import ClosedBookSystem
from src.eval.systems.frontier_sec_tools import FrontierSecToolsSystem
from src.eval.systems.html_rag import HtmlRagSystem
from src.eval.systems.long_context import LongContextSystem
from src.eval.systems.long_context_arms import LongContextMultiSystem, LongContextResolvedSystem
from src.eval.systems.naive_rag import NaiveRagSystem
from src.eval.systems.xbrlrag_full import XbrlRagFullSystem
from src.observability.audit_store import enable_audit_persistence

__all__ = [
    "ClosedBookSystem",
    "FrontierSecToolsSystem",
    "HtmlRagSystem",
    "LONG_CONTEXT_NAMES",
    "MODEL_NAMES",
    "LongContextMultiSystem",
    "LongContextResolvedSystem",
    "LongContextSystem",
    "NaiveRagSystem",
    "XbrlRagFullSystem",
    "build_system",
    "stored_system_names",
    "system_names",
]

_SYSTEM_NAMES = (
    "xbrlrag_full",
    "closed_book",
    "naive_rag_plain",
    "naive_rag_filtered",
    "long_context_oracle",
    "long_context_resolved",
    "long_context_multi",
    "html_rag",
    "frontier_sec_tools",
)

# A former name -> the current one. It still builds, and persisted rows keep it.
_RENAMED = {"long_context": "long_context_oracle"}

_LONG_CONTEXT_ARMS: dict[str, Callable[..., SystemUnderTest]] = {
    "long_context_oracle": LongContextSystem,
    "long_context_resolved": LongContextResolvedSystem,
    "long_context_multi": LongContextMultiSystem,
}

# Every name that takes a model: the three arms and the former name.
LONG_CONTEXT_NAMES = (*_LONG_CONTEXT_ARMS, "long_context")

# Every name that takes an explicit model: the long-context arms and the tool-calling arm.
MODEL_NAMES = (*LONG_CONTEXT_NAMES, "frontier_sec_tools")


def system_names() -> tuple[str, ...]:
    """The systems ``scripts/eval/run_suite.py --system`` can run.

    Returns:
        Every selectable system name, XbrlRag first.
    """
    return _SYSTEM_NAMES


def stored_system_names(name: str) -> tuple[str, ...]:
    """Every name a system's persisted runs can carry.

    Args:
        name: A system name, current or former.

    Returns:
        The current name first, then any former one.
    """
    current = _RENAMED.get(name, name)
    return (current, *(old for old, new in _RENAMED.items() if new == current))


def build_system(
    name: str, *, persist_audit: bool = False, model: str | None = None
) -> SystemUnderTest:
    """Builds a system by name, without touching the network.

    Args:
        name: One of :func:`system_names`, or a former name.
        persist_audit: Persist each case's trace (LLM calls, cost, final answer) to the
            audit schema, so the eval UI's per-case trace links resolve. XbrlRag
            registers the sink itself when its pipeline is built; every other arm
            answers inside a trace (``answer_in_trace``), and the sink is registered
            here. Building a system opens no connection either way.
        model: The exact model ID for a long-context arm or ``frontier_sec_tools`` (its
            default when None).

    Returns:
        The system under test.

    Raises:
        ValueError: If the name is not a known system, a model is given for any system
            but a long-context arm or ``frontier_sec_tools``, or the model has no price.
    """
    current = _RENAMED.get(name, name)
    if model is not None and current not in MODEL_NAMES:
        raise ValueError(
            f"--model applies only to the long-context arms and frontier_sec_tools, "
            f"not {name!r}"
        )
    if current == "xbrlrag_full":
        return XbrlRagFullSystem(persist_audit=persist_audit)
    system = _build_baseline(current, name, model)
    if persist_audit:
        enable_audit_persistence()
    return system


def _build_baseline(current: str, name: str, model: str | None) -> SystemUnderTest:
    """Builds a comparison arm by its current name.

    Args:
        current: The arm's current name.
        name: The name as given, for the error message.
        model: The exact model ID for a long-context arm or ``frontier_sec_tools``.

    Returns:
        The system under test.

    Raises:
        ValueError: If the name is not a known system.
    """
    if current == "html_rag":
        return HtmlRagSystem()
    if current == "closed_book":
        return ClosedBookSystem()
    if current == "frontier_sec_tools":
        if model is None:
            return FrontierSecToolsSystem()
        return FrontierSecToolsSystem(model=model)
    if current == "naive_rag_plain":
        return NaiveRagSystem(filtered=False)
    if current == "naive_rag_filtered":
        return NaiveRagSystem(filtered=True)
    if current in _LONG_CONTEXT_ARMS:
        arm = _LONG_CONTEXT_ARMS[current]
        return arm() if model is None else arm(model=model)
    raise ValueError(f"unknown system {name!r}; known: {', '.join(_SYSTEM_NAMES)}")
