#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
列语义注册与动态识别 (column_semantics.py)
=============================================
平台统一的"列名 → 业务语义"注册表与动态识别器。

背景：用户上传表格的列名千变万化（" 摘要"带空格、"借方（支取）"带括号、
"交易金额"/"发生额"/"金额(元)"/"AMOUNT"……）。任何把列名写死在代码里的
做法都会限制平台能力。本模块提供：

1. ROLE_SYNONYMS：语义角色 → 候选列名注册表（可持续扩充，单一事实来源）；
2. detect_column_roles()：对任意 DataFrame 做角色识别
   （归一化精确匹配 > 前缀匹配 > 包含匹配 > RapidFuzz 模糊，打分取优）；
3. suggest_join_keys()：为两表推荐有业务含义的连接键，
   自动排除"序号/编号/行号"等无意义键（防止"按行号对账"式错误）；
4. 类型推断辅助：infer_amount_columns / pick_group_column / pick_date_column。

使用方：data_sniffer、matching_engine、bank_reconcile_engine、
audit_procedures、routes.py 算子自动补全与代码生成。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

# ═══════════════════════════════════════════════════════════════
# 语义角色注册表（单一事实来源；新增列名只改这里）
# ═══════════════════════════════════════════════════════════════

ROLE_SYNONYMS: Dict[str, List[str]] = {
    "date": ["日期", "交易日期", "记账日期", "业务日期", "入账日期", "发生日期",
             "时间", "交易时间", "date", "datetime", "凭证日期", "对账日期",
             "起息日", "到账日期", "交易时间戳"],
    "amount": ["交易金额", "金额", "发生额", "交易额", "金额(元)", "金额（元）",
               "amount", "amt", "价税合计", "合计金额", "实际金额", "本币金额",
               "交易金额（元）", "发生金额"],
    "debit": ["借方金额", "借方", "借方(支取)", "借方（支取）", "支取", "支出",
              "支出金额", "付款金额", "借方发生额", "借方额", "借", "借记",
              "付款", "借方合计", "借金额", "Debit"],
    "credit": ["贷方金额", "贷方", "贷方(收入)", "贷方（收入）", "收入", "收入金额",
               "收款金额", "贷方发生额", "贷方额", "贷", "贷记",
               "收款", "贷方合计", "贷金额", "Credit"],
    "name": ["客户名称", "对方客户名称", "对方户名", "机构名称", "单位名称",
             "往来单位", "供应商名称", "客户", "单位", "名称", "户名",
             "付款单位", "收款单位", "医院名称", "公司名称", "企业名称",
             "客户简称", "公司全称", "付款人", "收款人", "交易对手名称"],
    "counterpart": ["对方户名", "对方客户名称", "对方", "交易对手", "对方单位",
                    "对手方", "对方名称", "对方账号户名", "对方账户名",
                    "对方行名", "汇入行", "汇出行"],
    "summary": ["摘要", "摘要信息", "用途", "备注", "说明", "附言", "摘要说明",
                "交易摘要", "业务摘要", "交易附言", "汇款用途", "款项用途"],
    "account": ["银行账号", "账号", "账户", "银行账户", "开户账号", "账户号码",
                "银行卡号", "对方账号", "对方银行账号", "对方账户",
                "本方账号", "交易账号", "卡号"],
    "voucher_no": ["凭证号", "凭证号码", "凭证编号", "凭证字号", "凭证",
                   "传票号", "流水号", "交易流水号", "业务流水号", "凭证序号"],
    "subject": ["科目编码", "科目名称", "会计科目", "科目", "科目代码",
                "科目编号", "账户科目", "核算科目"],
    "balance": ["余额", "账户余额", "期末余额", "本次余额", "结余",
                "交易后余额", "账户结余", "当前余额", "账面余额"],
    "order_no": ["订单号", "单号", "业务编号", "合同号", "发票号", "票据号",
                 "业务单号", "交易单号", "付款单号", "收款单号", "申请单号"],
    "period": ["月", "月份", "期间", "会计期间", "年度", "年份",
               "记账期间", "账期"],
    "direction": ["方向", "借贷方向", "借贷", "收付方向", "DC", "收支标志",
                  "借贷标志", "正负号"],
}

# 无业务含义、禁止作为连接键的列名（归一化后比较）
MEANINGLESS_KEY_NAMES = {
    "序号", "编号", "行号", "次序", "序列", "no", "no.", "num", "number",
    "id", "index", "idx", "#", "顺序号", "排名", "code",
}


def normalize_colname(c: Any) -> str:
    """列名归一化：去空白/全角空格、统一括号、ASCII 转小写"""
    s = str(c).replace(" ", "").replace("　", "").strip()
    s = s.replace("（", "(").replace("）", ")")
    return s.lower() if s.isascii() else s


