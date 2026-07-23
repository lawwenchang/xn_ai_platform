#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""few_shot_examples.py 补丁：利息移出噪音、逐笔精确到分、新增序时账×流水对账示例"""
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "config" / "few_shot_examples.py"
src = P.read_text(encoding="utf-8").replace("\r\n", "\n")


def rep(old: str, new: str, tag: str):
    global src
    assert src.count(old) == 1, f"[{tag}] 命中 {src.count(old)} 次而非 1 次"
    src = src.replace(old, new)
    print(f"  [PATCH] {tag}")


# ── 1. 示例1/2：利息移出噪音词（利息单独成类，见12号文） ──────
rep('''"noise_patterns": ["手续费", "短信费", "年费", "利息", "账户管理费", "冲正", "测试"]}},''',
    '''"noise_patterns": ["手续费", "短信费", "年费", "账户管理费", "测试"]}},''',
    "示例1 噪音词去利息/冲正")

rep('''"noise_patterns": ["手续费", "短信费", "年费", "利息", "账户管理费"]}},''',
    '''"noise_patterns": ["手续费", "短信费", "年费", "账户管理费"]}},''',
    "示例2 噪音词去利息")

# ── 2. 示例2：逐笔核对精确到分 ─────────────────────────────────
rep('''"col_a": "交易金额", "col_b": "业务金额", "tolerance_pct": 1.0, "output_mode": "all"''',
    '''"col_a": "交易金额", "col_b": "业务金额", "tolerance_abs": 0.01, "output_mode": "all"''',
    "示例2 逐笔容差改精确到分")

rep('''{"match_keys": ["客户名称", "交易金额"], "tolerance_pct": 1.0}''',
    '''{"match_keys": ["客户名称", "交易金额"], "tolerance_abs": 0.01}''',
    "示例2 context 容差改精确到分")

# ── 3. 新增示例11：序时账×银行流水逐笔对账 ─────────────────────
rep('''# Helper Functions

def build_few_shot_section(max_examples: int = 7) -> str:''',
    '''# Example 11: Journal vs Bank Statement Item-by-Item Reconciliation
_add("序时账与银行流水逐笔对账",
    "把序时账（农行5927账户）和银行流水逐笔核对，看两个文件的账款是否相符。",
    "文件1: 序时账.xlsx (列: 序号, 月, 日期, 凭证号, 摘要, 借方金额, 贷方金额, 银行账号)\\n文件2: 银行流水.xlsx (列: 序号, 银行账号, 日期, 摘要, 借方（支取）, 贷方（收入）)",
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

def build_few_shot_section(max_examples: int = 7) -> str:''',
    "新增序时账×流水对账示例")

# ── 4. 关键词路由补充 ─────────────────────────────────────────
rep('''        "筛选": [0, 2], "匹配": [0, 1, 3], "核对": [1, 3], "对账": [1],
        "流水": [1, 2], "台账": [1],''',
    '''        "筛选": [0, 2], "匹配": [0, 1, 3], "核对": [1, 3, 10], "对账": [1, 10],
        "流水": [1, 10, 2], "台账": [1], "序时账": [10], "逐笔": [10], "日记账": [10],''',
    "关键词路由补对账示例")

P.write_text(src, encoding="utf-8", newline="\n")
import ast
ast.parse(src)
print("few_shot 补丁完成，AST OK")
