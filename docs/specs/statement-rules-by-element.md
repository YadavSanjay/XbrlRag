# statement-rules-by-element — statement-line rules that match the standard element, and negating qualifiers

Status item P2 (parked from the test work): "Statement rules that match the standard element as
well as the label (schema change); unblocks TSM debt by element and the negating-qualifier rule
('excluding finance leases')". Follows [key-statement-witness](key-statement-witness.md)
(schema and engine), [witness-rules-r2](witness-rules-r2.md) and
[witness-rules-r2-1](witness-rules-r2-1.md) (rule content and its acceptance),
[key-definitions](key-definitions.md) (the `Debt` rule) and
[reviewed-resolutions](reviewed-resolutions.md).

**The gaps.** A statement-line rule (`configs/eval/v2/statement_lines.yaml`) selects a line by
its label only (`include` / `require` / `exclude`, searched in the FSDS `plabel`). Two things
cannot be said in that schema:

1. **"This line, identified by its standard element, is the family's."** TSM (an IFRS 20-F
   filer) presents its current borrowings as "Long-term liabilities - current portion", tagged
   `ifrs-full:CurrentPortionOfLongtermBorrowings`. The label names neither debt nor borrowings,
   and "current portion of long-term liabilities" is not a borrowing in accounting vocabulary,
   so no general label pattern may take it (key-definitions, Review points). The element's
   definition, however, *is* a borrowing. Today TSM's Debt is its "Bonds payable" plus
   "Long-term bank loans" only (FY2024: NT$958,428.9M instead of NT$1,018,286.8M), and
   key-definitions proposes no TSM Debt intents until rules can match on the element.
