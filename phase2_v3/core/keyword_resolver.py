#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
关键词三级供给 + 生命周期管理 (keyword_resolver.py)
===================================================

职责：
- resolve_patterns_full: 词典命中→直通；未命中→返回 propose 状态
- backtest_patterns:     回测/预览一体（纯 pandas，秒级）
- propose_via_search:    拓荒提案（搜索 → LLM 起草词条）
- approve_and_intake:    准入写回 extraction_dict.json，版本号 +0.1

这是执行链路调用的唯一关键词入口，取代散落在 routes.py 中的内联逻辑。
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from config.dictionary import (
    get_raw, save_raw, bump_version, get_version, get_dict,
)
from config.extraction_dictionary import (
    resolve_patterns_full as _resolve_from_dict,
    preview_patterns as _preview_patterns,
    add_to_dictionary as _add_to_dict_json,
)


# ═══════════════════════════════════════════════════════════════
# 一级：词典解析（hit → 免确认直通；propose → 需走提案+确认流程）
# ═══════════════════════════════════════════════════════════════

def resolve_patterns_full(intent: str) -> dict:
    """返回 {"status": "hit"|"propose", "patterns": str, "source": str,
             "category": str, "version": str}
    hit: 词典命中，直通；propose: 需走提案+确认流程。
    """
    kw = _resolve_from_dict(intent)
    if kw:
        return {
            "status": "hit",
            "patterns": kw["patterns"],
            "source": kw["source"],
            "category": kw["dict_key"],
            "version": get_version(),
            "columns": kw.get("columns", []),
            "exclude": kw.get("exclude", ""),
            "note": kw.get("note", ""),
        }
    return {
        "status": "propose",
        "patterns": "",
        "source": "proposal_pending",
        "category": "",
        "version": get_version(),
        "columns": ["摘要", "对方客户名称", "附言", "用途"],
        "exclude": "",
        "note": "词典未命中，需走 LLM 提案 + 用户确认流程",
    }


# ═══════════════════════════════════════════════════════════════
# 二级：回测/预览一体
# ═══════════════════════════════════════════════════════════════

def backtest_patterns(
    patterns: str,
    df: pd.DataFrame,
    columns: List[str],
    exclude: str = "",
    max_excluded: int = 10,
) -> dict:
    """回测/预览一体：命中数、占比、命中样例5条、各词命中分解、
    被排除行的高频摘要TOP10（漏召望远镜）。纯 pandas，秒级。

    Returns:
        {"hit_count": N, "total": N, "hit_rate": 0.XX,
         "hit_samples": [...], "keyword_breakdown": {...},
         "excluded_top": {"摘要前12字": count, ...}}
    """
    if df is None or df.empty or not patterns:
        return {"hit_count": 0, "total": 0, "hit_rate": 0,
                "hit_samples": [], "keyword_breakdown": {},
                "excluded_top": {}}

    total = len(df)
    actual_cols = [c for c in columns if c in df.columns]
    if not actual_cols:
        return {"hit_count": 0, "total": total, "hit_rate": 0,
                "hit_samples": [], "keyword_breakdown": {},
                "excluded_top": {}, "error": "指定列在数据中不存在"}

    try:
        pattern = re.compile(patterns)
    except re.error as e:
        return {"hit_count": 0, "total": total, "hit_rate": 0,
                "hit_samples": [], "keyword_breakdown": {},
                "excluded_top": {}, "error": f"正则编译失败: {e}"}

    # 整体命中
    hit_mask = pd.Series([False] * total, index=df.index)
    for col in actual_cols:
        hit_mask |= df[col].fillna("").astype(str).str.contains(
            pattern, regex=True, na=False)

    before_exclude = int(hit_mask.sum())
    if exclude:
        try:
            exc = re.compile(exclude)
            for col in actual_cols:
                em = df[col].fillna("").astype(str).str.contains(
                    exc, regex=True, na=False)
                hit_mask &= ~em
        except re.error:
            pass

    after = int(hit_mask.sum())
    excluded_by_rule = before_exclude - after
    hit_rate = round(after / max(total, 1), 4)

    # 命中样例 5 条
    hit_samples = []
    if after > 0:
        hit_df = df[hit_mask].head(5)
        desc_col = actual_cols[0]
        for _, row in hit_df.iterrows():
            hit_samples.append(str(row.get(desc_col, ""))[:80])

    # 各词命中分解（前50个关键词）
    keyword_breakdown = {}
    for kw in [k.strip() for k in str(patterns).split("|") if k.strip()][:50]:
        kw_mask = pd.Series([False] * total, index=df.index)
        for col in actual_cols:
            kw_mask |= df[col].fillna("").astype(str).str.contains(
                kw, na=False, case=False, regex=False)
        keyword_breakdown[kw] = int(kw_mask.sum())

    # 被排除行的高频摘要 TOP10（漏召望远镜）
    excluded_top: Dict[str, int] = {}
    if after < total:
        excluded_df = df[~hit_mask]
        desc_col = None
        for c in ["摘要", "附言", "用途", "说明", "备注"]:
            if c in excluded_df.columns:
                desc_col = c
                break
        if desc_col and len(excluded_df) > 0:
            desc_vals = excluded_df[desc_col].fillna("").astype(str).apply(
                lambda x: x.strip()[:12] if x.strip() else "(空)")
            excluded_top = desc_vals.value_counts().head(max_excluded).to_dict()
            excluded_top = {str(k): int(v) for k, v in excluded_top.items()}

    return {
        "hit_count": after,
        "total": total,
        "hit_rate": hit_rate,
        "before_exclude": before_exclude,
        "excluded_by_rule": excluded_by_rule,
        "hit_samples": hit_samples,
        "keyword_breakdown": keyword_breakdown,
        "excluded_top": excluded_top,
    }


