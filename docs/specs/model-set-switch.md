# model-set-switch — a config feature switch between the current and the replacement OpenAI models

## Requirement

OpenAI shuts down the `gpt-5`, `gpt-5-mini` and `gpt-5-nano` snapshots on 2026-12-11
(deprecation announced 2026-06-11) and names `gpt-5.6-sol`, `gpt-5.6-terra` and
`gpt-5.6-luna` as their replacements. Every XBRL query stage runs on the current models.

The replacement models must be declared in `src/llm/config.py` as a second **model set**,
selected by a **feature switch declared in the same config file and off by default**. With
the switch off, every stage and the escalation ladder keep exactly today's models. Turning it
on moves every stage to the replacement model of the *same tier*, and moves the OpenAI
`MODEL_TIERS` ladder with it, so escalation keeps climbing within one model generation.

The switch can be overridden for a run by the environment variable
`XBRLRAG_USE_REPLACEMENT_MODELS`, so an eval slice can test the replacement set without a
code edit. A value the switch does not understand raises instead of silently choosing a set.

`XBRL_QUERY_MODEL_TIERS` and `MODEL_TIERS["openai"]` stay importable module-level dicts,
computed from the switch at import time, so no existing consumer changes.

## Acceptance criteria

- **C1**: `xbrl_stage_models(False)` returns exactly today's stage models:
  `query_decomposer`, `intent_router`, `plan_generator` and `synthesizer` → `gpt-5-mini`;
  `faithfulness_verifier` → `gpt-5-nano`. `openai_model_set(False)` returns
  `{"small": "gpt-5-nano", "medium": "gpt-5-mini", "frontier": "gpt-5"}`.
  Oracle: `XBRL_QUERY_MODEL_TIERS` and `MODEL_TIERS["openai"]` on `main` at the time of
  writing (copied into the test).
- **C2**: `openai_model_set(True)` returns
  `{"small": "gpt-5.6-luna", "medium": "gpt-5.6-terra", "frontier": "gpt-5.6-sol"}`, and for
  every stage, `xbrl_stage_models(True)[stage]` is the replacement of the same tier as
  `xbrl_stage_models(False)[stage]` (the four medium stages → `gpt-5.6-terra`, the verifier →
  `gpt-5.6-luna`). Oracle: OpenAI's deprecation page (gpt-5 → gpt-5.6-sol, gpt-5-mini →
  gpt-5.6-terra, gpt-5-nano → gpt-5.6-luna).
- **C3**: `replacement_models_enabled(env)` returns the config default, which is **off**
  (`False`), when `env` has no `XBRLRAG_USE_REPLACEMENT_MODELS` key. When the key is present,
  `"1"`, `"true"`, `"yes"`, `"on"` mean `True` and `"0"`, `"false"`, `"no"`, `"off"` mean
  `False`, ignoring case and surrounding whitespace; any other value raises `ValueError`.
  `env=None` reads `os.environ`. Oracle: requirement (off by default; fail loud on unknown
  values).
- **C4**: Every model in either set has a `MODEL_PRICING` entry with `input`, `output` and
  `cached_input` rates, and a `SEC_TABLE_MODEL_CONFIGS` entry whose `model` field is the
  model id, so turning the switch on can never fail on a missing config key. The replacement
  rates are, per 1K tokens (input / cached input / output): `gpt-5.6-sol` 0.004 / 0.0004 /
  0.02, `gpt-5.6-terra` 0.002 / 0.0002 / 0.012, `gpt-5.6-luna` 0.0002 / 0.00002 / 0.0012.
  Oracle: OpenAI pricing page (Standard, short context, 2026-10), per 1M tokens divided by
  1000.
- **C5**: At import, `XBRL_QUERY_MODEL_TIERS == xbrl_stage_models(replacement_models_enabled())`
  and `MODEL_TIERS["openai"] == openai_model_set(replacement_models_enabled())`. Oracle:
  requirement (module constants follow the switch; existing consumers unchanged).

## Interface

- `src.llm.config.openai_model_set(use_replacement: bool) -> dict[str, str]`
- `src.llm.config.xbrl_stage_models(use_replacement: bool) -> dict[str, str]`
- `src.llm.config.replacement_models_enabled(env: Mapping[str, str] | None = None) -> bool`

## Notes

- Sources: https://developers.openai.com/api/docs/deprecations (GPT-5 snapshot shutdown
  2026-12-11 and named replacements) and https://developers.openai.com/api/docs/pricing
  (Standard short-context rates, read 2026-10-17). `gpt-5.6-sol`'s rate is promotional
  through at least 2026-11-21.
- The replacement set is OpenAI's named mapping. Whether to switch, and whether another set
  (for example `gpt-6-luna` / `gpt-6-sol`) fits better, is decided later by eval slices; this
  change only makes the choice a one-line config edit or an environment override.
- Each returned dict is a fresh copy: a caller mutating it must not change later results.
