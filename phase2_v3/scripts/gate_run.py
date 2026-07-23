#!/usr/bin/env python3
"""Gate评测：qwen3-235b vs audit-v4 一键跑，输出 data/gate_final.md"""
import sys, json, time, re, hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

VLLM = __import__("os").environ.get("VLLM_API_BASE", "http://localhost:18000/v1")

# ── 快速联通确认 ──
import httpx
try:
    r = httpx.get(f"{VLLM}/models", headers={"Authorization": "Bearer EMPTY"}, timeout=20)
    models = [m["id"] for m in r.json()["data"]]
    print(f"[CONN] 模型: {models}")
except Exception as e:
    print(f"[FAIL] vLLM不通: {e}")
    sys.exit(1)

# ── 加载题库 ──
from scripts.audit_bench import BENCH, grade, strip_think, params_for, check_overlap_with_v4
check_overlap_with_v4()
print(f"[LOAD] {len(BENCH)} 题就绪")

def ask(model, system, user, temp, mt):
    body = {"model": model, "temperature": temp, "max_tokens": mt,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "chat_template_kwargs": {"enable_thinking": False}}
    for attempt in (1, 2):
        try:
            r = httpx.post(f"{VLLM}/chat/completions",
                           headers={"Authorization": "Bearer EMPTY"},
                           json=body, timeout=180)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            if attempt == 1 and "chat_template_kwargs" in body:
                body.pop("chat_template_kwargs")
                continue
            return f"[ERR: {e.__class__.__name__}]"

# ── 跑评测 ──
lines, totals = [], {"qwen3-235b": [0, 0], "audit-v4": [0, 0]}
parse_ok = {"qwen3-235b": [0, 0], "audit-v4": [0, 0]}
now = time.strftime("%Y-%m-%d %H:%M")
lines.append(f"# Gate评测: qwen3-235b vs audit-v4\n\n时间: {now}\n")

for q in BENCH:
    temp, mt = params_for(q)
    lines.append(f"\n## {q['id']} [{q.get('scene','')} | {q.get('pain','')}]\n")
    lines.append(f"题面: {q['user'][:100]}...\n" if len(q["user"]) > 100
                 else f"题面: {q['user']}\n")
    for m in ["qwen3-235b", "audit-v4"]:
        print(f"  [{q['id']}] {m} ...", end=" ")
        t0 = time.time()
        out = ask(m, q["system"], q["user"], temp, mt)
        dt = time.time() - t0
        show = strip_think(out)
        if q.get("manual"):
            lines.append(f"### {m} ({dt:.1f}s)\n\n{show[:600]}\n")
            print(f"{dt:.1f}s")
        else:
            p, t, det = grade(out, q["checks"])
            totals[m][0] += p
            totals[m][1] += t
            if q["id"].startswith("DAG") and q["id"] != "DAG-09":
                parse_ok[m][1] += 1
                parse_ok[m][0] += 1 if re.search(r'"operators"\s*:\s*\[', strip_think(out)) else 0
            fails = ", ".join(n for n, ok in det if not ok) or "无"
            lines.append(f"### {m} — {p}/{t} ({dt:.1f}s) 未过: {fails}\n```\n{show[:400]}\n```\n")
            print(f"{p}/{t}")
    if q.get("manual"):
        lines.append("**人工**: □基座更好  □持平  □微调更好\n")

lines.append("\n---\n# 汇总\n")
for m in ["qwen3-235b", "audit-v4"]:
    p, t = totals[m]
    pr = parse_ok[m]
    rate = f"{pr[0]}/{pr[1]}" if pr[1] else "-"
    lines.append(f"- **{m}**: {p}/{t} ({p/max(1,t):.0%}), DAG解析 {rate}\n")
b, a = totals["qwen3-235b"], totals["audit-v4"]
gate = parse_ok["audit-v4"][0] >= parse_ok["audit-v4"][1] * 0.95 and a[0] >= b[0]
lines.append(f"\n**Gate**: {'✅ 切换' if gate else '❌ 回退裸基座'} (可解析率≥95%且总分≥基座)\n")

out_fp = ROOT / "data" / "gate_final.md"
out_fp.write_text("\n".join(lines), encoding="utf-8")
print(f"\n报告 -> {out_fp}\nBase: {b[0]}/{b[1]} Adapter: {a[0]}/{a[1]}")
