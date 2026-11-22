# grading-dont-care-and-ties — "don't care" members, declined aggregates, ties and date parts (grader version 7)

Follows [v2-grading](v2-grading.md), [peer-rank-grading](peer-rank-grading.md) and
[peer-rank-count-phrases](peer-rank-count-phrases.md) (all retired; the grader is at version 6),
and [key-fallback-witness](key-fallback-witness.md) (active), which defers these rules to this
spec. Decided by the maintainer on 2026-11-10:

- **K1 decision 3, option C.** A member excluded by a **reviewed `drop` resolution** is graded
  "don't care": an answer may rank it or leave it out, and either reading is accepted. The
  "with" reading uses the value the reviewed resolution records (SEC-sourced), never a number
  taken from the answer.
- **K1 decision 6.** Two grader fixes found in the K1 analysis: an aggregate is never credited
  (or penalised) because a declining answer lists a member whose value equals it; any order
  within a tie is accepted.
- **K2, folded in.** The grader read "December 31" as the produced value 31 and graded a refusal
  `INCORRECT` (MRK G&A, `int-3df803cba7fe-01c77e81`). Date parts are never produced values.

Sources: `data/eval/k1/k1_key_universe_2026-11-10.md` §2-§5 (side findings 1 and 2) and
`data/eval/k2/k2_key_adjudications_2026-11-10.md` C12 (3). Both are gitignored analysis notes;
the facts this spec relies on are restated here.

**The defects.**

1. A reviewed `drop` resolution (`configs/eval/v2/key_resolutions.yaml`, `reading: drop`)
   records that the filer's statement does not isolate the measure asked (WMT FY2024 interest
   expense on debt 2.259B without finance-lease interest; GOOGL FY2024 debt proceeds 13.589B
   including short-term debt; VST FY2023 comprehensive income mis-tagged). The key leaves the
   member out (`resolved_drop`), so an answer that includes the member's filed figure is graded
   wrong: a top-N swap, a peer rank one too high, a sum or median off by the member. The
   question is ambiguous for that filer, and neither reading is an error.
2. `grade_scalar` accepts any produced number that matches the key. An odd-count median is one
   member's value, so an answer that **declines** the median but lists the rows is graded
   `CORRECT` (dev `int-d599aa9e659a-02802349`: "the median ... is not available", with QCOM's
   4,121,000,000 listed; `int-a8e0f84b90f9-0bc7bca1`). The other way round, a **refused**
   answer that lists rows none of which is the aggregate is graded `INCORRECT`, and counts as
   silent-wrong when cited.
3. The key orders members by value and breaks a tie by ticker. That tie-break is arbitrary: two
   members with the same value have no order. Dev `int-722d72d7beb7-000ced2c` has COST and WMT
   both at 0 for third place in a top 3. Once COST is keyed (key-fallback-witness), the key
   lists COST, and an answer listing WMT third (and saying they tie) fails on set overlap.
4. `extract_numbers` / `extract_stated_numbers` read the day of a written date as an amount, so
   "the fiscal year ended December 31, 2024" yields 31.0. The MRK answer refused ("could not be
   determined ... No value is stated"), but its only produced value was 31.0, so it was graded
   `INCORRECT` (refused, cited: silent-wrong). Stored runs keep `numeric_values` as produced, so
   fixing the parser alone does not fix a $0 re-grade; the grader must read stored values
   through the same rule.

Nothing here loosens grading for a member the key can know: `no_fsds`, `fsds_not_published`,
`fallback_blocked` and every other exclusion stay strict, and an answer that silently drops
such a member still fails the S3 exclusions check.

## Requirement

Terms are those of v2-grading. A **v2 case** has `disclosures` a dict; its **ranked** members
are `disclosures["ranked"]` and its **exclusions** `disclosures["excluded"]` (dicts with
`ticker`, `reason`, and from this change an optional `value`). A value is **numeric** when it is
an `int` or `float`, not a `bool`, and finite. A grade is **value-correct** when
`CaseGrade.value_correct` is true. The **order rule** of a screen is value descending
(`screen.top`, H-X `screen_top`, `peer_relative`) or ascending (`screen.bottom`, H-X
`screen_bottom`).

### 1. Don't-care members (`dont_care_members`)

`dont_care_members(case) -> dict[str, float]` maps ticker to value for every exclusion of a v2
case whose `reason` is exactly `"resolved_drop"`, whose `ticker` is a non-empty string and whose
`value` is numeric, in the order of `excluded` (a repeated ticker keeps its first entry). Every
other case and exclusion contributes nothing; in particular these reasons are **never** don't
care: `no_fsds`, `fsds_not_published`, `fallback_blocked`, `presented_line_not_keyed`,
`value_recast`, `no_key`, `ambiguous_lines`, `witness_blocked`, `period_not_filed`,
`metric_not_tagged`, `currency`, `not_in_corpus`, `definition_excluded` and every
`substitute:<code>`. A `resolved_drop` exclusion without a numeric `value` is not don't care (it
is graded strictly, as today). An H-X case (`expected_exclusions`) has none. More than **six**
don't-care members raise `ValueError` (each adds a reading, requirement 3; a key that drops more
than six reviewed members is a key problem, not a grading one).

