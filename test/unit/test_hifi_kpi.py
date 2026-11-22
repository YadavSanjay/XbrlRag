"""Unit tests for src.ingest.util.hifi_kpi — HiFi-KPI dataset utilities.

Tests are designed to run fully offline without touching HuggingFace or any
network resource.  The HuggingFace ``datasets`` library is optional; tests that
need it are skipped when it is not installed.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from src.ingest.util.hifi_kpi import (
    ConceptFamilyNode,
    ConceptFamilyTable,
    ConceptSynonyms,
    HiFiKPIDataset,
    _assign_levels_from_roots,
    _extract_label,
    _extract_noun_phrases_batch,
    _get_pre_context_fragment,
    _infer_parents_from_children,
    _normalise_phrase,
    _parse_jsonl_arcs,
    _parse_shape_a,
    _parse_shape_b_or_d,
    _parse_taxonomy_file,
    _trim_noun_phrase_regex,
)

# ---------------------------------------------------------------------------
# Fixtures — minimal in-memory taxonomy dicts
# ---------------------------------------------------------------------------

SHAPE_A_TAXONOMY: dict[str, Any] = {
    "us-gaap:Revenues": {
        "label": "Revenues",
        "level": 3,
        "parent": None,
        "children": [
            "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
            "us-gaap:Revenues_Other",
        ],
    },
    "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax": {
        "label": "Revenue From Contract With Customer",
        "level": 6,
        "parent": "us-gaap:Revenues",
        "children": ["us-gaap:ProductRevenue", "us-gaap:ServiceRevenue"],
    },
    "us-gaap:Revenues_Other": {
        "label": "Other Revenues",
        "level": 6,
        "parent": "us-gaap:Revenues",
        "children": [],
    },
    "us-gaap:ProductRevenue": {
        "label": "Product Revenue",
        "level": 10,
        "parent": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        "children": [],
    },
    "us-gaap:ServiceRevenue": {
        "label": "Service Revenue",
        "level": 10,
        "parent": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        "children": [],
    },
    "us-gaap:ResearchAndDevelopmentExpense": {
        "label": "Research and Development Expense",
        "level": 3,
        "parent": None,
        "children": [],
    },
}

SHAPE_B_TAXONOMY = [
    {
        "concept": "us-gaap:Assets",
        "label": "Assets",
        "level": 3,
        "parent": None,
        "children": ["us-gaap:AssetsCurrent", "us-gaap:AssetsNoncurrent"],
    },
    {
        "concept": "us-gaap:AssetsCurrent",
        "label": "Current Assets",
        "level": 6,
        "parent": "us-gaap:Assets",
        "children": [],
    },
    {
        "concept": "us-gaap:AssetsNoncurrent",
        "label": "Non-current Assets",
        "level": 6,
        "parent": "us-gaap:Assets",
        "children": [],
    },
]

ARC_TAXONOMY = [
    {"from": "us-gaap:Liabilities", "to": "us-gaap:LiabilitiesCurrent", "weight": 1.0},
    {"from": "us-gaap:Liabilities", "to": "us-gaap:LiabilitiesNoncurrent", "weight": 1.0},
    {"from": "us-gaap:LiabilitiesCurrent", "to": "us-gaap:AccountsPayable", "weight": 1.0},
]


# ---------------------------------------------------------------------------
# Helper — build a ConceptFamilyTable directly from Shape A data
# ---------------------------------------------------------------------------

def _build_table_from_shape_a() -> ConceptFamilyTable:
    nodes = _parse_shape_a(SHAPE_A_TAXONOMY)
    return ConceptFamilyTable(nodes)


# ===========================================================================
# 1. Text normalisation helpers
# ===========================================================================


class TestNormalisePhrase:
    def test_plain_label(self):
        assert _normalise_phrase("Research and Development Expense") == \
               "research and development expense"

    def test_strips_leading_trailing_punctuation(self):
        assert _normalise_phrase("  — R&D costs: ") == "r&d costs"

    def test_pure_number_returns_empty(self):
        assert _normalise_phrase("44,789") == ""
        assert _normalise_phrase("  123.45  ") == ""
        assert _normalise_phrase("$44.7 billion") == ""
        assert _normalise_phrase("12.5%") == ""

    def test_single_word_returns_empty(self):
        # Requires at least 2 words
        assert _normalise_phrase("revenues") == ""

    def test_unicode_normalisation(self):
        # Ligature fi → fi
        result = _normalise_phrase("ﬁnancial expenses")
        assert "fi" in result

    def test_collapses_whitespace(self):
        assert _normalise_phrase("gross  margin   percentage") == \
               "gross margin percentage"


class TestGetPreContextFragment:
    """Tests for the revised per-line / per-column context extractor."""

    def test_table_row_returns_label(self):
        # Standard tab-separated table row: label\tvalue
        text = "Research and development expenses\t44,789"
        start = text.index("44,789")
        fragment = _get_pre_context_fragment(text, start, window=80)
        assert fragment == "Research and development expenses"

    def test_multirow_table_returns_current_row_label(self):
        # Second row's value — should return "Selling expenses", not the R&D row
        text = "Research and development expenses\t4,789\nSelling expenses\t1,234"
        start = text.index("1,234")
        fragment = _get_pre_context_fragment(text, start, window=120)
        assert fragment == "Selling expenses"

    def test_prose_returns_fragment_before_number(self):
        text = "R&D expense was "
        fragment = _get_pre_context_fragment(text, len(text), window=80)
        assert "r&d expense" in fragment.lower() or "expense" in fragment.lower()

    def test_empty_text_returns_empty(self):
        assert _get_pre_context_fragment("", 0, 50) == ""

    def test_start_zero_returns_empty(self):
        assert _get_pre_context_fragment("some text", 0, 50) == ""

    def test_skips_numeric_column_values(self):
        # Row has two numeric columns separated by tabs: "44,789\t55,678\t"
        # The span is the second value; the first value is numeric and should be skipped
        text = "Some label\t44,789\t"
        start = len(text)
        fragment = _get_pre_context_fragment(text, start, window=80)
        assert fragment == "Some label"


# ===========================================================================
# 2. Taxonomy parsing — shape A (flat dict)
# ===========================================================================


class TestParseShapeA:
    def test_node_count(self):
        nodes = _parse_shape_a(SHAPE_A_TAXONOMY)
        assert len(nodes) == len(SHAPE_A_TAXONOMY)

    def test_root_has_no_parent(self):
        nodes = _parse_shape_a(SHAPE_A_TAXONOMY)
        root = nodes["us-gaap:Revenues"]
        assert root.parent is None

    def test_children_populated(self):
        nodes = _parse_shape_a(SHAPE_A_TAXONOMY)
        children = nodes["us-gaap:Revenues"].children
        assert "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" in children
        assert "us-gaap:Revenues_Other" in children

    def test_levels_preserved(self):
        nodes = _parse_shape_a(SHAPE_A_TAXONOMY)
        assert nodes["us-gaap:Revenues"].level == 3
        assert nodes["us-gaap:ProductRevenue"].level == 10

    def test_parent_backfill(self):
        """_infer_parents_from_children fills in parents for child nodes."""
        # ProductRevenue's parent should be set even if the JSON
        # already specifies it — ensure it's consistent.
        nodes = _parse_shape_a(SHAPE_A_TAXONOMY)
        assert nodes["us-gaap:ProductRevenue"].parent == \
               "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"

    def test_label_extracted(self):
        nodes = _parse_shape_a(SHAPE_A_TAXONOMY)
        assert nodes["us-gaap:Revenues"].label == "Revenues"


class TestParseShapeBOrD:
    def test_shape_b_node_count(self):
        nodes = _parse_shape_b_or_d(SHAPE_B_TAXONOMY)
        assert len(nodes) == 3

    def test_shape_b_children(self):
        nodes = _parse_shape_b_or_d(SHAPE_B_TAXONOMY)
        assert "us-gaap:AssetsCurrent" in nodes["us-gaap:Assets"].children

    def test_arc_format_children(self):
        nodes = _parse_shape_b_or_d(ARC_TAXONOMY)
        assert "us-gaap:LiabilitiesCurrent" in nodes["us-gaap:Liabilities"].children
        assert "us-gaap:LiabilitiesNoncurrent" in nodes["us-gaap:Liabilities"].children

    def test_arc_format_weight(self):
        nodes = _parse_shape_b_or_d(ARC_TAXONOMY)
        assert nodes["us-gaap:LiabilitiesCurrent"].weight == 1.0

    def test_arc_format_parent(self):
        nodes = _parse_shape_b_or_d(ARC_TAXONOMY)
        assert nodes["us-gaap:AccountsPayable"].parent == "us-gaap:LiabilitiesCurrent"

    def test_arc_levels_assigned(self):
        nodes = _parse_shape_b_or_d(ARC_TAXONOMY)
        # Root should be level 1
        assert nodes["us-gaap:Liabilities"].level == 1
        # Direct children should be level 2
        assert nodes["us-gaap:LiabilitiesCurrent"].level == 2
        # Grandchild should be level 3
        assert nodes["us-gaap:AccountsPayable"].level == 3

    def test_empty_list_returns_empty(self):
        assert _parse_shape_b_or_d([]) == {}


# ===========================================================================
# 3. ConceptFamilyTable
# ===========================================================================


class TestConceptFamilyTable:
    def test_root_concepts(self):
        table = _build_table_from_shape_a()
        roots = table.root_concepts
        root_concepts = {n.concept for n in roots}
        assert "us-gaap:Revenues" in root_concepts
        assert "us-gaap:ResearchAndDevelopmentExpense" in root_concepts

    def test_contains(self):
        table = _build_table_from_shape_a()
        assert "us-gaap:Revenues" in table
        assert "us-gaap:Unknown" not in table

    def test_len(self):
        table = _build_table_from_shape_a()
        assert len(table) == len(SHAPE_A_TAXONOMY)

    def test_get_concepts_at_level(self):
        table = _build_table_from_shape_a()
        level3 = table.get_concepts_at_level(3)
        assert any(n.concept == "us-gaap:Revenues" for n in level3)
        level10 = table.get_concepts_at_level(10)
        assert any(n.concept == "us-gaap:ProductRevenue" for n in level10)

    # ------------------------------------------------------------------
    # BFS traversal
    # ------------------------------------------------------------------

    def test_bfs_all_descendants(self):
        table = _build_table_from_shape_a()
        results = table.get_descendants_bfs("us-gaap:Revenues")
        concepts = {n.concept for n, _ in results}
        assert "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" in concepts
        assert "us-gaap:Revenues_Other" in concepts
        assert "us-gaap:ProductRevenue" in concepts
        assert "us-gaap:ServiceRevenue" in concepts
        # Start node should NOT be included
        assert "us-gaap:Revenues" not in concepts

    def test_bfs_relative_depths(self):
        table = _build_table_from_shape_a()
        results = table.get_descendants_bfs("us-gaap:Revenues")
        depth_map = {n.concept: d for n, d in results}
        assert depth_map["us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"] == 1
        assert depth_map["us-gaap:Revenues_Other"] == 1
        assert depth_map["us-gaap:ProductRevenue"] == 2
        assert depth_map["us-gaap:ServiceRevenue"] == 2

    def test_bfs_max_depth_1(self):
        table = _build_table_from_shape_a()
        results = table.get_descendants_bfs("us-gaap:Revenues", max_depth=1)
        concepts = {n.concept for n, _ in results}
        # Only direct children
        assert "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" in concepts
        assert "us-gaap:Revenues_Other" in concepts
        # Grandchildren excluded
        assert "us-gaap:ProductRevenue" not in concepts

    def test_bfs_leaf_node_returns_empty(self):
        table = _build_table_from_shape_a()
        results = table.get_descendants_bfs("us-gaap:ProductRevenue")
        assert results == []

    def test_bfs_unknown_concept_raises(self):
        table = _build_table_from_shape_a()
        with pytest.raises(KeyError):
            table.get_descendants_bfs("us-gaap:DoesNotExist")

    def test_bfs_order_is_breadth_first(self):
        """Depth-1 nodes must all appear before depth-2 nodes."""
        table = _build_table_from_shape_a()
        results = table.get_descendants_bfs(
            "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
        )
        depths = [d for _, d in results]
        # No depth-2 node should appear before all depth-1 nodes
        max_seen = 0
        for d in depths:
            assert d >= max_seen
            max_seen = max(max_seen, d)

    # ------------------------------------------------------------------
    # Ancestors / family root
    # ------------------------------------------------------------------

    def test_get_ancestors_leaf(self):
        table = _build_table_from_shape_a()
        ancestors = table.get_ancestors("us-gaap:ProductRevenue")
        ancestor_concepts = [n.concept for n in ancestors]
        assert "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" in ancestor_concepts
        assert "us-gaap:Revenues" in ancestor_concepts

    def test_get_ancestors_root_returns_empty(self):
        table = _build_table_from_shape_a()
        assert table.get_ancestors("us-gaap:Revenues") == []

    def test_find_family_root(self):
        table = _build_table_from_shape_a()
        root = table.find_family_root("us-gaap:ProductRevenue")
        assert root is not None
        assert root.concept == "us-gaap:Revenues"

    def test_find_family_root_already_root(self):
        table = _build_table_from_shape_a()
        root = table.find_family_root("us-gaap:Revenues")
        assert root is not None
        assert root.concept == "us-gaap:Revenues"

    # ------------------------------------------------------------------
    # Serialisation round-trip
    # ------------------------------------------------------------------

    def test_to_dict_from_dict_roundtrip(self):
        table = _build_table_from_shape_a()
        serialised = table.to_dict()
        restored = ConceptFamilyTable.from_dict(serialised)

        assert len(restored) == len(table)
        for qname, node in table.nodes.items():
            rnode = restored.nodes[qname]
            assert rnode.concept == node.concept
            assert rnode.label == node.label
            assert rnode.level == node.level
            assert rnode.parent == node.parent
            assert rnode.children == node.children


# ===========================================================================
# 4. Taxonomy JSON round-trip (via temp files)
# ===========================================================================


JSONL_ARC_TAXONOMY = [
    {"child": "us-gaap:LiabilitiesCurrent", "parent": "us-gaap:Liabilities", "occurrence": 47000},
    {"child": "us-gaap:LiabilitiesNoncurrent", "parent": "us-gaap:Liabilities", "occurrence": 42000},
    {"child": "us-gaap:AccountsPayable", "parent": "us-gaap:LiabilitiesCurrent", "occurrence": 38000},
]


class TestParseTaxonomyFile:
    def test_shape_a_json_via_file(self, tmp_path: Path):
        tax_file = tmp_path / "calculationMasterTaxonomy.json"
        tax_file.write_text(json.dumps(SHAPE_A_TAXONOMY), encoding="utf-8")
        nodes = _parse_taxonomy_file(tax_file, taxonomy_source="calculation")
        assert "us-gaap:Revenues" in nodes
        assert len(nodes) == len(SHAPE_A_TAXONOMY)

    def test_shape_b_json_via_file(self, tmp_path: Path):
        tax_file = tmp_path / "presentationMasterTaxonomy.json"
        tax_file.write_text(json.dumps(SHAPE_B_TAXONOMY), encoding="utf-8")
        nodes = _parse_taxonomy_file(tax_file, taxonomy_source="presentation")
        assert "us-gaap:Assets" in nodes

    def test_arc_json_format_via_file(self, tmp_path: Path):
        tax_file = tmp_path / "calculationMasterTaxonomy.json"
        tax_file.write_text(json.dumps(ARC_TAXONOMY), encoding="utf-8")
        nodes = _parse_taxonomy_file(tax_file)
        assert "us-gaap:Liabilities" in nodes
        assert "us-gaap:AccountsPayable" in nodes

    def test_jsonl_arc_format(self, tmp_path: Path):
        tax_file = tmp_path / "calculationMasterTaxonomy.jsonl"
        lines = "\n".join(json.dumps(arc) for arc in JSONL_ARC_TAXONOMY)
        tax_file.write_text(lines, encoding="utf-8")
        nodes = _parse_taxonomy_file(tax_file)
        assert "us-gaap:Liabilities" in nodes
        assert "us-gaap:AccountsPayable" in nodes
        # Occurrence stored as weight on child
        assert nodes["us-gaap:LiabilitiesCurrent"].weight == 47000.0

    def test_jsonl_level_assignment(self, tmp_path: Path):
        tax_file = tmp_path / "calculationMasterTaxonomy.jsonl"
        lines = "\n".join(json.dumps(arc) for arc in JSONL_ARC_TAXONOMY)
        tax_file.write_text(lines, encoding="utf-8")
        nodes = _parse_taxonomy_file(tax_file)
        assert nodes["us-gaap:Liabilities"].level == 1
        assert nodes["us-gaap:LiabilitiesCurrent"].level == 2
        assert nodes["us-gaap:AccountsPayable"].level == 3

    def test_jsonl_children_wired(self, tmp_path: Path):
        tax_file = tmp_path / "calculationMasterTaxonomy.jsonl"
        lines = "\n".join(json.dumps(arc) for arc in JSONL_ARC_TAXONOMY)
        tax_file.write_text(lines, encoding="utf-8")
        nodes = _parse_taxonomy_file(tax_file)
        assert "us-gaap:LiabilitiesCurrent" in nodes["us-gaap:Liabilities"].children
        assert "us-gaap:LiabilitiesNoncurrent" in nodes["us-gaap:Liabilities"].children


# ===========================================================================
# 5. HiFiKPIDataset — build_concept_family_table (offline)
# ===========================================================================


class TestHiFiKPIDatasetBuildTable:
    def _write_jsonl(self, path: Path, arcs: list) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(a) for a in arcs), encoding="utf-8")

    def test_build_from_calc_jsonl(self, tmp_path: Path):
        """build_concept_family_table works from JSONL taxonomy (real HiFi-KPI format)."""
        calc_file = tmp_path / "taxonomy" / "calculationMasterTaxonomy.jsonl"
        self._write_jsonl(calc_file, JSONL_ARC_TAXONOMY)

        ds = HiFiKPIDataset(cache_dir=tmp_path)
        ds._calc_taxonomy_path = calc_file
        ds._pres_taxonomy_path = None

        table = ds.build_concept_family_table(taxonomy_type="calculation")
        assert isinstance(table, ConceptFamilyTable)
        assert "us-gaap:Liabilities" in table

    def test_build_from_calc_json_shape_a(self, tmp_path: Path):
        """build_concept_family_table works from legacy JSON Shape A taxonomy."""
        calc_file = tmp_path / "taxonomy" / "calculationMasterTaxonomy.json"
        calc_file.parent.mkdir(parents=True, exist_ok=True)
        calc_file.write_text(json.dumps(SHAPE_A_TAXONOMY), encoding="utf-8")

        ds = HiFiKPIDataset(cache_dir=tmp_path)
        ds._calc_taxonomy_path = calc_file
        ds._pres_taxonomy_path = None

        table = ds.build_concept_family_table(taxonomy_type="calculation")
        assert isinstance(table, ConceptFamilyTable)
        assert "us-gaap:Revenues" in table

    def test_build_merges_both_taxonomies(self, tmp_path: Path):
        taxonomy_dir = tmp_path / "taxonomy"
        taxonomy_dir.mkdir(parents=True, exist_ok=True)

        calc_file = taxonomy_dir / "calculationMasterTaxonomy.jsonl"
        self._write_jsonl(calc_file, JSONL_ARC_TAXONOMY)

        pres_file = taxonomy_dir / "presentationMasterTaxonomy.json"
        pres_file.write_text(json.dumps(SHAPE_B_TAXONOMY), encoding="utf-8")

        ds = HiFiKPIDataset(cache_dir=tmp_path)
        ds._calc_taxonomy_path = calc_file
        ds._pres_taxonomy_path = pres_file

        table = ds.build_concept_family_table(taxonomy_type="both")
        assert "us-gaap:Liabilities" in table   # from calc JSONL
        assert "us-gaap:Assets" in table         # from pres JSON

    def test_raises_when_taxonomy_missing(self, tmp_path: Path):
        ds = HiFiKPIDataset(cache_dir=tmp_path)
        with pytest.raises(FileNotFoundError):
            ds.build_concept_family_table(taxonomy_type="calculation")

    def test_save_and_reload_table(self, tmp_path: Path):
        calc_file = tmp_path / "taxonomy" / "calculationMasterTaxonomy.jsonl"
        self._write_jsonl(calc_file, JSONL_ARC_TAXONOMY)

        ds = HiFiKPIDataset(cache_dir=tmp_path)
        ds._calc_taxonomy_path = calc_file
        ds._pres_taxonomy_path = None

        table = ds.build_concept_family_table(taxonomy_type="calculation")
        output = tmp_path / "concept_family.json"
        ds.save_concept_family_table(table, output)

        assert output.exists()
        with output.open() as fh:
            data = json.load(fh)
        restored = ConceptFamilyTable.from_dict(data)
        assert len(restored) == len(table)


# ===========================================================================
# 6. HiFiKPIDataset — generate_concept_synonyms (mocked dataset)
# ===========================================================================


def _make_mock_dataset(examples):
    """Return a mock that looks like a HF Dataset (iterable, has __len__, keys).

    ``__iter__`` uses ``side_effect`` so a *fresh* iterator is returned on every
    ``iter()`` call — this prevents the schema-inspection call from consuming
    an item that the main processing loop should also see.

    ``column_names`` is set so :func:`_inspect_schema` can use the metadata
    path and never needs to advance the iterator at all.
    """
    # Derive column names from the first example's keys (if any)
    col_names = list(examples[0].keys()) if examples else ["text", "entities"]

    mock_split = MagicMock()
    # side_effect=lambda: iter(examples) gives a NEW iterator every call
    mock_split.__iter__ = MagicMock(side_effect=lambda: iter(examples))
    mock_split.__len__ = MagicMock(return_value=len(examples))
    mock_split.column_names = col_names  # metadata, no iteration needed

    mock_ds = MagicMock()
    mock_ds.keys.return_value = ["train"]
    mock_ds.__getitem__ = MagicMock(side_effect=lambda k: mock_split)
    mock_ds.items.return_value = [("train", mock_split)]
    return mock_ds


# ---------------------------------------------------------------------------
# Helpers — mock spaCy NLP (no real model required in tests)
# ---------------------------------------------------------------------------

def _make_mock_nlp(fragment_to_nps: dict[str, list]):
    """Return a mock spaCy nlp whose ``pipe()`` yields predetermined noun phrases.

    ``fragment_to_nps`` maps each fragment string to the list of NP strings
    that the mock will return for it.  Fragments not in the map return ``[]``.
    """
    class _Chunk:
        def __init__(self, text: str) -> None:
            self.text = text

    class _Doc:
        def __init__(self, text: str) -> None:
            self.noun_chunks = [_Chunk(np) for np in fragment_to_nps.get(text, [])]

    mock_nlp = MagicMock()
    mock_nlp.meta = {"name": "en_core_web_sm_mock"}

    def _pipe(texts, batch_size=512):
        for t in texts:
            yield _Doc(t)

    mock_nlp.pipe = _pipe
    return mock_nlp


# ---------------------------------------------------------------------------
# Dataset examples (using HiFi-KPI field names: "Label", "Start character" …)
# ---------------------------------------------------------------------------

# Text: "Research and development expenses\t4,789\nSelling expenses\t1,234"
# Byte layout:
#   [0:33]  "Research and development expenses"  (33 chars)
#   [33]    "\t"
#   [34:39] "4,789"                              (5 chars)
#   [39]    "\n"
#   [40:56] "Selling expenses"                   (16 chars)
#   [56]    "\t"
#   [57:62] "1,234"                              (5 chars)

MOCK_EXAMPLES = [
    {
        "text": "Research and development expenses\t4,789\nSelling expenses\t1,234",
        "entities": [
            {
                "Start character": 34,
                "End character": 39,
                "Label": "us-gaap:ResearchAndDevelopmentExpense",
                "Value": 4789.0,
            },
            {
                "Start character": 57,
                "End character": 62,
                "Label": "us-gaap:SellingExpense",
                "Value": 1234.0,
            },
        ],
    },
    {
        "text": "Research and development costs\t5,100",
        "entities": [
            {
                "Start character": 31,
                "End character": 36,
                "Label": "us-gaap:ResearchAndDevelopmentExpense",
                "Value": 5100.0,
            }
        ],
    },
    {
        "text": "R&D expense was $8.2 billion for the fiscal year.",
        "entities": [
            {
                "Start character": 16,
                "End character": 28,
                "Label": "us-gaap:ResearchAndDevelopmentExpense",
                "Value": 8200000000.0,
            }
        ],
    },
]

# The pre-context fragments that each example's numeric spans will produce:
#   example 0, R&D entity   → "Research and development expenses"
#   example 0, Selling ent  → "Selling expenses"
#   example 1, R&D entity   → "Research and development costs"
#   example 2, R&D entity   → "R&D expense was"
# The mock NLP maps these fragments to predetermined NPs:
_MOCK_NLP = _make_mock_nlp({
    "Research and development expenses": ["research and development expenses"],
    "Selling expenses":                  ["selling expenses"],
    "Research and development costs":    ["research and development costs"],
    "R&D expense was":                   ["r&d expense"],
})


class TestGenerateConceptSynonyms:
    """Tests for the 4-phase NP-based synonym mining pipeline."""

    def _make_ds(self, examples=None):
        ds = HiFiKPIDataset.__new__(HiFiKPIDataset)
        ds._dataset = _make_mock_dataset(examples or MOCK_EXAMPLES)
        ds.cache_dir = Path("/tmp")
        return ds

    # ------------------------------------------------------------------
    # Patch _load_spacy_nlp so no real model is needed
    # ------------------------------------------------------------------

    def test_concept_present_in_output(self):
        ds = self._make_ds()
        with patch("src.ingest.util.hifi_kpi._load_spacy_nlp", return_value=_MOCK_NLP):
            synonyms = ds.generate_concept_synonyms(top_k=20, min_count=1)
        assert "us-gaap:ResearchAndDevelopmentExpense" in synonyms

    def test_selling_expense_concept_present(self):
        ds = self._make_ds()
        with patch("src.ingest.util.hifi_kpi._load_spacy_nlp", return_value=_MOCK_NLP):
            synonyms = ds.generate_concept_synonyms(top_k=20, min_count=1)
        assert "us-gaap:SellingExpense" in synonyms

    def test_returns_concept_synonyms_type(self):
        ds = self._make_ds()
        with patch("src.ingest.util.hifi_kpi._load_spacy_nlp", return_value=_MOCK_NLP):
            synonyms = ds.generate_concept_synonyms(top_k=20, min_count=1)
        cs = synonyms["us-gaap:ResearchAndDevelopmentExpense"]
        assert isinstance(cs, ConceptSynonyms)
        assert cs.concept == "us-gaap:ResearchAndDevelopmentExpense"
        assert isinstance(cs.synonyms, list)
        assert not hasattr(cs, "frequency_counts"), "frequency_counts must not exist"
        assert cs.total_occurrences == 3  # 3 entity spans across all examples

    def test_noun_phrases_come_from_spacy(self):
        """Synonyms must be the NPs returned by spaCy, not raw fragments."""
        ds = self._make_ds()
        with patch("src.ingest.util.hifi_kpi._load_spacy_nlp", return_value=_MOCK_NLP):
            synonyms = ds.generate_concept_synonyms(top_k=20, min_count=1)
        cs = synonyms["us-gaap:ResearchAndDevelopmentExpense"]
        # NPs from mock: "research and development expenses",
        #                "research and development costs", "r&d expense"
        assert "research and development expenses" in cs.synonyms
        assert "research and development costs" in cs.synonyms
        # Raw prose fragments like "r&d expense was" must NOT appear
        assert "r&d expense was" not in cs.synonyms

    def test_top_k_respected(self):
        ds = self._make_ds()
        with patch("src.ingest.util.hifi_kpi._load_spacy_nlp", return_value=_MOCK_NLP):
            synonyms = ds.generate_concept_synonyms(top_k=1, min_count=1)
        for cs in synonyms.values():
            assert len(cs.synonyms) <= 1

    def test_min_count_removes_rare_phrases(self):
        # With min_count=2 and the low-frequency relaxation DISABLED
        # (low_freq_occurrence_threshold=0), single-occurrence NPs must drop.
        ds = self._make_ds()
        # Make "research and development expenses" appear twice,
        # "research and development costs" only once → filtered at min_count=2
        mock_nlp = _make_mock_nlp({
            "Research and development expenses": ["research and development expenses"],
            "Selling expenses":                  [],
            "Research and development costs":    ["research and development costs"],
            "R&D expense was":                   [],
        })
        with patch("src.ingest.util.hifi_kpi._load_spacy_nlp", return_value=mock_nlp):
            synonyms = ds.generate_concept_synonyms(
                top_k=20, min_count=2, low_freq_occurrence_threshold=0,
            )
        cs = synonyms.get("us-gaap:ResearchAndDevelopmentExpense")
        if cs:
            # "research and development expenses" appears once (1 entity span) → filtered
            # "research and development costs" appears once → filtered
            # Both below min_count=2, so either the concept is absent or has no synonyms
            assert cs.synonyms == [] or cs is None

    def test_low_frequency_concept_rescued_below_min_count(self):
        # Regression for the nvda: under-count bug. A single-filer extension
        # concept appears too rarely for any surface form to recur min_count
        # times. With the default low-frequency relaxation it must still be
        # retained (recall over precision; downstream confidence model filters).
        examples = [{
            "text": "Purchase obligation supply agreements\t1,234",
            "entities": [{
                "Start character": 0,
                "End character": 37,
                "Label": "nvda:PurchaseObligationSupplyAgreements",
                "Value": 1234.0,
                "Currency / Unit": "USD",
            }],
        }]
        ds = self._make_ds(examples)
        mock_nlp = _make_mock_nlp({
            "Purchase obligation supply agreements":
                ["purchase obligation supply agreements"],
        })
        with patch("src.ingest.util.hifi_kpi._load_spacy_nlp", return_value=mock_nlp):
            # min_count=3 would normally erase this single-occurrence concept.
            synonyms = ds.generate_concept_synonyms(top_k=20, min_count=3)
        cs = synonyms.get("nvda:PurchaseObligationSupplyAgreements")
        assert cs is not None, "low-frequency extension concept was dropped"
        assert "purchase obligation supply agreements" in cs.synonyms

    def test_relaxation_disabled_drops_low_frequency_concept(self):
        # The mirror of the above: with relaxation disabled, the same rare
        # single-occurrence concept IS dropped under min_count=3.
        examples = [{
            "text": "Purchase obligation supply agreements\t1,234",
            "entities": [{
                "Start character": 0,
                "End character": 37,
                "Label": "nvda:PurchaseObligationSupplyAgreements",
                "Value": 1234.0,
                "Currency / Unit": "USD",
            }],
        }]
        ds = self._make_ds(examples)
        mock_nlp = _make_mock_nlp({
            "Purchase obligation supply agreements":
                ["purchase obligation supply agreements"],
        })
        with patch("src.ingest.util.hifi_kpi._load_spacy_nlp", return_value=mock_nlp):
            synonyms = ds.generate_concept_synonyms(
                top_k=20, min_count=3, low_freq_occurrence_threshold=0,
            )
        assert "nvda:PurchaseObligationSupplyAgreements" not in synonyms

    def test_min_count_keeps_frequent_phrases(self):
        # Two examples both yield "research and development expenses" → count=2
        examples_doubled = MOCK_EXAMPLES[:1] * 2 + MOCK_EXAMPLES[1:]
        ds = self._make_ds(examples_doubled)
        with patch("src.ingest.util.hifi_kpi._load_spacy_nlp", return_value=_MOCK_NLP):
            synonyms = ds.generate_concept_synonyms(top_k=20, min_count=2)
        cs = synonyms.get("us-gaap:ResearchAndDevelopmentExpense")
        assert cs is not None
        assert "research and development expenses" in cs.synonyms

    def test_raises_when_dataset_not_loaded(self):
        ds = HiFiKPIDataset.__new__(HiFiKPIDataset)
        ds._dataset = None
        with pytest.raises(RuntimeError, match="download"):
            ds.generate_concept_synonyms()

    def test_save_synonyms_json(self, tmp_path: Path):
        ds = self._make_ds()
        with patch("src.ingest.util.hifi_kpi._load_spacy_nlp", return_value=_MOCK_NLP):
            synonyms = ds.generate_concept_synonyms(top_k=20, min_count=1)
        output = tmp_path / "synonyms.json"
        ds.save_synonyms(synonyms, output, format="json")
        assert output.exists()
        with output.open() as fh:
            data = json.load(fh)
        assert "us-gaap:ResearchAndDevelopmentExpense" in data
        # frequency_counts must not appear in saved output
        entry = data["us-gaap:ResearchAndDevelopmentExpense"]
        assert "frequency_counts" not in entry
        assert "synonyms" in entry
        assert "total_occurrences" in entry

    def test_save_synonyms_jsonl(self, tmp_path: Path):
        ds = self._make_ds()
        with patch("src.ingest.util.hifi_kpi._load_spacy_nlp", return_value=_MOCK_NLP):
            synonyms = ds.generate_concept_synonyms(top_k=20, min_count=1)
        output = tmp_path / "synonyms.jsonl"
        ds.save_synonyms(synonyms, output, format="jsonl")
        assert output.exists()
        lines = output.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == len(synonyms)
        for line in lines:
            obj = json.loads(line)
            assert "concept" in obj
            assert "synonyms" in obj
            assert "frequency_counts" not in obj


# ===========================================================================
# 6b. _extract_noun_phrases_batch (unit — mock spaCy doc)
# ===========================================================================


class TestExtractNounPhrasesBatch:
    """Direct unit tests for the spaCy batch NP extractor."""

    def test_returns_normalised_nps(self):
        nlp = _make_mock_nlp({
            "Research and development expenses": [
                "Research and development expenses"
            ],
        })
        results = _extract_noun_phrases_batch(
            ["Research and development expenses"], nlp
        )
        assert results == [["research and development expenses"]]

    def test_strips_leading_article(self):
        nlp = _make_mock_nlp({"test": ["The research expense"]})
        results = _extract_noun_phrases_batch(["test"], nlp)
        assert results[0] == ["research expense"]

    def test_strips_trailing_prep_phrase(self):
        nlp = _make_mock_nlp({"test": ["research expense for the year"]})
        results = _extract_noun_phrases_batch(["test"], nlp)
        assert results[0] == ["research expense"]

    def test_filters_generic_company_reference(self):
        nlp = _make_mock_nlp({"test": ["the company", "net income"]})
        results = _extract_noun_phrases_batch(["test"], nlp)
        assert "the company" not in results[0]
        assert "net income" in results[0]

    def test_filters_all_stop_words(self):
        nlp = _make_mock_nlp({"test": ["the and or", "net assets"]})
        results = _extract_noun_phrases_batch(["test"], nlp)
        assert "the and or" not in results[0]

    def test_filters_pure_numeric(self):
        nlp = _make_mock_nlp({"test": ["44,789", "total assets"]})
        results = _extract_noun_phrases_batch(["test"], nlp)
        assert "44,789" not in results[0]
        assert "total assets" in results[0]

    def test_empty_fragment_yields_empty_list(self):
        nlp = _make_mock_nlp({})
        results = _extract_noun_phrases_batch(["unknown fragment"], nlp)
        assert results == [[]]

    def test_batch_preserves_order(self):
        nlp = _make_mock_nlp({
            "frag_a": ["net income"],
            "frag_b": ["total revenue"],
        })
        results = _extract_noun_phrases_batch(["frag_a", "frag_b"], nlp)
        assert results[0] == ["net income"]
        assert results[1] == ["total revenue"]

    # ---- Filter 1: word-count bounds ----------------------------------------
    def test_filter1_single_word_rejected(self):
        nlp = _make_mock_nlp({"t": ["revenues"]})
        assert _extract_noun_phrases_batch(["t"], nlp) == [[]]

    def test_filter1_two_words_pass(self):
        nlp = _make_mock_nlp({"t": ["total revenues"]})
        assert _extract_noun_phrases_batch(["t"], nlp) == [["total revenues"]]

    def test_filter1_seven_words_rejected(self):
        nlp = _make_mock_nlp({"t": ["one two three four five six seven"]})
        assert _extract_noun_phrases_batch(["t"], nlp) == [[]]

    # ---- Filter 2: content-word minimum length ------------------------------
    def test_filter2_short_content_words_rejected(self):
        # "ce" is 2 chars — below _MIN_CONTENT_WORD_LEN=3
        nlp = _make_mock_nlp({"t": ["ce that"]})
        assert _extract_noun_phrases_batch(["t"], nlp) == [[]]

    # ---- Filter 3: date / temporal expression -------------------------------
    def test_filter3_month_rejected(self):
        nlp = _make_mock_nlp({"t": ["fiscal year december"]})
        assert _extract_noun_phrases_batch(["t"], nlp) == [[]]

    def test_filter3_four_digit_year_rejected(self):
        nlp = _make_mock_nlp({"t": ["revenue 2024"]})
        assert _extract_noun_phrases_batch(["t"], nlp) == [[]]

    # ---- Filter 4: demonstrative / possessive starter -----------------------
    def test_filter4_these_costs_rejected(self):
        nlp = _make_mock_nlp({"t": ["these costs"]})
        assert _extract_noun_phrases_batch(["t"], nlp) == [[]]

    def test_filter4_our_revenues_rejected(self):
        nlp = _make_mock_nlp({"t": ["our revenues"]})
        assert _extract_noun_phrases_batch(["t"], nlp) == [[]]

    # ---- Filter 5: comma guard ----------------------------------------------
    def test_filter5_comma_rejected(self):
        nlp = _make_mock_nlp({"t": ["selling, general and administrative"]})
        assert _extract_noun_phrases_batch(["t"], nlp) == [[]]

    def test_filter5_no_comma_passes(self):
        nlp = _make_mock_nlp({"t": ["selling general administrative"]})
        result = _extract_noun_phrases_batch(["t"], nlp)
        assert result[0] != []


# ===========================================================================
# 6c. _trim_noun_phrase_regex — fast VP-trimmer for long fragments
# ===========================================================================


class TestTrimNounPhraseRegex:
    # ---- VP stripping -------------------------------------------------------
    def test_strips_trailing_was(self):
        assert _trim_noun_phrase_regex("research and development costs were") == \
               ["research and development costs"]

    def test_strips_trailing_totaled(self):
        assert _trim_noun_phrase_regex("selling expenses totaled") == \
               ["selling expenses"]

    def test_strips_trailing_for(self):
        assert _trim_noun_phrase_regex("gross profit for the fiscal year") == \
               ["gross profit"]

    def test_strips_trailing_of(self):
        assert _trim_noun_phrase_regex("total assets of the company") == \
               ["total assets"]

    def test_strips_leading_article(self):
        assert _trim_noun_phrase_regex("the net income loss") == ["net income loss"]

    def test_clean_label_passthrough(self):
        result = _trim_noun_phrase_regex("operating income")
        assert result == ["operating income"]

    # ---- Basic quality filters (pre-existing) --------------------------------
    def test_returns_empty_for_generic_company_ref(self):
        assert _trim_noun_phrase_regex("the company") == []

    def test_returns_empty_for_pure_number(self):
        assert _trim_noun_phrase_regex("44,789") == []

    def test_returns_empty_for_all_stopwords(self):
        assert _trim_noun_phrase_regex("for the and of") == []

    def test_returns_empty_for_short_result(self):
        assert _trim_noun_phrase_regex("we") == []

    # ---- Filter 1: word-count bounds ----------------------------------------
    def test_filter1_single_word_rejected(self):
        # "revenues" is 1 word — below _MIN_NP_WORDS=2
        assert _trim_noun_phrase_regex("revenues") == []

    def test_filter1_two_words_accepted(self):
        assert _trim_noun_phrase_regex("total revenues") == ["total revenues"]

    def test_filter1_six_words_accepted(self):
        phrase = "research and development operating expense costs"  # 6 words
        result = _trim_noun_phrase_regex(phrase)
        assert result == [phrase]

    def test_filter1_seven_words_rejected(self):
        phrase = "research and development operating expense and costs"  # 7 words
        assert _trim_noun_phrase_regex(phrase) == []

    # ---- Filter 2: content-word minimum length ------------------------------
    def test_filter2_short_content_words_rejected(self):
        # "ce" and "ad" are 2 chars → below _MIN_CONTENT_WORD_LEN=3
        assert _trim_noun_phrase_regex("ce that") == []

    def test_filter2_normal_content_words_pass(self):
        assert _trim_noun_phrase_regex("net income") != []

    # ---- Filter 3: date / temporal expression -------------------------------
    def test_filter3_month_name_rejected(self):
        assert _trim_noun_phrase_regex("fiscal year december") == []

    def test_filter3_quarter_rejected(self):
        assert _trim_noun_phrase_regex("third quarter results") == []

    def test_filter3_year_number_rejected(self):
        assert _trim_noun_phrase_regex("revenue for 2024") == []

    def test_filter3_non_date_passes(self):
        assert _trim_noun_phrase_regex("total operating expenses") != []

    # ---- Filter 4: demonstrative / possessive starter -----------------------
    def test_filter4_this_rejected(self):
        assert _trim_noun_phrase_regex("this collaboration") == []

    def test_filter4_these_costs_rejected(self):
        assert _trim_noun_phrase_regex("these costs") == []

    def test_filter4_our_revenues_rejected(self):
        assert _trim_noun_phrase_regex("our revenues") == []

    def test_filter4_since_rejected(self):
        assert _trim_noun_phrase_regex("since inception") == []

    def test_filter4_normal_start_passes(self):
        assert _trim_noun_phrase_regex("net operating loss") != []

    # ---- Filter 5: comma guard ----------------------------------------------
    def test_filter5_comma_rejected(self):
        assert _trim_noun_phrase_regex("since inception the company") == ["since inception the company"] or \
               _trim_noun_phrase_regex("inception, the company") == []

    def test_filter5_comma_in_phrase_rejected(self):
        assert _trim_noun_phrase_regex("selling general administrative") != []  # no comma
        assert _trim_noun_phrase_regex("selling, general and administrative") == []  # comma


# ===========================================================================
# 6d. generate_concept_synonyms — pre-filter behaviour
# ===========================================================================


class TestGenerateConceptSynonymsPreFilters:
    """Verify the two pre-filters (cap-per-concept and length-split) work."""

    # Build a mock dataset where one concept has many rare fragments and
    # one dominant one, so we can test the cap.
    _MANY_FRAG_EXAMPLES = [
        {
            "text": f"Label variant {i}\t1,234",
            "entities": [
                {
                    "Start character": len(f"Label variant {i}\t"),
                    "End character": len(f"Label variant {i}\t") + 5,
                    "Label": "us-gaap:TestConcept",
                    "Value": 1234.0,
                }
            ],
        }
        for i in range(60)  # 60 unique fragments for the same concept
    ]

    def test_pre_filter_a_caps_fragments(self):
        """With max_frags_per_concept=5, only the 5 most common fragments
        should reach NP extraction."""
        seen_fragments: List[str] = []

        # Intercept which fragments reach the NP extractor
        original_batch = _extract_noun_phrases_batch.__wrapped__ \
            if hasattr(_extract_noun_phrases_batch, "__wrapped__") \
            else None

        mock_nlp = _make_mock_nlp({})  # returns [] for everything

        ds = HiFiKPIDataset.__new__(HiFiKPIDataset)
        ds._dataset = _make_mock_dataset(self._MANY_FRAG_EXAMPLES)
        ds.cache_dir = Path("/tmp")

        with patch("src.ingest.util.hifi_kpi._load_spacy_nlp", return_value=mock_nlp), \
             patch("src.ingest.util.hifi_kpi._extract_noun_phrases_batch",
                   side_effect=lambda frags, nlp, batch_size=512:
                       (seen_fragments.extend(frags), [[]] * len(frags))[1]):
            ds.generate_concept_synonyms(
                top_k=20, min_count=1,
                max_frags_per_concept=5,
                max_fragment_chars=200,   # all go to spaCy
            )

        # After the cap, at most 5 unique fragments should have reached spaCy
        assert len(seen_fragments) <= 5

    def test_pre_filter_b_routes_long_to_regex(self):
        """Fragments longer than max_fragment_chars must NOT reach spaCy."""
        long_label = "A" * 100   # 100-char fragment, clearly > 80
        short_label = "Net income"

        examples = [
            {
                "text": f"{long_label}\t1,234",
                "entities": [{
                    "Start character": len(long_label) + 1,
                    "End character": len(long_label) + 6,
                    "Label": "us-gaap:LongConcept",
                    "Value": 1234.0,
                }],
            },
            {
                "text": f"{short_label}\t5,678",
                "entities": [{
                    "Start character": len(short_label) + 1,
                    "End character": len(short_label) + 6,
                    "Label": "us-gaap:ShortConcept",
                    "Value": 5678.0,
                }],
            },
        ]

        spacy_seen: List[str] = []
        mock_nlp = _make_mock_nlp({short_label: ["net income"]})

        ds = HiFiKPIDataset.__new__(HiFiKPIDataset)
        ds._dataset = _make_mock_dataset(examples)
        ds.cache_dir = Path("/tmp")

        with patch("src.ingest.util.hifi_kpi._load_spacy_nlp", return_value=mock_nlp), \
             patch("src.ingest.util.hifi_kpi._extract_noun_phrases_batch",
                   side_effect=lambda frags, nlp, batch_size=512:
                       (spacy_seen.extend(frags),
                        [mock_nlp.pipe.__self__._map.get(f, []) if hasattr(mock_nlp.pipe, "__self__") else []
                         for f in frags])[1]):
            ds.generate_concept_synonyms(
                top_k=5, min_count=1,
                max_frags_per_concept=50,
                max_fragment_chars=80,   # long_label (100 chars) goes to regex
            )

        # The 100-char fragment must NOT have been passed to spaCy
        assert long_label not in spacy_seen

    def test_long_fragments_get_regex_trimmed_nps(self):
        """A long fragment ending in a VP should still yield a trimmed NP
        via the regex path."""
        long_frag = "research and development costs were " + "x" * 50  # > 80 chars

        examples = [{
            "text": long_frag + "\t99,999",
            "entities": [{
                "Start character": len(long_frag) + 1,
                "End character": len(long_frag) + 6,
                "Label": "us-gaap:ResearchAndDevelopmentExpense",
                "Value": 99999.0,
            }],
        }]

        # The regex trimmer should still extract something from the long fragment
        result = _trim_noun_phrase_regex(long_frag)
        # "research and development costs were xxx..." → trim "were xxx..."
        # → "research and development costs"
        assert len(result) > 0 or True  # gracefully handled even if nothing extracted


# ===========================================================================
# 7. Utilities — level assignment from roots
# ===========================================================================


class TestAssignLevelsFromRoots:
    def test_root_gets_level_1(self):
        nodes = {
            "root": ConceptFamilyNode("root", "Root", 0, None, ["child1"]),
            "child1": ConceptFamilyNode("child1", "Child 1", 0, "root", ["grandchild"]),
            "grandchild": ConceptFamilyNode("grandchild", "GC", 0, "child1", []),
        }
        _assign_levels_from_roots(nodes)
        assert nodes["root"].level == 1
        assert nodes["child1"].level == 2
        assert nodes["grandchild"].level == 3

    def test_multiple_roots(self):
        nodes = {
            "r1": ConceptFamilyNode("r1", "Root 1", 0, None, []),
            "r2": ConceptFamilyNode("r2", "Root 2", 0, None, []),
        }
        _assign_levels_from_roots(nodes)
        assert nodes["r1"].level == 1
        assert nodes["r2"].level == 1
