#!/usr/bin/env python3
"""审计专用执行模块 (audit_procedures.py) —— 白皮书 §2.2 函证/穿行/抽样"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def _suggest_form_template(subject: str = "") -> str:
    """按科目推荐本所询证函范本（仅文件路径，来自内部知识库文件名索引）"""
    try:
        from core.internal_kb_registry import suggest_confirmation_form
        t = suggest_confirmation_form(subject or "往来款项", "积极式")
        return t["path"] if t else ""
    except Exception:
        return ""


def build_confirmation_plan(data_file: str, amount_column: str = "余额",
                            threshold: float = 500000) -> Dict[str, Any]:
    """函证计划 DAG：Load → NoiseFilter → ConditionCheck → Sort → Export"""
    return {
        "objective": "函证清单生成", "procedure": "confirmation",
        "operators": [
            {"id": "op_1", "name": "Load", "source_file": data_file},
            {"id": "op_2", "name": "NoiseFilter",
             "params": {"exclude": "合计|小计|累计|结转|冲销"}, "input_from": ["op_1"]},
            {"id": "op_3", "name": "ConditionCheck",
             "params": {"column": amount_column, "operator": ">=", "value": threshold},
             "input_from": ["op_2"]},
            {"id": "op_4", "name": "Sort",
             "params": {"column": amount_column, "order": "desc"}, "input_from": ["op_3"]},
            {"id": "op_5", "name": "Export",
             "params": {"output": "函证清单.xlsx"}, "input_from": ["op_4"]}],
        "config": {"procedure_type": "confirmation", "threshold": threshold,
                   "amount_column": amount_column,
                   "form": "积极式（默认；重大/异常项目必须积极式，消极式仅限低风险小额）",
                   "follow_up": "未回函项目须执行替代程序：检查期后回款/对账单/"
                                "银行存款余额调节表/原始凭证，并登记回函差异",
                   "form_template": _suggest_form_template(amount_column)}}


@dataclass
class ConfirmationRecord:
    customer_name: str = ""; amount: float = 0.0; account_code: str = ""
    period: str = ""; status: str = "待发函"; notes: str = ""
    @classmethod
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
                   status="待发函" if amt >= threshold else "低于阈值")


def build_walkthrough_dag(data_files: List[str], trace_key: str) -> Dict[str, Any]:
    """穿行测试 DAG：多表按关联键串联 → Merge → Export"""
    ops = [{"id": f"op_{i+1}", "name": "Load", "source_file": fn}
           for i, fn in enumerate(data_files)]
    for i in range(len(data_files) - 1):
        ops.append({"id": f"op_m{i}", "name": "Merge",
                    "params": {"on": [trace_key], "how": "outer"},
                    "input_from": [f"op_{i+1}", f"op_{i+2}"]})
    ops.append({"id": "op_export", "name": "Export",
                "params": {"output": "穿行测试底稿.xlsx"},
                "input_from": [f"op_m{len(data_files)-2}"]})
    return {"objective": f"穿行测试-{trace_key}", "procedure": "walkthrough",
            "operators": ops, "config": {"trace_key": trace_key, "data_files": data_files}}


def analyze_walkthrough_breaks(merged_cols: List[str],
                               expected: Optional[List[str]] = None) -> Dict[str, Any]:
    # 节点列由调用方按业务流程指定（如 请购→审批→验收→入库→付款），
    # 默认 ["日期", "金额", "状态"] 仅为最简占位，不代表完整流程
    expected = expected or ["日期", "金额", "状态"]
    miss = [c for c in expected if c not in merged_cols]
    return {"total_nodes": len(expected), "missing_nodes": miss,
            "complete": len(miss) == 0,
            "recommendation": "流程完整" if not miss else f"缺失: {','.join(miss)}"}


def run_sampling_procedure(df, amount_column: str = "金额",
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
    真实抽样执行请用 run_sampling_procedure / core.audit_sampling）"""
    ops = [{"id": "op_1", "name": "Load", "source_file": data_file}]
    if risk_weight:
        ops.append({"id": "op_2", "name": "ColumnFilter",
                    "params": {"columns": [amount_column, risk_weight]}, "input_from": ["op_1"]})
    ops.append({"id": "op_sort", "name": "Sort",
                "params": {"column": amount_column, "order": "desc"}, "input_from": ["op_1"]})
    ops.append({"id": "op_export", "name": "Export",
                "params": {"output": "抽样清单.xlsx"}, "input_from": ["op_sort"]})
    return {"objective": f"审计抽样({method})", "procedure": "sampling",
            "operators": ops,
            "config": {"method": method, "sample_size": sample_size,
                       "amount_column": amount_column, "risk_weight": risk_weight}}


@dataclass
class SamplingResult:
    method: str = ""; population_size: int = 0; sample_size: int = 0
    total_amount: float = 0.0; sample_amount: float = 0.0; coverage_ratio: float = 0.0
    items: List[Dict] = field(default_factory=list)
    def summary(self):
        return (f"抽样方法: {self.method}\n总体: {self.population_size} 笔, "
                f"合计 {self.total_amount:,.2f} 元\n"
                f"样本: {self.sample_size} 笔, 覆盖率: {self.coverage_ratio:.1%}")


PROCEDURE_TEMPLATES = {
    "confirmation": {"name": "函证管理", "desc": "生成余额大于阈值的函证清单", "builder": build_confirmation_plan},
    "walkthrough": {"name": "穿行测试", "desc": "按关联键串联多表标记断点", "builder": build_walkthrough_dag},
    "sampling": {"name": "审计抽样", "desc": "按MUS/随机/分层抽取样本", "builder": build_sampling_plan},
}

def build_procedure_dag(procedure_type: str, **kwargs) -> Optional[Dict[str, Any]]:
    tmpl = PROCEDURE_TEMPLATES.get(procedure_type)
    return tmpl["builder"](**kwargs) if tmpl else None

def get_procedure_info() -> List[Dict]:
    return [{"id": k, "name": v["name"], "desc": v["desc"]}
            for k, v in PROCEDURE_TEMPLATES.items()]

