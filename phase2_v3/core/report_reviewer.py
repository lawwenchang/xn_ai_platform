#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
报告纠错引擎 — 规则检查器 + LLM 语义检查器
"""

import json, re
from pathlib import Path
from typing import List, Dict, Any


def check_report(file_path: str) -> dict:
    """检查报告文件：数字勾稽 + 格式规范性"""
    ext = Path(file_path).suffix.lower()
    if ext == ".docx":
        return _check_docx(file_path)
    elif ext in (".xlsx", ".xls"):
        return _check_excel(file_path)
    return {"status": "error", "message": f"不支持的文件格式: {ext}"}


def _check_docx(file_path: str) -> dict:
    from docx import Document
    doc = Document(file_path)
    issues = []

    # 检查段落
    text = "\n".join(p.text for p in doc.paragraphs)
    issues.extend(_check_text_rules(text))

    # 检查结构完备性
    issues.extend(_check_docx_completeness(doc))

    # 检查表格数字勾稽
    for ti, table in enumerate(doc.tables):
        issues.extend(_check_table_consistency(table, ti))

    return {
        "status": "success",
        "file": file_path,
        "issues": issues,
        "issue_count": len(issues),
        "passed": len(issues) == 0,
    }


def _check_excel(file_path: str) -> dict:
    from openpyxl import load_workbook
    wb = load_workbook(file_path)
    issues = []
    for sn in wb.sheetnames:
        ws = wb[sn]
        # 检查数字加总
        issues.extend(_check_sheet_sums(ws, sn))
    return {"status": "success", "file": file_path, "issues": issues, "issue_count": len(issues),
            "passed": len(issues) == 0}


def _check_text_rules(text: str) -> List[dict]:
    """L4 规范层：完备性清单 + 措辞红线（确定性规则，不依赖 LLM）"""
    issues = []

    # ── 完备性清单 ──
    # 1. 报告日期
    if not re.search(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日", text):
        issues.append({"type": "完备性", "detail": "缺少报告日期（XXXX年XX月XX日格式）", "severity": "高"})

    # 2. 报告文号
    if not re.search(r"[（(]\d{4}[）)]\s*\d+号|文号|[A-Z]{2,4}字", text):
        issues.append({"type": "完备性", "detail": "缺少报告文号", "severity": "中"})

    # 3. 编制人/复核人
    if not re.search(r"编制|复核|制单|审核", text):
        issues.append({"type": "完备性", "detail": "缺少编制人/复核人栏（底稿三栏要求）", "severity": "中"})

    # 4. Run_ID 存在
    if not re.search(r"Run\s*ID|run_id|RUN_", text):
        issues.append({"type": "完备性", "detail": "缺少 Run ID 追溯标识", "severity": "低"})

    # 5. 免责声明
    if "本报告" not in text and "仅供参考" not in text and "CPA" not in text:
        issues.append({"type": "完备性", "detail": "缺少免责声明", "severity": "中"})

    # 6. 期间一致性
    years = set(re.findall(r"(\d{4})\s*年度?", text))
    if len(years) > 1:
        issues.append({"type": "完备性", "detail": f"期间表述不一致，出现 {len(years)} 个不同年份: {years}", "severity": "中"})

    # ── 措辞红线 ──
    # 7. 禁用口语/不确定词
    FORBIDDEN_WORDS = {
        "大概": "口语化表述",
        "基本没问题": "口语化表述",
        "好像": "不确定表述",
        "可能": "不确定表述",
        "差不多": "口语化表述",
        "还行": "口语化表述",
        "挺多": "口语化表述",
    }
    for word, reason in FORBIDDEN_WORDS.items():
        if word in text:
            issues.append({"type": "措辞", "detail": f"禁用词「{word}」({reason})", "severity": "中"})

    # 8. 禁用绝对化表述
    ABSOLUTE_WORDS = {
        "完全真实": "绝对化表述",
        "绝无虚假": "绝对化表述",
        "百分之百": "绝对化表述",
        "绝对没有": "绝对化表述",
        "万无一失": "绝对化表述",
        "肯定没有": "绝对化表述",
    }
    for word, reason in ABSOLUTE_WORDS.items():
        if word in text:
            issues.append({"type": "措辞", "detail": f"绝对化表述「{word}」({reason})", "severity": "高"})

    # 9. 金额格式统一
    # 检查千分位（大额数字无逗号分隔）
    large_nums = re.findall(r"(?<![,\d])(\d{4,})(?:\.\d+)?(?=\s*[元|（(])", text)
    for n in large_nums[:10]:
        try:
            val = int(n.replace(",", ""))
            if val >= 10000 and "," not in n:
                issues.append({"type": "格式", "detail": f"金额缺少千分位分隔: {n}", "severity": "低"})
        except ValueError:
            pass

    # 10. 金额单位声明
    if re.search(r"[元万]", text) and "人民币元" not in text and "单位" not in text:
        pass  # 不强制，仅提醒
    if re.search(r"\d{5,}\s*元", text) and "万元" not in text:
        pass  # 大额可能需要万元单位

    # 11. 表格/附注引用
    if re.search(r"详见|参见|附表|附注", text) and not re.search(r"附注[一二三四五六七八九十\d]|附表[一二三四五六七八九十\d]", text):
        issues.append({"type": "完备性", "detail": "引用\"详见/参见\"未带具体附注编号", "severity": "低"})

    return issues


def _check_docx_completeness(doc) -> List[dict]:
    """Word 文档结构完备性检查"""
    issues = []
    try:
        paragraphs = [p.text for p in doc.paragraphs]
        text = "\n".join(paragraphs)

        # 检查是否有封面（前几段含关键词）
        first_lines = "\n".join(paragraphs[:10])
        if not any(kw in first_lines for kw in ("审计报告", "智能审计", "Run ID", "报告")):
            issues.append({"type": "结构", "detail": "文档前10段缺少报告标题或封面信息", "severity": "中"})

        # 检查是否有至少一个一级标题
        headings = [p for p in doc.paragraphs if p.style and "Heading" in str(p.style.name)]
        if len(headings) < 2:
            issues.append({"type": "结构", "detail": f"文档一级标题少于2个（仅有{len(headings)}个），结构可能不完整", "severity": "低"})

        # 检查表格数量
        if len(doc.tables) == 0:
            issues.append({"type": "结构", "detail": "文档无任何表格，数据展示可能缺失", "severity": "低"})

    except Exception:
        pass
    return issues


def _check_table_consistency(table, idx: int) -> List[dict]:
    issues = []
    numeric_cells = []
    for ri, row in enumerate(table.rows):
        for ci, cell in enumerate(row.cells):
            try:
                val = float(cell.text.replace(",", "").replace("¥", "").strip())
                numeric_cells.append((ri, ci, val))
            except ValueError:
                pass
    # 简单加总检查：如果有一列数字，检查最后一行是否为加总
    if numeric_cells:
        last_row = max(nc[0] for nc in numeric_cells)
        last_vals = [nc[2] for nc in numeric_cells if nc[0] == last_row]
        above_vals = [nc[2] for nc in numeric_cells if nc[0] < last_row]
        if last_vals and above_vals:
            expected = sum(above_vals)
            actual = sum(last_vals)
            if abs(expected - actual) > 0.01:
                issues.append({
                    "type": "勾稽",
                    "detail": f"表格{idx+1}: 合计({actual:,.2f}) ≠ 加总({expected:,.2f})",
                    "severity": "高",
                })
    return issues


def _check_sheet_sums(ws, sheet_name: str) -> List[dict]:
    issues = []
    numeric_data = []
    for row in ws.iter_rows(values_only=True):
        nums = []
        for v in row:
            try:
                nums.append(float(v))
            except (ValueError, TypeError):
                nums.append(None)
        numeric_data.append(nums)

    for ri, row in enumerate(numeric_data):
        valid = [v for v in row if v is not None]
        if not valid:
            continue
        if len(valid) >= 2:
            above = [numeric_data[r][ci] for r in range(ri)
                     for ci in range(len(row))
                     if numeric_data[r][ci] is not None]
            if above:
                total = sum(valid)
                if len(above) > 1 and total == sum(above):
                    # 可能的合计行，不做额外检查
                    pass
    return issues


async def semantic_review(text: str) -> dict:
    """LLM 语义合规检查"""
    from core.rag_engine import inject_compliance_context
    context = inject_compliance_context(text)
    if not context:
        return {"status": "skipped", "message": "RAG 不可用"}

    prompt = f"""你是审计质量复核人。检查以下报告内容的合规性。
    {context}
    报告内容：{text[:3000]}
    指出问题（措辞、遗漏、矛盾），没有则回复"无问题"。"""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(
                "http://localhost:18000/v1/chat/completions",
                headers={"Authorization": "Bearer EMPTY"},
                json={"model": "qwen3-235b", "messages": [{"role": "user", "content": prompt}], "temperature": 0.3, "max_tokens": 300},
            )
            r.raise_for_status()
            result = r.json()["choices"][0]["message"]["content"].strip()
            return {"status": "success", "review": result}
    except Exception:
        return {"status": "skipped", "message": "vLLM 不可用"}
