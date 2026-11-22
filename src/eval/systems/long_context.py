"""The ``long_context_oracle`` arm: "paste the 10-K into a frontier model".

Formerly the system ``long_context``; that name is still accepted, and older runs are
stored under it. The arms that choose the filing from the question are in
:mod:`src.eval.systems.long_context_arms`. The stage stays ``long_context``.

For each case the arm is handed the **right filing** (an oracle document, which favours
it): the one canonical annual filing that holds every fact the case cites (under S1 a
restated period is cited from a later filing; ``cited_filing_key``, found through a
:class:`~src.eval.systems.base.CitedFilingResolver`), else the filer's canonical annual
10-K / 20-F for the case's fiscal year, found by a
:class:`~src.eval.systems.base.FilingResolver`; it is read through the ``FilingStore``
façade (local FS or S3). The filing's canonical HTML is reduced to plain text by
:func:`filing_text` and sent, whole, in one LLM call on an explicit frontier model
(by default the active profile's frontier tier, chosen once when the arm is built and
recorded with the run, so a run always states which model it tested;
docs/specs/arm-model-from-profile.md). The reply is the arm-neutral JSON answer object, read by
``parse_structured_answer``; the arm never produces ``fact_ids`` or ``chunk_ids``.

Prompt caching shapes the call:

- the call has three messages: the system message (the same for every case); a user
  message holding the filing text (``json.dumps``-encoded: it is untrusted data) and
  marked ``cache_point``; and a user message holding the question. Every call on one
  filing shares the first two byte for byte, and the explicit cache point lets the
  provider serve them from its prompt cache (Anthropic ``cache_control``; OpenAI's
  Responses API with an explicit breakpoint, without which gpt-5.6 places its implicit
  breakpoint after the question and later questions miss). The user prompt (``v2``)
  marks the split with a ``<<cache point>>`` line
  (docs/specs/llm-anthropic-and-caching.md);
- on an Anthropic model, once the runner has planned the run (``plan_cases``), a planned
  case's filing is marked only when two or more planned cases share it, so a filing asked
  about once pays no cache write; every other case and provider keeps the mark
  (docs/specs/long-context-cache-reuse.md);
- calls on the same filing are serialized (one lock per store key), so the second and
  later questions on a filing can read the cache the first one wrote. Different filings
  run concurrently.

A case is **excluded**, never truncated: with no ticker or fiscal year (a cross-filer
screen has no single document), when no filing resolves, when the resolved key is not
in the store, or when the text exceeds ``4 * max_input_tokens`` characters, the arm
makes no call and returns ``SystemAnswer(error="excluded: ...")``.

Spec: [docs/specs/long-context-arm.md](../../../docs/specs/long-context-arm.md).
"""

from __future__ import annotations

import json
import re
import threading
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from prompts.prompts import PromptTemplate
from src.eval.core.answer_parsing import parse_structured_answer
from src.eval.core.case import EvalCase
from src.eval.core.system import SystemAnswer

# filing_text moved to src.eval.sec_tools (docs/specs/frontier-sec-tools.md, requirement 13);
# this module keeps exporting the name.
from src.eval.sec_tools.filing_text import filing_text as filing_text
from src.eval.systems._stage_llm import StageLLM, UserPart, answer_in_trace
from src.eval.systems.base import CitedFilingResolver, FilingResolver
from src.ingest.sec_edgar.storage import FilingStore, local_root
from src.llm.config import MODEL_PRICING
from src.llm.gateway import LLMGateway
from src.llm.stage_models import frontier_model
from src.logging_config import get_logger

logger = get_logger(__name__)

STAGE = "long_context"
# The default model is the active profile's frontier tier (frontier_model(),
# docs/specs/arm-model-from-profile.md); --model overrides it.
# Below both supported models' documented input limits (gpt-5.6-sol: 922K input tokens
# of a 1.05M window; Claude Opus 5.5: 1M), and below gpt-5.6-sol's long-context price
# tier (prompts over 272K input tokens are billed at 2x input / 1.5x output). The
# largest loaded filing reduces to about 184K estimated tokens (checked 2026-10-21).
DEFAULT_MAX_INPUT_TOKENS = 250_000
# The size check estimates tokens as characters / 4 (English filing text).
CHARS_PER_TOKEN = 4
EXCLUDED_PREFIX = "excluded: "

