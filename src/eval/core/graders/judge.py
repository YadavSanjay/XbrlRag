"""A calibrated, claim-by-claim LLM judge for text answers.

The numeric graders cannot score a text answer. ``ClaimJudge`` asks one judge model,
through the :class:`~src.llm.gateway.LLMGateway` at stage ``eval_judge``, whether each of
an answer's claims agrees with a reference answer and is supported by its own cited
passages, and which of the reference's key points the answer covers. The sub-scores are
computed here from those verdicts, never by the model; a malformed reply is a
``judge_error``, never a score. The judge is never from the generator's model family.

A judge version's scores may be reported only after it agrees with human labels:
``calibration_report`` measures Cohen's kappa against them and ``scores_reportable`` is
the gate. Which judge model to use is configuration (``configs/eval/judge.yaml``, read by
``load_judge_settings``), not code.

Spec: [docs/specs/eval-judge.md](../../../../docs/specs/eval-judge.md).
"""

from __future__ import annotations

import json
import threading
from collections import Counter
from collections.abc import Hashable, Sequence
from fractions import Fraction
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from prompts.prompt_registry import ResolvedPrompt, get_eval_registry
from src import PROJECT_ROOT
from src.llm.gateway import LLMGateway, call_params, extract_json, resolve_model_ref
from src.llm.profiles import DEFAULT_PROFILE, load_committed_settings
from src.llm.run_budget import RunHalt
from src.llm.xbrl_gateway import build_xbrl_gateway
from src.logging_config import get_logger

logger = get_logger(__name__)

JUDGE_STAGE = "eval_judge"
JUDGE_CONFIG_FILE = Path(PROJECT_ROOT) / "configs" / "eval" / "judge.yaml"

_JUDGE_ERROR = "judge_error: "
_MAX_COMPLETION_TOKENS = 4000
_TEMPERATURE = 0.0


def model_family(model: str) -> str:
    """The model family a model id belongs to.

    Args:
        model: A model id with a row in ``configs/llm/llm.yaml``.

    Returns:
        The provider that row names (``"openai"``, ``"anthropic"``, ``"deepseek"``).

    Raises:
        ValueError: If the model has no row, so its family can't be checked.
    """
    # The family is per model, whatever profile is active; pin one so a profile setting
    # cannot change the answer.
    settings = load_committed_settings(profile=DEFAULT_PROFILE)
    if not settings.has_model(model):
        raise ValueError(
            f"model {model!r} has no row in configs/llm/llm.yaml, so its family is unknown"
        )
    return settings.provider_for(model)


def judge_version(prompt_version: str, model: str) -> str:
    """The judge's version stamp: ``f"{prompt_version}:{model}"``."""
    return f"{prompt_version}:{model}"


class JudgeSettings(BaseModel):
    """The judge models of ``configs/eval/judge.yaml``.

    Attributes:
        prompt_version: The ``eval_judge`` prompt version.
        development_model: The judge used during development.
        milestone_models: Second judges used at milestones.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    prompt_version: str
    development_model: str
    milestone_models: tuple[str, ...]


def load_judge_settings(path: Path | str = JUDGE_CONFIG_FILE) -> JudgeSettings:
    """Loads and validates the judge configuration.

    Args:
        path: The YAML file; ``configs/eval/judge.yaml`` by default.

    Returns:
        The validated settings.

    Raises:
        ValueError: If a key is unknown or missing, a value has the wrong type, or a
            model has no row in ``configs/llm/llm.yaml``.
    """
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if isinstance(raw, dict) and isinstance(raw.get("milestone_models"), list):
        raw = {**raw, "milestone_models": tuple(raw["milestone_models"])}
    try:
        settings = JudgeSettings.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"invalid judge config {path}: {exc}") from exc
    for model in (settings.development_model, *settings.milestone_models):
        model_family(model)
    return settings


class Claim:
    """One claim of an answer, with the text of the passages it cites."""

    def __init__(self, claim_id: str, text: str, cited_passages: Sequence[str]) -> None:
        """Builds a claim.

        Args:
            claim_id: Unique within one answer.
            text: The claim as the answer states it.
            cited_passages: The text of each passage the claim cites, in order.
        """
        self.claim_id = claim_id
        self.text = text
        self.cited_passages = tuple(cited_passages)

    def __repr__(self) -> str:
        return (
            f"Claim(claim_id={self.claim_id!r}, text={self.text!r}, "
            f"cited_passages={list(self.cited_passages)!r})"
        )


class ClaimVerdict:
    """The final verdict on one claim (after the deterministic overrides)."""

    def __init__(self, claim_id: str, correct: bool, supported: bool) -> None:
        """Builds a verdict.

        Args:
            claim_id: The claim's id.
            correct: Whether the claim agrees with the reference answer.
            supported: Whether the claim's own cited passages support it.
        """
        self.claim_id = claim_id
        self.correct = correct
        self.supported = supported

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ClaimVerdict):
            return NotImplemented
        return (self.claim_id, self.correct, self.supported) == (
            other.claim_id, other.correct, other.supported
        )

    def __hash__(self) -> int:
        return hash((self.claim_id, self.correct, self.supported))

    def __repr__(self) -> str:
        return (
            f"ClaimVerdict(claim_id={self.claim_id!r}, correct={self.correct!r}, "
            f"supported={self.supported!r})"
        )


class JudgeResult:
    """The judge's scores for one answer, or a ``judge_error``."""

    def __init__(
        self,
        *,
        judge_version: str,
        verdicts: Sequence[ClaimVerdict],
        correctness: float | None,
        completeness: float | None,
        citation_support: float | None,
        unsupported_claims: int | None,
        error: str | None = None,
    ) -> None:
        """Builds a result.

        Args:
            judge_version: The stamp of the judge that produced it.
            verdicts: One verdict per claim, in the claims' order; empty on error.
            correctness: Share of claims that agree with the reference; None on error.
            completeness: Share of the reference's key points covered; None on error.
            citation_support: Share of claims supported by their own cited passages;
                None on error.
            unsupported_claims: Number of claims not so supported; None on error.
            error: ``"judge_error: ..."`` when the judge could not be scored.
        """
        self.judge_version = judge_version
        self.verdicts = tuple(verdicts)
        self.correctness = correctness
        self.completeness = completeness
        self.citation_support = citation_support
        self.unsupported_claims = unsupported_claims
        self.error = error

    def __repr__(self) -> str:
        return (
            f"JudgeResult(judge_version={self.judge_version!r}, "
            f"correctness={self.correctness!r}, completeness={self.completeness!r}, "
            f"citation_support={self.citation_support!r}, "
            f"unsupported_claims={self.unsupported_claims!r}, error={self.error!r})"
        )


