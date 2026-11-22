# The v2 dev and validation suites: intents enumerated from a corpus inventory, keyed from
# SEC companyfacts (S1-S6), cross-checked against the fact store, phrased, split, and written
# as hashed canonical JSON with a manifest. Policies: docs/design/XbrlRag_Eval_Semantics.md.
# Spec: docs/specs/v2-suite-build.md.

from __future__ import annotations

import datetime as dt
import hashlib
import json
from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

from src.eval.core.case import EvalCase
from src.eval.keys.base import SecSource
from src.eval.keys.core import (
    AnswerKey,
    Disagreement,
    KeyAmbiguousError,
    KeyNotFoundError,
    UnitMismatchError,
    annual_filing_labels,
    as_reported_key,
    cover_shares_key,
    cross_check,
    filed_under_year,
    labelled_fiscal_year_key,
    reporting_currency,
)
from src.eval.keys.key_definitions import key_definition
from src.eval.suites.intents import (
    RATIO_DEFINITIONS,
    Intent,
    Lexicon,
    Phrasebook,
    build_phrased_cases,
    intent_id,
    intent_shape,
)

if TYPE_CHECKING:
    from src.eval.suites.v2_witness import KeyWitness

# The plan's dev mix (§3.4) over the generated shapes: (class, weight, shapes in order).
SHAPE_CLASSES: tuple[tuple[str, int, tuple[str, ...]], ...] = (
    ("single_filer", 35, ("lookup", "compute.ratio")),
    ("cross_filer", 15, ("screen.top", "screen.bottom", "screen.sum", "screen.median",
                         "peer_relative")),
    ("cross_period", 10, ("compute.yoy_growth", "compute.difference", "compute.cagr",
                          "trend", "point_in_time")),
)
GENERATED_SHAPES: tuple[str, ...] = tuple(s for _c, _w, shapes in SHAPE_CLASSES for s in shapes)

# The compound shapes (docs/specs/compound-shape.md): built into their own files, never
# part of the main mix above.
COMPOUND_SHAPES: tuple[str, ...] = ("compound.rank_by_ratio", "compound.leader_growth")

# Annual report forms; the filing labelled F is the earliest fp=FY one of these with label F.
_ANNUAL_FORMS = frozenset({"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"})
# S2's "more than three months" between ranked year-ends.
_DISCLOSE_DAYS = 92
# Screens rank in one currency and never convert (S6).
_SCREEN_UNIT = "USD"
_COMPARISONS = frozenset({"compute.yoy_growth", "compute.difference", "compute.cagr", "trend"})
_CROSS_FILER = frozenset({"screen.top", "screen.bottom", "screen.sum", "screen.median",
                          "peer_relative"})
_DEFINITIONS = {"compute.yoy_growth": "growth", "compute.difference": "difference",
                "compute.cagr": "cagr"}
_CASE_KINDS = {"lookup": "lookup", "point_in_time": "lookup", "trend": "breakdown",
               "screen.top": "screen", "screen.bottom": "screen", "peer_relative": "screen"}
_UNITLESS = frozenset({"compute.yoy_growth", "compute.cagr", "compute.ratio"})
_FIRST_METRIC_UNIT = frozenset({"lookup", "point_in_time", "compute.difference", "trend"})
_ALT_PERCENT = frozenset({"compute.yoy_growth", "compute.cagr", "compute.ratio"})
_SPLITS = ("dev", "validation")
# A metric keyed only from one concept; any other inventory concept drops the intent with
# "concept_mismatch" (docs/specs/shares-outstanding-reading.md requirement 1).
_REQUIRED_CONCEPTS = {"SharesOutstanding": "us-gaap:CommonStockSharesOutstanding"}
# The metrics with a disclosed cover-page reading, and the shapes that carry it
# (requirement 4).
_COVER_READING_METRICS = frozenset({"SharesOutstanding"})
_COVER_READING_SHAPES = frozenset({"lookup", "trend"})
_FILE_NAMES = {"dev": "xbrl_v2_dev.json", "validation": "xbrl_v2_validation.json",
               "manifest": "xbrl_v2_manifest.json", "intents": "intents.json"}
_COMPOUND_FILE_NAMES = {"dev": "xbrl_v2_dev_compound.json",
                        "validation": "xbrl_v2_validation_compound.json",
                        "manifest": "xbrl_v2_compound_manifest.json",
                        "intents": "intents_compound.json"}
# Compound rankings rank this many filers (docs/specs/compound-shape.md requirement 3).
_COMPOUND_N = 3
# S6's ratio precision: two ratios equal at it tie (compound-shape requirement 4).
_RATIO_SIG_FIGS = 3
# What a compound part needs of each key input to grade at its own precision (req. 6).
_PART_INPUT_FIELDS = ("concept", "unit", "period_end", "value", "decimals")


class FactStoreSource(ABC):
    """Where the fact store's value for one filing, concept, unit and period comes from."""

    @abstractmethod
    def value(self, cik: str, concept: str, unit: str, period_start: str | None,
              period_end: str, accession: str) -> AnswerKey | None:
        """Returns the value one filing tagged, as a ``fact_store`` key.

        Args:
            cik: The filer's CIK, 10 digits.
            concept: ``"<taxonomy>:<tag>"``.
            unit: The unit, e.g. ``USD``.
            period_start: The duration's start, or ``None`` for an instant.
            period_end: The period's end (ISO date).
            accession: The filing whose value is wanted.

        Returns:
            An ``AnswerKey`` with ``key_source "fact_store"``, or ``None`` when the store
            has no such value.
        """
        raise NotImplementedError("tdd:v2-suite-build")


class IntentDroppedError(Exception):
    """An intent with no key; ``args[0]`` is the drop reason (spec requirement 9)."""


class KeyedIntent(TypedDict):
    """One intent with its expected answer and key provenance (spec requirement 11).

    Keys: intent (Intent), shape (str), value (float | None), rows (list[dict] | None),
    not_meaningful (bool), definition (str | None), same_filing (bool | None),
    inputs (list[AnswerKey]), disagreements (list[Disagreement]), key_status (str),
    key_source (str), policies (list[str]), disclosures (dict | None),
    subject (dict | None).
    """

    intent: Intent
    shape: str
    value: float | None
    rows: list[dict] | None
    not_meaningful: bool
    definition: str | None
    same_filing: bool | None
    inputs: list[AnswerKey]
    disagreements: list[Disagreement]
    key_status: str
    key_source: str
    policies: list[str]
    disclosures: dict | None
    subject: dict | None


# --------------------------------------------------------------------------- quotas


