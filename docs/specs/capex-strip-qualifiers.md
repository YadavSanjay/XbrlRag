# capex-strip-qualifiers — Capex reads the label's head, judged once on a fresh judge3 draw

Content route 2 of [statement-rules-by-element](statement-rules-by-element.md) (requirement 6,
"Qualifier stripping for judged families"; decisions 7 and 9) and K3 of the v2 status. The
protocol is [witness-rules-r2](witness-rules-r2.md)'s and
[witness-rules-r2-1](witness-rules-r2-1.md)'s: rules from accounting vocabulary and the
taxonomy, never from one filer; one judging read on a sample frozen and pinned here before it
is read; only its aggregates are read; marks written here first.

**The defect.** A Capex line whose label carries a negating qualifier is rejected for a term
the qualifier names: TSLA's "Purchases of property and equipment excluding finance leases, net
of sales" is its cash capex, and the accepted r2.1 rule rejects it on `\bleases?\b`
(witness-rules-r2-1 §1.3; the 2026-11-09 corpus measurement, TSLA FY2022 `rejected_line`).
statement-rules-by-element added `strip_qualifiers` (the patterns see `label_head(plabel)`).
This change turns it on for Capex, and makes the standard-label invariant read the same head
(its review point 9).

**Why only Capex.** It is the family the corpus motivation names. DebtIssued,
ResearchAndDevelopment, IntangibleAssets and the cash-flow totals spare their qualifiers with
per-pattern lookbehinds today; swapping those for stripping changes judged outcomes too and is
left to a later round (decision 2).

## Requirement

Terms are those of witness-rules-r2 (filing-family, outcomes, `Score`, `score_filing`,
`aggregate`), witness-rules-r2-1 (`acceptance_r2_1`, `standard_label_violations`,
`boundary_violations`, judge2) and statement-rules-by-element (`label_head`,
`line_is_candidate`).

### 1. The rule

The live `configs/eval/v2/statement_lines.yaml` `Capex` entry loads equal to the accepted r2.1
copy's `Capex` (`configs/eval/v2/statement_lines.accepted_r2_1.yaml`, SHA-256
`1f4612bc…3248`) plus `strip_qualifiers: true`. No pattern changes. Every other judged family
loads equal to the accepted r2.1 copy; `Debt` and `DeferredRevenue` are that copy's,
key-definitions' or debt-ifrs-elements' locked entries. No other family strips.

### 2. What stripping does to Capex (statement-rules-by-element requirements 3-4)

