"""Set B tooling: authoring parse, validation, SQL-only answer-key resolution, build, hash.

Set B is the hand-written held-out set of external test v1. The user
writes it as YAML; this module checks its structure and scope, confirms every written
amount against our fact store through the ``KeyFactStore`` seam (SQL only: no pipeline,
no LLM), and turns the verified cases into an ``EvalCase`` suite with canonical bytes and
a SHA-256 seal. No message, hint or report produced here contains a question, a note or
an expected amount; only :func:`build_suite` copies the question, into the suite itself.

Spec: docs/specs/set-b-tooling.md (requirements 1-10).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from decimal import Decimal, InvalidOperation

import yaml

from src.eval.set_b.base import KeyFactStore

# The 12 suite filers (pre-registration v0 section 3; spec decisions 2026-10-22).
SUITE_TICKERS = frozenset({"AAPL", "AMD", "AMZN", "AVGO", "GOOGL", "META", "MSFT", "MU",
                           "NVDA", "ORCL", "TSLA", "TSM"})

# Every (filer, FY2026) pair in the prospective set P (pre-registration v0 section 3):
# set B must not touch them, not even as a compute input.
P_SET = frozenset({("AAPL", 2026)})

# A case's own fiscal years, and a compute input's (a FY2024 YoY reads FY2023).
CASE_FY_RANGE = (2024, 2026)
INPUT_FY_RANGE = (2022, 2026)

KINDS = ("lookup", "dimensional", "compute", "breakdown")
FORMULAS = ("yoy", "ratio", "difference", "sum")
UNITS = frozenset({"USD", "TWD", "pure", "shares", "USD/shares"})
SCALES: dict[str | None, int] = {None: 1, "units": 1, "thousands": 10**3,
                                 "millions": 10**6, "billions": 10**9}

# The suite's target case count, and the most cases one filer may hold (fraction).
COUNT_RANGE = (40, 60)
MAX_TICKER_SHARE = 0.40

# The fixed inputs each formula takes (sum takes any >= 2 distinct names).
_FORMULA_INPUTS = {"yoy": ("current", "prior"), "ratio": ("numerator", "denominator"),
                   "difference": ("a", "b")}

# Status order for the report's counts; any other status follows, sorted.
STATUSES = ("verified", "ambiguous", "not_found", "arith_mismatch")

# Relative floor of the "agree" rule (requirement 3) and of the arithmetic check (7).
_AGREE_REL = 1e-9
_ARITH_REL = 1e-3

_NUMBER_RE = re.compile(r"(?P<int>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<frac>\d+))?")
_WHITESPACE_RE = re.compile(r"\s+")

SUITE_NAME = "heldout_b"
SUITE_VERSION = "v1"
CASE_SUITE = "heldout_b_v1"
CASE_ORIGIN = "handwritten:set_b"
CASE_ID_PREFIX = "b-"
_REL_TOL = {"lookup": 1e-4, "dimensional": 1e-4, "compute": 1e-2, "breakdown": 1e-4}


def load_authoring(text: str) -> list[dict]:
    """Parses the YAML authoring file into its case dicts.

    Args:
        text: The YAML text.

    Returns:
        The ``cases`` list.

    Raises:
        ValueError: If ``cases`` is missing or not a list.
    """
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict) or "cases" not in doc:
        raise ValueError("the authoring file has no top-level `cases:` list")
    cases = doc["cases"]
    if not isinstance(cases, list):
        raise ValueError("`cases` must be a list")
    return cases


def parse_amount(raw: object, scale: str | None = None) -> tuple[float, float]:
    """Turns an amount as written into its value and half of its last written digit.

    Args:
        raw: A number, or a string such as ``"391,035"``, ``"(1.25)"`` or ``"21.5%"``.
        scale: ``units``, ``thousands``, ``millions`` or ``billions``; None means units.

    Returns:
        ``(value, half_unit)``.

    Raises:
        ValueError: If the amount or the scale is not accepted.
    """
    if scale not in SCALES:
        raise ValueError(f"unknown scale {scale!r}")
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise ValueError("an amount must be a number or a string")
    if isinstance(raw, float) and not math.isfinite(raw):
        raise ValueError("an amount must be finite")
    text = _WHITESPACE_RE.sub("", str(raw))

    negative = False
    percent = False
    if text.endswith("%"):
        percent, text = True, text[:-1]
    if len(text) >= 2 and text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1]
    if not percent and text.endswith("%"):
        percent, text = True, text[:-1]
    if text.startswith("-"):
        if negative:
            raise ValueError("an amount has two minus signs")
        negative, text = True, text[1:]
    if text.startswith("$"):
        text = text[1:]
        if text.startswith("-") and not negative:
            negative, text = True, text[1:]

    match = _NUMBER_RE.fullmatch(text)
    if match is None:
        raise ValueError("not an amount")
    if percent and scale is not None:
        raise ValueError("a percentage takes no scale")
    frac = match.group("frac") or ""
    try:
        number = Decimal(match.group("int").replace(",", "") + ("." + frac if frac else ""))
    except InvalidOperation as exc:  # unreachable after the regex; kept fail-loud
        raise ValueError("not an amount") from exc
    if negative:
        number = -number
    digits = len(frac)
    if percent:
        return float(number / 100), 0.5 * 10.0**-digits / 100
    mult = SCALES[scale]
    return float(number * mult), 0.5 * mult * 10.0**-digits


def _agrees(value: float, amount: tuple[float, float]) -> bool:
    """Requirement 3: a fact value agrees with a written amount."""
    written, half_unit = amount
    return abs(value - written) <= max(half_unit, _AGREE_REL * abs(written))


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_amount_type(value: object) -> bool:
    return isinstance(value, (int, float, str)) and not isinstance(value, bool)


class _CaseErrors:
    """Collects one case's error messages, each prefixed with the case's id."""

    def __init__(self, prefix: str, sink: list[str]) -> None:
        self.prefix = prefix
        self.sink = sink

    def add(self, field: str, message: str) -> None:
        self.sink.append(f"{self.prefix}: {field}: {message}")

    def str_field(self, obj: dict, field: str, *, required: bool, label: str = "") -> bool:
        """Checks an optional or required string field; True when it is a usable str."""
        name = label or field
        if field not in obj or obj[field] is None:
            if required:
                self.add(name, "missing")
            return False
        if not isinstance(obj[field], str) or not obj[field].strip():
            self.add(name, "must be a non-empty string")
            return False
        return True

    def amount(self, obj: dict, field: str, scale_field: str, *, label: str = "",
               scale_label: str = "") -> None:
        """Checks an amount and its optional scale; never echoes the amount."""
        name = label or field
        sname = scale_label or scale_field
        scale = obj.get(scale_field)
        if scale is not None and scale not in SCALES:
            self.add(sname, f"unknown scale {scale!r} (use one of units, thousands, "
                            "millions, billions)")
            return
        if field not in obj or obj[field] is None:
            self.add(name, "missing")
            return
        if not _is_amount_type(obj[field]):
            self.add(name, "must be a number or a string")
            return
        try:
            parse_amount(obj[field], scale)
        except ValueError as exc:
            reason = ("a percentage takes no scale" if "percentage" in str(exc)
                      else "not an accepted amount")
            self.add(name, reason)

    def unit(self, obj: dict, field: str = "unit", *, label: str = "") -> None:
        name = label or field
        if field not in obj or obj[field] is None:
            self.add(name, "missing")
        elif not isinstance(obj[field], str):
            self.add(name, "must be a string")
        elif obj[field] not in UNITS:
            self.add(name, f"unknown unit {obj[field]!r} (use one of "
                           f"{', '.join(sorted(UNITS))})")


def _validate_case(case: dict, errs: _CaseErrors) -> None:
    """Checks one case's fields; ``case`` is a dict and its id prefix is set."""
    errs.str_field(case, "question", required=True)
    if "note" in case and case["note"] is not None and not isinstance(case["note"], str):
        errs.add("note", "must be a string")

    ticker = case.get("ticker")
    ticker_ok = False
    if ticker is None:
        errs.add("ticker", "missing")
    elif not isinstance(ticker, str):
        errs.add("ticker", "must be a string")
    elif ticker not in SUITE_TICKERS:
        errs.add("ticker", f"{ticker!r} is not one of the 12 suite filers")
    else:
        ticker_ok = True

    fy = case.get("fy")
    fy_ok = False
    if fy is None:
        errs.add("fy", "missing")
    elif not _is_int(fy):
        errs.add("fy", "must be an integer")
    elif not CASE_FY_RANGE[0] <= fy <= CASE_FY_RANGE[1]:
        errs.add("fy", f"{fy} is outside {CASE_FY_RANGE[0]}-{CASE_FY_RANGE[1]}")
    else:
        fy_ok = True
    if ticker_ok and fy_ok and (ticker, fy) in P_SET:
        errs.add("fy", f"({ticker}, {fy}) is in the prospective set P")

    kind = case.get("kind")
    if kind is None:
        errs.add("kind", "missing")
        return
    if not isinstance(kind, str) or kind not in KINDS:
        errs.add("kind", f"unknown kind {kind!r} (use one of {', '.join(KINDS)})")
        return

    if kind in ("lookup", "dimensional"):
        errs.amount(case, "expected", "scale")
        errs.unit(case)
        errs.str_field(case, "concept", required=False)
        errs.str_field(case, "member", required=kind == "dimensional")
    elif kind == "compute":
        _validate_compute(case, errs, ticker if ticker_ok else None)
    else:
        _validate_breakdown(case, errs)


