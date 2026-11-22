# v2 question generation: intents, a YAML phrasebook and lexicon, a renderer, a two-axis
# dev / validation hash split and the phrased-case builder.
# Spec: docs/specs/eval-intents.md; the compound kind: docs/specs/compound-shape.md.

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

KINDS = ("lookup", "dimensional", "breakdown", "compute", "trend", "point_in_time", "screen",
         "peer_relative", "compound")
OPERATIONS = {
    "compute": ("yoy_growth", "difference", "cagr", "ratio"),
    "screen": ("top", "bottom", "sum", "median"),
    "compound": ("rank_by_ratio", "leader_growth"),
}
# S4 ratios: (numerator, denominator) -> definition id. A compound rank_by_ratio intent
# names its ratio by the definition id (docs/specs/compound-shape.md requirement 1).
RATIO_DEFINITIONS: dict[tuple[str, str], str] = {
    ("OperatingIncome", "Revenue"): "operating_margin",
    ("NetIncome", "Revenue"): "net_margin",
    ("ResearchAndDevelopment", "Revenue"): "rd_intensity",
    ("CurrentAssets", "CurrentLiabilities"): "current_ratio",
}
SHAPES = (
    "lookup", "dimensional", "breakdown",
    "compute.yoy_growth", "compute.difference", "compute.cagr", "compute.ratio",
    "trend", "point_in_time",
    "screen.top", "screen.bottom", "screen.sum", "screen.median",
    "peer_relative",
    "compound.rank_by_ratio", "compound.leader_growth",
)
REGISTERS = ("terse", "conversational", "formal")
PLACEHOLDERS = frozenset({"company", "metric", "metric_2", "dimension", "period",
                          "first_period", "last_period", "filing_period", "universe", "n"})

_SPAN = frozenset({"company", "metric", "first_period", "last_period"})
_LOOKUP = frozenset({"company", "metric", "period"})
_DIM = frozenset({"company", "metric", "dimension", "period"})
_TOPN = frozenset({"metric", "period", "universe", "n"})
_AGG = frozenset({"metric", "period", "universe"})
# Shape -> (fillable placeholders, placeholders every template must use).
_FILL: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "lookup": (_LOOKUP, _LOOKUP),
    "dimensional": (_DIM, _DIM),
    "breakdown": (_DIM, _DIM),
    "compute.yoy_growth": (_SPAN, frozenset({"company", "metric", "last_period"})),
    "compute.difference": (_SPAN, _SPAN),
    "compute.cagr": (_SPAN, _SPAN),
    "compute.ratio": (frozenset({"company", "metric", "metric_2", "period"}),) * 2,
    "trend": (_SPAN, _SPAN),
    "point_in_time": (frozenset({"company", "metric", "period", "filing_period"}),) * 2,
    "screen.top": (_TOPN, _TOPN),
    "screen.bottom": (_TOPN, _TOPN),
    "screen.sum": (_AGG, _AGG),
    "screen.median": (_AGG, _AGG),
    "peer_relative": (frozenset({"company", "metric", "period", "universe"}),) * 2,
    "compound.rank_by_ratio": (frozenset({"metric", "metric_2", "period", "universe", "n"}),) * 2,
    "compound.leader_growth": (
        frozenset({"metric", "metric_2", "first_period", "last_period", "universe", "n"}),
        frozenset({"metric", "metric_2", "last_period", "universe", "n"})),
}

COMPANY_FORMS = ("ticker", "short", "legal")
PERIOD_FORMS = ("fy_short", "fiscal", "fiscal_year", "year_ended")
_COMPANY_PH = frozenset({"company"})
_METRIC_PH = frozenset({"metric", "metric_2"})
_PERIOD_PH = frozenset({"period", "first_period", "last_period", "filing_period"})
_LEXICON_KEYS = ("companies", "metrics", "dimensions", "universes", "period_ends")

_PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")
_METRIC_FORM_RE = re.compile(r"(synonym|shorthand):(0|[1-9][0-9]*)")
_ISO_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December")


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _opt_str(name: str, value: object) -> str | None:
    if value is None or isinstance(value, str):
        return value
    raise ValueError(f"{name} must be a str or None, got {value!r}")


def _opt_int(name: str, value: object) -> int | None:
    if value is None or (isinstance(value, int) and not isinstance(value, bool)):
        return value
    raise ValueError(f"{name} must be an int or None, got {value!r}")


