# paraphrase-suite-wiring — frozen paraphrases become eval cases

The frozen v2 paraphrase sets ([eval-paraphrases](eval-paraphrases.md),
[paraphrase-shorthand](paraphrase-shorthand.md)) exist, but nothing reads them: no paraphrased
question reaches a run, and the pre-registration's bar line "paraphrase stability ≥ 90%, per
intent, across all of an intent's phrasings"
([preregistration](../eval/heldout_v2/preregistration.md) §3) cannot be measured. This change
turns a split's template cases plus the kept paraphrases of that split into one derived,
hashed suite file that `run_suite.py --suite-path` runs unchanged.

## Design decisions

1. **A paraphrase is its own `EvalCase`, in a separate derived suite file**, not a run-time
   option of `run_suite.py`. The file is diffable and hashed, records the hashes of every input
   it was built from, and is what a pre-registered run names; a run-time option would make the
   questions a property of the command line, put the wiring into `run_suite.py` (another
   change's area) and leave nothing to diff. The frozen template suites are not rewritten, so
   their hashes (`1736014c…`, `333cbd28…`) stay valid.
2. **The derived suite holds the split's template cases and its paraphrase cases.** Stability is
   per intent across *all* phrasings, so both must be in one run. A paraphrase-only run is
   `--tag family:paraphrase`; a template-only run uses the frozen suite.
3. **The case id is the set's `paraphrase_id`** (`f"{case_id}:{persona}"`): unique within a
   set, already in the frozen file, and it names its source case.
4. **A paraphrase case is a deep copy of its source case record** with only `id`, `question`,
   `suite`, `tags` and a new `paraphrase` provenance object changed. Key, expected value or
   rows, alternates, kind, `paraphrase_group`, disclosures and subject are the source's,
   byte for byte. So a paraphrase is never graded against another case's key.
5. **The record's `split` decides the suite**, never the source case's. The dev set holds
   paraphrases of dev cases whose persona axis is `validation` (225 in the current set); they go
   to the validation suite, with the dev case's key. A paraphrase of a validation case can
   never be `dev`. Only `paraphrases` are read; `review` and `duplicates` never.
6. **Stability needs no new metric.** A paraphrase case keeps its source's `intent:` tag and
   `paraphrase_group`, so the existing `intent_stability` (the run's `paraphrase_stability`
   column, `run_stability`) groups template and paraphrase phrasings of one intent together.
   The `family:` tag becomes `paraphrase`, so accuracy by phrasing family separates them.
7. **The dev sample stays template-only.** `dev_sample.py` draws one phrasing per intent for
   cheap development runs; stability needs every phrasing of an intent, so a sample is the wrong
   place to measure it. It keeps drawing from the frozen template suite.

## Requirement

New module `src/eval/suites/paraphrase_suite.py`.

### Inputs

1. **Suites**: v2 suite documents as `write_v2_suites` writes them (`name`, `version`, `split`,
   `n_cases`, `content_hash`, `cases`; [v2-suite-build](v2-suite-build.md) rule 15).
2. **Paraphrase sets**: documents as `load_paraphrase_set` returns them (eval-paraphrases
   *Import*, rule 5).

### Building

`build_paraphrase_suite(split, suites, paraphrase_sets)` returns a suite document. It raises
`ValueError` when:

- `split` is not `"dev"` or `"validation"`;
- `suites` or `paraphrase_sets` is empty;
- a suite's `split` is not `"dev"` or `"validation"`, two suites share a `name`, a suite's
  `content_hash` is not `v2_suite.content_hash(suite["cases"])`, a case's `split:` tag is not
  its suite's `split`, or a case id appears twice across the suites;
- no suite has split `split`;
- a set's `schema` is not `"paraphrase-set/1"`, its `content_hash` is missing or is not the
  lowercase SHA-256 hex of `paraphrases.canonical_bytes` of the set without `content_hash`, two
  sets share a `content_hash`, or a `paraphrase_id` appears in two sets;
- for a **selected** record (rule below): its `paraphrase_id` is not
  `f"{case_id}:{persona}"`; its `persona` is not one of the five personas; one of its axes is
  not `"dev"` or `"validation"`; its `split` is not `"validation"` when any axis is and `"dev"`
  otherwise; its `case_id` is in none of the suites; its `intent_id` differs from the source
  case's `intent:` tag value or from its `paraphrase_group`; or its `split` is `"dev"` and the
  source case's suite split is `"validation"`.

No error message contains a question or a paraphrase text (sealed sets, eval-paraphrases
*No text in output*); messages name ids, hashes and counts.

**Selection.** A record is selected when it is in a set's `paraphrases` list and its `split`
equals `split`. Entries of `review` and `duplicates` are never read. Records of the other split
are not checked.

**Cases.** With `name = f"xbrl_v2_{split}_paraphrased"`:

- every case of every suite whose `split` equals `split` becomes a **template case**: a deep copy
  of its record with `suite` set to `name`, nothing else changed;
- every selected record `p` of set `s`, with source case record `c` (found in any suite), becomes
  a **paraphrase case**: a deep copy of `c` with
  - `id = p["paraphrase_id"]`, `question = p["paraphrase"]`, `suite = name`;
  - `tags`: `c`'s tags in order, the `split:` tag's value replaced by `p["split"]` and the
    `family:` tag's value by `paraphrase` (a tag absent from `c` is not added), followed by
    `persona:<persona>`, `paraphrase_of:<case_id>`, `paraphrase_set:<s["content_hash"]>`;
  - `paraphrase = {"set": s["content_hash"], "paraphrase_id": ..., "case_id": ...,
    "persona": ..., "source_sha256": p["source_sha256"]}`;
  - every other key of `c` unchanged.

**Document.**

```
{"name": name, "version": "1", "split": split, "n_cases": n,
 "content_hash": v2_suite.content_hash(cases),
 "inputs": {"suites": [{"name", "split", "content_hash"} of every given suite, sorted by name],
            "paraphrase_sets": [{"content_hash", "prompt_version", "n_selected"} of every
                                given set, sorted by content_hash]},
 "counts": {"template": n_template, "paraphrase": n_paraphrase},
 "cases": [records sorted by id]}
```

The result does not depend on the order of `suites` or of `paraphrase_sets`. (The order of
cases within a suite and of records within a set is fixed by their own content hashes.)

### Writing

`write_paraphrase_suite(doc, path)` raises `ValueError` when `doc["content_hash"]` is not
`v2_suite.content_hash(doc["cases"])` or `doc["n_cases"] != len(doc["cases"])`; otherwise it
writes `json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"`
as UTF-8 with `\n` line ends and returns `Path(path)`. `EvalSuite.load` reads the file (the
`paraphrase` key is ignored), and `run_split` over its cases is `split`.

## Acceptance criteria

Fixtures are synthetic Python data in `test/spec/paraphrase-suite-wiring/conftest.py`: a dev
suite (intents `int-aaa` with two template phrasings, `int-bbb` a compute, `int-ccc` a screen),
a validation suite (`int-vvv`, a validation intent, and `int-aaa-0003`, a validation-template
phrasing of the dev intent `int-aaa`), and two paraphrase sets with hand-chosen axes, `review`
and `duplicates` entries. Hashes are computed in the conftest with `hashlib` and `json` from the
rules above. The grader, `EvalSuite`, `run_split` and `intent_stability` are real; the only fake
is the system that answers.

- **C1**: The dev and validation builds contain exactly the hand-listed case ids, and the
  hand-listed `counts`, `n_cases`, `name` and `inputs`. A persona-validation paraphrase of a dev
  case is in the validation build and not the dev build; `review` and `duplicates` entries are
  in neither. Property: for any assignment of the records' persona axes, every dev-build
  paraphrase has three `dev` axes and every selected record appears exactly once in the build of
  its split. Oracle: the selection rule applied by hand.
- **C2**: Each paraphrase case equals its source case record except `id`, `question`, `suite`,
  `tags` and `paraphrase`; its tags and provenance are exactly the hand-written ones. Template
  cases equal their records except `suite`. Graded with the real grader, a paraphrase passes on
  its source case's value and fails on another case's. Metamorphic: changing a source case's
  expected value changes exactly the paraphrase cases of that source. Oracle: the case rules.
- **C3**: `build_paraphrase_suite` raises `ValueError` for each listed error, and the message
  never contains the paraphrase or question text; records of the other split are not checked.
  Property: a random distinctive paraphrase text in a failing record never appears in the
  message. Oracle: the error list.
- **C4**: `content_hash` is the hash of the case list computed in the test; the result does not
  depend on input order (property: any permutation of the suites and of the sets gives an equal
  document); `write_paraphrase_suite` writes the specified bytes, `EvalSuite.load` reads the
  cases back with the same ids, questions and expected values, `run_split` is the split, and a
  wrong hash or count raises. Oracle: the hashing and file rules.
- **C5**: Over a run of the dev build answered by a fake system, `intent_stability` groups
  template and paraphrase phrasings of one intent: all right gives 1.0; one wrong paraphrase of
  `int-aaa` gives 2/3; one wrong template case of `int-bbb` and its paraphrase also wrong gives
  1.0 (both phrasings agree). Metamorphic: flipping one paraphrase of a stable intent lowers the
  stable count by exactly one. Oracle: the stability definition (plan §3.2) by hand.

## Interface

New module `src/eval/suites/paraphrase_suite.py` (stubbed in the spec PR):

- `build_paraphrase_suite(split: str, suites: Sequence[Mapping[str, Any]], paraphrase_sets: Sequence[Mapping[str, Any]]) -> dict[str, Any]`
- `write_paraphrase_suite(doc: Mapping[str, Any], path: str | Path) -> Path`

Not in this change (implementer adds it with unit tests): the CLI
`scripts/eval/build_paraphrase_suite.py --split <s> --suite <path>... --paraphrases <path>...
--out <path>`, which loads the sets with `load_paraphrase_set`, prints counts and hashes only
(never a paraphrase text) and writes `data/eval/v2_frozen/xbrl_v2_<split>_paraphrased.json`.
Also out: paraphrases of H-F questions (the sets hold dev and validation only), any change to
`run_suite.py`, `dev_sample.py`, the stability metric or the DDL.

## Notes

- Two hash formats meet here on purpose: a set's `content_hash` is bare hex over
  `canonical_bytes` (eval-paraphrases), a suite's is `sha256:` + hex over `canonical_json`
  (v2-suite-build). Each is checked by its own rule.
- `run_split` sees a single split in each derived suite, so the existing mixed-split guard needs
  no change; a run built from the validation file cannot carry a dev case.
- The current sets: dev `eeb361ad…` (1,151 kept: 926 dev, 225 validation), validation `11aaca99…`
  (476 kept, all validation). Expected sizes, for the implementer's check only: dev 635 + 926,
  validation 265 + 225 + 476.
- Cost: $0; no network, no database, no LLM.
