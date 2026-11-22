# v2 held-out look G1/G2: results

Aggregates only. The binding rules are the [preregistration](preregistration.md) and its
Amendments 1–5; this report applies them and changes none. The figures come from
`data/handover/g1/g1_results.py`, run by the maintainer on the stored runs; no agent read a
held-out case, answer or per-case grade.

## Summary

On the hardest questions of the held-out set — ranking a metric across many filers, following a
filer across periods, and compound questions that rank first and then compute on the ranked
companies — XbrlRag answered **85.2%** correctly, against **53.6%** for a frontier model (Opus)
with tools over SEC's public APIs and **45.4%** for the same agent on deepseek-v4-pro (183
questions, H-F; McNemar p = 1.1e-16, 59 questions only XbrlRag got right against 1 only the Opus
agent got right). It did so:

- **with an audit trail**: 97.8% of the facts it cites are the right facts, and every cited
  number resolves to a source XBRL fact; the agents' answers carry no such link;
- **with fewer confident wrong answers**: 2.9% of all 441 answers, against 7.0% and 12.9% for the
  two agents;
- **at about a fifth of the Opus agent's cost and with far shorter tails**: $0.022 per question
  against $0.112 and $0.082, p95 latency 28 s against 77 s and 320 s.

On simple single-filer lookups and computations every system does well (XbrlRag 96.3%, the
agents 97.4% and 96.7%), and XbrlRag is within 5 points of an oracle that is handed the right
filing.

**Against the pre-registered success bar, the look is a miss on two of its lines** (§3). The bar
asked for at most one tenth of the Opus agent's cost on the hard questions; XbrlRag ran at 0.168
(about one sixth). It asked for at most 1% confident-wrong; XbrlRag had 2.9%. Every other line of
the bar is met. On the hand-written set H-X, XbrlRag did not confirm the direction (27 against 33
of 66 for the Opus agent; §4). Both are set out below, with the limitations.

## Why these questions are hard

The line-1 kinds of the bar are the questions an analyst cannot answer from one filing:

- **cross-filer**: rank or screen a metric across many companies, which needs the same concept
  read consistently from every filer;
- **cross-period**: follow one company across fiscal periods (growth, CAGR), which needs fiscal
  calendars and restatements handled;
- **compound**: two dependent steps, such as "of the top N by revenue, which has the highest
  margin" or "how fast did the leader grow", where the second step works on the result of the
  first.

For a tools agent each of these means many API calls and many numbers to keep straight. The
agents hit their per-question budget on 10–11% of H-F questions and answered about a fifth of
those correctly. XbrlRag answers them with deterministic SQL over a
fact store, and the LLM narrates rather than calculates.

## H-F results (the bar set)

Grader 14 (the look results).

| | XbrlRag | Opus agent | v4-pro agent | Oracle | Closed-book | G2 XbrlRag (openai-6.1) |
|---|---|---|---|---|---|---|
| Cross-filer | **84.4%** (81/96) | 32.3% (31/96) | 27.1% (26/96) | - | 0.0% | 81.2% (78/96) |
| Cross-period | **100.0%** (36/36) | 100.0% (36/36) | 100.0% (36/36) | 66.7% (24/36) | 0.0% | 100.0% (36/36) |
| Compound | **76.5%** (39/51) | 60.8% (31/51) | 41.2% (21/51) | - | 0.0% | 74.5% (38/51) |
| **Hard questions, pooled** | **85.2%** (156/183) | 53.6% (98/183) | 45.4% (83/183) | 66.7% (24/36) | 0.0% | 83.1% (152/183) |
| Lookup + compute | 96.3% (260/270) | 97.4% (263/270) | 96.7% (261/270) | 92.6% (250/270) | 11.5% (31/270) | 96.3% (260/270) |
| Confident-wrong | **2.9%** (13/441) | 7.0% (31/441) | 12.9% (57/441) | 5.8% (17/294) | 1.6% (7/441) | 2.5% (11/441) |
| Grounding precision | **97.8%** | n/a | n/a | n/a | n/a | n/a |
| Mean cost per question | $0.0220 | $0.1121 | $0.0823 | $0.0986 | $0.0034 | $0.0075 |
| Latency p50 / p95 (s) | 10.6 / 28.2 | 10.5 / 76.5 | 17.5 / 319.6 | 3.1 / 7.1 | 3.2 / 7.3 | 12.2 / 44.2 |

