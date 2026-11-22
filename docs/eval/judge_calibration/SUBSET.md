# E3 calibration subset

The maintainer labels a seeded random subset of the dev text questions, not all 37 (decided
2026-11-12, to cut labelling time). The subset is drawn by seed, never by how answers look.

- **Rule:** order the 37 case ids by `sha256("xbrlrag-e3-subset-1:" + case_id)`; take them in that
  order, with all of each case's answers, until every label dimension has at least 100 labels
  (judge-calibration `min_items`).
- **Taken:** 23 of 37 questions, 68 of 109 answers.
- **Labels:** correct 150, supported 103, covered 213
  (full export: 242, 167, 357).
- **Answer-set hashes:** subset `6bdd7aec8aaa4ee853c06f761f9408c8327a6d1b4af56c77ae0c7e4e95b3dc99`; full `b38af3012c2fe6263dccacaa2e132931715b3b4cb2a178783c83cac30e937861`.
- `answers_full.json` is the full export, unchanged; `answers.json` is the subset the label sheet
  and `calibrate` use. Rebuild: `python data/handover/e3_subset.py <checkout>` from the full export.
- Cases, in draw order: jcdev-qcom-2024-apple-modem, jcdev-mrk-2025-keytruda, jcdev-nvda-2025-export-controls, jcdev-intc-2024-restructuring, jcdev-tsla-2022-key-person, jcdev-aapl-2024-outsourcing, jcdev-amd-2023-segment-drivers, jcdev-abt-2023-nec-litigation, jcdev-pg-2025-restructuring-plan, jcdev-nflx-2023-streaming-drivers, jcdev-intc-2024-valuation-allowance, jcdev-crm-2024-dividend-restructuring, jcdev-mu-2023-china-cac, jcdev-tsm-2024-arizona, jcdev-txn-2023-capital-allocation, jcdev-amzn-2022-rivian, jcdev-cost-2024-membership, jcdev-orcl-2024-datacenter, jcdev-ceg-2023-nuclear-ptc, jcdev-msft-2023-useful-lives, jcdev-nvda-2025-datacenter-drivers, jcdev-wmt-2025-margin-drivers, jcdev-lly-2024-revenue-drivers.

Amendment 2 records this: the judge is calibrated on this subset.
