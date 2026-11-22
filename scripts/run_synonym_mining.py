"""Re-run HiFi-KPI synonym mining with spaCy NP extraction.

Quality filters active (all tunable via generate_concept_synonyms kwargs):
  top_k=15              max synonyms per concept
  min_count=3           NP must appear >= 3 times
  max_frags_per_concept=50  pre-filter A: cap rare fragments per concept
  max_fragment_chars=80     pre-filter B: long fragments -> regex VP-trim
  idf_max_concepts=100      IDF filter: drop NPs spanning >100 concepts
  NP filters: 2-6 words, >=3-char content words, no dates, no demonstratives,
              no commas, no possessives; fragment must start uppercase.

Run from project root:
    python scripts/run_synonym_mining.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ingest.util.hifi_kpi import HiFiKPIDataset

DEST = Path("data/storage/datasets/hifikpi")

# ── Load from cache ────────────────────────────────────────────────────────
print("Loading dataset from cache ...")
ds = HiFiKPIDataset(cache_dir=DEST)
ds._calc_taxonomy_path = DEST / "taxonomy" / "calculationMasterTaxonomy.jsonl"
ds._pres_taxonomy_path = DEST / "taxonomy" / "presentationMasterTaxonomy.jsonl"
ds.load(splits=["train"])
n_examples = len(ds._dataset["train"])
print(f"Loaded: {n_examples:,} examples\n")

# ── Synonym mining ─────────────────────────────────────────────────────────
print("=== Phase 1-5: NP-based synonym mining ===")
t0 = time.time()
synonyms = ds.generate_concept_synonyms(
    top_k=15,
    splits=["train"],
    min_count=3,
    context_window=120,
    spacy_batch_size=512,
    max_frags_per_concept=50,
    max_fragment_chars=80,
    idf_max_concepts=100,
)
elapsed = time.time() - t0
print(f"\nTotal time: {elapsed:.1f}s  |  concepts with synonyms: {len(synonyms):,}\n")

# ── Sample output ──────────────────────────────────────────────────────────
SAMPLE_CONCEPTS = [
    "us-gaap:ResearchAndDevelopmentExpense",
    "us-gaap:Revenues",
    "us-gaap:NetIncomeLoss",
    "us-gaap:Assets",
    "us-gaap:CashAndCashEquivalentsAtCarryingValue",
    "us-gaap:GrossProfit",
    "us-gaap:OperatingIncomeLoss",
    "us-gaap:EarningsPerShareBasic",
    # Single-filer extension concepts (regression check for the low-frequency fix)
    "nvda:PurchaseObligationSupplyAgreements",
    "nvda:UnrecognizedTaxBenefitNoncurrent",
]
print("--- Sample synonyms (all synonyms shown) ---")
for concept in SAMPLE_CONCEPTS:
    if concept in synonyms:
        cs = synonyms[concept]
        print(f"{concept}  (entity spans={cs.total_occurrences:,})")
        for phrase in cs.synonyms:        # show all (max 15)
            print(f"  - {phrase}")
        print()

# ── Extension-recall summary (regression check) ─────────────────────────────
nvda_concepts = sorted(c for c in synonyms if c.startswith("nvda:"))
print(f"--- nvda: concepts retained: {len(nvda_concepts)} ---")
for c in nvda_concepts[:40]:
    print(f"  {c}")
if len(nvda_concepts) > 40:
    print(f"  ... (+{len(nvda_concepts) - 40} more)")
print()

usgaap = sum(1 for c in synonyms if c.startswith("us-gaap:"))
ext = len(synonyms) - usgaap
print(f"--- totals: {len(synonyms):,} concepts  |  us-gaap={usgaap:,}  extensions/other={ext:,} ---\n")

# ── Save ───────────────────────────────────────────────────────────────────
out = DEST / "concept_synonyms.json"
ds.save_synonyms(synonyms, out)
size_kb = out.stat().st_size / 1024
print(f"Saved: {out}  ({size_kb:.0f} KB)")
