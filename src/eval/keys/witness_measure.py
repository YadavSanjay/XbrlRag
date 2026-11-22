# Scores statement-line rules against an element oracle on an FSDS sample, and judges r2.
# Spec: docs/specs/witness-rules-r2.md. Test-time tooling for the key witness rules; never
# imported by the product.

from __future__ import annotations

import datetime as dt
import hashlib
import importlib
import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, TypedDict

import yaml

from src.eval.keys.statement_witness import (
    PRIMARY_STATEMENTS,
    StatementRule,
    label_head,
    load_statement_rules,
    select_lines,
)
from src.eval.sec_reference.fsds import FsdsTables, load_tables, period_value, statement_lines

# The seven outcomes of one filing-family (requirement 3), and the evaluable ones.
OUTCOMES = ("hit", "none", "ambiguous", "wrong", "oracle_ambiguous", "no_oracle",
            "no_oracle_selected")
EVALUABLE = ("hit", "none", "ambiguous", "wrong")

PERIODS = ("instant", "duration")
DRAW_TABLES = ("sub", "pre", "num", "tag")
# Taxonomies a substitute concept may come from; anything else is one filer's extension.
STANDARD_TAXONOMIES = frozenset({"us-gaap", "ifrs-full", "srt", "dei"})

_ORACLE_FIELDS = ("elements", "statements", "parenthetical", "period", "unit")
_DIGIT_RUN = re.compile(r"\d{4,}")
# A duration family's start: the period end minus 364 days (requirement 3 step 1).
_DURATION_DAYS = 364


class OracleFamily(TypedDict):
    """One judged family of the oracle (requirement 2).

    Attributes:
        elements: us-gaap local names whose taxonomy definition is the family's measure.
        statements: FSDS statement codes an oracle line may sit on (r1's for the family).
        parenthetical: Whether parenthetical lines may be oracle lines.
        period: ``instant`` or ``duration``.
        unit: The companyfacts unit (``USD``, ``USD/shares``, ``shares``).
    """

    elements: list[str]
    statements: list[str]
    parenthetical: bool
    period: str
    unit: str


class Score(TypedDict):
    """Aggregated outcomes of one rules file on one sample (requirement 4).

    Attributes:
        families: family -> outcome -> count, for every family seen.
        totals: outcome -> count over all families.
        evaluable: hit + none + ambiguous + wrong.
        recall: hit / evaluable, or None when nothing is evaluable.
        precision: hit / (hit + wrong), or None when nothing is decided.
        ambiguity: ambiguous / evaluable, or None when nothing is evaluable.
    """

    families: dict[str, dict[str, int]]
    totals: dict[str, int]
    evaluable: int
    recall: float | None
    precision: float | None
    ambiguity: float | None


class Verdict(TypedDict):
    """The acceptance decision for r2 against r1 (requirement 5).

    Attributes:
        accept: Whether no mark fails.
        failed: The failed mark codes, sorted.
        regressed_families: How many families regress against r1.
    """

    accept: bool
    failed: list[str]
    regressed_families: int


# --------------------------------------------------------------------------- helpers


def _same(a: float, b: float) -> bool:
    """Whether two amounts are the same (FSDS four decimals; as statement_witness)."""
    return abs(a - b) <= 1e-9 * max(abs(a), abs(b), 1.0)


def _sha256(path: str | os.PathLike[str]) -> str:
    """SHA-256 of a file's bytes."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _evaluable(counts: Mapping[str, int]) -> int:
    """hit + none + ambiguous + wrong of one count map."""
    return sum(counts[o] for o in EVALUABLE)


def _oracle_family(family: str, raw: Any) -> OracleFamily:
    """One validated oracle entry."""
    if not isinstance(raw, dict):
        raise ValueError(f"{family}: expected a mapping")
    missing = [f for f in _ORACLE_FIELDS if f not in raw]
    unknown = sorted(set(raw) - set(_ORACLE_FIELDS))
    if missing or unknown:
        raise ValueError(f"{family}: missing {missing}, unknown {unknown}")
    elements = raw["elements"]
    if not isinstance(elements, list) or not elements:
        raise ValueError(f"{family}.elements: expected a non-empty list")
    for e in elements:
        if not isinstance(e, str) or not e or ":" in e:
            raise ValueError(f"{family}.elements: {e!r} is not a bare local name")
    statements = raw["statements"]
    if (not isinstance(statements, list) or not statements
            or any(s not in PRIMARY_STATEMENTS for s in statements)):
        raise ValueError(f"{family}.statements: expected codes of {PRIMARY_STATEMENTS}")
    if not isinstance(raw["parenthetical"], bool):
        raise ValueError(f"{family}.parenthetical: expected true or false")
    if raw["period"] not in PERIODS:
        raise ValueError(f"{family}.period: expected one of {PERIODS}")
    if not isinstance(raw["unit"], str) or not raw["unit"]:
        raise ValueError(f"{family}.unit: expected a unit name")
    return {"elements": list(elements), "statements": list(statements),
            "parenthetical": raw["parenthetical"], "period": raw["period"],
            "unit": raw["unit"]}


def _scalars(node: Any) -> Iterable[str]:
    """Every key and scalar of a parsed YAML document, as text (comments are already gone)."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield str(k)
            yield from _scalars(v)
    elif isinstance(node, list):
        for v in node:
            yield from _scalars(v)
    elif node is not None and not isinstance(node, bool):
        yield str(node)