class Intent:
    """One question's structured spec: kind, filers, years, metrics and kind-specific fields.

    Attributes are the normalised constructor fields (``tickers``, ``fiscal_years`` and
    ``metrics`` as tuples). Equal normalised fields mean equal, equally hashed intents.
    """

    def __init__(
        self,
        kind: str,
        *,
        tickers: Sequence[str] = (),
        fiscal_years: Sequence[int] = (),
        metrics: Sequence[str] = (),
        dimension: str | None = None,
        operation: str | None = None,
        universe: str | None = None,
        n: int | None = None,
        filing_year: int | None = None,
    ) -> None:
        """Normalises and validates the fields.

        Args:
            kind: One of the nine intent kinds.
            tickers: Filer tickers; stripped and upper-cased, order kept.
            fiscal_years: Fiscal years; sorted and deduplicated.
            metrics: Canonical metric ids, order kept (ratio: numerator first; compound:
                the ranking metric first).
            dimension: Member id (dimensional) or axis id (breakdown).
            operation: Compute, screen or compound operation.
            universe: Universe id (screen, peer_relative, compound).
            n: Row count (screen top/bottom, compound).
            filing_year: The filing's fiscal year (point_in_time).

        Raises:
            ValueError: When the fields break the spec's per-kind validation table.
        """
        if kind not in KINDS:
            raise ValueError(f"unknown intent kind {kind!r}")
        if isinstance(tickers, str) or isinstance(metrics, str):
            raise ValueError("tickers and metrics must be sequences of str, not a str")
        norm_tickers: list[str] = []
        for t in tickers:
            if not isinstance(t, str) or not t.strip():
                raise ValueError(f"ticker must be a non-empty str, got {t!r}")
            t = t.strip().upper()
            if t in norm_tickers:
                raise ValueError(f"repeated ticker {t!r}")
            norm_tickers.append(t)
        for y in fiscal_years:
            if not _is_int(y):
                raise ValueError(f"fiscal year must be an int, got {y!r}")
        norm_metrics: list[str] = []
        for m in metrics:
            if not isinstance(m, str) or not m:
                raise ValueError(f"metric must be a non-empty str, got {m!r}")
            if m in norm_metrics:
                raise ValueError(f"repeated metric {m!r}")
            norm_metrics.append(m)
        self.kind = kind
        self.tickers: tuple[str, ...] = tuple(norm_tickers)
        self.fiscal_years: tuple[int, ...] = tuple(sorted(set(fiscal_years)))
        self.metrics: tuple[str, ...] = tuple(norm_metrics)
        self.dimension = _opt_str("dimension", dimension)
        self.operation = _opt_str("operation", operation)
        self.universe = _opt_str("universe", universe)
        self.n = _opt_int("n", n)
        self.filing_year = _opt_int("filing_year", filing_year)
        self._validate()

    def _validate(self) -> None:
        kind = self.kind
        years = self.fiscal_years
        allowed = {
            "lookup": set(), "dimensional": {"dimension"}, "breakdown": {"dimension"},
            "compute": {"operation"}, "trend": set(), "point_in_time": {"filing_year"},
            "screen": {"operation", "universe", "n"}, "peer_relative": {"universe"},
            "compound": {"operation", "universe", "n"},
        }[kind]
        for field in ("dimension", "operation", "universe", "n", "filing_year"):
            value = getattr(self, field)
            if field in allowed:
                if value == "":
                    raise ValueError(f"{kind}: {field} must be non-empty")
            elif value is not None:
                raise ValueError(f"{kind}: {field} must be None, got {value!r}")
        for field in allowed - {"n"}:
            if getattr(self, field) is None:
                raise ValueError(f"{kind}: {field} is required")
        want_tickers = 0 if kind in ("screen", "compound") else 1
        if len(self.tickers) != want_tickers:
            raise ValueError(f"{kind}: needs {want_tickers} ticker(s), got {self.tickers}")
        if kind in OPERATIONS and self.operation not in OPERATIONS[kind]:
            raise ValueError(f"{kind}: unknown operation {self.operation!r}")
        n_metrics = 2 if self.operation == "ratio" or kind == "compound" else 1
        if len(self.metrics) != n_metrics:
            raise ValueError(f"{kind}: needs {n_metrics} metric(s), got {self.metrics}")
        if kind == "compound":
            self._validate_compound()
        elif kind == "trend":
            if len(years) < 3:
                raise ValueError("trend: needs at least 3 fiscal years")
        elif kind == "compute" and self.operation != "ratio":
            if len(years) != 2:
                raise ValueError(f"compute.{self.operation}: needs exactly 2 fiscal years")
            if self.operation == "yoy_growth" and years[1] - years[0] != 1:
                raise ValueError("compute.yoy_growth: the 2 years must be consecutive")
            if self.operation == "cagr" and years[1] - years[0] < 2:
                raise ValueError("compute.cagr: the endpoints must be at least 2 years apart")
        elif len(years) != 1:
            raise ValueError(f"{kind}: needs exactly 1 fiscal year, got {years}")
        if kind == "point_in_time" and self.filing_year is not None and (
                self.filing_year <= years[0]):
            raise ValueError("point_in_time: filing_year must be after the fiscal year")
        if kind == "screen":
            if self.operation in ("top", "bottom"):
                if self.n is None or self.n < 1:
                    raise ValueError(f"screen.{self.operation}: n must be >= 1")
            elif self.n is not None:
                raise ValueError(f"screen.{self.operation}: n must be None")

    def _validate_compound(self) -> None:
        """The compound rows of the validation table (compound-shape requirement 1)."""
        op, years = self.operation, self.fiscal_years
        if op == "rank_by_ratio":
            if len(years) != 1:
                raise ValueError(f"compound.rank_by_ratio: needs exactly 1 fiscal year, got "
                                 f"{years}")
            if self.metrics[1] not in RATIO_DEFINITIONS.values():
                raise ValueError(f"compound.rank_by_ratio: {self.metrics[1]!r} is not an S4 "
                                 f"ratio id")
        elif len(years) != 2 or years[1] - years[0] != 1:
            raise ValueError(f"compound.leader_growth: needs 2 consecutive fiscal years, got "
                             f"{years}")
        if self.n is None or self.n < 2:
            raise ValueError(f"compound.{op}: n must be an int >= 2, got {self.n!r}")

    def _key(self) -> tuple[object, ...]:
        return (self.kind, self.tickers, self.fiscal_years, self.metrics, self.dimension,
                self.operation, self.universe, self.n, self.filing_year)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Intent):
            return NotImplemented
        return self._key() == other._key()

    def __hash__(self) -> int:
        return hash(self._key())

    def __repr__(self) -> str:
        fields = ", ".join(f"{k}={v!r}" for k, v in _canonical(self).items() if k != "kind")
        return f"Intent({self.kind!r}, {fields})"


