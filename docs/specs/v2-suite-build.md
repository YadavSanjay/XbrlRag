# v2-suite-build — the v2 dev and validation suites, built from intents and independent keys

Phase 1 of the [v2 plan](../design/XbrlRag_v2_Plan.md) (§8) exits when the "dev and
validation suites [are] built and hashed". This change builds them: it enumerates intents
from a corpus inventory (§3.4), keys each one from the SEC's companyfacts through the
`sec-keys` builders (§3.3), cross-checks every key against the fact store, phrases the keyed
intents with the `eval-intents` renderer and split (§3.1, §3.2), and writes two hashed,
diffable suite files and a manifest. Keys and expectations follow
[Evaluation semantics](../design/XbrlRag_Eval_Semantics.md) S1–S6.

Everything new lives in `src/eval/suites/v2_suite.py`. The SEC payloads come through the
`SecSource` seam (`src/eval/keys/base.py`); the fact-store values through a new
`FactStoreSource` seam; the corpus inventory and the universes are plain data. The builder
never queries a database, never calls the pipeline's calculator and never reads
`mv_derived_metrics`.

## Requirement

### Inputs

1. **Inventory** (`Mapping[str, Mapping]`): ticker (upper case) →
   `{"cik": str, "years": {fiscal_year (int): {metric_id: {"concept": "<taxonomy>:<tag>",
   "unit": str}}}}`. It says which filer-years the corpus holds and, per filer-year, which
   canonical metrics (the lexicon's ids, e.g. `Revenue`) the filer tags with which concept
   and unit: the coverage-card entry a key cites (S5). The CLI builds it from the fact store;
   tests pass it as data.
2. **Universes** (`Mapping[str, Sequence[str]]`): universe id → member tickers (S3). The
   builder invents none; the CLI passes `covered` (every inventory ticker) and the named
   groups. A member need not be in the inventory.
3. **`FactStoreSource`** (an `ABC` in `v2_suite.py`) with one abstract method
   `value(cik, concept, unit, period_start, period_end, accession) -> AnswerKey | None`: the
   value that filing (`accession`) tagged for that concept, unit and period in the fact store,
   as an `AnswerKey` with `key_source "fact_store"`, or `None` when the store has none.
4. **`SecSource`** (from `sec-keys`): `company_facts(cik)` only. Frames are not used: every
   intent here names fiscal years (S2).

### Shapes, classes and quotas

5. The builder generates 12 of the 14 intent shapes, in three classes of the plan's dev mix
   (§3.4: single-filer numeric 35, cross-filer 15, cross-period 10; the other classes have no
   generator yet). Classes and their shapes, **in this order**:
   - `single_filer` (35): `lookup`, `compute.ratio`;
   - `cross_filer` (15): `screen.top`, `screen.bottom`, `screen.sum`, `screen.median`,
     `peer_relative`;
   - `cross_period` (10): `compute.yoy_growth`, `compute.difference`, `compute.cagr`,
     `trend`, `point_in_time`.

   `dimensional` and `breakdown` need `html_hand` keys (a later change) and are not generated.
6. **`shape_quotas(total)`** returns `{shape: quota}` for the 12 shapes. Class quotas are
   the largest-remainder apportionment of `total` by the weights 35/15/10: each class gets
   `total * w // 60`, and the units left over go one each to the classes with the largest
   `total * w % 60`, ties to the earlier class. A class quota `q` over its `k` shapes gives
   each shape `q // k`, and the first `q % k` shapes (in the order above) one more. `total`
   must be an `int >= 0`, else `ValueError`.

### Enumerating intents

7. **`enumerate_intents(inventory, universes)`** returns `{shape: [Intent, ...]}` for the 12
   shapes (every shape a key, possibly with an empty list), each list sorted by `intent_id`
   and free of duplicates. "T holds Y" means `Y` is in `inventory[T]["years"]`; "m is
   consistent over years Ys" means `m` is in each of those years' metrics with the same
   `concept` and `unit`. For every ticker `T`:
   - `lookup`: each `(Y, m)` with `m` in `T`'s year-`Y` metrics;
   - `compute.ratio`: each `Y` and each pair `(m1, m2)` of `RATIO_DEFINITIONS` (below) whose
     two metrics are both in year `Y`'s metrics with the same `unit`; `metrics = (m1, m2)`;
   - `compute.yoy_growth`: each `Y` with `T` holding `Y - 1` and `Y`, and `m` consistent over
     both; `fiscal_years = (Y - 1, Y)`;
   - `compute.difference`: each `Y1 < Y2` both held, `m` consistent over both;
   - `compute.cagr`: each `Y1 < Y2` both held with `Y2 - Y1 >= 2`, `m` consistent over both;
   - `trend`: each `Y` with `T` holding `Y`, `Y + 1`, `Y + 2`, and `m` consistent over the
     three; `fiscal_years = (Y, Y + 1, Y + 2)`;
   - `point_in_time`: each `Y < F` both held with `F - Y <= 2`, `m` consistent over both;
     `fiscal_years = (Y,)`, `filing_year = F`;
   - `peer_relative`: each universe `u` listing `T`, each `(Y, m)` with `m` in year `Y`'s
     metrics; `universe = u`.

   For every universe `u`, each `(Y, m)` such that at least one member of `u` in the
   inventory has `m` in year `Y`: one `screen` intent per operation, `n = 3` for `top` and
   `bottom` and `None` for `sum` and `median`.

   `RATIO_DEFINITIONS` (S4; `(numerator, denominator)` → definition id):
   `(OperatingIncome, Revenue)` → `operating_margin`, `(NetIncome, Revenue)` →
   `net_margin`, `(ResearchAndDevelopment, Revenue)` → `rd_intensity`,
   `(CurrentAssets, CurrentLiabilities)` → `current_ratio`.
8. **`order_candidates(intents)`** returns the order in which a shape's candidates are tried.
   An intent's **stratum** is its ticker, or its universe when it has no ticker; its
   **digest** is `sha1(intent_id(intent).encode("utf-8")).hexdigest()`. Within a stratum,
   intents are ranked `0, 1, ...` by digest; the result is sorted by `(rank, digest)`: a
   round-robin over the strata, so no filer dominates a shape. It depends only on the set
   of intents; a duplicate intent raises `ValueError`.

### Keying an intent

9. **`key_intent(intent, inventory, universes, sec, fact_store)`** returns a `KeyedIntent` or raises
   `IntentDroppedError(reason)` (an `Exception`; `reason` is `args[0]`). Notation, for ticker
   `T`, metric `m` and year `Y`: `c, u` are the inventory's concept and unit of `m` in year
   `Y` (multi-year shapes use one concept and unit, rule 7), `cf` is
   `sec.company_facts(inventory[T]["cik"])`, and `K(m, Y) = fiscal_year_key(cf, c, u, Y)`
   (S1 latest-filed over the filer's own fiscal year, S2). A builder error is a drop:
   `KeyNotFoundError` → `"no_key"`, `UnitMismatchError` → `"unit_mismatch"`,
   `KeyAmbiguousError` → `"ambiguous"`. Values per shape:
   - `lookup`: `K(m, Y).value`.
   - `point_in_time` (`Y`, filing year `F`): the **filing labelled F** is, among the entries
     of `c` in unit `u` with `form` in `{10-K, 10-K/A, 20-F, 20-F/A, 40-F, 40-F/A}`,
     `fp == "FY"` and `fy == F`, the accession with the smallest `filed` (then the smallest
     `accn`): the original annual report. The value is `as_reported_key(cf, c, u, start, end,
     accession=that)` over `K(m, Y)`'s period (S1 "as originally reported" in that filing).
     No such filing, or one that does not report the period: `"no_key"`.
   - **One filing for a comparison** (`compute.yoy_growth`, `compute.difference`,
     `compute.cagr`, `trend`; S1): let `Yn` be the latest year and `A` the accession of
     `K(m, Yn)`. If `A` reports every other year's period (each period from `K(m, Y)`), all
     values are `A`'s (`as_reported_key(..., accession=A)`) and `same_filing` is `True`;
     otherwise every value is its own `K(m, Y)` and `same_filing` is `False`. With `v1` the
     first year's value and `v2` the last's:
     - `compute.yoy_growth`: `(v2 - v1) / |v1|`; **not meaningful** when `v1 <= 0`
       (S4), the value still `(v2 - v1) / |v1|`, or `None` when `v1 == 0`;
     - `compute.difference`: `v2 - v1`;
     - `compute.cagr` (`n = Y2 - Y1`): `(v2 / v1) ** (1 / n) - 1` when `v1 > 0` and `v2 > 0`;
       otherwise not meaningful with value `None`;
     - `trend`: rows `{"entity": f"FY{Y}", "fiscal_year": Y, "value": v, "period_end": e}`
       in ascending year.
   - `compute.ratio` (`m1`, `m2`, `Y`): `K(m1, Y).value / K(m2, Y).value`, definition id from
     `RATIO_DEFINITIONS`; a zero denominator drops with `"zero_denominator"`.
   - `screen.*` and `peer_relative` over universe `U` (S3, S5, S6), each member `t` in
     ascending ticker order is **excluded** with the first failing reason: `"not_in_corpus"`
     (not in the inventory), `"period_not_filed"` (does not hold `Y`), `"metric_not_tagged"`
     (no `m` in year `Y`), `"currency"` (its unit for `m` is not `"USD"`; screens rank in
     USD and never convert), or the key error's drop reason; otherwise it is **ranked** with
     `K(m, Y)`. Ranking: value descending, ties by ticker ascending (`screen.bottom`: value
     ascending, ties by ticker ascending). Rows are `{"ticker", "value", "fiscal_year",
     "period_end"}`. Fewer than 2 ranked filers drops with `"too_few_ranked"`.
     - `screen.top` / `screen.bottom`: the first `n` rows;
     - `screen.sum`: the sum of the ranked values; `screen.median`: their median (the mean
       of the middle two for an even count);
     - `peer_relative` (subject `T`): every ranked row; the subject not ranked drops with
       `"subject_not_ranked"`; `subject` is `{"ticker": T, "rank": r (1 = highest),
       "universe_median": median of the ranked values, "position": "above" | "below" |
       "at"}` (the subject's value against that median).
     - `disclosures` (S2, S3): `{"universe": u, "members": len(U), "ranked": [tickers in row
       order], "excluded": [{"ticker", "reason"}] in ticker order, "period_ends": {ticker:
       period_end} of the ranked, "disclose_period_ends": bool}`, the last `True` when the
       ranked period ends are more than 92 days apart (S2's "more than three months").
   - A scalar value exactly `0` that is not "not meaningful" drops with `"zero_value"` (a
     zero target makes any answer pass).
10. **Cross-check** (§3.3, `sec-keys` requirement 9): every SEC key the intent used is passed
    to `fact_store.value(key["cik"], key["concept"], key["unit"], key["period_start"],
    key["period_end"], key["accession"])`; a returned key goes through `cross_check(key,
    other)`. The `KeyedIntent`'s `inputs` are the resulting keys (verified ones carry
    `decimals` from the store), in the order the values were used (ascending year; screens
    in row order; a ratio numerator first); `disagreements` lists every `Disagreement`.
    The SEC value is never replaced. `key_status` is `"verified"` when every input is
    verified, else `"single-source"`; `key_source` is the inputs' `key_source` values,
    sorted, unique, joined with `+` (here always `"sec_companyfacts"`).
11. **`KeyedIntent`** (a `TypedDict`) has exactly: `intent`, `shape`, `value` (`float |
    None`), `rows` (`list[dict] | None`), `not_meaningful` (`bool`), `definition` (`str |
    None`: `growth`, `difference`, `cagr`, a ratio id, else `None`), `same_filing` (`bool |
    None`, `None` outside the comparison shapes), `inputs`, `disagreements`, `key_status`,
    `key_source`, `policies` (sorted, unique: the inputs' policies, plus `S4` for every
    `compute.*` shape, plus `S3` and `S5` for screens and `peer_relative`), `disclosures`
    (`dict | None`), `subject` (`dict | None`). `value` is set for `lookup`,
    `point_in_time`, `compute.*`, `screen.sum` and `screen.median`; `rows` for `trend`,
    `screen.top`, `screen.bottom` and `peer_relative`; the other is `None`.

### Building the suites

12. **`build_v2_suites(inventory, universes, sec, fact_store, phrasebook, lexicon, *,
    quotas, per_intent, share, salt)`**:
    1. `quotas` maps shapes to quotas (`shape_quotas(total)` in the CLI); a shape outside
       the 12 raises `ValueError`; a missing shape has quota 0.
    2. Per shape, it walks `order_candidates(enumerate_intents(...)[shape])`, keying each
       intent, until `quota` intents are keyed or the candidates run out. A dropped intent
       is counted under its shape and reason and never emitted.
    3. The keyed intents go to `build_phrased_cases(intents, phrasebook, lexicon,
       per_intent=per_intent, share=share, salt=salt)` (eval-intents): their split is that
       builder's, unchanged.
    4. Each phrased case becomes one `EvalCase` (rule 13) and one case record (rule 14).
    5. `sec.company_facts` is called at most once per CIK.

    It returns `{"dev": suite, "validation": suite, "manifest": manifest, "intents":
    intents}` (rule 15).
13. **`EvalCase`** per phrased case: `id = case_id`, `question`, `suite =
    f"xbrl_v2_{split}"`, `kind` (`lookup`, `point_in_time` → `lookup`; `compute.*`,
    `screen.sum`, `screen.median` → `compute`; `screen.top`, `screen.bottom`,
    `peer_relative` → `screen`; `trend` → `breakdown`), `expected_value` and
    `expected_rows` from the keyed intent, `top_n = n` for top/bottom (else `None`),
    `order_sensitive = False` for `trend` only, `expect_not_meaningful`, `alt_values =
    [value * 100]` for `compute.yoy_growth`, `compute.cagr` and `compute.ratio` with a
    value (else `[]`), `origin = f"v2:{shape}"`, `paraphrase_group = intent_id`, `ticker`
    and `cik` (`int`) of a single-filer intent (`None` for screens), `fy =` the intent's
    last fiscal year, `concept` and `unit` of the intent's first metric for `lookup`,
    `point_in_time`, `compute.difference` and `trend` (`unit = "USD"`, `concept = None` for
    screens and `peer_relative`; both `None` for the dimensionless `compute.yoy_growth`,
    `compute.cagr`, `compute.ratio`), and `tags`, exactly and in this order:
    `split:<split>`, `intent:<intent_id>`, `family:<register>`, `template:<template_id>`,
    `key_source:<key_source>`, `key_status:<key_status>`, `shape:<shape>` (the
    `run_columns` conventions). The other fields keep their defaults.
14. **Case record**: `EvalCase.to_dict()` plus `"key": {"status", "source", "policies",
    "definition", "same_filing", "inputs", "disagreements"}`, and, when not `None`,
    `"disclosures"` and `"subject"`. `EvalSuite.load` reads a record as the `EvalCase`
    (extra keys ignored).
15. **Suite and manifest**. `canonical_json(obj)` is `json.dumps(obj, sort_keys=True,
    separators=(",", ":"), ensure_ascii=False, allow_nan=False)`; `content_hash(obj)` is
    `"sha256:" + sha256(canonical_json(obj).encode("utf-8")).hexdigest()`. A suite is
    `{"name": f"xbrl_v2_{split}", "version": "1", "split": split, "n_cases": n,
    "content_hash": content_hash(cases), "cases": [records sorted by id]}`. The manifest:
    - `"content_hashes": {"dev": ..., "validation": ...}`;
    - `"cases": {split: {"total": n, "by_shape": {shape: n}, "by_key_status": {status: n}}}`;
    - `"intents": {"quota": {shape: q} for the 12 shapes, "selected": {shape: n},
      "candidates": {shape: n}, "dropped": {reason: n}, "dropped_by_shape": {shape: {reason:
      n}}}` (a shape with no drops is absent from `dropped_by_shape`);
    - `"disagreements": [every Disagreement of a selected intent, once per intent]`;
    - `"phrasing": {"per_intent": per_intent, "share": share, "salt": salt}`;
    - `"intents_hash": content_hash(intents)`.

    `intents` is the **intent list**: one object per selected (keyed) intent with exactly
    `Intent`'s nine canonical fields `kind`, `tickers` (list), `fiscal_years` (list),
    `metrics` (list), `dimension`, `operation`, `universe`, `n`, `filing_year` (the object
    `intent_id` hashes, eval-intents C3), sorted by `intent_id`. With the manifest's
    `phrasing`, it is enough to rebuild the phrased cases with `build_phrased_cases` (the
    format the eval-paraphrases import reads).

    Nothing in a suite, the intent list or the manifest depends on the clock, the input order of the
    inventory, the universes or their members, or the process.
16. **`write_v2_suites(build, out_dir)`** writes `xbrl_v2_dev.json`,
    `xbrl_v2_validation.json`, `xbrl_v2_manifest.json` and `intents.json` (the intent list)
    into `out_dir`, each `json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False,
    allow_nan=False) + "\n"` as UTF-8 with `\n` line ends, and returns `{"dev": path,
    "validation": path, "manifest": path, "intents": path}`.

## Acceptance criteria

Fixtures (all synthetic, made-up CIKs `0000900101`–`0000900105`): companyfacts payloads
for a September filer `AAA` (three 10-Ks recasting prior years, an instant metric whose
FY2022 balance the FY2024 10-K does not report), a December filer `BBB` (a late 10-K/A of
FY2023 filed after the FY2024 10-K), a June filer `CCC` (revenue only in EUR, no net
income), a 20-F filer `TWX` (TWD) and a EUR filer `EEE` used only to extend a universe.
The phrasebook and lexicon fixtures are synthetic YAML next to the tests. Fakes sit only at the two seams: a `FakeSecSource`
and a `FakeFactStore` that serves each fixture entry's value by accession with `decimals
-6`, plus listed overrides. Every expected value is read off the fixture by hand in the test.
Values compare with `pytest.approx(rel=1e-12)`: they are arithmetic on the source's
numbers, so any larger difference is a bug, not rounding.

- **C1**: `shape_quotas`. `total = 60` gives 18/17 (lookup/ratio), 3 per cross-filer shape,
  2 per cross-period shape; `total = 10` gives class quotas 6/2/2 and lookup 3, ratio 3,
  top 1, bottom 1, yoy 1, difference 1, the rest 0; `total = 0` gives all zeros; a negative
  or non-int total raises `ValueError`. Property: for any total the quotas sum to `total`,
  each class is within 1 of `total * w / 60`, and within a class the quotas are
  non-increasing in shape order and differ by at most 1. Oracle: requirement 6, by hand.
- **C2**: `enumerate_intents`. On a two-filer inventory the per-shape candidate lists are
  exactly the hand-listed intents (counts and members), sorted by `intent_id`; a metric
  whose concept changes between years yields no multi-year intent; a universe member absent
  from the inventory adds no candidate of its own. Metamorphic: adding a filer leaves every
  single-filer candidate of the others unchanged; permuting the inventory or universe order
  changes nothing. Oracle: requirement 7, listed by hand.
- **C3**: `order_candidates`. The order equals the rule computed in the test with `hashlib`;
  the first `k` intents (with `k` strata) come from `k` distinct strata. Metamorphic: any
  permutation of the input gives the same order; a duplicate raises `ValueError`. Oracle:
  requirement 8.
- **C4**: single-filer keys. `AAA` FY2022 revenue is the FY2024 10-K's recast `102e9`, not
  the original `100e9`; `point_in_time` FY2022 in the FY2023 10-K is `100e9`, in the FY2024
  10-K `102e9`; `BBB` FY2023 revenue is the 10-K/A's `206e9` while `point_in_time` FY2023
  in the FY2024 10-K is `205e9`; `TWX` revenue is `2_894_307_699_000` in `TWD`; the
  `net_margin` ratio of `AAA` FY2024 is `12e9 / 121e9` with definition `net_margin` and
  policies including `S4`. Metamorphic: appending a later 10-K that recasts the year to `v`
  makes the lookup `v` and leaves the FY-F point-in-time value unchanged. Oracle:
  requirement 9, the fixture.
- **C5**: comparisons. `AAA` revenue growth FY2023 is `(111 - 102) / 102` (one filing);
  `BBB` FY2024 is `(220 - 205) / 205` from the FY2024 10-K, not the 10-K/A's `206`; `AAA`
  net-income growth FY2023 (prior `-5e9`) is not meaningful with value `13 / 5`; net-income
  CAGR FY2022–FY2024 is not meaningful with value `None`; revenue CAGR is
  `(121 / 102) ** 0.5 - 1`; revenue difference FY2022–FY2024 is `19e9`; the total-assets
  trend is `301e9, 321e9, 350e9` with `same_filing False`. Metamorphic: scaling every value
  of the metric by `k > 0` leaves growth and CAGR unchanged and scales the difference and
  the trend rows by `k`. Oracle: requirement 9, S1, S4.
- **C6**: screens and peer-relative. FY2024 revenue top over `covered` ranks `BBB` (220e9)
  then `AAA` (121e9), excludes `CCC` (`unit_mismatch`) and `TWX` (`currency`), and sets
  `disclose_period_ends` (94 days apart); FY2023 revenue does not (92 days); FY2024 net
  income `sum` is `37e9`, `median` `18.5e9`, with `CCC` `no_key` and `TWX`
  `metric_not_tagged`; FY2022 revenue drops `too_few_ranked`; `AAA` peer-relative FY2024
  revenue over `peers` has rank 2, median `170.5e9`, position `below`, and `ZZZ` excluded
  `not_in_corpus`. Metamorphic: permuting a universe's members changes nothing; adding a
  non-USD filer to the universe adds one `currency` exclusion and leaves the rows unchanged.
  Oracle: requirement 9, S2, S3, S6.
- **C7**: cross-check and key status. With the store agreeing, every input is `verified`
  with `verified_by ["fact_store"]` and `decimals -6`, and `key_status "verified"`; a store
  value that differs leaves the SEC value, `key_status "single-source"` and one
  `Disagreement` with reason `value`; a store with no value gives `single-source` and no
  disagreement; `fact_store.value` is called with each key's accession. Property: for any
  perturbed store value, the keyed value is the SEC one. Oracle: requirement 10.
- **C8**: drops. `key_intent` raises `IntentDroppedError` with `no_key`, `unit_mismatch`,
  `ambiguous`, `zero_value`, `zero_denominator`, `too_few_ranked` and `subject_not_ranked`
  on hand-made intents; in a build, dropped intents are never emitted, are counted under
  their shape and reason in the manifest, and the walk moves on to the next candidate, so a
  shape gets `min(quota, keyable candidates)` intents; with every quota above the candidate
  counts, `lookup` selects 14 of 16 (`CCC`: one `unit_mismatch`, one `no_key`) and
  `screen.top` 8 of 18 (10 `too_few_ranked`). Property: for any quotas, per shape
  `selected <= quota`, `selected + dropped <= candidates`, and `selected + dropped ==
  candidates` whenever `selected < quota`. Oracle: requirements 9 and 12, counted by hand.
- **C9**: case emission. Each emitted `EvalCase` has the seven tags in order, parsed by
  `slice_fields_from_tags` to the case's split, intent, register, template, key source and
  status; the kind mapping, `expected_value`/`expected_rows`, `top_n`, `order_sensitive`,
  `expect_not_meaningful`, `alt_values`, `ticker`, `cik`, `fy`, `concept` and `unit` of
  requirement 13 hold on hand-checked cases. Property: over builds with any `share` and
  `salt`, `run_split` of each suite's cases is that suite's split and every case's split
  is its phrased case's. Oracle: requirement 13, `run_columns`, `eval-intents`.
- **C10**: suite files. Each suite's `content_hash` equals `"sha256:" + sha256` of the
  canonical JSON of its cases, computed in the test with `hashlib` and `json`; the cases
  are sorted by id; the manifest's counts equal counts taken from the suites, and its
  quota, selected, candidates and dropped counts match the build, and it records
  `per_intent`, `share` and `salt`; the intent list holds exactly the nine canonical fields
  of each selected intent, sorted by intent id, one per distinct `intent:` tag in the
  suites, and `build_phrased_cases` over it with the recorded phrasing reproduces every
  case id; `write_v2_suites` writes the four files byte-for-byte as specified and
  `EvalSuite.load` round-trips each suite's cases. Metamorphic: two builds, or builds from a reordered inventory and
  universes, give byte-identical files. Oracle: requirements 14–16.

## Interface

New module `src/eval/suites/v2_suite.py` (stubbed in the spec PR; the stub `TypedDict`
lists its keys in its docstring and the implementer adds the annotations, `IntentDroppedError`
is an empty `Exception` subclass, and the implementer adds the module constants
`SHAPE_CLASSES` and `RATIO_DEFINITIONS`, which the stub lint does not admit):

- `class FactStoreSource(ABC)` with `@abstractmethod value(self, cik: str, concept: str, unit: str, period_start: str | None, period_end: str, accession: str) -> AnswerKey | None`;
- `class IntentDroppedError(Exception)`;
- `class KeyedIntent(TypedDict)`;
- `shape_quotas(total: int) -> dict[str, int]`;
- `enumerate_intents(inventory: Mapping[str, Any], universes: Mapping[str, Sequence[str]]) -> dict[str, list[Intent]]`;
- `order_candidates(intents: Sequence[Intent]) -> list[Intent]`;
- `key_intent(intent: Intent, inventory: Mapping[str, Any], universes: Mapping[str, Sequence[str]], sec: SecSource, fact_store: FactStoreSource) -> KeyedIntent`;
- `build_v2_suites(inventory: Mapping[str, Any], universes: Mapping[str, Sequence[str]], sec: SecSource, fact_store: FactStoreSource, phrasebook: Phrasebook, lexicon: Lexicon, *, quotas: Mapping[str, int], per_intent: int, share: float, salt: str) -> dict[str, Any]`;
- `canonical_json(obj: Any) -> str`;
- `content_hash(obj: Any) -> str`;
- `write_v2_suites(build: Mapping[str, Any], out_dir: str | Path) -> dict[str, Path]`.

Not in this change: the HTTP `SecSource` (`sec-live-source`), the Postgres
`FactStoreSource` and inventory reader, the CLI that runs the build against the corpus and
commits the suites, `html_hand` keys (dimensional, breakdown), calendar-year intents, the
average-balance ratios of S4 (ROE, ROA, ROIC), compound, narrative and honesty intents, and
disclosure grading (§3.5).

## Notes

- **Why the latest year's filing for a comparison.** S1 asks for one filing "where one
  filing reports all of them". The filing of the latest year's key is the one an analyst
  reads, and it is the only filing that can report the latest value. The fixture's `BBB`
  separates the readings: its FY2023 key comes from a 10-K/A filed after the FY2024 10-K,
  so the growth differs depending on whether the prior year is read from the FY2024 10-K
  (205) or keyed on its own (206).
- **All-or-nothing fallback.** When the latest filing misses one period, every value falls
  back to its own key instead of mixing bases; `same_filing` records which applied.
- **Screens rank USD only** (S6); a non-USD filer is a named exclusion, never converted.
  A per-share or pure metric would also be excluded as `currency`; screens over such
  metrics need their own rule in a later change.
- **Trend as `breakdown`.** `EvalCase` has no trend kind; a trend is one filer's set of
  values with no ranking, which is how `breakdown` grades (`order_sensitive False`).
- **The 92-day threshold** is S2's "more than three months": September 30 to December 31
  is 92 days and needs no disclosure; September 28 to December 31 (94) does.
- **Determinism.** Candidates are tried in a hash order, so the suite depends only on the
  inventory, universes, payloads and quotas. The drop counts cover the candidates tried,
  not every candidate; the manifest also records each shape's candidate count.
- **The intent list** (`intents.json`) is the format the eval-paraphrases import reads:
  the canonical intent objects plus the manifest's `phrasing` rebuild the phrased cases.
  This module does not import the paraphrase module.
- An intent whose `universe` is not in `universes` is a `ValueError` (a caller bug, not a
  drop).
- Cost: $0; no network, no database.
