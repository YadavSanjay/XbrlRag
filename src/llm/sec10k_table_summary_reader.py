"""Utilities for loading SEC 10-K table JSON produced by LLM prompts.

The LLM output is expected to be a single JSON object with the keys
`table_summary` and `table_json` as enforced by the prompt template
`sec10k_table_summary_and_data.txt`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from src import BASE_DATA_DIR
from src.logging_config import get_logger

logger = get_logger(__name__)


class InvalidTableJSONError(ValueError):
    """Raised when the table JSON is missing required keys or is malformed."""


REQUIRED_KEYS: set[str] = {"table_summary", "table_json"}


def _validate_top_level_keys(file_path: str | Path, payload: dict[str, Any]) -> None:
    missing = REQUIRED_KEYS - payload.keys()
    if missing:
        raise InvalidTableJSONError(f"Missing required keys: {', '.join(sorted(missing))} in file {file_path}")

def _strip_code_fence(raw_json: str) -> str:
    """Remove surrounding markdown fences like ```json ... ``` if present."""
    raw_json = raw_json.strip()
    if raw_json.startswith("```"):
        raw_json = re.sub(r"^```(?:json)?\s*", "", raw_json, count=1, flags=re.IGNORECASE)
        raw_json = re.sub(r"\s*```$", "", raw_json, count=1)
    return raw_json.strip()


def _extract_braced_json(raw_json: str) -> str:
    """Return substring between the first '{' and last '}'."""
    start = raw_json.find("{")
    end = raw_json.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise InvalidTableJSONError("Unable to locate JSON object in response")
    return raw_json[start : end + 1]


def load_table_json_from_string(file_path: str | Path, raw_json: str) -> dict[str, Any]:
    """Parse and validate LLM JSON output from a string."""
    cleaned = _strip_code_fence(raw_json)

    # Try direct parse; if it fails, attempt to extract the first JSON object
    # in case the model returned leading/trailing text.
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        try:
            payload = json.loads(_extract_braced_json(cleaned))
        except json.JSONDecodeError as exc:
            #raise InvalidTableJSONError(f"Invalid JSON in file {file_path}: {exc}. ") from exc
            payload = {}
            logger.error(f"Failed to parse JSON from file {file_path}: {exc}")

    if not isinstance(payload, dict):
        raise InvalidTableJSONError(f"Top-level JSON must be an object in file {file_path}")

    if payload:
        _validate_top_level_keys(file_path, payload)
    return payload


def load_table_json(file_path: str | Path) -> dict[str, Any]:
    """Read, parse, and validate LLM JSON output from a file."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"No such file: {path}")

    raw = path.read_text(encoding="utf-8").strip()
    return load_table_json_from_string(file_path, raw)


if __name__ == "__main__":
    # Example usage
    year = "2023"
    page_num = 54
    table_num = 1

    foldername = f"{year}-10K-COMMUNICATIONSERVICES-GOOGL"
    table_json_file = BASE_DATA_DIR / year / foldername / "tables" / f"page_{page_num}" / f"table_{table_num}_summary.txt"

    try:
        table_data = load_table_json(table_json_file)
        print("Successfully loaded table JSON:")
        print(json.dumps(table_data, indent=2))
    except InvalidTableJSONError as e:
        print(f"Error loading table JSON: {e}")
