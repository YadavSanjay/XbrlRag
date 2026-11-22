"""C1: ``is_flex_unavailable`` follows requirement 1 (docs/specs/llm-flex-retry.md).

The 429s are look-alikes of the pinned SDKs' ``RateLimitError`` (conftest.py), with ``body``
where the real clients put it.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.gateway.adapters.base import is_flex_unavailable

TIERS = (None, "auto", "default", "flex", "priority")


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("tier", TIERS)
def test_resource_unavailable_code_is_flex_at_any_tier(sdk_errors, tier):
    # oracle: requirement 1 — a 429 whose code names a resource being unavailable is a Flex
    # 429 whatever the tier; a 500 is never one
    exc = sdk_errors.openai_429(code="resource_unavailable", message="Please retry later.")
    assert is_flex_unavailable(exc, service_tier=tier) is True
    assert is_flex_unavailable(sdk_errors.openai_status(500), service_tier=tier) is False


@pytest.mark.criterion("C1")
def test_resource_unavailable_message_is_flex(sdk_errors):
    # oracle: requirement 1 — the message, lower-cased, contains "resource unavailable";
    # a plain rate-limit message does not
    exc = sdk_errors.openai_429(code=None, type_=None,
                                message="Resource Unavailable, please retry")
    plain = sdk_errors.openai_429(code="rate_limit_exceeded", type_="requests",
                                  message="Rate limit reached for requests")
    assert is_flex_unavailable(exc) is True
    assert is_flex_unavailable(plain) is False


@pytest.mark.criterion("C1")
def test_any_openai_429_is_flex_on_a_flex_run_except_quota(sdk_errors):
    # oracle: requirement 1 — at service_tier "flex" every 429 counts except
    # insufficient_quota; without the tier, or at "default", a rate-limit 429 does not
    rate = sdk_errors.openai_429(code="rate_limit_exceeded", type_="requests",
                                 message="Rate limit reached")
    quota = sdk_errors.openai_429(code="insufficient_quota", type_="insufficient_quota",
                                  message="You exceeded your current quota")
    assert is_flex_unavailable(rate, service_tier="flex") is True
    assert is_flex_unavailable(rate, service_tier=None) is False
    assert is_flex_unavailable(rate, service_tier="default") is False
    assert is_flex_unavailable(quota, service_tier="flex") is False
    assert is_flex_unavailable(quota) is False


@pytest.mark.criterion("C1")
def test_anthropic_429_and_timeouts(sdk_errors):
    # oracle: requirement 1 — Anthropic's wrapped body is read through "error"; its
    # rate_limit_error names no resource, so it counts only at the flex tier (which the
    # gateway never passes for Anthropic); a TimeoutError has no status
    exc = sdk_errors.anthropic_429()
    assert is_flex_unavailable(exc) is False
    assert is_flex_unavailable(exc, service_tier="flex") is True
    assert is_flex_unavailable(TimeoutError("read timed out"), service_tier="flex") is False
    assert is_flex_unavailable(sdk_errors.openai_timeout(), service_tier="flex") is False


class _StatusError(Exception):
    """A synthetic provider error: a status and a JSON body, as the SDKs expose them."""

    def __init__(self, status: int, body: object, attr: str = "status_code") -> None:
        super().__init__(f"status {status}")
        setattr(self, attr, status)
        self.body = body


_TEXTS = st.sampled_from([
    None, "", "resource_unavailable", "Resource unavailable", "RESOURCE-UNAVAILABLE",
    "rate_limit_exceeded", "insufficient_quota", "server_error", "unavailable resource",
    "the resource unavailable now",
])


def _names_resource(*texts: object) -> bool:
    for text in texts:
        if isinstance(text, str):
            norm = text.lower().replace("_", " ").replace("-", " ")
            if "resource unavailable" in norm:
                return True
    return False


@pytest.mark.criterion("C1")
@given(status=st.sampled_from([400, 404, 408, 409, 429, 429, 429, 500, 503, 529]),
       code=_TEXTS, type_=_TEXTS, message=_TEXTS, tier=st.sampled_from(TIERS),
       wrapped=st.booleans(), attr=st.sampled_from(["status_code", "http_status"]))
def test_rule_property(status, code, type_, message, tier, wrapped, attr):
    # oracle: requirement 1, evaluated here — 429, neither code nor type insufficient_quota,
    # and a resource-unavailable text or the flex tier; the body may be wrapped in "error"
    error = {"code": code, "type": type_, "message": message}
    exc = _StatusError(status, {"type": "error", "error": error} if wrapped else error, attr)
    expected = (status == 429
                and "insufficient_quota" not in (code, type_)
                and (_names_resource(code, type_, message) or tier == "flex"))
    assert is_flex_unavailable(exc, service_tier=tier) is expected