# ═══════════════════════════════════════════════════════════════
# 三级：拓荒提案（搜索 → LLM 起草）
# ═══════════════════════════════════════════════════════════════

def propose_via_search(intent: str) -> dict:
    """拓荒提案：搜索（政策/业务词）→ LLM 结合搜索结果起草词条
    → 返回 {"patterns":..., "依据摘要":..., "sources":[url,...]}

    LLM 只做两件事：写 query、起草词条。产出必须经过下一步预览才生效。
    """
    # ① LLM 写 2-3 个搜索 query
    queries = _llm_write_queries(intent)
    if not queries:
        # LLM 不可用时的兜底：直接从意图中提取关键词
        return _fallback_propose(intent)

    # ② 复用平台知识层的搜索工具
    hits = _web_search(queries)

    # ③ LLM 结合搜索结果起草词条
    draft = _llm_draft_keywords(intent, hits)

    # ④ LLM 总结合依据
    basis = _llm_summarize_basis(hits) if hits else intent

    sources = [h.get("url", "") for h in hits[:5] if h.get("url")]

    return {
        "patterns": draft or "",
        "依据摘要": basis,
        "sources": sources,
        "search_queries": queries,
    }


def _llm_write_queries(intent: str) -> List[str]:
    """LLM 写 2-3 个搜索 query（一次性）。"""
    try:
        import requests
        vllm_url = os.environ.get(
            "VLLM_TUNNEL_URL", "http://localhost:18000/v1/chat/completions")
        prompt = (
            f"你是审计关键词搜索专家。根据用户意图写2-3个搜索查询词（用于在政策/业务网站搜索相关关键词），"
            f"每行一个，只返回查询词，不要解释。\n\n用户意图：{intent}\n\n查询词："
        )
        r = requests.post(
            vllm_url,
            json={"model": "qwen3-235b",
                  "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.3, "max_tokens": 80},
            headers={"Authorization": "Bearer EMPTY"},
            timeout=15,
        )
        if r.status_code == 200:
            text = r.json()["choices"][0]["message"]["content"].strip()
            return [q.strip() for q in text.split("\n") if q.strip()][:3]
    except Exception as e:
        print(f"[keyword_resolver] LLM 写查询词失败: {e}")
    return []


def _web_search(queries: List[str]) -> List[Dict[str, str]]:
    """复用平台知识层的搜索工具进行联网搜索。
    尝试调用现有的 search_trigger / web_search 基础设施。
    """
    hits: List[Dict[str, str]] = []
    for q in queries[:3]:
        try:
            # 尝试导入平台搜索模块
            from core.search_trigger import web_search as _ws
            results = _ws(q, max_results=3)
            if results:
                hits.extend(results)
        except ImportError:
            pass
        except Exception as e:
            print(f"[keyword_resolver] 搜索 '{q}' 失败: {e}")

    if not hits:
        # 平台搜索不可用时，返回空列表让 LLM 兜底
        print(f"[keyword_resolver] 搜索不可用，LLM 将从意图直接起草")

    return hits


def _llm_draft_keywords(intent: str, hits: List[Dict[str, str]]) -> str:
    """LLM 结合搜索结果起草筛选关键词。"""
    try:
        import requests
        vllm_url = os.environ.get(
            "VLLM_TUNNEL_URL", "http://localhost:18000/v1/chat/completions")

        hits_text = ""
        if hits:
            snippets = [h.get("snippet", h.get("title", ""))[:200] for h in hits[:5]]
            hits_text = "\n".join(f"- {s}" for s in snippets if s)

        prompt = (
            f"你是审计数据筛选专家。从用户需求中提取关键词（用|分隔），"
            f"只返回关键词字符串，不要解释，不要标点。\n\n"
        )
        if hits_text:
            prompt += f"参考搜索结果：\n{hits_text}\n\n"
        prompt += f"用户需求：{intent}\n\n关键词："

        r = requests.post(
            vllm_url,
            json={"model": "qwen3-235b",
                  "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.1, "max_tokens": 80},
            headers={"Authorization": "Bearer EMPTY"},
            timeout=15,
        )
        if r.status_code == 200:
            result = r.json()["choices"][0]["message"]["content"].strip()
            cleaned = re.sub(r'[^一-龥a-zA-Z|]', '', result)
            if cleaned:
                return cleaned
    except Exception as e:
        print(f"[keyword_resolver] LLM 起草词条失败: {e}")
    return ""


def _llm_summarize_basis(hits: List[Dict[str, str]]) -> str:
    """LLM 总结搜索依据摘要。"""
    try:
        snippets = [h.get("snippet", h.get("title", ""))[:150] for h in hits[:5]]
        combined = "；".join(s for s in snippets if s)
        if combined:
            return f"基于搜索结果: {combined[:300]}"
    except Exception:
        pass
    return "基于 LLM 从用户意图中提取"


def _fallback_propose(intent: str) -> dict:
    """LLM 不可用时的兜底：直接从意图中提取关键词提案。"""
    try:
        from core.matching_engine import _extract_patterns_via_llm
        patterns = _extract_patterns_via_llm(intent)
        if patterns:
            return {
                "patterns": patterns,
                "依据摘要": "LLM 从用户意图中直接提取（搜索不可用）",
                "sources": [],
                "search_queries": [],
            }
    except Exception:
        pass
    return {
        "patterns": "",
        "依据摘要": "无法生成关键词提案，请手动输入",
        "sources": [],
        "search_queries": [],
    }


# ═══════════════════════════════════════════════════════════════
# 四级：准入入库（写回 JSON，版本 +0.1）
# ═══════════════════════════════════════════════════════════════

def approve_and_intake(
    category: str,
    patterns: str,
    meta: dict,
    approved_by: str,
) -> str:
    """准入：写回 extraction_dict.json，版本号 +0.1，返回新版本号。
    记录批准人/日期/来源/依据——词条从此免确认。

    Args:
        category: 词典类目名（如 "医保"、"社保" 或新类目）
        patterns: 筛选关键词 pattern
        meta: {"依据摘要": "...", "sources": [...], ...}
        approved_by: 批准人标识

    Returns:
        新版本号字符串
    """
    # 使用 extraction_dictionary 的 add_to_dictionary 写回 JSON
    columns = meta.get("columns", ["摘要", "对方客户名称", "附言", "用途"])
    note = meta.get("依据摘要", "")
    exclude = meta.get("exclude", "")

    _add_to_dict_json(
        dict_key=category,
        patterns=patterns,
        columns=columns,
        note=note,
        exclude=exclude,
        confirmed_by=approved_by,
    )

    # 版本号 +0.1
    new_ver = bump_version()
    return new_ver


# ═══════════════════════════════════════════════════════════════
# 辅助：保存/恢复提案状态
# ═══════════════════════════════════════════════════════════════

_KEYWORD_PROPOSALS: Dict[str, dict] = {}


def save_proposal(run_id: str, proposal: dict) -> None:
    """暂存关键词提案（内存），供用户确认后恢复。"""
    _KEYWORD_PROPOSALS[run_id] = proposal


def get_proposal(run_id: str) -> Optional[dict]:
    """获取已暂存的提案。"""
    return _KEYWORD_PROPOSALS.get(run_id)


def clear_proposal(run_id: str) -> None:
    """清除提案缓存。"""
    _KEYWORD_PROPOSALS.pop(run_id, None)