Why only `resolved_drop`: it is the only reason a human reviewed and decided that the filer's
statement does not answer the question. A `fallback_blocked` member is an *unreviewed* block
(key-fallback-witness requirement 2); once reviewed with `reading: drop` it becomes
`resolved_drop` and is covered. `presented_line_not_keyed` (ABT, TSM) is left strict here; see
decision 2.

### 2. The builder records the reviewed value (wiring, not locked)

The v2 builder (`src/eval/suites/v2_suite.py` `_cross_filer`) writes `"value"` on every
exclusion whose reason is `resolved_drop`: the value of the key the reviewed resolution dropped,
which is the resolution's own `value` (an entry covers exactly one key, value included). No
other exclusion gains a field. The frozen suites carry no such value today, so requirement 3
moves no stored grade until the suites are rebuilt (the K6 re-freeze). The rebuild diff must
show exactly the `resolved_drop` exclusions gaining `value`, each equal by hand to its
`key_resolutions.yaml` entry. This requirement has no locked test (the builder needs a full
witness environment); the re-freeze diff is its check.

### 3. Grading with don't-care members (readings)

Let D = `dont_care_members(case)`. When D is empty, requirement 3 changes nothing. Otherwise
the case has one **reading** per subset S of D: the key as it would be had the members of S been
ranked with their D values. Readings are ordered by size, then by the sorted tickers; the empty
subset (the key itself) comes first. The answer is graded against each reading by the sub-kind's
grader (with requirements 4, 5 and 7 applied); **the grade is the first value-correct one, else
the key's (S empty)**. Per sub-kind:

- **screen** (`screen.top`, `screen.bottom`, and a compound's screen part): the reading's
  expected rows are the first N members (N = `top_n`, else the number of expected rows) of the
  known members (requirement 5) plus S, ordered by the order rule with ties as in requirement 5.
  An added row is `{"ticker": t, "value": D[t]}`.
- **peer_relative**: the reading ranks `expected_rows` plus S by the order rule; the subject's
  accepted ranks are those requirement 5 gives in that ranking, and the number ranked is
  `len(ranked) + len(S)`.
- **sum**: the reading's value is `expected_value + sum(D[t] for t in S)`.
- **median**: the reading's value is the median (the middle value, or the mean of the two
  middle values) of the ranked members' values plus the D values of S. The ranked members'
  values are the `value`s of the first `len(ranked)` entries of `key["inputs"]`, used only when
  `key["inputs"]` has at least that many entries, every one numeric, and their median equals
  `expected_value` within relative 1e-9. Otherwise the case has only the key reading.

The **disclosure checks** of a case with don't-care members (v2-grading requirement 7) change
in two ways, in every reading: a don't-care ticker is not required to be named under
`"exclusions"` (the other exclusions still are), and `"universe"` also accepts the counts
`len(ranked) + k` for k = 1 ... `len(D)`.

Every other sub-kind (trend, CAGR, ratio, count, compound dependents) and every H-X case is
graded as today. The grade's `reason` names the reading when it is not the key's
(e.g. `"reading: with WMT"`), so a re-grade diff can count them.

### 4. A declined aggregate is not matched on a listed row

