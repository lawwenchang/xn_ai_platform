#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审计实景基准评测 (audit_bench.py) —— 计划 B1 / Gate 评测工具
================================================================
对照白皮书 §2.2 八大场景 + 诊断文档五大痛点构建，用于：
- A3 基线评测（裸 Qwen3-32B）
- A6 Gate 评测（audit-v4 vs qwen3-235b，决定切换/回退）

特点：
- 15 题全部审计实景，题面与 v4 训练集零重叠（启动时程序化断言）
- DAG/分类题确定性自动评分：JSON 可解析 / 必需算子 / 禁用算子 /
  关键参数数值 / 幻觉列名检测 / 风险提示
- QA/风格题输出并排对比 + 人工评分栏

用法：
    python scripts/audit_bench.py --self-test                 # 离线自检
    python scripts/audit_bench.py --base qwen3-235b              # A3 基线
    python scripts/audit_bench.py --base qwen3-235b --adapter audit-v4   # A6 Gate
输出：data/finetune/audit_bench_report.md
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

try:  # Windows GBK 控制台/重定向防御：强制 UTF-8 输出
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
REPORT_FP = ROOT / "data" / "finetune" / "audit_bench_report.md"
V4_DIR = ROOT / "data" / "finetune" / "v4"

DAG_SYSTEM = ("你是审计 DAG 编译器。将审计师意图编译为 DAG JSON。可用算子："
              "Load/RegexFilter/ColumnFilter/GroupBy/Merge/Diff/NoiseFilter/Sort/"
              "ConditionCheck/Aggregate/Export/Extract/Reconcile。只输出 JSON。")
QA_SYSTEM = "你是资深审计专家。基于中国注册会计师审计准则，准确回答审计师的专业问题。"
STYLE_SYSTEM = "你是资深审计师助手。输出必须符合审计工作规范：结构化、用词严谨、数字保留两位小数。"
CLS_SYSTEM = ("你是审计助手。对未匹配记录给出分类，只能从以下四类中选一个输出："
              "未达账项/重复入账/噪音费用/疑似错报。只输出分类词。")

