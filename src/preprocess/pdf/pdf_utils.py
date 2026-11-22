import os
import re
import shutil
import time
from time import time

import fitz
from pdf2image import convert_from_path

from src import BASE_DATA_DIR, TENK_DATA_STORAGE_DIR
from src.logging_config import get_logger

log = get_logger(__name__)


def split_pdf_into_single_pages(input_pdf_path, individual_pages_dir):
    """
    Splits a multi-page PDF into individual single-page PDF files.

    Args:
        input_pdf_path (str): The path to the input PDF file.
        output_directory (str): The directory where the individual page PDFs will be saved.
                                 If it doesn't exist, it will be created.
    """
    import os
    start = time.perf_counter()
    # Create the output directory if it doesn't exist
    if not os.path.exists(individual_pages_dir):
        os.makedirs(individual_pages_dir)

    try:
        # Open the input PDF document
        doc = fitz.open(input_pdf_path)

        # Iterate through each page
        for page_num in range(len(doc)):
            # Create a new, empty PDF document for the current page
            new_doc = fitz.open()
            new_doc.insert_pdf(doc, from_page=page_num, to_page=page_num)

            # Define the output filename for the single-page PDF
            output_filename = os.path.join(
                individual_pages_dir,
                f"{os.path.splitext(os.path.basename(input_pdf_path))[0]}_page_{page_num + 1}.pdf"
            )

            # Save the single-page PDF
            new_doc.save(output_filename)
            new_doc.close()  # Close the new document

        log.info(f"Successfully split '{input_pdf_path}' into individual pages in '{individual_pages_dir}'. Total time in seconds: {time.perf_counter() - start}.")

    except Exception as e:
        log.error(f"An error occurred: {e}")
    finally:
        if 'doc' in locals() and not doc.is_closed:
            doc.close()  # Ensure the original document is closed


def create_image_for_pdf_files(input_pdf_path, pdf_folder, output_image_folder, dpi=200, format="jpeg"):
    """
    Converts PDF files in a specified folder to images and saves them in an output folder.
    Parameters:
        input_pdf_path (str): The path to the folder containing the input PDF files.
        pdf_folder (str): The base name of the PDF files to be processed.
        output_image_folder (str): The path to the folder where the output images will be saved.
        dpi (int, optional): The resolution of the output images in dots per inch. Default is 200.
        format (str, optional): The format of the output images. Default is "jpeg".
    Returns:
        None: The function saves the converted images to the specified output folder.
    Raises:
        OSError: If the output folder cannot be created or accessed.
    """
    start = time.perf_counter()
    pattern = fr"{pdf_folder}_page_(\d+)\.pdf"
    #read files in order
    pdf_files = []
    pattern = re.compile(pattern)
    for fname in os.listdir(input_pdf_path):
        match = pattern.match(fname)
        if match:
            number = int(match.group(1))
            pdf_files.append((number, os.path.join(input_pdf_path, fname)))
    pdf_files.sort(key=lambda x: x[0])

    if not os.path.exists(output_image_folder):
        os.makedirs(output_image_folder)

    # Convert PDF pages to images with a 200 DPI (e.g., 200 for print quality)
    for page_num,pdf_file in pdf_files:
        images = convert_from_path(pdf_file, dpi, fmt=format)
        images[0].save(os.path.join(output_image_folder, f"{pdf_folder}_page_{page_num}.jpg"), "JPEG")

    log.info(f"Images saved in folder: {output_image_folder}. Total time in seconds: {time.perf_counter() - start}.")


if __name__ == "__main__":
    # Example usage
    # project_root = "<repo>"
    # temp_dir = f"{project_root}/data/storage/test/temp"
    # input_pdf = f"{project_root}/data/storage/test/multi-column.pdf"

    # individual_pages_dir = f"{temp_dir}/pages/"
    # output_image_folder = f"{temp_dir}/images/"
    # pdf_folder_base_name = "multi-column"

    year = "2022"
    base_input_path = TENK_DATA_STORAGE_DIR / year
    base_output_path = BASE_DATA_DIR / year
    #folders = [f.replace(".pdf", "") for f in os.listdir(base_input_path) if os.path.exists(os.path.join(base_input_path, f))]
    folders = [f"{year}-10K-INFOTECH-NVDA", f"{year}-10K-INFOTECH-AMD", f"{year}-10K-INFOTECH-AVGO"]
    #folders = []
    for folder in folders:
        individual_pages_dir = base_output_path / folder / "pages"
        output_image_folder = base_output_path / folder / "images"
        input_pdf = base_input_path / f"{folder}.pdf"
        print(f"Processing folder: {individual_pages_dir}")
        split_pdf_into_single_pages(input_pdf, individual_pages_dir)
        create_image_for_pdf_files(individual_pages_dir, folder, output_image_folder)

    # print("No error seen, cleaning up temp directory.")
    # shutil.rmtree(temp_dir)
