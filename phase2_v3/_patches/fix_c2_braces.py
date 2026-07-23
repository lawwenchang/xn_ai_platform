#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修正 C2 补丁中 f-string 花括号转义错误"""
from pathlib import Path

p = Path(__file__).resolve().parent.parent / "api" / "routes.py"
s = p.read_text(encoding="utf-8")
bad1 = 'code_lines.append(f"# [跳过] 未实现算子: {{op_name}}")'
good1 = 'code_lines.append(f"# [跳过] 未实现算子: {op_name}")'
bad2 = 'print(f"[警告] 算子 {{op_name}} 未实现，已跳过——请确认这是预期行为")'
good2 = 'print(f"[警告] 算子 {op_name} 未实现，已跳过——请确认这是预期行为")'
assert bad1 in s and bad2 in s
s = s.replace(bad1, good1).replace(bad2, good2)
p.write_text(s, encoding="utf-8", newline="\n")
import ast
ast.parse(s)
print("braces fixed, AST OK")
