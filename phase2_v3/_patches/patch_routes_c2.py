#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""routes.py 补丁 C2：注入 _reconcile_lite 助手 + 实现 Reconcile/AuditAdjustment 算子"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
P = ROOT / "api" / "routes.py"
src = P.read_text(encoding="utf-8").replace("\r\n", "\n")

# 1) 助手注入到 _is_meaningless_key 之后
ANCHOR1 = '''        "        except Exception:",
        "            pass",
        "    return False",'''
assert src.count(ANCHOR1) == 1, f"助手锚点命中 {src.count(ANCHOR1)} 次"
HELPER = (ROOT / "_patches" / "_reconcile_helper.txt").read_text(encoding="utf-8").rstrip("\n")
src = src.replace(ANCHOR1, ANCHOR1 + "\n" + HELPER)
print("  [PATCH] _reconcile_lite 助手注入")

# 2) Reconcile/AuditAdjustment 算子：替换"未实现算子"分支
OLD2 = '''        else:
            code_lines.append(f"# [跳过] 未实现算子: {op_name}")'''
assert src.count(OLD2) == 1, f"未实现分支命中 {src.count(OLD2)} 次"
OPS = (ROOT / "_patches" / "_reconcile_ops.txt").read_text(encoding="utf-8").rstrip("\n")
NEW2 = OPS + '''

        else:
            code_lines.append(f"# [跳过] 未实现算子: {{op_name}}")
            print(f"[警告] 算子 {{op_name}} 未实现，已跳过——请确认这是预期行为")'''
src = src.replace(OLD2, NEW2)
print("  [PATCH] Reconcile/AuditAdjustment 算子实现")

P.write_text(src, encoding="utf-8", newline="\n")
import ast
ast.parse(src)
print("routes 补丁C2 完成，AST OK")
