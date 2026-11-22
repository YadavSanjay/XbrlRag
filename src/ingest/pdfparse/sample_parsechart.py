# pip install pdfminer.six pillow
# Optional (preferred for vector cropping): pip install pymupdf
# Optional (fallback rasterizer): pip install pdf2image

import os
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

from pdfminer.high_level import extract_pages
from pdfminer.layout import (
    LAParams,
    LTAnno,
    LTChar,
    LTCurve,
    LTFigure,
    LTImage,
    LTItem,
    LTLine,
    LTPage,
    LTRect,
    LTTextBox,
    LTTextContainer,
    LTTextLine,
)

# Optional imports for rendering chart bounding boxes to images
try:
    import fitz  # PyMuPDF (preferred vector renderer)
    _HAVE_FITZ = True
except Exception:
    _HAVE_FITZ = False

try:
    from pdf2image import convert_from_path
    _HAVE_PDF2IMAGE = True
except Exception:
    _HAVE_PDF2IMAGE = False

from PIL import Image


def _iter_descendants(obj: LTItem):
    """Depth-first iteration over all layout descendants (including obj)."""
    stack = [obj]
    while stack:
        node = stack.pop()
        yield node
        if hasattr(node, "_objs"):
            stack.extend(reversed(node._objs))  # preserve page order roughly


def _bbox_area(bbox) -> float:
    x0, y0, x1, y1 = bbox
    return max(0.0, (x1 - x0)) * max(0.0, (y1 - y0))


def _extract_text_len(container: LTItem) -> int:
    """Count characters inside a container."""
    chars = 0
    for n in _iter_descendants(container):
        if isinstance(n, LTChar):
            chars += 1
        elif isinstance(n, LTTextLine):
            # Count characters in text line string
            s = n.get_text()
            chars += len(s) if s else 0
    return chars


def _count_vectors(container: LTItem) -> int:
    """Count vector primitives (lines, rects, curves)."""
    count = 0
    for n in _iter_descendants(container):
        if isinstance(n, (LTLine, LTRect, LTCurve)):
            count += 1
    return count


def _ltimage_to_pil(img: LTImage) -> Image.Image | None:
    """Decode an LTImage into a PIL image if possible."""
    try:
        if not hasattr(img, "stream") or img.stream is None:
            return None
        raw = img.stream.get_data()  # pdfminer decodes filters for you
        bio = BytesIO(raw)
        pil = Image.open(bio)
        pil.load()  # ensure it's fully read
        return pil
    except Exception:
        return None


def _looks_like_diagram(pil: Image.Image, max_palette_colors: int = 64) -> bool:
    """
    Heuristic: diagrams/charts tend to have fewer colors than photos.
    We quantize and see how many colors remain.
    """
    try:
        # Quantize down to a palette; count resulting distinct colors.
        q = pil.convert("RGBA").quantize(colors=max_palette_colors, method=Image.MEDIANCUT)
        colors = q.getcolors(maxcolors=10_000_000)
        if colors is None:
            # Extremely colorful / large; assume not a simple diagram
            return False
        # If the number of colors used after quantization is far below the cap,
        # it's likely a diagram. Tune threshold as needed.
        used_colors = len(colors)
        return used_colors <= int(max_palette_colors * 0.75)
    except Exception:
        return False


def _transform_bbox_for_fitz(bbox, page_width: float, page_height: float):
    """
    pdfminer uses PDF coords with origin at bottom-left.
    PyMuPDF expects top-left origin for clip rects.
    Convert (x0, y0, x1, y1) accordingly.
    """
    x0, y0, x1, y1 = bbox
    # Flip Y:
    top = page_height - y1
    bottom = page_height - y0
    return (x0, top, x1, bottom)


def _crop_with_fitz(pdf_path: str, page_index: int, bbox, out_path: str, zoom: float = 2.0):
    """
    Rasterize bbox region using PyMuPDF.
    """
    with fitz.open(pdf_path) as doc:
        page = doc[page_index]
        # Convert bbox from pdfminer coords
        x0, y0, x1, y1 = bbox
        # Use page.rect.height for conversion:
        page_h = page.rect.height
        clip = fitz.Rect(_transform_bbox_for_fitz(bbox, page.rect.width, page.rect.height))
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
        pix.save(out_path)


def _crop_with_pdf2image(pdf_path: str, page_index: int, bbox, out_path: str, dpi: int = 300, page_wh: tuple[float, float] = None):
    """
    Rasterize bbox region using pdf2image (Poppler).
    Need to map PDF points to pixels.
    """
    pages = convert_from_path(pdf_path, dpi=dpi, first_page=page_index + 1, last_page=page_index + 1)
    if not pages:
        return
    img = pages[0]  # PIL.Image
    # page_wh in PDF points; get from pdfminer page
    if page_wh is None:
        raise ValueError("page_wh (width,height in PDF points) is required for pdf2image cropping.")
    pw, ph = page_wh
    x0, y0, x1, y1 = bbox

    # Map points -> pixels
    sx = img.width / float(pw)
    sy = img.height / float(ph)

    # Convert bottom-left origin (PDF) to top-left origin (image)
    left = int(round(x0 * sx))
    right = int(round(x1 * sx))
    top = int(round((ph - y1) * sy))
    bottom = int(round((ph - y0) * sy))

    crop = img.crop((left, top, right, bottom))
    crop.save(out_path)


