# calc-check-c11 — the sum-of-parts check per XBRL Calculations 1.1, counted per filing

Area H of [`XbrlRag_Test_Strategy.md`](../design/XbrlRag_Test_Strategy.md). The current
check (`scripts/ddl/mv/12_mv_calculation_network.sql`, `40_mv_sum_of_parts_checks.sql`) fails
93.5% of its rows (9,757 of 10,431, read-only investigation of `xbrl_db`, 2026-11-07). Almost
all of that is four bugs in the check, not bad values:

| Cause | Rows |
|---|---:|
| Each arc counted 1–7 times: `mv_calculation_network` joins `custom_role_type` on `uri_id` only, and filers reuse a role URI across years (78,034 MV rows for 22,807 real arcs) | 4,948 |
| Repeated iXBRL facts (the same number tagged twice) summed instead of counted once | 3,321 |
| Wrong matching: start date ignored (a 10-Q quarter mixes with year-to-date), only the first dimension compared, unit ignored | 645 |
| A fixed 0.1% tolerance instead of rounding by `decimals` | 48 |
| Still failing under correct Calculations 1.1 | 795 |

The view also reads only the 2003 `summation-item` arcrole, so the 39 of 151 filings that use
the 2023 (Calculations 1.1) arcrole are never checked and pass by default. GATE-B in
`src/ingest/sec_edgar/xbrl/promotion.py` counts failures per (cik, fiscal year, fiscal
period), so two filings for the same period (an original and its amendment, or a 10-K and the
next year's comparatives) share one count.

With the four bugs fixed (simulated over all 151 filings, 23,773 checks), 0.87% of checks fail
when every child is reported and the context has no dimensions: **131 strict failures in 23
filings**. They are filer errors stored faithfully (hand-checked: AMD's sign, MCD's
precision), so they are listed as known exceptions rather than fixed.

## Requirement

1. **Check.** `check_calculations(facts, arcs)` checks one filing's facts against its
   calculation arcs per XBRL Calculations 1.1, round-to-nearest. It is pure, and is the
   reference the SQL view mirrors.
   - **Arcs.** Only arcs whose arcrole is
     `http://www.xbrl.org/2003/arcrole/summation-item` or
     `https://xbrl.org/2023/arcrole/summation-item` are used; any other arcrole is
     ignored. A tree is one (`link_role`, `parent`). Each (`link_role`, `parent`, `child`)
     counts once, however many times it appears in the input and under whichever of the two
     arcroles. If the copies of one (`link_role`, `parent`, `child`) carry different weights,
     every result of that tree is `inapplicable`.
   - **Context.** A context key is (`entity`, `period_start`, `period_end`, `dimensions`,
     `unit`), compared exactly: the start date too (an instant has `period_start=None`),
     every (axis, member) pair as a set (no subset or first-dimension match), and the unit.
   - **Fact groups.** The facts of one concept at one context key are one group (repeated
     iXBRL facts and duplicates at other precisions). A group is:
     - *absent*: no facts;
     - *nil*: every fact is nil;
     - *inconsistent*: some but not all facts are nil; or two facts with the same `decimals`
       have different values; or a fact has excess digits (`decimals` = *d* not None and the
       value is not a multiple of 10^(−*d*)); or the intervals below do not intersect;
     - otherwise it has the interval ∩ [*v* − ½·10^(−*d*), *v* + ½·10^(−*d*)] over its facts,
       closed at both ends (`decimals=None` is exact: [*v*, *v*]). For consistent duplicates
       this is the most precise fact's interval, so a duplicate counts once.
   - **Binding.** A result exists for a tree at a context key when the parent's group is
     neither absent nor nil, and at least one child's group is neither absent nor nil.
     Every context key is checked, not only the current period. Nothing else yields a result.
   - **Status.**
     - `inapplicable` when the parent's or any child's group is inconsistent (Calculations
       1.1: inconsistent duplicates stop the check), or the tree's weights conflict. Its four
       bounds are None.
     - Otherwise the reported interval is the parent's, and the computed interval is the sum
       over children with an interval of `weight` × [lo, hi] (ends swapped when the weight is
       negative); absent and nil children add nothing. The summation is consistent when the
       two closed intervals intersect: max(lows) ≤ min(highs).
     - Consistent: `pass`, even when some children are absent.
     - Not consistent and some child absent: `partial`.
     - Not consistent and no child absent: `fail`.
   - **Fields.** `dimensional` is `bool(dimensions)`. `parent_fact_ids` is every fact of the
     parent's group; `child_fact_ids` every fact of every child's group at that key (nil and
     duplicates included); `missing_children` the children whose group is absent; all three
     sorted ascending. Bounds are exact `Decimal`s, never floats.
   - **Order.** The list is sorted by (`link_role`, `parent`, `entity`, `period_start` or "",
     `period_end`, sorted `dimensions`, `unit`); it does not depend on input order.
2. **Strict failure.** A result with status `fail` and `dimensional` False. `partial`,
   dimensional `fail` and `inapplicable` results are recorded (counted separately), never
   gate.
3. **Exception list.** `configs/ingest/calc_c11_exceptions.csv`, committed and shrink-only:
   it may only lose rows. Columns `accession_no,link_role,parent,period_start,period_end,unit`
   (an empty `period_start` is an instant, read as None), one header row, no comments. It is
   seeded with the 131 strict failures (`test/spec/calc-check-c11/fixtures/seed_exceptions.csv`);
   every row it holds must be a seed row. `load_calc_exceptions(path)` reads it into keys.
4. **Gate.** `calc_gate_decision(accession_no, results, exceptions)` takes one filing's
   results. A strict failure's key is (`accession_no`, `link_role`, `parent`, `period_start`,
   `period_end`, `unit`). It is *excepted* when its key, with this filing's accession, is in
   `exceptions`; otherwise it is *new*. The filing is held back exactly when it has a new
   strict failure. Counts: `strict_failures` (all), `excepted_failures`, `new_failures`
   (distinct keys, sorted field by field with a None `period_start` as ""), `partial`,
   `dimensional_failures` (status `fail` and dimensional), `inapplicable`. An exception for another accession never excuses this filing's failure:
   counting is per filing, not per (cik, fiscal year, fiscal period).
