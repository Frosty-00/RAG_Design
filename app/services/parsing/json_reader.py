"""JSON reader — pretty-print the structure as text. Good enough for simple
config / record dumps; not optimized for huge JSON-array datasets."""
from __future__ import annotations

import json
from pathlib import Path

from app.services.parsing.base import Document, make_base_metadata


class JsonReader:
    MAX_BYTES = 2 * 1024 * 1024  # 2 MB hard cap to avoid OOM on giant dumps

    def load_data(self, file_path: str | Path) -> list[Document]:
        path = Path(file_path)
        raw = path.read_bytes()[: self.MAX_BYTES]
        try:
            obj = json.loads(raw)
            text = json.dumps(obj, ensure_ascii=False, indent=2)
        except Exception:
            # Not valid JSON? Treat as plain text.
            text = raw.decode("utf-8", errors="replace")
        if not text.strip():
            return []
        return [Document(
            text=text,
            metadata=make_base_metadata(file_path=path, parser="json"),
        )]
