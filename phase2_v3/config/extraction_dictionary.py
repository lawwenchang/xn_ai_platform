#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
提取式核对关键词词典 (extraction_dictionary.py)
=================================================
三级供给链：词典命中（免确认）→ LLM 提案（需确认）→ 准入流水线入库。

数据源：config/extraction_dict.json（通过 config.dictionary 加载器，mtime 热更新）。
新增词条走 add_to_dictionary → 写回 JSON 文件。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from config.dictionary import get_dict as _get_extraction_dict
from config.dictionary import get_raw as _get_raw
from config.dictionary import save_raw as _save_raw
from config.dictionary import get_version as _get_version
from config.dictionary import bump_version as _bump_version
from config.dictionary import reload as _reload_dict


# ═══════════════════════════════════════════════════════════════
# 透明代理：每次访问 EXTRACTION_DICT 都走 mtime 热更新
# 同时保持与旧版完全兼容的 dict 操作接口
# ═══════════════════════════════════════════════════════════════

class _DictProxy:
    """透明代理：EXTRACTION_DICT 的每次访问都通过 config.dictionary 获取最新数据。
    完全向后兼容：.items() / .values() / .keys() / [key] / in / .get() 均一致。"""

    @staticmethod
    def _get() -> Dict[str, Any]:
        return _get_extraction_dict()

    def items(self):
        return self._get().items()

    def values(self):
        return self._get().values()

    def keys(self):
        return self._get().keys()

    def __getitem__(self, key: str) -> Dict[str, Any]:
        return self._get()[key]

    def __contains__(self, key: str) -> bool:
        return key in self._get()

    def __len__(self) -> int:
        return len(self._get())

    def __iter__(self):
        return iter(self._get())

    def get(self, key: str, default=None):
        return self._get().get(key, default)


EXTRACTION_DICT: _DictProxy = _DictProxy()


# ═══════════════════════════════════════════════════════════════
# 三级供给链（函数逻辑不变，数据源已切换为 JSON）
# ═══════════════════════════════════════════════════════════════

def resolve_patterns(user_intent: str) -> Dict[str, Any]:
    """词典命中→直接用；未命中→返回空 dict（调用方走 LLM 提案路径）。
    
    Returns:
        {"patterns": "...", "columns": [...], "source": "dictionary", "dict_key": "..."}
        或 {}（未命中）
    """
    if not user_intent:
        return {}
    intent_lower = user_intent.lower()
    for key, entry in EXTRACTION_DICT.items():
        if key in intent_lower:
            return {
                "patterns": entry["patterns"],
                "columns": entry["columns"],
                "exclude": entry.get("exclude", ""),
                "source": "dictionary",
                "dict_key": key,
                "note": entry.get("note", ""),
            }
    return {}


def preview_patterns(patterns: str, columns: List[str], df_sample,
                      max_excluded: int = 5, exclude: str = "") -> Dict[str, Any]:
    """命中率预览：在一份数据样本上跑 pattern，返回命中统计和被排除的高频摘要。
    
    Args:
        patterns: 正则 pattern
        columns: 要搜索的列名
        df_sample: 数据样本 DataFrame
        max_excluded: 被排除摘要 top-N
    
    Returns:
        {"hit_count": N, "total": N, "hit_rate": 0.XX,
         "excluded_top": [("摘要前8字", count), ...]}
    """
    if df_sample is None or df_sample.empty or not patterns:
        return {"hit_count": 0, "total": 0, "hit_rate": 0, "excluded_top": []}

    total = len(df_sample)
    actual_cols = [c for c in columns if c in df_sample.columns]
    if not actual_cols:
        return {"hit_count": 0, "total": total, "hit_rate": 0,
                "excluded_top": [], "note": "指定筛选列在数据中不存在"}

    try:
        pattern = re.compile(patterns)
        import pandas as pd
        hit_mask = pd.Series([False] * total)
        for col in actual_cols:
            hit_mask |= df_sample[col].fillna("").astype(str).str.contains(
                pattern, regex=True, na=False)
        before_exclude = int(hit_mask.sum())
        if exclude:
            exc = re.compile(exclude)
            for col in actual_cols:
                em = df_sample[col].fillna("").astype(str).str.contains(exc, regex=True, na=False)
                hit_mask &= ~em
        after = int(hit_mask.sum())
        excluded_by_rule = before_exclude - after
        hit_rate = after / max(total, 1)

        excluded = df_sample[~hit_mask]
        desc_col = None
        for c in ["摘要", "附言", "用途", "说明", "备注"]:
            if c in excluded.columns:
                desc_col = c
                break
        excluded_top = []
        if desc_col and len(excluded) > 0:
            desc_vals = excluded[desc_col].fillna("").astype(str).apply(
                lambda x: x.strip()[:12] if x.strip() else "(空)")
            excluded_top = desc_vals.value_counts().head(max_excluded).to_dict()

        return {
            "hit_count": after, "total": total, "hit_rate": round(hit_rate, 3),
            "before_exclude": before_exclude, "excluded_by_rule": excluded_by_rule,
            "excluded_top": excluded_top,
        }
    except Exception as e:
        return {"hit_count": 0, "total": total, "hit_rate": 0,
                "excluded_top": [], "error": str(e)}


# ═══════════════════════════════════════════════════════════════
# 模糊回退：键未命中时，扫描 patterns 子串匹配
# ═══════════════════════════════════════════════════════════════

