"""C7: provider keys from a variable or its ``_FILE``, and which ones are missing.

Oracle: requirement 8. Keys are dummy strings; key files are written under pytest's temp dir.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.profiles import LlmConfigError, api_key, load_llm_settings, missing_api_keys

VAR = "SPEC_PROVIDER_KEY"
KEY_VARS = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
# The providers each hand-built profile uses (conftest DOC).
PROFILE_PROVIDERS = {
    "openai": {"openai"},
    "openai-5.6": {"openai"},
    "anthropic": {"anthropic"},
    "mixed": {"openai", "anthropic"},
}


@pytest.fixture(scope="session")
def key_file(tmp_path_factory):
    """A key file whose contents carry surrounding whitespace."""
    path = tmp_path_factory.mktemp("keys") / "key.txt"
    path.write_text("  sk-from-file-123\n\n", encoding="utf-8")
    return path


@pytest.mark.criterion("C7")
def test_api_key_reads_the_variable_then_the_file(key_file):
    # oracle: requirement 8 — the variable's value; else the stripped contents of the file
    # <VAR>_FILE names; the variable wins when both are set
    assert api_key(VAR, {VAR: "sk-from-var"}) == "sk-from-var"
    assert api_key(VAR, {f"{VAR}_FILE": str(key_file)}) == "sk-from-file-123"
    assert api_key(VAR, {VAR: "sk-from-var", f"{VAR}_FILE": str(key_file)}) == "sk-from-var"


@pytest.mark.criterion("C7")
@pytest.mark.parametrize("environ", [{}, {"OTHER_KEY": "x"}, {"OTHER_KEY_FILE": "x"}])
def test_api_key_raises_naming_the_variable(environ):
    # oracle: requirement 8 — with neither source set, LlmConfigError naming the variable
    with pytest.raises(LlmConfigError) as excinfo:
        api_key(VAR, environ)
    assert VAR in str(excinfo.value)


@pytest.mark.criterion("C7")
@pytest.mark.parametrize(
    ("profile", "environ", "missing"),
    [
        ("mixed", {}, ["ANTHROPIC_API_KEY", "OPENAI_API_KEY"]),
        ("mixed", {"OPENAI_API_KEY": "sk-o"}, ["ANTHROPIC_API_KEY"]),
        ("anthropic", {"OPENAI_API_KEY": "sk-o"}, ["ANTHROPIC_API_KEY"]),
        ("openai", {"ANTHROPIC_API_KEY": "sk-a"}, ["OPENAI_API_KEY"]),
        ("openai-5.6", {}, ["OPENAI_API_KEY"]),
    ],
)
def test_missing_api_keys_lists_exactly_the_missing_variables(make_doc, make_pricing, profile,
                                                              environ, missing):
    # oracle: requirement 8 — the key variables, for the providers the active profile uses,
    # that have neither a value nor a _FILE
    settings = load_llm_settings(make_doc(), make_pricing(), profile=profile, environ={})
    assert sorted(missing_api_keys(settings, environ)) == missing
    assert settings.providers_in_use() == PROFILE_PROVIDERS[profile]


@pytest.mark.criterion("C7")
def test_a_key_file_counts_as_present(make_doc, make_pricing, key_file):
    # oracle: requirement 8 — <VAR>_FILE supplies the key, so that variable is not missing
    settings = load_llm_settings(make_doc(), make_pricing(), profile="mixed", environ={})
    environ = {"ANTHROPIC_API_KEY_FILE": str(key_file)}
    assert missing_api_keys(settings, environ) == ["OPENAI_API_KEY"]


@pytest.mark.criterion("C7")
@given(
    has_var=st.booleans(),
    has_file=st.booleans(),
    value=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_", min_size=1, max_size=20),
)
def test_api_key_any_presence_is_the_closed_form(key_file, has_var, has_file, value):
    # oracle: C7's property — variable if set, else the stripped file, else LlmConfigError
    environ: dict[str, str] = {}
    if has_var:
        environ[VAR] = value
    if has_file:
        environ[f"{VAR}_FILE"] = str(key_file)
    if has_var:
        assert api_key(VAR, environ) == value
    elif has_file:
        assert api_key(VAR, environ) == "sk-from-file-123"
    else:
        with pytest.raises(LlmConfigError):
            api_key(VAR, environ)


@pytest.mark.criterion("C7")
@given(
    profile=st.sampled_from(sorted(PROFILE_PROVIDERS)),
    sources=st.fixed_dictionaries(
        {p: st.sampled_from(["none", "var", "file"]) for p in sorted(KEY_VARS)}
    ),
)
def test_missing_api_keys_any_presence_is_the_closed_form(make_doc, make_pricing, key_file,
                                                          profile, sources):
    # oracle: C7's property — exactly the in-use providers' variables with no source
    environ: dict[str, str] = {}
    for provider, source in sources.items():
        if source == "var":
            environ[KEY_VARS[provider]] = "sk-x"
        elif source == "file":
            environ[f"{KEY_VARS[provider]}_FILE"] = str(key_file)
    settings = load_llm_settings(make_doc(), make_pricing(), profile=profile, environ={})
    want = sorted(KEY_VARS[p] for p in PROFILE_PROVIDERS[profile] if sources[p] == "none")
    assert sorted(missing_api_keys(settings, environ)) == want
    assert settings.providers_in_use() == PROFILE_PROVIDERS[profile]
