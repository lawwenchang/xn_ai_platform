#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bank_reconcile_engine.py 补丁：台账泛化检查放宽（数值列推断兜底）"""
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "core" / "bank_reconcile_engine.py"
src = P.read_text(encoding="utf-8").replace("\r\n", "\n")

OLD = '''    # 通用台账：存在日期语义 + 金额语义（借贷对/收支对/带符号金额）即纳入对账范畴
    try:
        from core.column_semantics import detect_column_roles
        roles = detect_column_roles(df)
        has_date = "date" in roles
        has_amount = any(r in roles for r in ("amount", "debit", "credit"))
        if has_date and has_amount:
            return GENERIC_LEDGER
    except Exception:
        pass
    return "unknown"'''

NEW = '''    # 通用台账：存在日期语义 + 金额语义（借贷对/收支对/带符号金额，
    # 名称未命中时按数值型列推断）即纳入对账范畴
    try:
        from core.column_semantics import detect_column_roles, infer_amount_columns
        roles = detect_column_roles(df)
        has_date = "date" in roles
        has_amount = any(r in roles for r in ("amount", "debit", "credit")) \\
            or bool(infer_amount_columns(df, max_cols=1))
        if has_date and has_amount:
            return GENERIC_LEDGER
    except Exception:
        pass
    return "unknown"'''

assert src.count(OLD) == 1, f"命中 {src.count(OLD)} 次"
src = src.replace(OLD, NEW)
P.write_text(src, encoding="utf-8", newline="\n")
import ast
ast.parse(src)
print("泛化检查放宽完成，AST OK")
