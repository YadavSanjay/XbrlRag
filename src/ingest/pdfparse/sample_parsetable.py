# pip install pymupdf
import os
from typing import List, Tuple

import fitz  # PyMuPDF


def extract_chart_from_pdf(pdf_path: str, page_number: int) -> tuple[str, list[str]]:
    """
    Extracts images from a given page (0-based index) of a PDF using PyMuPDF.
    - Saves every image it finds on that page to the same folder as the PDF.
    - Picks the largest image by area as the "chart" and also saves a copy with a chart_* name.
    - If there are no images, renders the full page as a PNG (fallback).

    Returns:
        chart_path: Path to the primary chart image (largest image or page render fallback).
        all_image_paths: List of paths to all extracted images from that page (may be empty).
    """
    if page_number < 0:
        raise ValueError("page_number must be 0-based and >= 0")

    if not os.path.isfile(pdf_path):
        raise FileNotFoundError(f"No such file: {pdf_path}")

    base_dir = os.path.dirname(os.path.abspath(pdf_path))
    pdf_stem = os.path.splitext(os.path.basename(pdf_path))[0]

    chart_path = ""
    saved_paths: list[str] = []

    with fitz.open(pdf_path) as doc:
        if page_number >= len(doc):
            raise IndexError(f"PDF has only {len(doc)} pages (0-based). Requested page {page_number}.")

        page = doc[page_number]

        # --- Extract raster images embedded on the page ---
        # get_images(full=True) returns tuples with metadata for each image XObject
        # tuple format (xref, smask, width, height, bpc, colorspace, alt. ... varies by PyMuPDF version)
        images = page.get_images(full=True)

        for idx, img_info in enumerate(images, start=1):
            xref = img_info[0]
            pix = fitz.Pixmap(doc, xref)

            # If image has alpha or is CMYK, convert to RGB to ensure PNG compatibility
            if pix.alpha or pix.n > 4:  # CMYK or other exotic colorspaces
                pix = fitz.Pixmap(fitz.csRGB, pix)

            out_name = f"{pdf_stem}_p{page_number+1}_img{idx}.png"
            out_path = os.path.join(base_dir, out_name)
            pix.save(out_path)
            pix = None  # release
            saved_paths.append(out_path)

        # Pick the largest image by area as the "chart"
        if saved_paths:
            def area_for(path):
                # Open via PyMuPDF to read dimensions without PIL
                pix = fitz.Pixmap(path)
                area = pix.width * pix.height
                pix = None
                return area

            largest = max(saved_paths, key=area_for)
            chart_ext = os.path.splitext(largest)[1]
            chart_name = f"{pdf_stem}_p{page_number+1}_chart{chart_ext}"
            chart_path = os.path.join(base_dir, chart_name)

            # If it's not already named chart_*, create a copy
            if os.path.abspath(largest) != os.path.abspath(chart_path):
                # Cheap file copy
                with open(largest, "rb") as src, open(chart_path, "wb") as dst:
                    dst.write(src.read())
        else:
            # --- Fallback: no embedded raster images. Render the whole page. ---
            # You can tweak the zoom if you want higher resolution.
            zoom = 2.0  # 200% for better clarity
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            chart_name = f"{pdf_stem}_p{page_number+1}_chart_fullpage.png"
            chart_path = os.path.join(base_dir, chart_name)
            pix.save(chart_path)
            pix = None

    return chart_path, saved_paths


if __name__ == "__main__":
    # Example usage:
    pdf_file = "pdfparse\\10K_TSLA_2024.pdf"         # path to your PDF
    page_idx = 33                    # 0-based page index; page 3 here
    chart, all_imgs = extract_chart_from_pdf(pdf_file, page_idx)
    print("Primary chart saved to:", chart)
    if all_imgs:
        print("All extracted images:")
        for p in all_imgs:
            print(" -", p)
    else:
        print("No embedded images found; saved a full-page render as the chart.")