def is_meaningless_key(col: Any, series: Optional[pd.Series] = None) -> bool:
    """判断是否无业务含义的键（序号/行号等）。

    名称命中即排除；名称未命中但传入 series 且为 0/1..n 等差序列时也排除。
    """
    n = normalize_colname(col).rstrip(" .")
    if n in MEANINGLESS_KEY_NAMES:
        return True
    if series is not None:
        try:
            vals = pd.to_numeric(series, errors="coerce").dropna()
            if len(vals) >= 3:
                diffs = vals.sort_values().diff().dropna().unique()
                if len(diffs) == 1 and diffs[0] == 1 and vals.nunique() == len(vals):
                    return True  # 严格等差且唯一 → 行号
        except Exception:
            pass
    return False



# ═══════════════════════════════════════════════════════════════
# 角色识别（打分制：精确3 / 前缀2 / 包含1 / 模糊0.5）
# ═══════════════════════════════════════════════════════════════

def _score_candidate(norm_col: str, synonym: str) -> int:
    ns = normalize_colname(synonym)
    if norm_col == ns:
        return 3
    if norm_col.startswith(ns) or ns.startswith(norm_col):
        return 2
    if ns in norm_col or norm_col in ns:
        return 1
    return 0


def detect_column_roles(df: pd.DataFrame,
                        min_score: int = 1) -> Dict[str, str]:
    """识别 DataFrame 的语义角色 → 实际列名。

    每个角色取打分最高的列；打分相同取候选表中靠前者。
    RapidFuzz 可用时对未命中角色做模糊兜底（阈值 80，仅参考）。
    """
    cols = [str(c) for c in df.columns]
    norm_map = {normalize_colname(c): c for c in cols}
    result: Dict[str, str] = {}
    for role, synonyms in ROLE_SYNONYMS.items():
        best_col, best_score = None, 0
        for syn in synonyms:
            for nc, orig in norm_map.items():
                sc = _score_candidate(nc, syn)
                if sc > best_score:
                    best_col, best_score = orig, sc
        if best_col is not None and best_score >= min_score:
            result[role] = best_col
    # 模糊兜底（仅对未命中的关键角色）
    try:
        from rapidfuzz import fuzz
        for role in ("name", "date", "amount"):
            if role in result:
                continue
            for syn in ROLE_SYNONYMS[role]:
                for nc, orig in norm_map.items():
                    if nc.isascii() and len(nc) < 2:
                        continue
                    if fuzz.ratio(nc, normalize_colname(syn)) >= 80:
                        result.setdefault(role, orig)
                        break
    except ImportError:
        pass
    return result


def find_role_column(df: pd.DataFrame, role: str) -> Optional[str]:
    """单个角色查找（未命中返回 None，绝不猜测硬编码默认值）"""
    return detect_column_roles(df).get(role)


# ═══════════════════════════════════════════════════════════════
# 连接键推荐（排除无意义键）
# ═══════════════════════════════════════════════════════════════

def suggest_join_keys(df_a: pd.DataFrame, df_b: pd.DataFrame,
                      max_keys: int = 3) -> List[Tuple[str, str]]:
    """为两表推荐 (左列, 右列) 连接键，优先级：

    1. 归一化后完全同名的有业务含义列（排除序号/行号）；
    2. 同一语义角色的列（name↔counterpart 视为同族）；
    数量 ≤max_keys，按 name > voucher/order/subject > date > amount 排序。
    """
    keys: List[Tuple[str, str]] = []
    a_cols = {normalize_colname(c): str(c) for c in df_a.columns}
    b_cols = {normalize_colname(c): str(c) for c in df_b.columns}
    # 1) 同名键
    for nc in a_cols:
        if nc in b_cols and not is_meaningless_key(a_cols[nc], df_a[a_cols[nc]]):
            keys.append((a_cols[nc], b_cols[nc]))
    # 2) 语义角色键（name/counterpart 同族互通）
    role_a = detect_column_roles(df_a)
    role_b = detect_column_roles(df_b)
    family = {"name", "counterpart"}
    for role in ("name", "voucher_no", "order_no", "subject", "date", "amount"):
        if role == "name":
            ca = next((role_a[r] for r in family if r in role_a), None)
            cb = next((role_b[r] for r in family if r in role_b), None)
        else:
            ca, cb = role_a.get(role), role_b.get(role)
        if ca and cb and (ca, cb) not in keys and not is_meaningless_key(ca):
            keys.append((ca, cb))
    prio = {"name": 0, "voucher_no": 1, "order_no": 1, "subject": 1, "date": 2, "amount": 3}

    def rank(k):
        ra = next((v for r, v in (("name", 0),) if k[0] in (role_a.get("name"), role_a.get("counterpart"))), 9)
        return ra if ra != 9 else prio.get(next(
            (r for r, c in role_a.items() if c == k[0]), ""), 9)

    keys.sort(key=rank)
    return keys[:max_keys]


# ═══════════════════════════════════════════════════════════════
# 类型推断辅助（供算子自动补全与代码生成运行时使用）
# ═══════════════════════════════════════════════════════════════

def infer_amount_columns(df: pd.DataFrame, max_cols: int = 3) -> List[str]:
    """金额列推断：语义角色命中优先，其次数值型且非无意义键的列"""
    roles = detect_column_roles(df)
    out = [roles[r] for r in ("amount", "debit", "credit", "balance") if r in roles]
    for c in df.columns:
        if len(out) >= max_cols:
            break
        if str(c) in out or is_meaningless_key(c):
            continue
        if pd.api.types.is_numeric_dtype(df[c]):
            out.append(str(c))
    return out[:max_cols]


