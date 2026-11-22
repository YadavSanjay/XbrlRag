# XbrlRag v2 evaluation: pre-registration

**Status:** sealed by the merge of this file into `main`; the time GitHub records for that
merge is its timestamp. H-X is written only after that merge
([v2 plan](../../design/XbrlRag_v2_Plan.md) §10, decisions 2 and 7).

This document fixes how the v2 evaluation is read before anyone writes a held-out question
or sees a held-out result. It is separate from
[external test v1](../external_test_v1/README.md), whose sealed files stay as they are. The
question sets, systems and grading are those of the v2 plan §3; the filers are those of the
[draw record](README.md). The plan gives the reasons, and this document gives the rules.

## 1. The question sets and the looks

| Set | Role in the result |
|---|---|
| **H-F:** generated questions on the 8 drawn filers, in the quarantined database | Decides the success bar (§3) |
| **H-X:** 75 questions written by hand by the maintainer alone, 15 each on cross-filer, cross-period, compound, honesty and text, over development filers only | Confirms direction (§4) |
| **H-T:** filings published after the v2 freeze | Reported at the final milestone, on its own line |
| **Set B:** the 47 questions of external test v1 | At most one secondary check at the final milestone, on its own line |
| **Dev and validation** | Tuning and checking fixes; never evidence for the bar |

Sets are reported separately and never pooled.

**Looks:** H-F and H-X run at no more than three looks, one at each checkpoint: after the
baseline measurement (phase 4), after the fixes (phase 5), and at the final milestone.
H-T stays untouched until the final milestone. Each look is logged with its date, git sha
and purpose. Only the final milestone decides the bar; the earlier looks report aggregates
and failure-class counts and declare nothing.

## 2. Systems

All systems of the v2 plan §3.6 run at each look: `xbrlrag_full`, `html_rag`,
`frontier_sec_tools`, `long_context_multi`, `long_context_resolved`, `long_context_oracle`,
`naive_rag_filtered` and `closed_book`.

**Profile:** the decision runs use `openai-5.6`, and `xbrlrag_full` and the matched baselines
use its tiers. Other profiles are secondary and never decide the bar. Cost is what the gateway
records, priced by `configs/llm/pricing.yaml` at the commit of the run, and excludes
infrastructure and ingest.

**`frontier_sec_tools` settings** ([spec](../../specs/frontier-sec-tools.md)):

| Setting | Value |
|---|---|
| Model | `gpt-5.6-sol` |
| `max_steps` | 20 |
| `max_tool_calls` | 60 |
| `max_cost_usd` per case | $2 |
| SEC data | One snapshot per look, recorded close to `as_of` |

These settings have one exception, in the arm's favour. If the arm's pilot shows more than 5% of
cases hitting the step or cost budget, the budget is raised and the pilot rerun (pilot check 3
of the spec). The new value is recorded by amendment before the first look. A budget is never
lowered.

## 3. The success bar

On H-F at the final milestone (H-F carries the power; H-X confirms direction):

- XbrlRag beats `frontier_sec_tools` on the cross-filer, cross-period and compound kinds
  (McNemar p < 0.05) at **no more than a tenth of its cost per question**;
- XbrlRag beats `html_rag` on the single-filer numeric kinds (p < 0.05);
- XbrlRag is within 5 points of `long_context_oracle` on single-filer lookup + compute;
- confident-wrong ≤ 1%, paraphrase stability ≥ 90%, grounding precision ≥ 95%.

Every line must hold for the bar to be met. A line that does not hold is reported as missed.

**How the bar is read:**

- **Comparison metric:** value accuracy, for every system. The baselines cite no `fact_id`s,
  so accuracy is never used to compare with them (`arm-neutral-grading`).
- **Which cases:** every case of the kind in H-F. A decline counts as a failed case, and so
  does an errored case. A structural exclusion is counted and reported, and a pair is left
  out of a test only when the case was excluded for that system.
- **"Beats":** a two-sided exact McNemar test on the paired cases gives p < 0.05, and XbrlRag
  passes more of the discordant pairs. Each test stands alone: because every line must hold,
  no multiple-comparison correction is applied.
- **"The cross-filer, cross-period and compound kinds":** one pooled test over the three,
  with each kind also reported on its own line. The cost ratio is the mean cost per question
  over the same pooled cases, for each system.
- **Kinds, by suite shape:**
  - cross-filer: the `screen.*` shapes and `peer_relative`;
  - cross-period: `trend`, `point_in_time` and `compute.cagr`;
  - compound: a screen with a dependent compute or narrative (plan §3.4);
  - single-filer numeric: `lookup`, `dimensional`, `breakdown` and the single-filer `compute.*`
    shapes other than `compute.cagr`;
  - single-filer lookup + compute: `lookup` and the single-filer `compute.*` shapes.
- **"Within 5 points":** XbrlRag's value accuracy is at least `long_context_oracle`'s minus
  5.0 percentage points, as point estimates on the same cases.
- **Confident-wrong, paraphrase stability, grounding precision:** XbrlRag's own rates over its
  H-F cases, as point estimates. Stability is per intent, across all of an intent's phrasings.

## 4. What "H-X confirms direction" means

H-X has 75 questions, too few for a test of its own, so it gives a direction and no more:

- H-X **confirms direction** when, on the 75 questions, XbrlRag's pass count is higher than
  that of `frontier_sec_tools` and higher than that of `html_rag`.
- No significance claim is made from H-X. Counts are reported as a count over n with a Wilson
  95% interval.
- Only cases with a reportable grade count. If the judge has not passed calibration, text
  cases are left out for every system and the report says so.
- H-X does not change the verdict on the bar. If it does not confirm direction, the report says
  so beside the verdict.

## 5. Quarantine

- The H-X authoring file and built suite stay outside the repository, in the maintainer's
  vault. Nobody but their author sees a question, an answer or a passage before a look, and
  afterwards only aggregates are shared.
- H-X is written without LLM help, and no H-X question is run through any system, or pasted
  into a session or a web UI, before its look.
- H-F questions are generated blind, and their suite files are not opened, searched or
  sliced. Operators of the held-out environment read exit codes and counts only.
- No held-out set is used for tuning. A held-out miss is classified by a person who did not
  do the tuning, and only the counts per failure class leave the run.
- Any accident is logged with its date, what happened and why, and published with the results.

## 6. What comes later, by amendment

Each item below is fixed by a numbered amendment with its date and reason, before the first
look:

1. the SHA-256 of the sealed H-X suite, which can only exist after H-X is written;
2. the hashes of the dev, validation and H-F suites and of the frozen paraphrase set;
3. the code commit and freeze record for each look;
4. the list of runs, their repeats and their hard cost caps; a system run more than once
   counts a case as passed when it passes in at least 2 of 3 runs, as in external test v1;
5. the judge version and its calibration result;
6. what counts as "a baseline comes close" (plan §3.6, fixed before each milestone);
7. the rule for milestone results when a served model id differs from the freeze record.

**Known gap:** the suite generator has no compound shape yet, and the bar needs compound cases
on H-F. They must exist in H-F before the first look. Honesty and narrative shapes are missing
too, though no line of the bar depends on them.

## 7. Changes

The sections above add detail to the v2 plan and change none of its decisions. Amendments may
add detail. Once H-X writing has started, §3 and §4 are not changed; an error in them is
corrected only by a dated, numbered amendment that states the error and is disclosed with the
results. Every look's results are reported, whatever they show.

## 8. Amendments

### Amendment 1 (2026-11-04)

The merge of this amendment is its timestamp. Four values that need later measurement come by
Amendment 2, before the final look (listed at the end of this amendment). It puts into effect the decisions of the
[measurement plan](measurement_plan.md) (decided 2026-11-03), which gives the reasons and
the cost and power arithmetic. §3 and §4 change only where D4 says, before H-X writing has
started (§7).

#### Stated deviations from §2

These change §2 rather than add detail to it, and the results disclose them.

**D1. The decision profile is `openai-6.1`, at OpenAI's Flex tier.** §2 named `openai-5.6`.
The decision runs use `gpt-6-luna` (small), `gpt-6.1-terra` (medium) and `gpt-6.1-sol`
(frontier), and `frontier_sec_tools` uses `gpt-6.1-sol` in place of `gpt-5.6-sol`. *Reason:*
gpt-6.1 lists at about half of gpt-5.6's price, it is a newer generation and so less exposed
to retirement mid-campaign, and gpt-5.6-sol's price was promotional. The choice was made on
price and model generation, before any held-out measurement and with no comparison run.
*Cost:* the gateway records Standard (list) prices from `configs/llm/pricing.yaml`, so the
caps and every published cost per question are list-price costs; the bill was at Flex
rates, which halve every token class. If Flex is unusable on the day of a look (judged by
the frontier smoke's 429 count and latency), that look runs at Standard and says so.

**D2. Not every system runs on every set, and three arms are dropped.** §2 said all systems
run at each look. The matrix is:

| Arm | Model | H-F | H-X | H-T | Set B |
|---|---|---|---|---|---|
| `xbrlrag_full` | decision profile | yes | yes | yes | yes |
| `frontier_sec_tools` | `gpt-6.1-sol` | yes | yes | yes | no |
| `frontier_sec_tools` | `deepseek-v4-pro` | yes | yes | no | no |
| `long_context_oracle` | decision profile | single-filer cases | single-filer cases | no | no |
| `closed_book` | decision profile | yes | yes | yes | no |
| `naive_rag_filtered` | | no | no | no | no |

*Reason:* each arm runs where it answers a question of the bar or of the report.
`naive_rag_filtered` answers "how bad is basic RAG", which is not the claim, so it runs once on
dev. `html_rag` is dropped by D4.

`long_context_resolved` and `long_context_multi` are dropped (decided 2026-11-05). Pasting
whole filings into a chat is not how analysts in a regulated enterprise are expected to
work, so neither arm stands for a realistic competitor. Neither is read by the bar.
`long_context_oracle` stays: it is handed the right filing, it is the ceiling for the
single-filer kinds, and bar line 3 needs it. Dropping it would flatter XbrlRag.

**D3. One "not a GPT quirk" line, on `anthropic`.** The v2 plan (§7.3, §11) promised that
milestones repeat on the `openai-5.6` and `anthropic` profiles. Instead, `xbrlrag_full` runs
once more on the `anthropic` profile at the final look, on H-F and H-X, at list price. That
line decides nothing.

**D4. `html_rag` is dropped, and with it bar line 2.** §2 names `html_rag`, §3 has the line
"XbrlRag beats `html_rag` on the single-filer numeric kinds", and §4 compares H-X pass counts
with `html_rag`'s. From this amendment `html_rag` runs on no set, that line of §3 is withdrawn,
and §4's direction test compares XbrlRag with `frontier_sec_tools` only. The other lines of §3
are unchanged. *Reason:* the competitor an analyst actually has is a frontier model with tools
over the SEC's public APIs, and those APIs serve XBRL facts. Nobody would answer numeric
questions over tagged data with a RAG pipeline over parsed HTML, so beating one would show
little. The comparison that decides the bar is XbrlRag against `frontier_sec_tools`
(`docs/design/XbrlRag_HtmlRag_Decision.md`). *Seen before the change:* `html_rag`'s in-loop
pilots (51 cases on 2026-11-01, and a 24-case candidates check). No arm had run on a held-out
set, and H-X writing had not started. *Effect:* XbrlRag's narrative retrieval has no RAG
comparator; it is compared only with `long_context_oracle` and the agents, and the results say
so.

**D5. A second tools agent, on DeepSeek.** `frontier_sec_tools` also runs on
`deepseek-v4-pro`, with the same tools, prompt and budgets. *Reason:* if a cheaper model with
the same tools matches the frontier agent, XbrlRag's cost advantage must be shown against
the cheaper agent, or the claim is about model price rather than architecture. The bar
(§3) is still read against the `gpt-6.1-sol` agent. The cost ratio against the DeepSeek
agent is reported beside it.

**D6. The tools agents' budgets may be lowered, once, before the look.** §2 fixed
`max_steps` 20, `max_tool_calls` 60 and `max_cost_usd` $2 per case, and says "A budget is never
lowered". This amendment departs from that sentence. At the smoke's measured $0.09 to $0.15 per
case, the $2 ceiling would allow about $1,050 on H-F and H-X at list price, so it bounds
nothing. *The rule that replaces it:* each budget is set from the dev-sample runs, once and
before the look, at the lowest value that binds on at most 5% of dev-sample cases, which is the
threshold §2 already uses for raising a budget. The values come by Amendment 2, from the
dev-sample runs; the rule above is fixed here, so setting them is mechanical. The results report,
per agent, how often each budget was hit and the accuracy on those cases separately. If the
budgeted cases are mostly wrong, the budget decided the result, and that is disclosed. A budget
that binds on more than 5% of cases at the look is reported as a handicap. When a budget is hit,
the agent gives one final answer without tools, as the arm already does. The 250,000-token
window is unchanged (1.5 below).

#### Detail added to §1 and §2

**1.1 Looks.** H-F and H-X are looked at once, at the final milestone, which §1 allows (no
more than three looks). There is no held-out checkpoint after the baseline or the fixes. In
its place, the generated suite runs on the wave-2 development filers (XOM, V, TMUS, JNJ, PEP,
BA, CAT, DIS) right after their ingest and before any override or alias is written for them;
it is a development measurement, read case by case, and never evidence for the bar.

**1.2 Phase order.** The baseline measurement (phase 4) runs before phase 3b (quarterly
filings). Phase 3b runs within phase 4b with its own quarter slice, and its cases are
reported on their own line. *Reason:* 3b needs about nine changes (10-Q labels, download
scope, publish gate, Q4 derivation, period resolution, quarter keys and phrasings), and the
annual baseline depends on none of them. *Effect:* the baseline covers annual (10-K and 20-F)
questions only, and says so.

**1.3 Development models.** Development, pilot and dev-split runs of `xbrlrag_full` (its dev
baseline, the fixes, the slices and the validation runs) use the `deepseek` profile. The
baseline arms are measured on the dev sample (1.4) twice: on the `anthropic` profile, paid by an
Anthropic credit, and on the `deepseek` profile, which isolates architecture from model. The
credit expires on 2026-11-15. A run on the `anthropic` profile that is ready only after that
date, which is likely for `frontier_sec_tools` because it waits on its freeze (1.6), runs at
Anthropic's list price and is not dropped (maintainer decision, 2026-11-09). Costs are
reported at list price in either case.
OpenAI's gpt-6.1 is used only for the smokes, two validation runs of `xbrlrag_full` (below) and the final look. Baselines
never run on validation: they receive no fixes, so there is nothing for validation to check.

**1.4 The dev sample.** `frontier_sec_tools` and `long_context_oracle` measure dev
on a fixed sample, not the whole dev suite: 100 cases, one phrasing per intent, stratified by
shape over intent counts, seeded (`scripts/eval/build_dev_sample.py`, seed
`xbrlrag-v2-dev-sample-1`). It holds 76 single-filer and 24 cross-filer cases;
`long_context_oracle` reports the cross-filer ones as not applicable. `closed_book`,
`naive_rag_filtered` and `xbrlrag_full` run the whole dev suite.

**1.5 `frontier_sec_tools` window.** The arm estimates its prompt as characters / 4. In a
development pilot on `deepseek-flash` (20 dev cases, 95 calls), the provider counted a median
2.1 characters per token (1.75 to 3.0), so the estimate read about half the real count. The
decision runs keep the spec's window (250,000 estimated tokens), because D6 lowers only the turn,
tool-call and cost budgets. The arm's smoke on `gpt-6.1-sol` reports its own ratio and
any call that fails on the provider's context limit; if one does, the window is set by an
amendment before the look. In the development pilot, 0 of 20 cases hit the step or cost
budget.

**1.6 Freezing `frontier_sec_tools`.** Before its dev-sample runs, the arm is checked as any
arm is: a DeepSeek smoke of 10 cases, then a dev slice across the kinds. Both are read case by
case for defects in the arm's mechanics only (tool errors, truncated results, budget handling,
answers that cite no tool result), never to tune its accuracy. Its tools and prompt are then
frozen, its budgets are set by D6, and it gets no fix after its dev-sample runs. The commit is
recorded with the look (§6 item 3).

