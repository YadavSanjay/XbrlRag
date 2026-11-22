"""HiFi-KPI Dataset Downloader and Concept Mapping Generator.

Downloads the AAU-NLP/HiFi-KPI dataset from HuggingFace and provides utilities for:
1. Downloading the dataset into ``data/storage/datasets/`` (respects HF cache).
2. Mining concept synonyms from tagged entity text spans (for the deterministic
   resolver's ``concept_mapping.synonyms`` table).
3. Parsing ``calculationMasterTaxonomy.jsonl`` / ``presentationMasterTaxonomy.jsonl``
   into a navigable concept-family table that supports BFS traversal from any
   higher-level concept down to its leaves.

Actual dataset schema (confirmed from live inspection):
  * Taxonomy files: JSONL, one arc per line — ``{"child": str, "parent": str, "occurrence": int}``
    sorted descending by occurrence count across all SEC filings in the corpus.
  * Dataset examples: ``{"form_type", "accession_number", "filing_date", "quarter_ending",
    "company_name", "text", "entities"}`` where each entity has
    ``{"Label": str, "Start character": int, "End character": int,
     "Value": float, "Currency / Unit": str,
     "Start date for period": str, "End date for period": str}``.

Design reference: docs/design/XbrlRag_Architecture.md §3.3 (concept vocabulary)
Dataset: https://huggingface.co/datasets/AAU-NLP/HiFi-KPI

Usage example::

    from src.ingest.util.hifi_kpi import HiFiKPIDataset

    ds = HiFiKPIDataset()
    ds.download()                              # one-time; idempotent

    synonyms = ds.generate_concept_synonyms(top_k=20)
    print(synonyms["us-gaap:ResearchAndDevelopmentExpense"].synonyms[:5])

    table = ds.build_concept_family_table()
    nodes = table.get_descendants_bfs("us-gaap:Revenues", max_depth=3)
    for node, rel_depth in nodes:
        print("  " * rel_depth, node.concept, f"(level {node.level})")
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter, defaultdict, deque
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src import PROJECT_ROOT
from src.logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HF_DATASET_ID: str = "AAU-NLP/HiFi-KPI"
DATASETS_CACHE_DIR: Path = Path(PROJECT_ROOT) / "data" / "storage" / "datasets"

#: Taxonomy files stored alongside the HF dataset repo (JSONL arc lists)
TAXONOMY_FILENAMES: tuple[str, ...] = (
    "calculationMasterTaxonomy.jsonl",
    "presentationMasterTaxonomy.jsonl",
)

# Regex for stripping numeric/monetary spans that are useless as synonyms
_NUMERIC_RE = re.compile(
    r"""
    ^\s*[\-\+]?                     # optional sign
    (?:\$\s*)?                      # optional dollar sign
    [\d,]+(?:\.\d+)?                # digits (with optional decimal)
    (?:\s*(?:billion|million|thousand|bn|mn|k))? # optional scale
    (?:\s*%)?                       # optional percent
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Strip punctuation / noise at word boundaries
_PUNCT_RE = re.compile(r"^[^\w]+|[^\w]+$")

# Stop-words not useful as synonym content
_STOP_WORDS: frozenset[str] = frozenset(
    {
        "the", "a", "an", "and", "or", "of", "in", "for", "to", "from",
        "is", "are", "was", "were", "be", "been", "being", "have", "has",
        "had", "do", "does", "did", "its", "by", "on", "at", "as", "we",
        "our", "us", "which", "that", "this", "these", "those",
    }
)

# Minimum word count for a span to be treated as a meaningful synonym phrase
_MIN_WORDS: int = 2

# Namespace prefixes that denote *standard* taxonomies shared across all filers.
# Anything else is a single-filer company extension.
_STANDARD_TAXONOMY_PREFIXES: frozenset[str] = frozenset({"us-gaap", "dei", "srt"})

# Generic tokens that, on the *concept-label* side of the label-overlap gate, are
# too weak to anchor a synonym on their own.  Without this, "net cash" would
# match "NetIncomeLoss" via "net", and "total liabilities" would match
# "TotalAssets" via "total".  A synonym must share a *content* token, not just a
# connective, to be considered semantically anchored to a concept.
_LABEL_GENERIC_TOKENS: frozenset[str] = frozenset(
    {
        "net", "total", "gross", "other", "current", "noncurrent",
        "before", "after", "including", "excluding", "value", "amount", "per",
    }
)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ConceptSynonyms:
    """Surface-form synonyms mined for a single us-gaap concept.

    Attributes:
        concept: The fully-qualified concept QName, e.g.
            ``us-gaap:ResearchAndDevelopmentExpense``.
        synonyms: Ordered list of minimal noun-phrase synonyms, most frequent
            first (after spaCy NP extraction and top-k filtering).
        total_occurrences: Total number of entity spans processed for this
            concept across all dataset splits.
    """

    concept: str
    synonyms: list[str]
    total_occurrences: int


@dataclass
class ConceptFamilyNode:
    """A single node in the XBRL concept hierarchy.

    Attributes:
        concept: QName, e.g. ``us-gaap:Assets``.
        label: Human-readable label from the taxonomy.
        level: Hierarchy depth as stored in the taxonomy JSON (typically 3, 6
            or 10 for the three collapsed levels in HiFi-KPI).
        parent: QName of parent node, or ``None`` for root nodes.
        children: Ordered list of child concept QNames.
        weight: Calculation weight (+1 / −1) from the calculation linkbase.
            ``None`` for presentation-only nodes.
    """

    concept: str
    label: str
    level: int
    parent: str | None = None
    children: list[str] = field(default_factory=list)
    weight: float | None = None


# ---------------------------------------------------------------------------
# Concept-family table
# ---------------------------------------------------------------------------


