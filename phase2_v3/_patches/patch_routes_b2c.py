#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""routes.py 补丁 B2c：Merge 生成块替换（片段文件拼接）"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
P = ROOT / "api" / "routes.py"
src = P.read_text(encoding="utf-8").replace("\r\n", "\n")

OLD = '''            # 运行时自动检测：只保留两边都存在的列
            code_lines.extend([
                f"if '{left_var}' in dir() and '{right_var}' in dir() and not {left_var}.empty and not {right_var}.empty:",
                f"    _merge_candidates = {on_cols}",
                f"    _left_cols = set({left_var}.columns)",
                f"    _right_cols = set({right_var}.columns)",
                f"    _common = [c for c in _merge_candidates if c in _left_cols and c in _right_cols]",
                f"    _missing = [c for c in _merge_candidates if c not in _left_cols or c not in _right_cols]",
                f"    if _missing:",
                f"        print('[Merge] 警告：以下列不存在于数据中，已自动跳过: ' + str(_missing))",
                f"    if not _common:",
                f"        _common = list(set(_left_cols) & set(_right_cols))",
                f"        print('[Merge] 回退到全量公共列: ' + str(_common))",
                f"    if _common:",
                f"        {op_alias} = pd.merge({left_var}, {right_var}, on=_common, how='{how}')",
                f"        print('[Merge] on=' + str(_common) + ', how={how}, rows=' + str(len({op_alias})))",
                f"    else:",
                f"        print('[Merge] 错误：没有公共列可合并！左表列: ' + str(list(_left_cols)) + ', 右表列: ' + str(list(_right_cols)))",
                f"        {op_alias} = pd.DataFrame()",
                f"else:",
                f"    {op_alias} = pd.DataFrame()",
            ])'''

NEW = (ROOT / "_patches" / "_merge_new.txt").read_text(encoding="utf-8").rstrip("\n")

assert src.count(OLD) == 1, f"旧 Merge 块命中 {src.count(OLD)} 次"
src = src.replace(OLD, NEW)
P.write_text(src, encoding="utf-8", newline="\n")
import ast
ast.parse(src)
print("routes 补丁B2c（Merge 真 left_on/right_on + 日期窗口 + 无意义键排除）完成，AST OK")
