"""C12: the log-directory override: where it comes from and what it moves.

Oracle: requirement 11 of docs/specs/hf-suite-build.md, by hand; the existing
``expand_log_filename`` (docs/specs/per-process-log-file.md) composes with the relocated
names.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.log_dir import relocate_log_handlers, resolve_log_dir
from src.logging_config import expand_log_filename

CFG = {
    "version": 1,
    "formatters": {"plain": {"format": "%(message)s"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "level": "INFO"},
        "file": {"class": "logging.handlers.RotatingFileHandler",
                 "filename": "logs/xbrlrag_{process}_{pid}.log", "maxBytes": 10},
        "json": {"class": "logging.FileHandler", "filename": "logs/sub/events_{pid}.jsonl"},
    },
    "root": {"level": "INFO", "handlers": ["console", "file", "json"]},
}


@pytest.fixture(scope="module")
def dirs(tmp_path_factory):
    return tmp_path_factory.mktemp("logs_a"), tmp_path_factory.mktemp("logs_b")


@pytest.mark.criterion("C12")
def test_resolve_log_dir(dirs):
    # oracle: requirement 11: the argument, then XBRLRAG_LOG_DIR, else None; never relative.
    a, b = dirs
    assert resolve_log_dir(a, {}) == Path(a)
    assert resolve_log_dir(str(a), {"XBRLRAG_LOG_DIR": str(b)}) == Path(a)
    assert resolve_log_dir(None, {"XBRLRAG_LOG_DIR": str(b)}) == Path(b)
    assert resolve_log_dir(None, {}) is None
    assert resolve_log_dir(None, {"XBRLRAG_LOG_DIR": ""}) is None
    for explicit, env in (("logs", {}), (None, {"XBRLRAG_LOG_DIR": "logs/heldout"})):
        with pytest.raises(ValueError):
            resolve_log_dir(explicit, env)


@pytest.mark.criterion("C12")
def test_relocate_moves_file_handlers_only(dirs):
    # oracle: requirement 11, by hand: the two file handlers keep their names and
    # placeholders in the new directory; the console handler and the input are unchanged.
    a, _b = dirs
    before = copy.deepcopy(CFG)
    out = relocate_log_handlers(CFG, a)
    assert CFG == before
    assert out["handlers"]["file"]["filename"] == str(Path(a) / "xbrlrag_{process}_{pid}.log")
    assert out["handlers"]["json"]["filename"] == str(Path(a) / "events_{pid}.jsonl")
    assert out["handlers"]["console"] == CFG["handlers"]["console"]
    assert {k: v for k, v in out.items() if k != "handlers"} == \
        {k: v for k, v in CFG.items() if k != "handlers"}
    expanded = expand_log_filename(out["handlers"]["file"]["filename"], Path(a).parent / "repo",
                                   "build_v2_suite", 42)
    assert expanded == Path(a) / "xbrlrag_build_v2_suite_42.log"
    with pytest.raises(ValueError):
        relocate_log_handlers(CFG, "logs")


NAME = st.text(alphabet="abcdefgh_{}", min_size=1, max_size=10)


@pytest.mark.criterion("C12")
@settings(max_examples=40)
@given(handlers=st.dictionaries(
    st.text(alphabet="abcxyz", min_size=1, max_size=5),
    st.fixed_dictionaries({"level": st.sampled_from(["INFO", "DEBUG"])},
                          optional={"filename": st.tuples(NAME, NAME)}),
    max_size=5))
def test_only_filenames_change(dirs, handlers):
    # oracle: requirement 11: every handler keeps its keys; only a filename changes, to the
    # directory joined with the old name.
    a, _b = dirs
    cfg = {"version": 1,
           "handlers": {h: ({**spec, "filename": f"{spec['filename'][0]}/{spec['filename'][1]}"}
                            if "filename" in spec else dict(spec))
                        for h, spec in handlers.items()}}
    out = relocate_log_handlers(cfg, a)
    assert set(out["handlers"]) == set(cfg["handlers"])
    for h, spec in cfg["handlers"].items():
        got = out["handlers"][h]
        assert set(got) == set(spec)
        assert got["level"] == spec["level"]
        if "filename" in spec:
            assert got["filename"] == str(Path(a) / Path(spec["filename"]).name)
    assert out["version"] == 1