def _check_draw(sample_dir: str | os.PathLike[str], draw_sha256: Mapping[str, str]) -> None:
    """Checks a sample's four tables against the pinned draw hashes.

    Raises:
        ValueError: The hash map lacks or adds a table, or a file's SHA-256 differs.
        FileNotFoundError: A table file is missing.
    """
    if set(draw_sha256) != set(DRAW_TABLES):
        raise ValueError(f"draw_sha256 must have exactly the keys {DRAW_TABLES}")
    base = Path(sample_dir)
    for table in DRAW_TABLES:
        path = base / f"{table}.tsv"
        if not path.is_file():
            raise FileNotFoundError(f"{path}: missing")
        if _sha256(path) != draw_sha256[table]:
            raise ValueError(f"{path}: SHA-256 does not match the pinned draw")


def _score_sample(tables: FsdsTables, rules: Mapping[str, StatementRule],
                  oracle: Mapping[str, OracleFamily]) -> Score:
    """Scores every filing of a sample's ``sub`` with one rules file and aggregates."""
    return aggregate(
        (family, outcome)
        for sub in tables["sub"]
        for family, outcome in score_filing(tables, rules, oracle, sub["adsh"]).items())


# --------------------------------------------------------------------------- public


def load_oracle(path: str | os.PathLike[str]) -> dict[str, OracleFamily]:
    """Loads and validates the judging oracle (requirement 2).

    Args:
        path: The oracle YAML file.

    Returns:
        family -> oracle entry.

    Raises:
        ValueError: A version other than 1, an unknown or missing field, an empty or
            prefixed element, a statement code outside the primary statements, a bad
            period, or a non-boolean parenthetical.
    """
    try:
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: not valid YAML: {exc}") from exc
    if not isinstance(doc, dict):
        raise ValueError(f"{path}: expected a mapping at the top level")
    unknown = sorted(set(doc) - {"version", "families"})
    if unknown:
        raise ValueError(f"{path}: unknown fields {unknown}")
    if doc.get("version") != 1 or isinstance(doc.get("version"), bool):
        raise ValueError(f"{path}: version must be 1, got {doc.get('version')!r}")
    families = doc.get("families")
    if not isinstance(families, dict) or not families:
        raise ValueError(f"{path}: families must be a non-empty mapping")
    return {str(f): _oracle_family(str(f), raw) for f, raw in families.items()}


