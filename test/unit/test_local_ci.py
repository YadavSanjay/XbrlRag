"""scripts/ci/local_ci.py: the pure parts (environment, steps, verdict, report, PR selection).

The git / gh plumbing is a thin wrapper over the CLIs and is exercised by running the tool.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "local_ci", REPO_ROOT / "scripts" / "ci" / "local_ci.py")
assert _spec is not None and _spec.loader is not None
local_ci = importlib.util.module_from_spec(_spec)
sys.modules["local_ci"] = local_ci
_spec.loader.exec_module(local_ci)

SHA = "a" * 40


def _result(name: str, required: bool, status: str, tail: str = "") -> object:
    return local_ci.StepResult(name, required, status, 90.0, 0 if status == "pass" else 1, tail)


def test_scrub_env_drops_backends_and_keys_and_sets_offline() -> None:
    env = {"PATH": "p", "OPENAI_API_KEY": "k", "XBRL_DB_HOST": "h", "xbrlrag_llm_profile": "x",
           "ARAG_DEPLOY_MODE": "aws", "QDRANT_PORT": "6333", "RAGINFER_URL": "u",
           "AWS_PROFILE": "a", "PYTHONPATH": "src", "GH_CONFIG_DIR": "g"}
    out = local_ci.scrub_env(env)
    assert out == {"PATH": "p", "GH_CONFIG_DIR": "g", "HF_HUB_OFFLINE": "1",
                   "TRANSFORMERS_OFFLINE": "1", "PYTHONUTF8": "1"}
    assert "OPENAI_API_KEY" in env  # the input is not changed


def test_dotenv_above_finds_a_parent_env(tmp_path: Path) -> None:
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    assert local_ci.dotenv_above(deep) in (None, *[p / ".env" for p in tmp_path.parents])
    (tmp_path / ".env").write_text("X=1")
    assert local_ci.dotenv_above(deep) == tmp_path / ".env"


def test_build_steps_path_guard_only_for_a_pr() -> None:
    pr = local_ci.Target(SHA, "impl/x", 7)
    names = [s.name for s in local_ci.build_steps("py", "tdd.py", pr, "b" * 40)]
    assert names[:3] == ["tdd manifest-check", "tdd path-guard", "tdd ci (spec tests)"]
    guard = local_ci.build_steps("py", "tdd.py", pr, "b" * 40)[1]
    assert guard.argv == ["py", "tdd.py", "path-guard", "--base", "b" * 40, "--head", SHA,
                          "--head-ref", "impl/x"]
    main = local_ci.build_steps("py", "tdd.py", local_ci.Target(SHA, ""), None)
    assert "tdd path-guard" not in [s.name for s in main]
    required = {s.name for s in main if s.required}
    assert required == {"tdd manifest-check", "tdd ci (spec tests)", "pytest test/unit"}


def test_verdict_ignores_advisory_failures() -> None:
    assert local_ci.verdict([_result("a", True, "pass"), _result("ruff", False, "fail")])
    assert not local_ci.verdict([_result("a", True, "timeout"), _result("b", False, "pass")])


def test_render_report_marker_table_and_failed_tails() -> None:
    t = local_ci.Target(SHA, "impl/x", 7)
    md = local_ci.render_report(t, [_result("unit", True, "pass", "fine"),
                                    _result("ruff", False, "fail", "E501 here")],
                                {"main_sha": "c" * 40, "host": "H", "started": "now"})
    assert md.startswith(f"<!-- xbrlrag-local-ci sha={SHA} -->\n")
    assert "✅ passed" in md and "PR 7 (`impl/x`)" in md
    assert "| ruff | advisory | ❌ fail | 1.5 min |" in md
    assert "E501 here" in md and "fine" not in md


def test_render_report_stays_under_the_comment_limit() -> None:
    t = local_ci.Target(SHA, "")
    big = [_result(f"s{i}", True, "fail", "x" * 50_000) for i in range(3)]
    md = local_ci.render_report(t, big, {})
    assert len(md) <= local_ci.COMMENT_LIMIT
    assert "❌ failed" in md and "`main`" in md and "truncated" in md


def test_select_pending_skips_done_and_drafts() -> None:
    prs = [{"number": 9, "headRefName": "b9", "headRefOid": "9" * 40, "isDraft": False},
           {"number": 3, "headRefName": "b3", "headRefOid": "3" * 40, "isDraft": False},
           {"number": 5, "headRefName": "b5", "headRefOid": "5" * 40, "isDraft": True},
           {"number": 4, "headRefName": "b4", "headRefOid": "4" * 40, "isDraft": False}]
    got = local_ci.select_pending(prs, {"4" * 40})
    assert [(t.pr, t.head_ref) for t in got] == [(3, "b3"), (9, "b9")]
    assert [t.pr for t in local_ci.select_pending(prs, set(), include_drafts=True)] == [3, 4, 5, 9]
