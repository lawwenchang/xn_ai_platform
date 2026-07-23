#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智能搜索触发器 - 检测用户意图中的法规/政策关键词，自动启动搜索引擎
"""
from __future__ import annotations
import hashlib, json, os, re, time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "search_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_TTL = 3600 * 24

SEARCH_TRIGGERS = ["最新规定","新规","最新政策","修订","新发布","2025年","2026年","近日","刚刚","最新","会计准则变更",
                   "审计","函证","穿行测试","实质性程序","底稿","准则","审计程序","抽样","重要性水平",
                   "法规","条例","办法","指引","通知"]

@dataclass
class SearchNeed:
    should_search: bool = False
    query: str = ""
    reason: str = ""

@dataclass
class SearchResult:
    title: str = ""
    url: str = ""
    snippet: str = ""
    source: str = ""

def detect_search_need(user_intent: str) -> SearchNeed:
    matched = [kw for kw in SEARCH_TRIGGERS if kw in user_intent]
    if matched:
        query = user_intent.strip()[:100]
        if "医保" in query and "回款" in query:
            query = f"{query.strip()} 政策法规 回款比例"
        return SearchNeed(should_search=True, query=query, reason=f"触发关键词: {', '.join(matched[:3])}")
    policy_kw = ["法律","法规","政策","合规","合法","规定","条例","办法"]
    if any(kw in user_intent for kw in policy_kw):
        return SearchNeed(should_search=True, query=user_intent.strip()[:100], reason="含法规关键词")
    return SearchNeed()

async def execute_search(query: str, max_results: int = 5) -> List[SearchResult]:
    cache_key = hashlib.md5(query.encode()).hexdigest()
    cache_file = CACHE_DIR / f"{cache_key}.json"
    if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < CACHE_TTL:
        try:
            return [SearchResult(**r) for r in json.loads(cache_file.read_text(encoding="utf-8"))]
        except: pass
    results = await _search_builtin(query, max_results)
    if results:
        cache_file.write_text(json.dumps([r.__dict__ for r in results], ensure_ascii=False, indent=2), encoding="utf-8")
    return results

async def _search_builtin(query: str, max_results: int) -> List[SearchResult]:
    import httpx
    from urllib.parse import quote
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(
                f"https://www.bing.com/search?q={quote(query)}&count={max_results}",
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            )
            if resp.status_code != 200: return []
            pattern = r'<h2><a[^>]*href="([^"]*)"[^>]*>([^<]*)</a></h2>.*?<p[^>]*>(.*?)</p>'
            matches = re.findall(pattern, resp.text, re.DOTALL)
            return [SearchResult(title=t.strip(), url=u, snippet=re.sub(r'<[^>]+>', '', s).strip()[:300], source="Bing") for u, t, s in matches[:max_results]]
    except: return []

def format_search_context(results: List[SearchResult]) -> str:
    if not results: return ""
    lines = ["## 联网搜索结果（补充最新法规/政策信息）", f"（共检索到 {len(results)} 条）\n"]
    for i, r in enumerate(results):
        lines.extend([f"### {i+1}. {r.title}", f"来源: {r.url}", f"摘要: {r.snippet}", ""])
    return "\n".join(lines)

async def search_and_inject(user_intent: str) -> str:
    need = detect_search_need(user_intent)
    if not need.should_search: return ""
    results = await execute_search(need.query)
    return format_search_context(results) if results else ""