5. **Wiring (not locked; implementer).** Rewrite `12_` (both arcroles; no fan-out from
   `custom_role_type`) and `40_` (this rule set, every context, per filing) to mirror
   `check_calculations`, with a unit test that the view and the function agree on the
   fixture filings. GATE-B counts strict failures per accession through
   `calc_gate_decision`; enforcement stays behind `XBRLRAG_PUBLISH_GATE_B_ENFORCE`. Update
   `src/ingest/sec_edgar/CLAUDE.md` and the view headers.

## Acceptance criteria

- **C1**: Arcs. Apple's 10-Q `0000320193-26-000013` (2023 arcrole only) gives two `pass`
  results for `Assets`. NVIDIA's 24 `mv_calculation_network` rows for `Assets` (each of 8
  arcs three times, from the reused role URI) give exactly the results of the 8 arcs. Arcs
  under another arcrole add nothing. Bounds exact. Oracle: the filing's balance sheet
  (147,957 + 211,284 = 359,241; 144,114 + 226,968 = 371,082), by hand; Arelle 2.41.0
  `--calc c11r` reports no inconsistency in either tree. Property: repeating any arcs, in any
  number, leaves the results unchanged.
- **C2**: Context and unit. Intel's 10-Q `0000050863-26-000157` gives four `GrossProfit`
  results, one per period (two quarters, two six-month year-to-date), each `pass` with
  only its own period's facts. A fact whose dimensions are a superset or subset of the
  parent's, or whose unit or entity differs, never binds. Oracle: the statement of
  operations by hand (16,128 − 9,619 = 6,509; 29,705 − 17,849 = 11,856; 12,859 − 9,317 =
  3,542; 25,526 − 17,312 = 8,214). Property: adding facts at a new context key never changes
  any other key's result.
- **C3**: Duplicates. NVIDIA's 10-K `0001045810-26-000021` `Assets` passes in both years,
  with Goodwill (20,832 at −6 and 20.8B at −8), deferred tax assets (13,258 and 13.3B) and
  PP&E (two equal facts) each counted once: computed [206,799, 206,807] million against
  reported [206,802.5, 206,803.5]. A group with two facts at the same `decimals` and
  different values, a nil and a non-nil fact, or excess digits makes the result
  `inapplicable`; a nil-only parent gives no result. Oracle: the balance sheet by hand;
  Calculations 1.1 duplicate rules. Property: adding an exact copy of any fact (new id)
  changes no status or bound.
- **C4**: Rounding. McDonald's 10-K `0000063908-25-000012` `Assets` fails at both balance
  sheet dates: 2024-12-31 reported [55,181.95, 55,182.05] million (decimals −5) against
  computed [55,182.8, 55,183.2]; 2023-12-31 [56,146.95, 56,147.05] against [56,145.35,
  56,146.65]. Touching closed intervals pass. The fixture filings' non-`pass`,
  non-`inapplicable` results equal Arelle's inconsistencies in the same trees. Oracle: the
  filing's balance sheet by hand (4,599 + 11,950 + 13,339 + 25,295 = 55,183 ≠ 55,182);
  Arelle 2.41.0 `--calc c11r`, recorded in each fixture. Property: multiplying every value
  by 10^k and subtracting k from every `decimals` multiplies the bounds by 10^k and changes
  nothing else.
