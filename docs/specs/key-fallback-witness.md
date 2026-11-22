# key-fallback-witness — witness a key from the filer's own annual report when its filing has no FSDS data

Follows [key-statement-witness](key-statement-witness.md) (retired), requirements 1 and 5, and
[reviewed-resolutions](reviewed-resolutions.md) (retired). Decided by the maintainer on
2026-11-10 (decisions 1-3 of the K1 key-universe analysis, all accepted as recommended).

**The defect.** For an annual key the witness reads the **key's own accession**
(`statement_witness` step 1: `adsh = key["accession"] if annual else annual_adsh`). Under S1
that accession is the *latest* filing that reports the value. When that filing is newer than
the FSDS data (SEC publishes a quarter's Financial Statement Data Sets after the quarter ends),
`submission(...)` is None, the status is `no_fsds`, and the key is **held**: the member is
excluded from every screen and its single-filer intents are dropped. The filer's own annual
report for that fiscal year, which presents the same value, is in the FSDS data and is never
read. A second gap: a screen member whose own statement presents the family's line on an
**extension** element is excluded as `presented_line_not_keyed`, even when a later annual
report presents the same period on a standard element that companyfacts carries.

Both defects are properties of data availability and of one filer's tagging choice, not of the
member: the member is knowable, the key can say so, and today a correct answer that ranks it is
graded wrong (S3: the key's universe is short one member). Nothing here loosens grading.

**What this change does.**

1. **Own-annual-report fallback.** When the FSDS data has no submission for an annual key's own
   accession, the witness reads the filer's own annual report for the key's fiscal year
   (`annual_adsh`, already passed), provided its companyfacts value equals the key's value
   under the existing value-equality rule (the one the interim-report path already applies).
   A fallback the witness blocks stays **held** unless a reviewed resolution covers it.
2. **Extension-presented lines.** A screen member whose own report presents the family's line
   on one extension element is keyed from a **later** annual report that presents the same
   period on one standard element (us-gaap or ifrs-full), when the values are the same. The
   statement-line rules still decide what the line is; a line they reject stays rejected.
3. **FSDS not published.** A key or member for which no FSDS filing can be read at all is
   excluded with the distinct, disclosed reason `fsds_not_published`, counted in the manifest,
   never silently dropped and never failing the build. The manifest also reports the FSDS
   coverage of the build (first and last quarter present, missing quarters).

Out of scope (a later spec): grading members excluded by a reviewed `drop` resolution as
"don't care", and the grader fixes of the K1 analysis (an aggregate matched on a listed row
value; ties at the top-N cut).

## Requirement

Terms are those of key-statement-witness: a **key** (`AnswerKey`), **annual forms** (`10-K`,
`10-K/A`, `20-F`, `20-F/A`, `40-F`, `40-F/A`), two amounts **the same** (`abs(a - b) <= 1e-9 *
max(abs(a), abs(b), 1)`), a line's **concept**, the `Witness` record, the accepted, held and
blocking statuses, `resolve`, `select_key`, `universe_status`. A **standard element** is a
concept whose taxonomy is `us-gaap` or `ifrs-full`. FSDS reading goes through
`src/eval/sec_reference/fsds.py` as today.

### 1. The own-annual-report fallback (`statement_witness`)

`statement_witness(tables, rules, notes, family, key, annual_adsh, annual_value=None)` keeps
its signature. Steps 1-3 of key-statement-witness requirement 1 become:

1. **Filing read.**
   - A key whose `form` is not an annual form: `adsh` is `annual_adsh` (unchanged).
   - An annual key whose own accession has a submission (`submission(tables,
     key["accession"])` is not None): `adsh` is the key's accession (unchanged).
   - An annual key whose own accession has no submission, when `annual_adsh` differs from the
     key's accession and `submission(tables, annual_adsh)` is not None: `adsh` is
     `annual_adsh`. This is the **own-report fallback**; the record carries one more field,
     `"fallback": "own_annual_report"`. The field is present only on a fallback read, so every
     other record keeps exactly the thirteen fields of key-statement-witness.
   - Otherwise `adsh` is the key's accession.
2. **No data.** When `submission(tables, adsh)` is None: status `no_fsds`, `lines` empty,
   `value` None, no `fallback` field (unchanged).
3. **Recast.** For a key that is not annual (unchanged) **and for a fallback read**: when
   `annual_value` is not None and is not the same as the key's value, the status is
   `value_recast` (held). `lines` and `value` are still those of step 4. This is the existing
   interim-report condition, applied unchanged: `annual_value` None does not recast, and the
   statement lines then decide (step 5).

Steps 4-7 are unchanged and run on the filing read. The key itself is never changed: `key_value`,
`key_concept` and `key_accession` are the key's (S1 is unchanged; the latest filing still
supplies the value, and the own report only witnesses it).

### 2. A blocked fallback stays held

A fallback read whose status is blocking (`disagree`, `ambiguous`, `rejected_line`,
`note_unlisted`) is resolved by `resolve` exactly as today (same identity and value).

- **Resolved** with reading `key`: accepted, as today; reading `drop`: held as
  `resolved_drop`, as today.
- **Unresolved**: **held**, never blocking. `select_key` treats it like the held statuses
  (only when no candidate is accepted; among held candidates today's order holds:
  `value_recast`, then `no_fsds`, then any other held candidate, each by `key_concept`). `KeyWitness.fy_key` drops its input with
  reason `fallback_blocked` and records no block (`blocked` and `raise_if_blocked` never name
  it); `universe_status` with such a key returns `held` with reason `fallback_blocked`.
- `witness_manifest` does not raise on an unresolved blocked fallback; it counts it under its
  witness status in `by_status`, lists it in `held` (with its witness status), and returns one
  more field, `"fallback": {"n": <records with the fallback field>, "held": <of them held:
  status value_recast, a resolved drop, or an unresolved blocking status>}`. Every other
  field is unchanged; an unresolved blocking witness **without** the fallback field still
  raises `WitnessBlocked` (unchanged).
- A reviewed resolution's statement evidence for a fallback names the own annual report (the
  filing the witness read, `check_resolution_use` unchanged).