def _validate_compute(case: dict, errs: _CaseErrors, ticker: str | None) -> None:
    formula = case.get("formula")
    formula_ok = False
    if formula is None:
        errs.add("formula", "missing")
    elif not isinstance(formula, str) or formula not in FORMULAS:
        errs.add("formula", f"unknown formula {formula!r} (use one of "
                            f"{', '.join(FORMULAS)})")
    else:
        formula_ok = True
    errs.amount(case, "expected", "scale")

    inputs = case.get("inputs")
    if inputs is None:
        errs.add("inputs", "missing")
        return
    if not isinstance(inputs, list):
        errs.add("inputs", "must be a list")
        return
    names: list[str] = []
    for i, inp in enumerate(inputs):
        label = f"inputs[{i}]"
        if not isinstance(inp, dict):
            errs.add(label, "must be a mapping")
            continue
        if errs.str_field(inp, "name", required=True, label=f"{label}.name"):
            names.append(inp["name"])
        errs.amount(inp, "expected", "scale", label=f"{label}.expected",
                    scale_label=f"{label}.scale")
        errs.unit(inp, label=f"{label}.unit")
        errs.str_field(inp, "concept", required=False, label=f"{label}.concept")
        errs.str_field(inp, "member", required=False, label=f"{label}.member")
        if "fy" in inp and inp["fy"] is not None:
            in_fy = inp["fy"]
            if not _is_int(in_fy):
                errs.add(f"{label}.fy", "must be an integer")
            elif not INPUT_FY_RANGE[0] <= in_fy <= INPUT_FY_RANGE[1]:
                errs.add(f"{label}.fy", f"{in_fy} is outside "
                                        f"{INPUT_FY_RANGE[0]}-{INPUT_FY_RANGE[1]}")
            elif ticker is not None and (ticker, in_fy) in P_SET:
                errs.add(f"{label}.fy", f"({ticker}, {in_fy}) is in the prospective set P")

    if len(set(names)) != len(names):
        errs.add("inputs", "input names must be distinct")
    if not formula_ok:
        return
    if formula == "sum":
        if len(inputs) < 2:
            errs.add("inputs", "a sum needs at least 2 inputs")
        return
    want = _FORMULA_INPUTS[str(formula)]
    if len(inputs) != len(want) or sorted(names) != sorted(want):
        errs.add("inputs", f"a {formula} needs exactly the inputs {' and '.join(want)}")


