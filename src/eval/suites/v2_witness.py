# The statement witness in the v2 suite build: every key input is chosen by select_key from
# the concepts the mapping offers, and every screen member's membership is decided by
# universe_status, so a mapping error can no longer move a key and the product together.
# Spec: docs/specs/key-statement-witness.md (requirements 3, 5, 6 and 9; "Not locked here").
# Debt and DeferredRevenue are keyed by their stated definitions instead, and net and
# comprehensive income screen members by their attribution (docs/specs/key-definitions.md).

from __future__ import annotations

import datetime as dt
import os
import re
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from src.eval.keys.core import (
    ANNUAL_FORMS,
    AnswerKey,
    KeyAmbiguousError,
    KeyNotFoundError,
    UnitMismatchError,
    as_reported_key,
    labelled_fiscal_year_key,
    latest_filed_key,
    own_period_end,
)
from src.eval.keys.key_definitions import (
    BLOCKING_STATUSES,
    CONSOLIDATED_ELEMENTS,
    PARENT_ELEMENTS,
    DefinedKey,
    attribution_member,
    attribution_status,
    case_readings,
    debt_key,
    deferred_revenue_key,
    member_status,
)
from src.eval.keys.statement_witness import (
    BLOCKING,
    HELD,
    StatementRule,
    UniverseStatus,
    Witness,
    WitnessBlocked,
    check_resolution_use,
    check_resolutions,
    element_fit_status,
    later_standard_line,
    load_element_fit,
    load_mapping_substitutes,
    load_note_concepts,
    load_resolutions,
    load_statement_rules,
    merge_mapping_substitutes,
    select_key,
    universe_status,
    witness_manifest,
)
from src.eval.sec_reference.fsds import FsdsTables, load_tables

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = REPO_ROOT / "configs" / "eval" / "v2"
DEFAULT_RULES = CONFIG_DIR / "statement_lines.yaml"
DEFAULT_NOTES = CONFIG_DIR / "note_concepts.yaml"
DEFAULT_RESOLUTIONS = CONFIG_DIR / "key_resolutions.yaml"
# A pinned copy of the spec's locked list (test/spec/key-statement-witness/fixtures), so the
# runtime check does not read test/spec; a unit test keeps the two equal.
DEFAULT_LOCKED_RESOLUTIONS = CONFIG_DIR / "key_resolutions.locked.yaml"
# The concept mapping whose stated substitutes join the rules' own, and the reviewed list of
# standard elements outside a family's measure (key-currency-and-definition reqs. 3 and 4).
DEFAULT_MAPPING = REPO_ROOT / "configs" / "concept_mapping" / "concept_family_coverage.yaml"
DEFAULT_ELEMENT_FIT = CONFIG_DIR / "element_fit.yaml"

# How many blocked keys a WitnessBlocked message names before it only counts.
_BLOCKED_SHOWN = 25
# A duration screen without a key reads the year that ends at the filing's period (the
# FSDS period is month-end rounded; fsds_period rounds the length to whole quarters).
_YEAR_DAYS = 364
_KEY_ERRORS = (KeyNotFoundError, UnitMismatchError, KeyAmbiguousError)
# The families keyed by a stated definition rather than a mapped concept (key-definitions
# requirements 2 and 3), and the drop reason of each non-keyed, non-blocking status.
DEFINED_FAMILIES: dict[str, Callable[..., DefinedKey]] = {
    "Debt": debt_key, "DeferredRevenue": deferred_revenue_key}
_DEFINED_DROPS = {"no_fsds": "fsds_not_published", "not_presented": "metric_not_tagged"}
# The reason a key input or member no FSDS filing witnesses is excluded with
# (key-fallback-witness requirement 4), and the fields of its gap record.
FSDS_NOT_PUBLISHED = "fsds_not_published"
_GAP_FIELDS = ("family", "cik", "fiscal_year", "accession")
_QUARTER_RE = re.compile(r"^(\d{4})q([1-4])$")
# The annual-length bounds of a companyfacts duration (52/53-week years in, quarters out),
# as keys.core applies them.
_ANNUAL_MIN_DAYS, _ANNUAL_MAX_DAYS = 350, 380


class WitnessDrop(Exception):  # noqa: N818 - mirrors IntentDroppedError; args[0] is a reason
    """A key input the witness does not key; ``args[0]`` is the drop reason."""


def later_annual_reports(cf: dict, labels: Mapping[str, int], year: int) -> list[str]:
    """The original annual reports labelled after ``year``, the latest label first.

    Spec: docs/specs/key-fallback-witness.md, requirement 3. Each label's report is the one
    ``KeyWitness.annual_accession`` returns for it.

    Args:
        cf: The filer's companyfacts document.
        labels: The document's ``annual_filing_labels``.
        year: The fiscal-year label of the own report.

    Returns:
        One accession per label greater than ``year``, by label descending.
    """
    reports = _annual_reports(cf, labels)
    return [reports[y] for y in sorted(reports, reverse=True) if y > year]


