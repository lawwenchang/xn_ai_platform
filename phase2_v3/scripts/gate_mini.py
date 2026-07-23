import sys, json, time, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.audit_bench import BENCH, grade, strip_think, params_for

VLLM = __import__("os").environ.get("VLLM_API_BASE", "http://localhost:18000/v1")
import httpx

# 连通性
r = httpx.get(f"{VLLM}/models", headers={"Authorization": "Bearer EMPTY"}, timeout=30)
print(f"MODELS: {[m['id'] for m in r.json()['data']]}", flush=True)

# 跑一题验证
q = BENCH[0]
from scripts.audit_bench import ask
out = ask(VLLM, "qwen3-235b", q["system"], q["user"], 0.2, 2048)
print(f"DAG-01 base: {out[:200]}", flush=True)

# 评分
p, t, det = grade(out, q["checks"])
print(f"SCORE: {p}/{t}", flush=True)

# 写报告
lines = ["# Gate Quick Test\n", f"base={p}/{t}\n"]
(ROOT / "data" / "gate_final.md").write_text("".join(lines), encoding="utf-8")
print("DONE", flush=True)
