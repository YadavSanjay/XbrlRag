import argparse
import json
import re
from typing import Dict, List, Optional, Tuple, Union

try:
    import fitz  # PyMuPDF
except Exception as e:
    raise SystemExit("PyMuPDF (fitz) is required. Install with: pip install pymupdf") from e


# --- Standard 10-K structure (Items and canonical labels) ---
TENK_STRUCTURE: dict[str, list[tuple[str, str]]] = {
    "Part I": [
        ("1", "Item 1. Business"),
        ("1A", "Item 1A. Risk Factors"),
        ("1B", "Item 1B. Unresolved Staff Comments"),
        ("1C", "Item 1C. Cybersecurity"),
        ("2", "Item 2. Properties"),
        ("3", "Item 3. Legal Proceedings"),
        ("4", "Item 4. Mine Safety Disclosures"),
    ],
    "Part II": [
        ("5", "Item 5. Market for Registrant’s Common Equity, Related Stockholder Matters and Issuer Purchases of Equity Securities"),
        ("6", "Item 6. Reserved"),
        ("7", "Item 7. Management’s Discussion and Analysis of Financial Condition and Results of Operations"),
        ("7A", "Item 7A. Quantitative and Qualitative Disclosures About Market Risk"),
        ("8", "Item 8. Financial Statements and Supplementary Data"),
        ("9", "Item 9. Changes in and Disagreements With Accountants on Accounting and Financial Disclosure"),
        ("9A", "Item 9A. Controls and Procedures"),
        ("9B", "Item 9B. Other Information"),
        ("9C", "Item 9C. Disclosure Regarding Foreign Jurisdictions that Prevent Inspections"),
    ],
    "Part III": [
        ("10", "Item 10. Directors, Executive Officers and Corporate Governance"),
        ("11", "Item 11. Executive Compensation"),
        ("12", "Item 12. Security Ownership of Certain Beneficial Owners and Management and Related Stockholder Matters"),
        ("13", "Item 13. Certain Relationships and Related Transactions, and Director Independence"),
        ("14", "Item 14. Principal Accounting Fees and Services"),
    ],
    "Part IV": [
        ("15", "Item 15. Exhibits, Financial Statement Schedules"),
        ("16", "Item 16. Form 10-K Summary"),
    ],
}


# --- Helpers ---
RE_SPACE = r"[ \t\u00A0]"  # include non-breaking space
RE_DOTS = r"[\.·\u2022\u2026\u00B7\-\–\—]*"  # dotted leaders or dashes/bullets
RE_PAGENUM = r"([ivxlcdmIVXLCDM\d]+)\s*$"  # Roman or Arabic at end of line


def normalize(s: str) -> str:
    return re.sub(rf"{RE_SPACE}+", " ", s.strip()).lower()


def compile_item_patterns() -> dict[str, re.Pattern]:
    """
    Build robust regex for each item line allowing variations WITHOUT inline flags.
    We'll compile with re.IGNORECASE to be Python 3.12-safe.
    """
    patterns: dict[str, re.Pattern] = {}
    for part, items in TENK_STRUCTURE.items():
        for item_code, label in items:
            head = label
            head = head.replace("’", "[’']")
            head = head.replace("–", r"[\-–—]")
            head = head.replace("-", r"[\-–—]")
            head = head.replace("&", r"[&]")
            head = re.sub(r"\.", r"\.", head)
            strict = rf"{head}{RE_SPACE}*{RE_DOTS}{RE_SPACE}*{RE_PAGENUM}"
            minimal = rf"Item{RE_SPACE}+{re.escape(item_code)}\.?{RE_SPACE}*[\-–—:]?[^0-9\n\r]*{RE_DOTS}{RE_SPACE}*{RE_PAGENUM}"
            patterns[label] = re.compile(rf"(?:{strict})|(?:{minimal})", flags=re.IGNORECASE)
    return patterns


ITEM_PATTERNS = compile_item_patterns()


def detect_toc_page(doc: "fitz.Document", max_pages: int = 10) -> int | None:
    """
    Look for a page that likely contains a Table of Contents by scanning
    the first `max_pages` pages for keywords.
    """
    keywords = [
        "table of contents",
        "index",
        "contents",
    ]
    for i in range(min(max_pages, doc.page_count)):
        text = doc.load_page(i).get_text("text").lower()
        if any(k in text for k in keywords):
            return i
    return None


def parse_toc_page_text(text: str) -> list[str]:
    """
    Return non-empty lines, preserving order.
    """
    lines = [ln.rstrip() for ln in text.splitlines()]
    cleaned = [ln for ln in lines if (ln.strip() and (len(ln.strip()) > 2 or "item" in ln.lower()))]
    return cleaned


def extract_page_number_from_line(line: str) -> str | None:
    m = re.search(RE_PAGENUM, line)
    print("m=" + m)
    if m:
        # Take the last non-None captured group (works with alternations)
        groups = m.groups()
        for g in reversed(groups):
            if g is not None:
                return g
    return None


def find_item_page_in_lines(item_label: str, lines: list[str]) -> str | None:
    """
    Try to match the item label in the ToC lines and extract the trailing page number.
    Returns the page number as a string (may be Roman) or None if not found.
    """
    pat = ITEM_PATTERNS[item_label]
    for ln in lines:
        m = pat.search(ln)
        if m:
            # pick last non-None captured group (strict|minimal alternative)
            groups = m.groups()
            for g in reversed(groups):
                if g is not None:
                    return g
    norm_label = normalize(item_label)
    for ln in lines:
        if norm_label in normalize(ln):
            pn = extract_page_number_from_line(ln)
            if pn:
                return pn
    return None

def find_item_page_in_lines_wo_regex(item_label: str, lines: list[str]) -> str | None:
    """
    Try to match the item label in the ToC lines and extract the trailing page number.
    Returns the page number as a string (may be Roman) or None if not found.
    """
    pat = ITEM_PATTERNS[item_label]
    for ln in lines:
        m = pat.search(ln)
        if m:
            # pick last non-None captured group (strict|minimal alternative)
            groups = m.groups()
            for g in reversed(groups):
                if g is not None:
                    return g
    norm_label = normalize(item_label)
    for ln in lines:
        if norm_label in normalize(ln):
            pn = extract_page_number_from_line(ln)
            if pn:
                return pn
    return None

def map_items_to_pages(
    lines: list[str],
) -> dict[str, dict[str, int | str | None]]:
    """
    For each item in the standard 10-K structure, map to the page number found in the ToC lines.
    Page number is returned as int if Arabic, otherwise str for Roman numerals.
    """
    result: dict[str, dict[str, int | str | None]] = {}
    for part, items in TENK_STRUCTURE.items():
        result[part] = {}
        for code, label in items:
            pn = find_item_page_in_lines_wo_regex(label, lines)
            if pn is None:
                result[part][label] = None
            else:
                if pn.isdigit():
                    result[part][label] = int(pn)
                else:
                    result[part][label] = pn  # preserve Roman as string
    return result


if __name__ == "__main__":
    pdf_file = "2024-10K-ENERGY-XOM/2024-10K-ENERGY-XOM_page_2.pdf"
    page_index = 0
    doc = fitz.open(pdf_file)
    page = doc.load_page(page_index)
    text = page.get_text("text")
    print(text)
    lines = parse_toc_page_text(text)
    mapping = map_items_to_pages(lines)
    print(json.dumps({
            "pdf": pdf_file,
            "toc_page_index": page_index,
            "result": mapping
        }, indent=2, ensure_ascii=False))
