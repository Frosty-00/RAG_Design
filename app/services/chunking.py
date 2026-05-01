"""Chunking pipeline — Documents → TextNodes ready for embedding/insertion.

Composition (uses LlamaIndex low-level transformations only):
    1. MarkdownNodeParser    — heading hierarchy; emits one node per top-level chunk
                                with `header_path` metadata
    2. SentenceSplitter      — splits each node to ~CHUNK_SIZE tokens with overlap
    3. MarkdownTableMerger   — re-stitches tables that the splitter cut apart so
                                row-to-column alignment isn't shifted between chunks
    4. MetadataEnricher      — fills in chunk_id, chunk_index, prev/next_chunk_id,
                                breadcrumbs (from MarkdownNodeParser's header_path),
                                and prepends breadcrumbs to node.text for
                                lightweight contextual retrieval

The full IngestionPipeline (which also runs the embedder) is built in Layer 5.
This layer exposes `build_chunk_pipeline()` returning the parser-only
transformations so it can be unit-tested deterministically.
"""
from __future__ import annotations

from typing import Sequence

from llama_index.core.ingestion import IngestionPipeline
from llama_index.core.node_parser import MarkdownNodeParser, SentenceSplitter
from llama_index.core.schema import BaseNode, Document, TextNode, TransformComponent

from app.core.config import settings
from app.core.logger import get_logger

log = get_logger(__name__)


def _looks_like_table_line(line: str) -> bool:
    """A line that's part of a markdown table.

    SentenceSplitter sometimes cuts inside a single row, so we accept any
    non-blank line containing `|` — not just rows that *start* with `|`.
    This catches orphan tails like "| 不少于 18 个月 |" that are really the
    trailing cell of the previous chunk's last row.
    """
    s = line.strip()
    return bool(s) and ("|" in s)


def _split_table_prefix(text: str) -> tuple[str, str]:
    """Peel off the leading run of table-looking lines; return (prefix, rest).

    Empty prefix means `text` doesn't begin with a table fragment.
    """
    lines = text.split("\n")
    end = 0
    while end < len(lines) and _looks_like_table_line(lines[end]):
        end += 1
    if end == 0:
        return "", text
    return "\n".join(lines[:end]), "\n".join(lines[end:])


def _ends_inside_table(text: str) -> bool:
    """True iff the text's last non-empty line is part of a markdown table."""
    for line in reversed(text.split("\n")):
        if not line.strip():
            continue
        return _looks_like_table_line(line)
    return False


def _reflow_split_rows(text: str) -> str:
    """Glue rows that the splitter cut mid-line.

    Pattern that comes out of SentenceSplitter:
        "| 类别 07 | some long descriptive text, cut at"   (line A — starts with `|` but doesn't end with `|`)
        "| 不少于 7 年 |"                                  (line B — orphan trailing cell of an EARLIER row that the
                                                            chunk_overlap re-injected)
        "| 类别 07 | ...complete row | 不少于 8 年 |"        (line C — the actual full row 07)
    Heuristic: if line A starts with `|` but doesn't end with `|`, AND line B
    is a *single-cell* trailing cell (`| ... |` and no `|` in the middle), drop B
    and stitch A onto whichever row C completes the same key.
    """
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        # Case: line starts with `|` but doesn't end with `|` → mid-row cut
        if (
            stripped.startswith("|")
            and not stripped.endswith("|")
            and i + 1 < n
        ):
            nxt_stripped = lines[i + 1].strip()
            # If the next line is a single trailing-cell `| ... |` with nothing
            # before the leading pipe and the pipe count is 2, treat it as the
            # orphan tail of an EARLIER row (overlap echo) — drop it. Then if
            # the line *after that* is the real continuation, keep stitching.
            if (
                nxt_stripped.startswith("|")
                and nxt_stripped.endswith("|")
                and nxt_stripped.count("|") == 2
            ):
                # orphan single-cell row → drop, stitch line with the line after
                if i + 2 < n:
                    cont = lines[i + 2].strip()
                    if cont.startswith("|"):
                        # whichever row carries the full content already
                        # (overlap also re-emits the full row); just drop the
                        # half-row `line` and let the full row through.
                        i += 1   # skip orphan
                        i += 1   # skip the cut half-row (full version follows)
                        continue
                # No good continuation — drop the orphan, keep the half-row
                out.append(line)
                i += 2
                continue
        out.append(line)
        i += 1
    return "\n".join(out)


def _dedupe_consecutive_table_rows(text: str) -> str:
    """If two consecutive lines are identical full table rows (overlap echo),
    keep one. Cheap dedupe that doesn't touch unique rows."""
    lines = text.split("\n")
    out: list[str] = []
    for line in lines:
        s = line.strip()
        if (
            out
            and s == out[-1].strip()
            and s.startswith("|")
            and s.endswith("|")
            and s.count("|") >= 3
        ):
            continue
        out.append(line)
    return "\n".join(out)