2. **Negating qualifiers.** A label term inside a qualifier such as "excluding finance leases"
   or "net of issuance costs" names what the line is *not*. Today such a term both rejects
   (Capex's `\bleases?\b` rejects TSLA's "Purchases of property and equipment excluding finance
   leases, net of sales", left to a reviewed resolution by witness-rules-r2-1 §1.3 and the
   2026-11-09 corpus measurement) and includes (the Debt rule takes "Lease liabilities,
   excluding bank loans" as a debt line because it names bank loans). r2 and r2.1 worked around
   the first with negative lookbehinds per pattern; witness-rules-r2-1 §1.3 recorded the
   general rule as a follow-up needing a schema change with a per-family switch, because
   CostOfRevenue rejects "excluding" on purpose.

**What this change does and does not do.** It adds two optional rule fields, their validation
and their engine semantics (code + schema). It changes **no rule content**: the live rules file
and every locked or accepted copy load exactly as before, and every witness outcome is
unchanged. Rule content that uses the fields (TSM's current portion for `Debt`; qualifier
stripping for `Capex` or other judged families) follows the existing acceptance procedure in its
own spec change (requirement 6).

## Requirement

Terms are those of key-statement-witness (rule, candidate, `select_lines`, concept as
`_concept` forms it: `us-gaap:<tag>` for a `us-gaap/…` version, `ifrs-full:<tag>` for
`ifrs/…`, `<prefix>:<tag>` for another standard version, `ext:<tag>` for an extension).

### 1. Schema (`load_statement_rules`)

Two optional fields join the rule fields of key-statement-witness requirement 2:

- `elements`: a non-empty list of standard-taxonomy concepts `<prefix>:<LocalName>` with
  `<prefix>` one of `us-gaap`, `ifrs-full`, `srt`, `dei` (the standard taxonomies of
  witness-rules-r2 requirement 1), each matching `^[A-Za-z][\w.-]*:[A-Za-z_][\w.-]*$`, no
  duplicates, and none a key of the same rule's `substitutes` (a stated substitute is by
  definition not the family's measure).
- `strip_qualifiers`: a boolean.

`load_statement_rules` raises `ValueError` for: `elements` not a list, an empty list, a
non-string or malformed entry, an `ext:` or other non-standard prefix, a duplicate, an entry that
is also a substitute; `strip_qualifiers` not a boolean (`'yes'`, `1`). All existing validation is
unchanged.

**Sparse loading.** The loaded rule holds `elements` (a list, file order) only when the file
gives a non-empty list, and `strip_qualifiers` (value `True`) only when the file gives `true`;
`strip_qualifiers: false` loads as if absent. A rule using neither field loads exactly as today
(the nine keys of key-statement-witness requirement 2), so every existing rules file — the live
file, the r1, r2, r2.1 and key-definitions copies — loads equal to what it loads today, and
`StatementRule` gains the two keys as not-required.

### 2. Element match

A line whose concept is one of the rule's `elements` (exact string equality) is a candidate
**whatever its label**: `include`, `require` and `exclude` are not applied to it. Everything else
in `select_lines` applies to it unchanged: the statement codes and their preference order,
`parenthetical`, `section`, the `(tag, version)` de-duplication, the value lookup (a line without
a value for the period and unit is no candidate), `prefer` (on the full `plabel`) and `combine`.
An extension line (`ext:`) never matches by element. A line whose concept is not listed is
tested on its label as before (requirement 4).

### 3. Negating qualifiers (`label_head`)

`label_head(plabel)` returns the label without its negating qualifiers:

1. A **cue** is a case-insensitive match of
   `\b(?:excluding|exclusive of|not including|other than|except|net of)\b`.
2. Scanning left to right, each cue opens a **qualifier** that runs from the cue's first
   character up to, not including, the first `,`, `;`, `(` or `)` after the cue, or to the end
   of the label. The terminator is kept; the scan resumes at it. Every qualifier is removed.
3. Then, in order: every run of whitespace becomes one space; a space directly before `,`, `;`
   or `)` is removed; every `(` followed by optional whitespace and `)` is removed; whitespace
   runs become one space again; leading and trailing whitespace is stripped; then any trailing
   run of whitespace, `,`, `;` and `:` characters is removed.

Examples (C3 has the full table): "Purchases of property and equipment excluding finance leases,
net of sales" → "Purchases of property and equipment"; "Intangible assets, net (excluding
goodwill)" → "Intangible assets, net"; "Taxes other than income taxes" → "Taxes"; "Revenues,
net" → unchanged. `less` and `before` are not cues: "Less: accumulated depreciation" and
"Income before income taxes" name a different measure, not a qualified one.

### 4. Matching (`line_is_candidate`)

`line_is_candidate(rule, plabel, concept)` is the label-and-element test of selection:

- `True` when `concept` is in `rule.get("elements", [])`;
- otherwise, with `text` = `label_head(plabel)` when the rule has `strip_qualifiers: True`
  and `plabel` otherwise: `True` exactly when some `include`, every `require` and no `exclude`
  pattern matches `text` (case-insensitive `re.search`, as today).

`select_lines` uses it in place of its current include/require/exclude test; `section` and
`prefer` keep matching the full `plabel`. With neither new field, `line_is_candidate` is today's
label test and `select_lines`, `statement_witness`, `select_key`, `universe_status`,
`score_filing` and every witness outcome are unchanged.

### 5. No rule content changes here

The live `configs/eval/v2/statement_lines.yaml`, `statement_lines.accepted_r2_1.yaml`, the
acceptance records and every locked copy are not edited; no live family uses either field.
witness-rules-r2-1's invariant checks (`standard_label_violations`, `boundary_violations`,
`rule_identifier_violations`) are unchanged; they read `exclude` / `require` on the full label,
so for a rule that strips qualifiers they can over-report (never under-report) a rejection.

### 6. Rule content that uses the fields (not this change)

Each is its own spec change, and keeps the discipline of witness-rules-r2: patterns and element
lists come from accounting vocabulary and taxonomy definitions, never from one filer's wording,
ticker or accession; judging samples are read once, aggregates only; the corpus is measured once
at the end and what stays blocked gets a reviewed resolution.

- **Debt by element (TSM).** `Debt` is not a judged family (witness-rules-r2 requirement 2) and
  key-definitions owns its rule (its C7 holds the live `Debt` entry to the locked
  `statement_lines_kd.yaml`). An `elements` list for `Debt` is written from the definitions of
  the borrowing elements of us-gaap and ifrs-full as a whole (e.g. ifrs-full
  `CurrentPortionOfLongtermBorrowings`, `ShorttermBorrowings`; us-gaap `LongTermDebtCurrent`,
  `DebtCurrent`, `CommercialPaper`), never as TSM's one element. Acceptance follows
  key-definitions: hand-checked fixture lines (TSM FY2024 as in C2), a dry run over every corpus
  annual filing in the FSDS cache showing no asset, lease-only, deposit or equity line is
  selected, and the per-filer diff of witnessed Debt values reviewed by the maintainer; the
  key-definitions C7 pin moves to the new locked copy in that spec PR. Whether TSM Debt intents
  open is decided there.