For the sub-kinds `sum` and `median` (v2 and H-X), when the answer is `refused` or `hedged`, a
produced value is **not read as the aggregate** when it matches a value of one of the answer's
own rows (`row_value` of each dict in `answer.rows`): it matches row value r when it would match
a key equal to r (the case's own rule, `expected_value` replaced by r, no alternate readings).
Such values are removed before `grade_scalar` grades the answer; when none remains, the answer is
`REFUSED` (it declined and listed members), never `INCORRECT`. An answer that neither refuses nor
hedges is graded as today, so "the median is 200 (DEVB)" with the rows listed stays `CORRECT`.
Rows themselves, and every other sub-kind, are unchanged.

### 5. Ties (any order within a tie)

Two ranked members **tie** when their key values are exactly equal (`==`). Exact, not the
grader's tolerance: the key orders members by their filed values and breaks only exact equality
by ticker, so exact equality is precisely the set of orders the key leaves arbitrary. Values that
merely agree within `rel_tol` (or at the key's stated precision) are different filed amounts with
a defined order, and accepting their swap would accept a wrong ranking.

- **Known members** of a screen: every expected row (ticker, value); for a v2 case also the
  ranked members past the cut, with values from `key["inputs"]`, when the inputs are
  **aligned**: `key["inputs"]` has at least `len(ranked)` entries, and for every expected row i,
  `ranked[i]` is its ticker and `key["inputs"][i]["value"] == ` its value. Ranked member j then
  has value `key["inputs"][j]["value"]`. When not aligned, only the expected rows are known.
- **Screens** (`screen` sub-kind, v2 and H-X): the expected rows are re-formed from the known
  members (and S, requirement 3) by the order rule, with members of equal value ordered **as the
  answer orders them**: those the answer's rows name first, in the answer's row order, then the
  rest by ticker ascending; then cut at N. A tie group that straddles the cut therefore lets the
  answer choose which of its members fill the last places, and a tie inside the top N (the top 1
  included) lets the answer order them either way. The re-formed rows keep each original row's
  other fields.
- **peer_relative** (v2): the subject's rank is right when it lies between 1 + (members strictly
  before it by value) and 1 + (members other than the subject before it or tied with it). With no
  tie this is the key's rank alone.
- H-X `rank` cases carry no member values and are graded as today.

### 6. Date components (`date_components`)

`date_components(text) -> list[float]` returns, in order of position, every component of every
**written date** in the text. Matching is case-insensitive.

- A **month word** is an English month name, its three-letter abbreviation, or `Sept`, optionally
  followed by `.`, not directly preceded or followed by a letter (before the `.`).
- A **day** is one or two digits with value 1-31, optionally followed by `st`, `nd`, `rd` or
  `th`, not directly preceded by a digit and not directly followed (after the suffix) by a letter
  or digit.
- A **year** is four digits, or `'` and two digits, not directly followed by a letter or digit.
- Forms, each component one entry (a year as written: `2024` → 2024.0, `'24` → 24.0):
  1. month word, spaces, day; optionally then `,` and/or spaces and a year
     (`December 31`, `Dec. 31, 2024`, `December 31st 2024`, `Sept 30, '24`) → day [, year];
  2. day, spaces, optionally `of` and spaces, month word; optionally then `,` and/or spaces and
     a year (`31 December 2024`, `the 31st of December, 2024`) → day [, year];
  3. `a/b/c`: a and b one or two digits (1-31), c two or four digits, not directly preceded by a
     digit, `/` or `.` and not directly followed by a digit or `/` (`12/31/2024`, `3/1/24`) →
     a, b, c.
- An ISO date (`2024-12-31`) is already blanked by the parser and is not repeated here.

**The parser skips them.** `extract_numbers` and `extract_stated_numbers` skip every number at
the position of a date component (as they skip ISO dates, years and form names); nothing else
they return changes. New runs therefore never produce a date part as a value.

### 7. Stored values are read through the same rule (`graded_values`)

`graded_values(text, values) -> list[float]`: for each distinct value v of `values`, let
n_P = its copies in `values`, n_D = its copies in `date_components(text)`, and n_A = its copies
in `[n.value for n in extract_stated_numbers(text)]` (the amounts the text states, date parts
skipped). When n_D > 0, at most n_A copies of v are kept: the earliest `n_P - n_A` copies
(if positive) are dropped. Values the text never states as a date part are all kept, and the
order of what remains is unchanged. Equality is exact (`==`). The rule only removes entries,
never removes a copy the text also states as an amount, and is idempotent (a second pass
changes nothing). A stored copy the text explains neither as an amount nor otherwise is dropped
with the date-part copies, which is safe: every arm's values are read from its own text (the
JSON arms' object is part of the text).

`grade_v2` and `grade_legacy` (so every `grade_case`) grade the answer with `numeric_values`
replaced by `graded_values(answer.text, answer.numeric_values)`. Rows are unchanged. The
grade's `produced` may record either list (the rule is idempotent, so a re-grade gives the same
grade).

