# eval-paraphrases — export and import of hand-generated LLM paraphrases

Phrasing source 2 of [XbrlRag_v2_Plan.md](../design/XbrlRag_v2_Plan.md) §3.2: LLM paraphrases
from a model family used neither as a milestone arm nor as the development model, generated
once, reviewed, frozen and hashed, never regenerated per run. They feed the **paraphrase
stability** metric (§3.2) and widen the wording the dev / validation split (§3.1) covers. The
meaning traps the import must catch are fiscal vs calendar periods
([XbrlRag_Eval_Semantics.md](../design/XbrlRag_Eval_Semantics.md) S2) and the group of companies
a cross-filer question covers (S3).

**Maintainer decision (2026-10-29).** The paraphrases are generated **once, by the maintainer,
by hand**, in the web UI of an LLM from a fourth family (not OpenAI, Anthropic or DeepSeek).
Our code makes **no generation call**. This change is the export / import tooling around that
manual step:

1. **export** phrased questions into fixed-format batch files the maintainer pastes into the
   web UI, each starting with a versioned instruction block;
2. **import** the replies the maintainer saves: check their format, check deterministically
   that each paraphrase kept the question's meaning (failures go to a review list with a
   reason, never silently dropped), remove duplicates, give each paraphrase its split, and
   freeze the result with provenance and a content hash.

## Requirement

### Inputs: `ParaphraseSource`

The tooling depends only on its own input record, not on the eval-intents objects, so it is
testable on its own. One `ParaphraseSource` per phrased question:

`ParaphraseSource(case_id, question, *, intent_id, intent_axis, template_axis,
company_forms=(), years=(), period_ends=None, metric_forms=(), universe_forms=(),
forbidden_forms=())`

- `case_id`, `question`, `intent_id`: non-empty strings (after `strip()`).
- `intent_axis`, `template_axis`: `"dev"` or `"validation"` (the eval-intents split axes of the
  phrased case).
- `company_forms`: the surface forms of the question's one company (ticker, short and legal
  name); empty for a question with no company (a screen). Stored as a tuple, order kept.
- `years`: the fiscal years the question names, as `int` (not `bool`); stored sorted
  ascending, duplicates removed.
- `period_ends`: `None` or a mapping `{int year: "YYYY-MM-DD"}`; stored as a `dict`
  (`{}` for `None`). A value that is not an ISO `YYYY-MM-DD` date raises.
- `metric_forms`: one sequence of surface forms per metric (label, synonyms, shorthand);
  stored as a tuple of tuples. It must hold at least one metric, and each metric at least one
  form.
