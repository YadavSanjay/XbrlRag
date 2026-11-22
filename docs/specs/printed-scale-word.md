# printed-scale-word — a scale word printed with a number needs no `scale`

`printed_value(printed, scale)` in `src/retriever/html10k/answerer.py` is the one place the
`html_rag` arm applies scale and sign. [html-rag-arm](html-rag-arm.md) requirement 19 says a scale
word printed with the number (`$1.2 billion`) applies and `scale` is then ignored. It also says
`scale` must be 1, 1000, 1000000 or 1000000000, otherwise `None`. The code took the second rule
first, so `("$1.2 billion", None)` is `None`, and an unambiguous number is lost.

`html_rag` is a competitor arm. It should not lose a number the filing prints in full because the
model left out a field that the number doesn't need.

## Requirement

1. **A printed scale word applies whatever `scale` holds.** When `printed` is one number with a
   scale word (`thousand`, `million`, `billion`, `trillion`, singular or plural, any case),
   `printed_value` returns the signed amount times that word's multiplier, for any `scale`: a valid
   one, `None`, a bool, a string, or a number outside the four allowed.
2. **Without a scale word, `scale` is still checked.** A number printed with no scale word and a
   `scale` that isn't one of the four allowed gives `None`, as before. A bool is still not a number.
3. **What isn't a number stays `None`.** A nil or a text with no number gives `None` for any
   `scale`, never 0. A scale word alone (`billion`) is not a number.
4. **Nothing else changes.** No signature changes, and no prompt: the prompt still tells the model
   to send 1 for such a number. This supersedes requirement 19's order of checks only.

**In scope:** requirements 1-4. **Out of scope:** the rest of `printed_value`, `match_cell`, and
any re-run. The change touches no chunk text, so it needs no reload.

## Acceptance criteria

Bug-fix change: no new function and no stub. Every test fails on the code at the base of this
branch with an assertion. Each test holds a case the base code gets wrong, with the cases it must
keep getting right beside it.

- **C1**: `printed_value` of a number with a scale word equals the signed amount times the word's
  multiplier, within 1e-12 relative, for `scale` of `None`, `True`, `"1000"`, 7, 0 and each allowed
  value. Property: for any amount, sign, word and bad scale. Oracle: requirement 1, hand products.
- **C2**: A bare number with a bad scale is `None`; a nil, a non-number and a word alone are `None`
  for any `scale`; a bare number with a valid scale is unchanged. Property: for any bare integer and
  bad scale. Oracle: requirements 2-3 and the RAG rule that nil never becomes 0.

## Interface

No change: `src.retriever.html10k.answerer.printed_value(printed: str, scale: object) -> float | None`.

## Notes

- Raised in the review of `impl/html-rag-arm` and decided by the maintainer as option B: the printed word
  applies. The other reading, keeping `None`, was kept out as needlessly hard on a baseline arm.
- The locked html-rag-arm tests hold no case with a scale word and a bad scale, so they stay green
  and need no edit.
- All oracle values are hand products of synthetic numbers.