#### Detail added to the report

**R1. XbrlRag against `frontier_sec_tools`.** The results report XbrlRag and both agents in one
table, on the same H-F and H-X cases:

- value accuracy on the cross-filer, cross-period and compound kinds, pooled and per kind;
- the confident-wrong rate (`docs/specs/arm-neutral-grading.md`);
- grounding precision, for XbrlRag only. The agents cite no `fact_id`, so their cell says
  "not applicable" rather than being left blank;
- mean cost per question;
- latency per question, p50 and p95, end to end, from the runner's per-case `latency_ms`. Each
  arm's service tier and concurrency are stated beside its latency. Latency decides nothing.

#### §6 items

**Item 2: suite hashes.** On 2026-11-05 a key audit found answer keys that name a different
measure from the question: the namespace-expanded rows of `concept_mapping` kept ranks from
before the concept-key integrity fix, so a component concept (for example shares issued, for
"shares outstanding") still keyed some cases. The suites were rebuilt on 2026-11-06 after the
fixes of `docs/specs/ns-expand-rank-sync.md`, `docs/specs/family-measure-equivalence.md` and
`docs/specs/shares-outstanding-reading.md`. No decision run used any earlier freeze.

**Why the keys changed again after the freeze of 2026-11-06.** A key-fairness review
(2026-11-07 to 2026-11-09) found answer keys that a filer's own financial statements do not
support: keys that took their concept from our concept mapping rather than from what the filer
presents, keys for metrics with more than one reasonable definition, and keys for restated
periods. No decision run used a freeze built before these fixes. No held-out set was read or run
for any of this work (§5). Each change below was decided before the rebuild and stated in a
merged spec. None was chosen from a system's results.

**K1. Statement-line witness** ([key-statement-witness](../../specs/key-statement-witness.md),
decisions 1-4 approved 2026-11-08). Every key input is checked against the filer's primary
statements in SEC's Financial Statement Data Sets. A key that the statements contradict, or
that no statement line or listed note concept witnesses unambiguously, is blocked. It is either
resolved by a reviewed entry in `configs/eval/v2/key_resolutions.yaml`
([reviewed-resolutions](../../specs/reviewed-resolutions.md): each added entry carries a review
record by an allow-listed reviewer, and an entry that covers no blocked key fails the build) or
left out of the suite. The build records the counts in the manifest's `key_independence`.

**K2. Statement-line rules r2** ([witness-rules-r2](../../specs/witness-rules-r2.md), accepted
2026-11-09). The rules were rewritten from accounting vocabulary and us-gaap definitions only.
They were developed on a dev sample of filings and judged **once** on a separate, frozen judging
sample, against marks set in the spec before any rule was written (precision ≥ 0.992, recall
≥ 0.930, ambiguity ≤ 0.025, and a per-family rule). Result
(`configs/eval/v2/statement_lines.acceptance.json`, commit e56c1d6, merged in PR 351):

| | Recall | Precision | Ambiguity | Families regressed |
|---|---|---|---|---|
| r1 (before) | 0.8843 | 0.9950 | 0.0414 | |
| **r2 (accepted)** | **0.9317** | **0.9950** | **0.0208** | 0 |

Evaluable filing-families: 3,603. r2 rules SHA-256
`947ba5c6ac81628c96edcfe9661a1de86fd3dd314e0331947fab70fa67ee4a9b`; oracle SHA-256
`c7acf80693054531b3b2957e311aca633268d9c46fb935cf1801fca02ad80f43`; r1 rules SHA-256
`661bed5dc7d7d458e0402cb4d8e043f0979f474bfe4d4a113a625b0aa2eff295`.
*Disclosed:* recall fell 3.65 points between the dev sample and the judging sample, against
1.65 points for r1, so r2 fits its dev sample somewhat. Recall passed the 0.930 mark by 0.0017.
**That judging sample is spent.** It is never read again.

**K3. Statement-line rules r2.1** ([witness-rules-r2-1](../../specs/witness-rules-r2-1.md),
marks decided 2026-11-09, PR 357). One measurement of the corpus with r2 (2026-11-09) went from
94 blocked keys to 70. That was 34 blocks cleared and 10 new ones, and the 10 new blocks came
from four over-broad r2 exclusions. (That measurement, like the one before it, was built without
`--groups`; the freeze below is built with the frozen arguments.) r2.1 does not add rules for
corpus lines. It adds two invariants checked mechanically on the rules:

- the us-gaap standard label of an oracle element is never rejected;
- no exclude or require term starts inside a word.

It also adds three oracle elements, chosen from their us-gaap 2025 definitions. r2.1 is judged
**once**, on a fresh judging sample (judge2: seeded positions 240-359 of r2's population,
disjoint by CIK from the dev sample at 0-119 and the spent sample at 120-239). It is judged
**against r2 on the same filings**, not against a fixed recall mark. It is accepted when:

- precision ≥ 0.992;
- ambiguity ≤ 0.025;
- its hits are no fewer than r2's;
- no family with at least 10 evaluable filings loses more than one filing and more than
  0.05 recall.

The absolute 0.930 recall mark is dropped (maintainer decision, 2026-11-09). The reason: r2's
margin of 0.0017 is well inside the sampling error of about 0.004, so a fixed mark would test
sampling noise, not the change. The judge2 draw is pinned by SHA-256 before any read:

- `sub` `6cf3c9384d7662f4501d0b39a0ac64298c6ee4a6070a8d7dbf0f29e9fc123354`
- `pre` `ae8aec97e1e86ef5d8f96f624e5a8fbc286c85f7e23686976b508e9bacbb6edf`
- `num` `8a2ce00f08d3c0367af69c5b1e02d4b1e6fc37a65371ee4392332d91a51d3d38`
- `tag` `cdc7e2b7ef1bdd808d90fd3850176f8ec1e6bd1a46f13c4f6d6a5cded3b0f1c8`

The r2.1 oracle SHA-256 is `7cde3f087888f8e029ffd89d27bef452d0cf456a5e2002a77aca9968d76bf9ca`.

| | Hits | Wrong | Ambiguous | Evaluable | Recall | Precision | Ambiguity | Families regressed |
|---|---|---|---|---|---|---|---|---|
| r2 on judge2 (base) | 3,501 | 19 | 88 | 3,728 | 0.9391 | 0.9946 | 0.0236 | |
| r2.1 on judge2 | 3,501 | 19 | 88 | 3,728 | 0.9391 | 0.9946 | 0.0236 | 0 |

Verdict: accepted, no failed criterion, read once on 2026-11-09. r2.1 scored the same totals
as r2 on judge2. Its changes target standard-label and word-boundary defects, which are rare
in a random sample of filers, so their effect shows on the corpus. r2.1 rules SHA-256
(`statement_lines.accepted_r2_1.yaml`):
`1f4612bc4be62c95915ba8e1bc9c48290ac3d026541904ece0bfc35bcc493248`. Record:
`configs/eval/v2/statement_lines.acceptance_r2_1.json`, commit fe5f29f (PR 362). **judge2 is
spent.** After acceptance, the corpus was measured once with r2.1 and the key definitions
(main at 540cefc), with the frozen build arguments: **31 blocked keys** out of 1,037
witnessed. By witness status: 16 `rejected_line`, 10 `ambiguous`, 4 `disagree`, 1
`note_unlisted`. By the reviewer's bucket: 20 `rule_gap`, 4 `combined_line`, 6 `definition`,
1 `note_unlisted`. Each blocked key got a drafted resolution. The maintainer reviewed and
approved all 31 (2026-11-09), and 30 are in `configs/eval/v2/key_resolutions.yaml` (PR 365):
24 keep the key and 6 drop the intent. The 31st, P&G FY2025 `DebtRepaid`, was left out. Once
the other 30 resolve, the build never visits that key: it was reached only as a replacement
for an intent that a block had dropped. The build refuses an entry that covers no kept key,
so it would have been refused. The rules were not changed for any of these blocks.

**K4. One stated definition for four metrics** ([key-definitions](../../specs/key-definitions.md),
decided by the maintainer 2026-11-09, each as recommended):

- **Debt = total debt as presented**: the sum of every borrowing line the balance sheet presents.
  Finance leases count only inside a line the filer calls debt, and operating leases never
  count. Where the balance sheet presents no current debt line, the debt-note total is a
  disclosed alternate reading, `note_total`. Questions say "total debt".
- **Deferred revenue = total**: current plus non-current. Where only the current line is
  presented, that line is a disclosed alternate reading, `current_presented`. This replaces the
  earlier ORCL resolution.
- **Net income and comprehensive income = attributable to the parent.** The consolidated figure
  stands in only when the filing reports no different parent figure. Net income and
  comprehensive income are never accepted for each other.

The locked Debt and DeferredRevenue rules are in `test/spec/key-definitions/fixtures/statement_lines_kd.yaml`
(SHA-256 `fd520cc8382bfcd9740183dd4b3c0ad6f0ae2b08bfb115b48b92b10cd076634d`). Composite keys
(Debt, and DeferredRevenue built from more than one fact) carry no `decimals`, so they are graded
at the case's relative tolerance. A key whose filing repeats a component fact with disagreeing
values is blocked, not keyed. An alternate reading is graded as in
[shares-outstanding-reading](../../specs/shares-outstanding-reading.md): CORRECT within the
tolerance of the key or of the reading, and marked with the reading's name. Readings attach to
lookups and trends only. *Effect on questions:* the Debt surface forms change from "long-term
debt" to "total debt", and the deferred-revenue forms gain "total deferred revenue". The
affected intents therefore get new phrasings. Most keep their case ids, and the re-grade
refuses their stored answers as `question_changed` (below). Implementation: PR 363, merge
commit 540cefc. Live `configs/eval/v2/statement_lines.yaml` after it: SHA-256
`c5c415abc6f66f707016e3fd0ed02954175a3783c81870de363737e103ae320c`.

**K5. Restated periods (S1)** ([restatement-s1](../../specs/restatement-s1.md), approved
2026-11-07; [suite-templates-s1](../../specs/suite-templates-s1.md);
[set-b-restatement](../../specs/set-b-restatement.md)). A restated or split-adjusted value
from the latest periodic report answers. The value as originally reported is available on
request. The v2 keys already followed S1. The in-loop v1 suite (`test/eval/suites/sql_derived_v1.json`,
not a pre-registered set) was rebuilt twice on 2026-11-09:

- commit efe18fe: 507 to 511 cases, one value change (LLY FY2025 growth 0.447049 to 0.447039,
  prior year restated), and 104 cases now cite the latest filing that states the same value;
- commit 44c2949: the same 511 case ids, no value change, and 88 more cases now cite S1 (aq-5a
  50, aq-6a 32, aq-10a 6).

