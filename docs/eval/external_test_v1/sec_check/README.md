# The main suite's expected values checked against the SEC

This folder holds the evidence that **the main suite's expected
values match the SEC's published XBRL figures.** The check confirms our ingest and our
choice of concept, unit and period. It does **not** show independence from the filings,
because the SEC's companyfacts data is built from the same XBRL.

## Results

| Kind | Cases | Match | Mismatch | Can't be checked | Hand check |
|---|---:|---:|---:|---:|---:|
| lookup | 199 | 188 | 0 | 11 | 0 |
| compute | 108 | 104 | 0 | 4 | 0 |
| screen | 22 | 19 | 0 | 1 | 2 |
| dimensional | 100 | 0 | 0 | 0 | 100 |
| breakdown | 53 | 0 | 0 | 0 | 53 |
| **All** | **482** | **311** | **0** | **16** | **155** |

- **Automatic check: 311 of 311 checkable cases match, 0 mismatches.** A match needs the
  same concept, unit, period and **accession** (the very filing we cite) as the SEC's
  entry.
- **Can't be checked: 16 cases, all TSMC FY2025.** On 2026-10-21, companyfacts held only 2
  facts from that 20-F (accession `0001628280-26-025362`), against about 700 from each
  earlier one. The SEC hadn't extracted it yet.
- **Hand check: 18 of 18 match.** companyfacts carries no segment, product or region
  figures. So a seeded sample (seed `20261021`) of 12 dimensional and 6 breakdown cases
  was checked by hand against the 10-Ks on 2026-10-26, by the project owner rather than
  the system's author. There are 18, not 20, because the TSMC geography template produced
  no cases.

## Files

| File | What |
|---|---|
| `report.json` | Every case's verdict, each cited fact's status and SEC value, restatements, the summary, and the hand-check selection |
| `report.md` | The same as tables, plus every case that isn't a match, with its reason |
| `hand_check_worksheet.md` | The 18 hand-checked cases, with filing links and the recorded results |

## How it was produced

- **Suite:** `test/eval/suites/sql_derived_v1.json`, corpus fingerprint
  `pm:3bf0c14a2f4afad3`.
- **Run:** 2026-10-22, using `scripts/eval/check_against_sec.py`; spec
  `docs/specs/sec-companyfacts-check.md`.
- **Code:** the report's `git_sha` is `bcb83ee`, because the run was made on the
  implementation branch before its code was committed. The code that produced it is
  commit `4b15f27`, merged as PR 120.
- **SEC data:** fetched once per filer (12 requests) from
  `https://data.sec.gov/api/xbrl/companyfacts/`.

To rerun it (no LLM, $0):

```powershell
conda run -n py312 python scripts/eval/check_against_sec.py --out-dir <folder>
```

The SEC's data changes as filings are added or restated, so a rerun may differ. For
example, TSMC FY2025 becomes checkable once the SEC extracts it.