def _validate_breakdown(case: dict, errs: _CaseErrors) -> None:
    errs.unit(case)
    errs.str_field(case, "concept", required=False)
    rows = case.get("rows")
    if rows is None:
        errs.add("rows", "missing")
        return
    if not isinstance(rows, list):
        errs.add("rows", "must be a list")
        return
    if len(rows) < 2:
        errs.add("rows", "a breakdown needs at least 2 rows")
    for i, row in enumerate(rows):
        label = f"rows[{i}]"
        if not isinstance(row, dict):
            errs.add(label, "must be a mapping")
            continue
        errs.str_field(row, "member", required=True, label=f"{label}.member")
        errs.str_field(row, "concept", required=False, label=f"{label}.concept")
        errs.amount(row, "expected", "scale", label=f"{label}.expected",
                    scale_label=f"{label}.scale")


def validate(cases: list[dict]) -> dict:
    """Checks structure and scope; never echoes a question or note.

    Args:
        cases: The authoring cases.

    Returns:
        ``{"errors": [...], "warnings": [...], "mix": {...}}``.
    """
    errors: list[str] = []
    ids = Counter(c.get("id") for c in cases
                  if isinstance(c, dict) and isinstance(c.get("id"), str))
    duplicate_reported: set[str] = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            errors.append(f"#{index}: case: must be a mapping")
            continue
        cid = case.get("id")
        if isinstance(cid, str) and cid.strip():
            errs = _CaseErrors(cid, errors)
            if ids[cid] > 1 and cid not in duplicate_reported:
                duplicate_reported.add(cid)
                errs.add("id", f"duplicate id ({ids[cid]} cases)")
        else:
            errs = _CaseErrors(f"#{index}", errors)
            errs.add("id", "missing" if cid is None else "must be a non-empty string")
        _validate_case(case, errs)

    dicts = [c for c in cases if isinstance(c, dict)]
    kind_mix = Counter(c["kind"] for c in dicts if isinstance(c.get("kind"), str))
    ticker_mix = Counter(c["ticker"] for c in dicts if isinstance(c.get("ticker"), str))
    fy_mix = Counter(c["fy"] for c in dicts if _is_int(c.get("fy")))
    total = len(cases)
    mix = {"kind": dict(sorted(kind_mix.items())), "ticker": dict(sorted(ticker_mix.items())),
           "fy": dict(sorted(fy_mix.items())), "total": total}

    warnings: list[str] = []
    if not COUNT_RANGE[0] <= total <= COUNT_RANGE[1]:
        warnings.append(f"case count {total} is outside the target "
                        f"{COUNT_RANGE[0]}-{COUNT_RANGE[1]}")
    for kind in KINDS:
        if kind_mix.get(kind, 0) == 0:
            warnings.append(f"no {kind} cases")
    for ticker, n in sorted(ticker_mix.items()):
        if total and n > MAX_TICKER_SHARE * total:
            warnings.append(f"{ticker} has {n} of {total} cases ({100 * n / total:.1f}%), "
                            f"above {MAX_TICKER_SHARE:.0%}")
    return {"errors": errors, "warnings": warnings, "mix": mix}


