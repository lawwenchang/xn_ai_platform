#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""routes.py 补丁 E2：Catalog 语义富化（LLM 智能规划）+ 快车道接受台账"""
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "api" / "routes.py"
src = P.read_text(encoding="utf-8").replace("\r\n", "\n")


def rep(old: str, new: str, tag: str):
    global src
    assert src.count(old) == 1, f"[{tag}] 命中 {src.count(old)} 次而非 1 次"
    src = src.replace(old, new)
    print(f"  [PATCH] {tag}")


# ── 1. Catalog 语义富化 ────────────────────────────────────────
rep('''def _format_catalog_for_prompt(catalog: AssetCatalog) -> str:
    lines = [f"文件总数: {catalog.total_files}", "=== 文件清单 ==="]
    for f in catalog.files:
        lines.append(f"\\n文件: {f['filename']}")
        if "columns" in f:
            cols = [f"{col['name']}({col['dtype']})" for col in f["columns"]]
            lines.append(f"  列: {', '.join(cols)}")
    return "\\n".join(lines)''',
    '''def _format_catalog_for_prompt(catalog: AssetCatalog) -> str:
    """Data Catalog → LLM 提示文本。

    富化内容（让 LLM 看着真实语义规划，而不是照抄示例幻觉）：
    - 每文件：列名(dtype) + 语义角色（确定性识别）+ 文档文本预览/内嵌表格列
    - 跨表：连接键建议（已排除序号/行号等无意义键）
    - 顶部硬约束：source_file 与列名必须来自本目录
    """
    lines = [
        f"文件总数: {catalog.total_files}",
        "【硬约束】Load 的 source_file 和算子引用的列名必须逐字来自下方文件清单，"
        "严禁使用任何示例中的文件名/列名；严禁把'序号/编号/行号'用作连接键。",
        "=== 文件清单 ===",
    ]
    # 语义角色（列名驱动，确定性；与实际业务含义不符时 LLM 可按需修正并说明）
    try:
        import pandas as _pd
        from core.column_semantics import (detect_column_roles,
                                           is_meaningless_key, suggest_join_keys)
        _sem_ok = True
    except Exception:
        _sem_ok = False
    frames = {}
    for f in catalog.files:
        lines.append(f"\\n文件: {f['filename']}")
        if f.get("kind") and f["kind"] != "table":
            lines.append(f"  类型: {f['kind']}")
        if "columns" in f and f["columns"]:
            cols = [f"{col['name']}({col['dtype']})" for col in f["columns"]]
            lines.append(f"  列: {', '.join(cols)}")
            if _sem_ok:
                try:
                    _df = _pd.DataFrame(
                        columns=[str(col["name"]) for col in f["columns"]])
                    frames[f["filename"]] = _df
                    roles = detect_column_roles(_df)
                    if roles:
                        lines.append("  语义角色: " + ", ".join(
                            f"{r}→{c}" for r, c in roles.items()))
                    mk = [c for c in _df.columns if is_meaningless_key(c)]
                    if mk:
                        lines.append(f"  无意义键（禁止连接）: {', '.join(mk)}")
                except Exception:
                    pass
        if f.get("text_preview"):
            lines.append(f"  文本预览: {str(f['text_preview'])[:200]}")
        if f.get("tables_columns") and f["tables_columns"]:
            lines.append(f"  文档内嵌表格列: {', '.join(str(c) for c in f['tables_columns'][0])}")
    # 跨表连接键建议（两两组合，最多 5 组）
    if _sem_ok and len(frames) >= 2:
        try:
            names = list(frames.keys())
            hints = []
            for i in range(len(names)):
                for j in range(i + 1, len(names)):
                    keys = suggest_join_keys(frames[names[i]], frames[names[j]])
                    if keys:
                        hints.append(f"{names[i]} × {names[j]}: "
                                     + ", ".join(f"{a}↔{b}" for a, b in keys))
                if len(hints) >= 5:
                    break
            if hints:
                lines.append("\\n=== 跨表连接键建议（供参考） ===")
                lines.extend(hints[:5])
        except Exception:
            pass
    return "\\n".join(lines)''',
    "Catalog 语义富化")

# ── 2. 快车道：台账也可作为账方 ────────────────────────────────
rep('''                t = detect_book_type(tables[0], f.name)
                if t == JOURNAL and journal is None:
                    journal = f
                elif t == BANK_STATEMENT and bank is None:
                    bank = f''',
    '''                t = detect_book_type(tables[0], f.name)
                if t in (JOURNAL, "generic_ledger") and journal is None:
                    journal = f  # 序时账或通用台账均可作为账方
                elif t == BANK_STATEMENT and bank is None:
                    bank = f''',
    "快车道接受通用台账")

P.write_text(src, encoding="utf-8", newline="\n")
import ast
ast.parse(src)
print("routes 补丁E2 完成，AST OK")