# Annual forms a canonical filing may have (the original or a full restatement).
ANNUAL_FORMS = ("10-K", "10-K/A", "20-F", "20-F/A")


# ---------------------------------------------------------------------------------
# The Postgres FilingResolver (the default).
# ---------------------------------------------------------------------------------

_RESOLVE_SQL = """
    SELECT DISTINCT fr.accession_no, fr.form_type, a.entry_url
      FROM filing_registry fr
      JOIN entity_master em ON em.cik = fr.cik
      JOIN accession a ON a.filing_accession_number = fr.accession_no
     WHERE em.trading_symbol = %s
       AND fr.fiscal_year = %s
       AND fr.fiscal_period = 'FY'
       AND fr.superseded_by IS NULL
       AND fr.form_type = ANY(%s)
     ORDER BY fr.accession_no
"""

# Each cited fact's filing: its accession (flat or multi-axis fact), the filing's form, whether
# a later amendment supersedes it, and its primary document (set-b-restatement requirement 3).
_FACT_FILINGS_SQL = """
    WITH f AS (
        SELECT fact_id, accession_no FROM mv_financial_facts
         WHERE fact_id = ANY(%(fact_ids)s)
        UNION
        SELECT fact_id, accession_no FROM mv_financial_facts_multidim
         WHERE fact_id = ANY(%(fact_ids)s)
    )
    SELECT DISTINCT ON (f.fact_id)
           f.fact_id, f.accession_no, fr.form_type,
           (fr.accession_no IS NOT NULL AND fr.superseded_by IS NULL) AS canonical,
           a.entry_url
      FROM f
      LEFT JOIN filing_registry fr ON fr.accession_no = f.accession_no
      LEFT JOIN accession a ON a.filing_accession_number = f.accession_no
     ORDER BY f.fact_id, fr.superseded_by NULLS FIRST, a.entry_url
"""

# Bounds a hung database: a connect timeout and a per-statement timeout.
_CONNECT_TIMEOUT_S = 10
_STATEMENT_TIMEOUT_MS = 30_000


def _connect_with_timeouts() -> Any:
    from src.db.conn import connect

    return connect(
        connect_timeout=_CONNECT_TIMEOUT_S,
        options=f"-c statement_timeout={_STATEMENT_TIMEOUT_MS}",
    )


def canonical_key_from_entry_url(entry_url: str | None, sec_edgar_root: str) -> str | None:
    """Maps an accession's ``entry_url`` to its ``canonical.htm`` key in the normalized tree.

    The normalized tree mirrors the raw download tree, so the key is the primary
    document's directory relative to the raw root, plus ``canonical.htm`` (as in
    ``XBRLHTMLChunkPopulator._resolve_canonical_path``). Paths are compared as text with
    ``/`` separators, so a Windows ``entry_url`` maps the same on any OS.

    Args:
        entry_url: The primary document's path, as ``accession.entry_url`` records it.
        sec_edgar_root: The raw download root the path should sit under.

    Returns:
        A key such as ``"AMD/2025/10-K_A/canonical.htm"``, or None when the path is
        missing or not under the root.
    """
    if not entry_url:
        return None
    url = entry_url.replace("\\", "/")
    root = sec_edgar_root.replace("\\", "/").rstrip("/") + "/"
    windows_like = re.match(r"^[A-Za-z]:/", root) is not None
    url_cmp, root_cmp = (url.casefold(), root.casefold()) if windows_like else (url, root)
    if not url_cmp.startswith(root_cmp):
        return None
    rel = url[len(root):].strip("/")
    directory, sep, _ = rel.rpartition("/")
    if not sep or not directory:
        return None
    return f"{directory}/canonical.htm"