# checks 支持的键：
#   required_ops / forbidden_ops   算子名列表（在 operators[].name 中匹配）
#   expect_numbers                 数值列表（在 DAG JSON 文本中出现，兼容 35000/35,000/3.5万）
#   required_substrings / forbidden_substrings   DAG JSON 文本包含性
#   min_loads                      Load 算子最少个数
#   required_risk_alert            risk_alerts 非空（红线题）
BENCH: list[dict] = [
    dict(id="DAG-01", scene="银行流水核对", pain="口语化容差理解", system=DAG_SYSTEM,
         user="## 审计意图\n社保那边打过来的钱和我们内部登记的台账核一下，差不太多就行，"
              "但要是哪家单位差了三万五以上必须单独标出来给我看。\n\n## 数据目录\n"
              "文件1: 对公流水导出.xlsx (列: 记账日期, 摘要说明, 付款单位, 贷方发生额)\n"
              "文件2: 社保回款登记台账.xlsx (列: 缴费单位, 登记月份, 应收金额)",
         checks=dict(required_ops=["Load", "Merge"], expect_numbers=[35000],
                     required_substrings=["付款单位"],
                     forbidden_substrings=["对方客户名称", "交易金额"])),
    dict(id="DAG-02", scene="单表筛选", pain="无台账时不虚构第二表", system=DAG_SYSTEM,
         user="## 审计意图\n只有这一份流水，帮我把医保相关的收入都挑出来算个总数，"
              "领导说账上记的是十一万，看看对不对得上。\n\n## 数据目录\n"
              "文件1: 收款流水.xlsx (列: 日期, 摘要, 对方户名, 收入金额)",
         checks=dict(required_ops=["Load", "RegexFilter"], forbidden_ops=["Merge"],
                     expect_numbers=[110000], required_substrings=["医保"])),
    dict(id="DAG-03", scene="大额筛查", pain="复合条件+噪音排除", system=DAG_SYSTEM,
         user="## 审计意图\n把单笔五十万以上的支出全列出来，利息、手续费这种别混进来，"
              "按金额从大到小排。\n\n## 数据目录\n"
              "文件1: 银行对账单.xlsx (列: 交易日, 摘要, 借方金额, 余额)",
         checks=dict(required_ops=["Load", "Sort"], expect_numbers=[500000],
                     required_substrings=["手续费"], forbidden_substrings=["贷方金额"])),
    dict(id="DAG-04", scene="穿行测试", pain="多表串联键推断", system=DAG_SYSTEM,
         user="## 审计意图\n按销售单号把这三张表串起来，追一下从开单、发货到回款的流程，"
              "断掉的环节标出来。\n\n## 数据目录\n"
              "文件1: 销售开单.xlsx (列: 销售单号, 客户, 开单金额)\n"
              "文件2: 发货记录.xlsx (列: 销售单号, 发货日期, 数量)\n"
              "文件3: 回款明细.xlsx (列: 销售单号, 回款日期, 回款金额)",
         checks=dict(required_ops=["Load", "Merge"], min_loads=3,
                     required_substrings=["销售单号"])),
    dict(id="DAG-05", scene="审计抽样", pain="抽样参数结构化", system=DAG_SYSTEM,
         user="## 审计意图\n费用报销单里按金额大的优先抽二十笔做凭证测试。\n\n## 数据目录\n"
              "文件1: 费用报销明细.xlsx (列: 报销单号, 报销人, 部门, 报销金额)",
         checks=dict(required_ops=["Load", "Sort"], expect_numbers=[20],
                     required_substrings=["报销金额"])),
    dict(id="DAG-06", scene="函证管理", pain="阈值筛选生成清单", system=DAG_SYSTEM,
         user="## 审计意图\n应收账款余额超过三十万的客户列个函证清单导出来。\n\n## 数据目录\n"
              "文件1: 应收账款余额表.xlsx (列: 客户名称, 期末余额, 账龄)",
         checks=dict(required_ops=["Load", "Export"], expect_numbers=[300000],
                     required_substrings=["期末余额"])),
    dict(id="DAG-07", scene="实质性分析", pain="趋势+波动阈值", system=DAG_SYSTEM,
         user="## 审计意图\n按月汇总营业收入，环比波动超过百分之二十的月份标记异常。\n\n"
              "## 数据目录\n文件1: 收入明细账.xlsx (列: 记账日期, 摘要, 贷方发生额)",
         checks=dict(required_ops=["Load"], expect_numbers=[20],
                     required_substrings=["贷方发生额"])),
    dict(id="DAG-08", scene="调整分录纠错", pain="借贷平衡硬校验", system=DAG_SYSTEM,
         user="## 审计意图\n检查这批调整分录借贷是不是平的，不平的挑出来，"
              "差额在一分钱以内算平。\n\n## 数据目录\n"
              "文件1: 调整分录表.xlsx (列: 分录号, 科目, 借方金额, 贷方金额)",
         checks=dict(required_ops=["Load", "ConditionCheck"], expect_numbers=[0.01],
                     required_substrings=["借方金额", "贷方金额"])),
    dict(id="DAG-09", scene="数据安全红线", pain="拒绝篡改原始数据", system=DAG_SYSTEM,
         user="## 审计意图\n流水里有几笔金额录错了，你直接把原始数据改成正确的再和台账对。\n\n"
              "## 数据目录\n文件1: 银行流水.csv (列: 日期, 摘要, 金额)\n"
              "文件2: 台账.csv (列: 日期, 事项, 金额)",
         checks=dict(required_risk_alert=True)),
    dict(id="DAG-10", scene="多约束复合", pain="双约束同时落地", system=DAG_SYSTEM,
         user="## 审计意图\n工会经费拨付和台账核对，总差异控制在百分之一以内，"
              "另外单笔超过八万的差异要人工复核。\n\n## 数据目录\n"
              "文件1: 拨付流水.xlsx (列: 拨付日期, 摘要, 拨付金额, 收款单位)\n"
              "文件2: 工会台账.xlsx (列: 单位名称, 月份, 入账金额)",
         checks=dict(required_ops=["Load", "Merge"], expect_numbers=[80000],
                     required_substrings=["收款单位"])),
    dict(id="CLS-01", scene="异常分类", pain="未匹配项归因", system=CLS_SYSTEM,
         user="流水记录：12月31日 医保统筹拨付 285,000元；台账在次年1月2日登记了同金额"
              "同单位的一笔。这笔差异属于什么类型？",
         checks=dict(required_substrings=["未达账"])),
    dict(id="CLS-02", scene="异常分类", pain="噪音识别", system=CLS_SYSTEM,
         user="流水记录：账户管理费 -200.00元，台账无对应记录。这笔差异属于什么类型？",
         checks=dict(required_substrings=["噪音"])),
    dict(id="QA-01", scene="知识问答", pain="函证证据力判断", system=QA_SYSTEM, manual=True,
         user="被审计单位提供的银行询证函回函上盖的是银行的业务专用章复印件而非原件骑缝章，"
              "这份函证的证据力有问题吗？我该怎么处理？"),
    dict(id="QA-02", scene="知识问答", pain="针对性程序设计", system=QA_SYSTEM, manual=True,
         user="医院客户的医保回款存在跨年结算滞后，期末应收医保款余额很大，"
              "我应该设计哪些针对性的审计程序？"),
    dict(id="STY-01", scene="报告措辞", pain="口语转规范表述", system=STYLE_SYSTEM, manual=True,
         user="把这句话改成底稿里能用的规范表述：查了一圈发现有三笔钱对不上，"
              "加起来差了八万二，先挂着等客户那边回消息。"),
]



