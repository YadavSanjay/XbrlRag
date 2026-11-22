# witness-rules-r2-1 — taxonomy and word-boundary invariants on the statement-line rules

Follows [witness-rules-r2](witness-rules-r2.md) (retired: accepted on its one judging read,
recall 0.9317, precision 0.9950, ambiguity 0.0208). Decided by the maintainer on 2026-11-09:
fix the over-broad r2 exclusions as r2.1 under the same no-tuning protocol.

**The defect.** The corpus was measured once with the accepted r2 rules
(`data/eval/witness_blocks/README.md`, last section): 94 blocks became 70, but 10 new blocks
come from four over-broad r2 exclusions:

- DebtIssued/DebtRepaid exclude `costs?\b`, which rejects "Proceeds from issuance of debt, net
  of issuance costs", and so rejects the us-gaap standard label of
  `ProceedsFromDebtNetOfIssuanceCosts` ("Proceeds from Debt, Net of Issuance Costs") itself
  (5 keys);
- Capex exclude `\bleases?\b` rejects "... excluding finance leases" (2);
- Capex exclude `repayment` matches inside "prepayments" (2);
- DebtRepaid exclude `credit (facilit|agreement|line)` rejects "Repayments of senior notes and
  term loan credit agreements" (1).

r2's judging read could not see the first: `ProceedsFromDebtNetOfIssuanceCosts` and
`ProceedsFromIssuanceOfMediumTermNotes` were not DebtIssued oracle elements.

**What this change does not do.** It does not write a rule for any corpus line. The fix is
two invariants on the rules, each derived from the taxonomy or from regex hygiene and checked
mechanically, plus an oracle correction from the us-gaap definitions. The corpus lines are
named above only as the motivation; what stays blocked after r2.1 gets a reviewed resolution
(the "credit agreements" line is a genuinely combined line and is left to one; so are the
"excluding finance leases" lines unless an invariant below happens to cover them, which
neither does).

The protocol of witness-rules-r2 holds unchanged: rules from accounting vocabulary, us-gaap
definitions and standard labels, never from one filer's wording; developed only on r2's dev
sample; judged **once** on a fresh frozen sample against marks written here first; only the
judging aggregates are read.

## Requirement

Terms are those of witness-rules-r2 (filing-family, outcomes, `Score`, `Verdict`). The engine
and loader (`src/eval/keys/statement_witness.py`) and every witness-rules-r2 function are
unchanged; r2.1 is a rules-file change plus new checking functions in
`src/eval/keys/witness_measure.py`. If a rule cannot satisfy the invariants in the existing
schema, the implementer raises a dispute; the schema does not grow inside this change.

### 1. Invariants on the rules

Both are general: they hold for every family in scope, are derived from the taxonomy or from
the regular-expression language, and never from a corpus label.

**1.1 Standard labels are never rejected** (`standard_label_violations`). For every family of
the r2.1 oracle (requirement 2) and every oracle element of it, the element's us-gaap
**standard label** (role `http://www.xbrl.org/2003/role/label`, us-gaap 2025) is not rejected
by the family's rule: no `exclude` pattern matches it, and every `require` pattern matches it
(case-insensitive `re.search`, as the engine). Rationale: an oracle element's definition *is*
the family's measure, so a line carrying the taxonomy's own name for that measure is the
family's line; a rule that rejects it has an exclusion broader than the accounting vocabulary
it was written from.

