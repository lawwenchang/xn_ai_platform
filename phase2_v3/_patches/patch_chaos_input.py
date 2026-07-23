#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chaos_input.py 补丁：支持 pdf/md/txt + 文档嗅探接入 Data Catalog"""
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "core" / "chaos_input.py"
src = P.read_text(encoding="utf-8").replace("\r\n", "\n")


def rep(old: str, new: str, tag: str):
    global src
    assert src.count(old) == 1, f"[{tag}] 命中 {src.count(old)} 次而非 1 次"
    src = src.replace(old, new)
    print(f"  [PATCH] {tag}")


rep('''SUPPORTED_EXTS = {".xlsx", ".xls", ".csv", ".docx", ".doc"}''',
    '''SUPPORTED_EXTS = {".xlsx", ".xls", ".csv", ".docx", ".doc", ".pdf", ".md", ".txt"}''',
    "SUPPORTED_EXTS 扩展 pdf/md/txt")

# 嗅探分支：文档格式走统一文档加载器
rep('''            # 嗅探表头（自动检测表头行）
            try:
                if file_path.suffix.lower() == ".csv":''',
    '''            # 文档格式（docx/doc/pdf/md/txt）：统一文档加载器嗅探
            if file_path.suffix.lower() in (".docx", ".doc", ".pdf", ".md", ".txt"):
                try:
                    from core.document_loader import sniff_document
                    info = sniff_document(file_path)
                    entry = {
                        "filename": file_path.name,
                        "original_path": str(file_path.name),
                        "size_bytes": file_size,
                        "kind": info.get("kind", "document"),
                        "rows_estimated": (info.get("tables_rows") or [0])[0]
                                          if info.get("tables_rows") else 0,
                        "columns": [],
                    }
                    if info.get("text_preview"):
                        entry["text_preview"] = info["text_preview"]
                    if info.get("tables_columns"):
                        entry["tables_columns"] = info["tables_columns"]
                        # 文档内嵌表格的列也暴露给 LLM（与 Excel 同权）
                        entry["columns"] = [
                            {"name": str(c), "dtype": "object", "null_count": 0,
                             "unique_count": 0, "sample_values": []}
                            for c in (info["tables_columns"][0] if info["tables_columns"] else [])
                        ]
                    if info.get("errors"):
                        entry["errors"] = info["errors"]
                    files_info.append(entry)
                except Exception as e:
                    files_info.append({
                        "filename": file_path.name,
                        "original_path": str(file_path.name),
                        "size_bytes": file_size,
                        "error": str(e),
                    })
                continue

            # 嗅探表头（自动检测表头行）
            try:
                if file_path.suffix.lower() == ".csv":''',
    "文档嗅探接入 Data Catalog")

P.write_text(src, encoding="utf-8", newline="\n")
import ast
ast.parse(src)
print("chaos_input 补丁完成，AST OK")
