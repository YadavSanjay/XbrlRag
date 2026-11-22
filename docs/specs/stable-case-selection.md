# stable-case-selection — choose eval cases per (ticker, fiscal year), deterministically

Step 1 of the accuracy plan's next steps (suite refresh), in
[XbrlRag_Accuracy_Plan_Oct2026.md](../design/XbrlRag_Accuracy_Plan_Oct2026.md) §4.

## Requirement

The SQL-derived suite builder caps each template at `max_cases_per_template` (40 in the
`standard` tier) by stopping its parameter enumeration after 40 cases. The enumeration is
a cartesian product with `ticker` outermost in alphabetical order, so a capped template
covers only the first few tickers. In the committed 382-case suite:

- `aq-8b-multidim-lookup`: all 40 cases are Apple.
- `aq-3d-segment-member-lookup`: Apple, AMD and Amazon only.
- `aq-1c-pnl-line-lookup`, `aq-5a-balance-sheet-lookup` and `aq-7a-derived-metric-lookup`:
  AAPL through AVGO only.

So the suite measures a handful of filers on 7 of its 18 templates, and any new filing for an
early ticker reshuffles which cases survive. A rebuild on today's corpus yields 396 cases, not
382, with different members: Micron FY2024 growth cases drop out and MSFT FY2026 cases come
in. Run-to-run accuracy comparisons then mix a change of case set with a change of system.

After this change, a tier can cap a template **per stratum**, a stratum being one
(ticker, fiscal year). The builder chooses, in each stratum, the combinations whose
selection key ranks lowest under a fixed hash. The selection then depends only on what that
stratum holds:

- New data for one filer-year never changes another stratum's cases.
- Adding a combination to a stratum displaces at most one of its cases.
- The order in which tickers or years are listed does not matter.

## Acceptance criteria

- **C1**: `select_stratified(combos, per_stratum, *, ticker=None)` returns the combos (dicts
  of bound parameters) to keep, as a subsequence of `combos` in their input order:
  - **Stratum.** A combo's stratum is `(combo.get("ticker", ticker), combo.get("fy"))`. The
    keyword `ticker` is the template's pinned ticker, used when the combo binds none.
  - **Within a stratum.** Keep the `min(per_stratum, n)` combos whose selection key has the
    smallest SHA-1 hex digest.
  - **Selection key.** `"-".join(f"{k}={combo[k]}" for k in sorted(combo))`, UTF-8 encoded.
    It is the same string `case_key` hashes for long keys.
  - **Ties** (identical keys) keep the earlier combo.
  - **No quota.** `per_stratum=None` keeps every combo.
  - **Bad quota.** `per_stratum < 1` raises `ValueError`.

  Oracle: the rule above, computed in the test with `hashlib`.
- **C2**: Metamorphic properties of `select_stratified`:
  - Permuting `combos` changes only the order of the result, not its membership.
  - Adding combos to other strata leaves a stratum's selection unchanged.
  - Adding one combo to a stratum changes at most one of that stratum's selected combos.
  - Each stratum keeps exactly `min(per_stratum, n)` combos.

  Oracle: the selection depends only on each combo's own key and its stratum's contents.
- **C3**: When a tier sets `max_cases_per_stratum: k`, `SqlDerivedBuilder.build` keeps, per
  template, exactly the parameter combinations that `select_stratified` selects with
  quota `k`. It selects among the combinations that produced a case: a combination with no
  rows, an ambiguous or zero value, or no fact ids does not use up quota.
  - Each kept combination emits its `paraphrases` cases, as today.
  - `stats.per_template[id]` counts the emitted cases.
  - `stats.skipped_capped` counts the combinations dropped by the quota.

  Oracle: with a fake fact store, 12 tickers × 2 years × 6 concepts and `k = 2` gives 2 cases
  per (ticker, year): 48 in total, every ticker present, and `skipped_capped = 96`.
  Hand count in the test.
- **C4**: A template's own `max_per_stratum: m` overrides the tier's `max_cases_per_stratum`
  for that template only. Oracle: the same fake store with `m = 1` gives 24 cases for that
  template, while a second template keeps `k = 2` per stratum.
- **C5**: A tier that sets both `max_cases_per_stratum` and `max_cases_per_template` makes
  `build` raise `ValueError`. The two caps would interact order-dependently, which is the
  defect this change removes. Oracle: the requirement (fail loud on an ambiguous config).
- **C6**: Metamorphic properties of the builder under `max_cases_per_stratum`:
  - Reordering `corpus.tickers` or `corpus.fiscal_years` leaves the set of case ids unchanged.
  - Adding a ticker to `corpus.tickers` leaves every existing ticker's case ids unchanged.

  Oracle: the stability property in the requirement.

## Interface

New function (stubbed in the spec PR):

- `src.eval.suites.sql_derived.select_stratified(combos: Sequence[dict], per_stratum: int | None, *, ticker: str | None = None) -> list[dict]`

Behavior on existing code (added by the implementer; a spec PR may only add stubs):

- `SqlDerivedBuilder.build` reads the tier key `max_cases_per_stratum`. Without it, the
  builder behaves exactly as today: the `smoke` and `full` tiers and the existing unit
  tests are unaffected.
- `Template` gains `max_per_stratum: int | None`, parsed from the template key of the same
  name.

## Notes

- Config (implementer):
  - Switch the `standard` tier from `max_cases_per_template: 40` to `max_cases_per_stratum`.
    Choose `k` so the suite stays roughly 400–480 cases (about $2.40–3.00 per full run).
    Record the per-template counts in the impl PR.
  - Templates with few combinations per stratum (screens, 2c's three concepts) may set their
    own `max_per_stratum`, or leave it unset (no cap).
  - Measured on the 2026-10-14 corpus with a prototype: `k = 2` gives 432 cases (lookup 171,
    compute 96, dimensional 88, breakdown 60, screen 17), every filer with data present in
    `aq-1c`, `aq-3d` and `aq-8b`. `k = 1` gives 263 and `k = 3` gives 594. Under `k = 2`,
    `aq-7b-margin-screen` drops from 11 to 6 and `aq-10a-tsm-twd-lookup` from 11 to 4. Give
    those two their own `max_per_stratum` (4 and 6) to keep their coverage, for about 444
    cases in all.
- Same PR, config only: give the templates whose `ORDER BY` can tie among duplicate facts a
  final `fact_id` tie-break (2c, 2a, 3a, 3b). Duplicates are facts with the same value,
  concept, period and dimension in one filing.
  - Today the builder picks an arbitrary representative. This caused all 43 of the "changed
    `expected_fact_ids`" in the hand-off doc: in every one the value and the
    `fact_id_classes` are unchanged, so grading was never affected (verified 2026-10-14).
  - The other 6 differences were breakdown entity labels, now correct after a geography-map
    fix: Meta "Rest Of World" → ROW, not US-CA.
- Out of scope: rebuilding and committing the suite, and the new baseline run. Those follow
  once the corpus fingerprint covers the October 2026 filings.
- Fakes: the builder tests use a fake DB connection (an I/O boundary); `select_stratified`
  is pure.
