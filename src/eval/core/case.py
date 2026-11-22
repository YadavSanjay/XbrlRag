"""WS-9 / E1 — the labelled-case type every eval suite produces.

Generalises WS-2's :class:`~src.eval.xbrl.golden.GoldenCase` along the four axes
that type cannot express: **ranked-row** answers (``screen``), **fact-level**
grounding truth, **paraphrase** grouping, and corpus **coverage**. Design:
[docs/design/XbrlRag_Evaluation.md](../../../docs/design/XbrlRag_Evaluation.md).

A suite is plain JSON on disk (``test/eval/suites/*.json``) so a generated set is
reviewable in a diff and reproducible in CI — the same convention as the WS-2
golden set.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

# Question kinds. `lookup`/`compute`/`dimensional` grade to a scalar, `screen` to a
# ranked row list, `narrative` to prose (deterministic chunk-recall only until the
# Phase-3 LLM judge lands).
KIND_LOOKUP = "lookup"
KIND_COMPUTE = "compute"
KIND_DIMENSIONAL = "dimensional"
KIND_SCREEN = "screen"
KIND_BREAKDOWN = "breakdown"
KIND_NARRATIVE = "narrative"

SCALAR_KINDS = frozenset({KIND_LOOKUP, KIND_COMPUTE, KIND_DIMENSIONAL})
# `screen` and `breakdown` both have multi-row ground truth but the pipeline answers
# them differently, so they grade differently:
#   screen    — CROSS-FILER ranking. One `screen` step, rows ranked, order matters.
#   breakdown — ONE filer split by segment/geography. The planner emits a separate
#               `lookup` per component, so there is no `screen` step and no ranking
#               to check; what matters is whether the right component VALUES (and
#               their facts) were resolved. Grading these as screens scored a
#               correct answer as a refusal.
MULTI_ROW_KINDS = frozenset({KIND_SCREEN, KIND_BREAKDOWN})
KINDS = frozenset(SCALAR_KINDS | MULTI_ROW_KINDS | {KIND_NARRATIVE})

# The v2 / H-X keys EvalCase carries verbatim (docs/specs/v2-grading.md requirement 1):
# read when present (copied), written back when not None and not empty.
_V2_KEYS = (
    "universe",
    "expected_exclusions",
    "accessions",
    "passages",
    "assumptions",
    "decline_reason",
    "parts",
    "tickers",
    "key",
    "disclosures",
    "subject",
    "alt_readings",
)


class Coverage(str, Enum):
    """Whether the corpus can answer this case at all.

    External suites (Phase 4) ask about filings we have never ingested. A report
    must publish coverage alongside the score — silently dropping uncovered cases
    is how an inflated, indefensible number gets produced.
    """

    IN_CORPUS = "IN_CORPUS"
    OUT_OF_CORPUS = "OUT_OF_CORPUS"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


@dataclass
class EvalCase:
    """One labelled question + its ground truth + how to grade it."""

    id: str
    question: str
    suite: str = ""
    kind: str = KIND_LOOKUP

    # --- ground truth: exactly one of value / rows / text is authoritative ---
    expected_value: float | None = None
    expected_rows: list[dict] | None = None  # screens: ordered ranking
    expected_text: str | None = None  # narrative: gold answer
    # The reference answer's key points, for the claim judge (empty: not judged).
    key_points: list[str] = field(default_factory=list)
    expected_fact_ids: list[int] = field(default_factory=list)
    # One equivalence class per expected fact: every fact_id that carries the *same*
    # value for the same concept/period in the same filing. A filing routinely tags
    # one number on several rows (different presentation roles) — AAPL FY2026 revenue
    # is facts 15488, 15811 and 16207 — so demanding a specific surrogate id measures
    # row-selection convention rather than whether the answer is sourced correctly.
    # Empty (the default) means "each expected fact is its own class".
    fact_id_classes: list[list[int]] = field(default_factory=list)
    expected_chunk_ids: list[str] = field(default_factory=list)

    # --- grading policy ---
    rel_tol: float = 1e-4
    alt_values: list[float] = field(default_factory=list)
    # Disclosed alternate readings of the key, each graded on its own and named in the
    # grade (docs/specs/shares-outstanding-reading.md): {"reading", "value", "concept",
    # "period_end", "accession"}. Never part of targets().
    alt_readings: list[dict] = field(default_factory=list)
    require_citation: bool = True
    require_exact_fact: bool = False  # strict grounding: produced set == expected set
    order_sensitive: bool = True  # screens: does rank order matter
    top_n: int | None = None
    # A percentage change whose base is <= 0: the right answer is "n/m", not a number.
    # expected_value still carries the |base|-convention ratio so the grader knows
    # which numbers would be a percentage claim (docs/specs/pct-change-nm.md).
    expect_not_meaningful: bool = False

    # --- provenance & slicing ---
    origin: str = ""  # "analyst_queries.sql#7a" | "financebench:fb-042"
    paraphrase_group: str | None = None
    ticker: str | None = None
    cik: int | None = None
    fy: int | None = None
    fiscal_period: str = "FY"
    concept: str | None = None
    unit: str | None = None
    tags: list[str] = field(default_factory=list)
    coverage: Coverage = Coverage.IN_CORPUS

    # --- v2 / H-X case specifics (docs/specs/v2-grading.md), field name = JSON key ---
    # H-X (hx-authoring): the cross-filer universe and its expected exclusions, the
    # point-in-time accessions, text passages, honesty assumptions / decline reason,
    # compound parts and the tickers a question names.
    universe: list[str] = field(default_factory=list)
    expected_exclusions: list[dict] = field(default_factory=list)
    accessions: dict | None = None
    passages: list[dict] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    decline_reason: str | None = None
    parts: list[dict] = field(default_factory=list)
    tickers: list[str] = field(default_factory=list)
    # v2 suite (v2-suite-build): the answer key, the S2/S3 disclosures and a
    # peer-relative subject.
    key: dict | None = None
    disclosures: dict | None = None
    subject: dict | None = None

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(
                f"unknown case kind {self.kind!r} (expected one of {sorted(KINDS)})"
            )
        if isinstance(self.coverage, str):
            self.coverage = Coverage(self.coverage)

    # ------------------------------------------------------------------
    @property
    def is_scalar(self) -> bool:
        return self.kind in SCALAR_KINDS

    def grounding_classes(self) -> list[list[int]]:
        """Accepted fact sets, one per expected fact. Falls back to singletons so a
        case built before equivalence classes existed still grades correctly."""
        if self.fact_id_classes:
            return [list(c) for c in self.fact_id_classes]
        return [[f] for f in self.expected_fact_ids]

    def targets(self) -> list[float]:
        """Accepted scalar answers — the expected value plus any alternate forms
        (e.g. a ratio expressed as 0.75 or 75.0)."""
        if self.expected_value is None:
            return list(self.alt_values)
        return [self.expected_value, *self.alt_values]

    # ------------------------------------------------------------------
    @classmethod
    def from_dict(cls, d: dict, *, suite: str = "") -> EvalCase:
        return cls(
            id=d["id"],
            question=d.get("question") or d["query"],  # `query` = GoldenCase spelling
            suite=d.get("suite") or suite,
            kind=d.get("kind", KIND_LOOKUP),
            expected_value=(
                None if d.get("expected_value") is None else float(d["expected_value"])
            ),
            expected_rows=d.get("expected_rows"),
            expected_text=d.get("expected_text"),
            key_points=[str(p) for p in d.get("key_points") or []],
            expected_fact_ids=[int(f) for f in d.get("expected_fact_ids", [])],
            fact_id_classes=[
                [int(f) for f in group] for group in d.get("fact_id_classes", [])
            ],
            expected_chunk_ids=[str(c) for c in d.get("expected_chunk_ids", [])],
            rel_tol=float(d.get("rel_tol", 1e-4)),
            alt_values=[float(v) for v in d.get("alt_values", [])],
            require_citation=bool(d.get("require_citation", True)),
            require_exact_fact=bool(d.get("require_exact_fact", False)),
            order_sensitive=bool(d.get("order_sensitive", True)),
            top_n=d.get("top_n"),
            expect_not_meaningful=bool(d.get("expect_not_meaningful", False)),
            origin=d.get("origin", ""),
            paraphrase_group=d.get("paraphrase_group"),
            ticker=d.get("ticker"),
            cik=(None if d.get("cik") is None else int(d["cik"])),
            fy=(None if d.get("fy") is None else int(d["fy"])),
            fiscal_period=d.get("fiscal_period", "FY"),
            concept=d.get("concept"),
            unit=d.get("unit"),
            tags=list(d.get("tags", [])),
            coverage=Coverage(d.get("coverage", Coverage.IN_CORPUS.value)),
            **{k: copy.deepcopy(d[k]) for k in _V2_KEYS if d.get(k) is not None},
        )

    def to_dict(self) -> dict:
        """Round-trips through :meth:`from_dict`. Defaults are omitted to keep a
        generated suite file readable in review."""
        d: dict[str, Any] = {
            "id": self.id,
            "question": self.question,
            "kind": self.kind,
        }
        if self.suite:
            d["suite"] = self.suite
        for key in (
            "expected_value",
            "expected_rows",
            "expected_text",
            "top_n",
            "paraphrase_group",
            "ticker",
            "cik",
            "fy",
            "concept",
            "unit",
        ):
            val = getattr(self, key)
            if val is not None:
                d[key] = val
        for key in (
            "expected_fact_ids",
            "fact_id_classes",
            "expected_chunk_ids",
            "alt_values",
            "tags",
            "key_points",
        ):
            val = getattr(self, key)
            if val:
                d[key] = val
        if self.origin:
            d["origin"] = self.origin
        if self.fiscal_period != "FY":
            d["fiscal_period"] = self.fiscal_period
        d["rel_tol"] = self.rel_tol
        if not self.require_citation:
            d["require_citation"] = False
        if self.require_exact_fact:
            d["require_exact_fact"] = True
        if not self.order_sensitive:
            d["order_sensitive"] = False
        if self.expect_not_meaningful:
            d["expect_not_meaningful"] = True
        if self.coverage is not Coverage.IN_CORPUS:
            d["coverage"] = self.coverage.value
        for key in _V2_KEYS:
            val = getattr(self, key)
            if val is not None and val != [] and val != {} and val != "":
                d[key] = val
        return d


@dataclass
class EvalSuite:
    """A named, versioned set of cases plus the corpus state it was built against."""

    name: str
    version: str
    cases: list[EvalCase] = field(default_factory=list)
    generated_at: str | None = None
    corpus_fingerprint: str | None = None
    description: str = ""

    def __len__(self) -> int:
        return len(self.cases)

    @property
    def qualified_name(self) -> str:
        return f"{self.name}_{self.version}" if self.version else self.name

    def by_kind(self) -> dict[str, list[EvalCase]]:
        out: dict[str, list[EvalCase]] = {}
        for c in self.cases:
            out.setdefault(c.kind, []).append(c)
        return out

    def filter(
        self,
        *,
        kinds: list[str] | None = None,
        tags: list[str] | None = None,
        tickers: list[str] | None = None,
        coverage: Coverage | None = None,
    ) -> EvalSuite:
        """A narrowed copy — how `--kind` / `--tag` CLI selection is applied."""
        cases = self.cases
        if kinds:
            cases = [c for c in cases if c.kind in set(kinds)]
        if tags:
            wanted = set(tags)
            cases = [c for c in cases if wanted & set(c.tags)]
        if tickers:
            up = {t.upper() for t in tickers}
            cases = [c for c in cases if (c.ticker or "").upper() in up]
        if coverage is not None:
            cases = [c for c in cases if c.coverage is coverage]
        return EvalSuite(
            name=self.name,
            version=self.version,
            cases=list(cases),
            generated_at=self.generated_at,
            corpus_fingerprint=self.corpus_fingerprint,
            description=self.description,
        )

    # ------------------------------------------------------------------
    @classmethod
    def from_dict(cls, d: dict) -> EvalSuite:
        name = d.get("name") or d.get("suite") or "suite"
        version = str(d.get("version", ""))
        # Tolerate the WS-2 golden-set shape: {"version": "xbrl_golden_v1", "cases": [...]}
        if (
            version
            and not d.get("name")
            and version.endswith(tuple(f"_v{i}" for i in range(10)))
        ):
            name, _, version = version.rpartition("_")
        return cls(
            name=name,
            version=version,
            cases=[
                EvalCase.from_dict(c, suite=f"{name}_{version}" if version else name)
                for c in d.get("cases", [])
            ],
            generated_at=d.get("generated_at"),
            corpus_fingerprint=d.get("corpus_fingerprint"),
            description=d.get("description", ""),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "generated_at": self.generated_at,
            "corpus_fingerprint": self.corpus_fingerprint,
            "n_cases": len(self.cases),
            "cases": [c.to_dict() for c in self.cases],
        }

    @classmethod
    def load(cls, path: str) -> EvalSuite:
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
            f.write("\n")