`include` need not match the standard label. Standard labels are taxonomy-styled ("Assets,
Current", "Equity, Attributable to Parent", "Cash Provided by (Used in) Operating Activity,
Including Discontinued Operation"), not presentation-styled; r2's includes miss 13 of them,
and matching them would add taxonomy-styled patterns no filer prints. A rejection is a defect
of a rule; a non-inclusion is not.

`standard_label_violations(rules, oracle, labels)` takes `labels` mapping each element's
local name to its standard label, or None for an oracle element the 2025 taxonomy no longer
has (skipped); it returns one dict `{family, element, label, field, pattern}` per
rejecting pattern (`field` `exclude` or `require`), sorted by `(family, element, field,
pattern)`. It raises `ValueError` when an oracle family has no rule or an oracle element has
no entry in `labels`.

Scope: the 48 oracle families. With r2's rules it reports, among others, DebtIssued
`costs?\b` on "Proceeds from Debt, Net of Issuance Costs"; also `in process` (R&D, "Research
and Development Expense (Excluding Acquired in Process Cost)"), `goodwill` (IntangibleAssets,
"Intangible Assets, Net (Excluding Goodwill)") and `discontinued` (the three cash-flow totals,
"..., Including Discontinued Operation"). All of these are terms inside a qualifier of the
standard label; r2.1 must keep rejecting the lines those exclusions were written for while
accepting the standard label (for instance a negative lookbehind on the qualifier), which the
existing schema allows.

**1.2 No exclude or require term starts inside a word** (`left_bounded`,
`boundary_violations`). A pattern is **left-bounded** when, searched case-insensitively, it
can never start a match at a position i ≥ 1 of a text where the characters at i − 1 and i are
both letters (an empty match at such an i counts). Rationale: English inflection is suffixal,
so a stem open on the right ("accru", "revolv", "liabilit") is deliberate vocabulary, but a
term open on the left matches a different word formed with a prefix ("repayment" in
"prepayments", "current" in "noncurrent", "paid" in "unpaid"), usually of opposite meaning.
Only the left side is required: requiring right boundaries too would forbid r2's deliberate
stems for no gain.

`left_bounded(pattern)` decides this from the pattern's syntax (`re` parser), exactly for
literals, classes, groups, alternation, repetition and anchors:

- a branch is bounded when `^`, `\A`, `\b`, `$` or `\Z` comes before its first consuming
  item, or when that item cannot match a letter (a non-letter literal, a class without
  letters, `\d`, `\s`);
- lookaround assertions are passed over (they only remove matches); `\B` is passed over;
- an optional or zero-minimum repeat is bounded only when both taking and skipping it are;
- a branch that ends before any consuming item or anchor matches the empty string, so inside
  any word: unbounded;
- `.`, a negated class, `\w`, and any other construct (backreference, conditional) that can
  start a match are unbounded.

It raises `ValueError` on a pattern that does not compile. `boundary_violations(rules)`
returns one dict `{family, field, pattern}` per `exclude` or `require` pattern
that is not left-bounded, sorted by `(family, field, pattern)`; `include`, `prefer` and
`section` are not checked (they select, they do not reject).

Scope: every family except `Debt` and `DeferredRevenue` (49), which r2.1 does not touch
(requirement 3). In r2, 252 of the 302 exclude/require patterns in scope are not
left-bounded; r2.1 bounds them (typically a leading `\b`), which is a behaviour change on the
lines a mid-word match used to reject, and is judged as such.

**1.3 Not included: negating qualifiers in general.** A general rule "a term inside a
negating qualifier ('excluding …', 'net of …') does not reject a line by itself" cannot be
stated for every family: CostOfRevenue rejects "excluding" on purpose (a cost line excluding
D&A is a component). Making it general needs the engine to know a qualifier's extent and a
per-family opt-out — a schema change. **Follow-up**, not this change. Requirement 1.1 already
forces the qualifier cases that appear in a standard label.

### 2. The judging oracle

The locked file `test/spec/witness-rules-r2-1/fixtures/oracle.yaml` is witness-rules-r2's
oracle with three elements added, each decided from its us-gaap 2025 definition
(`fixtures/taxonomy_excerpt.json`):

| Family | Added element | Definition (us-gaap 2025) | Why it is the measure |
|---|---|---|---|
| DebtIssued | `ProceedsFromDebtNetOfIssuanceCosts` | "The cash inflow from additional borrowings, net of cash paid to third parties in connection with debt origination." | The borrowing inflow itself; the aggregate counterpart of `ProceedsFromIssuanceOfDebt` ("additional borrowings in aggregate debt"), already an oracle element. "Net of issuance costs" states how the line is presented, not a different flow; filers already present net-of-cost amounts on the long-term element. |
| DebtIssued | `ProceedsFromIssuanceOfMediumTermNotes` | "The cash inflow from a debt funding received on a regular basis with maturities ranging from 5-10 years." | A class of long-term debt principal raised; admitted on the same footing as `ProceedsFromIssuanceOfSeniorLongTermDebt`, which r2's oracle admits. |
| DebtRepaid | `RepaymentsOfMediumTermNotes` | "The cash outflow to pay off borrowing used to receive debt funding on a regular basis with maturities ranging from 5-10 years." | The repayment counterpart, same footing as `RepaymentsOfSeniorDebt`. us-gaap 2025 has no repayment element "net of costs". |

Every other family and field is r2's. 48 families are judged (r2's set). Three oracle
elements have no element in us-gaap 2025 (removed after deprecation:
`EarningsPerShareBasicAndDiluted`, `SalesRevenueNet`,
`WeightedAverageNumberOfShareOutstandingBasicAndDiluted`); the excerpt lists them as `absent`
and requirement 1.1 skips them. They stay oracle elements (an older-taxonomy filing may still
carry them).

