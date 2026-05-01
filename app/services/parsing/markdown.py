"""Markdown reader — pass-through.

We emit one Document with the raw markdown content. The downstream
`MarkdownNodeParser` understands heading semantics natively.
"""
from __future__ import annotations

from pathlib import Path

from app.services.parsing.base import Document, make_base_metadata


class MarkdownReader:
    def load_data(self, file_path: str | Path) -> list[Document]:
        path = Path(file_path)
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return []
        md = make_base_metadata(
            file_path=path,
            parser="markdown",
            section_idx=0,
        )
        return [Document(text=text, metadata=md)]