def cited_filing_key(fact_ids: Sequence[int], filings: dict[int, dict[str, Any]],
                     forms: Sequence[str] = ANNUAL_FORMS) -> str | None:
    """The store key of the one canonical annual filing that holds every cited fact.

    Args:
        fact_ids: The case's cited fact ids.
        filings: Per fact id, ``{"accession_no", "form_type", "canonical", "key"}``.
        forms: The annual form types the filing may have.

    Returns:
        The filing's key, or None when the facts are not all in one such filing.

    Spec: docs/specs/set-b-restatement.md (requirement 3).
    """
    ids = {int(i) for i in fact_ids}
    if not ids or any(i not in filings for i in ids):
        return None
    held = [filings[i] for i in ids]
    accessions = {f.get("accession_no") for f in held}
    if len(accessions) != 1 or None in accessions:
        return None
    keys = {f.get("key") for f in held}
    if len(keys) != 1:
        return None
    filing = held[0]
    if filing.get("form_type") not in forms or not filing.get("canonical"):
        return None
    key = filing.get("key")
    return None if key is None else str(key)


class PostgresFilingResolver(FilingResolver, CitedFilingResolver):
    """Resolves the canonical annual filing from the fact store's Layer-0 registries.

    ``filing_registry`` joined to ``entity_master`` on ``cik``: the ticker's
    ``FY`` filing for the year that no later amendment supersedes, of an annual form.
    The key is the accession's ``entry_url`` mirrored under the normalized root, so a
    full restatement resolves to its ``10-K_A/`` directory. More than one candidate is
    ambiguous: the resolver logs it and returns None rather than guess.

    ``fact_filings`` maps cited fact ids to the filings that hold them (fact id ->
    ``mv_financial_facts`` / ``mv_financial_facts_multidim`` accession -> ``filing_registry``
    form and ``superseded_by`` -> ``accession.entry_url`` -> store key).
    """

    def __init__(
        self,
        *,
        connect: Callable[[], Any] | None = None,
        sec_edgar_root: str | None = None,
        forms: Sequence[str] = ANNUAL_FORMS,
    ) -> None:
        """Builds the resolver; opens no connection.

        Args:
            connect: Returns a new DB-API connection; ``src.db.conn.connect`` (with
                connect and statement timeouts) when omitted.
            sec_edgar_root: The raw download root ``entry_url`` sits under;
                ``local_root("sec_edgar")`` when omitted.
            forms: The annual form types that may be canonical.
        """
        self._connect = connect or _connect_with_timeouts
        self._sec_edgar_root = sec_edgar_root
        self._forms = list(forms)

    def canonical_key(self, ticker: str, fiscal_year: int) -> str | None:
        """Returns the store key of the filing's ``canonical.htm``.

        Args:
            ticker: The filer's trading symbol.
            fiscal_year: The fiscal year of the annual report.

        Returns:
            The key, or None when no single canonical annual filing exists or its path
            is not under the raw download root.
        """
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(_RESOLVE_SQL, (ticker, int(fiscal_year), self._forms))
                rows = cur.fetchall()
        finally:
            conn.close()
        if not rows:
            logger.info("long_context: no canonical annual filing for %s FY%s",
                        ticker, fiscal_year)
            return None
        if len(rows) > 1:
            logger.warning(
                "long_context: %d canonical annual filings for %s FY%s (%s); not guessing",
                len(rows), ticker, fiscal_year, ", ".join(f"{r[0]} {r[1]}" for r in rows),
            )
            return None
        accession_no, _form_type, entry_url = rows[0]
        root = self._sec_edgar_root or local_root("sec_edgar")
        key = canonical_key_from_entry_url(entry_url, root)
        if key is None:
            logger.warning(
                "long_context: %s entry_url %r is not under the download root %s",
                accession_no, entry_url, root,
            )
        return key

    def fact_filings(self, fact_ids: list[int]) -> dict[int, dict]:
        """Returns, per fact id found, the filing that holds it.

        Args:
            fact_ids: The fact ids.

        Returns:
            ``{fact_id: {"accession_no", "form_type", "canonical", "key"}}``; an unknown id
            is left out. ``key`` is None when the primary document is not under the raw
            download root.
        """
        ids = sorted({int(i) for i in fact_ids})
        if not ids:
            return {}
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(_FACT_FILINGS_SQL, {"fact_ids": ids})
                rows = cur.fetchall()
        finally:
            conn.close()
        root = self._sec_edgar_root or local_root("sec_edgar")
        return {
            int(fact_id): {
                "accession_no": accession_no,
                "form_type": form_type,
                "canonical": bool(canonical),
                "key": canonical_key_from_entry_url(entry_url, root),
            }
            for fact_id, accession_no, form_type, canonical, entry_url in rows
        }


