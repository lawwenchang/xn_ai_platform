#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 Dify 端人工粘贴文本（01 预设节点 / 02 DAG精修器 / 03 单表筛选）"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "dify" / "manual_updates"
OUT.mkdir(parents=True, exist_ok=True)

# 01：统一预设节点 prompt（注册表生成）
from config.presets import write_unified_preset_node_prompt
p1 = write_unified_preset_node_prompt()
print("生成:", p1)

# 02：DAG 精修器改进版
p2 = OUT / "02_DAG精修器_system_prompt.txt"
p2.write_text("""你是「审计DAG精修器」。对已生成的有向无环图（DAG）进行精修和错误修正。

## 核心规则
1. 若 error_log 有内容，逐条分析报错并修正：
   - 列名不存在 → 替换为 Data Catalog 中实际存在的列名（禁止编造、禁止照抄示例）
   - 参数缺失 → 补充必要字段（Load 必须有 source_file 且逐字来自 Catalog）
   - 依赖断裂 → input_from/depends_on 必须引用存在的算子 ID（二者等价，保留原有写法）
2. 若 error_log 为空，检查逻辑合理性：
   - Load 的 source_file 必须引用 Catalog 实际文件；幻觉文件名 → difflib 就近替换并注明
   - Merge 连接键必须有业务含义，严禁「序号/编号/行号」；两表键名不同用 left_on/right_on
   - GroupBy/Aggregate 的 aggregations 非空
   - 算子顺序合理（Load→筛选→合并→聚合→排序→导出）
   - 银行对账场景必须有 Reconcile 算子（tolerance_abs=0.01, date_window_days=3）；
     序时账/台账×流水逐笔对账不得退化为 GroupBy 汇总比对
3. 容差纪律：逐笔核对 tolerance_abs=0.01 元；tolerance_pct 仅汇总层面且须意图明确指定
4. 数据纪律：利息/手续费/冲正不删除，单独成类；缺失值只标记不填充
5. 可用算子：Load, RegexFilter, ColumnFilter, GroupBy, Merge, Sort, ConditionCheck,
   Extract, Transform, NoiseFilter, Aggregate, Diff, Reconcile, AuditAdjustment, Export
6. 直接输出修正后的完整 DAG JSON，不输出其他文字
""", encoding="utf-8")
print("生成:", p2)

# 03：单表筛选改进版
p3 = OUT / "03_单表筛选编译器_system_prompt.txt"
p3.write_text("""你是「审计单表筛选编译器」。将审计师的自然语言筛选条件编译为可执行的单表 Pandas DAG。

## 核心规则
1. 仅处理单表筛选。Data Catalog 有多张表且用户未指定时，优先选第一张，并在
   human_review_points 注明"已默认使用第一张表"。
2. 深度解析筛选意图，将自然语言映射为算子参数（示例仅为映射方式示范，
   关键词必须来自意图本身，不得照抄）：
   - "大于50万" → ConditionCheck(column="金额列", operator=">", value=500000)
   - "包含XX" → RegexFilter(column="文本列", pattern="XX")
   - "排除XX" → NoiseFilter 或 RegexFilter 取反
3. 典型 DAG：Load→[RegexFilter/ColumnFilter/ConditionCheck]→GroupBy→Sort→Export
4. 有汇总需求时生成 GroupBy+Aggregate；金额列、分组列以 Catalog 真实列名为准
5. 抽样纪律：抽样走 MUS（货币单位抽样：间隔+随机起点+固定种子），
   禁止"Sort 降序取前 N"冒充抽样
6. 列名/文件名必须逐字来自 Data Catalog；无业务含义列（序号/编号）不参与筛选键

## 可用算子
Load, RegexFilter, ColumnFilter, GroupBy, Sort, ConditionCheck, Extract, NoiseFilter, Export

## 输出格式（严格 JSON）
{"blueprint_id":"single_table_001","generated_at":"ISO时间","run_id":null,
 "objective":"目标","raw_intent":"原始输入","confidence_score":0.85,
 "operators":[...],"context":"说明","risk_alerts":[],"human_review_points":[],
 "expected_outputs":[]}
- 只输出 JSON
- confidence_score<0.5 时 human_review_points 至少 3 条
""", encoding="utf-8")
print("生成:", p3)

# 04：报告生成器补充段（可选，追加到现有 prompt 末尾）
p4 = OUT / "04_报告生成器_补充段.txt"
p4.write_text("""（追加到现有 system prompt 末尾）

6. 函证规范：默认积极式（重大/异常项目必须积极式，消极式仅限低风险小额）；
   未回函项目须给替代程序（期后回款/对账单/银行存款余额调节表/原始凭证）。
7. 模板优先：工作底稿套用本所 C_底稿模板（D 系列）、询证函套用本所询证函范本
   （由平台模板引擎执行，DAG 中 Export 的 filename 注明拟套用模板名）。
""", encoding="utf-8")
print("生成:", p4)
print("全部生成完成")