class ConceptFamilyTable:
    """In-memory tree of XBRL concepts built from master taxonomy JSON files.

    Supports BFS traversal from any concept node so the plan-generator can
    emit Level-3/6 ``concept_family`` slots and the resolver can descend to
    leaf (Level-10) QNames.

    Parameters:
        nodes: Mapping from concept QName → :class:`ConceptFamilyNode`.
    """

    def __init__(self, nodes: dict[str, ConceptFamilyNode]) -> None:
        self.nodes: dict[str, ConceptFamilyNode] = nodes
        self._roots: list[str] = [
            qname for qname, n in nodes.items() if n.parent is None
        ]

    # ------------------------------------------------------------------
    # Core traversal
    # ------------------------------------------------------------------

    def get_descendants_bfs(
        self,
        concept: str,
        max_depth: int | None = None,
    ) -> list[tuple[ConceptFamilyNode, int]]:
        """Return all descendants of *concept* in breadth-first order.

        Parameters:
            concept: QName of the starting node.
            max_depth: If given, stop expanding at this many levels below
                the start node (``max_depth=1`` returns only direct children).

        Returns:
            List of ``(node, relative_depth)`` tuples, in BFS order.
            The start node itself is *not* included.  ``relative_depth``
            is 1 for direct children, 2 for grandchildren, etc.

        Raises:
            KeyError: If *concept* is not in the table.
        """
        if concept not in self.nodes:
            raise KeyError(f"Concept not found in family table: {concept!r}")

        results: list[tuple[ConceptFamilyNode, int]] = []
        queue: deque[tuple[str, int]] = deque()

        for child_qname in self.nodes[concept].children:
            queue.append((child_qname, 1))

        while queue:
            qname, depth = queue.popleft()
            if qname not in self.nodes:
                logger.debug("BFS: skipping unknown child %s", qname)
                continue
            node = self.nodes[qname]
            results.append((node, depth))
            if max_depth is None or depth < max_depth:
                for child_qname in node.children:
                    queue.append((child_qname, depth + 1))

        return results

    def get_ancestors(self, concept: str) -> list[ConceptFamilyNode]:
        """Return the path from *concept* up to the root (exclusive of *concept*).

        Returns nodes ordered from immediate parent → root.
        """
        path: list[ConceptFamilyNode] = []
        current = concept
        seen: set[str] = set()
        while True:
            node = self.nodes.get(current)
            if node is None or node.parent is None:
                break
            if node.parent in seen:
                logger.warning("Cycle detected in taxonomy at %s", node.parent)
                break
            seen.add(node.parent)
            parent_node = self.nodes.get(node.parent)
            if parent_node is None:
                break
            path.append(parent_node)
            current = node.parent
        return path

    def find_family_root(self, concept: str) -> ConceptFamilyNode | None:
        """Return the highest-level (smallest ``level`` value) ancestor of *concept*.

        This is the Level-3 "broad family" node the plan-generator emits —
        e.g. ``us-gaap:Revenues`` for ``us-gaap:RevenueFromContractWith…``.
        """
        ancestors = self.get_ancestors(concept)
        if not ancestors:
            return self.nodes.get(concept)
        # Ancestors are ordered parent → root; last element is the root
        return ancestors[-1]

    def get_concepts_at_level(self, level: int) -> list[ConceptFamilyNode]:
        """Return all nodes whose stored ``level`` attribute equals *level*."""
        return [n for n in self.nodes.values() if n.level == level]

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    @property
    def root_concepts(self) -> list[ConceptFamilyNode]:
        """Top-level (no-parent) nodes."""
        return [self.nodes[q] for q in self._roots if q in self.nodes]

    def __contains__(self, concept: str) -> bool:
        return concept in self.nodes

    def __len__(self) -> int:
        return len(self.nodes)

    def __repr__(self) -> str:
        return (
            f"ConceptFamilyTable(nodes={len(self.nodes)}, "
            f"roots={len(self._roots)})"
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            qname: {
                "label": n.label,
                "level": n.level,
                "parent": n.parent,
                "children": n.children,
                "weight": n.weight,
            }
            for qname, n in self.nodes.items()
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConceptFamilyTable:
        """Deserialise from the dict produced by :meth:`to_dict`."""
        nodes = {
            qname: ConceptFamilyNode(
                concept=qname,
                label=v.get("label", qname),
                level=v.get("level", 0),
                parent=v.get("parent"),
                children=v.get("children", []),
                weight=v.get("weight"),
            )
            for qname, v in data.items()
        }
        return cls(nodes)


# ---------------------------------------------------------------------------
# Main dataset class
# ---------------------------------------------------------------------------


class HiFiKPIDataset:
    """Downloader and concept-mapping generator for the AAU-NLP/HiFi-KPI dataset.

    The class is **lazy** — it does not download or parse anything until the
    corresponding methods are called.  All heavy artefacts are cached on disk
    so subsequent calls are fast.

    Parameters:
        cache_dir: Root directory for downloaded data.  Defaults to
            ``data/storage/datasets/`` inside the project root.

    Example::

        ds = HiFiKPIDataset()
        ds.download()
        synonyms = ds.generate_concept_synonyms(top_k=20)
        table = ds.build_concept_family_table()
    """

    def __init__(self, cache_dir: Path | str | None = None) -> None:
        self.cache_dir: Path = Path(cache_dir) if cache_dir else DATASETS_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self._dataset: Any = None          # datasets.DatasetDict (lazy)
        self._calc_taxonomy_path: Path | None = None
        self._pres_taxonomy_path: Path | None = None

    # ------------------------------------------------------------------
    # Step 1 — Download
    # ------------------------------------------------------------------

    def download(
        self,
        splits: Sequence[str] | None = None,
        force: bool = False,
    ) -> None:
        """Download the HiFi-KPI dataset and taxonomy JSON files.

        Both downloads are idempotent — HuggingFace's cache layer is used for
        the main dataset; the taxonomy JSON files are stored in
        ``<cache_dir>/taxonomy/`` and only re-downloaded when *force=True*.

        Parameters:
            splits: Which dataset splits to materialise, e.g.
                ``["train"]``.  ``None`` downloads all splits.
            force: Re-download taxonomy files even if they already exist on
                disk.
        """
        self._download_taxonomy_files(force=force)
        self._load_hf_dataset(splits=splits)

    def _download_taxonomy_files(self, force: bool = False) -> None:
        """Download calculationMasterTaxonomy.json / presentationMasterTaxonomy.json."""
        try:
            from huggingface_hub import hf_hub_download
        except ImportError as exc:
            raise ImportError(
                "huggingface_hub is required.  Run: pip install huggingface-hub"
            ) from exc

        taxonomy_dir = self.cache_dir / "taxonomy"
        taxonomy_dir.mkdir(parents=True, exist_ok=True)

        for filename in TAXONOMY_FILENAMES:
            dest = taxonomy_dir / filename
            if dest.exists() and not force:
                logger.info("Taxonomy file already cached: %s", dest)
                if filename.startswith("calculation"):
                    self._calc_taxonomy_path = dest
                else:
                    self._pres_taxonomy_path = dest
                continue

            logger.info("Downloading %s from %s …", filename, HF_DATASET_ID)
            try:
                downloaded = hf_hub_download(
                    repo_id=HF_DATASET_ID,
                    filename=filename,
                    repo_type="dataset",
                    local_dir=str(taxonomy_dir),
                )
                downloaded_path = Path(downloaded)
                # hf_hub_download may place the file in a sub-path; copy to dest
                if downloaded_path.resolve() != dest.resolve():
                    import shutil
                    shutil.copy2(downloaded_path, dest)
                logger.info("Saved taxonomy file to %s", dest)
            except Exception as exc:
                logger.warning(
                    "Could not download %s: %s — taxonomy features will be unavailable.",
                    filename, exc,
                )
                continue

            if filename.startswith("calculation"):
                self._calc_taxonomy_path = dest
            else:
                self._pres_taxonomy_path = dest

    def _is_calc_taxonomy(self, filename: str) -> bool:
        return filename.lower().startswith("calculation")

    def _load_hf_dataset(
        self, splits: Sequence[str] | None = None
    ) -> None:
        """Load (and cache) the HF dataset via the ``datasets`` library."""
        try:
            from datasets import load_dataset
        except ImportError as exc:
            raise ImportError(
                "The `datasets` library is required.  Run: pip install datasets"
            ) from exc

        logger.info("Loading HiFi-KPI dataset (cache_dir=%s) …", self.cache_dir)
        kwargs: dict[str, Any] = {
            "path": HF_DATASET_ID,
            "cache_dir": str(self.cache_dir),
        }
        if splits:
            # load_dataset returns a single Dataset when split= is given; wrap
            from datasets import Dataset, DatasetDict
            loaded: dict[str, Any] = {}
            for split in splits:
                try:
                    ds = load_dataset(split=split, **kwargs)
                    loaded[split] = ds
                except Exception as exc:
                    logger.warning("Could not load split %r: %s", split, exc)
            self._dataset = DatasetDict(loaded)
        else:
            self._dataset = load_dataset(**kwargs)

        logger.info(
            "Dataset loaded: %s",
            {k: len(v) for k, v in self._dataset.items()}
            if hasattr(self._dataset, "items")
            else self._dataset,
        )

    # ------------------------------------------------------------------
    # Step 2 — Synonym mining
    # ------------------------------------------------------------------

    def generate_concept_synonyms(
        self,
        top_k: int = 15,
        splits: Sequence[str] | None = None,
        min_count: int = 3,
        low_freq_occurrence_threshold: int = 30,
        require_label_overlap: bool = True,
        context_window: int = 120,
        spacy_batch_size: int = 512,
        max_frags_per_concept: int = 50,
        max_fragment_chars: int = 80,
        idf_max_concepts: int = 300,
    ) -> dict[str, ConceptSynonyms]:
        """Mine minimal noun-phrase synonyms per us-gaap concept.

        Uses a four-phase pipeline with two pre-filters that reduce the spaCy
        workload from ~2M fragments to ~150–300K:

        1. **Collect** — iterate dataset, extract a *text fragment* per entity:
           the span text itself when non-numeric; the pre-span context when the
           span is a numeric value cell.

        2. **Pre-filter A — cap per concept** — after collection, keep only the
           ``max_frags_per_concept`` most frequent fragments per concept.
           Rare one-off prose phrasings are eliminated; the most-frequent
           fragments are the table-row labels that produce clean NPs.

        3. **Deduplicate** across concepts and split into two pools:

           * *Short* (``len ≤ max_fragment_chars``): sent to spaCy for
             full ``noun_chunks`` extraction.
           * *Long* (``len > max_fragment_chars``): processed by a fast
             regex VP-trimmer (:func:`_trim_noun_phrase_regex`) — long prose
             fragments rarely produce better NPs than the trimmer delivers, and
             this keeps spaCy's workload small.

        4. **Extract NPs** — spaCy ``en_core_web_sm`` in batch for the short
           pool; regex trimmer for the long pool.

        5. **Aggregate** — weight NPs by entity-span counts, apply
           ``min_count``, return ``top_k`` per concept.

        6. **IDF filter** — drop any NP that appears across more than
           ``idf_max_concepts`` distinct concepts; such phrases are too generic
           to be useful discriminators (e.g. "carrying amounts", "net assets"
           appearing for hundreds of balance-sheet line items).

        Parameters:
            top_k: Maximum synonyms to keep per concept (default 15).
            splits: Dataset splits to process (``None`` uses all available).
            min_count: Drop NP phrases appearing fewer than this many times
                (default 3).  Applied only to concepts with enough data to
                clear it — see ``low_freq_occurrence_threshold``.
            low_freq_occurrence_threshold: A concept whose total entity-span
                count is below this value is treated as *low-frequency* and the
                ``min_count`` noise filter is relaxed to 1 for it (default 30).
                Without this relaxation, single-filer company-extension concepts
                (e.g. ``nvda:PurchaseObligationSupplyAgreements``) and rare
                us-gaap concepts are erased wholesale, because their surface
                forms appear in varied prose and almost never recur
                ``min_count`` times.  Phase-3 quality filters plus ``top_k``
                still bound the output, so recall is gained without flooding
                noise.  Set to ``0`` to disable the relaxation entirely
                (every concept then uses the full ``min_count`` threshold).
            require_label_overlap: When True (default), apply the **Phase-4b
                label-overlap precision gate**: keep a mined synonym only if it
                shares a content token with the concept's own QName label (see
                :func:`_concept_label_tokens`).  This removes the dominant noise
                class — single-filer *dimension members* that a concept
                legitimately tags but that are not synonyms of it (e.g.
                ``"silver sales"``, ``"united states"`` under a revenue
                concept).  Cross-concept frequency cannot catch these (most are
                df=1, and genuine noise such as ``"united states"`` is actually
                high-df), so label-overlap is the only effective gate.
                Trade-off: a few valid-but-distant surface forms are dropped
                (e.g. ``"net sales"`` for
                ``RevenueFromContractWithCustomer…``) — these are covered by the
                curated family→QName map on the resolver's primary path.  Set to
                False for the older high-recall / low-precision behaviour.
            context_window: Characters to look back from a numeric span.
            spacy_batch_size: Batch size passed to ``nlp.pipe()``.
            max_frags_per_concept: Pre-filter A cap — top-N fragments kept
                per concept before deduplication (default 50).
            max_fragment_chars: Pre-filter B threshold — fragments longer than
                this use the regex VP-trimmer instead of spaCy (default 80).
            idf_max_concepts: IDF filter threshold — NPs appearing in more
                than this many concepts are discarded as too generic
                (default 300; set to 0 to disable).

        Returns:
            Mapping ``concept_qname → ConceptSynonyms``.

        Raises:
            RuntimeError: If the dataset has not been loaded yet.
            ImportError: If spaCy or its English model is not installed.
        """
        if self._dataset is None:
            raise RuntimeError(
                "Dataset not loaded — call download() or load() first."
            )

        splits_to_use = (
            list(splits)
            if splits
            else list(self._dataset.keys())
            if hasattr(self._dataset, "keys")
            else ["train"]
        )

        # ------------------------------------------------------------------ #
        # Phase 1 — collect (concept, fragment) co-occurrence counts          #
        # fragment_counts[concept][fragment] = number of entity spans         #
        # ------------------------------------------------------------------ #
        fragment_counts: dict[str, Counter] = defaultdict(Counter)
        concept_span_totals: dict[str, int] = defaultdict(int)

        for split_name in splits_to_use:
            split_ds = (
                self._dataset[split_name]
                if hasattr(self._dataset, "__getitem__")
                else self._dataset
            )
            logger.info(
                "Phase 1 — collecting fragments from split %r (%d examples) …",
                split_name, len(split_ds),
            )
            schema = _inspect_schema(split_ds)

            for example in split_ds:
                text: str = example.get(schema.get("text", "text"), "") or ""
                entities_raw = example.get(
                    schema.get("entities", "entities"), []
                ) or []

                for ent in entities_raw:
                    if not isinstance(ent, dict):
                        continue

                    concept = (
                        ent.get("Label") or ent.get("label")
                        or ent.get("concept") or ent.get("tag") or ""
                    )
                    if not concept:
                        continue

                    start = (
                        ent.get("Start character")
                        if ent.get("Start character") is not None
                        else ent.get("start", ent.get("start_char"))
                    )
                    end = (
                        ent.get("End character")
                        if ent.get("End character") is not None
                        else ent.get("end", ent.get("end_char"))
                    )

                    if start is not None and end is not None:
                        span_text = text[int(start):int(end)]
                    else:
                        tokens = ent.get("tokens") or ent.get("words") or []
                        span_text = " ".join(str(t) for t in tokens)

                    if _NUMERIC_RE.match(span_text.strip()):
                        fragment = _get_pre_context_fragment(
                            text, int(start or 0), context_window
                        )
                    else:
                        fragment = span_text.strip()
                        # Non-numeric spans that start lowercase are mid-sentence
                        # iXBRL tags on prose text — skip, same rule as context frags.
                        if fragment and fragment[0].isalpha() and fragment[0].islower():
                            fragment = ""

                    concept_span_totals[concept] += 1
                    if fragment and len(fragment) >= 3:
                        fragment_counts[concept][fragment] += 1

        logger.info(
            "Phase 1 complete: %d concepts, %d unique (concept, fragment) pairs.",
            len(fragment_counts),
            sum(len(c) for c in fragment_counts.values()),
        )

        # ------------------------------------------------------------------ #
        # Pre-filter A — cap fragments per concept                            #
        # Keep only the top-N most frequent fragments per concept so that     #
        # rare one-off prose phrasings never reach spaCy.                     #
        # ------------------------------------------------------------------ #
        if max_frags_per_concept > 0:
            pruned = 0
            for concept in fragment_counts:
                fc = fragment_counts[concept]
                if len(fc) > max_frags_per_concept:
                    kept = dict(fc.most_common(max_frags_per_concept))
                    pruned += len(fc) - len(kept)
                    fragment_counts[concept] = Counter(kept)
            logger.info(
                "Pre-filter A (cap=%d/concept): pruned %d rare fragments.",
                max_frags_per_concept, pruned,
            )

        # ------------------------------------------------------------------ #
        # Phase 2 — deduplicate and split into short (spaCy) / long (regex)  #
        # ------------------------------------------------------------------ #
        all_unique_fragments: list[str] = list(
            {f for counter in fragment_counts.values() for f in counter}
        )
        short_frags = [f for f in all_unique_fragments if len(f) <= max_fragment_chars]
        long_frags  = [f for f in all_unique_fragments if len(f) >  max_fragment_chars]
        logger.info(
            "Phase 2 -- %d unique fragments: %d short (->spaCy), %d long (->regex VP-trim).",
            len(all_unique_fragments), len(short_frags), len(long_frags),
        )

        # ------------------------------------------------------------------ #
        # Phase 3 — NP extraction: spaCy for short, regex trimmer for long   #
        # ------------------------------------------------------------------ #
        fragment_to_nps: dict[str, list[str]] = {}

        # 3a — spaCy batch on short fragments
        if short_frags:
            nlp = _load_spacy_nlp()
            logger.info(
                "Phase 3a — spaCy NP extraction on %d short fragments (batch=%d) …",
                len(short_frags), spacy_batch_size,
            )
            for frag, nps in zip(
                short_frags,
                _extract_noun_phrases_batch(short_frags, nlp, batch_size=spacy_batch_size),
            ):
                fragment_to_nps[frag] = nps
            logger.info("Phase 3a complete.")

        # 3b — regex VP-trimmer on long fragments
        if long_frags:
            logger.info(
                "Phase 3b — regex VP-trim on %d long fragments …", len(long_frags)
            )
            for frag in long_frags:
                fragment_to_nps[frag] = _trim_noun_phrase_regex(frag)
            logger.info("Phase 3b complete.")

        logger.info("Phase 3 complete — %d fragments mapped to NPs.", len(fragment_to_nps))

        # ------------------------------------------------------------------ #
        # Phase 4 — aggregate NPs per concept, filter, top-k                 #
        # ------------------------------------------------------------------ #
        concept_np_counts: dict[str, Counter] = defaultdict(Counter)
        for concept, frag_counter in fragment_counts.items():
            for fragment, span_count in frag_counter.items():
                for np_phrase in fragment_to_nps.get(fragment, []):
                    concept_np_counts[concept][np_phrase] += span_count

        result: dict[str, ConceptSynonyms] = {}
        low_freq_rescued = 0
        for concept, np_counter in concept_np_counts.items():
            total = concept_span_totals[concept]
            # The min_count bar is a noise filter that only makes sense when a
            # concept has enough occurrences to support it. Single-filer
            # extension concepts (nvda:, etr:, …) and rare us-gaap concepts
            # appear in varied prose, so no NP recurs >= min_count times and the
            # concept would be dropped entirely. Relax the threshold to 1 for
            # such low-frequency concepts; the Phase-3 quality filters and top_k
            # already bound the output.
            if total < low_freq_occurrence_threshold:
                effective_min = 1
            else:
                effective_min = min_count
            filtered = {
                np: cnt for np, cnt in np_counter.items() if cnt >= effective_min
            }
            if not filtered:
                continue
            if effective_min < min_count:
                low_freq_rescued += 1
            top = sorted(filtered, key=filtered.__getitem__, reverse=True)[:top_k]
            result[concept] = ConceptSynonyms(
                concept=concept,
                synonyms=top,
                total_occurrences=total,
            )

        logger.info(
            "Phase 4: %d concepts kept (%d rescued via low-frequency relaxation, "
            "threshold=%d occurrences).",
            len(result), low_freq_rescued, low_freq_occurrence_threshold,
        )

        # ------------------------------------------------------------------ #
        # Phase 4b — label-overlap precision gate                            #
        # Drop synonyms sharing no content token with the concept's own QName  #
        # label. Removes single-filer dimension members the concept           #
        # legitimately tags but that are not synonyms of it — the dominant     #
        # noise class, which cross-concept frequency cannot catch.            #
        # ------------------------------------------------------------------ #
        if require_label_overlap and result:
            syn_before = sum(len(cs.synonyms) for cs in result.values())
            gated: dict[str, ConceptSynonyms] = {}
            concepts_dropped = 0
            for concept, cs in result.items():
                label_tokens = _concept_label_tokens(concept)
                kept_syns = [
                    s for s in cs.synonyms
                    if _synonym_overlaps_label(s, label_tokens)
                ]
                if kept_syns:
                    gated[concept] = ConceptSynonyms(
                        concept=concept,
                        synonyms=kept_syns,
                        total_occurrences=cs.total_occurrences,
                    )
                else:
                    concepts_dropped += 1
            syn_after = sum(len(cs.synonyms) for cs in gated.values())
            logger.info(
                "Phase 4b (label-overlap gate): synonyms %d -> %d; "
                "concepts %d -> %d (%d dropped with no anchored synonym).",
                syn_before, syn_after, len(result), len(gated), concepts_dropped,
            )
            result = gated

        # ------------------------------------------------------------------ #
        # Phase 5 — IDF filter: drop NPs that appear across too many concepts #
        # ------------------------------------------------------------------ #
        if idf_max_concepts > 0 and result:
            np_concept_freq: Counter = Counter(
                np for cs in result.values() for np in cs.synonyms
            )
            generic_nps = {
                np for np, cnt in np_concept_freq.items()
                if cnt > idf_max_concepts
            }
            if generic_nps:
                logger.info(
                    "Phase 5 (IDF filter, threshold=%d): removing %d over-generic NPs "
                    "that each span >%d concepts.",
                    idf_max_concepts, len(generic_nps), idf_max_concepts,
                )
                pruned_result: dict[str, ConceptSynonyms] = {}
                for concept, cs in result.items():
                    clean = [s for s in cs.synonyms if s not in generic_nps]
                    if clean:
                        pruned_result[concept] = ConceptSynonyms(
                            concept=concept,
                            synonyms=clean,
                            total_occurrences=cs.total_occurrences,
                        )
                result = pruned_result

        logger.info(
            "Synonym mining complete: %d concepts with at least one NP synonym.",
            len(result),
        )
        return result

    # ------------------------------------------------------------------
    # Step 3 — Concept-family table from taxonomy JSON
    # ------------------------------------------------------------------

    def build_concept_family_table(
        self,
        taxonomy_type: str = "both",
    ) -> ConceptFamilyTable:
        """Build a :class:`ConceptFamilyTable` from the master taxonomy files.

        Parameters:
            taxonomy_type: Which taxonomy to use.  One of ``"calculation"``,
                ``"presentation"``, or ``"both"`` (default).  When ``"both"``,
                the calculation taxonomy is used as the base and the
                presentation taxonomy fills in any concepts that are missing.

        Returns:
            A fully populated :class:`ConceptFamilyTable`.

        Raises:
            FileNotFoundError: If the required taxonomy file(s) have not been
                downloaded yet.
        """
        nodes: dict[str, ConceptFamilyNode] = {}

        if taxonomy_type in ("calculation", "both"):
            if self._calc_taxonomy_path is None or not self._calc_taxonomy_path.exists():
                raise FileNotFoundError(
                    "calculationMasterTaxonomy.jsonl not found. "
                    "Call download() first."
                )
            calc_nodes = _parse_taxonomy_file(
                self._calc_taxonomy_path, taxonomy_source="calculation"
            )
            nodes.update(calc_nodes)
            logger.info(
                "Loaded %d nodes from %s",
                len(calc_nodes), self._calc_taxonomy_path.name,
            )

        if taxonomy_type in ("presentation", "both"):
            if self._pres_taxonomy_path is None or not self._pres_taxonomy_path.exists():
                raise FileNotFoundError(
                    "presentationMasterTaxonomy.jsonl not found. "
                    "Call download() first."
                )
            pres_nodes = _parse_taxonomy_file(
                self._pres_taxonomy_path, taxonomy_source="presentation"
            )
            # Merge: add any concept not already present from calculation
            new_count = 0
            for qname, pnode in pres_nodes.items():
                if qname not in nodes:
                    nodes[qname] = pnode
                    new_count += 1
                else:
                    # Supplement missing label / children from presentation
                    existing = nodes[qname]
                    if not existing.label and pnode.label:
                        existing.label = pnode.label
                    for child in pnode.children:
                        if child not in existing.children:
                            existing.children.append(child)
            logger.info(
                "Loaded %d additional nodes from presentationMasterTaxonomy.json",
                new_count,
            )

        table = ConceptFamilyTable(nodes)
        logger.info("ConceptFamilyTable built: %s", table)
        return table

    # ------------------------------------------------------------------
    # Convenience: load without re-download
    # ------------------------------------------------------------------

    def load(self, splits: Sequence[str] | None = None) -> None:
        """Load already-downloaded dataset and taxonomy files from cache.

        Raises ``FileNotFoundError`` if the dataset has not been downloaded.
        """
        # Locate taxonomy files (support both .jsonl and legacy .json names)
        taxonomy_dir = self.cache_dir / "taxonomy"
        for filename in TAXONOMY_FILENAMES:
            for candidate in (taxonomy_dir / filename, taxonomy_dir / filename.replace(".jsonl", ".json")):
                if candidate.exists():
                    if filename.startswith("calculation"):
                        self._calc_taxonomy_path = candidate
                    else:
                        self._pres_taxonomy_path = candidate
                    break

        # Load HF dataset from local cache
        self._load_hf_dataset(splits=splits)

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def save_synonyms(
        self,
        synonyms: dict[str, ConceptSynonyms],
        output_path: Path | str,
        format: str = "json",
    ) -> Path:
        """Persist *synonyms* to disk.

        Parameters:
            synonyms: Result of :meth:`generate_concept_synonyms`.
            output_path: Target file path.
            format: ``"json"`` (default) or ``"jsonl"``.

        Returns:
            Resolved path of the written file.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if format == "jsonl":
            with output_path.open("w", encoding="utf-8") as fh:
                for cs in synonyms.values():
                    record = {
                        "concept": cs.concept,
                        "synonyms": cs.synonyms,
                        "total_occurrences": cs.total_occurrences,
                    }
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        else:
            payload = {
                qname: {
                    "synonyms": cs.synonyms,
                    "total_occurrences": cs.total_occurrences,
                }
                for qname, cs in synonyms.items()
            }
            with output_path.open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)

        logger.info("Saved %d concept synonyms to %s", len(synonyms), output_path)
        return output_path.resolve()

    def save_concept_family_table(
        self,
        table: ConceptFamilyTable,
        output_path: Path | str,
    ) -> Path:
        """Persist *table* to a JSON file.

        The file can be reloaded with :meth:`ConceptFamilyTable.from_dict`.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as fh:
            json.dump(table.to_dict(), fh, ensure_ascii=False, indent=2)
        logger.info(
            "Saved ConceptFamilyTable (%d nodes) to %s",
            len(table), output_path,
        )
        return output_path.resolve()


# ---------------------------------------------------------------------------
# Internal helpers — spaCy NP extraction
# ---------------------------------------------------------------------------

# Lazy-loaded spaCy model (populated by _load_spacy_nlp on first call)
_spacy_nlp: Any = None

# Strip leading articles before normalising a noun chunk
_LEADING_ARTICLE_RE = re.compile(r"^(?:the|a|an)\s+", re.IGNORECASE)

# Strip trailing preposition phrases that are not part of the core NP
# e.g. "research and development expense for the year" → "research and development expense"
_TRAILING_PREP_RE = re.compile(
    r"\s+(?:of|in|for|with|from|on|at|by|as|to|during|through|including|excluding)\b.*$",
    re.IGNORECASE,
)

# Regex VP-trimmer used for long fragments (> max_fragment_chars) as a fast
# alternative to spaCy.  Strips trailing verb + everything after it so that
# "research and development costs were" → "research and development costs".
_TRAILING_VP_RE = re.compile(
    r"\s+(?:was|were|is|are|has|have|had|totaled?|amounted?|"
    r"increased?|decreased?|declined?|grew|represented?|comprised?|"
    r"consisted?|included?|equaled?|reached|exceeded?|"
    r"of|for|during|in|per|at|with|from|through|excluding|including)\b.*$",
    re.IGNORECASE,
)

# Noun chunks whose content is *only* generic pronouns / company references
_GENERIC_NP_RE = re.compile(
    r"^(?:we|our|us|the company|company|it|its|they|their|"
    r"management|the group|the firm|the corporation|the entity)$",
    re.IGNORECASE,
)

# --- Five quality-tightening filters applied by both NP extractors ----------

# 1. Word-count bounds: at least 2 words (no bare single-word NPs like "charges"
#    or "loss") and at most 6 words (no long prose fragments like "our natural
#    gas pipelines segment's regulated operations").
_MIN_NP_WORDS: int = 2
_MAX_NP_WORDS: int = 6

# 2. Minimum content-word length: content words must be ≥ 3 chars.
#    Filters truncated fragments ("ce", "an", "ad") that creep in when the
#    context-window cuts off mid-word.
_MIN_CONTENT_WORD_LEN: int = 3

# 3. Date / temporal expression detector — any NP containing a month name,
#    quarter reference, four-digit year, or relative period phrase is a
#    timestamp, not a concept label.
_DATE_NP_RE = re.compile(
    r"\b(?:january|february|march|april|may|june|july|august|"
    r"september|october|november|december|"
    r"(?:first|second|third|fourth)\s+quarter|q[1-4]|"
    r"fiscal\s+year|(?:19|20)\d{2}|"
    # relative temporal phrases that appear in "for the X months ended…" context
    r"three\s+months?|six\s+months?|nine\s+months?|twelve\s+months?|"
    r"(?:first|second)\s+half|"
    r"(?:annual|reporting|prior|current|comparable)\s+period|"
    r"year[- ]ended|period[- ]ended)\b",
    re.IGNORECASE,
)

# 4. Prose / demonstrative / possessive sentence-starters — NPs whose first
#    word is one of these are contextual references or mid-sentence fragments,
#    not standalone concept labels.
_DEMONSTRATIVE_STARTS: frozenset = frozenset({
    # demonstratives
    "this", "these", "those", "such", "said",
    # possessives / pronouns
    "our", "their", "its", "we", "they",
    # quantifiers
    "any", "each", "all", "both", "other", "another", "certain", "some",
    # conditional / temporal / causal clause starters
    "if", "when", "while", "although", "however", "therefore", "upon",
    "pursuant", "since", "which", "as", "that",
    # prepositions that open prose phrases, not concept labels
    "in", "on", "at", "by", "with", "from", "per", "during",
    # other common mid-prose starters
    "no", "not", "also", "to",
})

# 5. Comma guard — commas inside NPs almost always signal a prose coordinate
#    structure ("since inception, the company") rather than a clean label.

# 6. Possessive guard — NPs where the first word ends with "'s" are contextual
#    modifiers ("company's net loss", "entity's total assets"), not clean labels.
#    Match both ASCII apostrophe U+0027 and Unicode curly quotes U+2018/U+2019
#    because NFKC does NOT map U+2019 → U+0027.
_POSSESSIVE_WORD_RE = re.compile(
    r"['‘’ʼ]s$|s['‘’ʼ]$",
    re.IGNORECASE,
)


def _load_spacy_nlp() -> Any:
    """Lazily load and return the spaCy ``en_core_web_sm`` model.

    Raises :class:`ImportError` with installation instructions if spaCy or
    the English model is not available.
    """
    global _spacy_nlp
    if _spacy_nlp is not None:
        return _spacy_nlp

    try:
        import spacy  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(
            "spaCy is required for noun phrase extraction.\n"
            "  pip install spacy\n"
            "  python -m spacy download en_core_web_sm"
        ) from exc

    try:
        # Disable NER — we only need the tagger + parser for noun_chunks
        _spacy_nlp = spacy.load("en_core_web_sm", disable=["ner"])
    except OSError as exc:
        raise ImportError(
            "spaCy English model not found.\n"
            "  python -m spacy download en_core_web_sm"
        ) from exc

    logger.info("spaCy model loaded: %s", _spacy_nlp.meta.get("name", "en_core_web_sm"))
    return _spacy_nlp


def _extract_noun_phrases_batch(
    fragments: list[str],
    nlp: Any,
    batch_size: int = 512,
) -> list[list[str]]:
    """Extract minimal noun phrases from *fragments* using spaCy.

    Each element of the returned list corresponds to the fragment at the same
    index and contains the normalised NP strings found in that fragment.

    Normalisation steps per chunk:
    1. Unicode NFKC → lowercase
    2. Strip leading articles (the / a / an)
    3. Strip trailing prepositional tails (``of``, ``in``, ``for``, …)
    4. Collapse whitespace

    Quality filters (shared with :func:`_trim_noun_phrase_regex`):
    * Word count: ``_MIN_NP_WORDS`` ≤ words ≤ ``_MAX_NP_WORDS``
    * Content-word minimum length: ``_MIN_CONTENT_WORD_LEN`` chars each
    * Generic company / pronoun reference rejected
    * Purely numeric span rejected
    * Date / temporal expression rejected
    * Demonstrative / possessive sentence-starter rejected
    * Comma inside NP rejected (prose coordinate structure, not a label)
    """
    results: list[list[str]] = []

    for doc in nlp.pipe(fragments, batch_size=batch_size):
        nps: list[str] = []
        for chunk in doc.noun_chunks:
            text = unicodedata.normalize("NFKC", chunk.text).strip()
            text = _LEADING_ARTICLE_RE.sub("", text)
            text = _TRAILING_PREP_RE.sub("", text)
            text = re.sub(r"\s+", " ", text).strip().lower()

            if not text or len(text) < 3:
                continue
            if _GENERIC_NP_RE.match(text):
                continue
            if _NUMERIC_RE.match(text):
                continue

            words = text.split()

            # Filter 1 — word-count bounds
            if not (_MIN_NP_WORDS <= len(words) <= _MAX_NP_WORDS):
                continue

            # Filter 2 — content-word minimum length
            content = [
                w for w in words
                if w not in _STOP_WORDS and len(w) >= _MIN_CONTENT_WORD_LEN
            ]
            if not content:
                continue

            # Filter 3 — date / temporal expression
            if _DATE_NP_RE.search(text):
                continue

            # Filter 4 — demonstrative / possessive sentence-starter
            if words[0] in _DEMONSTRATIVE_STARTS:
                continue

            # Filter 5 — comma guard (prose coordinate, not a clean label)
            if "," in text:
                continue

            # Filter 6 — possessive modifier at start
            #   e.g. "company's net loss", "entity's total assets"
            if _POSSESSIVE_WORD_RE.search(words[0]):
                continue

            nps.append(text)

        results.append(nps)

    return results


def _trim_noun_phrase_regex(fragment: str) -> list[str]:
    """Fast regex-based NP extraction for long fragments (spaCy fallback).

    Strips trailing verb-phrase words so that
    ``"research and development costs were"`` → ``["research and development costs"]``.

    Applies the **same five quality filters** as :func:`_extract_noun_phrases_batch`
    so both paths produce synonyms at the same quality level.
    Returns a list of 0 or 1 strings (same shape as the batch extractor output).
    """
    text = unicodedata.normalize("NFKC", fragment).strip()
    text = _TRAILING_VP_RE.sub("", text)
    text = _LEADING_ARTICLE_RE.sub("", text)
    text = _TRAILING_PREP_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip().lower()

    if not text or len(text) < 3:
        return []
    if _GENERIC_NP_RE.match(text):
        return []
    if _NUMERIC_RE.match(text):
        return []

    words = text.split()

    # Filter 1 — word-count bounds
    if not (_MIN_NP_WORDS <= len(words) <= _MAX_NP_WORDS):
        return []

    # Filter 2 — content-word minimum length
    content = [
        w for w in words
        if w not in _STOP_WORDS and len(w) >= _MIN_CONTENT_WORD_LEN
    ]
    if not content:
        return []

    # Filter 3 — date / temporal expression
    if _DATE_NP_RE.search(text):
        return []

    # Filter 4 — demonstrative / possessive sentence-starter
    if words[0] in _DEMONSTRATIVE_STARTS:
        return []

    # Filter 5 — comma guard
    if "," in text:
        return []

    # Filter 6 — possessive modifier at start
    if _POSSESSIVE_WORD_RE.search(words[0]):
        return []

    return [text]


def _get_pre_context_fragment(text: str, start: int, window: int = 120) -> str:
    """Return the best label-candidate text immediately before a numeric span.

    Strategy (designed for iXBRL table + prose patterns):

    1. Slice up to *window* characters before *start*.
    2. Split on ``\\n`` (row boundaries) — take lines from newest to oldest.
    3. Within each line, split on ``\\t`` (column boundaries) — take segments
       from right to left.
    4. Skip segments that are purely numeric (they are prior-period values in
       the same table row).
    5. Within the chosen segment, further split on sentence-terminal punctuation
       (``.`` / ``;`` / ``:``) and return the rightmost non-empty part.

    This correctly handles:
    * ``"Research and development expenses\\t4,789"``
      → pre-context ``"Research and development expenses\\t"``
      → line ``"Research and development expenses\\t"``
      → segment ``"Research and development expenses"``

    * ``"Operating expenses\\t12,345\\nSelling expenses\\t"``
      (span starts at the end of the second line before the value)
      → last line ``"Selling expenses\\t"``
      → segment ``"Selling expenses"``

    * ``"R&D expense was "``
      → single line, single segment → ``"R&D expense was"``
    """
    if not text or start <= 0:
        return ""
    pre = text[max(0, start - window):start]

    # Walk lines newest → oldest
    for line in reversed(pre.split("\n")):
        # Walk tab-delimited columns rightmost → leftmost
        for segment in reversed(line.split("\t")):
            segment = segment.strip()
            if not segment or len(segment) < 3:
                continue
            # Skip pure numeric segments (prior-column values)
            if _NUMERIC_RE.match(segment):
                continue
            # Apply sentence-terminal split inside the segment
            frags = re.split(r"(?<=[.;:])\s+", segment)
            candidate = frags[-1].strip()
            if candidate and len(candidate) >= 3:
                # Require uppercase letter or digit start:
                #   • table-row labels always start uppercase → accept
                #   • lowercase start → mid-sentence truncation → skip
                #   • '(', '-', '%', digit prefix → also reject (numeric/parenthetical)
                first = candidate[0]
                if not (first.isupper() or first.isdigit()):
                    continue  # skip; try older lines/segments
                return candidate

    return ""


# ---------------------------------------------------------------------------
# Internal helpers — text normalisation (kept for backward compat / tests)
# ---------------------------------------------------------------------------


def _normalise_phrase(raw: str) -> str:
    """Lightweight normalisation for a raw text span.

    Returns an empty string if the input is purely numeric or too short to be
    a meaningful synonym candidate.  Used as a pre-filter before spaCy.
    """
    if not raw:
        return ""

    text = unicodedata.normalize("NFKC", raw).strip()

    if _NUMERIC_RE.match(text):
        return ""

    text = _PUNCT_RE.sub("", text).strip()
    text = re.sub(r"\s+", " ", text).lower()

    words = text.split()
    content_words = [w for w in words if w not in _STOP_WORDS]
    if not content_words:
        return ""
    if len(words) < _MIN_WORDS:
        return ""

    return text


# ---------------------------------------------------------------------------
# Internal helpers — schema detection
# ---------------------------------------------------------------------------


def _inspect_schema(dataset: Any) -> dict[str, str]:
    """Return a mapping of logical role → actual field name for a HF dataset.

    Prefers the HuggingFace ``features`` / ``column_names`` metadata so the
    dataset iterator is **never consumed**.  Falls back to peeking at the first
    example only when metadata is unavailable.

    Returns:
        Dict with keys ``"text"`` and ``"entities"``, values being the
        actual column names in the dataset.
    """
    schema: dict[str, str] = {"text": "text", "entities": "entities"}

    # --- Preferred path: use dataset metadata (does NOT advance the iterator) ---
    keys: set[str] = set()
    for attr in ("features", "column_names"):
        meta = getattr(dataset, attr, None)
        if meta is not None:
            keys = set(meta) if not isinstance(meta, dict) else set(meta.keys())
            break

    # --- Fallback: inspect first example (advances iterator — avoid if possible) ---
    if not keys:
        try:
            first = next(iter(dataset))
        except (StopIteration, TypeError):
            return schema
        keys = set(first.keys()) if isinstance(first, dict) else set()

    # Detect text field
    for candidate in ("text", "paragraph", "sentence", "tokens", "words", "content"):
        if candidate in keys:
            schema["text"] = candidate
            break

    # Detect entity list field
    for candidate in (
        "entities", "ner_tags", "tags", "labels", "annotations",
        "keyphrases", "spans", "facts",
    ):
        if candidate in keys:
            schema["entities"] = candidate
            break

    return schema


# ---------------------------------------------------------------------------
# Internal helpers — taxonomy file parsing (JSONL + JSON)
# ---------------------------------------------------------------------------


def _parse_taxonomy_file(
    path: Path,
    taxonomy_source: str = "unknown",
) -> dict[str, ConceptFamilyNode]:
    """Parse a master taxonomy file into a dict of :class:`ConceptFamilyNode`.

    Supports two file formats auto-detected by extension / content:

    **JSONL arc list** (HiFi-KPI actual format, ``.jsonl``) — one arc per line::

        {"child": "us-gaap:Assets", "parent": "us-gaap:LiabilitiesAndStockholdersEquity", "occurrence": 47179}

    The ``occurrence`` field records how often this parent→child relationship
    appeared across all SEC filings in the HiFi-KPI corpus; it is stored on
    the child node as ``weight`` so downstream code can rank by prevalence.

    **JSON** (legacy / alternative formats, ``.json``) — four sub-shapes handled:

    *Shape A* — flat dict keyed by QName::

        {"us-gaap:Assets": {"label": "Assets", "level": 3, "children": [...]}}

    *Shape B* — flat list of node objects::

        [{"concept": "us-gaap:Assets", "label": "Assets", ...}]

    *Shape C* — nested tree (``"name"``/``"children"``)

    *Shape D* — arc list with ``"from"``/``"to"`` keys
    """
    suffix = path.suffix.lower()

    if suffix == ".jsonl":
        return _parse_jsonl_arcs(path)

    # --- JSON path ---
    with path.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)

    if isinstance(raw, dict) and not _is_arc_dict(raw):
        return _parse_shape_a(raw)
    elif isinstance(raw, list):
        return _parse_shape_b_or_d(raw)
    elif isinstance(raw, dict):
        return _parse_shape_a(raw)
    else:
        logger.warning(
            "Unrecognised taxonomy JSON shape in %s; returning empty table.", path
        )
        return {}