The committed v1 suite's SHA-256 is
`2d5e26964df987881eef097c44ab519c0305b3ec22f0d4b2dafc4d37a7833961`. The key-check store of
set B (external test v1) now reads S1 as well (set-b-restatement, PR 353). Set B's sealed
questions and keys were not opened in this work. Set B's one secondary check at the final
milestone (§1) goes through that store. `scripts/eval/set_b.py` resolves each hand-written
key to its fact through `PostgresKeyFactStore`, so from PR 353 on, those keys resolve under S1.
The hand-written expected values are unchanged.

**The freeze of 2026-11-09.** Rebuilt offline from the SEC cache at main b2aa7d5 (PR 365),
into `data/eval/v2_frozen/`, with the frozen arguments `--total 300 --per-intent 3 --share 0.15
--salt xbrlrag-v2-dev-1 --groups configs/eval/v2/groups.yaml`. The 2026-11-06 freeze is
kept, unchanged, in `data/eval/v2_frozen_2026-11-06/`. A second build from the same arguments
(a dry run at commit 57248f4, the same source tree as b2aa7d5) was byte-identical for all four
build outputs. No question text changed except where K4 changes a surface form (below).

| File | Cases | Content hash | File SHA-256 |
|---|---|---|---|
| `xbrl_v2_dev.json` | 628 | `sha256:2c02bef6442a537b81ee0d98f53990eafca6b1ad35d46454e4e47ff77cf1372e` | `84d86aee460977d95ed84123cfc5768af028c43638d9dd285e509061ffa131e8` |
| `xbrl_v2_validation.json` | 272 | `sha256:3400cf67c7246f4127696bf1991ef1c4aec923d455782d1801df8fb33b325d77` | `1e210087ea414df899be35d6b8f8fe4e4353fb910cfe108a96130d7d575738c0` |
| `intents.json` | | intents hash `sha256:d2ac35e6b47d4fda0559001d2d6666663477cec5aa3e92317802a45806918a24` | `17127f0e27f134e951a730da284296bba0dfdb5989f0457f87568c48c9587b90` |
| `xbrl_v2_dev_sample.json` | 100 | `sha256:72995614f4db58cc08446ac687af2023d1dd8c2325b738b24f5750ffe778add1` (redrawn, seed `xbrlrag-v2-dev-sample-1`; 45 of 100 cases differ) | `105d4ba2dce9aa71040f27f87f9a930e04098990b2f6b8c6051a5d807c90e8b0` |
| `xbrl_v2_manifest.json` | | | `e37ac43cef378fafb1b35503e7c1046562a965cf4171a6c0a46bcafa4c8a4173` |

Against the freeze of 2026-11-06 (from the suite diff, `data/eval/v2_frozen/diff_vs_v2_frozen.txt`):

- **Removed:** dev 54, validation 19. Of these, 5 dev and 2 validation are rephrased K4 intents
  whose phrasing now has a new case id. The other 49 dev and 17 validation belong to intents
  the seeded draw no longer selects. The draw changed because the witness now keys, holds or
  drops different candidates (K2, K3, the resolutions). The largest groups are R&D intensity,
  operating margin, current ratio, growth and CAGR ratios, and investing cash flow, long-term
  debt and buyback lookups. In validation they are net income to common, dividends,
  long-term debt and capex screens.
- **Added:** dev 48, validation 25. 4 dev and 2 validation are rephrased K4 intents; the rest
  are newly drawn intents. The largest groups are net margin (dev 9) and `debt_total` (dev 5,
  validation 3), then buybacks, interest expense (`InterestExpenseNonoperating`, newly keyable
  by resolution), debt repayment and issuance, and current assets.
- **Changed keys:** dev 104 cases, validation 51. Of these, dev 60 and validation 29 changed
  the expected value or rows. The largest causes:
  - the K4 definitions: deferred revenue dev 9, validation 2; total debt dev 5, validation 5;
    parent comprehensive and net income dev 6, validation 3;
  - screens that now rank more members (K2, K3). For example, the FY2024 covered companies'
    long-term borrowings raised went from 126.7B to 179.1B, with MSFT, KO, TSLA, VST, LMT and
    others now ranked;
  - members excluded by a reviewed drop, such as GOOGL FY2024 all-debt proceeds.

  The other dev 44 and validation 22 changed only key fields, such as the definition id, the
  cited accession or the selection, with the same value.
- **Rephrased in place (same case id):** dev 12, validation 5, every one a Debt or deferred
  revenue intent (K4). Examples: "long-term debt" became "total debt", and "contract
  liabilities" became "unearned revenue" or "total deferred revenue".
- **Alternate readings attached:** `note_total` 0 in dev and 0 in validation (no Debt lookup
  or trend of a filer without a presented current debt line was drawn). `current_presented`
  3 in dev and 0 in validation.

Key independence (`xbrl_v2_manifest.json` → `key_independence`, one count over both splits):

| Status | Keys |
|---|---|
| agree | 935 |
| agree_other_element | 10 |
| note_witnessed | 22 |
| value_recast (held) | 1 |
| no_fsds (held) | 39 |
| disagree / ambiguous / rejected_line / note_unlisted | 4 / 10 / 15 / 1, each resolved by a reviewed entry (an unresolved one fails the build) |
| **Total `n`** | 1,037 |
| Resolved by a reviewed entry | 30 (`key_resolutions.yaml` at SHA-256 `1b32fd34ea80d90486aee8bfed61dbb984924bc7c755ac359fcff6f813e0150d`; 30 reviewed, 0 original) |
| Held out of grading | 40 |

The paraphrase sets carry over, except that records of removed cases are dropped. Added and
rephrased cases are paraphrased by the fourth-family model (task E2).
That pass covers 60 dev and 30 validation questions: the cases the rebuild added, and the 12
dev and 5 validation questions rephrased in place. The hashes of `paraphrases_dev_frozen.json`,
`paraphrases_validation_frozen.json` and the derived `_paraphrased` suites come by
Amendment 2. Paraphrases are development evidence only (below), so the bar does not wait on them.
H-F is generated by the same builder, so K1 to K4 apply to it; its hash still comes by the later
amendment.

**Re-grade against the freeze of 2026-11-09.** The stored DeepSeek runs of
`xbrlrag_full` are re-graded from their stored answers against the rebuilt suites, at no cost,
with `scripts/eval/regrade_run.py` (grader version 4; the source runs
were graded under version 3). The re-grade refuses a stored answer whose case the suite no
longer holds or whose question changed, so those answers are left out of the source first with
`scripts/eval/prune_regrade_source.py`, which lists each with its reason. A case the rebuild
added or rephrased has no stored answer. It is answered in the next paid run and never filled
in from a re-grade.

| Source run | Split | Left out | Re-graded run | Accuracy before (stored) | Accuracy after |
|---|---|---|---|---|---|
| `eval-9a99afc97d4b` | dev | 70 of 635 (60 no longer in the suite, 10 question changed) | `eval-e47b82d082a7` | 72.6% (456 of 628, grader v3, as stored) | 75.0% (424 of 565) |
| `eval-0051272815db` | validation | 26 of 265 (21 no longer in the suite, 5 question changed) | `eval-24dde95f4291` | 60.1% (158 of 263, grader v3, as stored) | 66.1% (158 of 239) |

The "before" and "after" columns cover different case sets. On the kept cases alone, the
stored grades were 74.5% (421 of 565) in dev and 64.9% (155 of 239) in validation. The
re-grade changed 12 dev grades: 9 PARTIAL to INCORRECT (screens), 2 INCORRECT to CORRECT and 1
HEDGED_MISS to CORRECT (compute). It changed 9 validation grades: 6 PARTIAL to INCORRECT
(screens), 2 INCORRECT to CORRECT and 1 HEDGED_MISS to CORRECT (compute). Silent-wrong rate
after the re-grade: dev 0.7%, validation 0.0%. The suite cases without a usable stored answer
(dev 63 of 628, validation 33 of 272: added, rephrased, or left out above) are answered by the
next paid run, never filled in from a re-grade.

This item does not change §3 or §4. The bar is read on value accuracy against keys built by
these rules, for every system alike ([arm-neutral-grading](../../specs/arm-neutral-grading.md)).
A definition in K4 is stated in the question and in S4 of the
[eval semantics](../../design/XbrlRag_Eval_Semantics.md), so every arm is asked for the same
measure.

**The superseded freeze of 2026-11-06.** In `data/eval/v2_frozen/` until the rebuild above,
now kept in `data/eval/v2_frozen_2026-11-06/`,
built offline from the SEC cache with the same arguments as the superseded freeze below. No question text changed. Against that
freeze:

- **Removed:** dev loses 7 cases, the three phrasings each of UPS FY2025 operating expenses
  (UPS tags only total costs and expenses) and the retailers' combined operating expenses, and
  one of P&G's shares-outstanding trend. Validation loses 2, P&G's shares-outstanding trend.
  P&G tags outstanding shares only on a dimension, so it has no undimensioned key.
- **Added:** dev gains 6 cases (UPS long-term debt proceeds FY2024, Home Depot long-term debt
  trend) and validation gains 3 (big-tech combined equity FY2025). These fill the freed quota
  slots, drawn by the same seeded selection.
- **Changed keys:** 16 dev and 11 validation cases. The changes are:
  - long-term debt totals, medians and screens that no longer count noncurrent or
    lease-inclusive debt;
  - AMD operating-expense screens;
  - the retailers' median other income (expense), now keyed on `OtherNonoperatingIncomeExpense`;
  - the Qualcomm and Union Pacific shares cases, which gain the cover-page count as a marked
    alternate reading.

| File | Cases | Content hash | File SHA-256 |
|---|---|---|---|
| `xbrl_v2_dev.json` | 634 | `01c4c0336e32e8329036782888aacf653787a9150d2c8509468dbe3a6c743124` | `0f6793ba1ef7b7ffba9d8f74eceed125849be6b9bd5df305d27044e51289c62b` |
| `xbrl_v2_validation.json` | 266 | `e91ff9c145cfb736de3da86959d17a2945c80557adee5f7777cb0fc34b93ead9` | `2aaf81c685bdd60eee2249513d57f6a1382641a650fe14bdb734db498f658438` |
| `intents.json` | | intents hash `ed91d300b2d4a4916bd077d6f4a20ca3978895c4d04f0dae21144f577c9834b8` | `7d605b683a9d768fadf143caec9c625da9848bf8b958c8676529317ca1b36dfa` |
| `xbrl_v2_dev_sample.json` | 100 | `3338df1b2e78b5e7f5250e8f53f7ab52a23d68174c1bb3639c0b4b572666293a` | `fe3c9821875d49b95f8ed371b51518b8093f12e605d589e07001b2195d394562` |

The dev sample was drawn again with the same seed (`xbrlrag-v2-dev-sample-1`); 9 of its 100
cases differ from the superseded sample. The paraphrase sets carry over unchanged except that
the records of the 9 removed cases are dropped. The 9 added cases have no paraphrases yet, so the
paraphrase-set and paraphrased-suite hashes waited on that pass; they now come by Amendment 2.

**Re-grade against the freeze of 2026-11-06.** The two stored DeepSeek baseline runs were
re-graded against the new keys from their stored answers, at no cost. The locked re-grade
refuses a stored answer whose case the suite no longer holds, so the 7 dev and 2 validation
answers to removed cases were left out of the source first, and the run's notes name them.
Accuracy over the remaining cases is unchanged: dev 72.6% (456 of 628), validation 60.1%
(158 of 263). Six screen cases move from partial to incorrect, five in dev and one in
validation.

**The superseded freeze of 2026-11-03.** The dev and validation suites were frozen in `data/eval/v2_frozen/`,
built offline from the SEC cache with `--total 300 --per-intent 3 --share 0.15
--salt xbrlrag-v2-dev-1 --groups configs/eval/v2/groups.yaml`. A rebuild from the same
arguments was byte-identical.

| File | Cases | Content hash (`xbrl_v2_manifest.json`) | File SHA-256 |
|---|---|---|---|
| `xbrl_v2_dev.json` | 635 | `1736014c9a4b814c53c927bb0232f060834ec74a1b3794462831f61da93159c3` | `7bb14e288d389aeab3f60165584cf7427099e51db5b0866f4cd40dacfb552e82` |
| `xbrl_v2_validation.json` | 265 | `333cbd280a701f840a110122ff8e19ef7c5f8ed7d68528d89ba1e9fe19c6bbf6` | `73c023e6e90b10331084aa17046ebb7a7faf16bf0a9142d67674fe603d587940` |
| `intents.json` | | intents hash `a3872080eb15da4736fb67ceb8f229dd17dd50edf7bca4b5b2ab1a86eccd3e1d` | `ae07ab311443cb7311815767c8723dd63a215ea2f974af3094d6aade4b13a1ec` |
| `xbrl_v2_dev_sample.json` | 100 | `d68c594a289402eef38b1244d1b2cb63b0fef97e16b26c2a01c9d62edb7ccd11` | `7cce826bb06877d394dbcff122f1c934e49a218f408d4f27b76a23adfd2e39db` |
| `paraphrases_dev_frozen.json` | 1,151 kept | `eeb361ad3b54051a9e00a35323f33426d29ee4e69eb375cf1bce24594ae476ab` | `0235a04c2daeb7637d38ed5da206c1eceded70e3471e64e2d1b0f9877be0f45e` |
| `paraphrases_validation_frozen.json` | 476 kept | `11aaca9968b5e34e3d768f720dc361b0a12ad0a6377efab8b6c77b5a53c8ad8d` | `abec80c9f1cfeceb7e28587a16f4024cd877a23955ba1742927e1ee6f26d042a` |

