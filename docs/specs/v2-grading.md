# v2-grading — grade the v2 and H-X cases by their real semantics

Part of the v2 evaluation ([v2 plan](../design/XbrlRag_v2_Plan.md) §3.4–3.5;
[architecture](../design/XbrlRag_Eval_v2_Architecture.md) §7). Two merged changes emit
cases that `EvalCase` and the graders only half understand:

- [hx-authoring](hx-authoring.md) builds H-X cases whose H-X kind and sub-type travel in
  `tags` (`["split:external", <kind>, <subtype>?]`) and whose specifics travel as extra keys
  (`universe`, `expected_exclusions`, `accessions`, `passages`, `assumptions`,
  `decline_reason`, `parts`, `tickers`; `key_points` belongs to
  [judge-runner-wiring](judge-runner-wiring.md));
- [v2-suite-build](v2-suite-build.md) writes case records with the tag `shape:<shape>` and
  the extra keys `key`, `disclosures` and `subject`.

Today `EvalCase.from_dict` drops those keys and `grade_case` grades each case by its
mapped kind only: a trend passes with 80 % of its years, a screen passes with a silent
drop, an honesty case is an ungraded narrative. This change keeps the emission of both
specs exactly as it is, carries the extra keys on `EvalCase`, and grades each case by its
real semantics, deterministically (no LLM; text cases stay on the judge path). The
evaluation semantics applied are S3 (universe and exclusions are graded), S2 (year-end
spread disclosure), S4 (growth / CAGR not meaningful; the closing-balance fallback stated).

## Requirement

### Cases carry the extra keys

1. **`EvalCase` fields** (implementer; field name = JSON key): `universe: list[str]` (default
   `[]`), `expected_exclusions: list[dict]` (`[]`), `accessions: dict | None` (`None`),
   `passages: list[dict]` (`[]`), `assumptions: list[str]` (`[]`), `decline_reason: str |
   None` (`None`), `parts: list[dict]` (`[]`), `tickers: list[str]` (`[]`), `key: dict |
   None` (`None`), `disclosures: dict | None` (`None`), `subject: dict | None` (`None`).
   `from_dict` reads each (absent = the default; values kept as given, lists and dicts
   copied); `to_dict` writes each one that is not `None` and not empty, unchanged, so a
   case round-trips. Every existing field, the `kind` check and `to_dict` of a case without
   these keys are unchanged. `key_points` is judge-runner-wiring's and is not redefined here.

### Which grader

2. **`v2_subkind(case)`** returns the case's v2 sub-kind or `None`:
   - a tag `shape:<s>` (v2 suite): `screen.top`, `screen.bottom` → `"screen"`;
     `screen.sum` → `"sum"`; `screen.median` → `"median"`; `peer_relative` →
     `"peer_relative"`; `trend` → `"trend"`; `compute.cagr` → `"cagr"`; `compute.ratio`
     → `"ratio"`; any other shape → `None`;
   - else the H-X tags (the H-X kind tag and sub-type tag both among `tags`):
     `cross_filer` with `screen_top` / `screen_bottom` → `"screen"`, `count` → `"count"`,
     `sum` → `"sum"`, `median` → `"median"`, `peer_relative` → `"rank"`; `cross_period`
     with `trend` → `"trend"`, `cagr` → `"cagr"`, `point_in_time` → `"point_in_time"`;
     `compound` → `"compound"`; `honesty` with `ambiguous`, `false_premise`, `decline` →
     that sub-type; `text` → `"text"`;
   - else `None`.
3. **Dispatch.** `grade_case(case, answer)`: an answer with `error` gives `ERROR` as today
   (and `checks` `None`); else a case whose `v2_subkind` is not `None` is graded by
   `grade_v2(case, answer)`; else exactly as today (`grade_screen`, `grade_breakdown`,
   `grade_narrative`, `grade_scalar` by kind), with `checks` `None`. Those four functions
   keep today's behaviour. `grade_v2` of a case with no sub-kind raises `ValueError`.
