# peer-rank-count-phrases — "I ranked 32 of the 33" is a count, not a rank

A follow-up to [peer-rank-grading](peer-rank-grading.md) (implemented, grader version 5). Its
**numbered rank** rule (requirement 1) reads `ranked N` as rank `N`, and its owner rule 5
gives an expression with no candidate mentioned before it to the subject. The first stated
rank decides the grade.

A stored frontier answer to a dev `peer_relative` case (`int-ddc1ca21791a-042123a6`, run
`eval-e9fa0e0df9b7`) begins:

> I ranked 32 of the 33 covered companies on fiscal 2025 comprehensive income attributable to
> the parent. McDonald's is 17th, ...

The key's rank is 17. `stated_ranks` returns `[32, 17]`, so the grader reads 32 and grades the
answer `INCORRECT`. Here `32` is the number of companies the author ranked, not a rank. When
the subject of the rank word is the answer's author ("I", "we"), the phrase reports what the
author did, which is to rank some number of companies. A company is never the subject of
such a phrase. This change reads such a phrase as a count, and the count is not a rank
expression.

## Requirement

1. **Count phrase.** `is_count_phrase(text, start)` is true exactly when:
   - a rank word (`rank`, `ranks`, `ranked` or `ranking`, case-insensitive, not directly
     followed by a letter or digit) begins at index `start` of `text`;
   - and it has a **first-person subject**. The text before `start` ends with one of these
     subjects followed by exactly one space:
     - `I`, `we`;
     - `I've`, `we've` (the apostrophe is `'` or `’`);
     - `I have`, `we have` (one space between the words).

     Matching is case-insensitive. The subject must not be directly preceded by a letter or
     digit (`AI ranked` and `Wwe ranked` have no first-person subject).

   Otherwise it is false. That includes a `start` outside the text, and a `start` where no
   rank word begins. The set is closed: no other subject (`they`, `the screen`, a company)
   and no other words between the subject and the rank word (`I also ranked`, `we then
   ranked`) make a count phrase.
2. **Not a rank (amends requirement 1 of peer-rank-grading).** A numbered rank whose prefix
   form is a rank word (`rank`, `ranks`, `ranked`, `ranking`, with or without a connector) is
   no rank expression when `is_count_phrase(text, i)` holds, `i` being the index where that
   rank word begins. As with peer-rank-grading's other non-ranks, such text never conflicts
   with a stated rank. Nothing else in peer-rank-grading changes:
   - the other numbered forms (`#`, `No.`, `number`) never take a subject;
   - ordinals stay rank expressions whatever precedes them;
   - every other subject of a rank word keeps its meaning ("Intel ranked 14 of 14" is 14);
   - the mentions, sentences and owner rules apply as before to the expressions that remain.
3. **Grading.** `peer_relative` grading (peer-rank-grading requirement 4) is unchanged. It
   reads the first rank `stated_ranks` returns, so a count phrase no longer decides a
   grade.
4. **Grader version.** This changes grades, so `grader_version()` becomes `"6"`, and the
   `honesty.py` history gains the line `6 — a first-person "I/we ranked N" is a count of
   companies, not a rank`.