def score_filing(tables: FsdsTables, rules: Mapping[str, StatementRule],
                 oracle: Mapping[str, OracleFamily], adsh: str) -> dict[str, str]:
    """Classifies every oracle family of one filing (requirement 3).

    Args:
        tables: The FSDS tables holding the filing.
        rules: The statement-line rules under test.
        oracle: The judging oracle.
        adsh: The filing's accession.

    Returns:
        family -> outcome, one of ``OUTCOMES``.

    Raises:
        ValueError: The filing is not in ``sub``.
        KeyError: An oracle family has no rule.
    """
    sub = next((r for r in tables["sub"] if r["adsh"] == adsh), None)
    if sub is None:
        raise ValueError(f"{adsh}: not in sub")
    end_date = dt.datetime.strptime(sub["period"], "%Y%m%d").date()
    end = end_date.isoformat()
    duration_start = (end_date - dt.timedelta(days=_DURATION_DAYS)).isoformat()
    lines = statement_lines(tables, adsh)
    out: dict[str, str] = {}
    for family, o in oracle.items():
        start = duration_start if o["period"] == "duration" else None
        oracle_values: set[float] = set()
        for ln in lines:
            if ln["stmt"] not in o["statements"]:
                continue
            if ln["inpth"] and not o["parenthetical"]:
                continue
            if ln["custom"] or not ln["version"].startswith("us-gaap/"):
                continue
            if ln["tag"] not in o["elements"]:
                continue
            try:
                value = period_value(tables, adsh, ln["tag"], start, end, o["unit"],
                                     ln["version"])
            except ValueError:
                value = None
            if value is not None:
                oracle_values.add(value)
        rule = rules[family]
        try:
            selected = select_lines(tables, rule, adsh, start, end, o["unit"])
        except ValueError:
            selected = []
        witnessed: float | None = None
        if selected:
            if rule["combine"] == "sum":
                witnessed = float(sum(s["value"] for s in selected))
            elif all(_same(s["value"], selected[0]["value"]) for s in selected):
                witnessed = selected[0]["value"]
        if not oracle_values:
            out[family] = "no_oracle_selected" if selected else "no_oracle"
        elif len(oracle_values) > 1:
            out[family] = "oracle_ambiguous"
        elif not selected:
            out[family] = "none"
        elif witnessed is None:
            out[family] = "ambiguous"
        elif _same(witnessed, next(iter(oracle_values))):
            out[family] = "hit"
        else:
            out[family] = "wrong"
    return out


def aggregate(outcomes: Iterable[tuple[str, str]]) -> Score:
    """Counts outcomes and forms the ratios (requirement 4).

    Args:
        outcomes: ``(family, outcome)`` pairs.

    Returns:
        The score.

    Raises:
        ValueError: An unknown outcome.
    """
    families: dict[str, dict[str, int]] = {}
    for family, outcome in outcomes:
        if outcome not in OUTCOMES:
            raise ValueError(f"{family}: unknown outcome {outcome!r}")
        families.setdefault(family, dict.fromkeys(OUTCOMES, 0))[outcome] += 1
    totals = {o: sum(c[o] for c in families.values()) for o in OUTCOMES}
    evaluable = _evaluable(totals)
    decided = totals["hit"] + totals["wrong"]
    return {
        "families": families, "totals": totals, "evaluable": evaluable,
        "recall": totals["hit"] / evaluable if evaluable else None,
        "precision": totals["hit"] / decided if decided else None,
        "ambiguity": totals["ambiguous"] / evaluable if evaluable else None,
    }


def acceptance(r1: Score, r2: Score) -> Verdict:
    """Applies the pre-registered acceptance marks (requirement 5).

    Args:
        r1: The baseline score.
        r2: The candidate score on the same sample and oracle.

    Returns:
        The verdict, naming no family.

    Raises:
        ValueError: The scores' family sets or a family's evaluable count differ.
    """
    if set(r1["families"]) != set(r2["families"]):
        raise ValueError("the two scores cover different families")
    for family, c1 in r1["families"].items():
        if _evaluable(c1) != _evaluable(r2["families"][family]):
            raise ValueError("a family's evaluable count differs between the scores")
    t = r2["totals"]
    hit, wrong, ambiguous = t["hit"], t["wrong"], t["ambiguous"]
    evaluable = _evaluable(t)
    regressed = 0
    for family, c1 in r1["families"].items():
        n = _evaluable(c1)
        loss = c1["hit"] - r2["families"][family]["hit"]
        if n >= 10 and loss > 1 and loss * 100 > 5 * n:
            regressed += 1
    failed: list[str] = []
    if regressed:
        failed.append("family_regression")
    if not (hit + wrong > 0 and hit * 1000 >= 992 * (hit + wrong)):
        failed.append("precision")
    if not (evaluable > 0 and hit * 1000 >= 930 * evaluable):
        failed.append("recall")
    if ambiguous * 1000 > 25 * evaluable:
        failed.append("ambiguity")
    return {"accept": not failed, "failed": sorted(failed), "regressed_families": regressed}


def rule_identifier_violations(path: str | os.PathLike[str]) -> list[str]:
    """Lists filer identifiers in a rules file (requirement 1).

    Outside ``#`` comments, a run of four or more digits (a year, an amount, a CIK or an
    accession) is an identifier, and so is a substitute concept outside the standard
    taxonomies (an ``ext:`` concept is one filer's extension).

    Args:
        path: The rules YAML file.

    Returns:
        One message per violation; empty when clean.

    Raises:
        ValueError: The file is not valid YAML.
    """
    try:
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: not valid YAML: {exc}") from exc
    out = [f"digit run {m.group()!r} in {text!r}"
           for text in _scalars(doc) for m in _DIGIT_RUN.finditer(text)]
    families = doc.get("families") if isinstance(doc, dict) else None
    for family, rule in (families or {}).items():
        subs = rule.get("substitutes") if isinstance(rule, dict) else None
        for concept in subs or {}:
            prefix = str(concept).split(":", 1)[0]
            if prefix not in STANDARD_TAXONOMIES:
                out.append(f"{family}: substitute {concept!r} is not a standard taxonomy")
    return out


