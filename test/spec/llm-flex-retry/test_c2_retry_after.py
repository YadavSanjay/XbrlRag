"""C2: ``retry_after_s`` follows requirement 2 (docs/specs/llm-flex-retry.md).

Header order is openai 2.14.0's ``_parse_retry_after_header``: ``retry-after-ms`` first, then
``retry-after``. The errors are look-alikes of the SDKs' (conftest.py).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.gateway.adapters.base import retry_after_s


@pytest.mark.criterion("C2")
def test_seconds_and_milliseconds(sdk_errors):
    # oracle: requirement 2 — seconds as given; milliseconds / 1000; ms wins when both
    def ra(headers):
        return retry_after_s(sdk_errors.openai_429(headers=headers))

    assert ra({"retry-after": "7"}) == pytest.approx(7.0, abs=1e-12)  # exact parse
    assert ra({"retry-after-ms": "1500"}) == pytest.approx(1.5, abs=1e-12)  # exact parse
    assert ra({"retry-after": "7", "retry-after-ms": "1500"}) == pytest.approx(1.5, abs=1e-12)
    assert ra({"retry-after-ms": "abc", "retry-after": "4"}) == pytest.approx(4.0, abs=1e-12)
    assert ra({"retry-after": "2.5"}) == pytest.approx(2.5, abs=1e-12)  # exact parse


@pytest.mark.criterion("C2")
def test_unusable_values_give_none(sdk_errors):
    # oracle: requirement 2 — an HTTP-date, 0, a negative, text and no header give None; a
    # usable header on an Anthropic error is read the same way
    def ra(headers):
        return retry_after_s(sdk_errors.openai_429(headers=headers))

    assert ra({"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}) is None
    assert ra({"retry-after": "0"}) is None
    assert ra({"retry-after": "-3"}) is None
    assert ra({"retry-after": "abc"}) is None
    assert ra({}) is None
    assert retry_after_s(sdk_errors.anthropic_429(headers={"retry-after": "9"})) == \
        pytest.approx(9.0, abs=1e-12)  # exact parse


@pytest.mark.criterion("C2")
def test_no_response_gives_none(sdk_errors):
    # oracle: requirement 2 — a timeout has no response, a TimeoutError no attributes at all;
    # a 500 with a header is read
    assert retry_after_s(sdk_errors.openai_timeout()) is None
    assert retry_after_s(TimeoutError("x")) is None
    assert retry_after_s(sdk_errors.openai_status(500, headers={"retry-after": "11"})) == \
        pytest.approx(11.0, abs=1e-12)  # exact parse


@pytest.mark.criterion("C2")
@given(n=st.integers(min_value=1, max_value=100_000),
       m=st.integers(min_value=1, max_value=10_000_000),
       junk=st.text(alphabet="abcxyz;:, ", min_size=1, max_size=12))
def test_property(sdk_errors, n, m, junk):
    # oracle: requirement 2 — integer seconds n gives n, integer milliseconds m gives m/1000,
    # text that is no number gives None
    assert retry_after_s(sdk_errors.openai_429(headers={"retry-after": str(n)})) == \
        pytest.approx(float(n), rel=1e-12)  # float parse of an integer
    assert retry_after_s(sdk_errors.openai_429(headers={"retry-after-ms": str(m)})) == \
        pytest.approx(m / 1000.0, rel=1e-12)  # one division
    assert retry_after_s(sdk_errors.openai_429(headers={"retry-after": junk.strip() or "x"})) \
        is None
