#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audit_procedures.py 补丁：函证语义列 + 真 MUS 抽样 + 穿行测试参数化"""
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "core" / "audit_procedures.py"
src = P.read_text(encoding="utf-8").replace("\r\n", "\n")


def rep(old: str, new: str, tag: str):
    global src
    assert src.count(old) == 1, f"[{tag}] 命中 {src.count(old)} 次而非 1 次"
    src = src.replace(old, new)
    print(f"  [PATCH] {tag}")


# ── 1. 函证记录：硬编码列名 → 语义角色查找 ─────────────────────
rep('''    @classmethod
    def from_row(cls, row, threshold=500000):
        amt = float(row.get("余额", row.get("交易金额", 0)) or 0)
        return cls(customer_name=str(row.get("客户名称", row.get("对方户名", ""))),
                   amount=amt, account_code=str(row.get("科目编码", "")),
                   status="待发函" if amt >= threshold else "低于阈值")''',
    '''    @classmethod
    def from_row(cls, row, threshold=500000):
        # 语义角色查找（余额/名称/科目），不写死列名；均缺失时退化为首个数值列
        try:
            import pandas as pd
            from core.column_semantics import detect_column_roles
            roles = detect_column_roles(pd.DataFrame([dict(row)]))
        except Exception:
            roles = {}
        amt_col = roles.get("balance") or roles.get("amount")
        if amt_col is None:
            amt_col = next((k for k, v in row.items()
                            if isinstance(v, (int, float))), None)
        name_col = roles.get("name") or roles.get("counterpart")
        amt = float(row.get(amt_col, 0) or 0) if amt_col else 0.0
        return cls(customer_name=str(row.get(name_col, "")) if name_col else "",
                   amount=amt, account_code=str(row.get(roles.get("subject"), "")),
                   status="待发函" if amt >= threshold else "低于阈值")''',
    "函证记录语义列")

# ── 2. 函证计划：补充积极/消极式与替代程序指引 ─────────────────
rep('''        "config": {"procedure_type": "confirmation", "threshold": threshold,
                   "amount_column": amount_column}}''',
    '''        "config": {"procedure_type": "confirmation", "threshold": threshold,
                   "amount_column": amount_column,
                   "form": "积极式（默认；重大/异常项目必须积极式，消极式仅限低风险小额）",
                   "follow_up": "未回函项目须执行替代程序：检查期后回款/对账单/"
                                "银行存款余额调节表/原始凭证，并登记回函差异"}}''',
    "函证计划专业化")

# ── 3. 穿行测试：断点节点参数化 ────────────────────────────────
rep('''def analyze_walkthrough_breaks(merged_cols: List[str]) -> Dict[str, Any]:
    expected = ["日期", "金额", "状态"]''',
    '''def analyze_walkthrough_breaks(merged_cols: List[str],
                               expected: Optional[List[str]] = None) -> Dict[str, Any]:
    # 节点列由调用方按业务流程指定（如 请购→审批→验收→入库→付款），
    # 默认 ["日期", "金额", "状态"] 仅为最简占位，不代表完整流程
    expected = expected or ["日期", "金额", "状态"]''',
    "穿行测试节点参数化")

# ── 4. 抽样：保留 DAG 构造器，新增真 MUS 执行入口 ─────────────
rep('''def build_sampling_plan(data_file: str, method: str = "monetary_unit",
                        sample_size: int = 20, amount_column: str = "金额",
                        risk_weight: Optional[str] = None) -> Dict[str, Any]:
    """抽样 DAG：Load → Sort(desc) → Limit → Export"""''',
    '''def run_sampling_procedure(df, amount_column: str = "金额",
                             method: str = "monetary_unit", **kwargs) -> Dict[str, Any]:
    """真审计抽样执行入口（CSA 1314）：委托 core.audit_sampling。

    替代旧版"Sort 降序取前 N"的伪 MUS——那是'挑大的'，不是概率比例选样。
    支持 monetary_unit(MUS/PPS 系统选样) / random(简单随机) / stratified(分层)。
    """
    from core.audit_sampling import run_sampling
    return run_sampling(df, amount_col=amount_column, method=method, **kwargs)


def build_sampling_plan(data_file: str, method: str = "monetary_unit",
                        sample_size: int = 20, amount_column: str = "金额",
                        risk_weight: Optional[str] = None) -> Dict[str, Any]:
    """抽样 DAG：Load → Sort(desc) → Limit → Export（DAG 展示用；
    真实抽样执行请用 run_sampling_procedure / core.audit_sampling）"""''',
    "真 MUS 抽样入口")

P.write_text(src, encoding="utf-8", newline="\n")
import ast
ast.parse(src)
print("audit_procedures 补丁完成，AST OK")