def judge_record(sample_dir: str | os.PathLike[str], rules_path: str | os.PathLike[str],
                 r1_rules_path: str | os.PathLike[str], oracle_path: str | os.PathLike[str],
                 draw_sha256: Mapping[str, str]) -> dict[str, Any]:
    """Scores both rules files on a hash-checked sample (requirement 6).

    Args:
        sample_dir: Directory with sub/pre/num/tag.tsv.
        rules_path: The candidate rules.
        r1_rules_path: The baseline rules.
        oracle_path: The judging oracle.
        draw_sha256: Expected SHA-256 per table.

    Returns:
        The judging record: spec, hashes, both scores and the verdict.

    Raises:
        ValueError: A hash map that is incomplete or does not match the files.
        FileNotFoundError: A table file is missing.
    """
    _check_draw(sample_dir, draw_sha256)
    tables = load_tables([Path(sample_dir)])
    oracle = load_oracle(oracle_path)
    scores = {name: _score_sample(tables, load_statement_rules(rules_file), oracle)
              for name, rules_file in (("r1", r1_rules_path), ("r2", rules_path))}
    return {
        "spec": "witness-rules-r2",
        "draw_sha256": dict(draw_sha256),
        "oracle_sha256": _sha256(oracle_path),
        "rules_sha256": _sha256(rules_path),
        "r1_rules_sha256": _sha256(r1_rules_path),
        "r1": scores["r1"],
        "r2": scores["r2"],
        "verdict": acceptance(scores["r1"], scores["r2"]),
    }


# --------------------------------------------------------------------------- r2.1
# Spec: docs/specs/witness-rules-r2-1.md. Invariants on the rules themselves (requirement 1),
# and the r2.1 judging against the accepted r2 rules (requirements 3-5).


def standard_label_violations(rules: Mapping[str, StatementRule],
                              oracle: Mapping[str, OracleFamily],
                              labels: Mapping[str, str | None]) -> list[dict[str, str]]:
    """Lists the oracle elements' standard labels each family's rule rejects (req. 1.1).

    A rule with ``strip_qualifiers`` is checked on the label's head, as the engine applies it
    (docs/specs/capex-strip-qualifiers.md requirement 3); the record keeps the full label.

    Args:
        rules: The statement-line rules.
        oracle: The judging oracle; every family of it is checked.
        labels: Element local name -> its us-gaap standard label, or None for an element
            the taxonomy no longer has (skipped).

    Returns:
        One ``{family, element, label, field, pattern}`` per rejecting pattern (``field``
        ``exclude``: it matches the label; ``require``: it does not), sorted by (family,
        element, field, pattern); empty when none.

    Raises:
        ValueError: An oracle family has no rule, or an oracle element has no entry in
            ``labels``.
    """
    out: list[dict[str, str]] = []
    for family, o in oracle.items():
        if family not in rules:
            raise ValueError(f"{family}: oracle family has no rule")
        rule = rules[family]
        for element in o["elements"]:
            if element not in labels:
                raise ValueError(f"{family}: oracle element {element} has no label entry")
            label = labels[element]
            if label is None:
                continue
            text = label_head(label) if rule.get("strip_qualifiers") is True else label
            rejecting = [("exclude", p) for p in rule["exclude"]
                         if re.search(p, text, re.IGNORECASE)]
            rejecting += [("require", p) for p in rule["require"]
                          if not re.search(p, text, re.IGNORECASE)]
            out.extend({"family": family, "element": element, "label": label,
                        "field": field, "pattern": p} for field, p in rejecting)
    return sorted(out, key=lambda v: (v["family"], v["element"], v["field"], v["pattern"]))


