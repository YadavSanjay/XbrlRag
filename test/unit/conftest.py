"""No Hypothesis deadline in plain unit runs.

Hypothesis's default 200 ms deadline fails healthy property tests when the suite runs
under `-n auto` or beside other agents' runs (DeadlineExceeded on screen-exact-fiscal-year,
frontier-sec-tools C14, sign-and-nil, ...). The dev profile drops the deadline only; the
example counts stay. A test's own `@settings(deadline=...)` still wins.

This lives in test/unit, not test/, on purpose: every spec lock records `test/conftest.py`
as must-not-exist, because a conftest above test/spec could change how locked tests run.
Loading a profile is process-wide, so a plain `pytest test/spec test/unit` that collects
this file gets it too; the tdd gate does not (`--confcutdir test/spec`, and it selects its
own profile with `--hypothesis-profile`).
"""

from hypothesis import settings

settings.register_profile("xbrlrag-unit", deadline=None)
settings.load_profile("xbrlrag-unit")
