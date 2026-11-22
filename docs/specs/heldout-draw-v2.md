# heldout-draw-v2 — the pool and random draw for the v2 held-out filers

XbrlRag v2 holds out **eight filers, four in stratum S and four in D**, drawn from the
S&P 100 remainder and never chosen (v2 plan §4.3). They are ingested only into the
quarantined `xbrl_heldout` and `qdrant-heldout`. The draw reuses the method of the A′ draw
(`docs/specs/aprime-draw.md`): a pool built from a saved constituents list, a NIST Beacon
pulse at a minute fixed in advance, a SHA-256 ranking anyone can recompute, and eligibility
from EDGAR filing indexes only.

This change is a **v2 parameterisation** of that draw. It adds new functions next to the
A′ ones and changes none of them: the A′ files, tests and behaviour stay as they are.

What differs from A′:
- **Pool exclusions.** The sectors Financials, Real Estate **and Utilities**; the corpus
  filers; the wave-1 and wave-2 development filers (v2 plan §4.2); and the named left-outs
  (§4.4) that no sector rule catches. There is **no** `prospective` exclusion: the five
  pre-registration v0 P filers are ordinary candidates unless another rule applies.
- **The lists are committed data,** `configs/eval/v2/heldout_exclusions.yaml`, validated
  by a loader. A listed symbol that isn't among the constituents is a warning, not an
  error (SNDK, for example, may not be in the saved list).
- **A new ranking prefix,** `xbrlrag-heldout-v2|`, so the v2 ranking is independent of
  A′'s for the same pulse.
- **The draw** takes 4 filers and 2 ordered alternates per stratum. Strata (S = Information
  Technology and Communication Services, D = every other sector left) and eligibility
  (an original inline-XBRL 10-K for each of FY2023, FY2024 and FY2025) are A′'s.

Throughout, "normalized" means `src.eval.aprime.core.normalize_symbol`, and `cik10` means
a CIK as a 10-digit zero-padded string.

## Requirement

### The exclusion lists

1. **`parse_exclusion_lists(data)`** validates a mapping with exactly the keys `wave1`,
   `wave2` and `left_out`.
   - It returns `{"wave1": [...], "wave2": [...], "left_out": [...]}`, each list holding
     the normalized symbols in input order.
   - It raises `ValueError` when:
     - `data` isn't a dict;
     - a key is missing, or an unknown key is present (the error names the key);
     - a value isn't a non-empty list;
     - an entry isn't a string, or is empty after stripping;
     - two entries normalize to the same symbol, within one list or across lists (the
       error names the symbol).
2. **`load_exclusion_lists(path)`** reads the UTF-8 YAML file at `path` with
   `yaml.safe_load` and returns `parse_exclusion_lists` of it. A document that isn't a
   mapping raises `ValueError`.

   The committed file `configs/eval/v2/heldout_exclusions.yaml` (written by the
   implementer) holds exactly:
   - `wave1` (v2 plan §4.2, 18 filers): CSCO, ADBE, CRM, QCOM, TXN, NFLX, KO, PG, WMT,
     COST, HD, MCD, LLY, MRK, ABT, UNP, LMT, UPS;
   - `wave2` (§4.2, 8 filers): XOM, V, TMUS, JNJ, PEP, BA, CAT, DIS;
   - `left_out` (§4.4, the left-outs no excluded sector covers, 6 filers): UNH, CVS (the
     insurers outside Financials), GE, GEV, MMM, SNDK (spin-offs with recast history).

   The rest of §4.4 is covered by other rules: banks, brokers, BRK.B and MA by the
   Financials sector, DUK/SO/NEE by Utilities, AMT by Real Estate, and GOOG by the
   corpus rule (it shares GOOGL's CIK).

### The pool

3. **`build_pool_v2(constituents, ciks, corpus_ciks, lists)`** returns `{"candidates":
   [...], "excluded": [...], "warnings": [...], "counts": {...}}`. `constituents` comes
   from `load_constituents`, `ciks` from `cik_map`, and `lists` from
   `parse_exclusion_lists` (its symbols already normalized).
   - **Sectors and CIKs** are validated exactly as in aprime-draw requirement 4: a sector
     that isn't one of the 11 GICS names raises `ValueError` naming the symbol, and
     constituents missing from `ciks` raise one `ValueError` naming every one of them.
     `corpus_ciks` entries are compared as `cik10`.
   - **A list matches** a constituent when the constituent's normalized symbol is in the
     list, **or** its CIK equals `ciks[s]` for some symbol `s` in the list that is present
     in `ciks` (so a share class of a listed filer matches too). A listed symbol missing
     from `ciks` matches by symbol only and is not an error.
   - **Exclusions.** Each constituent is excluded for the **first** reason that applies,
     in this order:
     1. `sector`: its sector is `Financials`, `Real Estate` or `Utilities`;
     2. `corpus`: its CIK is in `corpus_ciks`;
     3. `wave1`: the `wave1` list matches it;
     4. `wave2`: the `wave2` list matches it;
     5. `left_out`: the `left_out` list matches it;
     6. `duplicate_cik`: another constituent not excluded for reasons 1–5 has the same CIK;
        the one whose normalized symbol sorts first is kept, the others are excluded.
   - **`candidates`:** `{"symbol": <normalized>, "name", "sector", "cik": <cik10>,
     "stratum"}` per remaining constituent, sorted by `cik`; `stratum` is `"S"` for
     Information Technology and Communication Services and `"D"` otherwise.
   - **`excluded`:** `{"symbol": <normalized>, "cik", "reason"}` per excluded
     constituent, sorted by `symbol`.
   - **`warnings`:** `{"list": <list name>, "symbol", "reason": "not_in_constituents"}`
     for each listed symbol that equals no constituent's normalized symbol (whether or
     not it matched one by CIK), sorted by `(list, symbol)`.
   - **`counts`:** `{"constituents": n, "S": n_S, "D": n_D, "excluded": n_excluded,
     "warnings": n_warnings}`.
   - The result doesn't depend on the order of `constituents`.

