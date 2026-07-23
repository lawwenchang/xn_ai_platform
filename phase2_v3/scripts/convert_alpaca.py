#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Alpaca格式 → ChatML格式转换器 (convert_alpaca.py)
==================================================
将 instruction/input/output 格式的数据集转为训练脚本所需的 messages 格式。

用法：
    python scripts/convert_alpaca.py <输入文件.jsonl> [输出文件.jsonl]

例：
    python scripts/convert_alpaca.py excel_dataset_cleaned.jsonl
    → 输出 data/finetune/synthetic/excel_converted.jsonl
"""

import json, sys
from pathlib import Path

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "finetune" / "synthetic"
DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# 代码/Excel类数据的 System Prompt
SYSTEM_PROMPT = "你是数据处理与Excel专家，精通Excel公式、Pandas和数据分析。准确回答用户的技术问题，给出可直接使用的公式或代码。"


def convert_record(rec: dict) -> dict:
    """单条 Alpaca 记录 → ChatML"""
    instruction = rec.get("instruction", "").strip()
    input_text = rec.get("input", "").strip()
    output = rec.get("output", "").strip()

    if not instruction or not output:
        return None

    # instruction + input 合并为 user 消息
    if input_text:
        user_content = f"{instruction}\n\n{input_text}"
    else:
        user_content = instruction

    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": output},
        ]
    }


def main(input_path: str, output_path: str = ""):
    src = Path(input_path)
    if not src.exists():
        print(f"文件不存在: {src}")
        return

    if not output_path:
        output_path = str(DEFAULT_OUTPUT_DIR / f"{src.stem}_chatml.jsonl")

    converted, skipped = 0, 0
    seen = set()

    with open(src, encoding="utf-8") as fin, \
         open(output_path, "w", encoding="utf-8") as fout:
        for line_num, line in enumerate(fin, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue

            chatml = convert_record(rec)
            if chatml is None:
                skipped += 1
                continue

            # 去重
            key = json.dumps(chatml["messages"], ensure_ascii=False)
            if key in seen:
                skipped += 1
                continue
            seen.add(key)

            fout.write(json.dumps(chatml, ensure_ascii=False) + "\n")
            converted += 1

    print(f"转换完成: {converted} 条")
    print(f"跳过(空/重复/格式错误): {skipped} 条")
    print(f"输出: {output_path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python scripts/convert_alpaca.py <输入.jsonl> [输出.jsonl]")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "")