def _local_name(concept: str) -> str:
    return concept.rsplit(":", 1)[-1]


def _class_key(cand: dict) -> tuple:
    return (cand["concept"], cand["unit"], cand["period_start"], cand["period_end"],
            cand["member_label"])


def _matches(candidates: list[dict], amount: tuple[float, float], unit: str,
             dimensional: bool, concept: str | None, member: str | None) -> list[dict]:
    out = []
    concept_lc = concept.lower() if concept is not None else None
    member_lc = member.lower() if member is not None else None
    for cand in candidates:
        if bool(cand["dimensional"]) != dimensional or cand["unit"] != unit:
            continue
        if not _agrees(float(cand["value"]), amount):
            continue
        if concept_lc is not None and concept != cand.get("concept_family") \
                and concept_lc not in _local_name(cand["concept"]).lower():
            continue
        if member_lc is not None:
            label = cand.get("member_label")
            if label is None or member_lc not in label.lower():
                continue
        out.append(cand)
    return out


def find_classes(
    candidates: list[dict],
    amount: tuple[float, float],
    unit: str,
    dimensional: bool,
    concept: str | None = None,
    member: str | None = None,
) -> list[list[int]]:
    """Finds the facts that carry a written amount, grouped into equivalence classes.

    Args:
        candidates: The filer-year's candidate facts.
        amount: ``(value, half_unit)`` from :func:`parse_amount`.
        unit: The required unit.
        dimensional: Whether to match dimensional or flat facts.
        concept: Optional concept-family or local-name hint.
        member: Optional member-label hint.

    Returns:
        Sorted classes of sorted fact ids.
    """
    groups: dict[tuple, set[int]] = {}
    for cand in _matches(candidates, amount, unit, dimensional, concept, member):
        groups.setdefault(_class_key(cand), set()).add(int(cand["fact_id"]))
    return sorted((sorted(ids) for ids in groups.values()), key=lambda c: c[0])