# Start-of-branch states of requirement 1.2: a path through a pattern item either reaches a
# bounding anchor or a non-letter consuming item first (bounded), reaches a consuming item
# that may match a letter first (unbounded), or passes through the item without either
# (transparent: the next item decides).
_BOUNDED, _UNBOUNDED, _TRANSPARENT = "bounded", "unbounded", "transparent"
# Anchors that cannot hold between two letters; ``\B`` (AT_NON_BOUNDARY) is passed over.
_BOUNDING_ANCHORS = frozenset({"AT_BEGINNING", "AT_BEGINNING_LINE", "AT_BEGINNING_STRING",
                               "AT_BOUNDARY", "AT_END", "AT_END_LINE", "AT_END_STRING"})
# Class categories that never match a letter (``\d``, ``\s``).
_NON_LETTER_CATEGORIES = frozenset({"CATEGORY_DIGIT", "CATEGORY_SPACE",
                                    "CATEGORY_LINEBREAK"})
_REPEATS = frozenset({"MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"})
# The stdlib regular-expression parser; typeshed has no stub for ``re._parser``.
_RE_PARSER: Any = importlib.import_module("re._parser")


def _is_letter(code: int) -> bool:
    """Whether a code point is a letter (either case; the rules search case-insensitively)."""
    return chr(code).isalpha()


def _class_starts(items: Iterable[tuple[Any, Any]]) -> str:
    """The start state of a character class: bounded only when it holds no letter."""
    for op, arg in items:
        name = str(op)
        if name == "LITERAL":
            if _is_letter(arg):
                return _UNBOUNDED
        elif name == "RANGE":
            lo, hi = arg
            if any(_is_letter(c) for c in range(lo, hi + 1)):
                return _UNBOUNDED
        elif name == "CATEGORY":
            if str(arg) not in _NON_LETTER_CATEGORIES:
                return _UNBOUNDED
        else:  # NEGATE, or anything the class can hold that is not listed above
            return _UNBOUNDED
    return _BOUNDED


def _item_starts(op: Any, arg: Any) -> set[str]:
    """The possible start states of one parsed pattern item."""
    name = str(op)
    if name == "AT":
        return {_BOUNDED} if str(arg) in _BOUNDING_ANCHORS else {_TRANSPARENT}
    if name in ("ASSERT", "ASSERT_NOT"):
        return {_TRANSPARENT}
    if name == "LITERAL":
        return {_UNBOUNDED} if _is_letter(arg) else {_BOUNDED}
    if name == "IN":
        return {_class_starts(arg)}
    if name == "SUBPATTERN":
        return _seq_starts(arg[3])
    if name == "ATOMIC_GROUP":
        return _seq_starts(arg)
    if name == "BRANCH":
        return set().union(*(_seq_starts(alt) for alt in arg[1]))
    if name in _REPEATS:
        low, _, body = arg
        starts = _seq_starts(body)
        return starts | {_TRANSPARENT} if low == 0 else starts
    # ANY, NOT_LITERAL, GROUPREF, GROUPREF_EXISTS and anything else that may start a match.
    return {_UNBOUNDED}


def _seq_starts(items: Iterable[tuple[Any, Any]]) -> set[str]:
    """The possible start states of a sequence of parsed items (transparent if all are)."""
    states = {_TRANSPARENT}
    for op, arg in items:
        if _TRANSPARENT not in states:
            break
        states = (states - {_TRANSPARENT}) | _item_starts(op, arg)
    return states


def left_bounded(pattern: str) -> bool:
    """Whether a pattern can never start a match inside a word (requirement 1.2).

    Args:
        pattern: A rules pattern (searched case-insensitively).

    Returns:
        True when no match can start at a letter that follows a letter.

    Raises:
        ValueError: The pattern is not a valid regular expression.
    """
    try:
        re.compile(pattern, re.IGNORECASE)
        parsed = _RE_PARSER.parse(pattern, re.IGNORECASE)
    except re.error as exc:
        raise ValueError(f"{pattern!r}: not a valid regular expression: {exc}") from exc
    # A path that ends while still transparent matches the empty string, so inside a word.
    return _seq_starts(parsed.data) == {_BOUNDED}


def boundary_violations(rules: Mapping[str, StatementRule]) -> list[dict[str, str]]:
    """Lists every exclude and require pattern that is not left-bounded (requirement 1.2).

    Args:
        rules: The statement-line rules to check (every family given).

    Returns:
        One ``{family, field, pattern}`` per pattern, sorted by (family, field, pattern);
        empty when none.
    """
    out = [{"family": family, "field": field, "pattern": p}
           for family, rule in rules.items()
           for field, patterns in (("exclude", rule["exclude"]), ("require", rule["require"]))
           for p in patterns
           if not left_bounded(p)]
    return sorted(out, key=lambda v: (v["family"], v["field"], v["pattern"]))