### 3. The r2.1 rules

r2.1 replaces the content of `configs/eval/v2/statement_lines.yaml` so that:

- both invariants hold in their scopes (1.1 over the 48 oracle families, 1.2 over the 49
  families other than `Debt` and `DeferredRevenue`);
- the 51 families are r2's; `rule_identifier_violations` is empty (witness-rules-r2
  requirement 1); every key-statement-witness hand-checked line is still selected (that
  retired spec's C2, run on the live file);
- the `Debt` and `DeferredRevenue` entries are r2's, byte for byte in meaning (they load
  equal). key-definitions (active lock) owns them; **its implementation rebases onto r2.1**,
  and the live entries may then be key-definitions' locked ones instead;
- every changed pattern is listed in the implementation report with the invariant or the
  us-gaap label it comes from. The maintainer reviews the diff against "accounting vocabulary
  and regex hygiene, not one filer's wording". No pattern is added for a corpus line.

### 4. The marks (`acceptance_r2_1`)

Pre-registered here, before any r2.1 rule is written (maintainer decision, 2026-11-09).
`acceptance_r2_1(base, candidate)` compares the accepted r2 rules' `Score` (base) with r2.1's
(candidate), both scored on judge2 with the r2.1 oracle, and returns a `Verdict`. r2.1 is
accepted when none of these fails (integer arithmetic; `hit`, `wrong`, `ambiguous`,
`evaluable` are the candidate's totals, `base hit` the base's):

| Code | Passes when |
|---|---|
| `precision` | `hit * 1000 >= 992 * (hit + wrong)`, and `hit + wrong > 0` (absolute) |
| `ambiguity` | `ambiguous * 1000 <= 25 * evaluable` (absolute) |
| `recall_regression` | `hit >= base hit` (the evaluable counts are equal, so recall(r2.1) >= recall(r2)) |
| `family_regression` | no family with `n >= 10` evaluable loses, against **r2**, more than one filing and more than 0.05 recall (`loss = base hit - candidate hit`; regresses when `loss > 1` and `loss * 100 > 5 * n`) |

There is **no absolute recall mark** (no `recall` code). `failed` is sorted;
`regressed_families` counts regressing families; the verdict names no family. `ValueError`
when the two scores' family sets or a family's evaluable count differ.