class _MalformedReplyError(ValueError):
    """The judge's reply is not exactly the requested object."""


def _parse_reply(
    content: str, claim_ids: Sequence[str], k: int
) -> tuple[dict[str, tuple[bool, bool]], list[bool]]:
    """Reads the judge's reply object (requirement 3) or raises ``_MalformedReplyError``.

    Args:
        content: The model's reply text.
        claim_ids: The input claim ids.
        k: The number of key points.

    Returns:
        ``({claim_id: (correct, supported)}, covered by key-point index)``.

    Raises:
        _MalformedReplyError: When the reply is not exactly the requested object.
    """
    try:
        obj = extract_json(content)
    except (json.JSONDecodeError, ValueError) as exc:
        raise _MalformedReplyError(f"no JSON object in the reply ({exc})") from exc
    if not isinstance(obj, dict):
        raise _MalformedReplyError("the reply is not a JSON object")
    claims = obj.get("claims")
    points = obj.get("key_points")
    if not isinstance(claims, list):
        raise _MalformedReplyError("'claims' missing or not a list")
    if not isinstance(points, list):
        raise _MalformedReplyError("'key_points' missing or not a list")

    expected_ids = set(claim_ids)
    verdicts: dict[str, tuple[bool, bool]] = {}
    for entry in claims:
        if not isinstance(entry, dict):
            raise _MalformedReplyError("a 'claims' entry is not an object")
        cid = entry.get("id")
        if not isinstance(cid, str) or cid not in expected_ids:
            raise _MalformedReplyError(f"unexpected claim id {cid!r}")
        if cid in verdicts:
            raise _MalformedReplyError(f"claim id {cid!r} appears more than once")
        correct, supported = entry.get("correct"), entry.get("supported")
        if not (isinstance(correct, bool) and isinstance(supported, bool)):
            raise _MalformedReplyError(f"claim {cid!r}: 'correct'/'supported' not booleans")
        verdicts[cid] = (correct, supported)
    if len(verdicts) != len(expected_ids):
        missing = sorted(expected_ids - set(verdicts))
        raise _MalformedReplyError(f"claim ids missing from the reply: {missing}")

    covered: dict[int, bool] = {}
    for entry in points:
        if not isinstance(entry, dict):
            raise _MalformedReplyError("a 'key_points' entry is not an object")
        index = entry.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < k:
            raise _MalformedReplyError(f"unexpected key-point index {index!r}")
        if index in covered:
            raise _MalformedReplyError(f"key-point index {index} appears more than once")
        value = entry.get("covered")
        if not isinstance(value, bool):
            raise _MalformedReplyError(f"key point {index}: 'covered' not a boolean")
        covered[index] = value
    if len(covered) != k:
        missing_points = sorted(set(range(k)) - set(covered))
        raise _MalformedReplyError(f"key points missing from the reply: {missing_points}")
    return verdicts, [covered[i] for i in range(k)]