These replace a freeze of 2026-11-02, rebuilt after the concept-key integrity fix
(`docs/specs/concept-key-integrity.md`) corrected the keys of some metric families. Every
value change between the two traced to that fix; no decision run used the earlier freeze. The paraphrases come from passes by a model of a fourth family, checked by the
meaning check with the analyst-shorthand forms of `configs/eval/v2/paraphrase_forms.yaml`;
the cases the rebuild reworded or added were paraphrased again. The H-F hash comes by a later
amendment: H-F needs the compound shape (§6), and H-X writing should not wait on it. H-F is
generated blind and sealed before any system runs on it, so recording its hash later loses
nothing.

**The suites are numeric only.** Both frozen suites hold the shapes `lookup`, `compute.*`,
`screen.*`, `peer_relative`, `trend` and `point_in_time`, and no compound, narrative, mixed or
honesty case. The known gap of §6 stands: compound cases must exist in H-F before the look.

**H-F size, by kind.** H-F has about 450 cases over the 24 drawn filings: at least 150 in the
line-1 kinds (cross-filer, cross-period and compound) and at least 200 single-filer lookup +
compute.

**Item 4: runs, repeats and caps.**

- **No repeats.** Each system runs once per set. The run-to-run noise that bar line 3 needs is
  measured on validation, with two extra gpt-6.1 runs of `xbrlrag_full`, never on H-F. The
  "2 of 3 runs" rule of §6 item 4 therefore has no case to apply to.
- **Hard caps:** each arm's cap is 1.5 × the mean cost per case measured by its gpt-6.1 smoke,
  times its number of cases. The smokes are 5 cases per long-context arm and 10 for
  `frontier_sec_tools`, on Flex. The caps come by Amendment 2, after the smokes and before the
  look. A cap is never set after any look result has been seen. The tools agents' per-case
  budgets follow D6.
- **Interrupted runs** resume under the same run id (`docs/specs/eval-run-checkpoint.md`),
  and every resume segment is recorded with the run. A case that has no stored result when a
  run is interrupted is answered once, on resume.
- **Errors:** an error is tagged as the arm's own or as transport (provider 429 and 5xx; 5xx
  and connection failures of RagInference, Qdrant and Postgres). Transport errors may be
  re-run once with `--resume --retry-errors transport`; an arm's own errors stand and count
  as failed cases, as §3 says. Both counts are reported.

**Paraphrase stability on H-F.** §3 reads stability per intent, across all of an intent's
phrasings. On H-F those phrasings are the generator's template phrasings only (several per
intent). H-F is not paraphrased: that would put held-out questions in front of a person or a
web UI, which §5 forbids. The persona paraphrases of the frozen sets above are run on dev and
validation (as derived `_paraphrased` suites, `docs/specs/paraphrase-suite-wiring.md`) and
reported as development evidence, never against the bar.

**Item 5: judges.** In development the text judge is `claude-haiku-5-5`. At milestones,
`claude-haiku-5-5` also judges the OpenAI and DeepSeek arms, and `gpt-6-luna`, replacing
`gpt-5.6-luna`, judges the Anthropic arm, so a judge never grades its own family
(`configs/eval/judge.yaml`). A judge's scores are reported only after its calibration against
at least 100 human labels reaches kappa ≥ 0.7 (`docs/specs/eval-judge.md`). The calibration
result comes by Amendment 2. Only text answers are judged; no line of the bar depends on them.

**Amendment 2** (before the final look) records four values that need later measurement. The
rule for each is fixed above, so recording a value later loses nothing:

1. the paraphrase-set and `_paraphrased` suite hashes, after the paraphrase pass (item 2);
2. the tools agents' budgets, from the dev-sample runs (D6);
3. each arm's hard cost cap, from the gpt-6.1 smokes (item 4);
4. each milestone judge's calibration result (item 5).

**Still to come by later amendment:** item 1 (H-X hash), item 2 for H-F only (the H-F
hash), item 3 (code commit and freeze record of the look), item 6 ("a baseline comes close") and item 7 (served model id rule).

### Amendment 1.1 (2026-11-11): the H-X kind mix

The merge of this amendment is its timestamp. It is numbered 1.1 so that "Amendment 2" keeps
the meaning Amendment 1 gives it (the four later values). It changes one decision, before H-X
writing has started (§7): no H-X question had been written and no held-out set had been run
or seen.

**The change.** §1 and the v2 plan's decision 2 say H-X has 15 questions of each kind. The
total stays 75; the mix becomes:

| Kind | Was | Now | Spread over subtypes |
|---|---|---|---|
| cross-filer | 15 | **24** | 4 each: screen top, screen bottom, count, sum, median, peer rank |
| cross-period | 15 | **15** | 5 each: trend, CAGR, point in time |
| compound | 15 | **18** | mixed: each dependent kind (cross-filer, cross-period, text) used at least 3 times |
| honesty | 15 | **9** | 3 each: ambiguous, false premise, decline |
| text | 15 | **9** | one subtype |

The three kinds the success bar is about (cross-filer, cross-period, compound) go from 45 to
57 questions.

**Reason.** The kinds differ in how much they hold. Cross-filer has six subtypes, so 15
questions gave about 2.5 per subtype, while text has one subtype. H-X is the wording check
(§4): more human wording on the bar's kinds tests more of what the bar claims. Confident-wrong
is counted over every case, not only the honesty traps, and text has no RAG comparator since
Amendment 1 withdrew `html_rag`, so fewer cases there lose little. The change was decided on
the shape of the set alone, not on any measurement.

**What does not change.** §4 is unchanged: direction is read on the pass counts over all 75
questions, with Wilson 95% intervals, and no significance claim is made from H-X. Per-kind
counts are reported as they are, with their own intervals. §5 is unchanged.

**Tooling.** `validate` still warns when a kind is not 15 (`docs/specs/hx-authoring.md`
requirement 8). Until a follow-up change sets the new targets, the warnings for the five kinds
at the counts above are expected and are not errors.

### Amendment 1.2 (2026-11-11): the freezes of 2026-11-10 and 2026-11-11 (K6)

The merge of this amendment is its timestamp. It records two rebuilds of the development
suites that followed the freeze of 2026-11-09 (Amendment 1). The first, on 2026-11-10, was
not recorded when it was made. No held-out set (H-F, H-X, set B) was built, run or read for
either, and no decision of Amendment 1 changes.

**The freeze of 2026-11-10.** This rebuild followed the IFRS concept reload of `xbrl_db`
(E1b: the ifrs-family-meaning post-merge step, which had never run). It was rebuilt at main
`cafc198` (PR 369) with the frozen arguments of 2026-11-09. The compound files were added at
main `556dfe3` (PR 371), after the compound shape (PR 370), with `--compound 60`. The 2026-11-10
baseline runs (`docs/design/XbrlRag_Dev_Baseline_2026-11.md`) ran on this freeze. It is kept,
unchanged, in `data/eval/v2_frozen_2026-11-10/`. Against 2026-11-09, one dev intent changed its
value: a FY2022 aggregate, 114,261.7M → 113,939.0M (3 phrasings), from the IFRS reload. Three
cases changed only key fields. The dev sample is unchanged.

| File | Cases | Content hash | File SHA-256 |
|---|---|---|---|
| `xbrl_v2_dev.json` | 628 | `sha256:9ca1405e36a4dd6c28aa6df67673f5e71b41e9947991a77dfd02cfac5391ab27` | `baac9b864fc8b12edbbd3fe3f837d0d68306612d0b6b41876cf4477263170350` |
| `xbrl_v2_validation.json` | 272 | `sha256:6c5ce47dff696cc7eab92bd8444063d2a7d86b48cf7e6d57a3f836de083d940c` | `25b6b4d0545f572350e880e1e62d7e1fb541dc397f436d0dff985483ff6592b0` |
| `intents.json` | 300 | intents hash `sha256:d2ac35e6b47d4fda0559001d2d6666663477cec5aa3e92317802a45806918a24` | `17127f0e27f134e951a730da284296bba0dfdb5989f0457f87568c48c9587b90` |
| `xbrl_v2_dev_sample.json` | 100 | `sha256:72995614f4db58cc08446ac687af2023d1dd8c2325b738b24f5750ffe778add1` (as on 2026-11-09) | `7585c860585dbd1a21233933a741c470090ee0b29fc5c6682f31a1b797eaafd4` |
| `xbrl_v2_manifest.json` | | | `8dc3c4ddb3bba070804330d3f84c3188fb67468a7b0454d01ea70d525d97fdf8` |
| `xbrl_v2_dev_compound.json` | 136 | `sha256:b42bd646c2c33f3954c9f937720bc0e5428009f6f55ea1d03d59be8a0f8f9ccb` | `f9e136ed2712a5f2d5307603376d85c4aee41b32c1673e2a13f65180ac1b4c15` |
| `xbrl_v2_validation_compound.json` | 44 | `sha256:2ecd343aed4638271ffac5669e40ae119229bcdb2a6d3fdafeb2cfb56795445e` | `c2cedc12ff09417e0ed2c471471fc933ea7b08da0d63eeaa826a4fd9b9ec939c` |
| `intents_compound.json` | 60 | | `984af352865ab2675de14eb16e7c71bcbc9db3dfa7700b48ac2731632343de65` |
| `xbrl_v2_compound_manifest.json` | | | `e87451f3072747173d224a71bd48cb679af8f97ab6f0cbf5d8a00c6b4abc2581` |

**The freeze of 2026-11-11 (K6).** This is the development re-freeze before H-F (task K6). It
follows the key work K1 to K4 and the pre-K6 reload. That reload had three parts, all logged in
`data/handover/ifrs_debt_rollout_20261111_162451/`: the IFRS Debt mapping fix (PR 436), the
debt-ifrs-elements key rule (PR 440) and the K4b extension classifier.

It was rebuilt at main `84b65d8` (PR 441) with the frozen arguments: `--total 300 --per-intent
3 --share 0.15 --salt xbrlrag-v2-dev-1 --groups configs/eval/v2/groups.yaml --sec-cache-only
--compound 60`. It is in `data/eval/v2_frozen/`. Two builds from the same tree and database
were byte-identical for every build output: the reload's POST build and the K6 build. The dev
sample was redrawn with `build_dev_sample.py --size 100 --seed xbrlrag-v2-dev-sample-1`. It
keeps 96 of 100 cases.

| File | Cases | Content hash | File SHA-256 |
|---|---|---|---|
| `xbrl_v2_dev.json` | 627 | `sha256:fbdfee70c310f7f2dbfa56ce23110cc3266a1cf5102b452ac9b774e275625d3f` | `97724b952b0e6defaf0edb253634ed27856f5875e38b7ec48dd78b439a268d1b` |
| `xbrl_v2_validation.json` | 273 | `sha256:0fb27fcbd2f09831373533642b01bd3745ea27404491668438010a22502d6f61` | `d32d88b59ed425f42f6715bd713b129d76e8a42175aa20481c68fc07fe51e9a3` |
| `intents.json` | 300 | intents hash `sha256:16ba64a70a5208802ac3296de4fa91452394052ae7e580931fccbd3ce6f8b9b6` | `c1924e7a287d4c981f4590b60fefec0898307bd7b43f51d09cbb3910bb551346` |
| `xbrl_v2_dev_sample.json` | 100 | `sha256:ca96251187979d56f8c444d8a1a4c3d7d0e89e16700806338e3c913859621d52` (redrawn; 4 of 100 cases differ) | `63ccefef2252251c35e5c7bf9803f311b60282b7f94b69a99c6dc5a1d04a3785` |
| `xbrl_v2_manifest.json` | | | `43f147bec5a5f0717b0a7f7fc9bc173fe9228f58038128b6e413f1d3c72f1fbc` |
| `xbrl_v2_dev_compound.json` | 136 | `sha256:b8182b79711ce52ba6cce7dfb8eb66d9f7e1bec9bed3a98061988bae308c4ebc` | `41e329ab63353006f2c882b5acf2891ee72829c3a320ed4f2609a47750478095` |
| `xbrl_v2_validation_compound.json` | 44 | `sha256:31ea55b0841cb83406d89e41d23996db0e82412681eac0a1918c9c541c4677a7` | `08281f72935c911b01e791440c9a2d490247eda39859d3385e6475c5c613e2c6` |
| `intents_compound.json` | 60 | | `7964f4cb8bed85a63b4a258cbdf479ff30aa2fdf10ebbe3adace28bbe4f683a0` |
| `xbrl_v2_compound_manifest.json` | | | `c4ac273e73a75f86c5a0872f533785525d871af41d853c4475de010f3e4bc957` |

Against the freeze of 2026-11-10, by cause. Each line is measured by rebuilding at the commit
before and after it on the same database (details in `data/eval/v2_frozen/moves_by_component.txt`):

| Cause | dev | validation |
|---|---|---|
| Database changes since 2026-11-10, at the 2026-11-10 code: the K4 period-disclosure views rollout (the pre-K6 reload alone changed no case) | none | 6 value changes, the two median total-debt intents (FY2022 14,701M → 13,661M; FY2023 18,385M → 17,922M) |
| Code merged before K2b (PRs 370 to 418) | +16 / −17 cases, 16 value changes, 5 rephrasings | +5 / −4, 11 value changes, 1 rephrasing |
| K2b, key-currency-and-definition (PR 419) | 4 value changes, 9 rephrasings | 2 value changes, 6 rephrasings |
| After K2b: capex-strip-qualifiers, debt-ifrs-elements, the pre-K6 reload | none | none |

