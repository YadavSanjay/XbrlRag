# stage-llm-output-cap — the baseline arms' output budget leaves room for reasoning

`StageLLM` asks for 2,000 completion tokens when a model has no budget of its own. On a reasoning
model the reasoning shares that budget, so a call can use it up before the answer is finished, or
before it starts. The first `html_rag` pilot on `deepseek-flash` lost 3 of 51 calls this way. Those
cases scored as an error or a refusal for a reason that has nothing to do with the
arm, which would be charged to `html_rag` in the baseline.

`frontier_sec_tools` already spends the model's 8,000 for this reason, and `gpt-5-mini`,
`gpt-5.6-terra` and `gpt-5.6-sol` already get 8,000. Only the shared default is out of line, so the
change reaches the DeepSeek and Claude models.

## Requirement

1. **The default is 8,000.** A `StageLLM` call on a model with no budget of its own asks for 8,000
   completion tokens.
2. **The model's ceiling still holds.** A model whose `max_output_tokens` is below 8,000 is asked
   for its ceiling, through the existing `call_params`.
3. **A model's own budget still wins.** A model in `SEC_TABLE_MODEL_CONFIGS` keeps its own value.
4. **Every arm built on `StageLLM` takes the same default**, so that no comparison between arms is
   a comparison of budgets: `closed_book`, `naive_rag_*`, `html_rag` and the long-context arms.
5. **Nothing else changes.** Not the temperature, the prompts, `call_params`, `llm.yaml`, the
   pipeline's own stages, or `frontier_sec_tools`.

**In scope:** requirements 1-5. **Out of scope:** recording the tokens of a billed call that
returned no content, a separate change; what a run does with a reply that is still truncated; any
re-run.

## Acceptance criteria

Bug-fix change: no new function and no stub. Every test fails on the code at the base of this
branch with an assertion. Each test holds a case the base code gets wrong, with the cases it must
keep getting right beside it.

- **C1**: A call on `deepseek-flash`, `deepseek-v4-pro`,
  `claude-sonnet-5-5` or `claude-opus-5-5` reaches the provider adapter with
  `max_completion_tokens` 8,000, from any arm's stage. Property: for any such model, stage and user
  text, the same, and the reply comes back unchanged. Oracle: requirements 1 and 4.
- **C2**: `gpt-5.6-luna` is asked for 4,000, and `gpt-5.6-terra`, `gpt-5.6-sol` and `gpt-5-mini` for
  the 8,000 they get today. Property: for any
  model in the table, the budget is the model's own value when it has one, and otherwise the
  smaller of 8,000 and its ceiling. Oracle: requirements 2 and 3, and `configs/llm/llm.yaml`.

## Interface

No change: `src.eval.systems._stage_llm.StageLLM(stage, gateway, *, model).complete(user_text) ->
str`.

## Notes

- Evidence, 2026-11-01, `html_rag` on `deepseek-flash`, 51 cases, in-loop slice: mean 441 completion
  tokens of which 255 reasoning, and three calls at the cap, all MU FY2024. One spent 1,520 on
  reasoning and came back `unsupported`. Two returned empty content, so the case was an `ERROR`.
- 8,000 is a ceiling, not a target: the mean call above is unchanged, and a run's cost moves only
  on calls that used to be cut off. The hard cap of a run still applies.
- `deepseek-v4-pro` allows 16,000 in `llm.yaml`. It is asked for 8,000, the same as the other arms.
- The gateway and `call_params` are real here. The provider adapter is the fake: it is the network
  boundary.
- [html-rag-arm](html-rag-arm.md) lists the 2,000 as a known risk to check in the pilot. The pilot
  did, and this is that check's result.