When one candidate's fallback disagrees and another candidate's fallback agrees, `select_key`
returns the agreeing candidate (requirement 3 of key-statement-witness, unchanged): a held
concept that was merely the alphabetically first of several held candidates no longer decides.

### 3. A line presented on an extension element (`later_annual_reports`, `later_standard_line`)

`later_annual_reports(cf, labels, year) -> list[str]`: for every fiscal-year label `L > year`
among the values of `labels`, the accession `KeyWitness.annual_accession(cf, labels, L)` returns
(the original annual report labelled `L`: among the accessions labelled `L` that appear on an
annual-form entry of `cf`, the least by (is an amendment, earliest `filed` of its entries,
accession)), ordered by `L` descending (the latest report first). A label no annual-form entry
carries contributes nothing.

`later_standard_line(tables, rule, adshs, period_start, period_end, unit, own_value)
-> dict | None`: for each accession of `adshs`, in the order given, `lines = select_lines(tables,
rule, adsh, period_start, period_end, unit)`. A filing whose lines are not exactly one line on a
standard element (no submission, no line, several lines, or one line on an extension or another
taxonomy) is passed over. The **first** filing whose lines are exactly one line on a standard
element decides: the result is `{"adsh": adsh, "concept": line concept, "value": line value}`
when that value is the same as `own_value`, else None. None when no filing decides. A
`ValueError` from `period_value` propagates.

`KeyWitness.unkeyed_member(cf, labels, cik, family, year, unit)` keeps its signature. When the
own report's `universe_status` (no key) is `ranked` with exactly one selected line and that
line's concept is an extension (`ext:`), instead of returning `presented_line_not_keyed` at
once:

1. `later = later_annual_reports(cf, labels, year)`; `hit = later_standard_line(<the rows of
   later>, rules[family], later, start, end, unit, <the own line's value>)`, with the own read's
   period (`start`, `end` as `unkeyed_member` computes them today).
