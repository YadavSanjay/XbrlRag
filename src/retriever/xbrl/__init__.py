"""Query-time XBRL hybrid pipeline components (design doc §2.2).

Modules:
- entity_extractor: deterministic SQL resolution of company/period mentions
  against the Layer-0 `entity_master` / `fiscal_calendar` tables (§2.2.2 step 2).
- intent_router: cheap LLM call classifying intent and lifting raw
  company/period/concept mentions, plus the trivial-lookup fast path (§2.2.2).
- branch_dispatcher: decides which downstream branch (fact/narrative/plan
  generator) handles a query, given the intent_router's output (§2.2.1).
"""
