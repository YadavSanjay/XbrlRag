---
name: xbrl-facts
applies_to: ["src/retriever/xbrl/{xbrl_factstore_lookup,xbrl_factstore_screen,xbrl_calculator_tool,plan_executor,plan_validator,plan_generator,entity_extractor,query_decomposer}.py", "src/pipelines/xbrl/retrieval_pipeline.py", "src/ingest/sec_edgar/**/*.py", "src/ingest/util/**/*.py", "scripts/ddl/**", "scripts/xbrl/**", "scripts/rebuild*.py", "src/eval/core/graders/**/*.py", "src/eval/xbrl/**/*.py", "configs/eval/**", "prompts/templates/xbrl/**"]
---
# XBRL fact correctness (ingest -> fact store -> plan -> audited number)

The product promise is that every number is an audited XBRL fact reached by deterministic SQL.
The costliest defect is a wrong number that carries a valid `fact_id`, a high `confidence` and a
clean `execution_result`, so nothing downstream (verifier, grader) objects. Hunt for that first.

## Hunt for

**Period and fiscal mapping** (`xbrl_factstore_lookup.py`, `xbrl_factstore_screen.py`, `entity_extractor.py`)
- `fiscal_period` / `ytd` / `duration_kind`: `_derive_duration_kind` maps `FY->annual`,
  `Q1->quarter`, `Q2->half|quarter`, `Q3->nine_month|quarter`, `Q4->quarter`. A change that
  loses `ytd` on the screen path, adds a quarter without a mapping, or returns `None` (no filter)
  where a duration is required mixes a discrete quarter with a cumulative figure at the same
  `period_end`.
- `INSTANT_CONCEPT_FAMILIES` decides instant vs duration. A balance-sheet concept family missing
  from the set (or a flow concept added to it) queries the wrong `period_type`: a stock summed
  over a period, or a flow read at a single date.
- `fy` is the resolved fiscal year (`fiscal_calendar`), not `EXTRACT(year FROM period_end)`.
  52/53-week and non-calendar filers (fiscal year ending Jan/Jun/Sep) are where this breaks.
- Screen takes "latest fiscal year <= requested `fy` per ticker" (`DISTINCT ON (ticker)`): ranking
  filers from different fiscal years against each other is a silent comparability error unless the
  row's `fiscal_year` is surfaced. Check that new code keeps `fiscal_year` on every screen row.
- Point-in-time: `as_of` must filter on `filed_date <= as_of`; `as_originally_reported` must pick
  the non-amendment filing. A restated value returned for an "as originally reported" request (or
  the reverse) is a wrong number. Amendments (10-K/A) must not silently shadow the original.

**Units, scale, sign**
- Screen SQL hard-filters `unit = 'USD'`; lookup returns whatever `unit` the row has. Code that
  adds, ranks, divides or compares values of different units (USD vs shares vs `pure` vs
  non-USD currency, per-share vs aggregate) without checking `unit` is wrong even when the
  arithmetic is exact. `compute` (`XbrlCalculatorTool.evaluate`) takes bare floats from
  `bindings`: it cannot notice a unit or period mismatch, so the guard must live in the plan
  validator/executor or the plan prompt.
- Scale/decimals: stored `value` is the full-unit number; display scaling (thousands/millions)
  belongs in the synthesis/UI layer. A new path that divides or multiplies by 1e3/1e6, or reads
  `decimals` as precision of a computed result, is suspect. Nil facts and `INF` decimals must not
  become 0.
- Sign: XBRL stores the reported sign with `balance` (debit/credit) and iXBRL `sign="-"`
  attributes. Hunt for ingest or MV changes that drop the sign transform, `ABS()` that hides it,
  and capex/repurchases/dividends (`Capex`, `StockRepurchases`, `DividendsPaid`) compared against
  inflows assuming one convention.
- Division and growth: `(a - b) / b` with `b` from a different period/unit, a zero/negative base
  (`CalculatorError` is raised only for exactly 0), percent vs fraction in a result later labelled
  `%`.

**Dimensions and concept resolution**
- `_parse_dimensions` / `AXIS_HINT_TO_LOCAL` / `_retry_across_axes`: widening an axis match or
  loosening `_dimension_match_exact` can return a different member's value with confidence
  >= `DEFAULT_MIN_CONFIDENCE` (0.70). The `_AXIS_DESCRIPTION_HINTS` guard exists to stop exactly
  that; a change to it needs a case that proves a breakdown request is not matched to a total.
- `dimensions: "NONE"` must mean the consolidated fact (no dimension), not "any dimension".
  Check the `segment_axis IS NULL` equivalents survive MV edits.
- Ambiguity: `AMBIGUOUS` is judged within the winning concept, ordered by
  `concept_mapping.preference_rank`. A change to ordering, `round(value, 6)` distinct-value logic,
  or the confidence math (`_score_confidence`) changes which fact wins silently.
  `PlanExecutor.execute` writes `bindings[step_id] = result.value` for every step regardless of
  `StepStatus`; verify an `AMBIGUOUS`/`NOT_FOUND` value cannot flow into a `compute` expression.
- `KNOWN_CONCEPT_FAMILIES` (plan validator) vs the prompt's taxonomy vs `concept_mapping` rows:
  a family accepted by one and unknown to another resolves to NOT_FOUND or, worse, a fuzzy match.

**Entities** (`entity_extractor.py`)
- Entities resolve by SQL (`entity_master` exact + `pg_trgm` fuzzy). Lowering the similarity
  threshold, or letting an LLM-emitted CIK/year pass through unresolved, answers about the wrong
  company. Share-class/ticker aliasing (GOOG/GOOGL), CIK zero-padding (`0001045810`) and
  as-of-date ticker reuse.

