# Evaluation records

This folder holds the records of XbrlRag's evaluation protocols: preregistrations fixed before
a run, sealed hash manifests, calibration data and hand checks, and the published spec of the
`frontier_sec_tools` comparator. They are **records, not living docs**: most are frozen at the
moment they were written, and several are hash-sealed, so they change only by a dated
amendment, never by rewriting what was recorded. How the evaluation works today is in the
[evaluation design](../design/XbrlRag_Evaluation.md),
[v2 architecture](../design/XbrlRag_Eval_v2_Architecture.md) and the
[operator guide](../guides/xbrl-flow/11-evaluation.md); measured results live in the `eval`
schema and render at `/tech/eval`.

**Provenance references.** These records were written in the private repository where XbrlRag is
developed, and are kept verbatim. Commit hashes, pull request numbers and branch names in them
refer to that repository's history, and run ids (`eval-…`) to its eval database; neither is
published. The public repository starts from a single commit. What a record pins by content (the
SHA-256 values in the freeze records and hash manifests, and the OpenTimestamps proofs in
`external_test_v1/sealed/`) can still be checked against the files here.

## Contents

| Path | What it holds |
|---|---|
| [`external_test_v1/`](external_test_v1/README.md) | The first test outside the development loop (closed): its sealed preregistration, hashes and OpenTimestamps proofs in `sealed/`, and the answer-key check against SEC companyfacts in [`sec_check/`](external_test_v1/sec_check/README.md) |
| [`heldout_v2/`](heldout_v2/README.md) | The v2 held-out protocol: the seeded draw record of the held-out filers, the [preregistration](heldout_v2/preregistration.md), the [measurement plan](heldout_v2/measurement_plan.md) and the [G1/G2 look results](heldout_v2/results_g1.md) |
| [`judge_calibration/`](judge_calibration/LABELLING.md) | Calibration of the text judge against human labels: labelling rubric, the seeded subset, answers, label sheet and judge outputs |
| [`html10k_tables/`](html10k_tables/hand_check_2026-10-30.md) | A dated, independent hand check of the HTML 10-K table oracle |
| [`frontier_sec_tools.md`](frontier_sec_tools.md) | The `frontier_sec_tools` arm's tools, prompt and recorded run fields, published with its results |

## Rules for this folder

- Files under `sealed/` and any file whose hash is recorded elsewhere stay byte-identical; a
  later change goes in a new amendment file next to them.
- The v2 preregistration changes only by appending a dated amendment to its own
  "Amendments" section; earlier text stays as written.
- Held-out question and answer files are not kept in this folder; it holds procedures,
  hashes, filer lists and aggregates.
