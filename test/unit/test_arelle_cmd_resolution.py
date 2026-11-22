"""ARELLE_CMD resolution — the Windows-path vs PATH-name split.

`ARELLE_CMD` is an absolute path to a frozen build on the Windows dev box, and a
bare console-script name (`arelleCmdLine`) in the Linux ingest image. Code that
validated with `Path(cmd).exists()` therefore passed on Windows and failed on
Linux, while `subprocess` itself PATH-resolved the same value happily — so one
pipeline stage ran and the next claimed Arelle was missing. These tests pin the
single resolution path all callers now share.
"""

from __future__ import annotations

import sys

import pytest

from src.ingest.util.arelle_cmd import (
    arelle_cmd_from_env,
    default_arelle_cmd,
    resolve_arelle_cmd,
)


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------
def test_resolves_a_bare_name_on_path():
    """The container case: ARELLE_CMD=arelleCmdLine, resolved via PATH."""
    exe = "python.exe" if sys.platform == "win32" else "python3"
    resolved = resolve_arelle_cmd(exe)
    assert resolved != exe  # expanded to a full path
    assert "python" in resolved.lower()


def test_resolves_an_absolute_path(tmp_path):
    """The Windows dev-box case: an absolute path to the frozen build."""
    exe = tmp_path / "arelleCmdLine.exe"
    exe.write_text("stub")
    assert resolve_arelle_cmd(str(exe)) == str(exe)


def test_absolute_path_wins_even_when_not_executable(tmp_path):
    """A present-but-not-+x file still resolves — historical Windows behaviour."""
    exe = tmp_path / "arelleCmdLine"
    exe.write_text("stub")
    exe.chmod(0o644)
    assert resolve_arelle_cmd(str(exe)) == str(exe)


def test_missing_command_raises_with_the_offending_value():
    with pytest.raises(FileNotFoundError) as excinfo:
        resolve_arelle_cmd("xbrlrag-definitely-not-installed")
    message = str(excinfo.value)
    assert "xbrlrag-definitely-not-installed" in message
    assert "ARELLE_CMD" in message  # tells the operator which knob to turn


# --------------------------------------------------------------------------
# defaults / env
# --------------------------------------------------------------------------
def test_default_is_platform_appropriate():
    default = default_arelle_cmd()
    if sys.platform == "win32":
        assert default.endswith(".exe")
        # No host-specific install folder: the frozen build is found on PATH or
        # named by ARELLE_CMD.
        assert ":" not in default and "/" not in default
    else:
        # A D:/ path on Linux produces an error naming a drive that cannot exist.
        assert default == "arelleCmdLine"
        assert ":" not in default


def test_env_precedence(monkeypatch):
    monkeypatch.setenv("ARELLE_CMD", "/from/env")
    assert arelle_cmd_from_env() == "/from/env"
    assert arelle_cmd_from_env("/explicit") == "/explicit"  # override wins


def test_empty_env_falls_back_to_default(monkeypatch):
    """`ARELLE_CMD=` in an env file must fall back, not resolve the empty string."""
    monkeypatch.setenv("ARELLE_CMD", "")
    assert arelle_cmd_from_env() == default_arelle_cmd()


def test_unset_env_falls_back_to_default(monkeypatch):
    monkeypatch.delenv("ARELLE_CMD", raising=False)
    assert arelle_cmd_from_env() == default_arelle_cmd()


# --------------------------------------------------------------------------
# every consumer goes through the same resolution
# --------------------------------------------------------------------------
def test_all_arelle_consumers_share_one_resolution_path():
    """Guard against a fourth call site re-introducing Path(cmd).exists()."""
    from src.ingest.sec_edgar.ingest import arelle_xbrl_ingestor as axi
    from src.ingest.sec_edgar.ingest import concept_labels_populator as clp
    from src.ingest.util import generate_geography_normalization as ggn

    for module in (clp, axi, ggn):
        assert hasattr(module, "arelle_cmd_from_env") or hasattr(
            module, "resolve_arelle_cmd"
        ), f"{module.__name__} does not import the shared ARELLE_CMD helpers"


def test_populator_defaults_are_platform_aware(monkeypatch):
    from src.ingest.sec_edgar.ingest.concept_labels_populator import (
        ConceptLabelsPopulator,
        DimensionMembersPopulator,
    )

    for cls in (ConceptLabelsPopulator, DimensionMembersPopulator):
        assert cls.DEFAULT_ARELLE_CMD == default_arelle_cmd()