- **Qualifier stripping for judged families** (Capex first; DebtIssued, ResearchAndDevelopment,
  IntangibleAssets and the cash-flow totals could then drop their per-pattern lookbehinds).
  This changes outcomes of judged families, so it is judged as r2.1 was: base the accepted r2.1
  copy, candidate the new rules, marks as `acceptance_r2_1` (precision and ambiguity absolute,
  no recall regression, no family regression), on a **fresh** draw pinned by SHA-256 in that spec
  before it is read; witness-rules-r2-1 C6's live-equals-accepted pin moves to the new accepted
  copy in that spec PR. Decision 7.

## Acceptance criteria

- **C1** (schema): a file with a legacy rule, an `elements` rule, a `strip_qualifiers: true`
  rule and a `strip_qualifiers: false` rule loads with exactly the keys of requirement 1 (the
  legacy and `false` rules equal key-statement-witness's nine-key shape); each of the ten
  malformed forms is refused while the good file loads; adding `strip_qualifiers: false` to any
  live family loads equal to it, and adding `elements` and `strip_qualifiers: true` adds exactly
  those keys. Oracle: requirement 1.
- **C2** (element match): a listed element is a candidate for any label (hypothesis text), an
  unlisted concept follows the label test, an `ext:` line never matches by element; on TSM's
  FY2024 20-F the live `Debt` rule plus `elements: [ifrs-full:CurrentPortionOfLongtermBorrowings]`
  selects lines 38, 42, 43: NT$59,857.9M + 926,604.5M + 31,824.4M = 1,018,286.8M (FY2023
  9,293.3 + 913,899.8 + 4,383.0 = 927,576.1; US$ 1,825.5 + 28,258.7 + 970.5 = 31,054.7), rel
  1e-12; relabelling line 38 (even to an excluded label) changes nothing, an extension version
  drops it, and statement, parenthetical and section filters still apply. Oracle: TSM's printed
  balance sheet, read by hand; requirement 2.
- **C3** (`label_head`): the 18 hand cases; a cue-free phrase is its own head; a qualifier
  appended in any of four forms (space, comma, semicolon, parentheses) leaves the head; a
  parenthesized qualifier ends at `)` and the text after it stays. Oracle: requirement 3 by hand.
- **C4** (qualifier stripping): TSLA's FY2025 capex line is rejected by the live `Capex` rule
  and, with `strip_qualifiers`, is the only line selected: 8,527M (2025), 11,342M (2024), 8,899M
  (2023), rel 1e-12; TSM's "Lease liabilities" relabelled "…, excluding bank loans" is a Debt
  candidate under the live rule and not with stripping (Debt stays 958,428.9M); for generated
  labels, an include or require term only inside a qualifier never makes a candidate, an exclude
  term only inside one never rejects, and the same terms in the head do. Oracle: TSLA's printed
  cash-flow statement, read by hand; requirement 4.
- **C5** (compatibility): no live family uses the new fields; for all 51 live families and all
  92 fixture lines, and for generated labels with cue words, `line_is_candidate` without the new
  fields equals the legacy label test computed in the test. Oracle: key-statement-witness
  requirement 2 step 1; requirement 5.

## Interface

Added to `src/eval/keys/statement_witness.py` (stubs in the spec PR):

- `label_head(plabel: str) -> str`
- `line_is_candidate(rule: StatementRule, plabel: str, concept: str) -> bool`

Changed in the implementation (behaviour extended, signatures unchanged):
`load_statement_rules` (requirement 1), `StatementRule` (two not-required keys), `select_lines`
(requirement 4). No other module changes; `src/eval/suites/v2_witness.py` and `v2_suite.py` are
not touched.

## Notes

### Fixtures (`test/spec/statement-rules-by-element/fixtures/`)

Byte-for-byte rows of SEC's Financial Statement Data Sets from the local cache
(`data/cache/sec_fsds/filtered`), cut on 2026-11-10 by a one-off script (not committed): the
`sub` row, every `pre` row of the named statement, the consolidated `num` rows (no segments, no
coreg) of those lines' `(tag, version)`, and their `tag` rows. `provenance.json` holds each
file's SHA-256, each filing's EDGAR URL and primary-document SHA-256 (our local EDGAR copy), and
each quarter zip's URL, SHA-256, size and retrieval time (`data/cache/sec_fsds/raw/manifest.json`).

| Filer | Accession | Quarter | Statement | Pins |
|---|---|---|---|---|
| TSM | 0001193125-25-083423 (20-F FY2024) | 2025q2 | BS (55 lines) | Debt by element; lease line relabelled |
| TSLA | 0001628280-26-003952 (10-K FY2025) | 2026q1 | CF (37 lines) | capex with "excluding finance leases, net of sales" |

### Hand checks

Read from the rendered primary documents (`data/storage/sec_edgar/TSM/2024/20-F/d896993d20f.htm`,
`.../TSLA/2025/10-K/tsla-20251231.htm`):

| Filing | Printed line | Printed (millions) |
|---|---|---|
| TSM FY2024 BS | "Long-term liabilities - current portion" (2023 / 2024 / US$ 2024) | NT$9,293.3 / 59,857.9 / US$1,825.5 |
| TSM FY2024 BS | "Bonds payable" | 913,899.8 / 926,604.5 / US$28,258.7 |
| TSM FY2024 BS | "Long-term bank loans" | 4,383.0 / 31,824.4 / US$970.5 |
| TSM FY2024 BS | "Lease liabilities" (non-current) | 28,681.8 / 28,755.3 / US$877.0 |
| TSLA FY2025 CF | "Purchases of property and equipment excluding finance leases, net of sales" (2025 / 2024 / 2023) | (8,527) / (11,342) / (8,899) |

### Split check

TSM has no Debt key in the frozen-rebuild dev or validation suites (`data/eval/v2_frozen_rebuild`);
TSLA's capex keys there are FY2022 only (dev; not pinned, and not in the FY2025 filing). No
held-out filer's data is used. Both filers are corpus filers.

### Evidence that motivated the change (not test data)

`data/eval/corpus_measurement_r2_1_kd/` (2026-11-09, r2.1 + key-definitions): TSLA FY2022 capex
`rejected_line` on "Purchases of property and equipment excluding finance leases, net of sales"
(7,158M), drafted as a `definition` resolution. key-definitions Review points: TSM current debt.

### Retired tests

None is changed: sparse loading (requirement 1) keeps every loaded rule of every existing file,
including key-statement-witness C2's nine-key `TotalAssets` shape, equal to today's. The five
witness specs' tests are run unchanged against the implementation.

### Decisions for the maintainer (recommendation first)

1. **An element match bypasses `include`/`require`/`exclude`.** Recommended: the element's
   definition is the stronger evidence, and label-independence is what makes it useful for a
   filer whose wording no general pattern may match. Alternative: element lines still subject to
   `exclude` (would keep a stray exclusion able to veto a definitional match).
2. **Standard taxonomies only** (`us-gaap`, `ifrs-full`, `srt`, `dei`); `ext:` refused, as for
   `substitutes`. An element that is also a stated substitute is refused.
3. **`strip_qualifiers` is an opt-in per family**, default off (CostOfRevenue rejects
   "excluding" on purpose; witness-rules-r2-1 §1.3). Alternative: on by default with an opt-out
   (would change every family's outcomes in this change; rejected).
4. **The cue set**: `excluding`, `exclusive of`, `not including`, `other than`, `except`,
   `net of`. `net of` is included (r2.1 §1.3 named it; "net of issuance costs" / "net of current
   portion" state presentation, not another measure). `less` and `before` are not cues.
5. **`prefer` and `section` keep the full label**; only include/require/exclude see the head.
6. **Sparse loading** (requirement 1), so no retired test changes. Alternative: fill both
   defaults on every rule and update key-statement-witness C2's `TotalAssets` dict in this PR.
7. **Fresh judging sample.** For `Debt` elements: **not needed** — `Debt` is not a judged
   family, and the dev/judge samples are US 10-Ks with no IFRS lines, so a judging read could not
   see the change; key-definitions' hand checks, corpus dry run and maintainer review of the
   element list (all from definitions) are the acceptance. For `strip_qualifiers` on any judged
   family: **needed** — dev, judge and judge2 are spent (judge2 by r2.1's one read), and the
   change moves judged outcomes. Recommended: draw judge3 from witness-rules-r2's population and
   seeded order (positions 360–479, disjoint by CIK from dev, judge and judge2; the size band
   holds 1,841 candidates), pin its hashes in that spec before it is read, and judge with
   `acceptance_r2_1`'s marks against the accepted r2.1 copy.
8. **TSM Debt intents** stay off until the Debt element content of requirement 6 is accepted.
9. **Invariant checks under stripping** (requirement 5): follow-up recommended in the qualifier
   content spec — evaluate `standard_label_violations` on `label_head` for rules that strip, so
   the check matches what the engine applies.
