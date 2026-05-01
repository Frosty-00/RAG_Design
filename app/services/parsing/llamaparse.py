"""LlamaParse-backed PDF reader.

Why this is the *primary* PDF parser when an API key is set:
    Local extraction (pypdf / PyMuPDF / RapidOCR) silently drops content
    on real-world Chinese enterprise PDFs — bullet-list term definitions,
    bold-font headings with broken CMap, multi-column layouts. We hit
    every one of these on the user's `抖音电商规则总则.pdf`. LlamaParse
    runs a layout-aware vision model server-side and returns clean
    Markdown that preserves headings / tables / bullet structure, which
    plugs straight into our existing `MarkdownNodeParser` chunking path.

Failure mode: any error (network, quota, malformed PDF) falls back to
the local PyMuPDF reader at the call site (see `pdf.py`).

Output shape: one Document per source page, so chunking can attach a
`page` number to each chunk and citations stay precise.
"""
from __future__ import annotations

from pathlib import Path

from app.core.config import settings
from app.core.logger import get_logger
from app.services.parsing.base import Document, make_base_metadata

log = get_logger(__name__)


# Marker LlamaParse inserts between pages when split_by_page=True is
# unavailable (older lib versions). We split on it as a fallback.
_PAGE_BREAK_MARKERS = ("\n---\n", "\f")


class LlamaParseReader:
    """Drop-in shape: `load_data(file_path) -> list[Document]`.

    Constructed lazily so the module can be imported without an API key
    (callers should check `is_configured()` first).
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or settings.llama_cloud_api_key
        self._parser = None

    @staticmethod
    def is_configured() -> bool:
        return bool(settings.llama_cloud_api_key)

    def _build_parser(self):
        if self._parser is not None:
            return self._parser
        # Local import — keeps the module importable when llama-parse
        # is not yet installed (during partial dev environment setup).
        from llama_parse import LlamaParse

        self._parser = LlamaParse(
            api_key=self._api_key,
            result_type=settings.llamaparse_result_type,
            language=settings.llamaparse_language,
            # Page-level Documents → easier to attach page metadata.
            split_by_page=True,
            # Don't let one slow PDF hold the whole worker. 10 min is the
            # service's typical p99 for a 50-page complex doc.
            verbose=False,
        )
        return self._parser

    def load_data(self, file_path: str | Path) -> list[Document]:
        path = Path(file_path)
        parser = self._build_parser()
        log.info("llamaparse.start", source=path.name)

        try:
            li_docs = parser.load_data(str(path))
        except Exception as e:
            log.warning("llamaparse.failed", source=path.name, err=str(e))
            raise

        # llama-parse returns LlamaIndex Document objects; we re-wrap
        # into our own Document type with normalised metadata so the
        # rest of the pipeline doesn't see two flavours.
        out: list[Document] = []
        for i, ld in enumerate(li_docs, start=1):
            text = (ld.text or "").strip()
            if not text:
                continue
            md = make_base_metadata(
                file_path=path,
                parser="llamaparse",
                page=(ld.metadata.get("page_label")
                      or ld.metadata.get("page")
                      or i),
                section_idx=i - 1,
            )
            out.append(Document(text=text, metadata=md))

        # Some lib versions return a single concatenated Document instead
        # of one per page. Detect and split on page-break markers so
        # downstream `page` metadata isn't all stuck at 1.
        if len(out) == 1 and any(m in out[0].text for m in _PAGE_BREAK_MARKERS):
            out = self._split_concat_doc(out[0], path)

        log.info("llamaparse.done", source=path.name, pages=len(out))
        return out

    @staticmethod
    def _split_concat_doc(doc: Document, path: Path) -> list[Document]:
        text = doc.text
        for marker in _PAGE_BREAK_MARKERS:
            if marker in text:
                parts = text.split(marker)
                break
        else:
            return [doc]
        out: list[Document] = []
        for i, part in enumerate(parts, start=1):
            t = part.strip()
            if not t:
                continue
            md = make_base_metadata(
                file_path=path, parser="llamaparse",
                page=i, section_idx=i - 1,
            )
            out.append(Document(text=t, metadata=md))
        return out
