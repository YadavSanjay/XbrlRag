"""H-X case drafts: amounts written the way ``check-keys`` reads them, and YAML output.

The UI builds a case dict from what you picked; ``render`` writes it as YAML in the
template's style, and ``assess`` runs the H-X tool's own structure check
(``validate_hx``) and, for numeric kinds, its own key check (``resolve_hx_case``). Both
report case ids, paths and statuses only, as they do on the command line.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

import yaml

from src.eval.set_b.core import load_authoring
from src.eval.set_b.hx import validate_hx

SCALE_EXPONENT = {"units": 0, "thousands": 3, "millions": 6, "billions": 9}


def default_scale(value: float, unit: str | None, decimals: str | None) -> str:
    """The scale the filing itself is written in, as far as ``decimals`` says.

    Args:
        value: The value in units.
        unit: The fact's unit.
        decimals: The fact's ``decimals`` attribute.

    Returns:
        ``millions`` for amounts reported to a hundred thousand or coarser (so ``-5``
        writes one decimal in millions, ``"34,124.1"``), ``thousands`` to the thousand,
        else ``units``; per-share amounts and ratios stay in units.
    """
    if unit and ("/" in unit or unit == "pure"):
        return "units"
    try:
        d = int(decimals or "")
    except ValueError:
        d = 0
    if d <= -5 and abs(value) >= 1e6:
        return "millions"
    if d <= -3 and abs(value) >= 1e3:
        return "thousands"
    return "units"


def format_amount(value: float, scale: str, decimals: str | None) -> str:
    """Writes a value at a scale with exactly the digits the filing reports.

    The number of decimal places is what ``decimals`` claims at that scale (for example
    ``decimals=-5`` in millions is one place), so the written amount's half-unit matches
    the filing's precision. ``INF`` or an absent ``decimals`` writes the shortest exact
    form.

    Args:
        value: The value in units.
        scale: ``units``, ``thousands``, ``millions`` or ``billions``.
        decimals: The fact's ``decimals`` attribute.

    Returns:
        E.g. ``"391,035"``; a negative value gets a leading minus.
    """
    exp = SCALE_EXPONENT[scale]
    scaled = Decimal(repr(value)) / (Decimal(10) ** exp)
    try:
        places = max(0, int(decimals or "") + exp)
    except ValueError:
        places = None
    if places is None:
        text = format(scaled.normalize(), "f")
        whole, _, frac = text.partition(".")
        sign = "-" if whole.startswith("-") else ""
        whole = whole.lstrip("-")
        out = f"{sign}{int(whole):,}"
        return out + (f".{frac}" if frac else "")
    q = scaled.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)
    return f"{q:,.{places}f}"


def format_percent(value: float, places: int = 1) -> str:
    """A ratio as a percent string (``0.1001`` -> ``"10.0%"``)."""
    if value is None or math.isnan(value):
        return ""
    return f"{value * 100:.{places}f}%"


class _Dumper(yaml.SafeDumper):
    """Block style for the case, flow style for leaf lists and amount mappings."""


def _represent_dict(dumper: yaml.SafeDumper, data: dict[str, Any]) -> yaml.Node:
    flow = all(not isinstance(v, dict | list) for v in data.values()) and len(data) <= 6
    return dumper.represent_mapping("tag:yaml.org,2002:map", data.items(),
                                    flow_style=flow and "question" not in data
                                    and "id" not in data and "ranking" not in data)


def _represent_list(dumper: yaml.SafeDumper, data: list[Any]) -> yaml.Node:
    flow = all(not isinstance(v, dict | list) for v in data) and not any(
        isinstance(v, str) and len(v) > 40 for v in data)
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=flow)


_Dumper.add_representer(dict, _represent_dict)
_Dumper.add_representer(list, _represent_list)


def render(case: dict[str, Any]) -> str:
    """The case as one ``cases:`` list item, ready to paste under ``cases:``."""
    body = yaml.dump([case], Dumper=_Dumper, sort_keys=False, allow_unicode=True,
                     width=100, default_flow_style=False)
    return "\n".join("  " + line if line else line for line in body.splitlines()) + "\n"


def assess(case: dict[str, Any], dev_filers: frozenset[str], heldout: frozenset[str],
           resolve: Callable[[dict[str, Any]], dict[str, Any]] | None) -> dict[str, Any]:
    """Round-trips the YAML, then runs the H-X validator and (when possible) the key check.

    Args:
        case: The draft case.
        dev_filers: The tickers H-X may name (the tool's corpus).
        heldout: The refused tickers.
        resolve: ``resolve_hx_case`` bound to a store (``FactStore.resolve``), or None to
            skip the key check.

    Returns:
        ``{yaml, errors, check}``; ``check`` is ``resolve_hx_case``'s result or None
        (skipped, or the case did not validate).
    """
    text = render(case)
    parsed = load_authoring("cases:\n" + text)
    report = validate_hx(parsed, dev_filers, heldout)
    check = None
    if resolve is not None and not report["errors"] and case.get("kind") not in (
            "text", "honesty"):
        check = resolve(parsed[0])
    # validate_hx's only warnings are the per-kind count targets, meaningless for one case.
    return {"yaml": text, "errors": report["errors"], "check": check}
