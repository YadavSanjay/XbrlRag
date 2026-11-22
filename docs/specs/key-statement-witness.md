# key-statement-witness — every v2 key's concept is witnessed by the filer's own statement

Area D of the [test strategy](../design/XbrlRag_Test_Strategy.md) (eval fairness: "keys'
concept from the filer's statement"). Approved by the maintainer on 2026-11-07.

**The defect.** The v2 key builder takes a key's *value* from SEC companyfacts, but its
*concept* from our own `concept_mapping`: the filer's lowest-rank member in
`mv_financial_facts` (`src/eval/suites/v2_sources.py` `INVENTORY_SQL`). Our mapping also
decides which filers a screen ranks (`metric_not_tagged` when the inventory lacks the family).
So a mapping error moves the key and the product together, and no grade can catch it. After
the 2026-11-07 mapping reload the live lookup is right, but the rebuilt keys are wrong for:

| Filer, period | Statement line (hand-checked) | Rebuilt key |
|---|---|---|
| VST FY2023 / FY2025 revenue | "Operating revenues" (`Revenues`) 14,779M / 17,738M | `RevenueFromContractWithCustomerExcludingAssessedTax` 13,804M / 17,586M, a component no statement presents |
| MCD FY2025 D&A | cash-flow "Depreciation and amortization" 2,199M | 457M, an income-statement sub-line |
| PG FY2023–25 shares outstanding | the balance sheet presents shares *issued* only | `CommonStockSharesIssued` 4,009.2M |
| AAPL FY2023 / FY2025 debt | "Term debt" current + non-current (total 105,103M / 90,678M) | `LongTermDebtNoncurrent` 95,281M / 78,328M |
| AMZN FY2024 / FY2025 operating expenses | "Total operating expenses", tagged `CostsAndExpenses` (includes cost of sales): 569,366M / 636,949M | ranked beside AAPL `OperatingExpenses` 57,467M / 62,151M as one measure |

MCD FY2025 D&A and AAPL FY2023 debt are validation keys: their figures are the
investigation's, and they are not fixtures here (Notes, "Substitutions"). Every other figure
above is hand-checked in a fixture filing.

Re-grading the stored runs with the statement line *as the key* flipped 15 passes to fails, all
cases where the frozen key was the better answer (a broader "and accrued" or "and other" line,
or the current portion only). So the statement line is a **witness that gates a key**, never a
replacement key. Investigation: `data/eval/key_independence/report.md` (local, gitignored).

**Scope.** This spec adds the witness (pure functions over FSDS rows and companyfacts entries)
and its three committed, reviewed lists. It does **not** state the definition a definitional
family's case carries or its alternates as disclosed readings (Debt total vs noncurrent,
DeferredRevenue total vs current, net vs comprehensive income incl. noncontrolling interests):
that is split into its own spec, `key-definitions`, because it changes S4 and the case schema.
The screen `LIMIT 50` defect is `screen-full-universe`, not here.

## Requirement

Terms. A **key** is an `AnswerKey` (`src/eval/keys/core.py`); this spec reads its `concept`
(`"<taxonomy>:<tag>"`), `unit`, `period_start`, `period_end`, `value`, `accession`, `form`,
`cik` (10 digits) and `fiscal_year`. **Annual forms** are `10-K`, `10-K/A`, `20-F`, `20-F/A`,
`40-F`, `40-F/A`. Two amounts are **the same** when `abs(a - b) <= 1e-9 * max(abs(a), abs(b),
1)` (FSDS keeps four decimals, companyfacts the XBRL value; docs/specs/fsds-reader.md
requirement 12). A line's **concept** is `"<p>:<tag>"`, where `<p>` is `ext` for a custom tag
(`version == adsh`), `ifrs-full` for an `ifrs/...` version, and otherwise the version's text
before `/` (`us-gaap/2025` → `us-gaap`). The **primary statements** are FSDS `stmt` `BS`, `IS`,
`CI`, `CF`, `EQ`. All FSDS reading goes through `src/eval/sec_reference/fsds.py`
(`statement_lines`, `period_value`, `submission`), consolidated values only.

### 1. The witness record (`statement_witness`)

