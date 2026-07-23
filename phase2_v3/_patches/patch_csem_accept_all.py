#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""column_semantics.py 补丁：LLM 兜底接受全部有效角色（不只缺失的）"""
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "core" / "column_semantics.py"
src = P.read_text(encoding="utf-8").replace("\r\n", "\n")

OLD = '''                data = _json.loads(m.group(0))
                for role in missing:
                    v = data.get(role)
                    if isinstance(v, str) and v in cols:
                        roles[role] = v'''
NEW = '''                data = _json.loads(m.group(0))
                # 接受 LLM 返回的全部有效角色（不覆盖规则命中，不臆造列名）
                for role, v in data.items():
                    if role in ROLE_SYNONYMS and role not in roles \\
                            and isinstance(v, str) and v in cols:
                        roles[role] = v'''
assert src.count(OLD) == 1, f"命中 {src.count(OLD)} 次"
src = src.replace(OLD, NEW)
P.write_text(src, encoding="utf-8", newline="\n")
import ast
ast.parse(src)
print("LLM 兜底角色放宽完成，AST OK")
