import hashlib
import json

from unstructured.documents.elements import Element
from unstructured.partition.utils import sorting

from src import BASE_DATA_DIR, CHUNK_SIZE_IN_CHARS
from src.chunking.chunk import Chunk, ChunkMetadata
from src.logging_config import get_logger
from src.preprocess.sec10k.sec10k_table_util import (
    get_table_info_for_chunking,
    is_within_parent_box,
)

logger = get_logger(__name__)

CHUNK_PERSIST_THRESHOLD = 25  # Number of chunks to accumulate before persisting

class BaseChunking:

    def __init__(self):
        """Base class for chunking strategies.
           Title based chunking with CHUNK_SIZE_IN_CHARS characters. Table element to have its own chunk.
        """
        self.processing_section:str = None
        self.processing_title:str = "Introduction" # default title for section if none found so far
        self.text_under_processing:str = ""
        self.last_chunk:Chunk = None
        self.chunks_under_processing:list[Chunk] = []
        self.chunk_persist_path: str = ""


    def generate_and_persist_chunks(self, year: str, folder: str, section:str, elements: list[(int,Element)], isTocBased: bool) -> None:
        if not self.chunk_persist_path:
            self.chunk_persist_path = BASE_DATA_DIR / year / folder / "chunks" / "base_chunked"
            self.chunk_persist_path.mkdir(parents=True, exist_ok=True)
        if not self.processing_section or isTocBased:
            self.processing_section = section
            self.processing_title = "Introduction"  # reset title for new section

        year, doctype, company = folder.split("-")[0], folder.split("-")[1], folder.split("-")[-1]

        if not isTocBased and self.processing_section != section:
            # as sections changed flush current chunk under processing
            if self.text_under_processing:
                page_num = elements[0][0] if elements else -1
                self._create_and_store_chunk(self.text_under_processing, year, doctype, company, page_num=page_num)
                self.text_under_processing = ""
            # check size and persist current chunks
            self._persist_chunks()
            self.processing_section = section

        page_elements_dict = self._convert_into_dict(elements)
        for page_num, page_elements in page_elements_dict.items():
            table_info_for_chunking = get_table_info_for_chunking(year, folder, page_num)
            self._process_elements_for_chunking(page_num, page_elements, table_info_for_chunking, year, doctype, company)

        if isTocBased:
            self._persist_chunks()


    def flush_remaining_chunks(self, folder) -> None:
        """Flush any remaining text under processing into chunks."""
        if self.text_under_processing:
            year, doctype, company = folder.split("-")[0], folder.split("-")[1], folder.split("-")[-1]
            self._create_and_store_chunk(self.text_under_processing, year, doctype, company, page_num=-1)
            self.text_under_processing = ""
        self._persist_chunks(persist_all=True)


    def _process_elements_for_chunking(self, page_num: int, elements: list[Element], table_info_for_chunking: dict, year: str, doctype: str, company: str) -> None:
        for element in elements:
            if element.category == 'Table':
                self._handle_table_chunking(element, table_info_for_chunking, year, doctype, company, page_num)
            else:
                self._handle_text_chunking(element, year, doctype, company,page_num)


    def _handle_table_chunking(self, element: Element, table_info_for_chunking: dict, year: str, doctype: str, company: str, page_num: int) -> None:
        #flush current text chunk under processing
        if self.text_under_processing:
            self._create_and_store_chunk(self.text_under_processing, year, doctype, company, page_num)
            self.text_under_processing = ""

        # create separate chunk for table
        # iterate over table_info_for_chunking to find matching table by element coordinates and extract summary and json
        table_num = -1
        element_coords = sorting.coordinates_to_bbox(element.metadata.coordinates)
        for table_id,table_info in table_info_for_chunking.items():
            table_bbox = table_info.get("table_coords", [])
            if (is_within_parent_box(table_bbox, element_coords)):
                table_num = table_id
                break
        if table_num == -1:
            logger.warning(f"Table element on page {page_num} does not match any table info for chunking.")
            return
        table_json = table_info_for_chunking[table_num].get("table_json", None)
        table_summary = table_info_for_chunking[table_num].get("table_summary", "")
        self._create_and_store_chunk(table_summary, year, doctype, company, page_num, is_table=True, table_json=table_json)

        # check size and persist current chunks
        self._persist_chunks()


    def _handle_text_chunking(self, element: Element, year: str, doctype: str, company: str, page_num: int) -> None:
        if element.category == 'Title':
            #flush current text chunk under processing
            if self.text_under_processing:
                self._create_and_store_chunk(self.text_under_processing, year, doctype, company, page_num)
                self.text_under_processing = ""
            self.processing_title = element.text.strip()

            # check size and persist current chunks
            self._persist_chunks()


        self.text_under_processing += " " + element.text
        #check if accumulated text exceeds chunk size
        while len(self.text_under_processing) >= CHUNK_SIZE_IN_CHARS:
            # Look for sentence boundaries around CHUNK_SIZE_IN_CHARS
            # First try to find a boundary within the limit, then look beyond if needed
            sentence_endings = ['. ', '! ', '? ', '.\n', '!\n', '?\n']
            split_pos = CHUNK_SIZE_IN_CHARS

            # Search for sentence boundary before CHUNK_SIZE_IN_CHARS (going backwards)
            boundary_before = -1
            for i in range(min(CHUNK_SIZE_IN_CHARS, len(self.text_under_processing)) - 1, -1, -1):
                if any(self.text_under_processing[i:i+2] == ending for ending in sentence_endings):
                    boundary_before = i + 1
                    break
            # Search for sentence boundary after CHUNK_SIZE_IN_CHARS (going forwards)
            boundary_after = -1
            for i in range(CHUNK_SIZE_IN_CHARS, len(self.text_under_processing) - 1):
                if any(self.text_under_processing[i:i+2] == ending for ending in sentence_endings):
                    boundary_after = i + 1
                    break

            # Choose the closest boundary to CHUNK_SIZE_IN_CHARS
            if boundary_before > 0 and boundary_after > 0:
            # Both boundaries exist, choose the closer one
                if (CHUNK_SIZE_IN_CHARS - boundary_before) <= (boundary_after - CHUNK_SIZE_IN_CHARS):
                    split_pos = boundary_before
                else:
                    split_pos = boundary_after
            elif boundary_before > 0:
                split_pos = boundary_before
            elif boundary_after > 0:
                split_pos = boundary_after
            else:
            # No sentence boundary found, fall back to word boundary
                last_space = self.text_under_processing[:CHUNK_SIZE_IN_CHARS].rfind(' ')
                if last_space > CHUNK_SIZE_IN_CHARS * 0.5:
                    split_pos = last_space + 1

            chunk_text = self.text_under_processing[:split_pos].strip()
            self._create_and_store_chunk(chunk_text, year, doctype, company, page_num)
            self.text_under_processing = self.text_under_processing[split_pos:]


    def _create_and_store_chunk(self, text: str, year: str, doctype: str, company: str, page_num: int, is_table: bool = False, table_json: dict = None) -> None:
        if not text.strip():
            return

        chunk_metadata = ChunkMetadata(
            doctype=doctype,
            year=year,
            company=company,
            section=self.processing_section,
            subsection=self.processing_title,
            physical_page=page_num,
            is_table=is_table,
            table_json=table_json
        )
        chunk = Chunk(
            text=text,
            metadata=chunk_metadata
        )
        if self.last_chunk:
            self.last_chunk.metadata.next_chunk_id = chunk.id
            chunk.metadata.prev_chunk_id = self.last_chunk.id
        self.last_chunk = chunk
        self.chunks_under_processing.append(chunk)


    def _persist_chunks(self, persist_all: bool = False) -> None:
        """Persist accumulated chunks if threshold reached or if persist_all is True."""
        if not persist_all and len(self.chunks_under_processing) < CHUNK_PERSIST_THRESHOLD:
            return

        # Process chunks in batches of CHUNK_PERSIST_THRESHOLD
        while len(self.chunks_under_processing) > CHUNK_PERSIST_THRESHOLD:
            chunks_to_persist = self.chunks_under_processing[:CHUNK_PERSIST_THRESHOLD]
            self._do_persist_chunks(chunks_to_persist)
            self.chunks_under_processing = self.chunks_under_processing[CHUNK_PERSIST_THRESHOLD:]

        # If persist_all is True, persist any remaining chunks
        if persist_all and self.chunks_under_processing:
            self._do_persist_chunks(self.chunks_under_processing)
            self.chunks_under_processing = []


    def _do_persist_chunks(self, chunks_to_persist: list[Chunk]) -> None:
        if not chunks_to_persist:
            return
        # Create SHA256 hash of all chunk IDs being persisted
        min_page = min([chunk.metadata.physical_page for chunk in chunks_to_persist])
        max_page = max([chunk.metadata.physical_page for chunk in chunks_to_persist])
        chunk_ids = ''.join([chunk.id for chunk in chunks_to_persist])
        hash_id = hashlib.sha256(chunk_ids.encode()).hexdigest()
        page_ranges = f"{min_page}_{max_page}"
        chunk_file_name = f"page_{page_ranges}_{hash_id[:25]}.json"
        chunk_dict = [chunk.to_dict() for chunk in chunks_to_persist]
        chunk_file = self.chunk_persist_path / chunk_file_name
        with open(chunk_file, 'w') as f:
            json.dump(chunk_dict, f, indent=4)
        logger.info(f"Persisted {len(chunks_to_persist)} chunks to {chunk_file}")


    def _convert_into_dict(self, elements: list[(int,Element)]) -> dict[int,list[Element]]:
        """Convert list of (page_num, Element) into dict of page_num to list of Elements."""
        page_elements_dict: dict[int,list[Element]] = {}
        for page_num, element in elements:
            if page_num not in page_elements_dict:
                page_elements_dict[page_num] = []
            page_elements_dict[page_num].append(element)
        return page_elements_dict