`statement_witness(tables, rules, notes, family, key, annual_adsh, annual_value=None)` returns a
`Witness`:

```python
{"status": str, "family": str, "cik": key["cik"], "fiscal_year": key["fiscal_year"],
 "adsh": str,                 # the filing read (below)
 "lines": [WitnessLine],      # the lines select_lines chose, in (report, line) order
 "value": float | None,       # the witnessed value
 "key_concept": key["concept"], "key_value": key["value"], "key_accession": key["accession"],
 "substitution": None | {"code": str, "note": str, "concept": key["concept"]},
 "reason": None | str,
 "resolution": None}          # set only by resolve (requirement 3)
```

A `WitnessLine` is `{"adsh", "stmt", "report": int, "line": int, "tag", "version", "concept",
"plabel", "negating": bool, "value": float}`, the `pre` row's fields as `statement_lines` gives
them plus the line's consolidated `period_value` for the key's period and unit (the value *as
tagged*, not sign-flipped by `negating`). A family with no rule raises `ValueError`.

The steps, in order:

1. **Filing read.** `adsh` is the key's `accession` when the key's `form` is an annual form,
   else `annual_adsh` (an interim report's condensed statements never witness an annual key).
2. **No data.** When `submission(tables, adsh)` is None: status `no_fsds`, `lines` empty,
   `value` None.
3. **Recast.** When the key's form is not an annual form, `annual_value` is not None and it is
   not the same as the key's value: status `value_recast` (a later interim report restated the
   balance; held for review, not keyed). `lines` and `value` are still those of step 4.
4. **Lines.** `lines = select_lines(tables, rules[family], adsh, period_start, period_end,
   unit)` (requirement 2). The witnessed `value` is the sum of the lines' values when the rule's
   `combine` is `sum`; for `one`, the lines' common value when every line carries the same
   value, else None.
5. **Status from the lines**, when `lines` is not empty:
   - `ambiguous`: `combine` is `one` and the lines carry more than one distinct value;
   - `agree`: the value is the same as the key's, and some selected line's concept is the
     key's concept;
   - `agree_other_element`: the value is the same as the key's, and no selected line has the
     key's concept (TSLA `Revenues` against its presented `RevenueFromContract…` line; AAPL
     `LongTermDebt` against the sum of its two "Term debt" lines);
   - `disagree`: otherwise.
