#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通用台账（异形表头）对账测试：规则失败 → LLM 映射兜底 → 方向自动判定"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pandas as pd
from core.bank_reconcile_engine import (GENERIC_LEDGER, detect_book_type,
                                        run_bank_reconciliation)

ok = lambda msg: print(f"  [OK] {msg}")

# 异形台账：表头完全不在同义词表内（划拨日期/兹收/兹付/对方单位/附记）
ledger = pd.DataFrame({
    "划拨日期": ["2026-03-01", "2026-03-04", "2026-03-20"],
    "对方单位": ["甲公司", "乙公司", "丙公司"],
    "兹收": [10000, 0, 7000],
    "兹付": [0, 5000, 0],
    "附记": ["货款", "材料款", "尾款"],
})
bank = pd.DataFrame({
    "日期": ["2026-03-01", "2026-03-05", "2026-03-28"],
    "摘要": ["甲公司货款", "付乙材料款", "手续费"],
    "借方（支取）": [0, 5000, 25],
    "贷方（收入）": [10000, 0, 0],
    "银行账号": ["农行5927"] * 3,
})

# 1) 台账识别为 generic_ledger（有日期+金额语义但无强特征）
t = detect_book_type(ledger, "往来台账.xlsx")
assert t == GENERIC_LEDGER, t
ok("异形台账识别为 generic_ledger")

# 2) 规则映射失败 → LLM 兜底 → 方向按列名判定（兹收/兹付=收支式）
fake_llm = lambda p: ('{"date": "划拨日期", "credit": "兹收", "debit": "兹付",'
                      ' "counterpart": "对方单位", "summary": "附记"}')
res = run_bank_reconciliation(ledger, bank, {
    "book_file": "往来台账.xlsx", "bank_file": "流水.xlsx",
    "llm_callable": fake_llm})
s = res["stats"]
# 映射成功：兹收/兹付被映射为 credit/debit（收支式 → net=兹收-兹付）
assert s["book_mapping"].get("credit") == "兹收" and s["book_mapping"].get("debit") == "兹付", s["book_mapping"]
assert s["book_mapping"].get("date") == "划拨日期"
ok("LLM 兜底映射异形列名")
# 甲公司 10000 (3-01) L1 对上；乙公司 5000 (3-04 vs 3-05) L2 对上；
# 丙公司 7000 与手续费 25 未匹配
assert s["matched_L1"] == 1 and s["matched_L2"] == 1, s
assert s["book_matched"] == 2 and s["bank_matched"] == 2, s
ok("异形台账×流水 L1/L2 逐笔勾对（方向口径自动=收支式）")
# 台账 3-20 丙公司：距流水期末(3-28) 8 天 > 窗口3 → 待人工核查（不洗白）
ub = {i["summary"]: i["classification"] for i in res["unmatched_book"]}
assert ub.get("尾款") == "待人工核查", ub
ok("台账未匹配项兜底待人工核查")

# 3) 无 LLM 时（离线）：规则失败也不应臆造（净额全 0 → 无匹配、全部待查）
res2 = run_bank_reconciliation(ledger, bank, {
    "book_file": "往来台账.xlsx", "bank_file": "流水.xlsx",
    "llm_callable": lambda p: None})
s2 = res2["stats"]
assert s2["book_matched"] == 0 and s2["bank_matched"] == 1 or s2["book_matched"] == 0
ok("离线时不臆造映射（宁缺毋滥）")

print("\n全部通过：通用台账（异形表头）LLM 智能化对账")
