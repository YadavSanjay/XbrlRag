# v2 look: system freeze record (2026-11-15)

Pre-registration §6 item 3 asks for the code commit and freeze record of each look. This folder
holds them for the first v2 look (G1 and G2). The later amendment (C4) records this folder's
hashes together with the H-F and H-X hashes. The merge of the PR that adds this folder is the
look commit. Its code is byte-identical to the frozen commit, because the PR adds only files under
`docs/`, and `freeze_record.py` refuses to run otherwise.

| Item | Value |
|---|---|
| Frozen commit | `66d5575e239bbe870b7e27a5c7c6a3e7a1f8161e` (PR 511) |
| Record | [`freeze_record.json`](freeze_record.json), SHA-256 `6280f916b4faa3e30c557cc3339622f634ac60e818430e0a36218fae5001171e`, written by [`freeze_record.py`](freeze_record.py) at `ff9747b` |
| Grader | version 12 (`docs/specs/grader-pick-echo-grow.md`) |
| Prompts, every profile | query_decomposer v5, intent_router v5, plan_generator v13, synthesizer v8, faithfulness_verifier v3; long_context v2, frontier_sec_tools v2, closed_book v1 |
| `frontier_sec_tools` | tools fingerprint `cd4d26db…6249d`, unchanged since the frontier freeze (A7, 3581590); budgets 13 / 22 / $0.23 (Amendment 2 item 2) |
| Development suites | K7 (Amendment 2): dev `sha256:3cb8ff82…7b0d`, validation `sha256:b3936161…9c26`, dev compound `sha256:3a42be6d…f6e3`, validation compound `sha256:00d5ee9f…91e1`, dev sample `sha256:4af3caf4…7263`; file SHA-256s in the record |
| Corpus | `xbrl_db`, fingerprint `pm:ca0f8f27e42d39d9`; Postgres 18.4; Qdrant 1.18.2; RagInference `2ced242` (RagInference-dev, clean) with BGE-M3 and jina-reranker-v3 |
| Served models | each of the 7 G1/G2 models reported itself as the model requested: claude-haiku-5-5, claude-sonnet-5-5, claude-opus-5-5, gpt-6-luna, gpt-6.1-terra, gpt-6.1-sol and deepseek-v4-pro. The probes cost $0.013 |

The config SHA-256s in the record cover the lexicon, phrasebooks, groups, statement rules,
note concepts, key resolutions, reviewers, judge, LLM profiles, pricing and prompt registry.

## Evidence for the freeze (development data only)

All runs are `xbrlrag_full` on the `deepseek` profile and are graded at grader 12.

| Run | What it shows |
|---|---|
| `eval-35fd048b0efc`: confirmation, full dev, 65bf9b9 code | value accuracy 98.7% (625 cases) |
| `eval-c78dd0934732`: dev compound at 6ed865a | 118/136 (86.8%), against 114/136 at the grader-12 baseline `eval-223d517429f8`; the 9 targeted intents now pass |
| `eval-1f401b1ef849`: validation compound at 6ed865a | 44/44 |
| `eval-8068355756e1`: dev sample at 6ed865a | 98/99 on the cases shared with the confirmation run (no regression from prompts v10–v12) |
| `eval-8d8a6a836f99`: v13 slice at 66d5575 (68 labelled compound cases) | 65/68, 0 silent-wrong; +2/−0 against v12 and +6/−0 against v11; the v12 regression (`int-78e4930a6782`) is fixed |
| Tests on this tree | full unit and spec suites; result in the PR |

These are in-loop numbers. Every fix since 2026-11-10 was chosen from development failures, so
they say nothing about the held-out sets.

## The stopping rule

Before this freeze, fixes were limited to:
- regressions that our own changes introduced;
- a new failure class seen in two or more cases.

Everything else is disclosed by C4. No change goes in between this record and the last G1/G2
run. A generic fix found necessary during the look is disclosed, and is never made silently.

## Known issues carried into the look (disclosed by C4)

- **Key side:**
  - FY2026 screens that drop members as `fsds_not_published`, such as MU and COST, before their quarter's FSDS appears;
  - INTC restructuring keyed on a rank-50 component element;
  - the items Amendment 2 already lists.
- **Grader:** a pick sentence worded "R&D-to-sales intensity" can fall back to a later superlative sentence (`int-a958f40b4ad4`, one case).
- **System:**
  - one compound case's first sub-query stored empty rows while its text states them (`int-f3864a210cd8`, one case, cause not traced);
  - the intent_router v5 sentence "lift only the company holding that position" rests on one observed case. It did not narrow a company list in the runs above.