4. **Sub-checks on the grade.** `CaseGrade` gains `checks: dict[str, bool] | None = None`
   (implementer). Every grade `grade_v2` returns has a `checks` dict as stated per sub-kind
   below (`text` has `None`); `CaseGrade.to_dict` adds `"checks"` only when it is not
   `None`, so a legacy grade's dict is unchanged. Check keys are names and indices, never
   case text.

### Phrases, names and disclosure

5. **`states_phrase(text, phrase)`.** Normalisation: lower-case; delete `'` and `’`;
   replace every run of characters other than `a-z` and `0-9` with one space; strip. The
   phrase is split on `|` into alternatives; it is stated when some alternative whose
   normalised form is non-empty occurs in the normalised text on word boundaries
   (`" " + alt + " "` in `" " + text + " "`). A blank phrase is never stated.
6. **Names.** A ticker is named when it occurs in the text case-sensitively, not preceded or
   followed by `[A-Za-z0-9]`; or when `states_phrase(text, alias)` for one of its
   `aliases[ticker]`.
7. **`disclosure_checks(case, text, aliases=None)`** returns `{}` for a case without
   disclosure data, else `{"universe", "exclusions"}` plus `"period_ends"` when required:
   - **data** — v2 (`case.disclosures` a dict): members = `ranked` + the `excluded`
     tickers; counts = `{members, len(ranked)}`; name = `universe`; exclusions = the
     `excluded` tickers; period ends required when `disclose_period_ends` is true, for
     every ranked ticker, from `period_ends`. H-X (`case.universe` non-empty): members =
     `universe`; counts = `{len(universe), len(universe) − len(expected_exclusions)}`; no
     name; exclusions = the `expected_exclusions` tickers; no period ends;
   - **universe** is stated when a count occurs as a standalone integer (digits not
     preceded by a word character, `.` or `,`, and not followed by a word character or by
     `.`/`,` plus a digit), or as its English number word when it is 1–20 (`one` …
     `twenty`, through `states_phrase`); or every member is named; or the name with `_`
     read as a space is stated (`states_phrase`);
   - **exclusions** — every exclusion ticker is named (true when there are none). The
     reason wording is not checked;
   - **period_ends** — every required period end `YYYY-MM-DD` is stated in one of the forms
     `YYYY-MM-DD`, `<Month> <d> <YYYY>`, `<Mon> <d> <YYYY>`, `<d> <Month> <YYYY>`, `<d>
     <Mon> <YYYY>` (full English month name or its first three letters, day without a
     leading zero), each through `states_phrase`.
8. **S4 fallback.** A case whose `key` is a dict with `key["balance"] == "closing"`
   requires `"fallback_disclosed"`: `states_phrase(text, "year end|closing balance|ending
   balance|end of year")`.

### The graders (`grade_v2`)

The `expected`, `produced`, `has_citation` and `error` of every grade are built as today's
graders build them. "Downgraded" means: a grade that would be `CORRECT` becomes `PARTIAL`
with `passed` false and a reason naming the failing check keys.

9. **Scalars with context** (`count`, `sum`, `median`, `rank`, `cagr`, `ratio`): the base is
   `g = grade_scalar(case, answer)` (tolerance `case.rel_tol`: `0.0` makes a count or a rank
   exact; `expect_not_meaningful` keeps the n/m rule). `checks = {"value":
   g.value_correct}` ∪ `disclosure_checks(case, answer.text)` ∪ the S4 check when
   required. Grade, `matched_value` and grounding are `g`'s, downgraded when a disclosure
   or S4 check fails.
10. **Screens** (`screen`): base `g = grade_screen(case, answer)`; `checks = {"ranking":
    g.value_correct}` ∪ `disclosure_checks(...)`; `g`'s grade, downgraded when a
    disclosure check fails.
