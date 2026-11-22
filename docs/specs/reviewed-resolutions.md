# reviewed-resolutions — residual witness blocks get reviewed resolutions, not rule iterations

Follows [key-statement-witness](key-statement-witness.md) (retired), requirement 3, and the
protocol of [witness-rules-r2](witness-rules-r2.md) / [witness-rules-r2-1](witness-rules-r2-1.md)
requirement 6: after the rules work the corpus is measured **once**, and the keys that stay
blocked get **reviewed resolutions**, never another rule iteration.

**The defect.** Resolutions are shrink-only today: `check_resolutions(current, locked)` accepts
a live entry only when it equals an entry of key-statement-witness's locked list
(`test/spec/key-statement-witness/fixtures/key_resolutions.yaml`, one ORCL entry), and the
build enforces the same through a pinned copy (`configs/eval/v2/key_resolutions.locked.yaml`,
`KeyWitness.from_paths`). A new resolution needs a re-lock of a retired spec, so the protocol's
last step cannot happen. Nothing records *who* reviewed an entry, *what* they checked, or that
the entry still covers a key the build actually blocks.

**What this change does.** It specifies the mechanism, not the entries. A resolution outside
the original list is admitted only with a complete **review record**, and the build refuses
any resolution that covers no blocked key of the build. The entries themselves are data,
added later by the maintainer's review (route `Spec-Skip: reviewed`, the diff read by the
human), never by an agent from a judging sample or held-out data.

## Requirement

Terms are those of key-statement-witness (resolution, `Witness`, *covers*, blocking status,
*the same* amount, `statement_lines`, primary statements `BS`, `IS`, `CI`, `CF`, `EQ`). An
entry's **identity** is `(family, cik, fiscal_year, concept)`.

### 1. The review record (`validate_review`)

A resolution may carry one more field, `review`, a mapping with exactly the fields:

| Field | Value |
|---|---|
| `by` | who reviewed the entry: a name of the reviewer allow-list, exactly (below) |
| `date` | the review date: a YAML date or a `YYYY-MM-DD` string naming a real calendar date; a date-time is refused |
| `bucket` | one of `rule_gap`, `combined_line`, `definition`, `note_unlisted`, `by_design` |
| `evidence` | the hand-checked source, one of the two forms below |

Evidence forms (exactly these fields):

- **statement** `{kind: statement, adsh, report, line, plabel}`: the filing's accession
  (`\d{10}-\d{2}-\d{6}`), the FSDS `report` and `line` (ints, not bool, at least 1) and the
  line's printed label `plabel` (non-empty string) as FSDS `pre` gives it;
- **note** `{kind: note, adsh, label}`: the accession and the note line's printed label
  (non-empty string).

Buckets (what the reviewer found; the entry's existing `reading` is the reading chosen and
its `reason` the explanation):

| Bucket | Meaning | Evidence kind |
|---|---|---|
| `rule_gap` | the presented line is the family's measure, but the rule does not select it | statement |
| `combined_line` | the filer presents the measure only inside a line combining it with another item | statement |
| `definition` | key and line measure the item under different scopes or definitions | either |
| `note_unlisted` | the measure is disclosed only in a note, on a concept the note list does not hold | note |
| `by_design` | the witness is right and the reading follows by design | either |

**Reviewer allow-list** (maintainer decision, 2026-11-09: an agent may prepare an entry, only
the maintainer marks it reviewed). The locked list is
`test/spec/reviewed-resolutions/fixtures/reviewers.yaml` (`version: 1`, `reviewers: [Sanjay]`).
The live copy `configs/eval/v2/reviewers.yaml` loads equal to it, and the build reads the live
copy (it never reads `test/spec`). Adding a reviewer is a re-lock of this spec by the
maintainer. `load_reviewers(path)` returns the names in file order and raises `ValueError` on
a top-level field other than `version` and `reviewers`, a `version` other than 1, a missing or
empty list, or a name that is not a string, is empty after stripping, or is repeated.

