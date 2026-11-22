"""configure_logging gives each process its own file under the project root and prunes
stale ones (docs/specs/per-process-log-file.md). Uses a temporary project root and a
minimal config; the project's logging is restored afterwards.
"""

from __future__ import annotations

import logging
import os
import sys
import time

import pytest

from src import logging_config
from src.logging_config import configure_logging, current_log_file, process_name

CONFIG = """
version: 1
disable_existing_loggers: false
handlers:
  file:
    class: logging.handlers.RotatingFileHandler
    level: INFO
    filename: logs/xbrlrag_{process}_{pid}.log
    maxBytes: 1000000
    backupCount: 1
    encoding: utf8
root:
  level: INFO
  handlers: [file]
"""


@pytest.fixture
def temp_root(tmp_path, monkeypatch):
    monkeypatch.setenv("XBRLRAG_PROJECT_ROOT", str(tmp_path))
    config = tmp_path / "logging.yaml"
    config.write_text(CONFIG, encoding="utf8")
    yield tmp_path, config
    for handler in logging.getLogger().handlers:
        handler.close()
    monkeypatch.delenv("XBRLRAG_PROJECT_ROOT")
    configure_logging()  # back to the project's own config


def test_each_process_gets_its_own_file_under_the_root(temp_root):
    root, config = temp_root
    configure_logging(config)
    expected = root / "logs" / f"xbrlrag_{process_name(sys.argv)}_{os.getpid()}.log"
    assert current_log_file() == expected
    logging.getLogger("t").info("hello")
    for handler in logging.getLogger().handlers:
        handler.flush()
    assert "hello" in expected.read_text(encoding="utf8")


def test_stale_files_are_pruned_and_others_kept(temp_root):
    root, config = temp_root
    logs = root / "logs"
    logs.mkdir()
    old = logs / "xbrlrag_run_suite_1.log"
    recent = logs / "xbrlrag_uvicorn_2.log"
    unrelated = logs / "notes.log"
    for p in (old, recent, unrelated):
        p.write_text("x", encoding="utf8")
    ten_days_ago = time.time() - 10 * 86400
    os.utime(old, (ten_days_ago, ten_days_ago))
    os.utime(unrelated, (ten_days_ago, ten_days_ago))

    configure_logging(config)

    assert not old.exists()
    assert recent.exists()
    assert unrelated.exists()


def test_legacy_root_variable_is_honoured(tmp_path, monkeypatch):
    monkeypatch.delenv("XBRLRAG_PROJECT_ROOT", raising=False)
    monkeypatch.setenv("ARAG_PROJECT_ROOT", str(tmp_path))
    assert logging_config._project_root() == tmp_path


def test_api_tails_this_process_file_unless_overridden(temp_root, monkeypatch):
    from src.api import config as api_config

    _, config = temp_root
    configure_logging(config)
    monkeypatch.delenv("XBRLRAG_LOG_FILE", raising=False)
    api_config.log_file_path.cache_clear()
    assert api_config.log_file_path() == str(current_log_file())
    monkeypatch.setenv("XBRLRAG_LOG_FILE", "elsewhere.log")
    api_config.log_file_path.cache_clear()
    assert api_config.log_file_path() == "elsewhere.log"
    api_config.log_file_path.cache_clear()
