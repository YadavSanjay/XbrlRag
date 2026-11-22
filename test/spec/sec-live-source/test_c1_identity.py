"""C1: the EDGAR identity is required at construction and sent as the User-Agent.

Oracle: requirement 3 of docs/specs/sec-live-source.md (the downloader's rule:
an explicit identity, else SEC_EDGAR_IDENTITY; missing fails).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.sec_live import LiveSecSource

_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 @.-_"


@pytest.mark.criterion("C1")
def test_explicit_identity_wins_over_environment(kit, monkeypatch):
    # oracle: requirement 3 — the explicit identity is the User-Agent, not the env value
    monkeypatch.setenv("SEC_EDGAR_IDENTITY", "Env Agent env@example.com")
    parts = kit.make(respond=kit.routes(kit.all_routes()), identity="  Arg Agent arg@example.com ")
    parts.source.company_facts(kit.CIK)
    assert [c[1]["User-Agent"] for c in parts.transport.calls] == ["Arg Agent arg@example.com"]


@pytest.mark.criterion("C1")
def test_environment_identity_used_without_argument(kit, monkeypatch):
    # oracle: requirement 3 — with identity=None the stripped SEC_EDGAR_IDENTITY is sent on
    # every request (two different URLs here)
    monkeypatch.setenv("SEC_EDGAR_IDENTITY", " Env Agent env@example.com\n")
    parts = kit.make(respond=kit.routes(kit.all_routes()), identity=None)
    parts.source.company_facts(kit.CIK)
    parts.source.frames("us-gaap", "Revenues", "USD", "CY2024")
    assert [c[1]["User-Agent"] for c in parts.transport.calls] == [
        "Env Agent env@example.com", "Env Agent env@example.com"]


@pytest.mark.criterion("C1")
@pytest.mark.parametrize(("arg", "env"), [(None, None), (None, "   "), ("   ", None), ("", None)])
def test_missing_or_blank_identity_fails_at_construction(kit, monkeypatch, arg, env):
    # oracle: requirement 3 — missing or blank raises ValueError at construction, no request
    if env is None:
        monkeypatch.delenv("SEC_EDGAR_IDENTITY", raising=False)
    else:
        monkeypatch.setenv("SEC_EDGAR_IDENTITY", env)
    clock = kit.FakeClock()
    transport = kit.FakeTransport(clock, kit.offline)
    with pytest.raises(ValueError):
        LiveSecSource(kit.MemoryStore(), transport=transport, identity=arg, clock=clock.now,
                      sleep=clock.sleep, wall_clock=lambda: kit.WALL)
    assert transport.calls == []


@pytest.mark.criterion("C1")
@given(core=st.text(alphabet=_ALPHABET, min_size=1, max_size=40).filter(lambda s: s.strip()),
       pad_left=st.sampled_from(["", " ", "\t", "  "]),
       pad_right=st.sampled_from(["", " ", "\n", " \r\n"]))
def test_any_identity_is_sent_stripped(kit, core, pad_left, pad_right):
    # oracle: requirement 3 — the header is the identity with surrounding whitespace stripped
    parts = kit.make(respond=kit.routes(kit.all_routes()), identity=pad_left + core + pad_right)
    parts.source.company_facts(kit.CIK)
    parts.source.frames("us-gaap", "Assets", "USD", "CY2024Q4I")
    assert [c[1]["User-Agent"] for c in parts.transport.calls] == [core.strip()] * 2
