#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Alpaca 格式 parquet -> ChatML JSONL 转换器

将下载的指令-回答对数据集（instruction/input/output 三列的 Alpaca 格式）
转换为 QLoRA 微调所需的 ChatML messages 格式（见 docs/QLoRA微调实施方案.md 第三节）。

用法:
    python convert_parquet_to_chatml.py \
        --parquet ../../train-00000-of-00001-8b6e212f3e1ece96.parquet \
        --out ../../outputs/training_data.jsonl \
        --max-samples 0        # 0 表示全部转换

依赖: pandas + pyarrow（本机已安装即可，无需 GPU）
"""
import argparse
import json
from pathlib import Path

import pandas as pd

DEFAULT_SYSTEM_PROMPT = (
    "You are an expert Python programming assistant. "
    "Follow the instruction and write clean, correct, well-structured Python code."
)


def build_messages(row: dict, system_prompt: str) -> list:
    """将一条 Alpaca 记录转换为 ChatML messages 列表"""
    instruction = (row.get("instruction") or "").strip()
    input_text = (row.get("input") or "").strip()
    output = (row.get("output") or "").strip()

    user_content = instruction
    if input_text:
        user_content += f"\n\n### Input:\n{input_text}"

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
        {"role": "assistant", "content": output},
    ]


def main():
    parser = argparse.ArgumentParser(description="Alpaca parquet -> ChatML jsonl")
    parser.add_argument("--parquet", required=True, help="输入 parquet 文件路径")
    parser.add_argument("--out", required=True, help="输出 jsonl 文件路径")
    parser.add_argument("--system-prompt", default=DEFAULT_SYSTEM_PROMPT,
                        help="system 角色提示词")
    parser.add_argument("--max-samples", type=int, default=0,
                        help="最多转换多少条（0=全部），调试时可设小值")
    args = parser.parse_args()

    df = pd.read_parquet(args.parquet)
    print(f"读取 {args.parquet}: {len(df)} 条, 列: {list(df.columns)}")

    required = {"instruction", "output"}
    missing = required - set(df.columns)
    if missing:
        raise SystemExit(f"缺少必需列: {missing}")

    if args.max_samples > 0:
        df = df.head(args.max_samples)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    written, skipped = 0, 0
    with open(out_path, "w", encoding="utf-8") as f:
        for row in df.to_dict(orient="records"):
            if not (row.get("instruction") or "").strip() or not (row.get("output") or "").strip():
                skipped += 1
                continue
            record = {"messages": build_messages(row, args.system_prompt)}
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1

    print(f"完成: 写入 {written} 条, 跳过空记录 {skipped} 条 -> {out_path}")


if __name__ == "__main__":
    main()