- **C5**: Status and scope. AMD's 10-K `0000002488-24-000012`: the FY2021 column fails
  (fact 18008 tagged +1,895 while the page shows (1,895); computed [−1,898, −1,892]
  million) and FY2022 and FY2023 pass, so prior-period columns are checked. A missing child
  gives `partial` when the rest does not reconcile and `pass` when it does; a dimensional
  context gives `fail` with `dimensional` True. Oracle: the cash flow statement by hand
  (104 − 237 − 1,762 = −1,895); Arelle reports the FY2021 context only. Property: permuting
  facts and arcs never changes the result list.
- **C6**: Gate. With the seed list, McDonald's `0000063908-25-000012` is not held back
  (2 strict failures, both excepted); with no exceptions it is held back with those 2 keys
  new; an exception under McDonald's next 10-K `0000063908-26-000035` does not excuse it.
  `partial`, dimensional and `inapplicable` results never hold a filing back. Oracle: the
  rule. Property: removing exception rows never releases a held-back filing, and adding
  non-strict results never changes `held_back`.
- **C7**: Exception list. The seed has 131 rows over 23 filings and holds the MCD and AMD
  failures above; the committed list loads to a subset of the seed. Oracle: the
  investigation's count (131 checks, 23 filings), and the hand checks of C4/C5. Property:
  any subset of seed rows written to a CSV loads to exactly those keys.

## Interface

Plain mappings in and dicts out, so the SQL view's rows and the gate's DB rows map onto them
directly (the implementer may build typed helpers inside the module).

- `src.ingest.sec_edgar.xbrl.calc_check.check_calculations(facts: Iterable[Mapping[str,
  Any]], arcs: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]`
  - a fact has the keys `fact_id` (int), `concept`, `entity`, `period_start` (None for an
    instant), `period_end`, `dimensions` (any iterable of (axis, member) pairs), `unit`,
    `value` (`Decimal`, or None when nil), `decimals` (int, or None for `INF`);
  - an arc has `arcrole`, `link_role`, `parent`, `child`, `weight` (`Decimal`);
  - a result has exactly the keys `link_role`, `parent`, `entity`, `period_start`,
    `period_end`, `dimensions` (frozenset of (axis, member) tuples), `unit`, `status`
    (`"pass"`, `"fail"`, `"partial"` or `"inapplicable"`), `dimensional` (bool),
    `reported_low`, `reported_high`, `computed_low`, `computed_high` (`Decimal` or None),
    `parent_fact_ids`, `child_fact_ids` (tuples of int), `missing_children` (tuple of str).
- `src.ingest.sec_edgar.xbrl.calc_check.calc_gate_decision(accession_no: str, results:
  Iterable[Mapping[str, Any]], exceptions: Collection[tuple]) -> dict[str, Any]`
  - an exception key is the tuple (`accession_no`, `link_role`, `parent`, `period_start`,
    `period_end`, `unit`);
  - the decision has exactly the keys `accession_no`, `held_back` (bool),
    `strict_failures`, `excepted_failures` (int), `new_failures` (tuple of keys),
    `partial`, `dimensional_failures`, `inapplicable` (int).
- `src.ingest.sec_edgar.xbrl.calc_check.load_calc_exceptions(path: str | Path) ->
  frozenset[tuple]`

## Notes

- **Oracle.** XBRL Calculations 1.1 (XBRL International Recommendation, 2023),
  round-to-nearest: closed value intervals from `decimals`, duplicate facts by interval
  intersection, inconsistent duplicates stop the check. Arelle's implementation
  (`arelle/ValidateXbrlCalcs.py`, `--calc c11r`) is the reference; its excess-digits rule is
  taken from there.
- **Arelle cross-check, 2026-11-07.** Arelle 2.41.0 (py312 env), `arelleCmdLine -f <local
  .htm> --calc c11r --internetConnectivity offline` on the five fixture filings. Its
  inconsistency counts over the whole filing equal the corrected simulation's failure counts:
  MCD 32, AMD 28, NVIDIA 23, Intel 13, Apple 0. In the fixture trees it reports exactly MCD
  `Assets` (both dates) and AMD FY2021 financing, with the bounds in C4/C5.
- **Fixtures** (`test/spec/calc-check-c11/fixtures/`): real excerpts from `xbrl_db`
  (read-only, 2026-11-07): one tree's arcs from `network`/`relationship`, and every fact of
  its concepts in the filing (all contexts, dimensions from `context_dimension`, unit from
  `unit_measure`). Each was checked against the filing's rendered page (local copy under
  `data/storage/sec_edgar/`). `xbrl_db` dates an instant and a duration end as the next day
  (2025-01-01 is the 2024-12-31 balance sheet); the check compares dates as given.
  Concept names are `namespace:local`, as the views store them.
- **Seed list.** The 131 strict failures from the investigation
  (`data/eval/calc_check/report.md`, `calc11_full_population.csv`: every child reported, no
  dimensions, failing), in the same key form.
- **Out of scope.** Ingest running Arelle itself as a gate; warnings on cited facts in a
  sign-flip failure; Calculations 1.1 truncation mode.