def _canonical(intent: Intent) -> dict[str, Any]:
    return {
        "kind": intent.kind,
        "tickers": list(intent.tickers),
        "fiscal_years": list(intent.fiscal_years),
        "metrics": list(intent.metrics),
        "dimension": intent.dimension,
        "operation": intent.operation,
        "universe": intent.universe,
        "n": intent.n,
        "filing_year": intent.filing_year,
    }


def intent_shape(intent: Intent) -> str:
    """Returns the intent's shape: its kind, or ``kind.operation`` for compute, screen and
    compound.

    Args:
        intent: The intent.

    Returns:
        The shape name.
    """
    if intent.kind in OPERATIONS:
        return f"{intent.kind}.{intent.operation}"
    return intent.kind


def intent_id(intent: Intent) -> str:
    """Returns ``"int-"`` plus 12 hex digits of the SHA-1 of the intent's canonical JSON.

    Args:
        intent: The intent.

    Returns:
        The stable intent id.
    """
    blob = json.dumps(_canonical(intent), sort_keys=True, separators=(",", ":"))
    return "int-" + hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class Template:
    """One phrasebook template: ``id``, ``register``, ``shapes`` (tuple) and ``text``."""

    id: str
    register: str
    shapes: tuple[str, ...]
    text: str


@dataclass(frozen=True)
class Phrasebook:
    """The loaded phrasebook: ``templates``, a tuple of :class:`Template` in file order."""

    templates: tuple[Template, ...]

    def get(self, template_id: str) -> Template:
        """Returns the template with this id.

        Args:
            template_id: The template's id.

        Returns:
            The template.

        Raises:
            ValueError: When no template has the id.
        """
        for t in self.templates:
            if t.id == template_id:
                return t
        raise ValueError(f"unknown template {template_id!r}")


@dataclass(frozen=True)
class Lexicon:
    """The loaded lexicon.

    Attributes: ``companies``, ``metrics``, ``dimensions``, ``universes``, ``period_ends``.
    """

    companies: dict[str, dict[str, str]]
    metrics: dict[str, dict[str, Any]]
    dimensions: dict[str, tuple[str, ...]]
    universes: dict[str, tuple[str, ...]]
    period_ends: dict[str, dict[int, dt.date]]


