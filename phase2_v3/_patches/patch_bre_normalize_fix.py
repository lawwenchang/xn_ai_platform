#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bank_reconcile_engine.py 补丁：归一化使用方向判定后的类型（修复台账符号反转）"""
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "core" / "bank_reconcile_engine.py"
src = P.read_text(encoding="utf-8").replace("\r\n", "\n")

OLD = '''    # 2) 方向镜像归一化
    book_std = normalize_to_std(book_df, book_map, JOURNAL, cfg.get("book_file", ""))
    bank_std = normalize_to_std(bank_df, bank_map, BANK_STATEMENT, cfg.get("bank_file", ""))'''

NEW = '''    # 2) 方向镜像归一化（使用方向判定后的口径：簿记式 借-贷 / 收支式 收入-支出）
    _book_orient = book_type if book_type in (JOURNAL, BANK_STATEMENT) else JOURNAL
    _bank_orient = bank_type if bank_type in (JOURNAL, BANK_STATEMENT) else BANK_STATEMENT
    book_std = normalize_to_std(book_df, book_map, _book_orient, cfg.get("book_file", ""))
    bank_std = normalize_to_std(bank_df, bank_map, _bank_orient, cfg.get("bank_file", ""))'''

assert src.count(OLD) == 1, f"命中 {src.count(OLD)} 次"
src = src.replace(OLD, NEW)
P.write_text(src, encoding="utf-8", newline="\n")
import ast
ast.parse(src)
print("归一化方向修正完成，AST OK")
