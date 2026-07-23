#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RAG 生成式问答端点冒烟测试 (test_rag_qa_endpoint.py)
=====================================================
验证 /rag/search（混合检索升级）与 /rag/qa（检索+LLM生成+多轮+降级）全链路。
vLLM 在线 → engine=vllm_rag（真实回答）；离线 → retrieval_only（纯片段降级）。

运行：cd phase2_v3 && python tests/test_rag_qa_endpoint.py
"""
import sys, os, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from fastapi.testclient import TestClient
from api.routes import create_app

client = TestClient(create_app())
FAILED = False

# ── 1. 纯检索接口（升级后：混合检索优先） ──
r1 = client.post("/api/v3/rag/search", data={"query": "审计报告 无保留意见 格式", "top_k": 3})
d1 = r1.json()
n_hits = len(d1.get("data") or [])
print(f"[1.rag/search] HTTP={r1.status_code} success={d1.get('success')} 命中片段={n_hits}")
if r1.status_code != 200:
    FAILED = True

# ── 2. 生成式问答（用户实测踩坑的原问题） ──
r2 = client.post("/api/v3/rag/qa", data={
    "query": "我现在有被审计公司的相关合同，如何生成审计报告？",
    "history": "[]",
    "top_k": 4,
})
d2 = (r2.json() or {}).get("data", {})
ans = (d2.get("answer") or "").strip()
print(f"[2.rag/qa   ] HTTP={r2.status_code} engine={d2.get('engine')} "
      f"来源数={len(d2.get('sources') or [])} 回答长度={len(ans)}")
print("-" * 60)
print("回答预览：")
print(ans[:600] if ans else "（无回答 → 降级为纯检索片段模式）")
print("-" * 60)
if r2.status_code != 200:
    FAILED = True

# ── 3. 多轮追问（验证上下文记忆） ──
hist = json.dumps([
    {"role": "user", "content": "我现在有被审计公司的相关合同，如何生成审计报告？"},
    {"role": "assistant", "content": ans[:500] or "（上一轮离线未生成回答）"},
], ensure_ascii=False)
r3 = client.post("/api/v3/rag/qa", data={
    "query": "那收入确认相关的审计程序要注意什么？",
    "history": hist,
    "top_k": 3,
})
d3 = (r3.json() or {}).get("data", {})
ans3 = (d3.get("answer") or "").strip()
print(f"[3.多轮追问 ] HTTP={r3.status_code} engine={d3.get('engine')} 回答长度={len(ans3)}")
if ans3:
    print("追问回答预览：", ans3[:300].replace("\n", " "))
if r3.status_code != 200:
    FAILED = True

# ── 4. 恶意 history 容错 ──
r4 = client.post("/api/v3/rag/qa", data={"query": "函证程序", "history": "{bad json", "top_k": 2})
print(f"[4.容错     ] HTTP={r4.status_code}（history 非法 JSON 应不报错）")
if r4.status_code != 200:
    FAILED = True

print()
print("RESULT:", "FAIL" if FAILED else "PASS")
sys.exit(1 if FAILED else 0)
