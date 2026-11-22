# pip install pdfminer.six pillow
# Optional (preferred vector renderer): pip install pymupdf
# Optional (fallback renderer): pip install pdf2image

import os
from io import BytesIO
from statistics import mean, pstdev
from typing import Any, Dict, List, Optional, Tuple

from pdfminer.high_level import extract_pages
from pdfminer.layout import (
    LAParams,
    LTChar,
    LTCurve,
    LTFigure,
    LTImage,
    LTItem,
    LTLine,
    LTPage,
    LTRect,
    LTTextLine,
)

# Optional renderers
try:
    import fitz  # PyMuPDF
    _HAVE_FITZ = True
except Exception:
    _HAVE_FITZ = False

try:
    from pdf2image import convert_from_path
    _HAVE_PDF2IMAGE = True
except Exception:
    _HAVE_PDF2IMAGE = False

from PIL import Image

# ----------------------------- Utilities -----------------------------

def _iter_descendants(obj: LTItem):
    stack = [obj]
    while stack:
        node = stack.pop()
        yield node
        if hasattr(node, "_objs"):
            stack.extend(reversed(node._objs))

def _bbox_area(bbox) -> float:
    x0, y0, x1, y1 = bbox
    return max(0.0, (x1 - x0)) * max(0.0, (y1 - y0))

def _expand_bbox(bbox, margin, page_w, page_h):
    x0, y0, x1, y1 = bbox
    x0 = max(0, x0 - margin)
    y0 = max(0, y0 - margin)
    x1 = min(page_w, x1 + margin)
    y1 = min(page_h, y1 + margin)
    return (x0, y0, x1, y1)

def _iou(a, b):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    inter_x0 = max(ax0, bx0); inter_y0 = max(ay0, by0)
    inter_x1 = min(ax1, bx1); inter_y1 = min(ay1, by1)
    iw = max(0, inter_x1 - inter_x0); ih = max(0, inter_y1 - inter_y0)
    inter = iw * ih
    if inter == 0: return 0.0
    return inter / float(_bbox_area(a) + _bbox_area(b) - inter + 1e-6)

def _extract_text_len(container: LTItem) -> int:
    chars = 0
    for n in _iter_descendants(container):
        if isinstance(n, LTChar):
            chars += 1
        elif isinstance(n, LTTextLine):
            s = n.get_text()
            chars += len(s) if s else 0
    return chars

def _count_vectors(container: LTItem) -> int:
    count = 0
    for n in _iter_descendants(container):
        if isinstance(n, (LTLine, LTRect, LTCurve)):
            count += 1
    return count

def _ltimage_to_pil(img: LTImage) -> Image.Image | None:
    try:
        if not hasattr(img, "stream") or img.stream is None:
            return None
        raw = img.stream.get_data()
        pil = Image.open(BytesIO(raw))
        pil.load()
        return pil
    except Exception:
        return None

def _looks_like_diagram(pil: Image.Image, max_palette_colors: int = 64) -> bool:
    try:
        q = pil.convert("RGBA").quantize(colors=max_palette_colors, method=Image.MEDIANCUT)
        colors = q.getcolors(maxcolors=10_000_000)
        if colors is None:
            return False
        return len(colors) <= int(max_palette_colors * 0.75)
    except Exception:
        return False

def _transform_bbox_for_fitz(bbox, page_w: float, page_h: float):
    x0, y0, x1, y1 = bbox
    top = page_h - y1
    bottom = page_h - y0
    return (x0, top, x1, bottom)

def _crop_with_fitz(pdf_path: str, page_index: int, bbox, out_path: str, zoom: float = 2.0):
    with fitz.open(pdf_path) as doc:
        page = doc[page_index]
        clip = fitz.Rect(_transform_bbox_for_fitz(bbox, page.rect.width, page.rect.height))
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
        pix.save(out_path)

