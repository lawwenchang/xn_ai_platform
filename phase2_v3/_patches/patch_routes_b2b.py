#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""routes.py 补丁 B2b：Merge 算子 — 参数解析（保留 left_on/right_on 与日期窗口）"""
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "api" / "routes.py"
src = P.read_text(encoding="utf-8").replace("\r\n", "\n")


def rep(old: str, new: str, tag: str):
    global src
    assert src.count(old) == 1, f"[{tag}] 命中 {src.count(old)} 次而非 1 次"
    src = src.replace(old, new)
    print(f"  [PATCH] {tag}")


rep('''        elif op_name == "Merge":
            on_cols = params.get("on", [])
            how = params.get("how", "outer")
            if not on_cols:
                lo = params.get("left_on", [])
                ro = params.get("right_on", [])
                if lo or ro:
                    lo_list = lo if isinstance(lo, list) else [lo]
                    ro_list = ro if isinstance(ro, list) else [ro]
                    on_cols = list(set(lo_list + ro_list))''',
    '''        elif op_name == "Merge":
            on_cols = params.get("on", [])
            if isinstance(on_cols, str):
                on_cols = [on_cols]
            how = params.get("how", "outer")
            lo = params.get("left_on", []) or []
            ro = params.get("right_on", []) or []
            lo_list = lo if isinstance(lo, list) else [lo]
            ro_list = ro if isinstance(ro, list) else [ro]
            date_window = params.get("date_window_days")''',
    "Merge 参数解析保留 left_on/right_on/date_window")

P.write_text(src, encoding="utf-8", newline="\n")
import ast
ast.parse(src)
print("routes 补丁B2b 完成，AST OK")
