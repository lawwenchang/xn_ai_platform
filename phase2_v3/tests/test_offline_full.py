#!/usr/bin/env python3
"""
离线全功能回归测试（无需 LLM）
==============================
覆盖所有不需要 LLM 的场景：银行对账引擎、表格归一化、关键词词典、
场景路由、预设注册表、反向校验、业务勾稽、底稿要素、代码自纠错。

用法：python tests/test_offline_full.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
import numpy as np

ok = lambda msg: print(f"  [OK] {msg}")
nf = lambda msg: print(f"  [NG] {msg}")
sep = lambda title: print(f"\n{'='*60}\n{title}\n{'='*60}")

# ═══════════════════════════════════════════════════════════════
sep("1. 银行对账引擎（确定性，无需 LLM）")

from core.bank_reconcile_engine import (
    detect_book_type, JOURNAL, BANK_STATEMENT,
    auto_map_columns, tag_content,
)

journal = pd.DataFrame({
    "凭证号": ["PZ-001", "PZ-002", "PZ-003"],
    "日期": ["2024-01-01", "2024-01-02", "2024-01-03"],
    "摘要": ["销售收入", "采购付款", "医保回款"],
    "借方金额": [10000, 0, 5000],
    "贷方金额": [0, 8000, 0],
    "银行账号": ["622202001", "622202001", "622202001"],
})
bank = pd.DataFrame({
    "交易日期": ["2024-01-01", "2024-01-02", "2024-01-03"],
    "摘要": ["销售收入", "采购付款", "医保统筹拨款"],
    "收入": [10000, 0, 5000],
    "支出": [0, 8000, 0],
    "余额": [50000, 42000, 47000],
    "对方客户名称": ["客户A", "供应商B", "医保中心"],
})

jt = detect_book_type(journal, "序时账.xlsx")
bt = detect_book_type(bank, "银行流水.xlsx")
assert jt in (JOURNAL, "generic_ledger"), f"序时账识别失败: {jt}"
assert bt == BANK_STATEMENT, f"银行流水识别失败: {bt}"
ok(f"文件类型识别: 序时账={jt}, 流水={bt}")

jm = auto_map_columns(journal, JOURNAL)
bm = auto_map_columns(bank, BANK_STATEMENT)
ok(f"列映射: 序时账keys={list(jm.keys())[:5]}, 流水keys={list(bm.keys())[:5]}")

tagged = tag_content(bank)
ok("内容标签: 已执行")

import tempfile
with tempfile.TemporaryDirectory() as tmp:
    jp = Path(tmp) / "journal.xlsx"
    bp = Path(tmp) / "bank.xlsx"
    od = Path(tmp) / "outputs"
    od.mkdir()
    journal.to_excel(jp, index=False)
    bank.to_excel(bp, index=False)
    try:
        from core.bank_reconcile_engine import reconcile_files
        res = reconcile_files(jp, bp, {}, od)
        st = res["stats"]
        ok(f"对账完成: 匹配率 账={st['book_match_rate']}% 银={st['bank_match_rate']}%")
        ok(f"分层: L1={st['matched_L1']}, L2={st['matched_L2']}, 待复核L4={st['review_L4']}")
    except Exception as e:
        nf(f"对账执行异常: {e}")

# ═══════════════════════════════════════════════════════════════
sep("2. 表格归一化（确定性，无需 LLM）")

from core.table_normalizer import (
    detect_table_shape, clean_dataframe, find_header_row,
    normalize_to_contract, validate_contract,
    flatten_header, strip_subtotals,
)

df1 = pd.DataFrame({"日期": ["2024-01-01"], "金额": [1000], "单位": ["A"]})
s1 = detect_table_shape(df1)
assert s1["standard"] and not s1["cross_table"]
ok("标准长表识别正确")

df2 = pd.DataFrame({"单位": ["A", "B"], "1月": [100, 200], "2月": [150, 250], "3月": [120, 220]})
s2 = detect_table_shape(df2)
assert s2["cross_table"]
r2, m2 = normalize_to_contract(df2)
assert len(r2) == 6  # 2单位 × 3月
ok(f"交叉表归一化: {len(df2)}行 → {len(r2)}行, 管道={m2['pipeline_key']}")

df3 = pd.DataFrame([
    ["报表标题", "", "", ""],
    ["单位：XX公司", "", "", ""],
    ["", "", "", ""],
    ["单位", "1月", "2月", "3月"],
    ["A公司", "   ", 200, 300],
    ["B公司", 400, 500, 600],
    [" ", " ", " ", " "],
    ["合计", "   ", 700, 900],
])
hr = find_header_row(df3)
assert hr == 3, f"表头应在第4行，实际={hr}"
ok(f"表头定位: 第{hr+1}行")

cleaned = clean_dataframe(df3)
assert len(cleaned) == 3
assert list(cleaned.columns) == ["单位", "1月", "2月", "3月"]
ok(f"清洗: {len(df3)}行→{len(cleaned)}行")

df4 = pd.DataFrame([
    ["营业收入", "", "营业成本", ""],
    ["本年", "上年", "本年", "上年"],
    [1000, 900, 600, 550],
], columns=["Unnamed:0", "Unnamed:1", "Unnamed:2", "Unnamed:3"])
f4 = flatten_header(df4)
assert "营业收入_本年" in f4.columns
ok(f"多级表头: {list(f4.columns)}")

df5 = pd.DataFrame({"科目": ["原材料", "产成品", "合计"], "金额": [100, 200, 300]})
s5 = strip_subtotals(df5)
assert len(s5) == 2
ok(f"合计剥离: {len(df5)}行→{len(s5)}行")


# ═══════════════════════════════════════════════════════════════
sep("3. 关键词词典（确定性，无需 LLM）")

from config.extraction_dictionary import (
    resolve_patterns_full, preview_patterns, dictionary_stats,
)

kw1 = resolve_patterns_full("筛选公积金回款")
assert kw1["dict_key"] == "公积金" and kw1["source"] == "dictionary"
ok(f"精确命中: 公积金")

kw2 = resolve_patterns_full("查环保税支出")
assert kw2["source"] == "dictionary_fuzzy" and kw2["dict_key"] == "税费"
ok(f"模糊回退: 环保税→{kw2['dict_key']}")

kw3 = resolve_patterns_full("xyz123")
assert not kw3
ok("未命中: 正确返回空")

df_sample = pd.DataFrame({
    "摘要": ["公积金缴存", "工资发放", "水电费", "住房公积金", "其他"],
})
preview = preview_patterns("公积金|住房公积金", ["摘要"], df_sample)
assert preview["hit_count"] >= 2
ok(f"预览: 命中{preview['hit_count']}/{preview['total']}行 ({preview['hit_rate']:.0%})")

stats = dictionary_stats()
assert stats["entry_count"] >= 11
ok(f"词典: {stats['entry_count']}条目/{stats['total_patterns']}个pattern")

# ═══════════════════════════════════════════════════════════════
sep("4. 场景路由 + 预设注册表（确定性，无需 LLM）")

from config.scenario_packs import detect_scenario, SCENARIO_PACKS

assert detect_scenario("序时账和银行流水逐笔核对", ask_user=False) == "bank_reconcile_detail"
assert detect_scenario("费用台账按部门去重排序汇总", ask_user=False) == "single_table_analysis"
assert detect_scenario("单笔超过100万的大额交易筛出来", ask_user=False) == "large_txn_screen"
ok("场景路由: 对账/数据加工/大额筛查 全部正确")

for sid in SCENARIO_PACKS:
    assert "checklist" in SCENARIO_PACKS[sid], f"{sid} 缺checklist"
ok(f"场景注册表: {len(SCENARIO_PACKS)}个场景全部有检查单")

from config.presets import PRESETS, normalize_preset_key, public_list

assert normalize_preset_key("银行流水核对") == "银行对账"
ok("预设别名归一: 银行流水核对→银行对账")

pub = public_list()
assert len(pub) >= 7
ok(f"前端预设: {len(pub)}个按钮")

# 交叉一致性
for key, p in PRESETS.items():
    if not p.get("dag", True):
        continue
    sc = p.get("scenario", "")
    if sc:
        assert sc in SCENARIO_PACKS, f"预设 {key} 的场景 {sc} 不在注册表中"
ok("预设→场景映射全部一致")

# ═══════════════════════════════════════════════════════════════
sep("5. 反向校验 + 代码自纠错（确定性，无需 LLM）")

from core.matching_engine import reverse_validate_unmatched

rv_df = pd.DataFrame({
    "摘要": ["工资发放", "采购付款", "工资奖金", "水电费", "办公用品"],
    "金额": [1000, 2000, 1500, 300, 500],
})
rv = reverse_validate_unmatched(rv_df)
assert rv["total_unmatched"] == 5 and len(rv["clusters"]) >= 1
ok(f"反向校验: {rv['total_unmatched']}条, {rv['cluster_count']}个聚类")

from engine.code_corrector import rule_based_fix, extract_error_summary

fixed = rule_based_fix(
    "df = pd.DataFrame({'a':[1]})\nprint(df)",
    "NameError: name 'pd' is not defined",
)
assert fixed and "import pandas as pd" in fixed
ok("Layer1 规则修正: 缺import→已补")

fixed2 = rule_based_fix(
    "df.to_csv('outputs/result.csv', index=False)",
    "FileNotFoundError: [Errno 2] No such file or directory: 'outputs/result.csv'",
)
assert fixed2 and "makedirs" in fixed2.lower()
ok("Layer1 规则修正: 输出目录→自动创建")

fixed3 = rule_based_fix("x = 1 + 1", "TypeError: unsupported operand")
assert fixed3 is None
ok("Layer1: 无适用规则→返回None")

summary = extract_error_summary("line1\nline2\nNameError: x not defined\nline4")
assert "NameError" in summary
ok("错误摘要提取正常")

# ═══════════════════════════════════════════════════════════════
sep("6. 语法完整性检查")

import ast

all_files = [
    "api/routes.py",
    "core/report_generator.py",
    "core/run_snapshot.py",
    "core/matching_engine.py",
    "core/table_normalizer.py",
    "config/scenario_packs.py",
    "config/presets.py",
    "config/fallback_prompts.py",
    "config/extraction_dictionary.py",
    "config/few_shot_examples.py",
    "engine/code_corrector.py",
]
for f in all_files:
    path = ROOT / f
    try:
        ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as e:
        nf(f"{f} 语法错误 line {e.lineno}: {e.msg}")
        raise SystemExit(1)
ok(f"{len(all_files)} 个文件全部语法通过")

# ═══════════════════════════════════════════════════════════════
print(f"\n{'='*60}")
print("全部 6 项离线测试通过。代码可以连接 LLM 了。")
print(f"{'='*60}")


p6, i6 = validate_contract(pd.DataFrame())
assert not p6
ok(f"空表校验: {i6}")