def _crop_with_pdf2image(pdf_path: str, page_index: int, bbox, out_path: str, dpi: int, page_wh: tuple[float, float]):
    pages = convert_from_path(pdf_path, dpi=dpi, first_page=page_index + 1, last_page=page_index + 1)
    if not pages:
        return
    img = pages[0]
    pw, ph = page_wh
    x0, y0, x1, y1 = bbox
    sx = img.width / float(pw)
    sy = img.height / float(ph)
    left = int(round(x0 * sx))
    right = int(round(x1 * sx))
    top = int(round((ph - y1) * sy))
    bottom = int(round((ph - y0) * sy))
    img.crop((left, top, right, bottom)).save(out_path)


# ----------------------- Figure/Image Heuristics ----------------------

def _is_plausible_chart_figure(fig: LTFigure, page_area: float) -> bool:
    area = _bbox_area(fig.bbox)
    if area < max(20_000, page_area * 0.02):
        return False
    vectors = _count_vectors(fig)
    text_len = _extract_text_len(fig)
    if vectors >= 12 and text_len <= 600:
        return True
    if vectors >= 6 and text_len <= 200 and area >= page_area * 0.05:
        return True
    return False

def _is_plausible_chart_image(img: LTImage, page_area: float) -> bool:
    area = _bbox_area(img.bbox)
    if area < max(20_000, page_area * 0.02):
        return False
    pil = _ltimage_to_pil(img)
    if pil is None:
        return False
    w, h = pil.size
    if min(w, h) < 150:
        return False
    return _looks_like_diagram(pil, max_palette_colors=64)


# ------------------- Bar/Histogram Detection (NEW) -------------------

def _collect_rects(container: LTItem, page_area: float):
    rects = []
    for n in _iter_descendants(container):
        if isinstance(n, LTRect):
            x0, y0, x1, y1 = n.bbox
            w = max(0.0, x1 - x0)
            h = max(0.0, y1 - y0)
            area = w * h
            # Filter out absurdly large/background rectangles and tiny ones
            if area >= 100 and area <= page_area * 0.5:
                rects.append({
                    "bbox": n.bbox,
                    "w": w, "h": h,
                    "cx": (x0 + x1) / 2.0, "cy": (y0 + y1) / 2.0,
                    "left": x0, "right": x1, "top": y1, "bottom": y0
                })
    return rects

def _cv(values: list[float]) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return 0.0
    mu = mean(values)
    if mu == 0:
        return 0.0
    return pstdev(values) / mu

def _cluster_by(values: list[tuple[float, int]], tol: float) -> list[list[int]]:
    """
    Generic 1D clustering by proximity (single-linkage).
    values: list of (key, idx). Returns clusters of indices.
    """
    if not values:
        return []
    values = sorted(values, key=lambda x: x[0])
    clusters = [[values[0][1]]]
    for (v, i) in values[1:]:
        if abs(v - values[len(clusters[-1]) - 1][0]) <= tol:
            clusters[-1].append(i)
        else:
            clusters.append([i])
    return clusters

def _find_vertical_bar_clusters(rects: list[dict[str, float]]) -> list[list[dict[str, float]]]:
    # Vertical bars: tall & narrow, similar widths, similar bottoms (same baseline)
    candidates = [r for r in rects if r["h"] > r["w"] * 1.3 and r["h"] >= 20 and 2 <= r["w"] <= 150]
    if not candidates:
        return []
    # Baseline cluster (by bottom)
    bottom_vals = [(r["bottom"], idx) for idx, r in enumerate(candidates)]
    baseline_clusters = _cluster_by(bottom_vals, tol=6.0)
    clusters: list[list[dict[str, float]]] = []
    for cl in baseline_clusters:
        group = [candidates[i] for i in cl]
        if len(group) < 3:
            continue
        # Sort by x (left) to analyze gaps/widths
        group.sort(key=lambda r: r["left"])
        widths = [r["w"] for r in group]
        gaps = [max(0.0, group[i+1]["left"] - group[i]["right"]) for i in range(len(group)-1)]
        width_cv = _cv(widths)
        avg_w = mean(widths)
        avg_gap = mean(gaps) if gaps else 0.0
        gap_ratio = (avg_gap / avg_w) if avg_w > 0 else 1.0
        # Heuristics:
        # - Need several bars with somewhat consistent widths
        # - Bars taller than wide (already enforced)
        # - For histograms: very small gaps; for bar charts: moderate gaps ok
        if width_cv <= 0.45 and len(group) >= 3:
            clusters.append(group)
    return clusters