11. **Peer-relative** (`peer_relative`, v2): the subject is `case.subject` (`ticker`,
    `rank`). The rank is right when the answer has rows and the subject's 1-based position
    among the rows' distinct `row_entity` values (screen grader's reading, upper-cased) is
    `rank`; or, with no rows, when some `numeric_values` entry equals `rank`. `checks =
    {"rank": ...}` ∪ `disclosure_checks(...)`. No rows and no values: `REFUSED` when
    refused or hedged, else `INCORRECT`. Rank wrong: `INCORRECT`. Rank right: `CORRECT`
    (`matched_value = float(rank)`), downgraded when a disclosure check fails, else
    `PARTIAL` when `require_citation` and no `fact_ids`.
12. **Trend** (`trend`): for each expected row in order, the first unused produced value
    (`numeric_values`) within `rel_tol` (`close_enough`) is matched and used up. `checks`
    = `{row entity (e.g. "FY2023"): matched}`. No values: `REFUSED` when refused or hedged,
    else `INCORRECT`. Every row matched: `CORRECT`, or `PARTIAL` when `require_citation`
    and no `fact_ids`. Otherwise `INCORRECT` with `incomplete = True` (as a breakdown).
13. **Point-in-time** (`point_in_time`, rows `as_originally_reported` and `latest`): rows
    matched as in 12, except that when the two expected values agree within `rel_tol` one
    produced value may match both. `labels` = `states_phrase(text, "originally
    reported|as reported|original|initially reported|first reported")` and
    `states_phrase(text, "latest|restated|recast|revised|most recent|updated")`. `checks =
    {"as_originally_reported", "latest", "labels"}`. No values: as 12. Both values: `CORRECT`
    when labelled (or `PARTIAL` when `require_citation` and no `fact_ids`), `PARTIAL` when
    not labelled. Otherwise `INCORRECT`, `incomplete = True`.
14. **Compound** (`compound`): each entry `p` of `case.parts` (in order, `p["part"]` its
    name) is graded against the same answer as the case `EvalCase.from_dict(p without
    "part", plus id f"{case.id}#{p['part']}" and the case's question)`, by its kind: `screen`
    → rule 10; `compute` → rule 9; `breakdown` → rule 13 when it has `accessions`, else
    rule 12; `narrative` → `grade_narrative`; another kind → today's dispatch. `checks`:
    `{name: part passed}` for every part, plus `"<name>.<key>"` for every key of each part's
    own checks. Grade: every part `CORRECT` → `CORRECT`; every part `REFUSED` → `REFUSED`;
    any part `INCORRECT` → `INCORRECT`; else `PARTIAL`. `passed` is true only for
    `CORRECT`. The reason is `"; ".join(f"{name}: {part reason}")`. With no parts, the case
    grades as today.
15. **Ambiguous**: `checks = {f"assumption[{i}]": states_phrase(text, assumptions[i])}`.
    Every assumption stated (at least one) → `CORRECT`; else `REFUSED` when refused; else
    `INCORRECT`.
16. **False premise**: the correction is `expected_text`. Tokens are the normalised text
    split on spaces. UP = {rose, rise, rises, risen, rising, increase, increased,
    increases, increasing, grew, grow, grows, grown, growing, higher, gained, climbed};
    DOWN = {fell, fall, falls, fallen, falling, decrease, decreased, decreases,
    decreasing, decline, declined, declines, declining, dropped, drop, drops, dropping,
    lower, shrank, shrunk, contracted}; NEG = {not, no, never, didnt, wasnt, isnt, hasnt,
    havent, doesnt, nor, neither}. The corrected direction is the set (UP or DOWN) of the
    correction's first token in UP ∪ DOWN; the other set is the premise's.
    - With a direction: `checks = {"direction_stated": some answer token is in the
      corrected set, "premise_not_affirmed": every answer token in the premise set has a
      NEG token among the 3 tokens before it}`.
    - Without one: `checks = {"correction_stated": states_phrase(text, correction)}`.

    Every check true → `CORRECT`; else `REFUSED` when refused; else `INCORRECT`.
