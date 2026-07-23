#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""routes.py 补丁 C1：Diff 算子重写（按键对齐，容差生效）"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
P = ROOT / "api" / "routes.py"
src = P.read_text(encoding="utf-8").replace("\r\n", "\n")

OLD = '''        elif op_name == "Diff":
            dep_ids = params.get("depends_on", [])
            src_vars = []
            for dep_id in dep_ids:
                dep_op = op_map.get(dep_id)
                if dep_op:
                    src_vars.append(alias_vars.get(dep_op.get('output_alias'), f'df_{dep_id}'))
            if len(src_vars) < 2:
                all_vars = list(alias_vars.values())
                src_vars = all_vars[-2:] if len(all_vars) >= 2 else (src_vars + ['df', 'df'])
            left_var, right_var = (src_vars[0], src_vars[1]) if len(src_vars) >= 2 else ('df', 'df')
            code_lines.extend([
                f"if '{left_var}' in dir() and '{right_var}' in dir():",
                f"    {op_alias}_only_left = {left_var}[~{left_var}.index.isin({right_var}.index)]",
                f"    {op_alias}_only_right = {right_var}[~{right_var}.index.isin({left_var}.index)]",
                f"    {op_alias}_common = pd.merge({left_var}, {right_var}, how='inner', suffixes=('_LEFT', '_RIGHT'))",
            ])
            col_pairs = params.get("columns_pairs", [])
            if not col_pairs:
                col_a = params.get("col_a", "金额")
                col_b = params.get("col_b", "金额")
                col_pairs = [(col_a, col_b)]
            for ci, (c_a, c_b) in enumerate(col_pairs):
                code_lines.append(f"    if '{c_a}_LEFT' in {op_alias}_common.columns:")
                code_lines.append(f"        {op_alias}_common['差异_{c_a}'] = {op_alias}_common['{c_a}_LEFT'] - {op_alias}_common['{c_b}_RIGHT']")
            code_lines.append(f"    print(f'[Diff] 差异列: {{len(col_pairs)}}')")'''

NEW = (ROOT / "_patches" / "_diff_new.txt").read_text(encoding="utf-8").rstrip("\n")

assert src.count(OLD) == 1, f"旧 Diff 块命中 {src.count(OLD)} 次"
src = src.replace(OLD, NEW)
P.write_text(src, encoding="utf-8", newline="\n")
import ast
ast.parse(src)
print("routes 补丁C1（Diff 按键对齐+容差生效）完成，AST OK")
