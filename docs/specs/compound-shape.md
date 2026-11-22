# compound-shape — generated compound questions: a top-N screen with a dependent ask

The v2 plan (§3.4) defines a **compound** question as "a screen with a dependent compute or
narrative". The pre-registration counts compound as one of the three line-1 kinds the bar is
tested on (§3, "Kinds, by suite shape"), and records a known gap (§6, Amendment 1 item 2): the
suite generator has no compound shape, and **compound cases must exist in H-F before the
look**. H-F is built by the same generator (`scripts/eval/build_v2_suite.py`) pointed at the
quarantined database, so the shape has to be generated, keyed and graded, with no hand-written
answers.

Hand-written compound cases already exist in H-X ([hx-authoring](hx-authoring.md),
requirement 5), and the grader already passes a compound only when every part passes
([v2-grading](v2-grading.md), requirement 14: `EvalCase.parts`, `_grade_compound`). This change
makes the v2 generator emit the same two-part structure from SEC keys.

Decisions (maintainer, 2026-11-10):

- **Additive files.** Compound cases go into their own dev and validation files. The frozen
  `xbrl_v2_dev.json` / `xbrl_v2_validation.json` (Amendment 1, by hash) stay byte-identical;
  the compound files' hashes are recorded in Amendment 2. H-F gets compound cases from the same
  builder.
- **Two dependent asks**, both on a ranking "the top `n` filers by metric A in fiscal year Y
  among universe U":
  - `rank_by_ratio`: which of those `n` has the highest S4 ratio R ("Of the three largest
    covered companies by revenue in fiscal 2024, which had the highest operating margin?");
  - `leader_growth`: the year-over-year growth of metric B for the top-ranked filer ("...and
    by how much did the largest one's net income grow from fiscal 2023?").
- **Size.** About 90 dev + validation cases (15 intents per ask × 3 phrasings); about 50 in
  H-F, set when H-F is generated (E4).

Policies: the ranking follows S2, S3, S5 and S6 exactly as `screen.top` does; the dependent
ask follows S1 (one filing for a comparison) and S4 (ratio and growth definitions) exactly as
`compute.ratio` and `compute.yoy_growth` do
([XbrlRag_Eval_Semantics.md](../design/XbrlRag_Eval_Semantics.md)).

## Requirement

### 1. Intent: the `compound` kind

`Intent` gains the kind `compound`, with the operations `rank_by_ratio` and `leader_growth`,
using only the existing nine fields. Its shapes are `compound.rank_by_ratio` and
`compound.leader_growth` (`intent_shape` gives `kind.operation`, as for `compute` and
`screen`).

| Field | `rank_by_ratio` | `leader_growth` |
|---|---|---|
| tickers | — (empty) | — (empty) |
| fiscal_years | exactly 1 (the ranking year) | exactly 2, consecutive; the ranking year is the later |
| metrics | exactly 2: `[A, R]`, A the ranking metric, R an S4 ratio id among `RATIO_DEFINITIONS`' values (`operating_margin`, `net_margin`, `rd_intensity`, `current_ratio`) | exactly 2, distinct: `[A, B]`, A the ranking metric, B the growth metric |
| dimension, filing_year | — | — |
| operation | required | required |
| universe | required, non-empty | required, non-empty |
| n | int ≥ 2 | int ≥ 2 |

Any violation raises `ValueError`, as for the other kinds. `intent_id` is unchanged (the same
nine keys, the same hash), so **every existing intent keeps its id**; a compound intent's id
differs from every non-compound intent's because its `kind` differs.

### 2. Placeholders and phrasebook

No new placeholder. `{metric}` is A, `{metric_2}` is R or B, `{universe}` and `{n}` as for
screens, `{company}` is never used (company form `none`, as for screens).

| Shape | Fillable | Required |
|---|---|---|
| `compound.rank_by_ratio` | metric, metric_2, period, universe, n | all fillable |
| `compound.leader_growth` | metric, metric_2, first_period, last_period, universe, n | metric, metric_2, last_period, universe, n |

For `leader_growth`, `{last_period}` is the ranking year and `{first_period}` the growth base
year (as for `compute.yoy_growth`). The ratio ids are rendered from the lexicon's `metrics`
section like any metric: `configs/eval/v2/lexicon.yaml` gains an entry for each of the four
ratio ids (label, and optional synonyms and shorthand).

Compound templates live in their own file, `configs/eval/v2/phrasebook_compound.yaml`, loaded
by the existing `load_phrasebook` and validated by the same rules. It holds at least three
templates per compound shape, and the templates of each shape use all three registers
(`terse`, `conversational`, `formal`). The main `phrasebook.yaml` is not changed.

### 3. Candidates

For each universe U (sorted), each (Y, A) pair that the screens enumerate for U (a metric A
some member of U tags in year Y), with `n = 3`:

- `rank_by_ratio`: one intent per ratio id R;
- `leader_growth`: one intent per metric B ≠ A that some member of U tags in both Y−1 and Y.

Each compound shape's candidates are ordered by `order_candidates` (the hash round-robin over
universes) and keyed in that order until the shape's quota of keyed intents is met; a dropped
intent is counted by reason, as for the other shapes.

### 4. Keys

**Ranking part.** Keyed exactly as the `screen.top` intent `(U, Y, A, n)` would be: the same
members, the same exclusions with their reasons, the same witness admission, the same
ordering (value descending, ties by ticker), the same `rows[:n]` and the same
`disclosures`. A ranking that the screen would drop drops the compound intent with the same
reason. With fewer than `n` ranked filers the compound intent is dropped as
`too_few_ranked_for_n` (the dependent ask would be over fewer filers than the question names).

**Dependent part, `rank_by_ratio`.** For each of the `n` ranked filers, the ratio R in year Y
keyed exactly as the `compute.ratio` intent `(ticker, Y, numerator, denominator)` of R's
definition would be: the same inputs, the same S4 definition, the same drop reasons. Then:

- if any of the `n` filers has no ratio key, the intent is dropped as
  `dependent:<that filer's drop reason>` (no filer is silently left out of the ask); when several
  have none, the first of them in ranking order gives the reason. A filer whose inventory does
  not hold both of R's inputs in year Y has the reason `metric_not_tagged` (as a screen member
  without the metric);