# ---------------------------------------------------------------------------------
# The arm.
# ---------------------------------------------------------------------------------

# The line in the long_context user prompt (v2+) that ends the filing message (the
# cache point) and starts the question message.
CACHE_POINT_MARKER = "<<cache point>>"


def split_user_template(user: PromptTemplate) -> tuple[PromptTemplate, PromptTemplate]:
    """Splits the user prompt at its cache-point marker line.

    Args:
        user: The resolved ``long_context`` user template; a line holding only
            :data:`CACHE_POINT_MARKER` separates the filing part (``{filing}``) from the
            question part (``{question}``).

    Returns:
        The filing template and the question template, without the marker line.

    Raises:
        ValueError: If the marker line is missing or repeated, or ``{filing}`` and
            ``{question}`` are not on their own sides of it.
    """
    lines = user.template.splitlines()
    hits = [i for i, line in enumerate(lines) if line.strip() == CACHE_POINT_MARKER]
    if len(hits) != 1:
        raise ValueError(
            f"{user.name}: the long_context user prompt needs exactly one "
            f"{CACHE_POINT_MARKER!r} line, found {len(hits)}"
        )
    at = hits[0]
    filing = PromptTemplate("\n".join(lines[:at]), name=f"{user.name}#filing")
    question = PromptTemplate("\n".join(lines[at + 1:]), name=f"{user.name}#question")
    if filing.variables != {"filing"} or question.variables != {"question"}:
        raise ValueError(
            f"{user.name}: expected {{filing}} before and {{question}} after the "
            f"{CACHE_POINT_MARKER!r} line"
        )
    return filing, question


@dataclass(frozen=True)
class _CachePlan:
    """A run plan: the planned case ids and the filing keys planned more than once."""

    case_ids: frozenset[str]
    reused_keys: frozenset[str]


