"""CAGR and ratio cues read from a question by rule (docs/specs/plan-cagr-and-ratios.md).

A question that asks for a CAGR or a ratio must be answered by a plan that computes it: the
cue is read from the question's words by a fixed rule (never by the LLM), keeps the lookup
fast path from answering, makes the plan generator's retry demand the operation, keeps the
decomposer from separating its operands, and marks an answer that lacks the asked value as
not fully answered.
"""

from __future__ import annotations

import ast
import copy
import re
from collections.abc import Mapping, Sequence
from typing import Any

# Requirement 5: a slash with whitespace on both sides between words ("A / B").
_SPACED_SLASH = re.compile(r"[A-Za-z)]\s+/\s+[A-Za-z(]")
_NON_WORD = re.compile(r"[^a-z0-9]+")

_CAGR_WORDS = frozenset({"cagr", "cagrs"})
_COMPOUND = frozenset({"compound", "compounded"})
_AFTER_COMPOUND = frozenset({"annual", "annually", "growth", "rate"})
_ANNUALIZED = frozenset({"annualized", "annualised"})
_SHARE_WORDS = frozenset({"percentage", "percent", "share", "proportion", "fraction"})
_RATIO_WORDS = frozenset({"ratio", "ratios"})
# Words that make "to ... ratio" a destination ("respond to the ratio covenant"), not an
# "X to Y ratio".
_NOT_OPERAND = frozenset({"the", "a", "an", "its", "their", "this", "that"})

# Steps that rank or split a metric; screen-aggregates owns those questions.
_EXEMPT_OPS = frozenset({"screen", "aggregate", "breakdown"})
_CUED_OPERATIONS = frozenset({"cagr", "ratio"})


def _words(text: str) -> list[str]:
    """The question's words: lower case, ``%`` read as ``percent``, punctuation as space."""
    lowered = str(text).lower().replace("%", " percent ")
    return _NON_WORD.sub(" ", lowered).split()


def _has_cagr_cue(words: list[str]) -> bool:
    for i, word in enumerate(words):
        following = words[i + 1] if i + 1 < len(words) else None
        if word in _CAGR_WORDS:
            return True
        if word in _COMPOUND and following in _AFTER_COMPOUND:
            return True
        if word in _ANNUALIZED and following == "growth":
            return True
    return False


def _has_ratio_cue(text: str, words: list[str]) -> bool:
    if _SPACED_SLASH.search(str(text)):
        return True
    n = len(words)
    for i, word in enumerate(words):
        following = words[i + 1] if i + 1 < n else None
        if word == "ratio" and following == "of":
            return True
        if word == "divided" and following == "by":
            return True
        if (word == "as" and following == "a" and i + 3 < n
                and words[i + 2] in _SHARE_WORDS and words[i + 3] == "of"):
            return True
        if word == "to" and i > 0:
            for gap in (1, 2):
                end = i + 1 + gap
                if end >= n or words[end] not in _RATIO_WORDS:
                    continue
                if not any(w in _NOT_OPERAND for w in words[i + 1:end]):
                    return True
    return False


def _has_difference_cue(words: list[str]) -> bool:
    return any(word == "difference" and following in ("between", "in")
               for word, following in zip(words, words[1:], strict=False))


def operation_kinds(text: str) -> frozenset[str]:
    """The operation kinds a text's words cue.

    Args:
        text: A question or sub-question.

    Returns:
        A subset of ``{"cagr", "ratio", "difference"}`` (Requirement 5).
    """
    words = _words(text)
    kinds: set[str] = set()
    if _has_cagr_cue(words):
        kinds.add("cagr")
    if _has_ratio_cue(text, words):
        kinds.add("ratio")
    if _has_difference_cue(words):
        kinds.add("difference")
    return frozenset(kinds)


def operation_cue(query: str) -> str | None:
    """The operation a question asks for by an unambiguous cue.

    Args:
        query: The question.

    Returns:
        ``"cagr"``, else ``"ratio"``, else None (Requirement 5).
    """
    kinds = operation_kinds(query)
    if "cagr" in kinds:
        return "cagr"
    if "ratio" in kinds:
        return "ratio"
    return None


def operation_intent(intent: str, operation: str | None) -> str:
    """The intent a cued question is routed with.

    Args:
        intent: The routed intent value, e.g. ``"NUMERIC_LOOKUP"``.
        operation: ``operation_cue(query)``.

    Returns:
        ``"NUMERIC_COMPUTE"`` for a cued ``NUMERIC_LOOKUP``; otherwise ``intent``
        (Requirement 6).
    """
    if intent == "NUMERIC_LOOKUP" and operation is not None:
        return "NUMERIC_COMPUTE"
    return intent


def _has_name(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Name) for n in ast.walk(node))


def _is_hundred(node: ast.AST) -> bool:
    return (isinstance(node, ast.Constant) and not isinstance(node.value, bool)
            and isinstance(node.value, (int, float)) and node.value == 100)