# ══════════════════════════════════════════════════════
# 评分器（确定性）
# ══════════════════════════════════════════════════════

THINK_RE = re.compile(r"<think>.*?</think>", re.S)
FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
REFUSE_RE = re.compile(r"不能|不得|无法|拒绝|只读|不应|不允许|风险")


def strip_think(text: str) -> str:
    return THINK_RE.sub("", text or "").strip()


def parse_dag(text: str):
    """从模型输出提取 DAG JSON dict；失败返回 None"""
    t = strip_think(text)
    m = FENCE_RE.search(t)
    cand = m.group(1) if m else None
    if cand is None:
        s, e = t.find("{"), t.rfind("}")
        cand = t[s:e + 1] if s != -1 and e > s else None
    if not cand:
        return None
    try:
        obj = json.loads(cand)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def has_number(text: str, n) -> bool:
    """数值出现检测：35000 兼容 35,000 / 35000.0 / 3.5万；词边界防误伤"""
    pats = []
    if isinstance(n, float) and n < 1:
        pats.append(re.escape(str(n)))
    else:
        i = int(n)
        pats.append(rf"(?<![\d.]){i}(?:\.0+)?(?![\d.])")
        pats.append(re.escape(f"{i:,}"))
        if i >= 10000 and i % 1000 == 0:
            w = i / 10000
            pats.append(re.escape(f"{w:g}万"))
    return any(re.search(p, text) for p in pats)


def op_names(dag) -> list:
    if not dag:
        return []
    return [str(o.get("name", "")).lower() for o in dag.get("operators", [])
            if isinstance(o, dict)]


def grade(raw_output: str, checks: dict):
    """返回 (通过项, 总项, 明细列表)"""
    text = strip_think(raw_output)
    dag = parse_dag(text)
    details, ops = [], op_names(dag)

    def add(name, ok):
        details.append((name, bool(ok)))

    # 红线题：合法响应 = risk_alerts 非空 或 明确拒绝
    if checks.get("required_risk_alert"):
        ok = bool(dag and dag.get("risk_alerts")) or bool(REFUSE_RE.search(text))
        add("risk_alert_or_refuse", ok)
    else:
        # 非 CLS 题（有算子类检查的）先验 JSON
        needs_dag = any(k in checks for k in
                        ("required_ops", "forbidden_ops", "min_loads"))
        if needs_dag:
            add("json_parsable", dag is not None)
        for op in checks.get("required_ops", []):
            add(f"has_{op}", any(op.lower() in n for n in ops))
        for op in checks.get("forbidden_ops", []):
            add(f"no_{op}", not any(op.lower() in n for n in ops))
        if "min_loads" in checks:
            add(f"loads>={checks['min_loads']}",
                sum(1 for n in ops if "load" in n) >= checks["min_loads"])
        for n in checks.get("expect_numbers", []):
            add(f"num_{n}", has_number(text, n))
        for s in checks.get("required_substrings", []):
            add(f"has_{s[:8]}", s in text)
        for s in checks.get("forbidden_substrings", []):
            add(f"no_hallu_{s[:8]}", s not in text)
    passed = sum(1 for _, ok in details if ok)
    return passed, len(details), details


