# The statement witness for v2 answer keys: a key's concept is checked against the filer's own
# presented statement line (SEC's Financial Statement Data Sets) or, for a metric no
# statement presents, against a reviewed list of note concepts. Both sources are independent
# of our concept_mapping, so a mapping change can no longer move a key without a reviewed
# reason. The witness gates a key; it never replaces it.
# Spec: docs/specs/key-statement-witness.md.

from __future__ import annotations

import copy
import datetime as dt
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, NotRequired, TypedDict

import yaml

from src.eval.core.case import EvalCase
from src.eval.core.graders.dispatch import grade_case
from src.eval.core.regrade import answer_from_payload
from src.eval.sec_reference.fsds import FsdsTables, period_value, statement_lines, submission

ANNUAL_FORMS = frozenset({"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"})
PRIMARY_STATEMENTS = ("BS", "IS", "CI", "CF", "EQ")
SECTIONS = ("current_assets", "current_liabilities")
COMBINES = ("one", "sum")

# Statuses (requirement 1), in the order the manifest lists them.
ACCEPTED = ("agree", "agree_other_element", "note_witnessed")
HELD = ("value_recast", "no_fsds")
BLOCKING = ("disagree", "ambiguous", "rejected_line", "note_unlisted")
STATUSES = (*ACCEPTED, *HELD, *BLOCKING)

# The fields of a case whose change re-grades it (requirement 10).
_GRADED_FIELDS = ("expected_value", "expected_rows", "alt_values", "alt_readings",
                  "expected_exclusions", "expect_not_meaningful", "unit")

_RULE_DEFAULTS: dict[str, Any] = {
    "parenthetical": False, "require": [], "exclude": [], "section": None, "prefer": [],
    "combine": "one", "substitutes": {},
}
_RULE_FIELDS = ("statements", "parenthetical", "include", "require", "exclude", "section",
                "prefer", "combine", "substitutes", "elements", "strip_qualifiers")
# The standard taxonomies an ``elements`` entry may name (statement-rules-by-element req. 1).
_STANDARD_PREFIXES = ("us-gaap", "ifrs-full", "srt", "dei")
# The standard elements a later report may key an extension-presented line on
# (key-fallback-witness requirement 3).
_LATER_STANDARD = ("us-gaap", "ifrs-full")
# The ``fallback`` marker of a witness read on the filer's own annual report (requirement 1).
OWN_REPORT_FALLBACK = "own_annual_report"
# A negating-qualifier cue and the characters that end its qualifier (req. 3).
_CUE_RE = re.compile(r"\b(?:excluding|exclusive of|not including|other than|except|net of)\b",
                     re.IGNORECASE)
_QUALIFIER_END_RE = re.compile(r"[,;()]")
_RESOLUTION_FIELDS = ("family", "cik", "fiscal_year", "concept", "value", "reading", "reason")
_CONCEPT_RE = re.compile(r"^[A-Za-z][\w.-]*:[A-Za-z_][\w.-]*$")
_CIK_RE = re.compile(r"^\d{10}$")

# The review record of a resolution outside the original list (reviewed-resolutions req. 1).
_REVIEW_FIELDS = ("by", "date", "bucket", "evidence")
_EVIDENCE_FIELDS = {"statement": ("kind", "adsh", "report", "line", "plabel"),
                    "note": ("kind", "adsh", "label")}
# Bucket -> the evidence kinds it admits.
_BUCKET_KINDS = {"rule_gap": ("statement",), "combined_line": ("statement",),
                 "definition": ("statement", "note"), "note_unlisted": ("note",),
                 "by_design": ("statement", "note")}
BUCKETS = tuple(_BUCKET_KINDS)
_ADSH_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# The live reviewer allow-list, located from the package like the other v2 config defaults.
DEFAULT_REVIEWERS = (Path(__file__).resolve().parents[3] / "configs" / "eval" / "v2"
                     / "reviewers.yaml")


class StatementRule(TypedDict):
    """One family's statement-line rule, as loaded and validated (requirement 2).

    Attributes:
        statements: FSDS statement codes searched, in preference order.
        parenthetical: Whether parenthetical lines are candidates too.
        include: Patterns; a candidate's label matches any of them.
        require: Patterns; a candidate's label matches all of them.
        exclude: Patterns; a candidate's label matches none of them.
        section: ``current_assets``, ``current_liabilities`` or None.
        prefer: Narrowing patterns, tried in order.
        combine: ``one`` or ``sum``.
        substitutes: concept -> ``{"code", "note"}``, a different measure than the family's.
        elements: Standard concepts whose lines are candidates whatever their label (present
            only when the file gives a non-empty list; statement-rules-by-element req. 1).
        strip_qualifiers: ``True`` when include/require/exclude see the label's head
            (``label_head``); present only when the file gives ``true``.
    """

    statements: list[str]
    parenthetical: bool
    include: list[str]
    require: list[str]
    exclude: list[str]
    section: str | None
    prefer: list[str]
    combine: str
    substitutes: dict[str, dict[str, str]]
    elements: NotRequired[list[str]]
    strip_qualifiers: NotRequired[bool]


class WitnessLine(TypedDict):
    """One statement line a rule selected, with its value for the key's period (requirement 1).

    Attributes:
        adsh: The filing.
        stmt: The FSDS statement code.
        report: The FSDS report number.
        line: The line number within the report.
        tag: The tag on the line.
        version: The tag's version (``us-gaap/2025``, or the adsh for an extension).
        concept: ``<taxonomy>:<tag>`` (``ext`` for an extension).
        plabel: The filer's label of the line.
        negating: Whether the statement shows the value negated.
        value: The consolidated value as tagged (not sign-flipped).
    """

    adsh: str
    stmt: str
    report: int
    line: int
    tag: str
    version: str
    concept: str
    plabel: str
    negating: bool
    value: float


class Witness(TypedDict):
    """The witness record of one key (requirement 1).

    Attributes:
        status: One of ``STATUSES``.
        family: The key's metric family.
        cik: The key's CIK (10 digits).
        fiscal_year: The key's fiscal year.
        adsh: The filing read.
        lines: The lines the rule selected.
        value: The witnessed value, or None.
        key_concept: The key's concept.
        key_value: The key's value.
        key_accession: The key's accession.
        substitution: ``{"code", "note", "concept"}`` when the key is a stated substitute.
        reason: The rejected line's label (``rejected_line``), else None.
        resolution: The reviewed resolution covering a blocked witness, else None.
        fallback: ``own_annual_report`` when an annual key whose filing has no FSDS data was
            read on the filer's own annual report (key-fallback-witness requirement 1);
            absent otherwise.
    """

    status: str
    family: str
    cik: str
    fiscal_year: int | None
    adsh: str
    lines: list[WitnessLine]
    value: float | None
    key_concept: str
    key_value: float | None
    key_accession: str
    substitution: dict[str, str] | None
    reason: str | None
    resolution: dict[str, Any] | None
    fallback: NotRequired[str]