Rationale, recorded with the marks: r2 already passed the pre-registered recall mark of
0.930 on the (now spent) judging sample. r2.1 fixes specific defects of r2, so the judge2
read tests the change against r2 on the same filings, not sampling noise: r2 measured 0.9317,
0.0017 above 0.930, against a standard error of about 0.004 at ~3,600 cases, so r2-level rules
would miss a fixed 0.930 on a fresh sample about one time in three. Precision and ambiguity
stay absolute: a `wrong` result is a witness approving a wrong key (the silent-wrong
failure), and an ambiguous line blocks a key, whatever r2 scored.

### 5. The judging record (`judge_record_r2_1`)

`judge_record_r2_1(sample_dir, rules_path, base_rules_path, oracle_path, draw_sha256)` checks
the draw exactly as witness-rules-r2's `judge_record` (keys exactly `sub`, `pre`, `num`, `tag`,
each file's SHA-256 equal, else `ValueError`; a missing file `FileNotFoundError`), scores
every filing of `sub` with both rules files (`score_filing`, `aggregate`) and returns

```python
{"spec": "witness-rules-r2-1",
 "draw_sha256": {...},                                   # as checked
 "oracle_sha256": str, "rules_sha256": str, "base_rules_sha256": str,   # file SHA-256s
 "base": aggregate(r2 outcomes), "candidate": aggregate(r2.1 outcomes),
 "verdict": acceptance_r2_1(base, candidate)}
```

**The one judging read.** When every other test passes, the implementer copies the live
rules byte for byte to `configs/eval/v2/statement_lines.accepted_r2_1.yaml`; the orchestrator
(not the implementing agent, who never reads the judging sample) runs once
`judge_record_r2_1(<judge2 sample>, configs/eval/v2/statement_lines.accepted_r2_1.yaml,
test/spec/witness-rules-r2/fixtures/statement_lines_r2.yaml,
test/spec/witness-rules-r2-1/fixtures/oracle.yaml, <judge2 hashes>)`, writes it as JSON to
`configs/eval/v2/statement_lines.acceptance_r2_1.json`, and reports the totals and verdict
only. The committed record must:

