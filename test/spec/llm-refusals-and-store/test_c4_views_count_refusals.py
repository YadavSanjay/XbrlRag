"""C4: the analyst views count refusals (requirement 4).

Reads the committed scripts/ddl/audit/analyst_views.sql as text. The checks normalize the
SQL (letter case lowered, every whitespace character and double quote removed), so the
implementer's formatting is free.
"""

from __future__ import annotations

import random
import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

ERRORS_ITEM = "count(*)filter(wherepayload->>'outcome'='error')aserrors"
REFUSALS_ITEM = "count(*)filter(wherepayload->>'outcome'='refusal')asrefusals"


def _normalize(sql: str) -> str:
    """Lower-case, strip ``--`` comments, drop all whitespace and double quotes."""
    no_comments = re.sub(r"--[^\n]*", "", sql)
    return re.sub(r"\s+", "", no_comments).replace('"', "").lower()


def _view_body(norm: str, view: str) -> tuple[int, str]:
    """The position of a view's CREATE and its body up to the closing semicolon."""
    m = re.search(rf"create(?:orreplace)?viewaudit\.{re.escape(view)}as", norm)
    assert m is not None, f"no CREATE VIEW audit.{view}"
    end = norm.index(";", m.end())
    return m.start(), norm[m.end():end]


def _select_items(body: str) -> list[str]:
    """The top-level comma-separated items between SELECT and FROM."""
    assert body.startswith("select")
    select_list = body[len("select"):body.index("fromaudit.query_event")]
    items: list[str] = []
    depth, current = 0, ""
    for ch in select_list:
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if ch == "," and depth == 0:
            items.append(current)
            current = ""
        else:
            current += ch
    items.append(current)
    return items


def _checks(sql: str) -> dict[str, bool]:
    norm = _normalize(sql)
    create_at, llm_body = _view_body(norm, "v_llm_calls")
    items = _select_items(llm_body)
    drop_match = re.search(r"dropviewifexistsaudit\.v_llm_calls(?:cascade|restrict)?;", norm)
    drop = drop_match.start() if drop_match else -1
    errors_at = items.index(ERRORS_ITEM) if ERRORS_ITEM in items else -1
    refusals = [i for i, item in enumerate(items) if item.endswith("asrefusals")]
    _cost_at, cost_body = _view_body(norm, "v_model_cost")
    outcome_in = re.findall(r"payload->>'outcome'in\(([^)]*)\)", cost_body)
    return {
        "errors_counts_only_error": errors_at >= 0,
        "refusals_column_filtered_on_refusal": REFUSALS_ITEM in items,
        "refusals_right_after_errors": errors_at >= 0 and refusals == [errors_at + 1],
        "drop_before_create": 0 <= drop < create_at,
        "model_cost_ok_and_refusal": (
            len(outcome_in) == 1
            and set(outcome_in[0].split(",")) == {"'ok'", "'refusal'"}
            and "payload->>'outcome'='ok'" not in cost_body
        ),
    }


@pytest.mark.criterion("C4")
def test_v_llm_calls_has_a_refusals_column_after_errors(analyst_views_sql):
    # oracle: requirement 4 — refusals = count(*) FILTER (WHERE outcome = 'refusal'),
    # right after errors; errors still counts only 'error'; the view is dropped first
    checks = _checks(analyst_views_sql)
    assert checks["refusals_column_filtered_on_refusal"] is True
    assert checks["refusals_right_after_errors"] is True
    assert checks["errors_counts_only_error"] is True
    assert checks["drop_before_create"] is True


@pytest.mark.criterion("C4")
def test_v_model_cost_sums_ok_and_refusal(analyst_views_sql):
    # oracle: requirement 4 — v_model_cost filters on outcome IN ('ok', 'refusal')
    assert _checks(analyst_views_sql)["model_cost_ok_and_refusal"] is True


def _reformat(sql: str, seed: int) -> str:
    """Re-spaces and re-cases *sql*: every whitespace run becomes random whitespace,
    random spaces go after commas and parentheses, and each word's case is flipped at
    random. Comment text is left alone (a comment ends at its newline)."""
    rng = random.Random(seed)
    out: list[str] = []
    for line in sql.split("\n"):
        code, sep, comment = line.partition("--")
        tokens = re.split(r"(\s+|[,()])", code)
        pieces: list[str] = []
        for tok in tokens:
            if not tok:
                continue
            if tok.isspace():
                pieces.append(rng.choice([" ", "  ", "\t", " \t "]))
            elif tok in ",()":
                pieces.append(tok + rng.choice(["", " ", "  "]))
            else:
                pieces.append(rng.choice([tok.upper(), tok.lower(), tok]))
        out.append("".join(pieces) + sep + comment)
    return rng.choice(["\n", "\n\n", "\r\n"]).join(out)


@pytest.mark.criterion("C4")
@given(seed=st.integers(min_value=0, max_value=2**32 - 1))
def test_checks_ignore_whitespace_and_case(analyst_views_sql, seed):
    # oracle: requirement 4 — the checks pass on the committed DDL, and re-spacing or
    # re-casing the SQL does not change any check's result
    original = _checks(analyst_views_sql)
    assert all(original.values()), original
    assert _checks(_reformat(analyst_views_sql, seed)) == original
