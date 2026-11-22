# witness-rules-r2 — general statement-line rules, judged once on a frozen out-of-corpus sample

Follows [key-statement-witness](key-statement-witness.md) (retired). Approved by the maintainer
on 2026-11-09, on the condition that the rules stay general and are never tuned per filer.

**The defect.** The v2 suite build is blocked by 94 witness blocks (`data/eval/witness_blocks/`):
keys whose statement line the r1 rules (`configs/eval/v2/statement_lines.yaml`, locked by
key-statement-witness) either miss (`none`), select more than once with different values
(`ambiguous`), or select wrongly. Measured on 120 out-of-corpus 10-Ks (the dev sample, below)
against an element oracle, r1 finds the statement line for 90.1% of evaluable filing-families,
and 3.75% are ambiguous.

**The trap this spec avoids.** The first eval reached 97% in-loop by tuning on its own suite
and scored 75.2% out of loop. Editing rules until our own 94 corpus blocks clear would repeat
that one level down: an answer-key checker bent to approve our own keys. So:

- the rules are written from accounting vocabulary, us-gaap definitions and standard labels,
  never from one filer's wording, ticker, CIK or accession;
- they are developed on a seeded **dev sample** of filers outside our corpus;
- they are judged **once** on a second, disjoint **judging sample**, frozen before the work
  started, against acceptance marks written here first; only the judging aggregates are read;
- the corpus is measured once at the end, and what stays blocked gets a reviewed resolution,
  not another rule iteration.

## Requirement

Terms are those of key-statement-witness (key, primary statements, *the same*, concept). A
**filing-family** is one (filing, family) pair of a sample.

### 1. r2 is a rules-file change

r2 replaces the content of `configs/eval/v2/statement_lines.yaml`. The witness engine and
loader (`src/eval/keys/statement_witness.py`) are unchanged: every retired key-statement-witness
test still passes against its locked r1 copy, and the live file loads with
`load_statement_rules` (rule fields, defaults and validation of key-statement-witness
requirement 2). If a general rule cannot be written in that schema, the implementer raises a
dispute; the schema does not grow inside this change.

The live rules:

- cover the same 51 families as the r1 copy;
- name no filer: outside `#` comments, no run of four or more digits (a year, an amount, a CIK
  or an accession), and every `substitutes` concept is in `us-gaap`, `ifrs-full`, `srt` or
  `dei` (an `ext:` concept is one filer's extension);
- still select every hand-checked line of key-statement-witness C2 (that spec's
  `HAND_CHECKED` table, now run against the live file as well as the locked r1 copy).

The mechanical checks above do not prove generality. The maintainer reviews every pattern of
the r2 diff against the rule "accounting vocabulary, not one filer's wording", and the
implementation report lists each new or changed pattern with the us-gaap element or standard
label it comes from.

### 2. The judging oracle (`load_oracle`)

The oracle is the locked file `test/spec/witness-rules-r2/fixtures/oracle.yaml`. Per judged
family it fixes:

- `elements`: the us-gaap local names whose taxonomy definition *is* the family's measure (not
  a component, not a broader total);
- `statements`, `parenthetical`: where an oracle line may sit, copied from r1's rule for the
  family;
- `period`: `instant` or `duration`;
- `unit`: the companyfacts unit (`USD`, `USD/shares`, `shares`).