def resolve_patterns_fuzzy(user_intent: str, min_match_len: int = 2) -> Dict[str, Any]:
    """键未命中时的模糊回退：扫描所有条目的 patterns，看用户意图是否命中任一子串。
    
    例：用户意图含"环保税"，词典中"税费"条目含"环保税"子串 →
    此函数会命中并返回"税费"条目。
    
    Returns: 同 resolve_patterns，未命中返回空 dict
    """
    if not user_intent:
        return {}
    intent_lower = user_intent.lower()
    best_key, best_len = "", 0
    for key, entry in EXTRACTION_DICT.items():
        patterns = entry.get("patterns", "")
        for token in re.split(r"[|]", patterns):
            token_clean = token.strip().lower()
            if len(token_clean) >= min_match_len and token_clean in intent_lower:
                if len(token_clean) > best_len:
                    best_key, best_len = key, len(token_clean)
    if best_key:
        entry = EXTRACTION_DICT[best_key]
        return {
            "patterns": entry["patterns"],
            "columns": entry["columns"],
            "exclude": entry.get("exclude", ""),
            "source": "dictionary_fuzzy",
            "dict_key": best_key,
            "note": f"模糊匹配：意图中的子串命中了「{best_key}」词条的 pattern",
        }
    return {}


def resolve_patterns_full(user_intent: str) -> Dict[str, Any]:
    """完整三级供给入口：精确键匹配 → patterns 子串回退 → 空。
    这是执行链路调用的统一入口，取代直接调 resolve_patterns。
    """
    result = resolve_patterns(user_intent)
    if result:
        return result
    return resolve_patterns_fuzzy(user_intent)


# ═══════════════════════════════════════════════════════════════
# 准入流水线：确认后的关键词入库（写回 JSON 文件）
# ═══════════════════════════════════════════════════════════════

def add_to_dictionary(
    dict_key: str,
    patterns: str,
    columns: List[str],
    note: str = "",
    exclude: str = "",
    confirmed_by: str = "审计师",
) -> Dict[str, Any]:
    """LLM 提案经用户确认后，收编入词典（写回 JSON 文件）。
    
    - 同 key 已存在（categories 或 ledger_only_categories）：追加 patterns（去重合并）、合并 columns
    - 不存在：在 categories 下新建条目
    
    Returns: 更新后的条目
    """
    raw = _get_raw()

    # 确定目标容器：先查 categories，再查 ledger_only_categories
    target = None
    target_name = "categories"
    if dict_key in raw.get("categories", {}):
        target = raw["categories"]
    elif dict_key in raw.get("ledger_only_categories", {}):
        target = raw["ledger_only_categories"]
        target_name = "ledger_only_categories"
    else:
        # 新建 → 放入 categories
        if "categories" not in raw:
            raw["categories"] = {}
        target = raw["categories"]

    if dict_key in target:
        existing = target[dict_key]
        # 合并 patterns（去重）
        existing_parts = set(re.split(r"[|]", existing["patterns"]))
        new_parts = set(re.split(r"[|]", patterns))
        merged = existing_parts | new_parts
        existing["patterns"] = "|".join(sorted(merged, key=len, reverse=True))
        # 合并 columns（去重保持顺序）
        for c in columns:
            if c not in existing.get("columns", []):
                existing.setdefault("columns", []).append(c)
        if note:
            existing["note"] = (existing.get("note", "") + f"；{note}").strip("；")
        if exclude:
            ep = set(re.split(r"[|]", existing.get("exclude", "")))
            np = set(re.split(r"[|]", exclude))
            existing["exclude"] = "|".join(sorted(ep | np, key=len, reverse=True))

        # 追加 entries_meta 记录
        from datetime import date
        meta_key = patterns
        existing.setdefault("entries_meta", {})[meta_key] = {
            "来源": "用户确认提案",
            "依据": note or "用户审批入库",
            "url": "",
            "批准人": confirmed_by,
            "批准日期": date.today().isoformat(),
        }
        _save_raw(raw)
        return existing
    else:
        # 新建条目
        from datetime import date
        target[dict_key] = {
            "patterns": patterns,
            "columns": columns,
            "note": note,
            "exclude": exclude,
            "direction": "双向",
            "priority": 5,
            "entries_meta": {
                patterns: {
                    "来源": "用户确认提案",
                    "依据": note or "用户审批入库",
                    "url": "",
                    "批准人": confirmed_by,
                    "批准日期": date.today().isoformat(),
                }
            },
        }
        _save_raw(raw)
        return target[dict_key]


# ═══════════════════════════════════════════════════════════════
# 词典管理：列表、统计、导出
# ═══════════════════════════════════════════════════════════════

def list_dictionary() -> List[Dict[str, Any]]:
    """列出所有词典条目（供前端展示/管理界面）。"""
    return [
        {
            "key": key,
            "pattern_count": len(re.split(r"[|]", entry["patterns"])),
            "patterns_preview": entry["patterns"][:80],
            "columns": entry.get("columns", []),
            "exclude": entry.get("exclude", ""),
            "note": entry.get("note", ""),
        }
        for key, entry in EXTRACTION_DICT.items()
    ]


def dictionary_stats() -> Dict[str, Any]:
    """词典统计信息。"""
    total_patterns = sum(
        len(re.split(r"[|]", entry["patterns"]))
        for entry in EXTRACTION_DICT.values()
    )
    return {
        "entry_count": len(EXTRACTION_DICT),
        "total_patterns": total_patterns,
        "entries": list(EXTRACTION_DICT.keys()),
        "version": _get_version(),
    }