The oracle answers single-filer questions only (its 147 multi-filer cases are excluded by
design). Closed-book answers from the model's memory alone and gets almost none of them right
without the filings. The XbrlRag column is the reference run on the `anthropic` profile; the G2
column is the same system on the `openai-6.1` profile at OpenAI's Flex tier, and decides nothing
in the bar.

What the table shows:

- **Cross-filer is where the gap is widest**: 84.4% against 32.3% and 27.1%. Reading one concept
  consistently across many filers is what the fact store is built for.
- **Compound questions**, the hardest kind, stay at 76.5% for XbrlRag, 15.7 points above the Opus
  agent and 35.3 above v4-pro.
- **Reliability**: XbrlRag's confident-wrong rate (a wrong answer stated without hedging or
  refusal) is less than half the Opus agent's and less than a quarter of v4-pro's.
- **The same system on a cheaper provider** (G2) answered 83.1% of the hard questions (152 of
  183, against 156 for the reference run), with identical lookup + compute (260 of 270) and a
  confident-wrong rate of 2.5%, at $0.0075 per question, about a third of the reference run's
  cost at list price (costs are recorded at Standard prices; Flex bills half). Its p95 latency is
  longer (44 s against 28 s). The two XbrlRag runs were not tested against each other, and G2's
  cost ratio on the hard questions was not computed; G2 decides nothing under §3.
- **Item 6** (does a baseline come close?): neither tools agent comes close on the hard questions
  (−31.7 and −39.9 points). The oracle comes close on lookup + compute (XbrlRag +3.7 points,
  p = 0.087), the expected outcome for a system handed the right filing.

### The pre-registered bar (§3)

| Line | Result | |
|---|---|---|
| 1. More accurate than the Opus agent on the hard questions … | 85.2% vs 53.6%, p = 1.06e-16 | met |
| … at at most 1/10 of its cost | **0.168** (the v4-pro agent, for context: 0.199) | **missed** |
| 2. html_rag | withdrawn (Amendment 1 D4) | - |
| 3. Within 5 points of the oracle on lookup + compute | +3.7 points over 270 cases | met |
| Confident-wrong at most 1% | **2.9%** (13/441) | **missed** |
| Grounding precision at least 95% | 97.8% | met |
| Paraphrase stability at least 90% | 136 of 147 intents (92.5%) | met |
| **Bar** | | **missed** |

