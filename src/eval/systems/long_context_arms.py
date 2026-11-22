"""The whole filing, without the oracle: the resolved and multi-filing long-context arms.

``long_context_oracle`` (:mod:`src.eval.systems.long_context`) is handed the right filing
from the case's own ``ticker`` and ``fy``, which makes it a ceiling. These two arms choose
from the question instead, with XbrlRag's own resolver: ``IntentRouter.route`` with a real
``EntityExtractor``, one LLM call on stage ``intent_router`` and then SQL.

- ``long_context_resolved`` reads the one filing the question names, on the oracle's
  stage and prompt. With several years it takes the latest, whose comparative columns
  carry the earlier ones; with several companies it refuses.
- ``long_context_multi`` reads the k filings the question names, in one call on its own
  stage ``long_context_multi``. The filing message is the cache point and holds a
  ``json.dumps`` list in :func:`filing_targets` order, so the same filings give the same
  prefix whatever order the question names them in.

Only ``companies`` and ``periods`` are read from the router's result, and the case's
``ticker``, ``fy`` and ``cik`` never choose a filing. Three outcomes are kept apart:

- **Refusal** (``refused=True``): the resolver could not choose, or no filing exists for
  what it chose. XbrlRag degrades on the same conditions and never guesses a filer or a
  year, so the case stays in the denominator and a resolver error costs the arm.
- **Exclusion** (``"excluded: "``): a fact about the case or the corpus. A filing the
  store does not hold, or text over the limit. Nothing is truncated or dropped to fit.
- **Error**: an exception from the router, resolver, store or provider. ``RunHalt``
  propagates from either LLM call.

Every answer that reached the resolver records what was chosen in ``selection``, which
is persisted with the answer. A run's table of where filings stop fitting comes from
:func:`fit_summary`.

Spec: [docs/specs/long-context-arms.md](../../../docs/specs/long-context-arms.md).
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from src.eval.core.answer_parsing import parse_structured_answer
from src.eval.core.case import KIND_SCREEN, EvalCase
from src.eval.core.system import SystemAnswer
from src.eval.systems._stage_llm import StageLLM, UserPart, answer_in_trace
from src.eval.systems.base import FilingResolver
from src.eval.systems.long_context import (
    CHARS_PER_TOKEN,
    DEFAULT_MAX_INPUT_TOKENS,
    STAGE,
    PostgresFilingResolver,
    _excluded,
    filing_text,
    split_user_template,
)
from src.ingest.sec_edgar.storage import FilingStore
from src.llm.config import MODEL_PRICING
from src.llm.gateway import LLMGateway
from src.llm.stage_models import frontier_model
from src.logging_config import get_logger
from src.retriever.extract_year_util import extract_years_in_range
from src.retriever.xbrl.branch_dispatcher import BranchDispatcher
from src.retriever.xbrl.entity_extractor import EntityExtractor
from src.retriever.xbrl.intent_router import IntentRouter, IntentRouterResult

logger = get_logger(__name__)

MULTI_STAGE = "long_context_multi"
# The extractor's own range for a year written in a period mention.
_MENTION_YEAR_RANGE = [2018, 2035]
# What a selection record's ``status`` says about fit; the rest are not fit results.
_FIT_COLUMNS = {"sent": "sent", "over_window": "over_window", "too_many": "over_window"}


def filing_targets(result: IntentRouterResult) -> tuple[list[tuple[str, int]], str | None]:
    """The filings a router result names.

    The reasons are XbrlRag's own (``BranchDispatcher._unresolved_reasons``), so the arms
    stop on exactly the conditions on which its pipeline degrades, plus the two that leave
    no filing to name: a company with no ticker, and no period with no ``last_seen_fy``.

    Args:
        result: The router's result for one question.

    Returns:
        Sorted, distinct ``(ticker, fiscal_year)`` targets and ``None``; or an empty list
        and the reason the resolver cannot choose.
    """
    reasons = BranchDispatcher._unresolved_reasons(result)
    if reasons:
        return [], "; ".join(reasons)
    no_ticker = [c.mention for c in result.companies if not c.ticker]
    if no_ticker:
        return [], f"no ticker for company mention(s) {no_ticker}"

    if not result.periods:
        latest: set[tuple[str, int]] = set()
        for company in result.companies:
            if company.last_seen_fy is None:
                return [], f"no period given and no last_seen_fy for {company.ticker}"
            latest.add((str(company.ticker), company.last_seen_fy))
        return sorted(latest), None

    tickers = {str(c.ticker) for c in result.companies}
    years = {p.fiscal_year for p in result.periods if p.fiscal_year is not None}
    for period in result.periods:
        # The extractor gives one period per year of a range; re-reading the mention keeps
        # every year for a result built otherwise (the same extract_years_in_range reading).
        years.update(extract_years_in_range(period.mention, year_range=_MENTION_YEAR_RANGE))
    if not years:
        return [], "no period resolved to a fiscal year"
    return sorted((ticker, year) for ticker in tickers for year in years), None


def fit_summary(selections: Iterable[Mapping[str, Any]]) -> dict[int, dict[str, int]]:
    """Where the filings stop fitting, across a run's ``selection`` records.

    Args:
        selections: The ``selection`` record of each answer.

    Returns:
        For each ``k``, ``{"sent": n, "over_window": m}``. ``too_many`` counts as over the
        window; statuses that are not fit results are ignored.
    """
    table: dict[int, dict[str, int]] = {}
    for selection in selections:
        column = _FIT_COLUMNS.get(str(selection.get("status")))
        if column is not None:
            row = table.setdefault(int(selection["k"]), {"sent": 0, "over_window": 0})
            row[column] += 1
    return dict(sorted(table.items()))


@dataclass(frozen=True)
class _Filing:
    """One filing read for a call."""

    ticker: str
    fiscal_year: int
    key: str
    text: str


def _targets_record(targets: Iterable[tuple[str, int]]) -> list[dict[str, Any]]:
    return [{"ticker": ticker, "fiscal_year": year} for ticker, year in targets]


def _selection(
    status: str,
    reason: str | None,
    *,
    filings: list[_Filing] | None = None,
    missing: list[tuple[str, int]] | None = None,
    k: int | None = None,
) -> dict[str, Any]:
    """A selection record, without the fit fields (which need the text to have been read)."""
    filings = filings or []
    return {
        "status": status,
        "reason": reason,
        "filings": [
            {"ticker": f.ticker, "fiscal_year": f.fiscal_year, "key": f.key, "chars": len(f.text)}
            for f in filings
        ],
        "missing": _targets_record(missing or []),
        "k": len(filings) if k is None else k,
    }


def _fit(filings: list[_Filing], limit: int) -> dict[str, int]:
    """The fit fields: ``fits`` is the longest prefix, in target order, within the limit."""
    total, fits = 0, 0
    for filing in filings:
        total += len(filing.text)
        if total <= limit:
            fits += 1
    return {"total_chars": total, "limit_chars": limit, "fits": fits}


class _RoutedArm:
    """What both arms share: the router, the filing backends and the send path.

    Thread-safe: the backends are built once under a lock, and provider calls on one set of
    filings are serialized so the later ones can read the cache the first one wrote.
    """

    name: str

    def __init__(
        self,
        stage: str,
        *,
        model: str | None,
        gateway: LLMGateway | None,
        router: IntentRouter | None,
        resolver: FilingResolver | None,
        store: FilingStore | None,
        max_input_tokens: int,
        max_filings: int,
    ) -> None:
        """Builds the arm; touches no network, database or store.

        Args:
            stage: The prompt-registry stage of the answer call.
            model: The exact model ID for the answer call.
            gateway: The gateway for the answer call; built on first use when omitted.
            router: XbrlRag's intent router; built on first use when omitted.
            resolver: Finds a filing's store key; the Postgres resolver when omitted.
            store: Reads the filings; the normalized store when omitted.
            max_input_tokens: Text over four characters per token of this is excluded.
            max_filings: More targets than this are excluded before anything is read.

        Raises:
            ValueError: If the model has no price, or the window is over 250,000 tokens.
        """
        if model is None:
            model = frontier_model()
        if model not in MODEL_PRICING:
            raise ValueError(
                f"{self.name}: model {model!r} has no MODEL_PRICING entry; the run's cost "
                "cap needs a price"
            )
        if max_input_tokens > DEFAULT_MAX_INPUT_TOKENS:
            raise ValueError(
                f"{self.name}: max_input_tokens {max_input_tokens:,} is over "
                f"{DEFAULT_MAX_INPUT_TOKENS:,}: pricing.yaml has no long-context tier, so "
                "a wider window would record less than the provider charges"
            )
        self.model = model
        self.max_input_tokens = int(max_input_tokens)
        self.max_filings = int(max_filings)
        self._llm = StageLLM(stage, gateway, model=model)
        self._filing_template, self._question_template = split_user_template(
            self._llm.prompt.user
        )
        self._router = router
        self._resolver = resolver
        self._store = store
        self._lock = threading.Lock()
        self._set_locks: dict[str, threading.Lock] = {}

    # ------------------------------------------------------------------
    # Lazily built backends (double-checked locking: answer() runs concurrently).
    # ------------------------------------------------------------------

    def _get_router(self) -> IntentRouter:
        if self._router is None:
            with self._lock:
                if self._router is None:
                    # As XbrlRetrievalPipeline builds it: the stage's model comes from the
                    # active profile, and no ticker list is offered to the LLM.
                    self._router = IntentRouter(
                        entity_extractor=EntityExtractor.from_env(), known_tickers=[]
                    )
        return self._router

    def _get_resolver(self) -> FilingResolver:
        if self._resolver is None:
            with self._lock:
                if self._resolver is None:
                    self._resolver = PostgresFilingResolver()
        return self._resolver

    def _get_store(self) -> FilingStore:
        if self._store is None:
            with self._lock:
                if self._store is None:
                    self._store = FilingStore.from_env(role="normalized")
        return self._store

    def _set_lock(self, filings: list[_Filing]) -> threading.Lock:
        """The lock that serializes provider calls on one set of filings."""
        with self._lock:
            return self._set_locks.setdefault(
                "\n".join(f.key for f in filings), threading.Lock()
            )

    # ------------------------------------------------------------------
    # What differs between the arms.
    # ------------------------------------------------------------------

    def _exclusion(self, case: EvalCase) -> str | None:
        """Why the case is out of scope, before any LLM call; None when it is in scope."""
        raise NotImplementedError

    def _choose(
        self, targets: list[tuple[str, int]]
    ) -> tuple[list[tuple[str, int]], str | None]:
        """Narrows the targets to the ones to send, or gives the reason it cannot."""
        return targets, None

    def _filing_payload(self, filings: list[_Filing]) -> str:
        """The filing message's untrusted payload, JSON-encoded."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # The path both arms follow.
    # ------------------------------------------------------------------

    def _answer_case(self, case: EvalCase) -> SystemAnswer:
        """Runs the arm in a trace, and keeps its record when the provider call fails.

        ``answer_in_trace`` replaces the answer on a provider error or refusal, so the
        record is filled in as the arm goes and stamped here.
        """
        record: dict[str, Any] = {}
        answer = answer_in_trace(case.question, lambda: self._produce(case, record))
        if record:
            answer.selection = record
        return answer

    def _produce(self, case: EvalCase, record: dict[str, Any]) -> SystemAnswer:
        exclusion = self._exclusion(case)
        if exclusion is not None:
            return _excluded(exclusion)

        targets, reason = filing_targets(self._get_router().route(case.question))
        if reason is None:
            targets, reason = self._choose(targets)
        if reason is not None:
            record.update(_selection("undecided", reason))
            return SystemAnswer(text=reason, refused=True)
        if len(targets) > self.max_filings:
            reason = f"{len(targets)} filings named, over the limit of {self.max_filings}"
            record.update(_selection("too_many", reason, k=len(targets)))
            return _excluded(reason)

        filings, missing, any_key = self._read_filings(targets)
        if not filings:
            names = ", ".join(f"{ticker} FY{year}" for ticker, year in missing)
            if any_key:
                reason = f"not in the filing store: {names}"
                record.update(_selection("not_in_store", reason, missing=missing))
                return _excluded(reason)
            reason = f"no canonical annual filing for {names}"
            record.update(_selection("no_filing", reason, missing=missing))
            return SystemAnswer(text=reason, refused=True)

        limit = CHARS_PER_TOKEN * self.max_input_tokens
        fit = _fit(filings, limit)
        if fit["total_chars"] > limit:
            reason = (
                f"{len(filings)} filing(s) total {fit['total_chars']:,} characters, over the "
                f"{limit:,}-character limit ({self.max_input_tokens:,} tokens)"
            )
            record.update(_selection("over_window", reason, filings=filings, missing=missing))
            record.update(fit)
            return _excluded(reason)

        record.update(_selection("sent", None, filings=filings, missing=missing))
        record.update(fit)
        return self._send(case, filings)

    def _read_filings(
        self, targets: list[tuple[str, int]]
    ) -> tuple[list[_Filing], list[tuple[str, int]], bool]:
        """Reads the targets, in order; bounded by ``max_filings`` in the caller.

        Args:
            targets: The filings to read.

        Returns:
            The filings read, the targets left out, and whether any target had a key.
        """
        filings: list[_Filing] = []
        missing: list[tuple[str, int]] = []
        any_key = False
        for ticker, fiscal_year in targets:
            # The key comes from the resolver and is used verbatim: since
            # fiscal-label-alignment the label and the path's year can differ.
            key = self._get_resolver().canonical_key(ticker, fiscal_year)
            html = None if key is None else self._read_html(key)
            any_key = any_key or key is not None
            if key is None or html is None:
                missing.append((ticker, fiscal_year))
            else:
                filings.append(_Filing(ticker, fiscal_year, key, filing_text(html)))
        return filings, missing, any_key

    def _read_html(self, key: str) -> str | None:
        """The object at *key*, or None when the store does not hold it."""
        store = self._get_store()
        try:
            return store.read_text(key) if store.exists(key) else None
        except FileNotFoundError:
            return None

    def _send(self, case: EvalCase, filings: list[_Filing]) -> SystemAnswer:
        """The one answer call: filing message (the cache point), then question."""
        parts = [
            UserPart(
                self._filing_template.format(filing=self._filing_payload(filings)),
                cache_point=True,
            ),
            UserPart(self._question_template.format(question=case.question)),
        ]
        with self._set_lock(filings):
            reply = self._llm.complete(parts)
        answer = parse_structured_answer(reply)
        answer.fact_ids = []
        answer.chunk_ids = []
        return answer


