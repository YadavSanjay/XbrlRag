# Comparing a produced number with its key at the precision the key carries: the
# reported fact's XBRL decimals, cents, the coarsest input, 3 significant figures or
# 0.1 percentage point, by metric kind.
# Spec: docs/specs/grading-precision.md.
#
# The steps (10**-decimals, cents, 0.1 point) are those of the
# shared number reader, src/numerics/stated_numbers.py, which the query pipeline's
# citation checks use too; this module adds the grader's rounding comparison.

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Context, Decimal, InvalidOperation
from typing import Any, NamedTuple

from src.eval.core.case import EvalCase
from src.eval.core.graders.numeric import close_enough
from src.numerics.stated_numbers import (
    PER_SHARE_STEP,
    PERCENT_POINT_STEP,
    reported_step,
)

# Rule kinds (requirement 1).
AMOUNT = "amount"
SHARES = "shares"
PER_SHARE = "per_share"
AGGREGATE = "aggregate"
RATIO = "ratio"
PERCENT = "percent"
LEGACY = "legacy"

# Comparisons.
QUANTUM = "quantum"
SIG_FIGS = "sig_figs"
EXACT = "exact"
REL_TOL = "rel_tol"

RATIO_SIG_FIGS = 3
# Both sides of a comparison are normalised to this many significant figures, which
# removes binary float noise (3.0 - 2.72 = 0.2799999999999998 reads 0.28).
NORMAL_SIG_FIGS = 15

_AGGREGATE_SHAPES = frozenset({"compute.difference", "screen.sum", "screen.median"})
_PERCENT_SHAPES = frozenset({"compute.yoy_growth", "compute.cagr"})
_RATIO_SHAPES = frozenset({"compute.ratio"})
_INCOME_PARTS = ("IncomeLoss", "ProfitLoss", "EarningsLossPerShare")
_INCOME_PREFIXES = ("EarningsPerShare", "ComprehensiveIncome")

# Wide enough that rounding a large amount at a fine place never overflows.
_CONTEXT = Context(prec=60)

# An ``INF`` (exact) decimals, as _decimals reads it.
_INF = "INF"


class PrecisionRule(NamedTuple):
    """How a produced number is compared with a key (requirement 1).

    Attributes:
        kind: ``amount``, ``shares``, ``per_share``, ``aggregate``, ``ratio``,
            ``percent`` or ``legacy``.
        compare: ``quantum``, ``sig_figs``, ``exact`` or ``rel_tol``.
        quantum: The place value compared at, for ``quantum``.
        sig_figs: The significant figures compared at, for ``sig_figs``.
        rel_tol: The relative tolerance, for ``rel_tol``.
    """

    kind: str
    compare: str
    quantum: float | None = None
    sig_figs: int | None = None
    rel_tol: float | None = None


def shape_of(case: EvalCase) -> str | None:
    """The case's ``shape:`` tag value (``lookup``, ``compute.ratio`` ...), or None."""
    for tag in case.tags:
        tag = str(tag)
        if tag.startswith("shape:"):
            return tag[len("shape:"):]
    return None


def _inputs(case: EvalCase) -> list[dict]:
    inputs = case.key.get("inputs") if isinstance(case.key, dict) else None
    return [i for i in inputs if isinstance(i, dict)] if isinstance(inputs, list) else []


