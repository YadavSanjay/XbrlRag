"""C5: a re-graded run is a new run that says where it came from."""

from __future__ import annotations

import datetime
import hashlib
import json
import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.checkpoint.run import ResumeRefused, check_resume
from src.eval.core.graders.honesty import GRADER_VERSION
from src.eval.core.regrade import regrade_report

SOURCE_HASH = "sha256:" + "ab" * 32  # the conftest source's resume_identity.suite_hash


def _hash(cases) -> str:
    # The eval-run-checkpoint suite hash, computed here from its definition.
    docs = [c.to_dict() for c in sorted(cases, key=lambda c: c.id)]
    text = json.dumps(docs, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture()
def regraded(lookup_case, answer, build_report, as_json_source, rekey):
    cases = [lookup_case("p1", 100.0), lookup_case("p2", 200.0)]
    report = build_report(
        [
            (cases[0], answer([100.0], query_id="q-1", cost_usd=0.125, latency_ms=1234.5)),
            (cases[1], answer([250.0], query_id="q-2", cost_usd=0.0625, latency_ms=99.5)),
        ],
        n_planned=3,
        status="ABORTED",
    )
    source = as_json_source(report)
    new_cases = [cases[0], rekey(cases[1], 250.0)]

    def run():
        # Called in the test body, so a stub fails the test rather than its setup.
        out = regrade_report(
            source, new_cases, suite="xbrl_v2_dev", suite_version="2",
            corpus_fingerprint="pm:new", regraded_at="2026-11-06T09:00:00+00:00",
        )
        return source, new_cases, out

    return run


@pytest.mark.criterion("C5")
def test_run_fields(regraded):
    # oracle: requirement 6, field by field
    source, _, out = regraded()
    assert re.fullmatch(r"eval-[0-9a-f]{12}", out.run_id)
    assert out.run_id != source["run_id"]
    assert (out.suite, out.suite_version, out.corpus_fingerprint) == (
        "xbrl_v2_dev", "2", "pm:new",
    )
    assert (out.system, out.tier, out.git_sha) == ("xbrlrag_full", "standard", "1234abc")
    assert out.started_at == "2026-11-04T10:00:00+00:00"
    assert out.finished_at == "2026-11-04T11:00:00+00:00"
    assert out.status == "ABORTED"
    assert out.n_planned == 3 and out.n_skipped == 1
    assert out.notes == "regrade of eval-aaaaaaaaaaaa"


@pytest.mark.criterion("C5")
def test_regrade_provenance(regraded):
    # oracle: requirement 6's table; suite hash computed with hashlib from its definition
    _, new_cases, out = regraded()
    assert out.config["regrade"] == {
        "source_run_id": "eval-aaaaaaaaaaaa",
        "root_run_id": "eval-aaaaaaaaaaaa",
        "source_suite": "xbrl_v2_dev",
        "source_suite_version": "1",
        "source_suite_hash": SOURCE_HASH,
        "source_grader_version": GRADER_VERSION,  # the JSON source carries the current version
        "suite_hash": _hash(new_cases),
        "grader_version": GRADER_VERSION,
        "regraded_at": "2026-11-06T09:00:00+00:00",
        "n_regraded": 2,
        "n_grade_changed": 1,
        "judge_stale": [],
        "judge_rerun": False,
        # regrade-raw-values requirement 4: the source was graded by grade_case, which stores
        # raw_numeric_values on every result (requirement 1), so the re-grade read raw values.
        "values": "raw",
    }
    assert out.config["split"] == "dev"
    assert out.config["keyfree"] == {"n_pairs": 0}  # carried with the copied config


@pytest.mark.criterion("C5")
def test_a_regraded_run_cannot_be_resumed(regraded):
    # oracle: requirement 6 — resume_identity is removed, and check_resume refuses without it
    source, _, out = regraded()
    assert "resume_identity" not in out.config
    with pytest.raises(ResumeRefused):
        check_resume(out.config, source["config"]["resume_identity"])


@pytest.mark.criterion("C5")
def test_per_case_execution_metadata_is_exact(regraded):
    # oracle: requirement 5 — the stored values, copied
    _, _, out = regraded()
    by_id = {r.case.id: r for r in out.results}
    assert (by_id["p1"].query_id, by_id["p2"].query_id) == ("q-1", "q-2")
    # tolerance: copied floats, exactly representable
    assert by_id["p1"].cost_usd == pytest.approx(0.125, abs=0.0)
    assert by_id["p2"].latency_ms == pytest.approx(99.5, abs=0.0)
    assert out.metrics.total_cost_usd == pytest.approx(0.1875, abs=1e-12)


@pytest.mark.criterion("C5")
def test_default_regraded_at_is_a_utc_timestamp(lookup_case, answer, build_report,
                                                as_json_source):
    # oracle: requirement 6 — regraded_at defaults to a UTC time in ISO 8601 (the clock itself
    # is not read by spec tests)
    case = lookup_case("t1", 1.0)
    source = as_json_source(build_report([(case, answer([1.0]))]))
    out = regrade_report(source, [case], suite="s", suite_version="v", corpus_fingerprint=None)
    stamp = datetime.datetime.fromisoformat(out.config["regrade"]["regraded_at"])
    assert stamp.utcoffset() == datetime.timedelta(0)


@pytest.mark.criterion("C5")
def test_suite_hash_covers_only_the_matched_cases(lookup_case, answer, build_report,
                                                  as_json_source):
    # oracle: requirement 6 — suite_hash(matched cases), computed here with hashlib; cases the
    # run never answered do not enter it
    case = lookup_case("u1", 1.0)
    source = as_json_source(build_report([(case, answer([1.0]))]))
    out = regrade_report(source, [lookup_case("u0", 5.0), case, lookup_case("u2", 7.0)],
                         suite="s", suite_version="v", corpus_fingerprint=None)
    assert out.config["regrade"]["suite_hash"] == _hash([case])


@pytest.mark.criterion("C5")
@given(n_hops=st.integers(min_value=1, max_value=4))
def test_root_run_id_survives_a_chain(lookup_case, answer, build_report, as_json_source,
                                      n_hops):
    # oracle: requirement 6 — root_run_id is the first run's, source_run_id the previous hop's,
    # and source_suite_hash falls back to the previous re-grade's suite_hash
    case = lookup_case("h1", 10.0)
    current = as_json_source(build_report([(case, answer([10.0]))]))
    previous_id, previous_hash = "eval-aaaaaaaaaaaa", SOURCE_HASH
    for hop in range(n_hops):
        rid = f"eval-{hop + 1:012x}"
        out = regrade_report(current, [case], suite="s", suite_version=str(hop),
                             corpus_fingerprint=None, run_id=rid)
        assert out.run_id == rid
        assert out.config["regrade"]["source_run_id"] == previous_id
        assert out.config["regrade"]["root_run_id"] == "eval-aaaaaaaaaaaa"
        assert out.config["regrade"]["source_suite_hash"] == previous_hash
        previous_id, previous_hash = rid, _hash([case])
        current = json.loads(json.dumps(out.to_dict(), default=str))
