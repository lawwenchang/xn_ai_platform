# Dify 语义编译器配置指南（v3.0）

> 本指南描述如何在 Dify 平台上配置"语义编译器"工作流。
> 与 v2 的本质区别：v2 输出静态蓝图，v3 输出动态 DAG JSON（含算子拓扑）。
> 算子拓扑示例：`Load → RegexFilter → GroupBy → ConditionCheck → Extract`

---

## 前置准备

### 1. SSH 隧道建立

在本地服务器终端执行：

```bash
# 将 AutoDL 上的 vLLM（端口 8000）映射到本地 18000
ssh -N -L 18000:localhost:8000 root@connect.autodl.com -p <你的SSH端口>

# 验证
curl http://localhost:18000/v1/models
```

### 2. Dify 配置 vLLM 模型供应商

1. Dify 控制台 → **设置** → **模型供应商**
2. 点击 **OpenAI-API-compatible**
3. 填写：

| 配置项 | 值                           |
|--------|-----------------------------|
| 模型名称 | `qwen3-235b`                   |
| API Key | `EMPTY`                     |
| API Endpoint | `http://localhost:18000/v1` |
| 最大 Token | 4096                        |

---

## 工作流一：语义编译器（dag_compiler_v3）

### 第 1 步：创建工作流

1. 工作室 → 创建空白应用 → **工作流编排**
2. 名称：`语义编译器`
3. 标识：`dag_compiler_v3`

### 第 2 步：开始节点输入

| 变量名 | 类型 | 必填 |
|--------|------|------|
| `catalog_text` | 字符串 | 是 | Data Catalog 文本（表头元数据）
| `user_intent` | 字符串 | 是 | 大白话审计意图
| `preset_button` | 字符串 | 否 | 预设按钮名称
| `parent_summary` | 字符串 | 否 | 父 Run 的结构化摘要

### 第 3 步：条件分支（是否使用预设按钮）

**节点**：条件分支
- IF：`{{#start.preset_button#}}` 不为空
- ELSE：走自由编译模式

### 第 4 步：LLM 节点 — 语义编译核心

**IF 分支（预设按钮模式）**：
- 系统 Prompt：复制 `dify/preset_prompts.md` 中对应场景的完整 Prompt（医保对账 / 银行流水核对 / 大额交易筛查三选一）
- 注意：预设模式下算子拓扑是**固定模板**（不能自由规划），大模型只负责填充 params 和列名映射

**ELSE 分支（自由编译模式）**：

**系统 Prompt**（完整版）：

```
你是「审计业务逻辑编译器」。你的任务是将审计师的自然语言意图 + 数据结构描述，
编译为可执行的 Pandas DAG（有向无环图）。

## 核心规则
1. 【拒绝关键字匹配】不要看到"医保"就调用医保模块。必须深度理解全量语义。
2. 【自主规划 DAG】根据意图自主规划算子拓扑，如：
   "把摘要带医保的行拉出来，超过50万的月份单独导出"
   → Load → RegexFilter('医保') → GroupBy(月份, 金额Sum) → ConditionCheck(>500000) → Extract

## 可用算子清单
- Load: 读取数据文件
- RegexFilter: 正则表达式筛选（column, pattern, case_sensitive）
- ColumnFilter: 列值筛选（column, operator, value）
- GroupBy: 分组聚合（columns, aggregations）
- Merge: 合并多数据源（left, right, on, how）
- Sort: 排序（columns, ascending）
- ConditionCheck: 条件检查（column, operator, value）
- Extract: 提取子集（满足条件的行）
- Transform: 数据变换（新增列、类型转换）
- NoiseFilter: 噪音过滤（keywords, patterns）
- Aggregate: 汇总统计（functions）
- Diff: 差异比对（left, right, keys）
- Export: 导出结果（format, filename）
- Reconcile: 对账匹配（strategy, tolerance）
- AuditAdjustment: 生成审计调整分录

## 输出格式（严格 JSON）
{
  "blueprint_id": "bp_{timestamp}_{random}",
  "generated_at": "ISO时间戳",
  "run_id": null,
  "objective": "编译后的目标描述",
  "raw_intent": "原始大白话",
  "confidence_score": 0.0-1.0,
  "operators": [
    {
      "id": "op_1",
      "name": "Load",
      "description": "读取银行流水",
      "input_from": [],
      "source_file": "银行流水.xlsx",
      "params": {},
      "output_alias": "bank_flow",
      "audit_context": {}
    },
    {
      "id": "op_2",
      "name": "RegexFilter",
      "description": "筛选医保相关行",
      "input_from": ["op_1"],
      "params": {"column": "摘要", "pattern": "医保|统筹|YBTD", "case_sensitive": false},
      "output_alias": "medical_rows",
      "audit_context": {"risk_note": "需确认正则是否覆盖全部医保标识"}
    }
  ],
  "context": {
    "column_mappings": {},
    "tolerance": {"type": "RELATIVE", "value": 0.005},
    "noise_rules": {"exclude_keywords": ["手续费", "短信费"]},
    "policy_hints": {}
  },
  "risk_alerts": [],
  "human_review_points": ["至少2条复核点"],
  "expected_outputs": [
    {"type": "excel", "filename": "医保大额明细.xlsx", "description": "超过50万的月份明细"}
  ]
}

## 思考链（Chain-of-Thought）
输出 JSON 前，必须完成：
1. 识别审计目标（筛选/对账/汇总/导出？）
2. 分析 Data Catalog 中的列名和数据类型
3. 规划算子拓扑（哪些算子、什么顺序）
4. 为每个算子填写 params
5. 标记风险点和人工复核点

## 安全约束
- 只输出 JSON，不输出其他文字
- 引用的列名必须来自 Data Catalog
- confidence_score < 0.5 时 human_review_points 至少 4 条
```

