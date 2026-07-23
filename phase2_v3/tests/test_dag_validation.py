#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DAG 校验强化 / 规则修复器 / 规则统计 / 引用扩展 —— 离线验证
运行: python tests/test_dag_validation.py
"""
import json
import sys
from pathlib import Path

# Windows GBK 控制台防御：强制 UTF-8 输出
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.dag_compiler import DAGBlueprint, DAGParser, Operator, rule_fix_dag

PASS = 0

def ok(name):
    global PASS
    PASS += 1
    print(f"[PASS] {name}")

# 1) 算子别名自愈 + 缺失 input_from 自动串链
raw = json.dumps({
    "operators": [
        {"id": "op_1", "type": "load", "file": "银行流水.xlsx"},
        {"id": "op_2", "name": "filter", "params": {"column": "金额"}},
        {"id": "op_3", "name": "output", "params": {}},
    ],
    "human_review_points": ["复核筛选条件"],
    "confidence_score": 0.8,
}, ensure_ascii=False)
bp = DAGParser.parse(raw)
names = [op.name for op in bp.operators]
assert names == ["Load", "ColumnFilter", "Export"], names
assert bp.operators[1].input_from == ["op_1"]
assert bp.operators[2].input_from == ["op_2"]
assert bp.get_execution_order() == ["op_1", "op_2", "op_3"]
ok("别名自愈(load/filter/output) + input_from 自动串链 + 拓扑排序")

# 2) think 块剥离（成对标签 + Thinking-2507 仅闭合标签两种形态）
for wrapped in (f"<think>推理过程...</think>\n{raw}", f"胡乱推理...</think>\n{raw}"):
    assert DAGParser.parse(wrapped).operator_count == 3
ok("<think> 思考块剥离（两种形态）")

# 3) 循环依赖 → 校验报错
cyc = json.dumps({
    "operators": [
        {"id": "a", "name": "Load", "source_file": "x.xlsx"},
        {"id": "b", "name": "Sort", "input_from": ["c"]},
        {"id": "c", "name": "Export", "input_from": ["b"]},
    ],
    "human_review_points": ["x"], "confidence_score": 0.5,
})
try:
    DAGParser.parse(cyc)
    raise AssertionError("循环依赖未被检出")
except ValueError as e:
    assert "循环依赖" in str(e), e
ok("循环依赖检测(b↔c)")

# 4) get_execution_order 对环抛错（修复旧版静默丢节点缺陷）
bp_cyc = DAGBlueprint(blueprint_id="t", generated_at="", operators=[
    Operator(id="a", name="Load", input_from=["b"]),
    Operator(id="b", name="Export", input_from=["a"]),
])
try:
    bp_cyc.get_execution_order()
    raise AssertionError("拓扑排序未对环抛错")
except ValueError as e:
    assert "循环依赖" in str(e)
ok("get_execution_order 环防护")

# 5) 悬空引用 → 报错；rule_fix_dag 剔除后可解析
dangling = json.dumps({
    "operators": [
        {"id": "op_1", "name": "Load", "source_file": "x.xlsx"},
        {"id": "op_2", "name": "Export", "input_from": ["ghost", "op_1"]},
    ],
    "human_review_points": ["x"], "confidence_score": 0.5,
})
try:
    DAGParser.parse(dangling)
    raise AssertionError("悬空引用未被检出")
except ValueError as e:
    assert "不存在的算子" in str(e)
fixed = rule_fix_dag(dangling)
assert fixed and "ghost" not in json.loads(fixed)["operators"][1]["input_from"]
DAGParser.parse(fixed)
ok("悬空引用检测 + 规则修复器剔除")

# 6) 重复 id → 报错；规则修复器重命名
dup = json.dumps({
    "operators": [
        {"id": "op_1", "name": "Load", "source_file": "x.xlsx"},
        {"id": "op_1", "name": "Export"},
    ],
    "human_review_points": ["x"], "confidence_score": 0.5,
})
try:
    DAGParser.parse(dup)
    raise AssertionError("重复 id 未被检出")
except ValueError as e:
    assert "重复算子 id" in str(e)
fixed2 = rule_fix_dag(dup)
ids = [o["id"] for o in json.loads(fixed2)["operators"]]
assert len(set(ids)) == 2, ids
DAGParser.parse(fixed2)
ok("重复算子 id 检测 + 规则修复器重命名")

# 7) known_files 存在性校验 + difflib 就近纠正（幻觉文件名防御）
wrongfile = json.dumps({
    "operators": [{"id": "op_1", "name": "Load", "source_file": "银行流水.xls"}],
    "human_review_points": ["x"], "confidence_score": 0.5,
}, ensure_ascii=False)
known = ["银行流水.xlsx", "医保回款汇总.xlsx"]
try:
    DAGParser.parse(wrongfile, known_files=known)
    raise AssertionError("不存在的 source_file 未被检出")
except ValueError as e:
    assert "不在数据目录" in str(e)
fixed3 = rule_fix_dag(wrongfile, known_files=known)
assert json.loads(fixed3)["operators"][0]["source_file"] == "银行流水.xlsx"
DAGParser.parse(fixed3, known_files=known)
ok("Load 源文件存在性校验 + difflib 就近纠正")

# 8) 规则修复器幂等（第二次无改动返回 None → 保证外层循环收敛）
assert rule_fix_dag(fixed3, known_files=known) is None
ok("规则修复器幂等性")

# 8b) 合法 JSON 值内含单引号：不再被引号启发式破坏（惰性修复）
quoted = json.dumps({"operators": [
    {"id": "op_1", "name": "Load", "source_file": "a.xlsx",
     "description": "排除'手续费'类噪音行"}],
    "human_review_points": ["x"], "confidence_score": 0.5}, ensure_ascii=False)
DAGParser.parse(quoted)
ok("值内单引号 JSON 解析安全（惰性启发式修复）")

# 8c) confidence_score 百分数 → 规则修复器钳制为 0-1
pct = json.dumps({"operators": [{"id": "op_1", "name": "Load", "source_file": "a.xlsx"}],
                  "human_review_points": ["x"], "confidence_score": 85})
try:
    DAGParser.parse(pct)
    raise AssertionError("越界 confidence 未被检出")
except ValueError:
    pass
fixed_pct = rule_fix_dag(pct)
assert json.loads(fixed_pct)["confidence_score"] == 0.85
DAGParser.parse(fixed_pct)
ok("confidence_score 85 -> 0.85 钳制修复")

# 8d) 算子名前缀兜底：LoadData→Load、SortDescending→Sort
pre = json.dumps({"operators": [
    {"id": "op_1", "name": "LoadData", "source_file": "a.xlsx"},
    {"id": "op_2", "name": "SortDescending", "input_from": ["op_1"]}],
    "human_review_points": ["x"], "confidence_score": 0.5})
assert [o.name for o in DAGParser.parse(pre).operators] == ["Load", "Sort"]
ok("算子名前缀兜底(LoadData/SortDescending)")

print(f"\n—— dag_compiler {PASS} 项通过，继续 matching/rag ——\n")

# 9) 匹配引擎规则级命中统计
import pandas as pd
from core.matching_engine import match_medical_insurance
bank = pd.DataFrame({
    "交易日期": ["2026-01-01", "2026-01-02", "2026-01-03"],
    "摘要": ["医保回款1月", "水电费", "统筹基金拨付"],
    "对方客户名称": ["市医保中心", "供电局", "市医保中心"],
    "交易金额": [10000.0, 200.0, 5000.0],
})
summ = pd.DataFrame({"机构名称": ["市医保中心"], "合计": [15000.0]})
res = match_medical_insurance(bank, summ, patterns="医保|统筹")
rs = res["match_stats"]["rule_stats"]
assert rs["keyword_hits"]["医保"] == 2, rs
assert rs["keyword_hits"]["统筹"] == 1, rs
assert any(c["column"] == "摘要" and c["hits"] == 2 for c in rs["filter_columns"]), rs
assert rs["fallback_full_table"] is False
ok(f"匹配引擎规则级命中统计 keyword_hits={rs['keyword_hits']}")

# 9b) 异常分类规则先行：噪音词表命中即秒判"噪音费用"，不触碰 LLM（离线可测）
from core.matching_engine import _classify_exception_via_llm
assert _classify_exception_via_llm(
    "对账", {"摘要": "账户管理费", "amount": -200}, {}, 0.3) == "噪音费用"
assert _classify_exception_via_llm(
    "对账", {"desc": "银行手续费扣款"}, {}, 0.5) == "噪音费用"
ok("异常分类规则先行（账户管理费/手续费 → 噪音费用，零 LLM 调用）")

# 10) RAG 法规引用 1-hop 扩展（注入假索引，不依赖真实知识库）
import core.rag_engine as rag
rag._doc_chunks = [
    {"text": "第1312号准则关于函证程序的规定……", "source": "01_中注协审计准则体系/审计准则第1312号—函证.pdf",
     "category": "01_中注协审计准则体系"},
    {"text": "无关文档", "source": "05_行业专项政策/其他.pdf", "category": "05_行业专项政策"},
]
hits = [{"score": 0.9, "text": "……应当按照《审计准则第1312号》执行函证……",
         "source": "01_中注协审计准则体系/收入准则应用指南.pdf", "category": "01_中注协审计准则体系"}]
expanded = rag.expand_with_citations(hits)
assert len(expanded) == 2 and expanded[1]["via"] == "citation", expanded
assert "1312" in expanded[1]["source"]
ok("RAG 引用扩展：《审计准则第1312号》→ 命中函证准则原文块")

print(f"\n全部 {PASS} 项验证通过 ✅")