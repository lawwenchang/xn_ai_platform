#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据嗅探与策略提案器（v2）
扫描上传文件（Excel/CSV/docx/doc/pdf/md/txt）→ 分析列结构/语义角色 → 推荐匹配策略

v2 变更：
- 表格以外的文档格式经 core.document_loader 统一加载（消除"只能处理 Excel"）；
- 列探测由 core.column_semantics 语义注册表驱动（消除硬编码列名关键词）；
- 新增"银行存款逐笔对账"策略提案（识别 序时账×银行流水 组合）。
"""

from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from core.column_semantics import (detect_column_roles, is_meaningless_key,
                                   suggest_join_keys)
from core.document_loader import TABLE_EXTS, load_document


def sniff_tables(file_paths: List[Path]) -> Dict[str, Any]:
    """扫描所有上传文件，返回数据特征画像（表格 + 文档统一处理）"""
    profiles = {}
    for fp in file_paths:
        try:
            profiles[fp.name] = _sniff_one(Path(fp))
        except Exception as e:
            profiles[fp.name] = {"error": str(e)}
    return profiles


def _sniff_one(fp: Path) -> dict:
    ext = fp.suffix.lower()
    if ext not in TABLE_EXTS:
        # 文档格式：返回文档画像（文本预览 + 内嵌表格结构）
        doc = load_document(fp)
        info = {
            "kind": doc.kind, "ext": ext,
            "chars": len(doc.text), "preview": doc.text[:300],
            "tables_count": len(doc.tables),
            "tables_columns": [[str(c) for c in t.columns] for t in doc.tables[:5]],
        }
        if doc.errors:
            info["errors"] = doc.errors
        if doc.tables:
            info.update(_table_profile(doc.tables[0]))
            info["from_document"] = True
        return info
    # 表格格式
    df = load_document(fp).tables[0]
    return _table_profile(df)


def _table_profile(df: pd.DataFrame) -> dict:
    cols = [str(c) for c in df.columns]
    roles = detect_column_roles(df)
    info: Dict[str, Any] = {
        "kind": "table",
        "rows": len(df), "cols": len(cols), "columns": cols,
        "dtypes": {c: str(df[c].dtype) for c in df.columns},
        "null_rate": {c: round(df[c].isna().mean() * 100, 1) for c in df.columns},
        "semantic_roles": roles,
        "meaningless_keys": [c for c in cols if is_meaningless_key(c)],
        "amount_cols": [roles[r] for r in ("amount", "debit", "credit", "balance")
                        if r in roles],
        "name_cols": [roles[r] for r in ("name", "counterpart") if r in roles],
        "date_cols": [roles["date"]] if "date" in roles else [],
    }
    return info


def _looks_journal(p: dict) -> bool:
    cols = " ".join(p.get("columns", []))
    return any(k in cols for k in ("凭证号", "凭证编号", "科目编码")) or \
        ("借方金额" in cols and "贷方金额" in cols)


def _looks_bank(p: dict) -> bool:
    cols = " ".join(p.get("columns", []))
    return any(k in cols for k in ("银行账号", "账号", "余额", "对方户名")) and \
        any(k in cols for k in ("收入", "支出", "支取", "贷方", "借方"))


def propose_strategy(profiles: Dict[str, Any], user_intent: str = "") -> Dict[str, Any]:
    """基于数据特征自动推荐匹配策略（语义驱动，不再写死业务词）"""
    has_medical = any(kw in user_intent for kw in ["医保", "医疗", "社保", "统筹"])
    has_amount = any(p.get("amount_cols") for p in profiles.values())
    has_name = any(p.get("name_cols") for p in profiles.values())
    table_count = len([p for p in profiles.values() if p.get("kind") == "table"])
    doc_count = len(profiles) - table_count

    strategies = []
    # 序时账 × 银行流水 → 逐笔对账（专业引擎）
    files = list(profiles.items())
    journal = next((n for n, p in files if _looks_journal(p)), None)
    bank = next((n for n, p in files if n != journal and _looks_bank(p)), None)
    if journal and bank:
        strategies.append({
            "name": "银行存款逐笔对账",
            "description": "方向镜像归一 → 账户过滤 → 勾稽 → L1~L4 逐笔勾对 → 未达四分类 → 余额调节表",
            "key_columns": f"{journal}（账）× {bank}（流水）",
            "confidence": "高", "engine": "bank_reconcile_engine",
        })
    if table_count >= 2 and has_name:
        if has_medical:
            strategies.append({
                "name": "医保回款匹配",
                "description": "按名称列匹配 + 关键词筛选（医保|统筹|社保）",
                "key_columns": "名称列 ↔ 名称列", "confidence": "高",
            })
        strategies.append({
            "name": "通用名称匹配",
            "description": "按名称列直接匹配，按金额汇总比对",
            "key_columns": "名称列 ↔ 名称列",
            "confidence": "中" if has_amount else "低",
        })
    if has_amount and table_count == 1:
        strategies.append({
            "name": "金额筛查",
            "description": "按金额阈值筛选大额/异常记录",
            "key_columns": "金额列", "confidence": "中",
        })
    if not strategies:
        strategies.append({
            "name": "全量比对",
            "description": "不筛选，全量 Merge",
            "key_columns": "自动检测", "confidence": "低",
        })

    return {
        "table_count": table_count, "document_count": doc_count,
        "features": {
            "has_medical_keywords": has_medical,
            "has_amount_columns": has_amount,
            "has_name_columns": has_name,
            "journal_file": journal, "bank_file": bank,
        },
        "proposals": strategies,
        "recommended": strategies[0]["name"] if strategies else "无",
    }
