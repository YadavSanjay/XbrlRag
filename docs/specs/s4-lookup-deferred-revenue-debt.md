# s4-lookup-deferred-revenue-debt — the system answers Debt and DeferredRevenue by their S4 definitions

D3 failure classes **C10** (GOOGL contract liabilities: the system answers the presented
current part for a family whose S4 definition is the total) and **C11** (HD total debt: the
system's Debt rank 1 is `LongTermDebt`, a different measure). Adjudicated in K2
(`k2_key_adjudications_2026-11-10.md`, under the eval data folder; the key is right in both).
The key side already builds these values: [key-definitions](key-definitions.md)
requirements 2 and 3 (`src/eval/keys/key_definitions.py`, implemented). This change makes the
**system** answer the same definition from the same facts, citing every part.

**Maintainer decisions (K2, accepted 2026-11-10):**

1. The fix is system-side, in the retriever, **not** in `concept_mapping` (so
   `mv_financial_facts`, the suite builder's inventory and the frozen suites stay
   byte-identical).
2. Debt and DeferredRevenue lookups and flat screens answer the S4 defined measure, built from
   cited parts: every part's `fact_id` is cited and the definition is disclosed.
3. Debt with no presented current line: the **presented sum** answers; the tagged note total
   is disclosed (as a reading), not answered.
4. K4 stands: a comparison (difference, growth) uses the total. The `current_presented`
   alternate is a key-side reading for lookups and trends only; the system always answers
   the total and only discloses the current figure.
5. Never filer-specific: the rule is the element definitions and the label rule of
   key-definitions, nothing keyed to a CIK.

## Requirement

Terms: a **line** is a line of the filing's balance sheet; a **note fact** is a tagged fact of
the filing at the balance-sheet date (on a line or only in the notes); *the same* is 1e-9
relative (as key-definitions); `M` below is 10^6.

### 1. Scope

`defined_measure(family, lines, notes, rule, unit)` returns a `DefinedMeasure` for `Debt` and
`DeferredRevenue`, and **None for every other family**, whatever the rows. For those families
`definition` is `debt_total` / `deferred_revenue_total` and `concept` is `s4:debt_total` /
`s4:deferred_revenue_total` (key-definitions requirement 1).

### 2. Inputs (rows as the caller passes them)

- `lines`: **every** line of one filing's balance sheet (one statement, not parenthetical), in
  presentation order, including lines of no family (the `Total current liabilities` line is
  what places the others). Each is a `MeasureLine` `{fact_id, concept, label, value, unit,
  decimals}`; `label` is the line's presented label (FSDS `plabel` on the key side); `value`
  None is a nil fact or no fact; a line with no fact has `fact_id` None. A line with a value and
  no `fact_id` raises `ValueError`.
- `notes`: the filing's facts at the balance-sheet date (`NoteFact`), including facts that are
  also on a line. Only the note concepts of requirements 5 and 6 are read; other rows are
  ignored.
- `rule`: the family's statement-line rule (`StatementRule`), the key side's locked rule.
- `unit`: the answer's unit (e.g. `USD`).
- `decimals`: None or the string `INF` is exact; else an int.

### 3. Lines selected

A line is **selected** when `line_is_candidate(rule, label, concept)` (statement-witness)
holds and its `value` is not None. Only the first selected line of a concept counts. When the
rule has `prefer` patterns, the first pattern that matches any selected label narrows the
selection to the lines it matches (as `select_lines`). A line's **section** is `current` when
it lies above the first line whose label matches `total current liabilities`
(case-insensitive), else `noncurrent` (also when there is no such line).

A selected line whose `unit` is not `unit` makes the result **`unit_mismatch`**: `value`,
`basis` None, `parts` and `fact_ids` empty; nothing is ever added across units.

### 4. Note facts read

The value of a note concept is its note fact with `unit` equal to `unit` and `value` not None
(**a nil is never 0**: it is absent; a fact in another unit is absent). Several such facts of
one concept that are the same count once (the first one's `fact_id`); several that are not the
same make the result **`conflicting_facts`** (as `unit_mismatch`), but only when that concept
is read.

### 5. Debt

1. No selected line: status `not_presented` (no note fallback, as the key).
2. Otherwise status `defined`, basis `presented`, `value` the sum of the selected lines, parts
   the selected lines in order.
3. **Reading `note_total`** (decision 3): when no part is `current`, the first present of
   `us-gaap:DebtLongtermAndShorttermCombinedAmount`, else `us-gaap:LongTermDebt`; when it is
   larger than `value` and not the same, `readings` is `[{"reading": "note_total", "value",
   "concept", "fact_ids": [its fact_id]}]`. It is disclosed, never answered.

### 6. DeferredRevenue (key-definitions requirement 3, same precedence)

1. Selected lines in both sections: their sum, basis `presented_total`; no note is read.
2. Selected lines in the current section only (`current` their sum):
   - the total (`us-gaap:ContractWithCustomerLiability`, else `us-gaap:DeferredRevenue`) is
     present and not below `current` (or the same): that value, basis `note_total`, parts the
     note fact only;
   - else the non-current part (`us-gaap:ContractWithCustomerLiabilityNoncurrent`, else
     `us-gaap:DeferredRevenueNoncurrent`) is present: `current` plus it, basis
     `current_plus_note_noncurrent`, parts the lines then the note fact;
   - else `current`, basis `no_noncurrent_reported`.

   When `value` is not the same as `current`, `readings` is `[{"reading":
   "current_presented", "value": current, "concept", "fact_ids"}]`: `concept` the line's
   concept when one line is selected, else `s4:deferred_revenue_presented`; `fact_ids` the
   selected lines'.
3. Selected lines in the non-current section only: their sum, basis `presented_total`.
4. No selected line: the total present: status `defined`, basis `note_total`, that note fact
   the only part; else `not_presented`.

### 7. The record

`DefinedMeasure` `{family, definition, concept, status, basis, value, unit, decimals, parts,
fact_ids, readings, disclosure, reason}`:

- `parts` are exactly the facts whose values add up to `value` (a `MeasurePart` `{fact_id,
  concept, value, section, label}`, section `note` and label None for a note fact); `value`
  is their sum; `fact_ids` their fact ids in order;
- `decimals` is the coarsest (smallest) finite `decimals` of the parts, None when every part
  is exact (`INF` is exact, never 0);
- `unit` is the given unit; `reason` is None for `defined` and `not_presented`, a non-empty
  text otherwise;
- `value`, `basis`, `decimals` are None and `parts`, `fact_ids`, `readings` empty unless the
  status is `defined`.

### 8. Disclosure

`disclosure` is always a non-empty text for the synthesizer, and (case-insensitive):

- for `defined` it names the definition (`total debt` / `total deferred revenue`) and the
  basis phrase: `presented` → `presented borrowing lines`; `presented_total` → `as
  presented`; `note_total` → `tagged total`; `current_plus_note_noncurrent` → `tagged
  non-current`; `no_noncurrent_reported` → `no non-current amount`;
- it names each reading's concept local name (the part after `:`);
- for `not_presented` it contains `no balance-sheet line`; for `unit_mismatch` `unit`; for
  `conflicting_facts` `conflicting`.

### 9. One definition with the key side

For any filing, rows built from the same facts give the key side's answer:
`defined_measure("Debt", ...)` equals `debt_key(...)` and `defined_measure("DeferredRevenue",
...)` equals `deferred_revenue_key(...)` in status (`defined` / `not_presented`), basis,
value, the concepts of the summed parts (the key's components, its note component only for
basis `note_total`) and the readings' kind, value and concept; and `screen_member` equals
`member_status` in status and value. Metamorphic: the key-side function is the oracle.

### 10. Precedence over `candidate_order`, and screens

`defined_precedence(defined)`: None (another family) → `candidate_order` (today's choice,
unchanged); `defined` → `defined` (the measure answers, over whatever `candidate_order`
ranks first); `not_presented` → `candidate_order` (with the disclosure stating that no line
meets the definition); `unit_mismatch` and `conflicting_facts` → `refuse`.

`screen_member(defined)` → `{"status", "reason", "concept", "value", "fact_ids"}`: `defined` →
`ranked`, reason None, the measure's concept, value and fact ids; `not_presented` →
`metric_not_tagged` (reason, concept, value None, fact ids empty), as the key's
`member_status`; `unit_mismatch` / `conflicting_facts` → `excluded` with that reason. A flat
screen ranks one measure, so a `not_presented` filer is never ranked by a fallback concept.

### 11. Shape

The value never depends on the question's shape: lookups, trends, comparisons and screens
all answer the total (decision 4).

## Acceptance criteria

All values are synthetic filings shaped like the corpus (`test/spec/<id>/conftest.py`), in
USD with `M` = 10^6, hand-added in the tests; sums of whole millions are exact in floating
point, so value checks use `rel=1e-12`.

- **C1** (Debt sum): Filer H (HD-shaped) 300 + 4,600 + 48,500 = 53,400 M, basis `presented`,
  fact ids of the three borrowing lines in order, sections current, current, noncurrent; the
  operating-lease, deferred-tax, deferred-revenue and payables lines are out, and
  `LongTermDebt` 51,400 (today's answer) is not a part; Filer T (extension lines) 1,600 +
  6,700 = 8,300; a `Total debt` line narrows to itself (1,000); no borrowing line is
  `not_presented`. Property: adding lines no rule selects, reordering lines within a section
  and scaling every value by k change nothing but the value (scaled by k). Oracle: hand
  addition; key-definitions requirement 2.
- **C2** (Debt reading): Filer G (no current debt line) 46,500 with `note_total` 49,000 on
  `DebtLongtermAndShorttermCombinedAmount` (preferred over `LongTermDebt` 48,800) and its fact
  id, disclosed by name; none for Filer H (current lines); none when the note is the same or
  smaller; `LongTermDebt` when the combined tag is absent. Property: a reading exists exactly
  when no part is current and the note exceeds the value. Oracle: decision 3; key-definitions
  requirement 2 step 5.
- **C3** (DeferredRevenue precedence): Filer T 3,400 + 3,600 = 7,000 `presented_total` (its
  tagged 3,900 not read); Filer G 8,600 `note_total` with reading `current_presented` 6,600;
  Filer O 9,300 + 1,200 = 10,500 `current_plus_note_noncurrent` (reading 9,300), and 10,500
  `note_total` from `DeferredRevenue` when that total is tagged; Filer H 2,600
  `no_noncurrent_reported` (its tagged 1,500 total is below the line and ignored); a
  non-current-only line 945 `presented_total`; no line with a note total 770 `note_total`;
  neither `not_presented`. Parts and fact ids are exactly the summed facts. Property: lines
  in both sections win over any note; a total below the current line is never the value.
  Oracle: hand addition; key-definitions requirement 3; the us-gaap definition of
  `ContractWithCustomerLiability`.
- **C4** (one definition): for Filers H, G, T, O the system and `debt_key` /
  `deferred_revenue_key` give the same hand-checked values; for any generated filing,
  requirement 9 holds. Oracle: the key-side functions (metamorphic) and the hand sums.
- **C5** (units, nil, precision, conflicts): a selected line in EUR → `unit_mismatch`
  (refused, no value); a note fact in another unit or nil is absent (Filer G's reading falls
  to `LongTermDebt`; Filer G's DeferredRevenue falls to `no_noncurrent_reported` 6,600, never
  0); a nil borrowing line is no part (53,100), all nil → `not_presented`, value None;
  decimals -6 and -5 → -6, with `INF` / None → the finite one, all exact → None; two
  different totals → `conflicting_facts` while Debt (not reading them) stays defined; equal
  duplicates count once with the first fact id; a valued line without a fact id raises
  `ValueError`. Property: nil lines never change the result; decimals is the minimum finite
  part decimals. Oracle: requirements 3, 4, 7; rag.md (no unit mixing, nil and INF never 0).
- **C6** (precedence and screens): `defined_precedence` maps None and `not_presented` to
  `candidate_order`, `defined` to `defined`, `unit_mismatch` / `conflicting_facts` to
  `refuse`; `screen_member` gives `ranked` with value and fact ids, `metric_not_tagged`, and
  `excluded` with the status as reason; a defined Debt measure answers over the
  `candidate_order` winner (`LongTermDebt`); disclosures carry requirement 8's phrases.
  Property: over generated filings, the precedence and screen status follow the status.
  Oracle: requirements 8 and 10; key-definitions requirement 6.
- **C7** (other families unaffected): `defined_measure` is None for `Revenue`, `NetIncome`,
  `SharesOutstanding`, `DebtIssued`, `LongTermDebt` and any other name, on the same rows that
  give a defined Debt measure. Property: any family name but the two gives None. Oracle:
  requirement 1.

## Interface

New module `src/retriever/xbrl/defined_measure.py` (stubbed in the spec PR):

- `MeasureLine`, `NoteFact`, `MeasurePart`, `DefinedMeasure` (`TypedDict`s, requirements 2, 7)
- `defined_measure(family: str, lines: Sequence[MeasureLine], notes: Sequence[NoteFact], rule: StatementRule, unit: str) -> DefinedMeasure | None`
- `defined_precedence(defined: DefinedMeasure | None) -> str`
- `screen_member(defined: DefinedMeasure) -> dict[str, Any]`

`StatementRule` is `src.eval.keys.statement_witness.StatementRule`; the implementation uses
that module's `line_is_candidate` (one label rule for keys and answers).

### Wiring (implementer; reviewed in the implementation PR, not exercised by these tests)

- **Rule source.** The retriever loads the `Debt` and `DeferredRevenue` entries of
  `configs/eval/v2/statement_lines.yaml` with `load_statement_rules` (once, cached). It does
  not copy them: a copy is the drift this change removes.
- **Lookup** (`xbrl_factstore_lookup.py`). For a flat (undimensioned) lookup whose family is
  `Debt` or `DeferredRevenue`, after S1 picks the filing and column (`current_column`, the
  same filing the candidate rows come from): read that filing's balance-sheet lines (the
  presentation network's balance-sheet role, `' - Statement - '`, not parenthetical, in
  `tree_sequence` order, with the preferred label of each arc and the consolidated fact at the
  column's `period_end`) and the note facts of requirements 5-6 at that date, call
  `defined_measure`, then `defined_precedence`. `defined`: the result is OK with `value`, the
  citation the first part, `sum_of` = `fact_ids` (the existing stated-sum field), a new
  `definition` field `{concept, basis, disclosure, readings}` the synthesizer states (a
  versioned prompt bump), and every reading's fact ids added to the evidence so a disclosed
  figure's citation resolves. `candidate_order`: today's path unchanged, plus the
  disclosure. `refuse`: `NOT_FOUND` with the reason. Candidates stay for the ambiguity checks;
  the multi-value AMBIGUOUS check does not apply to a defined answer (its parts differ by
  construction). Dimensioned lookups and every other family are unchanged. Trends call the
  lookup per year, so they inherit it.
- **Flat screen** (`xbrl_factstore_screen.py`). For `Debt` and `DeferredRevenue`, one query
  over all filers reads the same lines and note facts for each filer's FY-N column; members
  come from `screen_member` (ranked rows carry `fact_ids`; `metric_not_tagged` and `excluded`
  go to the existing exclusion list). No `LIMIT`, as screen-full-universe requires.
- **Compute** (difference, growth) reads lookup results, so it uses the total (decision 4).
- **Logging.** Each defined answer logs a `decision` event (`s4_defined_measure`, family,
  basis, fact ids).
- No change to `concept_mapping`, `mv_financial_facts`, the suite builder or any eval config.

## Notes

### Evidence (K2, read-only SQL and $0 simulation; no held-out data)

- **C10.** Where both readings exist (FY2022 on), the system's DeferredRevenue answer differs
  from S4 in 36 of 70 filer-years across 9 filers (AAPL, ADBE, AMZN, CSCO, GOOGL, MSFT, ORCL,
  QCOM, TSLA): `candidate_order` (D13, presented first) puts a presented current part ahead of
  the rank-1 notes-only total. The full rule reproduces 17 of 17 keyed DeferredRevenue
  filer-years. A reorder-only variant (rank 1 beats a presented part) gets 13 of 17 and breaks
  HD, whose policy-note `ContractWithCustomerLiabilityCurrent` 1.5B would beat the presented
  2.61B line; it also leaves TSLA (rollforward below its two face lines) and two-line filers
  wrong. Hence the full composition (Filer H and Filer T pin both failure modes).
- **C11.** HD's key is the sum of its presented borrowing lines (hand-checked: FY2023 1,368 +
  42,743 = 44,111; FY2024 316 + 4,582 + 48,485 = 53,383; FY2025 4,464 + 4,967 + 46,341 =
  55,772); the system answers `LongTermDebt` (42,150 / 51,365 / 49,397). `CommercialPaper`
  and `LongTermDebtAndCapitalLeaseObligationsCurrent` have no Debt membership in the mapping,
  so the lines must come from the presentation network, not from family rows. Of 112 keyed
  Debt filer-years, today 41 match the key; a rough mv simulation of the presented sum matches
  56, missing where its role and label detection failed (AAPL, KO, PG, TSLA, UPS, TSM); the
  faithful count comes from the implementation.

### Expected movement (dev, K2 simulation; to confirm on a slice)

| Arm | Cases | Expected |
|---|---|---|
| F3a | GOOGL contract liabilities difference ×3 | +3 (2 silent-wrongs removed, 1 hedged miss) |
| F7a | the same ×3 | +3 (hedged misses) |
| F3a, F7a | HD total debt trend ×3 | +3 each |
| F3a, F7a | ORCL lookups ×3, META ×6 | unchanged (CORRECT; ORCL moves from the reading to the key) |
| F3b, F7b | retailers' screen.bottom | 0 (AMZN 18.1B → 24.6B; the failure is COST inclusion) |
| F4, A8 | — | 0 (another system) |

Other failing Debt cases (QCOM peer rank, the semis sum, the FY2022 median) need every
member right and cannot be called from the simulation.

### Validation plan

1. Implementation PR green on these locked tests and `pytest test/spec` (all spec dirs).
2. A $0 deterministic check: for every keyed Debt and DeferredRevenue filer-year of the dev
   and validation suites (never held-out), run the wired lookup against `xbrl_db` and compare
   with the key value; report matches by family and the misses with their parts.
3. A DeepSeek dev slice (`build_slice_suite.py` over the Debt and DeferredRevenue cases plus a
   control slice of other families) → `run_suite.py --suite-path` with the maintainer's
   `--max-cost-usd` cap → `compare_runs.py` against the baseline. Control families must not
   move.
4. The frozen suites are not rebuilt (nothing on the key side changes); `sha256` of the frozen
   suite files is checked before and after.

### Review points

- **Equivalence depends on the label input.** The key reads FSDS `plabel`; the system reads
  the presentation arc's preferred label. They are the same text for the filings checked by
  K2, but a filer whose XBRL preferred label differs from SEC's rendering would select
  different lines. The validation step 2 surfaces any such filer; the fix would be general
  (the label resolution), never a per-filer rule.
- **Sectioning by position** needs the `Total current liabilities` line in `lines`; a caller
  that passes only family rows would place every line in `noncurrent` (and add spurious Debt
  readings). Requirement 2 states it; the wiring must pass the whole statement.
- **Unit mismatch refuses** where the key side would skip the line (it reads one unit). Only a
  balance sheet in two currencies differs, and a refusal is not a wrong number.
- **TSM** (key-definitions review point): its current portion line names no borrowing, so both
  sides omit it alike; no TSM Debt intents until the rules match elements too.
- **`not_presented` lookups fall back** to today's `candidate_order` with a disclosure; screens
  do not (one measure per ranking). The alternative, refusing the lookup, would turn today's
  disclosed near-measure answers into refusals; recommended: keep the fallback.