- `universe_forms`: the phrases of the question's universe (a screen or peer question);
  empty otherwise. `forbidden_forms`: phrases whose appearance changes the meaning (another
  universe's phrases). Both stored as tuples.
- Every surface form (company, metric, universe, forbidden) must have a non-empty
  `normalise(form)`.

Any violation raises `ValueError`. Attributes have the parameter names.

### Normalisation

`normalise(text)`:

1. `unicodedata.normalize("NFKC", text).casefold()`;
2. each of `’` (U+2019), `‘` (U+2018), `ʼ` (U+02BC) and `` ` `` (U+0060) becomes `'`;
3. possessives go: `re.sub(r"'s(?![^\W_])", "", t)`, then every remaining `'` is removed
   (`companies'` → `companies`, `FY'24` → `fy24`);
4. the result is `" ".join(re.findall(r"[^\W_]+", t))`.

A text **contains** a form when `n = normalise(form)` is non-empty and `f" {n} "` is a substring
of `f" {normalise(text)} "` (whole tokens, case-insensitive, punctuation-blind).

### Personas

Five personas, in this canonical order: `sell_side_shorthand`, `terse_pm`,
`conversational_retail`, `typo_prone`, `buried_ask` (sell-side shorthand; a terse PM;
conversational retail; typo-prone; the ask buried in context).

`assign_personas(case_id, k)` returns the `k` personas with the smallest
`sha1(f"{case_id}|{persona}".encode("utf-8")).hexdigest()`, listed in canonical order. `k`
outside `1..5` raises `ValueError`.

### The instruction block (versioned prompt file)

`instructions_path(version)` returns `Path(PROJECT_ROOT) / "prompts" / "templates" /
"eval_paraphrase" / version / "instructions.txt"`; a `version` for which `re.fullmatch(r"v[0-9]+", version)` is `None` raises
`ValueError`. The implementer writes `v1` (it is a prompt file, not a spec-branch path). Its
first line is exactly `# paraphrase-instructions v1`, and it states:

- the five persona ids, each with a one-line description;
- the meaning rules: keep the company, the period, the metric, the operation and the scope;
  never turn a fiscal period into a calendar one; never widen or narrow the group of
  companies; typos (typo-prone persona) never touch the company, the metric or the period;
- the exact reply format: one JSON object per line with the keys `case_id`, `persona` and
  `paraphrase`, one line per requested persona, nothing else.

### Export

`export_batches(sources, *, instructions, prompt_version, batch_size=50, personas_per_case=2)`
returns a list of batch texts (the CLI writes them to files):

- `ValueError` when `batch_size < 1`, `personas_per_case` is outside `1..5`, two sources share
  a `case_id`, or the first line of `instructions` is not exactly
  `f"# paraphrase-instructions {prompt_version}"`.
- No sources → `[]`. Otherwise the sources are sorted by `case_id` and cut, in that order, into
  `n = ceil(len / batch_size)` consecutive batches of `batch_size` (the last may be shorter).
- Batch `i` (1-based) of `n`, holding `m` sources, is exactly
  `instructions.rstrip("\n") + "\n\n" + f"### BATCH {i} OF {n} | PROMPT {prompt_version} | {m} QUESTIONS\n"`
  followed, per source, by
  `json.dumps({"case_id": c, "personas": assign_personas(c, personas_per_case), "question": q}, ensure_ascii=False) + "\n"`
  (key order as written, `personas` a list).

### The reply file

The maintainer saves each web-UI reply as a UTF-8 file that starts with a header:

```
# model: <model name as shown in the web UI>
# date: <YYYY-MM-DD the reply was generated>
# prompt: <instruction version, e.g. v1>
{"case_id": "...", "persona": "...", "paraphrase": "..."}
...
```

`parse_reply(text)` returns `{"model", "date", "prompt_version", "records"}`, where `records`
is a list of `{"line", "case_id", "persona", "paraphrase"}` in file order (`line` 1-based):

- A leading U+FEFF is dropped; lines are split on `\n` with a trailing `\r` removed.
- The header is the maximal run of lines, from line 1, whose `strip()` starts with `#`. Each
  must match `^#\s*(model|date|prompt)\s*:\s*(\S.*?)\s*$`; each key appears exactly once, and
  all three appear.
- `date` fully matches `[0-9]{4}-[0-9]{2}-[0-9]{2}` and is a real date
  (`datetime.date.fromisoformat` accepts it). `model`, lower-cased, must not contain `gpt`, `openai`,
  `claude`, `anthropic` or `deepseek`, nor satisfy `re.match(r"o[0-9]", model.lower())` (the families the decision excludes).
- In the body, a line that is blank after `strip()` or whose `strip()` starts with ```` ``` ````
  (a code fence a web UI adds) is skipped. Every other line must be a JSON object with exactly
  the keys `case_id`, `persona`, `paraphrase`, all strings, non-empty after `strip()`, and
  `persona` one of the five. Stored values are stripped.
- A body with no record raises.

Any violation raises `ValueError`.

### Meaning check

`check_meaning(source, paraphrase)` returns the reasons, in this order and each at most once,
that `paraphrase` may not mean what `source.question` means (empty list: passes):

- `company_missing`: `company_forms` is non-empty and the paraphrase contains none of them.
- `period_missing`: some year `Y` in `years` has none of its **accepted forms** in the
  paraphrase. With `YYYY = f"{Y:04d}"` and `YY = f"{Y % 100:02d}"`, the accepted forms are the
  normalised phrases `fy YYYY`, `fyYYYY`, `fy YY`, `fyYY`, `fiscal YYYY`, `fiscal YY`,
  `fiscal year YYYY`, `fiscal year YY`, `YYYY fiscal year`; `<month> <day> <yyyy>` of
  `period_ends[Y]` when present (English month name, day without a leading zero:
  `september 28 2024`); and the bare `YYYY` only when `company_forms` is non-empty (S2: a bare
  year about a single filer is its fiscal year; for a cross-filer question it is ambiguous).
- `metric_missing`: some metric has none of its forms in the paraphrase.
- `universe_missing`: `universe_forms` is non-empty and the paraphrase contains none of them.
- `semantic_drift`: the paraphrase contains a **drift phrase** that `source.question` does not
  contain. Drift phrases: every `forbidden_forms` entry; `calendar`, `quarter`, `quarterly`,
  `q1`, `q2`, `q3`, `q4`, `ttm`, `ltm`, `trailing`, `ytd`, `year to date`, `last year`,
  `this year`, `last fiscal year`, `latest`, `most recent`; and any token of the normalised
  text that is `cy` or matches `^cy\d+$` (a calendar-year abbreviation).

### Import

`import_replies(sources, reply_paths, *, prompt_version, share, salt, sealed=False)` returns a
**paraphrase set** (a plain `dict`):

1. `ValueError` when `reply_paths` is empty, two sources share a `case_id`, `share` is outside
   `[0, 1]` (or NaN), two reply files have equal bytes, a file is not UTF-8, `parse_reply`
   rejects a file, a file's `prompt_version` differs from `prompt_version`, a record's
   `case_id` is not a source's, or a `(case_id, persona)` pair appears twice (in one file or
   across files).
2. Each record's `paraphrase_id` is `f"{case_id}:{persona}"`. A record with a non-empty
   `check_meaning` goes to `review`; the rest are candidates.
3. **Duplicates**, per intent: candidates are taken in ascending `paraphrase_id`. Each intent's
   *seen* map starts with `normalise(question) → case_id` for every source of that intent (on a
   tie, the smallest `case_id`). A candidate whose `normalise(paraphrase)` is already seen goes
   to `duplicates` with `duplicate_of` = the seen value (a kept `paraphrase_id`, or the
   `case_id` whose question it repeats) and `kind` = `"exact"` if its text equals that text
   exactly, else `"near"`. Otherwise it is kept and its key is added to the map. Candidates of
   different intents are never duplicates of each other.
4. **Split.** A kept paraphrase inherits `intent_axis` and `template_axis` from its source;
   its `persona_axis` is `"validation"` iff
   `int(sha1(f"{salt}:{persona}".encode("utf-8")).hexdigest()[:8], 16) / 2**32 < share`
   (the eval-intents axis rule, keyed by the persona id), else `"dev"`. Its `split` is
   `"validation"` if any of the three axes is, else `"dev"`. So no validation intent, template
   or persona appears in a dev paraphrase.
5. The set:

   ```
   {"schema": "paraphrase-set/1", "prompt_version": str, "sealed": bool,
    "split": {"share": float, "salt": str},
    "sources": [{"file": basename, "sha256": hex of the file bytes, "model", "date",
                 "prompt_version"}, ...],                      # sorted by sha256
    "paraphrases": [{"paraphrase_id", "case_id", "intent_id", "persona", "paraphrase",
                     "source_sha256", "intent_axis", "template_axis", "persona_axis",
                     "split"}, ...],
    "review": [{"paraphrase_id", "case_id", "persona", "paraphrase", "reasons",
                "source_sha256"}, ...],
    "duplicates": [{"paraphrase_id", "case_id", "persona", "duplicate_of", "kind",
                    "source_sha256"}, ...],
    "content_hash": str}
   ```

   `paraphrases`, `review` and `duplicates` are sorted by `paraphrase_id`; `source_sha256` is
   the hash of the file the record came from.
6. `canonical_bytes(obj)` is `(json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) +
   "\n").encode("utf-8")`; `content_hash` is the lowercase SHA-256 hex of the canonical bytes
   of the set without its `content_hash` key.

`write_paraphrase_set(pset, path)` raises `ValueError` if `pset["content_hash"]` is missing or
wrong, else writes `canonical_bytes(pset)` and returns the hash. `load_paraphrase_set(path)`
reads the file and returns the set, raising `ValueError` if the hash is missing or does not
match the content.

### No text in output (sealed mode)

Held-out batches (H-F questions) must not be echoed by the tooling, mirroring
[set-b-tooling](set-b-tooling.md). Two rules:

- **Errors never carry text.** No `ValueError` raised by anything in this change contains a
  question or paraphrase (or any body-line content); messages name line numbers, file
  basenames, case_ids and counts.
- **The report.** `render_import_report(pset)` returns these lines, each ending `\n`:

  ```
  paraphrases: <len(paraphrases)>
  review: <len(review)>
  duplicates: <len(duplicates)>
  split: dev=<n> validation=<n>
  content_hash: <hash>
  ```

  (`split:` counts the kept `paraphrases` by their `split`), then
  `review <case_id> <persona> <reasons joined by ",">` per review entry (set order),
  each followed, **only when `pset["sealed"]` is false**, by `  text: <paraphrase>`; then
  `duplicate <case_id> <persona> of <duplicate_of>` per duplicate. A sealed set's report
  contains no paraphrase text. The batch files and the frozen set necessarily hold the text;
  they are data, not tool output.

## Acceptance criteria

All inputs are synthetic and written by hand; the two committed reply files
`test/spec/eval-paraphrases/replies/reply_a.txt` and `reply_b.txt` are the import fixtures,
and the expected outcome of every line is worked out by hand in the tests.

- **C1**: `ParaphraseSource` stores its fields as specified (tuples, years sorted and
  deduplicated, `period_ends` a dict, `{}` for `None`) and raises `ValueError` for each
  violation listed under *Inputs*. Property: for any list of years, `years` is the sorted set.
  Oracle: the input rules.
- **C2**: `normalise` and *contains* follow the four steps exactly (hand-worked table:
  possessives, typographic apostrophes, `FY'24`, NFKC full-width digits, punctuation).
  Properties: idempotent; invariant under ASCII case changes and under punctuation inserted
  between tokens. Oracle: the normalisation rules.
