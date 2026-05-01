"""OCR engine wrapper.

Implementation detail: we use **RapidOCR** (ONNX port of PaddleOCR's models)
instead of `paddleocr` itself. Same recognition quality (it's literally the
PaddleOCR weights converted to ONNX), but pip-only, no `paddlepaddle`
binary, Windows-friendly, ~50 MB total.

This deviation from plan §1 is intentional — see docs/layer-4.md.
"""
from __future__ import annotations

import re
import threading
from io import BytesIO
from pathlib import Path
from typing import ClassVar

import numpy as np
from PIL import Image

from app.core.logger import get_logger

log = get_logger(__name__)


class OCREngine:
    """RapidOCR singleton.

    Loads ONNX models on first use (~80 MB download to user's home cache
    on first run; afterwards instant).
    """

    _instance: ClassVar["OCREngine | None"] = None
    _lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self) -> None:
        from rapidocr_onnxruntime import RapidOCR

        log.info("ocr.loading")
        self._ocr = RapidOCR()
        log.info("ocr.loaded")

    @classmethod
    def get(cls) -> "OCREngine":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            cls._instance = None

    # ------------------------------------------------------------------- public

    def recognize(self, image: Image.Image | bytes | str | Path | np.ndarray) -> str:
        """OCR the image; return the concatenated recognized text.

        Visual line breaks are softened: a line that doesn't end with a
        sentence-final punctuation is joined to the next line (no
        separator) — fixes the "chunked mid-bullet" bug for Chinese PDFs
        whose definitions wrap across visual lines, e.g.
            "抖音小店平台:指供交易双"  ← OCR returned this as one line
            "方独立开展网络交易活动..."  ← and this as the next
        Without joining, downstream SentenceSplitter saw the newline as
        a paragraph boundary and chunked between them, leaving the LLM
        only the cut-off prefix.
        """
        arr = _to_numpy_image(image)
        result, _ = self._ocr(arr)
        if not result:
            return ""
        # result: list of [box, text, confidence]
        lines = [line[1] for line in result if line and len(line) >= 2 and line[1]]
        return _stitch_wrapping_lines(lines)


# Sentence-final punctuation across CJK and Western: lines ending here
# are treated as real paragraph boundaries; everything else is joined.
# IMPORTANT: do NOT include `:` / `：` here — Chinese term definitions
# (`抖音小店平台:指供交易双方...`) place the term on a line ending in `:`
# and the body on subsequent lines; treating `:` as sentence end leaves
# the term-definition pair sliced apart. `;` / `；` are list separators
# and likewise shouldn't break a paragraph.
_SENTENCE_END_RE = re.compile(r"[。！？\.!?]\s*$")
# Heading-shaped lines (chapter / section markers): keep these on their
# own line even if they don't end in punctuation, so they remain
# breadcrumbs in the chunked output.
_HEADING_RE = re.compile(
    r"^(第[一二三四五六七八九十百千]+[章节条款部篇]"
    r"|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\."
    r"|\d+(\.\d+)+\s*[^\d]"
    r"|\d+\.\s*[一-龥A-Za-z]"
    r")"
)


def _stitch_wrapping_lines(lines: list[str]) -> str:
    """Join consecutive OCR lines that visually wrap a single sentence.

    Heuristic: keep a line break ONLY when (a) the previous line ends
    with sentence-final punctuation, OR (b) the next line looks like a
    new heading. Everything else is concatenated with no separator —
    matches Chinese typography (no spaces between characters).
    """
    if not lines:
        return ""
    out: list[str] = [lines[0]]
    for nxt in lines[1:]:
        prev = out[-1]
        if _SENTENCE_END_RE.search(prev) or _HEADING_RE.match(nxt):
            out.append(nxt)
        else:
            # Join with no separator — Chinese text doesn't use word
            # spaces. For lines that look ASCII-heavy (latin), we'd want
            # a space, but in mixed-content this is rare and a missing
            # space is much better than a spurious paragraph break.
            out[-1] = prev + nxt
    return "\n".join(out)


def _to_numpy_image(image) -> np.ndarray:
    """Coerce common image inputs into HxWx3 uint8 numpy."""
    if isinstance(image, np.ndarray):
        arr = image
    elif isinstance(image, Image.Image):
        if image.mode != "RGB":
            image = image.convert("RGB")
        arr = np.array(image)
    elif isinstance(image, (bytes, bytearray)):
        img = Image.open(BytesIO(image))
        if img.mode != "RGB":
            img = img.convert("RGB")
        arr = np.array(img)
    elif isinstance(image, (str, Path)):
        img = Image.open(image)
        if img.mode != "RGB":
            img = img.convert("RGB")
        arr = np.array(img)
    else:
        raise TypeError(f"OCR cannot accept {type(image).__name__}")
    if arr.ndim == 2:  # grayscale → RGB
        arr = np.stack([arr] * 3, axis=-1)
    return arr
