"""The SEC companyfacts check: match rule, recomputation, case verdicts and summary.

Every number the suite expects is checked against the SEC's own extraction of the same
XBRL (``data.sec.gov`` companyfacts). Facts are matched on taxonomy concept, unit, period
and accession; derived values are recomputed from the SEC values with the formulas of
``scripts/ddl/mv/34_mv_derived_metrics.sql``. Dimensional facts are out of companyfacts'
reach and go to a seeded hand check instead.

Spec: docs/specs/sec-companyfacts-check.md (requirements 3-9).
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Callable
from typing import TypedDict

from src.eval.sec_check.base import CompanyFactsSource, FactRecord, FactStore
from src.logging_config import get_logger

logger = get_logger(__name__)

# Relative tolerance of the "agree" rule (requirement 4).
_REL_TOL = 1e-9

# Units that are one alias group in companyfacts (requirement 4).
_PURE_UNITS = frozenset({"pure", "Rate"})

# Case kinds that companyfacts cannot cover (requirement 7).
_HAND_CHECK_KINDS = frozenset({"dimensional", "breakdown"})

# Per-origin hand-check sample size, by kind (requirement 8).
_HAND_CHECK_CAPS = {"dimensional": 6, "breakdown": 2}

# Fact statuses that leave a value unverifiable (requirement 7, status 2).
_UNVERIFIABLE_FACT_STATUSES = frozenset(
    {"extension_concept", "concept_absent", "absent_for_accession"})

# Fact statuses for which restatements are listed (requirement 5, statuses 4-7).
_RESTATEMENT_STATUSES = frozenset(
    {"match", "value_mismatch", "match_other_accession", "absent_for_accession"})


class FactCheck(TypedDict):
    """The result of matching one fact against companyfacts (requirement 5).

    Attributes:
        fact_id: Our fact's id.
        status: One of ``dimensional``, ``extension_concept``, ``concept_absent``,
            ``match``, ``value_mismatch``, ``match_other_accession``,
            ``absent_for_accession``.
        sec_value: The SEC value the status refers to, if any.
        sign_flip: True when the SEC value for our accession is our value negated.
        restatements: ``{"accession_no", "value"}`` of other accessions that report a
            different value for the same concept, unit and period.
    """

    fact_id: int
    status: str
    sec_value: float | None
    sign_flip: bool
    restatements: list[dict]


class CaseCheck(TypedDict):
    """The result of checking one suite case (requirement 7).

    Attributes:
        case_id: The suite case id.
        kind: The case kind.
        status: ``match``, ``mismatch``, ``unverifiable``, ``hand_check`` or
            ``template_unknown``.
        reason: Why the case is unverifiable or a hand check; None otherwise.
        facts: One check per distinct fact the case used, in first-use order.
        items: ``{"label", "expected", "sec_value", "agree"}``, one per checked value.
    """

    case_id: str
    kind: str
    status: str
    reason: str | None
    facts: list[FactCheck]
    items: list[dict]


def _agree(a: float, b: float) -> bool:
    """Whether two values agree under requirement 4's relative tolerance."""
    return abs(a - b) <= _REL_TOL * max(abs(a), abs(b), 1.0)


def _ratio(num: float, den: float) -> float | None:
    """``num / den``, or None for a zero denominator."""
    return None if den == 0 else num / den


def taxonomy_key(namespace: str) -> str | None:
    """Maps a concept namespace URI to its companyfacts taxonomy key.

    Args:
        namespace: The namespace URI.

    Returns:
        ``us-gaap``, ``ifrs-full``, ``srt`` or ``dei``; None for a filer extension.
    """
    if "fasb.org/us-gaap" in namespace:
        return "us-gaap"
    if "xbrl.ifrs.org" in namespace and "ifrs-full" in namespace:
        return "ifrs-full"
    if "fasb.org/srt" in namespace:
        return "srt"
    if "xbrl.sec.gov/dei" in namespace:
        return "dei"
    return None