- **C3**: `assign_personas` and `export_batches` produce exactly the specified selection, batch
  cut and text, and raise for each listed error. Properties: same output for any source order;
  every source appears in exactly one batch; `ceil(len / batch_size)` batches. Oracle: the
  rules, computed in the test with `hashlib`, `json` and `math`.
- **C4**: `instructions_path` returns the specified path and rejects a bad version; the
  committed `v1` file starts with `# paraphrase-instructions v1`, names every persona, states
  the meaning rules (the words company, period, metric, operation, scope, fiscal, calendar) and
  the three reply keys, and `export_batches` accepts it with `prompt_version="v1"`. Property:
  any version not fully matching `v[0-9]+` raises. Oracle: the instruction-block requirement.
- **C5**: `parse_reply` returns the header and records of both fixture files (fences and blank
  lines skipped) and raises for each listed format error. Property: any header and records
  written in the format parse back to the same values. Oracle: the reply-file format.
- **C6**: `check_meaning` gives the hand-worked reasons for every fixture record and for
  targeted examples of each reason. Metamorphic: a passing paraphrase still passes under case
  changes, a possessive, or another form of the same company; appending a drift phrase the
  question lacks adds `semantic_drift`; deleting the period makes `period_missing` appear.
  Oracle: the meaning-check rules.
