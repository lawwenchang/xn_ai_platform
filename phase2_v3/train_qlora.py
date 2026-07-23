#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QLoRA 微调脚本（配套 docs/QLoRA微调实施方案.md）

在带 GPU 的机器（如 AutoDL）上运行。输入为 ChatML messages 格式的 JSONL
（可用 convert_parquet_to_chatml.py 从 Alpaca parquet 转换得到）。

环境安装（建议版本组合）:
    pip install "torch>=2.1" "transformers>=4.46" "peft>=0.13" \
                "trl>=0.12" "bitsandbytes>=0.44" "datasets>=3.0" accelerate

用法示例:
    # 32B（按项目方案文档，96GB 显存）
    python train_qlora.py --data /root/training_data.jsonl \
        --model Qwen/Qwen2.5-32B-Instruct --output-dir /root/audit_lora_v1

    # 本数据集为 Python 代码指令数据，也可用 7B Coder 基座更快验证
    python train_qlora.py --data /root/training_data.jsonl \
        --model Qwen/Qwen2.5-Coder-7B-Instruct --output-dir /root/pycode_lora_v1
"""
import argparse
import inspect
import json

import torch
from datasets import Dataset
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from trl import SFTTrainer

try:  # trl >= 0.9 提供 SFTConfig；旧版用 TrainingArguments 兜底
    from trl import SFTConfig
except ImportError:
    SFTConfig = None
    from transformers import TrainingArguments


def parse_args():
    p = argparse.ArgumentParser(description="QLoRA SFT for ChatML jsonl")
    p.add_argument("--data", required=True, help="ChatML messages 格式 jsonl")
    p.add_argument("--val-data", default=None,
                   help="外部验证集 jsonl（如 final_val.jsonl）。提供时忽略 --eval-ratio")
    p.add_argument("--model", default="Qwen/Qwen2.5-32B-Instruct", help="基座模型")
    p.add_argument("--output-dir", default="./qlora_output", help="adapter 输出目录")
    p.add_argument("--epochs", type=float, default=2)
    p.add_argument("--batch-size", type=int, default=4)
    p.add_argument("--grad-accum", type=int, default=4)
    p.add_argument("--lr", type=float, default=2e-4)
    p.add_argument("--max-seq-len", type=int, default=2048)
    p.add_argument("--eval-ratio", type=float, default=0.05)
    p.add_argument("--lora-r", type=int, default=16)
    p.add_argument("--lora-alpha", type=int, default=32)
    p.add_argument("--resume", action="store_true",
                   help="从 output-dir 中最近的 checkpoint 断点续训")
    p.add_argument("--init-adapter", default=None,
                   help="在已有 LoRA adapter 基础上继续训练（如第一轮的输出目录）")
    return p.parse_args()


def load_jsonl(path):
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def main():
    args = parse_args()

    # ═══ 精度选择：优先 bf16（无需 GradScaler，避免 fp16/bf16 梯度混用报错） ═══
    use_bf16 = torch.cuda.is_available() and torch.cuda.is_bf16_supported()
    compute_dtype = torch.bfloat16 if use_bf16 else torch.float16
    print(f"训练精度: {'bf16' if use_bf16 else 'fp16'}")

    # ═══ 量化（4-bit NF4 + 双重量化） ═══
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=compute_dtype,
        bnb_4bit_use_double_quant=True,
    )

    # ═══ 加载模型与分词器 ═══
    model = AutoModelForCausalLM.from_pretrained(
        args.model,
        quantization_config=bnb_config,
        torch_dtype=compute_dtype,
        device_map="auto",
        trust_remote_code=True,
    )
    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = prepare_model_for_kbit_training(model)
    model.config.use_cache = False  # 与 gradient checkpointing 兼容

    # ═══ LoRA：加载已有 adapter 继续训练，或新建 ═══
    if args.init_adapter:
        from peft import PeftModel
        print(f"加载第一轮 adapter 继续训练: {args.init_adapter}")
        model = PeftModel.from_pretrained(model, args.init_adapter, is_trainable=True)
    else:
        lora_config = LoraConfig(
            r=args.lora_r,
            lora_alpha=args.lora_alpha,
            lora_dropout=0.05,
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                            "gate_proj", "up_proj", "down_proj"],
            bias="none",
            task_type="CAUSAL_LM",
        )
        model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    # ═══ 加载并格式化数据（apply_chat_template 保证 ChatML 正确） ═══
    records = load_jsonl(args.data)
    print(f"训练数据: {len(records)} 条")

    formatted = [
        tokenizer.apply_chat_template(r["messages"], tokenize=False)
        for r in records
    ]

    if args.val_data:
        # 外部验证集（如 final_val.jsonl）
        val_records = load_jsonl(args.val_data)
        val_formatted = [
            tokenizer.apply_chat_template(r["messages"], tokenize=False)
            for r in val_records
        ]
        train_ds = Dataset.from_dict({"text": formatted})
        eval_ds = Dataset.from_dict({"text": val_formatted})
    else:
        split = max(1, int(len(formatted) * (1 - args.eval_ratio)))
        train_ds = Dataset.from_dict({"text": formatted[:split]})
        eval_ds = Dataset.from_dict({"text": formatted[split:]})
    print(f"train: {len(train_ds)} / eval: {len(eval_ds)}")

    # ═══ 训练参数 ═══
    common_kwargs = dict(
        output_dir=args.output_dir,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        num_train_epochs=args.epochs,
        learning_rate=args.lr,
        warmup_ratio=0.03,
        logging_steps=10,
        save_steps=200,
        save_total_limit=3,
        fp16=not use_bf16,
        bf16=use_bf16,
        optim="paged_adamw_8bit",
        gradient_checkpointing=True,
        report_to="none",
    )

    if SFTConfig is not None:
        cfg_params = inspect.signature(SFTConfig.__init__).parameters
        # trl<0.20 用 max_seq_length，>=0.20 改名为 max_length
        seq_kw = "max_seq_length" if "max_seq_length" in cfg_params else "max_length"
        training_args = SFTConfig(
            dataset_text_field="text",
            **{seq_kw: args.max_seq_len},
            **common_kwargs,
        )
        trainer_kwargs = {}
    else:
        training_args = TrainingArguments(**common_kwargs)
        trainer_kwargs = {
            "dataset_text_field": "text",
            "max_seq_length": args.max_seq_len,
        }

    # trl>=0.12 用 processing_class，旧版用 tokenizer
    trainer_params = inspect.signature(SFTTrainer.__init__).parameters
    tok_kw = "processing_class" if "processing_class" in trainer_params else "tokenizer"

    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        **{tok_kw: tokenizer},
        **trainer_kwargs,
    )

    resume_ckpt = None
    if args.resume:
        from transformers.trainer_utils import get_last_checkpoint
        resume_ckpt = get_last_checkpoint(args.output_dir)
        print(f"断点续训: {resume_ckpt}" if resume_ckpt
              else "未找到 checkpoint，从头开始训练")
    trainer.train(resume_from_checkpoint=resume_ckpt)
    trainer.save_model(args.output_dir)
    tokenizer.save_pretrained(args.output_dir)
    print(f"Adapter 已保存到 {args.output_dir}")


if __name__ == "__main__":
    main()