def shape_quotas(total: int) -> dict[str, int]:
    """Apportions ``total`` intents over the 12 generated shapes by the dev mix.

    Args:
        total: The number of intents, an int >= 0.

    Returns:
        ``{shape: quota}`` for the 12 shapes, summing to ``total``.

    Raises:
        ValueError: ``total`` is negative or not an int.
    """
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        raise ValueError(f"total must be an int >= 0, got {total!r}")
    weight_sum = sum(w for _c, w, _s in SHAPE_CLASSES)
    class_quota = [total * w // weight_sum for _c, w, _s in SHAPE_CLASSES]
    left = total - sum(class_quota)
    # Largest remainder first; ties go to the earlier class (stable sort on the index).
    by_remainder = sorted(range(len(SHAPE_CLASSES)),
                          key=lambda i: -(total * SHAPE_CLASSES[i][1] % weight_sum))
    for i in by_remainder[:left]:
        class_quota[i] += 1
    quotas: dict[str, int] = {}
    for (_c, _w, shapes), q in zip(SHAPE_CLASSES, class_quota, strict=True):
        base, extra = divmod(q, len(shapes))
        for k, shape in enumerate(shapes):
            quotas[shape] = base + (1 if k < extra else 0)
    return quotas


# --------------------------------------------------------------------------- inputs


def _normalize_inventory(inventory: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """The inventory with upper-case tickers, str CIKs, int years and plain metric specs."""
    out: dict[str, dict[str, Any]] = {}
    for ticker, entry in inventory.items():
        t = str(ticker).strip().upper()
        if not t or t in out:
            raise ValueError(f"inventory ticker {ticker!r} is empty or repeated")
        years: dict[int, dict[str, dict[str, str]]] = {}
        for year, metrics in entry["years"].items():
            years[int(year)] = {str(m): {"concept": str(spec["concept"]),
                                         "unit": str(spec["unit"])}
                                for m, spec in metrics.items()}
        out[t] = {"cik": str(entry["cik"]), "years": years}
    return out


def _normalize_universes(universes: Mapping[str, Sequence[str]]) -> dict[str, tuple[str, ...]]:
    """Each universe's members, upper-cased, deduplicated and sorted."""
    return {str(u): tuple(sorted({str(t).strip().upper() for t in members}))
            for u, members in universes.items()}


def _spec(filer: Mapping[str, Any], year: int, metric: str) -> dict[str, str] | None:
    """The filer's ``{"concept", "unit"}`` for a metric in a year, or ``None``."""
    result: dict[str, str] | None = filer["years"].get(year, {}).get(metric)
    return result


def _consistent(filer: Mapping[str, Any], metric: str, years: Sequence[int]) -> bool:
    """Whether ``metric`` is tagged in every year with one concept and unit (rule 7)."""
    specs = [_spec(filer, y, metric) for y in years]
    if any(s is None for s in specs):
        return False
    return len({(s["concept"], s["unit"]) for s in specs if s is not None}) == 1


# --------------------------------------------------------------------------- enumerate


def _enumerate(inv: Mapping[str, Mapping[str, Any]],
               unis: Mapping[str, Sequence[str]]) -> dict[str, list[Intent]]:
    found: dict[str, set[Intent]] = {shape: set() for shape in GENERATED_SHAPES}
    for ticker in sorted(inv):
        filer = inv[ticker]
        held = sorted(filer["years"])
        held_set = set(held)
        for y in held:
            metrics = filer["years"][y]
            for m in metrics:
                found["lookup"].add(Intent("lookup", tickers=[ticker], fiscal_years=[y],
                                           metrics=[m]))
            for (m1, m2) in RATIO_DEFINITIONS:
                if m1 in metrics and m2 in metrics and metrics[m1]["unit"] == metrics[m2]["unit"]:
                    found["compute.ratio"].add(Intent(
                        "compute", operation="ratio", tickers=[ticker], fiscal_years=[y],
                        metrics=[m1, m2]))
            for m in metrics:
                if y + 1 in held_set and y + 2 in held_set and _consistent(
                        filer, m, (y, y + 1, y + 2)):
                    found["trend"].add(Intent("trend", tickers=[ticker],
                                              fiscal_years=[y, y + 1, y + 2], metrics=[m]))
            for u in sorted(unis):
                if ticker in unis[u]:
                    for m in metrics:
                        found["peer_relative"].add(Intent(
                            "peer_relative", tickers=[ticker], fiscal_years=[y], metrics=[m],
                            universe=u))
        for i, y1 in enumerate(held):
            for y2 in held[i + 1:]:
                for m in filer["years"][y1]:
                    if not _consistent(filer, m, (y1, y2)):
                        continue
                    span = [y1, y2]
                    if y2 - y1 == 1:
                        found["compute.yoy_growth"].add(Intent(
                            "compute", operation="yoy_growth", tickers=[ticker],
                            fiscal_years=span, metrics=[m]))
                    found["compute.difference"].add(Intent(
                        "compute", operation="difference", tickers=[ticker], fiscal_years=span,
                        metrics=[m]))
                    if y2 - y1 >= 2:
                        found["compute.cagr"].add(Intent(
                            "compute", operation="cagr", tickers=[ticker], fiscal_years=span,
                            metrics=[m]))
                    if y2 - y1 <= 2:
                        found["point_in_time"].add(Intent(
                            "point_in_time", tickers=[ticker], fiscal_years=[y1],
                            filing_year=y2, metrics=[m]))
    for u in sorted(unis):
        pairs = {(y, m) for t in unis[u] if t in inv
                 for y, metrics in inv[t]["years"].items() for m in metrics}
        for y, m in pairs:
            for op in ("top", "bottom", "sum", "median"):
                n = 3 if op in ("top", "bottom") else None
                found[f"screen.{op}"].add(Intent("screen", operation=op, universe=u,
                                                 fiscal_years=[y], metrics=[m], n=n))
    return {shape: sorted(found[shape], key=intent_id) for shape in GENERATED_SHAPES}


def enumerate_intents(inventory: Mapping[str, Any],
                      universes: Mapping[str, Sequence[str]]) -> dict[str, list[Intent]]:
    """Lists the candidate intents per shape from the corpus inventory.

    Args:
        inventory: ticker -> ``{"cik", "years": {year: {metric: {"concept", "unit"}}}}``.
        universes: universe id -> member tickers.

    Returns:
        ``{shape: [Intent, ...]}`` for the 12 shapes, each list sorted by intent id.
    """
    return _enumerate(_normalize_inventory(inventory), _normalize_universes(universes))


def _enumerate_compound(inv: Mapping[str, Mapping[str, Any]],
                        unis: Mapping[str, Sequence[str]]) -> dict[str, list[Intent]]:
    """The compound candidates (docs/specs/compound-shape.md requirement 3).

    For each universe and each (year, metric A) its screens enumerate: one rank_by_ratio
    intent per S4 ratio id, and one leader_growth intent per other metric B that some
    member tags in both the year before and the year.
    """
    found: dict[str, set[Intent]] = {shape: set() for shape in COMPOUND_SHAPES}
    ratio_ids = sorted(set(RATIO_DEFINITIONS.values()))
    for u in sorted(unis):
        members = [t for t in unis[u] if t in inv]
        pairs = {(y, m) for t in members for y, metrics in inv[t]["years"].items()
                 for m in metrics}
        for y, a in pairs:
            for r in ratio_ids:
                if r != a:
                    found["compound.rank_by_ratio"].add(Intent(
                        "compound", operation="rank_by_ratio", universe=u, n=_COMPOUND_N,
                        fiscal_years=[y], metrics=[a, r]))
            growth = {b for t in members for b in inv[t]["years"].get(y, {})
                      if b in inv[t]["years"].get(y - 1, {})}
            for b in growth - {a}:
                found["compound.leader_growth"].add(Intent(
                    "compound", operation="leader_growth", universe=u, n=_COMPOUND_N,
                    fiscal_years=[y - 1, y], metrics=[a, b]))
    return {shape: sorted(found[shape], key=intent_id) for shape in COMPOUND_SHAPES}


def _digest(intent: Intent) -> str:
    return hashlib.sha1(intent_id(intent).encode("utf-8")).hexdigest()


def order_candidates(intents: Sequence[Intent]) -> list[Intent]:
    """Orders one shape's candidates: a hash round-robin over filers (or universes).

    Args:
        intents: The candidates.

    Returns:
        The intents sorted by (rank within stratum, digest).

    Raises:
        ValueError: An intent appears twice.
    """
    strata: dict[str, list[tuple[str, Intent]]] = {}
    seen: set[str] = set()
    for intent in intents:
        digest = _digest(intent)
        if digest in seen:
            raise ValueError(f"duplicate candidate intent {intent!r}")
        seen.add(digest)
        stratum = intent.tickers[0] if intent.tickers else str(intent.universe)
        strata.setdefault(stratum, []).append((digest, intent))
    ranked: list[tuple[int, str, Intent]] = []
    for items in strata.values():
        for rank, (digest, intent) in enumerate(sorted(items, key=lambda d: d[0])):
            ranked.append((rank, digest, intent))
    ranked.sort(key=lambda r: (r[0], r[1]))
    return [intent for _rank, _digest_, intent in ranked]


# --------------------------------------------------------------------------- keying


class _MemoSecSource(SecSource):
    """Fetches each filer's companyfacts at most once; a failure is remembered too."""

    def __init__(self, inner: SecSource) -> None:
        self._inner = inner
        self._docs: dict[str, dict | BaseException] = {}

    def company_facts(self, cik: str) -> dict:
        key = str(cik)
        if key not in self._docs:
            try:
                self._docs[key] = self._inner.company_facts(cik)
            except (KeyNotFoundError, UnitMismatchError, KeyAmbiguousError) as exc:
                self._docs[key] = exc
        found = self._docs[key]
        if isinstance(found, BaseException):
            raise found
        return found

    def frames(self, taxonomy: str, tag: str, unit: str, period: str) -> dict:
        raise NotImplementedError("v2-suite-build reads companyfacts only")


def _drop_reason(exc: Exception) -> str:
    """The drop reason of a key-builder error (requirement 9)."""
    if isinstance(exc, KeyNotFoundError):
        return "no_key"
    if isinstance(exc, UnitMismatchError):
        return "unit_mismatch"
    return "ambiguous"


def _guard[T](build: Callable[[], T]) -> T:
    """Runs a key builder, turning its key errors into ``IntentDroppedError``."""
    try:
        return build()
    except (KeyNotFoundError, UnitMismatchError, KeyAmbiguousError) as exc:
        raise IntentDroppedError(_drop_reason(exc)) from exc


def _require_spec(filer: Mapping[str, Any], year: int, metric: str) -> dict[str, str]:
    spec = _spec(filer, year, metric)
    if spec is None:
        raise ValueError(f"metric {metric!r} is not in the inventory for {year}")
    required = _REQUIRED_CONCEPTS.get(metric)
    if required is not None and spec["concept"] != required:
        raise IntentDroppedError("concept_mismatch")
    return spec


def _reading(key: AnswerKey) -> dict[str, Any]:
    """The alternate reading a cover key gives a case or row (requirement 4)."""
    return {"reading": key["selection"], "value": key["value"], "concept": key["concept"],
            "period_end": key["period_end"], "accession": key["accession"]}


def _filer(inv: Mapping[str, Mapping[str, Any]], ticker: str) -> Mapping[str, Any]:
    if ticker not in inv:
        raise ValueError(f"ticker {ticker!r} is not in the inventory")
    return inv[ticker]


def _labelled(key: AnswerKey, fiscal_key: AnswerKey) -> AnswerKey:
    """An as-reported key carrying the fiscal-year label and S2 of the key it re-reads."""
    key["fiscal_year"] = fiscal_key["fiscal_year"]
    key["policies"] = sorted(set(key["policies"]) | set(fiscal_key["policies"]))
    return key


def _original_annual_accession(cf: dict, labels: dict[str, int], concept: str, unit: str,
                               filing_year: int) -> str:
    """The filing labelled ``filing_year``: its earliest filed annual report (requirement 9).

    ``labels`` is the document's ``annual_filing_labels``.

    Raises:
        KeyNotFoundError: No annual report of that fiscal year tags the concept in the unit.
    """
    taxonomy, _sep, tag = concept.partition(":")
    node = cf.get("facts", {}).get(taxonomy, {}).get(tag) or {}
    entries = node.get("units", {}).get(unit, [])
    annual = [e for e in entries if e.get("form") in _ANNUAL_FORMS and e.get("fp") == "FY"
              and filed_under_year(e, labels, filing_year)]
    if not annual:
        raise KeyNotFoundError(f"no annual filing labelled {filing_year} for {concept}")
    first = min(annual, key=lambda e: (e["filed"], e["accn"]))
    accession: str = first["accn"]
    return accession


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _days_apart(ends: Sequence[str]) -> int:
    dates = [dt.date.fromisoformat(e) for e in ends]
    return (max(dates) - min(dates)).days


class _Keyer:
    """Keys intents against one normalized inventory, universe set and pair of sources."""

    def __init__(self, inv: Mapping[str, Mapping[str, Any]], unis: Mapping[str, Sequence[str]],
                 sec: SecSource, fact_store: FactStoreSource,
                 witness: KeyWitness | None = None) -> None:
        self.inv = inv
        self.unis = unis
        self.sec = sec
        self.fact_store = fact_store
        self.witness = witness
        self._labels_by_doc: dict[str | int, dict[str, int]] = {}

    def _cf(self, filer: Mapping[str, Any]) -> dict:
        return _guard(lambda: self.sec.company_facts(filer["cik"]))

    def _labels(self, cf: dict) -> dict[str, int]:
        """The document's labels, computed once per filer.

        Cached by the document's CIK, never by ``id(cf)`` alone: a source that returns a new
        document per call frees the old one, and its id can be reused by another filer's
        (key-currency-and-definition, Interface).
        """
        cik = cf.get("cik")
        memo: str | int = str(cik).zfill(10) if cik is not None else id(cf)
        if memo not in self._labels_by_doc:
            self._labels_by_doc[memo] = annual_filing_labels(cf)
        return self._labels_by_doc[memo]

    def _reports_in_screen_unit(self, filer: Mapping[str, Any], year: int) -> bool:
        """Whether a member some path would rank may be ranked: its filing's reporting
        currency is the screen unit, or cannot be told (key-currency-and-definition R2, S6).
        """
        try:
            cf = self._cf(filer)
        except IntentDroppedError:
            return True
        currency = reporting_currency(cf, self._labels(cf), year)
        return currency is None or currency == _SCREEN_UNIT

    def key(self, intent: Intent) -> KeyedIntent:
        shape = intent_shape(intent)
        result: dict[str, Any] = {"value": None, "rows": None, "not_meaningful": False,
                                  "definition": None, "same_filing": None,
                                  "disclosures": None, "subject": None}
        if shape == "lookup":
            keys = self._lookup(intent, result)
        elif shape == "point_in_time":
            keys = self._point_in_time(intent, result)
        elif shape in _COMPARISONS:
            keys = self._comparison(intent, shape, result)
        elif shape == "compute.ratio":
            keys = self._ratio(intent, result)
        elif shape in _CROSS_FILER:
            keys = self._cross_filer(intent, shape, result)
        else:
            raise ValueError(f"shape {shape!r} is not generated by v2-suite-build")
        if (result["rows"] is None and result["value"] == 0
                and not result["not_meaningful"]):
            raise IntentDroppedError("zero_value")
        inputs, disagreements = self._cross_check(keys)
        extra: set[str] = set()
        if shape.startswith("compute."):
            extra.add("S4")
        if shape in _CROSS_FILER:
            extra |= {"S3", "S5"}
        policies = sorted({p for k in inputs for p in k["policies"]} | extra)
        status = ("verified" if inputs and all(k["status"] == "verified" for k in inputs)
                  else "single-source")
        return KeyedIntent(
            intent=intent, shape=shape, value=result["value"], rows=result["rows"],
            not_meaningful=result["not_meaningful"], definition=result["definition"],
            same_filing=result["same_filing"], inputs=inputs, disagreements=disagreements,
            key_status=status,
            key_source="+".join(sorted({k["key_source"] for k in inputs})),
            policies=policies, disclosures=result["disclosures"], subject=result["subject"],
        )

    def cover_readings(self, keyed: KeyedIntent) -> dict[int, AnswerKey] | None:
        """Each year's cover-page reading of a shares-outstanding lookup or trend.

        Spec: docs/specs/shares-outstanding-reading.md, requirement 4. Each year reads the
        cover of its own original 10-K, dated after that year's key period end.

        Args:
            keyed: A keyed intent.

        Returns:
            Fiscal year -> cover key, for the years that have one; ``None`` when the intent
            carries no readings (another metric or shape).
        """
        intent = keyed["intent"]
        if (keyed["shape"] not in _COVER_READING_SHAPES
                or intent.metrics[0] not in _COVER_READING_METRICS):
            return None
        cf = self._cf(_filer(self.inv, intent.tickers[0]))
        labels = self._labels(cf)
        years = list(intent.fiscal_years)
        if keyed["rows"] is not None:
            ends = [str(r["period_end"]) for r in keyed["rows"]]
        else:
            ends = [keyed["inputs"][0]["period_end"]]
        found: dict[int, AnswerKey] = {}
        for year, end in zip(years, ends, strict=True):
            cover = cover_shares_key(cf, labels, year, end)
            if cover is not None:
                found[year] = cover
        return found

    def _cross_check(self, keys: list[AnswerKey]) -> tuple[list[AnswerKey], list[Disagreement]]:
        inputs: list[AnswerKey] = []
        disagreements: list[Disagreement] = []
        for key in keys:
            other = self.fact_store.value(key["cik"], key["concept"], key["unit"],
                                          key["period_start"], key["period_end"],
                                          key["accession"])
            if other is None:
                inputs.append(key)
                continue
            checked = cross_check(key, other)
            inputs.append(checked["key"])
            if checked["disagreement"] is not None:
                disagreements.append(checked["disagreement"])
        return inputs, disagreements

    def _fy_key(self, cf: dict, spec: Mapping[str, str], year: int,
                filer: Mapping[str, Any] | None = None, metric: str | None = None) -> AnswerKey:
        """The fiscal-year key of a spec; with a witness, the one ``select_key`` chooses."""
        if self.witness is None or filer is None or metric is None:
            return _guard(lambda: labelled_fiscal_year_key(cf, self._labels(cf), spec["concept"],
                                                           spec["unit"], year))
        return self._witnessed(cf, filer, metric, spec, year)[0]

    def _witnessed(self, cf: dict, filer: Mapping[str, Any], metric: str,
                   spec: Mapping[str, str], year: int) -> tuple[AnswerKey, str, float | None]:
        """The witnessed key, its annual report and annual value (key-statement-witness)."""
        from src.eval.suites.v2_witness import WitnessDrop

        if self.witness is None:
            raise ValueError("no statement witness configured")
        try:
            return self.witness.fy_key(cf, self._labels(cf), filer["cik"], metric, spec, year)
        except WitnessDrop as exc:
            # A resolved drop carries its reviewed value as args[1].
            raise IntentDroppedError(str(exc.args[0]), *exc.args[1:2]) from exc

    def _defined(self, metric: str) -> bool:
        """Whether ``metric`` is keyed by a stated definition (key-definitions R2, R3)."""
        from src.eval.suites.v2_witness import DEFINED_FAMILIES

        return self.witness is not None and metric in DEFINED_FAMILIES

    def _reread(self, cf: dict, filer: Mapping[str, Any], metric: str, fiscal: AnswerKey,
                filing_year: int) -> AnswerKey | None:
        """A defined key's balance as the annual report labelled ``filing_year`` presents it."""
        if self.witness is None:
            return None
        adsh = self.witness.annual_accession(cf, self._labels(cf), filing_year)
        if adsh is None:
            return None
        year = fiscal["fiscal_year"] if fiscal["fiscal_year"] is not None else filing_year
        return self.witness.reread(cf, filer["cik"], metric, year, adsh, fiscal["period_end"],
                                   fiscal["unit"])

    def _reread_all(self, cf: dict, filer: Mapping[str, Any], metric: str,
                    own: Mapping[int, AnswerKey], years: Sequence[int],
                    accession: str) -> list[AnswerKey]:
        """Each year's defined key as filing ``accession`` presents it (one filing, S2).

        Raises:
            KeyNotFoundError: The filing does not define one of the years.
        """
        if self.witness is None:
            raise KeyNotFoundError("no statement witness configured")
        keys: list[AnswerKey] = []
        for y in years:
            k = self.witness.reread(cf, filer["cik"], metric, y, accession,
                                    own[y]["period_end"], own[y]["unit"])
            if k is None:
                raise KeyNotFoundError(f"{metric} {own[y]['period_end']} is not defined in "
                                       f"{accession}")
            keys.append(_labelled(k, own[y]))
        return keys

    def defined_readings(self, keyed: KeyedIntent) -> dict[int, list[dict[str, Any]]] | None:
        """Each year's alternate readings of a Debt or DeferredRevenue lookup or trend.

        Spec: docs/specs/key-definitions.md, requirement 5.

        Args:
            keyed: A keyed intent.

        Returns:
            Fiscal year -> readings (possibly empty); ``None`` for another metric or shape,
            or without a statement witness.
        """
        intent = keyed["intent"]
        metric = intent.metrics[0] if intent.metrics else ""
        if self.witness is None or not self._defined(metric):
            return None
        shape = keyed["shape"]
        if shape not in ("lookup", "trend"):
            return None
        years = list(intent.fiscal_years)
        if keyed["rows"] is not None:
            years = [int(r["fiscal_year"]) for r in keyed["rows"]]
        return {y: self.witness.readings(metric, k, shape)
                for y, k in zip(years, keyed["inputs"], strict=True)}

    def _lookup(self, intent: Intent, result: dict[str, Any]) -> list[AnswerKey]:
        filer = _filer(self.inv, intent.tickers[0])
        year = intent.fiscal_years[0]
        key = self._fy_key(self._cf(filer), _require_spec(filer, year, intent.metrics[0]), year,
                           filer, intent.metrics[0])
        result["value"] = key["value"]
        return [key]

    def _point_in_time(self, intent: Intent, result: dict[str, Any]) -> list[AnswerKey]:
        filer = _filer(self.inv, intent.tickers[0])
        year = intent.fiscal_years[0]
        spec = _require_spec(filer, year, intent.metrics[0])
        cf = self._cf(filer)
        fiscal = self._fy_key(cf, spec, year, filer, intent.metrics[0])
        concept = fiscal["concept"]
        filing_year = int(intent.filing_year) if intent.filing_year is not None else year
        if self._defined(intent.metrics[0]):
            key = self._reread(cf, filer, intent.metrics[0], fiscal, filing_year)
            if key is None:
                raise IntentDroppedError("no_key")
            key = _labelled(key, fiscal)
            result["value"] = key["value"]
            return [key]
        key = _guard(lambda: as_reported_key(
            cf, concept, spec["unit"], fiscal["period_start"], fiscal["period_end"],
            accession=_original_annual_accession(cf, self._labels(cf), concept,
                                                 spec["unit"], filing_year)))
        key = _labelled(key, fiscal)
        result["value"] = key["value"]
        return [key]

    def _comparison(self, intent: Intent, shape: str,
                    result: dict[str, Any]) -> list[AnswerKey]:
        filer = _filer(self.inv, intent.tickers[0])
        years = list(intent.fiscal_years)
        spec = _require_spec(filer, years[0], intent.metrics[0])
        cf = self._cf(filer)
        own = {y: self._fy_key(cf, spec, y, filer, intent.metrics[0]) for y in years}
        latest_accession = own[years[-1]]["accession"]
        try:
            if self._defined(intent.metrics[0]):
                keys = self._reread_all(cf, filer, intent.metrics[0], own, years,
                                        latest_accession)
            else:
                keys = [_labelled(as_reported_key(cf, own[y]["concept"], spec["unit"],
                                                  own[y]["period_start"], own[y]["period_end"],
                                                  accession=latest_accession), own[y])
                        for y in years]
            result["same_filing"] = True
        except KeyNotFoundError:
            keys = [own[y] for y in years]
            result["same_filing"] = False
        except (UnitMismatchError, KeyAmbiguousError) as exc:
            raise IntentDroppedError(_drop_reason(exc)) from exc
        values = [k["value"] for k in keys]
        v1, v2 = values[0], values[-1]
        if shape == "trend":
            result["rows"] = [{"entity": f"FY{y}", "fiscal_year": y, "value": k["value"],
                               "period_end": k["period_end"]}
                              for y, k in zip(years, keys, strict=True)]
            return keys
        result["definition"] = _DEFINITIONS[shape]
        if v1 is None or v2 is None:
            raise IntentDroppedError("no_key")
        if shape == "compute.yoy_growth":
            result["not_meaningful"] = v1 <= 0
            result["value"] = None if v1 == 0 else (v2 - v1) / abs(v1)
        elif shape == "compute.difference":
            result["value"] = v2 - v1
        else:  # compute.cagr
            if v1 > 0 and v2 > 0:
                result["value"] = (v2 / v1) ** (1 / (years[-1] - years[0])) - 1
            else:
                result["not_meaningful"] = True
        return keys

    def _ratio(self, intent: Intent, result: dict[str, Any]) -> list[AnswerKey]:
        filer = _filer(self.inv, intent.tickers[0])
        year = intent.fiscal_years[0]
        m1, m2 = intent.metrics
        definition = RATIO_DEFINITIONS.get((m1, m2))
        if definition is None:
            raise ValueError(f"no ratio definition for ({m1}, {m2})")
        cf = self._cf(filer)
        num = self._fy_key(cf, _require_spec(filer, year, m1), year, filer, m1)
        den = self._fy_key(cf, _require_spec(filer, year, m2), year, filer, m2)
        if num["value"] is None or den["value"] is None:
            raise IntentDroppedError("no_key")
        if den["value"] == 0:
            raise IntentDroppedError("zero_denominator")
        result["definition"] = definition
        result["value"] = num["value"] / den["value"]
        return [num, den]

    def _cross_filer(self, intent: Intent, shape: str,
                     result: dict[str, Any]) -> list[AnswerKey]:
        universe = str(intent.universe)
        if universe not in self.unis:
            raise ValueError(f"universe {universe!r} is not among the universes")
        members = self.unis[universe]
        year, metric = intent.fiscal_years[0], intent.metrics[0]
        ranked: list[tuple[str, float, AnswerKey]] = []
        excluded: list[dict[str, Any]] = []
        for ticker in members:
            reason = None
            dropped: Any = None
            filer = self.inv.get(ticker)
            if filer is None:
                reason = "not_in_corpus"
            elif year not in filer["years"]:
                reason = "period_not_filed"
            elif metric not in filer["years"][year]:
                reason = "metric_not_tagged"
                if self.witness is not None:
                    reason, unkeyed = self._unkeyed_member(filer, metric, year)
                    if reason == "resolved_drop":
                        dropped = self.witness.resolved_drop_value(filer["cik"], metric, year)
                    if unkeyed is not None and unkeyed["value"] is not None:
                        if self._reports_in_screen_unit(filer, year):
                            ranked.append((ticker, unkeyed["value"], unkeyed))
                        else:
                            reason = "currency"
            elif filer["years"][year][metric]["unit"] != _SCREEN_UNIT:
                reason = "currency"
            else:
                try:
                    key = self._screen_key(filer, metric, year)
                except IntentDroppedError as exc:
                    reason = str(exc.args[0])
                    if reason == "resolved_drop" and len(exc.args) > 1:
                        dropped = exc.args[1]
                else:
                    if key["value"] is None:
                        reason = "no_key"
                    elif not self._reports_in_screen_unit(filer, year):
                        reason = "currency"
                    else:
                        ranked.append((ticker, key["value"], key))
            if reason is not None:
                entry: dict[str, Any] = {"ticker": ticker, "reason": reason}
                if reason == "resolved_drop" and dropped is not None:
                    # The reviewed resolution's value: the member is graded "don't care"
                    # (grading-dont-care-and-ties requirement 2).
                    entry["value"] = dropped
                excluded.append(entry)
        # Value descending (screen.bottom: ascending), ties by ticker ascending.
        sign = 1 if shape == "screen.bottom" else -1
        ranked.sort(key=lambda r: (sign * r[1], r[0]))
        if len(ranked) < 2:
            raise IntentDroppedError("too_few_ranked")
        tickers = [t for t, _v, _k in ranked]
        values = [v for _t, v, _k in ranked]
        rows = [{"ticker": t, "value": v, "fiscal_year": year, "period_end": k["period_end"]}
                for t, v, k in ranked]
        if shape == "peer_relative":
            subject = intent.tickers[0]
            if subject not in tickers:
                raise IntentDroppedError("subject_not_ranked")
            median = _median(values)
            own = values[tickers.index(subject)]
            position = "above" if own > median else "below" if own < median else "at"
            result["subject"] = {"ticker": subject, "rank": tickers.index(subject) + 1,
                                 "universe_median": median, "position": position}
            result["rows"] = rows
        elif shape in ("screen.top", "screen.bottom"):
            result["rows"] = rows[:intent.n]
        elif shape == "screen.sum":
            result["value"] = sum(values)
        else:
            result["value"] = _median(values)
        period_ends = {t: k["period_end"] for t, _v, k in ranked}
        result["disclosures"] = {
            "universe": universe, "members": len(members), "ranked": tickers,
            "excluded": excluded, "period_ends": period_ends,
            "disclose_period_ends": _days_apart(list(period_ends.values())) > _DISCLOSE_DAYS,
        }
        return [k for _t, _v, k in ranked]

    def _screen_key(self, filer: Mapping[str, Any], metric: str, year: int) -> AnswerKey:
        """A keyed member's key; with a witness, only a ``ranked`` member is keyed.

        Raises:
            IntentDroppedError: The member is not ranked; ``args[0]`` is its exclusion reason
                (``substitute:<code>`` for a stated substitute, key-statement-witness R6).
        """
        cf = self._cf(filer)
        spec = filer["years"][year][metric]
        if self.witness is None:
            return self._fy_key(cf, spec, year)
        key, adsh, annual_value = self._witnessed(cf, filer, metric, spec, year)
        status = self.witness.member(metric, adsh, key, annual_value)
        if status["status"] != "ranked":
            if status["reason"] == "resolved_drop":
                dropped = self.witness.resolved_drop_value(filer["cik"], metric, year)
                if dropped is not None:
                    raise IntentDroppedError("resolved_drop", dropped)
            raise IntentDroppedError(str(status["reason"]))
        return key

    def _unkeyed_member(self, filer: Mapping[str, Any], metric: str,
                        year: int) -> tuple[str | None, AnswerKey | None]:
        """A member the inventory has no key for: ranked only when its statement presents
        the family's line (key-statement-witness R5); otherwise its exclusion reason."""
        if self.witness is None:
            return "metric_not_tagged", None
        try:
            cf = self._cf(filer)
        except IntentDroppedError as exc:
            return str(exc.args[0]), None
        return self.witness.unkeyed_member(cf, self._labels(cf), filer["cik"], metric, year,
                                           _SCREEN_UNIT)

    def key_compound(self, intent: Intent) -> tuple[KeyedIntent, dict[str, Any]]:
        """Keys a compound intent: its ranking, then its dependent ask.

        Spec: docs/specs/compound-shape.md, requirement 4. The ranking is keyed as the
        ``screen.top`` intent (U, Y, A, n), each dependent ratio as its ``compute.ratio``
        intent and the leader's growth as its ``compute.yoy_growth`` intent, through
        ``key`` itself, so every rule and drop reason of those shapes applies unchanged.

        Args:
            intent: A ``compound`` intent.

        Returns:
            The keyed intent (``rows`` and ``disclosures`` are the ranking's, ``inputs``
            the ranking's then the dependent's) and the dependent key
            (``{"ticker", "value", "definition", ...}``).

        With a statement witness, the keying runs in the witness's provisional scope: a
        key input it blocks (one no reviewed resolution covers) drops the intent as
        ``witness_blocked``, whatever else the keying met, and never reaches the witness's
        report, so a main build sharing the witness is unaffected (requirement 4,
        "Witness blocks").

        Raises:
            IntentDroppedError: The intent has no key; ``args[0]`` is the reason.
        """
        if self.witness is None:
            return self._key_compound(intent)
        with self.witness.provisional() as blocks:
            try:
                keyed = self._key_compound(intent)
            except IntentDroppedError:
                if blocks:
                    raise IntentDroppedError("witness_blocked") from None
                raise
        if blocks:
            raise IntentDroppedError("witness_blocked")
        return keyed

    def _key_compound(self, intent: Intent) -> tuple[KeyedIntent, dict[str, Any]]:
        """``key_compound`` without the witness scope."""
        shape = intent_shape(intent)
        year, n = intent.fiscal_years[-1], int(intent.n or 0)
        ranking = self.key(Intent("screen", operation="top", universe=intent.universe,
                                  fiscal_years=[year], metrics=[intent.metrics[0]], n=n))
        disclosures = ranking["disclosures"] or {}
        if len(disclosures.get("ranked") or []) < n:
            raise IntentDroppedError("too_few_ranked_for_n")
        rows = ranking["rows"] or []
        if intent.operation == "rank_by_ratio":
            parts, dependent = self._rank_by_ratio(intent, rows)
        else:
            parts, dependent = self._leader_growth(intent, rows)
        inputs = list(ranking["inputs"])
        disagreements = list(ranking["disagreements"])
        policies = set(ranking["policies"]) | {"S3", "S4", "S5"}
        for part in parts:
            inputs += part["inputs"]
            disagreements += part["disagreements"]
            policies |= set(part["policies"])
        status = ("verified" if inputs and all(k["status"] == "verified" for k in inputs)
                  else "single-source")
        keyed = KeyedIntent(
            intent=intent, shape=shape, value=None, rows=rows, not_meaningful=False,
            definition=dependent["definition"], same_filing=dependent.get("same_filing"),
            inputs=inputs, disagreements=disagreements, key_status=status,
            key_source="+".join(sorted({k["key_source"] for k in inputs})),
            policies=sorted(policies), disclosures=ranking["disclosures"], subject=None,
        )
        return keyed, dependent

    def _rank_by_ratio(self, intent: Intent,
                       rows: Sequence[Mapping[str, Any]]) -> tuple[list[KeyedIntent],
                                                                   dict[str, Any]]:
        """The ranked filers' ``compute.ratio`` keys and the winner (requirement 4)."""
        year, ratio_id = intent.fiscal_years[-1], intent.metrics[1]
        numerator, denominator = next(pair for pair, rid in RATIO_DEFINITIONS.items()
                                      if rid == ratio_id)
        keyed: list[KeyedIntent] = []
        for row in rows:
            ticker = str(row["ticker"])
            filer = _filer(self.inv, ticker)
            if _spec(filer, year, numerator) is None or _spec(filer, year, denominator) is None:
                raise IntentDroppedError("dependent:metric_not_tagged")
            try:
                keyed.append(self.key(Intent("compute", operation="ratio", tickers=[ticker],
                                             fiscal_years=[year],
                                             metrics=[numerator, denominator])))
            except IntentDroppedError as exc:
                raise IntentDroppedError(f"dependent:{exc.args[0]}") from exc
        for one in keyed:
            if len({k["unit"] for k in one["inputs"]}) > 1:
                raise IntentDroppedError("dependent:unit_mismatch")
        ratios = {one["intent"].tickers[0]: float(one["value"] or 0.0) for one in keyed}
        ordered = sorted(ratios.items(), key=lambda tv: (-tv[1], tv[0]))
        if len(ordered) > 1 and _at_ratio_precision(ordered[0][1]) == _at_ratio_precision(
                ordered[1][1]):
            raise IntentDroppedError("dependent_tie")
        winner, value = ordered[0]
        return keyed, {"ticker": winner, "value": value, "definition": ratio_id,
                       "ratios": {t: ratios[t] for t in sorted(ratios)}}

    def _leader_growth(self, intent: Intent,
                       rows: Sequence[Mapping[str, Any]]) -> tuple[list[KeyedIntent],
                                                                   dict[str, Any]]:
        """The leader's ``compute.yoy_growth`` key (requirement 4)."""
        if len(rows) > 1 and rows[0]["value"] == rows[1]["value"]:
            raise IntentDroppedError("leader_tie")
        leader = str(rows[0]["ticker"])
        metric = intent.metrics[1]
        filer = _filer(self.inv, leader)
        if any(_spec(filer, y, metric) is None for y in intent.fiscal_years):
            raise IntentDroppedError("dependent:metric_not_tagged")
        try:
            growth = self.key(Intent("compute", operation="yoy_growth", tickers=[leader],
                                     fiscal_years=list(intent.fiscal_years), metrics=[metric]))
        except IntentDroppedError as exc:
            raise IntentDroppedError(f"dependent:{exc.args[0]}") from exc
        if growth["not_meaningful"] or growth["value"] is None:
            raise IntentDroppedError("dependent_not_meaningful")
        return [growth], {"ticker": leader, "value": growth["value"],
                          "definition": growth["definition"],
                          "same_filing": growth["same_filing"]}


def _at_ratio_precision(value: float) -> Decimal:
    """A ratio rounded half up to the S6 ratio precision, 3 significant figures."""
    exact = Decimal(repr(float(value)))
    if exact == 0:
        return exact
    step = Decimal(1).scaleb(exact.adjusted() - _RATIO_SIG_FIGS + 1)
    return exact.quantize(step, rounding=ROUND_HALF_UP)


def key_intent(intent: Intent, inventory: Mapping[str, Any],
               universes: Mapping[str, Sequence[str]], sec: SecSource,
               fact_store: FactStoreSource) -> KeyedIntent:
    """Computes an intent's expected answer from SEC keys and cross-checks the keys.

    Args:
        intent: The intent.
        inventory: The corpus inventory.
        universes: universe id -> member tickers (screens and peer-relative).
        sec: Where the companyfacts payloads come from.
        fact_store: Where the cross-check values come from.

    Returns:
        The keyed intent.

    Raises:
        IntentDroppedError: The intent has no key; ``args[0]`` is the reason.
    """
    keyer = _Keyer(_normalize_inventory(inventory), _normalize_universes(universes),
                   _MemoSecSource(sec), fact_store)
    return keyer.key(intent)


# --------------------------------------------------------------------------- build


def canonical_json(obj: Any) -> str:
    """The canonical JSON text of ``obj`` (sorted keys, no whitespace, no NaN).

    Args:
        obj: A JSON-serialisable value.

    Returns:
        The canonical text.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def content_hash(obj: Any) -> str:
    """``"sha256:"`` plus the SHA-256 hex digest of ``canonical_json(obj)``.

    Args:
        obj: A JSON-serialisable value.

    Returns:
        The content hash.
    """
    return "sha256:" + hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def _intent_object(intent: Intent) -> dict[str, Any]:
    """The nine canonical fields ``intent_id`` hashes (eval-intents C3)."""
    return {"kind": intent.kind, "tickers": list(intent.tickers),
            "fiscal_years": list(intent.fiscal_years), "metrics": list(intent.metrics),
            "dimension": intent.dimension, "operation": intent.operation,
            "universe": intent.universe, "n": intent.n, "filing_year": intent.filing_year}


def _eval_case(phrased: Any, keyed: KeyedIntent, inv: Mapping[str, Mapping[str, Any]],
               iid: str, covers: Mapping[int, AnswerKey] | None = None,
               defined: Mapping[int, Sequence[Mapping[str, Any]]] | None = None) -> EvalCase:
    """One phrased case as an ``EvalCase`` (requirement 13).

    ``covers`` (fiscal year -> cover key) adds the alternate readings of a
    shares-outstanding lookup or trend (docs/specs/shares-outstanding-reading.md);
    ``defined`` (fiscal year -> readings) those of a Debt or DeferredRevenue lookup or trend
    (docs/specs/key-definitions.md requirement 5).
    """
    intent: Intent = keyed["intent"]
    shape = keyed["shape"]
    ticker = intent.tickers[0] if intent.tickers else None
    cik = int(inv[ticker]["cik"]) if ticker is not None else None
    concept: str | None = None
    unit: str | None = None
    if shape in _CROSS_FILER:
        unit = _SCREEN_UNIT
    if shape in _FIRST_METRIC_UNIT and ticker is not None:
        spec = _spec(inv[ticker], intent.fiscal_years[0], intent.metrics[0])
        if spec is not None:
            concept, unit = spec["concept"], spec["unit"]
            if keyed["inputs"]:  # the statement witness may choose another offered concept
                concept = keyed["inputs"][0]["concept"]
    value = keyed["value"]
    alt = [value * 100] if shape in _ALT_PERCENT and value is not None else []
    rows = keyed["rows"]
    readings: list[dict] = []
    if covers:
        if rows is not None:
            rows = [{**row, "alt_readings": [_reading(covers[row["fiscal_year"]])]}
                    if row.get("fiscal_year") in covers else dict(row) for row in rows]
        elif intent.fiscal_years[0] in covers:
            readings = [_reading(covers[intent.fiscal_years[0]])]
    if defined:
        if rows is not None:
            with_readings = []
            for row in rows:
                year_readings = defined.get(int(row["fiscal_year"]), [])
                with_readings.append({**row, "alt_readings": [dict(r) for r in year_readings]}
                                     if year_readings else dict(row))
            rows = with_readings
        else:
            readings = [dict(r) for r in defined.get(intent.fiscal_years[0], [])]
    split = phrased.split
    tags = [f"split:{split}", f"intent:{iid}", f"family:{phrased.register}",
            f"template:{phrased.template_id}", f"key_source:{keyed['key_source']}",
            f"key_status:{keyed['key_status']}", f"shape:{shape}"]
    return EvalCase(
        id=phrased.case_id, question=phrased.question, suite=f"xbrl_v2_{split}",
        kind=_CASE_KINDS.get(shape, "compute"), expected_value=value,
        expected_rows=rows, alt_readings=readings,
        top_n=intent.n if shape in ("screen.top", "screen.bottom") else None,
        order_sensitive=shape != "trend",
        expect_not_meaningful=keyed["not_meaningful"], alt_values=alt,
        origin=f"v2:{shape}", paraphrase_group=iid, ticker=ticker, cik=cik,
        fy=intent.fiscal_years[-1], concept=concept, unit=unit, tags=tags,
    )


def _case_record(case: EvalCase, keyed: KeyedIntent,
                 covers: Mapping[int, AnswerKey] | None = None,
                 defined: Mapping[int, Sequence[Mapping[str, Any]]] | None = None,
                 definition: str | None = None) -> dict[str, Any]:
    """``EvalCase.to_dict()`` plus the key provenance (requirement 14).

    A shares-outstanding lookup or trend (``covers`` not ``None``) also lists the cover
    keys it reads, in year order (docs/specs/shares-outstanding-reading.md); a Debt or
    DeferredRevenue lookup or trend with readings (``defined``) lists them likewise. A case
    whose own shape states no definition records its family's (``definition``,
    docs/specs/key-definitions.md requirement 1).
    """
    record = case.to_dict()
    record["key"] = {"status": keyed["key_status"], "source": keyed["key_source"],
                     "policies": keyed["policies"],
                     "definition": keyed["definition"] or definition,
                     "same_filing": keyed["same_filing"], "inputs": keyed["inputs"],
                     "disagreements": keyed["disagreements"]}
    if covers is not None:
        record["key"]["alternates"] = [covers[y] for y in sorted(covers)]
    if defined and any(defined.values()):
        record["key"]["alternates"] = [dict(r) for y in sorted(defined) for r in defined[y]]
    if keyed["disclosures"] is not None:
        record["disclosures"] = keyed["disclosures"]
    if keyed["subject"] is not None:
        record["subject"] = keyed["subject"]
    return record


def _validated_quotas(quotas: Mapping[str, int]) -> dict[str, int]:
    unknown = sorted(set(quotas) - set(GENERATED_SHAPES))
    if unknown:
        raise ValueError(f"quotas name shapes that are not generated: {unknown}")
    out = {}
    for shape in GENERATED_SHAPES:
        q = quotas.get(shape, 0)
        if not isinstance(q, int) or isinstance(q, bool) or q < 0:
            raise ValueError(f"quota of {shape} must be an int >= 0, got {q!r}")
        out[shape] = q
    return out


def build_v2_suites(inventory: Mapping[str, Any], universes: Mapping[str, Sequence[str]],
                    sec: SecSource, fact_store: FactStoreSource, phrasebook: Phrasebook,
                    lexicon: Lexicon, *, quotas: Mapping[str, int], per_intent: int,
                    share: float, salt: str,
                    witness: KeyWitness | None = None) -> dict[str, Any]:
    """Builds the dev and validation suites and their manifest.

    Args:
        inventory: The corpus inventory.
        universes: universe id -> member tickers.
        sec: Where the companyfacts payloads come from.
        fact_store: Where the cross-check values come from.
        phrasebook: Question templates.
        lexicon: Surface forms.
        quotas: shape -> number of keyed intents wanted.
        per_intent: Phrasings per intent.
        share: The validation share of each split axis.
        salt: The split salt.
        witness: The statement witness (docs/specs/key-statement-witness.md). With it, every
            key input is chosen by ``select_key``, every screen member is admitted by
            ``universe_status``, and the manifest carries ``key_independence`` and
            ``key_resolutions`` (every resolution must cover a kept blocked key;
            docs/specs/reviewed-resolutions.md, requirement 3).

    Returns:
        ``{"dev": suite, "validation": suite, "manifest": manifest, "intents": intents}``,
        ``intents`` being the canonical objects of the selected intents.

    Raises:
        ValueError: A quota names an unknown shape.
        WitnessBlocked: The witness blocked a key input no reviewed resolution covers.
    """
    wanted = _validated_quotas(quotas)
    inv = _normalize_inventory(inventory)
    unis = _normalize_universes(universes)
    keyer = _Keyer(inv, unis, _MemoSecSource(sec), fact_store, witness)
    candidates = _enumerate(inv, unis)

    keyed: dict[str, KeyedIntent] = {}
    covers: dict[str, dict[int, AnswerKey] | None] = {}
    defined: dict[str, dict[int, list[dict[str, Any]]] | None] = {}
    selected: dict[str, int] = {}
    dropped_by_shape: dict[str, Counter[str]] = {}
    for shape in GENERATED_SHAPES:
        count = 0
        for intent in order_candidates(candidates[shape]):
            if count >= wanted[shape]:
                break
            try:
                one = keyer.key(intent)
            except IntentDroppedError as exc:
                dropped_by_shape.setdefault(shape, Counter())[str(exc.args[0])] += 1
                continue
            keyed[intent_id(intent)] = one
            covers[intent_id(intent)] = keyer.cover_readings(one)
            defined[intent_id(intent)] = keyer.defined_readings(one)
            count += 1
        selected[shape] = count
    resolution_use: dict[str, Any] = {}
    if witness is not None:
        witness.raise_if_blocked()
        resolution_use = witness.resolution_use()

    ordered_ids = sorted(keyed)
    phrased = build_phrased_cases([keyed[i]["intent"] for i in ordered_ids], phrasebook,
                                  lexicon, per_intent=per_intent, share=share, salt=salt)
    records: dict[str, list[dict[str, Any]]] = {s: [] for s in _SPLITS}
    for case in phrased:
        iid = intent_id(case.intent)
        eval_case = _eval_case(case, keyed[iid], inv, iid, covers[iid], defined[iid])
        metrics = keyed[iid]["intent"].metrics
        definition = (key_definition(metrics[0])
                      if witness is not None and metrics else None)
        records[case.split].append(_case_record(eval_case, keyed[iid], covers[iid],
                                                defined[iid], definition))

    return _assemble(
        records, [keyed[i] for i in ordered_ids], suffix="",
        intents_manifest=_intents_manifest(wanted, selected, candidates, dropped_by_shape),
        phrasing={"per_intent": per_intent, "share": share, "salt": salt},
        witness=witness, resolution_use=resolution_use)


def _intents_manifest(wanted: Mapping[str, int], selected: Mapping[str, int],
                      candidates: Mapping[str, Sequence[Intent]],
                      dropped_by_shape: Mapping[str, Counter[str]]) -> dict[str, Any]:
    """The manifest's ``intents`` block: quotas, selections, candidates and drops."""
    dropped: Counter[str] = Counter()
    for per_shape in dropped_by_shape.values():
        dropped.update(per_shape)
    return {
        "quota": dict(wanted),
        "selected": dict(selected),
        "candidates": {s: len(candidates[s]) for s in wanted},
        "dropped": dict(sorted(dropped.items())),
        "dropped_by_shape": {s: dict(sorted(c.items()))
                             for s, c in sorted(dropped_by_shape.items())},
    }


def _assemble(records: Mapping[str, list[dict[str, Any]]], kept: Sequence[KeyedIntent], *,
              suffix: str, intents_manifest: dict[str, Any], phrasing: dict[str, Any],
              witness: KeyWitness | None, resolution_use: dict[str, Any]) -> dict[str, Any]:
    """The two suites, the manifest and the intent list of a build (requirements 13-15).

    Args:
        records: split -> case records.
        kept: The selected keyed intents, in intent-id order.
        suffix: Appended to each suite's name (``""`` or ``"_compound"``).
        intents_manifest: The manifest's ``intents`` block.
        phrasing: The manifest's ``phrasing`` block.
        witness: The statement witness, whose manifests are added when given.
        resolution_use: The witness's resolution use.

    Returns:
        ``{"dev", "validation", "manifest", "intents"}``.
    """
    suites: dict[str, dict[str, Any]] = {}
    case_counts: dict[str, dict[str, Any]] = {}
    for split in _SPLITS:
        cases = sorted(records[split], key=lambda r: r["id"])
        suites[split] = {"name": f"xbrl_v2_{split}{suffix}", "version": "1", "split": split,
                         "n_cases": len(cases), "content_hash": content_hash(cases),
                         "cases": cases}
        shapes = Counter(r["origin"].removeprefix("v2:") for r in cases)
        statuses = Counter(r["key"]["status"] for r in cases)
        case_counts[split] = {"total": len(cases), "by_shape": dict(sorted(shapes.items())),
                              "by_key_status": dict(sorted(statuses.items()))}

    intents = [_intent_object(k["intent"]) for k in kept]
    manifest = {
        "content_hashes": {s: suites[s]["content_hash"] for s in _SPLITS},
        "cases": case_counts,
        "intents": intents_manifest,
        "disagreements": [d for k in kept for d in k["disagreements"]],
        "phrasing": phrasing,
        "intents_hash": content_hash(intents),
    }
    if witness is not None:
        manifest["key_independence"] = witness.manifest()
        manifest["key_definitions"] = witness.definitions_manifest()
        manifest["key_resolutions"] = resolution_use
        manifest["fsds"] = witness.fsds_manifest()
    return {"dev": suites["dev"], "validation": suites["validation"], "manifest": manifest,
            "intents": intents}


def _write(build: Mapping[str, Any], out_dir: str | Path,
           names: Mapping[str, str]) -> dict[str, Path]:
    """Writes each named part of a build as sorted, indented JSON plus a newline."""
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for key, name in names.items():
        text = json.dumps(build[key], sort_keys=True, indent=2, ensure_ascii=False,
                          allow_nan=False) + "\n"
        path = root / name
        path.write_bytes(text.encode("utf-8"))
        paths[key] = path
    return paths


def write_v2_suites(build: Mapping[str, Any], out_dir: str | Path) -> dict[str, Path]:
    """Writes the two suites, the manifest and the intent list as sorted, indented JSON.

    Args:
        build: The result of ``build_v2_suites``.
        out_dir: The directory to write into.

    Returns:
        ``{"dev": path, "validation": path, "manifest": path, "intents": path}``.
    """
    return _write(build, out_dir, _FILE_NAMES)


# --------------------------------------------------------------------------- compound


def _validated_compound_quotas(quotas: Mapping[str, int]) -> dict[str, int]:
    unknown = sorted(set(quotas) - set(COMPOUND_SHAPES))
    if unknown:
        raise ValueError(f"quotas name shapes that are not compound shapes: {unknown}")
    out = {}
    for shape in COMPOUND_SHAPES:
        q = quotas.get(shape, 0)
        if not isinstance(q, int) or isinstance(q, bool) or q < 0:
            raise ValueError(f"quota of {shape} must be an int >= 0, got {q!r}")
        out[shape] = q
    return out


def _part_inputs(keys: Sequence[AnswerKey]) -> list[dict[str, Any]]:
    """The fields of each key input a part's precision rule reads (requirement 6)."""
    plain = [dict(k) for k in keys]
    return [{f: k.get(f) for f in _PART_INPUT_FIELDS} for k in plain]


def _compound_record(phrased: Any, keyed: KeyedIntent, dependent: Mapping[str, Any],
                     lexicon: Lexicon) -> dict[str, Any]:
    """One phrased compound case as a record (docs/specs/compound-shape.md requirement 5).

    The ranking travels at the top level as a ``screen.top`` case does; ``parts`` holds
    the ranking and the dependent ask, each tagged with the shape its precision rule
    follows (requirement 6).
    """
    intent: Intent = keyed["intent"]
    shape = keyed["shape"]
    iid = intent_id(intent)
    rows = [dict(r) for r in keyed["rows"] or []]
    disclosures = keyed["disclosures"] or {}
    n_ranked = len(disclosures.get("ranked") or [])
    ranking = {"part": "ranking", "kind": "screen", "expected_rows": [dict(r) for r in rows],
               "top_n": intent.n, "order_sensitive": True, "unit": _SCREEN_UNIT,
               "disclosures": json.loads(json.dumps(disclosures)),
               "tags": ["shape:screen.top"],
               "key": {"inputs": _part_inputs(keyed["inputs"][:n_ranked])}}
    if intent.operation == "rank_by_ratio":
        candidates = {}
        for row in rows:
            ticker = str(row["ticker"])
            names = lexicon.companies.get(ticker, {})
            candidates[ticker] = [names[f] for f in ("short", "legal") if names.get(f)]
        forms = lexicon.metrics.get(intent.metrics[1], {})
        ratio = [str(forms["label"])] if forms.get("label") else []
        ratio += [str(f) for f in (*forms.get("synonyms", ()), *forms.get("shorthand", ()))]
        dep: dict[str, Any] = {
            "part": "dependent", "kind": "compute", "ticker": dependent["ticker"],
            "expected_value": dependent["value"], "tags": ["shape:compute.ratio"],
            "pick": {"candidates": candidates, "ratio": ratio}}
    else:
        dep = {"part": "dependent", "kind": "compute", "ticker": dependent["ticker"],
               "expected_value": dependent["value"], "alt_values": [dependent["value"] * 100],
               "tags": ["shape:compute.yoy_growth"]}
    split = phrased.split
    tags = [f"split:{split}", f"intent:{iid}", f"family:{phrased.register}",
            f"template:{phrased.template_id}", f"key_source:{keyed['key_source']}",
            f"key_status:{keyed['key_status']}", f"shape:{shape}", "compound"]
    case = EvalCase(
        id=phrased.case_id, question=phrased.question, suite=f"xbrl_v2_{split}_compound",
        kind="screen", expected_rows=rows, top_n=intent.n, order_sensitive=True,
        origin=f"v2:{shape}", paraphrase_group=iid, fy=intent.fiscal_years[-1],
        unit=_SCREEN_UNIT, tags=tags, parts=[ranking, dep])
    record = case.to_dict()
    record["key"] = {"status": keyed["key_status"], "source": keyed["key_source"],
                     "policies": keyed["policies"], "definition": keyed["definition"],
                     "same_filing": keyed["same_filing"], "inputs": keyed["inputs"],
                     "disagreements": keyed["disagreements"],
                     "dependent": dict(dependent)}
    record["disclosures"] = keyed["disclosures"]
    return record


def build_compound_suites(inventory: Mapping[str, Any], universes: Mapping[str, Sequence[str]],
                          sec: SecSource, fact_store: FactStoreSource, phrasebook: Phrasebook,
                          lexicon: Lexicon, *, quotas: Mapping[str, int], per_intent: int,
                          share: float, salt: str,
                          witness: KeyWitness | None = None) -> dict[str, Any]:
    """Builds the compound dev and validation suites and their manifest.

    Spec: docs/specs/compound-shape.md, requirement 7. Takes the inputs of
    ``build_v2_suites``, with ``quotas`` over the two compound shapes.

    Args:
        inventory: The corpus inventory.
        universes: universe id -> member tickers.
        sec: Where the companyfacts payloads come from.
        fact_store: Where the cross-check values come from.
        phrasebook: The compound question templates.
        lexicon: Surface forms (with the four ratio ids).
        quotas: compound shape -> number of keyed intents wanted.
        per_intent: Phrasings per intent.
        share: The validation share of each split axis.
        salt: The split salt.
        witness: The statement witness, as in ``build_v2_suites`` (it may be the main
            build's own). A candidate meeting a key input it blocks, with no reviewed
            resolution covering it, is dropped as ``witness_blocked`` and the next one is
            keyed; the build never raises ``WitnessBlocked`` and adds nothing to what the
            witness's ``raise_if_blocked`` reports (requirement 4, "Witness blocks").

    Returns:
        ``{"dev": suite, "validation": suite, "manifest": manifest, "intents": intents}``.

    Raises:
        ValueError: A quota names a shape that is not a compound shape, or (with a
            witness) a reviewed resolution covers no key the witness kept.
    """
    wanted = _validated_compound_quotas(quotas)
    inv = _normalize_inventory(inventory)
    unis = _normalize_universes(universes)
    keyer = _Keyer(inv, unis, _MemoSecSource(sec), fact_store, witness)
    candidates = _enumerate_compound(inv, unis)

    keyed: dict[str, tuple[KeyedIntent, dict[str, Any]]] = {}
    selected: dict[str, int] = {}
    dropped_by_shape: dict[str, Counter[str]] = {}
    for shape in COMPOUND_SHAPES:
        count = 0
        for intent in order_candidates(candidates[shape]):
            if count >= wanted[shape]:
                break
            try:
                keyed[intent_id(intent)] = keyer.key_compound(intent)
            except IntentDroppedError as exc:
                dropped_by_shape.setdefault(shape, Counter())[str(exc.args[0])] += 1
                continue
            count += 1
        selected[shape] = count
    resolution_use: dict[str, Any] = {}
    if witness is not None:
        # No raise_if_blocked: a compound block drops its candidate (key_compound) and
        # never reaches the witness's report (requirement 4, "Witness blocks").
        resolution_use = witness.resolution_use()

    ordered_ids = sorted(keyed)
    phrased = build_phrased_cases([keyed[i][0]["intent"] for i in ordered_ids], phrasebook,
                                  lexicon, per_intent=per_intent, share=share, salt=salt)
    records: dict[str, list[dict[str, Any]]] = {s: [] for s in _SPLITS}
    for case in phrased:
        one, dependent = keyed[intent_id(case.intent)]
        records[case.split].append(_compound_record(case, one, dependent, lexicon))

    return _assemble(
        records, [keyed[i][0] for i in ordered_ids], suffix="_compound",
        intents_manifest=_intents_manifest(wanted, selected, candidates, dropped_by_shape),
        phrasing={"per_intent": per_intent, "share": share, "salt": salt},
        witness=witness, resolution_use=resolution_use)


def write_compound_suites(build: Mapping[str, Any], out_dir: str | Path) -> dict[str, Path]:
    """Writes the compound suites, manifest and intent list in the main writer's format.

    Spec: docs/specs/compound-shape.md, requirement 7.

    Args:
        build: The result of ``build_compound_suites``.
        out_dir: The directory to write into.

    Returns:
        ``{"dev": path, "validation": path, "manifest": path, "intents": path}``.
    """
    return _write(build, out_dir, _COMPOUND_FILE_NAMES)
