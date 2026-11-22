# eval-intents — question intents, a phrasing renderer and a hash split

The mechanical half of v2 question generation, in
[XbrlRag_v2_Plan.md](../design/XbrlRag_v2_Plan.md) §3.1 (dev / validation split), §3.2
(phrasing variety), §3.4 (question types) and §3.8 (an intent spec plus phrasing renderer in
`src/eval/suites/`). The semantics the kinds will later be keyed against are in
[XbrlRag_Eval_Semantics.md](../design/XbrlRag_Eval_Semantics.md).

## Requirement

v1 asked each question in one templated wording, and its suite was both the tuning set and the
scoring set. v2 separates *what is asked* from *how it is worded*:

- An **intent** is a structured, validated description of one question: its kind, filers,
  fiscal years, metrics, and the dimension, operation, universe, count or filing year the kind
  needs. It has a stable id that does not depend on how it was constructed.
- A **phrasebook** of hand-written templates and a **lexicon** of surface forms (company names,
  metric synonyms and shorthand, dimension and universe phrases, fiscal period ends) are loaded
  from YAML and validated when loaded: a template that could not be filled for a kind it claims,
  or a lexicon that cannot be rendered cleanly, fails at load, not at question time.
- A **renderer** turns (intent, phrasing, lexicon) into one question by exact, documented
  substitution. A missing lexicon entry raises; nothing falls back silently.
- A **split** assigns each case to `dev` or `validation` by two independent hash axes, one over
  intents and one over templates. A case is `validation` if either axis says so, so no
  validation intent and no validation template ever appears in a dev case: a fix tuned on dev
  wording and dev intents is checked on wording and intents it never saw.
- A **builder** expands intents into phrased cases, choosing a fixed number of phrasings per
  intent by a SHA-1 selection key (the idiom of [stable-case-selection](stable-case-selection.md)),
  so the suite is deterministic and adding an intent never changes another intent's cases.

### Kinds and shapes

The eight intent kinds are `lookup`, `dimensional`, `breakdown`, `compute`, `trend`,
`point_in_time`, `screen` and `peer_relative`. The wording of `compute` and `screen` depends on
the operation, so templates are keyed by **shape**: the kind itself, or `kind.operation` for
`compute` and `screen`. The 14 shapes are `lookup`, `dimensional`, `breakdown`,
`compute.yoy_growth`, `compute.difference`, `compute.cagr`, `compute.ratio`, `trend`,
`point_in_time`, `screen.top`, `screen.bottom`, `screen.sum`, `screen.median`, `peer_relative`.

### Intent fields and per-kind validation

`Intent(kind, *, tickers=(), fiscal_years=(), metrics=(), dimension=None, operation=None,
universe=None, n=None, filing_year=None)`. Normalisation, applied before validation:

- `tickers`: each stripped and upper-cased; a tuple in the given order. An empty string or a
  repeated ticker (after normalisation) raises `ValueError`.
- `fiscal_years`: a tuple of `int`, sorted ascending, duplicates removed. A non-`int` (or
  `bool`) entry raises `ValueError`.
- `metrics`: a tuple of non-empty strings (canonical metric ids) in the given order (for
  `compute.ratio` the first is the numerator). A repeated metric raises `ValueError`.
- `dimension`, `operation`, `universe`: `str` or `None`; `n`, `filing_year`: `int` or `None`.

Validation (any violation raises `ValueError`); "—" means the field must be empty/`None`:

| Kind | tickers | fiscal_years | metrics | dimension | operation | universe | n | filing_year |
|---|---|---|---|---|---|---|---|---|
| `lookup` | 1 | 1 | 1 | — | — | — | — | — |
| `dimensional` | 1 | 1 | 1 | required (a member id) | — | — | — | — |
| `breakdown` | 1 | 1 | 1 | required (an axis id) | — | — | — | — |
| `compute` | 1 | per op | per op | — | required: `yoy_growth`, `difference`, `cagr`, `ratio` | — | — | — |
| `trend` | 1 | ≥ 3 | 1 | — | — | — | — | — |
| `point_in_time` | 1 | 1 | 1 | — | — | — | — | required, > the fiscal year |
| `screen` | 0 | 1 | 1 | — | required: `top`, `bottom`, `sum`, `median` | required | ≥ 1 for `top`/`bottom`; — for `sum`/`median` | — |
| `peer_relative` | 1 | 1 | 1 | — | — | required | — | — |

`compute` per operation: `yoy_growth` — 1 metric, exactly 2 consecutive years; `difference` —
1 metric, exactly 2 years; `cagr` — 1 metric, exactly 2 years at least 2 apart (the endpoints);
`ratio` — exactly 2 metrics, 1 year. An unknown kind or an operation outside the kind's set
raises `ValueError`. Required strings must be non-empty.

### Placeholders

