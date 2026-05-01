"""PDF reader with smart OCR fallback.

Why PyMuPDF (`fitz`) and not pypdf?
    pypdf returns garbled glyph indices for any Chinese PDF whose fonts
    lack a ToUnicode CMap. The user reports — `1.1 E5ER` / `N='VE+W...`
    style noise — were 100% reproducible on files exported from WPS / Word
    中文版 / many online converters. PyMuPDF wraps MuPDF, which has full
    CJK CMap support and decodes those files correctly.

Per page we:
  1. extract text via PyMuPDF
  2. detect whether the extracted text is *intelligible* (enough CJK or
     ASCII-letter content vs. punctuation/symbols). If not, treat it as
     missing and OCR-render the page instead.
  3. emit one Document per page (or skip blank pages).

OCR uses PyMuPDF's `page.get_pixmap()` to rasterise the entire page
(better than pypdf's `page.images` which only catches embedded raster
images and misses vector text entirely).
"""
from __future__ import annotations

import re
from pathlib import Path

import fitz  # PyMuPDF

from app.core.config import settings
from app.core.logger import get_logger
from app.services.parsing.base import Document, make_base_metadata
from app.services.parsing.ocr import OCREngine

log = get_logger(__name__)

# If a page yields less than this many "useful" chars (CJK + ASCII letters),
# we treat the extraction as failed and fall back to rasterise+OCR.
USEFUL_CHARS_THRESHOLD = 50

# OCR rendering: 3× zoom (~216 DPI) — empirically needed for long
# Chinese lines with mixed font weights. 2× was missing wraparound
# segments on bulleted definitions ("抖音小店平台:指供交易双方独立..."
# with the middle "方独立..." dropping out). Higher zoom uses more RAM
# per page but the user-reported failure mode is fully recovered.
OCR_RENDER_ZOOM = 3.0

# CJK ranges + ASCII letters. Anything outside this set is treated as
# punctuation/noise when judging text quality.
_USEFUL_CHAR_RE = re.compile(
    r"[A-Za-z"
    r"一-鿿"   # CJK Unified Ideographs
    r"㐀-䶿"   # CJK Extension A
    r"぀-ゟ"   # Hiragana
    r"゠-ヿ"   # Katakana
    r"가-힯"   # Hangul Syllables
    r"]"
)


def _useful_char_count(text: str) -> int:
    """Count letters / CJK characters — anything that conveys meaning.
    A page full of `N='VE+W...` returns near zero even though `len(text)`
    might be hundreds, which is exactly the signal we need to trigger
    OCR fallback."""
    return len(_USEFUL_CHAR_RE.findall(text))


class PdfReader:
    """PDF reader with three tiers, in priority order:

      1. **LlamaParse** (cloud, layout-aware) — used whenever
         `LLAMA_CLOUD_API_KEY` is set. Output is Markdown that retains
         headings + tables + bullets, which is what real Chinese
         enterprise PDFs need.
      2. **PyMuPDF text extraction** — fast, good for most digital PDFs.
      3. **PyMuPDF page rasterise + RapidOCR** — fallback for scanned
         pages or when extracted text is garbled / sparse.

    Errors in (1) automatically fall through to (2)+(3) so a missing
    quota or transient network blip doesn't block ingestion.
    """

    def __init__(self, useful_char_threshold: int = USEFUL_CHARS_THRESHOLD) -> None:
        self.useful_threshold = useful_char_threshold

    def load_data(self, file_path: str | Path) -> list[Document]:
        path = Path(file_path)

        # ── Tier 1: LlamaParse (preferred) ───────────────────────────
        from app.services.parsing.llamaparse import LlamaParseReader
        if LlamaParseReader.is_configured():
            try:
                return LlamaParseReader().load_data(path)
            except Exception as e:  # noqa: BLE001
                log.warning("pdf.llamaparse_fallback",
                            source=path.name, err=str(e))
                # fall through to local stack

        # ── Tier 2 + 3: PyMuPDF (+ OCR fallback) ─────────────────────
        return self._load_local(path)

    def _load_local(self, path: Path) -> list[Document]:
        docs: list[Document] = []
        ocr_pages = 0
        garbled_pages = 0

        force_ocr = settings.pdf_force_ocr
        with fitz.open(str(path)) as pdf:
            for i, page in enumerate(pdf, start=1):
                if force_ocr:
                    # Skip PyMuPDF text extraction entirely; OCR is the
                    # source of truth. Catches PDFs whose text layer
                    # silently drops bullet/heading lines (custom-font
                    # bold styles with broken CMap) — those still render
                    # correctly visually so OCR sees them.
                    ocr_text = self._ocr_page(page, page_no=i, source=path.name)
                    if ocr_text:
                        text = ocr_text
                        parser_tag = "pdf+ocr+forced"
                        ocr_pages += 1
                    else:
                        text = ""
                        parser_tag = "pdf+ocr+forced"
                    if not text:
                        continue
                    md = make_base_metadata(
                        file_path=path, parser=parser_tag,
                        page=i, section_idx=i - 1,
                    )
                    docs.append(Document(text=text, metadata=md))
                    continue

                text = (page.get_text("text") or "").strip()
                parser_tag = "pdf"

                useful = _useful_char_count(text)
                if useful < self.useful_threshold:
                    # Two cases land here:
                    #  (a) genuinely blank / scanned page — `text` empty
                    #  (b) broken-CMap text extraction — `text` is long but
                    #      `useful` is tiny (the noise reported by the user)
                    if text:
                        garbled_pages += 1
                        log.warning("pdf.text_low_quality",
                                    source=path.name, page=i,
                                    text_len=len(text), useful=useful)

                    ocr_text = self._ocr_page(page, page_no=i, source=path.name)
                    if _useful_char_count(ocr_text) > useful:
                        text = ocr_text
                        parser_tag = "pdf+ocr"
                        ocr_pages += 1

                if not text:
                    continue  # blank page → skip

                md = make_base_metadata(
                    file_path=path,
                    parser=parser_tag,
                    page=i,
                    section_idx=i - 1,
                )
                docs.append(Document(text=text, metadata=md))

        if ocr_pages or garbled_pages:
            log.info("pdf.parse_summary",
                     source=path.name,
                     ocr_pages=ocr_pages,
                     garbled_recovered=garbled_pages)
        return docs

    # ----------------------------------------------------------- OCR helper

    def _ocr_page(self, page: fitz.Page, *, page_no: int, source: str) -> str:
        """Rasterise the whole page and OCR it. Catches both true scans and
        broken-CMap text PDFs (rendering doesn't depend on the CMap — the
        page renders correctly visually, only the text-extraction path is
        broken)."""
        try:
            mat = fitz.Matrix(OCR_RENDER_ZOOM, OCR_RENDER_ZOOM)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            png_bytes = pix.tobytes("png")
        except Exception as e:  # noqa: BLE001
            log.warning("pdf.render_failed", source=source, page=page_no, err=str(e))
            return ""

        try:
            return OCREngine.get().recognize(png_bytes)
        except Exception as e:  # noqa: BLE001
            log.warning("pdf.ocr_failed", source=source, page=page_no, err=str(e))
            return ""
