#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""routes.py 补丁 D1：算子自动补全去硬编码列名 + GroupBy/Sort 运行时 auto 解析"""
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "api" / "routes.py"
src = P.read_text(encoding="utf-8").replace("\r\n", "\n")


def rep(old: str, new: str, tag: str):
    global src
    assert src.count(old) == 1, f"[{tag}] 命中 {src.count(old)} 次而非 1 次"
    src = src.replace(old, new)
    print(f"  [PATCH] {tag}")


# ── 1. _ensure_essential_operators：硬编码列名 → auto 运行时解析 ──
rep('''        ops.append({\"id\": f\"op_{max_id}\", \"name\": \"GroupBy\", \"input_from\": last, \"params\": {\"by\": [\"对方客户名称\"], \"aggregations\": {\"交易金额\": \"sum\"}}, \"output_alias\": f\"df_grouped_{max_id}\"}); last = [f\"op_{max_id}\"]; existing.add(\"GroupBy\"); changed = True''',
    '''        ops.append({\"id\": f\"op_{max_id}\", \"name\": \"GroupBy\", \"input_from\": last, \"params\": {\"by\": [\"auto\"], \"aggregations\": {\"auto\": \"sum\"}}, \"output_alias\": f\"df_grouped_{max_id}\"}); last = [f\"op_{max_id}\"]; existing.add(\"GroupBy\"); changed = True''',
    "GroupBy 补全去硬编码")

rep('''        ops.append({\"id\": f\"op_{max_id}\", \"name\": \"Diff\", \"input_from\": last, \"params\": {\"col_a\": \"交易金额\", \"col_b\": \"业务金额\", \"tolerance_pct\": 1.0}, \"output_alias\": f\"df_diff_{max_id}\"}); last = [f\"op_{max_id}\"]; existing.add(\"Diff\"); changed = True''',
    '''        ops.append({\"id\": f\"op_{max_id}\", \"name\": \"Diff\", \"input_from\": last, \"params\": {\"col_a\": \"auto\", \"col_b\": \"auto\", \"tolerance_abs\": 0.01}, \"output_alias\": f\"df_diff_{max_id}\"}); last = [f\"op_{max_id}\"]; existing.add(\"Diff\"); changed = True''',
    "Diff 补全去硬编码+精确到分")

rep('''        max_id += 1; sc = \"交易金额\" if \"交易金额\" in str(ops) else \"金额\"''',
    '''        max_id += 1; sc = \"auto\"''',
    "Sort 补全去硬编码")

# ── 2. GroupBy：运行时 auto 解析（分组列 + 聚合列） ─────────────
rep('''            if by_cols:
                by_safe = [_sanitize_code_param(str(c), max_len=200) for c in by_cols]
                code_lines.extend([
                    f"if '{src_var}' in dir() and {src_var} is not None and not {src_var}.empty:",
                    f"    _by_valid = [c for c in {by_safe} if c in {src_var}.columns]",''',
    '''            if by_cols:
                by_safe = [_sanitize_code_param(str(c), max_len=200) for c in by_cols]
                agg_auto = isinstance(aggs, dict) and list(aggs.keys()) == ["auto"]
                aggs_render = "{c: 'sum' for c in _num_cols[:3]}" if agg_auto else repr(aggs)
                code_lines.extend([
                    f"if '{src_var}' in dir() and {src_var} is not None and not {src_var}.empty:",
                    f"    if {by_safe} == ['auto']:",
                    f"        _objs = [c for c in {src_var}.columns if {src_var}[c].dtype == object and not _is_meaningless_key(c, {src_var})]",
                    f"        print('[GroupBy] 自动选择分组列: ' + str(_objs[:1]))",
                    f"    _by_valid = _objs[:1] if {by_safe} == ['auto'] else [c for c in {by_safe} if c in {src_var}.columns]",
                    f"    _num_cols = [c for c in {src_var}.columns if pd.api.types.is_numeric_dtype({src_var}[c]) and not _is_meaningless_key(c, {src_var})]",''',
    "GroupBy auto 分组/聚合列")

rep('''                    f"        {op_alias} = {src_var}.groupby(_by_valid).agg({aggs}).reset_index()",''',
    '''                    f"        {op_alias} = {src_var}.groupby(_by_valid).agg({aggs_render}).reset_index()",''',
    "GroupBy agg 渲染 auto 化")

# ── 3. Sort：运行时 auto 解析 ──────────────────────────────────
rep('''                f"    _sort_valid = [c for c in {by_safe} if c in {src_var}.columns]",''',
    '''                f"    _nums_auto = [c for c in {src_var}.columns if pd.api.types.is_numeric_dtype({src_var}[c]) and not _is_meaningless_key(c, {src_var})]",
                f"    _sort_valid = (_nums_auto[:1] if {by_safe} == ['auto'] else [c for c in {by_safe} if c in {src_var}.columns])",
                f"    if {by_safe} == ['auto'] and _sort_valid:",
                f"        print('[Sort] 自动选择排序列: ' + str(_sort_valid))",''',
    "Sort auto 排序列")

P.write_text(src, encoding="utf-8", newline="\n")
import ast
ast.parse(src)
print("routes 补丁D1 完成，AST OK")