def _period_entries(fact: FactRecord, company_facts: dict, taxonomy: str) -> list[dict]:
    """The companyfacts entries with the fact's concept, unit and period, in input order."""
    _, _, local_name = fact["concept"].rpartition(":")
    concept = (company_facts.get("facts") or {}).get(taxonomy, {}).get(local_name)
    if not concept:
        return []
    unit = fact["unit"]
    accepted = _PURE_UNITS if unit in _PURE_UNITS else frozenset({unit})
    start, end = fact["period_start"], fact["period_end"]
    out = []
    for unit_key, entries in (concept.get("units") or {}).items():
        if unit_key not in accepted:
            continue
        for e in entries:
            if e.get("end") != end:
                continue
            if start is None:
                if e.get("start") is None:
                    out.append(e)
            elif e.get("start") == start:
                out.append(e)
    return out


def check_fact(fact: FactRecord, company_facts: dict) -> FactCheck:
    """Matches one of our facts against the filer's companyfacts.

    Args:
        fact: Our fact.
        company_facts: The filer's companyfacts document.

    Returns:
        The match status, the SEC value and any restatements.
    """
    result = FactCheck(fact_id=fact["fact_id"], status="", sec_value=None, sign_flip=False,
                       restatements=[])
    if fact["dimensional"]:
        result["status"] = "dimensional"
        return result
    namespace, _, _ = fact["concept"].rpartition(":")
    taxonomy = taxonomy_key(namespace)
    if taxonomy is None:
        result["status"] = "extension_concept"
        return result
    entries = _period_entries(fact, company_facts, taxonomy)
    if not entries:
        result["status"] = "concept_absent"
        return result

    value = float(fact["value"])
    ours = [e for e in entries if e.get("accn") == fact["accession_no"]]
    others = [e for e in entries if e.get("accn") != fact["accession_no"]]
    ours_agreeing = [e for e in ours if _agree(float(e["val"]), value)]
    others_agreeing = [e for e in others if _agree(float(e["val"]), value)]
    if ours_agreeing:
        result["status"] = "match"
        result["sec_value"] = float(ours_agreeing[0]["val"])
    elif ours:
        first = float(ours[0]["val"])
        result["status"] = "value_mismatch"
        result["sec_value"] = first
        result["sign_flip"] = _agree(first, -value)
    elif others_agreeing:
        result["status"] = "match_other_accession"
        result["sec_value"] = float(others_agreeing[0]["val"])
    else:
        result["status"] = "absent_for_accession"
    if result["status"] in _RESTATEMENT_STATUSES:
        result["restatements"] = [
            {"accession_no": e["accn"], "value": float(e["val"])}
            for e in others if not _agree(float(e["val"]), value)
        ]
    return result


def _effective_tax_rate(v: dict[str, float]) -> float | None:
    if "effective_tax_rate" in v:
        return v["effective_tax_rate"]
    return _ratio(v["tax"], v["pretax_income"])


# metric -> (required roles, formula); the formulas of 34_mv_derived_metrics.sql.
_FORMULAS: dict[str, tuple[tuple[str, ...], Callable[[dict[str, float]], float | None]]] = {
    "GrossMarginPct": (("revenue", "cost_of_revenue"),
                       lambda v: _ratio(v["revenue"] - v["cost_of_revenue"], v["revenue"])),
    "OperatingMarginPct": (("revenue", "operating_income"),
                           lambda v: _ratio(v["operating_income"], v["revenue"])),
    "NetMarginPct": (("revenue", "net_income"),
                     lambda v: _ratio(v["net_income"], v["revenue"])),
    "RnDIntensityPct": (("revenue", "rnd"), lambda v: _ratio(v["rnd"], v["revenue"])),
    "CurrentRatio": (("current_assets", "current_liabilities"),
                     lambda v: _ratio(v["current_assets"], v["current_liabilities"])),
    "FreeCashFlow": (("operating_cash_flow", "capex"),
                     lambda v: v["operating_cash_flow"] - v["capex"]),
    "EBITDA": (("operating_income", "dep_amort"),
               lambda v: v["operating_income"] + v["dep_amort"]),
    "yoy": (("current", "prior"),
            lambda v: _ratio(v["current"] - v["prior"], abs(v["prior"]))),
}


def recompute_metric(metric: str, values: dict[str, float]) -> float | None:
    """Recomputes a derived value from its role values.

    Args:
        metric: A derived metric name, or ``yoy``.
        values: Role name to value.

    Returns:
        The value, or None for an unknown metric, a missing role or a zero denominator.
    """
    if metric == "EffectiveTaxRate":
        if "effective_tax_rate" not in values and not {"tax", "pretax_income"} <= set(values):
            return None
        return _effective_tax_rate(values)
    spec = _FORMULAS.get(metric)
    if spec is None:
        return None
    roles, formula = spec
    if any(r not in values for r in roles):
        return None
    return formula(values)


