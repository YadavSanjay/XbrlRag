"""scripts/dbq.py statement splitting: a ``;`` ends a statement only outside comments,
string literals, quoted identifiers and dollar-quoted bodies."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def split():
    spec = importlib.util.spec_from_file_location("dbq", REPO_ROOT / "scripts" / "dbq.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._split_statements


def test_semicolon_in_trailing_comment_does_not_split(split):
    # The 2026-11-07 failure: line 9 of scripts/ddl/layer_0/concept_mapping.sql.
    sql = (
        "CREATE TABLE t (\n"
        "    a TEXT,            -- NULL = all filers; CIK = filer-specific\n"
        "    b INT\n"
        ");\n"
        "ALTER TABLE t ADD COLUMN IF NOT EXISTS c TEXT;\n"
    )
    out = split(sql)
    assert len(out) == 2
    assert out[0].startswith("CREATE TABLE t (") and out[0].endswith(")")
    assert out[1] == "ALTER TABLE t ADD COLUMN IF NOT EXISTS c TEXT"


def test_semicolons_in_literals_identifiers_and_block_comments(split):
    sql = (
        "SELECT 'a;b', 'it''s; fine', \"odd;name\" FROM t; "
        "/* one; two */ SELECT 2;"
    )
    assert split(sql) == [
        "SELECT 'a;b', 'it''s; fine', \"odd;name\" FROM t",
        "/* one; two */ SELECT 2",
    ]


def test_dollar_quoted_body_is_one_statement(split):
    body = "BEGIN PERFORM 1; RETURN; END"
    sql = (
        f"CREATE FUNCTION f() RETURNS void AS $fn$ {body} $fn$ LANGUAGE plpgsql;\n"
        f"DO $$ {body} $$;"
    )
    out = split(sql)
    assert len(out) == 2
    assert body in out[0] and body in out[1]


def test_comment_only_chunks_are_dropped(split):
    sql = "-- header; with a semicolon\n-- more\nSELECT 1;\n-- trailer;\n"
    assert split(sql) == ["-- header; with a semicolon\n-- more\nSELECT 1"]


def test_last_statement_without_semicolon_is_kept(split):
    assert split("SELECT 1; SELECT 2") == ["SELECT 1", "SELECT 2"]


@pytest.mark.parametrize(
    "path",
    [
        "scripts/ddl/layer_0/concept_mapping.sql",
        "scripts/ddl/eval/eval_runs.sql",
        "scripts/ddl/populate/concept_mapping_ns_expand.sql",
    ],
)
def test_committed_ddl_files_split_without_fragments(split, path):
    # Every chunk must start like a statement, never mid-definition.
    starts = ("CREATE", "ALTER", "INSERT", "UPDATE", "DELETE", "DROP", "COMMENT",
              "WITH", "SELECT", "DO", "BEGIN", "COMMIT", "GRANT", "SET", "REFRESH")
    for chunk in split((REPO_ROOT / path).read_text(encoding="utf-8")):
        code = "\n".join(
            line for line in chunk.splitlines() if not line.strip().startswith("--")
        ).strip()
        assert code.upper().startswith(starts), f"{path}: fragment {code[:80]!r}"