6. **Status without lines**, when `lines` is empty:
   - `rejected_line`: the key's concept is a line of a primary statement of `adsh`
     (parenthetical lines included) with a consolidated value for the key's period and unit,
     but the rule does not select it. `reason` is that line's `plabel` (the first in
     `(report, line)` order). (KO "Accounts payable and accrued expenses"; PG "Common stock,
     issued (in shares)".)
   - `note_witnessed`: the key's concept is in `notes[family]` (requirement 4);
   - `note_unlisted`: otherwise.
7. `substitution` is set when the key's concept is a key of the rule's `substitutes`, whatever
   the status.

Statuses: **accepted** `agree`, `agree_other_element`, `note_witnessed`; **held**
`value_recast`, `no_fsds`; **blocking** `disagree`, `ambiguous`, `rejected_line`,
`note_unlisted`. A held key is not keyed: its intent is dropped with the status as the reason.

### 2. The statement-line rules (`load_statement_rules`, `select_lines`)

`configs/eval/v2/statement_lines.yaml` is byte-identical to the locked copy
`test/spec/key-statement-witness/fixtures/statement_lines.yaml`, SHA-256
`661bed5dc7d7d458e0402cb4d8e043f0979f474bfe4d4a113a625b0aa2eff295`. It names no
`concept_mapping` row, rank or view, and the loader takes no mapping: what it selects cannot
move with our mapping. Its fields (header comment of the file) per family: `statements` (in
preference order), `parenthetical` [false], `include`, `require` [], `exclude` [], `section`
[None], `prefer` [], `combine` ["one"], `substitutes` [{}]. `load_statement_rules` fills the
defaults and raises `ValueError` on an unknown field, a statement code outside the primary
statements, a `section` other than `current_assets` / `current_liabilities`, a `combine` other
than `one` / `sum`, a pattern that does not compile, a missing `statements` or `include`, or a
`version` other than 1.

`select_lines(tables, rule, adsh, period_start, period_end, unit)`:

1. A line of `statement_lines(tables, adsh)` is a **candidate** when its `stmt` is in
   `statements`; it is not parenthetical unless `parenthetical` is true; its `plabel` matches
   (`re.search`, case-insensitive) any `include` pattern, every `require` pattern and no
   `exclude` pattern; it lies inside the `section`, when one is set (`current_assets`: above the
   first line of the same report matching `total current assets`; `current_liabilities`: below
   the first line of the same report matching `^total assets` and above the first matching
   `total current liabilities`; a report without those lines has no candidate); and
   `period_value(tables, adsh, tag, period_start, period_end, unit, version)` is not None. Of
   lines with the same `(tag, version)` only the first in `(report, line)` order is kept.
2. **Statement preference.** Only the candidates of the first statement in `statements` that
   has any are kept (MCD D&A: the cash-flow line, before the income-statement sub-line).
3. **Prefer.** For each `prefer` pattern in order, the first one that matches any candidate's
   `plabel` narrows the candidates to those it matches.
4. The result is the candidates in `(report, line)` order, as `WitnessLine`s.

A `ValueError` from `period_value` propagates.

### 3. A disagreement blocks the build unless a reviewed resolution covers it

`configs/eval/v2/key_resolutions.yaml` holds reviewed resolutions; each covers exactly one key:
`{family, cik (10-digit string), fiscal_year (int), concept, value (number), reading: "key" |
"drop", reason (non-empty)}`. `load_resolutions` raises `ValueError` on a missing or unknown
field, another reading, an empty reason, two entries for one `(family, cik, fiscal_year,
concept)`, or a `version` other than 1.

- **Shrink-only.** The locked list is
  `test/spec/key-statement-witness/fixtures/key_resolutions.yaml`. `check_resolutions(current,
  locked)` returns `len(current)` when every live entry equals a locked entry field for field
  (values compared as the same amount), and raises `ValueError` naming the first entry that
  does not. The live file passes it against the locked list. A new resolution is a re-lock of
  this spec, reviewed by the maintainer.
- **`resolve(witness, resolutions)`** returns a copy of the witness whose `resolution` is the
  entry whose `family`, `cik`, `fiscal_year` and `concept` equal the witness's (`key_concept`)
  and whose `value` is the same as `key_value`, when the status is blocking; otherwise
  `resolution` is None. It never changes `status`. A key the mapping later moves to another
  concept or value is no longer covered.
- A blocking witness is **resolved** when it has a resolution: reading `key` accepts the key;
  reading `drop` drops the intent with reason `resolved_drop`.
- **`select_key(tables, rules, notes, resolutions, family, candidates, annual_adsh,
  annual_values)`** is how the builder chooses a key. `candidates` holds one key per concept the
  mapping offers for the filer, year and family, in any order; `annual_values[concept]` is that
  concept's companyfacts value in `annual_adsh`. Each candidate is witnessed
  (`statement_witness`, then `resolve`).
  - When any candidate is accepted (an accepted status, or resolved with reading `key`): if two
    of them carry values that are not the same, raise `WitnessBlocked`; otherwise return the
    best by status (`agree`, `agree_other_element`, `note_witnessed`, resolved `key`), then by
    `key_concept`.
  - Otherwise, when any is held (`value_recast`, `no_fsds`) or resolved with reading `drop`:
    return the first of them in that order, then by `key_concept`.
  - Otherwise (no candidate, or every one blocking and unresolved) raise `WitnessBlocked`
    naming each candidate's concept and status.
- `WitnessBlocked` is a `ValueError`.

### 4. Note-only keys come from a frozen, reviewed list

`configs/eval/v2/note_concepts.yaml` is byte-identical to the locked copy
`test/spec/key-statement-witness/fixtures/note_concepts.yaml`, SHA-256
`1176e22ebaea545ea57e86194c6cfba32e9cf0df561ae249675aaf9a30939095`. Per family, the us-gaap
elements whose own taxonomy definition (us-gaap 2025 documentation linkbase; committed excerpt
`taxonomy_excerpt.json`) is the family's measure, chosen without reference to
`concept_mapping`. `load_note_concepts` returns family → concepts and raises `ValueError` on a
concept not of the form `<taxonomy>:<tag>`, a duplicate within a family, an unknown field or a
`version` other than 1. A key may rest on a note concept only when the filing presents no line
the rule selects (requirement 1, step 6). The list may only shrink: every live family's
concepts are a subset of the locked copy's.

### 5. Universe membership is statement-based (`universe_status`)

`universe_status(tables, rules, notes, resolutions, family, adsh, period_start, period_end,
unit, key=None, annual_value=None)` returns `{"status", "reason", "concept", "value",
"witness"}`:

- **With a key** (the mapping offered one): `w = resolve(statement_witness(..., key,
  annual_adsh=adsh, annual_value), resolutions)`, `witness = w`.
  - Accepted (or resolved `key`) and `w["substitution"]` is None: `ranked`, `concept` and
    `value` the key's, `reason` None.
  - Accepted (or resolved `key`) with a substitution: `definition_excluded`, `reason`
    `"substitute:<code>"`, `concept` and `value` the key's.
  - Otherwise: `held`, `reason` the witness status (`resolved_drop` for a resolved `drop`),
    `concept` the key's, `value` None.
- **Without a key** (today's `metric_not_tagged`): `witness` None.
  - No submission for `adsh`: `held`, reason `no_fsds`.
  - `select_lines` returns nothing: `metric_not_tagged`, reason None, `concept`/`value` None.
  - Several lines with distinct values under `combine: one`: `definition_excluded`, reason
    `ambiguous_lines`, `concept`/`value` None.
  - Every selected line's concept is a substitute of the family: `definition_excluded`, reason
    `"substitute:<code of the first line's concept>"`, `concept` and `value` as for `ranked`.
  - Otherwise `ranked`: `value` the witnessed value (requirement 1, step 4), `concept` the
    line's concept when one line is selected, else None.

So a filer that presents the family's line is never `metric_not_tagged` (AMZN's PP&E on
`PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAsset…`, AMZN's `InterestExpenseNonoperating`
"Interest expense"), and one that presents none is (KO for SellingAndMarketing: its income
statement shows SG&A only).

### 6. Screens never rank a substitute beside primaries

A cross-filer screen or peer-relative case ranks only `ranked` members. A member whose line or
key is a stated substitute of the family (`CostsAndExpenses` for OperatingExpenses: "Total costs
of sales and operating expenses", us-gaap documentation) is `definition_excluded` with the named
reason `substitute:includes_cost_of_revenue`, and appears in the case's exclusions with that
reason; it is never ranked as if it were the same measure (AMZN FY2024 569,366M and FY2025
636,949M "Total operating expenses" beside AAPL's 57,467M and 62,151M). The codes and notes are
those of docs/specs/concept-family-coverage.md (stated substitutes).

### 7. Mapping-change invariance

The witness reads no mapping. For a filer, year and family, `select_key` returns the same
witness (canonical JSON, `src.eval.suites.v2_suite.canonical_json`) for any order of the
candidates, after adding a candidate that is not accepted (a component the rank-50 band no
longer holds back), and after removing a candidate that is not the one returned; removing the
returned candidate gives another accepted candidate with the same value, a held one, or
`WitnessBlocked`. So a mapping change can move a key only by raising.

### 8. Value sanity

A key whose value no filing presents for its period is never keyed silently:

- A key from an interim report whose annual report shows another value is held as
  `value_recast` (LMT 2025-12-31 PP&E: 11,292M from the FY2026 10-Qs against 8,875M on the
  FY2025 10-K balance sheet; AAPL 2025-09-27 `LongTermDebt`: 90,700M from the FY2026 10-Qs
  against 90,678M in the FY2025 10-K, which is also the sum of its two "Term debt" lines).
- A zero is keyed only when the statement witnesses it (AAPL FY2024 "Proceeds from issuance of
  term debt, net": "—", tagged 0). A zero key against a non-zero line is `disagree` (blocking).

### 9. Manifest counts

`witness_manifest(witnesses)` returns `{"n": int, "by_status": {status: int} (all nine
statuses, zero included), "resolved": int (witnesses with a resolution), "held": [{"family",
"cik", "fiscal_year", "key_concept", "status"}, ...] sorted by those fields}` and raises
`WitnessBlocked` when any witness is blocking without a resolution. The v2 build stores it as
`manifest["key_independence"]` (the build wiring is not locked here; see Notes).

### 10. Symmetric re-grade

`key_change_regrade(old_cases, new_cases, runs)` re-grades, for every arm in `runs`, the stored
answers of the cases whose expected answer changed:

- the cases are matched by `id`; different id sets raise `ValueError`;
- a case **changed** when any of `expected_value`, `expected_rows`, `alt_values`,
  `alt_readings`, `expected_exclusions`, `expect_not_meaningful`, `unit` differs;
- every arm must hold a stored result (`case_id`, `produced`) for every changed case, else
  `ValueError` naming the arm and case (a re-grade never moves one arm alone);
- per arm: `changed` (sorted ids), `before` and `after` (id → `grade_case(case,
  answer_from_payload(produced)).grade.value` under the old and the new case), `transitions`
  (`"<before> -> <after>"` → count, only where they differ). Unchanged cases are not re-graded.

## Acceptance criteria

- **C1**: `statement_witness` returns the record of requirement 1 for presented, note-only and
  missing filings: AAPL FY2025 `OperatingExpenses` 62,151M is `agree` on IS report 3 line 13
  "Total operating expenses"; ABT FY2023 effective tax rate 14.1% is `note_witnessed` with no
  lines; a filing absent from the tables is `no_fsds`; line fields equal the raw `pre` row and
  values equal the raw `num` row exactly. Oracle: the fixtures' `pre`/`num` rows and the
  rendered statements, checked by hand.
- **C2**: the committed rules select the hand-checked line for every fixture (MCD FY2024 D&A →
  CF 2,097M, not IS 447M; VST FY2023/25 → `Revenues` 14,779M/17,738M; PG shares outstanding →
  no line, its "issued" line rejected; AAPL debt → the two "Term debt" lines; TSLA → the lines
  attributable to common stockholders; AMZN PP&E → its finance-lease-inclusive line); the live
  rules file equals the locked copy (SHA-256 pinned); the rules hold no mapping reference; the
  loader rejects malformed rules. Oracle: hand check of each fixture against the rendered
  statement on EDGAR.
- **C3**: a disagreeing key is blocking: VST FY2023 component 13,804M, MCD FY2024 IS sub-line
  447M, AAPL FY2025 `LongTermDebtNoncurrent` 78,328M are `disagree`, KO's and PG's rebuilt keys
  `rejected_line`; `select_key` raises `WitnessBlocked` unless a committed resolution covers the
  exact key (ORCL FY2024 total contract liability 10,546M, resolved `key`); the live
  resolutions only shrink against the locked list. Oracle: the fixtures and the committed
  resolution file.
- **C4**: with no presented line, a key is accepted only on a listed note concept: ABT FY2023
  `EffectiveIncomeTaxRateContinuingOperations`, META FY2024/25 and INTC FY2023
  `InvestmentIncomeInterest`, KO FY2025 `AccountsPayableTradeCurrent` are `note_witnessed`; the
  statutory rate (21%) is `note_unlisted`; the live note list equals the locked copy. Oracle:
  the us-gaap 2025 element definitions (committed excerpt) and the fixtures.
- **C5**: `universe_status` ranks a filer that presents the line (AMZN FY2025 PP&E 357,025M,
  interest expense 2,274M; MCD FY2025 debt 39,973M) and returns `metric_not_tagged` only when
  none is presented (KO FY2025 SellingAndMarketing); never `metric_not_tagged` for a presented
  line. Oracle: the fixtures, checked by hand.
- **C6**: a screen never ranks a substitute: AMZN FY2024/FY2025 `CostsAndExpenses` (569,366M /
  636,949M) is `definition_excluded` with reason `substitute:includes_cost_of_revenue`, with or
  without a key, while AAPL's `OperatingExpenses` (57,467M / 62,151M) is ranked; a `ranked`
  status never carries a substitute concept. Oracle: us-gaap documentation of `CostsAndExpenses`
  and the rendered AMZN and AAPL income statements.
- **C7**: `select_key` is invariant under re-ranking, adding a non-accepted component and
  removing a non-returned candidate (VST revenue, MCD D&A, AAPL debt); otherwise it raises.
  Oracle: the unperturbed selection over the same fixtures (canonical JSON, byte-identical).
- **C8**: value sanity: LMT 2025-12-31 PP&E (11,292M from a 10-Q vs 8,875M in the 10-K) and AAPL
  2025-09-27 `LongTermDebt` (90,700M from a 10-Q vs 90,678M in the 10-K) are `value_recast` and
  held; AAPL FY2024 debt issued 0 is `agree`; a zero key against a non-zero line is `disagree`.
  Oracle: the fixtures' companyfacts entries and the rendered statements.
- **C9**: `witness_manifest` over the fixture keys gives the hand-counted statuses, resolved
  count and held list, and refuses an unresolved blocking witness. Oracle: hand count of the
  fixture keys (table in Notes).
- **C10**: `key_change_regrade` gives the hand-derived per-arm transitions for a key change (KO
  FY2025 AP: an answer of 5,649M goes CORRECT → INCORRECT, one of 14,813M INCORRECT → CORRECT),
  leaves unchanged cases out, and refuses an arm that lacks a changed case. Oracle: hand grading
  of the recorded answers at the cases' `rel_tol` 1e-4.

## Interface

All in `src/eval/keys/statement_witness.py`:

- `StatementRule`, `WitnessLine`, `Witness`, `UniverseStatus` (`TypedDict`s as above);
  `WitnessBlocked(ValueError)`
- `load_statement_rules(path: str | os.PathLike[str]) -> dict[str, StatementRule]`
- `load_note_concepts(path: str | os.PathLike[str]) -> dict[str, list[str]]`
- `load_resolutions(path: str | os.PathLike[str]) -> list[dict[str, Any]]`
- `check_resolutions(current: Sequence[Mapping[str, Any]], locked: Sequence[Mapping[str, Any]]) -> int`
- `select_lines(tables: FsdsTables, rule: StatementRule, adsh: str, period_start: str | None, period_end: str, unit: str) -> list[WitnessLine]`
- `statement_witness(tables: FsdsTables, rules: Mapping[str, StatementRule], notes: Mapping[str, Sequence[str]], family: str, key: Mapping[str, Any], annual_adsh: str, annual_value: float | None = None) -> Witness`
- `resolve(witness: Witness, resolutions: Sequence[Mapping[str, Any]]) -> Witness`
- `select_key(tables: FsdsTables, rules: Mapping[str, StatementRule], notes: Mapping[str, Sequence[str]], resolutions: Sequence[Mapping[str, Any]], family: str, candidates: Sequence[Mapping[str, Any]], annual_adsh: str, annual_values: Mapping[str, float | None]) -> Witness`
- `universe_status(tables: FsdsTables, rules: Mapping[str, StatementRule], notes: Mapping[str, Sequence[str]], resolutions: Sequence[Mapping[str, Any]], family: str, adsh: str, period_start: str | None, period_end: str, unit: str, key: Mapping[str, Any] | None = None, annual_value: float | None = None) -> UniverseStatus`
- `witness_manifest(witnesses: Iterable[Witness]) -> dict[str, Any]`
- `key_change_regrade(old_cases: Sequence[EvalCase], new_cases: Sequence[EvalCase], runs: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, dict[str, Any]]`

New committed files the implementation adds: `configs/eval/v2/statement_lines.yaml`,
`configs/eval/v2/note_concepts.yaml` (byte copies of the locked fixtures) and
`configs/eval/v2/key_resolutions.yaml` (a subset of the locked list).

## Notes

**Fixtures** (`test/spec/key-statement-witness/fixtures/`), from DEV filings only
(`src/eval/core/key_audit.py`: keys are tuned on dev alone; validation is covered by applying
the rules, never case by case). Every pinned fact is a dev key input of the frozen or rebuilt
v2 suites, or a prior-year column of a dev filing that is no key in any split:

- `fsds/<quarter>/{sub,pre,num,tag}.tsv`: SEC's header and, for each filing below, its `sub`
  row, every `pre` row on BS/IS/CI/CF/EQ, every consolidated `num` row (empty `segments` and
  `coreg`) of a tag on those lines, and those tags' `tag` rows; bytes copied from the quarter
  zip's files (via `data/cache/sec_fsds/filtered`);
- `companyfacts_excerpt.json`: for each listed (CIK, concept, period), every entry of that
  period in every unit, as SEC serves it, with the payload's SHA-256 and fetch time;
- `taxonomy_excerpt.json`: standard label and documentation of the listed us-gaap 2025
  elements, with the SHA-256 of `us-gaap-doc-2025.xml` and `us-gaap-lab-2025.xml`;
- `provenance.json`: per filing the accession, quarter, form, EDGAR URL of the primary
  document, the FSDS zip's URL, SHA-256, size and retrieval time, the primary document's
  SHA-256; per fixture file its SHA-256 (the tests verify these).

| Filer | Accession | Form, period | What it pins |
|---|---|---|---|
| AAPL | 0000320193-25-000079 | 10-K FY2025 (to 2025-09-27) | Debt as two "Term debt" lines; operating expenses; FY2024 debt issued 0 |
| KO | 0001628280-26-010047 | 10-K FY2025 | AP presented only as "and accrued expenses"; SG&A only |
| META | 0001628280-26-003942 | 10-K FY2025 | "Interest and other income, net" against note interest income |
| ORCL | 0000950170-25-087926 | 10-K FY2025 (to 2025-05-31) | 2024-05-31 deferred revenue: current line against total contract liability |
| MCD | 0000063908-26-000035 | 10-K FY2025 | FY2024 D&A on CF and IS; long-term debt |
| VST | 0001692819-26-000006 | 10-K FY2025 | "Operating revenues" against the unpresented component |
| PG | 0000080424-26-000103 | 10-K FY2026 (to 2026-06-30) | 2025-06-30 shares issued presented, outstanding not |
| AMZN | 0001018724-26-000004 | 10-K FY2025 | PP&E incl. finance leases; `CostsAndExpenses`; interest expense |
| AMD | 0000002488-26-000018 | 10-K FY2025 | FY2024 pretax before equity income (1,989M) against 2,022M |
| ABT | 0001628280-26-010185 | 10-K FY2025 | FY2023 effective tax rate, note-only |
| LMT | 0001628280-26-004195 | 10-K FY2025 | 2025-12-31 PP&E 8,875M against the 10-Q recast 11,292M |
| TSLA | 0001628280-26-003952 | 10-K FY2025 | revenue on another element; net and comprehensive income attributable |
| INTC | 0000050863-26-000011 | 10-K FY2025 | FY2023 "Interest and other, net" against note interest income |

**Substitutions for validation-only examples.** The report's MCD FY2025 D&A, AMD FY2025 pretax
and ORCL FY2022 zero `LongTermDebt` are keys of validation cases only. They are not fixtures:
MCD's and AMD's rules are pinned on the FY2024 columns of the same dev filings (no key in any
split), and the zero-value rule on AAPL's real zero and a constructed zero (C8). The rules then
apply to those validation keys unchanged.

**Hand checks** (rendered primary documents, our local EDGAR copies; amounts in millions):

| Filing | Statement line | Printed |
|---|---|---|
| AAPL FY2025 | BS "Term debt" (current) / "Term debt" (non-current), 2025-09-27 | 12,350 / 78,328 |
| AAPL FY2025 | Note "Total term debt", 2025 | 90,678 |
| AAPL FY2025 | IS "Total operating expenses", 2025 / 2024 | 62,151 / 57,467 |
| AAPL FY2025 | CF "Proceeds from issuance of term debt, net", 2024 | — |
| KO FY2025 | BS "Accounts payable and accrued expenses"; note "Accounts payable" | 14,813; 5,649 |
| KO FY2025 | IS "Selling, general and administrative expenses" (no S&M line) | 14,521 |
| META FY2025 | IS "Interest and other income, net", 2025 / 2024; note "Interest income" | 2,656 / 1,283; 2,123 / 2,517 |
| ORCL FY2025 | BS "Deferred revenues", 2024-05-31; note "Total deferred revenues" | 9,313; 10,546 |
| MCD FY2025 | CF "Depreciation and amortization" / IS "Depreciation and amortization", 2024 | 2,097 / 447 |
| MCD FY2025 | BS "Long-term debt", 2025-12-31 | 39,973 |
| VST FY2025 | IS "Operating revenues", 2025 / 2023; note "Total revenue from contracts with customers" | 17,738 / 14,779; 17,586 / 13,804 |
| PG FY2026 | BS "Common stock … shares issued: 2025 - 4,009.2"; no shares-outstanding line | 4,009.2 |
| AMZN FY2025 | IS "Total operating expenses", 2025 / 2024 | 636,949 / 569,366 |
| AMZN FY2025 | BS "Property and equipment, net"; IS "Interest expense", 2025 | 357,025; (2,274) |
| AMD FY2025 | IS "Income … before income taxes and equity income", 2024; note "Total pre-tax income including equity income in investee" | 1,989; 2,022 |
| ABT FY2025 | IS "Taxes on Earnings" (no rate line); note "Effective tax rate on earnings", 2023 | 941; 14.1% |
| LMT FY2025 | BS "Property, plant and equipment, net", 2025 / 2024 | 8,875 / 8,726 |
| TSLA FY2025 | IS "Total revenues" 2023; "Net income" / "Net income attributable to common stockholders" 2024 | 96,773; 7,153 / 7,091 |
| TSLA FY2025 | CI "Comprehensive income" / "… attributable to common stockholders", 2025 | 4,886 / 4,825 |
| INTC FY2025 | IS "Interest and other, net", 2023; note "Interest income", 2023 | 629; 1,335 |
| INTC FY2025 | IS "Restructuring and other charges", 2023 (`RestructuringSettlementAndImpairmentProvisions`) | (62) |
| INTC FY2025 | IS "Net income (loss)" / "Net income (loss) attributable to Intel", 2023 | 1,675 / 1,689 |
| AAPL FY2025 | BS current "Marketable securities" (above "Total current assets") / non-current, 2025 | 18,763 / 77,723 |
| AAPL FY2025 | BS "… 14,773,260 … shares issued and outstanding" (thousands), 2025 | 14,773,260 |
| AMZN FY2025 | IS "Sales and marketing", 2025 | 47,129 |

**Hand count for C9** (the 19 fixture keys of `test_c9_manifest.py`, each status assigned by
hand from the line beside it): agree 8, agree_other_element 3, note_witnessed 4, value_recast 2,
no_fsds 1, disagree 1 (ORCL, resolved), ambiguous 0, rejected_line 0, note_unlisted 0; resolved
1; held 3 (AAPL debt, the absent filing, LMT PP&E).

**Recorded answers for C10** (`recorded_answers.json`) are constructed in the shape a run
stores them, not copied from a stored run: each arm states one reading of KO's FY2025 accounts
payable (5,649M or 14,813M, both hand-checked), and both state TSLA's FY2023 revenue.

**How the fixtures were cut.** A one-off builder (not committed, not a test) read
`data/cache/sec_fsds/filtered/<q>/*.tsv`, the companyfacts payloads under
`data/storage/sec_edgar/_sec_api/` and the Arelle cache's `us-gaap-doc-2025.xml` and
`us-gaap-lab-2025.xml`, and wrote the excerpts and `provenance.json` (2026-11-07). The rules
were checked against every fixture filing for each of the 51 families during authoring; the
only remaining multi-line selections under `combine: one` are INTC's two "Additions to property,
plant and equipment" capex lines and VST's two debt-issuance lines, which block (by design)
until reviewed.

**Not locked here.** The build wiring (`build_v2_suites` calling `select_key` for every key
input and `universe_status` for every screen member, `manifest["key_independence"]`, the
copies under `configs/eval/v2/`) follows from requirements 3, 5, 6 and 9 but is not exercised
by these tests: the v2 build needs the full phrasing and source harness. The reviewer checks it
in the implementation PR, and the corpus-wide witness run (all keys, both splits, by rule) comes
after this spec. Rules for families with no fixture are reviewed in this PR but pinned only by
the SHA-256; that run is where they meet data.
