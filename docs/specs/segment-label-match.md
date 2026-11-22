# segment-label-match — a one-dimension member hint matches its label as the analyst wrote it

A one-dimension lookup (flat path, `mv_financial_facts.segment_label`) matches the planner's
member hint against the member label in two ways:
- a SQL filter, `lower(segment_label) LIKE '%hint%'`;
- the exact-match confidence credit, `_norm_label(label) == hint`.

Both compare the raw lowercased text, so a label differing from the hint only in spacing, a
generic suffix or which member is meant loses the answer. Three cases never pass in four
runs (2026-10-16):

- **AMD FY2024 "Datacenter".** The hint is `data center` and the label is
  `Datacenter [Member]`. The LIKE filter finds nothing, so the result is NOT_FOUND.
- **Apple FY2024 "Europe Segment".** The hint is `europe` and the label is
  `Europe Segment [Member]`. The filter finds the right fact (101,328M), but it misses the
  exact-match credit: 0.6 < 0.70, AMBIGUOUS.
- **Oracle FY2025 "License Support".** `license support` is contained in four labels:
  - `License Support` (19,523M), the member asked for;
  - `Cloud Services And License Support` (44,029M);
  - `Applications Cloud Services And License Support`;
  - `Infrastructure Cloud Services And License Support`.

  They are four distinct values, so the result is AMBIGUOUS (refused, but the right fact
  was there).

The multi-dimension path already ignores spaces (`_hint_in_label`). This change gives the
one-dimension path the same tolerance, plus a preference for the member the hint names
exactly.

## Requirement

1. **Label key.** `member_label_key(text)` is the comparison form of a label or hint:
   1. lowercase;
   2. remove the literal `[member]`;
   3. replace `&` with ` and `;
   4. split into words (runs of `[a-z0-9]`);
   5. drop **one** trailing word `segment`, `segments` or `member`, unless it is the only
      word;
   6. join the remaining words with no separator.

   Examples: `Europe Segment [Member]` → `europe`, `data center` → `datacenter`,
   `Search & other` → `searchandother`, `Segment` → `segment`.
2. **Containment.** A one-dimension flat lookup keeps a fact when the hint's key is a
   substring of the label's **compact form**: steps 1–4 above, joined, *without* step 5.
   `label_contains_hint(label, hint)` states this rule; the SQL filter must be equivalent
   to it. Not locked: the SQL is checked live.
3. **Exact preference.** If any fetched row's `member_label_key(segment_label)` equals
   the hint's key, the lookup keeps only those rows. A note names the members set aside.
4. **Exact credit.** On the flat path, the dimension credit (+0.2) is earned when
   `member_label_key(segment_label) == member_label_key(hint)`.
5. The multi-dimension path, axis handling and every other credit are unchanged.

## Acceptance criteria

- **C1**: `member_label_key` follows Requirement 1.

  Point cases:
  - `Europe Segment [Member]` → `europe`;
  - `Datacenter [Member]` and `data center` → `datacenter`;
  - `License Support [Member]` → `licensesupport`;
  - `Cloud Services And License Support [Member]` → `cloudservicesandlicensesupport`;
  - `Search & other` → `searchandother`;
  - `Operating Segments [Member]` → `operating`;
  - `Segment` → `segment`;
  - `` → ``.

  Invariants: the result does not change under upper case, extra whitespace, punctuation,
  or an appended ` [Member]`.

  Oracle: the definition.
- **C2**: `label_contains_hint(label, hint)` follows Requirement 2.

  Point cases:
  - `Datacenter [Member]` contains `data center`;
  - `Europe Segment [Member]` contains `europe` and `europe segment`;
  - `Cloud Services And License Support [Member]` contains `license support`;
  - `Europe Segment [Member]` does not contain `japan`.

  Property: the label always contains its own key as a hint.

  Oracle: the definition.
- **C3**: `_build_result` with a one-dimension spec, on corpus rows (2026-10-16):
  - Oracle `{"product": "license support"}`, all four rows → OK, 19,523M, the
    `License Support` fact, confidence 0.8.
  - Apple `{"segment": "europe"}` → OK, 101,328M, confidence 0.8.
  - AMD `{"segment": "data center"}`, label `Datacenter [Member]` → OK, 12,579M,
    confidence 0.8.
  - Metamorphic: adding rows of other members whose labels merely contain the hint never
    changes the result when an exact member exists.
  - With no exact member, the four-way Oracle ambiguity stays AMBIGUOUS (hint
    `cloud services`, which matches three labels).

  Oracle: `_score_confidence`'s fixed credits by hand (0.4 one value + 0.2 rank 1 + 0.2
  exact dimension).

## Interface

- `src.retriever.xbrl.xbrl_factstore_lookup.member_label_key(text: str) -> str`
- `src.retriever.xbrl.xbrl_factstore_lookup.label_contains_hint(label: str, hint: str) -> bool`

## Notes

- The member labels and values are from the local corpus (public SEC filings), checked
  2026-10-16. The suite's expected facts are 19541 (AMD), 14215 (Apple) and 73633 (Oracle).
- **Validation after the merge:** live lookups for the three cases, then a slice of the
  three plus ~60 stable cases. Dimensional and breakdown cases also go through the flat
  path, so the slice must include some.
