#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DAG 语义编译器 (dag_compiler.py)
==================================
"全无状态生命周期与语义编译版"白皮书 §3.1 + §4.2 的核心实现

将大模型从"蓝图生成器"升级为"业务逻辑编译器"：
- 输入：审计师大白话 + Data Catalog（表头元数据）
- 输出：DAG JSON 蓝图（有向无环图，含算子拓扑）

DAG 蓝图包含：
- 审计目标（objective）
- 算子拓扑（operators）：每个算子 = 一个 Pandas 操作步骤
- 数据流（edges）：算子间的数据流向
- 执行上下文（context）：列名映射、容差、噪音规则等

典型 DAG 示例：
    "帮我把摘要带'医保回款'的行拉出来，如果某个月的总金额超过了50万，
     把这个月的所有明细单独导出来"
    
    编译为 DAG：
    Step 1: Load → Step 2: RegexFilter('医保回款') → Step 3: GroupBy(月份)
    → Step 4: ConditionCheck(金额>50万) → Step 5: Extract(满足条件的明细)

与前后阶段衔接：
- Dify 工作流：输出此 DAG JSON（替代旧版静态蓝图）
- OpenClaw：读取 DAG，编译为 Python/Pandas 代码
- sandbox：执行编译后的代码

作者：智能审计平台开发团队
版本：3.0.0（语义编译版）
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional


# ═══════════════════════════════════════════════════════════════
# 算子白名单与别名映射（供 Schema 校验与确定性规则修复共用）
# ═══════════════════════════════════════════════════════════════

VALID_OPERATORS = {
    "Load", "RegexFilter", "ColumnFilter", "GroupBy", "Merge",
    "Sort", "ConditionCheck", "Extract", "Transform", "NoiseFilter",
    "Aggregate", "Diff", "Export", "Reconcile", "AuditAdjustment",
}

# LLM 常见的算子别名/大小写变体 → 标准算子（确定性模板替换，零 LLM 成本）
OP_NAME_ALIASES = {
    "read": "Load", "readfile": "Load", "loadfile": "Load",
    "regex": "RegexFilter", "keywordfilter": "RegexFilter", "textfilter": "RegexFilter",
    "filter": "ColumnFilter", "valuefilter": "ColumnFilter", "amountfilter": "ColumnFilter",
    "group": "GroupBy",
    "join": "Merge", "concat": "Merge", "union": "Merge",
    "orderby": "Sort", "rank": "Sort",
    "condition": "ConditionCheck", "check": "ConditionCheck", "validate": "ConditionCheck",
    "select": "Extract", "subset": "Extract",
    "convert": "Transform", "calc": "Transform", "compute": "Transform",
    "denoise": "NoiseFilter", "clean": "NoiseFilter",
    "agg": "Aggregate", "aggregation": "Aggregate", "summarize": "Aggregate", "summary": "Aggregate",
    "difference": "Diff", "compare": "Diff", "comparison": "Diff",
    "output": "Export", "save": "Export", "write": "Export",
    "reconciliation": "Reconcile", "match": "Reconcile", "matching": "Reconcile",
    "adjustment": "AuditAdjustment",
}


def normalize_operator_name(name: Any) -> Any:
    """算子名标准化：精确匹配白名单 → 别名表 → 忽略大小写/下划线比对白名单"""
    if not isinstance(name, str):
        return name
    if name in VALID_OPERATORS:
        return name
    key = name.strip().lower().replace("-", "").replace(" ", "")
    key_no_us = key.replace("_", "")
    for k in (key, key_no_us):
        if k in OP_NAME_ALIASES:
            return OP_NAME_ALIASES[k]
    for std in VALID_OPERATORS:
        if std.lower() == key_no_us:
            return std
    # 前缀兜底：SortDescending→Sort、LoadData→Load、ExportCSV→Export
    for std in sorted(VALID_OPERATORS, key=len, reverse=True):
        if key_no_us.startswith(std.lower()):
            return std
    for k in sorted(OP_NAME_ALIASES, key=len, reverse=True):
        if len(k) >= 4 and key_no_us.startswith(k):
            return OP_NAME_ALIASES[k]
    return name


