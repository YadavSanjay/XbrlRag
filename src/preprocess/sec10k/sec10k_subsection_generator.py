import os
import re

from unstructured.documents.elements import Element
from unstructured.partition.pdf import partition_pdf

from src import BASE_DATA_DIR, TENK_DATA_STORAGE_DIR, UNSTRUCTURED_HIRES_MODEL_NAME
from src.logging_config import get_logger
from src.preprocess.sec10k.sec10k_toc import (
    TENK_STRUCTURE,
    detect_toc_page,
    get_toc_section_physical_page_map,
)
from src.preprocess.utils.string_utils import are_string_starting_similar, are_strings_similar

LOGGER = get_logger(__name__)

OUTSIDE_SEC10K_TOC_RANGE = "OUTSIDE_SEC10K_TOC_RANGE"


def extract_subsections_per_toc(year: str, pdf_folder: str) -> dict[str, list[(int, Element)]]:
    pdf_file_path = BASE_DATA_DIR / year / pdf_folder / "pages"
    source_pdf_file = TENK_DATA_STORAGE_DIR / year / f"{pdf_folder}.pdf"

    section_physical_page_map = get_toc_section_physical_page_map(year,source_pdf_file, pdf_folder)
    starting_page = section_physical_page_map.get("Item 1. Business")
    sections = section_physical_page_map.keys()
    last_page_plusbuffer_from_toc = max(section_physical_page_map.values()) + 2

    section_elements_dict = {section:[] for section in sections}
    #section_elements_dict[OUTSIDE_SEC10K_TOC_RANGE] = []

    pdf_files = []
    pattern = fr"{pdf_folder}_page_(\d+)\.pdf"
    pattern = re.compile(pattern)
    for fname in os.listdir(pdf_file_path):
        match = pattern.match(fname)
        if match:
            number = int(match.group(1))
            pdf_files.append((number, os.path.join(pdf_file_path, fname)))
    pdf_files.sort(key=lambda x: x[0])
    pdf_files = pdf_files[starting_page-1:]

    sections_iter = _get_elements(sections)
    current_section = next(sections_iter, None)
    section_to_check = next(sections_iter, None)
    next_section_start_page = section_physical_page_map.get(section_to_check)

    for page_num,file_path in pdf_files:
        elements = partition_pdf(filename=file_path,languages=["eng"], strategy="hi_res", hi_res_model_name=UNSTRUCTURED_HIRES_MODEL_NAME)
        if page_num > last_page_plusbuffer_from_toc:
            #section_elements_dict[OUTSIDE_SEC10K_TOC_RANGE].extend(elements)
            print(f"Reached beyond last toc page plus buffer at page_num: {page_num}, stopping further processing.")
            break
        else:
            if next_section_start_page is None or page_num < next_section_start_page:
                page_elements = [(page_num, element) for element in elements]
                section_elements_dict[current_section].extend(page_elements)
            else:
                LOGGER.info(f'"looking for section : {section_to_check} -- page_num : {page_num}"')
                next_section_found = False
                for element in elements:
                    # if page_num == 71:
                    #     LOGGER.info(f"element.category={element.category}, element.text={element.text}")
                    if element.category in ["Title", "Header", "NarrativeText"]:
                        if are_strings_similar(section_to_check, element.text, threshold=0.8) \
                            or are_string_starting_similar(section_to_check, element.text, starting_length=8):
                            next_section_found = True
                    if next_section_found:
                        LOGGER.info(f'"moved to section : {section_to_check} -- page_num : {page_num}"')
                        current_section = section_to_check
                        section_to_check = next(sections_iter, None)
                        next_section_start_page = section_physical_page_map.get(section_to_check)
                        next_section_found = False
                    section_elements_dict[current_section].append((page_num, element))

    return section_elements_dict


def get_page_text_for_summarization(year, folder, page_number):
    page_elements = get_page_elements(year, folder, page_number)
    page_text = ""
    for _, element in page_elements:
        if element.category != "Table":
            if element.category == "Header":
                page_text += f"<header> {element.text}\n"
            elif element.category == "Title":
                page_text += f"<title> {element.text}\n"
            else:
                page_text += f"{element.text}\n"
    return page_text


def get_page_elements(year, folder, page_number):
    file_path = BASE_DATA_DIR / year / folder / "pages" / f"{folder}_page_{page_number}.pdf"
    elements = partition_pdf(filename=file_path,languages=["eng"], strategy="hi_res", hi_res_model_name=UNSTRUCTURED_HIRES_MODEL_NAME)
    return [(page_number, element) for element in elements]


def _get_elements(elements):
    for element in elements:
        yield element


if __name__ == "__main__":
    year = "2024"
    folder = f"{year}-10K-FINANCIALS-JPM"
    section_elements_dict = extract_subsections_per_toc(year, folder)
    for section, elements in section_elements_dict.items():
        print(f"Section: {section} -- # of elements: {len(elements)}")
        tables = [element for page_num, element in elements if element.category == "Table"]
        print(f"# of tables: {len(tables)}")
