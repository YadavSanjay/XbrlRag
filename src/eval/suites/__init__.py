"""WS-9 — eval suites (the labelled question sets).

* ``golden_xbrl`` — the WS-2 13-case hand-built set, adapted (Phase 0).
* ``sql_derived`` — ★ the primary internal benchmark, generated from
  ``scripts/xbrl/analyst_queries.sql`` fanned out across the corpus (Phase 1).
* ``financebench`` / ``finlora_xbrl`` — external adapters, **designed but
  deliberately unscheduled** (design doc §11); the internal SQL-derived suite was
  chosen as the preferred benchmark.
"""