class ClaimJudge:
    """Scores an answer claim by claim against a reference, through the LLMGateway."""

    def __init__(
        self,
        *,
        model: str,
        generator_model: str,
        gateway: LLMGateway | None = None,
        prompt_version: str = "v1",
    ) -> None:
        """Builds the judge.

        Args:
            model: The judge model id.
            generator_model: The model of the system whose answers are judged.
            gateway: The gateway for the ``eval_judge`` stage; built on first use when
                omitted.
            prompt_version: The ``eval_judge`` prompt version.

        Raises:
            ValueError: If either model's family is unknown, or the two are the same.
        """
        family = model_family(model)
        generator_family = model_family(generator_model)
        if family == generator_family:
            raise ValueError(
                f"judge {model!r} and generator {generator_model!r} are both "
                f"{family!r}; the judge must come from another model family"
            )
        self.model = model
        self.generator_model = generator_model
        self.prompt_version = prompt_version
        self._model_ref = resolve_model_ref(model)
        self._prompt: ResolvedPrompt = get_eval_registry().resolve(
            JUDGE_STAGE,
            provider=self._model_ref.provider,
            series=self._model_ref.series,
            version=prompt_version,
        )
        self._prompt_sig = self._prompt.signature()
        self._gateway = gateway
        self._lock = threading.Lock()

    @property
    def judge_version(self) -> str:
        """``judge_version(prompt_version, model)`` for this judge."""
        return judge_version(self.prompt_version, self.model)

    def _get_gateway(self) -> LLMGateway:
        """Returns the gateway, building the default one on first use."""
        if self._gateway is None:
            with self._lock:
                if self._gateway is None:
                    self._gateway = build_xbrl_gateway(JUDGE_STAGE)
        return self._gateway

    def _error(self, reason: str) -> JudgeResult:
        return JudgeResult(
            judge_version=self.judge_version,
            verdicts=[],
            correctness=None,
            completeness=None,
            citation_support=None,
            unsupported_claims=None,
            error=_JUDGE_ERROR + reason,
        )

    def _complete(self, payload: str) -> str:
        """Sends the system prompt and the user message holding *payload*; returns the reply."""
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._prompt.system.format()},
            # The payload is a format *argument*, so braces in it are inert.
            {"role": "user", "content": self._prompt.user.format(payload=payload)},
        ]
        params = call_params(
            self._model_ref.config,
            max_completion_tokens=_MAX_COMPLETION_TOKENS,
            temperature=_TEMPERATURE,
        )
        temperature = params["temperature"]
        result = self._get_gateway().complete(
            messages=messages,
            model_ref=self._model_ref,
            stage=JUDGE_STAGE,
            max_completion_tokens=params["max_completion_tokens"],
            temperature=None if temperature is None else float(temperature),
            response_format="json",
            prompt_templates=self._prompt_sig["templates"],
            prompt_version=self._prompt_sig["version"],
        )
        return result.content

    def reply_verdicts(
        self,
        question: str,
        reference_answer: str,
        key_points: Sequence[str],
        claims: Sequence[Claim],
    ) -> tuple[dict[str, tuple[bool, bool]], list[bool]] | str:
        """Makes one judge call and returns the reply's raw verdicts.

        The verdicts are the model's, before the deterministic overrides ``judge``
        applies; ``judge`` and the calibration code (docs/specs/judge-calibration.md)
        both read the reply through this method, so they see one call the same way.

        Args:
            question: The question asked.
            reference_answer: The reference answer.
            key_points: The reference's key points.
            claims: The answer's claims (at least one), with distinct ids.

        Returns:
            ``({claim_id: (correct, supported)}, covered by key-point index)``, or the
            reason (without the ``"judge_error: "`` prefix) when the call failed or the
            reply was malformed.

        Raises:
            RunHalt: When the run's budget or circuit breaker stops the call.
        """
        # Untrusted text travels only as JSON data inside the user message.
        payload = json.dumps(
            {
                "question": question,
                "reference_answer": reference_answer,
                "key_points": [{"index": i, "text": p} for i, p in enumerate(key_points)],
                "claims": [
                    {"id": c.claim_id, "text": c.text, "cited_passages": list(c.cited_passages)}
                    for c in claims
                ],
            }
        )
        try:
            content = self._complete(payload)
        except RunHalt:
            raise
        except Exception as exc:  # isolation point: one failed call must not abort a run
            logger.warning("eval judge call failed: %r", exc)
            return f"call failed: {exc!r}"

        try:
            return _parse_reply(content, [c.claim_id for c in claims], len(key_points))
        except _MalformedReplyError as exc:
            logger.warning("eval judge reply malformed: %s", exc)
            return f"malformed reply: {exc}"

    def judge(
        self,
        question: str,
        reference_answer: str,
        key_points: Sequence[str],
        claims: Sequence[Claim],
    ) -> JudgeResult:
        """Judges one answer's claims.

        Args:
            question: The question asked.
            reference_answer: The reference answer.
            key_points: The reference's key points (at least one).
            claims: The answer's claims with their cited passages.

        Returns:
            The verdicts and the four sub-scores, or a ``judge_error`` result.

        Raises:
            ValueError: If ``key_points`` is empty or two claims share an id.
            RunHalt: When the run's budget or circuit breaker stops the call.
        """
        if not key_points:
            raise ValueError("key_points must not be empty")
        claim_ids = [c.claim_id for c in claims]
        duplicates = sorted(cid for cid, count in Counter(claim_ids).items() if count > 1)
        if duplicates:
            raise ValueError(f"duplicate claim ids: {duplicates}")
        if not claims:
            # An empty answer asserts nothing: neither correct nor complete.
            return JudgeResult(
                judge_version=self.judge_version,
                verdicts=[],
                correctness=0.0,
                completeness=0.0,
                citation_support=0.0,
                unsupported_claims=0,
            )

        outcome = self.reply_verdicts(question, reference_answer, key_points, claims)
        if isinstance(outcome, str):
            return self._error(outcome)
        reply, covered = outcome

        verdicts = []
        for c in claims:
            correct, supported = reply[c.claim_id]
            # A claim that cites nothing is unsupported, whatever the judge says.
            verdicts.append(ClaimVerdict(c.claim_id, correct, supported and bool(c.cited_passages)))
        n = len(verdicts)
        n_supported = sum(1 for v in verdicts if v.supported)
        return JudgeResult(
            judge_version=self.judge_version,
            verdicts=verdicts,
            correctness=sum(1 for v in verdicts if v.correct) / n,
            completeness=sum(covered) / len(covered),
            citation_support=n_supported / n,
            unsupported_claims=n - n_supported,
        )