def _find_horizontal_bar_clusters(rects: list[dict[str, float]]) -> list[list[dict[str, float]]]:
    # Horizontal bars: wide & short, similar heights, similar left (same vertical axis)
    candidates = [r for r in rects if r["w"] > r["h"] * 1.3 and r["w"] >= 30 and 2 <= r["h"] <= 150]
    if not candidates:
        return []
    # Left-edge clusters
    left_vals = [(r["left"], idx) for idx, r in enumerate(candidates)]
    left_clusters = _cluster_by(left_vals, tol=6.0)
    clusters: list[list[dict[str, float]]] = []
    for cl in left_clusters:
        group = [candidates[i] for i in cl]
        if len(group) < 3:
            continue
        # Sort by y (top) to analyze gaps/heights
        group.sort(key=lambda r: r["top"], reverse=True)
        heights = [r["h"] for r in group]
        gaps = [max(0.0, group[i]["bottom"] - group[i+1]["top"]) for i in range(len(group)-1)]
        height_cv = _cv(heights)
        if height_cv <= 0.45:
            clusters.append(group)
    return clusters

def _cluster_bbox(group: list[dict[str, float]]) -> tuple[float, float, float, float]:
    x0 = min(r["left"] for r in group)
    y0 = min(r["bottom"] for r in group)
    x1 = max(r["right"] for r in group)
    y1 = max(r["top"] for r in group)
    return (x0, y0, x1, y1)

def _is_histogram_like_vertical(group: list[dict[str, float]]) -> bool:
    # Hist: contiguous/equal-width bars, small gaps
    group = sorted(group, key=lambda r: r["left"])
    widths = [r["w"] for r in group]
    gaps = [max(0.0, group[i+1]["left"] - group[i]["right"]) for i in range(len(group)-1)]
    if not widths:
        return False
    width_cv = _cv(widths)
    avg_w = mean(widths)
    avg_gap = mean(gaps) if gaps else 0.0
    gap_ratio = (avg_gap / avg_w) if avg_w > 0 else 1.0
    return width_cv <= 0.25 and gap_ratio <= 0.2

def _is_histogram_like_horizontal(group: list[dict[str, float]]) -> bool:
    group = sorted(group, key=lambda r: r["top"], reverse=True)
    heights = [r["h"] for r in group]
    gaps = [max(0.0, group[i]["bottom"] - group[i+1]["top"]) for i in range(len(group)-1)]
    if not heights:
        return False
    height_cv = _cv(heights)
    avg_h = mean(heights)
    avg_gap = mean(gaps) if gaps else 0.0
    gap_ratio = (avg_gap / avg_h) if avg_h > 0 else 1.0
    return height_cv <= 0.25 and gap_ratio <= 0.2


# ----------------------------- Main API ------------------------------

