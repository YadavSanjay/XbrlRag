"""C13: the rule against leakage (plan 5.2), watched at the boundaries and in the imports."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from src.pipelines.html10k.load_to_vectordb_pipeline import (
    Html10kLoadPipeline,
    list_annual_filings,
)

REPO = Path(__file__).resolve().parents[3]
_TABLE = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z_.]*)", re.IGNORECASE)
_ALLOWED_RETRIEVER = {"intent_router", "entity_extractor", "branch_dispatcher",
                      "xbrl_calculator_tool"}
ACCESSION = "0000000001-25-000001"
FILING = {
    "cik": "0000000001", "ticker": "AAA", "company_name": "Alpha Holdings",
    "accession_no": ACCESSION, "fiscal_year": 2024, "fiscal_period": "FY", "form_type": "10-K",
    "period_end": "2024-12-31",
}
IXBRL = (
    b'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL">'
    b'<head><title>t</title></head><body><p>Revenue was <ix:nonFraction name="us-gaap:Revenues" '
    b'contextRef="ctx-2024" unitRef="usd" id="fact-777" decimals="-6" scale="6" format="ixt:num">'
    b"1,200</ix:nonFraction> million.</p></body></html>"
)


def _tables(statements: list[str]) -> set[str]:
    return {name.split(".")[-1].lower() for sql in statements for name in _TABLE.findall(sql)}


def _new_modules() -> list[Path]:
    return [
        *sorted((REPO / "src" / "retriever" / "html10k").glob("*.py")),
        *sorted((REPO / "src" / "pipelines" / "html10k").glob("*.py")),
        REPO / "src" / "eval" / "systems" / "html_rag.py",
    ]


def _imports(path: Path) -> set[str]:
    """Every module a file imports, at any depth, by dotted name."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def _forbidden(module: str) -> bool:
    """Requirement 33, as a test on one dotted name."""
    parts = module.split(".")
    if parts[0] != "src":
        return False
    if module.startswith("src.retriever.xbrl."):
        return parts[3] not in _ALLOWED_RETRIEVER
    if module.startswith(("src.pipelines.xbrl", "src.eval.systems.xbrlrag_full")):
        return True
    if module.startswith("src.ingest.sec_edgar."):
        return parts[3] != "storage"
    return any(part.startswith("xbrl_") for part in parts)


@pytest.mark.criterion("C13")
def test_a_whole_answer_reads_only_the_resolvers_tables(make_arm, make_case, reply, table_chunk):
    # oracle: requirement 31 — every statement the arm ran, from the recording connection
    table = table_chunk()
    env = make_arm(
        [table], reply_text=reply(passages=[{"chunk_id": table["chunk_id"], "quote": "Net"}])
    )
    answer = env.arm.answer(make_case())
    assert answer.refused is False
    assert env.extractor.sql
    assert _tables(env.extractor.sql) <= {"entity_master", "fiscal_calendar"}
    assert answer.fact_ids == []


@pytest.mark.criterion("C13")
def test_only_the_html_collection_is_touched(make_arm, make_case, payload, chunk_id_of):
    # oracle: requirement 31 — the search and the expansion fetch, both on html10k_text
    accession = "0000000001-25-000001"
    neighbour = payload("n", start=100, end=200)
    kept = payload("k", next_chunk_id=chunk_id_of(accession, 100, 200))
    env = make_arm(
        [kept, neighbour], scores={"passage k": 0.875}, rerank_candidates=1, context_top_k=1
    )
    env.arm.answer(make_case())
    assert env.client.retrieve_calls
    assert env.client.touched == {"html10k_text"}


@pytest.mark.criterion("C13")
def test_the_load_reads_only_filing_metadata(registry_conn):
    # oracle: requirement 32 — the three tables, by name; no fact, element or chunk table
    conn = registry_conn([])
    assert list_annual_filings(conn, "D:/root") == []
    assert _tables(conn.sql) <= {"filing_registry", "entity_master", "accession"}
    assert "filing_registry" in _tables(conn.sql)


@pytest.mark.criterion("C13")
def test_no_new_module_imports_the_fact_store(make_arm, make_case):
    # oracle: requirement 33; the arm runs first, so this is judged on its real code
    assert make_arm().arm.answer(make_case()).selection["status"]
    offending = {
        path.relative_to(REPO).as_posix(): sorted(filter(_forbidden, _imports(path)))
        for path in _new_modules()
    }
    assert {k: v for k, v in offending.items() if v} == {}


@pytest.mark.criterion("C13")
def test_nothing_of_a_tagged_document_reaches_the_index(
    make_config, memory_store, fake_embedder, fake_qdrant
):
    # oracle: plan 5.2, through the load — the printed number stays, the tag's names do not
    key = "AAA/2024/10-K/a.htm"
    client, embedder = fake_qdrant(), fake_embedder()
    pipeline = Html10kLoadPipeline(
        store=memory_store({key: IXBRL}), embedder=embedder, client=client, config=make_config()
    )
    pipeline.load_filing(FILING, key)
    texts = [p.payload["text"] for p in client.uploaded]
    texts += [text for batch in embedder.batches for _, text in batch]
    assert "Revenue was 1,200 million." in texts[0]
    for text in texts:
        for trace in ("us-gaap", "ix:", "ctx-2024", "fact-777", "usd"):
            assert trace not in text


@pytest.mark.criterion("C13")
@pytest.mark.metamorphic
def test_a_poisoned_payload_changes_nothing_sent(make_arm, make_case, table_chunk):
    # oracle: requirement 16 and plan 5.2 — an excerpt is a closed set of keys, so XBRL fields on
    # a point, if a point ever had them, could not reach the model
    clean = make_arm([table_chunk()])
    clean.arm.answer(make_case())
    poisoned = make_arm(
        [table_chunk(xbrl_fact_ids=[777], xbrl_concepts=["us-gaap:Revenues"],
                     has_xbrl_facts=True)]
    )
    poisoned.arm.answer(make_case())
    assert poisoned.adapter.user_text() == clean.adapter.user_text()
    assert "us-gaap" not in poisoned.adapter.user_text()
    assert "777" not in poisoned.adapter.user_text()
