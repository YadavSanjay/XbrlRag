# key-currency-and-definition — screen keys rank only USD reporters, a member's element must fit its family, and defined keys follow S1

Follows [key-statement-witness](key-statement-witness.md), [key-definitions](key-definitions.md)
and [reviewed-resolutions](reviewed-resolutions.md) (all retired), and composes with
[key-fallback-witness](key-fallback-witness.md) (active; see Notes). Decided by the maintainer on
2026-11-11: decisions K-R1, K-R2, D3 and D4 of the K2b analysis
(`data/eval/k2/k2b_debt_proceeds_2026-11-11.md`, local), all accepted; D5 (S-R1, a system-side
wording change) is out of scope. The S1 column for the defined keys (requirement 6) was added
to this change by the maintainer the same day.

**The defects.**

1. **Currency (K-R1).** `V2Suite._cross_filer` checks the reporting currency only on the keyed
   branch (the inventory's `unit` is not USD). A member the inventory has no key for goes
   through `KeyWitness.unkeyed_member(..., unit="USD")`, which reads the FSDS statement line
   and companyfacts **in USD**. For a 20-F that is the convenience translation of the latest
   year. TSMC's FY2023 "Proceeds from issuance of bonds" (TWD 85,700,000,000) was ranked on its
   USD 2,798,800,000 translation, and the same hole lets TSMC into the defined-family
   (`s4:debt_total`) Debt screens. S6 says a cross-filer amount screen ranks in USD and a filer
   that does not report the amount in USD is excluded and named. The rule is general: TSMC is
   only the example that exposed it; no filer, value or system result chose it.
2. **Element definition (K-R2).** A line chosen by its presented label is keyed as the family
   even when its element's standard definition is outside the family's measure: AMD's "Proceeds
   from debt, net of issuance costs" is tagged `us-gaap:ProceedsFromShortTermDebt`. The
   statement-line rules test the label only. This is the precedent of reviewed resolution #23
   (VST: "tagged ProceedsFromShortTermDebt and are not long-term debt").
3. **The DebtIssued measure (D3).** The questions and lexicon say "long-term debt", but the
   family's primaries include `ProceedsFromIssuanceOfDebt` (its us-gaap definition includes
   short- and long-term debt) and `ProceedsFromDebtNetOfIssuanceCosts` (any term). The family
   is renamed "debt issued" (any term), keeping every primary; only the wording moves.
