# Key-free perturbation checks (v2 plan section 3.5): plan slots, plan and answer
# perturbation verdicts, and the deterministic ticker / year question perturbations.
# Pure functions: no pipeline, LLM or database. Spec: docs/specs/keyfree-checks.md.
from __future__ import annotations

import re
from typing import Any

_SLOT_KEYS = (
    "op", "cik", "ticker", "fy", "fiscal_period", "ytd", "as_originally_reported", "as_of",
    "concept_family", "dimensions", "axis", "metric_kind", "order", "top_n", "min_threshold",
    "unit", "expr",
)
# A number (kept whole, so the "e5" of "1e5" is not read as an identifier) or an identifier.
_EXPR_TOKEN = re.compile(r"\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?|[A-Za-z_]\w*")
_WHITESPACE = re.compile(r"\s+")
# A 4-digit 20dd with no digit either side, or FYdd (any case) with no letter or digit
# before and no digit after.
_YEAR_MENTION = re.compile(
    r"(?<![A-Za-z0-9])(?P<fy>[Ff][Yy])(?P<yy>\d\d)(?!\d)|(?<!\d)(?P<y4>20\d\d)(?!\d)")


def _norm_cik(cik: Any) -> Any:
    """Zero-pads an int or digit-string cik to 10 digits; anything else is kept."""
    if isinstance(cik, bool):
        return cik
    if isinstance(cik, int):
        return f"{cik:010d}" if cik >= 0 else cik
    if isinstance(cik, str) and cik.strip().isdigit():
        return cik.strip().zfill(10)
    return cik


def _norm_dimensions(dims: Any) -> Any:
    """Normalises a dimensions slot: empty or "NONE" to "NONE", a dict lower-cased."""
    if dims is None or dims == {} or (isinstance(dims, str) and dims.strip().upper() == "NONE"):
        return "NONE"
    if isinstance(dims, dict):
        return {str(k).strip().lower(): str(v).strip().lower() for k, v in dims.items()}
    return dims


def _norm_expr(expr: Any, positions: dict[str, int]) -> str | None:
    """Replaces step ids by `$<index>` and drops all whitespace."""
    if not isinstance(expr, str):
        return None

    def repl(m: re.Match[str]) -> str:
        tok = m.group(0)
        return f"${positions[tok]}" if tok in positions else tok

    return _WHITESPACE.sub("", _EXPR_TOKEN.sub(repl, expr))


def _step_slots(step: dict, positions: dict[str, int]) -> dict:
    """Builds one step's normalised slot dict (requirement 9)."""
    slots: dict[str, Any] = {k: step.get(k) for k in _SLOT_KEYS}
    if isinstance(slots["ticker"], str):
        slots["ticker"] = slots["ticker"].strip().upper()
    slots["cik"] = _norm_cik(slots["cik"])
    fp = slots["fiscal_period"]
    slots["fiscal_period"] = "FY" if fp is None else (fp.upper() if isinstance(fp, str) else fp)
    for key in ("ytd", "as_originally_reported"):
        if slots[key] is None:
            slots[key] = False
    dims = step["dimensions"] if "dimensions" in step else step.get("dimension")
    slots["dimensions"] = _norm_dimensions(dims)
    if isinstance(slots["order"], str):
        slots["order"] = slots["order"].lower()
    slots["expr"] = _norm_expr(slots["expr"], positions)
    return slots


def plan_slots(plan: dict) -> list[dict]:
    """Extract the normalised semantic slots of each step of a `{"steps": [...]}` plan.

    Args:
        plan: The plan JSON as produced by the plan generator.

    Returns:
        One dict per step, in plan order, keyed by the slot names of the spec.

    Raises:
        ValueError: `plan` is not a dict with a `steps` list, or a step is not a dict.
    """
    if not isinstance(plan, dict) or not isinstance(plan.get("steps"), list):
        raise ValueError("plan must be a dict with a 'steps' list")
    steps = plan["steps"]
    if not all(isinstance(s, dict) for s in steps):
        raise ValueError("every plan step must be a dict")
    positions: dict[str, int] = {}
    for i, step in enumerate(steps):
        sid = step.get("id")
        if isinstance(sid, str) and sid not in positions:
            positions[sid] = i
    return [_step_slots(step, positions) for step in steps]


def _has_steps(plan: Any) -> bool:
    """Whether a plan has a non-empty list of dict steps."""
    steps = plan.get("steps") if isinstance(plan, dict) else None
    return isinstance(steps, list) and bool(steps) and all(isinstance(s, dict) for s in steps)


