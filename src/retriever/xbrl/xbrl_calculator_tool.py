"""Sandboxed calculator for plan `compute` steps (design doc §2.2 / §1.4 "Tool sandbox").

`step.op == "compute"` evaluates an arithmetic expression over earlier steps'
resolved values, exposed by `plan_executor` as `bindings: {step_id: value}`.
Per `plan_generator`'s contract
(prompts/templates/xbrl/xbrl_query_plan_generator.txt RULES #2/#3) and
`plan_validator.COMPUTE_EXPR_ALLOWED_RE`, a valid `expr`:

  - references only earlier step ids (identifiers) and numeric literals
  - uses only `+ - * /`, unary `+`/`-`, and parentheses for grouping

This module evaluates that restricted grammar via Python's `ast` module,
walking a small ALLOW-LIST of node types (`Constant`, `Name`, `BinOp` with
`+ - * /`, `UnaryOp` with `+ -`). Anything else — function calls, attribute or
subscript access, comparisons, boolean/string/collection literals, comprehensions,
`import`, etc. — is rejected by construction (the node type itself is not in the
allow-list), not by trying to denylist dangerous names. `__import__('os')`,
`().__class__.__bases__`, `[x for x in ...]`, `a if b else c`, `"x" + "y"`,
`a < b`, ... are all rejected.

`plan_validator.PlanValidator` already rejects forward/undefined references and
disallowed characters at plan-generation time; this module is the second,
independent layer of defense at execution time (never trust the LLM's output
alone for something that gets `eval`-adjacent treatment).
"""
from __future__ import annotations

import ast
import operator
import re
from collections.abc import Mapping
from typing import Dict, List, Optional

from src.logging_config import get_logger

logger = get_logger(__name__)


class CalculatorError(Exception):
    """Raised when a `compute` expression is invalid, unsafe, or cannot be evaluated."""


# Binary/unary operators allowed in a compute expr, mapped to their implementation.
# Deliberately excludes Pow, Mod, FloorDiv, MatMult, and all bitwise/boolean ops.
_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}

_UNARYOPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _is_constant(node: ast.AST, value: float) -> bool:
    return (
        isinstance(node, ast.Constant)
        and not isinstance(node.value, bool)
        and isinstance(node.value, (int, float))
        and node.value == value
    )


def _change_ratio_base(node: ast.AST) -> str | None:
    """The base id of ``(a - b) / b`` or ``a / b - 1`` (parentheses are already gone
    in the AST), else None."""
    if not isinstance(node, ast.BinOp):
        return None
    if (
        isinstance(node.op, ast.Div)
        and isinstance(node.right, ast.Name)
        and isinstance(node.left, ast.BinOp)
        and isinstance(node.left.op, ast.Sub)
        and isinstance(node.left.left, ast.Name)
        and isinstance(node.left.right, ast.Name)
        and node.left.right.id == node.right.id
    ):
        return node.right.id
    if (
        isinstance(node.op, ast.Sub)
        and _is_constant(node.right, 1)
        and isinstance(node.left, ast.BinOp)
        and isinstance(node.left.op, ast.Div)
        and isinstance(node.left.left, ast.Name)
        and isinstance(node.left.right, ast.Name)
    ):
        return node.left.right.id
    return None


