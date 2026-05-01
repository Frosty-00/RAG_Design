"""Dispatch by file extension to the right reader."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from app.services.parsing.base import Document, validate_documents
from app.services.parsing.csv_reader import CsvReader, TsvReader
from app.services.parsing.docx import DocxReader
from app.services.parsing.html import HtmlReader
from app.services.parsing.image import ImageReader
from app.services.parsing.json_reader import JsonReader
from app.services.parsing.markdown import MarkdownReader
from app.services.parsing.pdf import PdfReader
from app.services.parsing.plain import PlainTextReader
from app.services.parsing.pptx import PptxReader
from app.services.parsing.xlsx import XlsxReader


class _Reader(Protocol):
    def load_data(self, file_path: str | Path) -> list[Document]: ...


# extension (lowercase, without dot) → reader factory
_READERS: dict[str, type[_Reader]] = {
    # Documents
    "pdf": PdfReader,
    "docx": DocxReader,
    "md": MarkdownReader,
    "markdown": MarkdownReader,
    "txt": PlainTextReader,
    # Web
    "html": HtmlReader,
    "htm": HtmlReader,
    # Images (OCR)
    "png": ImageReader,
    "jpg": ImageReader,
    "jpeg": ImageReader,
    "bmp": ImageReader,
    "webp": ImageReader,
    # Spreadsheets
    "xlsx": XlsxReader,
    "csv": CsvReader,
    "tsv": TsvReader,
    # Slides
    "pptx": PptxReader,
    # Structured data
    "json": JsonReader,
}


class UnsupportedFileType(ValueError):
    pass


def parse_file(file_path: str | Path) -> list[Document]:
    """Parse `file_path` into a list of LlamaIndex Documents.

    Raises UnsupportedFileType if extension is unknown.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(path)

    ext = path.suffix.lstrip(".").lower()
    reader_cls = _READERS.get(ext)
    if reader_cls is None:
        raise UnsupportedFileType(f"no reader registered for .{ext}")

    docs = reader_cls().load_data(path)
    validate_documents(docs)
    return docs