def recompute(formula: str, values: dict[str, float]) -> float | None:
    """Recomputes a compute case from its input values.

    Args:
        formula: ``yoy``, ``ratio``, ``difference`` or ``sum``.
        values: Input name to value.

    Returns:
        The value, or None for a zero denominator.

    Raises:
        ValueError: If the formula is unknown.
    """
    if formula == "yoy":
        prior = values["prior"]
        return None if prior == 0 else (values["current"] - prior) / abs(prior)
    if formula == "ratio":
        den = values["denominator"]
        return None if den == 0 else values["numerator"] / den
    if formula == "difference":
        return values["a"] - values["b"]
    if formula == "sum":
        return float(sum(values.values()))
    raise ValueError(f"unknown formula {formula!r}")


def _status(classes: list[list[int]]) -> str:
    if len(classes) == 1:
        return "verified"
    return "ambiguous" if classes else "not_found"


class _FactCache:
    """Reads each (ticker, fiscal year) from the store at most once per kind of read.

    A breakdown row reads ``breakdown_facts_for`` (one filing's split); everything else
    reads ``facts_for`` (docs/specs/set-b-dimensional-s1.md, requirement 3).
    """

    def __init__(self, store: KeyFactStore) -> None:
        self._store = store
        self._facts: dict[tuple[str, int, bool], list[dict]] = {}

    def get(self, ticker: str, fiscal_year: int, breakdown: bool = False) -> list[dict]:
        key = (ticker, int(fiscal_year), breakdown)
        if key not in self._facts:
            read = self._store.breakdown_facts_for if breakdown else self._store.facts_for
            self._facts[key] = read(ticker, int(fiscal_year))
        return self._facts[key]


def _hint(classes: list[list[int]], candidates: list[dict],
          name: str | None = None) -> list[dict]:
    """Each class's concept local name and member label; no values, no question."""
    by_id = {int(c["fact_id"]): c for c in candidates}
    out = []
    for cls in classes:
        cand = by_id[cls[0]]
        entry: dict = {}
        if name is not None:
            entry["input"] = name
        entry["concept"] = _local_name(cand["concept"])
        entry["member_label"] = cand.get("member_label")
        out.append(entry)
    return out


