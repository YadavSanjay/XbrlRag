# s4-screen-defined-members — a Debt or DeferredRevenue screen ranks every filer by its S4 measure

The flat cross-filer screen sets aside, before the S4 defined measure runs, every filer whose
first candidate row is not a primary of the family. For `Debt` and `DeferredRevenue`
(`DEFINED_FAMILIES`) that drops filers whose balance sheet presents the debt as a current line
plus a long-term line, with no total: their long-term line is the stated substitute
`LongTermDebtNoncurrent` / `LongTermDebtAndCapitalLeaseObligations` (`excludes_current_portion`)
and their current line a component. They never get the measure their own balance sheet
defines, and drop out of every Debt screen.

*Named for its scope:* the filers concerned are not all "part-only" in the mapping's sense
(PG has a substitute and a component; KO a substitute only), so the change is stated for every
member of a defined family's screen, whatever the roles of its candidate rows.

**Where (origin/main 65bf9b9):** `XbrlFactstoreScreen._query_flat`
(`src/retriever/xbrl/xbrl_factstore_screen.py`:1401) calls `select_preferred_concept` (:332,
the listing apart at :370–379), and only the kept rows reach the currency check (:1408–1421),
S1 `_restate` (:1422) and `_apply_defined_measures` (:1423–1426). The lookup does not have
the defect: `_build_result` keeps the candidates on the component-only path
(`xbrl_factstore_lookup.py`:1092–1103) and `_defined_column` (:901–915) builds the measure
from `candidates[order[0]]`, else `candidates[0]` (the query's order: latest period, lowest
rank, larger fact id; :1541 and `_select_s1` :1591–1595).

**Rules it breaks:** [s4-lookup-deferred-revenue-debt](s4-lookup-deferred-revenue-debt.md)
requirement 10 (a flat screen ranks one measure, the S4 definition) and 11 (the value never
depends on the question's shape); [key-definitions](key-definitions.md) requirement 6 (a
defined member is ranked by its measure; `not_presented` is `metric_not_tagged`).
[screen-candidate-order](screen-candidate-order.md) R1's listing apart ("a screen never ranks
a substitute") is right for a family whose answer is a fact; for a defined family the answer is
the measure, and the pick only names the filing and date (screen-candidate-order R4 already
says so: "the S4 defined measures ... the pick only names the filing and date").

**Effect (xbrl_db, read-only, 2026-11-13).** Filers listed `component_only` in the covered
Debt screens: FY2022 AAPL, AVGO, META, VST; FY2023 AVGO, COST, NFLX, PG; FY2024 AVGO, COST,
KO, NFLX, PG; FY2025 COST, KO, NFLX, PG; FY2026 COST, KO, NFLX, PG (KO and NFLX there with
their FY2025 rows: no FY2026 10-K). DeferredRevenue: none. In case `int-60f310828b3f-007d70d4`
(run `eval-a2aed28f410e`) Qualcomm is ranked 19 of 28 against the key's 21 of 32.

## Requirement

### 1. The measure's column (`family_resolution.measure_column`)

`measure_column(rows)` returns the index of the row whose filing (`accession_no`) and date
(`period_end`) a defined measure is built from, or None for no rows:

- the first index `candidate_order(rows)` returns, when it returns any;
- otherwise (every row a component) the row with the latest `period_end`, then the lowest
  `concept_pref_rank`, then the larger `fact_id`; a missing `period_end` or rank sorts last.

The result names the same row whatever the order of `rows`. On rows in the lookup's query
order (`period_end` descending with None last, rank ascending with None last, `fact_id`
descending) it is `candidate_order(rows)[0]` when that exists, else `0`: today's
`_defined_column`, which uses it (wiring) with no change of behaviour.

### 2. The screen's picks (`select_screen_picks`)

`select_screen_picks(rows, concept_family)` takes the flat screen query's candidate rows
(several per filer, any years) and returns `(kept, component_only)`:

- `concept_family` not in `DEFINED_FAMILIES`: exactly `select_preferred_concept(rows)`
  (screen-candidate-order R1, unchanged);
- `Debt` or `DeferredRevenue`: per ticker, the rows of the filer's latest `fiscal_year`
  (as R1 takes them), and of those the row `measure_column` names (the input dict, unchanged);
  `component_only` is empty.

`kept` is sorted by ticker, holds every input ticker exactly once (defined family), and never
depends on the rows' order.

### 3. The flat screen of a defined family

`_query_flat` takes its picks from `select_screen_picks`. Everything after the pick is today's
code, now reached by every pick:

