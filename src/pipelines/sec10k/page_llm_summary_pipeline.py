from pathlib import Path

import yaml

from src import BASE_DATA_DIR, CONFIGS_DIR, TENK_DATA_STORAGE_DIR
from src.llm.sec10k_page_summary import SEC10KPageSummarizer
from src.pipelines.sec10k import load_config
from src.preprocess.sec10k.sec10k_subsection_generator import get_page_text_for_summarization
from src.preprocess.sec10k.sec10k_toc import get_page_numbers_outside_toc


# Base interface for pipelines
def process_pipeline():
    years = ["2022"]
    for year in years:
        filings_to_process = load_config('filings')
        folders_to_process = filings_to_process[year]

        # folders_to_process = [f"{year}-10K-COMMUNICATIONSERVICES-GOOGL"]
        llm_summarizer = SEC10KPageSummarizer()

        for folder in folders_to_process:
            print(f"Processing folder: {folder}")
            summary_folder = BASE_DATA_DIR / year / folder / "pages_summary"
            summary_folder.mkdir(parents=True, exist_ok=True)
            _process_folder(year, folder, llm_summarizer, summary_folder)


def _process_folder(year: str, folder: str, llm_summarizer: SEC10KPageSummarizer, summary_folder: Path):
    pages_to_summarize = get_page_numbers_outside_toc(year, folder)
    print(f"Pages to summarize for folder {folder}: {pages_to_summarize}")
    for page_num in pages_to_summarize:
        print(f"... page: {page_num}")
        summary_file = summary_folder / f"page_{page_num}_summary.txt"
        page_text = get_page_text_for_summarization(year, folder, page_num)
        summary = None
        try:
            summary = llm_summarizer.summarize_sec10k_page(page_text)
            with open(summary_file, 'w', encoding='utf-8') as f:
                f.write(summary)
        except Exception as e:
            print(f"Error summarizing page {page_num} in folder {folder}: {e}")



if __name__ == "__main__":
    process_pipeline()