5. **Retired version pins.** The exact version is pinned by the newest change only (this
   spec's C4). The retired pins assert the version is at least the one that introduced their
   rules, so they hold at 5 and at 6, and they still fail on a downgrade below their own
   version. They are updated in this spec PR, each with a comment naming this change:
   - peer-rank-grading C6 (at least 5);
   - arm-neutral-grading C6 (at least 5, the version when these tests were last pinned);
   - grading-precision C8 (at least 5).

   See decision 2.

## Acceptance criteria

- **C1**: `is_count_phrase` (requirement 1).
  - True for every listed subject before each rank word ("I ranked", "We rank", "I've
    ranked", "we’ve ranked", "I have ranked", "WE HAVE RANKED", "i ranked"), with `start` at
    the rank word.
  - False for:
    - another subject ("Intel ranked", "Walmart ranks", "It ranks", "which ranks", "They
      ranked", "The screen ranked");
    - a subject that is part of a word ("AI ranked");
    - two spaces or a newline between subject and rank word ("I  ranked", "I\nranked");
    - an extra word between them ("I also ranked");
    - a non-rank word at `start` ("I rated", "I rankled");
    - a `start` that is not the rank word's first letter;
    - `start` 0, negative, or past the end.

  Properties:
  - for every subject × rank word × case variant, preceded by any sentence prefix, it is true;
  - for any subject word of letters outside the listed set, it is false.

  Oracle: requirement 1, hand-read.
- **C2**: `stated_ranks` drops count phrases (requirement 2). Point checks:
  - the quoted McDonald's text gives `[17]` for MCD;
  - "We ranked 24 of the 30 filers. Apple is 3rd." gives `[3]` for AAPL;
  - after a count-phrase sentence, the other forms keep their meaning: "Intel ranked 14 of
    14" gives `[14]` for INTC, "Netflix holds rank 19" gives `[19]`, "Meta is #2" gives `[2]`,
    and "Merck ranks 17th" gives `[17]`;
  - "I have ranked 30 companies; we've ranked 5 more." gives `[]` for the default subject.

  Metamorphic: prefixing any text with "I ranked N of the M covered companies. " (any N, M in
  1–999, any listed subject and rank word) never changes `stated_ranks` of the rest. Oracle:
  requirement 2, hand-read.
- **C3**: grading (requirement 3). A fictional peer case (subject MCD, rank 17 of 33) with the
  McDonald's answer text has `checks["rank"]` true and is not `INCORRECT`; with "McDonald's
  is 16th" it is `INCORRECT`. Metamorphic: the count-phrase prefix never changes the grade
  or `checks`. Oracle: requirement 3 and peer-rank-grading requirement 4, hand-derived.
- **C4**: `grader_version()` is `"6"`, and every scorecard carries it. Oracle: requirement 4.

## Interface

New, in `src/eval/core/graders/v2.py`:

```python
def is_count_phrase(text: str, start: int) -> bool: ...
```

Changed (implementer): `_rank_expressions` (or `stated_ranks`) skips a numbered rank whose
rank word `is_count_phrase` marks (requirement 2); `GRADER_VERSION` and the history in
`src/eval/core/graders/honesty.py` (requirement 4).

## Notes

- **Oracles.** Every expected value is read by hand from the texts in the tests. The McDonald's
  sentence is the opening of the stored dev answer named above. The rest is synthetic. No
  validation or held-out answer was read.
- **Why only I/we, as a closed list.** The author of an answer is the only subject that ranks
  companies rather than being ranked. "Intel ranked 14 of 14", "which ranks 20th" and "That
  ranks 20th" all have a ranked thing as subject and keep their meaning. A wider rule, such
  as "any pronoun" or "any subject that is not a company", would need a deterministic reader
  to decide what a subject is. That is the guesswork peer-rank-grading avoids.
  `they`, `the screen` and `the table` are left out: no stored answer uses them, and "They
  ranked 3rd and 4th" is a real rank reading. The list is fixed and general. It names no
  filer and no case.
- **Exactly one space, no words in between.** "I also ranked 32" or "we then ranked 32" stay
  rank expressions, owned by the default rule as before. Allowing adverbs would need an
  adverb list, and no stored answer uses one. Widen it only on an observed answer.
- **Re-grade required.** The implementation PR re-grades every stored run with peer-relative
  cases at $0 (`eval-regrade`, all arms, dev and the other splits the runs hold; held-out
  per-case changes stay unlisted). It records the before/after counts per run in the PR. The
  expected effect is small: frontier answers that open with "I ranked N of the M" and then
  state the subject's rank.

### Decisions for the maintainer (with recommendations)

1. **"ranked 32 companies" (N followed by a noun).** Not in this change. Recommended: defer.
   `ranked N companies|filers|peers|firms|issuers` with no first-person subject ("The screen
   ranked 32 companies") is also a count. A rule that rejects `N` directly followed by one
   space and such a noun would be general and low-risk, since no rank reads "rank 3
   companies". But no stored answer needs it. A first-person "I ranked 32 companies" is
   already covered by requirement 1. Add it as its own change when an answer needs it, with
   its noun list in the spec. This follows the rule of no tuning beyond observed failure
   classes.
2. **Grader version: bump to 6, and make the retired pins at-least-5.** Recommended. The
   options:
   - **(a) Bump, and re-pin the retired tests to exactly "6"** (what peer-rank-grading did
     for arm-neutral-grading C6 and grading-precision C8). The retired tests are graduated:
     `tdd ci` demands they pass. Re-pinned to "6" in the spec PR, they would fail against
     `main`'s version 5 from the spec merge until the implementation merges. Every PR's CI
     would be red in that window, and so would this spec PR's. Every later grading change
     would edit them again.
   - **(b) Bump, and turn the retired pins into at-least checks** (requirement 5,
     recommended). They pass at 5 and at 6, so CI stays green across the spec and
     implementation merges. A later bump never needs to touch them again. The exact pin
     moves to the newest spec (C4 here). Cost: a retired test no longer catches an
     unintended **bump** on its own. The newest spec's exact pin still does, and an
     unintended **downgrade** still fails every retired pin. The edit relaxes three
     graduated assertions, so the maintainer should see it in the diff.
   - **(c) Keep version 5.** No retired test changes. But stored runs graded before and after
     this change carry the same version with different peer grades, so a scorecard's
     `grader_version` would no longer tell which rules graded it. That breaks the
     provenance stamp `eval-grader-honesty` exists for. Not recommended.