class UniverseStatus(TypedDict):
    """Whether one filer is ranked in a cross-filer screen, and why not (requirement 5).

    Attributes:
        status: ``ranked``, ``definition_excluded``, ``metric_not_tagged`` or ``held``.
        reason: Why the filer is not ranked, or None.
        concept: The ranked concept, when one is known.
        value: The ranked value, when ranked.
        witness: The key's witness, when a key was given.
    """

    status: str
    reason: str | None
    concept: str | None
    value: float | None
    witness: Witness | None


class WitnessBlocked(ValueError):  # noqa: N818 - the name the spec's interface fixes
    """A key the statement disagrees with and no reviewed resolution covers (requirement 3)."""


# --------------------------------------------------------------------------- helpers


def _same(a: float | None, b: float | None) -> bool:
    """Whether two amounts are the same (FSDS four decimals against the XBRL value)."""
    if a is None or b is None:
        return False
    return abs(a - b) <= 1e-9 * max(abs(a), abs(b), 1.0)


def _concept(tag: str, version: str, adsh: str) -> str:
    """A line's ``<taxonomy>:<tag>``: ``ext`` for an extension, ``ifrs-full`` for IFRS."""
    if version == adsh:
        return f"ext:{tag}"
    if version.startswith("ifrs/"):
        return f"ifrs-full:{tag}"
    return f"{version.split('/', 1)[0]}:{tag}"


def _read_doc(path: str | os.PathLike[str], top: tuple[str, ...]) -> dict[str, Any]:
    """Reads a versioned YAML document whose top level has exactly the fields ``top``."""
    try:
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: not valid YAML: {exc}") from exc
    if not isinstance(doc, dict):
        raise ValueError(f"{path}: expected a mapping at the top level")
    unknown = sorted(set(doc) - set(top))
    if unknown:
        raise ValueError(f"{path}: unknown fields {unknown}")
    if doc.get("version") != 1 or isinstance(doc.get("version"), bool):
        raise ValueError(f"{path}: version must be 1, got {doc.get('version')!r}")
    return doc


def _patterns(family: str, field: str, value: Any) -> list[str]:
    """A rule's pattern list, every pattern compiled once to prove it is valid."""
    if not isinstance(value, list) or not all(isinstance(p, str) for p in value):
        raise ValueError(f"{family}.{field}: expected a list of patterns")
    for p in value:
        try:
            re.compile(p, re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"{family}.{field}: pattern {p!r} does not compile: {exc}") from exc
    return list(value)


def _rule(family: str, raw: Any) -> StatementRule:
    """One validated rule, defaults filled."""
    if not isinstance(raw, dict):
        raise ValueError(f"{family}: expected a mapping")
    unknown = sorted(set(raw) - set(_RULE_FIELDS))
    if unknown:
        raise ValueError(f"{family}: unknown fields {unknown}")
    for required in ("statements", "include"):
        if required not in raw:
            raise ValueError(f"{family}: missing {required}")
    statements = raw["statements"]
    if (not isinstance(statements, list) or not statements
            or any(s not in PRIMARY_STATEMENTS for s in statements)):
        raise ValueError(f"{family}.statements: expected codes of {PRIMARY_STATEMENTS}")
    include = _patterns(family, "include", raw["include"])
    if not include:
        raise ValueError(f"{family}.include: at least one pattern")
    merged = {**_RULE_DEFAULTS, **raw}
    if not isinstance(merged["parenthetical"], bool):
        raise ValueError(f"{family}.parenthetical: expected true or false")
    if merged["section"] is not None and merged["section"] not in SECTIONS:
        raise ValueError(f"{family}.section: expected one of {SECTIONS} or none")
    if merged["combine"] not in COMBINES:
        raise ValueError(f"{family}.combine: expected one of {COMBINES}")
    subs = merged["substitutes"]
    if not isinstance(subs, dict):
        raise ValueError(f"{family}.substitutes: expected a mapping")
    substitutes: dict[str, dict[str, str]] = {}
    for concept, entry in subs.items():
        if not isinstance(concept, str) or not _CONCEPT_RE.match(concept):
            raise ValueError(f"{family}.substitutes: bad concept {concept!r}")
        if (not isinstance(entry, dict) or set(entry) != {"code", "note"}
                or not all(isinstance(v, str) and v for v in entry.values())):
            raise ValueError(f"{family}.substitutes.{concept}: expected {{code, note}}")
        substitutes[concept] = {"code": entry["code"], "note": entry["note"]}
    rule: StatementRule = {
        "statements": list(statements), "parenthetical": merged["parenthetical"],
        "include": include, "require": _patterns(family, "require", merged["require"]),
        "exclude": _patterns(family, "exclude", merged["exclude"]),
        "section": merged["section"], "prefer": _patterns(family, "prefer", merged["prefer"]),
        "combine": merged["combine"], "substitutes": substitutes,
    }
    # The two optional fields load sparsely, so a rule using neither keeps its nine keys.
    if "elements" in raw:
        rule["elements"] = _elements(family, raw["elements"], substitutes)
    strip = raw.get("strip_qualifiers", False)
    if not isinstance(strip, bool):
        raise ValueError(f"{family}.strip_qualifiers: expected true or false")
    if strip:
        rule["strip_qualifiers"] = True
    return rule


def _elements(family: str, value: Any, substitutes: Mapping[str, Any]) -> list[str]:
    """A rule's validated ``elements`` list (statement-rules-by-element requirement 1)."""
    if not isinstance(value, list) or not value:
        raise ValueError(f"{family}.elements: expected a non-empty list of concepts")
    seen: set[str] = set()
    for concept in value:
        if not isinstance(concept, str) or not _CONCEPT_RE.match(concept):
            raise ValueError(f"{family}.elements: bad concept {concept!r}")
        if concept.split(":", 1)[0] not in _STANDARD_PREFIXES:
            raise ValueError(f"{family}.elements: {concept!r} is not a standard-taxonomy "
                             f"concept ({', '.join(_STANDARD_PREFIXES)})")
        if concept in seen:
            raise ValueError(f"{family}.elements: duplicate {concept!r}")
        if concept in substitutes:
            raise ValueError(f"{family}.elements: {concept!r} is also a stated substitute")
        seen.add(concept)
    return list(value)


def _search(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.IGNORECASE) is not None


