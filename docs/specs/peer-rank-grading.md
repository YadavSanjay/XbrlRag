# peer-rank-grading — read a peer-relative rank from what the answer states

D3 failure class C3 (`data/eval/d3/failure_classes_2026-11-10.md`, a local analysis file). On
the dev split, in 18 (F3a) and 19 (F7a) `peer_relative` cases the system's stated rank equals
the key's rank, but the case is graded `INCORRECT`. The cause is
`src/eval/core/graders/v2.py::_grade_peer_relative` (requirement 11 of
[v2-grading](v2-grading.md)): it takes the subject's rank to be its 1-based position among
the distinct entities of `answer.rows`, so the row **order** decides the rank.
`xbrlrag_full`'s rows are built in statement order (`answer_rows`,
[grading-precision](grading-precision.md) requirement 5). Its answer leads with the subject
("Walmart's accounts payable ... ranks 2nd of the 4 retailers"), so the subject becomes row 1
and the grader reads rank 1. Any arm that lists peers alphabetically, or names the subject first,
is graded the same way. This change reads the rank the answer **states**, else the rank its
rows' **values** imply. The same rule applies to every arm ([arm-neutral-grading](arm-neutral-grading.md)).

## Requirement

1. **Stated ranks.** `stated_ranks(text, ticker, names)` returns, in text order, every rank
   the text attributes to `ticker`. `names` maps every candidate entity (ticker) to its other
   names. The candidates are the keys of `names` plus `ticker`.
   - **Rank expressions.** All matching is case-insensitive and whole-word: a match is never
     directly preceded or followed by a letter or digit.
     - **Numeric ordinal:** digits followed by `st`, `nd`, `rd` or `th` (`2nd`, `14th`, `21st`).
       The rank is the number.
     - **Word ordinal:** `first` … `twentieth`, `twenty-first` … `forty-ninth` (hyphen or one
       space between the parts), `thirtieth`, `fortieth`. The rank is the number.
     - **Numbered rank:** an integer `N` from 1 to 999. It is directly preceded by one of these
       forms, each followed by one space unless it ends in `#`:
       - `#` (optionally followed by one space);
       - `No.` (optionally followed by one space);
       - `number`;
       - `rank`, `ranks`, `ranked` or `ranking`, optionally followed by one of `at `, `of `,
         `is `, `No. `, `#` or `number `.

       `N` is not followed by a digit, or by `.` or `,` and a digit. The rank is `N`
       (`rank 19`, `#2`, `ranked 14 of 14`).
     - **Top superlative:** `highest`, `largest`, `greatest` or `biggest`, with rank 1
       ("Amazon had the highest"). It is not an expression when directly preceded, through a
       hyphen or one space, by `next` or by an ordinal. `second-highest` is the ordinal's
       expression, rank 2.
   - **Not ranks** (no expression; such text never conflicts with a stated rank):
     - an ordinal directly followed, through a hyphen or one space, by `lowest`, `smallest` or
       `least`; or by `to last` (`to-last`), or by ` from the bottom`. These are bottom ranks:
       the key's rank counts from the highest, so converting one would need a count. They
       are left to requirement 4.
     - an ordinal followed by one space and `quarter`, `half`, `fiscal`, `year`, `years`,
       `time`, `percentile`, `consecutive`, `reported`, `filed` or `disclosed`;
     - an ordinal directly preceded by a month name (full or three-letter, optional `.`) and
       one space (`October 30th`);
     - the words `last`, `top` and `bottom`.
   - **Mentions.** A candidate is mentioned where its ticker appears exactly as written
     (case-sensitive) or where one of its names appears (case-insensitive), as a whole word.
     Possessives count: `Walmart's` mentions Walmart. When mentions overlap, the one that
     starts first wins, and of those the longest.
   - **Sentences.** The text splits into sentences after `.`, `!` or `?` followed by whitespace
     or the end of the text, and at every newline. A split point that falls inside a mention
     is not a split (`Amazon.com, Inc.`, `Apple Inc.`).
   - **Owner of an expression.** Each expression belongs to exactly one candidate:
     1. **Forward-binding:** an ordinal directly followed by `-ranked` or `-placed`
        (`the 12th-ranked (MRK)`) belongs to the first mention after it in its sentence, when
        there is one.
     2. **Parenthetical:** an expression inside parentheses belongs to the last mention in
        its sentence that ends before the expression, when there is one. Inside parentheses
        means after a `(` with no `)` between them. For example, "just below Intel
        (52,853,000,000 USD, rank 19) and just above Netflix" gives rank 19 to Intel.
     3. **Nearest in the sentence:** otherwise, it belongs to the mention in its sentence with
        the smallest gap, in characters, between the mention and the expression. On a tie,
        the mention before the expression wins.
     4. **Carry-over:** when its sentence mentions no candidate, it belongs to the last
        mention before that sentence ("Microsoft's inventory was $1.2 billion. That ranks 20th
        of the 24.").
     5. **Default:** when no candidate is mentioned before it, it belongs to `ticker`. The
        question is about the subject.
2. **Rank from values.** `value_rank(rows, ticker, names, n_ranked)` returns the subject's
   rank implied by the rows' values, or `None` when the rows cannot imply one:
   - **Entities.** Each row's entity (`row_entity`, upper-cased) is mapped to a candidate:
     - a candidate ticker equal to it;
     - else the candidate one of whose `names` equals it, case-insensitively;
     - else the entity itself.

     Only rows with a numeric value (`row_value`) count, and the first such row of an entity
     wins.
   - **When it can't say.** `None` when the subject has no valued row, or when fewer than
     `max(n_ranked, 2)` distinct entities have a valued row. A partial list can't imply a rank.
   - **The rank.** Otherwise it is `1` plus the number of other entities with a greater
     value, plus the number with an equal value whose entity sorts before `ticker`. That is the
     key's ordering: value descending, ties by ticker ascending
     ([v2-suite-build](v2-suite-build.md) requirement on `screen.*` / `peer_relative`). Row
     order never matters.
3. **Names.** `peer_names(case)` returns the candidate names for a peer-relative case:
   - **Keys:** the subject's ticker, every ticker in `case.disclosures["ranked"]`, every
     `ticker` in `case.disclosures["excluded"]`, every `ticker` of `case.expected_rows`, and
     every key of `case.subject["names"]`.
   - **Values:**
     - first, the names `case.subject["names"]` (a mapping ticker → list of names, optional)
       gives that ticker;
     - then, for a ticker in the committed v2 lexicon (`configs/eval/v2/lexicon.yaml`,
       `companies`), its `short` and `legal` names when not already present;
     - an empty list for any other ticker.

   Deterministic: the only input besides the case is that committed file.
4. **Grading (replaces requirement 11 of v2-grading).** For a `peer_relative` case:
   - **Subject.** The subject is `case.subject["ticker"]` (upper-cased), and the key's rank is
     `r = case.subject["rank"]`.
   - **Names.** `names` is `peer_names(case)`. Each `answer.rows` entity (`row_entity`) not
     already a key is added with no names.
   - **The answer's rank** is read in this order:
     1. **Stated:** the **first** rank in `stated_ranks(answer.text, ticker, names)`.
     2. **Rows:** else `value_rank(answer.rows, ticker, names, n_ranked)`. `n_ranked` is
        `len(case.disclosures["ranked"])` when `case.disclosures` is a dict with a `ranked`
        list, else `len(case.expected_rows or [])`.
     3. **Values:** else, when `answer.numeric_values` is non-empty, the rank is right
        exactly when some entry equals `r`. This is v2-grading's no-rows reading, kept for an
        answer that gives a rank only as a structured `value`.
     4. **None:** otherwise no rank can be read.
   - **The grade.**
     - The rank is right when the answer's rank equals `r`. `checks = {"rank": right}` ∪
       `disclosure_checks(case, answer.text)`, with the same keys and the same disclosure
       checks as before.
     - No subject ticker or no `r`: `INCORRECT`, with `checks["rank"]` false.
     - No rank read: `REFUSED` when the answer refused or hedged, else `INCORRECT` ("no rank
       in the answer").
     - Rank wrong: `INCORRECT`; the reason names the subject, the rank read and `r`.
     - Rank right: `CORRECT` with `matched_value = float(r)`. It is downgraded to `PARTIAL`
       when a disclosure check fails, and is `PARTIAL` when `require_citation` and no
       `fact_ids` (both unchanged).
   - **Precedence.** A stated rank decides even when the rows' values imply another. The rank
     the answer gives in words is what a reader takes from it. When the answer states none,
     the values decide.
5. **Grader version.** This changes grades, so `grader_version()` becomes `"5"`, and the
   `honesty.py` history gains the line `5 — peer-relative rank read from the stated rank, else
   the rows' values, never the row order`.
6. **Retired tests.** v2-grading C5 asserted that reversing the rows makes a rank wrong.
   arm-neutral-grading C6 and grading-precision C8 asserted version `"4"`. These tests are
   updated in this spec PR, each with a comment naming this change.

## Acceptance criteria

- **C1**: `stated_ranks` reads every rank form of requirement 1. Point checks:
  - `2nd`, `second`, `#2`, `No. 2`, `rank 2`, `ranked 2 of 4`, `second-highest` and
    `twenty-first`/`21st` each read 2 or 21;
  - "had the highest" reads 1;
  - these are not ranks: `second-lowest`, `first quarter`, `October 30th`, `ranked last`,
    `ranked FY2022`, `rank 2022` and "the next-highest".

  Properties:
  - for any n in 1–49, the numeric ordinal, the word ordinal and `#n` read the same `[n]`;
  - inserting a sentence with no digits, ordinals or mentions anywhere between sentences
    changes nothing.

  Oracle: requirement 1, hand-read.
- **C2**: attribution follows requirement 1's owner rules:
  - nearest mention (AMD's "13th just ahead of Broadcom" is AMD's);
  - forward-binding ("the 12th-ranked (MRK)" is MRK's, not the subject's);
  - parenthetical ("just below Intel (…, rank 19) and just above Netflix" gives 19 to Intel);
  - carry-over ("That ranks 20th" after a Microsoft sentence);
  - the default (a text naming no candidate);
  - names case-insensitive, tickers case-sensitive (`meta` in lower case is the name Meta,
    `aapl` is not AAPL);
  - overlapping mentions (the legal name wins over the short name inside it).

  Real dev texts read their first stated rank as the key's (WMT 2nd of 4, MSFT 20th of 24,
  META 3rd of 14, UNP 17th of 32, KO 20th with neighbours at "rank 19" and "rank 21").

  Properties:
  - permuting sentences of the form "<X> ranks <k>." over distinct entities never changes
    any entity's ranks;
  - renaming every entity consistently (ticker and names) never changes them.

  Oracle: requirement 1, hand-read.
- **C3**: `value_rank`:
  - descending order with negative values (financing cash flow: HD −0.694bn, AMZN −11.8bn,
    WMT −13.4bn puts AMZN 2nd);
  - a tie broken by ticker;
  - a row named by a name (`Walmart`) maps to its ticker;
  - the first row of an entity wins;
  - `None` for a partial list, for a subject with no valued row, and for a single row.

  Properties:
  - any permutation of the rows gives the same rank;
  - multiplying every value by a positive constant gives the same rank;
  - the rank is between 1 and the number of distinct valued entities.

  Oracle: requirement 2, hand-ordered.
- **C4**: `peer_names`:
  - the subject's own `names` come first;
  - members of `ranked`, `excluded` and `expected_rows` are keys;
  - a lexicon ticker (WMT) gains `Walmart` and `Walmart Inc.`;
  - a ticker not in the lexicon (DEVA) has only its case names, or `[]`.

  Property: the keys are exactly the union of requirement 3's sources. Oracle: requirement 3
  and the committed lexicon.
- **C5**: grading (requirement 4):
  - a correct stated rank with the subject listed first in the rows is `CORRECT`
    (`checks["rank"]` true, `matched_value` 2.0); this is the C3 class (WMT 2nd of 4, rows in
    statement order);
  - so is the same answer with rows in alphabetical order, or with no rows;
  - a wrong stated rank is `INCORRECT` even when the rows' values put the subject at the key's
    rank;
  - with no stated rank, complete rows decide by value, whatever their order;
  - with no stated rank and partial rows, the values rule applies, else `INCORRECT`;
  - a refusal stating no rank is `REFUSED`;
  - a correct rank silent on the universe is `PARTIAL`;
  - a correct rank with no `fact_ids` on a citation-required case is `PARTIAL`;
  - a frontier-style JSON reply whose `statements` state the rank is graded by that rank;
  - with no subject, the answer is `INCORRECT` with `checks["rank"]` false.

  Properties:
  - permuting `answer.rows` never changes the grade or `checks`;
  - renaming every ticker consistently in the case and the answer never changes the grade;
  - the same text graded with rows in statement order, alphabetical order or no rows gets
    the same grade when it states a rank.

  Oracle: requirement 4, hand-derived grades.
- **C6**: `grader_version()` is `"5"` and every scorecard carries it. Oracle: requirement 5.

## Interface

New, in `src/eval/core/graders/v2.py`:

```python
def stated_ranks(text: str, ticker: str, names: Mapping[str, Sequence[str]]) -> list[int]: ...
def value_rank(rows: Sequence[Any], ticker: str, names: Mapping[str, Sequence[str]],
               n_ranked: int) -> int | None: ...
def peer_names(case: EvalCase) -> dict[str, list[str]]: ...
```

Changed (implementer): `_grade_peer_relative` (requirement 4), `GRADER_VERSION` in
`src/eval/core/graders/honesty.py` (requirement 5). `case.subject` may carry an optional
`names` mapping; no other case field changes.

## Notes

- **Oracles.** Every expected rank and grade is read by hand from the hand-written texts and
  rows in the tests. The real texts are dev-split answers from the stored runs
  `eval-eefe3508621a` and `eval-5e602d782e6f` (xbrlrag_full, dev only), shortened. Their
  values are SEC companyfacts amounts (public domain). No validation or held-out answer was
  read.
- **Why "first stated".** The answers lead with the subject's rank, then place its neighbours:
  "KO ranks 20th ..., just below Intel (…, rank 19)", or a statement line such as "TXN
  ranked immediately above UNP (rank 16)". Such neighbour clauses are genuinely ambiguous
  to a deterministic reader. Taking the first rank attributed to the subject reads the
  direct answer. A self-contradicting answer whose first rank is right passes; this is
  accepted (decision 2).
- **Prototype check (scratch, not committed).** The rule was run read-only over the stored dev
  answers. On the xbrlrag_full runs, 18 of 28 INCORRECT peer cases in F3a
  (`eval-42942572934b`, and `eval-eefe3508621a`) and 12 of 29 in F7a (`eval-95c14a787892`,
  and `eval-5e602d782e6f`) would read the key's rank:
  - the F7a gap to the analysis' 19 (which compared the screen's rank, not the stated one)
    is mostly synthesizer "not valid JSON" answers, which state no rank;
  - none of the frontier, naive-RAG or closed-book INCORRECT peer cases flipped. Those
    answers state another rank (universe differences, class C4), or none.

  The implementer records the real before/after.