def _resolve_amount(cache: _FactCache, ticker: str, fy: int, amount: tuple[float, float],
                    unit: str, dimensional: bool, concept: str | None,
                    member: str | None,
                    breakdown: bool = False) -> tuple[str, list[list[int]], list[dict]]:
    candidates = cache.get(ticker, fy, breakdown)
    classes = find_classes(candidates, amount, unit, dimensional, concept, member)
    return _status(classes), classes, candidates


def resolve_case(case: dict, store: KeyFactStore) -> dict:
    """Confirms one case's answer key against the fact store, by SQL only.

    Args:
        case: An authoring case (already validated).
        store: The fact store.

    Returns:
        ``{"id", "status", "inputs", "classes", "candidates_hint"}``.
    """
    cache = _FactCache(store)
    kind = case["kind"]
    ticker = case["ticker"]
    fy = int(case["fy"])
    out: dict = {"id": case["id"], "status": "", "inputs": [], "classes": [],
                 "candidates_hint": []}

    if kind in ("lookup", "dimensional"):
        amount = parse_amount(case["expected"], case.get("scale"))
        status, classes, cands = _resolve_amount(
            cache, ticker, fy, amount, case["unit"], kind == "dimensional",
            case.get("concept"), case.get("member") if kind == "dimensional" else None)
        out["status"] = status
        out["classes"] = classes
        if status == "ambiguous":
            out["candidates_hint"] = _hint(classes, cands)
        return out

    if kind == "compute":
        parts = [(inp["name"], inp, int(inp.get("fy") or fy), inp.get("unit"),
                  inp.get("concept"), inp.get("member")) for inp in case["inputs"]]
    elif kind == "breakdown":
        parts = [(row["member"], row, fy, case["unit"], row.get("concept", case.get("concept")),
                  row["member"]) for row in case["rows"]]
    else:
        raise ValueError(f"unknown kind {kind!r}")

    values: dict[str, float] = {}
    for name, item, part_fy, unit, concept, member in parts:
        amount = parse_amount(item["expected"], item.get("scale"))
        values[name] = amount[0]
        status, classes, cands = _resolve_amount(cache, ticker, part_fy, amount, unit,
                                                 member is not None, concept, member,
                                                 breakdown=kind == "breakdown")
        out["inputs"].append({"name": name, "status": status, "classes": classes})
        out["classes"].append(classes[0] if status == "verified" else [])
        if status == "ambiguous":
            out["candidates_hint"].extend(_hint(classes, cands, name))

    bad = [i["status"] for i in out["inputs"] if i["status"] != "verified"]
    if bad:
        out["status"] = bad[0]
    elif kind == "compute":
        expected, half_unit = parse_amount(case["expected"], case.get("scale"))
        r = recompute(case["formula"], values)
        ok = r is not None and abs(r - expected) <= max(half_unit, _ARITH_REL * abs(expected))
        out["status"] = "verified" if ok else "arith_mismatch"
    else:
        out["status"] = "verified"
    return out


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}es"


def render_check_report(resolutions: list[dict]) -> str:
    """Renders the answer-key check without any question, note or amount.

    Only the id, status, class counts, input names and candidate hints (concept local
    names and member labels) are read from each resolution.

    Args:
        resolutions: The results of :func:`resolve_case`.

    Returns:
        The report text.
    """
    lines = ["set B answer-key check", ""]
    width = max([len(str(r["id"])) for r in resolutions] + [2])
    for r in resolutions:
        status = r["status"]
        lines.append(f"{str(r['id']):<{width}}  {status:<14}  classes={len(r['classes'])}")
        if status == "verified":
            continue
        for inp in r.get("inputs") or []:
            lines.append(f"    input {inp['name']}: {inp['status']}, "
                         f"{_plural(len(inp['classes']), 'class')}")
        for h in r.get("candidates_hint") or []:
            where = f"input {h['input']}: " if h.get("input") is not None else ""
            member = f" [{h['member_label']}]" if h.get("member_label") else ""
            lines.append(f"    candidate {where}{h['concept']}{member}")
    counts = Counter(r["status"] for r in resolutions)
    order = [s for s in STATUSES if s in counts] + sorted(set(counts) - set(STATUSES))
    lines.append("")
    lines.append(f"total: {len(resolutions)}")
    lines.extend(f"{status}: {counts[status]}" for status in order)
    return "\n".join(lines) + "\n"