def label_head(plabel: str) -> str:
    """A line label without its negating qualifiers.

    Spec: docs/specs/statement-rules-by-element.md, requirement 3. A negating qualifier runs
    from a cue (``excluding``, ``exclusive of``, ``not including``, ``other than``,
    ``except``, ``net of``) to the next ``,``, ``;``, ``(`` or ``)``, or the end.

    Args:
        plabel: The filer's line label (FSDS ``plabel``).

    Returns:
        The label with every negating qualifier removed, whitespace normalized.
    """
    kept: list[str] = []
    pos = 0
    while (cue := _CUE_RE.search(plabel, pos)) is not None:
        kept.append(plabel[pos:cue.start()])
        end = _QUALIFIER_END_RE.search(plabel, cue.end())
        if end is None:
            pos = len(plabel)
            break
        pos = end.start()  # the terminator is kept; the scan resumes at it
    kept.append(plabel[pos:])
    text = re.sub(r"\s+", " ", "".join(kept))
    text = re.sub(r" (?=[,;)])", "", text)
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"[\s,;:]+$", "", text)


def line_is_candidate(rule: StatementRule, plabel: str, concept: str) -> bool:
    """Whether a line's label or standard element makes it a candidate of a family's rule.

    Spec: docs/specs/statement-rules-by-element.md, requirements 2 and 4. Only the label and
    element test of selection; statements, parenthetical lines, sections and values are
    ``select_lines``'s.

    Args:
        rule: The family's rule.
        plabel: The filer's line label.
        concept: The line's ``<taxonomy>:<tag>`` (``ext`` for an extension).

    Returns:
        True when ``concept`` is one of the rule's ``elements``, or the label (its head when
        the rule strips qualifiers) matches the rule's include, require and exclude patterns.
    """
    if concept in rule.get("elements", []):
        return True
    text = label_head(plabel) if rule.get("strip_qualifiers") is True else plabel
    return (any(_search(p, text) for p in rule["include"])
            and all(_search(p, text) for p in rule["require"])
            and not any(_search(p, text) for p in rule["exclude"]))


# --------------------------------------------------------------------------- loaders


def load_statement_rules(path: str | os.PathLike[str]) -> dict[str, StatementRule]:
    """Loads and validates the statement-line rules file.

    Args:
        path: The YAML file (``configs/eval/v2/statement_lines.yaml``).

    Returns:
        family -> rule, every optional field filled with its default.

    Raises:
        ValueError: An unknown field, a bad statement code, section or combine value, a
            pattern that does not compile, or a version other than 1.
    """
    doc = _read_doc(path, ("version", "families"))
    families = doc.get("families")
    if not isinstance(families, dict):
        raise ValueError(f"{path}: families must be a mapping")
    return {str(f): _rule(str(f), raw) for f, raw in families.items()}


def load_note_concepts(path: str | os.PathLike[str]) -> dict[str, list[str]]:
    """Loads and validates the note-concept list.

    Args:
        path: The YAML file (``configs/eval/v2/note_concepts.yaml``).

    Returns:
        family -> concepts in ``<taxonomy>:<tag>`` form, in file order.

    Raises:
        ValueError: A malformed concept, a duplicate, an unknown field or a version other
            than 1.
    """
    doc = _read_doc(path, ("version", "families"))
    families = doc.get("families")
    if not isinstance(families, dict):
        raise ValueError(f"{path}: families must be a mapping")
    out: dict[str, list[str]] = {}
    for family, concepts in families.items():
        if not isinstance(concepts, list):
            raise ValueError(f"{path}: {family}: expected a list of concepts")
        for c in concepts:
            if not isinstance(c, str) or not _CONCEPT_RE.match(c):
                raise ValueError(f"{path}: {family}: {c!r} is not <taxonomy>:<tag>")
        if len(set(concepts)) != len(concepts):
            raise ValueError(f"{path}: {family}: duplicate concepts")
        out[str(family)] = list(concepts)
    return out


def load_resolutions(path: str | os.PathLike[str],
                     reviewers: Sequence[str] | None = None) -> list[dict[str, Any]]:
    """Loads and validates the reviewed resolutions.

    An entry may carry a ``review`` record (reviewed-resolutions requirement 1); it is
    returned normalized by ``validate_review`` (its ``date`` an ISO string).

    Args:
        path: The YAML file (``configs/eval/v2/key_resolutions.yaml``).
        reviewers: The allowed reviewer names; None reads ``configs/eval/v2/reviewers.yaml``.

    Returns:
        The entries, in file order.

    Raises:
        ValueError: A missing or unknown field, a bad reading, an empty reason, a duplicate
            key, a version other than 1, or an incomplete or malformed review record.
    """
    doc = _read_doc(path, ("version", "resolutions"))
    entries = doc.get("resolutions")
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        raise ValueError(f"{path}: resolutions must be a list")
    out: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for i, e in enumerate(entries):
        where = f"{path}: resolution {i}"
        if not isinstance(e, dict):
            raise ValueError(f"{where}: expected a mapping")
        missing = [f for f in _RESOLUTION_FIELDS if f not in e]
        unknown = sorted(set(e) - set(_RESOLUTION_FIELDS) - {"review"})
        if missing or unknown:
            raise ValueError(f"{where}: missing {missing}, unknown {unknown}")
        if not isinstance(e["family"], str) or not e["family"]:
            raise ValueError(f"{where}: family must be a name")
        if not isinstance(e["cik"], str) or not _CIK_RE.match(e["cik"]):
            raise ValueError(f"{where}: cik must be a 10-digit string")
        if not isinstance(e["fiscal_year"], int) or isinstance(e["fiscal_year"], bool):
            raise ValueError(f"{where}: fiscal_year must be an int")
        if not isinstance(e["concept"], str) or not _CONCEPT_RE.match(e["concept"]):
            raise ValueError(f"{where}: concept must be <taxonomy>:<tag>")
        if not isinstance(e["value"], int | float) or isinstance(e["value"], bool):
            raise ValueError(f"{where}: value must be a number")
        if e["reading"] not in ("key", "drop"):
            raise ValueError(f"{where}: reading must be key or drop")
        if not isinstance(e["reason"], str) or not e["reason"].strip():
            raise ValueError(f"{where}: reason must not be empty")
        ident = (e["family"], e["cik"], e["fiscal_year"], e["concept"])
        if ident in seen:
            raise ValueError(f"{where}: a second entry for {ident}")
        seen.add(ident)
        entry = {**e, "value": float(e["value"])}
        if "review" in e:
            try:
                entry["review"] = validate_review(e["review"], reviewers)
            except ValueError as exc:
                raise ValueError(f"{where}: {exc}") from exc
        out.append(entry)
    return out