1. the reporting-currency check (`select_reporting_currency` over the filer's observations of
   the family, components included): a filer reporting in another currency is `currency`;
2. S1 restatement (`_restate`) of each fiscal-year-`fy` pick;
3. `_apply_defined_measures`: a `ranked` measure is the member's row (its value, the first
   part as the citation, every part in `component_fact_ids`); a `metric_not_tagged`
   (`not_presented`) or `excluded` (`unit_mismatch`, `conflicting_facts`) member is not
   ranked and its reason goes to the disclosure, as for a primary pick today.

A fiscal-year-`fy` member is ranked **only** by its `ranked` measure: a pick that is not a
primary and reaches no measure (no accession or period end to read) is listed
`component_only`, never ranked on its own row. A pick of an earlier fiscal year is left to
`select_fiscal_year` (`period_not_filed`), as any other filer's. The ranked value, its order
and its member set never depend on the row order or on the other filers in the screen.

Families without a definition are unchanged.

### 4. Disclosures

No reason code or text changes. A defined-family member not ranked shows the reason
`screen_member` gives (`metric_not_tagged` → "metric not reported"; `unit_mismatch`,
`conflicting_facts`), as the key's `member_status` does; `component_only` ("reports only
components of the metric") remains only for requirement 3's no-measure pick and for every other
family.

## Acceptance criteria

The oracles are SEC data (`fixtures/`, FY2025 10-Ks of P&G, Coca-Cola and Qualcomm) and hand
addition of the FSDS balance-sheet lines; values are whole dollars from SEC, so value checks use
`abs=0.5`.

- **C1** (`measure_column`): P&G's candidate rows name its `LongTermDebtNoncurrent` row (the
  first answerable, a substitute); its component rows alone name `DebtCurrent`; Qualcomm's name
  `LongTermDebt` (the presented primary); no rows give None. Properties: for any rows the result
  is requirement 1's definition written out in the test; permuting the rows names the same row;
  on rows in the lookup's query order it is `candidate_order(rows)[0]` or `0`. Oracle:
  requirement 1; the committed mapping's roles (`family_member`); FSDS `pre`.
- **C2** (`select_screen_picks`): for `Debt` the three filers are all kept (KO's
  `LongTermDebtAndCapitalLeaseObligations`, PG's `LongTermDebtNoncurrent`, QCOM's `LongTermDebt`)
  and none is apart; the same rows under a family without a definition give
  `select_preferred_concept`'s answer (KO and PG apart). Properties: for any rows a family
  outside `DEFINED_FAMILIES` gives `select_preferred_concept(rows)`; a defined family keeps every
  ticker once, its latest year's `measure_column` row; permuting the rows changes nothing.
  Oracle: requirement 2; screen-candidate-order R1.
- **C3** (ranked by the measure): the covered FY2025 Debt screen ranks KO 45,492,000,000
  (1,551 + 1,822 + 42,119 M), PG 34,508,000,000 (9,513 + 24,995 M) and QCOM 14,811,000,000
  (0 + 14,811 M), in that order, each citing its first part and listing exactly its borrowing
  lines' facts; P&G's equity line "Reserve for ESOP debt retirement" is no part; nothing is
  excluded. P&G with only its component rows gives the same value. Properties: any permutation of
  the query's rows, any subset of the filers and any subset of a filer's candidate rows (one at
  least) leave each ranked member's value and the ranking order unchanged. Oracle: FSDS `num`
  lines of the three 10-Ks added by hand; s4-lookup requirement 10.
- **C4** (members not ranked): P&G with its borrowing lines off the balance sheet (the facts
  still tagged) is `metric_not_tagged` and its disclosure reads "PG (metric not reported)"; a
  Coca-Cola borrowing line in EUR is `unit_mismatch`; a P&G pick with no accession is
  `component_only`; a P&G latest year of FY2024 is `period_not_filed`; the other filers stay
  ranked at their sums. Property: for any set of filers whose borrowing lines are removed, those
  and only those are `metric_not_tagged`. Oracle: requirements 3 and 4; s4-lookup requirement
  10; key-definitions requirement 6.
- **C5** (other families unchanged): the same candidate rows under a family without a definition
  are `select_preferred_concept`'s: KO and PG `component_only`, QCOM ranked on its
  `LongTermDebt` row, and no balance sheet is read. Property: for any subset of the rows the
  ranked set and values are `select_preferred_concept`'s kept rows and the `component_only`
  exclusions its listed-apart filers. Oracle: screen-candidate-order R1.