- **Members newly keyed.** The key fallback witness (PR 405) witnesses keys that were held for
  lack of an FSDS table. MU and COST, both with 52/53-week years, now count in their groups'
  aggregates. For example, the retailers' FY2024 total assets go from 973.4B to 1,043.2B, and
  the semiconductor companies' FY2023 median capex from 3,260.5M to 1,833.0M. The same change
  moves the seeded draw: it removes TSLA's FY2022 operating margin, COST's FY2025 net margin,
  CSCO's FY2026 current-assets growth and TXN's FY2025 total debt, and adds MU's R&D intensity,
  COST's current-liabilities growth and current ratio, and MU's debt issued.
- **Rephrasings.**
  - Lexicon phrasing uniqueness (PR 398) changes "general and administrative expenses" to
    SG&A and "net PP&E" to "net fixed assets". Where the new phrasing gets a new case id, the
    case counts as removed and added.
  - K2b renames the long-term-debt-issuance family to "debt issued", which is why the AMZN,
    UPS, HD and retailers' questions are rephrased.
- **K2b values.** The semiconductor companies' FY2023 debt issued goes from 25,785.8M to
  22,987.0M: TSM's 20-F US-dollar convenience figure leaves the sum (S6). The covered
  companies' FY2024 total goes from 180,580.5M to 179,534.5M.
- **TSM Debt.** It reaches the intent inventory (FY2022 to FY2025; 12 new compute
  intents), but the seeded draw selects none of them. The TSM FY2025 Debt key stays
  unresolved, because SEC's companyfacts carries only 2 facts for TSM's FY2025 20-F.
- **Compound.** Dev: 6 rephrasings. Validation: +4 / −4 and 3 rephrasings. All come from the
  same two wording changes.

Key independence (`xbrl_v2_manifest.json` → `key_independence`), 2026-11-10 → 2026-11-11:

| Status | 2026-11-10 | 2026-11-11 |
|---|---|---|
| agree | 933 | 968 |
| agree_other_element | 10 | 7 |
| note_witnessed | 22 | 22 |
| value_recast (held) | 1 | 1 |
| no_fsds (held) | 39 | 5 |
| disagree / ambiguous / rejected_line / note_unlisted | 4 / 10 / 15 / 1 | 4 / 10 / 15 / 1 |
| fallback (key fallback witness) | — | 37 (1 held) |
| **Total `n`** | 1,035 | 1,033 |
| Resolved by a reviewed entry | 30 | 29 |
| Held out of grading | 40 | 7 |

`configs/eval/v2/key_resolutions.yaml` (SHA-256 of the committed bytes):
- 2026-11-10: `bb593d6c4e0bc475a9e5cfddfcb4099337f7473f0895312eb38b321d78d4fdfe`, 30 entries.
- 2026-11-11: `ff1d2f409e52bfb41f9acef3116e3546a63f327323e7879ab354ce09cc262d9d`, 29 entries.

Entry 30 (TSLA FY2022 Capex) was removed in PR 441. The capex-strip-qualifiers rule, accepted
on its one judge3 read (PR 437), witnesses that line, so the entry covered no blocked key.

**Re-grade against the freeze of 2026-11-11.** The stored baseline runs are re-graded from their
stored answers, at no cost, as on 2026-11-09:
- `prune_regrade_source.py` first leaves out the answers whose case the suite no longer holds
  or whose question changed: dev 33 (17 missing, 16 rephrased), validation 12 (4 / 8),
  dev sample 7 (4 / 3).
- Cases the rebuild added have no stored answer (dev 16, validation 5, dev sample 4). They are
  answered in the next paid run and never filled in from a re-grade.

A dry re-grade at grader version 8 gives these value-correct counts over the cases kept. The
gains are corrected keys: the members the old keys left out had been counted by the systems.

| Arm | Cases kept | Value-correct, before → after | Confident-wrong, before → after |
|---|---|---|---|
| F3a `xbrlrag_full` dev | 595 | 490 → 501 | 7 → 7 |
| F7a `xbrlrag_full` dev | 595 | 480 → 491 | 4 → 4 |
| F3b `xbrlrag_full` validation | 260 | 198 → 207 | 0 → 0 |
| F7b `xbrlrag_full` validation | 260 | 184 → 193 | 0 → 0 |
| F4 `frontier_sec_tools` dev sample | 93 | 83 → 87 | 6 → 4 |
| A8 `frontier_sec_tools` dev sample | 93 | 78 → 81 | 10 → 8 |
| other arms (F1a, F1b, F6a, F6b, F2, F6c, A7) | | change by at most 2 | unchanged, or −1 (A7) |

The stored re-grade runs at grader version 9 (grader-v9-number-reading). Its run ids become the
development baseline and are listed in the eval schema (`/tech/eval`).

Paraphrases: the rules of 2026-11-09 apply. Records of removed cases are dropped, and added and
rephrased cases are paraphrased by the fourth-family model (task E2). Their hashes come by
Amendment 2. H-F is generated by the same builder at the code of this freeze; its hash comes by
the later amendment.

### Amendment 2 (2026-11-15): the four later values, the G1 profile, and the K7 re-freeze

The merge of this amendment is its timestamp. It records the four values Amendment 1 left for
later (§6 item 2 for the paraphrases, D6, item 4 and item 5), states one change of profile, and
discloses what happened to the development data between Amendment 1.2 and this amendment. No
held-out set (H-F, H-X, set B) was run or read for any of it.

#### The four values

**1. Paraphrase sets and paraphrased suites (§6 item 2).** The persona paraphrases of the K6
freeze (Amendment 1.2) were produced by the fourth-family model (Gemini 4 Argon, prompt v1) in a
separate session run by the maintainer, and checked by the meaning check. The derived
`_paraphrased` suites (`docs/specs/paraphrase-suite-wiring.md`) were built from the K6 dev suite
(`sha256:fbdfee70…625d3f`) and validation suite (`sha256:0fb27fcb…6f61`).

| File | Kept | Content hash | File SHA-256 |
|---|---|---|---|
| `paraphrases_dev_frozen.json` | 1,151 | `6fd1664402bf44ae3e46bc9acf63be4c6346e87275c332731690db42b816f301` | not recovered |
| `paraphrases_validation_frozen.json` | 502 | `f9de97fd8911b871aec3e0e1ce29c98e99f7986aca01cb4f3ea57780ea94788e` | not recovered |
| `xbrl_v2_dev_paraphrased.json` | 1,553 cases | `sha256:61ccee605e0db7bbd60384433c55f6d6f999044e49bb6f7bfb0d4cd887ea2922` | not recovered |
| `xbrl_v2_validation_paraphrased.json` | 1,000 cases | `sha256:ca40de634fc0bb83238cbc2513c371cea46ab276a458224e360e0067fdf065c7` | not recovered |

- Dev: 148 new and 1,004 carried over; 53 left out on review and 49 duplicates dropped.
  Validation: 83 new and 419 carried over; 27 left out on review and 17 duplicates dropped.
  The dev set's size is recorded as 1,151 kept, but its recorded parts sum to 1,152. The file that
  would settle it was lost (below); the content hash above is the record.
- One dev case (`int-b1ea3c4b1d1e-00613ca4`) has no paraphrase.
- The paraphrases left out are mostly of the typo-prone persona, so the kept sets lean cleaner
  than the personas as written.
- **The files were lost on 2026-11-14** (Disclosures, below). Only these hashes survive. The sets
  were built over K6, not over K7, so a paraphrase measurement on K7 needs a new pass.
  Paraphrases are development evidence only (Amendment 1, "Paraphrase stability on H-F"); no line
  of the bar reads them.

**2. The tools agents' budgets (D6).** From the dev-sample runs of `frontier_sec_tools` on
`claude-opus-5-5` (`eval-5471b3c99d92`, re-graded `eval-e9fa0e0df9b7`) and on `deepseek-v4-pro`
(`eval-f8e487f196c1`, re-graded `eval-81fec35bc7d1`), 100 cases each. A budget binds on a case when
the arm would stop the tools there: tool turns ≥ `max_steps`, tool calls ≥ `max_tool_calls`, or
the case's cost after a tool turn ≥ `max_cost_usd`. Each budget is the lowest value that binds on
at most 5 of the 100 cases of each run. Because D5 gives both agents the same budgets, the arm
takes the larger of the two runs' values:

| Budget | Opus run | DeepSeek run | Set |
|---|---|---|---|
| `max_steps` | 7 (binds 2) | 13 (binds 5) | **13** |
| `max_tool_calls` | 9 (binds 5) | 22 (binds 5) | **22** |
| `max_cost_usd` per case | $0.23 (binds 5) | $0.20 (binds 5) | **$0.23** |

At these values the budgets bind on 0, 0 and 5 of the Opus cases (5 distinct, none answered
correctly) and on 5, 5 and 2 of the DeepSeek cases (8 distinct, 2 answered correctly). One
DeepSeek case (`int-06f3dc7f5a2d-0342f81c`) reached the old 20-step budget, and its final turn
without tools failed with an empty reply; it is counted as a budget hit and as wrong. On the 93
cases the K6 dev sample kept, the same values result. The 250,000-token window is unchanged
(1.5). The results report, per agent, how often each budget was hit, counting a hit whose final
turn failed, and the accuracy on those cases.

**3. Hard cost caps (§6 item 4).** The rule of Amendment 1 item 4 stands: each arm's cap is 1.5 ×
the mean cost per case of its smoke, times its number of cases. The gpt-6.1 smokes measured, on
Flex:

| Arm | Smoke | Total | Mean per case |
|---|---|---|---|
| `long_context_oracle` on `gpt-6.1-sol` | 5 cases | $1.16 | $0.18 |
| `frontier_sec_tools` on `gpt-6.1-sol` | 10 cases, 0 Flex 429s at 1 worker | $0.82 | $0.076 |

The caps in money follow from these means and each arm's case count, which the later amendment
(C4) records with the H-F and H-X hashes. These smokes measured gpt-6.1. G1 now runs on the `anthropic` profile (D1′ below), so they set only the G2 cap.
**Open:** a smoke of each G1 arm on the `anthropic` profile (5 cases per long-context arm, 10 for
`frontier_sec_tools`) is needed before the look. The G1 caps follow from it by the same rule and
are recorded by the later amendment (C4), before the look. A cap is never set after a look result
has been seen.

**4. Judge calibration (§6 item 5).** The maintainer labelled a seeded subset of the dev text
answers (`docs/eval/judge_calibration/SUBSET.md`): 23 of 37 questions, 68 of 109 answers, 466
labels (correct 150, supported 103, covered 213). The subset is drawn by seed, never by how answers
look. Claude did not label or pre-label. Both milestone judges labelled the same 68 answers with
judge prompt v2, and `calibrate` wrote the hashed record
(`docs/eval/judge_calibration/calibration.json`; record SHA-256
`51fe9b90faed3f168ef8e4d912fe70b875d238b88413b8dbdf765b8566032096`; answer set
`6bdd7aec8aaa4ee853c06f761f9408c8327a6d1b4af56c77ae0c7e4e95b3dc99`; label set
`f907f5bafaa9540acbb123615cb25ce2baad7c4e31ce6f09ca76554c398b0171`).

| Judge | correct: κ [95% CI] | supported: observed agreement | covered: κ [95% CI] | Passes |
|---|---|---|---|---|
| `v2:claude-haiku-5-5` | 0.489 [0.198, 0.779] | 1.000 | 0.803 [0.723, 0.883] | no |
| `v2:gpt-6-luna` | 0.273 [−0.105, 0.651] | 0.990 | 0.785 [0.702, 0.868] | no |

The gate is κ ≥ 0.7 (`docs/specs/eval-judge.md`), and observed agreement for a dimension whose
human labels are one class (`docs/specs/judge-calibration-one-class.md`); `supported` is such a
dimension. Neither judge passes `correct`. Neither made an error; each run cost about $0.03.
**Consequence:** no text-judge score is reported at G1. Text answers are still run and stored; no
line of the bar depends on them (Amendment 1 item 5).

#### The G1 profile

**D1′. G1 runs on the `anthropic` profile; G2 is the cross-provider line on `openai-6.1`.** D1 made
`openai-6.1` at Flex the decision profile, and D3 added one `anthropic` line. On 2026-11-14 the
maintainer swapped the two. The final look (G1) runs `xbrlrag_full`, `frontier_sec_tools`,
`long_context_oracle` and `closed_book` on the `anthropic` profile (`claude-haiku-5-5`,
`claude-sonnet-5-5`, `claude-opus-5-5`); the second tools agent stays on `deepseek-v4-pro` (D5).
The cross-provider line (G2) runs `xbrlrag_full` once on `openai-6.1` at Flex, on H-F and H-X, and
decides nothing. *Reason:* cost. An Anthropic credit covers most of the look, so its out-of-pocket
cost falls from about $120 to about $20–50 (estimates of 2026-11-13, before the Anthropic-profile
smokes; about $120–150 in total on `anthropic`). The development
baselines already exist on the `anthropic` profile. The choice was made on cost, before any
held-out measurement and with no comparison run. *Effect:* the bar (§3) is read against the
`claude-opus-5-5` agent, the model D6's budgets were measured on. A judge never grades its own
family (`configs/eval/judge.yaml`); with no text-judge score reported at G1 (item 4), the rule has
nothing to apply to at this look.

#### Disclosures

**Grading.**
- **Grader 10** (grading-stated-aggregate) reads a declining answer's stated aggregate. The
  development baseline was re-graded at grader 10 from the answers' raw values
  (regrade-raw-values), not from a grader's filtered values.
- **Grader 11** (pick-skips-questions): a question sentence is never the pick sentence, and a
  "change" question without a percent cue also accepts the absolute change. Both rules touch
  compound cases only. On the 14 re-graded runs below, no grade changed.