`include`, `require` and `exclude` see the head: a qualifier ("excluding finance leases", "net
of sales", "net of related payables", "excluding amounts in accounts payable") no longer
rejects a capex line, and a capex term that sits only inside a qualifier no longer admits a
non-capex line ("Principal payments on debt, excluding amortization of rental property debt").
A rejection the head itself carries stands ("… included in accounts payable", "Accrued …",
"Proceeds from sale of …", "… under finance leases"). `prefer` and `section` keep the full
label.

### 3. The standard-label invariant reads the head (`standard_label_violations`)

For a rule with `strip_qualifiers: True`, witness-rules-r2-1 requirement 1.1 is evaluated on
`label_head(label)`: an `exclude` pattern violates when it matches the head; a `require`
pattern when it does not match the head. A rule without the field is checked on the full label
exactly as today. The violation record still reports the full standard label. Signature
unchanged. `boundary_violations` is unchanged (it reads pattern syntax, not labels).

With requirement 1, both invariants hold on the live rules (48 oracle families; 49 families
for word boundaries).

### 4. The judging record function (`judge_record_strip`)

`judge_record_strip(sample_dir, rules_path, base_rules_path, oracle_path, draw_sha256)` is
`judge_record_r2_1` with `"spec": "capex-strip-qualifiers"`: it checks the draw exactly (keys
exactly `sub`, `pre`, `num`, `tag`, each file's SHA-256 equal, else `ValueError`; a missing
file `FileNotFoundError`), scores every filing of `sub` with both rules files and returns
`{"spec", "draw_sha256", "oracle_sha256", "rules_sha256", "base_rules_sha256", "base",
"candidate", "verdict"}` with `verdict = acceptance_r2_1(base, candidate)`.

The marks are `acceptance_r2_1`'s, unchanged and pre-registered there: precision
`hit * 1000 >= 992 * (hit + wrong)` and ambiguity `ambiguous * 1000 <= 25 * evaluable`
(absolute), `recall_regression` (candidate hits >= base hits), `family_regression` (no family
with n >= 10 loses more than one filing and more than 0.05 recall against the base).

### 5. The one judge3 read and its record

When every other test passes, the implementer copies the live rules byte for byte to
`configs/eval/v2/statement_lines.accepted_strip.yaml`. The orchestrator — **not** the
implementing agent, who never reads the judging sample — runs once:

```
judge_record_strip(<judge3 sample>,
                   configs/eval/v2/statement_lines.accepted_strip.yaml,
                   configs/eval/v2/statement_lines.accepted_r2_1.yaml,
                   test/spec/witness-rules-r2-1/fixtures/oracle.yaml,
                   <judge3 hashes, Notes>)
```

writes it as JSON to `configs/eval/v2/statement_lines.acceptance_strip.json`, and reports the
totals and verdict only. The committed record must:

- pin judge3 (Notes), the r2.1 oracle (`7cde3f08…bf9ca`), the accepted r2.1 copy as the base
  (`1f4612bc…3248`) and the judged copy (its SHA-256, different from the base's);
- have the live rules load equal to the judged copy in every family but `Debt` and
  `DeferredRevenue`, and the judged `Capex` strip;
- be internally consistent (each `Score` is `aggregate` of its own family counts; 48
  families; equal evaluable counts per family; the verdict is `acceptance_r2_1(base,
  candidate)`);
- accept.

**Before the read**, the maintainer runs the held-out check of Notes ("judge3 and the held-out
filers") and it prints `OK`. **If the candidate is rejected,** the record is not committed,
judge3 is spent, `Capex` goes back to the accepted r2.1 rule (the live file without
`strip_qualifiers`), and the totals are reported to the maintainer; another read needs a fresh
draw disjoint from dev, judge, judge2 and judge3, pinned by a re-lock before it is read.

### 6. After acceptance (not in this change)

The corpus is measured once with the accepted rules (the K6 dry rebuild's witness pass); TSLA's
capex keys (FY2022: the 2026-11-09 `rejected_line`) are then witnessed by rule, and the drafted
`definition` resolution for that line is not needed. What stays blocked keeps the
reviewed-resolution route.

## Acceptance criteria

- **C1** (the rule): the live `Capex` is the accepted r2.1 `Capex` plus `strip_qualifiers:
  true`; every other judged family is the accepted copy's; only `Capex` strips; "Capital
  expenditures, net of related payables" and "Purchases of property and equipment, excluding
  amounts in accounts payable" become candidates, "Principal payments on debt, excluding
  amortization of rental property debt" stops being one, "Accrued capital expenditures, net of
  payments" stays rejected. Property: for four heads the rule takes and five it rejects, any
  of six cues, six qualifier tails and four forms, the head alone decides. Oracle: the accepted
  r2.1 copy; requirement 2 by hand.
- **C2** (TSLA): the live `Capex` selects TSLA FY2025's line (report 9 line 20) alone:
  8,527M (2025), 11,342M (2024), 8,899M (2023), rel 1e-12; the accepted r2.1 rule selects
  nothing; relabelled to its head, both select it at 8,527M. Property: any of four cues and
  four tails after the head keep it at 8,527M. Oracle: TSLA's printed cash-flow statement.
- **C3** (invariant): `standard_label_violations` on "Intangible Assets, Net (Excluding
  Goodwill)": exclude `goodwill` violates without stripping and not with it; require
  `goodwill` the reverse; a head term (`\bnet\b`) violates either way. Property: for any of
  four heads, three cues, three tails, two forms and generated exclude/require terms, a
  stripping rule's violations are those of the head, sorted, each with the full label. The
  live rules have no label or boundary violation. Oracle: requirement 3, with heads known by
  construction.
- **C4** (record function): over witness-rules-r2's synthetic sample with the synthetic r2
  rules as base and the same rules stripping as candidate, the record names this spec, both
  scores are r2's (hit 5, ambiguous 1 of 6) and the verdict fails `ambiguity` only;
  metamorphic: with S2's ProfitLoss line relabelled "Net income, excluding noncontrolling
  interest", the candidate turns S2 NetIncome ambiguous (hit 4, ambiguous 2) and fails
  `ambiguity` and `recall_regression`; a wrong, incomplete or extended hash map raises
  `ValueError` and a missing table `FileNotFoundError`. Oracle: witness-rules-r2 Notes;
  `acceptance_r2_1`'s marks by hand.
- **C5** (the record): `configs/eval/v2/statement_lines.acceptance_strip.json` meets
  requirement 5. Oracle: the pinned hashes in Notes; `acceptance_r2_1`.

## Interface

- New stub: `src.eval.keys.witness_measure.judge_record_strip(sample_dir: str |
  os.PathLike[str], rules_path: str | os.PathLike[str], base_rules_path: str |
  os.PathLike[str], oracle_path: str | os.PathLike[str], draw_sha256: Mapping[str, str]) ->
  dict[str, Any]`.
- Changed behaviour, same signature: `src.eval.keys.witness_measure.standard_label_violations`
  (requirement 3).
- Files: `configs/eval/v2/statement_lines.yaml` (requirement 1); then
  `configs/eval/v2/statement_lines.accepted_strip.yaml` and
  `configs/eval/v2/statement_lines.acceptance_strip.json` (requirement 5, last); add both to
  `.gitattributes` as `-text` beside the r2.1 pair. Work may be split:
  `impl/capex-strip-qualifiers--part1` (everything but the copy and the record; C5 fails with
  an assertion), then `impl/capex-strip-qualifiers` adds them after the one read.

## Notes

### judge3 (drawn 2026-11-11, pinned here before any read)

From witness-rules-r2's population and seeded order (`sha256(seed + cik)`, seed
`witness-rules-r2/2026-11-09`; original 10-Ks, `fp` FY, US large-accelerated or accelerated
filers, 2023q1-2025q4, one per CIK, total assets in [$1B, $25B], minus the excluded CIKs):
**positions 360-479**, disjoint by CIK from dev (0-119), judge (120-239) and judge2
(240-359). The draw script (`sample_judge3.py`, kept with the sample) first checks that
positions 0-359 reproduce `data/eval/witness_rules_r2/out/draw.json`'s dev, judge and judge2
lists exactly (they do: 2,930 candidates, 1,841 in band, as on 2026-11-09), then extracts
positions 360-479 with `sample.py`'s own `extract`. It printed only counts (120 filings) and
the hashes below; no row of the tables was printed, opened or read by the spec author.

| Table | SHA-256 |
|---|---|
| `sub.tsv` | `d582e91a63539785cac828cbec470431f3c91f9bdc74f4ad10a2dfc710aa1c5c` |
| `pre.tsv` | `0ff29edfb3c5fcb2e51b8ed48d4f9eb2ca6a1d3478a9f5098a8d2cfb2110b84a` |
| `num.tsv` | `221a3c800db46b2568e253736330827e5d2481540e41907469ccd3c794dcae99` |
| `tag.tsv` | `87e616d50bbb5340d70ef1b68750c322275916a0c5ca6430957d547ddece5f15` |

`draw_judge3.json` (the 120 accessions and CIKs, in order):
`20b612f7c2b8a96498749a1b0d9863330baa0a38258857b9f80058bf757cc970`.

The tables and `draw_judge3.json` were written to the session scratchpad and are to be moved
by the orchestrator, unopened, to `data/eval/witness_rules_r2/out/judge3/` (gitignored, beside
judge2); the hashes are of the bytes, so the move does not change them.

### judge3 and the held-out filers

`sample.py`'s exclusions include the `xbrl_heldout` registry's CIKs; agents never connect to
that database, so the draw script used every other exclusion (the `xbrl_db` registry,
lexicon, wave lists, the sealed A′ list, the S&P sample). It found the same 140 excluded CIKs,
the same 2,930 candidates and the same order of positions 0-359 as the 2026-11-09 draw that
did read the held-out registry, so the held-out CIKs are already inside the other exclusions.
To confirm, the maintainer runs `check_judge3_heldout.py <draw_judge3.json>` (reads only the
CIK list and `select distinct cik from filing_registry` on `xbrl_heldout`, read-only) and it
must print `OK: 0 of 120 judge3 CIKs are held-out filers`. A `MISMATCH` voids the draw before
it is read.

### Development evidence (no judging sample read)

- **dev sample** (positions 0-119, the development sample of r2 and r2.1): accepted r2.1 and
  the candidate score identically (evaluable 3,714, hit 3,594, wrong 16, ambiguous 29; Capex
  93 hit, 2 ambiguous); no dev cash-flow capex label holds a cue.
- **label census** of every cash-flow line of the population outside all four samples (2,450
  filings, 104,734 lines, 40,741 distinct labels), label only: stripping makes 2 labels
  candidates ("Capital expenditures, net of related payables" ×2 filings, "Capital
  expenditures, net of construction payables and retention" ×1; both the cash paid for PP&E)
  and removes 1 ("Principal payments on debt, excluding normal amortization of rental property
  debt", a debt repayment the include took through "property" in the qualifier).
- So on judge3 the expected movement is about zero to two filing-families (review point 1).

### Fixtures

`test/spec/capex-strip-qualifiers/fixtures/fsds/2026q1/*.tsv`: byte copies of
statement-rules-by-element's TSLA rows (the `sub` row, every CF `pre` row, the consolidated
`num` rows of those lines, their `tag` rows); `provenance.json` holds their SHA-256s and the
filing's EDGAR and quarter-zip provenance. The synthetic sample, the r2.1 oracle and its us-gaap
2025 excerpt are read from `test/spec/witness-rules-r2-1/fixtures/`.

### Retired tests touched by this spec PR

Each keeps passing before and after the implementation:

- `test/spec/witness-rules-r2-1/test_c6_record.py`: the live `Capex` may be the accepted r2.1
  rule or that rule plus `strip_qualifiers: true` (the live-equals-accepted pin moves to this
  spec's C5 for the judged copy).
- `test/spec/statement-rules-by-element/test_c5_compatibility.py`: `Capex` may strip; the
  legacy comparison drops both new fields.
- `test/spec/statement-rules-by-element/test_c4_qualifiers.py`: the TSLA check starts from the
  live `Capex` without `strip_qualifiers`.
- `test/spec/statement-rules-by-element/test_c1_schema.py`: a live rule written with
  `strip_qualifiers: false` loads as the live rule without that key.

### Sequencing

This branch is cut on top of `spec/debt-ifrs-elements` (both edit the same retired tests).
Merge that spec PR first, then rebase this branch onto `main`; its diff is then this spec only.

### Decisions for the maintainer (recommendation first)

1. **Spend judge3 on a change that moves about one filing-family.** Recommended: yes, as
   statement-rules-by-element decision 7 requires for a judged family; the read is cheap ($0)
   and guards precision, and a pinned draw is what makes the result citable. Alternative:
   accept on the development evidence alone and keep judge3 for a larger round (e.g. the
   lookbehind removals of point 2), which saves the sample but breaks the "judged once on a
   fresh draw" rule for a judged family.
2. **Capex only.** Recommended; the lookbehind families (DebtIssued, ResearchAndDevelopment,
   IntangibleAssets, the three cash-flow totals) move to stripping in their own round, judged
   on a fresh draw (judge4) or together with another change.
3. **The invariant reads the head** for a stripping rule (requirement 3), so it checks what
   the engine applies; it can only report fewer violations than the full-label check for that
   rule, and none of the live rules changes verdict (C3).
4. **Base = the accepted r2.1 copy**, not the live file: the live file's judged families equal
   it today, and a pinned file keeps the base fixed while other content specs land.