def _annual_reports(cf: dict, labels: Mapping[str, int]) -> dict[int, str]:
    """Fiscal-year label -> the original annual report labelled so.

    Among the accessions labelled ``L`` that appear on an annual-form entry of ``cf``, the
    least by (is an amendment, earliest ``filed`` of its entries, accession).
    """
    first: dict[str, tuple[str, int]] = {}
    for concepts in cf.get("facts", {}).values():
        for node in concepts.values():
            for entries in node.get("units", {}).values():
                for e in entries:
                    if e.get("form") not in ANNUAL_FORMS or e.get("accn") not in labels:
                        continue
                    rank = (e["filed"], 1 if str(e["form"]).endswith("/A") else 0)
                    if e["accn"] not in first or rank < first[e["accn"]]:
                        first[e["accn"]] = rank
    by_year: dict[int, tuple[tuple[str, int], str]] = {}
    for accn, rank in first.items():
        label = labels[accn]
        if label not in by_year or (rank[1], rank[0], accn) < (
                by_year[label][0][1], by_year[label][0][0], by_year[label][1]):
            by_year[label] = (rank, accn)
    return {y: a for y, (_r, a) in by_year.items()}


def _quarter_index(name: str) -> int:
    """A quarter name's position on a consecutive scale (``year * 4 + quarter - 1``)."""
    m = _QUARTER_RE.match(name) if isinstance(name, str) else None
    if m is None:
        raise ValueError(f"not an FSDS quarter name (<yyyy>q<n>, n in 1-4): {name!r}")
    return int(m.group(1)) * 4 + int(m.group(2)) - 1


def _quarter_name(index: int) -> str:
    return f"{index // 4}q{index % 4 + 1}"


def fsds_coverage(quarters: Sequence[str]) -> dict[str, Any]:
    """The FSDS quarters a build read: count, first, last and the missing ones between.

    Spec: docs/specs/key-fallback-witness.md, requirement 5.

    Args:
        quarters: Quarter names ``<yyyy>q<n>`` (``n`` in 1-4), in any order.

    Returns:
        ``{"n", "first", "last", "missing"}``.

    Raises:
        ValueError: A name not of that form, or a repeated name.
    """
    indices = [_quarter_index(q) for q in quarters]
    repeated = sorted(_quarter_name(i) for i, n in Counter(indices).items() if n > 1)
    if repeated:
        raise ValueError(f"repeated FSDS quarter(s): {repeated}")
    if not indices:
        return {"n": 0, "first": None, "last": None, "missing": []}
    given = set(indices)
    lo, hi = min(given), max(given)
    return {"n": len(given), "first": _quarter_name(lo), "last": _quarter_name(hi),
            "missing": [_quarter_name(i) for i in range(lo + 1, hi) if i not in given]}