def expr_unit(expr: str, units: Mapping[str, str | None]) -> str:
    """The unit of a compute expression's result (docs/specs/units-currency-integrity.md).

    Args:
        expr: A compute step's arithmetic expression (the `evaluate` grammar).
        units: The stored unit of each step id the expression references.

    Returns:
        The result's canonical unit, e.g. ``"USD"``, ``"pure"`` or ``"USD/shares"``.

    Raises:
        CalculatorError: an input's unit is unknown, two currencies meet in one
            expression, ``+``/``-`` join different units, or the expression is invalid.
    """
    tree = _parse_expr(expr)
    names = sorted({n.id for n in ast.walk(tree) if isinstance(n, ast.Name)})
    parsed: dict[str, dict[str, int]] = {}
    for name in names:
        if name not in units:
            raise CalculatorError(f"expr {expr!r} references step '{name}' with no unit entry")
        unit = units[name]
        if unit is None:
            raise CalculatorError(f"step '{name}' has no unit (unknown unit)")
        parsed[name] = parse_unit(unit)
    currencies = sorted({base for m in parsed.values() for base in m if is_currency(base)})
    if len(currencies) > 1:
        raise CalculatorError(
            f"expr {expr!r} mixes currencies {', '.join(currencies)} (no currency conversion)"
        )
    return canonical_unit(_unit_of(tree.body, parsed, expr))


# Units with no dimension (docs/specs/units-currency-integrity.md, Requirement 4): `pure`
# is the XBRL standard unit for a ratio; `Rate` is the custom measure AMD, AVGO and MU use
# for tax-rate reconciliations and covenant ratios. Exact, case-sensitive strings.
DIMENSIONLESS_UNITS = frozenset({"pure", "Rate"})

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


def is_currency(unit: str) -> bool:
    """Whether *unit* has the ISO 4217 shape (three upper-case letters)."""
    return bool(_CURRENCY_RE.match(unit))


def parse_unit(unit: str) -> dict[str, int]:
    """A stored unit string as a map from base unit to exponent.

    A dimensionless unit is ``{}``. ``"A/B"`` puts A's ``*`` factors at +1 and B's at -1.
    The store renders a per-share unit as the product ``"shares*<currency>"``, read as
    ``<currency>/shares``. A dimensionless factor or ``1`` is ignored.

    Args:
        unit: A stored unit string.

    Returns:
        Base unit to exponent; a factor that cancels (``"USD/USD"``) keeps its key at 0,
        so it still counts as a currency met.
    """
    exps: dict[str, int] = {}
    if unit in DIMENSIONLESS_UNITS:
        return exps

    def add(factor: str, n: int) -> None:
        if factor in DIMENSIONLESS_UNITS or factor == "1":
            return
        exps[factor] = exps.get(factor, 0) + n

    if "/" in unit:
        num, den = unit.split("/", 1)
        for f in num.split("*"):
            add(f, 1)
        for f in den.split("*"):
            add(f, -1)
        return exps
    factors = unit.split("*")
    others = [f for f in factors if f != "shares"]
    if len(factors) == 2 and len(others) == 1 and is_currency(others[0]):
        return {others[0]: 1, "shares": -1}
    for f in factors:
        add(f, 1)
    return exps


def canonical_unit(exps: Mapping[str, int]) -> str:
    """The canonical string of an exponent map, e.g. ``"USD"``, ``"USD/shares"``, ``"pure"``.

    Args:
        exps: Base unit to exponent.

    Returns:
        Positive factors sorted and joined by ``*``, then ``/`` and the negative ones;
        ``"pure"`` when there is none, ``"1/<denominator>"`` when only negatives remain.
    """
    pos = [f for f in sorted(exps) for _ in range(exps[f]) if exps[f] > 0]
    neg = [f for f in sorted(exps) for _ in range(-exps[f]) if exps[f] < 0]
    if not pos and not neg:
        return "pure"
    head = "*".join(pos) if pos else "1"
    return head + ("/" + "*".join(neg) if neg else "")


def _nonzero(exps: Mapping[str, int]) -> dict[str, int]:
    return {k: v for k, v in exps.items() if v}


