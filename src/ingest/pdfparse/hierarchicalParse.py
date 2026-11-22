#!/usr/bin/env python3

"""
SEC PDF Outline CLI
-------------------
Parse a financial filing PDF (e.g., SEC 10-K) to extract a hierarchical outline
(headers → sections → subsections) while excluding tables, figures, and images.

Dependencies:
  pip install pdfminer.six

Usage:
  python sec_pdf_outline.py FILE.pdf --format json --out outline.json
  python sec_pdf_outline.py FILE.pdf --format markdown --out outline.md
"""

import argparse
import json
import math
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from pdfminer.high_level import extract_pages
from pdfminer.layout import (
    LAParams,
    LTChar,
    LTCurve,
    LTFigure,
    LTImage,
    LTTextContainer,
    LTTextLine,
)

BBox = tuple[float, float, float, float]  # (x0, y0, x1, y1)

# -----------------------------
# Geometry helpers
# -----------------------------
def bbox_intersects(a: BBox, b: BBox, pad: float = 0.0) -> bool:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return not (ax1 < bx0 - pad or bx1 < ax0 - pad or ay1 < by0 - pad or by1 < ay0 - pad)

def bbox_union(a: BBox, b: BBox) -> BBox:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))

def pad_bbox(b: BBox, p: float) -> BBox:
    return (b[0]-p, b[1]-p, b[2]+p, b[3]+p)

def merge_bboxes(boxes: list[BBox], pad: float = 2.0) -> list[BBox]:
    """Coalesce overlapping/adjacent boxes with a two-pass sweep."""
    if not boxes:
        return []
    boxes = [pad_bbox(b, pad) for b in boxes]
    boxes.sort(key=lambda b: (b[1], b[0]))  # y then x
    merged = []
    for b in boxes:
        if not merged:
            merged.append(b)
            continue
        if bbox_intersects(merged[-1], b):
            merged[-1] = bbox_union(merged[-1], b)
        else:
            merged.append(b)
    merged.sort(key=lambda b: (b[0], b[1]))
    out = []
    for b in merged:
        if not out:
            out.append(b)
            continue
        if bbox_intersects(out[-1], b):
            out[-1] = bbox_union(out[-1], b)
        else:
            out.append(b)
    return out

# -----------------------------
# Exclusion region detection
# -----------------------------
def detect_figure_like(layout) -> list[BBox]:
    boxes = []
    for obj in layout:
        if isinstance(obj, (LTFigure, LTImage)):
            boxes.append(obj.bbox)
    return boxes

def detect_table_like(layout, line_len_min: float = 20.0, density_thresh: int = 12) -> list[BBox]:
    """
    Heuristic table detector: cluster pages areas with many parallel ruled lines (LTCurve).
    density_thresh: how many line segments must fall in a cluster to consider it a table.
    """
    lines = []
    for obj in layout:
        if isinstance(obj, LTCurve):
            x0, y0, x1, y1 = obj.bbox
            w, h = (x1 - x0), (y1 - y0)
            # Approximate straight lines by thin boxes
            if (w >= line_len_min and h <= 1.5) or (h >= line_len_min and w <= 1.5):
                lines.append(obj.bbox)

    if not lines:
        return []

    clusters = merge_bboxes(lines, pad=3.0)
    table_regions = []
    for c in clusters:
        count = sum(1 for l in lines if bbox_intersects(l, c))
        if count >= density_thresh:
            table_regions.append(c)
    return table_regions

# -----------------------------
# Text extraction with font metrics
# -----------------------------
@dataclass
class TextBlock:
    text: str
    bbox: BBox
    page_no: int
    median_font: float
    font_names: list[str]
    n_chars: int

def text_container_stats(tc: LTTextContainer) -> tuple[float, list[str], int]:
    """
    Extract font statistics from a text container.
    
    Returns:
        Tuple of (median_font_size, list_of_font_names, character_count)
    """
    sizes = []
    names = set()
    n_chars = 0
    for line in tc:
        if isinstance(line, LTTextLine):
            for ch in line:
                if isinstance(ch, LTChar):
                    sizes.append(ch.size)
                    names.add(ch.fontname or "")
                    n_chars += 1
    if not sizes:
        return (0.0, list(names), n_chars)
    sizes.sort()
    mid = len(sizes) // 2
    median = sizes[mid] if len(sizes) % 2 == 1 else 0.5 * (sizes[mid-1] + sizes[mid])
    return (median, list(names), n_chars)

