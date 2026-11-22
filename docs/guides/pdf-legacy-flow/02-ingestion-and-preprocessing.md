# PDF Legacy Flow — 2. Ingestion & Preprocessing

This stage turns a source PDF into page images, detected tables, and LLM/VLM
summaries — the inputs the chunker consumes.

> ⚠️ **There is no automated PDF *downloader* in the legacy flow.** Source PDFs
> are placed manually at `data/storage/10k/<year>/<folder>.pdf` (folder name =
> `{YEAR}-10K-{SECTOR}-{TICKER}`). "Incremental" simply means adding a new
> `<folder>.pdf` and processing only that folder. The XBRL flow is the one with a
> real EDGAR downloader.

## ⚠️ How you control what gets processed

Every step below is run as `python -m <module>` with **no flags**. The set of
years/folders is **hardcoded in each module's `__main__`** (or in the
`chunking`/`load` pipelines). To process your filing you edit those lines first.
The exact locations are listed per step.

---

## 2.1 Split PDF into pages + render images

Module: [`src/preprocess/pdf/pdf_utils.py`](../../../src/preprocess/pdf/pdf_utils.py)

```bash
python -m src.preprocess.pdf.pdf_utils
```

- Functions: `split_pdf_into_single_pages(input_pdf, pages_dir)` and
  `create_image_for_pdf_files(pages_dir, folder, images_dir, dpi=200, format="jpeg")`.
- Produces `pages/<folder>_page_<n>.pdf` and `images/<folder>_page_<n>.jpg`.
- ⚠️ `__main__` hardcodes `year="2022"` and an NVDA/AMD/AVGO folder list — edit
  these to point at your folder.

## 2.2 Detect tables / layout (DETR)

Module: [`src/preprocess/vlm/detr_layout_parser.py`](../../../src/preprocess/vlm/detr_layout_parser.py)

```bash
python -m src.preprocess.vlm.detr_layout_parser
```

- `extract_and_save_tables_from_image(year, folder, page_number=-1, threshold=0.8, box_buffer=10.0)`
  using model `cmarkea/detr-layout-detection`.
- Saves crops `tables/page_<p>/table_<n>.jpeg` plus
  `table_<n>_metadata.txt` (score, box_buffer, x1,y1,x2,y2). IoU-dedups overlapping
  boxes at threshold 0.5.
- ⚠️ Edit the hardcoded `year`/folder list in `__main__`.

## 2.3 Extract table header / footer

Module: [`src/preprocess/sec10k/sec10k_table_util.py`](../../../src/preprocess/sec10k/sec10k_table_util.py)

```bash
python -m src.preprocess.sec10k.sec10k_table_util
```

- `extract_head_foot_for_identified_tables(year, folder, page_number=-1)` writes
  `table_<n>_headerfooter.txt` next to each crop.
- Also exposes `get_page_table_info_for_summarization` and
  `get_table_info_for_chunking`, consumed downstream.

> Steps 2.1–2.3 are the work the (empty) stub
> `pipelines/sec10k/table_vlm_preprocess_pipeline.py` *documents* but does not
> implement — run the three modules above directly.

## 2.4 Summarize tables with a vision model (VLM)

Pipeline: [`src/pipelines/sec10k/table_vlm_summary_pipeline.py`](../../../src/pipelines/sec10k/table_vlm_summary_pipeline.py)

```bash
python -m src.pipelines.sec10k.table_vlm_summary_pipeline
```

- ⚠️ `years = ["2024", "2023", "2022"]` is hardcoded (line ~14); folders come from
  `load_config('filings')[year]`.
- For each detected table it calls
  `SEC10KTableProcessor.summarize_table(image_path, company_name, filing_year, table_header, table_footer)`
  (vision model, default `gpt-5-nano`-class; see [`src/llm/sec10k_table_processor.py`](../../../src/llm/sec10k_table_processor.py)).
- Writes `tables/page_<p>/table_<n>_summary.txt`. Aborts a folder after 5 errors.
- Company name resolved via `ticker_to_company` (see the config caveat in
  [setup §1.5](./01-setup-and-dependencies.md)).

## 2.5 Per-page LLM summaries (for non-TOC pages)

Pipeline: [`src/pipelines/sec10k/page_llm_summary_pipeline.py`](../../../src/pipelines/sec10k/page_llm_summary_pipeline.py)

```bash
python -m src.pipelines.sec10k.page_llm_summary_pipeline
```

- ⚠️ `years = ["2022"]` is hardcoded (line ~14) — **only 2022 by default**.
- Finds non-TOC pages via `get_page_numbers_outside_toc(year, folder)`, then
  `SEC10KPageSummarizer().summarize_sec10k_page(page_text)`
  (see [`src/llm/sec10k_page_summary.py`](../../../src/llm/sec10k_page_summary.py),
  default `gpt-5-nano`, returns `{summary, keywords, section}`).
- Writes `pages_summary/page_<p>_summary.txt`. These summaries supply the
  `section` label for pages that fall outside the parsed Table of Contents.

## 2.6 (Background) TOC & subsection parsing

These are libraries used by chunking, not separate run steps, but worth knowing:

- [`sec10k_toc.py`](../../../src/preprocess/sec10k/sec10k_toc.py) — detects the TOC
  page, maps logical→physical page numbers, exposes
  `get_page_numbers_outside_toc`.
- [`sec10k_subsection_generator.py`](../../../src/preprocess/sec10k/sec10k_subsection_generator.py)
  — `extract_subsections_per_toc(year, folder)` partitions each page PDF with
  `unstructured` (`hi_res` / yolox) and assigns elements to TOC sections.

There is also a standalone, **fully-argparse** outline parser that is *not* wired
into the pipeline but is handy for inspection:

```bash
python -m src.ingest.pdfparse.hierarchicalParse <pdf> \
  --format {json,markdown} --out FILE --page-range 1:40 \
  --min-table-lines 12 --max-level 6 [--toc-skip] [--debug]
```

(`parse_pdf_structure(...)`, pdfminer-based, independent of the unstructured path.)

---

## 2.7 Recommended order for one filing

```bash
# 0. Put the PDF at data/storage/10k/2024/2024-10K-COMMUNICATIONSERVICES-GOOGL.pdf
# 1. Edit the hardcoded year/folder in each module below to match, then:
python -m src.preprocess.pdf.pdf_utils                 # split + images
python -m src.preprocess.vlm.detr_layout_parser        # detect tables
python -m src.preprocess.sec10k.sec10k_table_util      # head/footer
python -m src.pipelines.sec10k.table_vlm_summary_pipeline   # VLM table summaries
python -m src.pipelines.sec10k.page_llm_summary_pipeline    # page summaries
# → then chunking + load, see guide 3
```

Next: [3. Chunking & vector DB load](./03-chunking-and-vectordb.md).