- **C6** (one member set with the key): each filer's screen member equals the key side's
  `member_status(debt_key(...))` on the same SEC filing, in status and value. Property: for any
  subset of the filers, any removal of borrowing lines and any subset of candidate rows, the
  screen's ranked members and values are exactly the key's `ranked` members, and every key
  `metric_not_tagged` member is excluded as `metric_not_tagged`. Oracle: the key-side functions
  (metamorphic), key-definitions requirements 2 and 6.

## Interface

Stubbed in the spec PR:

- `src.retriever.xbrl.family_resolution.measure_column(rows: Sequence[Mapping[str, Any]]) -> int | None`
- `src.retriever.xbrl.xbrl_factstore_screen.select_screen_picks(rows: collections.abc.Sequence[dict], concept_family: str) -> tuple[list[dict], list[dict]]`

Changed behaviour, same signatures (wiring, exercised by C3–C6 through
`XbrlFactstoreScreen.screen` over a fake database): `_query_flat` (requirement 3),
`XbrlFactstoreLookup._defined_column` (uses `measure_column`; no behaviour change).
`select_preferred_concept`, `_apply_defined_measures`' contract and every other family's path
are unchanged.

## Notes

### Fixtures and the fake database

- `fixtures/fsds/<quarter>/{sub,pre,num,tag}.tsv`: byte copies of lines of SEC's FSDS zips
  (2025q3, 2025q4, 2026q1; URLs and SHA-256 in `fixtures/provenance.json`) for PG
  0000080424-25-000076, QCOM 0000804328-25-000085 and KO 0001628280-26-010047: every
  balance-sheet `pre` line, the undimensioned instant `num` rows, the lines' `tag` rows.
- `fixtures/companyfacts_excerpt.json`: every us-gaap USD instant entry SEC's companyfacts lists
  for those filings (cached `data.sec.gov/api/xbrl/companyfacts` documents; SHA-256 of each in
  the provenance).
- The conftest's `FakeConn` replaces `psycopg2.connect` (the only boundary) and serves each
  read by the table it names; the store and its rules are in the conftest's docstring. The
  candidate rows' roles come from the committed mapping, so the tests read the mapping as
  committed (PG: `DebtCurrent` component 50, `LongTermDebtNoncurrent` substitute 53; KO:
  `LongTermDebtAndCapitalLeaseObligations` substitute 54; QCOM: `LongTermDebt` primary 1,
  `DebtLongtermAndShorttermCombinedAmount` primary 2, the same as `xbrl_db`). Balance-sheet
  labels are FSDS `plabel`s as the filer's own labels, which is what the key side reads.