# ═══════════════════════════════════════════════════════════════
# 算子定义（Pandas 操作的原子单元）
# ═══════════════════════════════════════════════════════════════

@dataclass
class Operator:
    """
    DAG 算子（一个原子操作步骤）
    
    每个算子对应一个 Pandas 操作，如过滤、分组、合并等。
    OpenClaw 将算子序列编译为连续的 Python 代码。
    """
    id: str                              # 唯一标识，如 "op_1", "op_2"
    name: str                            # 算子名称，如 "RegexFilter", "GroupBy"
    description: str = ""               # 人类可读描述
    
    # 输入配置
    input_from: List[str] = field(default_factory=list)  # 上游算子 ID 列表
    source_file: Optional[str] = None   # 输入文件名（如 "银行流水.xlsx"）
    
    # 参数配置（算子特定的参数）
    params: Dict[str, Any] = field(default_factory=dict)
    # RegexFilter 示例: {"column": "摘要", "pattern": "医保回款", "case_sensitive": false}
    # GroupBy 示例: {"columns": ["月份"], "aggregations": {"金额": "sum"}}
    # ConditionCheck 示例: {"column": "金额_汇总", "operator": ">", "value": 500000}
    
    # 输出配置
    output_alias: Optional[str] = None  # 输出别名（下游引用）
    
    # 审计上下文
    audit_context: Dict[str, Any] = field(default_factory=dict)
    # 如：{"expected_rows": "筛选后剩余行数", "risk_note": "大额交易需复核"}


