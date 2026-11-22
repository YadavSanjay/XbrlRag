"""Fixtures and independent oracles for the eval-paraphrases spec tests.

Spec: docs/specs/eval-paraphrases.md. Everything under test is pure apart from the reply-file
reads (the committed files under ``replies/``, or files a test writes to a temporary
directory) and the set file round-trip. The four synthetic sources below are the ones the
committed reply files paraphrase; ``ParaphraseSource`` itself is constructed inside each test
(never at import time), so a stub fails the test, not its collection. The oracles restate the
spec's rules with ``hashlib``, ``json`` and ``math``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
REPLY_A = HERE / "replies" / "reply_a.txt"
REPLY_B = HERE / "replies" / "reply_b.txt"

PERSONAS = ("sell_side_shorthand", "terse_pm", "conversational_retail", "typo_prone",
            "buried_ask")

AAPL = ("AAPL", "Apple", "Apple Inc.")
MSFT = ("MSFT", "Microsoft", "Microsoft Corporation")
UNIVERSES_OTHER = ("covered companies", "S&P 100")

S1, S2, S3, S4 = ("int-aaaaaaaaaaaa-11111111", "int-aaaaaaaaaaaa-22222222",
                  "int-bbbbbbbbbbbb-33333333", "int-cccccccccccc-44444444")


def source_kwargs() -> list[dict]:
    """Keyword arguments of the four fixture sources (synthetic, hand-written)."""
    revenue_aapl = ("Revenues", "net sales", "revenue", "top line")
    return [
        dict(case_id=S1, question="What was Apple's net sales in fiscal 2024?",
             intent_id="int-aaaaaaaaaaaa", intent_axis="dev", template_axis="dev",
             company_forms=AAPL, years=[2024], period_ends={2024: "2024-09-28"},
             metric_forms=[revenue_aapl], forbidden_forms=("big tech",) + UNIVERSES_OTHER),
        dict(case_id=S2, question="AAPL revenue FY24",
             intent_id="int-aaaaaaaaaaaa", intent_axis="dev", template_axis="validation",
             company_forms=AAPL, years=[2024], period_ends={2024: "2024-09-28"},
             metric_forms=[revenue_aapl], forbidden_forms=("big tech",) + UNIVERSES_OTHER),
        dict(case_id=S3,
             question="How did Microsoft's revenue change from fiscal 2022 to fiscal 2024?",
             intent_id="int-bbbbbbbbbbbb", intent_axis="validation", template_axis="dev",
             company_forms=MSFT, years=[2024, 2022],
             metric_forms=[("Revenues", "revenue", "top line")],
             forbidden_forms=("big tech",) + UNIVERSES_OTHER),
        dict(case_id=S4,
             question="Which 3 big tech companies had the highest revenue in fiscal 2024?",
             intent_id="int-cccccccccccc", intent_axis="dev", template_axis="dev",
             years=[2024], metric_forms=[("Revenues", "revenue", "net sales")],
             universe_forms=("big tech",), forbidden_forms=UNIVERSES_OTHER),
    ]


# The hand-worked import outcome of the two fixture files (spec, Import steps 2-3).
ACCEPTED = {
    f"{S1}:buried_ask": "Before I rebalance: what did Apple book in revenue for FY2024?",
    f"{S1}:sell_side_shorthand": "AAPL FY24 top line?",
    f"{S1}:terse_pm": "Apple net sales, fiscal 2024.",
    f"{S1}:typo_prone": "Apple’s net sales in FY 24, pls",
    f"{S2}:buried_ask": ("I'm updating my model before earnings. What was Apple Inc.'s revenue "
                         "for the fiscal year ended September 28, 2024?"),
    f"{S3}:conversational_retail": ("So how did Microsoft's revenue move between fiscal 2022 "
                                    "and fiscal 2024?"),
    f"{S3}:sell_side_shorthand": "MSFT top line FY22 vs FY24",
    f"{S4}:sell_side_shorthand": "Top 3 big tech names by revenue — FY2024!",
}
REVIEW = {
    f"{S1}:conversational_retail": ["metric_missing", "semantic_drift"],
    f"{S3}:terse_pm": ["period_missing", "semantic_drift"],
    f"{S4}:conversational_retail": ["metric_missing", "universe_missing", "semantic_drift"],
    f"{S4}:typo_prone": ["period_missing", "metric_missing"],
}
DUPLICATES = {
    f"{S2}:sell_side_shorthand": (f"{S1}:sell_side_shorthand", "exact"),
    f"{S2}:typo_prone": (S2, "near"),
    f"{S3}:buried_ask": (S3, "exact"),
    f"{S4}:terse_pm": (f"{S4}:sell_side_shorthand", "near"),
}
# Which file each record came from.
FROM_B = {f"{S1}:typo_prone", f"{S3}:conversational_retail", f"{S4}:sell_side_shorthand",
          f"{S1}:buried_ask", f"{S3}:buried_ask", f"{S2}:sell_side_shorthand"}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def axis(key: str, share: float, salt: str) -> str:
    v = int(hashlib.sha1(f"{salt}:{key}".encode()).hexdigest()[:8], 16) / 2**32 < share
    return "validation" if v else "dev"


def oracle_personas(case_id: str, k: int) -> tuple[str, ...]:
    ranked = sorted(PERSONAS,
                    key=lambda p: hashlib.sha1(f"{case_id}|{p}".encode()).hexdigest())
    chosen = set(ranked[:k])
    return tuple(p for p in PERSONAS if p in chosen)


def oracle_hash(pset: dict) -> str:
    body = {k: v for k, v in pset.items() if k != "content_hash"}
    blob = json.dumps(body, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def write_reply(directory: Path, name: str, records: list[dict], *, model: str = "gemini-2.5-pro",
                date: str = "2026-10-20", prompt: str = "v1") -> Path:
    """Writes a reply file in the spec's format (header, then one JSON object per line)."""
    lines = [f"# model: {model}", f"# date: {date}", f"# prompt: {prompt}"]
    lines += [json.dumps(r, ensure_ascii=False) for r in records]
    p = Path(directory) / name
    p.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    return p


INSTRUCTIONS_V9 = "# paraphrase-instructions v9\nRewrite each question.\nReply in JSON lines.\n"


@pytest.fixture(scope="session")
def kit() -> SimpleNamespace:
    return SimpleNamespace(
        repo=REPO, reply_a=REPLY_A, reply_b=REPLY_B, personas=PERSONAS, aapl=AAPL, msft=MSFT,
        s1=S1, s2=S2, s3=S3, s4=S4, source_kwargs=source_kwargs, accepted=ACCEPTED,
        review=REVIEW, duplicates=DUPLICATES, from_b=FROM_B, sha256_file=sha256_file,
        axis=axis, oracle_personas=oracle_personas, oracle_hash=oracle_hash,
        write_reply=write_reply, instructions_v9=INSTRUCTIONS_V9,
    )
