import yaml

from src import BASE_DATA_DIR, CONFIGS_DIR
from src.llm.sec10k_table_processor import SEC10KTableProcessor
from src.logging_config import get_logger
from src.pipelines.sec10k import load_config
from src.preprocess.sec10k.sec10k_table_util import get_page_table_info_for_summarization

logger = get_logger(__name__)


# Base interface for pipelines
def process_pipeline():
    years = ["2024", "2023", "2022"]
    for year in years:
        filings_to_process = load_config('filings')
        ticker_to_company = load_config('ticker_to_company')
        folders_to_process = filings_to_process[year]
        # folders_to_process = ["2023-10K-FINANCIALS-JPM","2023-10K-INFOTECH-PATH","2023-10K-COMMUNICATIONSERVICES-GOOGL","2023-10K-UTILITIES-NEE","2023-10K-REALESTATE-PLD"]

        processor = SEC10KTableProcessor()

        for folder in folders_to_process:
            logger.info(f"Processing for table_vlm_summary folder: {folder}")
            summary_folder = BASE_DATA_DIR / year / folder / "tables"
            _process_folder(folder, ticker_to_company, processor, summary_folder)


def _process_folder(folder: str, ticker_to_company: dict, processor: SEC10KTableProcessor, summary_folder):
    year, ticker = folder.split('-')[0], folder.split('-')[-1]
    company_name = ticker_to_company.get(ticker, ticker)
    pages_and_tables_info = get_page_table_info_for_summarization(year, folder)
    error_counts = 0
    for page_num in pages_and_tables_info:
        logger.info(f"... page: {page_num}")
        tables_info = pages_and_tables_info[page_num]
        for table_num in tables_info:
            header = tables_info[table_num]['header']
            footer = tables_info[table_num]['footer']
            image_path = tables_info[table_num]['image_path']
            summary_file = summary_folder / f"page_{page_num}/table_{table_num}_summary.txt"
            try:
                summary = processor.summarize_table(image_path=image_path,company_name=company_name,
                                                    filing_year=year, table_header=header, table_footer=footer)
                with open(summary_file, 'w', encoding='utf-8') as f:
                    f.write(summary)
            except Exception as e:
                error_counts += 1
                logger.error(f"Error summarizing tables on page {page_num} in folder {folder}: {e}")
        if error_counts >= 5:
            logger.error(f"Too many errors in folder {folder}, stopping processing.")
            break


if __name__ == "__main__":
    process_pipeline()
