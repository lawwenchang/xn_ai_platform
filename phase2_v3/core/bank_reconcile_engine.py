#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
专业银行对账引擎 (bank_reconcile_engine.py)
==============================================
实现"序时账 × 银行流水"逐笔勾对的专业审计能力，与 matching_engine.py
（医保回款汇总比对专用）并存。

专业规则（依据《中国注册会计师审计准则问题解答第12号——货币资金审计》
及银行存款余额调节表编制惯例）：

1. 方向镜像（第一常识）
   - 企业序时账：借方金额 = 银行存款增加，贷方金额 = 减少
     → 归一化 net = 借方 − 贷方
   - 银行流水：贷方（收入）= 存款增加，借方（支取）= 减少
     → 归一化 net = 贷方收入 − 借方支取
   - 归一化后同号金额方可互为镜像勾对。
2. 逐笔核对精确到分（±0.01 元），百分比容差只用于汇总层面分析性复核。
3. 对账前双方各自勾稽：期初余额 + Σ借方 − Σ贷方 = 期末余额。
4. 未匹配项默认"待人工核查"，只有接近期末且有窗口证据的才列入
   未达账项候选（银收企未收 / 银付企未付 / 企收银未收 / 企付银未付），
   禁止把解释不了的差异一律洗白成"未达账项"。
5. 利息、手续费、冲正不删除、单独成类输出。

匹配层级（确定性规则优先，LLM 不参与自动确认）：
   L1 金额精确(±0.01) + 同日 + 同方向           → 自动确认
   L2 金额精确 + 日期窗口(±N 天可配) + 同方向    → 自动确认（跨期）
   L3 同方向同窗口 n:m 金额合计相等             → 拆分/合并入账
   L4 摘要/对方户名模糊(RapidFuzz)              → 仅"待人工复核"，永不自动确认