def check_resolutions(current: Sequence[Mapping[str, Any]],
                      locked: Sequence[Mapping[str, Any]],
                      reviewers: Sequence[str] | None = None) -> int:
    """Checks that each live resolution is an original entry or carries a complete review.

    Spec: docs/specs/reviewed-resolutions.md, requirement 2. The original list may only
    shrink; any other entry needs a review record ``validate_review`` accepts.

    Args:
        current: The live entries.
        locked: The original (locked) entries.
        reviewers: The allowed reviewer names; None reads ``configs/eval/v2/reviewers.yaml``.

    Returns:
        The number of live entries.

    Raises:
        ValueError: A live entry that no original entry equals field for field and whose
            review record is missing or refused.
    """
    def equal(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
        if set(a) != set(b):
            return False
        return all(_same(float(a[f]), float(b[f])) if f == "value" else a[f] == b[f]
                   for f in a)

    for entry in current:
        if any(equal(entry, ref) for ref in locked):
            continue
        if "review" not in entry:
            raise ValueError(f"resolution not in the locked list and not reviewed (a review "
                             f"record is needed): {dict(entry)}")
        try:
            validate_review(entry["review"], reviewers)
        except ValueError as exc:
            raise ValueError(f"resolution not in the locked list, and its review is refused "
                             f"({exc}): {dict(entry)}") from exc
    return len(current)


def load_reviewers(path: str | os.PathLike[str]) -> list[str]:
    """Loads and validates the allow-list of maintainer reviewers.

    Spec: docs/specs/reviewed-resolutions.md, requirement 1.

    Args:
        path: The YAML file (``configs/eval/v2/reviewers.yaml``).

    Returns:
        The reviewer names, in file order.

    Raises:
        ValueError: An unknown field, a version other than 1, an empty list, or a name that
            is empty, not a string or repeated.
    """
    doc = _read_doc(path, ("version", "reviewers"))
    names = doc.get("reviewers")
    if not isinstance(names, list) or not names:
        raise ValueError(f"{path}: reviewers must be a non-empty list")
    out: list[str] = []
    for name in names:
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{path}: reviewer {name!r} must be a non-empty string")
        if name in out:
            raise ValueError(f"{path}: reviewer {name!r} is repeated")
        out.append(name)
    return out


def _review_date(value: Any) -> str:
    """A review date as an ISO ``YYYY-MM-DD`` string; a date-time or a non-date is refused."""
    if isinstance(value, dt.datetime):
        raise ValueError(f"review date {value!r} is a date-time, not a date")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, str) and _ISO_DATE_RE.match(value):
        try:
            return dt.date.fromisoformat(value).isoformat()
        except ValueError as exc:
            raise ValueError(f"review date {value!r} is not a real date") from exc
    raise ValueError(f"review date {value!r} is not a YYYY-MM-DD date")


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def validate_review(review: Mapping[str, Any],
                    reviewers: Sequence[str] | None = None) -> dict[str, Any]:
    """Validates and normalizes a resolution's review record.

    Spec: docs/specs/reviewed-resolutions.md, requirement 1.

    Args:
        review: The ``review`` mapping of a resolution entry.
        reviewers: The allowed reviewer names; None reads ``configs/eval/v2/reviewers.yaml``.

    Returns:
        A new dict equal to ``review`` with ``date`` as an ISO ``YYYY-MM-DD`` string.

    Raises:
        ValueError: An incomplete or malformed record, or a ``by`` not in ``reviewers``.
    """
    if not isinstance(review, Mapping):
        raise ValueError(f"review must be a mapping, got {type(review).__name__}")
    missing = [f for f in _REVIEW_FIELDS if f not in review]
    unknown = sorted(str(f) for f in set(review) - set(_REVIEW_FIELDS))
    if missing or unknown:
        raise ValueError(f"review: missing {missing}, unknown {unknown}")
    allowed = load_reviewers(DEFAULT_REVIEWERS) if reviewers is None else list(reviewers)
    by = review["by"]
    if not isinstance(by, str) or not by.strip() or by not in allowed:
        raise ValueError(f"review: by {by!r} is not a listed reviewer")
    date = _review_date(review["date"])
    bucket = review["bucket"]
    if not isinstance(bucket, str) or bucket not in _BUCKET_KINDS:
        raise ValueError(f"review: unknown bucket {bucket!r}")
    evidence = review["evidence"]
    if not isinstance(evidence, Mapping):
        raise ValueError("review: evidence must be a mapping")
    kind = evidence.get("kind")
    if not isinstance(kind, str) or kind not in _EVIDENCE_FIELDS:
        raise ValueError(f"review: unknown evidence kind {kind!r}")
    fields = _EVIDENCE_FIELDS[kind]
    missing = [f for f in fields if f not in evidence]
    unknown = sorted(str(f) for f in set(evidence) - set(fields))
    if missing or unknown:
        raise ValueError(f"review evidence: missing {missing}, unknown {unknown}")
    if not isinstance(evidence["adsh"], str) or not _ADSH_RE.match(evidence["adsh"]):
        raise ValueError(f"review evidence: malformed accession {evidence['adsh']!r}")
    if kind == "statement":
        for f in ("report", "line"):
            if not _positive_int(evidence[f]):
                raise ValueError(f"review evidence: {f} must be an int >= 1, "
                                 f"got {evidence[f]!r}")
    label = "plabel" if kind == "statement" else "label"
    if not isinstance(evidence[label], str) or not evidence[label].strip():
        raise ValueError(f"review evidence: {label} must not be empty")
    if kind not in _BUCKET_KINDS[bucket]:
        raise ValueError(f"review: bucket {bucket} does not admit {kind} evidence")
    return {**review, "date": date, "evidence": dict(evidence)}


def _covers(entry: Mapping[str, Any], w: Witness) -> bool:
    """Whether ``resolve`` attached ``entry`` to the witness (same identity and value)."""
    res = w["resolution"]
    return (res is not None and res["family"] == entry["family"]
            and res["cik"] == entry["cik"] and res["fiscal_year"] == entry["fiscal_year"]
            and res["concept"] == entry["concept"]
            and _same(float(res["value"]), float(entry["value"])))