def acceptance_r2_1(base: Score, candidate: Score) -> Verdict:
    """Applies the r2.1 marks (requirement 4).

    Precision >= 0.992 and ambiguity <= 0.025 are absolute; recall has no absolute mark, only
    no regression against r2 (``recall_regression``), and the per-family rule is applied
    against r2 (``family_regression``).

    Args:
        base: The accepted r2 rules' score.
        candidate: The r2.1 rules' score on the same sample and oracle.

    Returns:
        The verdict, naming no family.

    Raises:
        ValueError: The scores' family sets or a family's evaluable count differ.
    """
    if set(base["families"]) != set(candidate["families"]):
        raise ValueError("the two scores cover different families")
    for family, c0 in base["families"].items():
        if _evaluable(c0) != _evaluable(candidate["families"][family]):
            raise ValueError("a family's evaluable count differs between the scores")
    t = candidate["totals"]
    hit, wrong, ambiguous = t["hit"], t["wrong"], t["ambiguous"]
    evaluable = _evaluable(t)
    regressed = 0
    for family, c0 in base["families"].items():
        n = _evaluable(c0)
        loss = c0["hit"] - candidate["families"][family]["hit"]
        if n >= 10 and loss > 1 and loss * 100 > 5 * n:
            regressed += 1
    failed: list[str] = []
    if not (hit + wrong > 0 and hit * 1000 >= 992 * (hit + wrong)):
        failed.append("precision")
    if ambiguous * 1000 > 25 * evaluable:
        failed.append("ambiguity")
    if hit < base["totals"]["hit"]:
        failed.append("recall_regression")
    if regressed:
        failed.append("family_regression")
    return {"accept": not failed, "failed": sorted(failed), "regressed_families": regressed}


def judge_record_r2_1(sample_dir: str | os.PathLike[str], rules_path: str | os.PathLike[str],
                      base_rules_path: str | os.PathLike[str],
                      oracle_path: str | os.PathLike[str],
                      draw_sha256: Mapping[str, str]) -> dict[str, Any]:
    """Scores the r2.1 and the r2 rules on a hash-checked sample (requirement 5).

    Args:
        sample_dir: Directory with sub/pre/num/tag.tsv.
        rules_path: The candidate (r2.1) rules.
        base_rules_path: The baseline (accepted r2) rules.
        oracle_path: The judging oracle.
        draw_sha256: Expected SHA-256 per table.

    Returns:
        The judging record: spec, hashes, both scores and the verdict.

    Raises:
        ValueError: A hash map that is incomplete or does not match the files.
        FileNotFoundError: A table file is missing.
    """
    _check_draw(sample_dir, draw_sha256)
    tables = load_tables([Path(sample_dir)])
    oracle = load_oracle(oracle_path)
    base = _score_sample(tables, load_statement_rules(base_rules_path), oracle)
    candidate = _score_sample(tables, load_statement_rules(rules_path), oracle)
    return {
        "spec": "witness-rules-r2-1",
        "draw_sha256": dict(draw_sha256),
        "oracle_sha256": _sha256(oracle_path),
        "rules_sha256": _sha256(rules_path),
        "base_rules_sha256": _sha256(base_rules_path),
        "base": base,
        "candidate": candidate,
        "verdict": acceptance_r2_1(base, candidate),
    }


def judge_record_strip(sample_dir: str | os.PathLike[str], rules_path: str | os.PathLike[str],
                       base_rules_path: str | os.PathLike[str],
                       oracle_path: str | os.PathLike[str],
                       draw_sha256: Mapping[str, str]) -> dict[str, Any]:
    """Scores the qualifier-stripping rules and the accepted r2.1 rules on a checked sample.

    Spec: docs/specs/capex-strip-qualifiers.md, requirement 4.

    Args:
        sample_dir: Directory with sub/pre/num/tag.tsv.
        rules_path: The candidate rules (Capex strips qualifiers).
        base_rules_path: The baseline (accepted r2.1) rules.
        oracle_path: The judging oracle.
        draw_sha256: Expected SHA-256 per table.

    Returns:
        The judging record: spec, hashes, both scores and the ``acceptance_r2_1`` verdict.

    Raises:
        ValueError: A hash map that is incomplete or does not match the files.
        FileNotFoundError: A table file is missing.
    """
    record = judge_record_r2_1(sample_dir, rules_path, base_rules_path, oracle_path,
                               draw_sha256)
    record["spec"] = "capex-strip-qualifiers"
    return record
