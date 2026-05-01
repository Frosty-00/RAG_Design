"""Image reader — runs OCR over the file, emits one Document of the text."""
from __future__ import annotations

from pathlib import Path

from app.services.parsing.base import Document, make_base_metadata
from app.services.parsing.ocr import OCREngine


class ImageReader:
    def load_data(self, file_path: str | Path) -> list[Document]:
        path = Path(file_path)
        text = OCREngine.get().recognize(path)
        if not text.strip():
            return []
        return [Document(
            text=text,
            metadata=make_base_metadata(file_path=path, parser="image+ocr"),
        )]