def fsds_gap_manifest(coverage: Mapping[str, Any],
                      gaps: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """The manifest's ``fsds`` block: the coverage and the keys no FSDS filing witnesses.

    Spec: docs/specs/key-fallback-witness.md, requirement 5.

    Args:
        coverage: ``fsds_coverage`` of the build's quarters.
        gaps: ``{"family", "cik", "fiscal_year", "accession"}`` records, repeats allowed.

    Returns:
        ``{"coverage", "not_published": {"n", "by_family", "keys"}}``.

    Raises:
        ValueError: A gap with a missing or extra field.
    """
    distinct: dict[tuple[Any, ...], dict[str, Any]] = {}
    for gap in gaps:
        missing = [f for f in _GAP_FIELDS if f not in gap]
        extra = sorted(str(f) for f in set(gap) - set(_GAP_FIELDS))
        if missing or extra:
            raise ValueError(f"FSDS gap {dict(gap)}: missing {missing}, extra {extra}")
        ident = tuple(gap[f] for f in _GAP_FIELDS)
        distinct.setdefault(ident, {f: gap[f] for f in _GAP_FIELDS})
    keys = [distinct[k] for k in sorted(distinct)]
    by_family = Counter(g["family"] for g in keys)
    return {"coverage": coverage,
            "not_published": {"n": len(keys), "by_family": dict(sorted(by_family.items())),
                              "keys": keys}}


def _same_amount(a: float, b: float) -> bool:
    """Whether two amounts are the same (key-statement-witness's equality)."""
    return abs(a - b) <= 1e-9 * max(abs(a), abs(b), 1.0)


def _annual_starts(cf: dict, concept: str, unit: str, end: str) -> list[str]:
    """The distinct starts of ``concept``'s annual-length entries ending at ``end``."""
    tax, _, tag = concept.partition(":")
    entries = cf.get("facts", {}).get(tax, {}).get(tag, {}).get("units", {}).get(unit, [])
    last = dt.date.fromisoformat(end)
    starts = {e["start"] for e in entries
              if e.get("end") == end and e.get("start") is not None
              and _ANNUAL_MIN_DAYS <= (last - dt.date.fromisoformat(e["start"])).days
              <= _ANNUAL_MAX_DAYS}
    return sorted(starts)


def _drop_reason(exc: Exception) -> str:
    if isinstance(exc, KeyNotFoundError):
        return "no_key"
    if isinstance(exc, UnitMismatchError):
        return "unit_mismatch"
    return "ambiguous"


def _is_block(found: object) -> bool:
    """Whether a memoized ``fy_key`` result is a block (not a drop or a key)."""
    return isinstance(found, WitnessDrop) and found.args[:1] == ("witness_blocked",)


def _index(tables: FsdsTables) -> dict[str, FsdsTables]:
    """The rows of each filing, so each witness reads one filing's rows only."""
    out: dict[str, FsdsTables] = {}
    for name in ("sub", "num", "pre"):
        for row in tables[name]:
            part = out.setdefault(row["adsh"], {"sub": [], "num": [], "pre": [], "tag": []})
            part[name].append(row)  # type: ignore[literal-required]
    return out


class KeyWitness:
    """The witness inputs of one build and what it witnessed.

    Attributes:
        rules: The statement-line rules.
        notes: The note-concept list.
        resolutions: The reviewed resolutions.
        element_fit: family -> standard element -> its reviewed entry: the elements whose
            definition is outside the family's measure (key-currency-and-definition R4).
        candidates: ``(cik, fiscal year, family)`` -> further concepts the mapping offers,
            beside the inventory's own concept.
        witnesses: Every chosen or held key's witness, by family, CIK, year and concept.
        blocked: A message for each key input the witness blocked.
    """

    def __init__(self, tables: FsdsTables, rules: Mapping[str, StatementRule],
                 notes: Mapping[str, Sequence[str]],
                 resolutions: Sequence[Mapping[str, Any]],
                 candidates: Mapping[tuple[str, int, str], Sequence[str]] | None = None,
                 quarters: Sequence[str] = (),
                 element_fit: Mapping[str, Mapping[str, Any]] | None = None) -> None:
        """Builds the witness over loaded inputs.

        Args:
            tables: The FSDS tables covering the corpus's filings.
            rules: The statement-line rules.
            notes: The note-concept list.
            resolutions: The reviewed resolutions.
            candidates: ``(cik, fiscal year, family)`` -> further candidate concepts.
            quarters: The names of the FSDS quarter directories the tables were read from
                (``fsds_manifest``'s coverage; key-fallback-witness requirement 5).
            element_fit: ``load_element_fit``'s result; None is an empty list.
        """
        self._tables_all = tables
        self._by_adsh = _index(tables)
        self._empty: FsdsTables = {"sub": [], "num": [], "pre": [], "tag": []}
        self.rules = rules
        self.notes = notes
        self.resolutions = list(resolutions)
        self.element_fit: Mapping[str, Mapping[str, Any]] = dict(element_fit or {})
        self.candidates = {k: list(v) for k, v in (candidates or {}).items()}
        self.witnesses: dict[tuple[Any, ...], Witness] = {}
        self.blocked: list[str] = []
        # (cik, family, fiscal year) -> the value of a key a reviewed resolution dropped
        # (grading-dont-care-and-ties requirement 2).
        self.resolved_drops: dict[tuple[str, str, int], float] = {}
        self._chosen: dict[tuple[str, str, int], tuple[AnswerKey, str, float | None]
                           | WitnessDrop] = {}
        # CIK (10 digits) -> fiscal-year label -> the original annual report's accession.
        self._annual: dict[str, dict[int, str]] = {}
        # The blocks met in the open provisional scope, or None outside one.
        self._scope: list[str] | None = None
        # (family, cik, accession, period end) -> the defined key built from that filing.
        self.defined: dict[tuple[str, str, str, str], DefinedKey] = {}
        self.quarters = list(quarters)
        # The key inputs and members no FSDS filing witnesses (requirement 4).
        self.gaps: list[dict[str, Any]] = []

    @classmethod
    def from_paths(cls, fsds_dirs: Iterable[str | os.PathLike[str]],
                   rules: str | os.PathLike[str] = DEFAULT_RULES,
                   notes: str | os.PathLike[str] = DEFAULT_NOTES,
                   resolutions: str | os.PathLike[str] = DEFAULT_RESOLUTIONS,
                   candidates: Mapping[tuple[str, int, str], Sequence[str]] | None = None,
                   locked: str | os.PathLike[str] = DEFAULT_LOCKED_RESOLUTIONS,
                   mapping: str | os.PathLike[str] = DEFAULT_MAPPING,
                   element_fit: str | os.PathLike[str] = DEFAULT_ELEMENT_FIT,
                   ) -> KeyWitness:
        """Loads the FSDS quarters and the three committed lists.

        The resolutions are checked against the pinned original list first
        (docs/specs/reviewed-resolutions.md, requirement 2): an original entry may only be
        removed, and any other entry needs a complete review record by a listed reviewer.
        An unreviewed addition fails the build before any FSDS row is read or key selected.
        The concept mapping's stated substitutes are merged into the rules, and the reviewed
        element-fit list is loaded (key-currency-and-definition requirements 3 and 4).

        Args:
            fsds_dirs: FSDS quarter directories (``sub``/``num``/``pre``/``tag.tsv`` each).
            rules: The statement-line rules file.
            notes: The note-concept file.
            resolutions: The resolutions file.
            candidates: As for the constructor.
            locked: The pinned original resolutions file (the entries admitted without a
                review record).
            mapping: The concept-family coverage file whose ``substitutes`` join the rules.
            element_fit: The reviewed element-fit list.

        Returns:
            The witness.

        Raises:
            ValueError: No FSDS directory, a malformed list, or a resolution neither in the
                original list nor carrying a complete review record.
        """
        dirs = sorted(Path(d) for d in fsds_dirs)
        if not dirs:
            raise ValueError("the statement witness needs at least one FSDS quarter")
        current = load_resolutions(resolutions)
        check_resolutions(current, load_resolutions(locked))
        merged = merge_mapping_substitutes(load_statement_rules(rules),
                                           load_mapping_substitutes(mapping))
        fit = load_element_fit(element_fit)
        return cls(load_tables(dirs), merged, load_note_concepts(notes), current, candidates,
                   quarters=[d.name for d in dirs], element_fit=fit)

    def _tables(self, *adsh: str) -> FsdsTables:
        """The rows of the named filings only."""
        out: FsdsTables = {"sub": [], "num": [], "pre": [], "tag": []}
        for a in dict.fromkeys(adsh):
            part = self._by_adsh.get(a, self._empty)
            for name in ("sub", "num", "pre"):
                out[name].extend(part[name])  # type: ignore[literal-required]
        return out

    @contextmanager
    def provisional(self) -> Iterator[list[str]]:
        """Keys whose blocks are the caller's to handle, never the build's to report.

        Spec: docs/specs/compound-shape.md, requirement 4 ("Witness blocks"). Inside the
        scope a blocked key input is reported only to the yielded list, never to
        ``blocked``, and its block is not memoized, so a later key of the same input
        outside a scope (a main build sharing this witness) chooses it afresh and records
        the block exactly as a fresh witness would. A block memoized before the scope is
        reported to the list too. Everything else (chosen keys, witness records, defined
        keys) is kept as outside a scope: those are what a fresh witness would compute.

        Yields:
            The scope's block messages; non-empty when any key input met was blocked.
        """
        outer = self._scope
        met: list[str] = []
        self._scope = met
        try:
            yield met
        finally:
            self._scope = outer

    def _block(self, message: str) -> None:
        """Records a blocked key input: in the open provisional scope, else in ``blocked``."""
        if self._scope is not None:
            self._scope.append(message)
        else:
            self.blocked.append(message)

    def _gap(self, family: str, cik: str, fiscal_year: int, accession: str) -> None:
        """Records a key input or member no FSDS filing witnesses (requirement 4)."""
        self.gaps.append({"family": family, "cik": str(cik).zfill(10),
                          "fiscal_year": int(fiscal_year), "accession": accession})

    def _record(self, w: Witness) -> None:
        self.witnesses[(w["family"], w["cik"], w["fiscal_year"], w["key_concept"],
                        w["key_accession"], w["adsh"])] = w

    def annual_accession(self, cf: dict, labels: Mapping[str, int], year: int) -> str | None:
        """The original annual report the filer labels ``year`` (its earliest filed one).

        Args:
            cf: The filer's companyfacts document.
            labels: The document's ``annual_filing_labels``.
            year: The fiscal-year label.

        Returns:
            The accession, or None when no annual report carries the label.
        """
        # Cached by the filer's CIK, never by id(cf): a witness shared by two builds sees
        # each build's own documents, and a freed document's id can be reused by another
        # filer's.
        cik = cf.get("cik")
        cache_key = str(cik).zfill(10) if cik is not None else None
        if cache_key is None or cache_key not in self._annual:
            reports = _annual_reports(cf, labels)
            if cache_key is None:
                return reports.get(year)
            self._annual[cache_key] = reports
        return self._annual[cache_key].get(year)

    def fy_key(self, cf: dict, labels: Mapping[str, int], cik: str, family: str,
               spec: Mapping[str, str], year: int) -> tuple[AnswerKey, str, float | None]:
        """Chooses one key input: ``select_key`` over the concepts the mapping offers.

        Args:
            cf: The filer's companyfacts document.
            labels: The document's ``annual_filing_labels``.
            cik: The filer's CIK (10 digits).
            family: The metric family.
            spec: The inventory's ``{"concept", "unit"}`` for the family and year.
            year: The fiscal-year label.

        Returns:
            The chosen key (a copy), the annual report it was witnessed in, and the
            companyfacts value of its concept in that report (or None).

        Raises:
            WitnessDrop: No candidate is keyed, the chosen key is held or resolved
                ``drop``, or the witness blocks it (the block is also recorded).
        """
        memo = (cik, family, year)
        found: tuple[AnswerKey, str, float | None] | WitnessDrop
        if memo in self._chosen:
            found = self._chosen[memo]
            if self._scope is not None and _is_block(found):
                self._scope.append(f"{family} cik {cik} FY{year}: blocked earlier")
        else:
            try:
                found = self._choose(cf, labels, cik, family, spec, year)
            except WitnessDrop as exc:
                found = exc
            # A block met in a provisional scope is not remembered (see ``provisional``).
            if self._scope is None or not _is_block(found):
                self._chosen[memo] = found
        if isinstance(found, WitnessDrop):
            raise WitnessDrop(*found.args)
        key, adsh, annual_value = found
        return dict(key), adsh, annual_value  # type: ignore[return-value]

    def defined_key(self, cf: dict, cik: str, family: str, fiscal_year: int, adsh: str,
                    period_end: str, unit: str) -> DefinedKey:
        """The defined key of a Debt or DeferredRevenue balance in one filing, memoized.

        A blocking key (a statement line whose companyfacts value is missing or differs)
        blocks the build only when it is a year's own key (``fy_key``); a blocking re-read
        of a comparative column is not used, and the year's own key stands in.

        Args:
            cf: The filer's companyfacts document.
            cik: The filer's CIK (10 digits).
            family: ``Debt`` or ``DeferredRevenue``.
            fiscal_year: The fiscal-year label the key carries.
            adsh: The filing read.
            period_end: The balance-sheet date.
            unit: The unit.

        Returns:
            The defined key.
        """
        memo = (family, cik, adsh, period_end)
        if memo not in self.defined:
            d = DEFINED_FAMILIES[family](self._tables(adsh), self.rules, cf, cik, fiscal_year,
                                         adsh, period_end, unit)
            self.defined[memo] = d
        return self.defined[memo]

    def reread(self, cf: dict, cik: str, family: str, fiscal_year: int, adsh: str,
               period_end: str, unit: str) -> AnswerKey | None:
        """A defined family's key for ``period_end`` as filing ``adsh`` presents it.

        Args:
            cf: The filer's companyfacts document.
            cik: The filer's CIK (10 digits).
            family: ``Debt`` or ``DeferredRevenue``.
            fiscal_year: The fiscal-year label the key carries.
            adsh: The filing read (e.g. a later 10-K's comparative column).
            period_end: The balance-sheet date.
            unit: The unit.

        Returns:
            A copy of the key, or None when the filing does not define one.
        """
        d = self.defined_key(cf, cik, family, fiscal_year, adsh, period_end, unit)
        if d["status"] != "defined" or d["key"] is None:
            return None
        return dict(d["key"])  # type: ignore[return-value]  # a copy of an AnswerKey

    def readings(self, family: str, key: Mapping[str, Any],
                 shape: str) -> list[dict[str, Any]]:
        """The alternate readings a case of ``shape`` carries for one defined key.

        Args:
            family: The key's metric family.
            key: A key ``fy_key`` or ``reread`` returned.
            shape: The case's v2 shape.

        Returns:
            ``case_readings`` of the key's defined record; empty for any other key.
        """
        d = self.defined.get((family, key["cik"], key["accession"], key["period_end"]))
        return [] if d is None else case_readings(d, shape)

    def _choose_defined(self, cf: dict, labels: Mapping[str, int], cik: str, family: str,
                        unit: str, year: int) -> tuple[AnswerKey, str, float | None]:
        """The defined key of a Debt or DeferredRevenue year, on the S1 column.

        Spec: docs/specs/key-currency-and-definition.md, requirement 6. The latest later
        annual report that defines the year's balance-sheet date is the key; one that does
        not (no FSDS, not presented, a blocking read) is passed over and never blocks. With
        none, the own report's defined key, with its block and drop reasons.
        """
        adsh = self.annual_accession(cf, labels, year)
        if adsh is None:
            raise WitnessDrop("no_key")
        end = own_period_end(cf, adsh)
        if end is None:
            raise WitnessDrop("no_key")
        for later in later_annual_reports(cf, labels, year):
            if later == adsh:
                continue
            found = self.defined_key(cf, cik, family, year, later, end, unit)
            if found["status"] == "defined" and found["key"] is not None:
                return found["key"], adsh, found["value"]
        d = self.defined_key(cf, cik, family, year, adsh, end, unit)
        if d["status"] in BLOCKING_STATUSES:
            self._block(f"{family} cik {cik} FY{year} ({adsh}, {end}): "
                        f"{d['status']}: {d['reason']}")
            raise WitnessDrop("witness_blocked")
        if d["status"] == "no_fsds":
            self._gap(family, cik, year, adsh)
        if d["status"] != "defined" or d["key"] is None:
            raise WitnessDrop(_DEFINED_DROPS.get(d["status"], d["status"]))
        return d["key"], adsh, d["value"]

    def _choose(self, cf: dict, labels: Mapping[str, int], cik: str, family: str,
                spec: Mapping[str, str], year: int) -> tuple[AnswerKey, str, float | None]:
        unit = spec["unit"]
        if family in DEFINED_FAMILIES:
            return self._choose_defined(cf, labels, cik, family, unit, year)
        concepts = list(dict.fromkeys([spec["concept"],
                                       *self.candidates.get((cik, year, family), [])]))
        keys: list[AnswerKey] = []
        errors: list[Exception] = []
        for concept in concepts:
            try:
                k = labelled_fiscal_year_key(cf, dict(labels), concept, unit, year)
            except _KEY_ERRORS as exc:
                errors.append(exc)
                continue
            if k["value"] is not None:
                keys.append(k)
        if not keys:
            raise WitnessDrop(_drop_reason(errors[0]) if errors else "no_key")
        adsh = self.annual_accession(cf, labels, year)
        if adsh is None:
            raise WitnessDrop("no_key")
        values: dict[str, float | None] = {}
        for k in keys:
            try:
                values[k["concept"]] = as_reported_key(cf, k["concept"], unit, k["period_start"],
                                                       k["period_end"], accession=adsh)["value"]
            except _KEY_ERRORS:
                values[k["concept"]] = None
        accessions = [adsh, *(k["accession"] for k in keys)]
        try:
            w = select_key(self._tables(*accessions), self.rules, self.notes, self.resolutions,
                           family, keys, adsh, values)
        except WitnessBlocked as exc:
            self._block(f"{family} cik {cik} FY{year}: {exc}")
            raise WitnessDrop("witness_blocked") from exc
        self._record(w)
        if w["status"] == "no_fsds":
            self._gap(family, cik, year, w["adsh"])
            raise WitnessDrop(FSDS_NOT_PUBLISHED)
        if w["status"] in HELD:
            raise WitnessDrop(w["status"])
        if w["status"] in BLOCKING and w["resolution"] is None:
            # An unresolved blocked fallback (select_key raises on any other).
            raise WitnessDrop("fallback_blocked")
        if (w["status"] in BLOCKING and w["resolution"] is not None
                and w["resolution"]["reading"] == "drop"):
            # args[1]: the dropped key's reviewed value, written on the screen exclusion
            # (grading-dont-care-and-ties requirement 2).
            value = float(w["resolution"]["value"])
            self.resolved_drops[(str(cik).zfill(10), family, year)] = value
            raise WitnessDrop("resolved_drop", value)
        chosen = next(k for k in keys if k["concept"] == w["key_concept"])
        return chosen, adsh, values.get(chosen["concept"])

    def resolved_drop_value(self, cik: str, family: str, year: int) -> float | None:
        """The value of the key a reviewed resolution dropped for a filer, family and year.

        Spec: docs/specs/grading-dont-care-and-ties.md (requirement 2).

        Args:
            cik: The filer's CIK.
            family: The metric family.
            year: The fiscal-year label.

        Returns:
            The resolution's value, or None when no reviewed drop was met for them.
        """
        return self.resolved_drops.get((str(cik).zfill(10), family, year))

    def member(self, family: str, adsh: str, key: AnswerKey,
               annual_value: float | None) -> UniverseStatus:
        """A keyed screen member's status (``universe_status`` with its key).

        Args:
            family: The screen's metric family.
            adsh: The member's annual report for the screened year.
            key: The member's chosen key.
            annual_value: The key concept's companyfacts value in ``adsh``.

        Returns:
            The member's status. A Debt or DeferredRevenue member's status is its defined
            key's ``member_status``; a ranked net or comprehensive income member on a
            consolidated element is excluded when the filing reports a different parent
            figure (key-definitions requirements 4 and 6). Any other ranked member on an
            element the ``element_fit`` list names for the family is excluded as
            ``element_outside_family`` (key-currency-and-definition requirement 4).

        Raises:
            ValueError: A Debt or DeferredRevenue key this witness did not define.
        """
        if family in DEFINED_FAMILIES:
            d = self.defined.get((family, key["cik"], key["accession"], key["period_end"]))
            if d is None:
                raise ValueError(f"{family}: no defined key for {key['accession']} "
                                 f"{key['period_end']}")
            m = member_status(d)
            if m["reason"] == "no_fsds":
                if key["fiscal_year"] is not None:
                    self._gap(family, key["cik"], key["fiscal_year"], adsh)
                m = {**m, "reason": FSDS_NOT_PUBLISHED}
            return {"status": m["status"], "reason": m["reason"], "concept": m["concept"],
                    "value": m["value"], "witness": None}
        tables = self._tables(adsh, key["accession"])
        status = universe_status(tables, self.rules, self.notes, self.resolutions, family, adsh,
                                 key["period_start"], key["period_end"], key["unit"], key,
                                 annual_value)
        if status["witness"] is not None:
            self._record(status["witness"])
            resolution = status["witness"]["resolution"]
            if (status["reason"] == "resolved_drop" and resolution is not None
                    and key["fiscal_year"] is not None):
                drop = (str(key["cik"]).zfill(10), family, key["fiscal_year"])
                self.resolved_drops[drop] = float(resolution["value"])
        concept = key["concept"]
        if (status["status"] == "ranked" and family in PARENT_ELEMENTS
                and concept in CONSOLIDATED_ELEMENTS[family]):
            attribution = attribution_status(tables, family, key["accession"],
                                             key["period_start"], key["period_end"],
                                             key["unit"], concept)
            m = attribution_member(attribution, concept, status["value"])
            return element_fit_status(
                {"status": m["status"], "reason": m["reason"], "concept": m["concept"],
                 "value": m["value"], "witness": status["witness"]}, family, self.element_fit)
        return element_fit_status(status, family, self.element_fit)

    def unkeyed_member(self, cf: dict, labels: Mapping[str, int], cik: str, family: str,
                       year: int, unit: str) -> tuple[str | None, AnswerKey | None]:
        """A screen member the mapping offers no key for (today's ``metric_not_tagged``).

        A filer that presents the family's line on one element is keyed from companyfacts on
        that element and witnessed like any other key; otherwise it is excluded with the
        ``universe_status`` reason.

        Args:
            cf: The filer's companyfacts document.
            labels: The document's ``annual_filing_labels``.
            cik: The filer's CIK.
            family: The screen's metric family.
            year: The screened fiscal year.
            unit: The screen's unit.

        Returns:
            ``(None, key)`` for a ranked member, else ``(reason, None)``.
        """
        if family in DEFINED_FAMILIES and family in self.rules:
            try:
                key, own, annual_value = self.fy_key(cf, labels, cik, family,
                                                     {"concept": "", "unit": unit}, year)
            except WitnessDrop as exc:
                return str(exc.args[0]), None
            defined = self.member(family, own, key, annual_value)
            if defined["status"] != "ranked":
                return str(defined["reason"]), None
            return None, key
        if family not in self.rules:
            return "metric_not_tagged", None
        adsh = self.annual_accession(cf, labels, year)
        if adsh is None:
            return "metric_not_tagged", None
        tables = self._tables(adsh)
        sub = tables["sub"][0] if tables["sub"] else None
        if sub is None:
            self._gap(family, cik, year, adsh)
            return FSDS_NOT_PUBLISHED, None
        p = sub["period"]
        end = dt.date(int(p[:4]), int(p[4:6]), int(p[6:]))
        instant = self.rules[family]["statements"][0] == "BS"
        start = None if instant else (end - dt.timedelta(days=_YEAR_DAYS)).isoformat()
        status = universe_status(tables, self.rules, self.notes, self.resolutions, family, adsh,
                                 start, end.isoformat(), unit)
        if status["status"] == "metric_not_tagged":
            return "metric_not_tagged", None
        if status["status"] != "ranked":
            return str(status["reason"]), None
        if status["concept"] is None:
            return "presented_line_not_keyed", None
        if status["concept"].startswith("ext:"):
            if status["value"] is None:
                return "presented_line_not_keyed", None
            return self._extension_member(cf, labels, family, year, unit, adsh, start,
                                          end.isoformat(), float(status["value"]))
        try:
            key, adsh, annual_value = self.fy_key(cf, labels, cik, family,
                                                  {"concept": status["concept"], "unit": unit},
                                                  year)
        except WitnessDrop as exc:
            return str(exc.args[0]), None
        keyed = self.member(family, adsh, key, annual_value)
        if keyed["status"] != "ranked":
            return str(keyed["reason"]), None
        return None, key

    def _extension_member(self, cf: dict, labels: Mapping[str, int], family: str, year: int,
                          unit: str, own: str, start: str | None, end: str,
                          own_value: float) -> tuple[str | None, AnswerKey | None]:
        """A member whose own report presents the line on an extension element.

        Spec: docs/specs/key-fallback-witness.md, requirement 3. Keyed from the first later
        annual report that presents the same period on one standard element with the same
        value, through companyfacts (S1), and admitted like any other keyed member.

        Args:
            cf: The filer's companyfacts document.
            labels: The document's ``annual_filing_labels``.
            family: The screen's metric family.
            year: The screened fiscal year.
            unit: The screen's unit.
            own: The own annual report.
            start: The own read's period start (None for a balance).
            end: The own read's period end.
            own_value: The own extension line's value.

        Returns:
            ``(None, key)`` for a ranked member, else ``(reason, None)``.
        """
        not_keyed: tuple[str | None, AnswerKey | None] = ("presented_line_not_keyed", None)
        later = later_annual_reports(cf, labels, year)
        hit = later_standard_line(self._tables(*later), self.rules[family], later, start, end,
                                  unit, own_value)
        if hit is None:
            return not_keyed
        concept = str(hit["concept"])
        key_end = own_period_end(cf, own)
        if key_end is None:
            return not_keyed
        key_start: str | None = None
        if self.rules[family]["statements"][0] != "BS":
            starts = _annual_starts(cf, concept, unit, key_end)
            if len(starts) != 1:
                return not_keyed
            key_start = starts[0]
        try:
            key = latest_filed_key(cf, concept, unit, key_start, key_end)
        except _KEY_ERRORS:
            return not_keyed
        if key["value"] is None or not _same_amount(float(key["value"]), own_value):
            return not_keyed
        key["fiscal_year"] = year
        key["policies"] = ["S1", "S2", "S6"]
        keyed = self.member(family, own, key, None)
        if keyed["status"] != "ranked":
            return str(keyed["reason"]), None
        return None, key

    def raise_if_blocked(self) -> None:
        """Raises when any key input was blocked.

        Raises:
            WitnessBlocked: Naming the blocked keys (the first ``_BLOCKED_SHOWN``).
        """
        if self.blocked:
            shown = "\n  ".join(self.blocked[:_BLOCKED_SHOWN])
            more = len(self.blocked) - _BLOCKED_SHOWN
            raise WitnessBlocked(
                f"{len(self.blocked)} key input(s) blocked by the statement witness; resolve "
                f"each (a reviewed key_resolutions entry, a re-lock) or fix the mapping:\n  "
                f"{shown}" + (f"\n  ... and {more} more" if more > 0 else ""))

    def resolution_use(self) -> dict[str, Any]:
        """``check_resolution_use`` over every kept witness, for ``manifest["key_resolutions"]``.

        Spec: docs/specs/reviewed-resolutions.md, requirement 3. Call it after
        ``raise_if_blocked``.

        Returns:
            ``{"n", "original", "reviewed", "by_bucket"}``.

        Raises:
            ValueError: A resolution covering no kept witness, or evidence the build's
                filings do not hold.
        """
        ordered = [self.witnesses[k] for k in sorted(self.witnesses, key=repr)]
        return check_resolution_use(self.resolutions, ordered, self._tables_all)

    def manifest(self) -> dict[str, Any]:
        """``witness_manifest`` over every witnessed key, for ``manifest["key_independence"]``.

        Returns:
            The counts, resolved count and held list.
        """
        ordered = [self.witnesses[k] for k in sorted(self.witnesses, key=repr)]
        return witness_manifest(ordered)

    def fsds_manifest(self) -> dict[str, Any]:
        """``fsds_gap_manifest`` of this build, for ``manifest["fsds"]``.

        Spec: docs/specs/key-fallback-witness.md, requirement 5.

        Returns:
            The coverage of the quarters ``from_paths`` was given and the gaps recorded.

        Raises:
            ValueError: A quarter directory whose name is not a quarter.
        """
        return fsds_gap_manifest(fsds_coverage(self.quarters), self.gaps)

    def definitions_manifest(self) -> dict[str, Any]:
        """Counts of the defined keys built, for ``manifest["key_definitions"]``.

        Returns:
            ``{"n", "by_family_status", "by_basis", "with_readings"}``.
        """
        records = [self.defined[k] for k in sorted(self.defined)]
        by_status = Counter(f"{d['family']}:{d['status']}" for d in records)
        by_basis = Counter(f"{d['family']}:{d['basis']}" for d in records
                           if d["basis"] is not None)
        return {"n": len(records), "by_family_status": dict(sorted(by_status.items())),
                "by_basis": dict(sorted(by_basis.items())),
                "with_readings": sum(1 for d in records if d["readings"])}