**Publish gate and read-time filters**
- All six factstore query sites append `publish_gate_clause(conn, <surface table>)`. A new query
  or MV read that omits it serves held-back filings (GATE-A failures). The clause correlates on
  `(cik, fiscal_year, fiscal_period)`; a surface table lacking those columns breaks silently
  because the clause is inert when `publish_manifest` is absent.
- `promotion.py`: GATE-A (anchor coverage) must stay hard; staged swap must stay atomic. A
  full ingest that writes into the live fact tables before the gate passes is a P0.

**Ingest (`src/ingest/sec_edgar/`)**
- Ordering and idempotence: facts before Layer-0/MVs; `--patch-accession-numbers` after every
  Arelle ingest (the incremental skip keys on `entry_url`/`filing_accession_number`); one
  transaction per filing. A reordered or partially-committed stage corrupts later joins.
- Arelle invocation: trailing `postgres` product type, the iXBRL patch plugin ordered before
  `xbrlDB`, `ARELLE_INTERNET_CONNECTIVITY=offline` honored by every Arelle stage, `ARELLE_CMD`
  resolved through `src/ingest/util/arelle_cmd.py` (never `Path(cmd).exists()`).
- Empty discovery must fail, not report `ok`. Roots come from `storage.local_root(role)`; no
  absolute Windows path literals (`PROJECT_ROOT` is derived).
- `canonical.htm` + DOM positions are the citation anchors: any change to canonicalization shifts
  `html_char_start/end`, which invalidates `dom_position_populator` output and Qdrant payloads
  until re-run (see the rag-integrity lens).
- MV changes: `scripts/ddl/mv/` numbering encodes dependency order; `rebuild_mvs.py` guards
  orphaned MVs. DDL must stay idempotent (`test_layer0_ddl_idempotent.py`).

**Eval graders** (`src/eval/core/graders/`, `src/eval/xbrl/numeric.py`)
- Numbers are graded from `execution_result` steps, never from prose. A grader change that reads
  the answer text, widens `close_enough` (default rel 1e-6; zero expected uses an absolute < 1.0
  band), or grades `breakdown` as `screen` converts wrong answers into passes.
- `sql_derived_v1.json` is generated from `scripts/xbrl/analyst_queries.sql` through
  `configs/eval/sql_derived_templates.yaml`; editing the JSON by hand or a template without
  regenerating desynchronizes the benchmark from its source SQL.

## Severity anchors

- **P0:** a wrong value with a valid citation reaches the answer by default (quarter vs YTD,
  instant vs duration, unit/scale/sign error, wrong member matched, restated value served as
  original, held-back filing served, AMBIGUOUS value consumed by `compute`); ingest writes
  unpublished or partial facts into the live store; a grader change that turns wrong answers
  into passes.
- **P1:** wrong results only for a subset (non-calendar fiscal year, 10-Q path, multi-dimension
  lookups, one share class); a stage ordering change in ingest; a new factstore query missing
  `publish_gate_clause`; unit-blind `compute`/`screen` ranking added without a guard.
- **P2:** edge-period issue on a rare path (53-week year, amended filing), confidence score drift
  that does not change the winner, missing test for a new fiscal-period or dimension branch.
- **P3:** note quality, resolution_notes wording.

## Proof recipes (py312, PYTHONPATH = repo root; no DB or network)

- **Calculator and validator, pure:** `python -c "from src.retriever.xbrl.xbrl_calculator_tool
  import XbrlCalculatorTool as C; print(C().evaluate('(a-b)/b', {'a': 3.0, 'b': 2.0}))"`; run the
  rejected-grammar cases (`2 ** 3`, `a if b else c`, a call, an attribute) and confirm
  `CalculatorError`.
- **Duration kind table:** `XbrlFactstoreLookup._derive_duration_kind` is a static method; assert
  the full `(fiscal_period, ytd)` grid, including an unknown period returning `None`.
- **Ranking/threshold (pure):** `XbrlFactstoreScreen(...)._rank_and_filter(raw, step, kind, [])`
  with crafted rows (None values, ties, threshold that excludes all, `top_n`); no connection is
  needed.
- **Executor with stubs:** `PlanExecutor(factstore_lookup=<fake returning AMBIGUOUS with a value>)`
  then a `compute` step consuming it; assert the compute step errors instead of using the value.
- **Plan validator:** feed forward references, unknown `concept_family`, bad `fiscal_period`,
  and unit-incompatible compute plans.
- **SQL text, not execution:** capture the SQL string from `_query_flat`/`_query_multidim`/screen
  methods with a fake cursor and assert `publish_manifest`, `unit`, `period_type`, `duration_kind`
  predicates are present.
- Scoped tests: `pytest -q -rs test/unit/test_xbrl_factstore_lookup.py
  test/unit/test_xbrl_plan_executor.py test/unit/test_xbrl_calculator_tool.py
  test/unit/test_xbrl_screen_and_decompose.py test/unit/test_numeric_grader.py`. Report
  skipped (`requires_db`) tests as unverified. Live-DB proof (`scripts/dbq.py`, MV rebuilds, Arelle
  ingest) is out of bounds for a review; name it under **Skipped**.

## Not findings

- `fiscal_period` defaulting to `FY`, and `ytd` defaulting to false (documented behavior).
- Screen returning the full ranking when `min_threshold` excludes every filer (deliberate,
  noted in `resolution_notes`).
- Filings with no `publish_manifest` row being served; GATE-B advisory.
- `AMBIGUOUS` judged per winning concept rather than across the concept family (deliberate; see
  the comment in `_build_result`).
- Raw-signal noise in the calc-network (sum-of-parts) checks; known and why GATE-B is advisory.
- Fuzzy member/axis hints resolving to a family-preferred concept when `preference_rank` says so.
