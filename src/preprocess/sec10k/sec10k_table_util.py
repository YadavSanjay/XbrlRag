import csv
import os
import re

from unstructured.partition.pdf import partition_pdf
from unstructured.partition.utils import sorting

from src import BASE_DATA_DIR, UNSTRUCTURED_HIRES_MODEL_NAME
from src.llm.sec10k_table_summary_reader import load_table_json
from src.logging_config import get_logger
from src.preprocess.sec10k.sec10k_toc import TENK_STRUCTURE
from src.preprocess.utils.coords_util import is_encapsulated

METADATA_PATTERN = r"table_(\d+)_metadata\.txt"
METADATA_PATTERN = re.compile(METADATA_PATTERN)
TABLES_PAGE_PATTERN = r"page_(\d+)"
TABLES_PAGE_PATTERN = re.compile(TABLES_PAGE_PATTERN)


LOGGER = get_logger(__name__)


def extract_head_foot_for_identified_tables(year: str, pdf_folder: str, page_number=-1):
    pdf_file_path = BASE_DATA_DIR / year / pdf_folder / "pages"
    pages_with_tables = _get_pages_with_tables(year, pdf_folder, page_number)

    files = [(page_num, os.path.join(pdf_file_path, f"{pdf_folder}_page_{page_num}.pdf")) for page_num in pages_with_tables]
    files.sort(key=lambda x: x[0])

    for page_num,file_path in files:
        elements = partition_pdf(filename=file_path,languages=["eng"], strategy="hi_res", hi_res_model_name=UNSTRUCTURED_HIRES_MODEL_NAME)
        all_table_coords = _get_table_coords(year, pdf_folder, page_num )
        LOGGER.info(f'{len(all_table_coords)} # of tables on page -- tablecoords -- {all_table_coords}')
        tables_header_footers = _get_header_footer_for_tables(elements, all_table_coords)
        _save_extracted_headers_footers(year, pdf_folder, page_num, tables_header_footers)
    LOGGER.info("completed processing extract_head_foot_for_identified_tables for folder %s", pdf_folder)


def get_page_table_info_for_summarization(year: str, pdf_folder: str, page_num = -1):
    pages_with_tables = _get_pages_with_tables(year, pdf_folder, page_num)
    page_info = {}
    if page_num != -1 and page_num not in pages_with_tables:
        return page_info
    for page_num in pages_with_tables:
        table_info = {}
        table_headfoot_basepath = BASE_DATA_DIR / year / pdf_folder / "tables" / f"page_{page_num}"
        for fname in os.listdir(table_headfoot_basepath):
            match = re.match(r"table_(\d+)_headerfooter\.txt", fname)
            if match:
                table_num = int(match.group(1))
                table_headfoot_path = table_headfoot_basepath / fname
                with table_headfoot_path.open("r", encoding="utf8") as headfoot_file:
                    content = headfoot_file.read()
                    header, footer = "", ""
                    for line in content.split("\n"):
                        if line.startswith("header="):
                            header = line[len("header="):]
                        elif line.startswith("footer="):
                            footer = line[len("footer="):]
                    table_info[table_num] = {
                        "image_path": table_headfoot_basepath / f"table_{table_num}.jpeg",
                        "header": header,
                        "footer": footer
                    }
        table_info = dict(sorted(table_info.items()))
        page_info[page_num] = table_info
    return page_info


def get_table_info_for_chunking(year: str, pdf_folder: str, page_num) -> dict[int, dict[str, any]]:
    table_info = {}
    page_basepath = BASE_DATA_DIR / year / pdf_folder / "tables" / f"page_{page_num}"
    table_metadata_info = _get_table_coords(year, pdf_folder, page_num)
    for table_num, coords in table_metadata_info:
        if table_num not in table_info:
            table_info[table_num] = {}
        table_info[table_num]["table_coords"] =  coords
        table_summary_path = page_basepath / f"table_{table_num}_summary.txt"
        if table_summary_path.exists():
            summary_jsondata = load_table_json(table_summary_path)
            table_summary = summary_jsondata.get("table_summary", {})
            table_summary_text = ""
            for key, value in table_summary.items():
                table_summary_text += f"{key}: {value}\n"
            table_info[table_num]["table_summary"] = table_summary_text
            #table_json = summary_jsondata.get("table_json", {})

            # storing path to json file instead of json data to reduce memory usage and vector db bloat.
            # To be read during retrieval if table chunk has high similarity to query.
            table_info[table_num]["table_json"] = str(table_summary_path)

    return table_info


def _get_pages_with_tables(year: str, pdf_folder: str, page_number=-1):
    # get list of pages which have tables identified
    identified_tables_path = BASE_DATA_DIR / year / pdf_folder / "tables"
    pages_with_tables = []
    if page_number != -1:
        pages_with_tables = [page_number]
    else:
        for foldername in os.listdir(identified_tables_path):
            match = TABLES_PAGE_PATTERN.match(foldername)
            if match:
                number = int(match.group(1))
                pages_with_tables.append(number)
    pages_with_tables.sort()
    return pages_with_tables


