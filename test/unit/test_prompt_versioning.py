"""Tests for prompt-template versioning (Phase 4 reproducibility)."""
from prompts.prompts import PromptManager, PromptTemplate


def _manager():
    m = PromptManager()
    m.add("sys", "You are a helpful assistant. {schema}")
    m.add("usr", "Question: {q}")
    return m


def test_content_hash_is_stable_and_short():
    t = PromptTemplate("hello {name}", name="greet")
    h1 = t.content_hash
    h2 = PromptTemplate("hello {name}").content_hash
    assert h1 == h2          # same text -> same hash
    assert len(h1) == 12     # short
    assert PromptTemplate("hello {other}").content_hash != h1  # different text -> different hash


def test_version_is_deterministic_and_order_independent():
    m = _manager()
    v1 = m.version("sys", "usr")
    v2 = m.version("usr", "sys")  # order should not matter
    assert v1 == v2
    assert len(v1) == 12


def test_version_changes_when_a_template_changes():
    m = _manager()
    before = m.version("sys", "usr")
    m.add("sys", "You are a STRICT assistant. {schema}")  # edit the system prompt
    after = m.version("sys", "usr")
    assert before != after


def test_signature_shape():
    m = _manager()
    sig = m.signature("sys", "usr")
    assert sig["templates"] == ["sys", "usr"]
    assert sig["version"] == m.version("sys", "usr")


def test_signature_distinguishes_template_sets():
    m = _manager()
    m.add("other", "Different {x}")
    assert m.version("sys", "usr") != m.version("sys", "other")