def _validate_perturbation(perturbation: dict) -> None:
    """Raises ValueError for an unknown kind, `from == to` or a lone cik."""
    kind = perturbation.get("kind")
    if kind == "ticker":
        if str(perturbation["from"]).upper() == str(perturbation["to"]).upper():
            raise ValueError("ticker perturbation: from equals to")
        if ("from_cik" in perturbation) != ("to_cik" in perturbation):
            raise ValueError("ticker perturbation: give both from_cik and to_cik, or neither")
    elif kind == "year":
        if perturbation["from"] == perturbation["to"]:
            raise ValueError("year perturbation: from equals to")
    else:
        raise ValueError(f"unknown perturbation kind {kind!r}")


def _diff(step: int, slot: str, original: Any, perturbed: Any) -> dict:
    """A difference record."""
    return {"step": step, "slot": slot, "original": original, "perturbed": perturbed}


def check_plan_perturbation(original_plan: dict, perturbed_plan: dict, perturbation: dict) -> dict:
    """Check that two plans differ in exactly the perturbed slots, and as expected.

    Args:
        original_plan: The original question's plan.
        perturbed_plan: The perturbed question's plan.
        perturbation: `{"kind": "ticker"|"year", "from", "to"}` (ticker may add ciks).

    Returns:
        A verdict dict (`check`, `verdict`, `reason`, `differences`, `changed`).

    Raises:
        ValueError: Unknown perturbation kind, or `from` equals `to`.
    """
    _validate_perturbation(perturbation)

    def result(verdict: str, reason: str, differences: list[dict], changed: list[dict]) -> dict:
        return {"check": "plan_perturbation", "verdict": verdict, "reason": reason,
                "differences": differences, "changed": changed}

    if not _has_steps(original_plan) or not _has_steps(perturbed_plan):
        return result("not_applicable", "no_plan", [], [])
    orig = plan_slots(original_plan)
    pert = plan_slots(perturbed_plan)
    if len(orig) != len(pert):
        return result("inconsistent", "step_count", [], [])
    if any(o["op"] != p["op"] for o, p in zip(orig, pert, strict=True)):
        return result("inconsistent", "op_mismatch", [], [])

    kind = perturbation["kind"]
    perturbed_keys = ("ticker", "cik") if kind == "ticker" else ("fy",)
    unexpected: list[dict] = []
    wrong: list[dict] = []
    differences: list[dict] = []
    changed: list[dict] = []
    has_target = False
    for i, (o_step, p_step) in enumerate(zip(orig, pert, strict=True)):
        for slot in _SLOT_KEYS:
            o, p = o_step[slot], p_step[slot]
            if slot not in perturbed_keys:
                if o != p:
                    d = _diff(i, slot, o, p)
                    unexpected.append(d)
                    differences.append(d)
                continue
            if o != p:
                changed.append(_diff(i, slot, o, p))
            accept_any, expected = _expected_slot(slot, o, o_step, perturbation)
            if accept_any:
                has_target = True
                continue
            if expected != o:
                has_target = True
            if p != expected:
                d = _diff(i, slot, o, p)
                wrong.append(d)
                differences.append(d)
    if unexpected:
        return result("inconsistent", "unexpected_change", differences, changed)
    if wrong:
        return result("inconsistent", "wrong_change", differences, changed)
    if not has_target:
        return result("not_applicable", "slot_absent", differences, changed)
    return result("consistent", "match", differences, changed)


def _expected_slot(slot: str, o: Any, o_step: dict, perturbation: dict) -> tuple[bool, Any]:
    """The expected perturbed value of a perturbed slot.

    Args:
        slot: The perturbed slot name (`ticker`, `cik` or `fy`).
        o: The slot's normalised original value.
        o_step: The original step's normalised slots.
        perturbation: The (validated) perturbation.

    Returns:
        `(accept_any, expected)`: `accept_any` is True for a cik on a swapped step when the
        perturbation names no ciks (any value is accepted; the step is a target).
    """
    if slot == "fy":
        delta = perturbation["to"] - perturbation["from"]
        is_int = isinstance(o, int) and not isinstance(o, bool)
        return False, (o + delta if is_int else o)
    frm = str(perturbation["from"]).upper()
    to = str(perturbation["to"]).upper()
    if slot == "ticker":
        return False, (to if o == frm else o)
    # cik
    if "from_cik" in perturbation:
        from_cik = _norm_cik(perturbation["from_cik"])
        return False, (_norm_cik(perturbation["to_cik"]) if o == from_cik else o)
    if o_step["ticker"] == frm:
        return True, None
    return False, o


