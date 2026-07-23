#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""训练数据体检 (inspect_datasets.py)
======================================
统计各轮训练集：样本数 / system 提示分布 / assistant 中文占比 /
序列长度分位数 / 文件间重叠率（按 user+assistant 内容签名去重）。

用途：为 v4 微调（换基座 Qwen3）的数据配比与去重合并提供事实依据。
用法：python scripts/inspect_datasets.py
"""
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
FILES = [
    "training_data.jsonl",        # 第一轮：通用 Python 代码指令
    "final_train.jsonl",          # 第二轮：审计x10 + 风格x5 + 代码cap
    "final_val.jsonl",            # 验证集（纯审计）
    "round3_train_final.jsonl",   # 第三轮：中文修复补训
    "zh_excel_qa_500.jsonl",      # 纯中文 Excel/Pandas QA 500 条
]


def load(fp: Path):
    recs = []
    with open(fp, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                recs.append(json.loads(ln))
            except json.JSONDecodeError:
                pass
    return recs


def sig(rec) -> str:
    """按 user+assistant 内容做签名（忽略 system 差异），用于跨文件重叠检测"""
    parts = [m.get("content", "") for m in rec.get("messages", [])
             if m.get("role") in ("user", "assistant")]
    return hashlib.md5("\x1e".join(parts).encode("utf-8")).hexdigest()


def zh_ratio(text: str) -> float:
    """中文字符 / (中文+英文字母)，衡量中英混杂程度"""
    if not text:
        return 0.0
    zh = len(re.findall(r"[\u4e00-\u9fff]", text))
    en = len(re.findall(r"[A-Za-z]", text))
    return zh / max(1, zh + en)


def main():
    data = {}
    for name in FILES:
        fp = BASE / name
        if not fp.exists():
            print(f"[跳过] {name} 不存在")
            continue
        recs = load(fp)
        data[name] = recs
        n = len(recs)
        print(f"\n=== {name} ===")
        print(f"样本数: {n}")
        if not n:
            continue
        sys_counter = Counter()
        zh_scores, seq_lens = [], []
        for r in recs:
            msgs = r.get("messages", [])
            s = next((m.get("content", "")[:36] for m in msgs
                      if m.get("role") == "system"), "(无system)")
            sys_counter[s] += 1
            a = next((m.get("content", "") for m in msgs
                      if m.get("role") == "assistant"), "")
            zh_scores.append(zh_ratio(a))
            seq_lens.append(sum(len(m.get("content", "")) for m in msgs))
        low_zh = sum(1 for z in zh_scores if z < 0.3)
        print(f"assistant 中文占比均值: {sum(zh_scores)/n:.1%} | 中文<30%的样本: {low_zh} ({low_zh/n:.0%})")
        lens = sorted(seq_lens)
        print(f"字符长度 p50/p90/p99/max: "
              f"{lens[n//2]}/{lens[int(n*0.9)]}/{lens[min(n-1, int(n*0.99))]}/{lens[-1]}")
        print("system 提示分布(前4):")
        for s, c in sys_counter.most_common(4):
            print(f"   {c:>6}  {s!r}")

    names = [n for n in FILES if n in data]
    print("\n=== 文件间重叠（user+assistant 签名） ===")
    sigs = {n: {sig(r) for r in data[n]} for n in names}
    found = False
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            inter = len(sigs[a] & sigs[b])
            if inter:
                found = True
                print(f"{a} ∩ {b}: {inter} 条 (占 {b} 的 {inter/max(1, len(sigs[b])):.0%})")
    if not found:
        print("无重叠")
    print("\n体检完成。")


if __name__ == "__main__":
    main()
