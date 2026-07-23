#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""routes.py 补丁 C3：删除 ffill 伪造层 → 数据缺失报告（缺失只标记，不编造）"""
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "api" / "routes.py"
src = P.read_text(encoding="utf-8").replace("\r\n", "\n")

OLD = '''    code_lines.extend([
        "",
        "# === 防御层：自动清洗脏数据 ===",
        "for _v in list(locals().values()):",
        "    if isinstance(_v, pd.DataFrame) and not _v.empty:",
        "        _v.dropna(how='all', inplace=True)",
        "        _v.dropna(axis=1, how='all', inplace=True)",
        "        _v.ffill(inplace=True)",
        "        for _c in _v.columns:",
        "            if _v[_c].dtype in ('float64', 'int64'):",
        "                _v[_c].fillna(0, inplace=True)",
        "            else:",
        "                _v[_c].fillna('', inplace=True)",
        "print('[防御] 所有 DataFrame 空值已清洗')",
    ])'''

NEW = '''    code_lines.extend([
        "",
        "# === 数据质量层：只清理全空行/列；缺失值一律标记报告，绝不编造 ===",
        "# （审计红线：前向填充会把上一行客户名填到缺失行、金额填 0，属于伪造审计证据）",
        "_missing_report = {}",
        "for _name, _v in list(locals().items()):",
        "    if isinstance(_v, pd.DataFrame) and not _v.empty:",
        "        _v.dropna(how='all', inplace=True)",
        "        _v.dropna(axis=1, how='all', inplace=True)",
        "        _miss = {c: int(_v[c].isna().sum()) for c in _v.columns if int(_v[c].isna().sum()) > 0}",
        "        if _miss:",
        "            _missing_report[_name] = _miss",
        "if _missing_report:",
        "    with open(os.path.join('outputs', 'data_missing_report.json'), 'w', encoding='utf-8') as _f:",
        "        json.dump(_missing_report, _f, ensure_ascii=False, indent=2)",
        "    print('[数据质量] ⚠ 检测到缺失值，已输出 data_missing_report.json（缺失不填充、不编造）: ' + str(_missing_report)[:300])",
        "else:",
        "    print('[数据质量] 无缺失值')",
    ])'''

assert src.count(OLD) == 1, f"防御层命中 {src.count(OLD)} 次"
src = src.replace(OLD, NEW)
P.write_text(src, encoding="utf-8", newline="\n")
import ast
ast.parse(src)
print("routes 补丁C3（ffill 伪造层 → 缺失报告）完成，AST OK")
