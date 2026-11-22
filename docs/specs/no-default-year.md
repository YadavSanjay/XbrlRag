# no-default-year — a mention that writes no year yields no year, never fiscal 2024

`src/retriever/extract_year_util.py::extract_years_in_range(text, year_range)` reads the fiscal
years a text writes (a four-digit year, `FY23`, `FY'23`, `2021/22`, `FY21-23`, a range such as
"FY2021 through FY2023"). It ends with

```python
if not years:
    years.add(2024)  # Latest year default
```

so a text that writes **no year inside the bounds** reads as `[2024]`. That default reaches every
caller:

- **XBRL extractor** (`EntityExtractor._resolve_one_period`, `src/retriever/xbrl/entity_extractor.py`).
  A period mention with a digit and no relative or prior-year phrase takes the digit path and
  resolves each year `extract_years_in_range` returns. "Q3", "H1", "1st quarter", "first 9
  months", "the 10-K", and a year outside the extractor's bounds ("fiscal 2015", bounds
  2018-2035) all resolve, **not ambiguous**, to fiscal 2024 for every filer whatever its
  `last_seen_fy`. The answer is then planned and cited for a year nobody asked about: a
  silent-wrong. It also defeats [yearless-period-mention](yearless-period-mention.md), whose
  premise is that "'Q3' with no year" fails and degrades (that spec's tests use synthetic
  periods, so they never saw this path).
- **Eval arms that re-read a mention** (`src/eval/systems/long_context_arms.py::filing_targets`
  and `src/retriever/html10k/scopes.py::search_scopes` via `_period_years`). For each
  non-ambiguous period they add the years its mention writes to its `fiscal_year`. A period
  resolved from a relative phrase ("most recent" → `last_seen_fy` 2026) therefore also gets
  2024: the arm reads or searches a filing for a year the question never named.
- **PDF legacy retriever** (`src/retriever/base_retriever.py::BaseRetriever.retrieve_chunks`,
  maintenance-only per [VISION.md](../../VISION.md)). It filters the Qdrant search on the
  query's years; a query with no year is filtered to `"2024"`. That flow's own router prompt
  also says "use 2024 if no year is specified" (`src/retriever/llm_query_router.py`), so there
  the default is the flow's (documented-by-prompt) intent, not an accident.
- `src/retriever/xbrl/fiscal_date.py::resolve_dated_mention` calls it on a relabelled mention
  that always writes the matched fiscal-year labels, all inside the bounds (it returns
  ambiguous first otherwise), so the default is unreachable there.

Found by the [last-fiscal-year-phrase](last-fiscal-year-phrase.md) spec author (its Notes).
This is a bug fix: no new or changed signature.

## Requirement