def check_answer_perturbation(
    original_values: list[float],
    perturbed_values: list[float],
    lookups_equal: bool,
    rel_tol: float = 1e-9,
) -> dict:
    """Check that the answers' values differ unless the underlying lookups are equal.

    Args:
        original_values: The original answer's values.
        perturbed_values: The perturbed answer's values.
        lookups_equal: Whether the underlying lookups are equal.
        rel_tol: Relative tolerance under which two values are equal.

    Returns:
        A verdict dict (`check`, `verdict`, `reason`, `original`, `perturbed`,
        `lookups_equal`, `tolerance`).
    """
    def result(verdict: str, reason: str) -> dict:
        return {"check": "answer_perturbation", "verdict": verdict, "reason": reason,
                "original": list(original_values), "perturbed": list(perturbed_values),
                "lookups_equal": lookups_equal, "tolerance": rel_tol}

    if not original_values or not perturbed_values:
        return result("not_applicable", "missing_value")
    equal = len(original_values) == len(perturbed_values) and all(
        abs(a - b) <= rel_tol * max(abs(a), abs(b))
        for a, b in zip(original_values, perturbed_values, strict=True))
    if lookups_equal:
        return result("consistent", "match") if equal else result("inconsistent", "changed")
    return result("inconsistent", "unchanged") if equal else result("consistent", "match")


def _is_alnum(ch: str) -> bool:
    """Whether a character is an ASCII letter or digit."""
    return ch.isascii() and ch.isalnum()


def _scan_forms(question: str, forms: list[tuple[str, bool]]) -> list[tuple[int, int, bool]]:
    """Finds non-overlapping form matches, left to right, longest form first.

    Args:
        question: The text to scan.
        forms: `(form, is_ticker)` pairs; a ticker matches case-sensitively, a name
            case-insensitively.

    Returns:
        `(start, end, is_ticker)` for each match, in text order.
    """
    lowered = question.lower()
    ordered = sorted((f for f in forms if f[0]), key=lambda f: -len(f[0]))
    matches: list[tuple[int, int, bool]] = []
    i = 0
    n = len(question)
    while i < n:
        if i > 0 and _is_alnum(question[i - 1]):
            i += 1
            continue
        hit = None
        for form, is_ticker in ordered:
            end = i + len(form)
            if end > n or (end < n and _is_alnum(question[end])):
                continue
            text = question[i:end] if is_ticker else lowered[i:end]
            if text == (form if is_ticker else form.lower()):
                hit = (i, end, is_ticker)
                break
        if hit is None:
            i += 1
            continue
        matches.append(hit)
        i = hit[1]
    return matches


def _forms(ticker: str, names: dict[str, list[str]]) -> list[tuple[str, bool]]:
    """A ticker's surface forms: the ticker itself, then its names."""
    return [(ticker, True)] + [(name, False) for name in names.get(ticker, [])]


def perturb_ticker(
    question: str, from_ticker: str, to_ticker: str, names: dict[str, list[str]]
) -> dict | None:
    """Swap a ticker or company surface form in a question, deterministically.

    Args:
        question: The question text.
        from_ticker: The ticker whose forms are replaced.
        to_ticker: The ticker to put in their place.
        names: Ticker to company surface forms; the first form is the replacement name.

    Returns:
        `{"question", "perturbation"}`, or None when the swap does not apply.

    Raises:
        ValueError: `from_ticker` equals `to_ticker`.
    """
    frm, to = from_ticker.upper(), to_ticker.upper()
    if frm == to:
        raise ValueError("from_ticker equals to_ticker")
    if _scan_forms(question, _forms(to, names)):
        return None
    matches = _scan_forms(question, _forms(frm, names))
    if not matches:
        return None
    to_names = names.get(to) or []
    name_repl = to_names[0] if to_names else to
    parts: list[str] = []
    last = 0
    for start, end, is_ticker in matches:
        parts.append(question[last:start])
        parts.append(to if is_ticker else name_repl)
        last = end
    parts.append(question[last:])
    return {"question": "".join(parts),
            "perturbation": {"kind": "ticker", "from": frm, "to": to}}


def perturb_year(question: str, from_year: int, to_year: int) -> dict | None:
    """Shift every year mention in a question by `to_year - from_year`.

    Args:
        question: The question text.
        from_year: A year the question must mention.
        to_year: What `from_year` becomes.

    Returns:
        `{"question", "perturbation"}`, or None when the shift does not apply.

    Raises:
        ValueError: `from_year` equals `to_year`.
    """
    if from_year == to_year:
        raise ValueError("from_year equals to_year")
    delta = to_year - from_year
    mentions = list(_YEAR_MENTION.finditer(question))
    years = [2000 + int(m["yy"]) if m["fy"] else int(m["y4"]) for m in mentions]
    if from_year not in years or any(not 2000 <= y + delta <= 2099 for y in years):
        return None
    parts: list[str] = []
    last = 0
    for m, year in zip(mentions, years, strict=True):
        shifted = year + delta
        parts.append(question[last:m.start()])
        parts.append(f"{m['fy']}{shifted % 100:02d}" if m["fy"] else str(shifted))
        last = m.end()
    parts.append(question[last:])
    return {"question": "".join(parts),
            "perturbation": {"kind": "year", "from": from_year, "to": to_year}}