def _case_params(case_id: str) -> dict[str, str]:
    """The ``key=value`` parameters after the ``:`` of a case id, ``-``-separated.

    A ``-`` inside a value (a slug with a hash suffix) joins back onto that value.
    """
    _, sep, tail = case_id.partition(":")
    if not sep or not tail:
        return {}
    params: dict[str, str] = {}
    last: str | None = None
    for token in tail.split("-"):
        key, eq, value = token.partition("=")
        if eq:
            params[key] = value
            last = key
        elif last is not None:
            params[last] += "-" + token
    return params


def _first_fact_id(component: object) -> int | None:
    """A role's fact id: the component itself, or a list component's first element."""
    if isinstance(component, list):
        component = component[0] if component else None
    if isinstance(component, (int, str)):
        return int(component)
    return None


class _CaseState:
    """Per-case caches: our records, one companyfacts document per CIK, fact checks."""

    def __init__(self, store: FactStore, source: CompanyFactsSource) -> None:
        self.store = store
        self.source = source
        self.records: dict[int, FactRecord] = {}
        self.docs: dict[str, dict] = {}
        self.checks: dict[int, FactCheck] = {}  # insertion order = first-use order

    def load(self, fact_ids: list[int]) -> None:
        """Reads the records not yet loaded, in one store call."""
        missing = list(dict.fromkeys(i for i in fact_ids if i not in self.records))
        if missing:
            self.records.update(self.store.facts(missing))

    def sec_value(self, fact_id: int) -> float | None:
        """Checks a fact (once) and returns its SEC value; None for an unknown fact."""
        if fact_id not in self.checks:
            self.load([fact_id])
            record = self.records.get(fact_id)
            if record is None:
                logger.warning("sec_check: fact %s not found in the fact store", fact_id)
                return None
            doc: dict = {}
            namespace, _, _ = record["concept"].rpartition(":")
            if not record["dimensional"] and taxonomy_key(namespace) is not None:
                cik = str(record["cik"]).zfill(10)
                if cik not in self.docs:
                    self.docs[cik] = self.source.company_facts(cik)
                doc = self.docs[cik]
            self.checks[fact_id] = check_fact(record, doc)
        return self.checks[fact_id]["sec_value"]

    def yoy(self, fact_ids: list[int]) -> float | None:
        """YoY over two facts: current is the later ``period_end``."""
        self.load(fact_ids)
        values = {i: self.sec_value(i) for i in fact_ids}
        known = [i for i in fact_ids if i in self.records]
        if len(known) != 2:
            return None
        current, prior = sorted(known, key=lambda i: self.records[i]["period_end"],
                                reverse=True)
        cur, pri = values[current], values[prior]
        if cur is None or pri is None:
            return None
        return recompute_metric("yoy", {"current": cur, "prior": pri})

    def metric(self, ticker: str, fiscal_year: int, metric: str) -> float | None:
        """A derived metric recomputed over its components' SEC values."""
        components = self.store.metric_components(ticker, int(fiscal_year), metric)
        if not components:
            return None
        roles = {role: _first_fact_id(c) for role, c in components.items()}
        self.load([i for i in roles.values() if i is not None])
        values: dict[str, float] = {}
        missing = False
        for role, fact_id in roles.items():
            v = self.sec_value(fact_id) if fact_id is not None else None
            if v is None:
                missing = True
            else:
                values[role] = v
        return None if missing else recompute_metric(metric, values)


def _item(label: str, expected: float, sec_value: float | None) -> dict:
    return {"label": label, "expected": float(expected), "sec_value": sec_value,
            "agree": sec_value is not None and _agree(sec_value, float(expected))}


def _verdict(facts: list[FactCheck], items: list[dict]) -> tuple[str, str | None]:
    """The case status and reason for a checked case (requirement 7)."""
    if any(f["status"] == "value_mismatch" for f in facts) or any(
            it["sec_value"] is not None and not it["agree"] for it in items):
        return "mismatch", None
    for f in facts:
        if f["status"] in _UNVERIFIABLE_FACT_STATUSES:
            return "unverifiable", f["status"]
    if any(it["sec_value"] is None for it in items):
        return "unverifiable", "no_value"
    return "match", None