- pin the judge2 draw (Notes), the r2.1 oracle, the r2 rules copy (SHA-256
  `947ba5c6…ee4a9b`, r2's recorded `rules_sha256`) and the accepted r2.1 copy (its
  `rules_sha256`, different from r2's);
- have the live rules load equal to the accepted copy in every family except `Debt` and
  `DeferredRevenue` (those are the copy's or key-definitions' locked entries);
- be internally consistent (each `Score` is `aggregate` of its own family counts; the verdict
  is `acceptance_r2_1(base, candidate)`); 48 families; equal evaluable counts;
- accept r2.1.

**If r2.1 is rejected,** the record is not committed and judge2 is spent: r2.1 goes back to
the dev sample, and another read needs a fresh draw disjoint from dev, judge and judge2, pinned
here by a re-lock before it is read. The totals are reported to the maintainer.

### 6. After acceptance (not in this change)

The corpus is measured once with r2.1. What stays blocked (at least the "credit agreements"
and "excluding finance leases" lines) gets a reviewed resolution, in its own spec change.

## Acceptance criteria

- **C1** (taxonomy invariant): `standard_label_violations` gives the hand-derived violations
  on synthetic rules (an exclude matching a label, a require missing it; include unchecked;
  None skipped), refuses a missing label or rule, adds exactly the violations an added
  exclude phrase implies; the oracle is r2's plus the three elements of requirement 2, whose
  standard labels and definitions are those of the excerpt (pinned source SHA-256s); r2's
  rules reject the six listed standard labels; the live rules reject none. Oracle: the
  us-gaap 2025 label and documentation linkbases; each listed label read by hand.
- **C2** (word boundaries): `left_bounded` gives the hand verdict on 26 constructed patterns
  and on prefix + word patterns (each verdict confirmed by a witness text), a leading `\b`
  always bounds and a leading letter never does, bounded patterns never match after a letter
  (generated matches), an invalid pattern is refused; `boundary_violations` gives the
  hand-derived list on synthetic rules; r2 has Capex `repayment` and Debt `costs?\b`; the
  live rules (49 families) have none. Oracle: requirement 1.2 by hand.
- **C3** (marks): `acceptance_r2_1` applies the precision and ambiguity marks at their
  boundaries (992/1000 passes, 991 fails; 25/1000 passes, 26 fails), has no absolute recall
  mark (a candidate at recall 0.5 with r2's hits passes), applies `recall_regression` (equal
  hits pass, one fewer fails, a family loss offset elsewhere does not trigger it) and the
  per-family rule against r2; names no family; refuses mismatched scores; matches the table
  computed in the test and is monotone. Oracle: requirement 4.
- **C4** (record function): `judge_record_r2_1` over witness-rules-r2's synthetic sample
  gives the hand-derived record (base hit 3 / candidate hit 5; reject on `ambiguity` only);
  swapped (candidate hit 3, wrong 1 against base hit 5), it rejects on `ambiguity`,
  `precision` and `recall_regression`; it refuses a wrong or incomplete hash map and a
  missing table. Oracle: witness-rules-r2 Notes, requirement 4.
- **C5** (live rules): the live file differs from the r2 copy, has r2's 51 families, no
  identifier, `Debt`/`DeferredRevenue` as r2's or key-definitions'; the invariant checks on
  it are not vacuous (an injected rejection is reported). Oracle: requirements 1 and 3.
- **C6** (record): `configs/eval/v2/statement_lines.acceptance_r2_1.json` meets requirement
  5. Oracle: the pinned hashes in Notes, requirement 4.

## Interface

Added to `src/eval/keys/witness_measure.py` (stubs in the spec PR; nothing existing changes):

- `standard_label_violations(rules: Mapping[str, StatementRule], oracle: Mapping[str, OracleFamily], labels: Mapping[str, str | None]) -> list[dict[str, str]]`
- `left_bounded(pattern: str) -> bool`
- `boundary_violations(rules: Mapping[str, StatementRule]) -> list[dict[str, str]]`
- `acceptance_r2_1(base: Score, candidate: Score) -> Verdict`
- `judge_record_r2_1(sample_dir: str | os.PathLike[str], rules_path: str | os.PathLike[str], base_rules_path: str | os.PathLike[str], oracle_path: str | os.PathLike[str], draw_sha256: Mapping[str, str]) -> dict[str, Any]`

Files the implementation changes or adds: `configs/eval/v2/statement_lines.yaml` (r2.1),
then `configs/eval/v2/statement_lines.accepted_r2_1.yaml` and
`configs/eval/v2/statement_lines.acceptance_r2_1.json` (requirement 5, last). Work may be
split: `impl/witness-rules-r2-1--part1` (everything but the record; C6 fails with an
assertion), then `impl/witness-rules-r2-1` adds the copy and the record.

## Notes

**Fresh judging draw (judge2).** Drawn by the orchestrator before this spec was written,
from witness-rules-r2's population and seeded order (`sha256(seed + cik)`, seed
`witness-rules-r2/2026-11-09`; see that spec's Notes): positions 240-359, disjoint by CIK
from the dev sample (0-119) and the spent judging sample (120-239).
`data/eval/witness_rules_r2/sample_judge2.py`, output `out/judge2/` (gitignored, never opened
by the spec author or implementer). SHA-256:

| Table | SHA-256 |
|---|---|
| `sub.tsv` | `6cf3c9384d7662f4501d0b39a0ac64298c6ee4a6070a8d7dbf0f29e9fc123354` |
| `pre.tsv` | `ae8aec97e1e86ef5d8f96f624e5a8fbc286c85f7e23686976b508e9bacbb6edf` |
| `num.tsv` | `8a2ce00f08d3c0367af69c5b1e02d4b1e6fc37a65371ee4392332d91a51d3d38` |
| `tag.tsv` | `cdc7e2b7ef1bdd808d90fd3850176f8ec1e6bd1a46f13c4f6d6a5cded3b0f1c8` |

`out/draw.json` (now holding all three accession lists):
`8a37b05119e71f53dd3514f966114fe3dd718bba35d23bfc942094715a98009e`.

No baseline is pinned: r2 has not been measured on judge2 (the one read measures both).

**Taxonomy excerpt** (`fixtures/taxonomy_excerpt.json`): the standard label (role `label`)
and documentation of every oracle element from the FASB us-gaap 2025 linkbases
`https://xbrl.fasb.org/us-gaap/2025/elts/us-gaap-lab-2025.xml` (SHA-256
`c10e9ec81f18676a2f047a5fd614baeec9a35b34a755d59d4b67bcd5349f69b0`) and `us-gaap-doc-2025.xml`
(`27f1d4ba06b0dcb0c91225db2ac746d5adefc167518fa3948abbdfb944dab019`) — the same files, by
hash, as key-statement-witness's excerpt; read from the local Arelle cache copies. 71
elements, 3 absent.

**r2 rules copy.** `test/spec/witness-rules-r2/fixtures/statement_lines_r2.yaml` is a byte
copy of the accepted r2 `configs/eval/v2/statement_lines.yaml` (SHA-256
`947ba5c6ac81628c96edcfe9661a1de86fd3dd314e0331947fab70fa67ee4a9b`, equal to the r2 record's
`rules_sha256`). It is the base of the r2.1 judging and the fixture r2's C7 now pins.

**Retired tests touched by this spec PR.**

- `test/spec/witness-rules-r2/test_c7_record.py`: `rules_sha256` was pinned to the live file,
  which r2.1 changes; it is now pinned to the r2 copy (and to the literal SHA-256). r2's
  record stays valid as a historical record. The rest of r2's C7 is unchanged.
- `test/spec/witness-rules-r2/conftest.py`: adds the copy's path.
- r2's C5 (live rules differ from r1, 51 families, no identifier) still holds for r2.1 and
  is unchanged; key-statement-witness's live hand-checked-line test is unchanged and must
  keep passing.

**key-definitions (active lock).** Its Notes plan the r2-C7 re-pin in a rebased spec PR;
that re-pin is done here instead. Its planned extra test "every live family except `Debt`
and `DeferredRevenue` loads equal to r2's copy" must not be added: after r2.1 the reference
is the accepted r2.1 copy, and this spec's C6 already holds the live file to it. Its
implementation rebases onto r2.1 (r2.1 touches neither of its families; C5 and C6 accept the
live `Debt`/`DeferredRevenue` entries in either r2's or key-definitions' locked form).

**Synthetic sample.** `fixtures/synthetic/` is a byte copy of witness-rules-r2's (four
filings, rules `rules_r1.yaml`/`rules_r2.yaml`, oracle `oracle_synthetic.yaml`); outcomes in
that spec's Notes table.

**Maintainer decisions (each locked as recommended):**

1. `include` is not required to match the standard label (requirement 1.1).
2. Word boundaries are required on the left only (requirement 1.2).
3. The negating-qualifier rule is a follow-up needing a schema change (1.3).
4. The three oracle additions of requirement 2, including the medium-term-note pair on the
   Senior footing. The stricter alternative — drop both the Senior and the MTN elements as
   components — is not taken: it would change r2's oracle beyond the defect.
5. Invariant scopes: 48 oracle families (1.1); 49 families without `Debt`/`DeferredRevenue`
   (1.2), so `EffectiveTaxRate` is bounded too.
6. The judged r2.1 file is a committed byte copy (`statement_lines.accepted_r2_1.yaml`), so
   key-definitions can change the live `Debt`/`DeferredRevenue` entries without breaking C6.
7. The recall mark is no regression against r2 on judge2 only; the absolute 0.930 recall mark
   is dropped (decided by the maintainer, 2026-11-09; requirement 4).