- **C7**: `import_replies` on the two fixture files yields exactly the hand-worked
  `paraphrases`, `review` and `duplicates` ids, the specified entry fields, the provenance
  (`sha256` computed with `hashlib` from the fixture bytes), and raises for each listed error.
  Metamorphic: permuting `reply_paths` or `sources` gives an identical set. Oracle: the import
  rules applied by hand to the fixture lines.
- **C8**: Duplicate removal: the four fixture duplicates (exact repeat of a kept paraphrase,
  near repeat of a source question, exact repeat of a source question, near repeat of a kept
  paraphrase), with the specified `duplicate_of` and `kind`; equal texts of different intents
  are both kept. Property: adding case and punctuation variants of a kept paraphrase under the
  intent's other cases and personas adds only `duplicates` entries, all pointing at the
  smallest `paraphrase_id`. Oracle: the dedup rule.
- **C9**: Each kept paraphrase's axes and split follow the rule (persona axis computed with
  `hashlib` in the test), and no `dev` paraphrase has a validation intent, template or persona.
  Property: for random `share` and `salt`, the same holds and `share=0` gives only the
  sources' own axes. Oracle: the axis rule.
- **C10**: `content_hash` equals the SHA-256 of the canonical bytes of the set without it;
  `write_paraphrase_set` / `load_paraphrase_set` round-trip; a tampered file or a wrong hash
  raises. Metamorphic: re-importing gives the same hash; changing a reply file's bytes without
  changing its records changes `sources` and `content_hash` but not `paraphrases`' texts.
  Oracle: the hashing rule computed with `hashlib` and `json`.
