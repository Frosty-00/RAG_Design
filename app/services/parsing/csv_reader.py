"""CSV / TSV reader — render as a markdown table."""
from __future__ import annotations

import csv
from pathlib import Path

from app.services.parsing.base import Document, make_base_metadata


class CsvReader:
    def __init__(self, delimiter: str = ",") -> None:
        self.delimiter = delimiter

    def load_data(self, file_path: str | Path) -> list[Document]:
        path = Path(file_path)
        with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.reader(f, delimiter=self.delimiter)
            rows = [[c.strip() for c in r] for r in reader if any(c.strip() for c in r)]
        if not rows:
            return []
        width = max(len(r) for r in rows)
        rows = [(r + [""] * (width - len(r)))[:width] for r in rows]

        out = ["| " + " | ".join(rows[0]) + " |",
               "| " + " | ".join(["---"] * width) + " |"]
        for r in rows[1:]:
            out.append("| " + " | ".join(c.replace("\n", " ") for c in r) + " |")
        text = "\n".join(out)
        return [Document(
            text=text,
            metadata=make_base_metadata(file_path=path, parser="csv"),
        )]


class TsvReader(CsvReader):
    def __init__(self) -> None:
        super().__init__(delimiter="\t")
