# yoy-growth-s1 — the v1 YoY template keys the growth the product's two S1 lookups give

Found on 2026-11-12: `test/integration/test_v1_suite_lookup_agreement_live.py` fails on 31
cases, all `aq-2c-yoy-growth` ("lookup cites 15406, the case expects 14262 (accepts [13253,
13565, 14262])", AAPL FY2024 revenue). Renamed from `yoy-current-s1`: both legs of the growth
move, not only the current year (D1).

[suite-templates-s1](suite-templates-s1.md) moved the v1 single-fact lookups onto rule S1
([evaluation semantics](../design/XbrlRag_Eval_Semantics.md) S1; [restatement-s1](restatement-s1.md)
requirements 1–3) with one Python pick, `s1_lookup_pick` in `src/eval/suites/sql_derived.py`,
and left `aq-2c` out (its requirement 3). `aq-2c` in `configs/eval/sql_derived_templates.yaml`
still reads `mv_yoy_comparison`: `fact_id_current` is the **fy filing's own column** and
`fact_id_prior` the **same filing's comparative** (restatement-s1 R5). The product answers a
single filer's growth with two lookup steps and a compute step (`prompts/templates/xbrl/_base/plan_generator/v9/system.txt:302`, Example 2:
`lookup fy-1`, `lookup fy`, `compute (a - b) / b`), and each lookup applies S1
(`XbrlFactstoreLookup._select_s1` = `latest_restatement(current_column(...))`). So the key's
citations are stale on either leg whenever a later report restates the period:

- **Current leg**: 31 of the 56 committed cases. AAPL FY2024 revenue is keyed on the FY2024
  10-K's fact; the product cites the FY2025 10-K 0000320193-25-000079's comparative.
- **Prior leg**: 32 of the 56. GOOGL FY2025 revenue keeps its current fact, but the prior year
  (FY2024) is cited from the FY2024 10-K 0001652044-25-000014, not the FY2025 10-K's comparative.
- In 9 cases the product's two legs come from **different filings** (NVDA FY2024 revenue: FY2024
  from the FY2026 10-K 0001045810-26-000021, FY2023 from the FY2025 10-K 0001045810-25-000023,
  the latest report of each year). The key has both legs from one filing.

Measured read-only against the local `xbrl_db` on 2026-11-12 (corpus fingerprint
`pm:ca0f8f27e42d39d9`): a dry build of the committed spec reproduces
`test/eval/suites/sql_derived_v1.json` exactly; with requirement 2 applied, 32 aq-2c
cases change and nothing else, all **citation-only** (no expected value changes, the same 56
case ids). `XbrlFactstoreLookup` for fy and fy - 1 then cites both of every case's legs and its
growth equals the expected value (0 mismatches over 56 cases, against 63 for the committed
suite).

## Requirement

Store rows are rows of `mv_financial_facts` as Python mappings, with the keys
suite-templates-s1 requirement 1 names.

