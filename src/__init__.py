import os
from pathlib import Path

from src.logging_config import configure_logging

configure_logging()  # call once at program start

from dotenv import load_dotenv

load_dotenv()  # load environment variables from .env file

# Back-compat for the ARag -> XbrlRag rename: every ``XBRLRAG_*`` setting used to be
# ``ARAG_*``. Mirror a legacy name onto its new one (the new name wins when both are
# set) so an existing .env / deploy env keeps working. Runs here, before anything
# reads the environment. Remove once no environment sets ``ARAG_*`` any more.
for _legacy, _value in list(os.environ.items()):
    if _legacy.startswith("ARAG_"):
        os.environ.setdefault("XBRLRAG_" + _legacy[len("ARAG_") :], _value)

# Derived from this file's location (<repo>/src/__init__.py -> <repo>), never
# hardcoded: the repo lives at a Windows path (D:/...) on the dev box but at /app
# in the container images, and a hardcoded Windows path is *relative* on POSIX, so
# every derived path below silently became /app/D:/... . Override with
# XBRLRAG_PROJECT_ROOT if the tree is ever relocated.
PROJECT_ROOT = os.environ.get(
    "XBRLRAG_PROJECT_ROOT", str(Path(__file__).resolve().parents[1])
)

BASE_DATA_DIR = Path(
    os.environ.get("XBRLRAG_BASE_DATA_DIR", f"{PROJECT_ROOT}/data/pagebreakdown")
).expanduser()
TENK_DATA_STORAGE_DIR = Path(
    os.environ.get("XBRLRAG_10K_STORAGE_DATA_DIR", f"{PROJECT_ROOT}/data/storage/10k")
).expanduser()
CONFIGS_DIR = Path(
    os.environ.get("XBRLRAG_BASE_CONFIGS_DIR", f"{PROJECT_ROOT}/configs")
).expanduser()
BASE_VECTORSTORE_DIR = Path(
    os.environ.get("XBRLRAG_VECTORSTORE_DIR", f"{PROJECT_ROOT}/data/vectordb/chroma")
).expanduser()
BENCHMARKS_DIR = Path(
    os.environ.get("XBRLRAG_BENCHMARKS_DIR", f"{PROJECT_ROOT}/benchmarks")
).expanduser()

CHUNK_SIZE_IN_TOKENS = 256
CHUNK_SIZE_IN_CHARS = 1000

TEXT_COLLECTION_NAME = "sec10k_text"
METADATA_COLLECTION_NAME = "sec10k_metadata"

# XBRL/HTML-canonical collections (Phase 10 — see
# src/embeddings/xbrl_bge_embedding.py and
# src/vectorstores/xbrl_qdrant_vectordb_client.py). Separate from the PDF
# collections above because the payload schema differs (cik/fiscal_year/
# section_path/xbrl_fact_ids vs company/year/section/subsection/table_json).
XBRL_TEXT_COLLECTION_NAME = "xbrl_sec10k_text"
XBRL_METADATA_COLLECTION_NAME = "xbrl_sec10k_metadata"

UNSTRUCTURED_HIRES_MODEL_NAME = "yolox"
