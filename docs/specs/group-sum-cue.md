# group-sum-cue — "total X of a named group" and "add up" ask for the group's sum

Follows [screen-aggregates](screen-aggregates.md) (retired; `aggregate_cue`, `group_plan_errors`).

**Today.** A question over one named group gets a forced `aggregate` step only when
`aggregate_cue(query)` finds an unambiguous word. Those words are median, combined, aggregate,
collectively, altogether, summed, "sum of", "in total" and "taken together"
(`src/retriever/xbrl/universe_routing.py`). The cue makes `group_plan_errors` reject a plan
without the step, which triggers the planner's retry. "total" is deliberately not a cue, because
it names metrics ("total revenue", "total current assets"). Without a cue, the planner LLM
decides on its own.

**The defect** (measured 2026-11-12, $0, `data/handover/total_cue/REPORT.md`):
- **Planner misses.** In every `xbrlrag_full` run since the aggregate op landed, plans for
  cued group-sum questions all include the sum (37 of 37). Plans for uncued ones miss it in
  **5 of 14** (36%): "total interest expense / total debt / total Assets, Current, the <group>",
  graded HEDGED_MISS or REFUSED.
- **Size of the gap.** 40 of the frozen suites' 113 group-sum questions carry no cue: "total X,
  the <group>" (about 23) and "If you add up the <group>, how much X …" (17).
- **Disclosure.** The "add up" wording was found by reading **validation** questions. It is plain
  English, but this change moves validation measurement, and the results must say so.

The right behaviour is stated independently by the question's words: "the total X of the
retailers" with no ranking or per-company wording asks for one figure over the group. A miss goes
unnoticed (a hedged or refused answer reads as caution). Route: locked spec.

**No tuning on eval data.** The word lists below are written from English usage and the group
definitions, not from the suites' wording. The tests use only phrasings written for this spec.
The frozen suites are measured once, after implementation (Wiring 3).

## Requirement

### 1. The group sum cue (`group_aggregate_cue`)

`group_aggregate_cue(query, universes, resolved_companies)` returns `"median"`, `"sum"` or None:

1. When `aggregate_cue(query)` is not None, it is returned unchanged (`aggregate_cue` itself does
   not change).
2. Otherwise None unless `len(universes) == 1` and `resolved_companies == 0`.
3. Otherwise `"sum"` when the question, read as `universe_routing._words` reads it, has:
   - **an add-up phrase**: the word pair `add up`, `adds up`, `added up` or `adding up`; or
   - **"total" as an amount word**: `total` or `totals`, and **none** of:
     - **a ranking, comparison or per-member word:** top, bottom, highest, lowest, largest,
       smallest, biggest, most, least, rank, ranks, ranked, ranking, which, who, whose, each,
       every, per, versus, vs, compare, compared, comparison, than, list, breakdown, split,
       median, average, mean;
     - **a per-member pair:** `by company`, `by companies`, `by filer`, `by member`, `by ticker`;
     - **a count:** a token of digits that is not a year (an integer from 2000 to 2099), or one
       of the number words one … twenty.
4. Otherwise None.

The function is pure; `universes` is not mutated.

### 2. Routing uses it (wiring, not locked)

`IntentRouter.route` computes `aggregate = group_aggregate_cue(query, universes, len(resolved))`
in place of `aggregate_cue(query)`, so `universe_intent` and `group_plan_errors` see it. The
`universe_routing` decision event logs which rule fired (`cue`, `add_up`, `total`).

### 3. Wiring and measurement (not locked; implementer, then the main session)

1. Unit tests over the router with a fake LLM: "total interest expense, the retailers, fiscal
   year 2024" routes `NUMERIC_SCREEN` with aggregate `sum`; "AAPL total revenue fiscal 2024"
   (a company) does not.
2. Module docstring of `aggregate_cue`: point at the new function.
3. **Measured once, after merge (main session, $0, then a small paid slice):**
   - the share of the frozen suites' group-sum questions the cue now covers;
   - a check that no group question with a ranking word gets it;
   - the planner's sum rate on a DeepSeek slice of the uncued cases.
   Report validation separately (see the disclosure above).

## Acceptance criteria

All phrasings are written for this spec; none is copied from an eval suite. Groups are the
committed universe ids (`retailers`, `semiconductors`, `big_tech`, `covered`). Nothing reads
`data/`, a database, the network or the clock.

- **C1**: precedence and preconditions (Requirement 1, steps 1-2).
  - A question with "median" or "combined" returns `aggregate_cue`'s answer whatever the
    universes.
  - "total revenue of the retailers in 2023" with two universes, or with one resolved company,
    is None.
  - Oracle: Requirement 1 by hand.
  - Property: whenever `aggregate_cue(q)` is not None, `group_aggregate_cue(q, u, n) ==
    aggregate_cue(q)` for any `u` and `n`.
- **C2**: add-up (Requirement 1, step 3).
  - "If we add up the semiconductor companies, what was their R&D in fiscal 2022?" → sum.
  - "Adding up the retailers' inventories for 2024, what do you get?" → sum.
  - "The retailers' revenue adds up to what in fiscal 2021?" → sum.
  - "up" without "add" → None.
  - Oracle: the word pairs by hand.
  - Metamorphic: inserting any add-up pair into an uncued one-group question makes the result
    sum.
- **C3**: "total" as an amount word (Requirement 1, step 3).
  - Sum:
    - "total long-term debt, the semiconductor companies, fiscal 2025";
    - "What was the total R&D spend across the big tech companies in fiscal year 2023?";
    - "Give the totals for net income of the retailers, FY2022".
  - None:
    - "Which retailer had the highest total assets in 2024?" (rank word);
    - "top 3 covered companies by total revenue, fiscal 2025" (rank word and count);
    - "total revenue by company for the retailers in 2023" (per-member pair);
    - "the five big tech companies' total assets in 2024" (number word);
    - "total revenue, the retailers vs the semiconductor companies" (two universes);
    - "Apple's total revenue, fiscal 2024" with one resolved company.
  - A year such as 2024 is not a count; "12 retailers" is.
  - Oracle: Requirement 1 lists by hand.
  - Metamorphic:
    - for a "total" question that returns sum, adding any one listed ranking word (except
      "median", which is also an `aggregate_cue` word, so step 1 returns "median"), per-member
      pair, number word or non-year number returns None;
    - adding or removing a year never changes the result;
    - letter case never matters.

## Interface

```python
# src/retriever/xbrl/universe_routing.py
def group_aggregate_cue(query: str, universes: Sequence[str], resolved_companies: int) -> str | None: ...
```

Changed, not locked: `IntentRouter.route` (the call), the `universe_routing` decision event.

## Notes

- **Evidence:** `data/handover/total_cue/REPORT.md` and `measurement.json` (read-only, 11-12).
  The simulation there used a word list adjusted against the suites, and it is not used here.
  This spec's lists are written from English, and the one measurement after merge is the
  judgement.
- **Alias gap, not here:** 13 paraphrases name a group in shorthand ("semis", "covered cos")
  that `find_universe_mentions` misses. Adding eval wording to the alias table would be tuning
  on eval data; any alias addition needs its own reviewed change, written from general usage.

## Review points

1. **"every" and "each" block.** "total revenue of every retailer" is then not cued.
   Recommendation: accept; it reads as per-member as often as as a total, and the planner still
   decides.
2. **Number words up to twenty.** Recommendation: accept; a group of named size ("the five big
   tech companies") is usually a selection, not the universe.
3. **Validation moves.** The "add up" template appears in 11 of 14 validation sum originals.
   Recommendation: state it in the results; it is a general English fix, not a validation-tuned
   one.
