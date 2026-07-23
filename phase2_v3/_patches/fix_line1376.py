#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修复 routes.py 第1376行：'''文档字符串 → # 注释"""
from pathlib import Path

p = Path(__file__).resolve().parent.parent / "api" / "routes.py"
s = p.read_text(encoding="utf-8")
bad = "    '''文档 → DataFrame：docx/pdf 表格 → 纯文本段落表（库缺失时清晰告警）''',\n"
good = "    # 文档 → DataFrame：docx/pdf 表格 → 纯文本段落表（库缺失时清晰告警）\",\n"
assert bad in s, "目标行未找到"
s = s.replace(bad, good)
p.write_text(s, encoding="utf-8", newline="\n")
import ast
ast.parse(s)
print("fixed, AST OK")
