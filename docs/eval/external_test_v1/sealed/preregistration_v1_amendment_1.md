# XbrlRag blog v1: pre-registration v1, amendment 1

**Date:** 2026-10-28.

**Status:** sealed by the merge of this file into `main`, and by an OpenTimestamps proof
over the hashes in [`HASHES_v1_amendment_1.txt`](HASHES_v1_amendment_1.txt).

[Pre-registration v1](preregistration_v1.md) says that any change to it is recorded as a
numbered amendment with its date and reason. This is amendment 1. It changes the three
items below and nothing else in v1.

## 1. The A′ beacon minute moves earlier

**Change.** v1 §4.1 fixes the NIST Randomness Beacon 2.0 pulse for 2026-11-02T15:00Z.
That minute is replaced by **2026-10-29T15:00Z**, on one condition:

- The OpenTimestamps proof of `HASHES_v1_amendment_1.txt` must be confirmed in a Bitcoin
  block (`ots verify` succeeds) **before 2026-10-29T12:00Z**.
- If it is not, this change lapses and the v1 minute, 2026-11-02T15:00Z, stands. No
  further amendment is needed for that.

Pre-registration v2 records the `ots verify` output (block height and time) that shows
which minute applies.

**Reason.** The work the draw waited for, the main-suite runs and the held-out
environment, can be finished by 2026-10-28, so waiting until 2026-11-02 only delays the
held-out runs. The new minute keeps what made the old one sound:

- It is later than this amendment's merge and its timestamp, so the pulse, and so the
  draw, was unknowable when the minute was fixed.
- It is at least 24 hours after the merge of v1 (2026-10-28T12:49Z), the same distance
  that v1 §4.1 requires when the minute has to move.
- The pool, the strata, the ranking hash and the eligibility rule are unchanged.

**Consequence.** v1 §4.1 includes a drawn filer's FY2026 10-K only if EDGAR shows it filed
before the beacon minute. That cutoff moves with the minute, to 2026-10-29T15:00Z. Any
10-K filed between that time and 2026-11-02T15:00Z is excluded; the post says so.

The draw command becomes:
`scripts/eval/aprime_draw.py draw --pulse-time 2026-10-29T15:00Z`, or with
`--pulse-time 2026-11-02T15:00Z` if this change lapsed.

## 2. Cost caps for M5–M10 and H3

**Change.** The caps per run in v1 §6 become:

| Run | v1 cap | New cap | Expected cost |
|---|---|---|---|
| M5 `long_context`, `gpt-5.6-sol` | $40 | **$20** | $9.3 |
| M6 `long_context`, `claude-opus-5-5` | $40 | **$30** | $13.6 |
| M7 `xbrlrag_full`, `openai-5.6` | $15 | $15 (unchanged) | $7.0 |
| M8 `naive_rag_filtered`, `openai-5.6` | $10 | **$15** | $5.6 |
| M9 `xbrlrag_full`, `anthropic` | $15 | **$20** | $9.6 |
| M10 `naive_rag_filtered`, `anthropic` | $10 | **$15** | $6.4 |
| H3 `long_context` on B, `gpt-5.6-sol` | $10 | **$20** | $8.7 |
| H3 `long_context` on B, `claude-opus-5-5` | $10 | **$30** | $13.2 |

Every other cap in v1 stays as it is. Each new cap is at least twice the expected cost,
rounded up to the next $5.

**Reason.** v1 reruns a run that hits its cap once, in full. A cap below the run's real
cost would make it fail twice and spend twice. v1's caps for M8–M10 and H3 were estimates
made before these systems had run on the other profiles, and the expected costs above
now come from measurements on the main suite (the development set), on 2026-10-28:

- **M5, M6 and H3:** two filings outside the 60 long-context cases, three questions each,
  on both models (eval runs `eval-943e0f821c67` and `eval-2540fa6000cb`). Each provider
  served the cached filing to every question after the first. Expected costs scale the
  measured cost per token to the actual size of the 20 frozen filings, and, for H3, to
  the 20 distinct filings that B's author reports for B. The H3 figure is the only input
  taken from B; its questions were not opened.
- **M7–M10:** ten main-suite cases, two of each kind, chosen by
  `sha256("xbrlrag-costsample-v1|" + case_id)`, run once on each of `xbrlrag_full` with
  `openai-5.6` and `anthropic`, and `naive_rag_filtered` with `openai`, `openai-5.6` and
  `anthropic` (eval runs `eval-d90fa91af9c4`, `eval-fa993848917d`, `eval-072ea33ff73e`,
  `eval-51041359332d`, `eval-e3eb775fc4b2`; $0.62 in all). XbrlRag's expected cost
  scales the full `openai` run `eval-84a9a08f3945` ($2.63) by the cost ratio on the same
  ten cases; `naive_rag_filtered` scales each kind's mean cost by its case count.

Caps limit spend and do not change what a run measures. No measured run had been made
when this amendment was written.

## 3. Correction: v1's hash file

v1's status line says its proof covers the hashes in `HASHES.txt`. That file is v0's.
v1's hashes are in [`HASHES_v1.txt`](HASHES_v1.txt), and its proof is
`HASHES_v1.txt.ots`.
