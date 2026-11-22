# arm-model-from-profile — the arms' default model comes from the LLM profile; openai-6.1 is the default profile

Two hardcoded choices go stale on 2026-12-11, when OpenAI shuts the gpt-5 family down
([measurement plan](../eval/heldout_v2/measurement_plan.md) §1 decision 1, §5 "The gpt-5 family
shuts down 2026-12-11", §12 item 1):

- the long-context arms (`long_context_oracle`, `long_context_resolved`, `long_context_multi`)
  and `frontier_sec_tools` default to the literal `gpt-5.6-sol`, whatever `--llm-profile` a run
  names. A run on `--llm-profile openai-6.1` that forgets `--model` tests gpt-5.6-sol, not the
  plan's `gpt-6.1-sol`; on `anthropic` it tests an OpenAI model. The repo rule is that no stage
  hardcodes a model name; the arms are the exception this change removes;
- with no profile chosen anywhere, the active profile is `openai`, the gpt-5 family. Every
  un-flagged run, the API and the MCP server fall through to it, and fail after that date.

The wrong result would not be loud: a long-context or frontier number measured on the wrong
model looks like any other number, and the run config would record the wrong model faithfully.

## Requirement

1. **The arms' default model is the active profile's frontier model.**
   `LongContextSystem` (`long_context_oracle`, also built as `long_context`),
   `LongContextResolvedSystem`, `LongContextMultiSystem` and `FrontierSecToolsSystem` take
   `model: str | None = None`. When it is None, the arm's model is `frontier_model()`
   (requirement 2), read **when the arm is constructed**: the profile a run selects with
   `--llm-profile` (which sets `XBRLRAG_LLM_PROFILE` for the run) or the environment, else the
   fallback of requirement 3. `build_system(name)` without a model does the same.
   - An explicit model wins, as today, and is used as given. `""` is an explicit model, not a
     missing one: it has no price, so it raises `ValueError` like any unpriced model.
   - The model is fixed at construction: changing the profile afterwards does not change an
     arm already built. The arm's `model` attribute, the model of its answer calls,
     `FrontierSecToolsSystem.run_config()["model"]` and `run_suite.py`'s
     `report.config["model"]` are that model. A resumed run's identity already includes it.
   - A profile's frontier model with no price raises `ValueError` at construction, as an
     unpriced explicit model does (every committed profile's models are priced).

   With the committed `configs/llm/llm.yaml`, the default is:

   | Active profile | Default arm model |
   |---|---|
   | `openai-6.1` (the fallback, requirement 3) | `gpt-6.1-sol` |
   | `openai-5.6` | `gpt-5.6-sol` |
   | `anthropic` | `claude-opus-5-5` |
   | `deepseek` | `deepseek-v4-pro` |
   | `openai` | `gpt-5` |

2. **`src.llm.stage_models.frontier_model(settings=None) -> str`** returns
   `settings.model_for_tier("frontier")`; with `settings` None, the committed settings with
   the profile active now (`load_committed_settings()`, which reads `os.environ`). An unknown
   profile raises `LlmConfigError`, as `resolve_stage` does.

3. **The fallback profile is `openai-6.1`.** In `llm-provider-profiles` requirement 3's
   precedence (explicit profile > non-empty `XBRLRAG_LLM_PROFILE` > legacy switch on →
   `openai-5.6` > fallback), the fallback changes from `openai` to `openai-6.1`. Nothing else
   in the precedence changes.
   - The `openai` profile **is kept unchanged** (`gpt-5-nano` / `gpt-5-mini` / `gpt-5`), and
     still works when named explicitly until the shutdown. It is not repointed: earlier runs
     are recorded with `llm_profile: "openai"` meaning the gpt-5 family, and a resumed run's
     identity carries the profile name, so the name keeps its meaning. Nothing falls back
     to it.
   - A document (`load_llm_settings`) with no `openai-6.1` profile and no profile chosen
     raises `LlmConfigError` (unknown profile), never a silent other choice.
   - The legacy "current" model set follows the fallback: `openai_model_set(False)`,
     `xbrl_stage_models(False)` and, with the legacy switch off, `MODEL_TIERS["openai"]` (the
     ladder an explicit OpenAI model climbs under a non-OpenAI profile) are the `openai-6.1`
     models. `openai_model_set(True)` stays `openai-5.6`.
   - In an empty environment every XBRL stage (`XBRL_STAGE_TIERS`; the API, the MCP server
     and un-flagged runs) runs on an `openai-6.1` model: `XBRL_QUERY_MODEL_TIERS` is
     `gpt-6.1-terra` for the medium stages and `gpt-6-luna` for `faithfulness_verifier`; no
     stage is a gpt-5-family model (`gpt-5`, `gpt-5-mini`, `gpt-5-nano`).
   - `configs/llm/pricing.yaml` keeps every row, the gpt-5 family's included: earlier runs
     are priced from it.

4. **`scripts/eval/run_suite.py --model` help** names no model ID: it says the default is the
   active profile's frontier model (`--llm-profile`). Its behaviour is otherwise unchanged
   (accepted only for the long-context arms and `frontier_sec_tools`; must be priced).

## Acceptance criteria

- **C1**: Built with no model, each of the four arm classes (and `build_system` for
  `long_context_oracle`, `long_context_resolved`, `long_context_multi`, `frontier_sec_tools`,
  `long_context`) has, under `XBRLRAG_LLM_PROFILE` = P, exactly requirement 1's table entry
  for P. The oracle arm's answer call is on that model. An explicit model wins under any
  profile. An arm built under one profile keeps its model after the profile changes, and a
  second arm built under another profile has that profile's model. `""` raises `ValueError`.

  Property: for any committed profile P with a frontier model other than `gpt-5.6-sol` and
  any arm, the default model is the table's entry for P. Metamorphic: building the same arm
  under two profiles gives the two table entries.

  Oracle: requirement 1's table, written from `configs/llm/llm.yaml`'s profile rows as
  `openai-6-1-profile` (requirement 2), `deepseek-provider` (requirement 1) and
  `llm-provider-profiles` (requirement 2) state them.
- **C2**: `frontier_model()` is the table's entry for the profile in `XBRLRAG_LLM_PROFILE`;
  `frontier_model(load_committed_settings(profile=P, environ={}))` is the entry for P
  whatever the environment says; an unknown profile in the environment raises
  `LlmConfigError`.

  Property: for every committed profile P, `frontier_model` of P's settings is the table's
  entry. Oracle: requirement 1's table.
- **C3**: With an empty environment, `load_committed_settings(environ={})` is `openai-6.1`
  with tier models `{small: gpt-6-luna, medium: gpt-6.1-terra, frontier: gpt-6.1-sol}`; the
  `openai` profile, named, still has `{gpt-5-nano, gpt-5-mini, gpt-5}`; the legacy switch on
  still gives `openai-5.6`; `openai_model_set(False)` is the `openai-6.1` set; in a fresh
  process with neither `XBRLRAG_LLM_PROFILE` nor the legacy switch set,
  `XBRL_QUERY_MODEL_TIERS` is the `openai-6.1` stage models and `MODEL_TIERS["openai"]` the
  `openai-6.1` set; a hand-built document without an `openai-6.1` profile and nothing chosen
  raises `LlmConfigError`, and with one, is that profile.

  Property: for every XBRL stage, the model the empty environment gives it is the
  `openai-6.1` model of its tier, and never a gpt-5-family model.

  Oracle: requirement 3, with `openai-6-1-profile` requirement 2 for the `openai-6.1` row and
  `llm-provider-profiles` requirement 2 for the `openai` row and `XBRL_STAGE_TIERS`.
- **C4**: `run_suite.py --help`'s `--model` entry does not contain `gpt-5.6-sol` (nor any
  other committed model ID) and says the default is the profile's frontier model;
  `FrontierSecToolsSystem.run_config()["model"]` is requirement 1's entry for the profile
  active when the arm was built.

  Property: for any committed profile whose frontier is not `gpt-5.6-sol`, the frontier arm's
  `run_config()["model"]` is the table's entry. Oracle: requirements 1 and 4.

## Interface

- `src.llm.stage_models.frontier_model(settings: LlmSettings | None = None) -> str` (new).
- `LongContextSystem.__init__`, `LongContextResolvedSystem.__init__`,
  `LongContextMultiSystem.__init__`, `FrontierSecToolsSystem.__init__`: `model: str | None =
  None` (was `model: str = "gpt-5.6-sol"`); every other parameter unchanged.
- `src.llm.profiles.DEFAULT_PROFILE == "openai-6.1"`.

## Notes

- **Why not repoint `openai`** to the gpt-6.1 models (the alternative the plan allows): run
  configs, the `eval` schema and checkpoint identities record the profile name. Repointing
  would make `openai` mean two model sets in the stored history, and a run checkpointed on
  gpt-5 could resume on gpt-6.1 under the same identity. Changing the fallback keeps every
  recorded name truthful. After 2026-12-11 a run that names `openai` explicitly fails at the
  provider, loudly.
- The eval decision runs name `--llm-profile` explicitly, so the fallback mostly affects
  un-flagged runs, the API and the MCP server. A deployment that sets
  `XBRLRAG_LLM_PROFILE=openai` keeps the gpt-5 family until it changes that setting.
- **Retired tests updated in this spec PR**, each to hold both before and after the change
  (graduated tests must pass on every PR), with a comment naming this change:
  `long-context-arm` C6 (the literal default), `long-context-arms` C1 and its conftest (the
  default, and the cost fixtures name `gpt-5.6-sol` explicitly), `frontier-sec-tools` C17 and
  its conftest (same), `llm-provider-profiles` C1, C3 (and its hand-built document gains an
  `openai-6.1` row) and C9, `model-set-switch` C1, C2 and C4, `deepseek-provider` C1,
  `openai-6-1-profile` C2, and `baseline-arms` C5 (its doubling property stays below the
  272K-token price tier that the `openai-6.1` medium model has).
- The window limits of the long-context arms (250,000 estimated tokens) are unchanged; they
  sit below `gpt-6.1-sol`'s 272K price tier as they did below `gpt-5.6-sol`'s.
- No data snapshots: every oracle is a committed profile row, written into the tests by hand.