class LongContextResolvedSystem(_RoutedArm):
    """Answers from the one filing XbrlRag's resolver chooses for the question."""

    name = "long_context_resolved"

    def __init__(
        self,
        *,
        model: str | None = None,
        gateway: LLMGateway | None = None,
        router: IntentRouter | None = None,
        resolver: FilingResolver | None = None,
        store: FilingStore | None = None,
        max_input_tokens: int = 250000,
    ) -> None:
        """Builds the arm; touches no network, database or store.

        Args:
            model: The exact model ID for the answer call.
            gateway: The gateway for the answer call; built on first use when omitted.
            router: XbrlRag's intent router; built on first use when omitted.
            resolver: Finds a filing's store key; the Postgres resolver when omitted.
            store: Reads the filing; the normalized store when omitted.
            max_input_tokens: Text over four characters per token of this is excluded.

        Raises:
            ValueError: If the model has no price, or the window is over 250,000 tokens.
        """
        super().__init__(
            STAGE, model=model, gateway=gateway, router=router, resolver=resolver, store=store,
            max_input_tokens=max_input_tokens, max_filings=1,
        )

    def answer(self, case: EvalCase) -> SystemAnswer:
        """Answers one case from the filing its question resolves to.

        Args:
            case: The eval case.

        Returns:
            The answer, a refusal when the resolver cannot choose, or an ``"excluded: "``
            error; with its ``selection``, trace id, cost and latency.

        Raises:
            RunHalt: The run's budget or circuit breaker stopped a call.
        """
        return self._answer_case(case)

    def _exclusion(self, case: EvalCase) -> str | None:
        # The oracle arm's own exclusion, so both arms are scored on the same cases. These
        # two fields are read for this check only.
        if not case.ticker or case.fy is None:
            return f"case {case.id} has no single filing (ticker or fiscal year missing)"
        return None

    def _choose(
        self, targets: list[tuple[str, int]]
    ) -> tuple[list[tuple[str, int]], str | None]:
        tickers = sorted({ticker for ticker, _ in targets})
        if len(tickers) > 1:
            return [], f"one filing cannot answer for several companies: {tickers}"
        # Sorted by year: the latest filing's comparative columns carry the earlier years.
        return targets[-1:], None

    def _filing_payload(self, filings: list[_Filing]) -> str:
        return json.dumps(filings[0].text, ensure_ascii=False)


