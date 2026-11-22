# Labelling rubric: judge calibration (E3)

You label every answer by hand. The text judge must then agree with your labels (kappa ≥ 0.7 per
dimension, [judge-calibration](../../specs/judge-calibration.md)). The rules below are the same
rules the judge's prompt states (`prompts/templates/eval/_base/eval_judge/v2/system.txt`). Apply
them the same way from the first answer to the last: a rule that drifts partway through lowers
kappa for reasons unrelated to the judge.

## What you label

The label sheet (`labels.csv`) has one row per label. Fill the `label` column with `y` or
`n`; a blank cell is an error. The context file (`context.md`) shows each answer with its
question, reference answer, key points, claims and cited passages.

| Dimension | Item | `y` when |
|---|---|---|
| `correct` | a claim | the claim agrees with the reference answer: it states nothing the reference contradicts, and every date and figure matches under the precision rules below |
| `supported` | a claim with at least one cited passage | the claim's **own** cited passages state or directly imply what it says. Passages cited by other claims, the reference answer and your own knowledge don't count |
| `covered` | a key point (`kp0`, `kp1`, …) | the answer's claims, taken together, convey **every part** of the key point |

## Every part counts (`covered`)

A key point is covered only when the answer conveys each part of it: each fact, figure, date,
named item in a list, and stated cause or reason. If any part is missing or stated wrongly, label
`n`. Wording may differ; the meaning must be the same.

- "Client revenue fell on lower processor sales and Gaming fell on lower semi-custom sales":
  an answer that only says Client fell is `n`.
- "ARPU fell 1% because of plan mix and growth in lower-revenue regions": the decline without
  the reasons is `n`.
- "Mounjaro, Zepbound and Verzenio were the main drivers": naming two of the three is `n`.

## Precision (both `correct` and `covered`)

- **Dates:** agree to the month and year. The day need not be given ("November 2023" covers
  "November 16, 2023"). A year alone does not cover a date given to the month or day. When the
  reference gives only a quarter or a year, that quarter or year must match. Fiscal labels must
  match ("fiscal 2025" is not "2024").
- **Figures:** equal after both are rounded to the coarser of the two precisions, and the answer
  keeps at least three significant figures (or all of the reference's, if it has fewer).
  - Reference $17.73 billion: "$17.7 billion" `y`, "$18 billion" `n`.
  - Reference $3.7 billion: "$3.7 billion" `y`, "about $4 billion" `n`.
  - Reference 11.8%: "11.8%" `y`, "12%" `n`.
  - Unit and scale must match: billion is not million, a percent is not percentage points.
  - "About" or "approximately" does not change the match.
- **More detail** than the reference is fine when it does not contradict it.

## When you're unsure

Decide by the rule as written, not by how useful the answer feels. If a case doesn't fit the
rules, note the label key and move on; we settle such cases together after the sheet is done,
before calibration, and apply one decision to every similar row.
