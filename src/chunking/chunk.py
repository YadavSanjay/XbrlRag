import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import List, Optional


class ChunkMetadata:

    def __init__(self,
        doctype: str | None = None,
        year: int | None = None,
        company: str | None = None,
        section: str | None = None,
        subsection: str | None = None,
        is_table: bool = False,
        table_json: str | None = None,
        physical_page: int | None = None,
        logical_page: int | None = None,
        prev_chunk_id: str | None = None,
        next_chunk_id: str | None = None,
        cross_ref_chunk_ids: list[str] | None = None
    ):
        """Metadata associated with a text chunk."""
        self.doctype = doctype
        self.year = year
        self.company = company
        self.section = section
        self.subsection = subsection
        self.is_table = is_table
        self.table_json = table_json
        self.physical_page = physical_page
        self.logical_page = logical_page
        self.prev_chunk_id = prev_chunk_id
        self.next_chunk_id = next_chunk_id
        self.cross_ref_chunk_ids = cross_ref_chunk_ids or []


class Chunk:
    """Text chunk with metadata for vector database storage."""
    text: str
    metadata: ChunkMetadata
    id: str | None = None

    def __init__(self, text: str, metadata: ChunkMetadata, id: str | None = None):
        self.text = text
        self.metadata = metadata
        if id is not None:
            self.id = id
        else:
            hex_str = hashlib.md5(self.text.encode('utf-8')).hexdigest()
            self.id = str(uuid.UUID(hex=hex_str))

    def to_dict(self) -> dict:
        """Convert chunk to dictionary for storage."""
        return {
            'id': self.id,
            'text': self.text,
            'metadata': {
                'doctype': self.metadata.doctype,
                'year': self.metadata.year,
                'company': self.metadata.company,
                'section': self.metadata.section,
                'subsection': self.metadata.subsection,
                'is_table': self.metadata.is_table,
                'table_json': self.metadata.table_json,
                'prev_chunk_id': self.metadata.prev_chunk_id,
                'next_chunk_id': self.metadata.next_chunk_id,
                'physical_page': self.metadata.physical_page,
                'logical_page': self.metadata.logical_page,
                'cross_ref_chunk_ids': self.metadata.cross_ref_chunk_ids
            }
        }

    def to_json(self) -> str:
        """Convert chunk to JSON string."""
        return json.dumps(self.to_dict(), indent=2)
