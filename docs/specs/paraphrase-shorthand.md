# paraphrase-shorthand — the meaning check knows analyst shorthand

The meaning check of [eval-paraphrases](eval-paraphrases.md) accepts a paraphrase only when it
contains one of the lexicon's forms for the company, each metric and the universe. The lexicon is
also what the suite renders questions from, so it holds no shorthand the suite would not use. On
the first import (2026-11-02, 1,800 replies from one model of a fourth family), 607 were held for
review. Read by hand, most are correct analyst shorthand: "current ratio", "op margin", "net
margin", "semis", "covered cos", "qcoms". The pass exists to test that shorthand, and the check
removes it.

This change gives the check a second, check-only source of forms. The suite build never reads it,
so the frozen suites do not change.

## Requirement

1. **A forms file.** `load_paraphrase_forms(path, lexicon)` reads a YAML mapping with only these
   top-level keys, each optional:
   - `metrics`: metric id → list of extra forms;
   - `ratios`: list of `{metrics: [numerator, denominator], forms: [...]}`;
   - `universes`: universe id → list of extra phrases;
   - `companies`: ticker → list of extra forms;
   - `possessive_exempt`: list of company forms that get no dropped-apostrophe variant
     (requirement 3).

   It returns `{"metrics": {id: tuple}, "ratios": ((numerator, denominator, forms_tuple), ...),
   "universes": {id: tuple}, "companies": {ticker: tuple}, "possessive_exempt": frozenset}`,
   where `possessive_exempt` holds normalised forms; an absent key gives an empty value. It raises
   `ValueError`, never quoting a form, when: the document is not a mapping; a top-level key is
   unknown; a metric id, universe id or ticker is not in the lexicon; a ratio does not name exactly
   two lexicon metrics; a value is not a list of strings; a form normalises to empty; a form
   contains a built-in drift phrase (eval-paraphrases, *Meaning check*); or a universe phrase
   normalises to one of another universe's lexicon phrases.
2. **Extending a source.** `extend_source(source, intent, forms)` returns a new
   `ParaphraseSource` equal to `source` except:
   - each metric's forms gain `forms["metrics"][metric]`;
   - when `intent.operation == "ratio"` and `tuple(intent.metrics)` equals a ratio's
     `(numerator, denominator)`, **both** metrics' forms gain that ratio's forms;
   - `universe_forms` gain `forms["universes"][intent.universe]`, and `forbidden_forms` gain every
     other universe's extra phrases, minus any that normalise to one of the case's own universe
     forms;
   - `company_forms` gain `forms["companies"][ticker]` for the intent's first ticker, then the
     dropped-apostrophe variants of requirement 3.

   Forms are appended in that order, after the source's own, without duplicates by normalised form.
   A source with no company, metric or universe gains nothing for it.
3. **Dropped apostrophes.** For each company form `f` whose `normalise(f)` is not in
   `possessive_exempt`, the variant `normalise(f) + "s"` is added: `"qcoms"` for `QCOM`,
   `"mcdonalds"` for `McDonald's`. An exempt form gets none, so `COST` does not accept `"costs"`.
4. **Abbreviated months.** In `period_missing`, the period-end form is also accepted with the
   month abbreviated: `jan feb mar apr may jun jul aug sep oct nov dec`, and `sept` for September
   (`sep 28 2024`, `sept 28 2024`). Nothing else in the meaning check changes.
5. **Where it applies.** The paraphrase export and import CLIs read
   `configs/eval/v2/paraphrase_forms.yaml` by default (a `--forms` option overrides it) and apply
   `extend_source` to every source. The suite build, the renderer and the lexicon never read it.
   The forms file's content is reviewed data, added with the implementation.

**In scope:** requirements 1-5. **Out of scope:** the content of the forms file beyond what the
tests fix; any change to the lexicon, phrasebook or suite build; accepting a universe that a
paraphrase does not name ("rank vs peers" stays `universe_missing`); two-digit year forms.

## Acceptance criteria

- **C1**: `load_paraphrase_forms` returns the documented structure for a valid file, and raises
  `ValueError` for each rule of requirement 1. Property: any metric id outside the lexicon raises.
  Oracle: requirement 1 by hand.
- **C2**: `extend_source` adds exactly requirement 2's forms, so the hand-worked paraphrases
  ("AAPL current ratio FY24", "top 3 semis by net income FY25", "GOOGL operating margin FY23") pass
  `check_meaning` after it and fail before, and a semis phrase in a covered-universe question is
  `semantic_drift`. Metamorphic: for a source with no universe, extending never adds a reason to
  any paraphrase, and every form of the source is still in the extended one. Oracle: requirement 2
  and the eval-paraphrases meaning check.
- **C3**: dropped-apostrophe variants exist for non-exempt forms only: `"qcoms"` passes for QCOM,
  `"costs"` does not pass for COST when `COST` is exempt. Property: for any single-token company
  form not exempt, `<form>s` passes. Oracle: requirement 3.
- **C4**: an abbreviated month passes `period_missing` exactly when the full month would. Property:
  for any date, `<abbreviation> <day> <yyyy>` passes iff `<month> <day> <yyyy>` passes. Oracle:
  requirement 4.

## Interface

- `src.eval.suites.paraphrase_forms.load_paraphrase_forms(path: str | Path, lexicon: Lexicon) -> dict[str, Any]`
- `src.eval.suites.paraphrase_forms.extend_source(source: ParaphraseSource, intent: Intent, forms: Mapping[str, Any]) -> ParaphraseSource`

`check_meaning` keeps its signature. The CLIs' `--forms` option (requirement 5) is wiring, checked
by the implementation's re-import, not by a spec test.

## Notes

- Evidence, 2026-11-02, the frozen v2 suite (`--total 300 --per-intent 3 --share 0.15 --salt
  xbrlrag-v2-dev-1`), 1,800 replies: 1,148 kept, 607 held for review, 45 duplicates. Flags by
  shape: ratio intents 207 (current ratio 72, net margin 71, operating margin 52, R&D intensity
  12), single-metric shorthand about 90, universes 230, companies 75 (mostly dropped apostrophes).
  Of the 17 `semantic_drift` and `period_missing` flags read, one changed the meaning (a year
  lost); the drift flags were lead-ins saying "quarterly" or "latest", correctly held.
- Ratio forms attach to a metric *pair*, because "current ratio" names neither "current assets"
  nor "current liabilities"; adding it to either metric alone would accept it for any intent with
  that metric.
- Universe extras are forbidden for the other universes, as the lexicon's own phrases are, so a
  covered-universe question paraphrased with "semis" still drifts.
- Tests use the committed `configs/eval/v2/lexicon.yaml` and forms files they write to `tmp_path`.
  Nothing is mocked.