def check_case(case: dict, store: FactStore, source: CompanyFactsSource) -> CaseCheck:
    """Checks one suite case's expected value(s) against companyfacts.

    Args:
        case: A suite case dict.
        store: Our fact store.
        source: The companyfacts source.

    Returns:
        The case's verdict, its fact checks and one item per checked value.
    """
    kind = case["kind"]
    result = CaseCheck(case_id=case["id"], kind=kind, status="", reason=None, facts=[],
                       items=[])
    if kind in _HAND_CHECK_KINDS:
        result["status"], result["reason"] = "hand_check", "dimensional"
        return result

    params = _case_params(case["id"])
    metric = params.get("metric")
    cited = [int(i) for i in dict.fromkeys(case.get("expected_fact_ids") or [])]
    state = _CaseState(store, source)
    state.load(cited)
    items: list[dict] = []

    if kind == "lookup":
        sec = state.sec_value(cited[0]) if cited else None
        items.append(_item(case["ticker"], case["expected_value"], sec))
    elif kind == "compute":
        if metric is not None:
            sec = state.metric(case["ticker"], case["fy"], metric)
        elif len(case.get("expected_fact_ids") or []) == 2:
            sec = state.yoy(cited)
        else:
            result["status"] = "template_unknown"
            return result
        items.append(_item(case["ticker"], case["expected_value"], sec))
    elif kind == "screen":
        for row in case.get("expected_rows") or []:
            entity = row["entity"]
            row_ids = [i for i in cited if i in state.records
                       and state.records[i]["ticker"] == entity]
            if metric is not None:
                sec = state.metric(entity, case["fy"], metric)
            elif len(row_ids) == 2:
                sec = state.yoy(row_ids)
            elif len(row_ids) == 1:
                sec = state.sec_value(row_ids[0])
            else:
                sec = None
            items.append(_item(entity, row["value"], sec))
    else:
        result["status"] = "template_unknown"
        return result

    result["facts"] = list(state.checks.values())
    result["items"] = items
    if kind == "screen" and (
            any(f["status"] == "dimensional" for f in result["facts"])
            or any(state.records[i]["dimensional"] for i in cited if i in state.records)):
        result["status"], result["reason"] = "hand_check", "dimensional"
        return result
    result["status"], result["reason"] = _verdict(result["facts"], items)
    return result


def select_hand_checks(cases: list[dict], seed: int) -> list[dict]:
    """Selects the dimensional and breakdown cases to check by hand.

    Args:
        cases: Suite case dicts.
        seed: The selection seed.

    Returns:
        The selected cases, sorted by id.
    """
    by_origin: dict[str, list[dict]] = {}
    for case in cases:
        if case["kind"] in _HAND_CHECK_CAPS:
            by_origin.setdefault(case["origin"], []).append(case)
    by_id: dict[str, dict] = {}
    for origin, group in by_origin.items():
        ids = sorted({c["id"] for c in group})
        k = min(_HAND_CHECK_CAPS[group[0]["kind"]], len(ids))
        picked = random.Random(f"{seed}:{origin}").sample(ids, k)
        lookup = {c["id"]: c for c in group}
        by_id.update({i: lookup[i] for i in picked})
    return [by_id[i] for i in sorted(by_id)]


def _rates(counts: Counter) -> dict:
    out: dict = dict(counts)
    checked = counts.get("match", 0) + counts.get("mismatch", 0)
    out["checked"] = checked
    out["match_rate"] = counts.get("match", 0) / checked if checked else None
    return out


def summarize(case_checks: list[CaseCheck]) -> dict:
    """Counts case statuses per kind, match rates and distinct fact statuses.

    Args:
        case_checks: The case results.

    Returns:
        Per-kind and overall counts, ``checked``, ``match_rate`` and
        ``fact_status_counts``.
    """
    per_kind: dict[str, Counter] = {}
    overall: Counter = Counter()
    fact_status: dict[int, str] = {}
    for cc in case_checks:
        per_kind.setdefault(cc["kind"], Counter())[cc["status"]] += 1
        overall[cc["status"]] += 1
        for fc in cc["facts"]:
            fact_status.setdefault(fc["fact_id"], fc["status"])
    summary: dict = {kind: _rates(counts) for kind, counts in per_kind.items()}
    summary["all"] = _rates(overall)
    summary["fact_status_counts"] = dict(Counter(fact_status.values()))
    return summary
