"""C8: the committed DDL of mv_coverage_card and its place in the MV rebuild runner
(docs/specs/coverage-card.md, requirement 2).

Reads scripts/ddl/mv/42_mv_coverage_card.sql and scripts/rebuild_mvs.py as text. The view's
semantics need Postgres and are checked by the implementer's integration test; these checks
pin its shape. The SQL is normalized (comments dropped; outside string literals, letter case
lowered, whitespace collapsed, spaces around parentheses, commas and semicolons removed), so
the implementer's formatting is free. String literals are compared as written.
"""

from __future__ import annotations

import random
import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

# Requirement 1, in the table's order.
METRICS = [
    ("Revenue", "concept", "Revenue"),
    ("CostOfRevenue", "concept", "CostOfRevenue"),
    ("OperatingIncome", "concept", "OperatingIncome"),
    ("NetIncome", "concept", "NetIncome"),
    ("TotalAssets", "concept", "TotalAssets"),
    ("StockholdersEquity", "concept", "StockholdersEquity"),
    ("CashFromOperations", "concept", "CashFromOperations"),
    ("Capex", "concept", "Capex"),
    ("SegmentAxis", "axis", "Revenue"),
    ("GeographyAxis", "axis", "Revenue"),
]
COLUMNS = ["cik", "ticker", "fiscal_year", "metric", "status", "standard_qnames",
           "extension_qnames", "component_qnames"]
STATUSES = ["standard", "extension", "component_only", "absent"]
AXES = ["us-gaap:StatementBusinessSegmentsAxis", "ifrs-full:SegmentsAxis",
        "srt:StatementGeographicalAxis", "ifrs-full:GeographicalAreasAxis"]

_LITERAL = re.compile(r"('(?:[^']|'')*')")
_TUPLE = re.compile(
    r"\('(\w+)'(?:::text)?,'(concept|axis)'(?:::text)?,'(\w+)'(?:::text)?(?:,[^()']*)?\)")


def _normalize(sql: str) -> str:
    no_block = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    no_comments = re.sub(r"--[^\n]*", "", no_block)
    parts: list[str] = []
    for part in _LITERAL.split(no_comments):
        if part.startswith("'"):
            parts.append(part)
            continue
        s = re.sub(r"\s+", " ", part).lower()
        parts.append(re.sub(r"\s*([(),;])\s*", r"\1", s))
    return re.sub(r"\s*([(),;])\s*", r"\1", "".join(parts)).strip()


def _literals(norm: str) -> set[str]:
    return {m[1:-1] for m in _LITERAL.findall(norm)}


def _outside_literals(norm: str) -> str:
    return " ".join(p for p in _LITERAL.split(norm) if not p.startswith("'"))


def _checks(sql: str) -> dict[str, bool]:
    norm = _normalize(sql)
    code = _outside_literals(norm)
    words = set(re.findall(r"\w+", code))
    lits = _literals(norm)
    drop = re.search(r"drop materialized view if exists mv_coverage_card\b", norm)
    create = re.search(r"create materialized view(?: if not exists)? mv_coverage_card\b", norm)
    index = re.search(
        r"create unique index(?: if not exists)?(?: \w+)? on mv_coverage_card"
        r"(?: using btree)?\(cik,fiscal_year,metric\)", norm)
    tuples = _TUPLE.findall(norm)
    return {
        "drop_before_create": bool(drop and create and drop.start() < create.start()),
        "unique_index": index is not None,
        "reads_sources": {"mv_financial_facts", "concept_mapping"} <= words,
        "columns": set(COLUMNS) <= words,
        "statuses": set(STATUSES) <= lits,
        "axes": set(AXES) <= lits,
        "metric_tuples": sorted(tuples) == sorted(METRICS) and len(tuples) == len(METRICS),
    }


def _managed(rebuild_py: str) -> bool:
    m = re.search(r"MANAGED\s*=\s*\{(.*?)\}", rebuild_py, flags=re.S)
    return bool(m) and re.search(r"[\"']mv_coverage_card[\"']", m.group(1)) is not None


@pytest.mark.criterion("C8")
def test_view_is_dropped_created_and_indexed(coverage_card_sql):
    # oracle: requirement 2 — DROP ... IF EXISTS before CREATE MATERIALIZED VIEW
    # mv_coverage_card, and a unique index on (cik, fiscal_year, metric)
    checks = _checks(coverage_card_sql)
    assert checks["drop_before_create"] is True
    assert checks["unique_index"] is True


@pytest.mark.criterion("C8")
def test_view_shape(coverage_card_sql):
    # oracle: requirements 1, 2, 5 and 6 — the sources, the columns, the four statuses, the
    # four standard axes, and exactly the ten metric tuples of the table
    checks = _checks(coverage_card_sql)
    assert checks["reads_sources"] is True
    assert checks["columns"] is True
    assert checks["statuses"] is True
    assert checks["axes"] is True
    assert checks["metric_tuples"] is True


@pytest.mark.criterion("C8")
def test_view_is_managed(rebuild_mvs_py):
    # oracle: requirement 2 — rebuild_mvs.py lists mv_coverage_card in MANAGED
    assert _managed(rebuild_mvs_py) is True


def _reformat(sql: str, seed: int) -> str:
    """Re-spaces and re-cases *sql* outside comments and string literals."""
    rng = random.Random(seed)
    out: list[str] = []
    for line in sql.split("\n"):
        code, sep, comment = line.partition("--")
        pieces: list[str] = []
        for part in re.split(r"('[^']*')", code):
            if part.startswith("'"):
                pieces.append(part)
                continue
            for tok in re.split(r"(\s+|[,();])", part):
                if not tok:
                    continue
                if tok.isspace():
                    pieces.append(rng.choice([" ", "  ", "\t", " \t "]))
                elif tok in ",();":
                    pieces.append(rng.choice(["", " "]) + tok + rng.choice(["", " ", "  "]))
                else:
                    pieces.append(rng.choice([tok.upper(), tok.lower(), tok]))
        out.append("".join(pieces) + sep + comment)
    return rng.choice(["\n", "\n\n", "\r\n"]).join(out)


@pytest.mark.criterion("C8")
@given(seed=st.integers(min_value=0, max_value=2**32 - 1))
def test_checks_ignore_whitespace_and_case(coverage_card_sql, seed):
    # oracle: requirement 2 — every check passes on the committed DDL, and re-spacing or
    # re-casing the SQL outside its literals does not change any check's result
    original = _checks(coverage_card_sql)
    assert all(original.values()), original
    assert _checks(_reformat(coverage_card_sql, seed)) == original