`validate_review(review, reviewers=None)` checks `by` against `reviewers`; None means
`load_reviewers` of the live copy `configs/eval/v2/reviewers.yaml` (located from the package,
like the other v2 config defaults). `by` must equal a listed name exactly (case and spacing
included). It returns a new dict equal to the input except that `date` is the ISO
string (`"2026-11-10"`) and `evidence` is a new dict; it raises `ValueError` on anything else:
not a mapping, a missing or unknown field (record or evidence), a `by` that is not a listed
name (an empty one included), a date that is
not a real date or is a date-time, an unknown bucket or kind, a malformed accession, a
`report`/`line` that is not an int >= 1 (bool refused), an empty `plabel`/`label`, or a bucket
whose evidence kind the table above forbids. It never mutates its argument.

`load_resolutions(path, reviewers=None)` accepts the optional `review` field: an entry with
one is returned with `review` replaced by `validate_review(review, reviewers)` (so its `date`
is a string); a `ValueError`
from it propagates. Every other rule of key-statement-witness requirement 3 is unchanged
(fields, readings `key` / `drop`, non-empty reason, unique identity, version 1).

### 2. What replaces shrink-only (`check_resolutions`)

The locked list of key-statement-witness becomes the **original list**: entries admitted
before review records existed. `check_resolutions(current, locked, reviewers=None)` returns
`len(current)` when every entry of `current` either

- equals an entry of `locked` field for field (values as the same amount), or
- carries `review` and `validate_review(entry["review"], reviewers)` does not raise (None:
  the live allow-list, so the two-argument call of existing callers checks `by` too);

and raises `ValueError` naming the first entry that does neither. So the original list may
only shrink (an original entry changed in any field and not reviewed is refused), and any
other entry needs a complete review record. Removing an entry, original or reviewed, is always
allowed (key-definitions removes the ORCL entry).

The pinned copy stays: `configs/eval/v2/key_resolutions.locked.yaml` remains an entry-for-entry
copy of the original list (the unit test that keeps them equal is unchanged), and
`KeyWitness.from_paths` keeps calling `check_resolutions(current, <the copy>)` before any FSDS
row is read. With requirement 2 it therefore admits a reviewed addition and still refuses an
unreviewed one before reading FSDS. The original list itself never grows: a new entry goes
into `configs/eval/v2/key_resolutions.yaml` with its review record, not into either copy.

### 3. No speculative entries (`check_resolution_use`)

An entry **covers** a witness when the witness's `resolution` is not None and has the entry's
identity and a `value` the same as the entry's (that is, `resolve` attached it, which happens
only for a blocking status). `check_resolution_use(resolutions, witnesses, tables)` checks,
for each entry in order, and raises `ValueError` naming the first that fails:

1. **Coverage.** Some witness of `witnesses` is covered by the entry. An entry that resolves
   no blocked key of the build (stale, speculative, or for a key the build no longer reaches)
   is refused, original entries included.
2. **Filing.** For a reviewed entry, `review.evidence.adsh` equals the `adsh` of a covered
   witness (the filing the witness read).
3. **Statement line.** For statement evidence, `statement_lines(tables, adsh)` holds a line
   whose `stmt` is a primary statement and whose `report`, `line` and `plabel` equal the
   evidence's (label compared exactly). Note evidence is not checked against FSDS (the data
   sets hold no note lines).

It returns `{"n": len(resolutions), "original": entries without review, "reviewed": entries
with review, "by_bucket": {bucket: count}}` with all five buckets, zero included. It reads the
review as loaded (`date` a string) and does not re-validate it. Its result is independent of
the order of `witnesses`, and of the order of `resolutions` except which failing entry is
named.

