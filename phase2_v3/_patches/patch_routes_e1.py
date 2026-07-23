#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""routes.py 补丁 E1：生成代码 UTF-8 输出 + input_from→depends_on 数据流修复"""
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "api" / "routes.py"
src = P.read_text(encoding="utf-8").replace("\r\n", "\n")


def rep(old: str, new: str, tag: str):
    global src
    assert src.count(old) == 1, f"[{tag}] 命中 {src.count(old)} 次而非 1 次"
    src = src.replace(old, new)
    print(f"  [PATCH] {tag}")


# ── 1. 生成代码头部：stdout/stderr UTF-8 重配置（GBK 控制台防崩） ──
rep('''    code_lines = [
        "import pandas as pd", "import json", "import os", "",''',
    '''    code_lines = [
        "import pandas as pd", "import json", "import os", "import sys",
        "try:",
        "    sys.stdout.reconfigure(encoding='utf-8', errors='replace')",
        "    sys.stderr.reconfigure(encoding='utf-8', errors='replace')",
        "except Exception:",
        "    pass", "",''',
    "生成代码 UTF-8 输出重配置")

# ── 2. 归一化：input_from → params.depends_on（数据流接线） ────
rep('''        n_op = {
            "id": op_dict.get("id") or f"op_{uuid.uuid4().hex[:6]}",
            "name": op_dict.get("name") or op_dict.get("type", "UnknownOperator"),
            "description": op_dict.get("description", ""),
            "params": op_dict.get("params") or {},
            "source_file": op_dict.get("source_file", ""),
            "output_alias": op_dict.get("output_alias", "df")
        }
        normalized_ops.append(n_op)''',
    '''        n_op = {
            "id": op_dict.get("id") or f"op_{uuid.uuid4().hex[:6]}",
            "name": op_dict.get("name") or op_dict.get("type", "UnknownOperator"),
            "description": op_dict.get("description", ""),
            "params": op_dict.get("params") or {},
            "source_file": op_dict.get("source_file", ""),
            "output_alias": op_dict.get("output_alias", "df")
        }
        # 关键修复：op 级 input_from 注入 params.depends_on——否则 Merge/Diff/
        # Reconcile 只能"取最后两个变量"猜输入，接线顺序一乱就张冠李戴
        _inp = op_dict.get("input_from") or op_dict.get("depends_on") or []
        if isinstance(_inp, str):
            _inp = [_inp]
        if _inp and isinstance(n_op["params"], dict) \\
                and "depends_on" not in n_op["params"]:
            n_op["params"]["depends_on"] = _inp
        normalized_ops.append(n_op)''',
    "input_from→depends_on 数据流接线")

P.write_text(src, encoding="utf-8", newline="\n")
import ast
ast.parse(src)
print("routes 补丁E1 完成，AST OK")