def _get_table_coords(year: str, pdf_folder: str, page_num, pdf_vs_img_coords_shift_factor = 1.0):
    table_coords = []
    table_image_path = BASE_DATA_DIR / year / pdf_folder / f"tables/page_{page_num}/"
    if not os.path.exists(table_image_path):
        return table_coords
    metadata_files = []
    for fname in os.listdir(table_image_path):
        match = METADATA_PATTERN.match(fname)
        if match:
            number = int(match.group(1))
            metadata_files.append((number, os.path.join(table_image_path, fname)))
    metadata_files.sort(key=lambda x: x[0])
    for table_num, meta_file_path in metadata_files:
        with open(meta_file_path) as meta_file:
            reader = csv.reader(meta_file)
            for row in reader:
                adj, x1, y1, x2, y2 = float(row[1]),float(row[2]),float(row[3]),float(row[4]),float(row[5])
                x1 = (x1 + adj)*pdf_vs_img_coords_shift_factor
                y1 = (y1 + adj)*pdf_vs_img_coords_shift_factor
                x2 = (x2 - adj)*pdf_vs_img_coords_shift_factor
                y2 = (y2 - adj)*pdf_vs_img_coords_shift_factor
                table_coords.append((table_num,[x1,y1,x2,y2]))
    return table_coords


def _get_header_footer_for_tables(page_elements, page_tables_bbox):
    if not page_tables_bbox:
        return page_elements
    filtered_elements = []
    elements_bboxes = []
    for element in page_elements:
        element_bbox = sorting.coordinates_to_bbox(element.metadata.coordinates)
        elements_bboxes.append((element, element_bbox))

    header_footer_data = []

    for table_num, table_bbox in page_tables_bbox:
        table_header = None
        table_footers = []

        # Find header: NarrativeText or Title element just above the table
        for idx, (element, element_bbox) in enumerate(elements_bboxes):
            if element.category == "Table" and idx > 0 and is_within_parent_box(table_bbox, element_bbox):
                possible_header_element, _ = elements_bboxes[idx - 1]
                if possible_header_element.category == "NarrativeText" or possible_header_element.category == "Title":
                    table_header = possible_header_element
                    break  # Found the header, exit the loop

        # Find footers: ListItem elements just below the table
        table_idx = None
        for idx, (element, element_bbox) in enumerate(elements_bboxes):
            if element.category == "Table" and is_within_parent_box(table_bbox, element_bbox):
                table_idx = idx
                break
        if table_idx is not None:
            for j in range(table_idx + 1, len(elements_bboxes)):
                element, element_bbox = elements_bboxes[j]
                if element.category == "ListItem":
                    table_footers.append(element.text)
                else:
                    break  # Stop if we encounter a non-ListItem element

        header_footer_data.append({
            'table_num': table_num,
            'table_bbox': table_bbox,
            'header': table_header,
            'footers': table_footers
        })

    LOGGER.info(f"Extracted headers/footers for {len(header_footer_data)} tables")
    return header_footer_data


def is_within_parent_box(parent_bbox, to_check_bbox, buffer=10.0):
    # [top-left-x, top-left-y, bottom-right-x, bottom-right-y]
    px1, py1, px2, py2 = parent_bbox
    px1 = px1 - buffer
    py1 = py1 - buffer
    px2 = px2 + buffer
    py2 = py2 + buffer
    cx1, cy1, cx2, cy2 = to_check_bbox
    return is_encapsulated([cx1, cy1, cx2, cy2], [px1, py1, px2, py2])


def _save_extracted_headers_footers(year: str, pdf_folder: str, page_number, tables_header_footers):
    table_headfoot_basepath = BASE_DATA_DIR / year / pdf_folder / "tables" / f"page_{page_number}"
    for table_data in tables_header_footers:
        table_num = table_data['table_num']
        table_headfoot_path = table_headfoot_basepath / f"table_{table_num}_headerfooter.txt"
        header = table_data['header']
        footers = table_data['footers']
        header_text = header.text if header else ""
        footer_text = ". ".join(footers) if footers else ""
        with table_headfoot_path.open("w", encoding="utf8") as headfoot_file:
            headfoot = "header=" +  header_text + "\nfooter=" + footer_text
            headfoot_file.write(headfoot)
    LOGGER.info("saved header-footer for to %s", table_headfoot_path)


if __name__ == "__main__":
    years = ["2024", "2023", "2022"]
    for year in years:
        base_path = BASE_DATA_DIR   / year
        #folders = [f for f in os.listdir(base_path) if os.path.isdir(os.path.join(base_path, f))]
        folders = [f"{year}-10K-INFOTECH-NVDA", f"{year}-10K-INFOTECH-AMD", f"{year}-10K-INFOTECH-AVGO"]
        for folder in folders:
            LOGGER.info("Processing directory: %s", folder)
            # Example usage
            extract_head_foot_for_identified_tables(year, folder)

            # page_info = get_page_table_info_for_summarization(year, folder)
            # for page_num, tables in page_info.items():
            #     for table_num, table_data in tables.items():
            #         header = table_data['header']
            #         footer = table_data['footer']
            #         image_path = table_data['image_path']
            #         LOGGER.info(f"Page {page_num} Table {table_num} -- Image: {image_path} -- Header: {header} -- Footer: {footer}")

            # table_info = get_table_info_for_chunking(year, folder, page_num=10)
            # for table_num, table_data in table_info.items():
            #     table_coords = table_data.get("table_coords", [])
            #     table_summary = table_data.get("table_summary", "")
            #     table_json = table_data.get("table_json", {})
            #     LOGGER.info(f"Table {table_num} -- Coords: {table_coords} -- Summary: \n{table_summary} -- JSON table: \n{table_json}")
