from unstructured.documents.elements import Element

from src import BASE_DATA_DIR
from src.chunking.base_chunking import BaseChunking
from src.llm.sec10k_page_summary_reader import Sec10kPageSummaryReader
from src.logging_config import get_logger
from src.pipelines.sec10k import load_config
from src.preprocess.sec10k.sec10k_subsection_generator import (
    extract_subsections_per_toc,
    get_page_elements,
)
from src.preprocess.sec10k.sec10k_toc import get_page_numbers_outside_toc

logger = get_logger(__name__)


# Base interface for pipelines
def process_pipeline():

    filings_to_process = load_config('filings')
    years = list(filings_to_process.keys())
    for year in years:
        folders_to_process = filings_to_process[year]
        folders_to_process = [f"{year}-10K-COMMUNICATIONSERVICES-GOOGL"]

        chunking_strategy = BaseChunking()
        page_summary_reader = Sec10kPageSummaryReader()

        for folder in folders_to_process:
            logger.info(f"Processing chunking for folder: {folder}")
            page_summary_base = BASE_DATA_DIR / year / folder / "pages_summary"

            # process elements found under toc sections
            section_element_list: dict[str, list[(int, Element)]] = extract_subsections_per_toc(year, folder)
            for section, elements in section_element_list.items():
                logger.info(f"... section: {section} with {len(elements)} elements")
                chunking_strategy.generate_and_persist_chunks(year, folder, section, elements, isTocBased=True)

            logger.info(f"completed toc section elements, processing elements outside TOC for folder: {folder}")
            # process elements outside toc sections
            outside_toc_page_list = get_page_numbers_outside_toc(year, folder)
            logger.info(f"pages outside TOC for folder {folder}: {outside_toc_page_list}")
            for page_num in outside_toc_page_list:
                logger.info(f"... page: {page_num}")
                # get elements for the page
                elements = get_page_elements(year, folder, page_num)
                page_summary_path = page_summary_base / f"page_{page_num}_summary.txt"
                pageSummary = page_summary_reader.read_summary(page_summary_path)
                page_section = pageSummary.get("section", "Other")
                logger.debug(f"page_num={page_num}, page_section={page_section}")
                chunking_strategy.generate_and_persist_chunks(year, folder, page_section, elements, isTocBased=False)

            # flush any remaining chunks
            chunking_strategy.flush_remaining_chunks(folder)


if __name__ == "__main__":
    process_pipeline()