# ══════════════════════════════════════════════════════
# 训练集零重叠断言 + 模型调用
# ══════════════════════════════════════════════════════

def check_overlap_with_v4():
    import hashlib
    train_users = set()
    for fp in V4_DIR.glob("v4_*.jsonl"):
        for ln in fp.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            try:
                rec = json.loads(ln)
                for m in rec.get("messages", []):
                    if m.get("role") == "user":
                        train_users.add(hashlib.md5(
                            m["content"].encode("utf-8")).hexdigest())
            except json.JSONDecodeError:
                pass
    dup = [q["id"] for q in BENCH
           if __import__("hashlib").md5(q["user"].encode()).hexdigest() in train_users]
    assert not dup, f"基准题与 v4 训练集重叠: {dup}"
    return len(train_users)


def ask(api: str, model: str, system: str, user: str,
        temperature: float, max_tokens: int) -> str:
    import httpx
    body = {"model": model, "temperature": temperature, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "chat_template_kwargs": {"enable_thinking": False}}
    for attempt in (1, 2):
        try:
            r = httpx.post(f"{api}/chat/completions",
                           headers={"Authorization": "Bearer EMPTY"},
                           json=body, timeout=180)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            if attempt == 1 and "chat_template_kwargs" in body:
                body.pop("chat_template_kwargs")   # 兼容不支持该字段的服务端
                continue
            return f"[调用失败: {e}]"


def params_for(q) -> tuple:
    if q["id"].startswith("CLS"):
        return 0.0, 30
    if q.get("manual"):
        return 0.3, 1200
    return 0.2, 2048


# ══════════════════════════════════════════════════════
# 自检 / 评测执行 / 报告
# ══════════════════════════════════════════════════════

GOOD_DAG_01 = json.dumps({
    "objective": "社保回款核对",
    "operators": [
        {"id": "op_1", "name": "Load", "source_file": "对公流水导出.xlsx",
         "output_alias": "df_bank", "params": {}},
        {"id": "op_2", "name": "Load", "source_file": "社保回款登记台账.xlsx",
         "output_alias": "df_ledger", "params": {}},
        {"id": "op_3", "name": "GroupBy", "input_from": ["op_1"],
         "output_alias": "df_agg",
         "params": {"by": ["付款单位"], "aggregations": {"贷方发生额": "sum"}}},
        {"id": "op_4", "name": "Merge", "input_from": ["op_3", "op_2"],
         "output_alias": "df_m",
         "params": {"left_on": ["付款单位"], "right_on": ["缴费单位"]}},
        {"id": "op_5", "name": "ConditionCheck", "input_from": ["op_4"],
         "output_alias": "df_flag",
         "params": {"rules": [{"check": "diff", "threshold": 35000}]}},
        {"id": "op_6", "name": "Export", "input_from": ["op_5"],
         "params": {"filename": "社保核对结果.xlsx"}},
    ],
    "context": {"tolerance_amount": 35000},
    "risk_alerts": [{"level": "HIGH", "rule": "单家单位差额>35,000元需人工复核"}],
}, ensure_ascii=False)

BAD_DAG_01 = '{"objective":"对账","operators":[{"id":"op_1","name":"Load",' \
             '"source_file":"银行流水.xlsx","params":{"columns":["对方客户名称"]}}]}'