### 8. Grader version

These rules move grades, so `grader_version()` becomes `"7"`, and the history in
`src/eval/core/graders/honesty.py` gains:
`7 — reviewed-drop members graded "don't care", a declined aggregate never matched on a listed
row, any order within a tie, date parts never produced values.`

### 9. Retired version pin

peer-rank-count-phrases C4 pinned `"6"` exactly. Following that spec's decision 2(b), it is
changed in this spec PR to "at least 6" (with a comment naming this change); this spec's C8 pins
`"7"` exactly. The other retired pins already read "at least 5" and hold at 7.

## Acceptance criteria

Cases are hand-built v2 records (fictional tickers `DEVA` ... `DEVX`, round amounts) in the
shape v2-suite-build emits; answers are hand-built `SystemAnswer`s with a citation, so only the
rule under test decides. The MRK and COST/WMT texts are shortened from stored dev answers. No
test reads `data/`, a database, the network or the clock; nothing in `src` is faked.

- **C1**: `dont_care_members` (requirement 1). `{"DEVW": 450e6}` for one `resolved_drop` with a
  value beside a `no_fsds`, a `fallback_blocked`, a `fsds_not_published` and a
  `presented_line_not_keyed` exclusion; a value of 0 counts; no value, a string, `True` or `NaN`
  do not; an H-X case and a case without disclosures give `{}`; seven members raise
  `ValueError`. Property: for random exclusion lists, the result is exactly the `resolved_drop`
  entries with numeric values. Oracle: requirement 1, hand-read.
- **C2**: don't-care screens (requirement 3, screen). Ranked DEVA 500, DEVB 400, DEVC 300, DEVD
  200 (top 3), DEVW don't care at 450: an answer `[DEVA, DEVW, DEVB]` and an answer `[DEVA, DEVB,
  DEVC]` that never names DEVW are both `CORRECT`; `[DEVW, DEVA, DEVB]`, a wrong DEVW value
  (300), and a strict exclusion (`no_fsds` DEVN at 450) ranked in are `INCORRECT`; the universe
  count `len(ranked)+1` is accepted. Bottom screens likewise. Property: for random distinct
  values, the top N of the ranked members plus any subset of the don't-care members, in order,
  is `CORRECT`. Oracle: requirement 3, hand-ranked.
- **C3**: don't-care peer ranks. Subject DEVS 3rd of 5; DEVW at 350 (above): "4th" and "3rd"
  right, "5th" wrong; DEVW at 250 (below): "4th" wrong. Property: the accepted ranks are exactly
  {r, r + [DEVW above the subject]}. Oracle: requirement 3, hand-ranked.
- **C4**: don't-care sums and medians. Sum key 4.727e9 with DEVW 2.259e9: 6.986e9 and 4.727e9
  `CORRECT`, 7.155e9 `INCORRECT`. Median of 100, 200, 300, 400 (250) with DEVW 500: 300 and 250
  `CORRECT`; 300 `INCORRECT` when the inputs are missing or do not reproduce the key. Property:
  random members and values. Oracle: requirement 3, hand arithmetic.
- **C5**: a declined aggregate (requirement 4). Median 200 of rows 100, 200, 300: a hedged
  answer listing the rows is `REFUSED`, not `CORRECT`; a refused one with sum key 600 is
  `REFUSED`, not `INCORRECT`; an unhedged answer stating 200 with the rows is `CORRECT`; a hedged
  answer that also states another, non-row amount keeps today's hedged grading. Property: for
  random odd-count rows, a declining answer that lists only its rows is never value-correct.
  Oracle: requirement 4, hand-read.
- **C6**: ties (requirement 5). COST and WMT both 0 at the cut of a top 3: either is `CORRECT`
  with aligned inputs; with misaligned inputs only the key's row is; a member at 1 (not tied)
  swapped in is `INCORRECT`. A tie for first in a top 2 is accepted in either order. A peer
  subject tied for 2nd-3rd may state either. Property: for random values with ties, every
  answer order that respects the value order (any order and choice within ties) is `CORRECT`,
  and replacing a top-N member by a strictly lower member is `INCORRECT`. Oracle: requirement 5,
  hand-ranked.
- **C7**: date parts (requirements 6, 7). `date_components` on each form, and none in
  "December 2024", "fiscal 2024", "31 stores", "$12/share". `extract_numbers` of "Revenue was $5
  million for the year ended December 31, 2024." is `[5e6]`. `graded_values` keeps a value also
  stated as an amount and drops a date-only copy; the MRK refusal is `REFUSED`. Properties:
  inserting a written date into a text changes no `extract_numbers` result; `graded_values` is
  idempotent and never returns a value `values` does not hold. Oracle: requirements 6-7,
  hand-read.
- **C8**: `grader_version()` is `"7"`, and every scorecard carries it. Oracle: requirement 8.

## Interface

New (stubs in this PR):

```python
# src/eval/core/answer_parsing.py
def date_components(text: str) -> list[float]: ...
def graded_values(text: str, values: Sequence[float]) -> list[float]: ...