The oracle depends on nothing in the rules under test. (The working scorer in
`data/eval/witness_rules_r2/measure.py` took `statements`, `parenthetical` and the period kind
from the rules being measured, so a rules file could shrink its own evaluable set. For r1 the
values are identical, so r1's measured numbers stand.)

48 families are judged: the 51 rule families minus `Debt` and `DeferredRevenue` (definitions
open, spec key-definitions) and `EffectiveTaxRate` (rarely a statement line).
`load_oracle(path)` returns family → `{"elements", "statements", "parenthetical", "period",
"unit"}`. It raises `ValueError` on a `version` other than 1, an unknown or missing field, an
empty `elements`, an element that is not a bare local name (it holds a `:`), a statement code
outside the primary statements, a `period` other than `instant`/`duration`, or a non-boolean
`parenthetical`.

### 3. Scoring one filing (`score_filing`)

`score_filing(tables, rules, oracle, adsh)` returns family → outcome for every oracle family,
for one filing of `tables`:

1. `end` is the filing's `sub.period` as an ISO date. For a `duration` family, `start` is `end`
   minus 364 days; for an `instant` family, `start` is None.
2. **Oracle lines**: the filing's statement lines (`statement_lines`) whose `stmt` is in the
   oracle family's `statements`, that are not parenthetical unless `parenthetical` is true,
   that are not custom (`version != adsh`), whose `version` starts with `us-gaap/`, and whose
   `tag` is in `elements`. Each one's value is `period_value(tables, adsh, tag, start, end,
   unit, version)`; a `ValueError` from it counts as None. The **oracle values** are the set
   of non-None values.
3. **Selection**: `select_lines(tables, rules[family], adsh, start, end, unit)`; a `ValueError`
   from it counts as no lines. The **witnessed value** is the sum of the lines' values when the
   rule's `combine` is `sum`; for `one`, the lines' common value when they are all the same,
   else None.
4. **Outcome**, first match:
   - `no_oracle`: no oracle values and no lines;
   - `no_oracle_selected`: no oracle values, some lines;
   - `oracle_ambiguous`: more than one oracle value (the filing is not evaluable);
   - `none`: one oracle value v\*, no lines;
   - `ambiguous`: lines, witnessed value None;
   - `hit`: witnessed value the same as v\*;
   - `wrong`: otherwise.

**Evaluable** outcomes are `hit`, `none`, `ambiguous`, `wrong`. Which filing-families are
evaluable, and their v\*, depends only on the tables and the oracle, never on the rules.

### 4. Aggregates (`aggregate`)

`aggregate(outcomes)` takes `(family, outcome)` pairs and returns a `Score`:

```python
{"families": {family: {outcome: int for each of the 7 outcomes}},   # families seen
 "totals": {outcome: int for each of the 7 outcomes},
 "evaluable": int,                       # hit + none + ambiguous + wrong
 "recall": hit / evaluable,              # None when evaluable is 0
 "precision": hit / (hit + wrong),       # None when hit + wrong is 0
 "ambiguity": ambiguous / evaluable}     # None when evaluable is 0
```

An unknown outcome raises `ValueError`.

### 5. The acceptance marks (`acceptance`)

Pre-registered on 2026-11-09, before any r2 rule was written. `acceptance(r1, r2)` compares two
`Score`s of the same sample and oracle, and returns
`{"accept": bool, "failed": [codes, sorted], "regressed_families": int}`. r2 is accepted when
none of these fails (comparisons in exact integer arithmetic):

| Code | r2 passes when |
|---|---|
| `precision` | `hit * 1000 >= 992 * (hit + wrong)`, and `hit + wrong > 0` |
| `recall` | `hit * 1000 >= 930 * evaluable`, and `evaluable > 0` |
| `ambiguity` | `ambiguous * 1000 <= 25 * evaluable` |
| `family_regression` | no family with `n >= 10` evaluable loses more than 0.05 recall **and** more than one filing against r1: with `loss = r1 hit - r2 hit`, a family regresses when `loss > 1` and `loss * 100 > 5 * n` |

`regressed_families` counts the families that regress. The verdict names no family: the
judging sample's per-family results are not to be read. `acceptance` raises `ValueError` when
the two scores' family sets differ or a family's evaluable count differs (they then do not come
from the same sample and oracle).

Rationale, recorded with the marks: r1 on the judging sample is recall 0.8843, precision
0.9950, ambiguity 0.0414. Precision may barely move: a `wrong` result is a witness approving a
wrong key, the silent-wrong failure, so 0.992 allows about 2.5 standard errors of noise at
~3,300 decided cases. Recall must gain about 4.6 points (dev has 368 misses; this is roughly
halving them). The per-family rule protects small families from being traded for large ones;
the "more than one filing" clause keeps a single filing from failing a 10-filing family.

### 6. The judging record (`judge_record`)

`judge_record(sample_dir, rules_path, r1_rules_path, oracle_path, draw_sha256)`:

1. checks that `draw_sha256` has exactly the keys `sub`, `pre`, `num`, `tag` and that each
   `<sample_dir>/<table>.tsv` has that SHA-256; otherwise raises `ValueError` (a missing file
   raises `FileNotFoundError`);
2. loads the tables, both rules files and the oracle; scores every filing of `sub` with each
   rules file;
3. returns

```python
{"spec": "witness-rules-r2",
 "draw_sha256": {...},                    # as checked
 "oracle_sha256": str, "rules_sha256": str, "r1_rules_sha256": str,   # file SHA-256s
 "r1": aggregate(r1 outcomes), "r2": aggregate(r2 outcomes),
 "verdict": acceptance(r1, r2)}
