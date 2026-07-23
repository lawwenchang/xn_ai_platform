#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bank_reconcile_engine.py 补丁：GENERIC_LEDGER 方向口径按映射列名决定"""
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "core" / "bank_reconcile_engine.py"
src = P.read_text(encoding="utf-8").replace("\r\n", "\n")

OLD = '''    # 通用台账按序时账方向归一（有借贷双列时）；金额单列时自动回退
    if book_type == GENERIC_LEDGER:
        book_type = JOURNAL
    if bank_type == GENERIC_LEDGER:
        bank_type = BANK_STATEMENT
    book_map = cfg.get("book_mapping") or auto_map_columns(book_df, book_type)
    bank_map = cfg.get("bank_mapping") or auto_map_columns(bank_df, bank_type)
    # LLM 映射兜底：关键角色（日期 + 金额类）缺失时，把真实列名交 LLM 判断
    # （台账格式多样，规则不可能穷举；LLM 不可用则维持规则结果，不臆造）
    if cfg.get("use_llm_mapping", True):
        try:
            from core.column_semantics import detect_roles_with_llm
            _llm = cfg.get("llm_callable")
            for side, df_, mp in (("book", book_df, book_map), ("bank", bank_df, bank_map)):
                need = ("date" not in mp) or not any(
                    r in mp for r in ("amount", "debit", "credit"))
                if need:
                    extra = detect_roles_with_llm(df_, f"{side}_table", llm_callable=_llm)
                    for role in ("date", "amount", "debit", "credit", "summary",
                                 "counterpart", "account", "voucher_no", "balance"):
                        if role not in mp and role in extra:
                            mp[role] = extra[role]
        except Exception:
            pass'''

NEW = '''    book_map = cfg.get("book_mapping") or auto_map_columns(book_df, book_type)
    bank_map = cfg.get("bank_mapping") or auto_map_columns(bank_df, bank_type)
    # LLM 映射兜底：关键角色（日期 + 金额类）缺失时，把真实列名交 LLM 判断
    # （台账格式多样，规则不可能穷举；LLM 不可用则维持规则结果，不臆造）
    if cfg.get("use_llm_mapping", True):
        try:
            from core.column_semantics import detect_roles_with_llm
            _llm = cfg.get("llm_callable")
            for side, df_, mp in (("book", book_df, book_map), ("bank", bank_df, bank_map)):
                need = ("date" not in mp) or not any(
                    r in mp for r in ("amount", "debit", "credit"))
                if need:
                    extra = detect_roles_with_llm(df_, f"{side}_table", llm_callable=_llm)
                    for role in ("date", "amount", "debit", "credit", "summary",
                                 "counterpart", "account", "voucher_no", "balance"):
                        if role not in mp and role in extra:
                            mp[role] = extra[role]
        except Exception:
            pass
    # 通用台账方向口径按映射列名决定：借方→簿记式（借=+）；
    # 收支式（兹付/付出/支出等）→ 流水式（收入=+）。金额单列时自动回退（正=流入）
    def _orient(mp, fallback):
        if "借" in str(mp.get("debit", "")):
            return JOURNAL
        return BANK_STATEMENT
    if book_type == GENERIC_LEDGER:
        book_type = _orient(book_map, JOURNAL)
    if bank_type == GENERIC_LEDGER:
        bank_type = _orient(bank_map, BANK_STATEMENT)'''

assert src.count(OLD) == 1, f"命中 {src.count(OLD)} 次"
src = src.replace(OLD, NEW)
P.write_text(src, encoding="utf-8", newline="\n")
import ast
ast.parse(src)
print("方向口径修正完成，AST OK")