def _is_plausible_chart_figure(fig: LTFigure, page_area: float) -> bool:
    """
    Heuristic for LTFigure to look like a chart/diagram:
    - Sufficient number of vector elements
    - Not dominated by text
    - Reasonable area (not micro-icon, not full-page unless it's a chart page)
    """
    area = _bbox_area(fig.bbox)
    if area < max(20_000, page_area * 0.02):  # too tiny
        return False

    vectors = _count_vectors(fig)
    text_len = _extract_text_len(fig)

    # Tunable thresholds
    if vectors >= 12 and text_len <= 600:
        return True

    # If contains some vectors and very little text, still plausible
    if vectors >= 6 and text_len <= 200 and area >= page_area * 0.05:
        return True

    return False


def _is_plausible_chart_image(img: LTImage, page_area: float) -> bool:
    """
    Heuristic for LTImage to look like a chart/diagram:
    - Big enough on page
    - Limited color variety after quantization
    """
    area = _bbox_area(img.bbox)
    if area < max(20_000, page_area * 0.02):
        return False

    pil = _ltimage_to_pil(img)
    if pil is None:
        return False

    # Basic size sanity
    w, h = pil.size
    if min(w, h) < 150:
        return False

    # Color-based heuristic:
    return _looks_like_diagram(pil, max_palette_colors=64)


def extract_charts_with_pdfminer(
    pdf_path: str,
    page_number: int,  # 0-based
    out_zoom: float = 2.0,
) -> list[str]:
    """
    Uses pdfminer.six to locate chart/diagram regions via LTFigure / LTImage heuristics,
    then saves each detected chart as an image next to the PDF.

    Returns:
        List of saved image paths (may be empty if nothing plausible found).
    """
    if not os.path.isfile(pdf_path):
        raise FileNotFoundError(pdf_path)

    base_dir = os.path.dirname(os.path.abspath(pdf_path))
    stem = os.path.splitext(os.path.basename(pdf_path))[0]

    saved_paths: list[str] = []

    laparams = LAParams(line_margin=0.2, char_margin=2.0, word_margin=0.1, all_texts=True)
    # Extract only the requested page
    pages = list(extract_pages(pdf_path, page_numbers=[page_number], laparams=laparams))
    if not pages:
        raise IndexError(f"No such page index {page_number}")

    # There should be exactly one
    layout: LTPage = pages[0]
    pw, ph = layout.width, layout.height
    page_area = pw * ph

    # Locate plausible chart regions
    candidate_regions: list[dict[str, Any]] = []

    for obj in layout:
        # We need to walk recursively (figures may contain nested items)
        for node in _iter_descendants(obj):
            if isinstance(node, LTFigure):
                if _is_plausible_chart_figure(node, page_area):
                    candidate_regions.append({"type": "figure", "bbox": node.bbox, "obj": node})
            elif isinstance(node, LTImage):
                if _is_plausible_chart_image(node, page_area):
                    candidate_regions.append({"type": "image", "bbox": node.bbox, "obj": node})

    # De-duplicate overlapping regions (keep larger / figure-preferred)
    def iou(a, b):
        ax0, ay0, ax1, ay1 = a
        bx0, by0, bx1, by1 = b
        inter_x0 = max(ax0, bx0)
        inter_y0 = max(ay0, by0)
        inter_x1 = min(ax1, bx1)
        inter_y1 = min(ay1, by1)
        iw = max(0, inter_x1 - inter_x0)
        ih = max(0, inter_y1 - inter_y0)
        inter = iw * ih
        if inter == 0:
            return 0.0
        area_a = _bbox_area(a)
        area_b = _bbox_area(b)
        return inter / float(area_a + area_b - inter + 1e-6)

    filtered: list[dict[str, Any]] = []
    for cand in sorted(candidate_regions, key=lambda c: _bbox_area(c["bbox"]), reverse=True):
        keep = True
        for existing in filtered:
            if iou(cand["bbox"], existing["bbox"]) > 0.6:
                keep = False
                break
        if keep:
            filtered.append(cand)

    # Save each region:
    idx = 0
    for reg in filtered:
        idx += 1
        kind = reg["type"]
        bbox = reg["bbox"]
        out_path = os.path.join(base_dir, f"{stem}_p{page_number+1}_chart{idx}.png")

        if kind == "image":
            # Try to directly export the embedded raster image
            pil = _ltimage_to_pil(reg["obj"])
            if pil is not None:
                pil.save(out_path)
                saved_paths.append(out_path)
                continue  # done

        # Otherwise (figure, or undecodable image), rasterize bbox
        if _HAVE_FITZ:
            try:
                _crop_with_fitz(pdf_path, page_number, bbox, out_path, zoom=out_zoom)
                saved_paths.append(out_path)
                continue
            except Exception as e:
                # fall through to pdf2image
                pass

        if _HAVE_PDF2IMAGE:
            try:
                _crop_with_pdf2image(pdf_path, page_number, bbox, out_path, dpi=300, page_wh=(pw, ph))
                saved_paths.append(out_path)
                continue
            except Exception:
                pass

        # Last resort: if we can't rasterize, but it was an LTImage we failed to decode, skip.
        # If it was a figure we can't render without a renderer; skip with no crash.

    return saved_paths


if __name__ == "__main__":
    # Example usage:
    # pdf_file = "pdfparse\\10K_JPMC_2024.pdf"   # your PDF
    pdf_file = "pdfparse\\10K_TSLA_2024.pdf"   # your PDF
    page_idx = 33              # 0-based (page 3)
    results = extract_charts_with_pdfminer(pdf_file, page_idx)
    if results:
        print("Saved chart images:")
        for p in results:
            print(" -", p)
    else:
        print("No chart-like regions found on that page.")