**用户 Prompt**：

```
## 数据目录
{{#start.catalog_text#}}

## 审计意图
{{#start.user_intent#}}

{{#start.parent_summary#}}
```

**参数**：温度 0.3，输出格式 JSON

### 第 5 步：代码节点 — DAG 校验

```python
def main(dag_json_str: str) -> dict:
    import json
    cleaned = dag_json_str.strip()
    if cleaned.startswith("```json"): cleaned = cleaned[7:]
    if cleaned.startswith("```"): cleaned = cleaned[3:]
    if cleaned.endswith("```"): cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    
    errors = []
    try:
        dag = json.loads(cleaned)
    except Exception as e:
        return {"is_valid": False, "errors": [f"JSON解析失败: {e}"], "dag_json": cleaned}
    
    valid_ops = {"Load", "RegexFilter", "ColumnFilter", "GroupBy", "Merge", "Sort",
                 "ConditionCheck", "Extract", "Transform", "NoiseFilter", "Aggregate",
                 "Diff", "Export", "Reconcile", "AuditAdjustment"}
    
    for i, op in enumerate(dag.get("operators", [])):
        if "id" not in op: errors.append(f"operators[{i}]: 缺少id")
        if "name" not in op: errors.append(f"operators[{i}]: 缺少name")
        elif op["name"] not in valid_ops: errors.append(f"operators[{i}]: 未知算子 {op['name']}")
    
    score = dag.get("confidence_score", 0)
    if not (0 <= score <= 1): errors.append(f"confidence_score 越界: {score}")
    if not dag.get("human_review_points"): errors.append("human_review_points 不能为空")
    
    return {"is_valid": len(errors) == 0, "errors": errors, "dag_json": json.dumps(dag, ensure_ascii=False)}
```

### 第 6 步：结束节点

- `dag_json` ← 校验通过的 JSON
- `status` = "success" / "validation_failed"

### 第 7 步：保存发布

1. 右上角 **发布**
2. **API 访问** → 记下 API Key

---

## 测试

**测试输入**：
```json
{
  "catalog_text": "全局哈希: abc123\n文件总数: 1\n总大小: 5.2 MB\n\n=== 文件清单 ===\n文件: 银行流水.xlsx (5420000 bytes)\n  列: 交易日期 | 类型: datetime64 | 空值: 0 | 唯一值: 180\n  列: 摘要 | 类型: object | 空值: 0 | 唯一值: 5230\n  列: 收入金额 | 类型: float64 | 空值: 45000 | 唯一值: 4230\n  列: 支出金额 | 类型: float64 | 空值: 52000 | 唯一值: 3800",
  "user_intent": "帮我把摘要带'医保回款'的行拉出来，如果某个月的总金额超过了50万，把这个月的所有明细单独导出来"
}
```

**期望输出**：DAG JSON，包含 Load → RegexFilter → GroupBy → ConditionCheck → Extract 算子链。

---

## 十、v3.1 修订记录（2026-07-18 架构评审落地）

1. **分支合流反模式修复**：`语义编译器.yml` 原先「LLM预设」「LLM自由」两个互斥分支直接汇入同一个代码节点、且代码节点同时引用两个分支的输出变量（Dify 已知风险，见 langgenius/dify Discussion #38799：被跳过分支的变量不存在，会触发 Variable Not Found）。现已插入**变量聚合器（branch_aggregator）**节点做互斥分支合流，代码节点只引用聚合器的单一输出。**需在 Dify 中重新导入本 YAML 生效。**
2. **推理参数规范化**：移除无效的 `reasoning_effort`（OpenAI o 系列专用参数，vLLM/Qwen 不识别），统一为 `enable_thinking`；知识问答工作流改为 `enable_thinking: false` 并在用户提示词尾部追加 `/no_think` 软开关（Qwen3 混合思考模型双保险，降低问答延迟）。
3. **DAG 精修工作流接线**：后端 `_call_dify_compiler` 已内置「Schema 强校验（环/悬空引用/重复ID/文件存在性）→ rule_fix_dag 规则修复（零 LLM）→ DAG 精修工作流（≤2 轮）→ 三级熔断」循环。启用 LLM 精修需配置环境变量 `DIFY_REFINE_API_KEY`（在 Dify「DAG精修与纠错」应用的 API 访问页生成）；未配置时自动跳过精修、直接走降级链，不影响可用性。
4. **思考块防御**：语义编译器与 DAG 精修的代码校验节点均增加 `<think>…</think>` 剥离逻辑，兼容 thinking 模型（含只输出闭合标签的 Thinking-2507 风格）与未配置 reasoning parser 的 vLLM 部署。

