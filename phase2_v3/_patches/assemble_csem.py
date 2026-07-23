#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from pathlib import Path
d = Path(__file__).resolve().parent.parent / "core"
cur = (d / "column_semantics.py").read_text(encoding="utf-8")
add = (d / "_csem_p3.py").read_text(encoding="utf-8")
if "detect_roles_with_llm" not in cur:
    src = cur.rstrip("\n") + "\n" + add
    (d / "column_semantics.py").write_text(src, encoding="utf-8")
(d / "_csem_p3.py").unlink()
import ast
ast.parse((d / "column_semantics.py").read_text(encoding="utf-8"))
print("column_semantics v2 assembled")

import pandas as pd
import sys
sys.path.insert(0, str(d.parent))
from core.column_semantics import detect_roles_with_llm
df = pd.DataFrame({"划拨日期": ["2026-01-01"], "兹付": [100], "对方单位": ["甲"]})
fake_llm = lambda p: '{"date": "划拨日期", "debit": "兹付", "name": "对方单位", "counterpart": "对方单位"}'
roles = detect_roles_with_llm(df, llm_callable=fake_llm)
assert roles.get("date") == "划拨日期" and roles.get("debit") == "兹付", roles
print("LLM 兜底映射 OK:", roles)