def collect_text_blocks(pdf_path: str,
                        page_start: int | None,
                        page_end: int | None,
                        min_table_lines: int,
                        debug: bool) -> list[TextBlock]:
    """
    Extract text blocks from PDF, excluding tables and figures.
    
    Returns:
        List of TextBlock objects sorted by page number and position.
    """
    laparams = LAParams(line_margin=0.2, word_margin=0.1, char_margin=1.0)
    blocks: list[TextBlock] = []

    for page_idx, layout in enumerate(extract_pages(pdf_path, laparams=laparams), start=1):
        if page_start and page_idx < page_start:
            continue
        if page_end and page_idx > page_end:
            break

        fig_boxes = detect_figure_like(layout)
        table_boxes = detect_table_like(layout, density_thresh=min_table_lines)
        exclusions = merge_bboxes(fig_boxes + table_boxes, pad=4.0)

        if debug:
            sys.stderr.write(
                f"[page {page_idx}] figures={len(fig_boxes)} tables={len(table_boxes)} excl={len(exclusions)}\n"
            )

        for obj in layout:
            if isinstance(obj, LTTextContainer):
                if any(bbox_intersects(obj.bbox, ex) for ex in exclusions):
                    continue
                text = obj.get_text().strip()
                if not text:
                    continue
                med, names, n_chars = text_container_stats(obj)
                blocks.append(TextBlock(
                    text=text, bbox=obj.bbox, page_no=page_idx,
                    median_font=med, font_names=names, n_chars=n_chars
                ))

    blocks.sort(key=lambda b: (b.page_no, -b.bbox[3], b.bbox[0]))
    return blocks

# -----------------------------
# Heading detection
# -----------------------------
ITEM_RE = re.compile(r'^\s*Item\s+(\d+[A-Z]?|1A)\.?(\s+[-–—]\s*)?.*$', re.IGNORECASE)
DECIMAL_RE = re.compile(r'^\s*(\d+(\.\d+){0,4})\s+.+')
OUTLINE_RE = re.compile(r'^\s*([A-Z]|\([a-zA-Z]\)|\([ivxlcdm]+\))\s+.+')

def is_all_caps_short(s: str, max_len=80) -> bool:
    if len(s) > max_len: return False
    letters = [c for c in s if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters)

def looks_bold(font_names: list[str]) -> bool:
    joined = " ".join(font_names).lower()
    return any(k in joined for k in ["bold", "bd", "black", "heavy", "medium"])

def compute_font_size_ranks(blocks: list[TextBlock]) -> dict[float, int]:
    sizes = sorted({round(b.median_font, 1) for b in blocks if b.median_font > 0.0}, reverse=True)
    return {sz: (len(sizes) - i) for i, sz in enumerate(sizes)}  # larger → larger rank

def heading_score(tb: TextBlock, size_ranks: dict[float, int]) -> float:
    score = 0.0
    rank = size_ranks.get(round(tb.median_font, 1), 0)
    score += rank * 2.0
    if looks_bold(tb.font_names): score += 1.0
    if is_all_caps_short(tb.text): score += 1.0
    if ITEM_RE.match(tb.text): score += 3.0
    if DECIMAL_RE.match(tb.text): score += 1.0
    if OUTLINE_RE.match(tb.text): score += 0.5
    if len(tb.text) > 120 and tb.n_chars > 200:  # long paragraph → negative for heading
        score -= 1.5
    return score

def assign_heading_level(score: float, tb: TextBlock, size_ranks: dict[float, int]) -> int:
    if ITEM_RE.match(tb.text):
        return 1  # SEC Item headings → top-level
    rank = size_ranks.get(round(tb.median_font, 1), 0)
    if score >= 5.0 or rank >= 5: return 1
    if score >= 3.5 or rank >= 4: return 2
    if score >= 2.5 or rank >= 3: return 3
    if score >= 2.0 or rank >= 2: return 4
    return 0

# -----------------------------
# Hierarchy assembly
# -----------------------------
@dataclass
class SectionNode:
    title: str
    level: int
    page_start: int
    page_end: int
    text: str = ""
    children: list["SectionNode"] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "level": self.level,
            "pages": [self.page_start, self.page_end],
            "text": self.text.strip(),
            "children": [c.to_dict() for c in self.children]
        }

