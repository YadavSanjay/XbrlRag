# screen-exact-fiscal-year — a screen for fiscal year N ranks only fiscal-year-N figures

The one real silent-wrong in the 486-case baseline (`eval-7f966d6da489`, case
`aq-5b-total-assets-screen:fy=2026`).

## Requirement

`XbrlFactstoreScreen` takes, per filer, the latest fiscal year **≤** the requested one. Every
per-view query uses `fiscal_year <= %(fy)s … DISTINCT ON (ticker) ORDER BY ticker,
fiscal_year DESC`: flat, geographic, segment and derived. A filer without a fiscal-year-N annual
report is therefore ranked on an **older** year, and the row is presented as an answer about
year N.

On the 2026-10-14 corpus, "top 5 covered companies by total assets in fiscal 2026" ranks AMZN
818.0B, MSFT 758.4B, GOOGL 595.3B, META 366.0B and AAPL 359.2B. Four of the five are FY2025
figures. Only MSFT, ORCL and NVDA have filed FY2026 annual reports. The answer is confident,
cited and wrong.

After this change, a screen for fiscal year N ranks only rows whose `fiscal_year` is N. Filers
whose latest figure is from an earlier year are not ranked. They are returned as **excluded**,
with that latest year, so the answer can say which companies have not reported year N yet,
instead of silently substituting.

## Acceptance criteria

- **C1**: `select_fiscal_year(rows, fy)` takes the per-filer rows the screen queries return
  (each with `ticker` and `fiscal_year`) and returns `(kept, excluded)`:
  - **kept** is the rows whose `fiscal_year == fy`, in input order;
  - **excluded** is one `{"ticker": t, "latest_fiscal_year": y}` per ticker with no kept row,
    where `y` is the largest `fiscal_year` among that ticker's rows, sorted by ticker.

  A row with `fiscal_year` `None` counts as not N. A ticker whose only rows have `None` is
  excluded with `latest_fiscal_year` `None`. Oracle: the requirement; the 2026-10-14
  total-assets rows above as the point case.
- **C2**: `XbrlFactstoreScreen.screen(step)` for a step with `fy = N` returns only rows with
  `fiscal_year == N`, ranked and cut to `top_n` as today, on each of the four views.
  `result.to_dict()["excluded"]` lists the filers left out, as in C1. When nothing is kept, the
  status is `NOT_FOUND`, with the excluded filers still listed.

  Oracle: requirement; a fake fact store (the DB is an I/O boundary) holding the point case.
  Expected: MSFT, ORCL, NVDA ranked and the nine FY2025 filers excluded.
- **C3**: Metamorphic: adding a filer whose figures are all from earlier years leaves the ranked
  rows unchanged and adds exactly that filer to `excluded`. Removing every fiscal-year-N row
  yields `NOT_FOUND`. Oracle: the requirement.

## Interface

New function (stubbed in the spec PR):

- `src.retriever.xbrl.xbrl_factstore_screen.select_fiscal_year(rows: Sequence[dict], fy: int) -> tuple[list[dict], list[dict]]`

Behavior on existing code (added by the implementer; a spec PR may only add stubs):

- `ScreenResult` gains `excluded: list[dict]` (default empty), written by `to_dict()` under
  the key `excluded`.
- `screen()` applies `select_fiscal_year` to each view's rows before ranking. The queries keep
  `fiscal_year <= fy` (that is how the screen learns a filer's latest year), and the module
  docstring changes to say so.
- `XbrlRetrievalPipeline._screen_step` carries `excluded` onto the `StepResult`, and
  `StepResult.to_dict()` writes it when non-empty, next to `rows`. The synthesizer then sees it
  in FACT_RESULTS and can say which filers have not reported year N. No prompt change.

## Notes

- Evidence (2026-10-14, local `xbrl_db`, the flat screen's own SQL for TotalAssets):
  MSFT 758,376,000,000 (FY2026), ORCL 261,759,000,000 (FY2026), NVDA 206,803,000,000 (FY2026);
  AMZN, GOOGL, META, AAPL, TSM, AVGO, TSLA, MU and AMD have FY2025 only.
- Eval: the suite's screen templates already use `fiscal_year = %(fy)s`, so ground truth is
  unchanged; this brings the system in line with it.
- Out of scope: a "latest available" screen (no year asked). The plan always sets `fy` today; a
  question without a year needs its own planner-level handling.