def _unit_of(node: ast.AST, parsed: Mapping[str, dict[str, int]], expr: str) -> dict[str, int]:
    """The exponent map of *node*; the node set is `evaluate`'s allow-list."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise CalculatorError(f"non-numeric literal {node.value!r} in expr {expr!r}")
        return {}
    if isinstance(node, ast.Name):
        return _nonzero(parsed[node.id])
    if isinstance(node, ast.UnaryOp):
        if type(node.op) not in _UNARYOPS:
            raise CalculatorError(
                f"unary operator '{type(node.op).__name__}' is not allowed in expr {expr!r}"
            )
        return _unit_of(node.operand, parsed, expr)
    if isinstance(node, ast.BinOp):
        if type(node.op) not in _BINOPS:
            raise CalculatorError(
                f"operator '{type(node.op).__name__}' is not allowed in expr {expr!r}"
            )
        left = _unit_of(node.left, parsed, expr)
        right = _unit_of(node.right, parsed, expr)
        if isinstance(node.op, (ast.Add, ast.Sub)):
            if left != right:
                raise CalculatorError(
                    f"expr {expr!r} adds or subtracts different units: "
                    f"{canonical_unit(left)} and {canonical_unit(right)}"
                )
            return left
        sign = 1 if isinstance(node.op, ast.Mult) else -1
        out = dict(left)
        for k, v in right.items():
            out[k] = out.get(k, 0) + sign * v
        return _nonzero(out)
    raise CalculatorError(
        f"expression element '{type(node).__name__}' is not allowed in expr {expr!r}"
    )


def _parse_expr(expr: str) -> ast.Expression:
    """Parse *expr*; the length cap stays `evaluate`'s (a unit has no length)."""
    if not isinstance(expr, str) or not expr.strip():
        raise CalculatorError("expr must be a non-empty string")
    try:
        return ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise CalculatorError(f"invalid expression syntax: {e}") from e


