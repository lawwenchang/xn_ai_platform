#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模型保留验收：运行稳定性 + DAG 可解析率 (check_model_stability.py)
====================================================================
判据：Qwen3-32B 运行稳定 且 DAG 可解析率 >= 90% → 保留该模型。

三档可解析口径（逐档趋严）：
  T1 宽松 JSON   —— audit_bench.parse_dag（剥think/围栏后 json.loads 成功）
  T2 严格 Schema —— core.dag_compiler.DAGParser.parse 直接通过
                    （含环检测/悬空引用/重复ID等图完整性校验）
  T3 生产口径    —— T2 失败后经 rule_fix_dag 规则修复再通过
                    （平台实际消费 DAG 的路径）★ 最终裁决用 T3

稳定性口径：
  - 传输层零失败（超时/5xx/空响应；每题允许一次兼容性重试）→ 稳定
  - 失败率 <=5% → 基本稳定（告警）；更高 → 不稳定
  - 同时记录每题延迟 P50/P95 与 completion tokens

样本量：10 道 DAG 题 × rounds 轮；DAG-09 红线题单列（拒绝也算对，
不计入解析率分母，与 gate_full.py 口径一致）→ 每轮 9 个有效样本。
  3 轮 = 27 样本，>=90% 需 >=25 个 T3 通过
  5 轮 = 45 样本，>=90% 需 >=41 个 T3 通过

用法：
  python scripts/check_model_stability.py --dry-run     # 离线自检（无需隧道）
  python scripts/check_model_stability.py --check-only  # 只测连通+冒烟推理
  python scripts/check_model_stability.py               # qwen3-235b × 3 轮完整验收
  python scripts/check_model_stability.py --rounds 5 --wait 300
  python scripts/check_model_stability.py --prod-prompt   # 生产口径提示复测（定位根因）
退出码：0=保留(KEEP) 1=未达标 2=基础设施不可用
报告：data/model_stability_report.md
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import socket
import statistics
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.audit_bench import (  # noqa: E402
    BENCH, GOOD_DAG_01, grade, params_for, parse_dag,
)
from core.dag_compiler import DAGParser, rule_fix_dag  # noqa: E402

REPORT = ROOT / "data" / "model_stability_report.md"
DAG_QS = [q for q in BENCH if q["id"].startswith("DAG")]
PARSE_QS = [q for q in DAG_QS if q["id"] != "DAG-09"]  # 红线题不计入解析率

# 生产口径系统提示（与 dify/workflows/语义编译器.yml「LLM 自由编译核心」一致）。
# audit_bench 原生 DAG_SYSTEM 是无 schema 的极简提示，用于测裸模型下限；
# --prod-prompt 用本提示 + 动态 few-shot，测"平台真实链路"口径。
PROD_DAG_SYSTEM = """你是「审计业务逻辑编译器」。你的任务是将审计师的自然语言意图 + 数据结构描述，编译为可执行的 Pandas DAG（有向无环图）。

## 核心规则
1. 【拒绝关键字匹配】不要看到"医保"就调用医保模块。必须深度理解全量语义。
2. 【自主规划 DAG】根据意图自主规划算子拓扑。

## 可用算子清单
Load, RegexFilter, ColumnFilter, GroupBy, Merge, Sort, ConditionCheck, Extract, Transform, NoiseFilter, Aggregate, Diff, Export, Reconcile, AuditAdjustment

## 输出格式（严格 JSON）
JSON Schema 结构需包含 blueprint_id, generated_at, operators, context, risk_alerts, human_review_points, expected_outputs
operators 数组元素结构示例：{"id": "op_1", "name": "Load", "source_file": "文件名.xlsx", "input_from": [], "params": {}}

## 安全约束
- 只输出 JSON，不输出其他文字
- 引用的列名必须来自 Data Catalog
- confidence_score < 0.5 时 human_review_points 至少 4 条"""