def _read_yaml(path: str | Path) -> Any:
    try:
        return yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: not valid YAML: {exc}") from exc


def template_placeholders(text: str) -> set[str]:
    """Returns the placeholders a template text uses, checking its braces.

    Args:
        text: The template text.

    Returns:
        The placeholder names.

    Raises:
        ValueError: On an unknown placeholder or a stray ``{`` or ``}``.
    """
    names = set(_PLACEHOLDER_RE.findall(text))
    unknown = names - PLACEHOLDERS
    if unknown:
        raise ValueError(f"unknown placeholder(s) {sorted(unknown)} in {text!r}")
    rest = _PLACEHOLDER_RE.sub("", text)
    if "{" in rest or "}" in rest:
        raise ValueError(f"stray brace in {text!r}")
    return names


def load_phrasebook(path: str | Path) -> Phrasebook:
    """Loads and validates a phrasebook YAML file.

    Args:
        path: The YAML file.

    Returns:
        The phrasebook.

    Raises:
        ValueError: On any rule in the spec's criterion C4.
    """
    doc = _read_yaml(path)
    if not isinstance(doc, dict) or set(doc) != {"templates"} or not isinstance(
            doc["templates"], list):
        raise ValueError(f"{path}: expected a mapping with one key, 'templates' (a list)")
    templates: list[Template] = []
    seen: set[str] = set()
    for raw in doc["templates"]:
        if not isinstance(raw, dict) or set(raw) != {"id", "register", "shapes", "text"}:
            raise ValueError(f"{path}: a template needs exactly id, register, shapes, text: "
                             f"{raw!r}")
        tid, register, shapes, text = raw["id"], raw["register"], raw["shapes"], raw["text"]
        if not isinstance(tid, str) or not tid:
            raise ValueError(f"{path}: template id must be a non-empty str, got {tid!r}")
        if tid in seen:
            raise ValueError(f"{path}: duplicate template id {tid!r}")
        seen.add(tid)
        if register not in REGISTERS:
            raise ValueError(f"{path}: {tid}: unknown register {register!r}")
        if not isinstance(shapes, list) or not shapes:
            raise ValueError(f"{path}: {tid}: shapes must be a non-empty list")
        if not isinstance(text, str) or not text:
            raise ValueError(f"{path}: {tid}: text must be a non-empty str")
        used = template_placeholders(text)
        for shape in shapes:
            if shape not in _FILL:
                raise ValueError(f"{path}: {tid}: unknown shape {shape!r}")
            fillable, required = _FILL[shape]
            if used - fillable:
                raise ValueError(f"{path}: {tid}: {shape} cannot fill "
                                 f"{sorted(used - fillable)}")
            if required - used:
                raise ValueError(f"{path}: {tid}: {shape} requires {sorted(required - used)}")
        if len(set(shapes)) != len(shapes):
            raise ValueError(f"{path}: {tid}: repeated shape")
        templates.append(Template(id=tid, register=register, shapes=tuple(shapes), text=text))
    return Phrasebook(templates=tuple(templates))


def _surface(where: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where}: surface must be a non-empty str, got {value!r}")
    if "{" in value or "}" in value:
        raise ValueError(f"{where}: surface contains a brace: {value!r}")
    return value


