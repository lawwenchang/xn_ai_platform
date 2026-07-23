#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bank_reconcile_engine.py 补丁：通用台账泛化 + LLM 映射兜底"""
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "core" / "bank_reconcile_engine.py"
src = P.read_text(encoding="utf-8").replace("\r\n", "\n")


def rep(old: str, new: str, tag: str):
    global src
    assert src.count(old) == 1, f"[{tag}] 命中 {src.count(old)} 次而非 1 次"
    src = src.replace(old, new)
    print(f"  [PATCH] {tag}")


# ── 1. 类型常量 + generic_ledger ───────────────────────────────
rep('''JOURNAL = "journal"                 # 企业序时账/银行存款日记账
BANK_STATEMENT = "bank_statement"   # 银行流水/对账单''',
    '''JOURNAL = "journal"                 # 企业序时账/银行存款日记账
BANK_STATEMENT = "bank_statement"   # 银行流水/对账单
GENERIC_LEDGER = "generic_ledger"   # 通用台账（格式多样：有日期+金额但无强特征）''',
    "GENERIC_LEDGER 类型")

# ── 2. detect_book_type：台账泛化 ──────────────────────────────
rep('''    if j_score > b_score:
        return JOURNAL
    if b_score > 0:
        return BANK_STATEMENT
    return "unknown"''',
    '''    if j_score > b_score:
        return JOURNAL
    if b_score > 0:
        return BANK_STATEMENT
    # 通用台账：存在日期语义 + 金额语义（借贷对/收支对/带符号金额）即纳入对账范畴
    try:
        from core.column_semantics import detect_column_roles
        roles = detect_column_roles(df)
        has_date = "date" in roles
        has_amount = any(r in roles for r in ("amount", "debit", "credit"))
        if has_date and has_amount:
            return GENERIC_LEDGER
    except Exception:
        pass
    return "unknown"''',
    "detect_book_type 台账泛化")

# ── 3. normalize_to_std：journal 分支金额列兜底（台账无借贷双列时） ──
rep('''    if book_type == JOURNAL:
        out["net_amount"] = out["debit"] - out["credit"]''',
    '''    if book_type == JOURNAL:
        out["net_amount"] = out["debit"] - out["credit"]
        # 通用台账：无借贷双列时回退带符号金额单列（正=流入）
        if (out["net_amount"] == 0).all() and amount_col and amount_col in df.columns:
            out["net_amount"] = df[amount_col].map(_to_float)''',
    "journal 归一化金额列兜底")

# ── 4. run()：类型未知不硬猜 + LLM 映射兜底 ────────────────────
rep('''    book_type = cfg.get("book_type") or detect_book_type(book_df, cfg.get("book_file", ""))
    bank_type = cfg.get("bank_type") or detect_book_type(bank_df, cfg.get("bank_file", ""))
    if book_type == "unknown":
        book_type = JOURNAL
    if bank_type == "unknown":
        bank_type = BANK_STATEMENT
    book_map = cfg.get("book_mapping") or auto_map_columns(book_df, book_type)
    bank_map = cfg.get("bank_mapping") or auto_map_columns(bank_df, bank_type)''',
    '''    book_type = cfg.get("book_type") or detect_book_type(book_df, cfg.get("book_file", ""))
    bank_type = cfg.get("bank_type") or detect_book_type(bank_df, cfg.get("bank_file", ""))
    if book_type == "unknown":
        book_type = JOURNAL
    if bank_type == "unknown":
        bank_type = BANK_STATEMENT
    # 通用台账按序时账方向归一（有借贷双列时）；金额单列时自动回退
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
            pass''',
    "run() LLM 映射兜底")

P.write_text(src, encoding="utf-8", newline="\n")
import ast
ast.parse(src)
print("bank_reconcile_engine 泛化补丁完成，AST OK")