class LongContextMultiSystem(_RoutedArm):
    """Answers from the k filings the question resolves to, concatenated in one call."""

    name = "long_context_multi"

    def __init__(
        self,
        *,
        model: str | None = None,
        gateway: LLMGateway | None = None,
        router: IntentRouter | None = None,
        resolver: FilingResolver | None = None,
        store: FilingStore | None = None,
        max_input_tokens: int = 250000,
        max_filings: int = 10,
    ) -> None:
        """Builds the arm; touches no network, database or store.

        Args:
            model: The exact model ID for the answer call.
            gateway: The gateway for the answer call; built on first use when omitted.
            router: XbrlRag's intent router; built on first use when omitted.
            resolver: Finds a filing's store key; the Postgres resolver when omitted.
            store: Reads the filings; the normalized store when omitted.
            max_input_tokens: Filings totalling over four characters per token of this
                are excluded, never truncated.
            max_filings: More targets than this are excluded before anything is read.

        Raises:
            ValueError: If the model has no price, or the window is over 250,000 tokens.
        """
        super().__init__(
            MULTI_STAGE, model=model, gateway=gateway, router=router, resolver=resolver,
            store=store, max_input_tokens=max_input_tokens, max_filings=max_filings,
        )

    def answer(self, case: EvalCase) -> SystemAnswer:
        """Answers one case from the filings its question resolves to.

        Args:
            case: The eval case.

        Returns:
            The answer, a refusal, or an ``"excluded: "`` error, with its ``selection``,
            trace id, cost and latency.

        Raises:
            RunHalt: The run's budget or circuit breaker stopped a call.
        """
        return self._answer_case(case)

    def _exclusion(self, case: EvalCase) -> str | None:
        if case.kind == KIND_SCREEN or case.universe:
            return f"case {case.id} ranges over filers its question does not name"
        return None

    def _filing_payload(self, filings: list[_Filing]) -> str:
        return json.dumps(
            [{"ticker": f.ticker, "fiscal_year": f.fiscal_year, "text": f.text} for f in filings],
            ensure_ascii=False,
        )