**A rule developed with validation in view.** The group-sum cue (PRs 465 and 471) reads "total X"
and "add up" over one named group as a sum. It was found by measuring the development suites: of
the 40 group-sum questions with no sum cue, 17 use "If you add up the <group>", and 11 of the 14
validation sum originals use that phrasing. The rule is general English, but it was chosen with
validation's phrasings in view, so it moves validation more than dev would suggest. Its paid slice
moved the plans that sum from 17 to 21 on dev and from 17 to 19 on validation. Validation results
after 2026-11-12 are therefore not a clean check of this rule.

**Broader-total presented lines (D13).** screen-candidate-order (PR 477) makes the screen pick as
the lookup does, and the PP&E, current available-for-sale, debt-proceeds and cash families gain
their presented lines. For some filers the only presented line is a broader total (PP&E including
finance-lease right-of-use assets; cash including restricted cash). Keys and screens use it.
Excluding such filers, with a reason, is left for after G1.

**Key-side errors left for after G1.**
- LMT `CostOfRevenue` keyed as operating expenses.
- The covered companies' FY2024 debt issued: the key (179,534M) is below the system's sum
  (193,347M) by two key-side exclusions. Alphabet's 13,589M is dropped by reviewed entry 18 as
  "not long-term debt", written on 2026-11-09 before D3 made the family debt of any term; Abbott's
  223M is a presented line the key does not key (`presented_line_not_keyed`). The case is a
  silent-wrong on the key's side.
- The witness ordering (INTC restructuring substitute; NVDA FY2023 `prefer` pattern).
- The retailers' FY2024 bottom-3 unearned revenue: the deferred-revenue statement rule misses
  Costco's "Deferred membership fees" (`DeferredRevenueCurrent`), so the key orders HD and COST
  differently from the system, and the system's order is the right one.
- The retailers' FY2024 median other income: Costco's key input is `fallback_blocked`, while the
  system reads the same measure (-2,185M against the key's -2,250M).
- P&G's cash including restricted cash is not cash (decided 2026-11-12); its key is fixed after G1.

The 2026-11-12 plan also left the UNP and INTC witness blocks for after G1. They were resolved on
2026-11-15 instead (PR 499, K7 re-freeze below), because the K7 build could not complete without
them.

**H-F after screen-candidate-order.** The maintainer ran the rollout on `xbrl_heldout`. H-F stays
sealed and was not rebuilt. The counts-only key-concept audit (run 2) counts the H-F cases whose
key and system read different measures in a way that can change the grade ("harmful"): 27 before
the rollout, 12 of 441 after it (PP&E 6 → 0, short-term investments 3 → 0, debt 6 → 3, cash 9 → 6,
operating expenses 3 → 3). The 9 remaining cash and debt cases are disclosed, not diagnosed:
diagnosing them would be tuning on held-out data. Only these counts were shared.

**Data loss.** On the evening of 2026-11-14 most of the repository's gitignored `data/` folder was
deleted. Postgres (the fact store and the `eval` and `audit` schemas), Qdrant, the held-out
folders and git were not affected. Two file-recovery passes and a shadow-copy check recovered
nothing usable.
- **Lost:** `data/eval/v2_frozen*` (K6 and the earlier freezes), the paraphrase files (item 1),
  the development frontier SEC snapshot, the FSDS cut and run logs. The filings, canonical HTML and
  SEC API cache, which live under `data/storage/`, survived.
- **The SEC API cache** holds the 33 companyfacts payloads fetched on 2026-10-29 and 2026-10-30.
  K6 was built `--sec-cache-only`, so this is the SEC data K6 used.
- **The FSDS cut** was rebuilt by downloading the 19 quarters 2022q1 to 2026q3 again and re-running
  the 2026-11-07 filter over the same 33 filers. Every logged quarter count, the totals (sub 656,
  num 301,943, pre 71,689, tag 17,915), the 6 accessions not in FSDS and the zip SHA-256s of the
  17 quarters recorded in `test/spec` fixtures match the original cut.
- **K6 could not be rebuilt.** The K6-code build on today's `xbrl_db` stops at the statement
  witness, because screen-candidate-order changed the concept mapping after K6. K6 stands as
  recorded by its Amendment 1.2 hashes, and its stored runs keep their answers.
- The development frontier snapshot held none of the held-out filers. G1 records its own (C4).

**The K7 re-freeze.** The development suites were rebuilt at main `34ebb77` (PR 499) with the
frozen arguments of Amendment 1.2, on the restored FSDS cut and the surviving SEC cache. The first
build stopped on the three keys screen-candidate-order predicted (its spec, "Frozen suites"). PR 499
resolves them with three reviewed `rule_gap` entries read as `key`, each at an unchanged value: UNP
FY2024 and FY2025 PP&E ("Properties, net", 58,343M and 59,645M) and INTC FY2023 cash (7,079M,
stated in the FY2025 10-K only in its cash-flow statement). A dry build and the build at merged main
were byte-identical for every build output. The dev sample was redrawn with `build_dev_sample.py
--size 100 --seed xbrlrag-v2-dev-sample-1`. K7 is in `data/eval/v2_frozen/`.

| File | Cases | Content hash | File SHA-256 |
|---|---|---|---|
| `xbrl_v2_dev.json` | 627 | `sha256:3cb8ff82de69e0a5248b0cafbd331d139c2a87a149af482cff60d8d224947b0d` | `57d3a984ab5b5655408c3fdd9f4da2bdf8b7faf96b7162d6e81adca7b943a09f` |
| `xbrl_v2_validation.json` | 273 | `sha256:b39361616e53cf7d59153a47dbc0d28a40993ee456a1ed1a7047419e8b029c26` | `a769167780294c61ccfdfce566ccd775fa1e92bcfa6414b80c1dbfc9fe64d2a4` |
| `intents.json` | 300 | intents hash `sha256:5df8cb2fd58908546796b0efc7cf0f235359179a89137132c9c23b7327b8f786` | `60552704fd08cff656334e3790abee72b49c97a050ab8738944d042e858d8d0d` |
| `xbrl_v2_dev_sample.json` | 100 | `sha256:4af3caf4fb210c2e95b87e09941a4f03232cf2188c6f837ec0ac322a6e297263` | `7c563b6ddb2d90b5e9d0de61dc9f96722228204a5b466a209089c3e14dcb0492` |
| `xbrl_v2_manifest.json` | | | `e77cdafb8a5faa6fb710ae5fa726655836ccb85b8a3b99069e4a02c093344941` |
| `xbrl_v2_dev_compound.json` | 136 | `sha256:3a42be6dce194de6f0f3c26d93451911ab0f76aeb8ca48ca51b5cf632c06f6e3` | `f570d0bcc5460eb1ff915bfedaf074e090432479971849d54709251e09f8c67d` |
| `xbrl_v2_validation_compound.json` | 44 | `sha256:00d5ee9fa2d720e4b3f451b92989bf320ebced0464410283d620926809d091e1` | `9f8df1ff154f41e7af9d05941a16b53a3303a2b56907571e35dfa9747c68f0de` |
| `intents_compound.json` | 60 | intents hash `sha256:e7d0f6903e8eecfc8bc02a74b72c577949d3c8bddfb332273afdc8fd7e8f7eda` | `7964f4cb8bed85a63b4a258cbdf479ff30aa2fdf10ebbe3adace28bbe4f683a0` (as K6) |
| `xbrl_v2_compound_manifest.json` | | | `c92aa878142f3260bda0d1e19123bde45a2774dc6fc17209e8f061dc4379e47c` |

Key independence (`xbrl_v2_manifest.json` → `key_independence`): `n` 1,037; agree 970,
agree_other_element 6, note_witnessed 22, value_recast 1, no_fsds 5, disagree 4, ambiguous 10,
rejected_line 18, note_unlisted 1; fallback 37 (1 held). 32 keys are resolved by a reviewed entry
and 7 are held out of grading. `configs/eval/v2/key_resolutions.yaml` at `34ebb77`: SHA-256
`4a87e3c69dcfdd6f9df4f5e21a39e299da541e48a9390f50f6dd499b8a495aa4`, 32 entries (rule_gap 22,
combined_line 4, definition 5, note_unlisted 1).

Against K6, compared with the K6 cases stored in `eval.eval_case_result` (the K6 files are lost):
- **Dev:** 2 cases out and 2 in, the swap screen-candidate-order predicted (META FY2025 total
  current assets out, META FY2025 PP&E in). One intent changes its value, which that spec did not
  predict: the retailers' FY2024 total interest expense goes from 7,155M to 4,896M (3 phrasings).
  WMT's 2,259M is now excluded as `resolved_drop` under reviewed entry 16 (debt-only interest, not
  total), and the "with WMT" reading is kept. K6 had summed it.
- **Validation:** 1 META case out; the other 259 cases compared are identical. The reference is the
  grader-10 re-grade of the 2026-11-10 run, which holds 260 of K6's 273 cases.
- **Compound and the dev sample:** no stored K6 run holds them in full, so they are not compared.

**Re-grade against K7.** The grader-10 development baseline (13 runs) and the 2026-11-12
confirmation run of `xbrlrag_full` were re-graded at grader 11 against K7 from their stored answers,
at no cost. `prune_regrade_source.py` first left out the answers whose case K7 no longer holds (the
2 META dev cases; 2 dev-sample cases). These runs are the development baseline:

| Arm | Profile | Source → re-grade | Cases | Value accuracy |
|---|---|---|---|---|
| `xbrlrag_full` dev | anthropic | `eval-89d896e3d40d` → `eval-afb40ef42cb9` | 593 | 84.3% |
| `xbrlrag_full` dev | deepseek | `eval-87895b6e0891` → `eval-ca1a25ec2c89` | 593 | 82.6% |
| `xbrlrag_full` dev, confirmation run | deepseek | `eval-a2aed28f410e` → `eval-03298f71aa59` | 625 | 98.7% |
| `closed_book` dev | anthropic | `eval-d1e4491ef701` → `eval-6283534ff0ab` | 593 | 31.5% |
| `closed_book` dev | deepseek | `eval-5762ef3aa66a` → `eval-d8c252e178d0` | 593 | 20.4% |
| `naive_rag_filtered` dev | anthropic | `eval-d048c51bb8aa` → `eval-97ea4eda313f` | 593 | 44.5% |
| `naive_rag_filtered` dev | deepseek | `eval-2331dd397633` → `eval-f2c9344be00c` | 593 | 41.5% |
| `frontier_sec_tools` dev sample | anthropic | `eval-c364b838e2af` → `eval-6c1746b8c722` | 91 | 93.4% |
| `frontier_sec_tools` dev sample | deepseek | `eval-b3dcfbfe8474` → `eval-36a1d2dc6129` | 91 | 86.8% |
| `frontier_sec_tools` dev sample, partial | deepseek | `eval-1497bc97489d` → `eval-64d1349e7b93` | 28 | 89.3% |
| `long_context_oracle` dev sample | anthropic | `eval-0fe6dadd8515` → `eval-35c8de10a9cf` | 91 | 69.2% |
| `long_context_oracle` dev sample | deepseek | `eval-b07b915063d4` → `eval-3ebeb8fcf7ca` | 91 | 68.1% |
| `xbrlrag_full` validation | anthropic | `eval-bb99ba1d6dbd` → `eval-b3faaba70944` | 259 | 80.3% |
| `xbrlrag_full` validation | deepseek | `eval-3a6e84be4fa7` → `eval-d7bbe02314b1` | 259 | 74.5% |

No grade changed in any of the 14 runs. v2 grounding is not attached to the re-grades of the
2026-11-10 runs, which carry no corpus fingerprint; each run records why. The confirmation run was
answered on 2026-11-12, after the fixes of D3; the other sources were answered on 2026-11-10,
before them (`docs/design/XbrlRag_Dev_Baseline_2026-11.md`).

**Still to come by the later amendment (C4), before the look:**
- item 1: the H-X hash;
- item 2 for H-F: the H-F hash;
- item 3: the look's code commit and freeze record;
- item 4 for G1: the caps from the `anthropic` smokes, and the SEC snapshot rule of the G1 tools
  runs;
- item 6: what counts as "a baseline comes close";
- item 7: the rule for a served model id that differs from the freeze record.

### Amendment 3 (2026-11-18): the look's hashes, caps and reading rules

The merge of this amendment is its timestamp. It records the items Amendment 2 left for this
amendment (§6 items 1, 2 for H-F, 3, 4 for G1, 6 and 7) and the disclosures carried into the look.
No held-out set (H-F, H-X, set B) was run or read for any of it; H-F's and H-X's hashes were
computed by the maintainer, and only the hashes were shared.

#### The look

**Item 3: code and freeze record.** The look commit is `d4c76e25c0c880001c1249391221c4d957f1309a`
(PR 512). Its code is byte-identical to the frozen commit `66d5575e239bbe870b7e27a5c7c6a3e7a1f8161e`
(PR 511); the merge adds only `docs/eval/heldout_v2/freeze/`. The freeze record is
[`freeze/freeze_record.json`](freeze/freeze_record.json), SHA-256
`6280f916b4faa3e30c557cc3339622f634ac60e818430e0a36218fae5001171e`. Every G1 and G2 run is made at
the look commit; a run made at any other commit is not a look result. No code change goes in before
the last G1/G2 run. A generic fix found necessary during the look is disclosed, and is not made
before the look ends.

**Item 2 for H-F: the H-F hashes.** Built by the maintainer with `run_e4.ps1 -Fetch` on
`xbrl_heldout` on 2026-11-11, salt `xbrlrag-v2-hf-1`, arguments `--split heldout_filers --total 130
--per-intent 3 --share 0 --compound 17`; the sealed build is byte-identical to its check build.

