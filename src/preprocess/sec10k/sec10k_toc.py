import glob
import os
import pprint
import re
from typing import Dict, List, Optional, Tuple

import fitz
from unstructured.partition.pdf import partition_pdf

from src import BASE_DATA_DIR, TENK_DATA_STORAGE_DIR, UNSTRUCTURED_HIRES_MODEL_NAME
from src.logging_config import get_logger

TENK_STRUCTURE: dict[str, list[tuple[str, str]]] = {
    "Part I": [
        ("Item 1", "Business"),
        ("Item 1A", "Risk Factors"),
        ("Item 1B", "Unresolved Staff Comments"),
        ("Item 1C", "Cybersecurity"),
        ("Item 2", "Properties"),
        ("Item 3", "Legal Proceedings"),
        ("Item 4", "Mine Safety Disclosures"),
    ],
    "Part II": [
        ("Item 5", "Market for Registrant’s Common Equity, Related Stockholder Matters and Issuer Purchases of Equity Securities"),
        ("Item 6", "Reserved"),
        ("Item 7", "Management’s Discussion and Analysis of Financial Condition and Results of Operations"),
        ("Item 7A", "Quantitative and Qualitative Disclosures About Market Risk"),
        ("Item 8", "Financial Statements and Supplementary Data"),
        ("Item 9", "Changes in and Disagreements With Accountants on Accounting and Financial Disclosure"),
        ("Item 9A", "Controls and Procedures"),
        ("Item 9B", "Other Information"),
        ("Item 9C", "Disclosure Regarding Foreign Jurisdictions that Prevent Inspections"),
    ],
    "Part III": [
        ("Item 10", "Directors, Executive Officers and Corporate Governance"),
        ("Item 11", "Executive Compensation"),
        ("Item 12", "Security Ownership of Certain Beneficial Owners and Management and Related Stockholder Matters"),
        ("Item 13", "Certain Relationships and Related Transactions, and Director Independence"),
        ("Item 14", "Principal Accounting Fees and Services"),
    ],
    "Part IV": [
        ("Item 15", "Exhibits, Financial Statement Schedules"),
        ("Item 16", "Form 10K Summary"),
    ],
}

TENK_SECTIONS = [subitem[1] for item in TENK_STRUCTURE.values() for subitem in item]

RE_SPACES_DOTS = r"[\.\]\[·]*"  # dotted leaders or dashes/bullets or non-breaking space
RE_FOOTER = r"[\.\]\[·\-]*"  # dotted leaders or dashes/bullets or non-breaking space

LOGGER = get_logger(__name__)


def get_toc_section_page_map(pdf_file):
    sections = TENK_SECTIONS
    section_pagestart_dict = dict.fromkeys(sections, "-1")
    #print(section_pagestart_dict)
    toc_text = get_toc_text(pdf_file)
    toc_textlines = toc_text.splitlines()
    toc_textlines = [normalize(toc_textline).strip() for toc_textline in toc_textlines]
    textlines_gen = text_generator(toc_textlines)
    #print(toc_text)
    for section in sections:
        #print(f"checking for section={section}")
        section_splits = section.split()
        section_start, section_end = section_splits[0], section_splits[-1]
        #print(toc_text)
        if section_start in toc_text or section_end in toc_text:
            # print(f"section_start={section_start}, section_end={section_end}")
            section_found = False
            for textline in textlines_gen:
                norm_textline = normalize(textline).strip()
                is_page_num, page_num = is_page_range_or_number(norm_textline)
                #print(f"textline={norm_textline}, page_num={page_num}, section_found={section_found}")
                if (section_found and is_page_num):
                    section_pagestart_dict[section] = page_num
                    section_found = False
                    break
                if textline.startswith(section_start) or textline.endswith(section_end):
                    section_found = True
    return section_pagestart_dict


def get_toc_section_physical_page_map(year: str, parent_pdf_file, pdf_breakdown_folder, max_pages: int = 10):
    section_page_map = get_toc_section_page_map(parent_pdf_file)
    toc_page_number = detect_toc_page(parent_pdf_file, max_pages=max_pages) + 1
    pdf_file_path = BASE_DATA_DIR / year / pdf_breakdown_folder / "pages"

    # look for first page with mapping to toc item and corresponding physical page number
    pdf_files = []
    pattern = fr"{pdf_breakdown_folder}_page_(\d+)\.pdf"
    pattern = re.compile(pattern)
    for fname in os.listdir(pdf_file_path):
        match = pattern.match(fname)
        if match:
            number = int(match.group(1))
            pdf_files.append((number, os.path.join(pdf_file_path, fname)))
    pdf_files.sort(key=lambda x: x[0])
    pdf_files = pdf_files[toc_page_number:max_pages]

    toc_business_section_page_num = int(section_page_map.get("Business", "-1"))
    physical_page_num = toc_page_number
    delta_pages = 0
    delta_found = False
    for page_num,file_path in pdf_files:
        elements = partition_pdf(filename=file_path,languages=["eng"], strategy="hi_res", hi_res_model_name=UNSTRUCTURED_HIRES_MODEL_NAME)
        elements = list(reversed(elements[-2:]))
        for element in elements:
            #LOGGER.info(f"element.category={element.category}, element.text={element.text}")
            element_text_normalized = normalize(element.text)
            if element_text_normalized.isdigit():
                logical_page_num = int(element_text_normalized)
                if logical_page_num == toc_business_section_page_num:
                    physical_page_num = page_num
                    delta_pages = physical_page_num - toc_business_section_page_num
                    delta_found = True
                    LOGGER.info(f"physical_page_num={physical_page_num}, toc_page_num={toc_page_number}, delta_page={delta_pages}")
                break
        if delta_found:
            break

    section_physical_page_map = {}
    sections = [subitem for item in TENK_STRUCTURE.values() for subitem in item]
    for section_tuple in sections:
        section = section_tuple[1]
        key = ". ".join(section_tuple)
        logical_page = section_page_map[section]
        logical_page_int = int(logical_page)
        if logical_page_int != -1:
            physical_page = logical_page_int + delta_pages
            section_physical_page_map[key] = physical_page
    return section_physical_page_map