**At build time** (behaviour on existing code, implementation PR): `build_v2_suites`, after
`witness.raise_if_blocked()`, calls `check_resolution_use(witness.resolutions, <every witness
the build kept>, <the build's FSDS tables>)` and stores the result as
`manifest["key_resolutions"]`; a `ValueError` fails the build. The kept witnesses are those of
`KeyWitness.witnesses` (every chosen or held key's witness, screen members included); a
resolution attached only to a candidate `select_key` did not return is not used.

### 4. A resolution decides a key, nothing more

Readings stay `key` (the key stands, resolved) and `drop` (the intent is dropped with reason
`resolved_drop`), as key-statement-witness requirement 3. A resolution never supplies another
value, concept or reading for a case, and never drops a case other than through its intent.

## Acceptance criteria

- **C1** (review record): `validate_review` returns the hand-normalized record for a complete
  statement and note record (YAML date and ISO string both give `"2026-11-10"`), refuses each
  listed defect (missing/unknown field, empty `by`, 2026-02-30, a date-time, unknown bucket or
  kind, bad accession, `report` 0 / `True` / `"3"`, empty label, `rule_gap` with note evidence,
  `note_unlisted` with statement evidence), never mutates its input; removing any one field of
  a complete record is refused; `load_resolutions` returns a reviewed entry with the normalized
  record and refuses an incomplete one. Oracle: requirement 1, by hand.
- **C2** (replaces shrink-only): against the original list (key-statement-witness's locked
  file), the constructed list (the ORCL original and four reviewed entries) passes with 5;
  an unreviewed addition and an original changed in one field are refused, and the same entry
  with a complete review passes; an incomplete review is refused; every subset of a valid list
  passes with its length (the ORCL entry may be removed). Oracle: requirement 2, by hand.
- **C3** (no speculative entries): over the constructed witnesses and FSDS lines,
  `check_resolution_use` gives `n` 5, `original` 1, `reviewed` 4, `by_bucket` rule_gap 1,
  combined_line 1, definition 1, note_unlisted 1, by_design 0; it refuses an entry covering no
  witness (no witness, an accepted witness, another value), a filing other than the covered
  witness's, and a statement line absent from the filing or with another label; the result is
  invariant under reordering and under added accepted witnesses, and removing a covering
  witness refuses. Oracle: the constructed fixtures, counted by hand.
- **C5** (reviewer allow-list): the locked list is `[Sanjay]` and the live
  `configs/eval/v2/reviewers.yaml` loads equal to it; `load_reviewers` refuses each listed
  defect; `validate_review` and `check_resolutions` refuse a `by` outside the list given
  ("sanjay", "Sanjay ", "maintainer", "claude") and, with no list given, outside the live
  list; any name outside a list is refused and every listed name accepted (property).
  Oracle: requirement 1 and the locked fixture, by hand.
- **C4** (build entry point): `KeyWitness.from_paths` admits the constructed reviewed list
  (its entries loaded with the normalized review) and still refuses an unreviewed addition
  before any FSDS directory is read. Oracle: requirement 2.

## Interface

In `src/eval/keys/statement_witness.py` (stubbed in the spec PR):

- `load_reviewers(path: str | os.PathLike[str]) -> list[str]`
- `validate_review(review: Mapping[str, Any], reviewers: Sequence[str] | None = None) -> dict[str, Any]`
- `check_resolution_use(resolutions: Sequence[Mapping[str, Any]], witnesses: Iterable[Witness], tables: FsdsTables) -> dict[str, Any]`

Behaviour on existing code (implementation PR): `load_resolutions` (optional `review`,
requirement 1) and `check_resolutions` (requirement 2) each gain only a trailing optional
`reviewers: Sequence[str] | None = None` (existing calls unchanged); the implementation adds
`configs/eval/v2/reviewers.yaml`, a byte copy of the locked fixture; `build_v2_suites`
calls `check_resolution_use` and stores `manifest["key_resolutions"]` (requirement 3);
`scripts/eval/build_v2_suite.py` reports it; the header comments of
`configs/eval/v2/key_resolutions.yaml` and `key_resolutions.locked.yaml`, the docstring of
`KeyWitness.from_paths` and `test/unit/test_v2_witness_wiring.py` describe the original list
plus reviewed additions instead of shrink-only. No resolution entry is added in this change.

## Notes

### Fixtures (constructed)

`test/spec/reviewed-resolutions/fixtures/reviewed.yaml` is a constructed list: the ORCL entry
copied verbatim from the original list (no review) and four reviewed entries on fictitious
filers (CIKs `00009002xx`, accessions `00009002xx-26-000001`), one per bucket except
`by_design`, each `by: Sanjay`. `fixtures/reviewers.yaml` is the locked reviewer list
(requirement 1). The witnesses and FSDS `pre` rows the tests read are built in the spec's
`conftest.py` from the table below; nothing comes from a corpus filing.

| Entry | Family, FY, concept, value | Reading | Bucket | Evidence | Witness |
|---|---|---|---|---|---|
| ORCL | DeferredRevenue 2024 `ContractWithCustomerLiability` 10,546,000,000 | key | (original) | — | disagree, ORCL 10-K |
| AAA | AccountsPayable 2025 `AccountsPayableAndAccruedLiabilitiesCurrent` 1,500 | drop | combined_line | BS report 2 line 14 "Accounts payable and accrued expenses" | rejected_line |
| BBB | DebtRepaid 2024 `RepaymentsOfLongTermDebt` 750 | key | rule_gap | CF report 6 line 22 "Repayments of senior notes and term loan credit agreements" | rejected_line |
| CCC | InterestIncome 2025 `InvestmentIncomeInterest` 120 | key | note_unlisted | note "Interest income" | note_unlisted |
| EEE | Debt 2025 `LongTermDebt` 2,000 | key | definition | BS report 4 line 30 "Long-term debt" | disagree |

### Retired tests touched by this spec PR

- `test/spec/key-statement-witness/test_c3_disagreement.py`: the module docstring and the
  oracle comments of `test_live_resolutions_only_shrink` and
  `test_any_entry_outside_the_locked_list_is_growth` now say "the original list only shrinks;
  an unreviewed entry outside it is refused (reviewed-resolutions requirement 2)". No
  assertion, test name or fixture changes: every entry those tests construct has no `review`,
  so requirement 2 refuses it exactly as shrink-only did, and the live list (original entries
  plus reviewed ones) passes `check_resolutions` against the original list.
- key-definitions (active) C7 `test_live_resolutions_drop_the_orcl_deferred_revenue_entry`
  asserts `check_resolutions(live, original) == len(live)`; under requirement 2 it keeps
  passing after reviewed entries are added, so it needs no change.

### Decisions (locked as recommended)

1. **Keep the original list and its pinned copy**, with `check_resolutions` generalised
   (original or reviewed) rather than a new check beside it: the retired tests and
   key-definitions' locked test keep their meaning, and the build entry point changes no call.
2. **Review record in the entry**, not a side file: an entry and its review cannot drift
   apart, and `load_resolutions` refuses an incomplete one where the entry is read.
3. **The unused-entry check is at build time** and refuses (fail-loud), original entries
   included, rather than a report: a resolution must name a key this build blocks, so a stale
   one (a key the mapping moved, a quota no longer reached, ORCL after key-definitions) is
   removed, not carried. Removal needs no review.
4. **Statement evidence is checked against FSDS** (filing, report, line, exact label), so a
   cited line must exist; note evidence is recorded only.
5. **Bucket constrains evidence kind** only for `rule_gap`/`combined_line` (statement) and
   `note_unlisted` (note).
6. **A resolution decides the key only** (requirement 4): no value override, no case-level
   drop beyond the existing `drop` reading.
7. **`by` is checked against a reviewer allow-list** (maintainer decision, 2026-11-09,
   replacing the draft's "any non-empty name"): the list is a locked spec fixture holding
   `Sanjay`, the live copy must equal it, and adding a reviewer is a maintainer re-lock. An
   agent can prepare an entry but cannot mark it reviewed. Entries are added under
   `Spec-Skip: reviewed`, never from the judging samples or held-out data. (The list stops an
   agent from *widening* the reviewers; it cannot stop an agent typing the listed name. That
   last step rests on the human reading the diff, as the route requires.)