17. **Decline** (`decline_reason` r): `checks = {"declined": refused or hedged,
    "no_number": no numeric_values and no rows, "reason": states_phrase(text,
    REASON_PHRASES[r])}` with REASON_PHRASES:
    - `out_of_corpus`: `not in the corpus|outside the corpus|not covered|no filings|not
      included|not part of|do not have|dont have|not available|no data`;
    - `out_of_period`: `not yet filed|not been filed|has not filed|hasnt filed|not yet
      available|outside the period|no data for|not yet`;
    - `not_tagged`: `not tagged|not reported|does not report|doesnt report|does not
      disclose|doesnt disclose|not disclosed|no xbrl|not separately`;
    - `out_of_scope`: `outside the scope|out of scope|outside my scope|not covered|proxy|non
      gaap|earnings release|compensation|adjusted`.

    A number given → `INCORRECT`; not declined → `INCORRECT`; declined with the reason →
    `CORRECT`; declined without it → `PARTIAL`.
18. **Text** (`text`): `grade_narrative(case, answer)` unchanged, `checks` `None`. Judging
    is judge-runner-wiring's `run_graded`.

## Acceptance criteria

Every case is a hand-built dict in the shape hx-authoring (C7) or v2-suite-build
(requirements 13–14) emits, with fictional tickers (`DEVA`, `DEVB`, …) and round amounts;
answers are hand-built `SystemAnswer`s. Nothing calls an LLM, a database or the pipeline.
Numeric tolerances in the tests are exact (counts, ranks) or the case's own `rel_tol`.

- **C1**: the extra keys round-trip. `EvalCase.from_dict` of an H-X screen, point-in-time,
  compound, ambiguous, false-premise and decline dict and a v2 peer-relative record exposes
  each extra key as the attribute of that name with the given value; `to_dict` writes each
  back unchanged; a case without them has the defaults and no such key in `to_dict`.
  Property: for random values of the eleven fields, `from_dict(to_dict())` keeps them.
  Oracle: requirement 1, the emitted dicts.
- **C2**: dispatch. `v2_subkind` gives the requirement-2 value for every H-X kind/sub-type
  and v2 shape listed; `None` for a legacy case and for `shape:lookup`; `grade_v2` of a
  legacy case raises `ValueError`; a text case grades as `grade_narrative` with `checks`
  `None`; an errored answer is `ERROR`. Property: for random legacy cases and answers,
  `grade_case` equals today's dispatch (same `to_dict`, `checks` `None`); metamorphic: for a
  v2 case, `grade_case` equals `grade_v2`. Oracle: requirements 2–4.
- **C3**: `states_phrase`. Case, punctuation, apostrophes and `|` alternatives as in
  requirement 5, word boundaries respected (`"rose"` is not in `"prose"`), blank never
  stated. Property: a phrase inserted between words of any text is stated; metamorphic:
  re-casing and re-punctuating the text does not change the result. Oracle: requirement 5.
- **C4**: `disclosure_checks`. For a v2 screen record and an H-X screen dict: the universe
  by count, number word, name or every member; exclusions all named vs one missing; an
  alias names a ticker; period ends required only when flagged, accepted in each listed
  form, missing one fails; `{}` with no data. Property: appending the disclosure sentence
  to any text makes every check true; metamorphic: deleting one exclusion ticker turns only
  `exclusions` false. Oracle: requirement 7.
- **C5**: screens, peer-relative and scalars with context. A correct H-X screen that names
  its universe and exclusion is `CORRECT` with checks all true; the same answer silent on
  the exclusion is `PARTIAL`, not passed, `checks["exclusions"]` false; a wrong ranking is
  `INCORRECT`. v2 peer-relative: rank from rows and from values; wrong rank `INCORRECT`.
  Count and rank are exact (2 passes, 2.5 fails at `rel_tol` 0); sum within `1e-2`;
  not-meaningful CAGR declined is `CORRECT`; a ratio with `key.balance == "closing"` needs
  the fallback phrase. Metamorphic: removing the disclosure sentence never raises a grade,
  and changes only disclosure checks. Oracle: requirements 7–11, S3, S4.