def detect_toc_page(pdf_file, max_pages: int = 10) -> int | None:
    """
    Look for a page that likely contains a Table of Contents by scanning
    the first `max_pages` pages for keywords.
    """
    keywords = [
        "unresolved staff comments",
        "risk factors",
        "legal proceedings",
        "executive compensation",
    ]
    doc = fitz.open(pdf_file)
    for i in range(min(max_pages, doc.page_count)):
        text = doc.load_page(i).get_text("text").lower()
        # print(text)
        # print(f"checking page={i}")
        text = text.replace("\xa0", " ")
        if any(k in text for k in keywords):
            return i
    return None


def get_toc_text(pdf_file,max_pages_to_check=10):
    toc_page_number = detect_toc_page(pdf_file, max_pages=max_pages_to_check)
    LOGGER.debug(f"file={pdf_file}, toc_page_number={toc_page_number}")
    doc = fitz.open(pdf_file)
    page = doc.load_page(toc_page_number)
    text = page.get_text("text")
    #print(text)
    return text


def text_generator(textlines):
    for textline in textlines:
        yield textline


def get_page_dimensions(pdf_file):
    doc = fitz.open(pdf_file)
    LOGGER.debug(f"file={pdf_file}")
    page = doc.load_page(0)
    height, width = page.rect.height, page.rect.width
    #print(text)
    return [height, width]


def normalize(s: str) -> str:
    return re.sub(rf"{RE_SPACES_DOTS}+", "", s.strip())


def normalize_footer(s: str) -> str:
    return re.sub(rf"{RE_FOOTER}+", "", s.strip())


def is_page_range_or_number(page_num):
    if page_num.isdigit():
        return (True,page_num)
    else:
        if "-" in page_num:
            start = page_num.split("-")
            if start[0].isdigit():
                return (True,start[0])
            return (True,page_num)
    return (False,"-1")


def get_page_numbers_outside_toc(year, folder: str) -> list[int]:
    pdf_folder_path = BASE_DATA_DIR / year / folder / "pages"
    source_pdf_file = TENK_DATA_STORAGE_DIR / year / f"{folder}.pdf"
    section_physical_page_map = get_toc_section_physical_page_map(year, source_pdf_file, folder)
    starting_page_beyond_toc_listing = max(section_physical_page_map.values()) + 3

    pages_outside_toc = []
    pdf_pages = sorted(pdf_folder_path.glob("*.pdf"))
    for pdf_page in pdf_pages:
        page_num = int(pdf_page.stem.split('_')[-1])
        if page_num < starting_page_beyond_toc_listing:
            continue
        pages_outside_toc.append(page_num)
    pages_outside_toc.sort()
    return pages_outside_toc


if __name__ == "__main__":
    year = "2024"
    root_folder = TENK_DATA_STORAGE_DIR
    folder = "*"
    mismatch_check = False
    print_toc = True

    if mismatch_check:
        matching_files = []
        for filename in os.listdir(os.path.join(root_folder, year)):
            if filename.endswith("pdf") and os.path.isfile(os.path.join(root_folder, year, filename)):
                matching_files.append(filename)
        files_with_mismatch = []
        for filename in matching_files:
                file_path = os.path.join(root_folder, year, filename)
                print(f"file={file_path}")
                section_page_map = get_toc_section_page_map(file_path)
                folder = filename.replace(".pdf", "")
                section_physical_page_map = get_toc_section_physical_page_map(year, file_path, folder)
                for section, page in section_page_map.items():
                    physical_page = section_physical_page_map.get(section, -1)
                    if page.isdigit() and physical_page == int(page):
                        files_with_mismatch.append((filename, section, page, physical_page))
                        print(f"mismatch found in file={filename}, section={section}, toc_page={page}, physical_page={physical_page}")
                        break
        print(f"Total files with mismatch: {len(files_with_mismatch)}")

    if print_toc:
        folder = "2024-10K-INFOTECH-AVGO"
        section_page_map = get_toc_section_page_map(f"{TENK_DATA_STORAGE_DIR}/{year}/{folder}.pdf")
        for section, page in section_page_map.items():
            print(f"{section} : {page}")
        print('--------------------------------')
        section_physical_page_map = get_toc_section_physical_page_map(
            year, f"{TENK_DATA_STORAGE_DIR}/{year}/{folder}.pdf", f"{folder}", max_pages=10)
        for section, page in section_physical_page_map.items():
            print(f"{section} : {page}")
