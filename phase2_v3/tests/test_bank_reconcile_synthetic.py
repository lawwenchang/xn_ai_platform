#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bank_reconcile_engine 合成序时账×流水场景断言测试
运行: python tests/test_bank_reconcile_synthetic.py

覆盖：方向镜像 / L1~L3 逐笔匹配 / 未达四分类 / 余额调节表 / 红旗规则
"""
import json
import sys
import tempfile
from pathlib import Path

# Windows GBK 控制台防御：强制 UTF-8 输出
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from core.bank_reconcile_engine import (
    JOURNAL, BANK_STATEMENT,
    CAT_ENT_RECV, CAT_ENT_PAY, CAT_BANK_RECV, CAT_BANK_PAY, CAT_REVIEW,
    detect_book_type, auto_map_columns, normalize_to_std, filter_bank_account,
    tie_out_balance, run_bank_reconciliation, export_reconciliation_outputs,
    detect_duplicates,
)

PASS = 0


def ok(name):
    global PASS
    PASS += 1
    print(f"[PASS] {name}")


# 合成数据：序时账（巨久-农行5927 风格）
book = pd.DataFrame({
    "序号": [1, 2, 3, 4, 5, 6],
    "月": [3] * 6,
    "日期": ["2026-03-01", "2026-03-02", "2026-03-05", "2026-03-28", "2026-03-10", "2026-03-28"],
    "凭证号": ["记-001", "记-002", "记-003", "记-004", "记-005", "记-006"],
    "摘要": ["收货款-甲公司", "付货款-乙公司", "收货款-丙公司", "收货款-丁公司", "付费用", "手续费扣款"],
    "借方金额": [10000, 0, 8000, 6000, 0, 0],
    "贷方金额": [0, 5000, 0, 0, 999, 100],
    "银行账号": ["农行5927"] * 6,
})

# 合成数据：银行流水（含前导空格列名、多账户、整数大额、一收一付同额）
bank = pd.DataFrame({
    "序号": list(range(1, 11)),
    "银行账号": ["农行5927", "农行5927", "农行5927", "农行5927", "农行5927",
                 "工行1101", "农行5927", "农行5927", "农行5927", "农行5927"],
    "日期": ["2026-03-01", "2026-03-03", "2026-03-05", "2026-03-05", "2026-03-29",
             "2026-03-01", "2026-03-15", "2026-03-16", "2026-03-16", "2026-03-28"],
    " 摘要": ["甲公司货款", "付乙公司货款", "丙公司款", "丙公司款", "利息收入",
              "其他行流水", "支取现金", "收甲转账", "付戊转账", "他行手续费"],
    "借方（支取）": [0, 5000, 0, 0, 0, 0, 60000, 0, 100000, 50],
    "贷方（收入）": [10000, 0, 5000, 3000, 88.8, 77777, 0, 100000, 0, 0],
})

# 1) 类型识别
assert detect_book_type(book, "序时账1.xlsx") == JOURNAL
assert detect_book_type(bank, "银行流水1.xlsx") == BANK_STATEMENT
ok("序时账/流水类型识别")

# 2) 列语义映射（含前导空格列名）
bm = auto_map_columns(bank, BANK_STATEMENT)
assert bm["summary"] == " 摘要" and bm["account"] == "银行账号", bm
assert bm["debit"] == "借方（支取）" and bm["credit"] == "贷方（收入）", bm
ok("列语义映射（前导空格/括号列名）")

# 3) 方向镜像：账方借方(+)=银行贷方(+)同号
bstd = normalize_to_std(book, auto_map_columns(book, JOURNAL), JOURNAL)
kstd = normalize_to_std(bank, bm, BANK_STATEMENT)
assert bstd.loc[0, "net_amount"] == 10000 and bstd.loc[1, "net_amount"] == -5000
assert kstd.loc[0, "net_amount"] == 10000 and kstd.loc[1, "net_amount"] == -5000
ok("方向镜像归一化（借-贷 / 收入-支取 同号可比）")

# 4) 账户过滤（流水含工行1101 → 过滤到农行5927）
kstd2, note = filter_bank_account(kstd)
assert len(kstd2) == 9 and "农行5927" in note and "9" in note, note
ok("多账户流水过滤")

# 5) 勾稽：期初0 + Σnet(18001) = 期末18001
assert tie_out_balance(bstd, opening=0, closing=17901)["balanced"]
ok("期初+发生额=期末勾稽")

# 6) 完整对账
res = run_bank_reconciliation(book, bank, {
    "book_file": "序时账1.xlsx", "bank_file": "银行流水1.xlsx",
    "book_closing": 10000, "bank_closing": 4138.8})
s = res["stats"]
assert s["matched_L1"] == 1, s          # 甲公司10000 同日
assert s["matched_L2"] == 1, s          # 乙公司5000 日期差1天
assert s["matched_L3_groups"] == 1, s   # 丙公司8000 = 5000+3000
assert s["book_matched"] == 3 and s["bank_matched"] == 4, s
ok(f"L1/L2/L3 逐笔匹配 (L1={s['matched_L1']}, L2={s['matched_L2']}, L3组={s['matched_L3_groups']})")

# 7) 未匹配四分类 + 专项标记 + 兜底待人工核查
ub = {i["summary"]: i for i in res["unmatched_book"]}
assert ub["收货款-丁公司"]["classification"] == CAT_ENT_RECV
assert ub["付费用"]["classification"] == CAT_REVIEW
assert ub["手续费扣款"]["classification"] == CAT_ENT_PAY
ubk = {i["summary"]: i for i in res["unmatched_bank"]}
assert ubk["利息收入"]["classification"] == CAT_BANK_RECV and ubk["利息收入"]["special"] == "利息"
assert ubk["他行手续费"]["classification"] == CAT_BANK_PAY and ubk["他行手续费"]["special"] == "银行费用"
assert ubk["支取现金"]["classification"] == CAT_REVIEW
assert ubk["收甲转账"]["classification"] == CAT_REVIEW
ok("未达四分类 + 利息/费用专项标记 + 兜底待人工核查")

# 8) 余额调节表：企业账面 10000 + 88.8 - 50 = 10038.8；银行 4138.8 + 6000 - 100 = 10038.8
recon = {r["项目"]: r["金额"] for r in res["balance_reconciliation"]}
assert abs(recon["企业银行存款日记账账面余额"] - 10000) < 0.01
assert abs(recon["银行对账单余额"] - 4138.8) < 0.01
assert abs(recon["调节后企业账面余额"] - 10038.8) < 0.01, recon
assert abs(recon["调节后银行对账单余额"] - 10038.8) < 0.01, recon
assert abs(recon["调节差异"] - 0) < 0.01, recon
ok("银行存款余额调节表（四方平衡）")

# 9) 红旗：一收一付同额(50000/3-16) + 大额现金(60000) + 整数大额(50000)
flag_types = {f["type"] for f in res["red_flags"]}
assert "一收一付同额" in flag_types and "大额现金" in flag_types, flag_types
assert "整数大额" in flag_types, flag_types
ok("12号文红旗规则（一收一付同额/大额现金/整数大额）")

# 10) 重复入账检测（独立小样本）
dup_df = normalize_to_std(pd.DataFrame({
    "日期": ["2026-03-11", "2026-03-11", "2026-03-20"],
    "摘要": ["付丙", "付丙", "付丙"],
    "借方金额": [0, 0, 0], "贷方金额": [500, 500, 500]}),
    auto_map_columns(pd.DataFrame({"日期": [], "摘要": [], "借方金额": [], "贷方金额": []}),
                     JOURNAL), JOURNAL)
dups = detect_duplicates(dup_df, "book")
assert len(dups) == 1 and len(dups[0]["rows"]) == 2, dups
ok("疑似重复入账检测")

# 11) 交付物导出
out = Path(tempfile.mkdtemp(prefix="reconcile_synthetic_"))
files = export_reconciliation_outputs(res, out)
assert len(files) == 5 and all((out / f).exists() for f in files), files
summary = json.loads((out / "reconciliation_summary.json").read_text(encoding="utf-8"))
assert summary["stats"]["matched_L1"] == 1
assert summary["tie_out"]["book"]["balanced"]
assert summary["tie_out"]["bank"]["balanced"]
ok("5 项专业交付物导出 + summary JSON 校验")

print(f"\n全部 {PASS} 项验证通过 ✅")