def is_ratio_expr(expr: Any) -> bool:
    """Whether a compute expression divides one step-based quantity by another.

    Args:
        expr: A compute step's ``expr``.

    Returns:
        True for a top-level division (after one outer ``* 100``) whose two sides each name
        a step (Requirement 7).
    """
    if not isinstance(expr, str) or not expr.strip():
        return False
    try:
        node: ast.AST = ast.parse(expr.strip(), mode="eval").body
    except (SyntaxError, ValueError, RecursionError):
        return False
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        if _is_hundred(node.right):
            node = node.left
        elif _is_hundred(node.left):
            node = node.right
    return (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
            and _has_name(node.left) and _has_name(node.right))


def _plan_steps(plan: Any) -> list[Mapping[str, Any]] | None:
    if not isinstance(plan, Mapping):
        return None
    steps = plan.get("steps")
    if not isinstance(steps, list):
        return None
    return [s for s in steps if isinstance(s, Mapping)]


def operation_plan_errors(plan: Any, operation: str | None) -> list[str]:
    """Plan errors when a plan does not do the operation the question cues.

    Run by the plan generator beside ``PlanValidator.validate``; the errors feed the same
    retry.

    Args:
        plan: The planner's ``{"steps": [...]}``.
        operation: ``operation_cue(query)``.

    Returns:
        At most one error (Requirement 7); ``[]`` when there is nothing to check.
    """
    if operation not in _CUED_OPERATIONS:
        return []
    steps = _plan_steps(plan)
    if steps is None or any(s.get("op") in _EXEMPT_OPS for s in steps):
        return []
    if operation == "cagr":
        if any(s.get("op") == "cagr" for s in steps):
            return []
        return [
            "the question asks for a CAGR: add a 'cagr' step "
            '{"id": ..., "op": "cagr", "start": <earlier lookup id>, "end": <later lookup '
            'id>} over two lookups of the same company and concept_family (the years are '
            "derived from their fy; never write a 'years' field, a power or an average)"
        ]
    if any(s.get("op") == "compute" and is_ratio_expr(s.get("expr")) for s in steps):
        return []
    return [
        "the question asks for a ratio: add a 'compute' step whose expr divides one "
        "looked-up step by the other (e.g. \"num / den\", or \"num / den * 100\" for a "
        "percentage); stating the two components is not an answer"
    ]


def split_loses_operation(query: str, subqueries: Sequence[str]) -> bool:
    """Whether a decomposition separated an operation's operands.

    Args:
        query: The original question.
        subqueries: The decomposer's sub-query texts.

    Returns:
        True when an operation kind of the query is in no sub-query (Requirement 8).
    """
    if len(subqueries) < 2:
        return False
    kept: set[str] = set()
    for sub in subqueries:
        kept |= operation_kinds(sub)
    return bool(operation_kinds(query) - kept)


def _status(value: Any) -> str:
    return str(getattr(value, "value", value))


def mark_missing_operation(
    response: Mapping[str, Any], query: str, operation: str | None
) -> dict[str, Any]:
    """Declare an asked CAGR or ratio missing from an answer as an unanswered part.

    Args:
        response: A (sub-)query's verified response as a dict (``XbrlRagResponse``
            ``model_dump()`` shape).
        query: That (sub-)query's text.
        operation: ``operation_cue(query)``.

    Returns:
        A new dict: unchanged when the asked value is present, else with one unanswered part
        and ``fully_answered`` False (Requirement 9).
    """
    out: dict[str, Any] = copy.deepcopy(dict(response))
    if operation not in _CUED_OPERATIONS:
        return out
    execution = out.get("execution_result")
    steps = [s for s in ((execution or {}).get("steps") or []) if isinstance(s, Mapping)] \
        if isinstance(execution, Mapping) else []
    if any(s.get("op") in _EXEMPT_OPS for s in steps):
        return out
    plan_steps = _plan_steps(out.get("plan")) or []
    exprs = {str(s.get("id")): s.get("expr") for s in plan_steps if s.get("id") is not None}
    cited = {str(fid) for detail in (out.get("answer_details") or [])
             if isinstance(detail, Mapping) for fid in (detail.get("fact_ids") or [])}

    def of_kind(step: Mapping[str, Any]) -> bool:
        if operation == "cagr":
            return step.get("op") == "cagr"
        return step.get("op") == "compute" and is_ratio_expr(exprs.get(str(step.get("step_id"))))

    asked = [s for s in steps if of_kind(s)]
    if any(_status(s.get("status")) == "OK" and str(s.get("step_id")) in cited for s in asked):
        return out

    label = "CAGR" if operation == "cagr" else "ratio"
    reason = (f"The question asks for a {label}, but the answer does not state one computed "
              f"from the filed facts")
    failures = [f"step {s.get('step_id')}: {s.get('error')}" for s in asked
                if _status(s.get("status")) != "OK" and s.get("error")]
    if failures:
        reason += " (" + "; ".join(failures) + ")"
    reason += "."
    part = {"question_fragment": query, "reason": reason}
    parts = list(out.get("unanswered_parts") or [])
    if part not in parts:
        parts.append(part)
    out["unanswered_parts"] = parts
    metadata = dict(out.get("metadata") or {})
    metadata["fully_answered"] = False
    out["metadata"] = metadata
    return out