4. **The mapping's stated substitutes (D4).** `universe_status` applies only the statement-line
   rules' own `substitutes`, never the concept mapping's (`concept_family_coverage.yaml`
   `substitutes:`). So decision D21 (TSMC's bonds line "answers only as a stated substitute")
   is bypassed: a member presenting only the IFRS bonds line is ranked. This is latent today
   (it would matter only for an IFRS filer reporting in USD; the corpus has none), and it is
   fixed here because it sits on the same member paths.
5. **Defined keys skip S1 (added by the maintainer, 2026-11-11).** A Debt or DeferredRevenue
   key reads the year's original annual report, while every other family's key is the latest
   filing's figure (S1). Found by the s4-lookup-deferred-revenue-debt implementer's $0 check
   (Notes). The defined keys now read the latest annual report that presents the year's
   balance-sheet date (requirement 6). General: no filer or value chose it.

## Requirement

Terms are those of key-statement-witness and key-definitions: an `AnswerKey`, `KeyWitness`,
`universe_status`, `UniverseStatus`, `StatementRule`, a **standard element** (taxonomy
`us-gaap`, `ifrs-full`, `srt` or `dei`), the **labels** of a companyfacts document
(`annual_filing_labels`), the **screen unit** `USD` (`_SCREEN_UNIT`), and a **cross-filer
screen** (`screen.top`, `screen.bottom`, `screen.sum`, `screen.median`, `peer_relative`, and
the rankings of the compound shapes, which are keyed as `screen.top`).

### 1. The reporting currency of a filer-year (`reporting_currency`)

`src.eval.keys.core.reporting_currency(company_facts, labels, fiscal_year) -> str | None`:

1. **Observations**: the set of `(unit, end)` pairs of every fact entry in `company_facts`, on
   any concept and taxonomy, whose unit is a **currency code** (exactly three upper-case
   letters, so `USD/shares`, `shares` and `pure` are not) and whose `accn` the labels map to
   `fiscal_year` (the annual reports labelled that year, amendments included).
2. No observation: None.
3. **Rule** (the system's reporting-currency rule, `choose_lookup_unit` with no requested unit,
   screen-reporting-currency requirement 1): `latest` is the greatest `end`; when exactly one
   currency is observed at `latest`, it is the result; otherwise, among the currencies
   observed at `latest`, the one observed at the most distinct ends; a tie is None.

The observations are the whole filing's, not one concept's. Reason (Notes, "Why
filing-level"): one concept can tie (both currencies at the latest end only), and TSMC's
FY2022 `RepaymentsOfNoncurrentBorrowings` and FY2023
`AdjustmentsForGainLossOnDisposalOfInvestmentsInSubsidiariesJointVenturesAndAssociates` do,
while every filing of the corpus decides.

### 2. Every member path of a screen requires a USD reporting currency (K-R1, `_cross_filer`)

The paths decide each member exactly as today (keyed, statement-line, defined family, and any
path key-fallback-witness adds). The new check gates **ranking**: whenever a path would rank a
member (it returned a key with a value), then

- `c = reporting_currency(document, annual_filing_labels(document), year)`, with the member's
  companyfacts document as the paths read it;
- when `c` is not None and is not `USD`, the member is not ranked; it is excluded with exactly
  `{"ticker": <ticker>, "reason": "currency"}` (today's shape of a `currency` exclusion);
- otherwise (USD, or None) it is ranked as today.

A member no path ranks keeps its path's reason (`metric_not_tagged`, `no_key`,
`presented_line_not_keyed`, `substitute:<code>`, `fsds_not_published`, …), and the existing
keyed-branch check (the inventory's unit is not USD → `currency`) still runs first. Why not
name `currency` ahead of every reason (S-R1's order): the retired v2-suite-build C6 pins a
non-USD filer with no key as `no_key`, and the grader checks only that an excluded ticker is
named, not why; the order of reasons is the system-side D5 item. Ranking, ties, aggregates,
period-end disclosure and the `too_few_ranked` and `subject_not_ranked` drops are unchanged;
a peer-relative subject excluded for currency is not ranked, so its intent drops as
`subject_not_ranked`.

### 3. The mapping's stated substitutes reach the witness (D4)

- `src.eval.keys.statement_witness.load_mapping_substitutes(path) -> dict`: reads the
  `substitutes` and `substitutions` sections of the coverage file (other sections are not
  read). Returns family → concept → `{"code", "note"}`: an entry that is a code string takes
  the note `substitutions[code]`; an entry `{code, note}` keeps its own note. Raises
  `ValueError` on a code with no note in `substitutions`, an entry that is neither a non-empty
  string nor a mapping with exactly non-empty string `code` and `note`, or a concept that is not
  `<prefix>:<Name>`.
- `merge_mapping_substitutes(rules, substitutes) -> dict[str, StatementRule]`: a new mapping
  with every family of `rules`; a family's `substitutes` become the mapping's entries for that
  family updated by the rule's own (the rule's entry wins for a concept both name); every other
  field is the rule's; a mapping family without a rule is ignored; the inputs are not changed.
  Raises `ValueError` when a mapping substitute is listed in its family's rule `elements`
  (a contradiction between the two committed files). Merging twice equals merging once.
- `KeyWitness.from_paths` gains a keyword `mapping` defaulting to
  `configs/concept_mapping/concept_family_coverage.yaml` and builds the witness with
  `merge_mapping_substitutes(load_statement_rules(rules), load_mapping_substitutes(mapping))`.
  The constructor is unchanged (it takes rules as given). Every build (main, compound, H-F)
  builds its witness through `from_paths`, so every build applies the mapping.

With the merge, the existing witness logic does the rest: `statement_witness` records the
substitution for a key on a substitute concept, and `universe_status` excludes a member as
`substitute:<code>`, keyed or unkeyed. A member presenting only the IFRS bonds line
(`ifrs-full:ProceedsFromIssueOfBondsNotesAndDebentures`) of a DebtIssued screen is excluded as
`substitute:one_of_several_lines` (D21).

**Not done here:** adding "bank loans" to the DebtIssued `include` patterns (the second half
of the K2b D4 item). The live statement-line rules are pinned to the accepted r2.1 copy by the
retired witness-rules-r2-1 C6 (every family other than Debt and DeferredRevenue loads equal to
`statement_lines.accepted_r2_1.yaml`), so a rule change needs its own acceptance round. The
merge alone closes the bypass: a TSMC-shaped member is excluded whether or not its bank-loans
line is selected (Decisions, review point 3).

### 4. A member's element must fit its family's definition (K-R2)

- **The list.** `configs/eval/v2/element_fit.yaml` (new, committed):

  ```yaml
  version: 1
  families:
    <Family>:
      <standard element>:
        definition: <the taxonomy's definition, quoted>
        review: {by, date, bucket, evidence}   # reviewed-resolutions requirement 1
  ```

  An entry states that the element's standard definition is outside the family's measure.
  `load_element_fit(path, reviewers=None) -> dict` returns family → concept →
  `{"definition", "review"}`, the review normalized by `validate_review(review, reviewers)`
  (`reviewers` None reads `configs/eval/v2/reviewers.yaml`, as `validate_review` does). It
  raises `ValueError` on: a top level other than exactly `version` (1) and `families` (a
  mapping; `{}` allowed); a family or concept that is not a mapping key of the stated form; a
  concept that is not a standard element; an entry with fields other than exactly
  `definition` and `review`; an empty or non-string definition; an invalid review record.
- **The rule.** `element_fit_status(status, family, element_fit) -> UniverseStatus`: when
  `status["status"]` is `ranked`, its `concept` is not None and is listed under `family`, the
  result is `{**status, "status": "definition_excluded", "reason": "element_outside_family"}`
  (concept, value and witness kept); otherwise `status` unchanged.
- **Where it applies.** `KeyWitness` has a public attribute `element_fit` (family → concept →
  entry; read at each call, like `rules`). The constructor takes it as a keyword defaulting to
  none (`{}`); `from_paths` gains a keyword `element_fit` defaulting to
  `configs/eval/v2/element_fit.yaml` and loads it with `load_element_fit`. `KeyWitness.member`
  applies `element_fit_status(<its result>, family, self.element_fit)` for a family that is not
  a defined family (Debt, DeferredRevenue keep `member_status`), after the attribution rule.
  `unkeyed_member` ends in `member`, so every member path (keyed, statement-line, and
  key-fallback-witness's later-standard-line path) is checked; an excluded member's reason is
  `element_outside_family`. Single-filer keys (`fy_key` for a lookup or a comparison) are not
  affected.
- **The live list holds only reviewed entries.** It is committed with `families: {}`. Each
  element from the K2b "Out of scope, noted" list (PPE including finance-lease right-of-use
  assets, `AvailableForSaleSecuritiesDebtSecuritiesCurrent` for ShortTermInvestments, PG cash
  including restricted cash, ORCL `ProceedsFromIssuanceOfSeniorLongTermDebt`, LMT
  `CostOfRevenue` under OperatingExpenses) is reviewed one by one against its definition before
  any entry is added; K-R2 is never applied as a blanket rule.
- **AMD under D3.** `us-gaap:ProceedsFromShortTermDebt` is "the cash inflow from a borrowing
  having initial term of repayment within one year or the normal operating cycle": proceeds
  from issuing debt, which is inside "debt issued" (any term, requirement 5). So it is **not**
  listed for DebtIssued, and AMD's line stays ranked (its FY2023 and FY2024 values are 0, so no
  value changes). This reverses the K2b note's expected K-R2 outcome for AMD (excluded under the
  old long-term meaning): review point 1.

### 5. DebtIssued is "debt issued" (D3)

The family keeps every primary, component and substitute and its statement-line rule; only its
meaning and wording change. Files (an exhaustive list from a repository search for the family
and its wording):

| File | Change |
|---|---|
| `configs/eval/v2/lexicon.yaml` | `DebtIssued: {label: Proceeds from Issuance of Debt, synonyms: [proceeds from debt issuance, debt issued]}` (the us-gaap standard label of `ProceedsFromIssuanceOfDebt`; two synonyms, as today, so the seeded phrasing choice keeps its range; no shorthand) |
| `configs/eval/v2/paraphrase_forms.yaml` | DebtIssued's check-only forms name no term (e.g. `debt issuance`, `debt issuance proceeds`, `proceeds from debt`, `debt proceeds`, `proceeds from issuing debt`, `new debt issued`, `borrowings raised`, `new borrowings`); each names debt or borrowings; the comment above them says DebtIssued is debt of any term and DebtRepaid stays long-term |
| `configs/eval/v2/statement_lines.yaml` | the DebtIssued **comment** only ("Debt issued, any term …"); the patterns do not change (requirement 3, "Not done here") |
| `configs/concept_mapping/concept_family_overrides.yaml` | the DebtIssued `reason` text says the family is gross debt issued of any term (no rank or member changes, so the mapping CSV and `concept_mapping` are unchanged) |
| `docs/design/XbrlRag_Concept_Decisions.md` | a note under D17 recording D3 (2026-11-11) |

No product file changes: the plan-generator prompts already describe DebtIssued as "debt
issuance, proceeds from long-term debt, borrowings issued, proceeds from notes", which covers
"debt issued"; `plan_validator.py` only lists the family id. DebtRepaid is not renamed (its
primaries are long-term repayment elements).

### 6. A defined-family key reads the S1 column (maintainer decision 2026-11-11)

Today a Debt or DeferredRevenue key (key-definitions requirements 2-3,
`KeyWitness._choose_defined`) is read from the **original** annual report labelled the fiscal
year. Every other family follows S1 (the latest filing that reports the period). The defined
keys now follow S1 too, for every year and both families:

1. `own` = the original annual report labelled `year` (`annual_accession`), `end` =
   `own_period_end(cf, own)`; either missing → `no_key` (unchanged).
2. For each fiscal-year label `L > year` among the document's labels, **latest first**, with
   `adsh = annual_accession(cf, labels, L)` (skipped when None or equal to `own`): `d =
   defined_key(cf, cik, family, year, adsh, end, unit)`. The first `d` whose status is
   `defined` with a key is the year's key: `fy_key` returns `(d["key"], own, d["value"])`.
   The key carries `adsh` as its accession, `end` as its period end and `year` as its fiscal
   year. A later filing whose read is not `defined` (`no_fsds`, `not_presented`, a blocking
   status) is passed over and never blocks: key-definitions' rule that only a year's own key
   may block is kept.
3. No later filing defines it: the own report's defined key, exactly as today (including its
   block and drop reasons).

The candidate filings are those of key-fallback-witness's `later_annual_reports(cf, labels,
year)` (whichever lands second may reuse it). The readings (`note_total`,
`current_presented`) are those of the defined record of the filing read, through
`KeyWitness.readings` (keyed by the key's accession), unchanged. The comparisons that read one
filing (`_reread_all`, S2 same-filing) are unchanged. Screen members keyed through the defined
path (`member`, `unkeyed_member`) get the same key.

### 7. Nothing else moves

- A filer whose filing reports in USD is keyed and ranked exactly as today in every path.
- The statement-line rules file, the note list, the resolutions and the reviewer list are not
  changed (the DebtIssued comment aside). The witness statuses and their order are unchanged.
- Without a witness, a build differs only where a keyed member's filing reports in another
  currency than its inventory unit says.

## Acceptance criteria

Fixtures (all synthetic, built in `test/spec/key-currency-and-definition/conftest.py`; made-up
CIKs `0000830001`-`0000830006`, December fiscal years, every filing the FY2023 annual report in
FSDS quarter `2024q1`): companyfacts documents in the SEC API's shape and FSDS-shaped
`sub`/`pre`/`num` rows, built from the conftest's FACTS and LINES tables; statement-line rules
for DebtIssued (no substitutes of its own) and Debt (`combine: sum`); an empty note list and
resolutions; a phrasebook copied from the compound-shape fixture; a small lexicon; an
element-fit list with one entry (`fixtures/`). Filers (millions): **USDA** (10-K, USD;
proceeds 1,000 keyed; debt 200 + 3,000 keyed), **USDB** (10-K, USD, no key; "Proceeds from
issuance of debt" 500; debt 1,500), **TWDC** (20-F, TWD with a FY2023 USD translation, no key;
"Proceeds from long-term borrowings" TWD 85,700 / USD 2,799; debt USD 326 + 3,260), **TWDK** (20-F, TWD, keyed on its USD row:
bank loans TWD 2,450 / USD 80), **IFRU** (20-F, IFRS, USD; bonds line only, 700), **AMDX**
(10-K, USD; "Proceeds from debt, net of issuance costs" on `ProceedsFromShortTermDebt`, 250),
and for C6 only **RSTD** (two 10-Ks, below).
Universe `semis` = TWDC, TWDK, USDA, USDB. Values compare with `pytest.approx(rel=1e-12)` (the
fixture's integer amounts; the tolerance absorbs only binary floating point).

- **C1**: `reporting_currency`. TWDC and TWDK FY2023 → `TWD`; USDA, USDB, IFRU, AMDX → `USD`; a
  year with no labelled filing → None; hand-written documents: both currencies only at the
  latest end → None; `USD/shares`, `shares`, `pure` are not currencies; another filing's facts
  never count; the filing decides across concepts; no facts → None. Property: for any
  generated document, the result is the rule written out in the test; metamorphic: order and
  repeats do not matter, and an earlier end for the chosen currency keeps it chosen. Oracle:
  requirement 1, restated in the test; the conftest's FACTS.
- **C2**: K-R1 in screens. A build (main shapes `screen.sum`, `screen.top`, `peer_relative`)
  keys DebtIssued FY2023 `semis` sum = 1,500 (USDA 1,000 + USDB 500), ranked [USDA, USDB],
  excluded [TWDC `currency`, TWDK `currency`] (TWDC by the statement-line path, TWDK by the
  keyed path on a USD inventory row); Debt sum = 4,700 (3,200 + 1,500), excluded [TWDC
  `currency` (the defined path), TWDK `metric_not_tagged` (no path ranks it, so its path's
  reason stands)]; DebtIssued top rows USDA 1,000, USDB 500; TWDK's peer-relative intent drops
  as `subject_not_ranked`. Metamorphic: with TWDK's TWD facts removed it is ranked on its USD
  row (sum 1,580) and nothing else moves; a universe of the USD reporters alone gives the same
  values and ranking. Oracle: requirement 2, S6, hand sums of
  FACTS.
- **C3**: D4. The committed mapping loads DebtIssued → {bonds: `one_of_several_lines`, "One
  of several gross debt lines; the filer reports no total."}, DebtRepaid's three D21 entries,
  Debt's two D11 entries (one with its own note); malformed mapping files raise; the merge adds
  the mapping's entry, lets a rule's own entry win, ignores a family without a rule, leaves
  every other field and the inputs unchanged, and raises on an `elements` conflict; property:
  the merged substitutes are the mapping's updated by the rule's, and merging is idempotent;
  `KeyWitness.from_paths` merges the committed mapping by default; IFRU's bonds line (700) is
  `substitute:one_of_several_lines` unkeyed and keyed. Metamorphic: the same witness without
  the merge ranks IFRU (700, the bypass), and USDB (500, a primary) is ranked either way.
  Oracle: D11 and D21; the IFRS definition of the bonds element (debt securities only).
- **C4**: K-R2. The fixture list loads to the hand-read record (review date as ISO text);
  malformed lists raise (version, unknown field, extension concept, empty definition, no
  review, unlisted reviewer, unknown entry field); `element_fit_status` excludes exactly a
  ranked member on a listed element of its family, keeping concept, value and witness
  (property: the rule written out); the witness applies its `element_fit` on the unkeyed and
  keyed paths (AMDX excluded as `element_outside_family` with the fixture list, ranked at 250
  with an empty one); the committed list loads, is the build witness's list, does not list
  `ProceedsFromShortTermDebt` under DebtIssued, and AMDX is ranked (250) by the default
  witness. Oracle: requirement 4; the us-gaap definition of `ProceedsFromShortTermDebt`; D3.
- **C5**: D3. The committed lexicon's DebtIssued is exactly `{label: Proceeds from Issuance of
  Debt, synonyms: [proceeds from debt issuance, debt issued]}`, DebtRepaid is unchanged, and
  the lexicon passes `check_lexicon_phrasings`; the check-only DebtIssued forms are unique, name
  debt or borrowings and no term, and DebtRepaid's forms are unchanged; rendering DebtIssued
  intents (a sum, a top-3, an AMD lookup) with every candidate phrasing uses the label and both
  synonyms and never names a term. Metamorphic: for any salt and phrasing count, no question
  names a term. Oracle: requirement 5; the us-gaap standard label and definition of
  `ProceedsFromIssuanceOfDebt`.
- **C6**: the S1 column of a defined key. A further synthetic filer **RSTD** (`0000830007`,
  USD): its FY2023 10-K presents FY2023 debt as one line, 5,713; its FY2024 10-K presents FY2023
  as 75 + 5,713 and FY2024 as 80 + 6,000. `fy_key` for Debt FY2023 is 5,788 from the FY2024
  10-K (period end 2023-12-31, fiscal year 2023, concept `s4:debt_total`); FY2024 is 6,080 from
  its own report. Metamorphic: without the FY2024 10-K (absent from both sources), or with it
  in companyfacts but not in the FSDS data, FY2023 is the own report's 5,713. Property: for any
  FY2023 comparative lines in the FY2024 10-K, the key is their hand sum from that filing.
  Oracle: requirement 6; S1; hand sums of the fixture.

## Interface

New stubs (this spec PR):

- `src.eval.keys.core.reporting_currency(company_facts: dict, labels: Mapping[str, int],
  fiscal_year: int) -> str | None`
- `src.eval.keys.statement_witness.load_mapping_substitutes(path: str | os.PathLike[str])
  -> dict[str, dict[str, dict[str, str]]]`
- `src.eval.keys.statement_witness.merge_mapping_substitutes(rules: Mapping[str,
  StatementRule], substitutes: Mapping[str, Mapping[str, Mapping[str, str]]])
  -> dict[str, StatementRule]`
- `src.eval.keys.statement_witness.load_element_fit(path: str | os.PathLike[str], reviewers:
  Sequence[str] | None = None) -> dict[str, dict[str, dict[str, Any]]]`
- `src.eval.keys.statement_witness.element_fit_status(status: UniverseStatus, family: str,
  element_fit: Mapping[str, Mapping[str, Any]]) -> UniverseStatus`

Changes the implementer makes to existing code (not stubbable; existing signatures unchanged
except the new keywords):

- `src/eval/suites/v2_suite.py` `_Keyer._cross_filer`: requirement 2. **Gotcha found on the
  prototype:** `_Keyer._labels` caches labels by `id(cf)`. With an SEC source that returns a
  new document per call (the unit tests' `_Sec`; the build wraps its source in
  `_MemoSecSource`, which keeps documents alive), an extra companyfacts read lets a freed
  document's id be reused by another filer's, so a member gets another filer's labels
  (`test/unit/test_v2_witness_wiring.py::test_a_screen_excludes_a_substitute_and_ranks_a_presented_unmapped_line`
  failed this way). Key that cache by the document's CIK (as `KeyWitness.annual_accession`
  already does), or reuse the document the path read.
- `src/eval/suites/v2_witness.py` `KeyWitness._choose_defined`: requirement 6.
- `src/eval/suites/v2_witness.py` `KeyWitness.__init__` (keyword `element_fit=None`, public
  attribute `element_fit`), `KeyWitness.from_paths` (keywords `mapping`, `element_fit`, with
  module defaults `DEFAULT_MAPPING`, `DEFAULT_ELEMENT_FIT`), `KeyWitness.member`
  (requirement 4).
- New committed file `configs/eval/v2/element_fit.yaml` (`version: 1`, `families: {}`, with a
  header comment stating requirement 4's review rule).
- The configuration files of requirement 5.
- Docs: `docs/design/XbrlRag_Eval_Semantics.md` S3 lists `element_outside_family` among the
  exclusion reasons and S6 notes that the key's currency is the filing's reporting currency;
  `docs/guides/xbrl-flow/11-evaluation.md` mentions `element_fit.yaml` beside the resolutions.

## Notes

### Oracle sources

Every expected value is read off the conftest's hand-written FACTS and LINES tables (summed and
ranked by hand in the tests), restated from requirement text (the currency rule, the merge, the
element-fit rule), or quoted from the committed decisions D11, D21 and the taxonomy
definitions below. No value comes from running the code, from `data/`, a database or the
network.

- us-gaap `ProceedsFromIssuanceOfDebt`: standard label "Proceeds from Issuance of Debt"; its
  definition covers debt of any term (short- and long-term), the family's D17 parent element.
- us-gaap `ProceedsFromShortTermDebt`: "The cash inflow from a borrowing having initial term of
  repayment within one year or the normal operating cycle, if longer."
- ifrs-full `ProceedsFromIssueOfBondsNotesAndDebentures` (ifrs/2023): "The cash inflow from the
  issuing of bonds, notes and debentures." `ProceedsFromNoncurrentBorrowings`: "The cash inflow
  from non-current borrowings obtained." (K2b note, section 1, from FSDS `tag.tsv`.)

### Evidence (dev and validation only; not test data)

From the K2b analysis (local, read-only: frozen dev and validation suites, `xbrl_db`
read-only, cached companyfacts and FSDS; no LLM call, no held-out data):

- Only one corpus filer reports mostly in a non-USD currency: TSMC (CIK 0001046179). Its
  companyfacts carry TWD for every year and USD only for the latest year of each 20-F.
- **Why filing-level.** Over the 34 cached companyfacts documents (2026-11-11), the filing-level
  rule names TWD for all eight TSMC 20-Fs (FY2017-FY2024) and USD for every annual report of
  every other filer; the per-concept rule ties on two TSMC concepts (above) and on one concept
  each of two USD filers (which a tie leaves as today), so per concept would leave TSMC ranked
  on a DebtRepaid translation.
- **Expected movement (from the K2b $0 simulation, K-R1 only):** 21 case inputs and **20
  distinct cases re-keyed**: dev `int-1a30cb910c27-00213e3a`, `-02fbc316` (DebtIssued semis
  FY2023 sum 25,785,800,000 → 22,987,000,000, the stored system answer, so **INCORRECT →
  CORRECT, +2 dev measured**); validation `-039d5463` (stays INCORRECT); DebtIssued covered
  FY2024 sums (dev, dev_sample, validation: 179,083,460,000 → 178,037,460,000, stay wrong);
  Debt covered medians FY2022 and FY2023 (validation, 6 cases, no grade change); 9 compounds
  (disclosure only). Validation's grades do not change. TSMC exclusions that are
  `metric_not_tagged`, `presented_line_not_keyed` or `no_key` today keep their reason
  (requirement 2 gates ranking only).
- **Requirement 6 (defined keys on S1)** moves every Debt and DeferredRevenue key whose later
  annual report presents the year differently (a restatement or reclassification of the
  comparative column). Two found by the s4-lookup implementer's $0 check (2026-11-11): COST
  FY2025 debt, keyed 5,713 from the original FY2025 10-K (…-25-000101), is 75 + 5,713 = 5,788
  in the FY2026 10-K's comparative (…-26-000093; `LongTermDebtCurrent` + 
  `LongTermDebtNoncurrent`), the system's answer; GOOGL FY2023, keyed 13,253 from the original
  filing, moves to the FY2024 10-K's restated comparative (the system answers its one line,
  11,870; the note total is 13,000). The full set is counted by the K6 dry rebuild's diff, not
  here; it is a K6 movement item, and the amendment states it as a general S1 rule.
- D3 changes the question text of every DebtIssued case (about 11 dev, 7 validation, 6
  dev-compound and 3 dev-sample cases in the frozen suites by their surfaces); those cases
  re-grade as `question_changed` and count only after the next paid run. AMD stays ranked
  (requirement 4), so the K2b note's K-R2 universe change ({8, 7} → {8, 6}) does not happen.
- D4 moves no value in the measured suites: the bonds members are TSMC's, which K-R1 excludes
  anyway; with D4 their reason becomes `substitute:one_of_several_lines` instead of `currency`
  (requirement 2 applies only to a member a path would rank; disclosure text only, the grader
  checks that an excluded ticker is named). Other mapping substitutes now reach the witness too (D&A `Depreciation`, Debt's two
  D11 entries, IFRS NCI totals, `AccountsPayableAndOtherAccruedLiabilitiesCurrent`, …); in the
  corpus only Salesforce presents the last, on a line the AccountsPayable rule does not select,
  and Debt keeps its defined path. The K6 dry rebuild's diff is the check (review point 4).

### Composition with key-fallback-witness (merged spec, implementation in progress)

- **Shared code, no shared stub.** Both change `KeyWitness.from_paths` (new keywords: its
  quarter names; here `mapping` and `element_fit`), `KeyWitness.member`, `unkeyed_member` and
  `_Keyer._cross_filer`'s callees; no signature is shared or redefined. The stubs of this spec
  are appended at the end of `statement_witness.py` and `core.py`, away from
  key-fallback-witness's `later_standard_line`. Either can merge first.
- **Order of reasons.** Requirement 2 gates only a member some path would rank, so every
  reason key-fallback-witness emits (`fsds_not_published`, `fallback_blocked`,
  `presented_line_not_keyed` for TSMC's extension lines) stands; a non-USD member its new
  later-standard-line path would rank is `currency`. key-fallback-witness's tests have no
  non-USD filer, so none of their expectations moves.
- **Later standard lines** are admitted through `self.member`, so K-R2 and the merged
  substitutes apply to them too.
- **The mapping merge and the key-fallback-witness fixture.** Its witness is built by
  `from_paths`, so the live mapping's D&A substitute `us-gaap:Depreciation` reaches its
  fixture's DepreciationAndAmortization rule. Its C2 asserts that `select_key` picks
  `DepreciationDepletionAndAmortization` over `Depreciation`. A substitute entry changes only
  a witness's `substitution` field, never its status, and `Depreciation` (800) disagrees with
  the presented 1,000 line either way, so the agreeing candidate is still the one chosen. (Its
  tests cannot be run green on a prototype of this spec alone, since they need its own
  implementation; this is reasoned from `select_key`.) Its C1 and C6 records are for other
  families.

### Rebuild (K6), H-F and the pre-registration amendment

- Keys and questions change, so the frozen suites are **rebuilt, not edited**: dev, validation,
  their compound suites and dev_sample, from the frozen arguments, then a suite diff, new content
  hashes, the paraphrase pass for the rephrased DebtIssued cases, and a `$0` re-grade of the
  stored runs (`key_change_regrade` / `scripts/eval/regrade_run.py`). That is **K6**, after this
  and key-fallback-witness land; the diff, not this spec, states the final movement.
- **H-F uses the same builder** (hf-suite-build) and the same lexicon, so this lands **before
  H-F is built**; nothing about the held-out database changes, and no agent reads it.
- The pre-registration amendment (K6) must say that the issue **surfaced in the D3/K2b dev
  failure analysis** (the semis DebtIssued sum), that **the rule is general** (S6 applied to
  every member path; no filer, value or system result chose it; TSMC is only the example), and
  that D3 is a wording change that keeps every element of the family; and that requirement 6
  (defined keys on S1) surfaced in the s4-lookup implementation check and makes Debt and
  DeferredRevenue follow the S1 rule every other family already follows.
- **No database rollout.** Eval key builder and eval configuration only; no DDL, view,
  fact-store or product change, no `xbrl_db` write.

### Retired and active tests

- A throwaway prototype of requirements 1-6 and the requirement 5 configuration (outside the
  repository, discarded) was run on 2026-11-11 against the whole `test/spec` tree and
  `test/unit/test_v2_witness_wiring.py`, `test_v2_key_definitions_wiring.py`,
  `test_hf_suite_build_wiring.py`, `test_eval_lexicon_phrasings.py` and
  `test_eval_paraphrase_tooling.py`: 5,787 passed and 154 failed + 2 collection errors, and the
  failures are exactly those of the same run on `main` without the prototype (5,744 passed):
  the active locks' own stubs (fiscal-year-ended-date, key-fallback-witness,
  plan-cagr-and-ratios, s4-lookup-deferred-revenue-debt). Every retired spec test and every
  listed unit test passes. The 43 tests of this spec pass on it, also under `tdd-verify`.
- **Learned on the prototype**, and built into requirement 2: a first draft named `currency`
  ahead of every path reason; the retired v2-suite-build C6 pins a non-USD filer with no key as
  `no_key`, so the check gates ranking only. The `_labels` cache gotcha (Interface) also came
  from it.
- No retired test reads `data/` or pins a frozen suite's bytes, so the intended re-freeze breaks
  no test.
- s4-lookup-deferred-revenue-debt (active) compares the system's defined measure with
  `debt_key` / `deferred_revenue_key` on one filing; requirement 6 changes which filing the
  witness reads, not those functions, so its locked tests are unaffected (its stubs fail the
  same way with and without the prototype).
- key-definitions C7 and witness-rules-r2-1 C5/C6 compare the loaded live statement-line rules
  with locked copies: the merge happens inside `from_paths`, never in `load_statement_rules` or
  the file, so they hold.
- key-definitions C7 lists DebtIssued among the families without a definition id: unchanged
  (D3 adds no S4 definition).

### Decisions and review points (recommendation first)

1. **AMD under D3 (review point; changes the K2b outcome).** By the renamed definition,
   `ProceedsFromShortTermDebt` fits "debt issued", so AMD stays ranked (value 0 in FY2023 and
   FY2024, no grade change) instead of the K2b note's exclusion. **Recommend accepting**: it
   follows from D3 and K-R2's own test (definition over label). Alternative: keep DebtIssued
   long-term and exclude AMD, which reverses D3.
2. **The DebtIssued rule still excludes labels saying "short-term"**, revolvers and commercial
   paper (gross issuance only). Under "debt issued" (any term), a filer that presents separate
   long-term and short-term gross proceeds lines is answered by its long-term line alone.
   **Recommend** a statement-line rules round (r2.2, its own spec and acceptance record) that
   decides, by definition, whether gross short-term proceeds lines join the family (and then
   `combine` and the several-lines case of D21), with "bank loans" in the include (point 3).
   Not needed for any measured case.
3. **"Bank loans" in the DebtIssued include** (K2b D4) is deferred to that rules round: the live
   rules are pinned by the retired witness-rules-r2-1 tests, and the substitute merge already
   closes the bypass.
4. **The merge is general** (every family's mapping substitutes reach the witness), as D4 states
   the defect ("universe_status does not apply mapping substitute codes"). **Recommend** reading
   the K6 dry-rebuild diff for any member that moves outside DebtIssued/DebtRepaid before the
   re-freeze; the search above found none in the corpus.
5. **Filing-level currency** rather than per concept (requirement 1): the system's screen rule
   is per concept and can tie on a TSMC concept (it then ranks the USD translation). That is a
   system-side gap for a later S-R1-style change, not a key change. **Recommend** noting it in
   the D5 follow-up.
6. **D5 (S-R1)** stays out of scope (system wording, not graded).