def extract_charts_with_pdfminer(
    pdf_path: str,
    page_number: int,      # 0-based
    out_zoom: float = 2.0, # for PyMuPDF rendering
    margin: float = 8.0    # expand detected regions slightly to include axes/labels
) -> list[str]:
    """
    Locate chart/diagram regions in a PDF page using pdfminer.six (LTFigure/LTImage)
    and NEW bar/histogram pattern detection (LTRect clusters). Saves each region
    as an image next to the PDF and returns a list of file paths.
    """
    if not os.path.isfile(pdf_path):
        raise FileNotFoundError(pdf_path)

    base_dir = os.path.dirname(os.path.abspath(pdf_path))
    stem = os.path.splitext(os.path.basename(pdf_path))[0]

    saved_paths: list[str] = []

    laparams = LAParams(line_margin=0.2, char_margin=2.0, word_margin=0.1, all_texts=True)
    pages = list(extract_pages(pdf_path, page_numbers=[page_number], laparams=laparams))
    if not pages:
        raise IndexError(f"No such page index {page_number}")

    layout: LTPage = pages[0]
    pw, ph = layout.width, layout.height
    page_area = pw * ph

    candidate_regions: list[dict[str, Any]] = []

    # 1) Existing: figures & images likely to be charts/diagrams
    for obj in layout:
        for node in _iter_descendants(obj):
            if isinstance(node, LTFigure) and _is_plausible_chart_figure(node, page_area):
                candidate_regions.append({"type": "figure", "bbox": node.bbox})
            elif isinstance(node, LTImage) and _is_plausible_chart_image(node, page_area):
                candidate_regions.append({"type": "image", "bbox": node.bbox, "obj": node})

    # 2) NEW: detect bar charts / histograms from rectangle clusters
    rects = _collect_rects(layout, page_area)

    v_clusters = _find_vertical_bar_clusters(rects)
    for group in v_clusters:
        bbox = _cluster_bbox(group)
        subtype = "histogram" if _is_histogram_like_vertical(group) else "bar"
        candidate_regions.append({"type": "barcluster", "orientation": "vertical", "subtype": subtype, "bbox": bbox})

    h_clusters = _find_horizontal_bar_clusters(rects)
    for group in h_clusters:
        bbox = _cluster_bbox(group)
        subtype = "histogram" if _is_histogram_like_horizontal(group) else "bar"
        candidate_regions.append({"type": "barcluster", "orientation": "horizontal", "subtype": subtype, "bbox": bbox})

    # De-duplicate overlapping regions, prefer larger boxes
    filtered: list[dict[str, Any]] = []
    for cand in sorted(candidate_regions, key=lambda c: _bbox_area(c["bbox"]), reverse=True):
        keep = True
        for existing in filtered:
            if _iou(cand["bbox"], existing["bbox"]) > 0.6:
                keep = False
                break
        if keep:
            filtered.append(cand)

    # Save each region (expand a touch to capture axes/ticks/labels)
    idx = 0
    for reg in filtered:
        idx += 1
        kind = reg["type"]
        bbox = _expand_bbox(reg["bbox"], margin, pw, ph)
        out_path = os.path.join(base_dir, f"{stem}_p{page_number+1}_chart{idx}.png")

        if kind == "image" and "obj" in reg:
            pil = _ltimage_to_pil(reg["obj"])
            if pil is not None:
                pil.save(out_path)
                saved_paths.append(out_path)
                continue

        # Vector/figure/barcluster: rasterize via PyMuPDF or pdf2image
        if _HAVE_FITZ:
            try:
                _crop_with_fitz(pdf_path, page_number, bbox, out_path, zoom=out_zoom)
                saved_paths.append(out_path)
                continue
            except Exception:
                pass
        if _HAVE_PDF2IMAGE:
            try:
                _crop_with_pdf2image(pdf_path, page_number, bbox, out_path, dpi=300, page_wh=(pw, ph))
                saved_paths.append(out_path)
                continue
            except Exception:
                pass
        # If we get here and it wasn't an LTImage we could decode, we skip.

    return saved_paths


if __name__ == "__main__":
    # Example usage:
    pdf_file = "pdfparse\\10K_JPMC_2024.pdf"   # your PDF
    page_idx = 146              # 0-based (page 3)
    results = extract_charts_with_pdfminer(pdf_file, page_idx)
    if results:
        print("Saved chart images:")
        for p in results:
            print(" -", p)
    else:
        print("No chart-like/diagram/bar/hist regions found on that page.")