Template text uses placeholders from the closed set `{company}`, `{metric}`, `{metric_2}`,
`{dimension}`, `{period}`, `{first_period}`, `{last_period}`, `{filing_period}`, `{universe}`,
`{n}` (`{metric_2}` is the ratio denominator; the requested set had no slot for a second
metric). Any other `{...}`, and any lone `{` or `}`, is an error. Which shape can fill which
placeholder, and which it requires every template to use:

| Shape | Fillable | Required |
|---|---|---|
| `lookup` | company, metric, period | all fillable |
| `dimensional`, `breakdown` | company, metric, dimension, period | all fillable |
| `compute.yoy_growth` | company, metric, first_period, last_period | company, metric, last_period |
| `compute.difference`, `compute.cagr`, `trend` | company, metric, first_period, last_period | all fillable |
| `compute.ratio` | company, metric, metric_2, period | all fillable |
| `point_in_time` | company, metric, period, filing_period | all fillable |
| `screen.top`, `screen.bottom` | metric, period, universe, n | all fillable |
| `screen.sum`, `screen.median` | metric, period, universe | all fillable |
| `peer_relative` | company, metric, period, universe | all fillable |

### YAML formats

Phrasebook:

```yaml
templates:
  - id: lookup-conv          # unique, non-empty
    register: conversational # terse | conversational | formal
    shapes: [lookup]         # non-empty list of shapes
    text: "What was {company}'s {metric} in {period}?"
```

Lexicon (every top-level key optional, an absent one is empty; an unknown top-level key raises):

```yaml
companies:   {AAPL: {short: Apple, legal: Apple Inc.}}                 # keys upper-cased
metrics:     {revenue: {label: Revenues, synonyms: [net sales], shorthand: [top line]}}
dimensions:  {segment: [business segment]}                             # non-empty lists
universes:   {big_tech: [big tech]}                                    # non-empty lists
period_ends: {AAPL: {2024: "2024-09-28"}}                              # ISO dates
```

`synonyms` and `shorthand` may be absent (empty). Every surface string is non-empty and contains
no `{` or `}`.

### Rendering

`Phrasing(template_id, *, company_form="ticker", metric_form="label", period_form="fiscal")`.

