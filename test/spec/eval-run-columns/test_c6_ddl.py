"""C6: the additive, idempotent DDL migration (requirement 7).

Reads the committed scripts/ddl/eval/eval_runs.sql as text. The checks normalize the SQL
(`--` comments dropped, letter case lowered, whitespace runs collapsed to one space,
spaces around parentheses, commas and semicolons removed), so the implementer's
formatting is free.
"""

from __future__ import annotations

import random
import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

TEXT = {"text"}
DOUBLE = {"double precision", "float8"}
INT = {"int", "integer", "int4"}
# table -> column -> accepted normalized type clauses (requirement 7)
EXPECTED = {
    "eval_run": {
        "split": {"text not null default 'legacy'", "text default 'legacy' not null"},
        "judge_version": TEXT,
    },
    "eval_case_result": {
        "intent_id": TEXT,
        "phrasing_family": TEXT,
        "template_id": TEXT,
        "key_source": TEXT,
        "key_status": TEXT,
        "judge_correctness": DOUBLE,
        "judge_completeness": DOUBLE,
        "judge_citation_support": DOUBLE,
        "judge_unsupported_claims": INT,
    },
}


def _normalize(sql: str) -> str:
    no_comments = re.sub(r"--[^\n]*", "", sql)
    s = re.sub(r"\s+", " ", no_comments).lower()
    return re.sub(r"\s*([(),;])\s*", r"\1", s).strip()


def _checks(sql: str) -> dict[str, bool]:
    norm = _normalize(sql)
    out: dict[str, bool] = {}
    for table, columns in EXPECTED.items():
        create = norm.find(f"create table if not exists eval.{table}(")
        for col, types in columns.items():
            hits = [m for m in re.finditer(
                rf"alter table eval\.{table} add column if not exists {col} ([^;]*);", norm)]
            out[f"{table}.{col}"] = (
                len(hits) >= 1
                and all(h.group(1).strip() in types for h in hits)
                and all(0 <= create < h.start() for h in hits)
            )
    adds = re.findall(r"add column(?: if not exists)?", norm)
    out["every_add_column_if_not_exists"] = bool(adds) and all(
        a == "add column if not exists" for a in adds)
    return out


@pytest.mark.criterion("C6")
def test_run_columns_are_added(eval_runs_sql):
    # oracle: requirement 7 — split TEXT NOT NULL DEFAULT 'legacy' and judge_version
    # TEXT on eval.eval_run, as ALTER ... ADD COLUMN IF NOT EXISTS after the CREATE TABLE
    checks = _checks(eval_runs_sql)
    assert checks["eval_run.split"] is True
    assert checks["eval_run.judge_version"] is True


@pytest.mark.criterion("C6")
def test_case_columns_are_added(eval_runs_sql):
    # oracle: requirement 7 — five TEXT, three DOUBLE PRECISION and one INT column on
    # eval.eval_case_result, each nullable, after its CREATE TABLE
    checks = _checks(eval_runs_sql)
    for col in EXPECTED["eval_case_result"]:
        assert checks[f"eval_case_result.{col}"] is True, col


@pytest.mark.criterion("C6")
def test_every_add_column_is_idempotent(eval_runs_sql):
    # oracle: requirement 7 — every ADD COLUMN in the file keeps IF NOT EXISTS, and the
    # eleven new columns are all present
    checks = _checks(eval_runs_sql)
    assert checks["every_add_column_if_not_exists"] is True
    assert sum(1 for k, v in checks.items() if "." in k and v) == 11


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


@pytest.mark.criterion("C6")
@given(seed=st.integers(min_value=0, max_value=2**32 - 1))
def test_checks_ignore_whitespace_and_case(eval_runs_sql, seed):
    # oracle: requirement 7 — the checks pass on the committed DDL, and re-spacing or
    # re-casing the SQL does not change any check's result
    original = _checks(eval_runs_sql)
    assert all(original.values()), original
    assert _checks(_reformat(eval_runs_sql, seed)) == original
