#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""routes.py 补丁 B1：生成代码前置注入 _load_any_document + Load 算子修复"""
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "api" / "routes.py"
src = P.read_text(encoding="utf-8").replace("\r\n", "\n")


def rep(old: str, new: str, tag: str):
    global src
    assert src.count(old) == 1, f"[{tag}] 命中 {src.count(old)} 次而非 1 次"
    src = src.replace(old, new)
    print(f"  [PATCH] {tag}")


# ── 1. 生成代码头部注入 _load_any_document 助手 ────────────────
HELPER = '''        "_used_files = []",
        "",
        "def _load_any_document(path):",
        "    ''' + "'''" + '''文档 → DataFrame：docx/pdf 表格 → 纯文本段落表（库缺失时清晰告警）''' + "'''" + ''',
        "    import os as _os",
        "    ext = _os.path.splitext(path)[1].lower()",
        "    tables, text = [], ''",
        "    try:",
        "        if ext == '.docx':",
        "            from docx import Document as _D",
        "            _d = _D(path)",
        "            text = '\\\\n'.join(p.text for p in _d.paragraphs if p.text.strip())",
        "            for _t in _d.tables:",
        "                _rows = [[c.text.strip() for c in r.cells] for r in _t.rows]",
        "                if len(_rows) > 1:",
        "                    tables.append(pd.DataFrame(_rows[1:], columns=[h or '列%d' % (i+1) for i, h in enumerate(_rows[0])]))",
        "        elif ext == '.pdf':",
        "            try:",
        "                import pdfplumber",
        "                with pdfplumber.open(path) as _pdf:",
        "                    for _pg in _pdf.pages:",
        "                        text += (_pg.extract_text() or '') + '\\\\n'",
        "                        for _raw in (_pg.extract_tables() or []):",
        "                            _rows = [[('' if c is None else str(c).strip()) for c in r] for r in _raw if r]",
        "                            _rows = [r for r in _rows if any(r)]",
        "                            if len(_rows) > 1:",
        "                                tables.append(pd.DataFrame(_rows[1:], columns=[h or '列%d' % (i+1) for i, h in enumerate(_rows[0])]))",
        "            except Exception as _e:",
        "                print('[Load] PDF 解析告警: ' + str(_e))",
        "        elif ext in ('.md', '.txt'):",
        "            text = open(path, 'rb').read().decode('utf-8', errors='replace')",
        "    except Exception as _e:",
        "        print('[Load] 文档解析告警: ' + str(_e))",
        "    if tables:",
        "        print('[Load] 文档 ' + _os.path.basename(path) + ' → ' + str(len(tables)) + ' 个表格，取第 1 个')",
        "        return tables[0]",
        "    if text.strip():",
        "        print('[Load] 文档 ' + _os.path.basename(path) + ' 无表格，转为段落表')",
        "        return pd.DataFrame({'段落': [l for l in text.splitlines() if l.strip()]})",
        "    print('[Load] ⚠ 文档未提取到内容: ' + _os.path.basename(path))",
        "    return pd.DataFrame()",'''

rep('''        "_used_files = []",''',
    HELPER,
    "生成代码头部注入 _load_any_document")

# ── 2. Load 算子：优先按名精确匹配 + 多格式 + 文档分支 ─────────
rep('''            code_lines.extend([
                f"# 智能文件匹配",
                f"_dag_file = '{source}'",
                f"_all_inputs = os.listdir(_inputs_dir) if os.path.exists(_inputs_dir) else []",
                f"_available = [f for f in _all_inputs if f not in _used_files and f.endswith(('.xlsx','.xls','.csv'))]",
                f"if _available:",
                f"    _pick = _available[0]",
                f"    source_file = os.path.join(_inputs_dir, _pick)",
                f"    _used_files.append(_pick)",
                f"    print('[Load] 自动分配: ' + _pick)",
                f"else:",
                f"    source_file = os.path.join(_inputs_dir, _dag_file) if os.path.exists(os.path.join(_inputs_dir, _dag_file)) else 'data/readonly/' + _dag_file",
                f"if not os.path.exists(source_file):",
                f"    print('[Load] 跳过: 文件不存在 ' + source_file)",
                f"    {var_name} = pd.DataFrame()",
                f"else:",''',
    '''            code_lines.extend([
                f"# 智能文件匹配（优先按 DAG 指定的真实文件名精确匹配，禁止乱序分配）",
                f"_dag_file = '{source}'",
                f"_all_inputs = os.listdir(_inputs_dir) if os.path.exists(_inputs_dir) else []",
                f"_LOAD_EXTS = ('.xlsx','.xls','.csv','.docx','.doc','.pdf','.md','.txt')",
                f"if _dag_file in _all_inputs and _dag_file not in _used_files:",
                f"    _pick = _dag_file",
                f"    print('[Load] 按名匹配: ' + _pick)",
                f"else:",
                f"    _avail = [f for f in _all_inputs if f not in _used_files and f.lower().endswith(_LOAD_EXTS)]",
                f"    _pick = _avail[0] if _avail else _dag_file",
                f"    if _avail and _pick != _dag_file:",
                f"        print('[Load] ⚠ 指定文件 {{}} 不存在，回退到 {{}}'.format(_dag_file, _pick))",
                f"source_file = os.path.join(_inputs_dir, _pick) if os.path.exists(os.path.join(_inputs_dir, _pick)) else 'data/readonly/' + _dag_file",
                f"_used_files.append(_pick)",
                f"if not os.path.exists(source_file):",
                f"    print('[Load] 跳过: 文件不存在 ' + source_file)",
                f"    {var_name} = pd.DataFrame()",
                f"elif source_file.lower().endswith(('.docx','.doc','.pdf','.md','.txt')):",
                f"    {var_name} = _load_any_document(source_file)",
                f"else:",''',
    "Load 按名匹配+多格式+文档分支")

P.write_text(src, encoding="utf-8", newline="\n")
import ast
ast.parse(src)
print("routes 补丁B1 完成，AST OK")