- `{company}` (the intent's one ticker `T`): `ticker` → `T`; `short` → `companies[T].short`;
  `legal` → `companies[T].legal`. `company_form="none"` is the only valid form for an intent
  with no ticker (a screen), and is invalid for one with a ticker.
- `{metric}` / `{metric_2}` (`metrics[0]` / `metrics[1]`, same form for both): `label` →
  `label`; `synonym:i` → `synonyms[i]`; `shorthand:i` → `shorthand[i]` (`i` a non-negative
decimal integer, 0-based; `synonym` without an index, or `synonym:-1`, is an unknown form).
- `{period}` (the one fiscal year), `{first_period}` / `{last_period}` (the first and last of
  the sorted years), `{filing_period}` (the filing year), for year `Y`:
  `fy_short` → `"FY"` + the last two digits of `Y` (`FY24`, `FY09`); `fiscal` → `"fiscal Y"`;
  `fiscal_year` → `"fiscal year Y"`; `year_ended` → `"the fiscal year ended <Month> <day>, <year>"`
  from `period_ends[T][Y]`, English month name, day without a leading zero (`the fiscal year
  ended September 28, 2024`). `year_ended` is valid only for an intent with exactly one ticker
  whose `period_ends` hold every year the intent renders (its fiscal years and its filing year).
- `{dimension}` → `dimensions[d][0]`; `{universe}` → `universes[u][0]`; `{n}` → `str(n)`.
  (Further phrases in those lists are reserved for a later phrasing axis.)
- The question is the template text with each placeholder replaced by its value and nothing
  else changed (no capitalisation or whitespace fix-ups).

A phrasing names its template by id, so `render(intent, phrasing, lexicon, phrasebook)` also
takes the phrasebook to look the text up. It raises `ValueError` when the template is unknown, the intent's shape is not among the
template's shapes, a form is unknown or not valid for the intent, an index `i` is out of range
for any of the intent's metrics, or a company, metric, dimension, universe or period end the
rendering needs is missing from the lexicon.

### Split

`axis_is_validation(key, *, share, salt)` is
`int(sha1(f"{salt}:{key}".encode("utf-8")).hexdigest()[:8], 16) / 2**32 < share`;
`share` outside `[0, 1]` (including NaN) raises `ValueError`. A case's `intent_axis` is
`"validation"` if `axis_is_validation(intent_id, ...)` else `"dev"`; its `template_axis`
likewise with the template id as the key; its `split` is `"validation"` if either axis is,
else `"dev"`.

### Building phrased cases

For each intent, the **candidate phrasings** are the templates whose shapes contain the
intent's shape (phrasebook order) × the applicable forms, where an axis is varied only if the
template uses one of its placeholders, and otherwise takes its default form:

| Axis | Placeholders | Forms when varied | Default |
|---|---|---|---|
| company | `{company}` | `ticker`, `short`, `legal` | `ticker`, or `none` for an intent with no ticker |
| metric | `{metric}`, `{metric_2}` | `label`; `synonym:i` for every `i` valid for all the intent's metrics; `shorthand:i` likewise | `label` |
| period | `{period}`, `{first_period}`, `{last_period}`, `{filing_period}` | `fy_short`, `fiscal`, `fiscal_year`, and `year_ended` when valid | `fiscal` |

A candidate's **phrasing key** is
`f"{intent_id}|{template_id}|{company_form}|{metric_form}|{period_form}"`. Per intent, the
builder keeps the `min(per_intent, candidates)` candidates whose phrasing key has the smallest
SHA-1 hex digest. Each kept candidate becomes a `PhrasedCase` with
`case_id = intent_id + "-" + digest[:8]`. The result is sorted by `case_id`.

## Acceptance criteria

- **C1**: `Intent` normalises its fields as specified: tickers stripped and upper-cased in
  order, fiscal years sorted and deduplicated, metrics kept in order, all three exposed as
  tuples, and `intent_shape(intent)` is the kind, or `kind.operation` for `compute`/`screen`.
  Two intents built from the same normalised fields compare equal and hash equal, whatever the
  keyword order, ticker case or year order. Oracle: the normalisation rules, applied by hand.
- **C2**: Every row of the per-kind validation table and the per-operation `compute` rules is
  enforced: each violation raises `ValueError`; each valid example constructs. Oracle: the
  table (a valid base intent per kind, mutated one field at a time).
- **C3**: `intent_id(intent)` is `"int-"` + the first 12 hex digits of the SHA-1 of
  `json.dumps(d, sort_keys=True, separators=(",", ":"))`, where `d` has exactly the nine keys
  `kind`, `tickers` (list), `fiscal_years` (list), `metrics` (list), `dimension`, `operation`,
  `universe`, `n`, `filing_year` with their normalised values (`None` → `null`). It is equal for
  equal intents and differs when any field differs. Oracle: the rule, computed in the test
  with `hashlib` and `json`.
- **C4**: `load_phrasebook(path)` returns a `Phrasebook` whose `templates` are `Template`s
  (`id`, `register`, `shapes` tuple, `text`) in file order, and raises `ValueError` for: a
  duplicate or empty id, an unknown register, an empty or unknown shape, an unknown placeholder
  or a stray brace, a placeholder some listed shape cannot fill, a required placeholder missing
  for some listed shape. Oracle: the committed fixture `phrasebook.yaml` read by hand, and one
  hand-made bad file per rule.
- **C5**: `load_lexicon(path)` returns a `Lexicon` whose attributes are plain dicts:
  `companies: {TICKER: {"short": str, "legal": str}}`,
  `metrics: {id: {"label": str, "synonyms": tuple, "shorthand": tuple}}`,
  `dimensions` and `universes: {id: tuple of phrases}`,
  `period_ends: {TICKER: {int year: datetime.date}}`, with ticker keys upper-cased in both
  `companies` and `period_ends`. It raises `ValueError` for: an unknown top-level key, a
  company without `short` or `legal`, a metric without `label`, an empty dimension or universe
  list, an empty surface string, a surface string containing a brace, a period end that is not
  an ISO date. Oracle: the fixture `lexicon.yaml` read by hand, and one bad file per rule.
- **C6**: `render(intent, phrasing, lexicon, phrasebook)` produces exactly the specified
  substitution for every placeholder and form. Oracle: hand-written expected questions over the
  fixtures (e.g. `What was Apple's net sales in the fiscal year ended September 28, 2024?`).
- **C7**: `render` fails loud with `ValueError` for: an unknown template; a template whose
  shapes exclude the intent's shape; an unknown form; `company_form="none"` with a ticker or a
  company form without one; a synonym or shorthand index out of range for any metric;
  `year_ended` without a period end for every rendered year, or for an intent with no ticker;
  a company, metric, dimension or universe missing from the lexicon. Oracle: the rendering rules.
- **C8**: Metamorphic properties of `render`: the output never contains `{` or `}`; changing
  only `company_form` between `ticker`, `short` and `legal` changes the output exactly by
  replacing each occurrence of one company surface with the other (likewise `period_form`
  between `fy_short`, `fiscal` and `fiscal_year` for the period surface). Oracle: the
  substitution rule.
- **C9**: `axis_is_validation` matches the rule, is monotone in `share` (validation at `s` ⇒
  validation at any `s' ≥ s`), is never true at `share=0` and always true at `share=1`, and
  raises `ValueError` for `share` outside `[0, 1]`. In every built case, `intent_axis` and
  `template_axis` are the axis values of its intent id and template id, `split` is
  `"validation"` iff either axis is, and no case whose split is `dev` has a validation intent or
  template. Oracle: the rule, computed with `hashlib`.
- **C10**: `build_phrased_cases(intents, phrasebook, lexicon, *, per_intent, share, salt)`
  keeps, per intent, exactly the `min(per_intent, n)` candidates with the smallest phrasing-key
  digests, with `case_id`, `intent`, `question` (= `render` of that phrasing), `template_id`,
  `register`, `forms` (`{"company", "metric", "period"}`), `split`, `intent_axis` and
  `template_axis` as specified, sorted by `case_id`. It raises `ValueError` for
  `per_intent < 1`, for two intents with the same id, and for an intent no template can phrase.
  Oracle: the candidate enumeration and selection, written out independently in the test with
  `hashlib`.
- **C11**: Metamorphic properties of `build_phrased_cases`: two calls give identical results;
  permuting `intents` gives an identical result; adding intents leaves every existing intent's
  cases unchanged; raising `per_intent` only adds cases to an intent (its earlier cases stay);
  changing `share` or `salt` changes splits but never which phrasings are kept. Oracle: the
  selection depends only on each intent's own id and the phrasebook.
- **C12**: The committed `configs/eval/v2/phrasebook.yaml` and `configs/eval/v2/lexicon.yaml`
  load without error, the phrasebook has at least one template for each of the 14 shapes, and
  each of the three registers is used by at least one template. Oracle: the shape list in this
  spec.

## Interface

New module `src/eval/suites/intents.py` (stubbed in the spec PR):

- `Intent.__init__(self, kind: str, *, tickers: Sequence[str] = (), fiscal_years: Sequence[int] = (), metrics: Sequence[str] = (), dimension: str | None = None, operation: str | None = None, universe: str | None = None, n: int | None = None, filing_year: int | None = None) -> None`
  — attributes `kind`, `tickers`, `fiscal_years`, `metrics`, `dimension`, `operation`,
  `universe`, `n`, `filing_year`; equality and hashing by value.
- `intent_shape(intent: Intent) -> str`
- `intent_id(intent: Intent) -> str`
- `Template` — attributes `id`, `register`, `shapes`, `text`.
- `Phrasebook` — attribute `templates: tuple[Template, ...]`.
- `Lexicon` — attributes `companies`, `metrics`, `dimensions`, `universes`, `period_ends`.
- `load_phrasebook(path: str | Path) -> Phrasebook`
- `load_lexicon(path: str | Path) -> Lexicon`
- `Phrasing.__init__(self, template_id: str, *, company_form: str = "ticker", metric_form: str = "label", period_form: str = "fiscal") -> None`
  — attributes of the same names.
- `render(intent: Intent, phrasing: Phrasing, lexicon: Lexicon, phrasebook: Phrasebook) -> str`
- `axis_is_validation(key: str, *, share: float, salt: str) -> bool`
- `PhrasedCase` — attributes `case_id`, `intent`, `question`, `template_id`, `register`,
  `forms`, `split`, `intent_axis`, `template_axis`.
- `build_phrased_cases(intents: Sequence[Intent], phrasebook: Phrasebook, lexicon: Lexicon, *, per_intent: int, share: float, salt: str) -> list[PhrasedCase]`

`Intent` and `Phrasing` keep their explicit `__init__` (not a generated dataclass one), since it
is the locked signature; the implementer adds `__eq__`, `__hash__` and `__repr__`. `Template`,
`Phrasebook`, `Lexicon` and `PhrasedCase` are declared as empty classes and may be filled in
any form (plain classes or dataclasses) that exposes the attributes.

## Notes

- Config (implementer): write `configs/eval/v2/phrasebook.yaml` (one template family per shape
  first, per v2 plan §3.4, across the three registers) and `configs/eval/v2/lexicon.yaml` for
  the dev filers. Period ends come from the filings' `DocumentPeriodEndDate`.
- Out of scope, later specs: **compound** intents (a screen with a dependent compute or
  narrative), **honesty** intents (decline / false premise / quarter decline tests),
  **narrative** and text intents, relative and calendar period phrasings ("last year",
  calendar-year forms), units requested, two asks in one question, LLM paraphrases,
  **answer keys** (v2 plan §3.3), persisting `intent_id` / `phrasing_family` in `eval_*`
  tables, and wiring the builder into a suite file.
- The fixtures `test/spec/eval-intents/phrasebook.yaml` and `lexicon.yaml` are synthetic. The
  fiscal period ends in them (Apple FY2022–FY2024, Microsoft FY2024) are public facts from the
  filers' 10-K cover pages, used only as rendering inputs.
- Everything here is pure except the two YAML loaders (file reads of committed files); no
  fakes are needed.
