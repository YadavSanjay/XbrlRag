# key-definitions — one stated definition for Debt, DeferredRevenue and net / comprehensive income

Split out of [key-statement-witness](key-statement-witness.md) ("Scope"), which keeps the
statement line as a *witness* of a key and left open what a definitional family's key *is*.
Area D of the [test strategy](../design/XbrlRag_Test_Strategy.md) (eval fairness) and nuance N9
("one definition per metric, stated in the answer"). It changes S4 of
[the eval semantics](../design/XbrlRag_Eval_Semantics.md) and the v2 case record, and it gates
the frozen v2 suite rebuild.

**Decided by the maintainer on 2026-11-09**, each as recommended in the draft (the options and
evidence are kept below under "Decision record"):

- **Debt = D-C, total debt as presented**: the sum of every borrowing line the balance sheet
  presents; finance leases only inside a line the filer calls debt; operating leases never. A
  disclosed alternate reading `note_total` only where the balance sheet presents no current
  debt line. Questions say "total debt".
- **DeferredRevenue = R-B, total deferred revenue**: current plus non-current, by the
  precedence of requirement 3. A disclosed alternate reading `current_presented` only where the
  balance sheet presents the current line alone. It replaces the ORCL resolution.
- **Net and comprehensive income = N-A, parent only**: the consolidated figure stands in only
  when the filing reports no different parent figure; net and comprehensive income are never
  accepted for each other.

**Sequencing.** [witness-rules-r2](witness-rules-r2.md) (`spec/witness-rules-r2`)
rewrites `configs/eval/v2/statement_lines.yaml` for every family except `Debt` and
`DeferredRevenue`. This change edits only those two entries, and **no** `NetIncome` or
`ComprehensiveIncome` rule. This branch is cut on top of `spec/witness-rules-r2` and is
rebased onto `main` after `spec/witness-rules-r2` merges; its implementation lands only after r2's
implementation merges (Notes, "Sequencing with r2").

## Requirement