def _decimals(raw: Any) -> int | str | None:
    """An XBRL ``decimals`` as an int, ``"INF"``, or None when missing or unreadable."""
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float) and raw.is_integer():
        return int(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if text.upper() == _INF:
            return _INF
        try:
            return int(text)
        except ValueError:
            return None
    return None


def _coarsest(values: Sequence[int | str | None]) -> int | str | None:
    """The coarsest integer decimals; else ``"INF"`` when every one is INF; else None."""
    integers = [d for d in values if isinstance(d, int)]
    if integers:
        return min(integers)
    if values and all(d == _INF for d in values):
        return _INF
    return None


def _same_value(a: Any, b: Any) -> bool:
    if not isinstance(a, (int, float)) or isinstance(a, bool):
        return False
    if not isinstance(b, (int, float)) or isinstance(b, bool):
        return False
    return close_enough(float(a), float(b), 1e-12) if b else a == b


def _scalar_decimals(case: EvalCase, inputs: list[dict]) -> int | str | None:
    """The decimals of the input whose value is the key; else the coarsest input's."""
    for item in inputs:
        if case.expected_value is not None and _same_value(item.get("value"),
                                                           case.expected_value):
            return _decimals(item.get("decimals"))
    return _coarsest([_decimals(i.get("decimals")) for i in inputs])


def _row_decimals(row: Mapping[str, Any], inputs: list[dict]) -> int | str | None:
    """A row's own decimals; else those of the input with its value and period end."""
    if "decimals" in row:
        return _decimals(row.get("decimals"))
    for item in inputs:
        if _same_value(item.get("value"), row.get("value")) and (
                item.get("period_end") == row.get("period_end")):
            return _decimals(item.get("decimals"))
    return None


def _from_decimals(kind: str, decimals: int | str | None, rel_tol: float) -> PrecisionRule:
    if isinstance(decimals, int):
        return PrecisionRule(kind, QUANTUM, quantum=reported_step(None, decimals))
    if decimals == _INF:
        return PrecisionRule(kind, EXACT)
    return PrecisionRule(kind, REL_TOL, rel_tol=rel_tol)


def precision_rule(case: EvalCase, row: dict | None = None) -> PrecisionRule:
    """The rule a case (or one of its expected rows) is graded at.

    Args:
        case: The case.
        row: One of the case's expected rows, for a screen or trend.

    Returns:
        The rule of requirement 1.
    """
    shape = shape_of(case)
    if shape is None:
        return PrecisionRule(LEGACY, REL_TOL, rel_tol=case.rel_tol)
    if shape in _RATIO_SHAPES:
        return PrecisionRule(RATIO, SIG_FIGS, sig_figs=RATIO_SIG_FIGS)
    if shape in _PERCENT_SHAPES:
        return PrecisionRule(PERCENT, QUANTUM, quantum=PERCENT_POINT_STEP / 100)
    inputs = _inputs(case)
    unit = case.unit or (str(inputs[0].get("unit") or "") if inputs else "")
    if unit.endswith("/shares"):
        return PrecisionRule(PER_SHARE, QUANTUM, quantum=PER_SHARE_STEP)
    if shape in _AGGREGATE_SHAPES:
        decimals = _coarsest([_decimals(i.get("decimals")) for i in inputs])
        return _from_decimals(AGGREGATE, decimals, case.rel_tol)
    kind = SHARES if unit == "shares" else AMOUNT
    if row is not None:
        return _from_decimals(kind, _row_decimals(row, inputs), case.rel_tol)
    return _from_decimals(kind, _scalar_decimals(case, inputs), case.rel_tol)


def matches_at_precision(value: float, expected: float, rule: PrecisionRule) -> bool:
    """Whether a produced number matches a key under a rule (requirement 2).

    Args:
        value: The produced number.
        expected: The key.
        rule: The rule from ``precision_rule``.

    Returns:
        True when the number matches.
    """
    if rule.compare == REL_TOL or rule.kind == LEGACY:
        return close_enough(value, expected, rule.rel_tol or 0.0)
    if not (math.isfinite(value) and math.isfinite(expected)):
        return False
    a, t = _normal(value), _normal(expected)
    if rule.compare == EXACT:
        return a == t
    if rule.compare == SIG_FIGS:
        if t == 0:
            return a == 0
        q = Decimal(1).scaleb(t.adjusted() - (rule.sig_figs or RATIO_SIG_FIGS) + 1)
    else:
        q = _power_of_ten(rule.quantum or 0.0)
    if _agrees(a, t, q):
        return True
    # A ratio or percent key also accepts its percentage written without the scale.
    return rule.kind in (RATIO, PERCENT) and _agrees(a, t.scaleb(2), q.scaleb(2))


def precision_outcome(
    value: float, stated_quantum: float | None, expected: float, rule: PrecisionRule
) -> str:
    """Whether a produced number matches, is only imprecise, or is wrong (requirement 8).

    Args:
        value: The produced number.
        stated_quantum: The place value of its last stated digit, or None when the
            answer's text does not state it.
        expected: The key.
        rule: The rule from ``precision_rule``.

    Returns:
        ``"match"``, ``"imprecise"`` (it agrees with the key rounded at its own stated
        precision, which is coarser than the rule's) or ``"wrong"``.
    """
    if matches_at_precision(value, expected, rule):
        return "match"
    if rule.kind == LEGACY or stated_quantum is None or stated_quantum <= 0:
        return "wrong"
    if not (math.isfinite(value) and math.isfinite(expected)):
        return "wrong"
    a, t, s = _normal(value), _normal(expected), _power_of_ten(stated_quantum)
    readings = [t.scaleb(2), t] if rule.kind in (RATIO, PERCENT) else [t]
    for key in readings:
        rounded = _round(key, s)
        if rounded is not None and rounded != 0 and a == rounded:
            return "imprecise"
    return "wrong"


def is_income_metric(case: EvalCase) -> bool:
    """Whether a case's metric is income-type, so a loss word signs it (requirement 9).

    Args:
        case: The case; its concept is ``case.concept``, else the first key input's.

    Returns:
        True for net, operating, pretax and comprehensive income, EPS and IFRS profit.
    """
    concept = case.concept
    if not concept:
        inputs = _inputs(case)
        concept = str(inputs[0].get("concept") or "") if inputs else ""
    local = concept.rsplit(":", 1)[-1]
    return any(p in local for p in _INCOME_PARTS) or local.startswith(_INCOME_PREFIXES)


def _normal(x: float) -> Decimal:
    """A float as a decimal of ``NORMAL_SIG_FIGS`` significant figures, trailing zeros cut."""
    if x == 0:
        return Decimal(0)
    return Decimal(f"{x:.{NORMAL_SIG_FIGS - 1}e}").normalize()


def _power_of_ten(step: float) -> Decimal:
    """A positive step read as the power of ten it is (1e-05 -> Decimal("1E-5"))."""
    return Decimal(1).scaleb(round(math.log10(step)))


def _round(x: Decimal, q: Decimal) -> Decimal | None:
    """Rounds half away from zero to a multiple of the power of ten ``q``.

    Returns None when the result would need more than ``_CONTEXT``'s digits: a value
    that large at a place that fine (a subnormal float's) equals no 15-figure number.
    """
    try:
        return x.quantize(q, rounding=ROUND_HALF_UP, context=_CONTEXT).normalize(_CONTEXT)
    except InvalidOperation:
        return None


def _place(x: Decimal) -> Decimal:
    """The place value of a number's last stated digit, trailing zeros not counted.

    An integer-valued answer such as 80,150 states its digits down to the 10, so it is
    compared as the key rounded there (requirement 2's "the key rounded to any place
    finer than the rule passes").
    """
    exponent = x.normalize(_CONTEXT).as_tuple().exponent
    if not x or not isinstance(exponent, int):
        return Decimal(1)
    return Decimal(1).scaleb(exponent)


def _agrees(a: Decimal, t: Decimal, q: Decimal) -> bool:
    """Requirement 2: equal at ``q``, or a finer answer that is the key rounded there."""
    rounded = _round(a, q)
    if rounded is not None and rounded == _round(t, q):
        return True
    s = _place(a)
    return s < q and a == _round(t, s)