def build_prompts(q: dict, prod: bool) -> tuple:
    """返回 (system, user)。prod=True 采用生产口径：Dify 系统提示 + 动态 few-shot"""
    if not prod or not q["id"].startswith("DAG"):
        return q["system"], q["user"]
    user = q["user"]
    try:
        from config.few_shot_examples import build_dynamic_few_shot
        fs = build_dynamic_few_shot(q["user"], max_examples=3)
        if fs:
            user = fs + "\n\n" + user
    except Exception as e:
        print(f"[WARN] few-shot 注入不可用（{type(e).__name__}），仅用生产系统提示")
    return PROD_DAG_SYSTEM, user


def port_open(host: str, port: int, timeout: float = 3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# 从题面"文件N: xxx.xlsx"提取数据目录文件清单（模拟生产 catalog 注入）
FILE_RE = re.compile(r"文件\d+[:：]\s*([^\s(（]+\.(?:xlsx|xls|csv))")


def tiers(content: str, known_files: list = None) -> dict:
    """三档解析判定：T1 宽松 JSON / T2 严格 Schema / T3 生产口径。

    T3 与生产链路对齐：
    - 生产中 Dify 校验节点已将 LLM 输出重序列化为纯净 JSON（无围栏/引号噪音），
      故 T3 先尝试"重序列化后直接严格解析"；
    - 再走 rule_fix_dag 规则修复（别名/引用/文件名/置信度钳制）；
    - known_files 模拟生产 catalog 文件清单（缺失 source_file 时注入首文件）。
    """
    t1 = parse_dag(content) is not None
    t2 = t3 = False
    err = ""
    try:
        DAGParser.parse(content, known_files=known_files)
        t2 = t3 = True
    except Exception as e:
        err = str(e)[:200]
        try:
            obj = parse_dag(content)
        except Exception:
            obj = None
        if obj is not None:
            reser = json.dumps(obj, ensure_ascii=False)
            candidates = [reser]                          # 重序列化（Dify 校验节点形态）
            fixed = rule_fix_dag(reser, known_files)      # 确定性规则修复
            if fixed:
                candidates.append(fixed)
            for cand in candidates:
                try:
                    DAGParser.parse(cand, known_files=known_files)
                    t3 = True
                    break
                except Exception as e2:
                    err = str(e2)[:200]
    return {"t1": t1, "t2": t2, "t3": t3, "err": err}


def call(api: str, model: str, system: str, user: str,
         temperature: float, max_tokens: int) -> dict:
    """单次推理调用：返回内容/延迟/tokens/传输状态（兼容性降级重试一次）"""
    import httpx
    body = {"model": model, "temperature": temperature, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "chat_template_kwargs": {"enable_thinking": False}}
    last_err, t0 = "", time.time()
    for attempt in (1, 2):
        t0 = time.time()
        try:
            r = httpx.post(f"{api}/chat/completions",
                           headers={"Authorization": "Bearer EMPTY"},
                           json=body, timeout=180)
            r.raise_for_status()
            data = r.json()
            content = data["choices"][0]["message"]["content"] or ""
            if not content.strip():
                raise ValueError("空响应")
            usage = data.get("usage") or {}
            return {"ok": True, "content": content,
                    "latency": time.time() - t0,
                    "tokens": usage.get("completion_tokens", 0),
                    "retried": attempt > 1, "error": ""}
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            if attempt == 1 and "chat_template_kwargs" in body:
                body.pop("chat_template_kwargs")  # 兼容不支持该字段的服务端
                continue
    return {"ok": False, "content": "", "latency": time.time() - t0,
            "tokens": 0, "retried": True, "error": last_err[:300]}


# ══════════════════════════════════════════════════════
# 评测执行与汇总
# ══════════════════════════════════════════════════════

def run_bench(api: str, model: str, rounds: int, prod: bool = False) -> list:
    calls, total, n = [], rounds * len(DAG_QS), 0
    for rd in range(1, rounds + 1):
        for q in DAG_QS:
            n += 1
            temp, mt = params_for(q)
            sys_p, usr_p = build_prompts(q, prod)
            print(f"[{n:>2}/{total}] R{rd} {q['id']} ...", end="", flush=True)
            res = call(api, model, sys_p, usr_p, temp, mt)
            rec = {"round": rd, "qid": q["id"], **res}
            rec["raw"] = res.get("content", "")
            if res["ok"]:
                kf = FILE_RE.findall(q["user"])
                rec.update(tiers(res["content"], known_files=kf or None))
                p, t, det = grade(res["content"], q["checks"])
                rec["score"] = (p, t)
                rec["fails"] = [nm for nm, ok in det if not ok]
                print(f" {res['latency']:5.1f}s  T3={'√' if rec['t3'] else '×'}"
                      f"  score={p}/{t}"
                      + (f"  未过:{','.join(rec['fails'][:3])}" if rec["fails"] else ""))
            else:
                rec.update({"t1": False, "t2": False, "t3": False,
                            "err": res["error"], "score": (0, 0),
                            "fails": ["transport"]})
                print(f" 传输失败: {res['error'][:80]}")
            calls.append(rec)
    return calls


def summarize(calls: list, args) -> bool:
    parse_calls = [c for c in calls if c["qid"] != "DAG-09"]
    n = len(parse_calls)
    n_fail_t = sum(1 for c in calls if not c["ok"])
    n_retry = sum(1 for c in calls if c["ok"] and c.get("retried"))
    t1 = sum(1 for c in parse_calls if c["t1"])
    t2 = sum(1 for c in parse_calls if c["t2"])
    t3 = sum(1 for c in parse_calls if c["t3"])
    rate = t3 / n if n else 0.0
    need = math.ceil(args.threshold * n)
    lats = sorted(c["latency"] for c in calls if c["ok"])
    p50 = statistics.median(lats) if lats else 0
    p95 = lats[min(len(lats) - 1, max(0, math.ceil(len(lats) * 0.95) - 1))] if lats else 0
    toks = [c["tokens"] for c in calls if c["ok"] and c["tokens"]]
    red = [c for c in calls if c["qid"] == "DAG-09" and c["ok"]]
    red_pass = sum(1 for c in red if c["score"][0] == c["score"][1])
    sp = sum(c["score"][0] for c in calls)
    st = sum(c["score"][1] for c in calls)

    # T3 失败原因直方图（区分"提示词/结构问题"与"模型能力问题"的关键证据）
    from collections import Counter
    err_hist = Counter()
    for c in parse_calls:
        if c["ok"] and not c["t3"]:
            err_hist[(c.get("err") or "无错误信息")[:100]] += 1

    # 原始输出落盘（逐条 JSONL，人工检查模型到底输出了什么结构）
    raw_fp = ROOT / "data" / f"stability_raw_{args.model}.jsonl"
    try:
        with open(raw_fp, "w", encoding="utf-8") as f:
            for c in calls:
                row = {k: c.get(k) for k in ("round", "qid", "ok", "latency",
                                             "tokens", "t1", "t2", "t3",
                                             "err", "fails")}
                row["content"] = (c.get("raw") or "")[:6000]
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except Exception as e:
        print(f"[WARN] 原始输出落盘失败: {e}")
        raw_fp = None

    if n_fail_t == 0:
        stab, stab_ok = "稳定（传输零失败）", True
    elif n_fail_t / len(calls) <= 0.05:
        stab, stab_ok = f"基本稳定（{n_fail_t} 次传输失败，<=5%）", False
    else:
        stab, stab_ok = f"不稳定（{n_fail_t}/{len(calls)} 次传输失败）", False
    keep = stab_ok and rate >= args.threshold

    # 按题聚合
    per_q = []
    for q in DAG_QS:
        qs = [c for c in calls if c["qid"] == q["id"]]
        okn = sum(1 for c in qs if c["t3"])
        fail_names = sorted({f for c in qs for f in c["fails"]})
        per_q.append((q["id"], f"{okn}/{len(qs)}",
                      ", ".join(fail_names[:4]) or "-"))

    lines = [
        f"# 模型保留验收报告：{args.model}",
        f"\n时间: {time.strftime('%Y-%m-%d %H:%M')}  |  轮数: {args.rounds}"
        f"  |  API: {args.api}"
        f"  |  提示口径: {'生产(prod)' if getattr(args, 'prod_prompt', False) else 'bench极简'}",
        "\n## 裁决",
        f"\n- 稳定性: **{stab}**（成功重试 {n_retry} 次）",
        f"- DAG 可解析率（T3 生产口径）: **{t3}/{n} = {rate:.1%}**"
        f"（阈值 {args.threshold:.0%} → 需 >={need}）",
        f"- **结论: {'✅ 保留该模型 (KEEP)' if keep else '❌ 未达标 (NOT KEEP)'}**",
        "\n## 三档解析口径",
        f"\n| 口径 | 通过 | 比率 |\n|---|---|---|",
        f"| T1 宽松 JSON | {t1}/{n} | {t1/n:.1%} |" if n else "",
        f"| T2 严格 Schema | {t2}/{n} | {t2/n:.1%} |" if n else "",
        f"| T3 生产口径（规则修复后）| {t3}/{n} | {rate:.1%} |" if n else "",
        f"| **修复依赖率 (T3-T2)** | {t3 - t2}/{n} | {(t3 - t2)/n:.1%} |" if n else "",
        "\n> 反过拟合护栏：修复依赖率 >15% 说明指标提升主要靠规则修复而非模型原生输出，"
        "应回到提示词/微调层解决，禁止通过扩张修复规则抬分。",
        "\n## 性能与质量",
        f"\n- 延迟: P50 {p50:.1f}s / P95 {p95:.1f}s（{len(lats)} 次成功调用）",
        f"- completion tokens: 均值 {statistics.mean(toks):.0f}" if toks else "- tokens: 无数据",
        f"- 红线题 DAG-09: {red_pass}/{len(red)} 通过（拒绝/风险提示均计通过）",
        f"- checks 总分: {sp}/{st} ({sp/max(1,st):.0%})",
        "\n## T3 失败原因分布（'缺少 operators 字段'=结构不匹配 / '未知算子'=命名不匹配 / '循环依赖'=逻辑错误）",
    ] + [f"- {cnt} 次: {msg}" for msg, cnt in err_hist.most_common(8)] + [
        "\n## 按题聚合（T3 通过/轮数 | 未过检查项并集）",
        "\n| 题目 | T3 | 未过项 |\n|---|---|---|",
    ] + [f"| {a} | {b} | {c} |" for a, b, c in per_q] + [
        "\n## 失败明细",
    ] + [f"- R{c['round']} {c['qid']}: "
         + (c["error"] or c.get("err") or ",".join(c["fails"]))
         for c in calls if (not c["ok"]) or not c["t3"]] or ["- 无"]

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(x for x in lines if x), encoding="utf-8")
    print("\n" + "=" * 62)
    print(f"稳定性: {stab}")
    print(f"DAG 可解析率 T1/T2/T3: {t1}/{n}  {t2}/{n}  {t3}/{n} ({rate:.1%}，需>= {need})")
    dep = (t3 - t2) / n if n else 0
    print(f"修复依赖率(T3-T2): {t3 - t2}/{n} ({dep:.1%})"
          + ("  [警告] 超过15%护栏，应回归提示词/微调层解决" if dep > 0.15 else ""))
    print(f"延迟 P50/P95: {p50:.1f}s / {p95:.1f}s   红线题: {red_pass}/{len(red)}")
    if err_hist:
        print("T3 失败原因 Top3: " + "; ".join(
            f"「{m[:60]}」×{c}" for m, c in err_hist.most_common(3)))
    print(f"结论: {'✅ 保留该模型 (KEEP)' if keep else '❌ 未达标 (NOT KEEP)'}")
    print(f"报告 -> {REPORT}" + (f"\n原始输出 -> {raw_fp}" if raw_fp else ""))
    return keep


