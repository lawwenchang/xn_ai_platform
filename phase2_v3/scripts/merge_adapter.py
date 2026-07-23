#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
合并 QLoRA adapter 到基座模型

用法：
    python merge_adapter.py \
        --base-model /root/autodl-tmp/models/Qwen2.5-32B-Instruct \
        --adapter /root/audit_lora_v3 \
        --output /root/autodl-tmp/models/Qwen2.5-32B-Instruct-V3

说明：
    - 先加载 4-bit 量化的基座模型并注入 adapter 权重
    - merge_and_unload() 把 adapter 融入基座，输出标准 FP16/BF16 模型
    - 输出的合并模型可被 vLLM 直接加载，不再需要加载 adapter
"""
import argparse
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import PeftModel


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base-model", required=True, help="基座模型路径或名称")
    p.add_argument("--adapter", required=True, help="QLoRA adapter 目录")
    p.add_argument("--output", required=True, help="合并后模型输出目录")
    args = p.parse_args()

    print(f"基座: {args.base_model}")
    print(f"适配: {args.adapter}")
    print(f"输出: {args.output}")

    # 加载基座（4-bit 量化，与训练时一致）
    bnb = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )

    print("加载基座模型...")
    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        quantization_config=bnb,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        trust_remote_code=True,
    )

    print("加载 adapter...")
    model = PeftModel.from_pretrained(model, args.adapter)

    print("合并 adapter 到基座...")
    model = model.merge_and_unload()

    print("保存合并模型...")
    model.save_pretrained(args.output, safe_serialization=True)

    print("保存 tokenizer...")
    tokenizer = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)
    tokenizer.save_pretrained(args.output)

    print(f"完成! 合并模型已保存到 {args.output}")


if __name__ == "__main__":
    main()