| Suite | Cases | By key status | Content hash | File SHA-256 |
|---|---|---|---|---|
| `heldout_hf.json` | 390 | single-source 75, verified 315 | `sha256:78a3f1264cdf198ee52ce3066166bf117a4972fef366db0a550580c6f06ef790` | `0d178c3d1d204409da9e37b09d23a086d2f09fce12c6eddb9f3ab034d262adda` |
| `heldout_hf_compound.json` | 51 | single-source 30, verified 21 | `sha256:a4a31464445a98eb1b202d5026a019d289610b4039e2d209b7170700265ee94c` | `7a8de0886606c90133b292543562db157d5d9ee99f5704a7e5c861a6d978a079` |
| `heldout_hf_intents.json` | | | | `1c994e4e642e034e668e77c05b06ad39b9ed699d4474b5db94b3deaa9c4fddd9` |
| `heldout_hf_manifest.json` | | | | `a34616dff57f4dce5aa63b63ca7c983117db0aaf14da248233e3a1b457c6ef9f` |
| `heldout_hf_compound_intents.json` | | | | `ecdb43e7cfb20f77a5f937efad69c7249c68ff709b8b8bbfc8fbd1567a97dc40` |
| `heldout_hf_compound_manifest.json` | | | | `5084d6c77072d125a6e098a8cc5bd3bf54950cdbb87131d722c2bfe821178ce2` |

Blind drops: unresolved witness blocks were left out as `witness_blocked` (main 16, compound 18),
before any system saw a case. H-F was not rebuilt after screen-candidate-order (Amendment 2).

**Item 1: the H-X hash.** `heldout_x_v1.json`, 75 cases (24 cross-filer, 15 cross-period,
18 compound, 9 honesty, 9 text; Amendment 1.1), SHA-256
`fd94add7d4a2067e3be5a58daa29485c7b397859cb3e5ab0353a6bd53bbfdb38`, built and sealed on
2026-11-18. The maintainer wrote it by hand without LLM help, checked it and built it with
`scripts/eval/hx.py` (`check-keys`: 57 cases verified by SQL against `xbrl_db`, 18 honesty and
text cases skipped by design, none excluded) and keeps it outside the repository. No agent has
read it; only these counts and the hash were shared.

**The corpus.** The G1 and G2 runs read `xbrl_heldout`. Its fingerprint is recorded by the run
script before the first run and after the last; the two must match. *Disclosure:* the runner takes
a run's `corpus_fingerprint` from the suite file, and the v2 suites carry none, so every v2 run
since 2026-11-09 stores it empty (180 runs). The code is frozen, so the look's fingerprint is
recorded by the run script instead, and stated with the results.

#### Item 4 for G1: runs, repeats and caps

**Runs.** Each arm runs once (no repeats), at the look commit, on H-F and on H-X:

| Look | Arm | Model | H-F | H-X | Set B |
|---|---|---|---|---|---|
| G1 | `xbrlrag_full` | `anthropic` profile | 441 | 75 | 47 |
| G1 | `frontier_sec_tools` | `claude-opus-5-5` | 441 | 75 | no |
| G1 | `frontier_sec_tools` | `deepseek-v4-pro` | 441 | 75 | no |
| G1 | `long_context_oracle` | `claude-opus-5-5` | single-filer cases | single-filer cases | no |
| G1 | `closed_book` | `anthropic` profile | 441 | 75 | no |
| G2 | `xbrlrag_full` | `openai-6.1`, Flex | 441 | 75 | no |

G2 is `xbrlrag_full` only (D1′). Amendment 2 item 3 says its two gpt-6.1 smokes "set only the G2
cap"; with G2 limited to `xbrlrag_full` they set no cap, and that sentence is corrected here. H-T
runs after the look, once its filings exist (v2 plan, "After the look"). Set B is the
one secondary check §1 allows at the final milestone, `xbrlrag_full` only (D2); it runs at G1.

**The cap rule (a change to Amendment 1 item 4, made before any look result exists).** Amendment 1
set each arm's cap at 1.5 × its smoke's mean cost per case × its number of cases. Applied to the
2026-11-16 smokes, that rule would stop both tools agents before the end of H-F: the dev sample is
61% lookups and ratios, while H-F holds 78 screen, 18 peer and 51 compound cases, which cost the
tools agents four to six times a lookup. A halted arm costs a re-run, and the oracle's cost follows
its filings, not its cases. The rule is therefore:

- **By question type.** An arm's cap on a set is 1.5 × Σ over suite shapes of (the arm's mean cost
  per case of that shape in its basis run × the set's cases of that shape). Shape counts are
  aggregates of the sealed builds (E4 build summary; Amendment 1.1 for H-X); no case was read.
- **The oracle, by filing.** The arm writes the prompt cache only for a filing asked about twice or
  more (long-context-cache-reuse, PR 489). Its cap is 1.5 × ($0.5751 × F + $0.0269 × (n − F)) on a
  set whose n single-filer cases fall on F reused filings, plus $0.4609 per single-use filing. The
  per-call values are the smoke's measured tokens (`eval-4f354a45a24b`: 114,212 prompt and 201
  output tokens a call) at Opus list prices. The G1 run script counts n and F from the sealed suite
  before the run (counts only) and records them; the cap never exceeds its ceiling at F = 98, one
  filing per single-filer intent.
- **Bases.** Where a shape has no measured basis on the arm, the stated substitute applies:

| Arm | Basis run | Substitutes |
|---|---|---|
| `xbrlrag_full`, anthropic | `eval-89d896e3d40d` (full dev, 595 cases) | compound = its mean × 1.99, the compound/single ratio of `xbrlrag_full` on DeepSeek (`eval-c78dd0934732` at 6ed865a over `eval-87895b6e0891`) |
| `frontier_sec_tools`, Opus | `eval-5471b3c99d92` (dev sample, 100) | compound = the D6 per-case budget, $0.23 |
| `frontier_sec_tools`, v4-pro | `eval-a99badef421c` (dev sample, 93) | compound = $0.23 |
| `closed_book` | `eval-d1e4491ef701` (full dev, 593) | compound = its mean × 1.99 |
| `xbrlrag_full`, openai-6.1 (G2) | the anthropic figure × 0.321, the cost ratio of `eval-7fe5ce2c0c14` over `eval-2e0653f5a8d4` (the same 10 cases, seed 1729) | |
| all | H-F's 78 screen cases split 7/7/6/6 intents over four screen shapes in an unrecorded order, so they share one mean; H-X's count screens use it too; H-X honesty and text use the basis run's mean over all shapes | |

The two frontier basis runs predate D6 and ran under the $2 budget, so their screen and peer means
are higher than D6 allows; the caps keep them unclipped, and the expected costs below clip each basis
case at $0.23. `pricing.yaml` tracks DeepSeek at peak and OpenAI at Standard, so those caps are list
prices.

**Caps and expected costs** ([`amendment3/g1_caps.py`](amendment3/g1_caps.py), read-only on the
development `eval` schema; re-run on 2026-11-18 with the same output):

| Arm | Expected H-F | Expected H-X | **Cap H-F** | **Cap H-X** |
|---|---|---|---|---|
| `xbrlrag_full`, anthropic | $9.20 | $1.98 | **$13.79** | **$2.98** |
| `frontier_sec_tools`, Opus | $42.41 | $10.52 | **$70.62** | **$17.87** |
| `frontier_sec_tools`, v4-pro (peak) | $26.86 | $7.02 | **$48.47** | **$13.30** |
| `long_context_oracle`, Opus | $21.05 (F = 24) to $29.83 (F = 40) | ≤ $15.21 (33 single-use) | **1.5 × formula; ≤ $92.43** | **≤ $22.81** |
| `closed_book`, anthropic | $1.78 | $0.33 | **$2.67** | **$0.50** |
| `xbrlrag_full`, openai-6.1 Flex (G2) | $2.95 | $0.64 | **$4.43** | **$0.96** |
| `xbrlrag_full`, set B | about $0.90 | | **$1.30** (1.5 × $0.0185 × 47) | |

Expected G1 total at list price: about $102–111 on Anthropic, about $34 on DeepSeek at peak (about
$17 off-peak), and about $3.6 on OpenAI at Standard (about $1.8 at Flex). A cap is checked between
cases, so a run can pass it by the cost of the cases in flight; that overshoot is reported.

**Run structure (cost only; answers do not depend on it).**
1. **The oracle runs as one single-system invocation per set** (`run_suite.py --system
   long_context_oracle`, no `--with-system`, no `--kind`, `--sample` or `--limit`), with
   `--cache-groups filing` (its default), so the plan sees every case of a filing and cases on one
   filing run back to back inside the cache's five-minute lifetime. A resume is allowed; it costs
   one cache write per filing in flight.
2. **The `deepseek-v4-pro` agent runs off-peak** (outside 01:00–04:00 and 06:00–10:00 UTC, Monday
   to Friday, or at a weekend), which halves its bill. Its cap and recorded cost stay at the peak
   price.
3. **Not used at this look:** Anthropic's batch interface would halve every Anthropic arm, but the
   runner has no batch path and the code is frozen. It is noted for after G1.

**Run order and stopping at the first halt.** The look runs as one scripted batch, in this order:

1. `closed_book`, then `xbrlrag_full` (anthropic), then `long_context_oracle`, then
   `frontier_sec_tools` on Opus, then on `deepseek-v4-pro` (off-peak, on the Opus agent's
   snapshot), then G2, then set B.
2. Within each arm, H-X runs before H-F. The two sets' caps come from the same basis, so a cap
   that is wrong shows on the smaller set first.
3. Each run is preceded by the pre-run checks (Postgres `xbrl_heldout` and its fingerprint,
   Qdrant, RagInference, keys) and a one-case smoke that is not stored. A failed check or smoke stops
   the batch before anything is spent on the run.

The batch **stops at the first run that does not finish cleanly**. Later runs do not start:
- its exit code is not 0;
- its stored status is not `OK`. `ABORTED` means the run's cost cap or the circuit breaker
  stopped it; the halt type is in `config.halt`, and its skipped cases are in `n_skipped`;
- `n_skipped` is above 0.

What the script prints for a stopped run is limited to the run id, the spend against the cap, the
cases answered and skipped, and the halt type. It prints no accuracy, grade or failure count. The
maintainer then decides, from cost data alone, how to continue. A cost-cap halt continues the
same run with `--resume <run id>` and a higher cap, once a dated note records the new cap and its
cost reason. That note is appended to this amendment before the resume and is disclosed with the
results. Nothing graded is lost, because a run is checkpointed case by case. A circuit-breaker
halt resumes at the same cap, after the cause (a provider outage, for example) is cleared. Cases
that failed on transport are re-answered with `--retry-errors transport`. An arm's own errors are
kept.

A tools agent hitting its per-case D6 budget is not a halt. It is the arm working as designed,
and it is reported per Amendment 2 item 2.

**The SEC snapshot of the tools runs.** The development snapshot holds none of the 38 held-out-pool
filers, and was lost on 2026-11-14 in any case. The G1 tools runs therefore fetch live into one new
snapshot for the look (`XBRLRAG_SEC_SNAPSHOT_DIR` on the held-out machine), with
`--sec-as-of 2026-10-29`, the corpus's date, so every list is filtered to what was filed by then.
The `claude-opus-5-5` agent runs first and the `deepseek-v4-pro` agent second on the same snapshot,
so a URL both agents ask for returns the same bytes. A URL first asked for later is fetched then;
`first_fetched` and `last_fetched` are reported, and so is the known gap of `get_frame` (no filing
date, so a frame fetched after `as_of` can show a later value). The snapshot's id and URL count are
recorded with the results, and the snapshot is kept, not frozen, until the last G1 run.

#### Item 6: what counts as "a baseline comes close"

Decided by the maintainer on 2026-11-18.

External test v1 §9 used the phrase without a number: if a baseline comes close, the report says
so, and its story becomes auditability, declining instead of guessing, and cost. For G1:

- A baseline **comes close** on a bar line when XbrlRag does not beat it on that line as §3 reads
  "beats" (two-sided exact McNemar p < 0.05 with XbrlRag ahead on the discordant pairs), **or** its
  value accuracy is within 5.0 points of XbrlRag's on the same cases, as point estimates.
- It is read for `frontier_sec_tools` (each agent) on the pooled cross-filer, cross-period and
  compound cases, and for `long_context_oracle` on the single-filer lookup + compute cases (where
  the bar line already asks only "within 5 points", so the baseline coming close is the expected
  outcome there and is reported, not headlined).
- When a baseline comes close on the pooled line, the report says so in its first paragraph,
  and leads with auditability (cited `fact_id`s), confident-wrong and cost per question instead of
  accuracy. The verdict on the bar is unchanged by this rule.

#### Item 7: a served model id that differs from the freeze record

Decided by the maintainer on 2026-11-18.

Each run records, per case, the model ids the provider reported as serving each call
(`served_models`), and the run's union in its config. The freeze record holds one served id per
model, each equal to the id requested.

- A look result stands when every call of the run was served under the freeze record's id.
- If any call was served under another id, the run still stands and is reported, with the ids,
  the number of calls and cases they served, and the cases' grades shown separately.
- If more than 10% of a run's cases had a call served under another id, the run is reported as
  **not a look result** for that arm; the arm's line of the bar is reported as not measured, and a
  re-run is made only by a later dated amendment, before which nothing in the run is tuned on.
- A provider alias that resolves to a dated snapshot of the same model is not a different id when
  the provider documents it as the same model; the report names it.