class MarkdownTableMerger(TransformComponent):
    """Re-stitch markdown tables that `SentenceSplitter` chopped in half.

    Background: SentenceSplitter respects sentence boundaries but a markdown
    table row has no sentence terminator, so it gets cut at arbitrary token
    counts. The pathological case is a chunk that ends with the *body* of one
    row and starts the next chunk with the *trailing cell* of the previous
    row — to a downstream LLM the row keys and values look misaligned, which
    causes confident wrong answers (e.g. "row X = value-of-row-Y").

    Fix: while a node ends inside a table, peel its successor's leading run
    of `|`-rows and append them. If the successor becomes empty, drop it.
    """

    def __call__(self, nodes, **kwargs):  # type: ignore[override]
        if not nodes:
            return list(nodes)

        # Group by source so we don't cross-merge unrelated documents
        per_doc: dict[str, list[BaseNode]] = {}
        order: list[str] = []
        for n in nodes:
            key = n.metadata.get("doc_id") or n.metadata.get("source") or "unknown"
            if key not in per_doc:
                order.append(key)
                per_doc[key] = []
            per_doc[key].append(n)

        merged_total: list[BaseNode] = []
        for key in order:
            group = per_doc[key]
            out: list[BaseNode] = []
            i = 0
            while i < len(group):
                cur = group[i]
                j = i + 1
                while j < len(group) and _ends_inside_table(cur.text):
                    nxt = group[j]
                    prefix, rest = _split_table_prefix(nxt.text)
                    if not prefix:
                        break  # next chunk doesn't start with table — stop
                    cur.text = cur.text + "\n" + prefix
                    if rest.strip():
                        nxt.text = rest
                        break  # successor still has non-table content; keep it
                    # Successor fully absorbed — skip it entirely
                    j += 1
                # Final pass on cur: drop overlap echoes + reflow split rows
                cur.text = _reflow_split_rows(cur.text)
                cur.text = _dedupe_consecutive_table_rows(cur.text)
                out.append(cur)
                i = j
            merged_total.extend(out)
        return merged_total


class MetadataEnricher(TransformComponent):
    """Final post-processing: assign stable chunk_id + linked-list neighbours,
    propagate breadcrumbs, and prepend them to the node text.

    Stateless across calls (idempotent over its inputs).
    """

    doc_id_key: str = "doc_id"

    def __call__(self, nodes: Sequence[BaseNode], **kwargs) -> list[BaseNode]:  # type: ignore[override]
        # Group by source doc to preserve correct chunk_index ordering
        out: list[BaseNode] = []
        # nodes from LlamaIndex retain order; we still group defensively
        per_doc: dict[str, list[BaseNode]] = {}
        for n in nodes:
            doc_id = n.metadata.get(self.doc_id_key) or n.metadata.get("source") or "unknown"
            per_doc.setdefault(doc_id, []).append(n)

        for doc_id, group in per_doc.items():
            n_chunks = len(group)
            for idx, node in enumerate(group):
                # MarkdownNodeParser stores headers under 'header_path' / 'Header_*'
                breadcrumbs = _extract_breadcrumbs(node)
                node.metadata["breadcrumbs"] = breadcrumbs
                node.metadata["chunk_index"] = idx
                node.metadata["chunk_count"] = n_chunks

                # Stable chunk_id: doc_id (and version, when present) come from
                # Layer 5 via Document.metadata; chunking unit tests omit version.
                node.metadata["doc_id"] = doc_id
                version = node.metadata.get("doc_version")
                if version is not None:
                    chunk_id = f"{doc_id}:v{version}:c{idx:04d}"
                else:
                    chunk_id = f"{doc_id}:c{idx:04d}"
                node.metadata["chunk_id"] = chunk_id
                if isinstance(node, TextNode):
                    node.id_ = chunk_id
                    # Lightweight contextual retrieval: prepend breadcrumbs
                    if breadcrumbs and not node.text.startswith("["):
                        crumb = " > ".join(breadcrumbs)
                        node.text = f"[{crumb}]\n{node.text}"

            for idx, node in enumerate(group):
                node.metadata["prev_chunk_id"] = (
                    group[idx - 1].metadata["chunk_id"] if idx > 0 else None
                )
                node.metadata["next_chunk_id"] = (
                    group[idx + 1].metadata["chunk_id"] if idx + 1 < n_chunks else None
                )
                out.append(node)
        return out


def _extract_breadcrumbs(node: BaseNode) -> list[str]:
    """LlamaIndex's MarkdownNodeParser writes header_path as 'H1/H2/...' or
    individual Header_1, Header_2 keys (varies by version). We accept both."""
    md = node.metadata
    if isinstance(md.get("header_path"), str):
        return [s for s in md["header_path"].split("/") if s]
    crumbs = []
    for i in range(1, 7):
        v = md.get(f"Header_{i}") or md.get(f"header_{i}")
        if v:
            crumbs.append(str(v))
    return crumbs


def chunk_documents(docs: list[Document]) -> list[BaseNode]:
    """Convenience: run the chunking pipeline (no embedder) over `docs`."""
    pipeline = build_chunk_pipeline()
    return pipeline.run(documents=docs)


def build_chunk_pipeline(
    *,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> IngestionPipeline:
    """Build the parser-only IngestionPipeline. Layer 5 will wrap this and
    append the embedder + vector store transforms."""
    return IngestionPipeline(
        transformations=[
            MarkdownNodeParser(),
            SentenceSplitter(
                chunk_size=chunk_size or settings.chunk_size,
                chunk_overlap=chunk_overlap or settings.chunk_overlap,
            ),
            MarkdownTableMerger(),
            MetadataEnricher(),
        ]
    )