class LongContextSystem:
    """Answers each case from its own filing's full text, in one frontier-model call."""

    name = "long_context_oracle"

    def __init__(
        self,
        *,
        model: str | None = None,
        gateway: LLMGateway | None = None,
        resolver: FilingResolver | None = None,
        store: FilingStore | None = None,
        max_input_tokens: int = 250000,
    ) -> None:
        """Builds the arm; touches no network, database or store.

        Args:
            model: The exact model ID to call; it must have a ``MODEL_PRICING`` entry.
            gateway: The gateway for the ``long_context`` stage; built on first use when
                omitted.
            resolver: Finds the case's filing; the Postgres resolver on first use when
                omitted.
            store: Reads the filing; ``FilingStore.from_env(role="normalized")`` on
                first use when omitted.
            max_input_tokens: A filing whose text exceeds four characters per token of
                this is excluded, never truncated.

        Raises:
            ValueError: If the model has no ``MODEL_PRICING`` entry (the run's cost cap
                cannot work without a price), or the resolved user prompt has no
                cache-point marker line.
        """
        if model is None:
            model = frontier_model()
        if model not in MODEL_PRICING:
            raise ValueError(
                f"long_context: model {model!r} has no MODEL_PRICING entry; the run's "
                "cost cap needs a price"
            )
        self.model = model
        self.max_input_tokens = int(max_input_tokens)
        self._llm = StageLLM(STAGE, gateway, model=model)
        self._filing_template, self._question_template = split_user_template(
            self._llm.prompt.user
        )
        self._resolver = resolver
        self._store = store
        self._lock = threading.Lock()
        self._filing_locks: dict[str, threading.Lock] = {}
        # The last plan (docs/specs/long-context-cache-reuse.md): the planned case ids and
        # the keys planned for two or more of them. Replaced whole, never mutated, so a
        # concurrent answer reads either the old plan or the new one.
        self._plan: _CachePlan | None = None

    # ------------------------------------------------------------------
    # Lazily built backends (double-checked locking: answer() runs concurrently).
    # ------------------------------------------------------------------

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

    def _filing_lock(self, key: str) -> threading.Lock:
        """The lock that serializes provider calls on one filing."""
        with self._lock:
            return self._filing_locks.setdefault(key, threading.Lock())

    # ------------------------------------------------------------------

    def answer(self, case: EvalCase) -> SystemAnswer:
        """Answers one case from its filing's text.

        Args:
            case: The eval case; needs a ticker and a fiscal year.

        Returns:
            The parsed answer, or an ``"excluded: ..."`` error when no single filing
            fits; with its trace id, cost and latency.

        Raises:
            RunHalt: The run's budget or circuit breaker stopped the call.
        """
        return answer_in_trace(case.question, lambda: self._answer(case))

    def plan_cases(self, cases: Sequence[EvalCase]) -> None:
        """Records which filings the run will ask about more than once.

        Called by the eval runner once per run, before any case is answered, with the cases
        this arm will answer, in suite order. On an Anthropic model only a filing planned for
        two or more cases is marked as a cache point; every other provider, and every case
        outside the plan, keeps the mark.

        Args:
            cases: The cases this arm will be asked to answer in this run.

        Spec: docs/specs/long-context-cache-reuse.md (requirement 2).
        """
        self._plan = None  # a failed call leaves no plan, not a partial one
        planned: set[str] = set()
        counts: Counter[str] = Counter()
        for case in cases:
            if not case.ticker or case.fy is None:
                continue
            key = self._document_key(case)
            if key is None:
                continue
            planned.add(case.id)
            counts[key] += 1
        reused = frozenset(key for key, n in counts.items() if n >= 2)
        self._plan = _CachePlan(case_ids=frozenset(planned), reused_keys=reused)

    def _cache_point(self, case: EvalCase, key: str) -> bool:
        """Whether the case's filing message is marked as a cache point.

        Always on outside Anthropic, and for a case outside the last plan; on Anthropic a
        planned case is marked only when its filing is planned for two or more cases.
        """
        plan = self._plan
        if self._llm.provider != "anthropic" or plan is None or case.id not in plan.case_ids:
            return True
        return key in plan.reused_keys

    def _document_key(self, case: EvalCase) -> str | None:
        """The store key of the case's oracle document.

        The single canonical annual filing holding every cited fact (S1 may cite a later
        filing than the one labelled with the case's year), when the resolver can map fact
        ids to filings; else the canonical annual filing labelled with the case's year.

        Spec: docs/specs/set-b-restatement.md (requirement 3).
        """
        resolver = self._get_resolver()
        if isinstance(resolver, CitedFilingResolver) and case.expected_fact_ids:
            ids = [int(i) for i in case.expected_fact_ids]
            key = cited_filing_key(ids, resolver.fact_filings(ids))
            if key is not None:
                return key
        return resolver.canonical_key(str(case.ticker), int(case.fy or 0))

    def _answer(self, case: EvalCase) -> SystemAnswer:
        if not case.ticker or case.fy is None:
            return _excluded(f"case {case.id} has no single filing (ticker or fiscal year "
                             "missing)")
        key = self._document_key(case)
        if key is None:
            return _excluded(f"no canonical annual filing for {case.ticker} FY{case.fy}")
        store = self._get_store()
        try:
            if not store.exists(key):
                return _excluded(f"{key} is not in the filing store")
            html = store.read_text(key)
        except FileNotFoundError:
            return _excluded(f"{key} is not in the filing store")

        text = filing_text(html)
        limit = CHARS_PER_TOKEN * self.max_input_tokens
        if len(text) > limit:
            return _excluded(
                f"{key} text is {len(text):,} characters, over the {limit:,}-character "
                f"limit ({self.max_input_tokens:,} tokens)"
            )

        # Filing first (a cache point), question after: calls on one filing share the
        # system message and the filing message byte for byte.
        parts = [
            UserPart(
                self._filing_template.format(filing=json.dumps(text, ensure_ascii=False)),
                cache_point=self._cache_point(case, key),
            ),
            UserPart(self._question_template.format(question=case.question)),
        ]
        with self._filing_lock(key):
            reply = self._llm.complete(parts)
        answer = parse_structured_answer(reply)
        answer.fact_ids = []
        answer.chunk_ids = []
        return answer


def _excluded(reason: str) -> SystemAnswer:
    """An excluded case: no call, no numbers, an ``"excluded: "`` error."""
    logger.info("long_context: excluded: %s", reason)
    return SystemAnswer(error=EXCLUDED_PREFIX + reason)