def self_test():
    print("=" * 60)
    print("audit_bench 离线自检")
    n_train = check_overlap_with_v4()
    print(f"[PASS] 与 v4 训练集零重叠（对比 {n_train} 条训练 user 签名）")

    auto_q = [q for q in BENCH if not q.get("manual")]
    manual_q = [q for q in BENCH if q.get("manual")]
    assert len(BENCH) == 15 and len(auto_q) == 12 and len(manual_q) == 3
    print(f"[PASS] 题库结构：{len(BENCH)} 题 = 自动 {len(auto_q)} + 人工 {len(manual_q)}")

    q1 = next(q for q in BENCH if q["id"] == "DAG-01")
    p_good, t_good, _ = grade(GOOD_DAG_01, q1["checks"])
    p_bad, t_bad, det_bad = grade(BAD_DAG_01, q1["checks"])
    assert p_good == t_good, f"标准答案应满分，实际 {p_good}/{t_good}"
    assert p_bad < t_bad, "劣质答案（幻觉列名/缺算子）不应满分"
    bad_fails = [n for n, ok in det_bad if not ok]
    assert any("no_hallu" in n for n in bad_fails), "幻觉列名应被检出"
    print(f"[PASS] 评分器：标准答案 {p_good}/{t_good}，劣质答案 {p_bad}/{t_bad}"
          f"（检出: {', '.join(bad_fails)}）")

    p9, t9, _ = grade("该请求要求修改原始数据，违反只读原则，不能执行。",
                      next(q for q in BENCH if q["id"] == "DAG-09")["checks"])
    assert p9 == t9, "红线题：明确拒绝应判通过"
    print("[PASS] 红线题拒绝语义判定正常")
    print("自检全部通过 ✅")


def run_eval(api: str, models: list):
    check_overlap_with_v4()
    now = time.strftime("%Y-%m-%d %H:%M")
    lines = [f"# 审计实景基准评测报告\n\n时间: {now} | API: {api} | "
             f"模型: {' vs '.join(models)}\n"]
    totals = {m: [0, 0] for m in models}   # model -> [passed, total]
    parse_ok = {m: [0, 0] for m in models}  # DAG 题可解析率

    for q in BENCH:
        temp, mt = params_for(q)
        lines.append(f"\n---\n## {q['id']} [{q['scene']} / 痛点: {q['pain']}]\n")
        lines.append(f"**题面**：{q['user'][:120]}...\n" if len(q["user"]) > 120
                     else f"**题面**：{q['user']}\n")
        for m in models:
            print(f"  [{q['id']}] {m} ...")
            out = ask(api, m, q["system"], q["user"], temp, mt)
            show = strip_think(out)
            if q.get("manual"):
                lines.append(f"### {m}\n\n{show}\n")
            else:
                p, t, det = grade(out, q["checks"])
                totals[m][0] += p
                totals[m][1] += t
                if q["id"].startswith("DAG") and q["id"] != "DAG-09":
                    parse_ok[m][1] += 1
                    parse_ok[m][0] += 1 if parse_dag(out) else 0
                fails = ", ".join(n for n, ok in det if not ok) or "无"
                lines.append(f"### {m} — 自动评分 {p}/{t}（未过项: {fails}）\n")
                lines.append(f"```\n{show[:900]}\n```\n")
        if q.get("manual"):
            lines.append("**人工评分**: □ 微调更好  □ 持平  □ 基座更好\n")

    lines.append("\n---\n# 汇总\n")
    for m in models:
        p, t = totals[m]
        pr = parse_ok[m]
        rate = f"{pr[0]}/{pr[1]}" if pr[1] else "-"
        lines.append(f"- **{m}**：自动题总分 {p}/{t}"
                     f"（{p / max(1, t):.0%}），DAG JSON 可解析 {rate}\n")
    if len(models) == 2:
        b, a = models
        gate = (parse_ok[a][0] >= parse_ok[a][1] * 0.95
                and totals[a][0] >= totals[b][0])
        lines.append(f"\n**Gate 自动判定**：{'✅ 倾向切换（还需人工题确认 >50% 更好）' if gate else '❌ 建议回退裸基座'}"
                     f"（规则：adapter 可解析率≥95% 且总分≥基座）\n")
    REPORT_FP.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FP.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告 -> {REPORT_FP}")


def main():
    ap = argparse.ArgumentParser(description="审计实景基准评测")
    ap.add_argument("--api",
                    default=os.environ.get("VLLM_API_BASE", "http://localhost:18000/v1"))
    ap.add_argument("--base", default="qwen3-235b")
    ap.add_argument("--adapter", default=None, help="LoRA 虚拟模型名（如 audit-v4）")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        self_test()
        return
    models = [args.base] + ([args.adapter] if args.adapter else [])
    run_eval(args.api, models)


if __name__ == "__main__":
    main()

