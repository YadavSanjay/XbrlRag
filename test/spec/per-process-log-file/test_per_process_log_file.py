"""Spec tests for per-process-log-file: each process logs to its own file, anchored at
the project root, with stale files pruned (docs/specs/per-process-log-file.md).

Pure: name derivation, template expansion and stale-file selection; no filesystem.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.logging_config import expand_log_filename, process_name, stale_log_files

DAY = 86_400
PATTERN = "xbrlrag_*.log*"
TEMPLATE = "logs/xbrlrag_{process}_{pid}.log"


def _name_oracle(argv):
    """C1 written out."""
    if not argv or argv[0] in ("", "-c", "-m"):
        return "python"
    base = re.split(r"[\\/]", argv[0])
    stem = base[-1].rsplit(".", 1)[0] if "." in base[-1] else base[-1]
    if stem == "__main__" and len(base) > 1:
        stem = base[-2]
    s = re.sub(r"[^a-z0-9_-]+", "_", stem.lower()).strip("_")
    return s or "python"


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_process_name_point_cases():
    # oracle: the definition, by hand
    assert process_name(["D:/Tools/AI/XbrlRag/scripts/eval/run_suite.py", "--kind", "x"]) == (
        "run_suite")
    assert process_name([r"D:\Tools\Anaconda3\envs\py312\Scripts\uvicorn.exe", "app"]) == (
        "uvicorn")
    assert process_name(["/usr/lib/python3/site-packages/pytest/__main__.py", "-q"]) == "pytest"
    assert process_name([r"D:\Tools\AI\XbrlRag\src\ingest\sec_edgar\ingest\__main__.py"]) == (
        "ingest")
    assert process_name(["-c"]) == "python"
    assert process_name([]) == "python"
    assert process_name(["C:/x/My Script!.py"]) == "my_script"


argv0 = st.one_of(
    st.just("-c"), st.just("-m"), st.just(""),
    st.builds(lambda d, n, e: f"{d}{n}{e}",
              st.sampled_from(["", "C:\\x\\", "/usr/bin/", "./a/b/"]),
              st.text(st.characters(codec="ascii", exclude_characters="/\\"), max_size=12),
              st.sampled_from(["", ".py", ".exe", ".tar.gz"])),
    st.builds(lambda d, p: f"{d}{p}/__main__.py",
              st.sampled_from(["/s/", "C:/s/"]),
              st.text(st.characters(codec="ascii", exclude_characters="/\\"), min_size=1,
                      max_size=10)),
)


@pytest.mark.criterion("C1")
@given(argv0, st.lists(st.text(max_size=5), max_size=3))
def test_process_name_matches_the_rule(first, rest):
    # oracle: C1 written out (`_name_oracle`); anchored by a fixed real entrypoint
    assert process_name([first, *rest]) == _name_oracle([first, *rest])
    assert process_name(["/x/run_suite.py", *rest]) == "run_suite"


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
def test_expand_point_cases():
    # oracle: the definition, by hand
    assert expand_log_filename(TEMPLATE, Path("/r"), "run_suite", 4242).as_posix() == (
        "/r/logs/xbrlrag_run_suite_4242.log")
    assert expand_log_filename("logs/xbrlrag_app.log", Path("/r"), "x", 1).as_posix() == (
        "/r/logs/xbrlrag_app.log")
    # An absolute template is not re-anchored. Built from this platform's own root, as a
    # drive-less "/var/log" is not absolute on Windows.
    anchor = Path(Path.cwd().anchor)
    template = str(anchor / "var" / "log" / "xbrlrag_{pid}.log")
    absolute = expand_log_filename(template, Path("/r"), "x", 7)
    assert absolute == anchor / "var" / "log" / "xbrlrag_7.log"


@pytest.mark.criterion("C2")
@given(st.from_regex(r"[a-z][a-z0-9_]{0,10}", fullmatch=True),
       st.integers(min_value=1, max_value=10**7))
def test_expand_substitutes_both_placeholders(proc, pid):
    # oracle: the definition — the expanded name is the template with both values put in
    out = expand_log_filename(TEMPLATE, Path("/r"), proc, pid)
    assert out.as_posix() == f"/r/logs/xbrlrag_{proc}_{pid}.log"


# --------------------------------------------------------------------------- C3
NOW = 1_000_000.0


@pytest.mark.criterion("C3")
def test_stale_point_case():
    # oracle: the definition, by hand — 8 days old is stale, 6 days is not, a non-matching
    # name never is
    entries = [
        (Path("logs/xbrlrag_run_suite_1.log"), NOW - 8 * DAY),
        (Path("logs/xbrlrag_uvicorn_2.log.1"), NOW - 6 * DAY),
        (Path("logs/other.log"), NOW - 8 * DAY),
        (Path("logs/xbrlrag_app.log.5"), NOW - 30 * DAY),
    ]
    assert stale_log_files(entries, NOW, 7 * DAY, PATTERN) == [
        Path("logs/xbrlrag_run_suite_1.log"), Path("logs/xbrlrag_app.log.5")]


names = st.one_of(
    st.from_regex(r"xbrlrag_[a-z]{1,6}_[0-9]{1,5}\.log(\.[1-5])?", fullmatch=True),
    st.from_regex(r"[a-z]{1,8}\.(log|txt)", fullmatch=True),
)
entry_lists = st.lists(st.tuples(names, st.floats(min_value=0, max_value=60 * DAY)),
                       max_size=8)


@pytest.mark.criterion("C3")
@given(entry_lists, st.floats(min_value=0, max_value=60 * DAY))
def test_stale_matches_the_rule(raw, max_age):
    # oracle: C3 written out; a fixed ancient matching file anchors a non-empty result
    entries = [(Path("logs") / n, NOW - age) for n, age in raw]
    entries.append((Path("logs/xbrlrag_x_1.log"), NOW - 61 * DAY))
    expected = [p for p, m in entries if fnmatch.fnmatch(p.name, PATTERN) and NOW - m > max_age]
    assert stale_log_files(entries, NOW, max_age, PATTERN) == expected
    assert expected


# --------------------------------------------------------------------------- C4
@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(argv0, entry_lists, st.floats(0, 30 * DAY), st.floats(0, 30 * DAY))
def test_properties(first, raw, a, b):
    # oracle: the C4 properties, checked directly
    assert re.fullmatch(r"[a-z0-9_-]+", process_name([first]))
    lo, hi = sorted((a, b))
    entries = [(Path("logs") / n, NOW - age) for n, age in raw]
    entries.append((Path("logs/xbrlrag_x_1.log"), NOW - 61 * DAY))
    loose = stale_log_files(entries, NOW, lo, PATTERN)
    strict = stale_log_files(entries, NOW, hi, PATTERN)
    assert set(strict) <= set(loose) and strict
    assert all(fnmatch.fnmatch(p.name, PATTERN) for p in loose)
    p1 = expand_log_filename(TEMPLATE, Path("/r"), "x", 1)
    p2 = expand_log_filename(TEMPLATE, Path("/r"), "x", 2)
    assert p1 != p2


@pytest.mark.criterion("C4")
def test_properties_point_case():
    # oracle: the C4 properties on fixed inputs
    assert process_name(["???"]) == "python"
    entries = [(Path("logs/xbrlrag_a_1.log"), NOW - 10 * DAY),
               (Path("logs/xbrlrag_b_2.log"), NOW - 3 * DAY)]
    assert stale_log_files(entries, NOW, 7 * DAY, PATTERN) == [Path("logs/xbrlrag_a_1.log")]
    assert stale_log_files(entries, NOW, 1 * DAY, PATTERN) == [p for p, _ in entries]