def cohen_kappa(labels_a: Sequence[Hashable], labels_b: Sequence[Hashable]) -> float:
    """Cohen's kappa between two raters' labels of the same items.

    kappa = (p_o - p_e) / (1 - p_e), with p_o the observed agreement and
    p_e = sum_k p_a(k) * p_b(k) (Cohen 1960). Computed in exact fractions.

    Raises:
        ValueError: If the sequences differ in length, are empty, or the expected
            agreement is 1 (kappa undefined).
    """
    n = len(labels_a)
    if n != len(labels_b):
        raise ValueError(f"label sequences differ in length: {n} vs {len(labels_b)}")
    if n == 0:
        raise ValueError("no labels")
    agree = sum(1 for a, b in zip(labels_a, labels_b, strict=True) if a == b)
    p_o = Fraction(agree, n)
    counts_a, counts_b = Counter(labels_a), Counter(labels_b)
    p_e = Fraction(sum(counts_a[k] * counts_b[k] for k in counts_a), n * n)
    if p_e == 1:
        raise ValueError(
            "expected agreement is 1 (both raters used one same label); kappa undefined"
        )
    return float((p_o - p_e) / (1 - p_e))


class CalibrationReport:
    """The judge's agreement with human labels, for one judge version."""

    def __init__(
        self,
        *,
        judge_version: str,
        n: int,
        kappa: float,
        threshold: float,
        min_items: int,
        passed: bool,
    ) -> None:
        """Builds a report (see ``calibration_report``)."""
        self.judge_version = judge_version
        self.n = n
        self.kappa = kappa
        self.threshold = threshold
        self.min_items = min_items
        self.passed = passed

    def __repr__(self) -> str:
        return (
            f"CalibrationReport(judge_version={self.judge_version!r}, n={self.n}, "
            f"kappa={self.kappa!r}, threshold={self.threshold!r}, "
            f"min_items={self.min_items}, passed={self.passed})"
        )


def calibration_report(
    judge_labels: Sequence[Hashable],
    human_labels: Sequence[Hashable],
    *,
    judge_version: str,
    threshold: float = 0.7,
    min_items: int = 100,
) -> CalibrationReport:
    """Measures a judge version against human labels of the same items.

    ``passed`` is ``n >= min_items and kappa >= threshold``.

    Raises:
        ValueError: As ``cohen_kappa``.
    """
    kappa = cohen_kappa(judge_labels, human_labels)
    n = len(judge_labels)
    return CalibrationReport(
        judge_version=judge_version,
        n=n,
        kappa=kappa,
        threshold=threshold,
        min_items=min_items,
        passed=n >= min_items and kappa >= threshold,
    )


def scores_reportable(judge_version: str, reports: Sequence[CalibrationReport]) -> bool:
    """Whether a judge version's scores may be reported.

    True only when the last report in *reports* for that judge version passed.
    """
    for report in reversed(reports):
        if report.judge_version == judge_version:
            return report.passed
    return False