def check_resolution_use(resolutions: Sequence[Mapping[str, Any]], witnesses: Iterable[Witness],
                         tables: FsdsTables) -> dict[str, Any]:
    """Checks that every resolution covers a blocked key of the build.

    Spec: docs/specs/reviewed-resolutions.md, requirement 3.

    Args:
        resolutions: The loaded resolutions.
        witnesses: Every witness the build kept.
        tables: The build's FSDS tables.

    Returns:
        ``n``, ``original``, ``reviewed`` and ``by_bucket`` (every bucket, zero included).

    Raises:
        ValueError: An entry covering no witness, evidence naming another filing, or a
            statement line the filing does not hold.
    """
    kept = list(witnesses)
    by_bucket = dict.fromkeys(BUCKETS, 0)
    reviewed = 0
    for entry in resolutions:
        ident = (entry["family"], entry["cik"], entry["fiscal_year"], entry["concept"])
        covered = [w for w in kept if _covers(entry, w)]
        if not covered:
            raise ValueError(f"resolution {ident} covers no blocked key of this build (stale "
                             f"or speculative: remove it)")
        review = entry.get("review")
        if review is None:
            continue
        evidence = review["evidence"]
        adsh = evidence["adsh"]
        if not any(w["adsh"] == adsh for w in covered):
            raise ValueError(f"resolution {ident}: evidence filing {adsh} is not the filing "
                             f"the covered witness read ({sorted({w['adsh'] for w in covered})})")
        if evidence["kind"] == "statement" and not any(
                ln["stmt"] in PRIMARY_STATEMENTS and ln["report"] == evidence["report"]
                and ln["line"] == evidence["line"] and ln["plabel"] == evidence["plabel"]
                for ln in statement_lines(tables, adsh)):
            raise ValueError(f"resolution {ident}: filing {adsh} holds no primary-statement "
                             f"line report {evidence['report']} line {evidence['line']} "
                             f"labelled {evidence['plabel']!r}")
        reviewed += 1
        by_bucket[review["bucket"]] += 1
    return {"n": len(resolutions), "original": len(resolutions) - reviewed,
            "reviewed": reviewed, "by_bucket": by_bucket}


# --------------------------------------------------------------------------- selection


def _in_section(lines: Sequence[Mapping[str, Any]], section: str | None,
                report: int, line: int) -> bool:
    """Whether a line of ``report`` lies inside ``section`` (requirement 2 step 1)."""
    if section is None:
        return True
    same = [ln for ln in lines if ln["report"] == report]
    def first(pattern: str) -> int | None:
        return next((ln["line"] for ln in same if _search(pattern, ln["plabel"])), None)
    if section == "current_assets":
        end = first("total current assets")
        return end is not None and line < end
    top = first("^total assets")
    end = first("total current liabilities")
    return top is not None and end is not None and top < line < end


def select_lines(tables: FsdsTables, rule: StatementRule, adsh: str, period_start: str | None,
                 period_end: str, unit: str) -> list[WitnessLine]:
    """Selects a filing's statement lines for one family, period and unit.

    Args:
        tables: The FSDS tables.
        rule: The family's rule.
        adsh: The filing to read.
        period_start: The period's start (ISO), or None for an instant.
        period_end: The period's end (ISO, XBRL's inclusive end).
        unit: The key's unit (companyfacts form, e.g. ``USD``, ``shares``, ``pure``).

    Returns:
        The lines the rule selects, in ``(report, line)`` order; empty when none.
    """
    all_lines = statement_lines(tables, adsh)
    candidates: list[WitnessLine] = []
    seen: set[tuple[str, str]] = set()
    for ln in all_lines:
        if ln["stmt"] not in rule["statements"]:
            continue
        if ln["inpth"] and not rule["parenthetical"]:
            continue
        label = ln["plabel"]
        concept = _concept(ln["tag"], ln["version"], adsh)
        if not line_is_candidate(rule, label, concept):
            continue
        if not _in_section(all_lines, rule["section"], ln["report"], ln["line"]):
            continue
        if (ln["tag"], ln["version"]) in seen:
            continue
        value = period_value(tables, adsh, ln["tag"], period_start, period_end, unit,
                             ln["version"])
        if value is None:
            continue
        seen.add((ln["tag"], ln["version"]))
        candidates.append({
            "adsh": ln["adsh"], "stmt": ln["stmt"], "report": ln["report"], "line": ln["line"],
            "tag": ln["tag"], "version": ln["version"],
            "concept": concept, "plabel": label,
            "negating": ln["negating"], "value": value,
        })
    for stmt in rule["statements"]:
        in_stmt = [c for c in candidates if c["stmt"] == stmt]
        if in_stmt:
            candidates = in_stmt
            break
    for pattern in rule["prefer"]:
        hits = [c for c in candidates if _search(pattern, c["plabel"])]
        if hits:
            candidates = hits
            break
    return candidates


def _witnessed_value(rule: StatementRule, lines: Sequence[WitnessLine]) -> float | None:
    """The sum (``combine: sum``) or the common value (``one``) of the lines, else None."""
    if not lines:
        return None
    if rule["combine"] == "sum":
        return float(sum(ln["value"] for ln in lines))
    first = lines[0]["value"]
    if all(_same(ln["value"], first) for ln in lines):
        return first
    return None


def later_standard_line(tables: FsdsTables, rule: StatementRule, adshs: Sequence[str],
                        period_start: str | None, period_end: str, unit: str,
                        own_value: float) -> dict[str, Any] | None:
    """The first later filing that presents a period's line on one standard element.

    Spec: docs/specs/key-fallback-witness.md, requirement 3. For each filing in order,
    ``select_lines`` is run; a filing whose selection is not exactly one line on a ``us-gaap``
    or ``ifrs-full`` element is passed over, and the first one whose selection is decides.

    Args:
        tables: The FSDS tables.
        rule: The family's rule.
        adshs: The later annual reports, in the order to try (latest first).
        period_start: The period's start (ISO), or None for an instant.
        period_end: The period's end (ISO).
        unit: The key's unit.
        own_value: The value the own report presents on its extension line.

    Returns:
        ``{"adsh", "concept", "value"}`` of the deciding filing's line when its value is the
        same as ``own_value``; None when it is not, or when no filing decides.
    """
    for adsh in adshs:
        if submission(tables, adsh) is None:
            continue
        lines = select_lines(tables, rule, adsh, period_start, period_end, unit)
        if len(lines) != 1 or lines[0]["concept"].split(":", 1)[0] not in _LATER_STANDARD:
            continue
        line = lines[0]
        if not _same(line["value"], own_value):
            return None
        return {"adsh": adsh, "concept": line["concept"], "value": line["value"]}
    return None


def _rejected_label(tables: FsdsTables, adsh: str, concept: str, period_start: str | None,
                    period_end: str, unit: str) -> str | None:
    """The label of the first primary-statement line on ``concept`` with a value, or None."""
    for ln in statement_lines(tables, adsh):
        if ln["stmt"] not in PRIMARY_STATEMENTS:
            continue
        if _concept(ln["tag"], ln["version"], adsh) != concept:
            continue
        if period_value(tables, adsh, ln["tag"], period_start, period_end, unit,
                        ln["version"]) is not None:
            return ln["plabel"]
    return None


