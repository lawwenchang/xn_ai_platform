#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Few-shot DAG compiler examples library.

7 high-quality audit intent -> DAG JSON pairs +
50 synthetic bank reconciliation examples from JSONL fine-tuning pool.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import List, Dict, Any, Optional

FEW_SHOT_EXAMPLES: List[Dict[str, Any]] = []

_SYNTHETIC_POOL: List[Dict[str, Any]] = []  # 50条 JSONL 合成示例


def _add(scenario, intent, summary, ops, ctx=None, risks=None):
    FEW_SHOT_EXAMPLES.append({
        "scenario": scenario,
        "user_intent": intent,
        "catalog_summary": summary,
        "dag_output": {
            "objective": scenario,
            "operators": ops,
            "context": ctx or {},
            "risk_alerts": risks or [],
        },
    })


def load_synthetic_pool(jsonl_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """加载 JSONL 合成示例池（50条对账场景），转为 few-shot 可用格式。"""
    global _SYNTHETIC_POOL
    if _SYNTHETIC_POOL:
        return _SYNTHETIC_POOL

    if jsonl_path is None:
        jsonl_path = str(
            Path(__file__).resolve().parent.parent
            / "data" / "finetune" / "synthetic" / "对账few-shot合成池_50条.jsonl"
        )
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                plan = entry.get("correct_plan", {})
                fingerprint = entry.get("table_fingerprint", {})
                left_tbl = fingerprint.get("left_table", {})
                right_tbl = fingerprint.get("right_table", {})

                # 构建 catalog_summary
                left_cols = ", ".join(left_tbl.get("columns", []))
                right_cols = ", ".join(right_tbl.get("columns", []))
                left_role = left_tbl.get("role", "unknown")
                right_role = right_tbl.get("role", "unknown")
                left_quirks = left_tbl.get("quirks", "")
                right_quirks = right_tbl.get("quirks", "")
                catalog = (
                    f"左表({left_role}): {left_cols}，{left_quirks} | "
                    f"右表({right_role}): {right_cols}，{right_quirks}"
                )

                # 构建 DAG 计划描述（不是真正的 DAG JSON，而是规划元信息）
                dag_plan = {
                    "objective": entry.get("scenario", "银行对账"),
                    "form_judgment": plan.get("form_judgment", ""),
                    "steps": plan.get("steps", []),
                    "column_mapping": plan.get("column_mapping", {}),
                    "direction_rule": plan.get("direction_rule", ""),
                    "tolerance_abs": plan.get("tolerance_abs", 0.01),
                    "date_window_days": plan.get("date_window_days", 3),
                    "special_handling": plan.get("special_handling", []),
                    "pitfalls": entry.get("pitfalls", []),
                    "expected_outputs": entry.get("expected_outputs", []),
                }

                # 取第一条用户表述作为 intent
                utterances = entry.get("user_utterances", [])
                intent = utterances[0] if utterances else "银行对账"

                _SYNTHETIC_POOL.append({
                    "case_id": entry.get("case_id", ""),
                    "scenario": entry.get("scenario", ""),
                    "user_intent": intent,
                    "catalog_summary": catalog,
                    "dag_output": dag_plan,
                    "perturbation": entry.get("perturbation", []),
                })
    except Exception as e:
        print(f"[Few-shot] JSONL 合成池加载失败: {e}")

    return _SYNTHETIC_POOL


def build_synthetic_few_shot(intent: str, max_examples: int = 3) -> str:
    """从50条合成池中选取与意图最相关的示例，构建 Few-shot 提示文本。"""
    pool = load_synthetic_pool()
    if not pool:
        return ""

    # 关键词匹配打分
    intent_lower = intent.lower()
    keyword_map = {
        "对账": ["detail2detail", "extract_partial"],
        "核对": ["detail2detail", "extract_partial"],
        "逐笔": ["detail2detail"],
        "未达": ["detail2detail"],
        "流水": ["detail2detail"],
        "序时账": ["detail2detail"],
        "日记账": ["detail2detail"],
        "提取": ["extract_partial"],
        "筛选": ["extract_partial"],
        "专项": ["extract_partial"],
        "回款": ["extract_partial"],
        "社保": ["extract_partial"],
        "医保": ["extract_partial"],
    }
    scores = {}
    for kw, scenarios in keyword_map.items():
        if kw in intent_lower:
            for i, ex in enumerate(pool):
                if ex["scenario"] in scenarios:
                    scores[i] = scores.get(i, 0) + 1

    sorted_idx = sorted(scores.keys(), key=lambda i: scores[i], reverse=True)[:max_examples]
    if not sorted_idx:
        # 关键词零命中 → embedding 兜底
        try:
            from config.scenario_packs import _get_embedder
            model = _get_embedder()
            if model:
                from sentence_transformers.util import cos_sim
                texts = [ex["user_intent"] + " " + ex["catalog_summary"] for ex in pool]
                pool_vecs = model.encode(texts, convert_to_tensor=True)
                intent_vec = model.encode(intent, convert_to_tensor=True)
                emb_scores = {}
                for i in range(len(pool)):
                    emb_scores[i] = float(cos_sim(intent_vec, pool_vecs[i])[0][0])
                sorted_idx = sorted(emb_scores, key=lambda i: emb_scores[i], reverse=True)[:max_examples]
                if sorted_idx and emb_scores[sorted_idx[0]] < 0.3:
                    sorted_idx = []  # 相似度太低，不如不注入
        except Exception:
            pass

    if not sorted_idx:
        return ""

    lines = ["", f"## 对账场景 Few-shot 参考（{len(sorted_idx)} 个相似案例）", ""]
    for rank, idx in enumerate(sorted_idx, 1):
        ex = pool[idx]
        plan = ex["dag_output"]
        lines.append(f"### 案例 {rank}：{ex['case_id']}")
        lines.append(f"【审计意图】{ex['user_intent']}")
        lines.append(f"【数据结构】{ex['catalog_summary']}")
        lines.append(f"【判断】{plan.get('form_judgment', '')}")
        lines.append(f"【步骤】")
        for s in plan.get("steps", []):
            lines.append(f"  - {s}")
        if plan.get("column_mapping"):
            lines.append(f"【列映射】{json.dumps(plan['column_mapping'], ensure_ascii=False)}")
        if plan.get("direction_rule"):
            lines.append(f"【方向规则】{plan['direction_rule']}")
        if plan.get("pitfalls"):
            lines.append(f"【易错点】{'; '.join(plan['pitfalls'])}")
        lines.append(f"【容差】abs={plan.get('tolerance_abs')}, 窗口={plan.get('date_window_days')}天")
        lines.append("")

    lines.append("请参考以上案例的步骤、列映射和方向规则，处理当前审计师的真实需求。")
    return "\n".join(lines)


# Example 1: Text Pattern Filtering & Table Matching
_add("文本筛选与匹配",
    "帮我找出银行流水中所有与特定业务相关的记录，按对方机构汇总金额，再和业务汇总表比对。",
    "文件1: 银行流水.xlsx (列: 交易日期, 摘要, 对方客户名称, 交易金额)\n文件2: 业务汇总表.xlsx (列: 机构名称, 月份, 业务金额)",
    [
        {"id": "op_1", "name": "Load", "source_file": "银行流水.xlsx", "output_alias": "df_bank", "params": {}},
        {"id": "op_2", "name": "Load", "source_file": "业务汇总表.xlsx", "output_alias": "df_summary", "params": {}},
        {"id": "op_3", "name": "RegexFilter", "input_from": ["op_1"], "output_alias": "df_filtered",
         "params": {"columns": ["摘要", "对方客户名称"], "pattern": "示例关键词1|示例关键词2", "case_sensitive": False}},
        {"id": "op_4", "name": "NoiseFilter", "input_from": ["op_3"], "output_alias": "df_clean",
         "params": {"columns": ["摘要"], "noise_patterns": ["手续费", "短信费", "年费", "账户管理费", "测试"]}},
        {"id": "op_5", "name": "GroupBy", "input_from": ["op_4"], "output_alias": "df_bank_agg",
         "params": {"by": ["对方客户名称"], "aggregations": {"交易金额": "sum"}}},
        {"id": "op_6", "name": "Merge", "input_from": ["op_5", "op_2"], "output_alias": "df_merged",
         "params": {"how": "left", "left_on": ["对方客户名称"], "right_on": ["机构名称"]}},
        {"id": "op_7", "name": "Diff", "input_from": ["op_6"], "output_alias": "df_result",
         "params": {"col_a": "交易金额_sum", "col_b": "业务金额", "tolerance_pct": 1.0, "output_mode": "all"}},
        {"id": "op_8", "name": "Export", "input_from": ["op_7"], "params": {"filename": "业务核对结果.xlsx"}},
    ],
    {"tolerance_pct": 1.0, "noise_rules": ["手续费", "短信费", "年费", "利息"]},
    [{"level": "HIGH", "rule": "差异超过容差阈值时需人工复核"}],
)

# Example 2: Bank Statement Reconciliation
_add("银行流水与台账核对",
    "拿银行流水和台账核对，看哪些台账金额在流水里找不到对应，差异控制在1%以内。",
    "文件1: 银行流水.xlsx (列: 交易日期, 摘要, 对方客户名称, 交易金额)\n文件2: 业务台账.xlsx (列: 日期, 客户名称, 业务金额, 业务类型)",
    [
        {"id": "op_1", "name": "Load", "source_file": "银行流水.xlsx", "output_alias": "df_bank", "params": {}},
        {"id": "op_2", "name": "Load", "source_file": "业务台账.xlsx", "output_alias": "df_ledger", "params": {}},
        {"id": "op_3", "name": "NoiseFilter", "input_from": ["op_1"], "output_alias": "df_bank_clean",
         "params": {"columns": ["摘要"], "noise_patterns": ["手续费", "短信费", "年费", "账户管理费"]}},
        {"id": "op_4", "name": "Merge", "input_from": ["op_3", "op_2"], "output_alias": "df_merged",
         "params": {"how": "outer", "left_on": ["对方客户名称", "交易金额"], "right_on": ["客户名称", "业务金额"], "date_window_days": 3}},
        {"id": "op_5", "name": "Diff", "input_from": ["op_4"], "output_alias": "df_diff",
         "params": {"col_a": "交易金额", "col_b": "业务金额", "tolerance_abs": 0.01, "output_mode": "all"}},
        {"id": "op_6", "name": "Export", "input_from": ["op_5"], "params": {"filename": "差异汇总表.xlsx"}},
    ],
    {"match_keys": ["客户名称", "交易金额"], "tolerance_abs": 0.01},
    [{"level": "HIGH", "rule": "单笔差异 > 5% 标记为异常"}, {"level": "MEDIUM", "rule": "仅台账有但流水无，可能存在未达账项"}],
)

# Example 3: Large Transaction Screening
_add("大额交易筛查",
    "把银行流水中单笔超过100万的交易筛出来，按对方户名汇总金额和笔数，标注风险等级。",
    "文件1: 银行流水.xlsx (列: 交易日期, 摘要, 对方客户名称, 交易金额, 余额)",
    [
        {"id": "op_1", "name": "Load", "source_file": "银行流水.xlsx", "output_alias": "df_raw", "params": {}},
        {"id": "op_2", "name": "ColumnFilter", "input_from": ["op_1"], "output_alias": "df_large",
         "params": {"column": "交易金额", "operator": ">", "value": 1000000}},
        {"id": "op_3", "name": "GroupBy", "input_from": ["op_2"], "output_alias": "df_summary",
         "params": {"by": ["对方客户名称"], "aggregations": {"交易金额": ["sum", "count", "max"]}}},
        {"id": "op_4", "name": "ConditionCheck", "input_from": ["op_3"], "output_alias": "df_flagged",
         "params": {"rules": [{"condition": "交易金额_sum > 500000", "tag": "HIGH"},
                              {"condition": "交易金额_count > 20", "tag": "MEDIUM"}], "default_tag": "LOW"}},
        {"id": "op_5", "name": "Export", "input_from": ["op_4"], "params": {"filename": "大额交易筛查结果.xlsx"}},
    ],
    {"threshold": 1000000, "risk_levels": {"LOW": "正常", "MEDIUM": "关注", "HIGH": "重点复核"}},
    [{"level": "HIGH", "rule": "单笔 >= 50万标记为高风险，需重点复核"}],
)

# Example 4: Multi-table Diff Summary
_add("两表数据差异汇总",
    "已经把银行流水和业务回款表按机构匹配好了，现在按月份汇总两边金额，看每个月差异多大，差异超5%的标红导出。",
    "文件1: 银行流水汇总.xlsx (列: 机构名称, 月份, 银行到账金额)\n文件2: 业务回款记录.xlsx (列: 机构, 结算月份, 回款金额)",
    [
        {"id": "op_1", "name": "Load", "source_file": "银行流水汇总.xlsx", "output_alias": "df_bank", "params": {}},
        {"id": "op_2", "name": "Load", "source_file": "业务回款记录.xlsx", "output_alias": "df_biz", "params": {}},
        {"id": "op_3", "name": "GroupBy", "input_from": ["op_1"], "output_alias": "df_bank_monthly",
         "params": {"by": ["机构名称", "月份"], "aggregations": {"银行到账金额": "sum"}}},
        {"id": "op_4", "name": "GroupBy", "input_from": ["op_2"], "output_alias": "df_biz_monthly",
         "params": {"by": ["机构", "结算月份"], "aggregations": {"回款金额": "sum"}}},
        {"id": "op_5", "name": "Merge", "input_from": ["op_3", "op_4"], "output_alias": "df_merged",
         "params": {"how": "outer", "left_on": ["机构名称", "月份"], "right_on": ["机构", "结算月份"]}},
        {"id": "op_6", "name": "Diff", "input_from": ["op_5"], "output_alias": "df_diff",
         "params": {"col_a": "银行到账金额", "col_b": "回款金额", "tolerance_pct": 5.0, "output_mode": "exceed_only"}},
        {"id": "op_7", "name": "Export", "input_from": ["op_6"], "params": {"filename": "月度差异报告.xlsx"}},
    ],
    {"tolerance_pct": 5.0, "group_by_period": "monthly"},
    [{"level": "HIGH", "rule": "单月差异率 > 5% 需逐笔追溯原始凭证"}],
)

# Example 5: Data Cleaning & Classification
_add("单表数据清洗与分类汇总",
    "帮我把费用明细表清洗一下，去掉空行和手续费等噪音，按科目汇总金额，导出干净的汇总表。",
    "文件1: 费用明细.xlsx (列: 日期, 科目名称, 摘要, 金额, 经办人)",
    [
        {"id": "op_1", "name": "Load", "source_file": "费用明细.xlsx", "output_alias": "df_raw", "params": {}},
        {"id": "op_2", "name": "NoiseFilter", "input_from": ["op_1"], "output_alias": "df_clean",
         "params": {"columns": ["摘要"], "noise_patterns": ["手续费", "测试", "冲正", "作废"]}},
        {"id": "op_3", "name": "GroupBy", "input_from": ["op_2"], "output_alias": "df_grouped",
         "params": {"by": ["科目名称"], "aggregations": {"金额": "sum"}}},
        {"id": "op_4", "name": "Sort", "input_from": ["op_3"], "output_alias": "df_sorted",
         "params": {"by": ["金额"], "ascending": False}},
        {"id": "op_5", "name": "Export", "input_from": ["op_4"], "params": {"filename": "费用科目汇总表.xlsx"}},
    ],
    {"noise_rules": ["手续费", "测试", "冲正", "作废"]},
    [],
)

# Example 6: Audit Report Generation
_add("审计报告生成",
    "根据审定后的调整分录生成审计报告，汇总科目余额，确保报告数字与底稿审定数一致，借贷平衡，导出Word文档。",
    "文件1: 审定调整分录.xlsx (列: 科目编码, 科目名称, 借方金额, 贷方金额, 调整说明)\n文件2: 科目余额表.xlsx (列: 科目编码, 科目名称, 期初余额, 本期借方, 本期贷方, 期末余额)",
    [
        {"id": "op_1", "name": "Load", "source_file": "审定调整分录.xlsx", "output_alias": "df_adj", "params": {}},
        {"id": "op_2", "name": "Load", "source_file": "科目余额表.xlsx", "output_alias": "df_bal", "params": {}},
        {"id": "op_3", "name": "Aggregate", "input_from": ["op_1"], "output_alias": "df_summary",
         "params": {"by": ["科目编码", "科目名称"], "aggregations": {"借方金额": "sum", "贷方金额": "sum"}}},
        {"id": "op_4", "name": "Reconcile", "input_from": ["op_3", "op_2"], "output_alias": "df_report",
         "params": {"left_key": "科目编码", "right_key": "科目编码", "verify_rule": "报告数字必须与底稿审定数一致"}},
        {"id": "op_5", "name": "Export", "input_from": ["op_4"],
         "params": {"filename": "审计报告_附调整分录.docx", "format": "docx",
                    "template": "审计报告模板.docx", "include_attachments": True}},
    ],
    {"verify_rule": "报告数字与底稿审定数同源勾稽"},
    [{"level": "CRITICAL", "rule": "报告数字与底稿不一致，禁止出具报告"},
     {"level": "HIGH", "rule": "借贷不平衡时自动生成补充分录并标记人工复核"}],
)

# Example 7: Trial Balance Auto-Correction
_add("试算平衡与自动纠错",
    "检查调整分录的借贷是否平衡，如果不平衡，自动计算差额并生成补充分录使其平衡，标注需要人工复核的异常项目。",
    "文件1: 待审调整分录.xlsx (列: 序号, 日期, 科目编码, 科目名称, 借方金额, 贷方金额, 摘要)",
    [
        {"id": "op_1", "name": "Load", "source_file": "待审调整分录.xlsx", "output_alias": "df_entries", "params": {}},
        {"id": "op_2", "name": "Aggregate", "input_from": ["op_1"], "output_alias": "df_check",
         "params": {"aggregations": {"借方金额": "sum", "贷方金额": "sum"}}},
        {"id": "op_3", "name": "ConditionCheck", "input_from": ["op_2"], "output_alias": "df_balanced",
         "params": {"rules": [{"condition": "abs(借方金额_sum - 贷方金额_sum) < 0.01", "tag": "借贷平衡"}],
                    "default_tag": "借贷不平衡"}},
        {"id": "op_4", "name": "AuditAdjustment", "input_from": ["op_1", "op_3"], "output_alias": "df_corrected",
         "params": {"trigger": "借贷不平衡", "adjustment_type": "补充分录",
                    "diff_account": "待处理差额",
                    "generate_explanation": "系统自动生成的平衡调整分录，差额={diff}元，请人工确认",
                    "add_review_flag": True}},
        {"id": "op_5", "name": "Export", "input_from": ["op_4"],
         "params": {"filename": "调整分录_已纠正.xlsx"}},
    ],
    {"auto_correct": True, "balance_tolerance": 0.01, "diff_account": "待处理差额"},
    [{"level": "CRITICAL", "rule": "系统自动生成的分录必须经审计师确认后方可生效"},
     {"level": "HIGH", "rule": "单笔差额超过重要性水平时，禁止自动调整，必须人工判断"}],
)


# Example 11: Journal vs Bank Statement Item-by-Item Reconciliation
_add("序时账与银行流水逐笔对账",
    "把序时账（农行5927账户）和银行流水逐笔核对，看两个文件的账款是否相符。",
    "文件1: 序时账.xlsx (列: 序号, 月, 日期, 凭证号, 摘要, 借方金额, 贷方金额, 银行账号)\n文件2: 银行流水.xlsx (列: 序号, 银行账号, 日期, 摘要, 借方（支取）, 贷方（收入）)",
    [
        {"id": "op_1", "name": "Load", "source_file": "序时账.xlsx", "output_alias": "df_journal", "params": {}},
        {"id": "op_2", "name": "Load", "source_file": "银行流水.xlsx", "output_alias": "df_bank", "params": {}},
        {"id": "op_3", "name": "Reconcile", "input_from": ["op_1", "op_2"], "output_alias": "df_reconciled",
         "params": {"tolerance_abs": 0.01, "date_window_days": 3,
                    "note": "方向镜像：序时账借方金额↔流水贷方（收入），序时账贷方金额↔流水借方（支取）；流水多账户时先按银行账号过滤"}},
        {"id": "op_4", "name": "Export", "input_from": ["op_3"], "params": {"filename": "逐笔对账底稿.xlsx"}},
    ],
    {"tolerance_abs": 0.01, "date_window_days": 3, "direction_mirror": True,
     "unmatched_default": "待人工核查"},
    [{"level": "HIGH", "rule": "逐笔核对必须精确到分（±0.01元）；未匹配项默认待人工核查，不得默认未达账项"},
     {"level": "MEDIUM", "rule": "未达账项（银收企未收/银付企未付/企收银未收/企付银未付）需期后到账验证"}],
)

# Helper Functions

def build_few_shot_section(max_examples: int = 7) -> str:
    """Build Few-shot text for appending to Dify/vLLM System Prompt."""
    lines = [
        "",
        "## Few-shot 示例（请参照以下范例生成 DAG JSON）",
        "",
    ]
    for i, ex in enumerate(FEW_SHOT_EXAMPLES[:max_examples], 1):
        lines.append(f"### 示例 {i}：{ex['scenario']}")
        lines.append("")
        lines.append(f"【审计师意图】{ex['user_intent']}")
        lines.append(f"【数据目录】{ex['catalog_summary']}")
        lines.append("【期望的 DAG JSON 输出】")
        lines.append("```json")
        lines.append(json.dumps(ex["dag_output"], ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")
    lines.append("现在，请根据以上范例风格，处理审计师的真实需求。")

# Example 8: Multi-Sheet Workbook
_add("多Sheet工作簿处理",
    "工作簿里有三个Sheet分别是收入、支出、汇总，把收入和支出都加载出来，按月份合并到一起看看有没有对不上的。",
    "文件1: 财务数据.xlsx (Sheet: 收入, 支出, 汇总; 列: 月份, 科目, 金额, 摘要)",
    [
        {"id": "op_1", "name": "Load", "source_file": "财务数据.xlsx",
         "params": {"sheet_name": "收入"}, "output_alias": "df_income"},
        {"id": "op_2", "name": "Load", "source_file": "财务数据.xlsx",
         "params": {"sheet_name": "支出"}, "output_alias": "df_expense"},
        {"id": "op_3", "name": "GroupBy", "input_from": ["op_1"], "output_alias": "df_income_agg",
         "params": {"by": ["月份"], "aggregations": {"金额": "sum"}}},
        {"id": "op_4", "name": "GroupBy", "input_from": ["op_2"], "output_alias": "df_expense_agg",
         "params": {"by": ["月份"], "aggregations": {"金额": "sum"}}},
        {"id": "op_5", "name": "Merge", "input_from": ["op_3", "op_4"], "output_alias": "df_merged",
         "params": {"how": "outer", "left_on": ["月份"], "right_on": ["月份"]}},
        {"id": "op_6", "name": "Diff", "input_from": ["op_5"], "output_alias": "df_result",
         "params": {"col_a": "金额_x", "col_b": "金额_y", "tolerance_pct": 1.0}},
        {"id": "op_7", "name": "Export", "input_from": ["op_6"],
         "params": {"filename": "月度核对结果.xlsx"}},
    ],
    {"tolerance_pct": 1.0},
    [{"level": "MEDIUM", "rule": "月度差异超过1%需追溯原始凭证"}],
)

# Example 9: Pivot Table
_add("数据透视表生成",
    "按月份和科目交叉汇总金额，做一个交叉透视表，行是月份，列是科目，值是金额合计。",
    "文件1: 费用明细.xlsx (列: 月份, 科目名称, 金额, 摘要)",
    [
        {"id": "op_1", "name": "Load", "source_file": "费用明细.xlsx",
         "params": {}, "output_alias": "df_raw"},
        {"id": "op_2", "name": "Pivot", "input_from": ["op_1"], "output_alias": "df_pivot",
         "params": {"index": ["月份"], "columns": ["科目名称"], "values": "金额",
                    "aggfunc": "sum", "fill_value": 0}},
        {"id": "op_3", "name": "Export", "input_from": ["op_2"],
         "params": {"filename": "费用透视表.xlsx"}},
    ],
    {},
    [],
)

# Example 10: Column Name Fuzzy Matching
_add("列名模糊匹配",
    "两张表的列名不完全一样，'客户名称'和'对方客户名称'实际是同一个意思，帮我按这个列合并两张表。",
    "文件1: 销售表.xlsx (列: 客户名称, 金额, 日期)\n文件2: 回款表.xlsx (列: 对方客户名称, 回款金额, 回款日期)",
    [
        {"id": "op_1", "name": "Load", "source_file": "销售表.xlsx",
         "params": {}, "output_alias": "df_sales"},
        {"id": "op_2", "name": "Load", "source_file": "回款表.xlsx",
         "params": {}, "output_alias": "df_payment"},
        {"id": "op_3", "name": "Transform", "input_from": ["op_1"], "output_alias": "df_sales_normalized",
         "params": {"columns": ["客户名称"], "operation": "standardize_name"}},
        {"id": "op_4", "name": "Transform", "input_from": ["op_2"], "output_alias": "df_payment_normalized",
         "params": {"columns": ["对方客户名称"], "operation": "standardize_name"}},
        {"id": "op_5", "name": "Merge", "input_from": ["op_3", "op_4"], "output_alias": "df_merged",
         "params": {"how": "outer", "left_on": ["客户名称"], "right_on": ["对方客户名称"]}},
        {"id": "op_6", "name": "Export", "input_from": ["op_5"],
         "params": {"filename": "合并结果.xlsx"}},
    ],
    {},
    [],
)

# Example 11: 单表筛选去重排序（数据加工杂活）
_add("单表筛选去重排序",
    "把费用台账里所有与折旧相关的记录筛出来，按部门去重，按金额降序排，看各部门花了多少钱。",
    "文件1: 费用台账.xlsx (列: 凭证日期, 凭证号, 摘要, 部门, 费用金额, 费用科目)",
    [
        {"id": "op_1", "name": "Load", "source_file": "费用台账.xlsx",
         "params": {}, "output_alias": "df_raw"},
        {"id": "op_2", "name": "RegexFilter", "input_from": ["op_1"], "output_alias": "df_filtered",
         "params": {"columns": ["摘要", "费用科目"], "pattern": "折旧|累计折旧|资产减值",
                    "case_sensitive": False}},
        {"id": "op_3", "name": "GroupBy", "input_from": ["op_2"], "output_alias": "df_grouped",
         "params": {"by": ["部门"], "aggregations": {"费用金额": ["sum", "count"]}}},
        {"id": "op_4", "name": "Sort", "input_from": ["op_3"], "output_alias": "df_sorted",
         "params": {"columns": ["费用金额_sum"], "ascending": False}},
        {"id": "op_5", "name": "Export", "input_from": ["op_4"],
         "params": {"filename": "折旧费用按部门汇总.xlsx"}},
    ],
    {"duplicate_check": ["部门"], "sort_key": "费用金额_sum"},
    [{"level": "MEDIUM", "rule": "各部门金额差异超过均值±2倍标准差时人工复核"}],
)

# Example 12: 多条件计算列（数据加工杂活）
_add("多条件计算列",
    "员工薪资表里新增三列：个税（超额累进）、社保（基数×比例）、实发（应发−个税−社保），最后按部门汇总平均应发和平均实发。",
    "文件1: 员工薪资表.xlsx (列: 员工编号, 姓名, 部门, 应发工资, 社保基数, 社保比例)",
    [
        {"id": "op_1", "name": "Load", "source_file": "员工薪资表.xlsx",
         "params": {}, "output_alias": "df_raw"},
        {"id": "op_2", "name": "Transform", "input_from": ["op_1"], "output_alias": "df_calculated",
         "params": {"columns": ["应发工资", "社保基数", "社保比例"],
                    "operation": "compute_columns",
                    "formulas": {
                        "个税": "df['应发工资'].apply(lambda x: max(0, (x-5000)*0.03) if x<=8000 else (x-5000)*0.1-210)",
                        "社保": "df['社保基数'] * df['社保比例']",
                        "实发": "df['应发工资'] - df['个税'] - df['社保']"
                    }}},
        {"id": "op_3", "name": "GroupBy", "input_from": ["op_2"], "output_alias": "df_dept_avg",
         "params": {"by": ["部门"],
                    "aggregations": {"应发工资": "mean", "实发": "mean"}}},
        {"id": "op_4", "name": "Export", "input_from": ["op_3"],
         "params": {"filename": "部门薪资汇总.xlsx"}},
    ],
    {"calc_tolerance": 0.01, "verify_total": "应发=实发+个税+社保"},
    [],
)

# Example 13: 数据透视汇总（数据加工杂活）
_add("数据透视汇总",
    "把销售收入明细按月份和产品大类做透视，看每个月每个产品的销售额和毛利，最后算出毛利率。",
    "文件1: 销售收入明细.xlsx (列: 销售日期, 产品名称, 产品类别, 销售额, 成本, 毛利)",
    [
        {"id": "op_1", "name": "Load", "source_file": "销售收入明细.xlsx",
         "params": {}, "output_alias": "df_raw"},
        {"id": "op_2", "name": "Transform", "input_from": ["op_1"], "output_alias": "df_with_month",
         "params": {"columns": ["销售日期"], "operation": "extract_date_part",
                    "date_part": "month", "new_column": "月份"}},
        {"id": "op_3", "name": "GroupBy", "input_from": ["op_2"], "output_alias": "df_pivot",
         "params": {"by": ["月份", "产品类别"],
                    "aggregations": {"销售额": "sum", "毛利": "sum"}}},
        {"id": "op_4", "name": "Transform", "input_from": ["op_3"], "output_alias": "df_final",
         "params": {"columns": ["销售额_sum", "毛利_sum"],
                    "operation": "compute_columns",
                    "formulas": {"毛利率": "df['毛利_sum'] / df['销售额_sum'] * 100"}}},
        {"id": "op_5", "name": "Sort", "input_from": ["op_4"], "output_alias": "df_sorted",
         "params": {"columns": ["月份", "销售额_sum"], "ascending": [True, False]}},
        {"id": "op_6", "name": "Export", "input_from": ["op_5"],
         "params": {"filename": "月度产品销售透视.xlsx"}},
    ],
    {"period": "月度", "dimensions": ["产品类别"], "metrics": ["销售额", "毛利", "毛利率"]},
    [{"level": "LOW", "rule": "负毛利月份单独标注"}],
)

def build_few_shot_section(max_examples: int = 10) -> str:
    """Build Few-shot text."""
    lines = ["", "## Few-shot 示例（请参照以下范例生成 DAG JSON）", ""]
    for i, ex in enumerate(FEW_SHOT_EXAMPLES[:max_examples], 1):
        lines.append(f"### 示例 {i}：{ex['scenario']}")
        lines.append("")
        lines.append(f"【审计师意图】{ex['user_intent']}")
        lines.append(f"【数据目录】{ex['catalog_summary']}")
        lines.append("【期望的 DAG JSON 输出】")
        lines.append("```json")
        lines.append(json.dumps(ex["dag_output"], ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")
    lines.append("现在，请根据以上范例风格，处理审计师的真实需求。")
    return "\n".join(lines)


def get_examples_by_keyword(intent: str, max_examples: int = 3) -> list:
    """Return most relevant examples matching the user intent keywords."""
    keywords_map = {
        "筛选": [0, 2, 10], "匹配": [0, 1, 3], "核对": [1, 3, 10], "对账": [1, 10],
        "流水": [1, 10, 2], "台账": [1], "序时账": [10], "逐笔": [10], "日记账": [10],
        "大额": [2], "筛查": [2], "风险": [2],
        "差异": [3], "汇总": [3, 4, 10, 11, 12], "月份": [3, 12],
        "清洗": [4], "分类": [4, 10], "导出": [4],
        "报告": [5], "勾稽": [5], "底稿": [5], "审定": [5], "出具": [5], "同源": [5],
        "借贷": [6], "平衡": [6], "纠错": [6], "调整分录": [6],
        "补充分录": [6], "不平衡": [6], "差额调整": [6],
        "Sheet": [7], "工作簿": [7], "多表": [7],
        "透视表": [8, 12], "交叉汇总": [8, 12], "交叉": [8],
        "列名": [9], "模糊匹配": [9], "名字不一样": [9], "对不上": [9],
        # 数据加工类新增关键词（→ Examples 10/11/12）
        "去重": [10], "排序": [10, 12], "降序": [10], "升序": [12],
        "折旧": [10], "部门": [10, 11], "花了多少": [10],
        "计算列": [11], "新增列": [11], "公式": [11], "个税": [11],
        "社保": [11], "实发": [11], "平均应发": [11],
        "透视": [12, 8], "毛利率": [12], "销售额": [12],
        "产品类别": [12], "合计": [10], "平均值": [11], "占比": [12],
    }
    scores = {}
    intent_lower = intent.lower()
    for kw, indices in keywords_map.items():
        if kw in intent_lower:
            for idx in indices:
                scores[idx] = scores.get(idx, 0) + 1
    sorted_idx = sorted(scores.keys(), key=lambda i: scores[i], reverse=True)
    return [FEW_SHOT_EXAMPLES[i] for i in sorted_idx[:max_examples]]


def build_dynamic_few_shot(intent: str, max_examples: int = 3) -> str:
    """B3 动态注入：按意图关键词选 top-N 相关范例构建 Few-shot 文本。
    命中关键词 -> 只注入最相关的 max_examples 条（省 token、防照抄无关样例）；
    未命中 -> 回退全量 build_few_shot_section()，行为与旧版一致。"""
    hits = get_examples_by_keyword(intent or "", max_examples=max_examples)
    if not hits:
        return build_few_shot_section()
    lines = ["", f"## Few-shot 示例（与本次意图最相关的 {len(hits)} 个范例）", ""]
    for i, ex in enumerate(hits, 1):
        lines.append(f"### 示例 {i}：{ex['scenario']}")
        lines.append("")
        lines.append(f"【审计师意图】{ex['user_intent']}")
        lines.append(f"【数据目录】{ex['catalog_summary']}")
        lines.append("【期望的 DAG JSON 输出】")
        lines.append("```json")
        lines.append(json.dumps(ex["dag_output"], ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")
    lines.append("现在，请根据以上范例风格，处理审计师的真实需求。")
    return "\n".join(lines)