# src/eval/core/graders/v2.py
def dont_care_members(case: EvalCase) -> dict[str, float]: ...
```

Changed in behaviour, not signature (implementer): `extract_numbers`, `extract_stated_numbers`
(requirement 6); `grade_v2`, `grade_legacy` (requirement 7); `disclosure_checks` and the screen,
peer-relative and scalar-in-context graders in `v2.py` (requirements 3-5);
`GRADER_VERSION` and its history (requirement 8). Wiring, not locked: `_cross_filer` in
`src/eval/suites/v2_suite.py` (requirement 2).

## Notes

- **Oracles.** Every expected grade is derived by hand from the requirement and the synthetic
  values in the tests (rankings sorted by hand, sums and medians computed in the test). The MRK
  refusal text, the declined-median wording and the COST/WMT tie come from stored dev answers of
  the 2026-11-10 F3a baseline (`data/eval/baseline_runs/2026-11-10/`); only dev answers were
  read. No validation or held-out answer was read.
- **Expected movement (from the K1 simulation, $0, offline; value accuracy).** The simulation
  modelled option B; option C restricts don't care to reviewed drops, so the don't-care effect is
  the WMT latent sums (+3 F3a, +2 F7a) and the A8 COST-median case (+1), all after the K6
  re-freeze adds the reviewed values. The side-finding fixes are arm-neutral and move today's
  frozen suites without a re-freeze: the spurious median CORRECTs (F3a −2, F7a −2: these were
  never right) and the tie artifact (F4 +1 once COST is keyed). The date-part fix turns
  refusals graded `INCORRECT` into `REFUSED` (MRK `-01c77e81`: F3a, F7a), which moves the
  silent-wrong and confident-wrong rates, not value accuracy. The simulation showed the K1 rules
  help the frontier arm about as much as XbrlRag, so the paired McNemar comparison is not biased.
- **Measured on a discarded prototype ($0, dev and dev_sample stored 2026-11-10 baselines, today's
  frozen suites, compared with today's grader):** value accuracy F3a −5 and F7a −5 (each a
  declining XbrlRag answer that listed rows and was credited for a member's value:
  `int-d599aa9e659a-*`, `int-bd7ee7bd1bce-*`, `int-a8e0f84b90f9-0bc7bca1`,
  `int-3318dfdb281b-089b72bd`; now `HEDGED_MISS`, since rank numbers remain among their values,
  see decision 6); every other arm 0. `INCORRECT` → `REFUSED`: F3a 5, F7a 4 (MRK included);
  `HEDGED_MISS` → `REFUSED`: F3a 3, F7a 1, F1b 2, F6b 3, F4 1. No tie or don't-care movement
  yet (COST is not keyed and no exclusion carries a value before the K6 re-freeze). **This side
  is not arm-neutral:** only XbrlRag declines aggregates while listing rows, so the fix removes
  credit from XbrlRag alone. It removes credit that was never earned.
- **Re-grade.** After the implementation merges, every stored baseline run (dev, validation,
  dev_sample; all arms) is re-graded at $0 (`eval-regrade`), with before/after counts per run in
  the implementation PR. Held-out per-case changes stay unlisted. A second re-grade follows the
  K6 re-freeze.
- **Retired grader specs stay green.** The retired grading specs (v2-grading, grading-precision,
  arm-neutral-grading, peer-rank-grading, peer-rank-count-phrases, hx-peer-rank) must pass
  unchanged except for the version pin of requirement 9. The spec author ran `pytest test/spec`
  against a discarded prototype of requirements 1 and 3-8 (outside the repository): this spec's
  42 tests pass, and no test that passes without the prototype fails with it, other than
  timing-sensitive tests (frontier-sec-tools C5/C6 failed in the base run, xbrlrag-run-halt C4
  flaked in the prototype run, all under `-n auto`, none touching grading). The remaining
  failures in both runs are the active, unimplemented locks.
- **Bounds.** At most 2^6 = 64 readings per case (requirement 1's cap); tie re-forming is one
  sort. The rules are pure and deterministic; no LLM, no I/O.
- **S3.** The semantics doc gains one sentence under S3 Grading, with the implementation PR: a
  member a reviewed resolution drops may be ranked or left out, and is not required to be
  named; ties in the key's values may be listed in any order.

### Decisions for the maintainer (with recommendations)

1. **Where the "with" value comes from.** Recommended: the builder writes the reviewed
   resolution's value on the exclusion (requirement 2, B-values), as option C says. The
   alternative, reading the member's value from the answer's own row (B-answer), needs no
   re-freeze but trusts an unverified number, which K1 rejected. Consequence: the don't-care rule
   moves no grade until the K6 re-freeze.
2. **Which exclusions are don't care.** Recommended: `resolved_drop` only, as decided. Option C's
   text also named ABT's definitional extension line (`presented_line_not_keyed`) and a blocked
   COST fallback; those have no reviewed resolution yet. The path for them is a reviewed
   `reading: drop` entry, which makes them `resolved_drop` and so don't care with a recorded
   value. This keeps "don't care" tied to a human decision and needs no new reason.
3. **Ties: exact equality.** Recommended (requirement 5). Tolerance-equal values have a filed
   order; accepting their swap would weaken the ranking test.
4. **Ties beyond screens' cut need the key's inputs.** Requirement 5 reads past-the-cut values
   from `key["inputs"]` only when aligned with `ranked`. Every v2 screen today carries all ranked
   members' keys there; a compound screen part does not, so its ties at the cut stay strict.
   Recommended: accept; an explicit `ranked_values` field would need a re-freeze for no case
   that needs it today.
5. **Peer-relative ties too.** The scope named the top-N cut; requirement 5 applies the same
   principle to a peer subject's rank and to ties inside the top N. Recommended: keep, since the
   key's ticker tie-break is equally arbitrary there; drop the peer clause if the change should
   stay minimal.
6. **Ordinals and rank numbers are still produced values** ("Rank 1:", "ranked 3rd" yield 1.0
   and 3.0 in `numeric_values`). Out of scope here; they could match a count or a small key.
   Recommended: a later change with the citation-integrity exemption table (ranks, counts) as
   its oracle, only if a stored answer shows a grade moved by it. The prototype re-grade shows
   it does: the five F3a/F7a declined aggregates become `HEDGED_MISS` rather than `REFUSED`
   because "rank 1" ... remain among their values. Neither counts towards value accuracy or
   confident-wrong, so it does not block this change.
7. **The declined-aggregate fix lowers XbrlRag alone (−5 dev value accuracy per XbrlRag arm).**
   Recommended: accept. Those CORRECTs were answers that said the aggregate was not available;
   K1 already counted them as spurious. Report it as a grader correction in the re-grade note,
   not as a system regression, and compare runs only at the same grader version.

### Retired tests relaxed for requirement 2 (2026-11-11, after the implementer's dispute)

Requirement 2 writes the reviewed resolution's `value` on every `resolved_drop` exclusion. Two
retired tests pinned that exclusion's exact shape and failed once the value was wired through:
key-fallback-witness C2 (`WitnessDrop.args == ("resolved_drop",)`) and compound-shape C10
(dict equality on `{"ticker": "DEVB", "reason": "resolved_drop"}`). Maintainer decision: both
now check the reason and accept the value as the fixture's (169M and 48M), present or absent,
and nothing else on the entry. Their other assertions are unchanged. The wiring lands on
`impl/grading-dont-care-and-ties` after this merges.
