"""HTML reader — bs4 based, preserves heading hierarchy as markdown."""
from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup

from app.services.parsing.base import Document, make_base_metadata

_DROP_TAGS = ("script", "style", "noscript", "nav", "footer", "iframe", "svg")


class HtmlReader:
    def load_data(self, file_path: str | Path) -> list[Document]:
        path = Path(file_path)
        raw = path.read_text(encoding="utf-8", errors="replace")
        soup = BeautifulSoup(raw, "lxml")

        for tag in soup(list(_DROP_TAGS)):
            tag.decompose()

        # Walk the DOM in document order; emit `# heading` for h1-h6 and a
        # plain paragraph for everything else.
        out_lines: list[str] = []
        for el in soup.find_all(True):
            name = el.name.lower()
            text = (el.get_text(separator=" ", strip=True) or "").strip()
            if not text:
                continue
            if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                level = int(name[1])
                out_lines.append(f"{'#' * level} {text}")
            elif name in {"p", "li", "blockquote", "td", "th"}:
                out_lines.append(text)

        # Fallback: nothing structured? dump body text whole
        if not out_lines:
            body = soup.body or soup
            text = body.get_text(separator="\n", strip=True)
            if text:
                out_lines.append(text)

        if not out_lines:
            return []

        full = "\n\n".join(out_lines)
        title = (soup.title.get_text(strip=True) if soup.title else None)
        md = make_base_metadata(
            file_path=path,
            parser="html",
            extra={"title": title} if title else None,
        )
        return [Document(text=full, metadata=md)]
