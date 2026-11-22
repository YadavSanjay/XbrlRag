"""C1-C6: the SEC transforms load, a missing plugin is loud, and stored facts repair in place."""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.sec_edgar.ingest.arelle_xbrl_ingestor import ArelleXBRLIngestor
from src.ingest.sec_edgar.ingest.transform_backfill import (
    apply_fact_updates,
    plan_fact_updates,
)
from src.ingest.sec_edgar.xbrl.promotion import PublishGate

REPO_ROOT = Path(__file__).resolve().parents[3]
MARKER = "(ixTransformValueError)"
COMMIT = "4891c4c9889c77af247428ceff4fa7cc52d534ea"
SHA_INIT = "2296586f945ffd95ab37d3d4147c4e45f42ce4e5f397f61143e053208adefbf1"
SHA_TEXT2NUM = "6c9b26354320a2fb34fe380cdc71ff7f8d1cc712811e6b10c661e512f0383409"


def _ingestor() -> ArelleXBRLIngestor:
    return ArelleXBRLIngestor(
        arelle_cmd="arelleCmdLine",
        sec_root=Path("."),
        db_conn_params={"host": "h", "port": 5432, "dbname": "d", "user": "u", "password": "p"},
        persist_audit=False,
    )


def _dockerfile() -> str:
    return (REPO_ROOT / "infra/docker/Dockerfile.ingest").read_text(encoding="utf-8")


def _candidate(**extra) -> dict:
    return {
        "accession_no": "0000000001-24-000001", "cik": "0000000001", "fiscal_year": 2024,
        "fiscal_period": "FY", "form_type": "10-K", "anchor_coverage_pct": 1.0,
        "completeness_pct": 0.9, "sum_of_parts_failures": 0, **extra,
    }


def _stored(*values: str) -> list[dict]:
    return [{"fact_id": 100 + i, "xml_id": f"f-{i + 1}", "fact_value": value}
            for i, value in enumerate(values)]


def _exported(*rows: tuple) -> dict:
    return {f"f-{i + 1}": {"value": value, "effective_value": effective}
            for i, (value, effective) in enumerate(rows)}


@pytest.mark.criterion("C1")
def test_the_plugins_load_in_order():
    # oracle: requirement 1
    argv = _ingestor()._arelle_argv(Path("/filings/a.htm"))
    plugins = argv[argv.index("--plugins") + 1].split("|")
    assert len(plugins) == 3
    assert plugins[0].endswith("xbrl_db_ixbrl_patch.py")
    assert plugins[1:] == ["EDGAR/transform", "xbrlDB"]


@pytest.mark.criterion("C1")
@given(name=st.text(alphabet="abcdefgh-0123456789", min_size=1, max_size=20))
def test_any_filing_loads_the_same_plugins(name):
    # oracle: requirement 1, the plugins don't depend on the filing
    argv = _ingestor()._arelle_argv(Path(f"/filings/{name}.htm"))
    assert argv[argv.index("--plugins") + 1].split("|")[1:] == ["EDGAR/transform", "xbrlDB"]


@pytest.mark.criterion("C2")
def test_the_image_pins_the_plugin():
    # oracle: the two files at that commit, hashed on 2026-11-01
    text = _dockerfile()
    assert COMMIT in text
    assert SHA_INIT in text
    assert SHA_TEXT2NUM in text
    assert "sha256sum" in text
    assert "EDGAR/transform" in text


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
def test_each_hash_names_one_file():
    # oracle: requirement 2, a hash used twice would check one file and not the other
    text = _dockerfile()
    assert text.count(SHA_INIT) == 1
    assert text.count(SHA_TEXT2NUM) == 1


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("enforce", [False, True])
def test_a_transform_error_holds_the_filing_back(enforce):
    # oracle: requirement 3, hard whatever gate_b_enforce says
    result = PublishGate(gate_b_enforce=enforce)._decide(_candidate(transform_error_facts=43))
    assert result.gate_a_passed is False
    assert result.is_published is False
    assert any("43" in reason and "GATE-A" in reason for reason in result.held_back_reasons)


@pytest.mark.criterion("C3")
@pytest.mark.parametrize(
    "extra", [{}, {"transform_error_facts": None}, {"transform_error_facts": 0}]
)
def test_no_count_passes_as_before(extra):
    # oracle: requirement 3; the first line is the case the base code gets wrong
    assert PublishGate()._decide(_candidate(transform_error_facts=1)).is_published is False
    assert PublishGate()._decide(_candidate(**extra)).is_published is True


@pytest.mark.criterion("C3")
@given(count=st.integers(min_value=0, max_value=100_000))
def test_published_exactly_at_zero(count):
    # oracle: requirement 3
    result = PublishGate()._decide(_candidate(transform_error_facts=count))
    assert result.is_published is (count == 0)


@pytest.mark.criterion("C3")
def test_the_candidate_sql_supplies_the_count():
    # oracle: requirement 3, the rule is empty without the field
    sql = PublishGate._CANDIDATE_SQL
    assert "transform_error_facts" in sql
    assert re.search(r"fact_value\s*=\s*'\(ixTransformValueError\)'", sql)


