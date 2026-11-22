"""The committed v1 suite cites the fact the lookup answers with (live xbrl_db).

The single-fact templates of `configs/eval/sql_derived_templates.yaml` order candidates
with `@LOOKUP_ORDER`, the SQL form of `family_resolution.candidate_order`. This test
checks that claim end to end: for every lookup case of
`test/eval/suites/sql_derived_v1.json` (and the current-year fact of every YoY case) it
runs `XbrlFactstoreLookup` for the same filer, year and family, and asserts that the
lookup's cited fact is the case's expected fact, or one the case accepts as the same
fact (its `fact_id_classes`), with the expected value.

Read-only. Skips when the database is unreachable; a case whose filer the database no
longer holds fails, since the suite must be rebuilt after a corpus change.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import psycopg2
import pytest
from dotenv import load_dotenv

from src.eval.core.graders.numeric import close_enough
from src.retriever.xbrl.xbrl_factstore_lookup import LookupStatus, XbrlFactstoreLookup

pytestmark = pytest.mark.integration

SUITE = Path(__file__).resolve().parents[2] / "test" / "eval" / "suites" / "sql_derived_v1.json"

# Templates whose expected fact is one lookup's answer, and the unit the step asks for.
LOOKUP_TEMPLATES = {
    "aq-1a-revenue-lookup": None,
    "aq-1c-pnl-line-lookup": None,
    "aq-5a-balance-sheet-lookup": None,
    "aq-6a-cash-flow-lookup": None,
    "aq-10a-tsm-twd-lookup": "TWD",
}
YOY_TEMPLATE = "aq-2c-yoy-growth"


def _conn_params() -> dict:
    load_dotenv()
    return {
        "host": os.environ.get("XBRL_DB_HOST", "localhost"),
        "port": int(os.environ.get("XBRL_DB_PORT", "5432")),
        "dbname": os.environ.get("XBRL_DB_NAME", "xbrl_db"),
        "user": os.environ.get("XBRL_DB_USER", "postgres"),
        "password": os.environ.get("XBRL_DB_PASSWORD", "postgres"),
    }


def _db_available() -> bool:
    try:
        with psycopg2.connect(**_conn_params()) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM mv_financial_facts LIMIT 1")
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(not _db_available(), reason="xbrl_db not reachable")


def _cases() -> list[dict]:
    with open(SUITE, encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    wanted = set(LOOKUP_TEMPLATES) | {YOY_TEMPLATE}
    return [c for c in cases if c["id"].split(":", 1)[0] in wanted]


@pytest.fixture(scope="module")
def ciks() -> dict[str, str]:
    with psycopg2.connect(**_conn_params()) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT trading_symbol, cik FROM entity_master WHERE trading_symbol IS NOT NULL"
        )
        return {str(t).upper(): str(c) for t, c in cur.fetchall()}


@pytest.fixture(scope="module")
def lookup() -> XbrlFactstoreLookup:
    load_dotenv()
    from src.db.conn import conn_params

    return XbrlFactstoreLookup(db_conn_params=conn_params())


@requires_db
@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["id"])
def test_case_cites_the_lookup_answer(case, ciks, lookup):
    template = case["id"].split(":", 1)[0]
    ticker = case["ticker"]
    assert ticker in ciks, f"{ticker} is not in entity_master: rebuild the suite"
    step = {
        "id": "s1",
        "op": "lookup",
        "cik": ciks[ticker],
        "fy": case["fy"],
        "concept_family": case["concept"],
        "dimensions": "NONE",
    }
    unit = LOOKUP_TEMPLATES.get(template)
    if unit is not None:
        step["unit"] = unit

    result = lookup.lookup(step)

    # Which fact answers is what the suite must agree with; whether the lookup is
    # confident about it (AMBIGUOUS below its threshold) is the lookup's own concern.
    assert result.status in (LookupStatus.OK, LookupStatus.AMBIGUOUS), (
        f"lookup {result.status.value}: {result.resolution_notes}"
    )
    cited = result.citation.fact_id if result.citation else None
    expected = case["expected_fact_ids"][0]  # the current-year fact of a YoY case
    accepted = case["fact_id_classes"][0]
    assert cited == expected or cited in accepted, (
        f"lookup cites {cited}, the case expects {expected} (accepts {accepted})"
    )
    if template != YOY_TEMPLATE:
        assert result.value is not None
        assert close_enough(result.value, case["expected_value"], 1e-9)
