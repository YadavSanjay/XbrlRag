"""Storage roots must come from env, never from a hardcoded Windows default.

The old `D:/...` defaults were *relative* paths on POSIX, so a stage that
passes one instead of consulting `SEC_EDGAR_ROOT` / `XBRLRAG_NORMALIZED_ROOT`
resolves it against the process cwd — `/app/D:/.../data/storage/...` in
the container. Discovery then finds zero filings and the stage reports success
having done nothing, which is worse than crashing: `canonical-html` "passed",
then `dom-positions` found no canonical.htm and `fact_dom_position` stayed empty.

Observed on the AWS runner, July 2026.
"""

from __future__ import annotations

import re

import pytest

from src.ingest.sec_edgar.storage import local_root

# A Windows drive path such as D:/x/ or D:\x\ inside a string literal.
_DRIVE_PATH = re.compile(r"\b[A-Za-z]:[/\\]{1,2}[A-Za-z0-9_.\-]+[/\\]")


@pytest.fixture(autouse=True)
def _clear_roots(monkeypatch):
    monkeypatch.delenv("SEC_EDGAR_ROOT", raising=False)
    monkeypatch.delenv("XBRLRAG_NORMALIZED_ROOT", raising=False)
    monkeypatch.delenv("XBRLRAG_STORAGE_BACKEND", raising=False)
    monkeypatch.delenv("XBRLRAG_DEPLOY_MODE", raising=False)


def test_local_root_prefers_env(monkeypatch):
    monkeypatch.setenv("SEC_EDGAR_ROOT", "/data/sec_edgar")
    monkeypatch.setenv("XBRLRAG_NORMALIZED_ROOT", "/data/normalized")
    assert local_root("sec_edgar") == "/data/sec_edgar"
    assert local_root("normalized") == "/data/normalized"


def test_local_root_falls_back_when_unset():
    # The local-dev default, only reached with no env var set: the repo's own data/
    # tree under PROJECT_ROOT, never a host-specific literal.
    from pathlib import Path as _Path

    from src import PROJECT_ROOT

    root = _Path(PROJECT_ROOT)
    assert _Path(local_root("sec_edgar")) == root / "data" / "storage" / "sec_edgar"
    assert _Path(local_root("normalized")) == root / "data" / "storage" / "xbrl" / "normalized"


def test_no_default_root_names_a_host_path():
    """A host-specific literal in a default only works on the machine that wrote it."""
    from src import PROJECT_ROOT
    from src.ingest.sec_edgar.storage import DEFAULT_LOCAL_ROOT, default_local_root

    assert DEFAULT_LOCAL_ROOT == default_local_root("sec_edgar")
    for role in ("sec_edgar", "normalized"):
        assert default_local_root(role).startswith(str(PROJECT_ROOT)), role


def test_roles_are_distinct(monkeypatch):
    """A single root for both trees would overwrite raw filings with output."""
    monkeypatch.setenv("SEC_EDGAR_ROOT", "/data/sec_edgar")
    monkeypatch.setenv("XBRLRAG_NORMALIZED_ROOT", "/data/normalized")
    assert local_root("sec_edgar") != local_root("normalized")


@pytest.mark.parametrize(
    "factory",
    [
        pytest.param(
            lambda: __import__(
                "src.ingest.sec_edgar.xbrl.dom_position_populator",
                fromlist=["DOMPositionPopulator"],
            ).DOMPositionPopulator,
            id="dom_position_populator",
        ),
        pytest.param(
            lambda: __import__(
                "src.chunking.xbrl_html_base_chunking",
                fromlist=["XBRLHTMLChunkPopulator"],
            ).XBRLHTMLChunkPopulator,
            id="xbrl_html_base_chunking",
        ),
    ],
)
def test_populators_take_both_roots_from_env(monkeypatch, tmp_path, factory):
    """`normalized_root` had no env var at all — it silently used the D:/ default."""
    sec = tmp_path / "sec_edgar"
    norm = tmp_path / "normalized"
    sec.mkdir()
    norm.mkdir()
    monkeypatch.setenv("SEC_EDGAR_ROOT", str(sec))
    monkeypatch.setenv("XBRLRAG_NORMALIZED_ROOT", str(norm))
    monkeypatch.setenv("XBRL_DB_HOST", "127.0.0.1")

    populator = factory().from_env()
    assert populator.sec_edgar_root == sec.resolve()
    assert populator.normalized_root == norm.resolve()


def test_project_root_is_derived_from_the_package_location():
    """PROJECT_ROOT drove CONFIGS_DIR and four other paths off a hardcoded D:/ root.

    The repo is at a D:/ path on the dev box and /app in the images, so the
    literal made every config lookup resolve to /app/D:/... in the container.
    """
    from pathlib import Path as _Path

    from src import CONFIGS_DIR, PROJECT_ROOT

    root = _Path(PROJECT_ROOT)
    assert root.is_dir(), PROJECT_ROOT
    assert (root / "src" / "__init__.py").is_file()
    assert (root / "configs").is_dir()
    assert CONFIGS_DIR.is_dir(), CONFIGS_DIR