# ══════════════════════════════════════════════════════
# 离线自检 / 入口
# ══════════════════════════════════════════════════════

def dry_run() -> int:
    print("离线自检（不联网，验证三档判定管线）")
    fenced = "<think>推理过程...</think>\n```json\n" + GOOD_DAG_01 + "\n```"
    dangling = json.dumps({"operators": [
        {"id": "op_1", "name": "Load", "source_file": "a.xlsx"},
        {"id": "op_2", "name": "Export", "input_from": ["ghost", "op_1"]}],
        "human_review_points": ["x"], "confidence_score": 0.5}, ensure_ascii=False)
    broken = "这不是JSON也没有花括号"

    r_good, r_fence = tiers(GOOD_DAG_01), tiers(fenced)
    r_dang, r_bad = tiers(dangling), tiers(broken)
    assert r_good["t2"] and r_good["t3"], r_good
    assert r_fence["t2"] and r_fence["t3"], r_fence
    assert (not r_dang["t2"]) and r_dang["t3"], r_dang  # 悬空引用: 严格失败→修复→通过
    assert not (r_bad["t1"] or r_bad["t3"]), r_bad
    print("[PASS] T1/T2/T3 三档判定正确（含 think/围栏剥离、规则修复路径）")

    # 生产口径校准：值内单引号 / 百分数置信度 / 前缀算子名 / 缺失 source_file 注入
    quoted = json.dumps({"operators": [
        {"id": "op_1", "name": "Load", "source_file": "a.xlsx",
         "description": "排除'手续费'类噪音行"}],
        "human_review_points": ["x"], "confidence_score": 0.5}, ensure_ascii=False)
    rq = tiers(quoted)
    assert rq["t2"], rq  # 合法 JSON 不再被引号启发式破坏
    pct = json.dumps({"operators": [
        {"id": "op_1", "name": "Load", "source_file": "a.xlsx"}],
        "human_review_points": ["x"], "confidence_score": 85})
    rp = tiers(pct)
    assert (not rp["t2"]) and rp["t3"], rp  # 85 → 0.85 钳制修复
    pre = json.dumps({"operators": [
        {"id": "op_1", "name": "LoadData", "source_file": "a.xlsx"},
        {"id": "op_2", "name": "SortDescending", "input_from": ["op_1"]}],
        "human_review_points": ["x"], "confidence_score": 0.5})
    assert [o.name for o in DAGParser.parse(pre).operators] == ["Load", "Sort"]
    nof = json.dumps({"operators": [{"id": "op_1", "name": "Load"}],
                      "human_review_points": ["x"], "confidence_score": 0.5})
    rn = tiers(nof, known_files=["银行流水.xlsx"])
    assert (not rn["t2"]) and rn["t3"], rn  # 缺 source_file → 注入目录首文件
    kf = FILE_RE.findall(next(q for q in BENCH if q["id"] == "DAG-01")["user"])
    assert kf == ["对公流水导出.xlsx", "社保回款登记台账.xlsx"], kf
    print("[PASS] 生产校准：引号安全/置信度钳制/前缀别名/缺失文件注入/题面文件提取")

    q1 = next(q for q in BENCH if q["id"] == "DAG-01")
    p, t, _ = grade(GOOD_DAG_01, q1["checks"])
    assert p == t, f"标准答案应满分: {p}/{t}"
    print(f"[PASS] 评分器联动正常（DAG-01 标准答案 {p}/{t}）")

    s, u = build_prompts(next(q for q in BENCH if q["id"] == "DAG-01"), prod=True)
    assert "operators" in s and "只输出 JSON" in s
    print(f"[PASS] 生产口径提示构建正常（system {len(s)} 字，few-shot 注入后 user {len(u)} 字）")

    print(f"[INFO] 解析率分母: {len(PARSE_QS)} 题/轮（DAG-09 红线题单列）")
    for r in (1, 3, 5):
        nn = len(PARSE_QS) * r
        print(f"       {r} 轮 -> {nn} 样本，>=90% 需 >={math.ceil(0.9 * nn)} 个 T3 通过")
    print("离线自检通过 ✅")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="模型保留验收：稳定性 + DAG 可解析率")
    ap.add_argument("--model", default="qwen3-235b", help="vLLM served-model-name/别名")
    ap.add_argument("--rounds", type=int, default=3, help="评测轮数（默认3）")
    ap.add_argument("--threshold", type=float, default=0.90, help="T3 解析率阈值")
    ap.add_argument("--api",
                    default=os.environ.get("VLLM_API_BASE", "http://localhost:18000/v1"))
    ap.add_argument("--wait", type=int, default=0, help="等待隧道就绪的秒数")
    ap.add_argument("--prod-prompt", action="store_true",
                    help="生产口径提示（Dify 系统提示 + 动态 few-shot），"
                         "用于区分'提示词不足'与'模型能力不足'")
    ap.add_argument("--check-only", action="store_true", help="只测连通与冒烟推理")
    ap.add_argument("--dry-run", action="store_true", help="离线自检判定管线")
    args = ap.parse_args()

    if args.dry_run:
        return dry_run()

    from urllib.parse import urlparse
    u = urlparse(args.api)
    host, port = u.hostname or "127.0.0.1", u.port or 80

    deadline = time.time() + args.wait
    while not port_open(host, port):
        if time.time() >= deadline:
            print(f"[FAIL] {host}:{port} 端口未通（先建 SSH 隧道，"
                  f"将本地 {port} 端口转发至服务器 vLLM）")
            return 2
        print(f"... 等待隧道 {host}:{port} 就绪（剩余 {int(deadline - time.time())}s）")
        time.sleep(5)
    print(f"[PASS] 端口 {host}:{port} 已通")

    import httpx
    try:
        r = httpx.get(f"{args.api}/models",
                      headers={"Authorization": "Bearer EMPTY"}, timeout=10)
        r.raise_for_status()
        ids = [m["id"] for m in r.json().get("data", [])]
        print(f"[PASS] vLLM 在线，已加载模型: {ids}")
        if args.model not in ids:
            print(f"[WARN] 别名 '{args.model}' 不在列表中，仍按该别名调用"
                  f"（served-model-name 需与之一致）")
    except Exception as e:
        print(f"[FAIL] {args.api}/models 异常: {type(e).__name__}: {e}")
        return 2

    smoke = call(args.api, args.model, "你是助手。", "回复OK两个字母", 0.0, 8)
    if not smoke["ok"]:
        print(f"[FAIL] 冒烟推理失败: {smoke['error']}")
        return 2
    print(f"[PASS] 冒烟推理 {smoke['latency']:.1f}s -> {smoke['content']!r}")
    if args.check_only:
        print("连通性检查完成（--check-only），未执行评测")
        return 0

    n_parse = len(PARSE_QS) * args.rounds
    print(f"\n提示口径: "
          f"{'生产(prod-prompt: Dify系统提示+few-shot)' if args.prod_prompt else 'bench 极简（无 schema，测裸模型下限）'}")
    print(f"开始验收：{args.rounds} 轮 × {len(DAG_QS)} 题"
          f"（解析率分母 {n_parse}，>= {args.threshold:.0%} 需 "
          f">={math.ceil(args.threshold * n_parse)} 个 T3 通过）\n")
    calls = run_bench(args.api, args.model, args.rounds, prod=args.prod_prompt)
    keep = summarize(calls, args)
    return 0 if keep else 1


if __name__ == "__main__":
    sys.exit(main())
