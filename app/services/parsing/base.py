"""Common types and metadata conventions for all readers.

Every Reader returns `list[Document]` with these required metadata keys:

    source        : original filename (str)
    file_type     : extension w/o dot (e.g. "pdf", "docx", "md")
    page          : 1-based page number, or None if N/A
    breadcrumbs   : list[str] of heading hierarchy (e.g. ["产品手册", "权限"])
    section_idx   : ordinal of the section within the file
    parser        : which reader produced it (e.g. "pdf", "pdf+ocr", "docx")

Downstream `MetadataEnricher` (chunking) layers in `doc_id`, `chunk_index`,
`prev_chunk_id` and `next_chunk_id`.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

# LlamaIndex Document is the unified intermediate representation
try:
    from llama_index.core.schema import Document  # noqa: F401  re-export
except ImportError:  # pragma: no cover
    from llama_index.core import Document  # type: ignore  # noqa: F401


# Required keys every parser must populate
REQUIRED_METADATA_KEYS = (
    "source", "file_type", "parser", "section_idx",
)


def make_base_metadata(
    *,
    file_path: str | Path,
    parser: str,
    page: int | None = None,
    breadcrumbs: list[str] | None = None,
    section_idx: int = 0,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    p = Path(file_path)
    md: dict[str, Any] = {
        "source": p.name,
        "file_type": p.suffix.lstrip(".").lower(),
        "page": page,
        "breadcrumbs": breadcrumbs or [],
        "section_idx": section_idx,
        "parser": parser,
        "ingested_at": time.time(),
    }
    if extra:
        md.update(extra)
    return md


def validate_documents(docs: list[Document]) -> None:
    """Sanity-check parser output. Raises AssertionError on contract violation."""
    for i, d in enumerate(docs):
        assert d.text is not None and len(d.text.strip()) > 0, \
            f"document {i} has empty text"
        for k in REQUIRED_METADATA_KEYS:
            assert k in d.metadata, f"document {i} missing metadata key {k!r}"