```

### 7. The one judging read and its record

When every other test passes, the judging measurement runs **once**:
`judge_record(<judging sample>, configs/eval/v2/statement_lines.yaml,
test/spec/key-statement-witness/fixtures/statement_lines.yaml,
test/spec/witness-rules-r2/fixtures/oracle.yaml, <pinned draw hashes>)`, written as JSON to
`configs/eval/v2/statement_lines.acceptance.json`. The implementing agent does not run it and
does not read the judging sample; the orchestrator runs it and reports the totals and verdict
only. The committed record must:

- pin the judging draw (hashes in Notes), the locked oracle, the r1 copy
  (`661bed5dc7d7d458e0402cb4d8e043f0979f474bfe4d4a113a625b0aa2eff295`) and the live rules
  (its `rules_sha256` is the live file's, and differs from r1's);
- be internally consistent: each `Score` equals `aggregate` over its own family counts, and the
  verdict equals `acceptance(r1, r2)`;
- show the pre-registered sample: 3,603 evaluable filing-families for both r1 and r2, and r1's
  recall 0.8843, precision 0.9950 and ambiguity 0.0414 (to 4 decimals), which shows the engine
  and oracle still reproduce the baseline;
- accept r2.

**If r2 is rejected,** the record is not committed and the judging sample is spent: r2 goes
back to the dev sample, and a second judging read needs a fresh draw, disjoint from both
samples, with its hashes pinned here by a re-lock before it is read. The rejected verdict's
totals are reported to the maintainer.

### 8. After acceptance (not in this change)

The corpus is measured once with the accepted r2 (`scripts/eval/build_v2_suite.py` witness
pass). What stays blocked gets a reviewed resolution. The live resolutions are shrink-only
under key-statement-witness requirement 3, so adding them needs its own spec change; that is
a follow-up, not part of this one.

## Acceptance criteria

- **C1**: `load_oracle` returns the locked oracle: 48 families (the 51 r1 families minus
  `Debt`, `DeferredRevenue`, `EffectiveTaxRate`), each family's `statements` and
  `parenthetical` equal to r1's, `period` instant exactly when r1's first statement is `BS`,
  the per-share and share-count units of requirement 2; malformed oracles are refused.
  Oracle: the r1 locked copy and requirement 2.
- **C2**: `score_filing` gives the hand-derived outcome for each family of four synthetic
  filings (hit, none, ambiguous, wrong, oracle_ambiguous, no_oracle, no_oracle_selected; custom,
  wrong-statement and prior-period oracle lines excluded); the evaluable set and v\* do not
  change when the rules change. Oracle: hand derivation from the fixture rows (table in Notes).
- **C3**: `aggregate` gives the hand-counted totals and ratios (None on empty denominators),
  is invariant under reordering, and refuses an unknown outcome. Oracle: hand count; the
  ratio definitions of requirement 4.
- **C4**: `acceptance` applies the marks exactly at their boundaries (precision 992/1000 passes,
  991/1000 fails; recall 930/1000 passes, 929/1000 fails; ambiguity 25/1000 passes, 26/1000
  fails; a family of 10 losing one filing passes and two fail; a family of 9 is ignored; a
  family of 40 losing two passes and three fail), names no family, refuses mismatched scores,
  and is monotone (turning an r2 `none` into a `hit` never turns accept into reject). Oracle:
  the table of requirement 5, by hand.
- **C5**: the live rules cover the r1 families, load, and pass the identifier check; the
  check flags a CIK, an accession, a four-digit run and an `ext:` substitute; the live rules
  select every key-statement-witness hand-checked line (in that spec's C2 file). Oracle:
  requirement 1.
- **C6**: `judge_record` over the synthetic sample gives the hand-derived record (r1 recall
  1/2, precision 3/4; r2 recall 5/6, precision 1; verdict reject on `ambiguity` and `recall`);
  it refuses a wrong or incomplete hash map; with r2 = r1 the two scores are equal. Oracle:
  hand derivation (Notes).
- **C7**: the committed record `configs/eval/v2/statement_lines.acceptance.json` meets
  requirement 7. Oracle: the pinned hashes and pre-registered r1 baseline in Notes, and
  requirement 5.

## Interface

All in `src/eval/keys/witness_measure.py` (new):

- `OracleFamily`, `Score`, `Verdict` (`TypedDict`s as above)
- `load_oracle(path: str | os.PathLike[str]) -> dict[str, OracleFamily]`
- `score_filing(tables: FsdsTables, rules: Mapping[str, StatementRule], oracle: Mapping[str, OracleFamily], adsh: str) -> dict[str, str]`
- `aggregate(outcomes: Iterable[tuple[str, str]]) -> Score`
- `acceptance(r1: Score, r2: Score) -> Verdict`
- `rule_identifier_violations(path: str | os.PathLike[str]) -> list[str]`
- `judge_record(sample_dir: str | os.PathLike[str], rules_path: str | os.PathLike[str], r1_rules_path: str | os.PathLike[str], oracle_path: str | os.PathLike[str], draw_sha256: Mapping[str, str]) -> dict[str, Any]`

Files the implementation changes or adds: `configs/eval/v2/statement_lines.yaml` (r2),
`configs/eval/v2/statement_lines.acceptance.json` (requirement 7, last). Work may be split:
`impl/witness-rules-r2--part1` (everything but the record; C7 fails with an assertion), then
`impl/witness-rules-r2` adds the record.

## Notes

**Samples** (`data/eval/witness_rules_r2/`, gitignored; drawn by `sample.py`, state in its
`README.md`). Population: original 10-Ks (`prevrpt` 0, `fp` FY) filed 2023q1–2025q4 by US
large-accelerated or accelerated filers, one per CIK (the latest), consolidated total assets
in [$1B, $25B], minus 140 CIKs (corpus, `xbrl_heldout`, lexicon, waves, the sealed A' list, the
S&P sample). 2,930 candidates, 1,841 in the band. Order `sha256(seed + cik)`, seed
`witness-rules-r2/2026-11-09`: the first 120 are the dev sample, the next 120 the judging
sample, disjoint by CIK.

Pinned judging draw (`out/judge/`), SHA-256:

| Table | SHA-256 |
|---|---|
| `sub.tsv` | `24d067a9946c0d93a0da244996d05623b5b0c7fa056fed77a3c708fae3d00fe4` |
| `pre.tsv` | `288cf49d1ec5fb140bf2249acc89a7b9e9923317f88e14f1c4350f9433ba3185` |
| `num.tsv` | `0c4046a158c58751da492d719c0571eec9808d0d3420c93a0c1d4fcf0898fca8` |
| `tag.tsv` | `e230c280c076c9112428fd0d2da79af68f79d53c0aed27b8a082cc2af8c01a4f` |

`out/draw.json` (both accession lists): `197b50fd8e31954f8056b19cf5fe4b4f35da32816222c53f716f2ff4ffdd1c7c`.

**r1 baseline**, measured 2026-11-09 before the marks were set (`measure.py`; its
rule-derived statements equal the oracle's for r1):

| Sample | Evaluable | Recall | Precision | Ambiguity |
|---|---|---|---|---|
| dev | 3,709 | 0.9008 | 0.9935 | 0.0375 |
| judging (aggregate only) | 3,603 | 0.8843 | 0.9950 | 0.0414 |

C7 pins the judging row as a provenance check (same sample, same oracle, unchanged engine),
not as an expected result of the code under change.

**Leak, recorded.** While the baseline was measured, a too-loose grep printed two judging
per-family rows: `TotalAssets` (120 evaluable, recall 0.967) and `TotalLiabilities` (97,
0.959). No rule was written from them; both families are already near the top, and the
per-family rule of requirement 5 still applies to them.

**Synthetic sample** (`test/spec/witness-rules-r2/fixtures/synthetic/`): four constructed
filings (period 2024-12-31, duration start 2024-01-02), three families with the synthetic
rules `rules_r1.yaml` / `rules_r2.yaml` and oracle `oracle_synthetic.yaml`. Hand-derived
outcomes (r1 rules; r2 differs only where shown):

| Filing | TotalAssets | Revenue | NetIncome |
|---|---|---|---|
| S1 | hit (`Total assets`, 1,000) | hit (`Total revenues`, 500) | hit (`Net income`, 50) |
| S2 | none: v\* 2,000 on `Assets, total` (r2: hit) | ambiguous: two custom revenue lines 300 / 200; v\* 500 on `Total net sales` | wrong: `Net income including noncontrolling interest` (`ProfitLoss`) 60 vs v\* 55 (r2: hit, `Attributable to parent`) |
| S3 | no_oracle: no balance-sheet line | oracle_ambiguous: `Revenues` 500 and `RevenueFromContract…` 480 | no_oracle_selected: custom `Net income` 70 |
| S4 | no_oracle_selected: custom-version `Assets` 900 | no_oracle_selected: `Revenues` 500 on CF only, custom IS `Revenue` 500 | no_oracle: `NetIncomeLoss` only for 2023 |

r1 totals: hit 3, none 1, ambiguous 1, wrong 1, oracle_ambiguous 1, no_oracle 2,
no_oracle_selected 3; evaluable 6, recall 1/2, precision 3/4, ambiguity 1/6. r2 totals: hit 5,
none 0, ambiguous 1, wrong 0; recall 5/6, precision 1, ambiguity 1/6. Verdict:
`{"accept": false, "failed": ["ambiguity", "recall"], "regressed_families": 0}`.