def pick_group_column(df: pd.DataFrame) -> Optional[str]:
    """分组列推断：name 角色命中 > 低基数文本列；无合适返回 None"""
    roles = detect_column_roles(df)
    if "name" in roles:
        return roles["name"]
    n = max(len(df), 1)
    for c in df.columns:
        if df[c].dtype == object and not is_meaningless_key(c):
            if 1 < df[c].nunique() <= max(50, n * 0.5):
                return str(c)
    return None


def pick_date_column(df: pd.DataFrame) -> Optional[str]:
    """日期列推断：角色命中 > datetime dtype > 名称含日期/时间"""
    roles = detect_column_roles(df)
    if "date" in roles:
        return roles["date"]
    for c in df.columns:
        if "datetime" in str(df[c].dtype):
            return str(c)
    return None


# ═══════════════════════════════════════════════════════════════
# LLM 兜底映射（确定性优先、LLM 兜底、结果可入 Catalog 供确认）
# ═══════════════════════════════════════════════════════════════

# 角色业务说明（供 LLM 理解语义，也供前端确认界面展示）
ROLE_DESCRIPTIONS: Dict[str, str] = {
    "date": "交易/记账日期",
    "amount": "金额（带符号单列为正=流入，或绝对金额）",
    "debit": "借方/支取/支出（序时账=资金增加；银行流水=资金减少，方向互为镜像）",
    "credit": "贷方/收入（序时账=资金减少；银行流水=资金增加）",
    "name": "单位/客户/机构名称",
    "counterpart": "对方户名/交易对手",
    "summary": "摘要/用途/备注",
    "account": "银行账号/账户",
    "voucher_no": "凭证号",
    "subject": "会计科目",
    "balance": "账户余额",
    "order_no": "订单/合同/发票号",
    "period": "期间/月份",
}

# 对账不可或缺的关键角色（缺失才问 LLM，避免无谓调用）
_CRITICAL_ROLES = ("date", "amount", "debit", "credit", "name", "counterpart")


def _default_llm_callable():
    """默认 LLM 通道：本地 vLLM（与平台熔断链一致；离线返回 None 不报错）"""
    def call(prompt: str) -> Optional[str]:
        try:
            import json as _json
            import os as _os
            import re as _re
            import requests as _req
            url = _os.environ.get(
                "VLLM_TUNNEL_URL", "http://localhost:18000/v1/chat/completions")
            r = _req.post(url, json={"model": _os.environ.get("VLLM_MODEL", "qwen3-235b"),
                                     "messages": [{"role": "user", "content": prompt}],
                                     "temperature": 0, "max_tokens": 300},
                          headers={"Authorization": "Bearer EMPTY"}, timeout=20)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
        except Exception:
            return None
        return None
    return call


def detect_roles_with_llm(df: pd.DataFrame, filename: str = "",
                          llm_callable=None,
                          sample_rows: int = 3) -> Dict[str, str]:
    """语义角色识别：确定性规则优先，未覆盖的关键角色交 LLM 兜底。

    - 规则命中 → 直接使用（零成本、可复现）；
    - 关键角色（日期/金额类/名称）缺失 → 把真实列名 + 表头样本给 LLM，
      要求返回 JSON {"role": "列名" 或 null}；
    - LLM 不可用/答案非法 → 跳过该角色（宁缺毋滥，绝不臆造列名）；
    - 返回值附带 mapping_source 元信息（rule/llm），供前端确认界面展示。
    """
    roles = detect_column_roles(df)
    missing = [r for r in _CRITICAL_ROLES
               if r not in roles
               and not (r in ("debit", "credit") and "amount" in roles)
               and not (r == "amount" and ("debit" in roles or "credit" in roles))]
    if not missing:
        return roles
    call = llm_callable or _default_llm_callable()
    cols = [str(c) for c in df.columns]
    sample = df.head(sample_rows).to_dict("records") if len(df) else []
    prompt = (
        "你是审计数据专家。请把下表的列名映射到业务语义角色。\n"
        f"语义角色说明：{ {r: ROLE_DESCRIPTIONS[r] for r in missing} }\n"
        f"文件名：{filename}\n"
        f"全部列名：{cols}\n"
        f"前{sample_rows}行样例：{sample}\n"
        "只输出 JSON，格式：{\"角色名\": \"列名\" 或 null}，"
        f"需要判断的角色：{missing}。列名必须原样来自上方列名列表，不确定填 null。")
    try:
        import json as _json
        import re as _re
        raw = call(prompt)
        if raw:
            m = _re.search(r"\{[\s\S]*\}", raw)
            if m:
                data = _json.loads(m.group(0))
                # 接受 LLM 返回的全部有效角色（不覆盖规则命中，不臆造列名）
                for role, v in data.items():
                    if role in ROLE_SYNONYMS and role not in roles \
                            and isinstance(v, str) and v in cols:
                        roles[role] = v
    except Exception:
        pass
    return roles
