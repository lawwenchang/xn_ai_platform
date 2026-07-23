import asyncio, hashlib, json, os, re
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "match_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
VLLM_URL = os.environ.get("VLLM_TUNNEL_URL", "http://localhost:18000/v1/chat/completions")
VLLM_MODEL = os.environ.get("VLLM_MODEL", "qwen3-235b")
@dataclass
class MatchResult:
    matched: bool = False
    confidence: str = "LOW"
    score: float = 0.0
    method: str = ""
    evidence: str = ""


def _rapidfuzz_score(n1: str, n2: str) -> float:
    try:
        from rapidfuzz import fuzz
        return fuzz.WRatio(n1, n2)
    except ImportError:
        return _jaccard_bigram(n1, n2)

def _jaccard_bigram(n1: str, n2: str) -> float:
    def bg(s): return {s[i:i+2] for i in range(len(s)-1)}
    b1, b2 = bg(n1), bg(n2)
    if not b1 or not b2: return 0
    return len(b1 & b2) / len(b1 | b2) * 100

async def _call_vllm(prompt: str, max_tokens: int = 100) -> str:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(VLLM_URL,
                headers={"Authorization": "Bearer EMPTY"},
                json={"model": VLLM_MODEL, "messages": [{"role": "user", "content": prompt}],
                      "temperature": 0.1, "max_tokens": max_tokens})
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"[vLLM: {e}]"

async def _is_proper_noun(name1: str, name2: str) -> bool:
    ck = hashlib.md5(f"pn:{name1}|{name2}".encode()).hexdigest()
    cf = CACHE_DIR / f"{ck}.json"
    if cf.exists():
        try: return json.loads(cf.read_text())["result"]
        except: pass
    r = await _call_vllm(f"判断以下名称是否为机构/公司/单位等专有名词。只回复是或否。名称1: {name1} 名称2: {name2}", 5)
    v = "是" in r
    cf.write_text(json.dumps({"result": v}))
    return v

async def _search_verify(name1: str, name2: str):
    import httpx
    from urllib.parse import quote
    ck = hashlib.md5(f"sv:{name1}|{name2}".encode()).hexdigest()
    cf = CACHE_DIR / f"{ck}.json"
    if cf.exists():
        try: d = json.loads(cf.read_text()); return d["matched"], d.get("evidence", "")
        except: pass
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as c:
            r = await c.get(f"https://www.bing.com/search?q={quote(name1+' '+name2)}",
                headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200: return False, "搜索不可用"
            kw1 = re.findall(r"[\u4e00-\u9fa5]{3,6}", name1)
            kw2 = re.findall(r"[\u4e00-\u9fa5]{3,6}", name2)
            co = sum(1 for m in re.finditer(r'<h2><a.*?</a></h2>', r.text)
                     if any(k in m.group() for k in kw1) and any(k in m.group() for k in kw2))
            v = co >= 1; ev = f"共现{co}次" if co else "未共现"
            cf.write_text(json.dumps({"matched": v, "evidence": ev}))
            return v, ev
    except: return False, "搜索引擎不可用"

async def _semantic_match(name1: str, name2: str):
    r = await _call_vllm(f"判断以下两个文本含义是否相同。回复相同或不同并说明。\n文本1: {name1}\n文本2: {name2}", 100)
    return "相同" in r, r[:200]

async def match_names(name1: str, name2: str) -> MatchResult:
    n1, n2 = str(name1).strip(), str(name2).strip()
    if not n1 or not n2: return MatchResult(matched=False, method="空值")
    if n1 == n2: return MatchResult(matched=True, confidence="HIGH", score=100, method="精确匹配")
    s = _rapidfuzz_score(n1, n2)
    if s >= 90: return MatchResult(matched=True, confidence="HIGH", score=s, method="RapidFuzz高分")
    if s < 40: return MatchResult(matched=False, confidence="HIGH", score=s, method="RapidFuzz低分")
    is_pn = await _is_proper_noun(n1, n2)
    if is_pn:
        v, ev = await _search_verify(n1, n2)
        return MatchResult(matched=v, confidence="MEDIUM", score=s, method="搜索引擎验证", evidence=ev)
    else:
        v, ev = await _semantic_match(n1, n2)
        return MatchResult(matched=v, confidence="MEDIUM", score=s, method="语义判断", evidence=ev)