class XbrlCalculatorTool:
    """Evaluates a `compute` step's `expr` against resolved step `bindings`.

    Example:
        >>> calc = XbrlCalculatorTool()
        >>> calc.evaluate(
        ...     "(dc26 - dc25) / dc25",
        ...     {"dc26": 193737000000.0, "dc25": 115186000000.0},
        ... )
        0.6818...
    """

    # Generous bound on expression source length - plan_validator caps step count
    # at 50 and exprs are short arithmetic, so anything this long is suspect.
    MAX_EXPR_LENGTH = 200

    def evaluate(self, expr: str, bindings: dict[str, float | None]) -> float:
        """Evaluate `expr`, resolving identifiers from `bindings`.

        Raises:
            CalculatorError: invalid syntax, a disallowed construct, a reference
                to an undefined/unresolved step id, or division by zero.
        """
        if not isinstance(expr, str) or not expr.strip():
            raise CalculatorError("expr must be a non-empty string")
        if len(expr) > self.MAX_EXPR_LENGTH:
            raise CalculatorError(f"expr exceeds max length {self.MAX_EXPR_LENGTH} chars")

        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError as e:
            raise CalculatorError(f"invalid expression syntax: {e}") from e

        result = self._eval(tree.body, bindings, expr)
        # Direct-use audit breadcrumb. The full, fact-id-linked numeric_lineage
        # event is emitted by PlanExecutor (it owns the per-step provenance);
        # this debug line covers calculator use outside the executor.
        logger.debug("calculator: expr=%r -> %r", expr, result)
        return result

    @staticmethod
    def referenced_step_ids(expr: str) -> list[str]:
        """Return the sorted set of step-id identifiers referenced in *expr*.

        Deterministic, side-effect-free AST walk used by the executor to build a
        compute step's numeric-lineage record (which earlier steps fed it).
        Returns ``[]`` for empty or unparseable expressions rather than raising.
        """
        if not isinstance(expr, str) or not expr.strip():
            return []
        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError:
            return []
        return sorted({n.id for n in ast.walk(tree) if isinstance(n, ast.Name)})

    @staticmethod
    def pct_change_base(expr: str) -> str | None:
        """The base step id when *expr* is a percentage change, else None.

        Recognized forms (any whitespace, redundant parentheses): ``(a - b) / b``,
        ``a / b - 1`` and ``(a - b) / b * 100``.

        Args:
            expr: A compute step's arithmetic expression.

        Returns:
            The step id of the base ``b``, or None for any other expression.
        """
        if not isinstance(expr, str) or not expr.strip():
            return None
        try:
            node = ast.parse(expr, mode="eval").body
        except SyntaxError:
            return None
        # "... * 100" / "100 * ...": a percentage expressed in points, same base.
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            if _is_constant(node.right, 100):
                node = node.left
            elif _is_constant(node.left, 100):
                node = node.right
        return _change_ratio_base(node)

    def not_meaningful_reason(
        self, expr: str, bindings: dict[str, float | None]
    ) -> str | None:
        """Why *expr* has no meaningful value for these *bindings*, if it has none.

        A percentage change from a base of zero or below is reported as "n/m" in
        financial statements rather than as a number.

        Args:
            expr: A compute step's arithmetic expression.
            bindings: Resolved step values, by step id.

        Returns:
            A non-empty reason when *expr* is a percentage change whose base is
            ``<= 0``; None otherwise.
        """
        base = self.pct_change_base(expr)
        if base is None:
            return None
        value = bindings.get(base)
        # A missing or non-numeric base is evaluate()'s error to report, not n/m.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if value > 0:
            return None
        return (
            f"percentage change from a non-positive base ({base} = {value:,.0f}) "
            "is not meaningful (n/m)"
        )

    # ------------------------------------------------------------------
    # AST walker (allow-list only)
    # ------------------------------------------------------------------

    def _eval(self, node: ast.AST, bindings: dict[str, float | None], expr: str) -> float:
        if isinstance(node, ast.Constant):
            value = node.value
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise CalculatorError(f"non-numeric literal {value!r} in expr {expr!r}")
            return float(value)

        if isinstance(node, ast.Name):
            if node.id not in bindings:
                raise CalculatorError(f"expr {expr!r} references undefined step id '{node.id}'")
            value = bindings[node.id]
            if value is None:
                raise CalculatorError(f"step '{node.id}' has no value (its lookup did not resolve)")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise CalculatorError(f"step '{node.id}' value {value!r} is not numeric")
            return float(value)

        if isinstance(node, ast.BinOp):
            op_fn = _BINOPS.get(type(node.op))
            if op_fn is None:
                raise CalculatorError(f"operator '{type(node.op).__name__}' is not allowed in expr {expr!r}")
            left = self._eval(node.left, bindings, expr)
            right = self._eval(node.right, bindings, expr)
            if op_fn is operator.truediv and right == 0:
                raise CalculatorError(f"division by zero in expr {expr!r}")
            return op_fn(left, right)

        if isinstance(node, ast.UnaryOp):
            op_fn = _UNARYOPS.get(type(node.op))
            if op_fn is None:
                raise CalculatorError(f"unary operator '{type(node.op).__name__}' is not allowed in expr {expr!r}")
            return op_fn(self._eval(node.operand, bindings, expr))

        # Anything else (Call, Attribute, Subscript, Compare, BoolOp, IfExp,
        # comprehensions, f-strings, collection/lambda literals, ...) is rejected.
        raise CalculatorError(f"expression element '{type(node).__name__}' is not allowed in expr {expr!r}")


if __name__ == "__main__":
    calc = XbrlCalculatorTool()
    bindings = {"dc26": 193737000000.0, "dc25": 115186000000.0, "tot26": 215938000000.0}

    print(calc.evaluate("(dc26 - dc25) / dc25", bindings))  # YoY growth
    print(calc.evaluate("dc26 / tot26", bindings))          # share of total

    for bad in ["__import__('os').system('echo pwn')", "(1).__class__", "1 if True else 2", "a < b", "2 ** 100"]:
        try:
            calc.evaluate(bad, bindings)
        except CalculatorError as e:
            print(f"rejected {bad!r}: {e}")