@dataclass
class DAGBlueprint:
    """
    DAG JSON 蓝图（语义编译器的输出）
    
    替代旧版的静态 AuditBlueprint，增加了：
    - 算子拓扑（operators）：描述"对数据做什么"的完整流水线
    - 数据流（edges）：算子间的依赖关系
    - 动态性：同一意图在不同数据上可产生不同的算子序列
    """
    
    # 元数据
    blueprint_id: str
    generated_at: str
    run_id: Optional[str] = None         # 关联的 Run_ID
    
    # 语义编译结果
    objective: str = ""                  # 编译后的审计目标描述
    raw_intent: str = ""                # 原始大白话输入
    confidence_score: float = 0.0
    
    # 算子拓扑（核心）
    operators: List[Operator] = field(default_factory=list)
    
    # 执行上下文
    context: Dict[str, Any] = field(default_factory=dict)
    # context 包含：
    # - column_mappings: 列名映射（如 {"日期": "transaction_date"}）
    # - tolerance: 容差规则
    # - noise_rules: 噪音过滤规则
    # - policy_hints: 政策提示（如医保结算规则）
    
    # 审计风控
    risk_alerts: List[Dict[str, str]] = field(default_factory=list)
    human_review_points: List[str] = field(default_factory=list)
    
    # 输出规范
    expected_outputs: List[Dict[str, Any]] = field(default_factory=list)
    # 如：[{"type": "excel", "filename": "医保大额明细.xlsx", "description": "超过50万的月份明细"}]

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def get_execution_order(self) -> List[str]:
        """返回算子的拓扑排序执行顺序"""
        # 简单的拓扑排序
        in_degree: Dict[str, int] = {op.id: 0 for op in self.operators}
        adj: Dict[str, List[str]] = {op.id: [] for op in self.operators}
        
        for op in self.operators:
            for upstream in op.input_from:
                adj[upstream].append(op.id)
                in_degree[op.id] += 1
        
        queue = [op_id for op_id, deg in in_degree.items() if deg == 0]
        order = []
        
        while queue:
            current = queue.pop(0)
            order.append(current)
            for neighbor in adj.get(current, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        # 环检测：Kahn 排序无法覆盖全部节点 = 存在循环依赖
        # （修复缺陷：旧版对环静默丢弃节点，导致执行顺序缺步且无告警）
        if len(order) < len(self.operators):
            stuck = sorted(op_id for op_id, deg in in_degree.items() if deg > 0)
            raise ValueError(f"DAG 存在循环依赖，无法拓扑排序，涉及算子: {stuck}")

        return order

    @property
    def operator_count(self) -> int:
        return len(self.operators)


# ═══════════════════════════════════════════════════════════════
# DAG 到 OpenClaw 代码的编译描述
# ═══════════════════════════════════════════════════════════════

class DAGToCodeDescription:
    """
    将 DAG 蓝图转换为人类可读的代码执行描述
    
    这不是真正的代码生成器（那是第四阶段 OpenClaw 的工作），
    而是为审计师提供"AI 打算做什么"的可视化说明。
    """

    OPERATOR_DESCRIPTIONS: Dict[str, str] = {
        "Load": "读取数据文件",
        "RegexFilter": "正则表达式筛选行",
        "ColumnFilter": "按列值筛选",
        "GroupBy": "分组聚合",
        "Merge": "合并多个数据源",
        "Sort": "排序",
        "ConditionCheck": "条件检查",
        "Extract": "提取满足条件的子集",
        "Transform": "数据变换（类型转换/计算新列）",
        "NoiseFilter": "噪音过滤",
        "Aggregate": "汇总统计",
        "Diff": "差异比对",
        "Export": "导出结果",
        "Reconcile": "对账匹配",
        "AuditAdjustment": "生成审计调整分录",
    }

    @classmethod
    def describe(cls, dag: DAGBlueprint) -> str:
        """生成人类可读的执行计划描述"""
        lines = [f"审计目标: {dag.objective}", f"置信度: {dag.confidence_score:.0%}", ""]
        
        order = dag.get_execution_order()
        op_map = {op.id: op for op in dag.operators}
        
        for i, op_id in enumerate(order, 1):
            op = op_map[op_id]
            desc = cls.OPERATOR_DESCRIPTIONS.get(op.name, op.name)
            lines.append(f"Step {i}: {op.description or desc} ({op.name})")
            
            # 参数摘要
            if op.params:
                for k, v in op.params.items():
                    lines.append(f"         {k}: {v}")
            
            # 审计上下文
            if op.audit_context.get("risk_note"):
                lines.append(f"         ⚠️ {op.audit_context['risk_note']}")
        
        lines.append("")
        lines.append(f"预计输出: {len(dag.expected_outputs)} 个文件")
        for out in dag.expected_outputs:
            lines.append(f"  - {out.get('filename', 'N/A')}: {out.get('description', '')}")
        
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# Dify 工作流输出的 DAG JSON 解析
# ═══════════════════════════════════════════════════════════════

class DAGParser:
    """
    解析 Dify 工作流输出的 DAG JSON 字符串
    
    负责：
    1. 清理可能的 markdown 代码块
    2. JSON 解析
    3. 结构校验
    4. 转为 DAGBlueprint 对象
    """

    @classmethod
    def parse(cls, dag_json_str: str, known_files: Optional[List[str]] = None) -> DAGBlueprint:
        """解析并校验 DAG JSON。

        Args:
            dag_json_str: LLM 输出的原始字符串
            known_files: 数据目录中的真实文件名清单；提供时将校验
                Load 算子的 source_file 是否真实存在（幻觉文件名防御）
        """
        # 1. 清洗：去除 <think>...</think> 标签及其内容
        cleaned = dag_json_str.strip()
        import re
        cleaned = re.sub(r'<think>.*?</think>', '', cleaned, flags=re.DOTALL)
        # Thinking-2507 风格防御：模板自动注入 <think> 时，输出只含闭合标签
        if "</think>" in cleaned and "<think>" not in cleaned:
            cleaned = cleaned.split("</think>", 1)[1]
        cleaned = cleaned.strip()

        # 尝试提取 JSON 代码块
        json_match = re.search(r'```json\s*(\{.*?\})\s*```', cleaned, re.DOTALL)
        if json_match:
            cleaned = json_match.group(1)
        else:
            json_match = re.search(r'(\{.*\})', cleaned, re.DOTALL)
            if json_match:
                cleaned = json_match.group(1)

        # 2. JSON 解析（惰性启发式修复：合法 JSON 原样解析，
        #    避免 replace("'", '"') 把值内单引号（如"排除'手续费'"）改坏）
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            repaired = cleaned.replace("'", '"')
            repaired = re.sub(r'\bTrue\b', 'true', repaired)
            repaired = re.sub(r'\bFalse\b', 'false', repaired)
            repaired = re.sub(r'\bNone\b', 'null', repaired)
            try:
                data = json.loads(repaired)
            except json.JSONDecodeError as e:
                raise ValueError(f"DAG JSON 解析失败: {e}") from e

        # 2.1 安全校验：params 值长度限制（防止超长注入）
        MAX_PARAM_VALUE_LEN = 5000  # 单个参数值的最大字符长度
        if isinstance(data, dict):
            for op_data in data.get("operators", []):
                if isinstance(op_data, dict) and "params" in op_data:
                    params = op_data["params"]
                    if isinstance(params, dict):
                        for k, v in params.items():
                            if isinstance(v, str) and len(v) > MAX_PARAM_VALUE_LEN:
                                params[k] = v[:MAX_PARAM_VALUE_LEN]

        # ==========================================================
        # 【新增防御】2.5 结构自动疗愈（完美解决 vLLM Fallback 格式不规范问题）
        # ==========================================================
        if isinstance(data, dict):
            # 兼容：有些 LLM 会把整个 DAG 嵌套在 "dag"、"dag_json"、"blueprint" 或 "data" 字段内
            for wrap_key in ["dag", "dag_json", "blueprint", "data"]:
                if wrap_key in data and isinstance(data[wrap_key], dict):
                    # 如果包裹层内含有算子，则剥离外壳
                    if "operators" in data[wrap_key] or "nodes" in data[wrap_key] or "steps" in data[wrap_key]:
                        data = data[wrap_key]
                        break

            # 兼容：如果 LLM 把算子写成了 "nodes"、"steps"、"pipeline" 等，自动重命名为 "operators"
            if "operators" not in data:
                for alt_key in ["nodes", "steps", "pipeline", "flow"]:
                    if alt_key in data and isinstance(data[alt_key], list):
                        data["operators"] = data.pop(alt_key)
                        break
        # ==========================================================

        # 3. 算子兼容处理
        STANDARD_OP_FIELDS = {"id", "name", "description", "input_from", "source_file", "params", "output_alias",
                              "audit_context"}

        for idx, op in enumerate(data.get("operators", [])):
            # 兼容 type/operator -> name
            if "type" in op and "name" not in op:
                op["name"] = op.pop("type")
            if "operator" in op and "name" not in op:
                op["name"] = op.pop("operator")

            # 算子别名自愈（Filter→ColumnFilter、Join→Merge、load→Load 等）
            if "name" in op:
                op["name"] = normalize_operator_name(op["name"])

            # 兼容 Load 算子的文件路径输入（解决 Dify file_path 问题）
            if op.get("name") == "Load" and "source_file" not in op:
                if "params" in op and isinstance(op["params"], dict):
                    if "file_path" in op["params"]:
                        op["source_file"] = op["params"].pop("file_path")
                    elif "source_file" in op["params"]:
                        op["source_file"] = op["params"].pop("source_file")
                    elif "file" in op["params"]:  # 🔥 【新增】完美解决 params.file 的兼容
                        op["source_file"] = op["params"].pop("file")
                if "file" in op and "source_file" not in op:
                    op["source_file"] = op.pop("file")

            # 兼容输入依赖
            if "inputs" in op and "input_from" not in op:
                op["input_from"] = op.pop("inputs")
            if "input" in op and "input_from" not in op:
                op["input_from"] = [op.pop("input")]
            if isinstance(op.get("input_from"), str):
                op["input_from"] = [op["input_from"]]

            # 初始化 params 字典
            if "params" not in op or op["params"] is None:
                op["params"] = {}

            # 非标准字段自动向内收容至 params
            extra_keys = [k for k in op.keys() if k not in STANDARD_OP_FIELDS]
            for key in extra_keys:
                op["params"][key] = op.pop(key)

            # 确保必填项兜底（解决 ID 冲突缺陷）
            if "id" not in op:
                op["id"] = f"op_{idx + 1}"
            if "description" not in op:
                op["description"] = op.get("name", "未知操作")
            if "output_alias" not in op:
                op["output_alias"] = f"df_{op['id']}"
            if "audit_context" not in op:
                op["audit_context"] = {}

        # 3.5 非 Load 算子缺失 input_from 时按顺序自动串链（确定性自愈：
        #     LLM 常输出线性流水线但漏写依赖，按算子顺序补链即可执行）
        ops_list = data.get("operators", [])
        if isinstance(ops_list, list):
            for idx, op in enumerate(ops_list):
                if not isinstance(op, dict):
                    continue
                if op.get("name") == "Load":
                    op.setdefault("input_from", [])
                    continue
                if not op.get("input_from") and idx > 0:
                    prev = ops_list[idx - 1]
                    if isinstance(prev, dict) and prev.get("id"):
                        op["input_from"] = [prev["id"]]

        # 4. expected_outputs 类型归一化
        if "expected_outputs" in data:
            outputs = data["expected_outputs"]
            if isinstance(outputs, list):
                normalized = []
                for item in outputs:
                    if isinstance(item, str):
                        normalized.append({"filename": item, "description": item})
                    elif isinstance(item, dict):
                        if "filename" not in item:
                            item["filename"] = item.get("description", "output")
                        if "description" not in item:
                            item["description"] = item.get("filename", "无描述")
                        normalized.append(item)
                    else:
                        normalized.append({"filename": str(item), "description": str(item)})
                data["expected_outputs"] = normalized
            else:
                data["expected_outputs"] = []

                # ====== 🔥 【新增】4.5 risk_alerts 类型归一化 ======
        if "risk_alerts" in data:
            alerts = data["risk_alerts"]
            if isinstance(alerts, list):
                normalized_alerts = []
                for item in alerts:
                    if isinstance(item, str):
                        normalized_alerts.append({"level": "MEDIUM", "description": item})
                    elif isinstance(item, dict):
                        if "level" not in item:
                            item["level"] = "MEDIUM"
                        if "description" not in item:
                            item["description"] = item.get("level", "未知风险提示")
                        normalized_alerts.append(item)
                    else:
                        normalized_alerts.append({"level": "MEDIUM", "description": str(item)})
                data["risk_alerts"] = normalized_alerts
            else:
                data["risk_alerts"] = []

        # 5. 结构校验
        errors = cls._validate(data, known_files)
        if errors:
            raise ValueError(f"DAG Schema 校验失败: {'; '.join(errors)}")

        # 6. 转为对象
        operators = [Operator(**op) for op in data.get("operators", [])]

        # 6.1 智能推断 confidence_score（LLM 常遗漏该字段导致前端显示 0%）
        # v3.1: 推断上限从 0.65 → 0.85，对齐预设指南（跨文件对比/银行对账等场景明确要求 0.9-1.0）
        # 同时增加文件数量/数据列完整性等客观信号参与评分
        raw_score = data.get("confidence_score")
        if raw_score is None or (isinstance(raw_score, (int, float)) and raw_score == 0):
            op_count = len(operators)
            op_names = {op.get("name", "") for op in data.get("operators", [])}
            has_load = bool(op_names & {"Load", "load"})
            has_merge_reconcile = bool(op_names & {"Merge", "merge", "Reconcile", "reconcile", "Diff", "diff"})
            has_export = bool(op_names & {"Export", "export"})
            has_noise_filter = bool(op_names & {"NoiseFilter", "ColumnFilter", "RegexFilter"})
            has_sort = bool(op_names & {"Sort", "sort"})
            has_aggregate = bool(op_names & {"Aggregate", "aggregate", "GroupBy"})

            # 统计 Load 算子引用不同文件的数量（同文件被多次 Load 是异常信号）
            load_files = set()
            for op in data.get("operators", []):
                if op.get("name") in ("Load", "load"):
                    sf = op.get("source_file", "") or op.get("file", "")
                    if sf:
                        load_files.add(sf)
            distinct_input_count = len(load_files) if load_files else 1

            # 人工复核点数量：复核点越少说明 LLM 对结果越自信
            review_points = data.get("human_review_points", [])
            review_count = len(review_points) if isinstance(review_points, list) else 0
            # review 0-1 条 → +0.05, 2-3 条 → 0, ≥4 条 → -0.10（信号弱，仅微调）
            review_bonus = 0.05 if review_count <= 1 else (-0.10 if review_count >= 4 else 0.0)

            # 完整链路：Load + 对账 + Export（预设模式理想形态）→ 0.85 基准
            if has_load and has_merge_reconcile and has_export and op_count >= 4:
                inferred = 0.85
                # 数据质量加分：多预处理算子 + 不同源文件 → 更完整
                if has_noise_filter and has_sort:
                    inferred += 0.03
                if distinct_input_count >= 2:
                    inferred += 0.02
                # 同文件被当作两个源（异常信号）：大幅降级
                if op_count >= 3 and distinct_input_count == 1:
                    inferred -= 0.15
            # 中等链路：Load + Export 且算子 ≥ 3 → 0.55
            elif has_load and has_export and op_count >= 3:
                inferred = 0.55
                if has_noise_filter:
                    inferred += 0.05
                if distinct_input_count >= 2:
                    inferred += 0.05
                if op_count >= 3 and distinct_input_count == 1:
                    inferred -= 0.10
            # 最小链路 → 0.40
            elif op_count >= 2:
                inferred = 0.40
            else:
                inferred = 0.25

            # 应用复核点微调
            inferred = round(inferred + review_bonus, 2)
            # 硬钳制在 0.20-0.90（推断永远到不了 LLM 原生高分区间，留空间给 LLM 自己输出）
            inferred = max(0.20, min(0.90, inferred))
            confidence_score = inferred
        else:
            raw = float(raw_score) if raw_score is not None else 0.0
            # 非零值也做合理性钳制：LLM 有时输出极端值（如 1.0 但算子链残缺）
            if raw > 1.0:
                raw = round(raw / 100, 4) if raw <= 100 else 1.0
            # 同文件被多次 Load + 置信度很高 → 疑似 LLM 幻觉，钳制到 0.70
            load_files2 = set()
            for op in data.get("operators", []):
                if op.get("name") in ("Load", "load"):
                    sf = op.get("source_file", "") or op.get("file", "")
                    if sf:
                        load_files2.add(sf)
            op_cnt = len(operators)
            if op_cnt >= 3 and len(load_files2) == 1 and raw >= 0.85:
                raw = 0.70
            confidence_score = raw

        # 6.2 自动补充 expected_outputs（LLM 常遗漏，从 Export 算子推断）
        expected = data.get("expected_outputs", [])
        if not expected:
            for op in data.get("operators", []):
                if op.get("name") in ("Export", "export"):
                    fn = (op.get("params", {}).get("filename", "")
                          or op.get("output_alias", "")
                          or "analysis_result.xlsx")
                    expected.append({"filename": fn, "description": f"由算子 {op.get('id','')} 导出"})
            if not expected:
                expected.append({"filename": "analysis_result.xlsx", "description": "默认输出"})

        return DAGBlueprint(
            blueprint_id=data.get("blueprint_id", ""),
            generated_at=data.get("generated_at", ""),
            run_id=data.get("run_id"),
            objective=data.get("objective", ""),
            raw_intent=data.get("raw_intent", ""),
            confidence_score=confidence_score,
            operators=operators,
            context=data.get("context", {}),
            risk_alerts=data.get("risk_alerts", []),
            human_review_points=data.get("human_review_points", []),
            expected_outputs=expected,
        )
    @classmethod
    def _validate(cls, data: Dict, known_files: Optional[List[str]] = None) -> List[str]:
        """校验 DAG 结构（Schema + 图完整性：环/悬空引用/重复ID/文件存在性）"""
        errors = []

        # 必填字段
        if "operators" not in data:
            errors.append("缺少 operators 字段")
        elif not isinstance(data["operators"], list):
            errors.append("operators 必须是数组")
        elif len(data["operators"]) == 0:
            errors.append("operators 不能为空")

        valid_ops = VALID_OPERATORS
        operators = data.get("operators", [])
        if not isinstance(operators, list):
            operators = []

        seen_ids = set()
        dup_ids = set()
        for i, op in enumerate(operators):
            prefix = f"operators[{i}]"

            # 检查 id（含重复检测）
            if "id" not in op:
                errors.append(f"{prefix}: 缺少 id")
            else:
                if op["id"] in seen_ids:
                    dup_ids.add(op["id"])
                seen_ids.add(op["id"])

            # 检查 name（兼容处理已经在 parse 中把 type 转成了 name）
            if "name" not in op:
                if "type" in op:
                    errors.append(f"{prefix}: 使用了 type 字段，请使用 name 字段")
                else:
                    errors.append(f"{prefix}: 缺少 name")
            elif op["name"] not in valid_ops:
                errors.append(f"{prefix}: 未知算子 '{op['name']}'，有效算子: {valid_ops}")

            # 对 Load 算子特殊检查
            if op.get("name") == "Load":
                if not op.get("source_file"):
                    if "file" in op:
                        errors.append(f"{prefix}: Load 算子请使用 source_file 字段替代 file")
                    else:
                        errors.append(f"{prefix}: Load 算子缺少 source_file")
                elif known_files:
                    # Load 源文件存在性校验（幻觉文件名防御，仅在提供清单时启用）
                    sf = str(op["source_file"])
                    if sf not in known_files:
                        errors.append(
                            f"{prefix}: Load 的 source_file '{sf}' 不在数据目录中，"
                            f"可用文件: {known_files}")

        if dup_ids:
            errors.append(f"存在重复算子 id: {sorted(dup_ids)}")

        # 悬空引用与自环：input_from 必须指向真实存在的其他算子
        for i, op in enumerate(operators):
            refs = op.get("input_from") or []
            if isinstance(refs, str):
                refs = [refs]
            for ref in refs:
                if ref not in seen_ids:
                    errors.append(f"operators[{i}]: input_from 引用了不存在的算子 '{ref}'")
                elif ref == op.get("id"):
                    errors.append(f"operators[{i}]: input_from 引用自身，构成自环")

        # 循环依赖检测（Kahn 拓扑排序，仅在 id 完整且无重复时有意义）
        if operators and not dup_ids and all("id" in op for op in operators):
            in_degree = {op["id"]: 0 for op in operators}
            adj = {op["id"]: [] for op in operators}
            for op in operators:
                refs = op.get("input_from") or []
                if isinstance(refs, str):
                    refs = [refs]
                for ref in refs:
                    if ref in adj and ref != op["id"]:
                        adj[ref].append(op["id"])
                        in_degree[op["id"]] += 1
            queue = [k for k, v in in_degree.items() if v == 0]
            visited = 0
            while queue:
                cur = queue.pop(0)
                visited += 1
                for nb in adj[cur]:
                    in_degree[nb] -= 1
                    if in_degree[nb] == 0:
                        queue.append(nb)
            if visited < len(operators):
                stuck = sorted(k for k, v in in_degree.items() if v > 0)
                errors.append(f"DAG 存在循环依赖（不可执行），涉及算子: {stuck}")

        # confidence_score 范围
        score = data.get("confidence_score", 0)
        if not (0 <= score <= 1):
            errors.append(f"confidence_score 超出范围: {score}")

        return errors


# ═══════════════════════════════════════════════════════════════
# 确定性规则修复器（"规则修复优先、LLM 兜底"在编译层的落地）
# ═══════════════════════════════════════════════════════════════

def rule_fix_dag(dag_json_str: str, known_files: Optional[List[str]] = None) -> Optional[str]:
    """
    对校验失败的 DAG JSON 做确定性修复（幂等，零 LLM 成本）。

    修复能力：
    1. 算子别名 → 标准算子（filter→ColumnFilter、join→Merge 等）
    2. 缺失 id 补齐；重复算子 id → 追加序号重命名（保留首个，引用仍指向首个）
    3. input_from 悬空引用 / 自环 → 剔除无效引用
    4. Load 的 source_file 缺失或不在数据目录 → difflib 就近替换为真实文件名
    5. 跨节点循环依赖无法确定性安全拆解，留给 LLM 精修层处理

    Returns:
        修复后的 JSON 字符串；无法解析或无任何改动时返回 None
        （幂等性保证外层"修复→重校验"循环必然收敛）。
    """
    import difflib
    try:
        data = json.loads(dag_json_str)
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("operators"), list):
        return None

    ops = data["operators"]
    changed = False

    # 1) type→name 兼容 + 别名归一 + 缺失 id 补齐
    for idx, op in enumerate(ops):
        if not isinstance(op, dict):
            return None
        if "name" not in op and "type" in op:
            op["name"] = op.pop("type")
            changed = True
        if "name" in op:
            fixed_name = normalize_operator_name(op["name"])
            if fixed_name != op["name"]:
                op["name"] = fixed_name
                changed = True
        if not op.get("id"):
            op["id"] = f"op_{idx + 1}"
            changed = True

    # 2) 重复 id 重命名（保留首个占用者；下游引用继续指向首个，语义确定）
    seen_ids = set()
    for op in ops:
        oid = str(op["id"])
        if oid in seen_ids:
            all_ids = {str(o["id"]) for o in ops}
            suffix = 2
            new_id = f"{oid}_{suffix}"
            while new_id in all_ids:
                suffix += 1
                new_id = f"{oid}_{suffix}"
            op["id"] = new_id
            changed = True
            seen_ids.add(new_id)
        else:
            seen_ids.add(oid)

    # 3) 悬空引用/自环剔除
    ids = {str(op["id"]) for op in ops}
    for op in ops:
        refs = op.get("input_from")
        if isinstance(refs, str):
            refs = [refs]
            op["input_from"] = refs
            changed = True
        if isinstance(refs, list):
            cleaned_refs = [r for r in refs if r in ids and r != op["id"]]
            if cleaned_refs != refs:
                op["input_from"] = cleaned_refs
                changed = True

    # 4) Load 源文件就近纠正（幻觉文件名 → 数据目录真实文件）
    if known_files:
        for op in ops:
            if op.get("name") != "Load":
                continue
            sf = op.get("source_file") or op.get("file") or \
                (op.get("params") or {}).get("file_path")
            if sf and sf not in known_files:
                close = difflib.get_close_matches(str(sf), known_files, n=1, cutoff=0.4)
                new_sf = close[0] if close else known_files[0]
                if new_sf != op.get("source_file"):
                    op["source_file"] = new_sf
                    changed = True
            elif not sf:
                op["source_file"] = known_files[0]
                changed = True

    # 5) confidence_score 越界钳制（LLM 常输出百分数：85 → 0.85）
    score = data.get("confidence_score")
    if isinstance(score, (int, float)) and not isinstance(score, bool) \
            and not (0 <= score <= 1):
        if 1 < score <= 100:
            data["confidence_score"] = round(score / 100, 4)
        else:
            data["confidence_score"] = min(1.0, max(0.0, float(score)))
        changed = True

    if not changed:
        return None
    return json.dumps(data, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════
# 预设按钮的固化 Prompt 映射
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# 预设按钮的固化 Prompt 映射（由 config.presets 派生，单一事实来源）
# ═══════════════════════════════════════════════════════════════

def _build_preset_buttons() -> Dict[str, Dict[str, Any]]:
    """从 config.presets 注册表派生（含别名归一），取代旧的四键硬编码"""
    try:
        from config.presets import PRESETS, all_keys_with_aliases
        out: Dict[str, Dict[str, Any]] = {}
        canon: Dict[str, Dict[str, Any]] = {}
        for key, p in PRESETS.items():
            if not p.get("dag", True):
                continue
            canon[key] = {
                "system_prompt_suffix": p.get("system_suffix", ""),
                "default_operators": p.get("default_operators", []),
                "scenario": p.get("scenario", ""),
                "review_points": p.get("review_points", []),
            }
        for alias, key in all_keys_with_aliases().items():
            if key in canon:
                out[alias] = canon[key]
        return out
    except Exception:
        return {}


PRESET_BUTTONS: Dict[str, Dict[str, Any]] = _build_preset_buttons()


def get_preset_button_config(button_name: str) -> Optional[Dict[str, Any]]:
    """获取预设按钮配置"""
    return PRESET_BUTTONS.get(button_name)
