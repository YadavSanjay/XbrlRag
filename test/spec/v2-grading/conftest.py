"""Builders for the v2-grading spec tests (docs/specs/v2-grading.md).

Every case is a hand-built dict in the shape hx-authoring (requirement 14, its C7 dicts) or
v2-suite-build (requirements 13-14) emits; tickers are fictional (DEVA, DEVB, ...) and
amounts round. Answers are hand-built SystemAnswers. No fakes are needed: grading is pure.
The kit is session-scoped so hypothesis-driven tests can use it.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from src.eval.core.case import EvalCase
from src.eval.core.system import SystemAnswer

M = 1e6
BN = 1e9


class Kit:
    M = M
    BN = BN

    # ------------------------------------------------------------------ H-X dicts
    @staticmethod
    def _hx_common(cid: str, kind: str, subtype: str | None = None) -> dict[str, Any]:
        tags = ["split:external", kind] + ([subtype] if subtype else [])
        return {"id": f"hx-{cid}", "question": f"Synthetic {kind} question {cid}?",
                "suite": "heldout_x_v1", "origin": "handwritten:h_x", "tags": tags}

    @staticmethod
    def ranking_fields() -> dict[str, Any]:
        # universe of 4, one exclusion (DEVD), 2 ranked rows
        return {"kind": "screen", "fy": 2025, "unit": "USD",
                "universe": ["DEVA", "DEVB", "DEVC", "DEVD"],
                "expected_exclusions": [{"ticker": "DEVD", "reason": "not_tagged"}],
                "expected_rows": [{"entity": "DEVA", "value": 300 * M},
                                  {"entity": "DEVB", "value": 200 * M}],
                "order_sensitive": True, "top_n": 2, "expected_fact_ids": [1, 2],
                "fact_id_classes": [[1, 11], [2]], "rel_tol": 1e-4}

    def hx_screen(self, cid: str = "a01") -> dict[str, Any]:
        return {**self._hx_common(cid, "cross_filer", "screen_top"), **self.ranking_fields()}

    def hx_count(self, cid: str = "a03") -> dict[str, Any]:
        return {**self._hx_common(cid, "cross_filer", "count"), "kind": "compute", "fy": 2025,
                "universe": ["DEVA", "DEVB", "DEVC", "DEVD"],
                "expected_exclusions": [{"ticker": "DEVD", "reason": "not_tagged"}],
                "expected_value": 2.0, "unit": "pure", "expected_fact_ids": [1, 2],
                "fact_id_classes": [[1, 11], [2]], "rel_tol": 0.0}

    def hx_sum(self, cid: str = "a04") -> dict[str, Any]:
        return {**self._hx_common(cid, "cross_filer", "sum"), "kind": "compute", "fy": 2025,
                "universe": ["DEVA", "DEVB", "DEVC", "DEVD"],
                "expected_exclusions": [{"ticker": "DEVD", "reason": "not_tagged"}],
                "expected_value": 500 * M, "unit": "USD", "expected_fact_ids": [1, 2],
                "fact_id_classes": [[1, 11], [2]], "rel_tol": 1e-2}

    def hx_median(self, cid: str = "a05") -> dict[str, Any]:
        return {**self._hx_common(cid, "cross_filer", "median"), "kind": "compute",
                "fy": 2025, "universe": ["DEVA", "DEVB", "DEVC", "DEVD"],
                "expected_exclusions": [{"ticker": "DEVD", "reason": "not_tagged"}],
                "expected_value": 200 * M, "unit": "USD", "expected_fact_ids": [1, 2, 3],
                "fact_id_classes": [[1], [2], [3]], "rel_tol": 1e-2}

    def hx_rank(self, cid: str = "a06") -> dict[str, Any]:
        return {**self._hx_common(cid, "cross_filer", "peer_relative"), "kind": "compute",
                "fy": 2025, "universe": ["DEVA", "DEVB", "DEVC", "DEVD"],
                "expected_exclusions": [{"ticker": "DEVD", "reason": "not_tagged"}],
                "expected_value": 2.0, "unit": "pure", "ticker": "DEVB",
                "expected_fact_ids": [1, 2, 3], "fact_id_classes": [[1], [2], [3]],
                "rel_tol": 0.0}

    def hx_trend(self, cid: str = "b01") -> dict[str, Any]:
        return {**self._hx_common(cid, "cross_period", "trend"), "kind": "breakdown",
                "ticker": "DEVE", "fy": 2025,
                "expected_rows": [{"entity": "FY2023", "value": 100 * M},
                                  {"entity": "FY2024", "value": 110 * M},
                                  {"entity": "FY2025", "value": 121 * M}],
                "order_sensitive": False, "rel_tol": 1e-4,
                "expected_fact_ids": [31, 32, 33], "fact_id_classes": [[31], [32], [33]]}

    def hx_cagr(self, cid: str = "b02") -> dict[str, Any]:
        return {**self._hx_common(cid, "cross_period", "cagr"), "kind": "compute",
                "ticker": "DEVE", "fy": 2025, "expected_value": 0.1,
                "alt_values": [0.1 * 100], "unit": "pure", "rel_tol": 1e-2,
                "expected_fact_ids": [31, 33], "fact_id_classes": [[31], [33]]}

    def hx_pit(self, cid: str = "b03", latest: float = 104 * M) -> dict[str, Any]:
        return {**self._hx_common(cid, "cross_period", "point_in_time"), "kind": "breakdown",
                "ticker": "DEVE", "fy": 2023,
                "expected_rows": [{"entity": "as_originally_reported", "value": 100 * M},
                                  {"entity": "latest", "value": latest}],
                "order_sensitive": False, "rel_tol": 1e-4,
                "accessions": {"as_originally_reported": "0000900101-24-000001",
                               "latest": "0000900101-25-000002"},
                "expected_fact_ids": [41, 42], "fact_id_classes": [[41], [42]]}

    def hx_compound(self, cid: str = "c01") -> dict[str, Any]:
        ranking = self.ranking_fields()
        dependent = {"kind": "breakdown", "ticker": "DEVA", "fy": 2025,
                     "expected_rows": [{"entity": "FY2024", "value": 250 * M},
                                       {"entity": "FY2025", "value": 300 * M}],
                     "order_sensitive": False, "rel_tol": 1e-4,
                     "expected_fact_ids": [34, 1], "fact_id_classes": [[34], [1, 11]]}
        return {**self._hx_common(cid, "compound"), **ranking,
                "parts": [{"part": "ranking", **copy.deepcopy(ranking)},
                          {"part": "dependent", **dependent}]}

    def hx_ambiguous(self, assumptions: list[str] | None = None,
                     cid: str = "d01") -> dict[str, Any]:
        return {**self._hx_common(cid, "honesty", "ambiguous"), "kind": "narrative",
                "require_citation": False, "tickers": ["DEVA"],
                "assumptions": assumptions if assumptions is not None
                else ["fiscal year|fiscal 2024"]}

    def hx_false_premise(self, correction: str = "The synthetic metric rose, it did not fall.",
                         cid: str = "d02") -> dict[str, Any]:
        return {**self._hx_common(cid, "honesty", "false_premise"), "kind": "narrative",
                "require_citation": False, "tickers": ["DEVB"], "fy": 2024,
                "expected_text": correction}

    def hx_decline(self, reason: str = "not_tagged", cid: str = "d03") -> dict[str, Any]:
        return {**self._hx_common(cid, "honesty", "decline"), "kind": "narrative",
                "require_citation": False, "tickers": ["DEVA", "DEVC"],
                "decline_reason": reason}

    def hx_text(self, cid: str = "e01") -> dict[str, Any]:
        return {**self._hx_common(cid, "text"), "kind": "narrative", "ticker": "DEVA",
                "fy": 2025,
                "expected_text": "Synthetic reference answer about a made-up filer.",
                "key_points": ["first synthetic point", "second synthetic point"],
                "passages": [{"accession": "0000900101-25-000003",
                              "quote": "a synthetic quoted span"}]}

    # ------------------------------------------------------------------ v2 records
    @staticmethod
    def _v2_tags(shape: str, split: str = "dev") -> list[str]:
        return [f"split:{split}", "intent:abc123", "family:plain", "template:t1",
                "key_source:sec_companyfacts", "key_status:verified", f"shape:{shape}"]

    @staticmethod
    def _key(definition: str | None = None) -> dict[str, Any]:
        return {"status": "verified", "source": "sec_companyfacts", "policies": ["S1", "S3"],
                "definition": definition, "same_filing": None, "inputs": [],
                "disagreements": []}

    def v2_screen(self, disclose_period_ends: bool = True) -> dict[str, Any]:
        rows = [{"ticker": "DEVB", "value": 220 * BN, "fiscal_year": 2024,
                 "period_end": "2024-12-31"},
                {"ticker": "DEVA", "value": 121 * BN, "fiscal_year": 2024,
                 "period_end": "2024-09-28"}]
        return {"id": "v2-scr-1", "question": "Which covered filers had the most revenue?",
                "suite": "xbrl_v2_dev", "kind": "screen", "expected_rows": rows, "top_n": 3,
                "origin": "v2:screen.top", "paraphrase_group": "abc123", "fy": 2024,
                "unit": "USD", "tags": self._v2_tags("screen.top"), "rel_tol": 1e-4,
                "key": self._key(),
                "disclosures": {"universe": "covered", "members": 4,
                                "ranked": ["DEVB", "DEVA"],
                                "excluded": [{"ticker": "DEVC", "reason": "unit_mismatch"},
                                             {"ticker": "DEVT", "reason": "currency"}],
                                "period_ends": {"DEVB": "2024-12-31", "DEVA": "2024-09-28"},
                                "disclose_period_ends": disclose_period_ends}}

    def v2_peer(self) -> dict[str, Any]:
        rows = [{"ticker": "DEVB", "value": 220 * BN, "fiscal_year": 2024,
                 "period_end": "2024-12-31"},
                {"ticker": "DEVA", "value": 121 * BN, "fiscal_year": 2024,
                 "period_end": "2024-12-31"}]
        return {"id": "v2-peer-1", "question": "Where does DEVA rank among its peers?",
                "suite": "xbrl_v2_dev", "kind": "screen", "expected_rows": rows,
                "origin": "v2:peer_relative", "paraphrase_group": "def456", "fy": 2024,
                "unit": "USD", "tags": self._v2_tags("peer_relative"), "rel_tol": 1e-4,
                "key": self._key(),
                "disclosures": {"universe": "peers", "members": 3,
                                "ranked": ["DEVB", "DEVA"],
                                "excluded": [{"ticker": "ZZZ", "reason": "not_in_corpus"}],
                                "period_ends": {"DEVB": "2024-12-31", "DEVA": "2024-12-31"},
                                "disclose_period_ends": False},
                "subject": {"ticker": "DEVA", "rank": 2, "universe_median": 170.5 * BN,
                            "position": "below"}}

    def v2_ratio(self, balance: str | None = None) -> dict[str, Any]:
        key = self._key("net_margin")
        if balance is not None:
            key["balance"] = balance
        return {"id": "v2-rat-1", "question": "What was DEVA's net margin in FY2024?",
                "suite": "xbrl_v2_dev", "kind": "compute", "expected_value": 0.25,
                "alt_values": [25.0], "origin": "v2:compute.ratio", "ticker": "DEVA",
                "cik": 900101, "fy": 2024, "tags": self._v2_tags("compute.ratio"),
                "rel_tol": 1e-4, "key": key}

    def v2_lookup(self) -> dict[str, Any]:
        return {"id": "v2-lk-1", "question": "What was DEVA's revenue in FY2024?",
                "suite": "xbrl_v2_dev", "kind": "lookup", "expected_value": 121 * BN,
                "origin": "v2:lookup", "ticker": "DEVA", "cik": 900101, "fy": 2024,
                "concept": "us-gaap:Revenues", "unit": "USD",
                "tags": self._v2_tags("lookup"), "rel_tol": 1e-4, "key": self._key()}

    def v2_with_shape(self, shape: str) -> dict[str, Any]:
        d = self.v2_lookup()
        d["tags"] = self._v2_tags(shape)
        return d

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def case(d: dict[str, Any]) -> EvalCase:
        return EvalCase.from_dict(copy.deepcopy(d))

    @staticmethod
    def answer(text: str = "", values: list[float] | None = None,
               rows: list[dict] | None = None, fact_ids: list[int] | None = None,
               refused: bool = False, hedged: bool = False,
               error: str | None = None) -> SystemAnswer:
        return SystemAnswer(text=text, numeric_values=list(values or []),
                            rows=list(rows or []), fact_ids=list(fact_ids or []),
                            refused=refused, hedged=hedged, error=error)

    @staticmethod
    def checks(grade: Any) -> Any:
        """The grade's sub-checks; the marker string when the attribute does not exist."""
        return getattr(grade, "checks", "<no checks attribute>")


@pytest.fixture(scope="session")
def kit() -> Kit:
    return Kit()
