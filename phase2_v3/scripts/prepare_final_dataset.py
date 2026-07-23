#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
最终训练集准备 (prepare_final_dataset.py)
==========================================
按"知识库驱动+轻量适配"路线合并数据：
- Excel/Python数据：数据处理能力主体
- 审计v2数据：变体扩展（非盲目复制）至适度占比
- 风格数据：审计表达风格

输出：
  data/finetune/final_train.jsonl  （训练集）
  data/finetune/final_val.jsonl    （验证集，全部为审计数据，监控过拟合）

推荐超参：learning_rate=1e-4, epochs=2, 早停看val loss
"""
import json, random
from pathlib import Path

random.seed(42)
BASE = Path(__file__).resolve().parent.parent / "data" / "finetune"
SYN = BASE / "synthetic"

def load(fp):
    if not fp.exists(): return []
    return [json.loads(l) for l in open(fp, encoding="utf-8") if l.strip()]

# 1. 加载各数据源
excel = load(SYN / "excel_dataset_cleaned_chatml.jsonl")
audit = load(SYN / "auto_generated_v2.jsonl")
style = load(SYN / "style_data.jsonl")
# Python数据（如已转换）
python_data = load(SYN / "python_dataset_chatml.jsonl")

print(f"Excel: {len(excel)} | Python: {len(python_data)} | 审计v2: {len(audit)} | 风格: {len(style)}")

# 2. 审计数据切验证集（20%），剩余用于训练
random.shuffle(audit)
val_size = max(10, len(audit) // 5)
audit_val = audit[:val_size]
audit_train = audit[val_size:]

# 3. 审计训练数据适度上采样（10倍，非30倍——配合风格数据稀释）
audit_upsampled = audit_train * 10
style_upsampled = style * 5   # 风格数据×5

# 4. 代码数据总量控制：审计+风格数据的2倍以内（防止淹没）
audit_total = len(audit_upsampled) + len(style_upsampled)
code_cap = audit_total * 2
code_pool = excel + python_data
random.shuffle(code_pool)
code_selected = code_pool[:code_cap]

# 5. 合并打乱
train = audit_upsampled + style_upsampled + code_selected
random.shuffle(train)

# 6. 输出
train_fp = BASE / "final_train.jsonl"
val_fp = BASE / "final_val.jsonl"
with open(train_fp, "w", encoding="utf-8") as f:
    for p in train:
        f.write(json.dumps(p, ensure_ascii=False) + "\n")
with open(val_fp, "w", encoding="utf-8") as f:
    for p in audit_val:
        f.write(json.dumps(p, ensure_ascii=False) + "\n")

print(f"""
═══ 最终配比 ═══
审计数据(x10):  {len(audit_upsampled)} 条
风格数据(x5):   {len(style_upsampled)} 条
代码数据(截取): {len(code_selected)} 条
─────────────────
训练集合计:     {len(train)} 条 -> {train_fp.name}
验证集(纯审计): {len(audit_val)} 条 -> {val_fp.name}

═══ 推荐训练超参 ═══
learning_rate = 1e-4   (原2e-4减半, 防过拟合)
num_train_epochs = 2
eval_strategy = "steps", eval_steps = 100
load_best_model_at_end = True  (按val loss选最优checkpoint)
""")