def _mapping(where: str, value: object) -> Mapping[Any, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{where}: expected a mapping, got {value!r}")
    return value


def _phrases(where: str, value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{where}: expected a non-empty list of phrases")
    return tuple(_surface(where, v) for v in value)


def _ticker_key(where: str, key: object, seen: Mapping[str, Any]) -> str:
    if not isinstance(key, str) or not key.strip():
        raise ValueError(f"{where}: ticker key must be a non-empty str, got {key!r}")
    ticker = key.strip().upper()
    if ticker in seen:
        raise ValueError(f"{where}: repeated ticker {ticker!r}")
    return ticker


def _period_end(where: str, value: object) -> dt.date:
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value
    if not isinstance(value, str) or not _ISO_DATE_RE.fullmatch(value):
        raise ValueError(f"{where}: period end must be an ISO date, got {value!r}")
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{where}: period end must be an ISO date, got {value!r}") from exc


def load_lexicon(path: str | Path) -> Lexicon:
    """Loads and validates a lexicon YAML file.

    Args:
        path: The YAML file.

    Returns:
        The lexicon.

    Raises:
        ValueError: On any rule in the spec's criterion C5.
    """
    doc = _read_yaml(path)
    doc = {} if doc is None else _mapping(str(path), doc)
    unknown = set(doc) - set(_LEXICON_KEYS)
    if unknown:
        raise ValueError(f"{path}: unknown top-level key(s) {sorted(map(str, unknown))}")

    companies: dict[str, dict[str, str]] = {}
    for key, entry in _mapping("companies", doc.get("companies") or {}).items():
        ticker = _ticker_key("companies", key, companies)
        entry = _mapping(f"companies.{ticker}", entry)
        if set(entry) != {"short", "legal"}:
            raise ValueError(f"companies.{ticker}: needs exactly short and legal")
        companies[ticker] = {f: _surface(f"companies.{ticker}.{f}", entry[f])
                             for f in ("short", "legal")}

    metrics: dict[str, dict[str, Any]] = {}
    for mid, entry in _mapping("metrics", doc.get("metrics") or {}).items():
        where = f"metrics.{mid}"
        if not isinstance(mid, str) or not mid:
            raise ValueError(f"{where}: metric id must be a non-empty str")
        entry = _mapping(where, entry)
        if "label" not in entry or set(entry) - {"label", "synonyms", "shorthand"}:
            raise ValueError(f"{where}: needs label, and only synonyms and shorthand besides")
        forms: dict[str, Any] = {"label": _surface(f"{where}.label", entry["label"])}
        for f in ("synonyms", "shorthand"):
            values = entry.get(f) or []
            if not isinstance(values, list):
                raise ValueError(f"{where}.{f}: expected a list")
            forms[f] = tuple(_surface(f"{where}.{f}", v) for v in values)
        metrics[mid] = forms

    groups: dict[str, dict[str, tuple[str, ...]]] = {}
    for top in ("dimensions", "universes"):
        groups[top] = {}
        for gid, phrases in _mapping(top, doc.get(top) or {}).items():
            if not isinstance(gid, str) or not gid:
                raise ValueError(f"{top}: id must be a non-empty str, got {gid!r}")
            groups[top][gid] = _phrases(f"{top}.{gid}", phrases)

    period_ends: dict[str, dict[int, dt.date]] = {}
    for key, years in _mapping("period_ends", doc.get("period_ends") or {}).items():
        ticker = _ticker_key("period_ends", key, period_ends)
        ends: dict[int, dt.date] = {}
        for year, end in _mapping(f"period_ends.{ticker}", years).items():
            if not _is_int(year):
                raise ValueError(f"period_ends.{ticker}: year must be an int, got {year!r}")
            ends[year] = _period_end(f"period_ends.{ticker}.{year}", end)
        period_ends[ticker] = ends

    return Lexicon(companies=companies, metrics=metrics, dimensions=groups["dimensions"],
                   universes=groups["universes"], period_ends=period_ends)


# Entry ids that are two names for one measure, so they may share phrasings: each S4 ratio
# definition id of RATIO_DEFINITIONS and the mv_derived_metrics ratio it defines.
SAME_MEASURE_IDS: tuple[frozenset[str], ...] = (
    frozenset({"operating_margin", "OperatingMarginPct"}),
    frozenset({"net_margin", "NetMarginPct"}),
    frozenset({"rd_intensity", "RnDIntensityPct"}),
    frozenset({"current_ratio", "CurrentRatio"}),
)
# (section, entry id, phrasing) of the one-word phrasings that are true names of their entry
# though neither its id nor its label. Any other one-word phrasing is a generic word, such as
# "selling" or "taxes" split off by an unquoted comma in a YAML flow list.
ONE_WORD_PHRASINGS: frozenset[tuple[str, str, str]] = frozenset({
    ("metrics", "Revenue", "sales"),
    ("metrics", "Revenue", "turnover"),
    ("metrics", "Revenue", "rev"),
    ("metrics", "OperatingExpenses", "opex"),
    ("metrics", "Inventory", "inventories"),
    ("metrics", "IntangibleAssets", "intangibles"),
    ("metrics", "StockholdersEquity", "equity"),
    ("metrics", "StockRepurchases", "buybacks"),
    ("dimensions", "geography", "region"),
    ("dimensions", "professional_visualization", "proviz"),
})
_ONE_WORD_RE = re.compile(r"[A-Za-z]+")


def _norm_phrase(phrase: str) -> str:
    return " ".join(phrase.casefold().split())


def check_lexicon_phrasings(
    lexicon: Lexicon,
    *,
    same_measure: Sequence[frozenset[str]] = SAME_MEASURE_IDS,
    one_word: frozenset[tuple[str, str, str]] = ONE_WORD_PHRASINGS,
) -> None:
    """Checks that every phrasing of the lexicon names one thing, and names it fully.

    Two rules, applied to the metrics, the dimensions and the universes, each section on its
    own, comparing phrasings case-folded with whitespace collapsed:

    1. A phrasing belongs to one entry of its section. A metric's phrasings are its label,
       synonyms and shorthand. Two entries may share one only when ``same_measure`` names
       them as one measure.
    2. A one-word phrasing (letters only, not all upper case, so not an acronym such as
       ``EBITDA``) is the entry's own id or label, or is listed in ``one_word``.

    Both catch a phrase split by an unquoted comma in a YAML flow list, which the loader
    cannot see: ``[SG&A expense, selling, general and administrative expenses]`` yields
    "selling", and the EBITDA list yields "taxes" and the D&A phrasing.

    Args:
        lexicon: The loaded lexicon.
        same_measure: Sets of entry ids that are names of one measure.
        one_word: Allowed ``(section, entry id, phrasing)`` one-word phrasings, the phrasing
            case-folded.

    Raises:
        ValueError: Naming every offending phrasing and its entries.
    """
    aliases = {mid: group for group in same_measure for mid in group}
    sections: dict[str, dict[str, tuple[str, ...]]] = {
        "metrics": {mid: (e["label"], *e["synonyms"], *e["shorthand"])
                    for mid, e in lexicon.metrics.items()},
        "dimensions": dict(lexicon.dimensions),
        "universes": dict(lexicon.universes),
    }
    problems: list[str] = []
    for section, entries in sections.items():
        owners: dict[str, set[str]] = {}
        for eid, phrases in entries.items():
            names = {_norm_phrase(eid)}
            if section == "metrics":
                names.add(_norm_phrase(lexicon.metrics[eid]["label"]))
            for phrase in phrases:
                key = _norm_phrase(phrase)
                owners.setdefault(key, set()).add(eid)
                if (_ONE_WORD_RE.fullmatch(phrase) and not phrase.isupper()
                        and key not in names and (section, eid, key) not in one_word):
                    problems.append(f"{section}: one-word phrasing {phrase!r} of {eid!r} is "
                                    "neither its id nor its label nor an allowed one-word name")
        for key, eids in sorted(owners.items()):
            first = next(iter(eids))
            if len(eids) > 1 and not eids <= aliases.get(first, frozenset()):
                problems.append(f"{section}: phrasing {key!r} names {len(eids)} entries: "
                                f"{sorted(eids)}")
    if problems:
        raise ValueError("lexicon phrasings are ambiguous or generic:\n  "
                         + "\n  ".join(problems))


class Phrasing:
    """How one intent is worded: a template id and a company, metric and period form."""

    def __init__(
        self,
        template_id: str,
        *,
        company_form: str = "ticker",
        metric_form: str = "label",
        period_form: str = "fiscal",
    ) -> None:
        """Stores the template id and the three forms as attributes of the same names.

        Args:
            template_id: The phrasebook template's id.
            company_form: ``ticker``, ``short``, ``legal`` or ``none``.
            metric_form: ``label``, ``synonym:i`` or ``shorthand:i``.
            period_form: ``fy_short``, ``fiscal``, ``fiscal_year`` or ``year_ended``.
        """
        self.template_id = template_id
        self.company_form = company_form
        self.metric_form = metric_form
        self.period_form = period_form

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Phrasing):
            return NotImplemented
        return self._key() == other._key()

    def __hash__(self) -> int:
        return hash(self._key())

    def _key(self) -> tuple[str, str, str, str]:
        return (self.template_id, self.company_form, self.metric_form, self.period_form)

    def __repr__(self) -> str:
        return (f"Phrasing({self.template_id!r}, company_form={self.company_form!r}, "
                f"metric_form={self.metric_form!r}, period_form={self.period_form!r})")


def _rendered_years(intent: Intent) -> list[int]:
    years = list(intent.fiscal_years)
    if intent.filing_year is not None:
        years.append(intent.filing_year)
    return years


def _year_ended_ok(intent: Intent, lexicon: Lexicon) -> bool:
    if len(intent.tickers) != 1:
        return False
    ends = lexicon.period_ends.get(intent.tickers[0], {})
    return all(y in ends for y in _rendered_years(intent))


def _metric_surface(form: str, metric: str, lexicon: Lexicon) -> str:
    entry = lexicon.metrics.get(metric)
    if entry is None:
        raise ValueError(f"metric {metric!r} is not in the lexicon")
    if form == "label":
        return str(entry["label"])
    match = _METRIC_FORM_RE.fullmatch(form)
    if match is None:
        raise ValueError(f"unknown metric form {form!r}")
    values = entry["synonyms" if match.group(1) == "synonym" else "shorthand"]
    i = int(match.group(2))
    if i >= len(values):
        raise ValueError(f"metric form {form!r} out of range for {metric!r}")
    return str(values[i])


def _period_surface(form: str, year: int, intent: Intent, lexicon: Lexicon) -> str:
    if form == "fy_short":
        return f"FY{year % 100:02d}"
    if form == "fiscal":
        return f"fiscal {year}"
    if form == "fiscal_year":
        return f"fiscal year {year}"
    end = lexicon.period_ends[intent.tickers[0]][year]
    return f"the fiscal year ended {_MONTHS[end.month - 1]} {end.day}, {end.year}"


def render(intent: Intent, phrasing: Phrasing, lexicon: Lexicon, phrasebook: Phrasebook) -> str:
    """Renders one question by substituting the template's placeholders.

    Args:
        intent: The intent.
        phrasing: The template and forms.
        lexicon: Surface forms.
        phrasebook: Where the template text is looked up.

    Returns:
        The question.

    Raises:
        ValueError: When the phrasing does not fit the intent or the lexicon lacks an entry.
    """
    template = phrasebook.get(phrasing.template_id)
    shape = intent_shape(intent)
    if shape not in template.shapes:
        raise ValueError(f"template {template.id!r} does not phrase shape {shape!r}")

    values: dict[str, str] = {}
    cform = phrasing.company_form
    if intent.tickers:
        ticker = intent.tickers[0]
        if cform not in COMPANY_FORMS:
            raise ValueError(f"company form {cform!r} is not valid for an intent with a ticker")
        if ticker not in lexicon.companies:
            raise ValueError(f"company {ticker!r} is not in the lexicon")
        values["company"] = ticker if cform == "ticker" else lexicon.companies[ticker][cform]
    elif cform != "none":
        raise ValueError(f"company form {cform!r} is not valid for an intent with no ticker")

    surfaces = [_metric_surface(phrasing.metric_form, m, lexicon) for m in intent.metrics]
    values["metric"] = surfaces[0]
    if len(surfaces) > 1:
        values["metric_2"] = surfaces[1]

    pform = phrasing.period_form
    if pform not in PERIOD_FORMS:
        raise ValueError(f"unknown period form {pform!r}")
    if pform == "year_ended" and not _year_ended_ok(intent, lexicon):
        raise ValueError(f"year_ended needs a period end for every year of {intent!r}")
    years = intent.fiscal_years
    values["period"] = _period_surface(pform, years[0], intent, lexicon)
    values["first_period"] = values["period"]
    values["last_period"] = _period_surface(pform, years[-1], intent, lexicon)
    if intent.filing_year is not None:
        values["filing_period"] = _period_surface(pform, intent.filing_year, intent, lexicon)

    if intent.dimension is not None:
        if intent.dimension not in lexicon.dimensions:
            raise ValueError(f"dimension {intent.dimension!r} is not in the lexicon")
        values["dimension"] = lexicon.dimensions[intent.dimension][0]
    if intent.universe is not None:
        if intent.universe not in lexicon.universes:
            raise ValueError(f"universe {intent.universe!r} is not in the lexicon")
        values["universe"] = lexicon.universes[intent.universe][0]
    if intent.n is not None:
        values["n"] = str(intent.n)

    def _fill(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise ValueError(f"template {template.id!r}: {{{name}}} cannot be filled for "
                             f"{intent!r}")
        return values[name]

    return _PLACEHOLDER_RE.sub(_fill, template.text)


def axis_is_validation(key: str, *, share: float, salt: str) -> bool:
    """Returns whether ``key`` falls on the validation side of one hash axis.

    Args:
        key: An intent id or a template id.
        share: The validation share, in ``[0, 1]``.
        salt: The split salt.

    Returns:
        ``int(sha1(f"{salt}:{key}")[:8], 16) / 2**32 < share``.

    Raises:
        ValueError: When ``share`` is outside ``[0, 1]``.
    """
    if math.isnan(share) or not 0.0 <= share <= 1.0:
        raise ValueError(f"share must be in [0, 1], got {share!r}")
    digest = hashlib.sha1(f"{salt}:{key}".encode()).hexdigest()
    return int(digest[:8], 16) / 2**32 < share


@dataclass(frozen=True)
class PhrasedCase:
    """One phrased question.

    Attributes: ``case_id``, ``intent``, ``question``, ``template_id``, ``register``, ``forms``
    (``{"company", "metric", "period"}``), ``split``, ``intent_axis``, ``template_axis``.
    """

    case_id: str
    intent: Intent
    question: str
    template_id: str
    register: str
    forms: dict[str, str]
    split: str
    intent_axis: str
    template_axis: str


def _candidates(intent: Intent, phrasebook: Phrasebook,
                lexicon: Lexicon) -> list[tuple[Template, str, str, str]]:
    """The spec's candidate phrasings: matching templates x the forms each one varies."""
    shape = intent_shape(intent)
    entries = []
    for m in intent.metrics:
        if m not in lexicon.metrics:
            raise ValueError(f"metric {m!r} is not in the lexicon")
        entries.append(lexicon.metrics[m])
    n_syn = min(len(e["synonyms"]) for e in entries)
    n_short = min(len(e["shorthand"]) for e in entries)
    year_ended = _year_ended_ok(intent, lexicon)
    out = []
    for t in phrasebook.templates:
        if shape not in t.shapes:
            continue
        used = template_placeholders(t.text)
        if used & _COMPANY_PH:
            cforms: list[str] = list(COMPANY_FORMS)
        else:
            cforms = ["ticker"] if intent.tickers else ["none"]
        mforms = ["label"]
        if used & _METRIC_PH:
            mforms += [f"synonym:{i}" for i in range(n_syn)]
            mforms += [f"shorthand:{i}" for i in range(n_short)]
        if used & _PERIOD_PH:
            pforms = ["fy_short", "fiscal", "fiscal_year"] + (["year_ended"] if year_ended else [])
        else:
            pforms = ["fiscal"]
        out += [(t, c, m, p) for c in cforms for m in mforms for p in pforms]
    return out


def build_phrased_cases(
    intents: Sequence[Intent],
    phrasebook: Phrasebook,
    lexicon: Lexicon,
    *,
    per_intent: int,
    share: float,
    salt: str,
) -> list[PhrasedCase]:
    """Expands intents into phrased cases, ``per_intent`` phrasings each, with their split.

    Args:
        intents: The intents.
        phrasebook: Templates.
        lexicon: Surface forms.
        per_intent: Phrasings kept per intent (at least 1).
        share: The validation share of each split axis.
        salt: The split salt.

    Returns:
        The cases, sorted by ``case_id``.

    Raises:
        ValueError: On ``per_intent < 1``, a duplicate intent or an intent no template phrases.
    """
    if not _is_int(per_intent) or per_intent < 1:
        raise ValueError(f"per_intent must be an int >= 1, got {per_intent!r}")
    seen: set[str] = set()
    cases: list[PhrasedCase] = []
    for intent in intents:
        iid = intent_id(intent)
        if iid in seen:
            raise ValueError(f"duplicate intent {iid} ({intent!r})")
        seen.add(iid)
        candidates = _candidates(intent, phrasebook, lexicon)
        if not candidates:
            raise ValueError(f"no template phrases shape {intent_shape(intent)!r} ({intent!r})")
        keyed = []
        for t, c, m, p in candidates:
            key = f"{iid}|{t.id}|{c}|{m}|{p}"
            keyed.append((hashlib.sha1(key.encode("utf-8")).hexdigest(), t, c, m, p))
        keyed.sort(key=lambda k: k[0])
        intent_axis = "validation" if axis_is_validation(iid, share=share, salt=salt) else "dev"
        for digest, t, c, m, p in keyed[:per_intent]:
            phrasing = Phrasing(t.id, company_form=c, metric_form=m, period_form=p)
            template_axis = ("validation" if axis_is_validation(t.id, share=share, salt=salt)
                             else "dev")
            split = "validation" if "validation" in (intent_axis, template_axis) else "dev"
            cases.append(PhrasedCase(
                case_id=f"{iid}-{digest[:8]}",
                intent=intent,
                question=render(intent, phrasing, lexicon, phrasebook),
                template_id=t.id,
                register=t.register,
                forms={"company": c, "metric": m, "period": p},
                split=split,
                intent_axis=intent_axis,
                template_axis=template_axis,
            ))
    cases.sort(key=lambda case: case.case_id)
    return cases
