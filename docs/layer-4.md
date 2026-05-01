# Layer 4 — 解析与分块

## 产出

| 文件 | 内容 |
|---|---|
| `app/services/parsing/base.py` | `Document` 类型别名 + `make_base_metadata` + `validate_documents` 契约 |
| `app/services/parsing/pdf.py` | `PdfReader`：pypdf 提文本，每页 < 50 字符触发 OCR fallback |
| `app/services/parsing/docx.py` | `DocxReader`：把 heading 重组为 markdown `#` 前缀；表格 → markdown 表 |
| `app/services/parsing/markdown.py` | `MarkdownReader`：pass-through |
| `app/services/parsing/plain.py` | `PlainTextReader`：txt pass-through |
| `app/services/parsing/ocr.py` | `OCREngine` 单例：RapidOCR ONNX，接受 PIL/bytes/np.ndarray |
| `app/services/parsing/router.py` | `parse_file(path)` 按扩展名分发 |
| `app/services/chunking.py` | `MarkdownNodeParser → SentenceSplitter → MetadataEnricher`；`chunk_documents()` 便捷入口 |
| `tests/unit/test_parsing.py` | 12 个单测 |

## OCR 引擎选择（偏离 plan §1：PaddleOCR → RapidOCR）

**理由**：
- `paddlepaddle` 在 Windows 上要装 ~500 MB + C++ 运行库，CI 与本机环境踩坑率高
- RapidOCR 是 PaddleOCR **原模型转 ONNX**（同源），靠 onnxruntime，体积 ~50 MB，pip 一键装
- 识别质量等同；接口（输入图像/输出 `[box, text, score]`）几乎一致
- 后续 Layer 15a 启用 ONNX 加速时，OCR 已经是 ONNX，零额外工作

文档化在此处而不是改 plan，保留"plan 是意图、layer 文档是事实"这一区分。

## 关键设计

### MetadataEnricher 双职责

1. **填稳定标识**：`chunk_id = "{doc_id}:c{idx:04d}"`，`prev_chunk_id` / `next_chunk_id` 形成双链表
2. **轻量 contextual retrieval**：把 breadcrumbs（`[H1 > H2 > H3]\n`）prepend 到 chunk text，让 BGE-M3 的上下文中显式包含层级信息——这一招对中文 KB 召回有显著增益且零额外计算

### Document 输出契约

每个 reader 必填 metadata 键：`source / file_type / parser / section_idx`，`validate_documents` 在 router 出口强制检查。其余键（`page` / `breadcrumbs`）按 reader 能力可选。

### IngestionPipeline 仅含解析+分块

不带 embedder——Layer 5 在 Celery 任务中拼接 BGE-M3 transformation。这层保持纯解析，可独立单测、可按需替换。

## 验证结果（12/12 PASS）

```
$ pytest tests/unit/test_parsing.py -v
TestReaders::test_pdf_text_path ..................... PASSED   ← 2 页 PDF，每页一 Document
TestReaders::test_pdf_ocr_fallback .................. PASSED   ← 扫描 PDF parser="pdf+ocr"
TestReaders::test_docx .............................. PASSED   ← #/##/### + 表格
TestReaders::test_markdown .......................... PASSED
TestReaders::test_txt ............................... PASSED
TestReaders::test_unsupported_extension ............. PASSED
TestReaders::test_missing_file ...................... PASSED
TestOCREngine::test_recognize_image ................. PASSED
TestChunking::test_pipeline_builds .................. PASSED
TestChunking::test_chunks_have_required_metadata .... PASSED   ← chunk_id/index/doc_id/prev/next/breadcrumbs
TestChunking::test_breadcrumbs_prepended ............ PASSED   ← text 以 [crumb] 开头
TestChunking::test_linked_list_neighbours ........... PASSED   ← 双链表完整

12 passed in 4.99s
```

DoD 核对：
- [x] PDF / DOCX / Markdown / TXT 四种格式全部跑通
- [x] 纯文 PDF 不误触发 OCR（`parser=pdf`，无 ocr 标记）
- [x] 扫描 PDF 走 OCR 路径，输出 `parser=pdf+ocr` 且文本含识别结果
- [x] chunks 含正确 `chunk_id` / `chunk_index` / `prev_chunk_id` / `next_chunk_id` / `breadcrumbs`

## 注意 / 后续 layer

- **Layer 5 接入**：在 IngestionPipeline 末尾加自定义 `EmbedTransformation`，调 `BGEM3Embedder.encode([n.text])` 写 `node.embedding` 与 `node.metadata["sparse"]`，然后由 `MilvusRepository.insert(...)` 写库
- **MarkdownNodeParser 行为**：当输入 markdown 内容很短时，可能不切节点（仅一节），这正常；SentenceSplitter 后续会再切
- **`unstructured` 兜底未实现**：plan 提的 ppt/xlsx/html 罕见格式现阶段拒绝（router 抛 `UnsupportedFileType`）。Layer 15 视实际需求再加
- **OCR 准确率**：当前用 RapidOCR 默认中英文模型；遇到表格/公式/竖排日文等极端版式会下降——若实际数据有这类内容，Layer 15 可加 PP-Structure 或 MinerU