@pytest.mark.criterion("C4")
def test_only_the_marker_rows_are_planned():
    # oracle: requirements 4 and 5, worked by hand
    stored = _stored(MARKER, MARKER, "1200")
    exported = _exported(("P5Y", None), ("3", "3"), ("1200", "1200"))
    assert plan_fact_updates(stored, exported) == [
        (100, "P5Y", None),
        (101, "3", Decimal("3")),
    ]


@pytest.mark.criterion("C4")
def test_a_numeric_value_is_exact():
    # oracle: requirement 5; a float cannot hold these 18 digits
    digits = "0.123456789012345678"
    plan = plan_fact_updates(_stored(MARKER), _exported((digits, digits)))
    assert plan[0][2] == Decimal(digits)
    assert isinstance(plan[0][2], Decimal)


@pytest.mark.criterion("C4")
@given(marks=st.lists(st.booleans(), min_size=1, max_size=30))
def test_one_update_per_marker_row(marks):
    # oracle: requirement 4, in any filing
    stored = [{"fact_id": i, "xml_id": f"f-{i}", "fact_value": MARKER if mark else "1"}
              for i, mark in enumerate(marks)]
    exported = {row["xml_id"]: {"value": "1", "effective_value": "1"} for row in stored}
    plan = plan_fact_updates(stored, exported)
    assert [update[0] for update in plan] == [i for i, mark in enumerate(marks) if mark]


@pytest.mark.criterion("C5")
@pytest.mark.parametrize(
    "stored, exported",
    [
        (_stored(MARKER, "1"), {"f-2": {"value": "1", "effective_value": "1"}}),
        (_stored(MARKER), _exported((MARKER, None))),
        (_stored("1"), _exported(("2", "2"))),
        (_stored("1"), _exported(("1", "1"), ("9", "9"))),
    ],
    ids=["marker_row_missing", "still_the_marker", "another_value_differs", "unknown_id"],
)
def test_any_doubt_stops_the_filing(stored, exported):
    # oracle: requirement 6
    with pytest.raises(ValueError):
        plan_fact_updates(stored, exported)


@pytest.mark.criterion("C5")
@given(size=st.integers(min_value=1, max_value=20), pick=st.integers(min_value=0, max_value=19))
def test_removing_any_marker_row_raises(size, pick):
    # oracle: requirement 6
    stored = [{"fact_id": i, "xml_id": f"f-{i}", "fact_value": MARKER} for i in range(size)]
    exported = {row["xml_id"]: {"value": "x", "effective_value": None} for row in stored}
    del exported[f"f-{pick % size}"]
    with pytest.raises(ValueError):
        plan_fact_updates(stored, exported)


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
def test_repeating_plans_nothing():
    # oracle: requirement 8; the first call is the case that needs the code to exist
    assert len(plan_fact_updates(_stored(MARKER), _exported(("P5Y", None)))) == 1
    assert plan_fact_updates(_stored("P5Y"), _exported(("P5Y", None))) == []


class _Cursor:
    """The database boundary: records statements and reports a row count."""

    def __init__(self, rowcount: int = 1) -> None:
        self.rowcount = rowcount
        self.calls: list[tuple] = []
        self.commits = 0

    def execute(self, sql: str, params: tuple) -> None:
        self.calls.append((" ".join(sql.split()), tuple(params)))

    @property
    def connection(self) -> _Cursor:
        return self

    def commit(self) -> None:
        self.commits += 1


@pytest.mark.criterion("C6")
def test_each_update_is_one_guarded_statement():
    # oracle: requirement 7
    cursor = _Cursor()
    apply_fact_updates(cursor, [(7, "3", Decimal("3")), (8, "P5Y", None)])
    expected = ("UPDATE fact SET fact_value = %s, effective_value = %s "
                "WHERE fact_id = %s AND fact_value = %s")
    assert cursor.calls == [
        (expected, ("3", Decimal("3"), 7, MARKER)),
        (expected, ("P5Y", None, 8, MARKER)),
    ]
    assert cursor.commits == 0


@pytest.mark.criterion("C6")
@pytest.mark.parametrize("rowcount", [0, 2])
def test_another_row_count_raises(rowcount):
    # oracle: requirement 7, and the error names the fact
    with pytest.raises(RuntimeError, match="7777"):
        apply_fact_updates(_Cursor(rowcount), [(7777, "3", Decimal("3"))])


@pytest.mark.criterion("C6")
@given(
    ids=st.lists(st.integers(min_value=1, max_value=10**6), min_size=1, max_size=20, unique=True)
)
def test_any_updates_reach_the_parameters(ids):
    # oracle: requirement 7
    cursor = _Cursor()
    apply_fact_updates(cursor, [(i, str(i), None) for i in ids])
    assert [call[1] for call in cursor.calls] == [(str(i), None, i, MARKER) for i in ids]
