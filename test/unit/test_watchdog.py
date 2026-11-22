"""Tests for the H4 runaway-process watchdog (WS-8 / H4).

Drives the pure `find_runaways` detection core with synthetic process lists —
no real processes, no psutil needed. Covers the two safety properties (scope +
never-touch-self/critical) and the three runaway triggers (over-age, over-rss,
orphaned).
"""

from __future__ import annotations

import os
import re
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts", "harness"))

import watchdog as wd  # noqa: E402

NOW = 1_000_000.0
SIG = re.compile(wd.DEFAULT_SIGNATURE, re.IGNORECASE)


def P(pid, ppid, cmd, age=0.0, rss_mb=0.0, name="python.exe"):
    return wd.ProcInfo(
        pid=pid,
        ppid=ppid,
        name=name,
        cmdline=cmd,
        create_time=NOW - age,
        rss_bytes=int(rss_mb * 1024 * 1024),
    )


def flags_by_pid(flags):
    return {f.proc.pid: f.reasons for f in flags}


def test_over_age_flagged_only_when_in_scope():
    procs = [
        P(
            100, 1, "python -m src.pipelines.xbrl.retrieval_pipeline", age=3600
        ),  # XbrlRag, old
        P(200, 1, "python -m some.other.tool", age=99999),  # not XbrlRag
    ]
    pol = wd.Policy(signature=SIG, max_age_secs=1800)
    flags = flags_by_pid(wd.find_runaways(procs, pol, NOW, self_pid=9))
    assert 100 in flags and any("over-age" in r for r in flags[100])
    assert 200 not in flags  # out of scope — never flagged


def test_over_rss_flagged():
    procs = [P(101, 1, "pytest test/unit", age=10, rss_mb=9000)]
    pol = wd.Policy(signature=SIG, max_rss_mb=8000)
    flags = flags_by_pid(wd.find_runaways(procs, pol, NOW, self_pid=9))
    assert any("over-rss" in r for r in flags[101])


def test_orphan_detection():
    # ppid 500 is not in the process list -> parent gone -> orphan.
    procs = [P(102, 500, "python -m src.ingest.sec_edgar.ingest", age=120)]
    pol = wd.Policy(signature=SIG, max_age_secs=None, flag_orphans=True)
    flags = flags_by_pid(wd.find_runaways(procs, pol, NOW, self_pid=9))
    assert 102 in flags and any("orphaned" in r for r in flags[102])


def test_fresh_orphan_ignored():
    # Orphan but younger than min_orphan_age_secs -> not flagged (spawn race).
    procs = [P(103, 500, "python -m src.foo", age=5)]
    pol = wd.Policy(signature=SIG, flag_orphans=True, min_orphan_age_secs=30)
    assert wd.find_runaways(procs, pol, NOW, self_pid=9) == []


def test_never_flags_self_parent_or_critical():
    procs = [
        P(9, 1, "python -m src.foo", age=99999),  # self
        P(7, 1, "python -m src.foo", age=99999),  # parent
        P(4, 0, "python -m src.foo", age=99999),  # critical
    ]
    pol = wd.Policy(signature=SIG, max_age_secs=0)
    flags = wd.find_runaways(procs, pol, NOW, self_pid=9, parent_pid=7)
    assert flags == []


def test_protected_pids_excluded():
    procs = [P(300, 1, "pytest test/unit", age=99999)]
    pol = wd.Policy(signature=SIG, max_age_secs=0, protected_pids={300})
    assert wd.find_runaways(procs, pol, NOW, self_pid=9) == []


def test_root_pid_scope_flags_descendants_only():
    procs = [
        P(1000, 1, "bash run.sh", age=99999),  # root (not a descendant of itself)
        P(1001, 1000, "sleep 999", age=99999),  # child of root
        P(1002, 1001, "python weird.py", age=99999),  # grandchild
        P(2000, 1, "python -m src.foo", age=99999),  # XbrlRag-signature but NOT under root
    ]
    pol = wd.Policy(root_pid=1000, max_age_secs=1800)  # signature ignored when root set
    flags = flags_by_pid(wd.find_runaways(procs, pol, NOW, self_pid=9))
    assert set(flags) == {
        1001,
        1002,
    }  # descendants only; 2000 out of scope, 1000 is root


def test_no_scope_no_flags():
    # Signature that matches nothing and no root -> nothing flagged.
    procs = [P(400, 1, "notepad.exe", age=99999)]
    pol = wd.Policy(signature=SIG, max_age_secs=0)
    assert wd.find_runaways(procs, pol, NOW, self_pid=9) == []


def test_never_flags_ancestor_chain():
    # Watchdog (pid 9) launched by bash 8 launched by bash 7 — all carry the
    # XbrlRag marker in their cmdline. None of the ancestors may be flagged.
    procs = [
        P(7, 1, "bash -c python -m src.foo", age=99999),
        P(8, 7, "bash -c python -m src.foo", age=99999),
        P(9, 8, "python scripts/harness/watchdog.py", age=99999),
        P(500, 1, "python -m src.foo", age=99999),  # unrelated XbrlRag proc -> flagged
    ]
    pol = wd.Policy(signature=SIG, max_age_secs=0)
    flags = flags_by_pid(wd.find_runaways(procs, pol, NOW, self_pid=9, parent_pid=8))
    assert set(flags) == {500}  # ancestors 7,8 and self 9 all excluded


def test_ancestors_helper():
    procs = [P(1, 0, "init"), P(7, 1, "a"), P(8, 7, "b"), P(9, 8, "c")]
    assert wd._ancestors(procs, 9) == {
        1,
        7,
        8,
    }  # full chain up to init; stops at ppid 0
    assert wd._ancestors(procs, 7) == {1}


def test_descendants_helper():
    procs = [P(1, 0, "init"), P(2, 1, "a"), P(3, 2, "b"), P(4, 1, "c")]
    assert wd._descendants(procs, 1) == {2, 3, 4}
    assert wd._descendants(procs, 2) == {3}
    assert wd._descendants(procs, 3) == set()