- if the numerator and denominator of a filer's ratio are in different units, the intent is
  dropped as `dependent:unit_mismatch` (S6);
- the **winner** is the filer with the largest ratio; if the two largest ratios are equal at 3
  significant figures (the ratio precision of S6), the intent is dropped as `dependent_tie`.

The dependent key is `{ticker: winner, value: winner's ratio}`, with the definition id R; all
`n` ratios and their inputs are kept in the key provenance.

**Dependent part, `leader_growth`.** The leader is the first ranked row. If the first two
ranked values are equal, the intent is dropped as `leader_tie`. The growth of B for the leader
from Y−1 to Y is keyed exactly as the `compute.yoy_growth` intent `(leader, [Y−1, Y], B)`
would be (same-filing rule S1, growth definition S4). A not-meaningful growth (prior ≤ 0) drops
the intent as `dependent_not_meaningful`; any other drop of that key drops the intent as
`dependent:<reason>`.

**Status and provenance.** `key_status` is `verified` only when every input of both parts is
`verified`; otherwise `single-source`. `policies` is the union of both parts' policies plus S3,
S4 and S5. The record's `key.inputs` lists the ranking's inputs (all ranked members', as the
`screen.top` key's), then the dependent's: for `rank_by_ratio` the `n` filers' ratio inputs in
ranking order, each numerator before its denominator; for `leader_growth` the growth key's
inputs, year Y−1 before year Y.

**Witness blocks.** With a statement witness, a compound candidate whose keying meets a key
input, of the ranking or of the dependent part, that the witness blocks (a blocking status,
or accepted candidates that disagree: what makes `select_key` raise `WitnessBlocked`) and
that no reviewed resolution covers is dropped as `witness_blocked`, whatever other drop
reason its keying also meets. It is counted like any other drop, and the shape's keying
moves on to the next candidate in `order_candidates` order (requirement 3), so the quota is
still met when enough candidates remain. Such a block never makes the compound build raise
and never reaches the witness's report: when the main build and the compound build share one
witness (the build script passes the same instance to both), keying compound candidates adds
nothing to what `raise_if_blocked()` reports, whether the main build ran before or runs
after; a key the compound build met blocked still blocks the main build when the main build
keys it, exactly as on a fresh witness. A blocked key has no witness record, so
`key_independence` counts it nowhere, as in the main build. Reviewed resolutions apply to
compound keys exactly as in the main build: a key resolved `key` is used; a key resolved
`drop` is not a block: a ranking member resolved `drop` is excluded with the reason
`resolved_drop` (as the `screen.top` key excludes it), and a dependent key resolved `drop`
drops the intent as `dependent:resolved_drop`. (Amended 2026-11-10 before any compound case
was built; see Notes.)

### 5. Cases

Each phrased compound case is an `EvalCase` that:

- carries the ranking at the top level exactly as a `screen.top` case does (`kind "screen"`,
  `expected_rows` = the ranking rows, `top_n = n`, `order_sensitive` true, `unit "USD"`), and
  its record carries the ranking's `disclosures`;
- carries `parts = [{"part": "ranking", ...}, {"part": "dependent", ...}]` in that order;
  - the ranking part holds the ranking fields above plus the `disclosures`;
  - the `rank_by_ratio` dependent part holds the winner as `ticker` and its ratio as
    `expected_value`, and **no** disclosure data (its universe is the ranking's rows, already
    disclosed by the ranking part);
  - the `leader_growth` dependent part is a scalar compute: `kind "compute"`,
    `expected_value` = the growth, `alt_values = [growth × 100]`, the leader as `ticker`;
- is tagged `split:<split>`, `intent:<id>`, `family:<register>`, `template:<id>`,
  `key_source:<...>`, `key_status:<...>`, `shape:compound.<op>` and `compound`, in that order;
- has `origin "v2:compound.<op>"` and `paraphrase_group` = the intent id.

### 6. Grading

A generated compound case is graded by the existing compound grader: the dispatch routes the
shapes `compound.rank_by_ratio` and `compound.leader_growth` to `compound`, so the case passes
only when both parts pass, with the per-part checks reported (`"ranking"`, `"dependent"`,
`"ranking.<check>"`, `"dependent.<check>"`).

- The ranking part is graded as a v2 ranked screen in context (rows, universe stated,
  exclusions named, period ends when required).
- The `rank_by_ratio` dependent part passes when the answer names the winner as the filer
  with the highest R **and** states the winner's ratio within the ratio precision (3
  significant figures), in decimal or percent form (`0.301` or `30.1%`). Naming another filer
  as the highest fails the part even if the winner's ratio appears somewhere in the answer. A
  part with no disclosure data runs no universe or exclusion check.

  **Which filer the answer names as highest.** A superlative is one of `highest`, `largest`,
  `greatest`, `best`, `top`, `leads`, `led` (case-insensitive, whole word). The **pick
  sentence** is the first sentence of the answer that contains a superlative **and** names
  the ratio R (its lexicon label, a synonym or a shorthand, case-insensitive); when no
  sentence does, it is the **last** sentence that contains a superlative. (The ranking is
  usually restated first in the question's own words, "the three largest ... by revenue",
  so the first superlative sentence alone would read the ranking as the pick; amended
  2026-11-10 before any compound case was built.) In the pick sentence, the named filer is the one of the `n`
  ranked filers (by ticker or the lexicon's short or legal name) whose mention ends nearest
  before the superlative, or, when none is named before it, the first named after it. The
  part's `"dependent.pick"` check is true when that filer is the winner; its
  `"dependent.value"` check is true when the winner's ratio appears in the answer at the ratio
  precision. The part passes when both are true. No superlative sentence, or no ranked filer
  named in the pick sentence, makes `"dependent.pick"` false. The ratio's surface forms come
  from the lexicon at build time and travel with the part, as the filers' names do.
- The `leader_growth` dependent part is graded as a scalar compute in context (value within
  the growth precision of 0.1 percentage point, percent form accepted).
- **Precision per part.** Each part is graded at its own S6 precision, whatever the case's
  top-level `rel_tol`: the ranking's amounts at the keys' `decimals` (as a `screen.top` case),
  the ratio to 3 significant figures, the growth to 0.1 percentage point. A part therefore
  carries what the precision rules need (its shape or metric kind and its keys' `decimals`),
  rather than falling back to `rel_tol`.

### 7. Building and writing

`build_compound_suites` takes the same inputs as `build_v2_suites` (with `quotas` over the two
compound shapes) and returns `{"dev", "validation", "manifest", "intents"}`:

- the suites are named `xbrl_v2_dev_compound` and `xbrl_v2_validation_compound` (and each
  case's `suite` field likewise), with `version`, `split`, `n_cases`, `content_hash` and the
  sorted cases, as in `build_v2_suites`;
- phrasing and the split use `build_phrased_cases` with the given `per_intent`, `share` and
  `salt`, so a compound case is `validation` when its intent's axis or its template's axis
  says so;
- the manifest has the same keys as the main manifest (content hashes, case counts by shape
  and key status, quota / selected / candidates / dropped / dropped-by-shape, disagreements,
  phrasing, `intents_hash`, and with a witness `key_independence`, `key_definitions`,
  `key_resolutions`);
- a witness block that no reviewed resolution covers never raises: the candidate is dropped
  as `witness_blocked` and counted in `dropped` and `dropped_by_shape` (requirement 4,
  "Witness blocks"). The main build is unchanged: it still raises `WitnessBlocked` on its own
  unresolved blocks.

`write_compound_suites(build, out_dir)` writes `xbrl_v2_dev_compound.json`,
`xbrl_v2_validation_compound.json`, `xbrl_v2_compound_manifest.json` and
`intents_compound.json` in the main writer's format.

`scripts/eval/build_v2_suite.py` gains `--compound N` (an int ≥ 0, default 0) and
`--compound-phrasebook` (default `configs/eval/v2/phrasebook_compound.yaml`). With `N > 0` it
also builds and writes the compound suites into the same `--out-dir`, splitting `N` over the
two shapes as evenly as possible (`rank_by_ratio` takes the odd one). With `N = 0` its outputs
are exactly what they are today.

### 8. Nothing else moves

With the new code and the changed `lexicon.yaml`, `build_v2_suites` on the same inputs returns
the same result as before this change, byte for byte once written: the same candidates,
selected intents, ids, cases, hashes and manifest. No compound shape enters `SHAPE_CLASSES`,
`GENERATED_SHAPES` or `shape_quotas`. The dev sample (`dev_sample.py`) is unchanged.

## Acceptance criteria

- **C1**: `Intent("compound", ...)` validates per the requirement 1 table: each valid example
  constructs, each single-field violation raises `ValueError`, and `intent_shape` gives
  `compound.rank_by_ratio` / `compound.leader_growth`. `intent_id` of a compound intent follows
  the eval-intents C3 formula, and the ids of the eval-intents fixture's non-compound intents
  are unchanged. Oracle: the table and the C3 rule, computed in the test with `hashlib` and
  `json`.
- **C2**: `load_phrasebook` accepts compound templates that respect the requirement 2 table and
  rejects one that uses `{company}`, one missing a required placeholder, and one using a
  placeholder its shape cannot fill; `render` fills a compound intent's `{metric}` (A),
  `{metric_2}` (R or B, including a ratio id from the lexicon), `{period}` /
  `{first_period}` / `{last_period}`, `{universe}` and `{n}` by exact substitution. The
  committed `configs/eval/v2/phrasebook_compound.yaml` loads, has at least three templates per
  compound shape, covers the three registers per shape, and every ratio id has a lexicon
  label. Oracle: the table, and the expected question text written by hand in the test.
- **C3**: The ranking part of a keyed compound intent equals the keyed `screen.top` intent
  `(U, Y, A, n)` on the same inputs: rows, exclusions with reasons, disclosures, and drop
  reason when the screen drops; fewer than `n` ranked filers drops it as
  `too_few_ranked_for_n`. Oracle: a hand-made inventory and companyfacts fixture whose ranking
  is computed by hand in the test, plus the metamorphic comparison with the `screen.top` key.
- **C4**: `rank_by_ratio`: the winner and its ratio equal the hand-computed S4 ratios of the
  `n` ranked filers (to 1e-12 relative); each filer's ratio equals the `compute.ratio` key of
  that filer; a missing ratio input drops the intent as `dependent:<reason>`; mixed units drop
  it as `dependent:unit_mismatch`; two largest ratios equal at 3 significant figures drop it as
  `dependent_tie`; reordering the universe's members or the fixture's filer order never changes
  the winner. Oracle: hand arithmetic on the fixture values.
- **C5**: `leader_growth`: the leader is the first ranked row; the growth equals
  `(B_Y − B_{Y−1}) / |B_{Y−1}|` computed by hand from the fixture under S1 (both values from
  the later filing where it reports both) and equals the `compute.yoy_growth` key of the leader;
  prior ≤ 0 drops as `dependent_not_meaningful`; a tie between the first two ranked values
  drops as `leader_tie`. Oracle: hand arithmetic on the fixture values.
- **C6**: A compound `EvalCase` and its record carry the requirement 5 fields: top-level
  ranking fields, `parts` in order with the stated fields, no disclosure data in the
  `rank_by_ratio` dependent part, the tags including `shape:compound.<op>` and `compound`, the
  `origin`, `paraphrase_group`, `key.status` (verified only when every input is), `key.inputs`
  order and `policies` ⊇ {S3, S4, S5}. Oracle: the fixture's hand-keyed values.
- **C7**: Grading: a correct answer to each compound shape is `CORRECT`; an answer with the
  right ranking and a wrong dependent value, or the right ranking and the dependent naming a
  different filer as highest (with the winner's ratio present elsewhere), is not `CORRECT` and
  reports `checks["dependent"]` false; an answer right on the dependent but missing the
  universe statement fails `ranking.universe`; percent and decimal forms of the ratio and the
  growth both pass. Oracle: hand-written answers against the hand-keyed fixture cases.
- **C8**: `build_compound_suites` and `write_compound_suites`: suite names, file names,
  manifest keys, per-shape quotas met in `order_candidates` order with drops counted by reason;
  the split agrees with `axis_is_validation` on the intent and template axes; the build is
  deterministic (two builds identical); adding a candidate filer to the inventory (in a new
  universe) never changes the case records of an intent kept in both builds. Oracle: the
  spec's rules applied by hand to the fixture, and the eval-intents split formula computed in
  the test.
- **C9**: Nothing else moves: on the fixture, `build_v2_suites` returns the same result with the
  compound-era lexicon (ratio ids added) as with the lexicon without them, and
  `GENERATED_SHAPES`, `SHAPE_CLASSES` and `shape_quotas` are unchanged and share no shape with
  `COMPOUND_SHAPES`; a compound build over the same sources leaves the main build unchanged; the
  compound files written into the main files' directory leave the four main files' bytes
  untouched. Oracle: the metamorphic comparison of the two builds and the v2-suite-build spec's
  shape list. (The build script's `--compound` flags need the database and the FSDS cache, so
  the script itself is not spec-tested; the library-level checks above stand in for its
  `--compound 0` rule.)
- **C10**: Witness blocks (requirement 4, "Witness blocks"; requirement 7). With a real
  `KeyWitness` over a synthetic FSDS fixture in which DEVB's two 10-Ks present no net income
  line (its `NetIncomeLoss` keys are `note_unlisted`, blocked) and every other key agrees with
  its statement line: the `leader_growth` candidates that meet a DEVB net income key (as the
  leader's growth metric, or as a ranking member) are dropped as `witness_blocked` and counted
  in `dropped` and `dropped_by_shape`, the others are kept with their hand-computed growth,
  and any quota is met in `order_candidates` order; the build does not raise, and
  `raise_if_blocked()` on the witness reports nothing afterwards; a main build sharing the
  witness passes before and after the compound build when it has no block of its own, and,
  run after the compound build, raises the same `WitnessBlocked` as on a fresh witness when it
  has; with DEVB's keys resolved `key`, every candidate is kept and DEVB's values are used;
  resolved `drop`, the ranking excludes DEVB as `resolved_drop` and DEVB's growth drops the
  intent as `dependent:resolved_drop`; `key_independence` counts no blocking status. Oracle:
  hand arithmetic on the fixture values, requirement 4 applied by hand to the six
  candidates, and the metamorphic comparison with a fresh witness.

## Interface

```python
# src/eval/suites/intents.py  (existing functions extended; no new public function)
#   Intent(kind="compound", operation="rank_by_ratio" | "leader_growth", ...)
#   intent_shape(Intent) -> "compound.rank_by_ratio" | "compound.leader_growth"

# src/eval/suites/v2_suite.py
COMPOUND_SHAPES: tuple[str, ...]   # ("compound.rank_by_ratio", "compound.leader_growth")

def build_compound_suites(inventory: Mapping[str, Any], universes: Mapping[str, Sequence[str]],
                          sec: SecSource, fact_store: FactStoreSource, phrasebook: Phrasebook,
                          lexicon: Lexicon, *, quotas: Mapping[str, int], per_intent: int,
                          share: float, salt: str,
                          witness: KeyWitness | None = None) -> dict[str, Any]: ...

def write_compound_suites(build: Mapping[str, Any], out_dir: str | Path) -> dict[str, Path]: ...
```

Grading needs no new public function; the dispatch and the compound part grader are extended
(requirement 6). `COMPOUND_SHAPES` is a module constant, which a spec branch cannot stub (only
new functions can be); C9 tests it through the module's attribute.

## Notes

- **Oracles.** Synthetic inventories and companyfacts payloads committed under
  `test/spec/compound-shape/`, in the shape the v2-suite-build spec fixtures use; every
  expected value is hand arithmetic in the test. No `data/`, no network, no database.
- **Retired specs this touches.** `eval-intents` (kind table, `_FILL`), `v2-suite-build`
  (keyer), `v2-grading` (dispatch, compound parts) and `hx-authoring` (the H-X compound case
  format the generated cases follow) are retired; their tests must keep passing
  (`pytest test/spec`). The committed main `phrasebook.yaml` is not changed, so eval-intents
  C12 is unaffected.
- **Witness blocks (amended 2026-11-10 before any compound case was built).** A $0 dry build
  found 21 compound key inputs (mostly `leader_growth`'s growth metric B: intangible assets,
  share-based compensation, interest expense, ...) that the statement witness blocks and no
  reviewed resolution covers; with requirement 7's original sentence the whole build failed.
  The pre-registration (docs/eval/heldout_v2/preregistration.md, K1) lets a blocked key be
  resolved by a reviewed entry or left out of the suite. A compound shape has about 1,000
  candidates per ask for 15 slots, and H-F is generated blind, so no human may review
  held-out keys: compound candidates meeting an unresolved block are left out automatically
  (`witness_blocked`). The main build still raises on its own blocks. C10's fixture is under
  `test/spec/compound-shape/witness/` (synthetic FSDS rows and rule, note and resolution
  lists, consistent with the conftest's companyfacts table).
- **H-F.** No separate code: E4 runs the build script with `--compound` against
  `xbrl_heldout` after E1.
- **Out of scope.** A narrative dependent (H-X carries it), `screen.bottom` rankings, more
  than two parts, and the pipeline's handling of compound questions (phase 5, D3).