2. No hit: `presented_line_not_keyed`.
3. The key: the companyfacts period of the hit's concept ends at `own_period_end(cf, own
   report)`; for a duration family (the rule's first statement is not `BS`) its start is the
   one start of the concept's annual-length entries ending there (none or several: not keyed),
   for a balance None. The key is `latest_filed_key(cf, hit concept, unit, start, end)` (S1)
   with `fiscal_year` = `year` and `policies` `["S1", "S2", "S6"]`. A key error
   (`KeyNotFoundError`, `UnitMismatchError`, `KeyAmbiguousError`), a nil value, or a value not
   the same as the own line's value: `presented_line_not_keyed`.
4. The key is admitted by `self.member(family, own report, key, None)` (the witness reads the
   key's own annual accession, so its record is kept like any other). `ranked`: `(None, key)`;
   otherwise `(<its reason>, None)`.

Every other path of `unkeyed_member` is unchanged: several presented lines, a line the rule
rejects (the statement-line rules decide what the family's line is, so a definitional mismatch
stays excluded), a standard own line (keyed through `fy_key` as today).

### 4. FSDS not published (`fsds_not_published`)

A key input or member for which the witness can read no FSDS filing is excluded with the
reason **`fsds_not_published`**:

- `KeyWitness.fy_key` (and so every intent and screen member keyed through it): the chosen
  witness is `no_fsds` (neither the key's accession nor, for an annual key, its own annual
  report has a submission) → `WitnessDrop("fsds_not_published")`; a Debt or DeferredRevenue
  defined key with status `no_fsds` → the same.
- `KeyWitness.unkeyed_member`: the own annual report has no submission →
  `("fsds_not_published", None)`.
- `KeyWitness.member`: a defined key whose `member_status` reason is `no_fsds` → reason
  `fsds_not_published`.

The internal statuses are unchanged: `statement_witness`, `universe_status` and
`key_definitions` still say `no_fsds`, and `key_independence` counts and lists the witness as
`no_fsds` (held). Only the reason the build emits (intent drop reasons, `excluded` entries of a
screen's disclosures) is `fsds_not_published`. It is never a block: `blocked` and
`raise_if_blocked` never name it, and the build never fails because of it.

Each such exclusion is recorded once as a **gap** `{"family", "cik" (10 digits), "fiscal_year"
(int), "accession"}`: the accession is the filing the witness would read (the witness's
`adsh` for a keyed input; the own annual report for an unkeyed member or a defined key).

### 5. FSDS coverage in the manifest (`fsds_coverage`, `fsds_gap_manifest`)

`fsds_coverage(quarters) -> dict`: `quarters` are FSDS quarter names `<yyyy>q<n>` (`n` in
1-4), in any order. Returns `{"n": <number of quarters>, "first": <earliest> | None, "last":
<latest> | None, "missing": [<each quarter strictly between first and last that is not given,
ascending>]}`. Raises `ValueError` on a name of another form or a repeated name. No quarters:
`{"n": 0, "first": None, "last": None, "missing": []}`.

`fsds_gap_manifest(coverage, gaps) -> dict`: returns `{"coverage": coverage, "not_published":
{"n": <distinct gaps>, "by_family": {family: <distinct gaps of it>, keys sorted}, "keys":
[<distinct gaps sorted by (family, cik, fiscal_year, accession)>]}}`. A gap with a missing or
extra field raises `ValueError`. Repeated gaps count once.

`KeyWitness.fsds_manifest() -> dict` is `fsds_gap_manifest(fsds_coverage(<quarters>), <the gaps
recorded so far>)`, where the quarters are the directory names `KeyWitness.from_paths` was given
(none for a witness built by the constructor). A directory whose name is not a quarter makes
`fsds_manifest` raise `ValueError` (from `fsds_coverage`).

`build_v2_suites` with a witness stores `manifest["fsds"] = witness.fsds_manifest()` beside
`key_independence`, `key_definitions` and `key_resolutions`; without a witness the manifest has
no `fsds` field (unchanged). The compound and held-out builds, which assemble the same witness
blocks, store it the same way.

### 6. Nothing else moves

- A key whose own accession has a submission is witnessed exactly as today (same record, field
  for field), whatever other filings the tables hold.
- The nine statuses, `ACCEPTED`, `HELD`, `BLOCKING` and their order are unchanged; the
  statement-line rules, note list and resolutions files are not changed by this spec.
- Without a witness the build is unchanged.

## Acceptance criteria

Fixtures (all synthetic, built in `test/spec/key-fallback-witness/conftest.py`; made-up CIKs
`0000810001`-`0000810005`, December fiscal years, USD in millions written out in full):
companyfacts documents in the SEC API's shape and FSDS-shaped `sub`/`pre`/`num`/`tag` rows,
written by the conftest into quarter directories `2023q1`, `2024q1`, `2025q1` under
`tmp_path` for `KeyWitness.from_paths`; a minimal statement-line rules file, an empty note list
and two resolution files (`fixtures/`). Filers:

- **FALL** (`0000810001`): FY2024 10-K `0000810001-25-000010` (in 2025q1) and FY2025 10-K
  `0000810001-26-000010` (not in the FSDS data). The FY2025 10-K re-reports FY2024 financing
  cash flow −10,764 (own report −10,764), D&A 1,000 on `DepreciationDepletionAndAmortization`
  and 800 on `Depreciation` (the own report presents the 1,000 line), interest income and other
  169 (the own report presents "Interest income and other, net", which the rule excludes), and
  restates FY2024 capex 500 → 520. FY2025 financing −9,000 is in the FY2025 10-K only.
- **FEXT** (`0000810002`): 10-Ks for FY2022-2024; FY2022 and FY2023 present capex on an
  extension element (976, 1,833); the FY2024 10-K presents FY2022-2024 on
  `us-gaap:PaymentsToAcquireProductiveAssets` (976, 1,833, 1,069), which companyfacts carries.
- **FRST** (`0000810003`): as FEXT, but the FY2024 10-K presents FY2022 as 990 (restated) and
  FY2023 as 1,833 (same).
- **FDEF** (`0000810004`): as FEXT, but the FY2024 10-K's standard line is "Purchases of
  property and equipment and businesses", which the rule excludes.
- **FNOP** (`0000810005`): one FY2025 10-K `0000810005-26-000050`, not in the FSDS data.

Values compare exactly or with `pytest.approx(rel=1e-12)` (the fixture's integer amounts; the
tolerance absorbs only binary floating point).

- **C1**: own-report fallback. An annual FALL FY2024 financing key from the FY2025 10-K
  (absent) is witnessed on the FY2024 10-K: status `agree`, `adsh` the FY2024 10-K, `lines` its
  "Net cash used in financing activities" line (report 4, line 3, −10,764), `key_accession`
  the FY2025 10-K, `fallback` `own_annual_report`; with `annual_value` 500 against a 520 key
  (capex) the status is `value_recast`; a key whose accession has a submission is read on that
  accession with no `fallback` field; when both filings are absent the record is `no_fsds` on
  the key's accession with no `fallback` field. Property: for any key value, own-report line
  value, `annual_value` (None, the same, or different), form (10-K or 10-Q) and presence of the
  key's filing, the filing read, the status and the `fallback` field follow requirement 1's
  decision table. Oracle: requirement 1 and the fixture rows.
- **C2**: a blocked fallback stays held. FALL FY2024 interest-and-other 169 is `rejected_line`
  on the fallback ("Interest income and other, net"); `KeyWitness.fy_key` drops it as
  `fallback_blocked` and `blocked` stays empty; with a resolution of reading `drop` it drops as
  `resolved_drop`, with reading `key` it is keyed (169, from the FY2025 10-K); `witness_manifest`
  of an unresolved blocked fallback does not raise, counts it under `rejected_line`, lists it in
  `held`, and reports `fallback` `{"n": 1, "held": 1}`, while the same witness without the
  `fallback` field still raises; `select_key` over the D&A candidates `Depreciation` (800,
  disagrees on the fallback) and `DepreciationDepletionAndAmortization` (1,000, agrees) returns
  the latter. Property: for any multiset of the hand-labelled fallback witnesses, the
  manifest's `fallback` counts are the hand counts. Oracle: requirement 2 and the fixture rows.
- **C3**: `later_standard_line`. Over FEXT's FY2024 and FY2023 10-Ks (in either order) for
  FY2022 and own value 976 it returns the FY2024 10-K, `us-gaap:PaymentsToAcquireProductiveAssets`,
  976; own value 975 gives None; FRST FY2022 (990 against 976) gives None and FRST FY2023 the
  FY2024 10-K with 1,833; FDEF gives None; an accession absent from the tables is passed over.
  Property: for any sequence of synthetic filings each absent, without a line, with one
  extension line, one srt line, two lines, or one us-gaap or ifrs-full line, the result is
  decided by the first filing with one standard line (its value against the own value), else
  None. Oracle: requirement 3, computed in the test from the generated filings.
- **C4**: `later_annual_reports`. For FEXT's three 10-Ks: FY2022 → [FY2024 10-K, FY2023 10-K],
  FY2023 → [FY2024 10-K], FY2024 → []; a later 10-K/A labelled FY2023 does not replace the
  original FY2023 10-K. Property: for any set of labelled originals with optional amendments
  filed later, the result is the originals of the labels above the year, latest label first.
  Oracle: requirement 3, the hand-written labels.
- **C5**: an extension-presented member is keyed from the later standard line.
  `unkeyed_member` for FEXT capex FY2022 returns `(None, key)` with concept
  `us-gaap:PaymentsToAcquireProductiveAssets`, value 976, accession the FY2024 10-K, period
  2022-01-01..2022-12-31, fiscal year 2022, and the witness keeps one `agree` record on the
  FY2024 10-K; FY2023 gives 1,833. FRST FY2022 (restated later) and FDEF FY2022 (the rule
  rejects the later line) stay `presented_line_not_keyed`; FRST FY2023 is keyed (1,833).
  Metamorphic: without the 2025q1 quarter (no later standard presentation) FEXT FY2022 and
  FY2023 are `presented_line_not_keyed`. Oracle: requirement 3 and the fixture rows.
- **C6**: FSDS not published. `fy_key` for FALL FY2025 financing and FNOP FY2025 capex raises
  `WitnessDrop("fsds_not_published")`, `blocked` stays empty, and `key_independence` lists the
  witness as `no_fsds`; `unkeyed_member` for FNOP FY2025 capex returns
  `("fsds_not_published", None)`; FALL FY2024 financing is keyed through the fallback (−10,764
  from the FY2025 10-K). A build of FALL FY2025 and FNOP FY2025 lookups never raises and
  records both drops as `fsds_not_published`. Metamorphic: adding a quarter that holds the
  FY2025 10-K (FALL) makes FALL FY2025 financing keyed (−9,000) and removes its gap. Oracle:
  requirement 4 and the fixture rows.
- **C7**: `fsds_coverage`. `["2023q1", "2024q1", "2025q1"]` → `n` 3, first `2023q1`, last
  `2025q1`, missing the six quarters 2023q2-2023q4 and 2024q2-2024q4; empty → the empty
  record; a bad name or a repeat raises. Property: for any set of quarters, the result does not
  depend on their order, and `n` plus the missing count is the number of quarters from first to
  last. Oracle: requirement 5, counted in the test.
- **C8**: the manifest records the gaps and the coverage. `fsds_gap_manifest` deduplicates,
  counts and sorts hand-written gaps and refuses a malformed one; `KeyWitness.fsds_manifest`
  after the C6 drops reports both gaps (FALL `FinancingCashFlow` FY2025 on
  `0000810001-26-000010`, FNOP `Capex` FY2025 on `0000810005-26-000050`) and the fixture's
  coverage; the build's `manifest["fsds"]` equals it, `intents.dropped` holds
  `fsds_not_published: 2`, and a build without a witness has no `fsds` field. Property: for
  any subset of the two not-published lookups in the inventory, the drop count and the gap
  count are the subset's size. Oracle: requirements 4-5, counted in the test.

## Interface

New stubs (this spec PR):

- `src.eval.keys.statement_witness.later_standard_line(tables: FsdsTables, rule: StatementRule,
  adshs: Sequence[str], period_start: str | None, period_end: str, unit: str, own_value: float)
  -> dict[str, Any] | None`
- `src.eval.suites.v2_witness.later_annual_reports(cf: dict, labels: Mapping[str, int], year:
  int) -> list[str]`
- `src.eval.suites.v2_witness.fsds_coverage(quarters: Sequence[str]) -> dict[str, Any]`
- `src.eval.suites.v2_witness.fsds_gap_manifest(coverage: Mapping[str, Any], gaps:
  Iterable[Mapping[str, Any]]) -> dict[str, Any]`
- `src.eval.suites.v2_witness.KeyWitness.fsds_manifest(self) -> dict[str, Any]`

Changes the implementer makes to existing code (not stubbable; signatures unchanged unless
noted):

- `statement_witness`: requirement 1 (filing read, `fallback` field, recast on a fallback).
  `Witness` gains `fallback: NotRequired[str]`.
- `select_key`, `universe_status`, `witness_manifest` and the private `_held` / `_accepted`:
  requirement 2 (an unresolved blocked fallback is held; the `fallback` counts).
- `KeyWitness.from_paths` keeps the quarter directory names; `KeyWitness.__init__` may take
  them as a new keyword argument defaulting to none (the spec tests do not call it).
- `KeyWitness.fy_key` / `_choose` / `_choose_defined`, `unkeyed_member`, `member`:
  requirements 2-4 (`fallback_blocked`, the extension path, `fsds_not_published`, gap records).
- `src/eval/suites/v2_suite.py` `_assemble` (and the compound and held-out assemblies):
  `manifest["fsds"]` with a witness (requirement 5).
- `scripts/eval/build_v2_suite.py`: print the `fsds` coverage and the not-published count (a
  count, so the held-out summary stays counts-only); no new argument.
- Docs: `docs/design/XbrlRag_Eval_Semantics.md` S3 gains the two exclusion reasons
  (`fsds_not_published`, `fallback_blocked`); guide 11 notes the coverage line.

## Notes

### Oracle sources

Every expected value is read off the fixture's own rows (written by hand in the conftest, with
the table above in its docstring) or computed in the test from requirement text: the filing a
witness reads, the decision tables of requirements 1-3, quarter arithmetic. No value comes from
running the code, the `data/` directory, a database or the network.

### Evidence that motivated the change (dev and validation only; not test data)

From the K1 analysis (`data/eval/k1/k1_key_universe_2026-11-10.md`, local, read-only work on the
frozen dev and validation suites, the FSDS cache 2022q1-2026q3 and SEC companyfacts; no
held-out set, no LLM call):

- The manifest's 39 `no_fsds` keys are two filers whose FY2026 10-Ks (filed 2026-10) fall in
  FSDS 2026q4, which SEC had not published. Their own FY2024 and FY2025 annual reports are in
  the FSDS cache. Re-witnessed on the own report with the current rules: 30 `agree` with the
  identical value, 3 disagree only on the held concept (the presented line is another candidate,
  accepted once witnessing is possible: requirement 2's last paragraph), 1 genuine
  `rejected_line` (it stays held under requirement 2), and 5 FY2026 keys (compound suites only)
  whose own report is the unpublished filing (`fsds_not_published`).
- `presented_line_not_keyed`: one filer's FY2022/FY2023 capex on an extension element, presented
  on `us-gaap:PaymentsToAcquireProductiveAssets` by the later 10-Ks with the same values
  (requirement 3 keys them); one filer's debt-proceeds line (includes short-term debt over three
  months, no standard element in any filing: stays excluded); one IFRS filer reporting in TWD
  (stays excluded: its key fails in USD; the reason label stays `presented_line_not_keyed`,
  which the grade does not depend on).
- Simulated effect (re-keying the frozen cases in memory, re-grading every stored answer): dev
  **51 cases gain a keyed member** (28 change the expected answer, 23 disclosures only),
  validation **24** (17, 7), dev_sample 12 (8, 4), and most of the 65 dev and 10 validation
  compound cases with such a member. The change is **arm-neutral**: it helps the frontier
  arm about as much as XbrlRag (for example F3a dev +13, F7a dev +15, F3b validation +14, F7b
  validation +11 recovered cases), so the paired comparison is not tilted.

### Re-freeze (K6) and the pre-registration amendment

- Keys change, so the frozen suites are **rebuilt**, not edited: dev and validation with their
  compound suites, from the frozen arguments, then a suite diff, new content hashes, a paraphrase
  pass for added or rephrased cases, and a `$0` re-grade of the stored runs
  (`key_change_regrade` / `scripts/eval/regrade_run.py`). The seeded draw can change too (8
  single-filer candidates dropped and 39 keys held become keyable), so the diff, not this spec,
  states the case movement. Questions do not change, so stored answers re-grade.
- The amendment (K6, after K1-K5) must say that the issue **surfaced in the D3 dev failure
  analysis**, that the rule is general (no filer, value or system result chose it; it is a
  property of FSDS publication dates and of extension tagging), and that it was simulated as
  arm-neutral before the rebuild.
- **H-F is built after this lands.** H-F is generated by the same builder, so the fallback and
  the `fsds_not_published` reason apply to it, and any drawn filer whose latest 10-K postdates
  its FSDS cut has the same defect there. `xbrl_heldout` gets the same behaviour through the
  build; nothing about the held-out database changes. The H-F manifest's `fsds` block (with
  CIKs in `keys`) is written into the held-out tree only, like `key_independence`; the held-out
  summary prints its counts only (hf-suite-build requirement 9).
- **No database rollout.** The change is in the eval key builder only (FSDS and companyfacts
  reading); no DDL, materialized view, fact-store or product change, and no `xbrl_db` write.

### Retired and active tests

- Retired witness spec tests (key-statement-witness, witness-rules-r2, witness-rules-r2-1,
  key-definitions, reviewed-resolutions, statement-rules-by-element, v2-suite-build,
  compound-shape) and `test/unit/test_v2_witness_wiring.py` were run on 2026-11-10 against a
  throwaway prototype of this requirement (outside the repository, discarded): 571 passed, 0
  failed; the 35 tests of this spec passed on it, also under the `tdd-verify` profile. The
  whole `test/spec` tree on the prototype failed only in the active locks (compound-text-part,
  hf-suite-build, hx-peer-rank: their stubs) and in two files that pass when run on their own
  (xdist isolation). None pins the bytes of the frozen suites (no test reads `data/`), so the
  intended re-freeze breaks no test. Their fixtures never read a fallback (every annual key's
  accession has a submission, or none of the filings has one), so every record they compare
  field for field is unchanged; `witness_manifest` gains a field, which no retired test compares
  as a whole dict.
- compound-shape C8 pins the manifest's field set of a build **without** a witness; requirement
  5 adds `fsds` only with a witness, so it holds.
- hf-suite-build (active) builds with the compound-shape witness fixture, whose filings all have
  submissions; its manifest checks are per field. Its implementation should carry `fsds` into
  the held-out manifest and print its counts in `heldout_summary` (counts only); no locked test
  of it needs to change.
- `test/unit/test_v2_witness_wiring.py` may need its expectations updated by the implementer
  where it asserts a `no_fsds` drop reason (none today).

### Decisions (recommendation first; the maintainer decided 1-3 of the K1 analysis)

1. **A blocked fallback is held, not a build block** (the maintainer's "stays held unless a
   reviewed resolution covers it"), with the reason `fallback_blocked`. Alternative: block the
   build for review like any other blocked key; rejected because it turns a data-availability
   artifact into review load and would block H-F, which no one may review.
2. **The value-equality guard is the interim-report condition reused unchanged** (requirement 1
   step 3): `annual_value` None does not recast, and the own report's lines still must equal the
   key's value to be accepted. Alternative: require `annual_value` present and equal; rejected
   as a second, stricter definition of the same check.
3. **The extension path keys only one standard line** of the first later filing that presents
   one (latest first), with the value guard against the own extension line. Several lines, srt
   or dei elements, or a key error stay `presented_line_not_keyed`.
4. **`no_fsds` stays the internal status**; only the emitted reason is renamed
   `fsds_not_published`, at the `KeyWitness` boundary. The retired tests pin `no_fsds` on
   `statement_witness`, `universe_status` and `member_status`, and the status is still exact
   there.
5. **Coverage comes from the quarter directory names** the build was given, the cache's actual
   coverage; a gap list of missing quarters between first and last is reported, not refused.