def _parse_jsonl_arcs(path: Path) -> dict[str, ConceptFamilyNode]:
    """Parse a JSONL file of ``{"child", "parent", "occurrence"}`` arcs.

    Each line is one directed edge in the taxonomy DAG.  Nodes are created
    on first encounter; ``occurrence`` is stored as the child's ``weight``
    (higher = more common parent-child relationship across SEC filings).
    BFS from roots is run afterwards to assign integer ``level`` values.
    """
    nodes: dict[str, ConceptFamilyNode] = {}

    with path.open("r", encoding="utf-8") as fh:
        for lineno, raw_line in enumerate(fh, 1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                arc = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.debug("Skipping malformed line %d in %s: %s", lineno, path.name, exc)
                continue

            child_qname = arc.get("child") or arc.get("to") or ""
            parent_qname = arc.get("parent") or arc.get("from") or ""
            occurrence = arc.get("occurrence") or arc.get("weight") or 0

            if not child_qname:
                continue

            # Ensure parent node exists
            if parent_qname and parent_qname not in nodes:
                nodes[parent_qname] = ConceptFamilyNode(
                    concept=parent_qname,
                    label=_qname_to_label(parent_qname),
                    level=0,
                    parent=None,
                    children=[],
                    weight=None,
                )

            # Ensure child node exists
            if child_qname not in nodes:
                nodes[child_qname] = ConceptFamilyNode(
                    concept=child_qname,
                    label=_qname_to_label(child_qname),
                    level=0,
                    parent=parent_qname or None,
                    children=[],
                    weight=float(occurrence) if occurrence else None,
                )
            else:
                # Node seen before (from a different arc) — fill in parent if missing
                child_node = nodes[child_qname]
                if child_node.parent is None and parent_qname:
                    child_node.parent = parent_qname
                if occurrence and child_node.weight is None:
                    child_node.weight = float(occurrence)

            # Wire parent → child
            if parent_qname:
                parent_node = nodes[parent_qname]
                if child_qname not in parent_node.children:
                    parent_node.children.append(child_qname)

    logger.debug(
        "Parsed %d nodes from JSONL arc file %s", len(nodes), path.name
    )
    _assign_levels_from_roots(nodes)
    return nodes


def _is_arc_dict(data: dict) -> bool:
    """Return True if the dict looks like a list of arc objects (shape D)."""
    # A flat-node dict will have dict values; an arc list wrapped in a dict won't
    first_val = next(iter(data.values()), None) if data else None
    return isinstance(first_val, list) and all(
        isinstance(item, dict) and ("from" in item or "to" in item)
        for item in first_val[:3]
    )


def _parse_shape_a(raw: dict[str, Any]) -> dict[str, ConceptFamilyNode]:
    """Shape A: flat dict keyed by QName."""
    nodes: dict[str, ConceptFamilyNode] = {}

    for qname, val in raw.items():
        if not isinstance(val, dict):
            continue
        node = ConceptFamilyNode(
            concept=qname,
            label=_extract_label(val, qname),
            level=int(val.get("level", val.get("depth", val.get("tier", 0)))),
            parent=val.get("parent") or val.get("parentConcept") or None,
            children=_extract_children(val),
            weight=_to_float(val.get("weight")),
        )
        nodes[qname] = node

    _infer_parents_from_children(nodes)
    return nodes


def _parse_shape_b_or_d(raw: list[Any]) -> dict[str, ConceptFamilyNode]:
    """Shape B (list of node objects) or Shape D (list of arc objects)."""
    if not raw:
        return {}

    first = raw[0]
    if not isinstance(first, dict):
        return {}

    # Detect arc format (Shape D)
    if "from" in first or "to" in first:
        return _parse_shape_d_arcs(raw)

    # Shape B — list of node dicts
    nodes: dict[str, ConceptFamilyNode] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        qname = (
            item.get("concept")
            or item.get("name")
            or item.get("qname")
            or item.get("id")
            or ""
        )
        if not qname:
            continue
        node = ConceptFamilyNode(
            concept=qname,
            label=_extract_label(item, qname),
            level=int(item.get("level", item.get("depth", item.get("tier", 0)))),
            parent=item.get("parent") or item.get("parentConcept") or None,
            children=_extract_children(item),
            weight=_to_float(item.get("weight")),
        )
        nodes[qname] = node

    _infer_parents_from_children(nodes)
    return nodes


def _parse_shape_d_arcs(arcs: list[dict[str, Any]]) -> dict[str, ConceptFamilyNode]:
    """Shape D: list of calculation arc dicts {"from", "to", "weight", …}."""
    nodes: dict[str, ConceptFamilyNode] = {}

    for arc in arcs:
        parent_qname = arc.get("from") or arc.get("parent") or ""
        child_qname = arc.get("to") or arc.get("child") or ""
        weight = _to_float(arc.get("weight"))

        for qname in (parent_qname, child_qname):
            if qname and qname not in nodes:
                nodes[qname] = ConceptFamilyNode(
                    concept=qname,
                    label=arc.get("fromLabel" if qname == parent_qname else "toLabel", qname),
                    level=0,
                    parent=None,
                    children=[],
                    weight=None,
                )

        if parent_qname and child_qname:
            parent_node = nodes[parent_qname]
            if child_qname not in parent_node.children:
                parent_node.children.append(child_qname)
            child_node = nodes[child_qname]
            child_node.parent = parent_qname
            if weight is not None:
                child_node.weight = weight

    _assign_levels_from_roots(nodes)
    return nodes


def _parse_nested_tree(
    subtree: dict[str, Any],
    parent_qname: str | None,
    nodes: dict[str, ConceptFamilyNode],
    depth: int,
) -> None:
    """Recursively parse a nested tree (Shape C)."""
    qname = (
        subtree.get("name")
        or subtree.get("concept")
        or subtree.get("qname")
        or subtree.get("id")
        or ""
    )
    if not qname:
        return

    children_raw = subtree.get("children") or []
    child_qnames = []
    for child in children_raw:
        child_qname = (
            child.get("name") or child.get("concept") or child.get("qname") or ""
        )
        if child_qname:
            child_qnames.append(child_qname)

    nodes[qname] = ConceptFamilyNode(
        concept=qname,
        label=_extract_label(subtree, qname),
        level=int(subtree.get("level", subtree.get("depth", depth))),
        parent=parent_qname,
        children=child_qnames,
        weight=_to_float(subtree.get("weight")),
    )

    for child in children_raw:
        _parse_nested_tree(child, qname, nodes, depth + 1)


# ---------------------------------------------------------------------------
# Internal helpers — small utilities
# ---------------------------------------------------------------------------


def _qname_to_label(qname: str) -> str:
    """Convert a camelCase QName to a human-readable label.

    ``us-gaap:ResearchAndDevelopmentExpense`` → ``Research And Development Expense``
    """
    local = qname.split(":")[-1] if ":" in qname else qname
    # Insert space before each uppercase letter that follows a lowercase letter
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", local)
    return spaced


def _stem_token(tok: str) -> str:
    """Crude singular-stemming so common financial plurals compare equal to
    their singular in the label-overlap test:
    ``costs``/``cost``, ``expenses``/``expense``, ``revenues``/``revenue``,
    ``services``/``service``, ``sales``/``sale``.

    Strips only a single trailing ``s`` (when the stem stays >= 3 chars).  Note
    that an ``es`` rule would *break* this: ``revenues`` -> ``revenu`` would no
    longer match ``revenue``, and ``expenses`` -> ``expens`` would not match
    ``expense``.  Single-``s`` stripping keeps the pair aligned."""
    t = tok.lower()
    if len(t) > 3 and t.endswith("s"):
        return t[:-1]
    return t


def _concept_label_tokens(qname: str) -> set:
    """Return the set of stemmed *content* tokens from a concept's QName label.

    Splits the camelCase local name, lowercases, drops stop-words, generic
    connective tokens (:data:`_LABEL_GENERIC_TOKENS`), digits, and tokens
    shorter than 3 chars, then stems.  These are the tokens a synonym must
    overlap (see :func:`_synonym_overlaps_label`) to be retained by the
    Phase-4b precision gate.
    """
    label = _qname_to_label(qname).lower()
    return {
        _stem_token(t)
        for t in re.split(r"\W+", label)
        if len(t) >= 3
        and t not in _STOP_WORDS
        and t not in _LABEL_GENERIC_TOKENS
        and not t.isdigit()
    }


def _synonym_overlaps_label(synonym: str, label_tokens: set) -> bool:
    """True if *synonym* shares at least one stemmed content token with
    *label_tokens* (the output of :func:`_concept_label_tokens`).

    When *label_tokens* is empty (a concept whose label has no anchorable
    content token — rare), the synonym is kept conservatively since the gate
    cannot make a decision."""
    if not label_tokens:
        return True
    syn_tokens = {
        _stem_token(t)
        for t in re.split(r"\W+", synonym.lower())
        if len(t) >= 3 and t not in _STOP_WORDS
    }
    return bool(label_tokens & syn_tokens)


def _extract_label(data: dict[str, Any], fallback: str) -> str:
    """Pull the best human-readable label out of a taxonomy node dict."""
    for key in ("label", "name", "preferredLabel", "description", "verboseLabel"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return fallback


def _extract_children(data: dict[str, Any]) -> list[str]:
    """Extract child QNames from a node dict (handles list or nested dict)."""
    raw = data.get("children") or data.get("childConcepts") or []
    if isinstance(raw, list):
        result = []
        for item in raw:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, dict):
                child_qname = (
                    item.get("name")
                    or item.get("concept")
                    or item.get("qname")
                    or item.get("id")
                )
                if child_qname:
                    result.append(str(child_qname))
        return result
    if isinstance(raw, dict):
        return list(raw.keys())
    return []


def _to_float(val: Any) -> float | None:
    """Safely convert a value to float, returning None on failure."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _infer_parents_from_children(nodes: dict[str, ConceptFamilyNode]) -> None:
    """Back-fill ``parent`` pointers from the ``children`` lists."""
    for qname, node in nodes.items():
        for child_qname in node.children:
            child = nodes.get(child_qname)
            if child is not None and child.parent is None:
                child.parent = qname


def _assign_levels_from_roots(nodes: dict[str, ConceptFamilyNode]) -> None:
    """BFS from roots to assign integer ``level`` values (1-based depth)."""
    roots = [n for n in nodes.values() if n.parent is None]
    queue: deque[tuple[ConceptFamilyNode, int]] = deque(
        (n, 1) for n in roots
    )
    visited: set[str] = set()
    while queue:
        node, depth = queue.popleft()
        if node.concept in visited:
            continue
        visited.add(node.concept)
        node.level = depth
        for child_qname in node.children:
            child = nodes.get(child_qname)
            if child and child.concept not in visited:
                queue.append((child, depth + 1))