def test_xbrl_pipeline_config_loads():
    """Reproduces the AWS failure: FileNotFoundError on the load_to_vectordb yaml."""
    from src.pipelines.xbrl import load_config

    config = load_config("load_to_vectordb")
    assert isinstance(config, dict) and config


def test_ensure_writable_root_creates_a_missing_dir(tmp_path):
    from src.ingest.sec_edgar.storage import ensure_writable_root

    target = tmp_path / "normalized"
    ensure_writable_root(target, role="normalized")
    assert target.is_dir()


@pytest.mark.skipif(
    not hasattr(__import__("os"), "geteuid"), reason="POSIX permissions only"
)
def test_ensure_writable_root_rejects_a_read_only_dir(tmp_path):
    """The container case: the mount exists but the image's uid cannot write it."""
    import os

    from src.ingest.sec_edgar.storage import ensure_writable_root

    if os.geteuid() == 0:
        pytest.skip("root bypasses the permission check")

    target = tmp_path / "normalized"
    target.mkdir()
    target.chmod(0o555)
    try:
        with pytest.raises(PermissionError) as excinfo:
            ensure_writable_root(target, role="normalized")
        message = str(excinfo.value)
        assert "not writable" in message
        assert "chown" in message  # must tell the operator what to run
    finally:
        target.chmod(0o755)


def test_no_config_file_hardcodes_an_absolute_host_path():
    """Configs are checked in and shared by the dev box and the containers.

    `load_to_vectordb.yaml` and `sec_edgar_downloads.yaml` both carried
    `D:/...` roots that beat the env var, so the container wrote to
    /app/D:/... . The code-side guard below never scanned configs/, which is
    exactly why that survived the first sweep.
    """
    import re
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    drive_path = re.compile(r"\b[A-Za-z]:[/\\]{1,2}[A-Za-z0-9_.\-]+[/\\]")

    offenders: list[str] = []
    for cfg in (repo / "configs").rglob("*.y*ml"):
        for n, line in enumerate(cfg.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue  # prose explaining the rule is fine
            if drive_path.search(line):
                offenders.append(f"{cfg.relative_to(repo)}:{n}: {line.strip()}")

    assert not offenders, (
        "absolute host path in a checked-in config — leave the key empty and let "
        "the env var / local_root() supply it:\n" + "\n".join(offenders)
    )


def test_no_ingest_entry_point_hardcodes_a_windows_root():
    """Guard: a new stage must resolve roots via local_root(), not a D:/ literal.

    Only the constants module (storage.py) and docs/help strings may name the
    historical default.
    """
    import ast
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    # These two own the historical constants by design: storage.py holds
    # _ROLE_LOCAL_DEFAULT (the single source, via default_local_root), and
    # arelle_cmd.py holds the platform-aware Arelle default.
    owners = {"storage.py", "arelle_cmd.py"}
    # PDF-legacy demo entry points: __main__ scratch blocks pointing at sample
    # files on the dev box. Not on any runtime path (VISION.md: the PDF flow is
    # maintenance-only). Listed explicitly so they are a decision, not an oversight.
    legacy_demos = {
        "bge_embedding.py",
        "sec10k_table_processor.py",
        "pdf_utils.py",
        "pymuparse.py",
        "sample_parsetable.py",
        "sample_parsechart.py",
        "sample_parsebarhistogram.py",
    }

    offenders: list[str] = []
    # Scan all of src/, not a hand-picked subset — the subset is how the config
    # and PROJECT_ROOT cases were missed.
    for py in (repo / "src").rglob("*.py"):
        if py.name in owners or py.name in legacy_demos:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))

        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            )
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        }
        # `help="… Default: D:/…"` is operator-facing text, not a resolved path.
        help_text = {
            id(kw.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for kw in node.keywords
            if kw.arg == "help"
        }
        # A module/class constant is a tolerated documented default; the bug is
        # *passing* one somewhere instead of calling local_root().
        const_defaults = {
            id(node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            for t in node.targets
            if isinstance(t, ast.Name) and "DEFAULT" in t.id
        }
        exempt = docstrings | help_text | const_defaults

        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if not _DRIVE_PATH.search(node.value):
                continue
            if id(node) in exempt:
                continue
            offenders.append(f"{py.relative_to(repo)}:{node.lineno}: {node.value!r}")

    assert not offenders, (
        "hardcoded Windows root used at a call site — resolve via local_root():\n"
        + "\n".join(offenders)
    )
