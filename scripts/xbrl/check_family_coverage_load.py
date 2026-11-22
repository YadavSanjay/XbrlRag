#!/usr/bin/env python
"""Check the loaded concept_mapping and mv_financial_facts against the committed mapping.

The last step of the concept-family-coverage reload (docs/specs/concept-family-coverage.md,
Notes "Reload"; the steps are in docs/guides/xbrl-flow/04-ingest-and-populate.md §4.6).
Read-only: the session is opened read-only and nothing is written.

For every filer of the spec's fixture (test/spec/concept-family-coverage/filings.json):

1. every committed (family, QName) row that applies to the filer is in concept_mapping
   with the rank, role and substitution ``family_member`` gives (an ``auto_override``
   row may re-rank, so only its role and substitution are compared);
2. every fixture fact's ``presented`` in mv_financial_facts equals the fixture's;
3. no filer's ``auto_override`` ranking puts a fallback-only primary of the coverage
   file first, for any filer (docs/specs/screen-candidate-order.md R3).

Prints one line per mismatch and a summary; exits 1 when there is any mismatch.

    conda run -n py312 python scripts/xbrl/check_family_coverage_load.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import psycopg2

from src.db.conn import conn_params
from src.ingest.util.generate_concept_mapping import (
    committed_mapping_rows,
    default_role,
    load_family_coverage,
)
from src.retriever.xbrl.family_resolution import family_member

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "test" / "spec" / "concept-family-coverage" / "filings.json"
)


def _db_rows(cur, ciks: list[str]) -> dict[tuple[str, str, str], tuple]:
    cur.execute(
        "SELECT concept_family, qname, COALESCE(cik_override, ''), preference_rank,"
        " concept_role, substitution_code, substitution_note, source"
        " FROM concept_mapping"
        " WHERE source <> 'ns_expanded'"
        "   AND (cik_override IS NULL OR cik_override = ANY(%s))",
        (ciks,),
    )
    return {(f, q, c): rest for f, q, c, *rest in cur.fetchall()}


def check_mapping(cur, ciks: list[str]) -> list[str]:
    """Mismatches between the loaded rows and ``family_member`` for the filers."""
    loaded = _db_rows(cur, ciks)
    committed = {(r["concept_family"], r["qname"]) for r in committed_mapping_rows()}
    problems = []
    for cik in ciks:
        for family, qname in sorted(committed):
            want = family_member(family, cik, qname)
            row = loaded.get((family, qname, cik)) or loaded.get((family, qname, ""))
            if want is None:
                continue
            if row is None:
                problems.append(f"{cik} {family} {qname}: not loaded")
                continue
            rank, role, code, note, source = row
            got_role = role or default_role(rank)
            sub = want["substitution"] or {}
            if source != "auto_override" and rank != want["rank"]:
                problems.append(f"{cik} {family} {qname}: rank {rank}, want {want['rank']}")
            if (got_role, code, note) != (want["role"], sub.get("code"), sub.get("note")):
                problems.append(
                    f"{cik} {family} {qname}: role/substitution {(got_role, code)}, "
                    f"want {(want['role'], sub.get('code'))}"
                )
    return problems


def check_fallback(cur) -> list[str]:
    """``auto_override`` rows that rank a fallback-only primary first (any filer)."""
    fallback = load_family_coverage()["fallback"]
    pairs = sorted((f, q) for f, qs in fallback.items() for q in qs)
    if not pairs:
        return []
    cur.execute(
        "SELECT concept_family, qname, cik_override FROM concept_mapping"
        " WHERE source = 'auto_override' AND preference_rank = 1"
        "   AND (concept_family, qname) IN (SELECT * FROM unnest(%s::text[], %s::text[]))",
        ([f for f, _ in pairs], [q for _, q in pairs]),
    )
    return [
        f"{cik} {family} {qname}: fallback-only primary ranked 1 by auto_override"
        for family, qname, cik in sorted(cur.fetchall())
    ]


def check_presented(cur, cases: dict) -> tuple[list[str], int]:
    """Mismatches of ``presented`` for the fixture facts, and how many were checked."""
    want = {r["fact_id"]: r["presented"] for c in cases.values() for r in c["rows"]}
    cur.execute(
        "SELECT fact_id, presented FROM mv_financial_facts WHERE fact_id = ANY(%s)",
        (sorted(want),),
    )
    got = dict(cur.fetchall())
    problems = [
        f"fact {fid}: presented {got.get(fid)!r}, fixture {p!r}"
        for fid, p in sorted(want.items())
        if got.get(fid) != p
    ]
    return problems, len(want)


def main() -> int:
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
    ciks = sorted({c["cik"] for c in cases.values()})
    with psycopg2.connect(**conn_params()) as conn:
        conn.set_session(readonly=True)
        with conn.cursor() as cur:
            mapping = check_mapping(cur, ciks)
            presented, n_facts = check_presented(cur, cases)
            fallback = check_fallback(cur)
        conn.rollback()
    for line in mapping + presented + fallback:
        print(line)
    print(
        f"{len(ciks)} filers: {len(mapping)} mapping mismatches; "
        f"{n_facts} fixture facts: {len(presented)} presented mismatches; "
        f"{len(fallback)} fallback-only primaries ranked first"
    )
    return 1 if mapping or presented or fallback else 0


if __name__ == "__main__":
    raise SystemExit(main())