#### Disclosures carried into the look

From the freeze README and the pre-freeze measurement (development data only):

- **Stopping rule.** Before the freeze, fixes were limited to regressions our own changes
  introduced and new failure classes seen in two or more cases. Everything else is listed here.
- **Key side:** FY2026 screens that drop members as `fsds_not_published` (MU, COST) before their
  quarter's FSDS appears, kept strict (decided 2026-11-15); INTC restructuring keyed on a rank-50
  component element; the items Amendment 2 lists.
- **Grader 12:** a pick sentence worded "R&D-to-sales intensity" can fall back to a later
  superlative sentence (`int-a958f40b4ad4`, one case; the system's answer was right).
- **System:** one compound case's first sub-query stored empty rows while its text states them
  (`int-f3864a210cd8`, one case, cause not traced); the intent_router v5 sentence "lift only the
  company holding that position" rests on one observed case, and did not narrow a company list in
  the pre-freeze runs.
- **The pre-freeze evidence is in-loop.** Every fix since 2026-11-10 was chosen from development
  failures (freeze README); those numbers say nothing about H-F or H-X.
- **Corpus fingerprint not stored by v2 runs** (above).

### Amendment 3.1 (2026-11-18): which corpus each set reads, and the tools agent's universes

The merge of this amendment is its timestamp. The maintainer decided it on 2026-11-18, after
Amendment 3 and before any look. No held-out set was run or read for it. Only counts and hashes
of the files below were shared. It corrects two gaps in Amendment 3 that would have measured the
setup instead of the systems. The code, prompts and configs of the freeze record are unchanged.

**H-X and set B read `xbrl_db`; H-F reads `xbrl_heldout`.** Amendment 3 had every G1 and G2 run
read `xbrl_heldout`. H-X and set B are written over development filers only (§1; guide 12), and
their keys were checked against `xbrl_db`: H-X's with `hx.py check-keys` on 2026-11-18.
`xbrl_heldout` also holds the 8 held-out filers, so a question over every covered company would be
answered over 41 filers while its key covers 33. So:

- H-X and set B runs use the development environment: `xbrl_db`, its Qdrant collections and filing
  roots.
- H-F runs use the held-out environment (`heldout.ps1`): `xbrl_heldout` and its filing roots.
- The run script records each database's fingerprint before its first run and after its last; the
  two must match. The sentence "The G1 and G2 runs read `xbrl_heldout`" in Amendment 3 is replaced by
  this paragraph. The cap table does not change.

**The named groups hold for the held-out corpus.** The H-F keys apply the committed group
`definitions` of `configs/eval/v2/groups.yaml` to the committed and held-out sector tags
(hf-suite-build §4). XbrlRag reads the committed file's generated member lists.
[`amendment3/g1_universes.py`](amendment3/g1_universes.py) applies the same function the H-F build
used (`heldout_groups`). On 2026-11-18 it found that no held-out filer joins a group:

| Group | Members | Committed | Held-out |
|---|---|---|---|
| `semiconductors` | 8 | 8 | 0 |
| `retailers` | 4 | 4 | 0 |
| `big_tech` | 7 | 7 | 0 |

The generated file (`groups_heldout.yaml`, SHA-256
`46f0ae8c1596b6094f40571e58fb933222dbc8715a03a500a2d50fbd8a350cbf`) therefore has the committed
members. Every run uses the committed `groups.yaml` (freeze record SHA-256
`8f17215aefa2569a477889509c9514b40c370b5def4d4bdf2988ce3b4c86335f`) unchanged, and nothing
outside `docs/` differs from the look commit.

**The tools agent is told the held-out corpus on H-F.** Without a universe on its cases,
`frontier_sec_tools` is told the development universe from `configs/entities/sectors.yaml`.
That file never holds a held-out filer, so on H-F the agent would rank "every covered company"
without them. The arm's `--sec-universes` option exists for this (frontier-sec-tools requirement
29). Every `frontier_sec_tools` H-F run therefore passes `--sec-universes` with
`sec_universes_heldout.yaml`, SHA-256
`25b77f6ac8a55fe7ed5a8e82e7a96522aff2b2df81077512047509b4cd5a552f`, written by the same script:

- `covered` is the H-F covered universe, every filer of the committed lexicon and the held-out
  overlay: 41 filers, 33 development and 8 held-out, with their CIKs from the two tag files;
- `groups` are the groups above;
- `as_of` is 2026-10-29.

The run script checks the file's hash before each such run and records it with the results.
The file stays in the held-out tree, because it names held-out filers. H-X cases carry their
own `universe`, which the arm reads first, and the arm's default is unchanged on H-X and set B.

### Amendment 4 (2026-11-19): the compound grader's citation error, and the re-grade of the look

The merge of this amendment is its timestamp. It is written **after** the G1 and G2 runs and after
their aggregates at grader 12 were read, and **before** any number re-graded at grader 13 was seen.
It corrects a grading error; it changes no system, no key, no case, no threshold and no line of §3.

**The error.** `value_correct` is the arm-neutral metric of §3 ("Comparison metric": value
accuracy, never `passed`, because the baselines cite no `fact_id`). The compound grader broke it.
It grades every part of a compound against the answer, and when any part is `PARTIAL` it gives the
compound a new, bare `PARTIAL` grade that carries no matched value, which `value_correct` never
accepts. A part is `PARTIAL` whenever the answer cites no `fact_id`. So an answer that cites
nothing, which is every arm but `xbrlrag_full`, could never be value-correct on a compound case,
however right its numbers.

**How it was found.** At grader 12, both tools agents scored 0 of 51 H-F and 0 of 18 H-X compound
cases. The figure was checked at no cost on development data only: the stored answers of the
`xbrlrag_full` dev compound run `eval-c78dd0934732` are 118 of 136 value-correct as stored, and 0
of 136 when only their `fact_ids` are removed; the 593 non-compound dev answers
(`eval-afb40ef42cb9`) are 500 value-correct with or without them. No held-out case, answer or
per-case grade was read.

**Why it was not caught before the look.** No arm other than `xbrlrag_full` had ever been graded
on a compound case. The tools agents and the oracle were measured, frozen and capped on the dev
sample (Amendment 1, 1.4), which was drawn before the compound shape existed and holds none.
Amendment 3's cap table noticed the missing basis for the agents' compound cost and substituted the
D6 budget, but the same gap in grading was not checked. The arm-neutral rule
(`arm-neutral-grading`) predates the compound grader, and the compound grader shipped without a
test of an uncited answer.

**The fix: grader 13.** [compound-value-correct](../../specs/compound-value-correct.md) (spec
PR 516, implementation PR 517): a compound is value-correct exactly when every graded part is,
cited or not. Its `grade`, `passed`, `checks`, `incomplete` and `confident_wrong` are unchanged, so
a compound still passes only when every part is `CORRECT`. The rule applies to every arm alike. On
the dev run above, grader 13 gives 118 of 136 with and without `fact_ids`.

**The re-grade.** Every G1 and G2 run (19 runs) is re-graded at grader 13 from its stored answers,
at no cost: no system, LLM or network is called, and each re-grade is stored as a new run that
names its source; the original runs are kept. Each re-grade attaches grounding on the corpus the
run answered on, stated as the fingerprint the batch recorded before its first run and after its
last (`xbrl_hx` `pm:ca0f8f27e42d39d9`, `xbrl_heldout` `pm:cc7bd7398c9b9cf0`), and only after
checking that the database still has it. The maintainer runs it; only aggregates are shared.

**How the look is read after the re-grade.**

- The grader-13 runs are the look results. The grader-12 figures are published beside them for
  every number that differs, with this amendment.
- Grader 13 changes only `value_correct` on compound cases. Cost, confident-wrong and `passed`
  cannot move; grounding is recomputed by the same function on the same corpus. **The two bar
  lines found missed at grader 12, the cost ratio (0.168 against at most 0.10) and confident-wrong
  (2.9% against at most 1%), are unaffected, so the bar stays missed whatever the re-grade
  shows.** Line-1 accuracy, line 3, paraphrase stability (read from `value_correct` per intent),
  grounding, item 6 and the H-X direction (§4) are recomputed and reported as they come out,
  whichever way they move.
- The correction was made after a held-out aggregate was seen. It is disclosed as such with the
  results. It applies a rule fixed before the look (`arm-neutral-grading`) to a code path that did
  not follow it, and it was chosen and specified on development data only.

### Amendment 5 (2026-11-20): two more grading errors in the baselines' answers, the re-grade at grader 14, and the failure-class sample

The merge of this amendment is its timestamp. It is written **after** the grader-13 re-grade of
the look (Amendment 4) and after its aggregates were read, and **before** any number re-graded at
grader 14 was seen. Like Amendment 4, it corrects grading; it changes no system, no key, no case,
no threshold and no line of §3.

**How it was found.** Amendment 4 left the tools agents' compound grading unvalidated: no
non-XbrlRag arm had been graded on a compound case in development. Both tools agents were
therefore run on 15 **development** compound cases (`frontier_sec_tools` on Opus,
`eval-6d3120902a79`, and on deepseek-v4-pro, `eval-09d1506ac876`; about $5). The maintainer then
marked each of the 30 answers right or wrong against its key, blind to the grades and to Claude's
reading. Of the 20 answers marked right, grader 13 credits 7; it credits none of the 10 marked
wrong. All 13 answers it fails have the right final value. Claude's own earlier reading found only
9 of them, which is why the human review, not Claude's reading, is the reference.

**The errors.**

1. **The text readers read a JSON envelope.** The baseline arms (`frontier_sec_tools`,
   `long_context_oracle`, `closed_book`) reply with a JSON object, and the answer's text keeps the
   whole object. Every deterministic text reader of the grader (the pick reader, the disclosure and
   phrase checks, stated ranks, stated aggregates) read the object's `rows` and `statements` as if
   they were prose; XbrlRag's text is prose. The judge already read the object's `"text"`
   (`judge-baseline-claim-text`). Three answers fail because the pick reader found the wrong
   winner in the envelope.
2. **The generated compound's ranking part compares what its question does not ask.** Both
   generated compound shapes ask about "the top N" (which of them has the highest ratio, or the
   growth of the one ranked first), not for the order of the N or their figures. Eight answers name
   the right N companies in their rows but carry the second step's figures there, ordered by them,
   and fail on order or figures.

**The fix: grader 14.** [prose-and-ranking-names](../../specs/prose-and-ranking-names.md) (spec
PR 519, implementation PR to follow). The grader reads a JSON reply's `"text"`, as it reads
XbrlRag's prose; in `compound.rank_by_ratio` and `compound.leader_growth`, the ranking part is right
when its rows' first N names equal the key's top N as a set (the maintainer's decision, 2026-11-20;
disclosure checks still apply). H-X compounds and standalone screens are graded as before; the rule
applies to every arm alike. Measured on development data at no cost: on the 30 reviewed answers,
grader 14 credits 18 of the 20 marked right and none of the 10 marked wrong; the two it still fails
are single cases (a ranking stated only in prose, an answer naming only the leader), left as known
misses rather than graded around. Over 985 other stored baseline answers (the dev sample and the
full dev suite) and 372 XbrlRag answers, no `value_correct` changes.

**The re-grade.** As in Amendment 4: every G1 and G2 run (19 runs) is re-graded at grader 14 from
its stored answers at no cost, stored as a new run naming its source, with grounding attached on
the corpus each run answered on (`xbrl_hx` `pm:ca0f8f27e42d39d9`, `xbrl_heldout`
`pm:cc7bd7398c9b9cf0`) after checking that the database still has it. The maintainer runs it; only
aggregates are shared.

**How the look is read after the re-grade.**

- The grader-14 runs are the look results. The grader-12 and grader-13 figures are published beside
  them for every number that differs, with Amendments 4 and 5.
- Grader 14 can change `value_correct`, `passed` and `confident_wrong` on the baselines' answers
  and on the generated compound cases. For `xbrlrag_full` it can only add value-correct answers on
  generated compound cases (its text is already prose), so **its confident-wrong (the bar line found
  at 2.9% against at most 1%) can only fall**. **The cost ratio (0.168 against at most 0.10) cannot
  move, so the bar stays missed whatever the re-grade shows.** Every other figure (line-1 accuracy,
  line 3, stability, grounding, item 6 and the H-X direction of §4) is recomputed and reported as it
  comes out, whichever way it moves.
- The corrections were made after held-out aggregates were seen and are disclosed as such. They
  were found, chosen and specified on development data only, against a blind human reading; no
  held-out case, answer or per-case grade was read.

**A known limitation of the keys.** The same review found one development key built on the wrong
concept: for "Other Nonoperating Income (Expense)" the key used a filer's broader
`NonoperatingIncomeExpense` (629m) although its preferred `OtherNonoperatingIncomeExpense` (172m) is
filed, so both agents' right answer was marked wrong against it. Keys built the same way may carry
the same defect on held-out sets. It is not measured or corrected for this look (the maintainer's
decision) and is published as a limitation with the results.

**Failure classification (§5, runbook 4.6).** XbrlRag's held-out misses at grader 14 are classified
by the maintainer, who did not do the tuning, on a seeded random sample rather than all of them: 40
misses (or all, if fewer), drawn with the seed `g1-failclass-v1` and split by set and kind in
proportion to the misses (largest remainder). A miss is a result that is not value-correct, has no
run error and is not a judged text case. The classes are those of the development rules (phrasing,
entity, period, concept, plan, synthesis, key) plus grader and other. Only the counts per class
leave the look folder, published with the sample's size and the population it was drawn from.