Line 1 is one line in §3 (accuracy and cost together); it is split here to show which half
missed. The bar is read against the Opus agent only (D1').

## H-X (direction only)

H-X is 75 questions written by hand by the maintainer over development filers. It is read for
direction only (§4), with Wilson 95% intervals; text is left out because no text judge passed
calibration (Amendment 2 item 4). H-X is unchanged across graders 12–14.

| Arm | All but text | Cross-filer | Cross-period | Compound | Honesty |
|---|---|---|---|---|---|
| XbrlRag | 27/66 = 40.9% [29.9, 53.0] | 13/24 | 10/15 | **2/18** | 2/9 |
| Opus agent | 33/66 = 50.0% [38.3, 61.7] | 18/24 | 13/15 | 0/18 | 2/9 |
| v4-pro agent | 28/66 = 42.4% [31.2, 54.4] | 14/24 | 11/15 | 0/18 | 3/9 |
| Oracle | 7/19 = 36.8% [19.1, 59.0] | 0/4 | 7/15 | - | - |
| Closed-book | 5/66 = 7.6% [3.3, 16.5] | 0/24 | 3/15 | 0/18 | 2/9 |
| G2 XbrlRag | 22/66 = 33.3% [23.2, 45.3] | 11/24 | 10/15 | 0/18 | 1/9 |

**Direction: not confirmed** (XbrlRag 27 against the Opus agent's 33; against v4-pro, 27 against
28). The intervals overlap widely, and H-X is too small to say either system is better on it. On
the hard questions the Opus agent comes close (54.4% against 43.9%, p = 0.21), so on H-X the
comparison rests on reliability, auditability and cost: XbrlRag's confident-wrong rate is 13.3%
against 25.3% and 37.3% for the agents, at $0.031 per question against $0.105 and $0.057. Its
grounding precision on H-X is 68.1%, well below H-F's 97.8%. The H-F advantage on cross-filer and
cross-period questions did not carry over to the hand-written questions of H-X.

## Grading corrections (Amendments 4 and 5)

Two corrections were made after held-out aggregates were seen, found and specified on
development data only; both apply to every arm alike and change no system, key, case or
threshold.

- **Grader 13** (Amendment 4, [compound-value-correct](../../specs/compound-value-correct.md)):
  a compound is value-correct when every graded part is, cited or not. Before, an uncited but
  right part never counted, so no arm but XbrlRag could score on a compound.
- **Grader 14** (Amendment 5,
  [prose-and-ranking-names](../../specs/prose-and-ranking-names.md)): the grader reads a JSON
  reply's `"text"` as prose, and the ranking part of the two generated compound shapes is judged
  on the set of the top N names, which is all their questions ask. Checked against a blind human
  reading of 30 development answers: it credits 18 of the 20 marked right and none of the 10 marked
  wrong.

Both corrections raised the agents' scores more than XbrlRag's, and XbrlRag's lead survives them. Figures that differ across
graders (H-F; all others are identical):

| Figure | Grader 12 | Grader 13 | **Grader 14** |
|---|---|---|---|
| XbrlRag compound | 33/51 | 39/51 | **39/51** |
| Opus agent compound | 0/51 | 12/51 | **31/51** |
| v4-pro agent compound | 0/51 | 6/51 | **21/51** |
| G2 XbrlRag compound | 33/51 | 38/51 | **38/51** |
| Hard questions, XbrlRag / Opus | 82.0% / 36.6% | 85.2% / 43.2% | **85.2% / 53.6%** |
| Hard questions, v4-pro | 33.9% | 37.2% | **45.4%** |
| McNemar vs Opus | p 2.1e-25, 83 vs 0 | p 1.3e-23, 77 vs 0 | **p 1.1e-16, 59 vs 1** |
| Confident-wrong, Opus / v4-pro | 10.2% / 15.4% | 10.2% / 15.4% | **7.0% / 12.9%** |
| Value-correct on budget hits, Opus / v4-pro | 16.0% / 6.8% | 20.0% / 9.1% | **22.0% / 20.5%** |

Neither correction could move the two missed lines: cost does not depend on grading, and
XbrlRag's confident-wrong could only fall and did not.

## Failure classification (§5)

XbrlRag's grader-14 misses (not value-correct, no run error, not a judged text case) were
classified by the maintainer, who did not do the tuning, on a seeded sample: 40 of 69 misses
(seed `g1-failclass-v1`, stratified by set and kind, largest remainder; H-F 22 of 37, H-X 18 of
32). Only these counts left the look folder.

| Class | All (40) | H-F (22) | H-X (18) |
|---|---|---|---|
| Key | 18 | 15 | 3 |
| Entity | 8 | 6 | 2 |
| Plan | 5 | 1 | 4 |
| Grader | 4 | 0 | 4 |
| Other | 3 | 0 | 3 |
| Concept | 1 | 0 | 1 |
| Synthesis | 1 | 0 | 1 |
| Phrasing, period | 0 | 0 | 0 |

By kind: key misses fall on compound (8), compute (6), screen (3) and breakdown (1); entity
misses on lookup (4), compute (3) and screen (1); all 4 grader misses are H-X compounds.

- **On H-F, most sampled misses were classified as key errors**: 15 of 22 = 68% (Wilson 95%
  [47, 84]); over both sets 18 of 40 = 45% [31, 60]. A key mark covers a key that is wrong and a key
  that is one defensible reading among others (a screen's membership, the concept chosen, how a
  derived metric is built). XbrlRag's H-F figures are best read as lower bounds; the agents'
  misses were not classified, so how much they lose to the same keys is not known.
- **XbrlRag's own H-F errors are mostly entity resolution** (6 of the 7 non-key misses), and no
  miss was classified as phrasing or period.
- **On H-X the misses are spread**: plan 4, grader 4, key 3, other 3, entity 2. The grader marks
  all fall on H-X compounds, a grading path not validated on agent answers (see Limitations).

## Logged departures (§2)

- 2026-11-18: the batch stopped twice before a run had answered anything (the held-out Qdrant was
  down; an argument-passing bug in the batch script at the oracle H-X smoke check). Both were
  fixed and the batch restarted; no answer was affected.
- 2026-11-18: the v4-pro H-F main run continued through DeepSeek's peak-price window (the
  maintainer's decision; about $4 more on the bill; recorded list-price cost and answers
  unaffected).