"""

from __future__ import annotations

import itertools
import json
import re
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

import pandas as pd

# ═══════════════════════════════════════════════════════════════
# 常量与列语义注册表
# ═══════════════════════════════════════════════════════════════

JOURNAL = "journal"                 # 企业序时账/银行存款日记账
BANK_STATEMENT = "bank_statement"   # 银行流水/对账单
GENERIC_LEDGER = "generic_ledger"   # 通用台账（格式多样：有日期+金额但无强特征）

AMOUNT_TOLERANCE = 0.01             # 逐笔核对硬容差（精确到分）
DEFAULT_DATE_WINDOW = 3             # 默认日期窗口（天）

STD_COLUMNS = [
    "row_id", "date", "voucher_no", "summary", "counterpart", "account",
    "debit", "credit", "net_amount", "balance", "source_file", "src_index",
]

# 列语义 → 候选列名（按优先级排列；匹配时忽略空格与全半角括号差异）
COLUMN_ROLE_SYNONYMS: Dict[str, List[str]] = {
    "date":        ["日期", "交易日期", "记账日期", "业务日期", "入账日期", "date"],
    "voucher_no":  ["凭证号", "凭证号码", "凭证编号", "凭证字号", "凭证"],
    "summary":     ["摘要", "摘要信息", "用途", "备注", "说明", "附言"],
    "counterpart": ["对方户名", "对方客户名称", "对方", "交易对手", "对方单位",
                    "对手方", "对方账号户名"],
    "account":     ["银行账号", "账号", "账户", "银行账户", "开户账号"],
    "debit":       ["借方金额", "借方", "借方(支取)", "借方（支取）", "支取",
                    "支出", "支出金额", "付款金额", "借方发生额"],
    "credit":      ["贷方金额", "贷方", "贷方(收入)", "贷方（收入）", "收入",
                    "收入金额", "收款金额", "贷方发生额"],
    "balance":     ["余额", "账户余额", "期末余额", "本次余额"],
    "amount":      ["交易金额", "金额", "发生额", "交易额"],
    "subject":     ["科目编码", "科目名称", "会计科目", "科目"],
}

# 序时账特征列（出现即加分）
_JOURNAL_HINTS = {"凭证号", "凭证号码", "凭证编号", "科目编码", "科目名称", "月"}
# 银行流水特征列
_BANK_HINTS = {"对方户名", "对方客户名称", "银行账号", "账号", "余额",
               "对方账号", "开户行"}

# 利息/费用/冲正词表（单独成类输出，不参与噪音删除）
INTEREST_WORDS = ("利息", "结息")
FEE_WORDS = ("手续费", "短信费", "年费", "账户管理费", "工本费", "服务费")
REVERSAL_WORDS = ("冲正", "冲销", "红冲", "撤销")
from config.dictionary import INTERBANK_WORDS as _INTERBANK
# ── v3.3 内容标签层 ──
CONTENT_TAGS = {
    "医保":  ("医保", "医疗保险", "医保结算", "医保回款", "医保中心"),
    "社保":  ("社保", "社会保险", "社保局", "社保回款"),
    "工资":  ("工资", "薪酬", "奖金", "绩效", "劳务费"),
    "税款":  ("税款", "税收", "增值税", "所得税", "营业税", "附加税", "印花税"),
    "现金":  ("现金", "库存现金", "提现"),
    "转账":  ("转账", "汇兑", "网银", "电汇", "划款"),
    "费用":  ("手续费", "短信费", "年费", "账户管理费", "工本费", "服务费", "费用外收", "批量扣费", "收费"),
    "利息":  ("利息", "结息", "利息收入", "利息支出", "批量结息"),
    "冲正":  ("冲正", "冲销", "红冲", "撤销", "调账"),
    "货款":  ("货款", "采购款", "材料款", "货款结算"),
    "往来":  ("往来款", "往来", "借款", "还款", "暂付", "暂收"),
}

_VERB_PREFIX = re.compile(r"^(收到|支付|收|付|转|汇入|汇出|支)")
_COMPANY_SUFFIX = re.compile(r"(.+?(?:有限责任公司|有限公司|集团|公司|厂|店|中心|合作社|事务所|经营部))")

def extract_counterpart(summary: Any) -> str:
    """序时账无对方列形态：从摘要提取对手方主体。
    '收阳信康润商贸有限公司 往来款' → '阳信康润商贸有限公司'
    """
    if not isinstance(summary, str) or not summary.strip():
        return ""
    first = summary.strip().split()[0]          # 摘要习惯：动词+主体 空格 用途
    first = _VERB_PREFIX.sub("", first)
    m = _COMPANY_SUFFIX.match(first)
    return m.group(1) if m else first

def tag_content(df: pd.DataFrame) -> pd.DataFrame:
    """给每行打内容标签（确定性关键词分类器）。

    标签服务于：
    ① 场景筛选——提取式核对按标签过滤
    ② L2/L3 消歧——窗口内同额多笔用标签区分
    ③ 未达分类——利息/手续费自动归类

    返回原 DataFrame，新增 content_tag 列。
    """
    tags = []
    for _, r in df.iterrows():
        summary = str(r.get("summary", ""))
        counterpart = str(r.get("counterpart", ""))
        text = summary + " " + counterpart
        row_tags = []
        for tag, keywords in CONTENT_TAGS.items():
            if any(kw in text for kw in keywords):
                row_tags.append(tag)
        tags.append(",".join(row_tags) if row_tags else "")
    df = df.copy()
    df["content_tag"] = tags
    return df



# ═══════════════════════════════════════════════════════════════
# 步骤 1：文件类型识别与列语义映射
# ═══════════════════════════════════════════════════════════════

def _norm_col(c: Any) -> str:
    """列名归一化：去空格/全角空格，统一括号"""
    s = str(c).replace(" ", "").replace("　", "").strip()
    return s.replace("（", "(").replace("）", ")")


def detect_book_type(df: pd.DataFrame, filename: str = "") -> str:
    """识别账簿类型：journal（序时账）/ bank_statement（流水）/ generic_ledger / unknown

    v3.3: 主路径走 column_semantics 240 词注册表（角色组合判定），
    旧 hints 词表降级为文件名线索辅助。
    """
    cols = {_norm_col(c) for c in df.columns}
    fname = str(filename)

    # ── 主路径：列语义角色组合判定 ──
    try:
        from core.column_semantics import detect_column_roles
        roles = detect_column_roles(df)

        # 序时账信号：有日期 + (借贷双列 或 凭证号)
        has_journal = (
            "date" in roles
            and (("debit" in roles and "credit" in roles)
                 or "voucher_no" in roles or "subject" in roles)
        )
        # 银行流水信号：有日期 + (余额 或 (收支列 + 对方/账号))
        has_bank = (
            "date" in roles and "balance" in roles
            or ("date" in roles
                and ("debit" in roles or "credit" in roles or "amount" in roles)
                and ("counterpart" in roles or "account" in roles))
        )

        if has_journal and not has_bank:
            return JOURNAL
        if has_bank and not has_journal:
            return BANK_STATEMENT
        if has_journal and has_bank:
            # 歧义：用文件名打破
            if any(k in fname for k in ("流水", "对账单", "银行")):
                return BANK_STATEMENT
            if any(k in fname for k in ("序时账", "日记账", "明细账", "台账")):
                return JOURNAL
            return JOURNAL  # 默认序时账
    except Exception:
        pass

    # ── 降级路径：旧 hints + 文件名 ──
    j_score = sum(1 for h in _JOURNAL_HINTS if any(h in c for c in cols))
    b_score = sum(1 for h in _BANK_HINTS if any(h in c for c in cols))
    if any(k in fname for k in ("流水", "对账单", "银行")): b_score += 2
    if any(k in fname for k in ("序时账", "日记账", "明细账")): j_score += 2
    if j_score > b_score: return JOURNAL
    if b_score > 0: return BANK_STATEMENT

    # ── 兜底：date + amount → 通用台账 ──
    try:
        from core.column_semantics import detect_column_roles, infer_amount_columns
        roles2 = detect_column_roles(df) if 'roles' not in dir() else roles
        has_date = "date" in roles2
        has_amount = any(r in roles2 for r in ("amount", "debit", "credit")) \
            or bool(infer_amount_columns(df, max_cols=1))
        if has_date and has_amount:
            return GENERIC_LEDGER
    except Exception:
        pass
    return "unknown"


def auto_map_columns(df: pd.DataFrame,
                     book_type: Optional[str] = None) -> Dict[str, str]:
    """列语义自动映射：返回 {role: 实际列名}。

    银行流水方向镜像关键：列名含"收入/贷方"→ credit；含"支出/支取/借方"→ debit。
    序时账：借方金额 → debit（存款增加）；贷方金额 → credit（存款减少）。
    """
    norm2orig = {_norm_col(c): str(c) for c in df.columns}
    mapping: Dict[str, str] = {}
    for role, candidates in COLUMN_ROLE_SYNONYMS.items():
        for cand in candidates:
            nc = _norm_col(cand)
            if nc in norm2orig:
                mapping[role] = norm2orig[nc]
                break
        else:
            # 包含式兜底（如 "借方金额(元)"）
            for cand in candidates:
                nc = _norm_col(cand)
                for n, orig in norm2orig.items():
                    if nc in n and role not in mapping:
                        mapping[role] = orig
                        break
    return mapping



# ═══════════════════════════════════════════════════════════════
# 步骤 2：方向镜像归一化
# ═══════════════════════════════════════════════════════════════

def _to_float(v: Any) -> float:
    """金额解析：去千分位/中文逗号/货币符号，失败返回 0.0"""
    if isinstance(v, (int, float)) and not pd.isna(v):
        return float(v)
    try:
        s = str(v).replace(",", "").replace("，", "").replace(" ", "")
        s = s.replace("¥", "").replace("￥", "").strip()
        if s in ("", "-", "--", "nan", "None"):
            return 0.0
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def normalize_to_std(df: pd.DataFrame, mapping: Dict[str, str],
                     book_type: str, source_file: str = "") -> pd.DataFrame:
    """归一化为标准结构，核心是按账簿类型做方向镜像：

    - journal（企业序时账）：net = 借方金额 − 贷方金额（正 = 存款增加）
    - bank_statement（银行流水）：net = 贷方收入 − 借方支取（正 = 存款增加）

    归一化后双方 net_amount 同号即表示同向资金流动，可直接勾对。
    """
    out = pd.DataFrame()
    n = len(df)

    def col(role: str) -> pd.Series:
        c = mapping.get(role)
        if c and c in df.columns:
            return df[c]
        return pd.Series([None] * n)

    out["row_id"] = [f"{'B' if book_type == BANK_STATEMENT else 'J'}{i}" for i in range(n)]
    out["date"] = pd.to_datetime(col("date"), errors="coerce")
    out["voucher_no"] = col("voucher_no").astype(str).replace("None", "")
    out["summary"] = col("summary").astype(str).replace("None", "")
    out["counterpart"] = col("counterpart").astype(str).replace("None", "")
    out["account"] = col("account").astype(str).replace("None", "")
    out["debit"] = pd.to_numeric(
        col("debit").astype(str).str.replace(",", "").str.replace("，", "").str.replace("¥", "").str.replace("￥", "").str.strip(),
        errors="coerce").fillna(0)
    out["credit"] = pd.to_numeric(
        col("credit").astype(str).str.replace(",", "").str.replace("，", "").str.replace("¥", "").str.replace("￥", "").str.strip(),
        errors="coerce").fillna(0)
    amount_col = mapping.get("amount")
    if book_type == JOURNAL:
        out["net_amount"] = out["debit"] - out["credit"]
        # 通用台账：无借贷双列时回退带符号金额单列（正=流入）
        if (out["net_amount"] == 0).all() and amount_col and amount_col in df.columns:
            out["net_amount"] = df[amount_col].map(_to_float)
    elif book_type == BANK_STATEMENT:
        # 方向镜像：流水借方(支取)=减少，贷方(收入)=增加
        out["net_amount"] = out["credit"] - out["debit"]
        # 只有单列交易金额（正=收入）时直接使用
        if (out["net_amount"] == 0).all() and amount_col and amount_col in df.columns:
            out["net_amount"] = df[amount_col].map(_to_float)
    else:
        if amount_col and amount_col in df.columns:
            out["net_amount"] = df[amount_col].map(_to_float)
        else:
            out["net_amount"] = out["debit"] - out["credit"]
    bal = pd.to_numeric(
        col("balance").astype(str).str.replace(",", "").str.replace("，", "").str.replace("¥", "").str.strip(),
        errors="coerce").fillna(0)
    out["balance"] = bal if not (bal == 0).all() else pd.Series([None] * n)
    out["source_file"] = source_file
    out["src_index"] = list(range(n))
    # 金额转分（int），消除浮点误差
    out["net_cents"] = (out["net_amount"] * 100).round().astype("int64")
    out["abs_cents"] = out["net_cents"].abs()
    # 剔除借贷均为 0 的空行
    out = out[out["net_cents"] != 0].reset_index(drop=True)
    return out


def filter_bank_account(bank_std: pd.DataFrame,
                        account: Optional[str] = None) -> Tuple[pd.DataFrame, str]:
    """按银行账号过滤流水（流水含多个账户、序时账为单账户时必须先过滤）。

    account 为 None 时：若只含一个账号原样返回；含多个账号时返回
    出现次数最多的账号并给出提示（确定性规则，不猜测）。
    """
    if "account" not in bank_std.columns:
        return bank_std, "流水无账号列，未过滤"
    accounts = bank_std["account"].replace("", pd.NA).dropna().unique().tolist()
    if not accounts:
        return bank_std, "流水账号列为空，未过滤"
    if account:
        key = re.sub(r"\s", "", str(account))
        mask = bank_std["account"].astype(str).str.replace(r"\s", "", regex=True)
        hit = bank_std[mask.str.contains(key, na=False)]
        if hit.empty:
            return bank_std, f"⚠ 指定账号 {account} 在流水中未出现，未过滤（请人工确认）"
        return hit.reset_index(drop=True), f"已按账号 {account} 过滤：{len(hit)} 笔"
    if len(accounts) == 1:
        return bank_std, f"流水为单一账户 {accounts[0]}，无需过滤"
    top = bank_std["account"].value_counts().idxmax()
    hit = bank_std[bank_std["account"] == top].reset_index(drop=True)
    return hit, (f"⚠ 流水含 {len(accounts)} 个账户，自动取笔数最多的 {top}"
                 f"（{len(hit)} 笔）；如不符请指定账号")


# ═══════════════════════════════════════════════════════════════
# 步骤 3：双方勾稽（对账前置校验）
# ═══════════════════════════════════════════════════════════════

def tie_out_balance(std_df: pd.DataFrame, opening: Optional[float] = None,
                    closing: Optional[float] = None) -> Dict[str, Any]:
    """勾稽校验：期初余额 + Σ(net) = 期末余额。

    优先级：显式传入 > 余额列推算（首行余额 − 首行净额 = 期初）。
    不平不阻断对账，但必须在结果中显式报告。
    """
    total = round(float(std_df["net_amount"].sum()), 2)
    result: Dict[str, Any] = {"total_net": total, "checked": False}
    bal = std_df["balance"].dropna()
    if opening is None and len(bal) > 0:
        opening = round(float(bal.iloc[0]) - float(std_df["net_amount"].iloc[0]), 2)
        result["opening_source"] = "余额列推算"
    if closing is None and len(bal) > 0:
        closing = round(float(bal.iloc[-1]), 2)
        result["closing_source"] = "余额列推算"
    result["opening"] = opening
    result["closing"] = closing
    if opening is not None and closing is not None:
        computed = round(opening + total, 2)
        diff = round(computed - closing, 2)
        result.update({
            "checked": True, "computed_closing": computed,
            "difference": diff, "balanced": abs(diff) < AMOUNT_TOLERANCE,
        })
    return result

def _detect_date_granularity(book: pd.DataFrame) -> str:
    """检测账面日期粒度：>90% 集中在月末(日>=28) → 'month'（月末批量记账），否则 'day'。"""
    days = pd.to_datetime(book["date"], errors="coerce").dt.day.dropna()
    if len(days) == 0:
        return "day"
    ratio = float((days >= 28).mean())
    print(f"[日期粒度] 账面日期日>=28占比: {ratio:.1%}")
    return "month" if ratio >= 0.8 else "day"

# ═══════════════════════════════════════════════════════════════
# 步骤 4：逐笔匹配（L1 精确 → L2 窗口 → L3 拆分合并 → L4 模糊待复核）
# ═══════════════════════════════════════════════════════════════

def _match_l1(book: pd.DataFrame, bank: pd.DataFrame,
              book_used: set, bank_used: set,
              tol_cents: int) -> List[Dict[str, Any]]:
    """L1 v3.4：金额精确 + 同日 → pandas merge 向量化 O(n log n)。

    按 (cents_bkt, date_key, dup_n) 三键配对，同额同日多对多各自编号。
    tol_cents 容差：分桶键用 round() 而非 int()，避免浮点 0.1+0.2 假差异。
    """
    # 未匹配行
    left = book[~book.index.isin(book_used)][["date", "net_cents"]].copy()
    right = bank[~bank.index.isin(bank_used)][["date", "net_cents"]].copy()
    if left.empty or right.empty:
        return []

    left["date_key"] = left["date"].apply(lambda d: d.date().isoformat() if pd.notna(d) else "NaT")
    left["cents_bkt"] = (left["net_cents"] / tol_cents).round().astype("int64") * tol_cents
    left["_idx"] = left.index                                          # ← 移到这里（reset 之前）
    left = left.sort_values(["cents_bkt", "date_key"]).reset_index(drop=True)
    left["dup_n"] = left.groupby(["cents_bkt", "date_key"]).cumcount()

    right["date_key"] = right["date"].apply(lambda d: d.date().isoformat() if pd.notna(d) else "NaT")
    right["cents_bkt"] = (right["net_cents"] / tol_cents).round().astype("int64") * tol_cents
    right["_idx"] = right.index                                        # ← 同上
    right = right.sort_values(["cents_bkt", "date_key"]).reset_index(drop=True)
    right["dup_n"] = right.groupby(["cents_bkt", "date_key"]).cumcount()

    m = pd.merge(left, right, on=["cents_bkt", "date_key", "dup_n"], suffixes=("_L", "_R"))
    matches = []
    for _, row in m.iterrows():
        ji = row["_idx_L"]
        bi = row["_idx_R"]
        matches.append({"book_idx": ji, "bank_idx": bi, "level": "L1",
                        "note": "金额精确+同日"})
        book_used.add(ji)
        bank_used.add(bi)
    return matches


def _match_l2(book: pd.DataFrame, bank: pd.DataFrame,
              book_used: set, bank_used: set,
              date_window: int, tol_cents: int) -> List[Dict[str, Any]]:
    """L2 v3.4：金额精确 + 日期窗口 → merge 候选 + 日期差贪心指派。

    金额桶内 merge（笛卡尔候选），日期差≤window 筛选，
    按日期差升序贪心一对一配对（drop_duplicates 双侧去重）。
    """
    left = book[~book.index.isin(book_used)][["date", "net_cents"]].copy()
    right = bank[~bank.index.isin(bank_used)][["date", "net_cents"]].copy()
    if left.empty or right.empty:
        return []

    left["cents_bkt"] = (left["net_cents"] / tol_cents).round().astype("int64") * tol_cents
    left["_idx"] = left.index
    right["cents_bkt"] = (right["net_cents"] / tol_cents).round().astype("int64") * tol_cents
    right["_idx"] = right.index

    cand = pd.merge(left, right, on="cents_bkt", suffixes=("_L", "_R"))
    if cand.empty:
        return []

    cand["dd"] = (cand["date_L"] - cand["date_R"]).dt.days.abs()
    cand = cand[cand["dd"] <= date_window]
    if cand.empty:
        return []

    # 贪心指派：日期差最小优先，双侧去重（一对一）
    cand = cand.sort_values("dd")
    cand = cand.drop_duplicates("_idx_L", keep="first")
    cand = cand.drop_duplicates("_idx_R", keep="first")

    matches = []
    for _, row in cand.iterrows():
        ji = row["_idx_L"]
        bi = row["_idx_R"]
        matches.append({"book_idx": ji, "bank_idx": bi, "level": "L2",
                        "note": f"金额精确+日期差{int(row['dd'])}天"})
        book_used.add(ji)
        bank_used.add(bi)
    return matches

def _match_l3_month_remainder(book_m, bank_m, book_used, bank_used, tol_cents):
    """月模式专用：某月某方向上，未匹配流水合计 == 单笔账面 → n:1 成组。
    覆盖'整月流水汇总记一笔账'形态。O(n) 分组求和，无组合爆炸。"""
    matches = []
    for side_src, side_dst in (("bank", "book"), ("book", "bank")):
        src = bank_m if side_src == "bank" else book_m
        dst = book_m if side_src == "bank" else bank_m
        src_used = bank_used if side_src == "bank" else book_used
        dst_used = book_used if side_src == "bank" else bank_used
        rem = src[~src.index.isin(src_used)]
        if rem.empty:
            continue
        grp = rem.groupby([rem["date"].dt.to_period("M"), rem["net_cents"].gt(0)])["net_cents"].sum()
        for di, r in dst[~dst.index.isin(dst_used)].iterrows():
            key = (r["date"].to_period("M"), r["net_cents"] > 0)
            if key in grp.index and abs(grp.loc[key] - r["net_cents"]) <= tol_cents:
                idxs = rem[(rem["date"].dt.to_period("M") == key[0]) &
                           ((rem["net_cents"] > 0) == key[1]) & (~rem.index.isin(src_used))].index.tolist()
                matches.append({"book_idxs": [di] if side_src == "bank" else idxs,
                                "bank_idxs": idxs if side_src == "bank" else [di],
                                "level": "L3_month",
                                "note": f"月末汇总记账：{key[0]}月同方向{len(idxs)}笔合计"})
                src_used.update(idxs); dst_used.add(di)
    return matches

def _match_l3_fee_monthly(book_m, bank_m, book_used, bank_used, tol_cents):
    matches = []
    # 账方：只取"手续费"（与验证基线 247 行口径一致）
    fee_b = book_m[(~book_m.index.isin(book_used)) &
                   book_m["summary"].astype(str).str.contains("手续费", na=False)]
    # 银方：只取"费用外收"（流水摘要原生类型码，2333 行）
    fee_k = bank_m[(~bank_m.index.isin(bank_used)) &
                   (bank_m["summary"].astype(str).str.strip() == "费用外收")]
    if fee_b.empty or fee_k.empty:
        return matches
    b_month = fee_b.groupby(fee_b["date"].dt.to_period("M"))["net_cents"].sum()
    k_month = fee_k.groupby(fee_k["date"].dt.to_period("M"))["net_cents"].sum()
    for m in k_month.index:
        if m not in b_month.index:
            continue
        if abs(b_month[m] - k_month[m]) <= tol_cents:
            bidx = fee_b[(fee_b["date"].dt.to_period("M") == m) & (~fee_b.index.isin(book_used))].index.tolist()
            kidx = fee_k[(fee_k["date"].dt.to_period("M") == m) & (~fee_k.index.isin(bank_used))].index.tolist()
            if bidx and kidx:
                matches.append({"book_idxs": bidx, "bank_idxs": kidx, "level": "L3_fee_month",
                                "note": f"手续费月度聚合：{m}月 账{len(bidx)}笔↔银{len(kidx)}笔 总额相等"})
                book_used.update(bidx)
                bank_used.update(kidx)
    return matches

def _match_l3_fee_difference(book: pd.DataFrame, bank: pd.DataFrame,
                              book_used: set, bank_used: set,
                              date_window: int, tol_cents: int = 1000,
                              max_fee: int = 1000) -> List[Dict[str, Any]]:
    """P2: 手续费差额规则——流水扣款 - 账面记录 ≈ 小额手续费时自动成组。

    v3.6: 向量化候选过滤（numpy 掩码替代 iterrows 双重循环），语义不变。
    差额 ≤ 10 元且较大一侧的摘要含"手续费/费用"关键词时，自动匹配。
    max_fee=1000 即 ±10.00 元（单位：分）。
    """
    import numpy as np
    matches = []
    FEE_KEYWORDS = ("手续费", "费用", "服务费", "管理费", "短信费", "年费", "工本费")

    # 一次性转 numpy 数组
    b_idx = book.index.to_numpy()
    b_c = pd.to_numeric(book["net_cents"], errors="coerce").fillna(0).to_numpy("int64")
    b_d = pd.to_datetime(book["date"], errors="coerce").to_numpy()
    k_idx = bank.index.to_numpy()
    k_c = pd.to_numeric(bank["net_cents"], errors="coerce").fillna(0).to_numpy("int64")
    k_d = pd.to_datetime(bank["date"], errors="coerce").to_numpy()

    # 流水侧已用位置标记（增量维护，避免反复 np.isin）
    k_pos = {int(v): p for p, v in enumerate(k_idx)}
    k_used = np.zeros(len(k_idx), dtype=bool)
    for i in bank_used:
        p = k_pos.get(int(i))
        if p is not None:
            k_used[p] = True

    b_used_lookup = set(int(i) for i in book_used)

    for n in range(len(b_idx)):
        ji = int(b_idx[n])
        if ji in b_used_lookup:
            continue
        jc = int(b_c[n])

        # 向量化候选：同方向 + 差额≤max_fee + 日期窗口 + 未使用
        m = ((k_c > 0) == (jc > 0)) & (~k_used) & (np.abs(k_c - jc) <= max_fee)
        if pd.notna(b_d[n]):
            a64 = np.datetime64(pd.Timestamp(b_d[n]).to_datetime64())
            dd = np.abs((k_d - a64) / np.timedelta64(1, "D"))
            m &= (dd <= date_window) | np.isnan(dd)
        cand = np.nonzero(m)[0]

        for p in cand:
            bi = int(k_idx[p])
            bc = int(k_c[p])
            # 差额 ≤ 10元，且较大一侧的摘要含手续费关键词
            if abs(jc) >= abs(bc):
                summary = str(book.loc[ji, "summary"])
            else:
                summary = str(bank.loc[bi, "summary"])
            if not any(kw in summary for kw in FEE_KEYWORDS):
                continue
            diff = abs(jc - bc)
            matches.append({
                "book_idxs": [ji], "bank_idxs": [bi], "level": "L3_fee",
                "note": f"手续费差额规则：差额{round(diff/100, 2)}元，{summary[:30]}"
            })
            book_used.add(ji)
            bank_used.add(bi)
            b_used_lookup.add(ji)
            k_used[p] = True
            break
    return matches

def _check_balance_continuity(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """P5: 对账单余额连贯性检查——逐行验证 上日余额+发生=本日余额。

    对于有 balance 列的数据，检查每行余额是否等于上一行余额 + 本行净发生额。
    返回所有不连贯的行。
    """
    flags = []
    if "balance" not in df.columns or "net_amount" not in df.columns:
        return flags
    prev_bal = None
    for i, r in df.iterrows():
        cur_bal = r["balance"]
        net_amt = r["net_amount"]
        if pd.isna(cur_bal):
            continue
        if prev_bal is not None:
            expected = prev_bal + net_amt
            diff = round(abs(cur_bal - expected), 2)
            if diff > 0.02:  # 容差 ±0.02 元（允许舍入）
                flags.append({
                    "type": "余额不连贯", "side": "银行流水",
                    "rows": [r.get("row_id", str(i))],
                    "amount": round(float(cur_bal), 2),
                    "detail": (
                        f"上日余额 {round(float(prev_bal),2)} + 发生 {round(float(net_amt),2)}"
                        f" = {round(float(expected),2)}，但实际余额 {round(float(cur_bal),2)}，差额 {diff}"
                    ),
                })
        prev_bal = cur_bal
    return flags

def _match_l3(book: pd.DataFrame, bank: pd.DataFrame,
              book_used: set, bank_used: set,
              date_window: int, tol_cents: int,
              max_group: int = 3, max_candidates: int = 30) -> List[Dict[str, Any]]:
    """L3：同方向同窗口内 n:m 金额合计相等 → 拆分/合并入账。

    v3.5: 向量化候选池（numpy 掩码替代 iterrows 全表扫描），
          语义不变；候选池超上限时显式记日志，避免静默漏配。
    """
    import numpy as np
    matches: List[Dict[str, Any]] = []
    overflow_log = []

    def subset_hit(target_cents: int,
                   cand: List[Tuple[int, int, Any]]) -> Optional[List[int]]:
        if len(cand) > max_candidates:
            return None
        for size in range(2, max_group + 1):
            for combo in itertools.combinations(cand, size):
                if abs(sum(c[1] for c in combo) - target_cents) <= tol_cents:
                    return [c[0] for c in combo]
        return None

    def _arrays(src: pd.DataFrame):
        idx = src.index.to_numpy()
        cents = pd.to_numeric(src["net_cents"], errors="coerce").fillna(0).to_numpy(dtype="int64")
        dates = pd.to_datetime(src["date"], errors="coerce").to_numpy()
        return idx, cents, dates

    _cache = {}

    def window_pool(src_key: str, src: pd.DataFrame, used: set, sign: int, anchor) -> list:
        if src_key not in _cache:
            _cache[src_key] = _arrays(src)
        idx, cents, dates = _cache[src_key]
        if len(idx) == 0:
            return []
        m = (cents > 0) == (sign > 0)
        if used:
            m &= ~np.isin(idx, list(used))
        if pd.notna(anchor):
            anchor64 = np.datetime64(pd.Timestamp(anchor).to_datetime64())
            dd = np.abs((dates - anchor64) / np.timedelta64(1, "D"))
            # 与原语义一致：date 为空的记录保留（不做日期过滤）
            m &= (dd <= date_window) | np.isnan(dd)
        sel = np.nonzero(m)[0]
        return [(int(idx[i]), int(cents[i]), dates[i]) for i in sel]

    # 方向一：1 笔账 ←→ n 笔流水
    b_idx, b_cents, b_dates = _cache.get("bank") or _arrays(bank)
    _cache["bank"] = (b_idx, b_cents, b_dates)
    j_idx, j_cents, j_dates = _cache.get("book") or _arrays(book)
    _cache["book"] = (j_idx, j_cents, j_dates)

    for k in range(len(j_idx)):
        ji = int(j_idx[k])
        if ji in book_used:
            continue
        cents = int(j_cents[k])
        pool = window_pool("bank", bank, bank_used, cents, j_dates[k])
        if len(pool) > max_candidates:
            overflow_log.append(f"L3 overflow: book J{ji} cents={cents} pool={len(pool)}>{max_candidates}")
        hit = subset_hit(cents, pool)
        if hit:
            matches.append({"book_idxs": [ji], "bank_idxs": hit, "level": "L3",
                            "note": f"1笔账面↔{len(hit)}笔流水（拆分/合并入账）"})
            book_used.add(ji)
            bank_used.update(hit)

    # 方向二：n 笔账 ←→ 1 笔流水
    for k in range(len(b_idx)):
        bi = int(b_idx[k])
        if bi in bank_used:
            continue
        cents = int(b_cents[k])
        pool = window_pool("book", book, book_used, cents, b_dates[k])
        if len(pool) > max_candidates:
            overflow_log.append(f"L3 overflow: bank B{bi} cents={cents} pool={len(pool)}>{max_candidates}")
        hit = subset_hit(cents, pool)
        if hit:
            matches.append({"book_idxs": hit, "bank_idxs": [bi], "level": "L3",
                            "note": f"{len(hit)}笔账面↔1笔流水（拆分/合并入账）"})
            book_used.update(hit)
            bank_used.add(bi)

    if overflow_log:
        print("[L3] 超出组合能力（静默漏配→显式记录）:")
        for msg in overflow_log[:5]:
            print(f"  {msg}")
        if len(overflow_log) > 5:
            print(f"  ... 共 {len(overflow_log)} 条")

    return matches

import unicodedata
import re as _re_cp

def normalize_counterpart_name(s) -> str:
    """对手方名称归一化：消灭'同一个户的不同写法'。
    NFKC 处理全/半角（括号、字母、数字），再去掉所有空白和常见标点。"""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))          # （）→()、全角字母数字→半角
    s = _re_cp.sub(r"[\s　]+", "", s)                   # 半角/全角空格、换行
    s = _re_cp.sub(r"[,.，.。、·・''\"\"()（）\[\]【】\-—_*/\\]", "", s)  # 标点全部剥掉
    return s

def _align_counterpart_names(book_names, bank_names, min_len=3, fuzzy_th=92):
    """三级对齐：精确 → 包含 → 模糊兜底。
    模糊兜底红线：只有'唯一高分'才接受（第一名领先第二名≥5分），有歧义就放弃。"""
    from rapidfuzz import fuzz, process
    mapping = {}
    bank_list = [k for k in bank_names if len(k) >= min_len]
    for bn in book_names:
        bn = bn.strip()
        if len(bn) < min_len:
            continue
        # 1) 精确/包含
        hit = next((kn for kn in bank_list if bn == kn or bn in kn or kn in bn), None)
        # 2) 模糊兜底（错别字、个别字差异）
        if hit is None and bank_list:
            res = process.extract(bn, bank_list, scorer=fuzz.ratio,
                                  score_cutoff=fuzzy_th, limit=2)
            if res and (len(res) == 1 or res[0][1] - res[1][1] >= 5):
                hit = res[0][0]
        if hit:
            mapping[bn] = hit
    return mapping

def _match_l3_counterpart(book, bank, book_used, bank_used, tol_cents,
                          max_group=4, max_candidates=40):
    """对手方分区匹配：同名小区间内做 n:m 子集和。
    池子小（同户名几到几十笔），所以允许比全局 L3 更大的组。"""
    import itertools
    matches = []
    b_rem = book[~book.index.isin(book_used)]
    k_rem = bank[~bank.index.isin(bank_used)]
    _bad = {"", "nan", "none", "nat"}
    b_cp = b_rem[~b_rem["counterpart"].astype(str).str.strip().str.lower().isin(_bad)]
    b_cp = b_cp[b_cp["counterpart"].astype(str).str.strip().str.len() >= 2].copy()
    k_cp = k_rem[~k_rem["counterpart"].astype(str).str.strip().str.lower().isin(_bad)]
    k_cp = k_cp[k_cp["counterpart"].astype(str).str.strip().str.len() >= 2].copy()
    b_cp["_cp"] = b_cp["counterpart"].map(normalize_counterpart_name)
    k_cp["_cp"] = k_cp["counterpart"].map(normalize_counterpart_name)
    b_cp["_mk"] = b_cp["date"].dt.to_period("M")
    k_cp["_mk"] = k_cp["date"].dt.to_period("M")
    b_cp["_pos"] = b_cp["net_cents"].apply(lambda x: int(x) > 0)
    k_cp["_pos"] = k_cp["net_cents"].apply(lambda x: int(x) > 0)
    if b_cp.empty or k_cp.empty:
        print("[对手方分区] 一侧无对手方，跳过")
        return matches
    name_map = _align_counterpart_names(
        b_cp["_cp"].unique(),
        k_cp["_cp"].unique())
    print(f"[对手方分区] 账方户名 {b_cp['_cp'].nunique()} 个，"
          f"银方 {k_cp['_cp'].nunique()} 个，对齐成功 {len(name_map)} 个")
    # 诊断（调好后可删）
    print("账方未对齐户名示例:", sorted(set(b_cp["_cp"]) - set(name_map.keys()))[:10])
    print("银方未对齐户名示例:", sorted(set(k_cp["_cp"]) - set(name_map.values()))[:10])
    _sizes = k_cp[k_cp["_cp"].isin(name_map.values())].groupby("_cp").size().sort_values(ascending=False)
    print(f"对齐户名的银方池子: >40笔的有 {(_sizes > 40).sum()} 个，最大5个: {_sizes.head(5).to_dict()}")

    for bname, kname in name_map.items():
        bg = b_cp[b_cp["_cp"] == bname]
        kg = k_cp[k_cp["_cp"] == kname]
        # ↓↓↓ 第一层：同户名+同月+同额 1:1 配对（大池子削峰） ↓↓↓
        _kg_avail = kg[~kg.index.isin(bank_used)]
        for ji, jr in bg.iterrows():
            if ji in book_used:
                continue
            cand = _kg_avail[(~_kg_avail.index.isin(bank_used)) &
                             (_kg_avail["_mk"] == jr["_mk"]) &
                             (_kg_avail["net_cents"] == jr["net_cents"])]
            if len(cand) == 1:                      # 恰好唯一才自动确认，多个候选留给子集和
                bi = cand.index[0]
                matches.append({"book_idxs": [ji], "bank_idxs": [bi],
                                "level": "L3_counterpart",
                                "note": f"对手方[{kname}]：同月同额1:1"})
                book_used.add(ji)
                bank_used.add(bi)
        # ↓↓↓ 第二层：同户同月总额闭环（账n笔↔银m笔，月度净额相等即整组核销） ↓↓↓
        _bg_av = bg[~bg.index.isin(book_used)]
        _kg_av = kg[~kg.index.isin(bank_used)]
        if not _bg_av.empty and not _kg_av.empty:
            _bm = _bg_av.groupby(_bg_av["_mk"])["net_cents"].sum()
            _km = _kg_av.groupby(_kg_av["_mk"])["net_cents"].sum()
            for _m in _bm.index:
                if _m not in _km.index:
                    continue
                if abs(int(_bm[_m]) - int(_km[_m])) <= tol_cents:
                    _bidx = _bg_av[(_bg_av["_mk"] == _m) & (~_bg_av.index.isin(book_used))].index.tolist()
                    _kidx = _kg_av[(_kg_av["_mk"] == _m) & (~_kg_av.index.isin(bank_used))].index.tolist()
                    if _bidx and _kidx:
                        matches.append({
                            "book_idxs": _bidx, "bank_idxs": _kidx,
                            "level": "L3_counterpart",
                            "note": (f"对手方[{kname}] {str(_m)[:7]}月闭环："
                                     f"账{len(_bidx)}笔↔银{len(_kidx)}笔 总额相等")})
                        book_used.update(_bidx)
                        bank_used.update(_kidx)
        # 方向一：1笔账 ↔ n笔银（同方向，即净额同号）
        for ji, jr in bg.iterrows():
            if ji in book_used:
                continue
            cents = int(jr["net_cents"])
            pool = kg[(~kg.index.isin(bank_used)) & (kg["_mk"] == jr["_mk"]) &
                      (kg["_pos"] == (cents > 0))]
            if pool.empty or len(pool) > max_candidates:
                continue
            idxs, vals = pool.index.tolist(), [int(x) for x in pool["net_cents"]]
            hit = None
            for n in range(1, min(max_group, len(idxs)) + 1):
                for combo in itertools.combinations(range(len(idxs)), n):
                    if sum(vals[i] for i in combo) == cents:
                        hit = [idxs[i] for i in combo]
                        break
                if hit:
                    break
            if hit:
                matches.append({"book_idxs": [ji], "bank_idxs": hit,
                                "level": "L3_counterpart",
                                "note": f"对手方[{kname}]：账1笔↔银{len(hit)}笔"})
                book_used.add(ji)
                bank_used.update(hit)
        # 方向二：n笔账 ↔ 1笔银
        kg2 = kg[~kg.index.isin(bank_used)]
        for bi, br in kg2.iterrows():
            cents = int(br["net_cents"])
            pool = bg[(~bg.index.isin(book_used)) & (bg["_mk"] == br["_mk"]) & 
                      (bg["_pos"] == (cents > 0))]
            if pool.empty or len(pool) > max_candidates:
                continue
            idxs, vals = pool.index.tolist(), [int(x) for x in pool["net_cents"]]
            hit = None
            for n in range(1, min(max_group, len(idxs)) + 1):
                for combo in itertools.combinations(range(len(idxs)), n):
                    if sum(vals[i] for i in combo) == cents:
                        hit = [idxs[i] for i in combo]
                        break
                if hit:
                    break
            if hit:
                matches.append({"book_idxs": hit, "bank_idxs": [bi],
                                "level": "L3_counterpart",
                                "note": f"对手方[{kname}]：账{len(hit)}笔↔银1笔"})
                book_used.update(hit)
                bank_used.add(bi)
    return matches

def _match_l4(book: pd.DataFrame, bank: pd.DataFrame,
              book_used: set, bank_used: set,
              fuzzy_threshold: int = 85,
              amount_pct: float = 0.01) -> List[Dict[str, Any]]:
    """L4 v3.4：金额分桶预过滤 + RapidFuzz 精排（不再 O(n^2) 全量比较）。"""
    matches = []
    try:
        from rapidfuzz import fuzz
    except ImportError:
        return matches

    left = book[~book.index.isin(book_used)][["summary", "counterpart", "net_cents"]].copy()
    right = bank[~bank.index.isin(bank_used)][["summary", "counterpart", "net_cents"]].copy()
    if left.empty or right.empty:
        return matches

    left["cents_bkt"] = (left["net_cents"] / 100).round().astype("int64")
    right["cents_bkt"] = (right["net_cents"] / 100).round().astype("int64")
    left["_idx"] = left.index
    right["_idx"] = right.index

    cand = pd.merge(left, right, on="cents_bkt", suffixes=("_L", "_R"))
    if cand.empty:
        return matches

    cand["pct"] = abs(cand["net_cents_L"] - cand["net_cents_R"]) / cand[["net_cents_L", "net_cents_R"]].abs().max(axis=1).clip(lower=1)
    cand = cand[cand["pct"] <= amount_pct]
    cand = cand[(cand["net_cents_L"] > 0) == (cand["net_cents_R"] > 0)]
    if cand.empty:
        return matches

    ranked = {}
    for _, row in cand.iterrows():
        ji, bi = int(row["_idx_L"]), int(row["_idx_R"])
        if ji in book_used or bi in bank_used:
            continue
        score = fuzz.token_set_ratio(
            f"{row['summary_L']} {row['counterpart_L']}".strip(),
            f"{row['summary_R']} {row['counterpart_R']}".strip())
        if score >= fuzzy_threshold:
            if ji not in ranked or score > ranked[ji][1]:
                ranked[ji] = (bi, score)

    for ji, (bi, score) in sorted(ranked.items(), key=lambda x: x[1][1], reverse=True):
        if ji in book_used or bi in bank_used:
            continue
        matches.append({"book_idx": ji, "bank_idx": bi, "level": "L4",
                        "score": round(score, 1),
                        "note": "模糊匹配，待人工复核"})
        book_used.add(ji)
        bank_used.add(bi)
    return matches

    return matches


CAT_BANK_RECV = "银收企未收"
CAT_BANK_PAY = "银付企未付"
CAT_ENT_RECV = "企收银未收"
CAT_ENT_PAY = "企付银未付"
CAT_REVIEW = "待人工核查"


def _row_payload(r, side):
    return {"side": side, "row_id": r["row_id"], "src_index": int(r["src_index"]),
            "date": r["date"].date().isoformat() if pd.notna(r["date"]) else "",
            "net_amount": round(float(r["net_amount"]), 2),
            "summary": r["summary"], "counterpart": r["counterpart"],
            "voucher_no": r["voucher_no"], "account": r["account"]}


def classify_unmatched(book, bank, book_used, bank_used, date_window):
    def fw(s):
        for w in ("利息","结息"):
            if w in s: return "利息"
        for w in ("手续费","短信费","年费","账户管理费","工本费","服务费"):
            if w in s: return "银行费用"
        for w in ("冲正","冲销","红冲","撤销"):
            if w in s: return "冲正"
        return ""
    max_bd, max_bb = book["date"].max(), bank["date"].max()
    ub, ubn = [], []
    for ji, r in book.iterrows():
        if ji in book_used: continue
        item = _row_payload(r, "book")
        s = fw(str(r["summary"]))
        if s: item["special"] = s
        if any(w in str(r["summary"]) for w in _INTERBANK):
            item["classification"] = "他行互转-待他行流水"
            item["basis"] = "资金在企业其他银行账户间划转，超出本账户流水范围"
        else:
            ne = pd.notna(r["date"]) and pd.notna(max_bb) and (max_bb - r["date"]).days <= date_window
            item["classification"] = (CAT_ENT_RECV if r["net_cents"] > 0 else CAT_ENT_PAY) if ne else CAT_REVIEW
            item["basis"] = f"接近期末(≤{date_window}天),需期后验证" if ne else "无窗口证据,需人工查明"
        ub.append(item)
    for bi, r in bank.iterrows():
        if bi in bank_used: continue
        item = _row_payload(r, "bank")
        s = fw(str(r["summary"]))
        if s: item["special"] = s
        _txt = str(r["summary"]) + str(r.get("counterpart", ""))
        if any(w in _txt for w in _INTERBANK):
            item["classification"] = "他行互转-待他行流水"
            item["basis"] = "收款/付款方为企业其他银行账户，超出本账户范围"
        else:
            ne = pd.notna(r["date"]) and pd.notna(max_bd) and (max_bd - r["date"]).days <= date_window
            item["classification"] = (CAT_BANK_RECV if r["net_cents"] > 0 else CAT_BANK_PAY) if ne else CAT_REVIEW
            item["basis"] = f"接近期末(≤{date_window}天),需期后验证" if ne else "无窗口证据,需人工查明"
        ubn.append(item)
    return ub, ubn


def detect_duplicates(std_df, side, day_gap=1):
    from collections import defaultdict
    flagged, groups = [], defaultdict(list)
    for i, r in std_df.iterrows():
        d = r["date"]
        groups[(int(r["net_cents"]), d.date().isoformat()[:7] if pd.notna(d) else "NaT")].append(i)
    for idxs in groups.values():
        if len(idxs) < 2: continue
        idxs = sorted(idxs, key=lambda i: (pd.isna(std_df.loc[i,"date"]), std_df.loc[i,"date"]))
        cl = [idxs[0]]
        for p, c in zip(idxs, idxs[1:]):
            d1, d2 = std_df.loc[p,"date"], std_df.loc[c,"date"]
            if pd.notna(d1) and pd.notna(d2) and abs((d2-d1).days) <= day_gap: cl.append(c)
            else:
                if len(cl) >= 2:
                    flagged.append({"side":side,"rows":[std_df.loc[i,"row_id"] for i in cl],
                                    "net_amount":round(float(std_df.loc[cl[0],"net_amount"]),2),
                                    "reason":f"同侧同额{len(cl)}笔相隔≤{day_gap}天"})
                cl = [c]
        if len(cl) >= 2:
            flagged.append({"side":side,"rows":[std_df.loc[i,"row_id"] for i in cl],
                            "net_amount":round(float(std_df.loc[cl[0],"net_amount"]),2),
                            "reason":f"同侧同额{len(cl)}笔相隔≤{day_gap}天"})
    return flagged


def build_balance_reconciliation(bc, sc, ub, ubn):
    def sc_sum(items, cat): return round(sum(i["net_amount"] for i in items if i.get("classification")==cat),2)
    br, bp = sc_sum(ubn, CAT_BANK_RECV), sc_sum(ubn, CAT_BANK_PAY)
    er, ep = sc_sum(ub, CAT_ENT_RECV), sc_sum(ub, CAT_ENT_PAY)
    bc = bc or 0.0; sc = sc or 0.0
    return pd.DataFrame([
        {"项目":"企业账面余额","金额":round(bc,2)},
        {"项目":"加:银收企未收","金额":br},
        {"项目":"减:银付企未付","金额":round(-bp,2)},
        {"项目":"调节后企业余额","金额":round(bc+br+bp,2)},
        {"项目":"银行对账单余额","金额":round(sc,2)},
        {"项目":"加:企收银未收","金额":er},
        {"项目":"减:企付银未付","金额":round(-ep,2)},
        {"项目":"调节后银行余额","金额":round(sc+er+ep,2)},
        {"项目":"调节差异","金额":round((bc+br+bp)-(sc+er+ep),2)},
    ])


def detect_fee_small_monthly_diff(book, bank, max_small_cents=5000):
    """账面费用 vs 流水费用外收：同月两侧都有费用、月度差为固定小额(≤50元)
    → 疑似费用扣自其他账户或漏记，列为审计线索（不核销、不改分类）。"""
    fee_kw = ("手续费", "短信费", "年费", "账户管理费", "工本费", "服务费")
    bf = book[book["summary"].astype(str).str.contains("|".join(fee_kw), na=False)]
    kf = bank[bank["summary"].astype(str).str.strip() == "费用外收"]
    if bf.empty or kf.empty:
        return []
    bm = bf.groupby(bf["date"].dt.to_period("M"))["net_cents"].sum()
    km = kf.groupby(kf["date"].dt.to_period("M"))["net_cents"].sum()
    flags = []
    for m in bm.index:
        if m not in km.index:
            continue
        diff = int(bm[m] - km[m])
        if 0 < abs(diff) <= max_small_cents:
            flags.append({
                "side": "book", "type": "费用小额月度差异",
                "detail": (f"{m}月账面费用合计{abs(bm[m])/100:.2f}元 vs 流水费用外收"
                           f"{abs(km[m])/100:.2f}元，差{abs(diff)/100:.2f}元——"
                           f"费用或扣自其他账户、或属漏记/跨期，建议索取其他账户流水核对"),
                "rows": bf[(bf["date"].dt.to_period("M") == m)]["row_id"].tolist(),
            })
    return flags

def detect_counterpart_month_gap(book, bank, min_cents=100_000_000):
    """同户名月度透视：一侧整月为零、另一侧≥阈值(默认100万) → 账外收支嫌疑红旗。"""
    def _pv(df):
        d = df[df["counterpart"].astype(str).str.strip().str.len() >= 2].copy()
        d["_cp"] = d["counterpart"].map(normalize_counterpart_name)
        d = d[d["_cp"].str.len() >= 2]
        return d.groupby(["_cp", d["date"].dt.to_period("M")])["net_cents"].sum()
    bm, km = _pv(book), _pv(bank)
    flags = []
    for cp in set(bm.index.get_level_values(0)) | set(km.index.get_level_values(0)):
        months = set(bm.loc[cp].index if cp in bm.index.get_level_values(0) else []) | \
                 set(km.loc[cp].index if cp in km.index.get_level_values(0) else [])
        for m in months:
            b = int(bm.get((cp, m), 0)); k = int(km.get((cp, m), 0))
            if (b == 0) != (k == 0) and max(abs(b), abs(k)) >= min_cents:
                side = "银有账无" if b == 0 else "账有银无"
                flags.append({"side": "both", "type": "对手方整月单边记录",
                              "detail": (f"对手方[{cp}] {m}月 {side}，单边金额"
                                         f"{max(abs(b), abs(k))/100:,.2f}元——"
                                         f"疑似账外收支或跨户混记，需重点核查")})
    return flags

def aggregate_red_flags(red_flags, top_n=20):
    """红旗聚合输出：同类归并为摘要旗（数量+最大若干示例），
    明细不丢——由 export 写进 Excel/底稿，摘要在报告里可读。"""
    from collections import defaultdict
    by_type = defaultdict(list)
    for f in red_flags:
        by_type[f.get("type", "其他")].append(f)
    out = []
    for t, fs in by_type.items():
        if len(fs) <= top_n:
            out.extend(fs)
            continue
        fs_sorted = sorted(fs, key=lambda f: len(str(f.get("detail", ""))), reverse=True)
        out.append({
            "side": "both", "type": t,
            "detail": (f"共{len(fs)}项，示例：{fs_sorted[0].get('detail', '')[:60]}；"
                       f"……明细见《异常资金交易清单》"),
            "count": len(fs)})
    return out

def detect_red_flags(std_df, side, pair_window=3, large_threshold=100000.0, round_unit=10000, burst_days=7, burst_count=3):
    flags = []; df = std_df

    # 一收一付同额（v3.4: pandas merge 向量化，替代 O(n²) 双重循环）
    if len(df) > 100:
        try:
            sub = df[df["abs_cents"] > 0][["abs_cents", "net_cents", "date", "row_id"]].copy()
            sub["_sign"] = (sub["net_cents"] > 0).astype(int)
            m = pd.merge(sub[sub["_sign"] == 1], sub[sub["_sign"] == 0], on="abs_cents", suffixes=("_in", "_out"))
            if not m.empty:
                m["gap"] = (m["date_in"] - m["date_out"]).dt.days.abs()
                m = m[m["gap"] <= pair_window]
                seen = set()
                for _, r in m.iterrows():
                    key = tuple(sorted((r["row_id_in"], r["row_id_out"])))
                    if key not in seen:
                        seen.add(key)
                        flags.append({"type": "一收一付同额", "side": side, "rows": list(key),
                                      "amount": round(r["abs_cents"] / 100, 2),
                                      "detail": f"同额资金{int(r['gap'])}天一进一出"})
        except Exception:
            pass  # 量大时降级跳过，不阻塞主流程
    else:
        from collections import defaultdict
        by_abs = defaultdict(list)
        for i, r in df.iterrows(): by_abs[int(r["abs_cents"])].append(i)
        seen = set()
        for cents, idxs in by_abs.items():
            if cents == 0 or len(idxs) < 2: continue
            ins = [i for i in idxs if df.loc[i, "net_cents"] > 0]
            outs = [i for i in idxs if df.loc[i, "net_cents"] < 0]
            for i in ins:
                for j in outs:
                    d1, d2 = df.loc[i, "date"], df.loc[j, "date"]
                    gap = abs((d1 - d2).days) if pd.notna(d1) and pd.notna(d2) else 0
                    key = tuple(sorted((df.loc[i, "row_id"], df.loc[j, "row_id"])))
                    if gap <= pair_window and key not in seen:
                        seen.add(key)
                        flags.append({"type": "一收一付同额", "side": side, "rows": list(key),
                                      "amount": round(cents / 100, 2), "detail": f"同额资金{gap}天内一进一出"})

    # 整数大额
    for i, r in df.iterrows():
        amt = abs(float(r["net_amount"]))
        if amt >= large_threshold and int(amt) % round_unit == 0:
            flags.append({"type": "整数大额", "side": side, "rows": [r["row_id"]],
                          "amount": round(amt, 2), "detail": f"单笔>{large_threshold:,.0f}且整{round_unit}倍数"})

    # 期末负余额
    bal = df["balance"].dropna()
    if len(bal) > 0 and float(bal.iloc[-1]) < 0:
        flags.append({"type": "期末负余额", "side": side, "rows": [],
                      "amount": round(float(bal.iloc[-1]), 2), "detail": "透支/未入账负债"})

    # 分次转入转出
    cp = df[df["counterpart"].astype(str).str.len() >= 2]
    for name, grp in cp.groupby("counterpart"):
        grp = grp.sort_values("date"); dates = grp["date"].tolist(); idxs = grp.index.tolist()
        for k in range(len(idxs)):
            d0 = dates[k]
            if pd.isna(d0): continue
            wi = [x for x, d in zip(idxs, dates) if pd.notna(d) and 0 <= (d - d0).days <= burst_days]
            if len(wi) >= burst_count:
                sub = df.loc[wi]
                if (sub["net_cents"] > 0).any() and (sub["net_cents"] < 0).any():
                    flags.append({"type": "分次转入转出", "side": side,
                                  "rows": [df.loc[x, "row_id"] for x in wi],
                                  "amount": round(float(sub["net_amount"].abs().sum()), 2),
                                  "detail": f"对手方[{name}] {burst_days}天内{len(wi)}笔双向资金往来"})
                break

    # 大额现金
    for i, r in df.iterrows():
        if "现金" in str(r["summary"]) and abs(float(r["net_amount"])) >= 50000:
            flags.append({"type": "大额现金", "side": side, "rows": [r["row_id"]],
                          "amount": round(abs(float(r["net_amount"])), 2), "detail": "大额现金收支"})
    return flags


def run_bank_reconciliation(book_df, bank_df, config=None, progress_callback=None):
    cfg = dict(config or {})
    date_window = int(cfg.get("date_window_days", 3))
    tol = float(cfg.get("amount_tolerance", 0.01))
    tol_cents = max(1, int(round(tol * 100)))
    fuzzy_th = int(cfg.get("fuzzy_threshold", 85))

    def _p(pct, step):
        if progress_callback:
            try: progress_callback(pct, step)
            except: pass

    _p(5, "类型识别与列映射")
    book_type = cfg.get("book_type") or detect_book_type(book_df, cfg.get("book_file", ""))
    bank_type = cfg.get("bank_type") or detect_book_type(bank_df, cfg.get("bank_file", ""))
    if book_type == "unknown": book_type = JOURNAL
    if bank_type == "unknown": bank_type = BANK_STATEMENT
    book_map = cfg.get("book_mapping") or auto_map_columns(book_df, book_type)
    bank_map = cfg.get("bank_mapping") or auto_map_columns(bank_df, bank_type)
    if cfg.get("use_llm_mapping", False):  # v3.4: 默认不调 LLM，列映射规则足够
        try:
            from core.column_semantics import detect_roles_with_llm
            _llm = cfg.get("llm_callable")
            for side, df_, mp in (("book", book_df, book_map), ("bank", bank_df, bank_map)):
                need = ("date" not in mp) or not any(r in mp for r in ("amount", "debit", "credit"))
                if need:
                    extra = detect_roles_with_llm(df_, f"{side}_table", llm_callable=_llm)
                    for role in ("date", "amount", "debit", "credit", "summary", "counterpart", "account", "voucher_no", "balance"):
                        if role not in mp and role in extra: mp[role] = extra[role]
        except Exception: pass
    def _orient(mp, fb):
        if "借" in str(mp.get("debit", "")): return JOURNAL
        return BANK_STATEMENT
    if book_type == GENERIC_LEDGER: book_type = _orient(book_map, JOURNAL)
    if bank_type == GENERIC_LEDGER: bank_type = _orient(bank_map, BANK_STATEMENT)

    _p(10, "方向镜像归一化")
    _bo = book_type if book_type in (JOURNAL, BANK_STATEMENT) else JOURNAL
    _bk = bank_type if bank_type in (JOURNAL, BANK_STATEMENT) else BANK_STATEMENT
    book_std = normalize_to_std(book_df, book_map, _bo, cfg.get("book_file", ""))
    bank_std = normalize_to_std(bank_df, bank_map, _bk, cfg.get("bank_file", ""))
    book_std = tag_content(book_std); bank_std = tag_content(bank_std)

    # v3.4: L4 默认关闭（RapidFuzz 语义匹配 O(n²) 太重，L1-L3+L3_fee 已覆盖确定匹配）
    _enable_l4 = cfg.get("enable_l4", False)  # 需要时显式开启
    bank_std, account_note = filter_bank_account(bank_std, cfg.get("account"))
    tie_book = tie_out_balance(book_std, cfg.get("book_opening"), cfg.get("book_closing"))
    tie_bank = tie_out_balance(bank_std, cfg.get("bank_opening"), cfg.get("bank_closing"))
    # v3.10: 账方对手方缺失时，从摘要提取主体（序时账无对方列形态）
    if book_std["counterpart"].fillna("").astype(str).str.strip().eq("").mean() > 0.5:
        print("[对手方] 账方对手方缺失>50%，从摘要提取主体")
        book_std["counterpart"] = book_std["summary"].map(extract_counterpart)
    # v3.7: 日期偏移自适应——月末批量记账形态（账面记账日系统性滞后交易日）
    _p(12, "日期粒度检测")
    _granularity = _detect_date_granularity(book_std)
    book_m = book_std.copy(); bank_m = bank_std.copy()
    if _granularity == "month":
        print("[日期粒度] 账面为月末批量记账形态，匹配按 年月+金额 对齐（日信息不参与勾对）")
        book_m["date"] = pd.to_datetime(book_m["date"]).dt.to_period("M").dt.to_timestamp()
        bank_m["date"] = pd.to_datetime(bank_m["date"]).dt.to_period("M").dt.to_timestamp()
    book_used, bank_used = set(), set()
    _p(14, "L3_fee_month 手续费月度聚合（前置）")
    m3_fee_month = _match_l3_fee_monthly(book_m, bank_m, book_used, bank_used, tol_cents)
    # 费用行隔离：通用匹配不碰费用行，费用只走 fee 规则（防"货款↔费用外收"错配）
    from config.dictionary import FEE_WORDS
    _FEE_KW = "|".join(FEE_WORDS[:7]) 
    book_g = book_m[~book_m["summary"].astype(str).str.contains(_FEE_KW, na=False)]
    bank_g = bank_m[bank_m["summary"].astype(str).str.strip() != "费用外收"]
    _p(14.5, "L3_counterpart 对手方分区匹配（前置）")
    matches_cp = _match_l3_counterpart(book_g, bank_g, book_used, bank_used, tol_cents)
    _p(15, "L1 金额精确+同日匹配")
    
    m1 = _match_l1(book_g, bank_g, book_used, bank_used, tol_cents)
    _p(25, "L2 金额精确+日期窗口匹配")
    m2 = _match_l2(book_g, bank_g, book_used, bank_used, date_window, tol_cents)
    _p(40, "L3 n:m 拆分合并匹配")
    m3 = _match_l3(book_g, bank_g, book_used, bank_used, date_window, tol_cents)
    # ↓ 新增
    _p(45, "L3_month 月末汇总匹配")
    if _granularity == "month":   # 只有月末记账形态才启用，探测不到不动
        m3_month = _match_l3_month_remainder(book_g, bank_g, book_used, bank_used, tol_cents)
    else:
        m3_month = []

    _p(50, "L3_fee 手续费差额匹配")
    m3_fee = _match_l3_fee_difference(book_m, bank_m, book_used, bank_used, date_window)
    
    if not _enable_l4:
        _p(60, "L4 跳过（数据规模大，L1-L3已覆盖核心匹配）")
        m4 = []
    else:
        _p(60, "L4 模糊匹配")
        m4 = _match_l4(book_m, bank_m, book_used, bank_used, fuzzy_th)

    _p(70, "未匹配项四分类")
    unmatched_book, unmatched_bank = classify_unmatched(book_std, bank_std, book_used, bank_used, date_window)
    duplicates = detect_duplicates(book_std, "book") + detect_duplicates(bank_std, "bank")

    bc = tie_book.get("closing"); sc = tie_bank.get("closing")
    if bc is None and tie_book.get("opening") is not None: bc = round(tie_book["opening"] + tie_book["total_net"], 2)
    if sc is None and tie_bank.get("opening") is not None: sc = round(tie_bank["opening"] + tie_bank["total_net"], 2)
    recon_table = build_balance_reconciliation(bc, sc, unmatched_book, unmatched_bank)

    _p(80, "红旗检测与余额连贯性")
    red_flags = detect_red_flags(bank_std, "bank") + detect_red_flags(book_std, "book")
    red_flags += _check_balance_continuity(bank_std)
    red_flags += detect_fee_small_monthly_diff(book_std, bank_std)
    red_flags += detect_counterpart_month_gap(book_std, bank_std)
    red_flags = aggregate_red_flags(red_flags)

    n_book, n_bank = len(book_std), len(bank_std)
    stats = {
        "book_rows": n_book, "bank_rows": n_bank,
        "book_type_detected": book_type, "bank_type_detected": bank_type,
        "book_mapping": book_map, "bank_mapping": bank_map,
        "account_filter": account_note,
        "matched_L1": len(m1), "matched_L2": len(m2),
        "matched_L3_groups": len(m3) + len(m3_month) + len(m3_fee) + len(m3_fee_month) + len(matches_cp),
        "matched_L3_fee": len(m3_fee), "matched_L3_subset": len(m3), "review_L4": len(m4),
        "matched_L3_month": len(m3_month), "matched_L3_fee_month": len(m3_fee_month),
        "matched_L3_counterpart": len(matches_cp),
        "book_matched": len(book_used), "bank_matched": len(bank_used),
        "book_match_rate": round(len(book_used)/n_book*100,2) if n_book else 0.0,
        "bank_match_rate": round(len(bank_used)/n_bank*100,2) if n_bank else 0.0,
        "unmatched_book": len(unmatched_book), "unmatched_bank": len(unmatched_bank),
        "date_granularity": _granularity,
        "interbank_transfers": sum(1 for i in unmatched_book + unmatched_bank if i.get("classification") == "他行互转-待他行流水"),
        "timing_categories": {
            CAT_BANK_RECV: sum(1 for i in unmatched_bank if i["classification"]==CAT_BANK_RECV),
            CAT_BANK_PAY: sum(1 for i in unmatched_bank if i["classification"]==CAT_BANK_PAY),
            CAT_ENT_RECV: sum(1 for i in unmatched_book if i["classification"]==CAT_ENT_RECV),
            CAT_ENT_PAY: sum(1 for i in unmatched_book if i["classification"]==CAT_ENT_PAY),
            CAT_REVIEW: sum(1 for i in unmatched_book+unmatched_bank if i["classification"]==CAT_REVIEW),
        },
        "red_flag_count": len(red_flags),
        "tolerance": f"{tol}元", "date_window_days": date_window,
    }
    _p(100, "完成")
    return {"stats": stats, "tie_out": {"book": tie_book, "bank": tie_bank},
            "matches_L1": m1, "matches_L2": m2, "groups_L3": m3 + m3_month + m3_fee + m3_fee_month + matches_cp, "review_L4": m4,
            "unmatched_book": unmatched_book, "unmatched_bank": unmatched_bank,
            "all_matches":m1 + m2 + m3 + m3_month + m3_fee + m3_fee_month + matches_cp + m4,
            "duplicates": duplicates, "balance_reconciliation": recon_table.to_dict("records"),
            "red_flags": red_flags, "book_std": book_std, "bank_std": bank_std, "config": cfg}


def _build_detail_workpaper(result):
    book_std = result["book_std"]; status = {}
    for m in result["matches_L1"] + result["matches_L2"]:
        status[m["book_idx"]] = {"状态":"已核对","层级":m["level"],"对方行":result["bank_std"].loc[m["bank_idx"],"row_id"],"备注":m["note"]}
    for m in result["groups_L3"]:
        bank_list = m.get("bank_idxs") or ([m["bank_idx"]] if "bank_idx" in m else [])
        book_list = m.get("book_idxs") or ([m["book_idx"]] if "book_idx" in m else [])
        partners = ",".join(result["bank_std"].loc[b,"row_id"] for b in bank_list)
        for j in book_list: 
            status[j] = {"状态":"已核对","层级":m["level"],"对方行":partners,"备注":m["note"]}
    for m in result["review_L4"]:
        status[m["book_idx"]] = {"状态":"待人工复核","层级":m["level"],"对方行":result["bank_std"].loc[m["bank_idx"],"row_id"],"备注":m["note"]}
    unmatched_cls = {i["row_id"]:i for i in result["unmatched_book"]}
    rows = []
    for ji, r in book_std.iterrows():
        base = {"行号":r["row_id"],"日期":r["date"].date().isoformat() if pd.notna(r["date"]) else "","凭证号":r["voucher_no"],"摘要":r["summary"],"对方":r["counterpart"],"借方金额":round(float(r["debit"]),2),"贷方金额":round(float(r["credit"]),2),"净额":round(float(r["net_amount"]),2)}
        if ji in status: base.update(status[ji])
        else:
            cls = unmatched_cls.get(r["row_id"],{})
            base.update({"状态":cls.get("classification","待人工核查"),"层级":"-","对方行":"-","备注":cls.get("basis","")})
        rows.append(base)
    return pd.DataFrame(rows)




def _build_marked_sheets(result):
    """逐笔标记底稿：银行流水侧+序时账侧，每笔标注完整对账状态"""
    book_std, bank_std = result["book_std"], result["bank_std"]

    # 银行流水侧
    bank_status = {}
    for m in result["matches_L1"] + result["matches_L2"]:
        bank_status[m["bank_idx"]] = "对银成功"
    for m in result["groups_L3"]:
        for bi in m.get("bank_idxs") or ([m["bank_idx"]] if "bank_idx" in m else []):
            bank_status[bi] = "对银成功(L3)"
    for m in result["review_L4"]:
        bank_status[m["bank_idx"]] = "对银成功(L4)"
    bank_rows = []
    unmatched_bank_map = {i["row_id"]: i for i in result["unmatched_bank"]}
    for bi, r in bank_std.iterrows():
        row = {"行号": r["row_id"], "日期": str(r["date"])[:10], "摘要": r["summary"],
               "对方": r.get("counterpart", ""), "净额": round(float(r["net_amount"]), 2)}
        if bi in bank_status:
            row["对账状态"] = bank_status[bi]
        else:
            cls = unmatched_bank_map.get(r["row_id"], {})
            row["对账状态"] = cls.get("classification", "待人工核查")
            row["依据"] = cls.get("basis", "")
        bank_rows.append(row)
    bank_df = pd.DataFrame(bank_rows)

    # 序时账侧
    book_status = {}
    for m in result["matches_L1"] + result["matches_L2"]:
        book_status[m["book_idx"]] = "对账成功"
    for m in result["groups_L3"]:
        for ji in m.get("book_idxs") or ([m["book_idx"]] if "book_idx" in m else []):
            book_status[ji] = "对账成功(L3)"
    for m in result["review_L4"]:
        book_status[m["book_idx"]] = "对账成功(L4)"
    book_rows = []
    unmatched_book_map = {i["row_id"]: i for i in result["unmatched_book"]}
    for ji, r in book_std.iterrows():
        row = {"行号": r["row_id"], "日期": str(r["date"])[:10], "凭证号": r["voucher_no"],
               "摘要": r["summary"], "对方": r.get("counterpart", ""),
               "借方": round(float(r["debit"]), 2), "贷方": round(float(r["credit"]), 2),
               "净额": round(float(r["net_amount"]), 2)}
        if ji in book_status:
            row["对账状态"] = book_status[ji]
        else:
            cls = unmatched_book_map.get(r["row_id"], {})
            row["对账状态"] = cls.get("classification", "待人工核查")
            row["依据"] = cls.get("basis", "")
        book_rows.append(row)
    book_df = pd.DataFrame(book_rows)
    return bank_df, book_df


def export_reconciliation_outputs(result, out_dir):
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True); written = []
    detail = _build_detail_workpaper(result)
    p = out / "逐笔对账明细底稿.xlsx"; detail.to_excel(str(p), index=False); written.append(p.name)
    recon = pd.DataFrame(result["balance_reconciliation"])
    # ↓↓↓ 新增：日期口径披露（有偏移时才出现）↓↓↓
    if result["stats"].get("date_granularity") == "month":
        recon = pd.concat([recon, pd.DataFrame([
            {"项目": "—— 口径说明 ——", "金额": ""},
            {"项目": "日期口径：账面为月末批量记账，本次按 年月+金额 勾对（日信息不参与）。", "金额": ""},
        ])], ignore_index=True)
    # ↑↑↑ 新增结束 ↑↑↑
    p = out / "银行存款余额调节表.xlsx"; recon.to_excel(str(p), index=False); written.append(p.name)
    un_book = [{**i,"方向":"企业账"} for i in result["unmatched_book"]]
    un_bank = [{**i,"方向":"银行流水"} for i in result["unmatched_bank"]]
    unmatched = pd.DataFrame(un_book + un_bank)
    if not unmatched.empty: unmatched = unmatched.rename(columns={"date":"日期","net_amount":"净额","summary":"摘要","counterpart":"对方","classification":"分类","basis":"依据","special":"标记"})
    p = out / "未达账项与待核查清单.xlsx"; unmatched.to_excel(str(p), index=False); written.append(p.name)
    # v3.9: 逐笔标记底稿
    try:
        bank_marked, book_marked = _build_marked_sheets(result)
        bank_marked.to_excel(str(out / "银行流水匹配情况.xlsx"), index=False)
        book_marked.to_excel(str(out / "序时账匹配情况.xlsx"), index=False)
        written += ["银行流水匹配情况.xlsx", "序时账匹配情况.xlsx"]
    except Exception as _e:
        print(f"[export] 逐笔标记底稿（匹配情况）生成失败: {type(_e).__name__}: {_e}")
    flags = pd.DataFrame(result["red_flags"] + [{**d,"type":"疑似重复入账","detail":d["reason"]} for d in result["duplicates"]])
    if not flags.empty: flags = flags.rename(columns={"type":"类型","side":"侧别","rows":"涉及行","amount":"金额","detail":"说明"})
    p = out / "异常资金交易清单.xlsx"; flags.to_excel(str(p), index=False); written.append(p.name)
    summary = {"stats":result["stats"],"tie_out":result["tie_out"],"reconciliation":result["balance_reconciliation"]}
    p = out / "reconciliation_summary.json"; p.write_text(json.dumps(summary,ensure_ascii=False,indent=2,default=str),encoding="utf-8"); written.append(p.name)
     # ── v3.9: 报告生成器数据契约（journal_entries.json + analysis_result.csv）──
    st = result["stats"]
    book_std = result["book_std"]
    _tot_amt = float(book_std["net_amount"].abs().sum()) if len(book_std) else 0.0
    _ub_amt = sum(abs(i.get("net_amount", 0.0)) for i in result["unmatched_book"])
    _diff_pct = round(_ub_amt / _tot_amt * 100, 2) if _tot_amt else 0.0

    journal_entries = {
        "total_rows": int(st.get("book_rows", 0)),
        "columns": ["行号", "日期", "凭证号", "摘要", "对方", "借方金额", "贷方金额",
                    "净额", "状态", "层级", "对方行", "备注"],
        "numeric_summary": {
            "净额": {
                "sum": round(float(book_std["net_amount"].sum()), 2),
                "mean": round(float(book_std["net_amount"].mean()), 2),
                "max": round(float(book_std["net_amount"].max()), 2),
                "min": round(float(book_std["net_amount"].min()), 2),
            }
        },
        "match_stats": {
            "total_left": int(st.get("book_rows", 0)),
            "total_right": int(st.get("bank_rows", 0)),
            "matched_count": int(st.get("book_matched", 0)),
            "match_rate": float(st.get("book_match_rate", 0.0)),   # 百分数刻度（阈值>90/>70）
            "diff_percentage": _diff_pct,                          # 未匹配金额占比（阈值5/15/30）
            "interbank_transfers": st.get("interbank_transfers", 0),
        },
    }
    p = out / "journal_entries.json"
    p.write_text(json.dumps(journal_entries, ensure_ascii=False, indent=2), encoding="utf-8")
    written.append(p.name)

    # CSV 预览（报告"数据明细前50行"用）
    detail.head(51).to_csv(str(out / "analysis_result.csv"), index=False, encoding="utf-8-sig")
    written.append("analysis_result.csv")
    return written


def reconcile_files(book_path, bank_path, config=None, out_dir=None):
    from core.document_loader import load_tables
    cfg = dict(config or {})
    book_tables = load_tables(book_path); bank_tables = load_tables(bank_path)
    if not book_tables: raise ValueError(f"无法读取: {book_path}")
    if not bank_tables: raise ValueError(f"无法读取: {bank_path}")
    cfg.setdefault("book_file", Path(book_path).name)
    cfg.setdefault("bank_file", Path(bank_path).name)
    progress_cb = cfg.pop("progress_callback", None)
    result = run_bank_reconciliation(book_tables[0], bank_tables[0], cfg, progress_callback=progress_cb)
    if out_dir: result["output_files"] = export_reconciliation_outputs(result, out_dir)
    return result


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("用法: python -m core.bank_reconcile_engine <序时账> <流水> [输出目录]")
        sys.exit(1)
    odir = sys.argv[3] if len(sys.argv) > 3 else "outputs/reconcile"
    res = reconcile_files(sys.argv[1], sys.argv[2], out_dir=odir)
    s = res["stats"]
    print(f"账面{s['book_rows']}笔(匹配率{s['book_match_rate']}%)|流水{s['bank_rows']}笔(匹配率{s['bank_match_rate']}%)")
    print(f"L1={s['matched_L1']} L2={s['matched_L2']} L3={s['matched_L3_groups']} L4={s['review_L4']}")
    print("未达四分类:", json.dumps(s["timing_categories"], ensure_ascii=False))
    print("交付物:", res.get("output_files"))
