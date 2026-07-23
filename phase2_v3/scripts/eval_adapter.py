#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Adapter 效果对比评估 (eval_adapter.py)
=======================================
同一批测试题分别问基座和微调模型，输出 Markdown 并排对比报告。

前提：vLLM 已启动并加载 adapter
用法：python scripts/eval_adapter.py
输出：data/finetune/eval_report.md（含'人工评分'栏，供逐题打勾）
"""
import argparse, json, httpx, time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
VAL_FILE = BASE_DIR / "data" / "finetune" / "final_val.jsonl"
REPORT = BASE_DIR / "data" / "finetune" / "eval_report.md"

TESTS = [
    ("审计-DAG", "帮我核对医保回款，差异控制在5万以内。数据有银行流水(摘要/对方户名/交易金额)和台账(机构名称/回款金额)。请给出处理方案。"),
    ("审计-DAG", "帮我做科目余额核对，总账和明细账对一下。"),
    ("审计-QA",  "询证函发出一个月没收到回函，我该怎么办？"),
    ("审计-风格","把这句话改成规范的审计表述：这个数对不上，可能有问题"),
    ("审计-风格","核对一致后结论怎么写？"),
    ("代码",     "用pandas读取Excel文件，筛选金额列大于50万的行，按日期排序后导出新Excel，写出完整代码。"),
    ("代码",     "Excel里SUMIFS怎么按两个条件求和？给出公式。"),
    ("通用",     "用一句话解释什么是复利。"),
    ("通用",     "把'今天天气很好'翻译成英文。"),
]


def ask(api: str, model: str, q: str) -> str:
    try:
        r = httpx.post(f"{api}/chat/completions",
            headers={"Authorization": "Bearer EMPTY"},
            json={"model": model, "messages": [{"role": "user", "content": q}],
                  "temperature": 0.3, "max_tokens": 800}, timeout=120)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"[失败: {e}]"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--api",
                   default=__import__("os").environ.get("VLLM_API_BASE",
                                                        "http://localhost:18000/v1"))
    p.add_argument("--base", default="Qwen/Qwen2.5-32B-Instruct")
    p.add_argument("--adapter", default="audit-v2")
    p.add_argument("--max-val", type=int, default=5)
    args = p.parse_args()

    tests = list(TESTS)
    if VAL_FILE.exists():
        val = [json.loads(l) for l in open(VAL_FILE, encoding="utf-8") if l.strip()]
        for r in val[:args.max_val]:
            tests.append(("验证集", r["messages"][1]["content"]))

    print(f"共 {len(tests)} 题，对比基座 vs {args.adapter}...")
    now = time.strftime("%Y-%m-%d %H:%M")
    lines = [f"# 微调效果对比报告\n\n基座: {args.base} | Adapter: {args.adapter}\n时间: {now}\n"]

    for i, (cat, q) in enumerate(tests, 1):
        print(f"  [{i}/{len(tests)}] {cat}")
        base_a = ask(args.api, args.base, q)
        lora_a = ask(args.api, args.adapter, q)
        lines += [
            f"\n---\n## {i}. [{cat}] {q[:80]}\n",
            f"### 基座\n\n{base_a}\n",
            f"### {args.adapter}\n\n{lora_a}\n",
            f"### 评分: □ 微调更好  □ 持平  □ 基座更好\n",
        ]

    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告 -> {REPORT}")
    print("规则：微调更好>50%上线，基座更好>30%回退旧checkpoint。")


if __name__ == "__main__":
    main()