Terms are those of key-statement-witness (key, *the same*, concept, primary statements,
WitnessLine, `WitnessBlocked`) and shares-outstanding-reading (alternate reading). All FSDS
reading goes through `src/eval/sec_reference/fsds.py`; the statement lines through
`select_lines`. A companyfacts document is SEC's shape (`{"facts": {"us-gaap": {tag: {"units":
{unit: [entry, ...]}}}}}`); a **filing fact** of `<tag>` is the us-gaap entry of that tag in the
unit whose `accn` is the filing read, `end` is the balance-sheet date and that has no `start`.

### 1. Definitions are named (`key_definition`)

`key_definition(family)` returns `debt_total` for `Debt`, `deferred_revenue_total` for
`DeferredRevenue`, `net_income_parent` for `NetIncome`, `comprehensive_income_parent` for
`ComprehensiveIncome`, and None for every other family. A v2 case of these families records it
as `key.definition`; every other family's case record is unchanged. S4 of the eval semantics
gains these rows (implementation PR):

| Metric | Definition |
|---|---|
| Debt | Every borrowing line the balance sheet presents (short-term borrowings, commercial paper, current maturities, long-term debt), summed; finance-lease obligations only where a debt line includes them; operating leases never |
| Deferred revenue | Contract liabilities, current plus non-current (requirement 3) |
| Net income | Attributable to the parent; the consolidated figure only where the filing reports no different parent figure |
| Comprehensive income | Total comprehensive income attributable to the parent, same rule |

### 2. Debt (`debt_key`)

`debt_key(tables, rules, company_facts, cik, fiscal_year, adsh, period_end, unit)` returns a
`DefinedKey` `{"family": "Debt", "definition": "debt_total", "status", "basis", "value",
"components", "key", "readings", "reason"}` for the balance at `period_end` in the annual
filing `adsh`:

1. **No filing.** `submission(tables, adsh)` is None: status `no_fsds`; `basis`, `value`,
   `key` None; `components` and `readings` empty.
2. **Lines.** `lines = select_lines(tables, rules["Debt"], adsh, None, period_end, unit)` (the
   locked `Debt` rule, Notes). None: status `not_presented`, the rest as in step 1. There is no
   note fallback (the `Debt` note-concept entry is removed, requirement 7).
3. **Components.** Each line, in `(report, line)` order, is a `DefinedComponent`
   `{"concept", "value", "source": "statement", "section", "plabel", "stmt", "report",
   "line", "lease_inclusive"}`:
   - `section` is `current` when the line lies above the first line of the same report whose
     label matches `total current liabilities` (case-insensitive), else `noncurrent` (also
     when the report has no such line);
   - `lease_inclusive` is true when the line's label or tag contains `lease`
     (case-insensitive);
   - `value`: for a standard concept (not `ext:`), the filing fact of the line's tag. When
     that fact is missing, status `component_missing`; when its value is not the same as the
     line's FSDS value, status `component_mismatch` (both blocking: `value`, `key` None,
     `reason` names the concept). For an `ext:` concept (companyfacts holds no extension
     facts) the line's FSDS value.
4. **Key.** Otherwise status `defined`, `basis` `presented`, `value` the sum of the
   components, and `key` an `AnswerKey` with that value, `unit`, `decimals` None, `concept`
   `s4:debt_total`, `cik`, `period_start` None, `period_end`, `fiscal_year`, `calendar_year`
   None, `accession` `adsh`, `filed` and `form` of the submission (`filed` as ISO date),
   `key_source` the `+`-joined sorted sources of the component values (`sec_companyfacts` for a
   filing fact, `sec_fsds` for an FSDS value), `selection` `statement_sum`, `policies` `["S1",
   "S4", "S6"]`, `status` `single-source`, `verified_by` empty.
5. **Reading `note_total`.** When no component is `current`, and the filing fact of
   `DebtLongtermAndShorttermCombinedAmount` (else of `LongTermDebt`) exists, is larger than the
   key's value and not the same: `readings` is `[{"reading": "note_total", "value",
   "concept": "us-gaap:<tag>", "period_end", "accession": adsh}]`. Otherwise empty.

### 3. Deferred revenue (`deferred_revenue_key`)

Same signature and record (`family` `DeferredRevenue`, `definition`
`deferred_revenue_total`, key concept `s4:deferred_revenue_total`). Steps 1 and 3-4 of
requirement 2 apply to the lines of `rules["DeferredRevenue"]`; the value and basis are:

1. Presented lines in **both** sections: their sum, basis `presented_total`. No filing fact is
   consulted.
2. Presented lines in the **current** section only; `current` is their sum:
   - the filing fact of `ContractWithCustomerLiability` (else `DeferredRevenue`) exists and is
     not below `current`: that value, basis `note_total`;
   - else the filing fact of `ContractWithCustomerLiabilityNoncurrent` (else
     `DeferredRevenueNoncurrent`) exists: `current` plus it, basis
     `current_plus_note_noncurrent`;
   - else `current`, basis `no_noncurrent_reported`.

   A filing fact used here is appended to `components` with `source` and `section` `note` and
   no line fields. When the value is not the same as `current`, `readings` is
   `[{"reading": "current_presented", "value": current, "concept", "period_end", "accession"}]`,
   `concept` the presented line's concept (or `s4:deferred_revenue_presented` for several
   lines).
3. Presented lines in the **non-current** section only: their sum, basis `presented_total`.
4. **No line**: the filing fact of `ContractWithCustomerLiability` (else `DeferredRevenue`):
   status `defined`, basis `note_total`, that fact as the only (note) component; none: status
   `not_presented`.

Readings are empty unless step 2 sets one.

### 4. Net and comprehensive income attribution (`attribution_status`)

`attribution_status(tables, family, adsh, period_start, period_end, unit, concept)`:

- the **parent elements**: `NetIncome` `us-gaap:NetIncomeLoss`,
  `ifrs-full:ProfitLossAttributableToOwnersOfParent`; `ComprehensiveIncome`
  `us-gaap:ComprehensiveIncomeNetOfTax`,
  `ifrs-full:ComprehensiveIncomeAttributableToOwnersOfParent`;
- the **consolidated elements**: `NetIncome` `us-gaap:ProfitLoss`,
  `us-gaap:NetIncomeLossIncludingPortionAttributableToNonredeemableNoncontrollingInterest`,
  `ifrs-full:ProfitLoss`; `ComprehensiveIncome`
  `us-gaap:ComprehensiveIncomeNetOfTaxIncludingPortionAttributableToNoncontrollingInterest`,
  `ifrs-full:ComprehensiveIncome`.

It returns `parent` for a parent element; for a consolidated element,
`consolidated_is_parent` when no parent element of the family has a consolidated FSDS value
(`period_value`) for the period and unit in `adsh`, or every such value is the same as the
concept's own value, else `includes_noncontrolling_interests`. Another family or concept raises
`ValueError`. A key with `includes_noncontrolling_interests` is never keyed (the witness
already blocks it: its value differs from the parent's line); the `NetIncome` and
`ComprehensiveIncome` statement rules are unchanged.

### 5. Readings reach the case (`case_readings`)

`case_readings(defined, shape)` returns a copy of `defined["readings"]` for shapes `lookup` and
`trend`, and `[]` for every other shape (screens, comparisons, ratios, point-in-time). The v2
builder puts them in a lookup's `alt_readings` and a trend row's own `alt_readings`, and lists
them in `key.alternates`, exactly as shares-outstanding-reading requirement 4 does for cover
readings. Grading is that spec's requirements 6-9, unchanged.

### 6. Screens (`member_status`, `attribution_member`)

`member_status(defined)` returns `{"status", "reason", "concept", "value"}`: `defined` →
`ranked`, reason None, the key's concept and value; `no_fsds` → `held`, reason `no_fsds`;
`not_presented` → `metric_not_tagged`, reason None (both with concept and value None); a
blocking status (`component_missing`, `component_mismatch`) raises `WitnessBlocked`.

`attribution_member(status, concept, value)` returns `ranked` (reason None) for `parent` and
`consolidated_is_parent`, and `definition_excluded` with reason
`substitute:includes_noncontrolling_interests` for `includes_noncontrolling_interests`, each
with the given concept and value. A screen ranks only `ranked` members.

### 7. Config changes

- `configs/eval/v2/statement_lines.yaml`: the `Debt` and `DeferredRevenue` entries load equal
  to the locked `fixtures/statement_lines_kd.yaml`; no other entry changes.
- `configs/eval/v2/note_concepts.yaml`: `Debt` removed; `DeferredRevenue` is
  `[us-gaap:ContractWithCustomerLiability, us-gaap:DeferredRevenue]`; every other family as in
  key-statement-witness's locked list.
- `configs/eval/v2/key_resolutions.yaml`: the ORCL FY2024 DeferredRevenue entry is removed.
- `configs/eval/v2/lexicon.yaml`: `Debt` label `Total Debt`, synonyms including `total debt`
  and no surface naming long-term debt; `DeferredRevenue` synonyms gain `total deferred
  revenue`. `configs/eval/v2/paraphrase_forms.yaml`: no `Debt` form names long-term ("LT").

## Acceptance criteria

All figures are hand-checked against the rendered primary documents (Notes, "Hand checks");
"the same" is 1e-9 relative (FSDS and companyfacts carry the filed value).

- **C1** (Debt key): `debt_key` sums the presented borrowing lines: AAPL FY2025 7,979 + 12,350
  + 78,328 = 98,657; KO FY2025 1,551 + 1,822 + 42,119 = 45,492; WMT FY2025 3,068 + 2,598 +
  33,401 = 39,067 (finance-lease 800 / 5,923 and operating-lease lines out); TSLA FY2025 1,640 +
  6,736 = 8,376 from FSDS (extension lines), both `lease_inclusive`; MCD FY2025 39,973 (lease
  lines out); LLY FY2025 1,635 + 40,868 = 42,503. Components equal the raw `pre` rows; the
  composite key's fields are requirement 2's; an added non-debt line never changes the key; a
  disagreeing companyfacts value is `component_mismatch`; absent filing, removed lines and a
  missing fact give `no_fsds`, `not_presented`, `component_missing`; the live `Debt` rule
  selects AAPL's three borrowing lines. Oracle: hand addition of the printed lines.
- **C2** (Debt reading): AMZN FY2025 65,648 with `note_total` 68,836 and GOOGL FY2025 46,547
  with 49,085 (no current debt line); none for AAPL and LLY (current lines) or MCD (note equal
  to the key); `DebtLongtermAndShorttermCombinedAmount` preferred; an added current debt line
  removes the reading; a reading exists exactly when the note exceeds the key. Oracle: printed
  face lines and debt-note totals.
- **C3** (DeferredRevenue key): CSCO FY2025 16,416 + 12,363 = 28,779 and TSLA FY2025 3,424 +
  3,631 = 7,055 (`presented_total`; TSLA's rollforward 3,867 not consulted); ORCL 2024-05-31
  10,546, AAPL FY2025 13,700, GOOGL FY2025 8,600 (`note_total`, readings 9,313 / 9,055 / 6,578);
  LMT FY2025 11,440 (`no_noncurrent_reported`); MCD FY2025 945 (non-current only); META
  2024-12-31 772 (note only); KO `not_presented`; without ORCL's total, 9,313 + 1,233 =
  10,546 (`current_plus_note_noncurrent`); a total below the current line is ignored;
  presented parts win over any tagged total. Oracle: printed lines and note totals; the
  us-gaap definition of `ContractWithCustomerLiability`.
- **C4** (attribution): INTC 2023 `NetIncomeLoss` 1,689 `parent`, `ProfitLoss` 1,675
  `includes_noncontrolling_interests`; AVGO FY2024 `ProfitLoss` 5,895 (no parent element) and
  VST FY2025 944 (parent the same) `consolidated_is_parent`; TSLA FY2025 comprehensive income
  4,825 `parent` / 4,886 `includes_...`; INTC FY2025 850 against 557 `includes_...`; AVGO
  FY2025 23,137 `consolidated_is_parent`; other families and concepts refused; removing the
  parent rows, or setting the parent equal, makes the consolidated figure the parent's.
  Oracle: the us-gaap definitions of `NetIncomeLoss` and `ProfitLoss`; the printed lines.
- **C5** (readings in cases and grades): `case_readings` gives AMZN's `note_total` reading for
  lookups and trends and none for other shapes; graded, 68,836 is CORRECT marked
  `note_total`, 65,648 CORRECT marked `key`, 66,500 INCORRECT; ORCL's 9,313 CORRECT marked
  `current_presented`; for any value, CORRECT exactly within 1e-4 of the key or the reading.
  Oracle: shares-outstanding-reading requirements 6-9; distances by hand.
- **C6** (screens): the FY2025 Debt members rank AAPL 98,657, AMZN 65,648, GOOGL 46,547, KO
  45,492, LLY 42,503, MCD 39,973, TSLA 8,376, in any build order; `no_fsds` is held,
  `not_presented` is `metric_not_tagged`, a mismatch raises `WitnessBlocked`; a consolidated
  income figure with a different parent figure is `definition_excluded`
  (`substitute:includes_noncontrolling_interests`). Oracle: hand ranking; requirements 4, 6.
- **C7** (names and config): `key_definition` gives the four ids and None elsewhere; the live
  `Debt` and `DeferredRevenue` rules load equal to the locked copy; the live note list,
  resolutions and lexicon are those of requirement 7. Oracle: requirements 1 and 7.

## Interface

New module `src/eval/keys/key_definitions.py` (stubbed in the spec PR):

- `DefinedComponent`, `DefinedKey` (`TypedDict`s, requirement 2)
- `key_definition(family: str) -> str | None`
- `debt_key(tables: FsdsTables, rules: Mapping[str, StatementRule], company_facts: dict, cik: str, fiscal_year: int, adsh: str, period_end: str, unit: str) -> DefinedKey`
- `deferred_revenue_key(tables: FsdsTables, rules: Mapping[str, StatementRule], company_facts: dict, cik: str, fiscal_year: int, adsh: str, period_end: str, unit: str) -> DefinedKey`
- `attribution_status(tables: FsdsTables, family: str, adsh: str, period_start: str | None, period_end: str, unit: str, concept: str) -> str`
- `attribution_member(status: str, concept: str, value: float | None) -> dict[str, Any]`
- `member_status(defined: DefinedKey) -> dict[str, Any]`
- `case_readings(defined: DefinedKey, shape: str) -> list[dict[str, Any]]`

Behaviour on existing code (implementer; reviewed in the implementation PR, not exercised by
these tests): `src/eval/suites/v2_witness.py` routes `Debt` and `DeferredRevenue` keys and
screen members through `debt_key` / `deferred_revenue_key` / `member_status`, and income
members through `attribution_status` / `attribution_member`; `src/eval/suites/v2_suite.py`
records `key.definition` and attaches `case_readings`; the S4 rows of requirement 1; the config
files of requirement 7. After the implementation, the v2 suites are rebuilt and diffed and the
stored runs re-graded with `key_change_regrade` before any comparison.

## Notes

### Locked rules (`fixtures/statement_lines_kd.yaml`)

Written from accounting vocabulary only (the names of borrowings and contract liabilities in
the us-gaap labels), never from one filer's wording; a dry run over every corpus 10-K/20-F of
FY2023-2026 in the FSDS cache selected no asset, operating-lease or equity line.

- **Debt** includes `debt`, `borrowings`, `commercial paper`, `notes payable`, `loans payable`,
  `bonds payable`, `bank loans`, `short-term loans`, `senior notes`, `term loan(s)`; excludes
  issuance costs, securities, held for sale, deferred, interest, related party, ESOP, reserve,
  investment, receivable, asset(s), operating lease, right-of-use, any lease line that names
  neither debt, borrowings, loans, notes nor bonds, guarantee and equity; a `total debt` /
  `total borrowings` line is preferred; `combine: sum`.
- **DeferredRevenue** includes `deferred revenue`, `contract liabilit`, `unearned revenue`,
  `deferred income`; excludes tax, government, incentive; `combine: sum` (current and
  non-current both selected).

### Fixtures

From the local FSDS cache (`data/cache/sec_fsds/filtered`, byte-for-byte rows of SEC's quarter
zips), the companyfacts payloads of the fixture CIKs (entries of the fixture filings only) and
the Arelle cache's us-gaap 2025 label and documentation linkbases; `provenance.json` holds each
file's SHA-256, each filing's EDGAR URL and primary-document SHA-256, and each quarter zip's URL,
SHA-256, size and retrieval time. FSDS rows: the `sub` row, every BS/IS/CI `pre` row, the
consolidated `num` rows of those lines' tags and their `tag` rows. A one-off builder (not
committed) cut them on 2026-11-09.

| Filer | Accession | Quarter | Pins |
|---|---|---|---|
| AAPL | 0000320193-25-000079 | 2025q4 | Debt three lines; deferred revenue current + note total |
| KO | 0001628280-26-010047 | 2026q1 | Debt incl. "Loans and notes payable"; no deferred revenue |
| WMT | 0000104169-25-000021 | 2025q1 | FY2025 debt; finance- and operating-lease lines excluded |
| TSLA | 0001628280-26-003952 | 2026q1 | extension debt-and-finance-lease lines; deferred revenue two lines; CI |
| MCD | 0000063908-26-000035 | 2026q1 | one debt line, lease lines; non-current deferred revenue |
| LLY | 0000059478-26-000013 | 2026q1 | mixed current debt line |
| AMZN | 0001018724-26-000004 | 2026q1 | no current debt line; `note_total` |
| GOOGL | 0001652044-26-000018 | 2026q1 | the same; deferred revenue note total |
| CSCO | 0000858877-25-000111 | 2025q3 | deferred revenue current + non-current |
| ORCL | 0000950170-25-087926 | 2025q2 | 2024-05-31 deferred revenue current, non-current, total |
| LMT | 0001628280-26-004195 | 2026q1 | current-only contract liabilities |
| META | 0001628280-26-003942 | 2026q1 | 2024-12-31 note-only contract liabilities |
| INTC | 0000050863-26-000011 | 2026q1 | 2023 net income parent vs consolidated; 2025 CI |
| AVGO | 0001730168-25-000121 | 2025q4 | `ProfitLoss` only (FY2024); CI (FY2025) |
| VST | 0001692819-26-000006 | 2026q1 | consolidated equals parent |

### Split check

Every pinned fact was checked against the key inputs of the frozen and rebuilt v2 suites
(`data/eval/v2_frozen`, `data/eval/v2_frozen_rebuild`, dev and validation case records; no
held-out data). The draft's validation-split pins were swapped for other columns of the same
dev filings:

- TSLA net income (FY2023-25 are all validation keys) → INTC FY2023 net income (dev only);
- INTC FY2025 net income -267 (validation) → not pinned (the draft's sign-flip example stays as
  prose only);
- AVGO FY2025 `ProfitLoss` 23,126 (validation) → AVGO FY2024 5,895 (dev only);
- META 2025-12-31 contract liabilities (no key) → META 2024-12-31 772 (a dev key);
- WMT FY2026 (no key, latest filing) → WMT FY2025 (a dev key, 39,067 = its debt lines).

Every other pinned balance or flow is a dev key input or no key in any split.

### Hand checks

The printed figure and row label of each pinned fact in the rendered primary document (our
local EDGAR copies under `data/storage/sec_edgar/<ticker>/<year>/10-K/`), read from the iXBRL
row that carries the fact (USD millions):

| Filing | Statement line (printed label) | Printed |
|---|---|---|
| AAPL FY2025 | BS "Commercial paper" / "Term debt" (current) / "Term debt" (non-current) | 7,979 / 12,350 / 78,328 |
| AAPL FY2025 | Note "Total term debt" | 90,678 |
| AAPL FY2025 | BS "Deferred revenue"; note total deferred revenue | 9,055; $13.7 billion |
| KO FY2025 | BS "Loans and notes payable" / "Current maturities of long-term debt" / "Long-term debt" | 1,551 / 1,822 / 42,119 |
| WMT FY2025 | BS "Short-term borrowings" / "Long-term debt due within one year" / "Long-term debt" | 3,068 / 2,598 / 33,401 |
| WMT FY2025 | BS "Finance lease obligations due within one year" / "Long-term finance lease obligations"; operating-lease lines | 800 / 5,923; 1,499 / 12,825 |
| TSLA FY2025 | BS "Current portion of debt and finance leases" / "Debt and finance leases, net of current portion" | 1,640 / 6,736 |
| TSLA FY2025 | BS "Deferred revenue" / "Deferred revenue, net of current portion"; note "Deferred revenue — end of period" | 3,424 / 3,631; 3,867 |
| TSLA FY2025 | CI "Comprehensive income" / "Comprehensive income attributable to common stockholders" | 4,886 / 4,825 |
| MCD FY2025 | BS "Long-term debt"; "Lease liability" (current) / "Long-term lease liability" | 39,973; 694 / 14,147 |
| MCD FY2025 | BS "Deferred revenues - initial franchise fees" | 945 |
| LLY FY2025 | BS "Short-term borrowings and current maturities of long-term debt" / "Long-term debt"; note "Total debt" | 1,635 / 40,868; 42,503 |
| AMZN FY2025 | BS "Long-term debt"; note "Total face value of long-term debt"; BS "Long-term lease liabilities" | 65,648; 68,836; 87,339 |
| GOOGL FY2025 | BS "Long-term debt"; debt note "Total" | 46,547; 49,085 |
| GOOGL FY2025 | BS "Deferred revenue"; note total deferred revenue | 6,578; $8.6 billion |
| CSCO FY2025 | BS "Deferred revenue" (current) / (non-current); note "Total" | 16,416 / 12,363; 28,779 |
| ORCL FY2025 | BS "Deferred revenues", 2024-05-31; note "Deferred revenues, non-current (in other non-current liabilities)" / "Total deferred revenues" | 9,313; 1,233 / 10,546 |
| LMT FY2025 | BS "Contract liabilities" (no non-current or total fact) | 11,440 |
| META FY2025 | Revenue note, contract liabilities at 2024-12-31 | 772 |
| INTC FY2025 | IS "Net income (loss)" / "Net income (loss) attributable to Intel", 2023 | 1,675 / 1,689 |
| INTC FY2025 | CI "Total comprehensive income (loss)" / "... attributable to Intel", 2025 | 850 / 557 |
| AVGO FY2025 | IS "Net income", FY2024 (`ProfitLoss`; no `NetIncomeLoss` fact); CI "Comprehensive income", FY2025 | 5,895; 23,137 |
| VST FY2025 | IS "Net income" / "Net income attributable to Vistra" / "... Vistra common stock" | 944 / 944 / 752 |

### Retired tests touched by this spec PR

- `test/spec/key-statement-witness/test_c2_rules.py`: r2's `test_live_rules_select_the_hand_checked_line`
  runs every `HAND_CHECKED` row against the live rules; the Debt row (AAPL "two Term debt
  lines") is filtered out of that live check, because the live rule is total debt. C1's
  `test_live_debt_rule_selects_aapl_borrowing_lines` holds the live rule to AAPL's three
  lines instead. The r1 locked-copy check of the row is unchanged.
- `test/spec/key-statement-witness/test_c4_note_concepts.py`: the byte pin of the live note
  list becomes the subset check its requirement 4 states ("may only shrink"); the locked copy's
  SHA-256 pin is unchanged.
- `test_c3_disagreement.py` already lets the live resolutions shrink; unchanged.

### Sequencing with r2

- r2's `test_c7_record.py` pins `rules_sha256` of its judging record to the SHA-256 of the live
  `statement_lines.yaml`. This change's implementation edits that file, so the pin would fail.
  r2's lock is active, so this spec cannot touch its tests now. **After r2's implementation
  merges** (r2 retired), the rebased spec PR adds a byte copy of r2's accepted rules file as a
  fixture (SHA-256 equal to the record's `rules_sha256`), changes r2's C7 pin to that copy, and
  adds a C7 test that every live family except `Debt` and `DeferredRevenue` loads equal to it.
- Until then the branch sits on `spec/witness-rules-r2`; the coordinator rebases it onto
  `main` after `spec/witness-rules-r2` merges.

### Review points

- **TSM current debt.** TSM's current part is the line "Long-term liabilities - current
  portion" (`ifrs-full:CurrentPortionOfLongtermBorrowings`). Its label names neither debt nor
  borrowings, and "current portion of long-term liabilities" is not a borrowing in accounting
  vocabulary (it can hold any long-term liability), so the general rule does not add it. TSM's
  Debt key is its non-current "Bonds payable" plus "Long-term bank loans" and omits the current
  portion. TSM is excluded from USD screens (S6); a TSM Debt lookup would be keyed low. Proposal:
  no TSM Debt intents until rules can match on the standard element as well as the label (a
  schema change, out of scope here).
- **AMZN's `note_total`** is `LongTermDebt` 68,836, which leaves out its 455 of short-term
  borrowings (also note-only). An answer adding them (69,291) is 0.66% from the reading and
  graded wrong. Kept: the reading is one tagged figure, not a reconstruction.
- **Lease-inclusive debt lines** (KO, AVGO, UNP, VST, CEG, MU, HD tag
  `LongTermDebtAndCapitalLeaseObligations`; TSLA, UPS name finance leases) are ranked like
  any other; the flag is informational. MSFT's 46,172 of finance leases sit outside its debt
  lines and stay out.
- **Precision.** Composite keys carry `decimals` None (companyfacts gives none), so grading
  falls back to the case's `rel_tol`; note totals printed in billions (AAPL 13.7, GOOGL 8.6)
  are keyed at their filed value.

## Decision record (draft of 2026-11-09, decided as recommended)

Evidence: read-only survey of the corpus filers' annual filings in the FSDS cache and their
companyfacts payloads (corpus CIKs only). No held-out data, no DB, no LLM.

**Debt.** No filer presents a total-debt line. About a third mix short-term borrowings into
the current line (`DebtCurrent`: CSCO, LLY, PG, MRK, INTC, MU, NVDA, QCOM, ADBE, UPS). AAPL, KO,
HD, CEG, VST and WMT present separate short-term lines. Finance leases sit inside debt lines at
TSLA and UPS; WMT presents them separately; operating leases are always separate. AMZN, GOOGL,
META, COST FY2025 and MCD FY2025 present only the non-current line. `LongTermDebt` is untagged
by 11 of 33 filers. Options: **D-A** non-current line (AAPL 78,328; S4 ratios would need a
second definition); **D-B** `LongTermDebt` incl. current maturities (90,678; unwitnessable for a
third of filers); **D-C** total of presented borrowing lines (98,657; S4's own definition;
witnessed for 32 of 33 filers). Decided: D-C with `note_total`.

**DeferredRevenue.** CSCO, MSFT, ADBE, QCOM and TSLA present current and non-current lines;
ORCL, AAPL, AMZN and GOOGL only the current line with the rest in the notes; LMT, CRM, NFLX and
HD only the current line and no non-current fact; MCD only a non-current line; 17 filers none.
TSLA's tagged `ContractWithCustomerLiability` (3,867, a rollforward) is below its two face lines
(7,055). Options: **R-A** current line (CSCO 16,416; reverses the ORCL resolution); **R-B** total
(CSCO 28,779, ORCL 10,546); **R-C** face lines only (mixes measures in screens). Decided: R-B with
`current_presented`.

**Net income.** 24 of 33 filers have no noncontrolling-interest line. KO, PG, MRK, TSLA, CEG,
VST and INTC present both; INTC FY2025 shows 26 consolidated against -267 for the parent. AVGO
tags only `ProfitLoss`. Options: **N-A** parent only, consolidated standing in only without a
different parent figure; **N-B** parent with a consolidated alternate reading; **N-C**
consolidated. Decided: N-A, for comprehensive income too; S4's ROE, D48 and EPS use the parent.