1. **The pick** — `s1_yoy_pick(rows, fy)` in `src/eval/suites/sql_derived.py` returns the
   year-over-year growth a v1 YoY case keys for fiscal year `fy`, or `None`:
   - The **current** row is `s1_lookup_pick(rows, fy)` and the **prior** row
     `s1_lookup_pick(rows, fy - 1)`: each year as the product's lookup answers it, the year's
     own column as the latest later-labelled canonical filing restates it, in the v1 lookup
     order with that year's reporting currency first. The two may come from different filings.
   - `None` when either is `None` (a year with no own column has no answer, so the product's
     compute has no input), when their units differ (the compute refuses two currencies,
     [units-currency-integrity](units-currency-integrity.md)), or when the prior value is 0.
   - Otherwise a dict with `value` = (current value - prior value) / |prior value|, `fy_prior`
     = `fy - 1`, `fact_id_current` and `fact_id_prior` (the two rows' `fact_id`), `unit` (the
     current row's), `not_meaningful` = prior value <= 0 ([pct-change-nm](pct-change-nm.md)),
     and `current` and `prior`: the two rows as dicts equal to the input mappings.
2. **The template** — `aq-2c-yoy-growth` keys each case on `s1_yoy_pick` applied to the rows its
   SQL returns for the bound (`ticker`, `fy`, `concept`): the filer's rows of the concept family
   (no segment, `period_type` duration, `duration_kind` annual) from **every** filing of any
   fiscal period, with the columns `s1_lookup_pick` reads, as `aq-1c`'s SQL returns them. No
   pick means no case. A case's expected value is `value`; its expected fact ids are
   `[fact_id_current, fact_id_prior]` in that order (the live agreement test reads the first as
   the current year); unit, `fy_prior` (the third phrasing's `{fy_prior}`), `not_meaningful`
   and `alt_scale: [100]` as today; fact-equivalence classes per fact as today.
   How the template declares this (e.g. `pick: s1_yoy`) is the implementer's choice.
3. Nothing else changes: the other templates (D3), `s1_lookup_pick`, restatement-s1's
   functions, `mv_yoy_comparison` and the product. Two unit tests of
   `test/unit/test_sql_derived_suite.py` pin today's template and fail against the reference
   implementation: `TestLookupOrder::test_every_single_fact_template_uses_the_lookup_order`
   (lists `aq-2c` as using `@LOOKUP_ORDER(c)` in SQL) and
   `TestPeriodScoping::test_no_committed_pick_template_restricts_the_year` (the set of pick
   templates). The implementer updates both (unit tests, not locked).

## Acceptance criteria

Fixtures are byte-identical copies of suite-templates-s1's (`companyfacts.json`, `dei.json`,
`fsds.json`, Notes). Oracle values are FSDS `num` rows of the named accession (`qtrs` 4), an
independent source; the answering filing is chosen by hand from `dei.json` (the latest canonical
filing labelled later than the year that reports its period). Tolerances: amounts at the tagged
precision (`abs=5e4`, half of $0.1M); growth to `abs=1e-6` on the fraction (FSDS values are
exact integers in millions, so the hand ratio is exact to float precision); accessions exact.

- **C1**: both legs follow S1. `s1_yoy_pick` gives:
  - INTC FY2023 revenue: current $54,228M from 0000050863-26-000011, prior $63,054M from
    0000050863-25-000009 (two filings, neither the FY2023 10-K), growth 54,228 / 63,054 - 1;
  - INTC FY2024 revenue: $53,101M and $54,228M, both from 0000050863-26-000011;
  - TSM FY2023 net income: TWD 851,027.7M from 0001628280-26-025362 and TWD 993,294.7M from
    0001193125-25-083423, unit `TWD` (never the FY2023 USD convenience translation 27,793.2M);
  - INTC FY2023 financing cash flow: $8,505M from 0000050863-26-000011 over the restated $1,115M
    from 0000050863-25-000009 (never the FY2022 10-K's $1,361M), growth 8,505 / 1,115 - 1;
  - META FY2023 capex: $27,045M from 0001628280-26-003942 over $31,186M from
    0001326801-25-000017 (never the as-filed $27,266M / $31,431M), growth 27,045 / 31,186 - 1.

  Properties: each leg satisfies S1 checked by hand (it ends on its year's own filing's DEI end
  + 1 day, and no later-labelled canonical filing filed after it reports the same concept, unit
  and period); permuting the rows never changes the result; removing any filing other than the
  two own filings and the two answering filings never changes it; adding a later-labelled
  canonical filing, filed last, that reports the fy (or fy - 1) period with another value makes
  it that leg, and the growth moves to the hand ratio of the new values. Oracle: FSDS `num`; DEI;
  requirement 1.
- **C2**: unchanged without a later report, and no answer without both years. INTC FY2025
  revenue: $52,853M and $53,101M, both from its own 0000050863-26-000011 (no later 10-K), growth
  52,853 / 53,101 - 1, the filing's own comparison; TSM FY2025 net income likewise from
  0001628280-26-025362 in TWD (never the USD 54,036.5M). INTC FY2022 and TSM FY2022 have no
  result (no corpus filing is labelled 2021), nor INTC FY2026 (no FY2026 10-K). Properties:
  removing the fy - 1 own filing's own-column rows makes the result `None` whatever later
  filings report; a prior leg in another unit than the current makes it `None`; a prior value
  <= 0 sets `not_meaningful` with the |base| ratio; a prior value of 0 gives `None`. Oracle:
  FSDS `num`; DEI; requirement 1; pct-change-nm; units-currency-integrity.
- **C3**: the template. `SqlDerivedBuilder` over `aq-2c-yoy-growth` as committed in
  `configs/eval/sql_derived_templates.yaml` and the fixture store (the fake database of
  suite-templates-s1's Notes) emits: INTC FY2023 Revenue with the C1 value, expected fact ids
  `[current, prior]` of C1's rows, unit `USD`, `alt_values` [100 x value]; TSM FY2023 NetIncome
  in `TWD` with C1's rows; INTC FY2025 Revenue as C2; **no** INTC FY2022 or FY2026 Revenue case
  and no OperatingIncome case (the store holds none). With three phrasings, the third question
  names fiscal `fy - 1`. Properties: every case's two expected facts satisfy S1 by hand for fy
  and fy - 1, and its value is their hand ratio; permuting the store's rows never changes any
  case's value or cited facts. Oracle: FSDS `num`, DEI, S1 applied by hand; requirement 2.

## Interface

New, stubbed in the spec PR:

`src/eval/suites/sql_derived.py`:
- `s1_yoy_pick(rows: Sequence[collections.abc.Mapping[str, Any]], fy: int) -> dict | None`

Behaviour changes, no signature change: `SqlDerivedBuilder` (requirement 2), `aq-2c`'s SQL in
`configs/eval/sql_derived_templates.yaml`, and whatever template key declares the pick.

## Decisions (settled for this spec, each with its evidence)

- **D1 — both legs follow the product's lookups.** The product's YoY is two independent lookups
  (`prompts/templates/xbrl/_base/plan_generator/v9/system.txt` Example 2; the compute step only
  divides) and each lookup applies S1 (`src/retriever/xbrl/xbrl_factstore_lookup.py`
  `_select_s1`, line 1563). So the key's prior leg is `s1_lookup_pick(rows, fy - 1)`, not the fy
  filing's comparative: confirmed on all 56 cases (both legs and the value). Renamed to
  `yoy-growth-s1` for that reason.
- **D2 — the semantics' "one filing" rule is not applied here.** Evaluation semantics S1 says
  values of a comparison come from one filing where one filing reports them all. The product
  does not do this today (9 of the 56 cases mix filings); a key that did would grade the product
  wrong on citations. This spec keys what the product cites. Making the product's comparisons
  one-basis (and the key with it) is a separate product spec, if wanted.
- **D3 — other templates, out of scope** (measured on 2026-11-12, citations only, no value
  differs):
  - `aq-1b-revenue-screen`, `aq-5b-total-assets-screen`: the product's flat screen applies S1
    (`XbrlFactstoreScreen._restate`, `src/retriever/xbrl/xbrl_factstore_screen.py:1551`, restatement-s1 R4); the keys pick the fy-labelled filing's
    row. 6 of 15 aq-1b rows and 12 of 15 aq-5b rows cite a fact outside the key's classes. A
    screen pick is a different mechanism (fiscal-year label, USD, then `latest_restatement`,
    per filer, before ranking); follow-up spec.
  - `aq-2a-yoy-revenue-screen`: the product ranks `mv_derived_metrics` `RevenueGrowthPct`
    (current and prior from each year's own filing); the key reads `mv_yoy_comparison`. Every
    ranked row's citations differ. Derived screens are restatement-s1's "later spec".
  - `aq-7a-derived-metric-lookup` (`mv_derived_metrics`, each year's own filing): the product
    answers with S1 lookups and a compute, so 35 of 56 cases have a component the lookup would
    cite from another filing. Eight formulas with several inputs each; follow-up spec.
  - `aq-7b-margin-screen` (`mv_derived_metrics`) and `aq-4c-china-exposure-screen`
    (`mv_geographic_facts`): the product's derived and geographic screens are not on S1 either;
    nothing to align.
- **D4 — retired spec tests**: none pin `aq-2c`'s choice. Every spec directory passes against the
  reference implementation (Notes).

## Notes

- **Fixtures** (`test/spec/yoy-growth-s1/fixtures/`): byte-identical copies of
  `test/spec/suite-templates-s1/fixtures/` (same SHA-256), whose provenance is in that spec's
  Notes: companyfacts entries of INTC, META, LLY, COST, TSM (sources and hashes inside the file),
  DEI of their 23 corpus filings from each iXBRL instance, FSDS `sub`/`num` rows of the oracle
  filings with each quarter archive's URL and SHA-256. Copied, not shared, because a spec's
  fixtures are its own; nothing under the operator's data directory is read by the tests.
- **Store rows and fake database**: as suite-templates-s1's Notes (one row per companyfacts entry
  of a corpus filing; TSM from FSDS `num`; `concept_pref_rank` 1, `presented` false, `decimals`
  null; the fake returns every store row matching the SQL's equality predicates on `ticker`,
  `concept_family`, `unit`, `period_type`, `duration_kind`). Year selection must therefore happen
  in the pick, and the template SQL must not restrict `fiscal_year` / `fiscal_period`.
- **Reference implementation** (throwaway, not committed): `s1_yoy_pick` as requirement 1 on top
  of `s1_lookup_pick`, a `pick: s1_yoy` branch in `SqlDerivedBuilder._cases_for`, and `aq-2c`'s
  SQL replaced by aq-1c's with `concept = %(concept)s`. With it every directory of `test/spec`
  passes, and the dry build above changes only the 32 aq-2c cases.
- **Live check after implementation** (the maintainer, after the rebuild):
  `pytest -m integration test/integration/test_v1_suite_lookup_agreement_live.py` passes on every
  aq-2c case.
