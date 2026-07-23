#!/usr/bin/env python3
"""
联网搜索 + vLLM 智能摘要 (§9 Week 5-6)
========================================
当 RAG 知识库覆盖不到最新法规时，联网搜索 + vLLM 摘要注入 Dify Prompt 上下文。
"""
import json, os, re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class SearchSummary:
    query: str = ""
    raw_results: list = field(default_factory=list)
    summary: str = ""           # vLLM 摘要后的合规要点
    source_count: int = 0

    def to_context(self) -> str:
        """转为可注入 Dify System Prompt 的上下文"""
        if not self.summary:
            return ""
        return f"\n## 联网搜索：最新法规/政策补充\n{self.summary}\n"


def search_web(query: str, max_results: int = 5) -> list:
    """DuckDuckGo 搜索。降级到 Bing 抓取。"""
    results = []
    # 优先 DuckDuckGo
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", "")[:300],
                    "source": "DuckDuckGo"
                })
        if results:
            return results
    except ImportError:
        pass
    except Exception as e:
        print(f"[搜索] DuckDuckGo 失败: {e}")

    # 降级 Bing
    import httpx
    from urllib.parse import quote
    try:
        r = httpx.get(
            f"https://www.bing.com/search?q={quote(query)}&count={max_results}",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=10, follow_redirects=True)
        if r.status_code == 200:
            pat = r'<h2><a[^>]*href="([^"]*)"[^>]*>([^<]*)</a></h2>.*?<p[^>]*>(.*?)</p>'
            for u, t, s in re.findall(pat, r.text, re.DOTALL)[:max_results]:
                results.append({"title": t.strip(), "url": u,
                                "snippet": re.sub(r'<[^>]+>', '', s).strip()[:300],
                                "source": "Bing"})
    except Exception as e:
        print(f"[搜索] Bing 失败: {e}")

    return results


def search_and_summarize(query: str, max_results: int = 5) -> SearchSummary:
    """
    搜索 + vLLM 摘要。将搜索结果转为一句话合规要点。
    """
    sm = SearchSummary(query=query)
    sm.raw_results = search_web(query, max_results)
    sm.source_count = len(sm.raw_results)

    if not sm.raw_results:
        return sm

    context = "\n".join(
        f"[{i+1}] {r['title']}\n{r['snippet'][:200]}"
        for i, r in enumerate(sm.raw_results[:5])
    )

    prompt = f"""你是审计法规专家。总结以下搜索结果中的审计合规要点。

【搜索问题】{query}

【搜索结果】
{context[:3000]}

【要求】用3-5句话总结关键合规要点，包含具体日期和法规编号（如有），直接输出文本。"""

    try:
        import httpx
        r = httpx.post(
            os.environ.get("VLLM_TUNNEL_URL", "http://localhost:18000/v1/chat/completions"),
            headers={"Authorization": "Bearer EMPTY"},
            json={"model": os.environ.get("VLLM_MODEL", "qwen3-235b"),
                  "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.3, "max_tokens": 512}, timeout=30)
        r.raise_for_status()
        sm.summary = r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        # 降级：直接拼接搜索结果
        sm.summary = f"以下为原始搜索结果（vLLM不可用，未做摘要）:\n{context}"

    return sm


def search_and_inject_context(user_intent: str) -> str:
    """
    便捷入口：检测意图中的法规关键词 → 搜索 → vLLM摘要 → 返回可注入上下文。
    供 Dify Prompt 调用。
    """
    if not any(kw in user_intent for kw in
               ["最新", "新规", "新发布", "修订", "2025", "2026", "近日",
                "法律", "法规", "政策", "合规", "规定", "条例", "办法"]):
        return ""

    sm = search_and_summarize(user_intent[:100])
    return sm.to_context()