- **C6**: trend and point-in-time. Three of three years `CORRECT` with per-year checks; two
  of three `INCORRECT`, `incomplete`, the missing year false (where today's breakdown
  passes at 0.8); one value used once. Point-in-time both values labelled `CORRECT`,
  unlabelled `PARTIAL`, one value `INCORRECT`; equal values matched by one number.
  Property: any permutation of the produced values gives the same grade and checks.
  Oracle: requirements 12–13, architecture §7.
- **C7**: compound. Both parts passing `CORRECT` with checks `ranking`, `dependent` and the
  prefixed sub-checks; a failing dependent `INCORRECT`, `ranking` true, `dependent` false;
  a ranking silent on its exclusion `PARTIAL`; the reason names both parts. Property: the
  grade is `CORRECT` exactly when both parts' grades are. Oracle: requirement 14.
- **C8**: ambiguous. Every assumption stated `CORRECT`; one of two missing `INCORRECT`
  with its index false; refused without it `REFUSED`; `|` alternatives accepted.
  Metamorphic: removing the assumption sentence flips `CORRECT` to not passed. Oracle:
  requirement 15.
- **C9**: false premise. "did not fall; it rose" `CORRECT`; "fell because …" `INCORRECT`
  (`premise_not_affirmed` false); a non-directional correction uses `correction_stated`.
  Property: for random fillers, an answer of the corrected word passes and one asserting
  the premise word un-negated fails. Oracle: requirement 16.
- **C10**: decline. Declined, no number, its reason phrase `CORRECT`; a number `INCORRECT`;
  the wrong reason category `PARTIAL`; not declined `INCORRECT`. Property: for every reason
  and each of its phrases, a declining answer stating it is `CORRECT`. Oracle:
  requirement 17.

## Interface

New, stubbed (`src/eval/core/graders/v2.py`; no module docstring, which the stub lint
does not admit):

- `v2_subkind(case: EvalCase) -> str | None`
- `grade_v2(case: EvalCase, answer: SystemAnswer) -> CaseGrade`
- `states_phrase(text: str, phrase: str) -> bool`
- `disclosure_checks(case: EvalCase, text: str, aliases: Mapping[str, Sequence[str]] | None = None) -> dict[str, bool]`

Behaviour only (implementer): the `EvalCase` fields (requirement 1), `CaseGrade.checks` and
its `to_dict` (requirement 4), `grade_case`'s dispatch (requirement 3). The lexicons of
requirements 8, 13, 16 and 17 become module constants of `v2.py`.

## Notes

- **Oracles.** Every expected grade is read off the requirement by hand for a hand-built
  case; the regression property's oracle is today's dispatch (`grade_scalar`,
  `grade_screen`, `grade_breakdown`, `grade_narrative`), which this change must not alter.
- **Quarantine.** The tests use made-up cases only and never open a set B or H-X file.
  Check keys are names and indices (`assumption[0]`, `FY2023`), never authored text.
- **Why PARTIAL for a missed disclosure.** S3 says a correct ranking with a silent drop
  counts as wrong: it is not passed. It is `PARTIAL`, not `INCORRECT`, because the number
  is right: `value_correct` stays true and it never counts as confident-wrong.
- **Deterministic phrase checks are a floor.** They accept fixed wording (lexicons above);
  an answer that states the assumption or the reason in other words fails. H-X authors
  write `assumptions` (and non-directional corrections) as `|`-separated alternatives. The
  universe check accepts a count anywhere in the text, so it can pass on an unrelated
  number; exclusion reasons and ratio-definition statements (S4) are not graded here.
- **Grader version.** Legacy cases grade exactly as before, so `GRADER_VERSION` is not
  bumped here; persisted v2 runs differ by suite. A later change may bump it.
- **judge-runner-wiring** also edits `EvalCase.from_dict` / `to_dict` (adds `key_points`):
  textually adjacent, semantically independent. A compound's text dependent grades
  `PARTIAL` until combined with the judge's scores (later change).
- **Not here:** the S4 `balance` key itself (no emitter writes it yet; v2-suite-build has
  no average-balance ratios), aliases from a names file (the dispatch passes none), and
  scorecard columns for the checks.
