#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
单表筛选 + 人工补录引擎
当审计师仅上传一张表（无台账）时，按关键词筛选 + 汇总 → 人工勾选补录
"""
import json, os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


def filter_single_table(
    input_dir: Path,
    output_dir: Path,
    patterns: str = "",
    keywords: Optional[List[str]] = None,
) -> dict:
    """单表筛选：加载 → 关键词匹配 → 汇总 → 可选人工补录"""
    import pandas as pd

    excel_files = [f for f in input_dir.glob("*") if f.suffix.lower() in (".xlsx", ".xls", ".csv")]
    if not excel_files:
        return {"status": "error", "message": "无数据文件"}

    df = pd.read_excel(excel_files[0])
    output_dir.mkdir(parents=True, exist_ok=True)

    # 列识别
    cols = _identify_columns(df)
    desc_col = cols.get("desc_col")
    amount_col = cols.get("amount_col")

    # 关键词筛选
    if not patterns and keywords:
        patterns = "|".join(keywords)
    if not patterns:
        patterns = ""

    if patterns and desc_col and desc_col in df.columns:
        mask = df[desc_col].astype(str).str.contains(patterns, na=False, case=False)
        filtered = df[mask].copy()
    else:
        filtered = df.copy()

    # 提取金额
    amounts = []
    if amount_col and amount_col in filtered.columns:
        amounts = pd.to_numeric(filtered[amount_col], errors="coerce").fillna(0)

    total = amounts.sum() if len(amounts) > 0 else 0

    # 导出
    filtered.to_excel(output_dir / "单表筛选结果.xlsx", index=False, engine="openpyxl")

    result = {
        "status": "success",
        "total_rows": len(df),
        "filtered_rows": len(filtered),
        "total_amount": round(float(total), 2),
        "patterns_used": patterns,
        "output_file": "单表筛选结果.xlsx",
        "manual_supplement": [],  # 人工补录清单
    }

    (output_dir / "single_filter_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def apply_manual_supplement(
    run_dir: Path,
    add_rows: List[int] = None,
    remove_rows: List[int] = None,
    reasons: List[str] = None,
) -> dict:
    """人工补录：添加/移除记录"""
    result_file = run_dir / "outputs" / "single_filter_result.json"
    excel_file = run_dir / "outputs" / "单表筛选结果.xlsx"
    if not result_file.exists():
        return {"status": "error", "message": "无筛选结果"}

    result = json.loads(result_file.read_text(encoding="utf-8"))
    supplements = result.get("manual_supplement", [])

    if add_rows:
        for i, row_idx in enumerate(add_rows):
            supplements.append({
                "action": "add",
                "row_index": row_idx,
                "reason": reasons[i] if reasons and i < len(reasons) else "",
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            })

    if remove_rows:
        for i, row_idx in enumerate(remove_rows):
            supplements.append({
                "action": "remove",
                "row_index": row_idx,
                "reason": reasons[i] if reasons and i < len(reasons) else "",
            })

    result["manual_supplement"] = supplements
    result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "success", "supplements": len(supplements)}


def _identify_columns(df) -> dict:
    cols_lower = {str(c).lower(): str(c) for c in df.columns}
    mapping = {}
    mapping["desc_col"] = _find_col(cols_lower, ["摘要", "摘要信息", "描述", "说明", "用途", "备注"])
    mapping["amount_col"] = _find_col(cols_lower, ["交易金额", "金额", "发生额", "合计", "总计"])
    mapping["name_col"] = _find_col(cols_lower, ["客户名称", "对方客户名称", "对方户名", "对方", "机构名称", "单位"])
    return mapping


def _find_col(cols_lower: dict, candidates: list):
    for c in candidates:
        if c.lower() in cols_lower:
            return cols_lower[c.lower()]
    for c in candidates:
        for cl, orig in cols_lower.items():
            if c.lower() in cl:
                return orig
    return None