def statement_witness(tables: FsdsTables, rules: Mapping[str, StatementRule],
                      notes: Mapping[str, Sequence[str]], family: str, key: Mapping[str, Any],
                      annual_adsh: str, annual_value: float | None = None) -> Witness:
    """Witnesses one key against the filer's statement.

    An annual key whose own accession has no FSDS submission is read on ``annual_adsh``
    when that one has (the own-report fallback, marked ``fallback``), under the same
    ``annual_value`` recast check as an interim key (docs/specs/key-fallback-witness.md,
    requirement 1).

    Args:
        tables: The FSDS tables.
        rules: The statement-line rules.
        notes: The note-concept list.
        family: The key's metric family.
        key: The key (an ``AnswerKey``).
        annual_adsh: The annual report the fiscal year is labelled by (S2).
        annual_value: The companyfacts value of the key's concept, unit and period in
            ``annual_adsh``, or None when that filing does not tag it.

    Returns:
        The witness record.

    Raises:
        ValueError: ``family`` has no rule.
    """
    if family not in rules:
        raise ValueError(f"no statement-line rule for family {family!r}")
    rule = rules[family]
    annual = key["form"] in ANNUAL_FORMS
    fallback = False
    if not annual:
        adsh = annual_adsh
    elif submission(tables, key["accession"]) is not None:
        adsh = key["accession"]
    elif annual_adsh != key["accession"] and submission(tables, annual_adsh) is not None:
        # The own-report fallback (key-fallback-witness requirement 1).
        adsh, fallback = annual_adsh, True
    else:
        adsh = key["accession"]
    concept = key["concept"]
    key_value = None if key["value"] is None else float(key["value"])
    sub = rule["substitutes"].get(concept)
    witness: Witness = {
        "status": "no_fsds", "family": family, "cik": key["cik"],
        "fiscal_year": key["fiscal_year"], "adsh": adsh, "lines": [], "value": None,
        "key_concept": concept, "key_value": key_value, "key_accession": key["accession"],
        "substitution": None if sub is None else {**sub, "concept": concept},
        "reason": None, "resolution": None,
    }
    if fallback:
        witness["fallback"] = OWN_REPORT_FALLBACK
    if submission(tables, adsh) is None:
        return witness
    start, end, unit = key["period_start"], key["period_end"], key["unit"]
    lines = select_lines(tables, rule, adsh, start, end, unit)
    value = _witnessed_value(rule, lines)
    witness["lines"] = lines
    witness["value"] = value
    if ((not annual or fallback) and annual_value is not None
            and not _same(annual_value, key_value)):
        witness["status"] = "value_recast"
    elif lines:
        if rule["combine"] == "one" and value is None:
            witness["status"] = "ambiguous"
        elif _same(value, key_value):
            on_line = any(ln["concept"] == concept for ln in lines)
            witness["status"] = "agree" if on_line else "agree_other_element"
        else:
            witness["status"] = "disagree"
    else:
        label = _rejected_label(tables, adsh, concept, start, end, unit)
        if label is not None:
            witness["status"], witness["reason"] = "rejected_line", label
        elif concept in notes.get(family, ()):
            witness["status"] = "note_witnessed"
        else:
            witness["status"] = "note_unlisted"
    return witness


# --------------------------------------------------------------------------- resolution


def resolve(witness: Witness, resolutions: Sequence[Mapping[str, Any]]) -> Witness:
    """Attaches the reviewed resolution that covers a blocked witness, if any.

    Args:
        witness: A witness record.
        resolutions: The reviewed resolutions.

    Returns:
        A copy of ``witness`` with ``resolution`` set to the covering entry or None.
    """
    out: Witness = {**witness, "resolution": None}  # type: ignore[typeddict-item]
    if witness["status"] not in BLOCKING:
        return out
    for entry in resolutions:
        if (entry["family"] == witness["family"] and entry["cik"] == witness["cik"]
                and entry["fiscal_year"] == witness["fiscal_year"]
                and entry["concept"] == witness["key_concept"]
                and _same(float(entry["value"]), witness["key_value"])):
            out["resolution"] = dict(entry)
            break
    return out


def _reading(w: Witness) -> str | None:
    """``key`` / ``drop`` for a resolved blocking witness, else None."""
    res = w["resolution"]
    return None if res is None else str(res["reading"])


def _accepted(w: Witness) -> bool:
    return w["status"] in ACCEPTED or (w["status"] in BLOCKING and _reading(w) == "key")


def _fallback_blocked(w: Witness) -> bool:
    """An unresolved blocking witness read on the own report (key-fallback-witness R2)."""
    return "fallback" in w and w["status"] in BLOCKING and w["resolution"] is None


def _held(w: Witness) -> bool:
    return (w["status"] in HELD or (w["status"] in BLOCKING and _reading(w) == "drop")
            or _fallback_blocked(w))


def select_key(tables: FsdsTables, rules: Mapping[str, StatementRule],
               notes: Mapping[str, Sequence[str]], resolutions: Sequence[Mapping[str, Any]],
               family: str, candidates: Sequence[Mapping[str, Any]], annual_adsh: str,
               annual_values: Mapping[str, float | None]) -> Witness:
    """Chooses the key of one filer, year and family from the mapping's candidate keys.

    Args:
        tables: The FSDS tables.
        rules: The statement-line rules.
        notes: The note-concept list.
        resolutions: The reviewed resolutions.
        family: The metric family.
        candidates: One key per candidate concept, in any order.
        annual_adsh: The annual report the fiscal year is labelled by.
        annual_values: concept -> its companyfacts value in ``annual_adsh`` (or None).

    Returns:
        The accepted candidate's witness, or a held one (the intent is then dropped).

    Raises:
        WitnessBlocked: No candidate is accepted or held, or accepted candidates disagree.
    """
    witnesses = [
        resolve(statement_witness(tables, rules, notes, family, k, annual_adsh,
                                  annual_values.get(k["concept"])), resolutions)
        for k in candidates
    ]
    accepted = [w for w in witnesses if _accepted(w)]
    if accepted:
        first = accepted[0]["key_value"]
        if not all(_same(w["key_value"], first) for w in accepted):
            raise WitnessBlocked(
                f"{family}: accepted candidates disagree: "
                + ", ".join(f"{w['key_concept']}={w['key_value']}" for w in accepted))
        rank = {s: i for i, s in enumerate(ACCEPTED)}
        return min(accepted, key=lambda w: (rank.get(w["status"], len(ACCEPTED)),
                                            w["key_concept"]))
    held = [w for w in witnesses if _held(w)]
    if held:
        order = {"value_recast": 0, "no_fsds": 1}
        return min(held, key=lambda w: (order.get(w["status"], 2), w["key_concept"]))
    detail = ", ".join(f"{w['key_concept']}: {w['status']}"
                       for w in sorted(witnesses, key=lambda w: w["key_concept"]))
    raise WitnessBlocked(f"{family} (cik {witnesses[0]['cik'] if witnesses else '?'}): "
                         f"no accepted or held candidate [{detail or 'no candidates'}]")


