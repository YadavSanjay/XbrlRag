"""Back-compat shim — the implementation now lives in ``src.eval.core.graders.numeric``.

WS-2 established this module as the *single* source of truth for numeric tolerance
and audited-value extraction; WS-9 / E1 moved it into the eval core and extended it
(``screen`` rows, fact-id extraction, narrative chunk ids) without forking it. This
module re-exports every public name so existing importers keep working unchanged:

* ``src.eval.xbrl.golden`` (the A3 grader)
* ``test/integration/conftest.py`` (``close_enough``, ``lookup_step_values``,
  ``compute_step_values``)

New code should import from ``src.eval.core.graders.numeric`` directly.
"""

from src.eval.core.graders.numeric import (
    all_fact_ids,  # noqa: F401
    all_numeric_values,
    close_enough,
    compute_step_values,
    has_fact_citation,
    lookup_step_values,
    matches_any,
    narrative_chunk_ids,
    screen_rows,
)

__all__ = [
    "all_fact_ids",
    "all_numeric_values",
    "close_enough",
    "compute_step_values",
    "has_fact_citation",
    "lookup_step_values",
    "matches_any",
    "narrative_chunk_ids",
    "screen_rows",
]