- Statement-line rules: the live `configs/eval/v2/statement_lines.yaml` on both sides (the
  system's `defined_rules` reads it; key-definitions C7 locks its Debt entry).

### Decisions (recommendations, with evidence)

1. **Every non-primary filer of a defined family gets a pick, not only component-only ones.**
   The filers dropped today are substitute-first (`LongTermDebtNoncurrent`,
   `LongTermDebtAndCapitalLeaseObligations` are Debt substitutes, `concept_family_coverage.yaml`
   `substitutes.Debt`); a fix limited to component-only filers would change nothing in the
   corpus. The lookup already builds the measure from a substitute's column (`_defined_column`
   takes `candidate_order`'s first row).
2. **Shared column rule.** `measure_column` is the lookup's `_defined_column` rule made
   order-independent; the lookup uses it. The fallback `candidates[0]` relies on the query's
   order, which the screen's rows do not have (`ORDER BY ticker, fact_id`); requirement 1 states
   that order explicitly.
3. **Reasons follow the key.** A defined member whose measure is not presented is
   `metric_not_tagged`, as `member_status` (key-definitions R6) and as a primary pick is today;
   `component_only` keeps its text and only marks a pick that reached no measure. A pick of an
   older year becomes `period_not_filed` (today, FY2026's KO and NFLX are listed
   `component_only` with their FY2025 rows; the key says `period_not_filed`).
4. **Not a mapping change.** As s4-lookup decision 1: `concept_mapping`, the views and the suite
   builder are untouched; the keys cannot move (the v2 key path imports no `src.retriever.xbrl`
   module: `src.eval.suites.v2_suite`, `v2_witness`, `src.eval.keys.key_definitions`).
5. **screen-candidate-order R3 (`fallback`)** decides facts-count ranks for `DebtIssued` and
   `CashAndEquivalents` only; no defined family is listed, so no interaction.
   `select_reporting_currency` now sees the new picks; it reads every unit of the family's
   observations, so a non-USD filer is still `currency` (TSM: unchanged).

### Consumers of the screen's rows for a defined family (none changes code)

Ranked screens (`screen.top` / `screen.bottom`), `aggregate` sum and median
(`screen_aggregate.aggregate_screen_rows`), peer_relative (rank and universe median over the
rows), compound dependent asks (`<<TOP:id:n>>` injects the top-n ranked tickers), the currency
check, S1 `_restate`, `_apply_defined_measures`, the disclosure (`build_screen_disclosure`:
ranked list and count, exclusions, period ends; the "Universe: ... N ranked" and "Not ranked:"
sentences of `screen_disclosure_text`), the verifier's screen evidence
(`screen_evidence.py`: ranked count, exclusions) and the synthesizer's universe sentence, which
reads the same disclosure. Each gains the new members and loses their `component_only` entries.

### Dry check on frozen material ($0, read-only `xbrl_db`, reference implementation)

Every Debt and DeferredRevenue ranking of the K6 frozen suites (`data/eval/v2_frozen`: dev,
validation, dev_sample, dev and validation compound): 27 rankings, 8 distinct screens; the v1
suite has no Debt or DeferredRevenue screen. Keys are untouched (decision 4).

| Case(s) | Ranking | Today vs key | With this change |
|---|---|---|---|
| dev `int-60f310828b3f-007d70d4`, `-009d93e9`; validation `-00471ff0` (peer_relative) | Debt FY2025 covered | QCOM 19 of 28; COST, KO, NFLX, PG missing | QCOM 21 of 32 = key; median 29,953.5M unchanged |
| validation `int-556d7f04ac46` ×3 (median) | Debt FY2022 covered | 12,823.5M; AAPL, AVGO, META, VST missing | 13,661M = key |
| validation `int-a6fa57305fc3` ×3 (median) | Debt FY2023 covered | 17,922M = key; AVGO, COST, NFLX, PG missing | 17,922M, all 32 members ranked |
| dev compound `int-21e0eddbbb5b` ×3 (leader_growth), `int-684520183450` ×2, validation compound `-03e5d45d` (rank_by_ratio) | Debt FY2024 covered, top 3 | AAPL, ORCL, HD | AAPL, ORCL, AVGO = key (the leader, AAPL, is unchanged; rank_by_ratio's ratios now include AVGO, not HD) |
| dev `int-addfb630e7ce` ×2, validation ×1 (sum) | Debt FY2025 semiconductors | = key | unchanged |
| dev `int-8ca92f363f0e` ×1, validation ×2 (screen.bottom) | DeferredRevenue FY2024 retailers | COST ranked, the key excludes it | unchanged (a different defect) |
| dev compound `int-7a3c37c6524f` ×3, `int-f3864a210cd8` ×3 | DeferredRevenue FY2024 / FY2023 | = key | unchanged |

After the change every Debt ranking ranks exactly the key's members in the key's order. One
value differs: COST FY2025 5,788M (S1, its FY2026 10-K's comparative) against the key's 5,713M,
which waits for FSDS 2026q4 (K6 notes); its rank is the same. The paraphrased suites hold the
same intents (dev: peer_relative 5, others unchanged; validation: peer_relative 3, FY2022 median
9). Per screen, no ranked member's value changes; only members are added (FY2022 AAPL 120,069M,
AVGO 39,515M, VST 12,621M, META 9,923M; FY2025 KO 45,492M, PG 34,508M, NFLX 14,462.836M, COST
5,788M; ...). Answers change for 3 + 3 + 6 = 12 frozen cases (peer rank, FY2022 median, the
FY2024 top three), plus their paraphrases; the FY2023 medians change only their disclosure.

### Retired tests and collection

Running every spec directory against the reference implementation: 6,587 passed; two
property tests (`reviewed-resolutions/test_c4_build_entry.py::test_any_subset_is_admitted`,
`suite-templates-s1/test_c4_templates.py::test_row_order_never_changes_a_case`) failed only
under random seeds with xdist and pass under the `tdd-gate` profile and on unchanged main: no
retired spec test pins the listing apart of a defined family (screen-candidate-order C1 tests
`select_preferred_concept`, which keeps its behaviour). `test/unit` passes with it (3,499).
Test files here carry an `s4sdm` prefix; no basename clashes under `test/spec`.

### Out of scope

- Members the flat query never sees (no candidate row of the family): the key may rank a filer
  whose borrowing lines no Debt member tags (the key reads the balance sheet, the screen starts
  from family rows). None in the frozen rankings after this change.
- `component_only` listing an older year's row for families without a definition (requirement
  3's `period_not_filed` applies to defined families only).
- The DeferredRevenue retailers' COST inclusion.