def universe_status(tables: FsdsTables, rules: Mapping[str, StatementRule],
                    notes: Mapping[str, Sequence[str]], resolutions: Sequence[Mapping[str, Any]],
                    family: str, adsh: str, period_start: str | None, period_end: str, unit: str,
                    key: Mapping[str, Any] | None = None,
                    annual_value: float | None = None) -> UniverseStatus:
    """Decides whether one filer is ranked in a cross-filer screen of ``family``.

    Args:
        tables: The FSDS tables.
        rules: The statement-line rules.
        notes: The note-concept list.
        resolutions: The reviewed resolutions.
        family: The screen's metric family.
        adsh: The filer's annual report for the screened fiscal year.
        period_start: The period's start, or None for an instant.
        period_end: The period's end.
        unit: The screen's unit.
        key: The filer's key, when the mapping offers one.
        annual_value: As for ``statement_witness``.

    Returns:
        The filer's status in the screen.
    """
    if key is not None:
        w = resolve(statement_witness(tables, rules, notes, family, key, adsh, annual_value),
                    resolutions)
        if _accepted(w):
            sub = w["substitution"]
            return {"status": "ranked" if sub is None else "definition_excluded",
                    "reason": None if sub is None else f"substitute:{sub['code']}",
                    "concept": w["key_concept"], "value": w["key_value"], "witness": w}
        if _reading(w) == "drop":
            reason = "resolved_drop"
        elif _fallback_blocked(w):
            reason = "fallback_blocked"
        else:
            reason = w["status"]
        return {"status": "held", "reason": reason, "concept": w["key_concept"],
                "value": None, "witness": w}
    if family not in rules:
        raise ValueError(f"no statement-line rule for family {family!r}")
    rule = rules[family]
    if submission(tables, adsh) is None:
        return {"status": "held", "reason": "no_fsds", "concept": None, "value": None,
                "witness": None}
    lines = select_lines(tables, rule, adsh, period_start, period_end, unit)
    if not lines:
        return {"status": "metric_not_tagged", "reason": None, "concept": None, "value": None,
                "witness": None}
    value = _witnessed_value(rule, lines)
    if rule["combine"] == "one" and value is None:
        return {"status": "definition_excluded", "reason": "ambiguous_lines", "concept": None,
                "value": None, "witness": None}
    concept = lines[0]["concept"] if len(lines) == 1 else None
    subs = rule["substitutes"]
    if all(ln["concept"] in subs for ln in lines):
        code = subs[lines[0]["concept"]]["code"]
        return {"status": "definition_excluded", "reason": f"substitute:{code}",
                "concept": concept, "value": value, "witness": None}
    return {"status": "ranked", "reason": None, "concept": concept, "value": value,
            "witness": None}


# --------------------------------------------------------------------------- manifest


def witness_manifest(witnesses: Iterable[Witness]) -> dict[str, Any]:
    """Counts a build's witness records for the suite manifest.

    Args:
        witnesses: Every key's witness.

    An unresolved blocking witness read on the own report is held, not a block
    (key-fallback-witness requirement 2).

    Returns:
        ``n``, ``by_status`` (every status, zero included), ``resolved``, ``held`` and
        ``fallback`` (``{"n", "held"}`` of the records read on the own report).

    Raises:
        WitnessBlocked: A blocking witness without a resolution or the fallback field.
    """
    items = list(witnesses)
    blocked = [w for w in items if w["status"] in BLOCKING and w["resolution"] is None
               and "fallback" not in w]
    if blocked:
        raise WitnessBlocked(
            f"{len(blocked)} unresolved blocking witness(es): "
            + "; ".join(f"{w['family']} cik {w['cik']} FY{w['fiscal_year']} "
                        f"{w['key_concept']}: {w['status']}" for w in blocked[:10]))
    by_status = dict.fromkeys(STATUSES, 0)
    for w in items:
        by_status[w["status"]] += 1
    held = sorted(
        ({"family": w["family"], "cik": w["cik"], "fiscal_year": w["fiscal_year"],
          "key_concept": w["key_concept"], "status": w["status"]}
         for w in items if w["status"] in HELD or _fallback_blocked(w)),
        key=lambda h: (h["family"], h["cik"],
                       -1 if h["fiscal_year"] is None else h["fiscal_year"],
                       h["key_concept"], h["status"]))
    fallbacks = [w for w in items if "fallback" in w]
    return {"n": len(items), "by_status": by_status,
            "resolved": sum(1 for w in items if w["resolution"] is not None), "held": held,
            "fallback": {"n": len(fallbacks),
                         "held": sum(1 for w in fallbacks if w["status"] == "value_recast"
                                     or (w["status"] in BLOCKING and _reading(w) == "drop")
                                     or _fallback_blocked(w))}}


