"""Plain text (.txt) reader."""
from __future__ import annotations

from pathlib import Path

from app.services.parsing.base import Document, make_base_metadata


class PlainTextReader:
    def load_data(self, file_path: str | Path) -> list[Document]:
        path = Path(file_path)
        text = path.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            return []
        return [Document(
            text=text,
            metadata=make_base_metadata(file_path=path, parser="text"),
        )]
