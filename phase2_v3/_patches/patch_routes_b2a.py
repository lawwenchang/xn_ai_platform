#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""routes.py 补丁 B2a：生成代码前置注入 _is_meaningless_key 助手"""
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "api" / "routes.py"
src = P.read_text(encoding="utf-8").replace("\r\n", "\n")

HELPER_ANCHOR = '''        "    print('[Load] ⚠ 文档未提取到内容: ' + _os.path.basename(path))",
        "    return pd.DataFrame()",'''
assert src.count(HELPER_ANCHOR) == 1, f"锚点命中 {src.count(HELPER_ANCHOR)} 次"

HELPER = HELPER_ANCHOR + '''
        "",
        "def _is_meaningless_key(col, df=None):",
        "    # 序号/行号等无业务含义的键，禁止作为连接键（防止'按行号对账'）",
        "    n = str(col).replace(' ', '').lower()",
        "    if n in ('序号', '编号', '行号', 'no', 'no.', 'id', 'index', 'idx', '#', '顺序号', '排名', 'code'):",
        "        return True",
        "    if df is not None and col in df.columns:",
        "        try:",
        "            v = pd.to_numeric(df[col], errors='coerce').dropna()",
        "            if len(v) >= 3 and v.nunique() == len(v):",
        "                d = v.sort_values().diff().dropna().unique()",
        "                if len(d) == 1 and d[0] == 1:",
        "                    return True",
        "        except Exception:",
        "            pass",
        "    return False",'''

src = src.replace(HELPER_ANCHOR, HELPER)
P.write_text(src, encoding="utf-8", newline="\n")
import ast
ast.parse(src)
print("routes 补丁B2a（_is_meaningless_key 注入）完成，AST OK")
