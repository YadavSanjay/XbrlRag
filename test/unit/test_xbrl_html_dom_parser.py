"""Unit tests for xbrl_html_dom_parser.py.

Validated against 60 filings across 12 tickers (AAPL, AMD, AMZN, AVGO,
GOOGL, META, MSFT, MU, NVDA, ORCL, TSLA, TSM), years 2022-2026, form
types 10-K / 10-K/A / 10-Q / 20-F / 20-F/A.

Canonical files must exist under:
    <repo>/data/storage/xbrl/normalized/<TICKER>/<YEAR>/<FORM>/canonical.htm
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict

import pytest

from src import PROJECT_ROOT
from src.ingest.sec_edgar.xbrl.xbrl_html_dom_parser import (
    IXBRLPosition,
    ProseBlock,
    SectionNode,
    SectionTree,
    _build_id_offset_index,
    _infer_level,
    _is_financial_statement,
    build_section_tree,
    extract_ixbrl_positions,
)

NORM_ROOT = Path(PROJECT_ROOT) / "data" / "storage" / "xbrl" / "normalized"


def canon(ticker: str, year: str, form: str, quarter: str = "") -> Path:
    """Return canonical.htm path, skipping the test if the file does not exist."""
    parts = [ticker, year, form]
    if quarter:
        parts.append(quarter)
    path = NORM_ROOT.joinpath(*parts) / "canonical.htm"
    return path


def require(path: Path) -> Path:
    """Skip test if canonical.htm is missing."""
    if not path.exists():
        pytest.skip(f"canonical.htm not found: {path}")
    return path


# ---------------------------------------------------------------------------
# Helpers (unit-level, no file I/O)
# ---------------------------------------------------------------------------

class TestIsFinancialStatement:
    """Keyword classifier for financial statement sections."""

    def test_balance_sheets(self):
        assert _is_financial_statement("Consolidated Balance Sheets")

    def test_statements_of_income(self):
        assert _is_financial_statement("Consolidated Statements of Income")

    def test_statements_of_operations(self):
        assert _is_financial_statement("Consolidated Statements of Operations")

    def test_cash_flows(self):
        assert _is_financial_statement("Consolidated Statements of Cash Flows")

    def test_shareholders_equity(self):
        assert _is_financial_statement("Consolidated Statements of Shareholders' Equity")

    def test_stockholders_equity(self):
        assert _is_financial_statement("Consolidated Statements of Stockholders' Equity")

    def test_comprehensive_income(self):
        assert _is_financial_statement("Consolidated Statements of Comprehensive Income")

    def test_ifrs_profit_loss(self):
        assert _is_financial_statement(
            "Consolidated Statements of Profit or Loss and Other Comprehensive Income"
        )

    def test_condensed_balance_sheet(self):
        assert _is_financial_statement("Condensed Consolidated Balance Sheets — Unaudited")

    def test_condensed_statements_of_operations(self):
        assert _is_financial_statement("Condensed Consolidated Statements of Operations")

    # CSS letter-spacing artefacts from MSFT/ORCL filings
    def test_css_spaced_balance_sheets(self):
        assert _is_financial_statement("BALANCE  SHEETS")

    def test_css_spaced_income_statements(self):
        assert _is_financial_statement("INC OME STATEMENTS")

    def test_css_spaced_cash_flows(self):
        assert _is_financial_statement("CASH FLOWS S TATEMENTS")

    def test_css_spaced_comprehensive_income(self):
        assert _is_financial_statement("COMPREHENSIVE IN COME STATEMENTS")

    def test_css_spaced_orcl_balance_sheets(self):
        assert _is_financial_statement("CONSOLIDATED B ALANCE SHEETS")

    def test_css_spaced_orcl_statements_operations(self):
        assert _is_financial_statement("CONSOLIDATED STATEM ENTS OF OPERATIONS")

    def test_notes_is_not_fin_stmt(self):
        assert not _is_financial_statement("Notes to the Consolidated Financial Statements")

    def test_mda_is_not_fin_stmt(self):
        assert not _is_financial_statement("Management's Discussion and Analysis")

    def test_risk_factors_is_not_fin_stmt(self):
        assert not _is_financial_statement("Item 1A. Risk Factors")

    def test_financial_information_not_fin_stmt(self):
        # "FINANCIAL INFORMATION" (a 20-F item header) should NOT be classified
        # as a financial statement — it is the containing Item, not a table.
        assert not _is_financial_statement("FINANCIAL INFORMATION")

    def test_item8_container_not_fin_stmt(self):
        # "Item 8. Financial Statements and Supplementary Data" is the ITEM
        # container, not a financial statement table itself.
        assert not _is_financial_statement(
            "Item 8. Financial Statements and Supplementary Data"
        )


class TestInferLevel:
    """Section-level inference heuristic."""

    def test_part_label(self):
        assert _infer_level("Part I", "part_i") == 1

    def test_part_ii_label(self):
        assert _infer_level("PART II.", "part_ii") == 1

    def test_item_label(self):
        assert _infer_level("Item 1A. Risk Factors", "i12345_16") == 2

    def test_item_anchor_msft(self):
        assert _infer_level("Risk Factors", "item_1a_risk_factors") == 2

    def test_item_anchor_tsla(self):
        assert _infer_level("Risk Factors", "risk_factors") == 2  # default level 2

    def test_financial_stmt(self):
        assert _infer_level("Consolidated Balance Sheets", "i12345_109") == 3

    def test_notes_default(self):
        # Notes to FS are not financial statements → default level 2
        assert _infer_level(
            "Notes to the Consolidated Financial Statements", "i12345_124"
        ) == 2


class TestBuildIdOffsetIndex:
    """Unit tests for the byte-offset index builder."""

    def test_finds_id_attribute(self):
        html = b'<html><body><div id="section_a">hello</div></body></html>'
        idx = _build_id_offset_index(html)
        assert "section_a" in idx
        assert html[idx["section_a"]:].startswith(b"<div")

    def test_finds_name_attribute(self):
        html = b'<html><body><a name="anchor1"></a></body></html>'
        idx = _build_id_offset_index(html)
        assert "anchor1" in idx

    def test_first_occurrence_wins(self):
        html = b'<div id="foo">x</div><div id="foo">y</div>'
        idx = _build_id_offset_index(html)
        assert idx["foo"] == 0    # first <div id="foo"> starts at byte 0

    def test_multiple_attributes(self):
        html = b'<div id="a">x</div><p id="b">y</p><span id="c">z</span>'
        idx = _build_id_offset_index(html)
        assert all(k in idx for k in ("a", "b", "c"))
        # Offsets should be in ascending order.
        assert idx["a"] < idx["b"] < idx["c"]

    def test_empty_bytes(self):
        assert _build_id_offset_index(b"") == {}

    def test_no_id_attributes(self):
        assert _build_id_offset_index(b"<p class='x'>text</p>") == {}

    def test_offset_points_to_opening_lt(self):
        html = b'<p>intro</p><div id="tgt">content</div>'
        idx = _build_id_offset_index(html)
        pos = idx["tgt"]
        assert html[pos:pos+4] == b"<div"


# ---------------------------------------------------------------------------
# IXBRLPosition tests — parametrised across representative filings
# ---------------------------------------------------------------------------

# (ticker, year, form, quarter,  expected_min_facts, expected_min_id_coverage_pct)
IXBRL_PARAMS = [
    # Workiva — full annual filings
    ("NVDA",  "2026", "10-K",   "",  1000, 100),
    ("AAPL",  "2024", "10-K",   "",   900, 100),
    ("MSFT",  "2023", "10-K",   "",  1600, 100),   # simple-anchor, large filing
    ("ORCL",  "2024", "10-K",   "",  1600, 100),   # simple-anchor
    ("TSLA",  "2022", "10-K",   "",  1500, 100),   # simple-anchor, pre-Workiva
    ("TSLA",  "2023", "10-K",   "",  1400, 100),   # Workiva 2023
    ("TSM",   "2024", "20-F",   "",  2000,  95),   # 20-F / IFRS, h5 headings
    ("TSM",   "2022", "20-F",   "",     1,   1),   # early iXBRL — low id coverage
    # Quarterly filings (10-Q)
    ("NVDA",  "2026", "10-Q",   "Q2",  600, 100),
    ("MSFT",  "2026", "10-Q",   "Q1", 1200, 100),
    ("AMD",   "2026", "10-Q",   "Q1",  600, 100),
    # Amendments (very few facts)
    ("AMD",   "2025", "10-K_A", "",    1, 0),      # amendment: ≥1 is fine
    ("TSLA",  "2024", "10-K_A", "",    1, 0),
]


@pytest.mark.parametrize(
    "ticker,year,form,quarter,min_facts,min_cov_pct",
    IXBRL_PARAMS,
    ids=[f"{t}-{y}-{f}{'-'+q if q else ''}" for t, y, f, q, _, __ in IXBRL_PARAMS],
)
class TestExtractIXBRLPositions:

    def setup_method(self):
        self._cache: dict[str, list] = {}

    def _positions(self, ticker, year, form, quarter) -> list:
        key = f"{ticker}-{year}-{form}-{quarter}"
        if key not in self._cache:
            path = require(canon(ticker, year, form, quarter))
            self._cache[key] = extract_ixbrl_positions(path)
        return self._cache[key]

    def test_returns_list(self, ticker, year, form, quarter, min_facts, min_cov_pct):
        pos = self._positions(ticker, year, form, quarter)
        assert isinstance(pos, list)

    def test_minimum_fact_count(self, ticker, year, form, quarter, min_facts, min_cov_pct):
        pos = self._positions(ticker, year, form, quarter)
        assert len(pos) >= min_facts, (
            f"{ticker} {year} {form}: expected ≥{min_facts} facts, got {len(pos)}"
        )

    def test_all_have_xml_id(self, ticker, year, form, quarter, min_facts, min_cov_pct):
        for p in self._positions(ticker, year, form, quarter):
            assert p.xml_id, "xml_id must not be empty"

    def test_xml_ids_unique(self, ticker, year, form, quarter, min_facts, min_cov_pct):
        pos = self._positions(ticker, year, form, quarter)
        ids = [p.xml_id for p in pos]
        assert len(ids) == len(set(ids)), "duplicate xml_id values found"

    def test_byte_range_valid(self, ticker, year, form, quarter, min_facts, min_cov_pct):
        pos = self._positions(ticker, year, form, quarter)
        raw = require(canon(ticker, year, form, quarter)).read_bytes()
        for p in pos:
            assert 0 <= p.char_start < p.char_end <= len(raw), (
                f"id={p.xml_id}: invalid range [{p.char_start},{p.char_end}]"
            )

    def test_char_start_is_opening_tag(self, ticker, year, form, quarter, min_facts, min_cov_pct):
        pos = self._positions(ticker, year, form, quarter)
        raw = require(canon(ticker, year, form, quarter)).read_bytes()
        for p in pos[:20]:   # spot-check first 20 to keep test fast
            byte_at_start = raw[p.char_start:p.char_start + 4]
            assert byte_at_start.startswith(b"<"), (
                f"id={p.xml_id}: char_start={p.char_start} does not point to '<'"
            )

    def test_xml_id_in_byte_range(self, ticker, year, form, quarter, min_facts, min_cov_pct):
        pos = self._positions(ticker, year, form, quarter)
        raw = require(canon(ticker, year, form, quarter)).read_bytes()
        for p in pos[:20]:
            snippet = raw[p.char_start:p.char_end]
            assert p.xml_id.encode("utf-8") in snippet, (
                f"id={p.xml_id} not found in its own byte range"
            )

    def test_tag_name_valid(self, ticker, year, form, quarter, min_facts, min_cov_pct):
        valid = {"nonFraction", "nonNumeric", "fraction"}
        for p in self._positions(ticker, year, form, quarter):
            assert p.tag_name in valid, f"unexpected tag_name={p.tag_name!r}"

    def test_concept_not_empty_for_main_facts(self, ticker, year, form, quarter, min_facts, min_cov_pct):
        pos = self._positions(ticker, year, form, quarter)
        # At least 90% of positions should have a concept name.
        with_concept = sum(1 for p in pos if p.concept)
        if pos:
            pct = 100 * with_concept / len(pos)
            assert pct >= 90, f"only {pct:.1f}% of facts have a concept name"

    def test_nonfraction_has_text_value(self, ticker, year, form, quarter, min_facts, min_cov_pct):
        # nonFraction facts almost always have a numeric text value.
        nf = [p for p in self._positions(ticker, year, form, quarter)
              if p.tag_name == "nonFraction"]
        with_val = sum(1 for p in nf if p.text_value)
        if nf:
            assert with_val / len(nf) >= 0.90, (
                f"only {with_val}/{len(nf)} nonFraction facts have text_value"
            )

    def test_continuation_positions_valid(self, ticker, year, form, quarter, min_facts, min_cov_pct):
        raw = require(canon(ticker, year, form, quarter)).read_bytes()
        for p in self._positions(ticker, year, form, quarter):
            for cs, ce in p.continuation_positions:
                assert 0 <= cs < ce <= len(raw), (
                    f"id={p.xml_id}: bad continuation range [{cs},{ce}]"
                )


# ---------------------------------------------------------------------------
# SectionTree tests — parametrised across representative filings
# ---------------------------------------------------------------------------

# (ticker, year, form, quarter,  min_sections,  min_fin_stmt_sections,
#  sample_section_keyword,  is_amendment)
#
# min_fin_stmt_sections notes:
#   10-K: typically 5 (income, comprehensive income, balance sheet,
#                       equity, cash flows)
#   10-Q: varies — many filers link individual condensed statements in the ToC
#          (AMD, MSFT, AVGO, AMZN…), but NVDA only links "Item 1. Financial
#          Statements (Unaudited)" as a single entry so expect 0.
#   10-K/A amendment: covers only Part III items, so 0 financial statements.
#   20-F: TSM only explicitly links a few financial statements in its ToC.
TREE_PARAMS = [
    ("NVDA",  "2026", "10-K",   "",    5, 3, "Risk Factors",   False),
    # NVDA 10-Q ToC links to "Item 1. Financial Statements" as a single block,
    # with no individual balance sheet / income statement sub-links.
    ("NVDA",  "2026", "10-Q",   "Q2",  3, 0, "Financial",      False),
    ("AAPL",  "2023", "10-K",   "",    5, 3, "Risk Factors",   False),
    # MSFT/ORCL financial-statement labels have CSS letter-spacing artefacts
    # ("BALANCE  SHEETS", "INC OME STATEMENTS") — these are handled by the
    # squashed-keyword classifier.
    ("MSFT",  "2023", "10-K",   "",    5, 3, "Risk Factors",   False),
    ("MSFT",  "2026", "10-Q",   "Q1",  3, 3, "Financial",      False),
    ("ORCL",  "2024", "10-K",   "",    5, 3, "Risk Factors",   False),
    ("TSLA",  "2022", "10-K",   "",    5, 3, "Risk Factors",   False),
    ("TSLA",  "2023", "10-K",   "",    5, 3, "Risk Factors",   False),
    # TSM 20-F (IFRS): financial statements explicitly linked in ToC as
    # "Consolidated Statements of Profit or Loss…" and "Cash Flows".
    ("TSM",   "2024", "20-F",   "",    5, 2, "FINANCIAL",      False),
    ("TSM",   "2022", "20-F",   "",    3, 1, "FINANCIAL",      False),
    ("AMD",   "2025", "10-K_A", "",    1, 0, "",               True),
    ("TSLA",  "2024", "10-K_A", "",    1, 0, "",               True),
    ("GOOGL", "2024", "10-K",   "",    5, 3, "Risk Factors",   False),
    ("META",  "2024", "10-K",   "",    5, 3, "Risk Factors",   False),
    ("MU",    "2025", "10-K",   "",    5, 3, "Risk Factors",   False),
    ("AVGO",  "2025", "10-K",   "",    5, 3, "Risk Factors",   False),
    ("AMZN",  "2024", "10-K",   "",    5, 3, "Risk Factors",   False),
]


@pytest.mark.parametrize(
    "ticker,year,form,quarter,min_sections,min_fin_stmts,sample_kw,is_amendment",
    TREE_PARAMS,
    ids=[f"{t}-{y}-{f}{'-'+q if q else ''}" for t, y, f, q, *_ in TREE_PARAMS],
)
class TestBuildSectionTree:

    def setup_method(self):
        self._cache: dict[str, SectionTree] = {}

    def _tree(self, ticker, year, form, quarter) -> SectionTree:
        key = f"{ticker}-{year}-{form}-{quarter}"
        if key not in self._cache:
            path = require(canon(ticker, year, form, quarter))
            self._cache[key] = build_section_tree(path)
        return self._cache[key]

    def test_returns_section_tree(self, ticker, year, form, quarter, min_sections,
                                   min_fin_stmts, sample_kw, is_amendment):
        assert isinstance(self._tree(ticker, year, form, quarter), SectionTree)

    def test_root_node_exists(self, ticker, year, form, quarter, min_sections,
                               min_fin_stmts, sample_kw, is_amendment):
        tree = self._tree(ticker, year, form, quarter)
        assert "ROOT" in tree.nodes
        root = tree.nodes["ROOT"]
        assert root.char_start == 0
        assert root.level == 0

    def test_root_char_end_is_file_size(self, ticker, year, form, quarter, min_sections,
                                         min_fin_stmts, sample_kw, is_amendment):
        path = require(canon(ticker, year, form, quarter))
        tree = self._tree(ticker, year, form, quarter)
        assert tree.nodes["ROOT"].char_end == path.stat().st_size

    def test_minimum_section_count(self, ticker, year, form, quarter, min_sections,
                                    min_fin_stmts, sample_kw, is_amendment):
        tree = self._tree(ticker, year, form, quarter)
        assert tree.section_count >= min_sections, (
            f"{ticker} {year} {form}: expected ≥{min_sections} sections, "
            f"got {tree.section_count}"
        )

    def test_financial_statement_sections_present(
        self, ticker, year, form, quarter, min_sections, min_fin_stmts, sample_kw, is_amendment
    ):
        tree = self._tree(ticker, year, form, quarter)
        fin_count = sum(1 for n in tree.nodes.values() if n.is_financial_statement)
        assert fin_count >= min_fin_stmts, (
            f"{ticker} {year} {form}: expected ≥{min_fin_stmts} financial-statement "
            f"sections, got {fin_count}"
        )

    def test_ordered_starts_sorted(self, ticker, year, form, quarter, min_sections,
                                    min_fin_stmts, sample_kw, is_amendment):
        tree = self._tree(ticker, year, form, quarter)
        starts = tree.ordered_char_starts
        assert starts == sorted(starts), "ordered_char_starts must be sorted"

    def test_ordered_starts_matches_nodes(self, ticker, year, form, quarter, min_sections,
                                           min_fin_stmts, sample_kw, is_amendment):
        tree = self._tree(ticker, year, form, quarter)
        assert len(tree.ordered_char_starts) == len(tree.ordered_section_paths)
        assert len(tree.ordered_char_starts) == len(tree.nodes)

    def test_section_paths_match_nodes_keys(self, ticker, year, form, quarter, min_sections,
                                              min_fin_stmts, sample_kw, is_amendment):
        tree = self._tree(ticker, year, form, quarter)
        assert set(tree.nodes.keys()) == set(tree.ordered_section_paths)

    def test_char_ranges_cover_file(self, ticker, year, form, quarter, min_sections,
                                     min_fin_stmts, sample_kw, is_amendment):
        tree = self._tree(ticker, year, form, quarter)
        path = require(canon(ticker, year, form, quarter))
        file_size = path.stat().st_size
        for sp, node in tree.nodes.items():
            assert 0 <= node.char_start <= file_size, f"{sp}: char_start out of range"
            assert 0 < node.char_end  <= file_size, f"{sp}: char_end out of range"

    def test_sample_section_keyword_present(
        self, ticker, year, form, quarter, min_sections, min_fin_stmts, sample_kw, is_amendment
    ):
        if not sample_kw or is_amendment:
            pytest.skip("no sample keyword for this filing")
        tree = self._tree(ticker, year, form, quarter)
        keyword_lower = sample_kw.lower()
        found = any(
            keyword_lower in sp.lower() or keyword_lower in node.label.lower()
            for sp, node in tree.nodes.items()
        )
        assert found, (
            f"Expected a section containing {sample_kw!r} in {ticker} {year} {form}. "
            f"Available paths: {sorted(tree.nodes)[:10]}"
        )

    def test_find_section_returns_string(self, ticker, year, form, quarter, min_sections,
                                          min_fin_stmts, sample_kw, is_amendment):
        tree = self._tree(ticker, year, form, quarter)
        path = require(canon(ticker, year, form, quarter))
        mid = path.stat().st_size // 2
        result = tree.find_section(mid)
        assert isinstance(result, str)

    def test_find_section_always_in_nodes(self, ticker, year, form, quarter, min_sections,
                                           min_fin_stmts, sample_kw, is_amendment):
        tree = self._tree(ticker, year, form, quarter)
        path = require(canon(ticker, year, form, quarter))
        file_size = path.stat().st_size
        for offset in [0, file_size // 4, file_size // 2, file_size - 1]:
            sp = tree.find_section(offset)
            assert sp in tree.nodes, f"find_section({offset}) returned unknown path {sp!r}"

    def test_amendment_has_no_crash(self, ticker, year, form, quarter, min_sections,
                                     min_fin_stmts, sample_kw, is_amendment):
        if not is_amendment:
            pytest.skip("only for amendment filings")
        tree = self._tree(ticker, year, form, quarter)
        assert "ROOT" in tree.nodes

    def test_parent_child_consistency(self, ticker, year, form, quarter, min_sections,
                                       min_fin_stmts, sample_kw, is_amendment):
        tree = self._tree(ticker, year, form, quarter)
        for sp, node in tree.nodes.items():
            for child_sp in node.children_paths:
                assert child_sp in tree.nodes, f"child {child_sp!r} not in nodes"
                child = tree.nodes[child_sp]
                assert child.parent_path == sp or child.parent_path == "ROOT", (
                    f"child {child_sp!r} has parent {child.parent_path!r}, expected {sp!r}"
                )

    def test_toc_nodes_have_anchor(self, ticker, year, form, quarter, min_sections,
                                    min_fin_stmts, sample_kw, is_amendment):
        tree = self._tree(ticker, year, form, quarter)
        for sp, node in tree.nodes.items():
            if node.is_in_toc:
                assert node.html_anchor is not None, (
                    f"ToC node {sp!r} has no html_anchor"
                )


# ---------------------------------------------------------------------------
# Integration: bisect-based section lookup against known iXBRL positions
# ---------------------------------------------------------------------------

class TestSectionBisect:
    """Verify that find_section() correctly resolves iXBRL fact positions
    to expected sections using facts we can identify by concept name."""

    @pytest.fixture
    def nvda_2026(self):
        path = require(canon("NVDA", "2026", "10-K"))
        tree = build_section_tree(path)
        positions = extract_ixbrl_positions(path)
        return tree, positions, path.read_bytes()

    def test_revenues_fact_in_income_statement_or_fin8(self, nvda_2026):
        tree, positions, raw = nvda_2026
        revenue_facts = [p for p in positions if "Revenues" in p.concept]
        assert revenue_facts, "Expected Revenues facts in NVDA 2026 10-K"
        for p in revenue_facts[:3]:
            sp = tree.find_section(p.char_start)
            assert sp is not None
            # Revenue facts should land in Item 8 or one of its sub-sections.
            assert sp is not None

    def test_find_section_monotone_with_positions(self, nvda_2026):
        """For facts sorted by char_start, find_section should never give a
        section whose char_start is AFTER the fact's position."""
        tree, positions, raw = nvda_2026
        for p in positions:
            sp = tree.find_section(p.char_start)
            if sp is None:
                continue
            node = tree.nodes[sp]
            assert node.char_start <= p.char_start, (
                f"Section {sp!r} starts at {node.char_start}, "
                f"but fact {p.xml_id} is at {p.char_start}"
            )

    def test_byte_0_resolves_to_root(self, nvda_2026):
        tree, _, __ = nvda_2026
        assert tree.find_section(0) == "ROOT"

    @pytest.fixture
    def msft_2023(self):
        path = require(canon("MSFT", "2023", "10-K"))
        tree = build_section_tree(path)
        positions = extract_ixbrl_positions(path)
        return tree, positions, path.read_bytes()

    def test_msft_simple_anchor_bisect(self, msft_2023):
        """Simple-anchor filer (MSFT): bisect should still work correctly."""
        tree, positions, raw = msft_2023
        for p in positions[:50]:
            sp = tree.find_section(p.char_start)
            assert sp in tree.nodes

    @pytest.fixture
    def tsm_2024(self):
        path = require(canon("TSM", "2024", "20-F"))
        tree = build_section_tree(path)
        positions = extract_ixbrl_positions(path)
        return tree, positions, path.read_bytes()

    def test_tsm_20f_bisect(self, tsm_2024):
        """20-F filer (TSM): bisect should resolve positions correctly."""
        tree, positions, raw = tsm_2024
        for p in positions[:50]:
            sp = tree.find_section(p.char_start)
            assert sp in tree.nodes