- **Re-grade required.** The implementation PR re-grades every stored run that has
  peer-relative cases, at $0 (`eval-regrade`, all arms, dev and the splits the runs hold;
  held-out per-case changes stay unlisted). It records the before/after counts per run in the
  PR.
- **Out of scope:**
  - **The verifier dropping the rank statement.** The product side of C3: `citation_integrity`
    flags the count in "of the 4 retailers", and the LLM verifier sees no rank in the screen
    evidence. The rank then survives only in the summary. This is a separate D3 product fix.
    Grading reads `answer.text`, which for xbrlrag_full is the final answer text
    (grading-precision requirement 5), so a dropped statement's rank is graded only if the
    summary keeps it.
  - **The universe-median position.** The key's `subject` carries `universe_median` and
    `position` (`above`/`below`/`at`). Neither v2-grading nor this change grades them
    (decision 3).
  - **Disclosure checks** are unchanged.

### Decisions for the maintainer (with recommendations)

1. **Names source.** The grader needs company names, because answers say "Walmart" and not
   "WMT". Recommended: the case's optional `subject["names"]`, else the committed v2 lexicon.
   This lets stored runs be re-graded against the frozen suites without a rebuild. The
   alternative, names written only into the case by the suite emitter (as compound-shape's
   `pick.candidates`), needs a suite rebuild and diff before any re-grade. Held-out filers
   are never in the lexicon, so a held-out peer case is read by ticker only until the emitter
   writes `names` (a follow-up).
2. **First stated rank, not "all must agree".** Recommended: first. Requiring agreement made
   2 of the 30 real flips fail on neighbour clauses ("TXN ranked immediately above UNP
   (rank 16)"), so it penalised correct answers for ambiguous context. Alternative: treat
   disagreement as `INCORRECT`, which is stricter on self-contradiction.
3. **Median position.** Recommended: don't grade it yet. Most answers decline the median
   because no step computes it (class C2's aggregate step). Grading it now would turn this
   fix into a new failure. Revisit after C2 lands, as its own change with its own phrase
   rules.
4. **Keep the values fallback (requirement 4.3).** Recommended: keep it. A frontier reply
   gave `{"value": 21, "unit": "rank"}`, and dropping the fallback would change baseline
   grades beyond C3. It is loose ("any value equals r"), but it now applies only when neither
   a stated rank nor complete rows exist.
5. **Rows need a complete list.** `value_rank` requires at least as many valued entities as
   the key ranked. Recommended: keep. A partial list (the subject and two neighbours) implies
   no rank. An answer ranking a larger universe still gets its position, and is graded
   against the key like any other answer.
