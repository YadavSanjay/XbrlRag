"""entity_extractor + intent_router (design doc §2.2.2).

The cheapest, first-stage LLM call in the XBRL hybrid query pipeline (§2.2).
Runs on every query after a cache miss:

1. One small-model LLM call classifies intent and lifts raw company/period/
   concept mentions (this module, `_do_llm_call` + `_parse_raw_extraction`).
2. Deterministic SQL resolution of those mentions against `entity_master` /
   `fiscal_calendar` (delegated to `EntityExtractor` — never lets the LLM
   invent a CIK or fiscal-year boundary).
3. For the trivial case (single company/period/concept, no dimensional slice,
   intent in {NUMERIC_LOOKUP, NARRATIVE}), emit a 1-step `plan` directly so
   `plan_generator` (§2.2.3) is skipped entirely.
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from prompts.prompt_registry import PromptRegistry, get_xbrl_registry
from src.llm.gateway import extract_json, resolve_model_ref, stage_call_params
from src.llm.stage_models import require_api_key, resolve_stage
from src.llm.xbrl_gateway import build_xbrl_gateway
from src.observability.trace_context import log_event
from src.retriever.xbrl.entity_extractor import EntityExtractor, ResolvedCompany, ResolvedPeriod
from src.retriever.xbrl.entity_sectors import load_universe_groups
from src.retriever.xbrl.operation_cues import operation_cue, operation_intent
from src.retriever.xbrl.period_evidence import partition_yearless_periods
from src.retriever.xbrl.ranked_top import strip_rank_label
from src.retriever.xbrl.universe_routing import (
    find_universe_mentions,
    group_aggregate_rule,
    split_company_mentions,
    universe_aliases,
    universe_intent,
)

_PIT_ORIGINAL_PHRASES = (
    "as originally reported", "originally reported", "as first reported",
    "as originally filed", "as-originally-reported", "before restatement",
)


def _detect_point_in_time(query: str) -> dict:
    """Detect an 'as originally reported' point-in-time request in the raw query."""
    q = query.lower()
    if any(p in q for p in _PIT_ORIGINAL_PHRASES):
        return {"as_originally_reported": True}
    return {}
from src.logging_config import get_logger

logger = get_logger(__name__)


class XBRL_INTENT(Enum):
    NUMERIC_LOOKUP = "NUMERIC_LOOKUP"
    NUMERIC_COMPUTE = "NUMERIC_COMPUTE"
    NUMERIC_SCREEN = "NUMERIC_SCREEN"
    NARRATIVE = "NARRATIVE"
    HYBRID = "HYBRID"


INTENT_ROUTER_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": "false",
    "properties": {
        "intent": {"type": "string", "enum": ["NUMERIC_LOOKUP", "NUMERIC_COMPUTE", "NUMERIC_SCREEN", "NARRATIVE", "HYBRID"]},
        "company_mentions": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "period_mentions": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "concept_phrases": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "dimension_hint_present": {"type": "boolean"},
    },
    "required": ["intent", "company_mentions", "period_mentions", "concept_phrases", "dimension_hint_present"],
}


# Condensed concept-family glossary used for the fast-path single-fact lookup.
# A small subset of the full CONCEPT_FAMILIES taxonomy in
# prompts/templates/xbrl/xbrl_query_plan_generator.txt — keep the family names
# in sync with that file (plan_generator owns the authoritative taxonomy).
#
# Only families that are a single tagged fact (and exist in concept_mapping) belong here.
# Ratios, margins and derived measures (gross margin, EBITDA, free cash flow, any
# growth or per-share figure) are deliberately absent: an unmatched phrase goes to
# plan_generator, which can compute them, instead of fast-pathing a near-miss fact.
CONDENSED_CONCEPT_FAMILIES: dict[str, list[str]] = {
    "Revenue": ["revenue", "revenues", "net sales", "total revenue", "sales", "top line", "turnover"],
    "CostOfRevenue": [
        "cost of revenue", "cost of sales", "cogs", "cost of goods sold",
        "cost of goods and services sold",
    ],
    "GrossProfit": ["gross profit", "gross income"],
    "ResearchAndDevelopment": ["r&d", "research and development", "r&d expense", "product development"],
    "SellingGeneralAndAdministrative": ["sg&a", "selling general and administrative", "sga expense"],
    "SellingAndMarketing": ["sales and marketing", "selling expense", "s&m"],
    "GeneralAndAdministrative": ["g&a", "general and administrative", "admin expense"],
    "OperatingExpenses": ["operating expenses", "total operating expenses", "opex"],
    "OperatingIncome": ["operating income", "operating profit", "income from operations", "ebit"],
    "InterestExpense": ["interest expense", "interest paid"],
    "InterestIncome": ["interest income", "investment income"],
    "PretaxIncome": [
        "pretax income", "pre-tax income", "income before taxes", "income before income taxes",
        "ebt",
    ],
    "TaxExpense": ["income tax", "income tax expense", "tax provision"],
    "EffectiveTaxRate": ["effective tax rate", "etr"],
    "NetIncome": ["net income", "net earnings", "net profit", "net loss", "bottom line"],
    "EarningsPerShareBasic": ["basic eps", "basic earnings per share"],
    "EarningsPerShareDiluted": ["diluted eps", "diluted earnings per share"],
    # An unqualified count of shares is the period-end shares outstanding; a weighted-average,
    # basic or diluted count is not a synonym here and goes to plan_generator.
    "SharesOutstanding": [
        "shares outstanding", "common shares outstanding", "share count", "shares count",
        "number of shares", "number of shares outstanding",
    ],
    "TotalAssets": ["total assets", "assets"],
    "CurrentAssets": ["current assets", "total current assets"],
    "CashAndEquivalents": ["cash", "cash and cash equivalents"],
    "TotalLiabilities": ["total liabilities", "liabilities"],
    "CurrentLiabilities": ["current liabilities", "total current liabilities"],
    "Debt": ["total debt", "debt", "borrowings", "long-term debt"],
    "StockholdersEquity": ["stockholders equity", "shareholders equity", "total equity"],
    "RetainedEarnings": ["retained earnings", "accumulated deficit"],
    # Cash-flow subtotals: the router often echoes the statement label verbatim.
    "CashFromOperations": [
        "operating cash flow", "cash from operations", "ocf",
        "net cash provided by operating activities", "cash provided by operating activities",
        "net cash from operating activities", "cash flow from operations",
    ],
    "CashFromInvesting": [
        "investing cash flow", "cash used in investing",
        "net cash used in investing activities", "cash used in investing activities",
        "net cash from investing activities",
    ],
    "CashFromFinancing": [
        "financing cash flow", "cash from financing",
        "net cash used in financing activities", "cash used in financing activities",
        "net cash from financing activities",
    ],
    "Capex": ["capex", "capital expenditures", "ppe additions"],
    "ShareBasedCompensation": [
        "stock-based compensation", "sbc", "share-based compensation",
        "stock-based compensation expense", "share-based compensation expense",
    ],
    "StockRepurchases": ["buybacks", "share repurchases", "stock repurchases"],
    "DividendsPaid": ["dividends paid", "cash dividends paid"],
    "EmployeeCount": ["number of employees", "headcount", "employees"],
}


def _normalize_concept_phrase(phrase: str) -> str:
    """Lower-case, collapse whitespace and drop trailing punctuation — formatting that
    does not change which metric a phrase names."""
    return " ".join(phrase.lower().split()).rstrip(".,;:!?").strip()


# Normalized synonym -> family. Matching is exact on purpose: character similarity
# mapped "total current assets" to TotalAssets, "gross margin" to GrossProfit and
# "revenue per share" to DividendsPerShare — a real but different fact, cited with
# confidence. A phrase that is not a known synonym is not a fast-path lookup. The
# family names themselves are synonyms too: the router sometimes echoes them run
# together ("totalassets", "grossprofit").
_CONCEPT_SYNONYM_INDEX: dict[str, str] = {
    **{family.lower(): family for family in CONDENSED_CONCEPT_FAMILIES},
    **{
        _normalize_concept_phrase(syn): family
        for family, synonyms in CONDENSED_CONCEPT_FAMILIES.items()
        for syn in synonyms
    },
}

# A trailing parenthetical that only restates the sign convention — "operating income
# (loss)" — names the same fact.
_SIGN_PARENTHETICALS = frozenset({"loss", "income", "gain", "deficit"})
_TRAILING_PARENTHETICAL = re.compile(r"^(?P<base>.+?)\s*\((?P<note>[^()]+)\)$")


def extract_currency_unit(phrase: str) -> tuple[str, str | None]:
    """Split a trailing currency mention off a concept phrase.

    Args:
        phrase: A concept phrase, e.g. "revenue in New Taiwan dollars".

    Returns:
        ``(phrase_without_currency, iso_code)``, e.g. ``("revenue", "TWD")``; the phrase
        unchanged and None when it names no currency.
    """
    m = _TRAILING_CURRENCY.match(phrase)
    if not m or not m["concept"].strip():
        return phrase, None
    for code, pattern in _CURRENCY_PATTERNS:
        if re.fullmatch(pattern, m["currency"], flags=re.IGNORECASE):
            return m["concept"], code
    return phrase, None


# ISO 4217 code -> how a question names the currency. Only currencies a filer in the corpus
# reports in or translates to; extend alongside a new foreign filer.
_CURRENCY_PATTERNS: tuple[tuple[str, str], ...] = (
    ("TWD", r"new\s+taiwan\s+dollars?|nt\s*dollars?|nt\$|twd"),
    ("USD", r"u\.?\s*s\.?\s+dollars?|us\s+dollars?|usd"),
)
_TRAILING_CURRENCY = re.compile(
    r"^(?P<concept>.*?)[\s,;:]*\(?\s*\bin\s+"
    r"(?P<currency>" + "|".join(p for _, p in _CURRENCY_PATTERNS) + r")"
    r"\s*\)?[\s.,;:]*$",
    flags=re.IGNORECASE,
)


@dataclass
class IntentRouterResult:
    """Output envelope of the entity_extractor + intent_router stage (§2.2 diagram)."""

    intent: XBRL_INTENT
    companies: list[ResolvedCompany] = field(default_factory=list)
    periods: list[ResolvedPeriod] = field(default_factory=list)
    concept_candidates: list[str] = field(default_factory=list)
    dimension_hint_present: bool = False
    plan: dict | None = None
    raw_extraction: dict = field(default_factory=dict)
    # The one named universe the question ranks ("covered" or a groups.yaml id), resolved
    # by rule from its words, and the aggregate fn an unambiguous cue word asks for
    # (docs/specs/screen-aggregates.md); None when there is none.
    universe: str | None = None
    aggregate: str | None = None
    # The operation the question cues by rule ("cagr" or "ratio";
    # docs/specs/plan-cagr-and-ratios.md); None when there is none.
    operation: str | None = None

    def to_dict(self) -> dict:
        return {
            "intent": self.intent.value,
            "companies": [vars(c) | {"is_ambiguous": c.is_ambiguous} for c in self.companies],
            "periods": [vars(p) for p in self.periods],
            "concept_candidates": self.concept_candidates,
            "dimension_hint_present": self.dimension_hint_present,
            "plan": self.plan,
            "universe": self.universe,
            "aggregate": self.aggregate,
            "operation": self.operation,
        }


class IntentRouter:
    """First-stage classifier + extractor for the XBRL hybrid query pipeline."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        entity_extractor: EntityExtractor | None = None,
        known_tickers: list[str] | None = None,
        prompt_version: str | None = None,
        prompt_provider: str | None = None,
        prompt_series: str | None = None,
        prompt_registry: PromptRegistry | None = None,
        universe_groups: Mapping[str, Sequence[str]] | None = None,
    ):
        # The model comes from the LLM profile active now (an explicit model wins), and
        # the key from that model's provider (docs/specs/llm-provider-profiles.md).
        stage_model = resolve_stage("intent_router", model)
        # Named universe groups (configs/eval/v2/groups.yaml, via load_universe_groups);
        # never from the LLM. Read lazily so a router that never sees a group needs no file.
        self._universe_groups = universe_groups
        self.api_key = require_api_key(stage_model.model, stage_model.settings, api_key)
        self.model = stage_model.model
        self._llm_settings = stage_model.settings
        self.entity_extractor = entity_extractor
        self.known_tickers = known_tickers or []

        self._model_ref = resolve_model_ref(self.model)
        self.model_config = self._model_ref.config
        self._registry = prompt_registry or get_xbrl_registry()
        self._prompt = self._registry.resolve(
            "intent_router",
            provider=prompt_provider or self._model_ref.provider,
            series=prompt_series or self._model_ref.series,
            version=prompt_version,
        )
        self._prompt_sig = self._prompt.signature()
        self._llm_client = build_xbrl_gateway("intent_router", api_key=api_key)

        logger.info(f"Initialized XBRL Intent Router with model={model}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def route(self, query: str, **kwargs) -> IntentRouterResult:
        """Classify intent, extract raw mentions, resolve them, and try the fast path."""
        messages = self._build_messages(query)
        llm_response = self._do_llm_call(messages, **kwargs)
        raw = self._parse_raw_extraction(llm_response)

        llm_intent = XBRL_INTENT(raw["intent"])
        period_mentions = raw.get("period_mentions", [])
        concept_phrases = raw.get("concept_phrases", [])
        dimension_hint_present = bool(raw.get("dimension_hint_present", False))

        # A group lifted as a company ("the big tech companies") is not a company: it
        # would resolve to no filer and be refused (docs/specs/screen-aggregates.md R2).
        groups = self._groups()
        aliases = universe_aliases()
        # A mention lifted from an injected ranked list ("AMZN (#1)") loses its rank label
        # before it resolves (docs/specs/ranked-top-injection.md R2).
        company_mentions, split_universes = split_company_mentions(
            [strip_rank_label(str(m)) for m in raw.get("company_mentions", []) or []], groups, aliases
        )

        companies: list[ResolvedCompany] = []
        periods: list[ResolvedPeriod] = []
        ignored_periods: list[ResolvedPeriod] = []
        if self.entity_extractor is not None:
            companies = self.entity_extractor.resolve_companies(company_mentions)
            periods = self.entity_extractor.resolve_periods(period_mentions, companies)
            # A mention naming no year ("year-over-year") never degrades a query whose
            # other periods resolved (docs/specs/yearless-period-mention.md R3).
            periods, ignored_periods = partition_yearless_periods(periods)
            for ignored in ignored_periods:
                log_event(
                    "decision", point="period_resolution", decision="ignore_yearless_mention",
                    mention=ignored.mention, reason=ignored.reason,
                )
            # A range the LLM split into its endpoints gets its middle years back
            # (docs/specs/fiscal-year-range-split.md). A duck-typed resolver (only
            # resolve_companies / resolve_periods) has no calendar to complete against.
            if isinstance(self.entity_extractor, EntityExtractor):
                periods = self.entity_extractor.complete_stated_ranges(query, periods, companies)
        elif company_mentions or period_mentions:
            logger.warning(
                "No EntityExtractor configured - skipping deterministic company/period "
                "resolution; downstream branches will treat companies/periods as unresolved."
            )

        # Universes named in the question's own words; a resolved company's mention is
        # masked so "Big Tech Holdings" (a filer) never names the big_tech group.
        resolved = [c for c in companies if c.cik and not c.is_ambiguous]
        universes = find_universe_mentions(
            query, groups, aliases, ignore=[c.mention for c in resolved]
        )
        universes += [u for u in split_universes if u not in universes]
        # The cue words, or "total"/"add up" over one named group and no company
        # (docs/specs/group-sum-cue.md).
        aggregate, aggregate_rule = group_aggregate_rule(query, universes, len(resolved))
        intent = XBRL_INTENT(
            universe_intent(llm_intent.value, universes, len(resolved), aggregate)
        )
        # Also logged when the LLM already chose a screen but a group rule supplied the
        # aggregate, so every rule that fired shows in the trace.
        if intent is not llm_intent or aggregate_rule in ("add_up", "total"):
            log_event(
                "decision", point="universe_routing",
                decision="route_to_screen" if intent is not llm_intent else "keep_intent",
                llm_intent=llm_intent.value, intent=intent.value, universes=universes,
                aggregate=aggregate, aggregate_rule=aggregate_rule,
            )
        # A CAGR or ratio cued by rule is never answered by the lookup fast path
        # (docs/specs/plan-cagr-and-ratios.md, Requirement 6).
        operation = operation_cue(query)
        cued_intent = XBRL_INTENT(operation_intent(intent.value, operation))
        if cued_intent is not intent:
            log_event(
                "decision", point="operation_cue", decision="route_to_compute",
                previous_intent=intent.value, intent=cued_intent.value, operation=operation,
            )
            intent = cued_intent

        # The cue is a group requirement (R6 forces an aggregate over a screen), so it is
        # carried only for a question over one named group with no company named. "The
        # combined revenue of Apple and Microsoft" stays a lookup + compute plan; carrying
        # the cue there would reject that correct plan on every retry.
        group_question = len(universes) == 1 and not resolved
        result = IntentRouterResult(
            intent=intent,
            companies=companies,
            periods=periods,
            concept_candidates=concept_phrases,
            dimension_hint_present=dimension_hint_present,
            raw_extraction=raw,
            universe=universes[0] if len(universes) == 1 else None,
            aggregate=aggregate if group_question else None,
            operation=operation,
        )
        # A dropped yearless mention may still shape the period ("year-over-year"), so a
        # one-step lookup never answers it; the plan generator reads the whole question.
        if ignored_periods and intent is XBRL_INTENT.NUMERIC_LOOKUP:
            result.plan = None
        else:
            result.plan = self._try_fast_path_plan(result)
        # Point-in-time: apply an "as originally reported" hint to fast-path
        # lookups. (as-of-date phrasing is left to the API — no reliable NL date
        # parse here.)
        pit = _detect_point_in_time(query)
        if pit and result.plan:
            for step in result.plan.get("steps", []):
                if step.get("op") == "lookup":
                    step.update(pit)

        logger.info(
            f"intent_router: query={query!r} -> intent={intent.value}, "
            f"companies={[c.ticker or c.mention for c in companies]}, "
            f"periods={[p.fiscal_year or p.mention for p in periods]}, "
            f"fast_path={'yes' if result.plan else 'no'}"
        )

        # Surface the extracted/resolved slots as a structured event so the Ask UI
        # can show the user how their question was understood (confirmable chips).
        log_event(
            "intent_extraction",
            stage="intent_router",
            intent=intent.value,
            companies=[
                {"mention": c.mention, "ticker": c.ticker, "cik": c.cik,
                 "company_name": c.company_name, "resolution": c.resolution,
                 "candidates": c.candidates}
                for c in companies
            ],
            periods=[
                {"mention": p.fiscal_year and str(p.fiscal_year) or p.mention,
                 "fiscal_year": p.fiscal_year, "fiscal_period": p.fiscal_period,
                 "ambiguous": p.ambiguous}
                for p in periods
            ],
            concepts=list(concept_phrases or []),
            dimension_hint=dimension_hint_present,
            fast_path=result.plan is not None,
            universe=result.universe,
            universes=universes,
            aggregate=result.aggregate,
            operation=result.operation,
        )
        return result

    def _groups(self) -> Mapping[str, Sequence[str]]:
        """The named universe groups (``load_universe_groups()["groups"]``), read once."""
        if self._universe_groups is None:
            self._universe_groups = load_universe_groups()["groups"]
        return self._universe_groups

    # ------------------------------------------------------------------
    # Step 3: trivial-lookup fast path
    # ------------------------------------------------------------------

    def _try_fast_path_plan(self, result: IntentRouterResult) -> dict | None:
        """Emit a 1-step plan for the trivial NUMERIC_LOOKUP / NARRATIVE case.

        Returns None if `plan_generator` (§2.2.3) is needed instead — i.e. for
        NUMERIC_COMPUTE, HYBRID, or a NUMERIC_LOOKUP that needs a dimensional
        slice or spans multiple companies/periods/concepts.
        """
        if result.intent == XBRL_INTENT.NARRATIVE:
            return {"steps": [{"id": "n1", "op": "narrative", "section_hint": None}]}

        if result.intent != XBRL_INTENT.NUMERIC_LOOKUP:
            return None  # NUMERIC_COMPUTE / NUMERIC_SCREEN / HYBRID always go to plan_generator
        if result.dimension_hint_present:
            return None  # dimensional slices need the full taxonomy in plan_generator

        non_ambiguous_companies = [c for c in result.companies if not c.is_ambiguous]
        non_ambiguous_periods = [p for p in result.periods if not p.ambiguous]

        if len(result.companies) != 1 or len(non_ambiguous_companies) != 1:
            return None
        if len(result.periods) != 1 or len(non_ambiguous_periods) != 1:
            return None
        if len(result.concept_candidates) != 1:
            return None

        company = non_ambiguous_companies[0]
        period = non_ambiguous_periods[0]
        if not company.cik or period.fiscal_year is None:
            return None

        # "revenue in New Taiwan dollars": the currency is a unit, not part of the metric.
        concept_phrase, unit = extract_currency_unit(result.concept_candidates[0])
        concept_family = self._match_concept_family(concept_phrase)
        if concept_family is None:
            return None  # unrecognized metric phrase - let plan_generator's full taxonomy try

        step = {
            "id": "f1",
            "op": "lookup",
            "cik": company.cik,
            "ticker": company.ticker,
            "fy": period.fiscal_year,
            "fiscal_period": period.fiscal_period,
            "ytd": period.ytd,
            "concept_family": concept_family,
            "dimensions": "NONE",
            "section_hint": None,
        }
        if unit is not None:
            step["unit"] = unit
        return {"steps": [step]}

    @staticmethod
    def _match_concept_family(phrase: str) -> str | None:
        """Map a concept phrase to a CONDENSED_CONCEPT_FAMILIES key by exact synonym.

        Matching ignores case, whitespace runs and trailing punctuation, a leading
        "total " ("total current assets" is "current assets"), and a trailing
        parenthetical that only restates the sign or the same metric ("operating income
        (loss)", "capital expenditures (capex)"). Anything else, including every ratio,
        margin or derived measure, returns None and is planned instead.
        """
        normalized = _normalize_concept_phrase(phrase)
        if not normalized:
            return None
        family = IntentRouter._exact_concept_family(normalized)
        if family is None and (m := _TRAILING_PARENTHETICAL.match(normalized)):
            base = IntentRouter._exact_concept_family(m["base"])
            note = m["note"].strip()
            if base is not None and (
                note in _SIGN_PARENTHETICALS
                or IntentRouter._exact_concept_family(note) == base
            ):
                family = base
        return family

    @staticmethod
    def _exact_concept_family(normalized: str) -> str | None:
        """Exact synonym lookup, allowing a leading "total "."""
        family = _CONCEPT_SYNONYM_INDEX.get(normalized)
        if family is None and normalized.startswith("total "):
            family = _CONCEPT_SYNONYM_INDEX.get(normalized.removeprefix("total "))
        return family

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------

    def _build_messages(self, query: str) -> list[dict]:
        system_prompt_text = self._prompt.system.format(
            response_schema_json=INTENT_ROUTER_RESPONSE_SCHEMA,
        )
        user_prompt_text = self._prompt.user.format(
            user_query=query,
            known_tickers=", ".join(self.known_tickers) if self.known_tickers else "(none provided)",
        )
        return [
            {
                "role": "system",
                "content": [{"type": "text", "text": system_prompt_text}],
            },
            {
                "role": "user",
                "content": [{"type": "text", "text": user_prompt_text}],
            },
        ]

    def _do_llm_call(self, messages: list[dict], **kwargs) -> str:
        """Run the intent-router LLM call through the instrumented gateway.

        Latency, token usage / cost, retries and the structured ``llm_call``
        event are all handled by :class:`~src.llm.gateway.LLMGateway`.
        """
        result = self._llm_client.complete(
            messages=messages,
            model_ref=self._model_ref,
            stage="intent_router",
            **stage_call_params(self.model_config, temperature=0.1),
            response_format="json",
            prompt_templates=self._prompt_sig["templates"],
            prompt_version=self._prompt_sig["version"],
            **kwargs,
        )
        return result.content

    def _parse_raw_extraction(self, response_content: str) -> dict:
        fallback = {
            "intent": "NARRATIVE",
            "company_mentions": [],
            "period_mentions": [],
            "concept_phrases": [],
            "dimension_hint_present": False,
        }
        try:
            data = extract_json(response_content)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse intent router response as JSON ({e}); falling back to NARRATIVE")
            return fallback

        if not isinstance(data, dict):
            logger.error("intent router returned non-object JSON; falling back to NARRATIVE")
            return fallback

        if data.get("intent") not in {i.value for i in XBRL_INTENT}:
            logger.warning(f"Unknown/missing intent '{data.get('intent')}' - falling back to NARRATIVE")
            data["intent"] = "NARRATIVE"

        data.setdefault("company_mentions", [])
        data.setdefault("period_mentions", [])
        data.setdefault("concept_phrases", [])
        data.setdefault("dimension_hint_present", False)
        return data


if __name__ == "__main__":
    # Example usage (requires OPENAI_API_KEY and a reachable xbrl_db for full resolution)
    router = IntentRouter(entity_extractor=EntityExtractor.from_env())

    for q in [
        "What was Apple's revenue in fiscal 2023?",
        "What was NVIDIA's Data Center revenue last year?",
        "What was the year-over-year growth in Google's R&D expense from FY2022 to FY2023?",
        "What are the main risk factors Amazon discusses related to its logistics network?",
        "Why did Meta's operating margin improve in fiscal 2024?",
    ]:
        result = router.route(q)
        print(q)
        print(json.dumps(result.to_dict(), indent=2, default=str))
        print()