def key_change_regrade(
    old_cases: Sequence[EvalCase],
    new_cases: Sequence[EvalCase],
    runs: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """Re-grades every arm's stored answers on the cases whose expected answer changed.

    Args:
        old_cases: The suite's cases under the old keys.
        new_cases: The same cases under the new keys.
        runs: arm -> its stored results (``case_id``, ``produced``).

    Returns:
        arm -> ``changed``, ``before``, ``after`` and ``transitions``.

    Raises:
        ValueError: The two case lists differ in ids, or an arm lacks a changed case.
    """
    old = {c.id: c for c in old_cases}
    new = {c.id: c for c in new_cases}
    if set(old) != set(new):
        raise ValueError(f"the case lists differ in ids: {sorted(set(old) ^ set(new))[:10]}")
    changed = sorted(cid for cid in old
                     if any(getattr(old[cid], f) != getattr(new[cid], f) for f in _GRADED_FIELDS))
    out: dict[str, dict[str, Any]] = {}
    for arm, results in runs.items():
        produced = {r["case_id"]: r["produced"] for r in results}
        missing = [cid for cid in changed if cid not in produced]
        if missing:
            raise ValueError(f"arm {arm!r} has no stored result for changed case {missing[0]!r}")
        before: dict[str, str] = {}
        after: dict[str, str] = {}
        transitions: dict[str, int] = {}
        for cid in changed:
            answer = answer_from_payload(produced[cid])
            before[cid] = grade_case(old[cid], answer).grade.value
            after[cid] = grade_case(new[cid], answer).grade.value
            if before[cid] != after[cid]:
                move = f"{before[cid]} -> {after[cid]}"
                transitions[move] = transitions.get(move, 0) + 1
        out[arm] = {"changed": list(changed), "before": before, "after": after,
                    "transitions": transitions}
    return out


# --------------------------------------------------------------------------- definitions


def load_mapping_substitutes(
        path: str | os.PathLike[str]) -> dict[str, dict[str, dict[str, str]]]:
    """The stated substitutes of the concept mapping, with the note each one states.

    Spec: docs/specs/key-currency-and-definition.md, requirement 3. Reads the ``substitutes``
    and ``substitutions`` sections of ``configs/concept_mapping/concept_family_coverage.yaml``
    (a code, or ``{code, note}`` where a code states another note).

    Args:
        path: The concept-family coverage file.

    Returns:
        family -> concept -> ``{"code", "note"}``.

    Raises:
        ValueError: A code with no note, a malformed entry or concept.
    """
    try:
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: not valid YAML: {exc}") from exc
    if not isinstance(doc, dict):
        raise ValueError(f"{path}: expected a mapping at the top level")
    notes = doc.get("substitutions") or {}
    families = doc.get("substitutes") or {}
    if not isinstance(notes, dict) or not isinstance(families, dict):
        raise ValueError(f"{path}: substitutions and substitutes must be mappings")
    out: dict[str, dict[str, dict[str, str]]] = {}
    for family, entries in families.items():
        if not isinstance(family, str) or not family or not isinstance(entries, dict):
            raise ValueError(f"{path}: substitutes.{family!r}: expected a mapping")
        found: dict[str, dict[str, str]] = {}
        for concept, entry in entries.items():
            if not isinstance(concept, str) or not _CONCEPT_RE.match(concept):
                raise ValueError(f"{path}: substitutes.{family}: bad concept {concept!r}")
            if isinstance(entry, str) and entry:
                note = notes.get(entry)
                if not isinstance(note, str) or not note.strip():
                    raise ValueError(f"{path}: substitutes.{family}.{concept}: code {entry!r} "
                                     f"has no note in substitutions")
                found[concept] = {"code": entry, "note": note}
            elif (isinstance(entry, dict) and set(entry) == {"code", "note"}
                  and all(isinstance(v, str) and v for v in entry.values())):
                found[concept] = {"code": entry["code"], "note": entry["note"]}
            else:
                raise ValueError(f"{path}: substitutes.{family}.{concept}: expected a code "
                                 f"or {{code, note}}")
        out[family] = found
    return out


def merge_mapping_substitutes(
        rules: Mapping[str, StatementRule],
        substitutes: Mapping[str, Mapping[str, Mapping[str, str]]]) -> dict[str, StatementRule]:
    """The statement-line rules with the mapping's stated substitutes added.

    Spec: docs/specs/key-currency-and-definition.md, requirement 3. A rule's own substitute
    entry wins; a family without a rule is ignored; the inputs are not changed.

    Args:
        rules: The statement-line rules.
        substitutes: ``load_mapping_substitutes``'s result.

    Returns:
        A new rules mapping.

    Raises:
        ValueError: A mapping substitute that its family's rule lists in ``elements``.
    """
    out: dict[str, StatementRule] = {}
    for family, rule in rules.items():
        stated = substitutes.get(family, {})
        elements = set(rule.get("elements", []))
        clash = sorted(c for c in stated if c in elements)
        if clash:
            raise ValueError(f"{family}: mapping substitute(s) {clash} are listed in the "
                             f"statement-line rule's elements")
        merged = copy.deepcopy(dict(rule))
        merged["substitutes"] = {**{c: dict(e) for c, e in stated.items()},
                                 **copy.deepcopy(dict(rule["substitutes"]))}
        out[family] = merged  # type: ignore[assignment]
    return out


def load_element_fit(
        path: str | os.PathLike[str],
        reviewers: Sequence[str] | None = None) -> dict[str, dict[str, dict[str, Any]]]:
    """Loads the reviewed list of standard elements outside a family's measure.

    Spec: docs/specs/key-currency-and-definition.md, requirement 4.

    Args:
        path: The element-fit file (``configs/eval/v2/element_fit.yaml``).
        reviewers: The allowed reviewer names; None reads ``configs/eval/v2/reviewers.yaml``.

    Returns:
        family -> concept -> ``{"definition", "review"}`` (the review normalized by
        ``validate_review``).

    Raises:
        ValueError: An unknown field, a version other than 1, a concept that is not a
            standard-taxonomy concept, an empty definition, or an invalid review record.
    """
    doc = _read_doc(path, ("version", "families"))
    if "families" not in doc:
        raise ValueError(f"{path}: missing families")
    families = doc["families"]
    if not isinstance(families, dict):
        raise ValueError(f"{path}: families must be a mapping")
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for family, entries in families.items():
        if not isinstance(family, str) or not re.match(r"^[A-Za-z]\w*$", family):
            raise ValueError(f"{path}: bad family {family!r}")
        if not isinstance(entries, dict):
            raise ValueError(f"{path}: {family}: expected a mapping of concepts")
        found: dict[str, dict[str, Any]] = {}
        for concept, entry in entries.items():
            if not isinstance(concept, str) or not _CONCEPT_RE.match(concept):
                raise ValueError(f"{path}: {family}: bad concept {concept!r}")
            if concept.split(":", 1)[0] not in _STANDARD_PREFIXES:
                raise ValueError(f"{path}: {family}: {concept!r} is not a standard element "
                                 f"({', '.join(_STANDARD_PREFIXES)})")
            if not isinstance(entry, dict) or set(entry) != {"definition", "review"}:
                raise ValueError(f"{path}: {family}.{concept}: expected exactly "
                                 f"definition and review")
            definition = entry["definition"]
            if not isinstance(definition, str) or not definition.strip():
                raise ValueError(f"{path}: {family}.{concept}: empty definition")
            try:
                review = validate_review(entry["review"], reviewers)
            except ValueError as exc:
                raise ValueError(f"{path}: {family}.{concept}: {exc}") from exc
            found[concept] = {"definition": definition, "review": review}
        out[family] = found
    return out


def element_fit_status(status: UniverseStatus, family: str,
                       element_fit: Mapping[str, Mapping[str, Any]]) -> UniverseStatus:
    """A screen member's status after the element-definition check (rule K-R2).

    Spec: docs/specs/key-currency-and-definition.md, requirement 4.

    Args:
        status: The member's status (``universe_status`` or ``KeyWitness.member``).
        family: The screen's metric family.
        element_fit: ``load_element_fit``'s result.

    Returns:
        ``definition_excluded`` with reason ``element_outside_family`` for a ranked status
        whose concept is listed under ``family``; otherwise ``status`` unchanged.
    """
    concept = status["concept"]
    if (status["status"] == "ranked" and concept is not None
            and concept in element_fit.get(family, {})):
        return {**status, "status": "definition_excluded", "reason": "element_outside_family"}
    return status