def build_structure(blocks: list[TextBlock],
                    toc_skip: bool,
                    debug: bool) -> list[SectionNode]:
    size_ranks = compute_font_size_ranks(blocks)
    root: SectionNode = SectionNode(title="__ROOT__", level=0, page_start=1, page_end=1)
    stack = [root]

    def should_skip_heading(title: str) -> bool:
        if not toc_skip:
            return False
        t = title.strip().lower()
        return t == "table of contents" or t.startswith("table of contents")

    for tb in blocks:
        sc = heading_score(tb, size_ranks)
        lvl = assign_heading_level(sc, tb, size_ranks)
        root.page_end = max(root.page_end, tb.page_no)

        if lvl > 0 and not should_skip_heading(tb.text):
            while stack and stack[-1].level >= lvl:
                stack.pop()
            node = SectionNode(
                title=tb.text.splitlines()[0].strip(),
                level=lvl, page_start=tb.page_no, page_end=tb.page_no
            )
            stack[-1].children.append(node)
            stack.append(node)
            if debug:
                sys.stderr.write(f"[H{lvl}] p{tb.page_no}: {node.title[:80]}\n")
        else:
            cur = stack[-1]
            cur.text += (("\n" if cur.text else "") + tb.text)
            cur.page_end = max(cur.page_end, tb.page_no)

    return root.children

# -----------------------------
# Markdown rendering
# -----------------------------
def tree_to_markdown(nodes: list[SectionNode], max_level: int = 6) -> str:
    out = []

    def walk(n: SectionNode):
        if n.level > 0:
            level = min(n.level, max_level)
            out.append("#" * level + " " + n.title)
            out.append(f"*Pages: {n.page_start}–{n.page_end}*")
            if n.text.strip():
                out.append("")
                out.append(n.text.strip())
                out.append("")
        for c in n.children:
            walk(c)

    for n in nodes:
        walk(n)
    return "\n".join(out).strip() + "\n"

# -----------------------------
# Public API
# -----------------------------
def parse_pdf_structure(pdf_path: str,
                        page_range: str | None,
                        min_table_lines: int,
                        toc_skip: bool,
                        debug: bool) -> list[dict[str, Any]]:
    start = end = None
    if page_range:
        try:
            s, e = page_range.split(":")
            start = int(s) if s else None
            end = int(e) if e else None
        except Exception:
            raise ValueError("--page-range must be like 'start:end' (e.g., '1:40', ':20', '10:')")

    blocks = collect_text_blocks(pdf_path, start, end, min_table_lines=min_table_lines, debug=debug)
    nodes = build_structure(blocks, toc_skip=toc_skip, debug=debug)
    return [n.to_dict() for n in nodes]

# -----------------------------
# CLI
# -----------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Extract hierarchical outline from SEC-style PDF (excluding tables/figures/images)."
    )
    ap.add_argument("pdf", help="Path to PDF file")
    ap.add_argument("--format", choices=["json", "markdown"], default="json", help="Output format")
    ap.add_argument("--out", help="Write output to file (otherwise prints to stdout)")
    ap.add_argument("--page-range", help="Limit to pages 'start:end' (e.g., 1:40, :20, 10:)")
    ap.add_argument("--min-table-lines", type=int, default=12,
                    help="Heuristic sensitivity for table detection (higher = fewer tables detected)")
    ap.add_argument("--toc-skip", action="store_true",
                    help="Skip/demote 'Table of Contents' as a top-level section")
    ap.add_argument("--max-level", type=int, default=6,
                    help="Max heading level for markdown")
    ap.add_argument("--debug", action="store_true", help="Emit diagnostics to stderr")
    args = ap.parse_args()

    if not os.path.exists(args.pdf):
        sys.stderr.write(f"File not found: {args.pdf}\n")
        sys.exit(1)

    try:
        tree = parse_pdf_structure(
            args.pdf,
            page_range=args.page_range,
            min_table_lines=args.min_table_lines,
            toc_skip=args.toc_skip,
            debug=args.debug
        )
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(2)

    if args.format == "json":
        out_text = json.dumps(tree, indent=2, ensure_ascii=False)
    else:
        # reconstruct nodes to render markdown
        def mk(n: dict[str, Any]) -> SectionNode:
            node = SectionNode(
                title=n["title"], level=n["level"],
                page_start=n["pages"][0], page_end=n["pages"][1],
                text=n.get("text", "")
            )
            node.children = [mk(c) for c in n.get("children", [])]
            return node
        nodes = [mk(n) for n in tree]
        out_text = tree_to_markdown(nodes, max_level=args.max_level)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out_text)
        if args.debug:
            sys.stderr.write(f"Wrote {args.out}\n")
    else:
        print(out_text)

if __name__ == "__main__":
    main()
