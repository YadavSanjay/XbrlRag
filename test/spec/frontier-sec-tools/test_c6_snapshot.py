"""C6: the dated snapshot keeps a run comparable when SEC data changes."""

from __future__ import annotations

import hashlib

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.sec_tools.client import SecSnapshot, SecSnapshotMissError

AS_OF = "2024-06-30"
URL = "https://data.sec.gov/submissions/CIK0000000001.json"
MISSING = "https://data.sec.gov/submissions/CIK0000000404.json"
STAMP = "2024-07-01T12:00:00Z"


def _id_of(entries: list[tuple[str, int, bytes]]) -> str:
    lines = sorted(f"{u} {s} {hashlib.sha256(b).hexdigest()}" for u, s, b in entries)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


@pytest.mark.criterion("C6")
def test_a_held_url_sends_no_request_even_after_the_sec_changes(make_client):
    # oracle: requirement 10 — the first response is the response for good
    client, _, sec = make_client()
    first = client.get(URL)
    sec.pages[URL] = (200, b'{"changed": true}')
    second = client.get(URL)
    assert second == first
    assert len(sec.calls) == 1
    assert second[1] != b'{"changed": true}'


@pytest.mark.criterion("C6")
def test_a_reopened_snapshot_holds_what_it_held(make_client, tmp_path):
    # oracle: requirement 10 — a later run reads the same bytes
    client, _, sec = make_client(root=tmp_path)
    first = client.get(URL)
    sec.pages[URL] = (200, b'{"changed": true}')
    later, _, later_sec = make_client(root=tmp_path)
    later_sec.pages[URL] = (200, b'{"changed": true}')
    assert later.get(URL) == first
    assert later_sec.calls == []


@pytest.mark.criterion("C6")
def test_a_404_is_held(make_client):
    # oracle: requirement 10 — a concept a filer lacks must stay absent on a rerun
    client, snapshot, sec = make_client()
    client.get(MISSING)
    client.get(MISSING)
    assert len(sec.calls) == 1
    assert snapshot.lookup(MISSING)[0] == 404


@pytest.mark.criterion("C6")
def test_a_failure_is_not_held(tmp_path):
    # oracle: requirement 10 — only 200 and 404 are answers
    snapshot = SecSnapshot(tmp_path, as_of=AS_OF)
    with pytest.raises(ValueError):
        snapshot.store(URL, 503, b"")
    assert snapshot.lookup(URL) is None


@pytest.mark.criterion("C6")
def test_store_on_a_held_url_changes_nothing(tmp_path):
    # oracle: requirement 10 — append-only
    snapshot = SecSnapshot(tmp_path, as_of=AS_OF)
    snapshot.store(URL, 200, b"first")
    before = snapshot.provenance()["snapshot_id"]
    snapshot.store(URL, 200, b"second")
    assert snapshot.lookup(URL) == (200, b"first")
    assert snapshot.provenance()["snapshot_id"] == before


@pytest.mark.criterion("C6")
def test_a_snapshot_has_one_as_of(tmp_path):
    # oracle: requirement 10
    snapshot = SecSnapshot(tmp_path, as_of=AS_OF)
    snapshot.store(URL, 200, b"x")
    with pytest.raises(ValueError):
        SecSnapshot(tmp_path, as_of="2024-07-31")


@pytest.mark.criterion("C6")
@pytest.mark.parametrize("as_of", ["2024", "06/30/2024", "2024-13-40", ""])
def test_a_malformed_date_is_refused(tmp_path, as_of):
    # oracle: requirement 10 — YYYY-MM-DD
    with pytest.raises(ValueError):
        SecSnapshot(tmp_path, as_of=as_of)


@pytest.mark.criterion("C6")
def test_a_frozen_snapshot_sends_nothing(make_client, tmp_path):
    # oracle: requirement 10 — a held URL reads, and any other raises without a request
    client, _, _ = make_client(root=tmp_path)
    expected = client.get(URL)
    frozen, _, sec = make_client(root=tmp_path, frozen=True)
    assert frozen.get(URL) == expected
    with pytest.raises(SecSnapshotMissError):
        frozen.get(MISSING)
    assert sec.calls == []


@pytest.mark.criterion("C6")
def test_a_frozen_snapshot_refuses_a_store(tmp_path):
    # oracle: requirement 10
    SecSnapshot(tmp_path, as_of=AS_OF)
    frozen = SecSnapshot(tmp_path, as_of=AS_OF, frozen=True)
    with pytest.raises(SecSnapshotMissError):
        frozen.store(URL, 200, b"x")


@pytest.mark.criterion("C6")
def test_provenance_names_the_data(tmp_path):
    # oracle: requirement 10 — the id is the hash computed here
    times = iter(["2024-07-01T12:00:00Z", "2024-07-03T09:30:00Z"])
    snapshot = SecSnapshot(tmp_path, as_of=AS_OF, wall_clock=lambda: next(times))
    snapshot.store(URL, 200, b"a")
    snapshot.store(MISSING, 404, b"b")
    assert snapshot.provenance() == {
        "as_of": AS_OF,
        "frozen": False,
        "urls": 2,
        "first_fetched": "2024-07-01T12:00:00Z",
        "last_fetched": "2024-07-03T09:30:00Z",
        "snapshot_id": _id_of([(URL, 200, b"a"), (MISSING, 404, b"b")]),
    }


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
def test_two_runs_on_one_snapshot_see_the_same_tool_results(make_toolbox, fake_sec):
    # oracle: requirement 10 — the same question over one snapshot, whatever the SEC says later
    toolbox, sec = make_toolbox()
    first = toolbox.execute("list_filings", {"cik": "1"}).payload
    sec.pages.clear()
    second = toolbox.execute("list_filings", {"cik": "1"}).payload
    assert first == second
    assert first["rows"]


_ENTRY = st.tuples(st.integers(0, 40), st.sampled_from([200, 404]), st.binary(max_size=16))


@pytest.mark.criterion("C6")
@given(entries=st.lists(_ENTRY, min_size=1, max_size=12, unique_by=lambda e: e[0]),
       seed=st.randoms(use_true_random=False))
def test_the_id_ignores_order_and_the_manifest_only_grows(tmp_path_factory, entries, seed):
    # oracle: requirement 10 — the id is of the sorted lines, and store never removes
    rows = [(f"https://data.sec.gov/x/{n}.json", s, b) for n, s, b in entries]
    forward = SecSnapshot(tmp_path_factory.mktemp("a"), as_of=AS_OF, wall_clock=lambda: STAMP)
    count = 0
    for url, status, body in rows:
        forward.store(url, status, body)
        count += 1
        assert forward.provenance()["urls"] == count
    shuffled = list(rows)
    seed.shuffle(shuffled)
    other = SecSnapshot(tmp_path_factory.mktemp("b"), as_of=AS_OF, wall_clock=lambda: STAMP)
    for url, status, body in shuffled:
        other.store(url, status, body)
    assert forward.provenance()["snapshot_id"] == other.provenance()["snapshot_id"]
    assert forward.provenance()["snapshot_id"] == _id_of(rows)
