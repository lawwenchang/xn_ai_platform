# QLoRA 微调实施方案

> 硬件：AutoDL RTX PRO 6000 96GB | 基座：Qwen2.5-32B-Instruct

---

## 一、QLoRA 核心原理（30秒版）

```
全量微调 32B：需要 ~64GB 显存（存不下）
QLoRA 微调 32B：~18GB 显存（96GB 绰绰有余）

做到这一点的三个技术：
1. 4-bit NF4 量化     → 把 32B 参数从 64GB 压到 ~8GB
2. Double Quantization → 量化常数再量化，省 ~0.4GB
3. LoRA 旁路矩阵       → 只在量化模型旁边加两条小矩阵训练
                         trainable: 65M / 32B = 0.2%
```

---

## 二、Qwen2.5-32B QLoRA 关键参数

### 2.1 target_modules（必须精准）

Qwen2.5 的注意力层和 FFN 层名称：

```python
target_modules = [
    "q_proj", "k_proj", "v_proj", "o_proj",      # 注意力投影
    "gate_proj", "up_proj", "down_proj",           # FFN 门控+投影
]
```

### 2.2 LoRA 超参

```python
lora_config = LoraConfig(
    r=16,              # rank：16（通用）或 64（高精度）
    lora_alpha=32,     # scaling：通常 r*2
    lora_dropout=0.05, # 防止过拟合
    target_modules=target_modules,
    bias="none",
    task_type="CAUSAL_LM",
)
```

### 2.3 量化配置

```python
bnb_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type="nf4",        # NormalFloat4
    bnb_4bit_compute_dtype=torch.float16,
    bnb_4bit_use_double_quant=True,   # 双重量化
)
```

### 2.4 训练参数

```python
training_args = TrainingArguments(
    output_dir="./qlora_audit_v1",
    per_device_train_batch_size=2,     # 96GB 可以开到 4
    gradient_accumulation_steps=8,     # effective batch = 2*8 = 16
    num_train_epochs=3,
    learning_rate=2e-4,
    warmup_ratio=0.03,
    logging_steps=10,
    save_steps=200,
    save_total_limit=3,
    fp16=True,
    optim="paged_adamw_8bit",
)
```

---

## 三、训练数据格式：ChatML

Qwen2.5 原生格式：

```json
{
  "messages": [
    {
      "role": "system",
      "content": "你是审计 DAG 编译器。将审计师意图编译为 DAG JSON。可用算子：Load/RegexFilter/Merge/Diff/Export。只输出 JSON。"
    },
    {
      "role": "user",
      "content": "## 审计意图\n帮我核对医保回款，差异控制在5万以内\n\n## 数据目录\n银行流水(摘要/金额/对方户名)..."
    },
    {
      "role": "assistant",
      "content": "{\"objective\": \"医保回款核对\", \"operators\": [...]}"
    }
  ]
}
```

### 三类训练数据来源

| 来源 | 数量目标 | 内容 |
|------|---------|------|
| **合成数据** | 500-1000条 | 质控合伙人手写"意图→正确答案"对 |
| **自动采集** | 随使用积累 | 审计师纠正AI的记录 |
| **知识库衍生** | 200条 | 从知识库准则条文 + 模拟问题生成 |

---

## 四、完整训练脚本

```python
# train_qlora.py — 在 AutoDL 上运行

import torch, json, os
from transformers import (
    AutoModelForCausalLM, AutoTokenizer,
    BitsAndBytesConfig, TrainingArguments,
)
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from datasets import Dataset
from trl import SFTTrainer

# ═══ 配置 ═══
BASE_MODEL = "Qwen/Qwen2.5-32B-Instruct"
OUTPUT_DIR = "/root/audit_lora_v1"
DATA_FILE = "/root/training_data.jsonl"

# ═══ 量化 ═══
bnb_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type="nf4",
    bnb_4bit_compute_dtype=torch.float16,
    bnb_4bit_use_double_quant=True,
)

# ═══ 加载模型 ═══
model = AutoModelForCausalLM.from_pretrained(
    BASE_MODEL,
    quantization_config=bnb_config,
    device_map="auto",
    trust_remote_code=True,
)
tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL, trust_remote_code=True)
tokenizer.pad_token = tokenizer.eos_token

model = prepare_model_for_kbit_training(model)

# ═══ LoRA ═══
lora_config = LoraConfig(
    r=16, lora_alpha=32, lora_dropout=0.05,
    target_modules=["q_proj","k_proj","v_proj","o_proj",
                    "gate_proj","up_proj","down_proj"],
    bias="none", task_type="CAUSAL_LM",
)
model = get_peft_model(model, lora_config)
model.print_trainable_parameters()

# ═══ 加载数据 ═══
def load_jsonl(path):
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            records.append(json.loads(line))
    return records

records = load_jsonl(DATA_FILE)
print(f"训练数据: {len(records)} 条")

# ChatML 格式化
def format_chatml(record):
    text = ""
    for msg in record["messages"]:
        text += f"<|im_start|>{msg['role']}\n{msg['content']}<|im_end|>\n"
    text += "<|im_start|>assistant\n"
    return text

formatted = [format_chatml(r) for r in records]

# 切分
split = int(len(formatted) * 0.95)
train_ds = Dataset.from_dict({"text": formatted[:split]})
eval_ds = Dataset.from_dict({"text": formatted[split:]})

# ═══ 训练 ═══
training_args = TrainingArguments(
    output_dir=OUTPUT_DIR,
    per_device_train_batch_size=2,
    gradient_accumulation_steps=8,
    num_train_epochs=3,
    learning_rate=2e-4,
    warmup_ratio=0.03,
    logging_steps=10,
    save_steps=200,
    save_total_limit=3,
    fp16=True,
    optim="paged_adamw_8bit",
    report_to="none",
)

trainer = SFTTrainer(
    model=model,
    args=training_args,
    train_dataset=train_ds,
    eval_dataset=eval_ds,
    tokenizer=tokenizer,
    max_seq_length=4096,
)

trainer.train()
model.save_pretrained(OUTPUT_DIR)
tokenizer.save_pretrained(OUTPUT_DIR)
print(f"Adapter 已保存到 {OUTPUT_DIR}")
```

---

## 五、训练时间与资源

| 数据量 | 显存占用 | 时间 |
|--------|---------|------|
| 500条 | ~18GB | 3-4 小时 |
| 1000条 | ~18GB | 6-8 小时 |
| 2000条 | ~18GB | 12-16 小时 |

96GB 显存可以开 `batch_size=4` 把时间缩短约 30%。

---

## 六、部署：vLLM 加载 Adapter

```bash
# 训练产出：adapter_model.safetensors（~300MB）

python -m vllm.entrypoints.openai.api_server \
    --model Qwen/Qwen2.5-32B-Instruct \
    --lora-modules audit-v1=/root/audit_lora_v1 \
    --max-lora-rank 16 \
    --port 8000
```

调用时指定：
```json
{
    "model": "Qwen2.5-32B-Instruct",
    "lora_request": {"lora_name": "audit-v1"}
}
```

---

## 七、你现在需要做的

| 步骤 | 做什么 | 谁来做 |
|------|--------|--------|
| 1 | 准备 500-1000 条 ChatML 训练数据 | 质控合伙人 + 你 |
| 2 | 在 AutoDL 上安装环境 | 你 |
| 3 | 上传数据，运行 train_qlora.py | 你 |
| 4 | vLLM 加载 adapter，灰度上线 | 你 |