1. **No year written, no year read.** `extract_years_in_range(text, year_range)` returns exactly
   the years the text writes inside the bounds, read as today (ranges expanded, an overlong
   range's endpoints, two-digit years with their context). When the text writes none — no year
   at all, or only years outside the bounds — it returns `[]`. There is no default year. Every
   result that is non-empty today is unchanged.
2. **The XBRL extractor flags such a mention, it never drops or guesses it.** On the digit path
   of `_resolve_one_period` (after the relative and prior-year phrases and the written-date
   reading, all unchanged), a mention for which `extract_years_in_range(mention, [2018, 2035])`
   is empty resolves to **exactly one** `ResolvedPeriod` with `mention` the mention as given,
   `ambiguous=True`, `fiscal_year=None`, and a non-empty `reason` string (the wording is the
   implementer's; it should say that no fiscal year in range is written). This holds with any
   company or none and whatever its `last_seen_fy`. An empty list (the mention silently
   vanishing) is not allowed: a period mention whose meaning is unknown must still be able to
   degrade the query.
3. **It degrades.** Such a mention contains a digit, so it carries year evidence by
   `period_evidence.has_year_evidence`; `partition_yearless_periods` keeps it next to a resolved
   period, and `BranchDispatcher._unresolved_reasons` then names it, so the query degrades as
   yearless-period-mention's contract says a "Q3" with no year must. No change is needed in
   those two modules beyond Requirement 2; this is what the tests check end to end.
4. **Re-reading callers add only written years.** `filing_targets` and `search_scopes` keep
   their rule (a period's `fiscal_year` plus the years its mention writes); after Requirement 1
   a mention that writes no year adds nothing. For a non-ambiguous period whose mention writes
   no year, the targets and the scope's `fiscal_years` hold exactly its `fiscal_year`.
   `filing_targets`' existing "no period resolved to a fiscal year" refusal is unchanged.
5. **`resolve_dated_mention` is unchanged** (its re-read always writes in-bound labels).
6. **Unlocked, implementer — the PDF legacy flow keeps its behaviour, explicitly.**
   `BaseRetriever.retrieve_chunks` must not crash and must not change what the legacy flow
   searches: when `extract_years_in_range(query)` is empty it uses a module-level constant of
   the legacy retriever (for example `LEGACY_DEFAULT_FISCAL_YEARS = [2024]`, commented as the
   legacy PDF corpus's year that its router prompt also assumes) and logs that it did so. The
   default lives there, named, and nowhere in the shared helper. Covered by an implementer unit
   test in `test/unit/` with a fake vector client (the filter passed is `["2024"]` for a yearless
   query and the query's years otherwise). Do not pass an empty list to
   `QdrantVectorDBClient.hybrid_query_text`: `MatchAny(any=[])` would filter out every chunk.
7. **Unlocked, implementer:** remove the `__main__` example's reliance on the default if it
   prints it, update the period bullet of `src/retriever/xbrl/CLAUDE.md` (a digit mention that
   writes no year in range is one ambiguous period and degrades), and update
   `test/unit/test_extract_year_ranges.py` only if one of its cases relied on the default (none
   is known to).

## Acceptance criteria

All years are exact integers; there are no floats. "Yearless digit mention" below means a text
built only from the pools in the tests: quarter and half tokens ("Q1"-"Q4", "H1", "H2", "1st
quarter" ... "4th quarter", "first 9 months", "6 months"), form names ("10-K", "10-Q", "8-K"),
counts that are not years (one digit, or 100-1899, or 2100 and above), and neutral words with no
period meaning, every two numeric tokens separated by a word — so by hand no token is a year
form of `extract_years_in_range` (no 19xx/20xx, no two-digit number next to a Q/FY marker, no
apostrophe, no dd-dd or dd/dd pair).

- **C1** (`extract_years_in_range`): "Q3", "H1", "1st quarter", "third quarter", "first 9
  months", "the 10-K", "top 3 filers", "Q3 results" and "" read as `[]` with bounds
  `[2018, 2035]` and with the default bounds; "fiscal 2015" and "FY2040" read as `[]` with
  bounds `[2018, 2035]`; "Q3 2023" reads as `[2023]`, "Q3 FY24" as `[2024]` and "fiscal 2015"
  with the default bounds as `[2015]`. Property: every yearless digit mention reads as `[]`
  (both bounds). Metamorphic: appending one year `Y` in the bounds as `Y`, `fiscal Y` or `FYY`
  makes it read exactly `[Y]`; appending one year outside `[2018, 2035]` keeps `[]` under those
  bounds. Oracle: Requirement 1 — a text writes a year or it does not, read by hand.
- **C2** (extractor): with a filer anchored on `last_seen_fy = 2025`, and with no company, each
  of "Q3", "H1", "1st quarter", "first 9 months", "Q3 results" and "fiscal 2015" resolves to one
  period, ambiguous, `fiscal_year` None, with a non-empty `reason`; "Q3 2023" resolves to
  `(2023, "Q3", not ambiguous)`. Property: for any anchor in 2018-2035 or none, every yearless
  digit mention resolves to one ambiguous period with no year. Metamorphic: appending
  ` fiscal Y` (Y in 2018-2035) makes the same mention resolve to one period with
  `fiscal_year == Y`, not ambiguous — the year comes from the text, never from the anchor.
  Oracle: Requirements 1-2, by hand.
- **C3** (degradation): the mentions `["fiscal 2024", "Q3"]` for an anchored filer resolve to
  FY2024 and one ambiguous "Q3"; `partition_yearless_periods` ignores nothing; the dispatcher's
  unresolved reasons are non-empty and one names "Q3". Property: for any anchor, any year `Y`
  in the bounds and any yearless digit mention `m`, `["fiscal Y", m]` gives the same three
  facts. Oracle: Requirements 2-3 with yearless-period-mention's degradation contract, by hand.
- **C4** (re-reading callers): a filer `EXCO` with one non-ambiguous period of `fiscal_year`
  2026 whose mention is "most recent", "latest fiscal year", "Q3 of the last fiscal year" or
  "Q3" gives `filing_targets == ([("EXCO", 2026)], None)` and `search_scopes(result, 5)` gives
  one scope whose `fiscal_years` is `[2026]`. Property: for any `fiscal_year` Y in 2018-2035 and
  any mention that writes no year (word-only relative phrases or yearless digit mentions) the
  targets are `[("EXCO", Y)]` and the scope years `[Y]`; metamorphic: replacing the mention by
  "FY{Z-1} through FY{Z}" (Z = Y) adds exactly Z-1. Oracle: Requirement 4, by hand.

## Interface

No new or changed signature and no stub (bug-fix mode). The tests call the existing
`src.retriever.extract_year_util.extract_years_in_range(text, year_range)`,
`src.retriever.xbrl.entity_extractor.EntityExtractor.resolve_periods(mentions, companies)`,
`src.retriever.xbrl.period_evidence.partition_yearless_periods(periods)`,
`src.retriever.xbrl.branch_dispatcher.BranchDispatcher._unresolved_reasons(result)`,
`src.eval.systems.long_context_arms.filing_targets(result)` and
`src.retriever.html10k.scopes.search_scopes(result, max_filer_searches)`.

## Notes

- **Oracle.** The phrase semantics: a text that writes no year names no year. Expected values
  are the years written in synthetic mention text, read by hand. No data file, database, LLM or
  clock is read. The database is faked at `psycopg2.connect` (the I/O boundary); the fake
  `fiscal_calendar` read returns no row.
- **Callers after the change** (every caller of `extract_years_in_range`; the module's other
  public helper, `overlong_year_ranges`, has no default and is unchanged):

  | Caller | Flow | Today, text with no year | After |
  |---|---|---|---|
  | `EntityExtractor._resolve_one_period` | XBRL | resolves to FY2024, not ambiguous | one ambiguous period; query degrades (R2-R3) |
  | `fiscal_date.resolve_dated_mention` | XBRL | unreachable | unchanged (R5) |
  | `long_context_arms.filing_targets` | eval arms | adds 2024 to every relative-phrase period | adds nothing (R4) |
  | `html10k.scopes.search_scopes` | html_rag arm (dropped 2026-11-06, code live) | adds 2024 to the scope years | adds nothing (R4) |
  | `BaseRetriever.retrieve_chunks` | PDF legacy | Qdrant filter `["2024"]` | same filter, from a named legacy constant (R6) |
  | `__main__` example in `extract_year_util.py` | none | prints `[2024]` | prints `[]` |
  | Spec tests (fiscal-year-range, fiscal-year-ended-date) | tests | always pass years | unaffected |

- **Evidence (read-only, local `xbrl_db`, `current_database()` checked, 2026-11-11, $0).**
  `audit.query_event` holds 13,197 `intent_extraction` events with 15,012 extracted periods.
  **0** periods have a mention containing a digit that writes no year in 2018-2035 (so 0 hit the
  default in the extractor), and **0** resolved periods have a mention that writes no year at
  all (so the arms' re-read never added a stray 2024 either). Of 1,813 distinct stored queries
  only 4 write no year (NVIDIA export-control risks, the China-exposure screen and its compound),
  none names a quarter or half, and the router lifted no period from any of them. Of 1,848
  distinct eval questions (20,079 graded results linked to a trace: `xbrlrag_full` 13,349,
  `naive_rag_*` 4,319, `closed_book` 1,745, `long_context*` 426, `frontier_sec_tools` 240),
  **0** write no year. The defect is latent: the generated suites always state a year, and the
  router rewrites relative phrases into explicit years. It bites only on user-typed questions
  ("What was Apple's Q3 revenue?").
- **Expected movement.** None on stored eval runs. A user question whose only period is "Q3"
  (no year) moves from a cited FY2024 Q3 answer to a narrative degradation with a reason naming
  the mention. A stray digit mention next to a resolved year ("the 10-K" beside "fiscal 2023")
  moves from a planned answer that also pulls FY2024 to a degradation — fail-safe, but it is
  the one direction that can turn an answer into a refusal (see the review point below).
- **Other hard-coded years in the retrieval path, out of scope:**
  - `src/retriever/llm_query_router.py` `ROUTER_DOC_SCOPE_INSTRUCTIONS`: "use 2024 if no year is
    specified" in the PDF legacy router prompt. Same idea as the default, but it is that flow's
    stated policy and the flow is maintenance-only.
  - `src/retriever/base_reranker.py::_get_recency_boost`: `exp(-(2025 - year) / 3)`, a fixed
    "now" for a recency weight (PDF legacy). It ranks, it does not choose a year.
  - `src/pipelines/sec10k/load_to_vectordb_pipeline.py`, `table_vlm_summary_pipeline.py`,
    `src/llm/sec10k_page_summary.py`, `sec10k_table_processor.py`: hard-coded ingest years and
    `__main__` examples in the PDF legacy flow; scope, not defaults.
  - The XBRL path has no other year default: relative phrases anchor on the filer's
    `last_seen_fy` and fail ambiguous without it; `xbrl_factstore_screen._latest_year` reads the
    rows. No `datetime.now().year` in the retrieval path.
- **Seen but out of scope.** The token regex reads "Q3 10-K" as Q3 of 2010 (a two-digit number
  next to a quarter marker is a year form) — inside bounds 2018-2035 that reads as no year, so it
  only matters to a caller with wider bounds; the tests' pools avoid the juxtaposition.
- **Validation plan.** Implementer: `pytest test/spec/no-default-year`, `pytest test/spec -n
  auto` (fiscal-year-range, fiscal-year-ended-date, last-fiscal-year-phrase,
  yearless-period-mention, long-context-arms, html-rag-arm, last-seen-fy must stay green), the
  R6 unit test, unit tests. No paid run: no stored case moves.
