#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""matching_engine.py 补丁 A：文件类型/列识别/清洗规则/医保局部化"""
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "core" / "matching_engine.py"
src = P.read_text(encoding="utf-8").replace("\r\n", "\n")


def rep(old: str, new: str, tag: str):
    global src
    assert src.count(old) == 1, f"[{tag}] 命中 {src.count(old)} 次而非 1 次"
    src = src.replace(old, new)
    print(f"  [PATCH] {tag}")


# ── 1. detect_file_type：增加 journal（序时账）识别 ─────────────
rep('''def detect_file_type(df: pd.DataFrame, filename: str) -> str:
    """智能识别文件类型：bank_statement / summary_table / ledger / unknown"""
    cols_str = " ".join(str(c).lower() for c in df.columns)
    fname_lower = filename.lower()
    bank_kw = ["交易日期", "摘要", "收入", "支出", "余额", "对方", "借方", "贷方"]''',
    '''def detect_file_type(df: pd.DataFrame, filename: str) -> str:
    """智能识别文件类型：journal / bank_statement / summary_table / unknown"""
    cols_str = " ".join(str(c).lower() for c in df.columns)
    fname_lower = filename.lower()
    # 序时账/日记账强特征：凭证号、科目编码、借贷双列+凭证组合
    journal_kw = ["凭证号", "凭证编号", "凭证号码", "科目编码", "科目名称"]
    if any(k in fname_lower for k in ("序时账", "日记账", "明细账")) \\
            or sum(1 for k in journal_kw if k in cols_str) >= 1 \\
            or ("借方金额" in cols_str and "贷方金额" in cols_str and "凭证" in cols_str):
        return "journal"
    bank_kw = ["交易日期", "摘要", "收入", "支出", "余额", "对方", "借方", "贷方"]''',
    "detect_file_type 增加 journal 识别")

# ── 2. identify_columns：方向修正 + journal 分支 ────────────────
rep('''        mapping["income_col"] = _find_col(cols_lower, ["收入", "收入金额", "借方金额", "credit", "income"])
        mapping["expense_col"] = _find_col(cols_lower, ["支出", "支出金额", "贷方金额", "debit", "expense"])
    elif file_type == "summary_table":''',
    '''        # 方向修正（银行流水第一常识）：收入=贷方（收入），支出=借方（支取）
        mapping["income_col"] = _find_col(cols_lower, ["收入", "收入金额", "贷方（收入）", "贷方(收入)", "贷方金额", "credit", "income"])
        mapping["expense_col"] = _find_col(cols_lower, ["支出", "支出金额", "借方（支取）", "借方(支取)", "借方金额", "debit", "expense"])
        mapping["account_col"] = _find_col(cols_lower, ["银行账号", "账号", "账户", "银行账户"])
        mapping["balance_col"] = _find_col(cols_lower, ["余额", "账户余额", "期末余额"])
    elif file_type == "journal":
        mapping["date_col"] = _find_col(cols_lower, ["日期", "记账日期", "交易日期", "业务日期"])
        mapping["voucher_col"] = _find_col(cols_lower, ["凭证号", "凭证号码", "凭证编号", "凭证字号"])
        mapping["desc_col"] = _find_col(cols_lower, ["摘要", "摘要信息", "说明", "用途", "备注"])
        mapping["debit_col"] = _find_col(cols_lower, ["借方金额", "借方", "借方发生额"])
        mapping["credit_col"] = _find_col(cols_lower, ["贷方金额", "贷方", "贷方发生额"])
        mapping["account_col"] = _find_col(cols_lower, ["银行账号", "账号", "账户"])
        mapping["subject_col"] = _find_col(cols_lower, ["科目编码", "科目名称", "会计科目"])
    elif file_type == "summary_table":''',
    "identify_columns 方向修正+journal 分支")

# ── 3. identify_columns：语义兜底（消除硬编码列名依赖） ─────────
rep('''        mapping["total_col"] = _find_col(cols_lower, ["合计", "总计", "汇总", "sum", "total"])
    return mapping''',
    '''        mapping["total_col"] = _find_col(cols_lower, ["合计", "总计", "汇总", "sum", "total"])
    # 语义兜底：候选表未命中的关键角色交给统一语义注册表
    try:
        from core.column_semantics import detect_column_roles
        roles = detect_column_roles(df)
        for k, role in {"date_col": "date", "amount_col": "amount", "name_col": "name",
                        "counterparty_col": "counterpart", "desc_col": "summary",
                        "account_col": "account", "balance_col": "balance"}.items():
            if not mapping.get(k) and role in roles:
                mapping[k] = roles[role]
    except Exception:
        pass
    return mapping''',
    "identify_columns 语义兜底")

# ── 4. _fuzzy_match_name：全局行政区划清洗 → 参数化 ────────────
rep('''def _fuzzy_match_name(n1: str, n2: str) -> bool:
    """模糊匹配两个机构名称"""
    clean = lambda s: s.replace("市", "").replace("县", "").replace("区", "").replace("中心", "").replace("管理", "")
    a, b = clean(n1), clean(n2)''',
    '''# 医保场景特化的行政区划清洗词（仅医保匹配链路显式开启，全局禁用——
# 否则"朝阳区甲公司"与"海淀区甲公司"会被误判为同一家，造成张冠李戴）
MEDICAL_STRIP_WORDS = ("市", "县", "区", "中心", "管理")


def _fuzzy_match_name(n1: str, n2: str, strip_admin: bool = False) -> bool:
    """模糊匹配两个机构名称。strip_admin=True 仅用于医保回款场景。"""
    if strip_admin:
        clean = lambda s: s.replace("市", "").replace("县", "").replace("区", "").replace("中心", "").replace("管理", "")
        a, b = clean(n1), clean(n2)
    else:
        a, b = n1.strip(), n2.strip()''',
    "_fuzzy_match_name 清洗规则参数化")

# ── 5. 医保提取清洗保留但局部化 ────────────────────────────────
rep('''        short = inst.replace("市", "").replace("县", "").replace("区", "").replace("医疗保险", "").replace("基金管理中心", "").replace("新型农村合作医疗", "")''',
    '''        short = inst
        for _w in ("市", "县", "区", "医疗保险", "基金管理中心", "新型农村合作医疗"):
            short = short.replace(_w, "")  # 医保场景专用清洗（本函数即医保链路）''',
    "医保提取清洗局部化")

P.write_text(src, encoding="utf-8", newline="\n")
import ast
ast.parse(src)
print("matching_engine 补丁A 完成，AST OK")
