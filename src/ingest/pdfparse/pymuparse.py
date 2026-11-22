from pprint import pprint

import pymupdf

doc = pymupdf.open("pdfparse\\10KTSLA2024.pdf")
print("Number of pages:", doc.page_count)
page = doc.load_page(35)  # Load the first page
tabs = page.find_tables()
print(f"{len(tabs.tables)} found on {page}")
if tabs.tables:
    for table in tabs.tables:
        print(table.header.names)
        pprint(table.to_markdown())
        print('----------------------')

images = page.find_images()
print(f"{len(images)} found on {page}")
if images:
    for image in images:
        print(image)
        print('----------------------')

pagenumber = page.number + 1  # Page numbers are zero-based
print(f"Page {pagenumber} Text:")