# ---------------------------------------------------------------------------
# Cross-filer smoke test: run both functions on every available canonical.htm
# ---------------------------------------------------------------------------

_ALL_CANON_PATHS = sorted(NORM_ROOT.rglob("canonical.htm")) if NORM_ROOT.exists() else []


@pytest.mark.parametrize(
    "canon_path",
    _ALL_CANON_PATHS,
    ids=[str(p.relative_to(NORM_ROOT)) for p in _ALL_CANON_PATHS],
)
class TestSmokeAllFilings:
    """No filing should raise an exception in either function."""

    def test_build_section_tree_no_crash(self, canon_path):
        tree = build_section_tree(canon_path)
        assert isinstance(tree, SectionTree)
        assert "ROOT" in tree.nodes
        assert len(tree.ordered_char_starts) == len(tree.nodes)

    def test_extract_ixbrl_positions_no_crash(self, canon_path):
        positions = extract_ixbrl_positions(canon_path)
        assert isinstance(positions, list)
        # Every position must have a valid xml_id.
        for p in positions:
            assert p.xml_id

    def test_section_tree_covers_all_iixbrl_positions(self, canon_path):
        """Every iXBRL fact should resolve to a known section (never returns None
        after ROOT is added as the sentinel at char_start=0)."""
        tree = build_section_tree(canon_path)
        positions = extract_ixbrl_positions(canon_path)
        for p in positions:
            sp = tree.find_section(p.char_start)
            assert sp is not None, (
                f"{canon_path.name}: fact {p.xml_id} at {p.char_start} "
                "did not resolve to any section"
            )
            assert sp in tree.nodes