def _common(case: dict) -> dict:
    return {"id": CASE_ID_PREFIX + case["id"], "question": case["question"],
            "kind": case["kind"], "ticker": case["ticker"], "fy": int(case["fy"]),
            "suite": CASE_SUITE, "origin": CASE_ORIGIN, "tags": ["set-b", case["kind"]]}


def _eval_case(case: dict, resolution: dict, labels: dict[int, str]) -> dict:
    kind = case["kind"]
    classes = [list(c) for c in resolution["classes"]]
    out = _common(case)
    if kind in ("lookup", "dimensional"):
        out["expected_value"] = parse_amount(case["expected"], case.get("scale"))[0]
        out["unit"] = case["unit"]
        out["expected_fact_ids"] = [classes[0][0]]
        out["fact_id_classes"] = [classes[0]]
    elif kind == "compute":
        value = parse_amount(case["expected"], case.get("scale"))[0]
        out["expected_value"] = value
        out["alt_values"] = [value * 100] if "%" in str(case["expected"]) else []
        out["expected_fact_ids"] = [c[0] for c in classes]
        out["fact_id_classes"] = classes
        out["unit"] = case["inputs"][0]["unit"]
    elif kind == "breakdown":
        rows = []
        for row, cls in zip(case["rows"], classes, strict=True):
            if cls[0] not in labels:
                raise ValueError(f"{case['id']}: no member label for fact {cls[0]}")
            rows.append({"entity": labels[cls[0]],
                         "value": parse_amount(row["expected"], row.get("scale"))[0]})
        out["expected_rows"] = rows
        out["order_sensitive"] = False
        out["expected_fact_ids"] = [c[0] for c in classes]
        out["fact_id_classes"] = classes
    else:
        raise ValueError(f"{case['id']}: unknown kind {kind!r}")
    out["rel_tol"] = _REL_TOL[kind]
    return out


def build_suite(
    cases: list[dict],
    resolutions: list[dict],
    labels: dict[int, str],
    allow_unverified: bool = False,
) -> dict:
    """Builds the EvalCase suite from verified cases.

    Args:
        cases: The authoring cases.
        resolutions: Their :func:`resolve_case` results.
        labels: Fact id to member label, for breakdown rows.
        allow_unverified: Leave unverified cases out instead of raising.

    Returns:
        The suite dict.

    Raises:
        ValueError: If a case is unverified and ``allow_unverified`` is false, or a
            breakdown row's fact has no label.
    """
    by_id = {r["id"]: r for r in resolutions}
    unverified = sorted(c["id"] for c in cases
                        if by_id.get(c["id"], {}).get("status") != "verified")
    if unverified and not allow_unverified:
        raise ValueError(f"unverified case(s): {', '.join(unverified)}")
    skip = set(unverified)
    built = [_eval_case(c, by_id[c["id"]], labels) for c in cases if c["id"] not in skip]
    built.sort(key=lambda c: c["id"])
    suite: dict = {"name": SUITE_NAME, "version": SUITE_VERSION, "n_cases": len(built),
                   "cases": built}
    if allow_unverified:
        suite["excluded"] = unverified
    return suite


def canonical_bytes(suite: dict) -> bytes:
    """The suite's canonical JSON bytes, which are what gets hashed.

    Args:
        suite: The suite dict.

    Returns:
        UTF-8 bytes.
    """
    return (json.dumps(suite, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode(
        "utf-8")


def sha256_hex(data: bytes) -> str:
    """The lowercase SHA-256 hex digest.

    Args:
        data: The bytes to hash.

    Returns:
        The digest.
    """
    return hashlib.sha256(data).hexdigest()