- 2026-11-20/21, after the look and the grader-14 re-grade: while reading traces for the failure
  classification, the maintainer asked 8 questions in the held-out trace UIs (H-F 3, $0.0621;
  H-X 5, $0.0511). They wrote only audit rows; both corpora were checked unchanged afterwards and
  every look run's stored results predate them. Nothing from them was shared with an agent or
  enters any figure.

## What was run

19 runs (G1: XbrlRag, both tools agents, the oracle and closed-book on H-F and H-X, plus set B;
G2: XbrlRag on `openai-6.1` at Flex), 2026-11-18 to 2026-11-19, all clean, **$156.72** at list
price. Each run finished below its cap. The corpora were checked before the first run and after
the last (`xbrl_heldout` `pm:cc7bd7398c9b9cf0`, `xbrl_hx` `pm:ca0f8f27e42d39d9`) and again before
each re-grade and the failure classification. Item 7 (served model ids): no run had a call served
under an id outside the freeze record, so every run is a look result.

## Limitations

- **Two bar lines missed.** Cost on the hard questions is 0.168 of the Opus agent's (bar: at most
  0.10), and confident-wrong is 2.9% (bar: at most 1%).
- **H-X does not confirm the direction**, and XbrlRag's H-X grounding precision is 68.1%; neither
  was investigated at this look.
- **Keys.** Keys take values from SEC companyfacts, but the concept, the definition of a derived
  metric and a screen's membership are our choices. A development key built on a broader concept
  than the filer's preferred one was found (Amendment 5), and the failure classification suggests
  such issues are common among XbrlRag's H-F misses. Not corrected for this look.
- **Grading was corrected after the look** (Amendments 4 and 5), on development evidence only and
  raising the agents' scores more than XbrlRag's; graders 12–14 are all reported above.
- **H-X compound grading is not validated for agent answers.** Graders 13 and 14 were validated on
  the generated compound shapes, not on the hand-written H-X compounds, where all three
  non-XbrlRag systems score 0 of 18.
- **Tools-agent budgets bind.** On H-F, the Opus agent hit its budget on 11.3% of questions (all
  cost) and the v4-pro agent on 10.0% (tool calls 8, steps 17, cost 19), both above the 5%
  handicap line (Amendment 2 item 2); value-correct on those questions was 22.0% and 20.5%. On H-X,
  v4-pro hit 5.3% (steps), Opus none. The agents were measured at these budgets.
- **v4-pro errors.** 8 H-F questions ended in the agent's own error (none of transport).
- **Text.** No text-judge score is reported: no judge passed calibration (Amendment 2 item 4).
- **Set B** (secondary, XbrlRag only): 40 of 47 value-correct = 85.1% [72.3, 92.6], 1
  confident-wrong, $0.0174 per question. Its grounding precision reads 0.067, unexplained at this
  look.
- **Service tier** was not recorded for the Anthropic and DeepSeek runs (G2 records Flex). Costs
  are list-price costs throughout.