- **C11**: `render_import_report` gives the specified lines; a sealed set's report holds no
  paraphrase text while an unsealed one shows the review texts; errors from `parse_reply`,
  `import_replies`, `export_batches` and `ParaphraseSource` never contain the offending text.
  Property: random distinctive texts never appear in a sealed report or in an error message.
  Oracle: the no-text rules.

## Interface

New module `src/eval/suites/paraphrases.py` (stubbed in the spec PR; comments only at the top,
no module docstring):

- `ParaphraseSource.__init__(self, case_id: str, question: str, *, intent_id: str, intent_axis: str, template_axis: str, company_forms: Sequence[str] = (), years: Sequence[int] = (), period_ends: Mapping[int, str] | None = None, metric_forms: Sequence[Sequence[str]] = (), universe_forms: Sequence[str] = (), forbidden_forms: Sequence[str] = ()) -> None`
- `normalise(text: str) -> str`
- `assign_personas(case_id: str, k: int) -> tuple[str, ...]`
- `instructions_path(version: str) -> Path`
- `export_batches(sources: Sequence[ParaphraseSource], *, instructions: str, prompt_version: str, batch_size: int = 50, personas_per_case: int = 2) -> list[str]`
- `parse_reply(text: str) -> dict`
- `check_meaning(source: ParaphraseSource, paraphrase: str) -> list[str]`
- `import_replies(sources: Sequence[ParaphraseSource], reply_paths: Sequence[str | Path], *, prompt_version: str, share: float, salt: str, sealed: bool = False) -> dict`
- `canonical_bytes(obj: dict) -> bytes`
- `write_paraphrase_set(pset: dict, path: str | Path) -> str`
- `load_paraphrase_set(path: str | Path) -> dict`
- `render_import_report(pset: dict) -> str`

`ParaphraseSource` keeps its explicit `__init__` (the locked signature); the implementer adds
`__eq__` / `__repr__` as useful. A `PERSONAS` constant is the implementer's.

## Notes

- **No generation call.** Nothing here imports an LLM module or the retrieval pipeline.
- **Implementer, not spec-tested:** the instruction file
  `prompts/templates/eval_paraphrase/v1/instructions.txt` (its required content is C4); the
  adapter from an eval-intents `PhrasedCase` and `Lexicon` to `ParaphraseSource` (added once
  eval-intents is implemented; suggested: `company_forms` = ticker, short and legal name;
  `years` = the years the question renders, i.e. the one fiscal year, the first and last of a
  span when the template names them, and the filing year; `period_ends` from the lexicon;
  `metric_forms` = label, synonyms and shorthand per metric; `universe_forms` = the
  universe's phrases; `forbidden_forms` = every other universe's phrases); a CLI
  `scripts/eval/paraphrases.py export|import` that prints only `render_import_report` and
  counts; a short guide in `docs/guides/xbrl-flow/`.
- **Other companies are not drift phrases by default:** short tickers (`MU`, `ON`) collide with
  ordinary words, so naming another filer is left to review by the maintainer.
- **Review is manual.** A `review` entry is kept in the frozen set for audit; to accept a
  reviewed paraphrase the maintainer edits the reply file and re-imports, which changes that
  file's SHA-256 in `sources` and the set's `content_hash`.
- **Persona requests are advisory.** Export asks for `personas_per_case` personas per case;
  import accepts any of the five, because the split rule does not depend on which were asked.
- With five personas, a persona axis at small `share` can put zero or several personas on the
  validation side; the split rule is the maintainer's, applied as written.
- Out of scope: generating paraphrases, approving review entries in the tool, wiring the
  frozen set into a suite file or `eval_*` tables, and the stability metric itself.