### Ranking

4. **`rank_key_v2(output_value, cik)`** is the lowercase SHA-256 hex digest of the ASCII
   string `"xbrlrag-heldout-v2|" + output_value.upper() + "|" + cik10`.
   **`rank_v2(candidates, output_value)`** returns a new list: each candidate dict copied,
   with `"key": rank_key_v2(output_value, cik)` and a 1-based `"rank"` added, sorted by
   `(key, cik)`. The input list and its dicts aren't modified.

### The draw

5. **`draw_v2(pool, pulse, source, per_stratum=4, alternates=2)`** behaves exactly as
   aprime-draw requirement 10's `draw`, with `rank_v2` in place of `rank`:
   - it returns `{"output_value": <outputValue upper-cased>, "pulse_timestamp":
     <timeStamp>, "strata": {"S": {...}, "D": {...}}}`, and raises `ValueError` for an
     `outputValue` that isn't exactly 128 hexadecimal characters;
   - per stratum it walks the candidates in `rank_v2` order, calls `source.filings(cik)`
     once per candidate walked, applies `check_eligibility` with its default years
     (2023, 2024, 2025), fills `drawn` up to `per_stratum` and then `alternates` up to
     `alternates`, and stops as soon as both are full;
   - each stratum's entry is `{"drawn", "alternates", "checked", "shortfall"}` with
     aprime-draw's entry shapes, and `shortfall = per_stratum + alternates - len(drawn) -
     len(alternates)`;
   - the result doesn't depend on the order of `pool["candidates"]`.

### The CLI (contract; not spec-tested)

6. `scripts/eval/aprime_draw.py` gains two subcommands. The A′ `pool` and `draw`
   subcommands are unchanged.
   - `pool-v2 --constituents CSV --source TEXT --out POOL.json
     [--exclusions PATH] [--tickers-json PATH]`. `--exclusions` defaults to
     `configs/eval/v2/heldout_exclusions.yaml`. Corpus CIKs and `company_tickers.json`
     come from where A′'s `pool` gets them. It writes the pool plus `{"scheme":
     "xbrlrag-heldout-v2", "source", "constituents_sha256", "tickers_sha256",
     "exclusions_sha256", "corpus_ciks": [...]}`, prints each warning, and prints the
     file's SHA-256.
   - `draw-v2 --pool POOL.json --pulse-time ISO --out DRAW.json [--cache-dir DIR]`. It
     refuses a pool file whose `scheme` isn't `xbrlrag-heldout-v2`, and a pulse time
     later than the current time. It fetches the pulse, runs `check_pulse`, then
     `draw_v2` with the EDGAR source, and writes the result plus `"scheme"`, the full
     pulse object and the pool file's SHA-256. It prints the file's SHA-256 and the drawn
     symbols per stratum.
   - Both write with `src.eval.set_b.core.canonical_bytes`, so the files hash
     deterministically. Neither downloads a filing or reads any financial data.

## Acceptance criteria

Fakes sit only at the I/O seam: a fake `FilingIndexSource` returning synthetic records and
recording its calls. Lists, constituents, CIK maps, pools and pulses are built in the
tests; all are synthetic and chosen by hand, except the wave and left-out symbols (v2 plan
§4.2 and §4.4) and the five pre-registration v0 P CIKs (aprime-draw requirement 4).

- **C1**: `parse_exclusion_lists`.
  - Padded, lower-case and dotted symbols (`" csco "`, `"brk.b"`) come back normalized,
    in input order.
  - Each error of requirement 1 raises: a non-dict, a missing key, an unknown key, a
    non-list value, an empty list, a non-string entry, an empty entry, a duplicate within
    a list and a duplicate across lists after normalization.

  Property: for random valid lists, the result is the input normalized, and parsing the
  result again returns it unchanged. Oracle: requirement 1.
- **C2**: `load_exclusion_lists` and the committed lists.
  - The committed file loads to exactly the wave-1, wave-2 and left-out sets of
    requirement 2 (18, 8 and 6 symbols).
  - A YAML file written in the test loads to the parsed lists; a YAML document that is a
    list raises; an unknown key in the file raises.

  Metamorphic: the same lists written in block style, in JSON (flow) style and with
  comments load identically. Oracle: requirement 2 and v2 plan §4.2/§4.4.
- **C3**: `build_pool_v2`.
  - A hand-built constituents list with one constituent per exclusion reason (including a
    Utilities filer, a share class matched to a left-out by CIK, and a GOOG/GOOGL-style
    pair) gives the hand-worked candidates, exclusions, warnings and counts.
  - Precedence: sector before a wave match, corpus before a wave match, wave1 before a
    left_out match by CIK.
  - The five v0 P filers, absent from every list and the corpus, are all candidates.
  - Strata per sector, with Utilities excluded (2 S + 6 D for one filer per kept sector).
  - Errors: an unknown sector, and two missing CIKs named in one error. A listed symbol
    absent from `ciks` is not an error.
  - `corpus_ciks` given unpadded still matches.

  Metamorphic: shuffling the constituents gives an identical result. Oracle: requirement 3.
- **C4**: `rank_key_v2` and `rank_v2`.
  - `rank_key_v2` equals `hashlib.sha256` over the requirement-4 string, computed in the
    test, for a lower-case `outputValue` and an unpadded CIK, and differs from the A′ key
    (prefix `xbrlrag-aprime-v1|`) for the same inputs.
  - `rank_v2` orders by the v2 key, numbers ranks from 1, and doesn't modify its input.

  Property: for random output values and CIKs, `rank_key_v2` equals the hashlib oracle.
  Metamorphic: adding a candidate never changes the others' relative order; shuffling the
  input gives the same output. Oracle: requirement 4.
- **C5**: `draw_v2`.
  - A hand-built pool, with eligibility set per CIK in the fake, gives the expected
    `drawn`, `alternates`, `checked` and `shortfall` per stratum under the **default**
    sizes 4 and 2; the expected order is computed in the test with `hashlib` and the v2
    prefix.
  - Ineligible candidates are skipped but listed in `checked`; `source.filings` is not
    called after both lists are full; a short stratum reports its `shortfall`; a
    malformed `outputValue` raises.

  Metamorphic: shuffling `pool["candidates"]` gives an identical result. Property: for
  random pools, eligibility and sizes, `drawn` followed by `alternates` equals the first
  `per_stratum + alternates` eligible candidates of the stratum in v2 rank order. Oracle:
  requirement 5.

## Interface

New, stubbed in the spec PR, in the new file `src/eval/aprime/v2.py` (comments only at the
top: the stub lint allows no module docstring in a new file):

- `parse_exclusion_lists(data: dict) -> dict`
- `load_exclusion_lists(path: str) -> dict`
- `build_pool_v2(constituents: list[dict], ciks: dict[str, str], corpus_ciks: set[str], lists: dict) -> dict`
- `rank_key_v2(output_value: str, cik: str) -> str`
- `rank_v2(candidates: list[dict], output_value: str) -> list[dict]`
- `draw_v2(pool: dict, pulse: dict, source: FilingIndexSource, per_stratum: int = 4, alternates: int = 2) -> dict`

Not stubbed (implementer, not spec-tested):
- `configs/eval/v2/heldout_exclusions.yaml`, the committed lists (requirement 2). It is
  covered by C2.
- The `pool-v2` and `draw-v2` subcommands of `scripts/eval/aprime_draw.py`
  (requirement 6).
- A short operator section in `docs/guides/xbrl-flow/11-evaluation.md`.

The implementer may reuse `src/eval/aprime/core.py` (`normalize_symbol`, `GICS_SECTORS`,
`check_eligibility`, `STRATUM_S_SECTORS`, ...) but must not change any existing function
there: the retired aprime-draw spec tests keep passing.

## Notes

- **Why a new prefix.** With the same pulse, the A′ and v2 rankings would otherwise be
  identical, so the two held-out sets would be correlated. The prefix makes them
  independent and ties the ranking to this benchmark.
- **Why no `prospective` exclusion.** The v0 P set belonged to external test v1. In v2,
  QCOM is a wave-1 filer and DIS a wave-2 filer; AAPL is in the corpus. EMR and SBUX are
  ordinary candidates.
- **Why match lists by CIK too.** A share class of a development filer (another ticker on
  the same CIK) is the same company; drawing it would put a dev filer into the held-out
  set.
- **UNH and CVS** are listed as left-outs because §4.4 leaves the insurers out, and their
  GICS sector (Health Care) isn't excluded. This is a judgment call to confirm in review.
- **The pulse minute** is fixed in the v2 pre-registration before the draw, and must be
  later than its seal, as for A′.
