import sys, json, time, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "data" / "gate_final.md"

try:
    from scripts.audit_bench import BENCH, grade, strip_think, params_for, ask as bench_ask
    OUT.write_text(f"# Gate 开始\n\nLOADED {len(BENCH)} 题\n", encoding="utf-8")
except Exception as e:
    OUT.write_text(f"# IMPORT ERROR\n\n{type(e).__name__}: {e}", encoding="utf-8")
    raise

import httpx
import os
VLLM = os.environ.get("VLLM_API_BASE", "http://localhost:18000/v1")

def ask(model, system, user, temp, mt):
    body = {"model": model, "temperature": temp, "max_tokens": mt,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}]}
    if "thinking" not in model.lower():
        body["chat_template_kwargs"] = {"enable_thinking": False}
    r = httpx.post(f"{VLLM}/chat/completions",
                   headers={"Authorization": "Bearer EMPTY"},
                   json=body, timeout=300)
    return r.json()["choices"][0]["message"]["content"]

lines, totals = [f"# Gate评测: qwen3-235b vs audit-v4\n\n时间: {time.strftime('%Y-%m-%d %H:%M')}\n"], \
                {"qwen3-235b": [0, 0], "audit-v4": [0, 0]}
parse_ok = {"qwen3-235b": [0, 0], "audit-v4": [0, 0]}

for qi, q in enumerate(BENCH):
    temp, mt = params_for(q)
    lines.append(f"\n## {q['id']} [{q.get('scene','')} | {q.get('pain','')}]\n")
    lines.append(f"题面: {q['user'][:150]}\n")
    OUT.write_text("".join(lines) + f"\n\n⏳ 第{qi+1}/{len(BENCH)}题...", encoding="utf-8")
    for m in ["qwen3-235b", "audit-v4"]:
        out_raw = ask(m, q["system"], q["user"], temp, mt)
        show = strip_think(out_raw)
        if q.get("manual"):
            lines.append(f"### {m}\n\n{show[:600]}\n")
        else:
            p, t, det = grade(out_raw, q["checks"])
            totals[m][0] += p
            totals[m][1] += t
            if q["id"].startswith("DAG") and q["id"] != "DAG-09":
                parse_ok[m][1] += 1
                parse_ok[m][0] += 1 if re.search(r'"operators"\s*:\s*\[', show) else 0
            fails = ", ".join(n for n, ok in det if not ok) or "无"
            lines.append(f"### {m} — {p}/{t} 未过: {fails}\n```\n{show[:500]}\n```\n")
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
lines.append(f"\n**Gate**: {'✅ A6切换audit-v4' if gate else '❌ A3回退裸基座'} (解析率≥95%且总分≥基座)\n")

OUT.write_text("".join(lines), encoding="utf-8")
