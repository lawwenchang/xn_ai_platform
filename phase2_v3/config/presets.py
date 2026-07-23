#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
预设按钮统一注册表 (presets.py) —— 单一事实来源
==================================================
此前平台存在四套互不相通的预设定义（前端按钮 / dag_compiler.PRESET_BUTTONS /
fallback_prompts.py / dify preset_prompts.md），按钮值传到 Dify 后无对应分支，
固化准则永远不会触发。本注册表取代全部四处定义：

消费方（全部改为"读取"，不再各自定义）：
1. 前端按钮          → GET /api/v3/presets 拉取本表 public_list()
2. dag_compiler      → PRESET_BUTTONS 由本表派生（保留原接口）
3. fallback_prompts  → 降级 prompt 由本表 fallback_prompt 字段提供
4. dify/preset_prompts.md → 由 render_dify_md() 从本表生成（python -m config.presets）

新增/修改预设：只改本文件，然后运行 python -m config.presets 重新生成 md。
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional

# ═══════════════════════════════════════════════════════════════
# 预设定义（6 个 DAG 预设 + 1 个特殊路由）
# ═══════════════════════════════════════════════════════════════

PRESETS: Dict[str, Dict[str, Any]] = {

    "银行对账": {
        "label": "银行流水对账", "icon": "🏦",
        "scenario": "bank_reconcile_detail",
        "engine": "bank_reconcile_engine",
        "aliases": ["银行流水核对", "银行核对", "银企对账"],
        "default_operators": ["Load", "Load", "Reconcile", "Export"],
        "system_suffix": """
你是银行对账专家。必须遵守：
1. 方向镜像：序时账/台账 借方−贷方 ↔ 流水 贷方（收入）−借方（支取），归一后同号勾对
2. 流水含多账户时，先按银行账号过滤到与账方同一账户
3. 逐笔核对精确到分（tolerance_abs=0.01 元），禁止百分比容差
4. 匹配层级：金额精确+同日 → 金额精确+日期窗口(±3天) → n:m 拆分合并 → 模糊（仅人工复核）
5. 未匹配项默认"待人工核查"；未达账项按四分类标注且需期后到账验证
6. 利息/手续费/冲正不删除，单独成类
7. 交付：逐笔对账明细底稿 + 银行存款余额调节表 + 未达/待核查清单 + 异常资金交易清单
""",
        "review_points": [
            "请确认对账银行账号（流水含多账户时必填）",
            "未达账项候选需次月流水期后验证后方可确认为时间性差异",
            "调节表'调节差异'非零时说明仍有未解释差异，必须查明",
            "L4 模糊匹配仅人工复核，不自动确认",
        ],
        "dify_prompt": """## 预设模式：银行对账

你是银行对账专家。本次编译以下为推荐算子序列，可根据数据实际情况增加预处理算子（NoiseFilter/Sort等），但核心算子必须包含。

### 推荐算子序列（核心算子必须包含，可按需增加预处理）
Step 1: Load — 读取序时账/台账（账方）
Step 2: Load — 读取银行流水
Step 3: Reconcile — 逐笔对账（tolerance_abs=0.01, date_window_days=3）
Step 4: Export — 导出对账底稿

### 固化审计准则（不可偏离）
1. 方向镜像：账方借方金额 ↔ 流水贷方（收入）；账方贷方金额 ↔ 流水借方（支取）
2. 流水含多账户时，params.note 中注明需按银行账号过滤，账号从意图提取
3. 逐笔容差固定 ±0.01 元；日期窗口默认 ±3 天（意图指定则从其值）
4. 未匹配项默认"待人工核查"，不得默认未达账项
5. 利息/手续费/冲正不删除，单独成类
6. 汇总级台账（按年/分类汇总）不适用本预设 → 写入 human_review_points 提示切换"数据比对"

### params 填充规则
- Load[0].source_file：账方文件（含"序时账/日记账/台账/ledger"，或凭证号/借贷双列特征）
- Load[1].source_file：流水文件（含"流水/对账单/银行"，或对方/账号/余额特征）
- Reconcile.params：{"tolerance_abs": 0.01, "date_window_days": 3,
  "note": "方向镜像：序时账借方金额↔流水贷方（收入）；流水多账户时先按银行账号过滤"}

### 必须的人工复核点
1. 请确认对账银行账号（流水含多账户时必填）
2. 未达账项候选需期后到账验证
3. 调节差异非零必须查明
4.【预设模式】逐笔引擎另有专业快车道（bank_reconcile_engine），本 DAG 为轻量版

### 置信度评分
- 0.9-1.0：两表明确识别为 序时账/台账 × 流水，借贷/收支列清晰
- 0.7-0.9：识别成功但方向口径需人工确认
- 0.5-0.7：账簿类型模糊，需人工确认映射
- < 0.5：无法识别两表结构，建议切换自由编译模式""",
    },

    "数据比对": {
        "label": "数据比对与核对", "icon": "🔗",
        "scenario": "summary_compare",
        "engine": None,
        "aliases": ["数据核对", "两表比对", "汇总勾稽"],
        "default_operators": ["Load", "NoiseFilter", "Sort", "Merge", "Diff", "Export"],
        "system_suffix": """
你是一位精通审计数据比对的专家。执行数据比对时必须：
1. 自动识别两表/多表的共有关键列（机构名称、日期、金额等）；
   严禁使用"序号/编号/行号"作为连接键
2. 机构名称"去市县区中心管理后缀"仅用于医保回款场景，其他场景禁止
3. 容差分层：逐笔核对必须精确到分（tolerance_abs=0.01 元）；
   百分比容差（tolerance_pct）仅用于汇总层面，且须审计师明确指定
4. 支持日期窗口匹配（date_window_days，默认±3天）；
   银行对账注意方向镜像（序时账借方↔流水贷方收入）
5. 差异表必须同时列示：仅左方有/仅右方有/双方都有但金额不等 三类
适用场景：往来款对账、台账比对、汇总勾稽、穿行测试等
""",
        "review_points": [
            "请确认连接键的业务含义（禁止序号/行号）",
            "汇总级比对容差由审计师指定，未指定则差异全列示",
        ],
        "dify_prompt": """## 预设模式：数据比对

你是审计数据比对专家。本次编译以下为推荐算子序列，可根据数据实际情况增加预处理算子（NoiseFilter/Sort等），但核心算子必须包含。

### 推荐算子序列（核心算子必须包含，可按需增加预处理）
Step 1: Load — 读取文件A
Step 2: Load — 读取文件B（如有）
Step 3: NoiseFilter — 排除噪音（不含利息/冲正，二者单独成类）
Step 4: Sort — 按日期或关键列排序
Step 5: Merge — 按业务键匹配（严禁序号/行号；left_on/right_on 必须来自 Data Catalog）
Step 6: Diff — 三类差异（仅左/仅右/双方不等）
Step 7: Export — 导出差异表

### 固化审计准则（不可偏离）
1. 连接键必须有业务含义；两表键名不同用 left_on/right_on 显式映射
2. 逐笔容差 tolerance_abs=0.01；汇总容差须意图明确指定方可使用 tolerance_pct
3. 日期窗口 date_window_days 默认 3，真正生效（日期对窗口过滤）
4. 差异表必须含 仅左方有/仅右方有/双方都有但金额不等 三类
5. 汇总级数据先 GroupBy 同维度再比对

### 必须的人工复核点
1. 请确认连接键的业务含义
2. 汇总容差是否经审计师指定
3.【预设模式】序时账×银行流水逐笔对账请用"银行对账"预设

### 置信度评分
- 0.9-1.0：两表键映射清晰，列名来自 Data Catalog
- 0.7-0.9：键映射成功但存在类型转换
- < 0.7：键无法确认，建议自由编译""",
    },

    "提取式核对": {
        "label": "提取式核对", "icon": "🎯",
        "scenario": "filtered_extraction_match",
        "engine": "matching_engine",
        "aliases": ["医保对账", "医保回款", "社保核对", "提取比对", "筛选核对",
                    "专项核对", "补贴核对", "退费核对"],
        "default_operators": ["Load", "RegexFilter", "NoiseFilter", "GroupBy", "Merge", "Diff", "Export"],
        "system_suffix": """
你是专项提取核对专家。必须遵守（此为通用模式：医保回款只是其中一个实例，
任何"从流水/台账中提取某类业务金额再与另一方核对"的场景都适用）：
1. 提取规则确定化：从意图提炼关键词/条件写进 RegexFilter.pattern，
   禁止口头描述；规则留痕（哪一列、哪个关键词命中多少笔）
2. 提取后按对方/机构/月份等维度汇总，再与另一方对应维度比对
3. 未提取部分单列输出，供检查提取规则完整性（未命中≠不存在）
4. 业务特殊规则（如医保 8% 质保金扣留）只作为意图给定的参数处理，
   必须列入 human_review_points 由审计师确认，不得预设默认值
5. 噪音排除不含利息/冲正（单独成类）
6. 逐笔容差 ±0.01 元；汇总容差须审计师明确指定
适用场景：医保/社保/补贴/退费/专项拨款等任意"提取特定业务金额再核对"
""",
        "review_points": [
            "请确认提取关键词/条件是否覆盖全部目标业务（未命中≠不存在）",
            "业务特殊参数（如扣留比例/结算周期）须经审计师确认",
        ],
        "dify_prompt": """## 预设模式：提取式核对（通用）

你是专项提取核对专家。本次编译以下为推荐算子序列，可根据数据实际情况增加预处理算子（NoiseFilter/Sort等），但核心算子必须包含。
注意：这是通用模式——"医保回款"只是其中一个实例；任何
"从流水/台账中提取某类业务金额再与另一方核对"的需求都走本模板。

### 推荐算子序列（核心算子必须包含，可按需增加预处理）
Step 1: Load — 读取资金流水/明细
Step 2: NoiseFilter — 排除噪音（不含利息/冲正）
Step 3: RegexFilter — 按意图提炼的关键词/条件提取目标业务行
Step 4: GroupBy — 按对方/机构/月份等维度汇总
Step 5: Merge — 与另一方（台账/汇总表）按同维度比对
Step 6: Diff — 差异三类（仅左/仅右/双方不等）
Step 7: Export — 导出（差异明细 + 提取命中统计 + 未提取部分）

### 固化审计准则（不可偏离）
1. 提取规则确定化并留痕：哪一列、哪个关键词命中多少笔
2. 业务特殊规则（扣留比例/结算周期等）只接受意图显式给定的值，
   一律写入 human_review_points 由审计师确认，不得预设默认值
3. 未提取部分单列输出，供检查规则完整性
4. 逐笔容差 ±0.01 元；汇总容差须意图明确指定

### params 填充规则
- RegexFilter.column：摘要/用途/对方等多列联合（来自 Data Catalog）
- RegexFilter.pattern：从意图提炼（如 医保|统筹|社保 → 仅当意图确为医保时）
- GroupBy：与另一方语义一致的维度
- Merge/Diff：同维度键比对，严禁序号/行号

### 必须的人工复核点
1. 请确认提取规则覆盖完整性（未提取部分已单列）
2. 业务特殊参数须经审计师确认
3.【预设模式】规则无法确定化时建议切换自由编译

### 置信度评分
- 0.9-1.0：提取规则明确、双方维度一致
- 0.7-0.9：规则可提炼但业务参数待确认
- < 0.5：无法提炼提取规则，建议自由编译""",
    },

    "大额交易筛查": {
        "label": "大额交易筛查", "icon": "💰",
        "scenario": "large_txn_screen",
        "engine": None,
        "aliases": ["大额筛查", "大额交易", "大额流水"],
        "default_operators": ["Load", "NoiseFilter", "ConditionCheck", "GroupBy", "Sort", "Export"],
        "system_suffix": """
你是大额交易筛查专家。必须遵守：
1. 阈值条件化：写进 ConditionCheck（默认单笔 ≥10 万，意图指定则从其值）
2. 风险三级：HIGH 单笔≥500万或日累计≥1000万；MEDIUM 单笔≥100万或周累计≥500万；LOW ≥10万
3. 红旗特征标记：整数大额、一收一付同额、频繁小额试探、非工作时间
4. 结果按风险等级+金额降序
5. 噪音排除：利息/手续费/税费/内部调拨
适用场景：大额资金流水筛查、异常交易识别
""",
        "review_points": [
            "请确认金额阈值 10万/100万/500万 是否符合项目要求",
            "请确认'同一对手'的识别列（对方户名/对方账号）",
            "HIGH 风险交易建议逐笔核查原始凭证",
        ],
        "dify_prompt": """## 预设模式：大额交易筛查

你是大额交易筛查专家。本次编译以下为推荐算子序列，可根据数据实际情况增加预处理算子（NoiseFilter/Sort等），但核心算子必须包含。

### 推荐算子序列（核心算子必须包含，可按需增加预处理）
Step 1: Load — 读取交易流水
Step 2: NoiseFilter — 排除噪音（利息、手续费、税费、内部调拨）
Step 3: ConditionCheck — 筛选 ≥ 10 万（意图指定阈值则从其值）
Step 4: GroupBy — 按交易对手汇总金额和频次
Step 5: ConditionCheck — 风险三级（HIGH/MEDIUM/LOW）
Step 6: Sort — 风险等级 + 金额降序
Step 7: Export — 导出风险清单

### 固化审计准则（不可偏离）
1. HIGH: 单笔 ≥500 万，或同一对手日累计 ≥1000 万
2. MEDIUM: 单笔 ≥100 万，或同一对手周累计 ≥500 万
3. LOW: 单笔 ≥10 万
4. 红旗标记：整数大额 / 一收一付同额 / 频繁小额试探 / 非工作时间

### 必须的人工复核点
1. 请确认金额阈值是否符合项目要求
2. 请确认"同一对手"识别列
3. HIGH 风险交易建议逐笔核查原始凭证
4.【预设模式】异常模式检测为启发式规则，可能有误报

### 置信度评分
- 0.9-1.0：金额列清晰，对手列无空值
- 0.7-0.9：对手列部分空值
- < 0.5：交易结构无法识别，建议自由编译""",
    },

    "智能筛选": {
        "label": "智能筛选与抽样", "icon": "🔍",
        "scenario": "single_table_analysis",
        "engine": "audit_sampling",
        "aliases": ["筛选", "单表筛选", "抽样"],
        "default_operators": ["Load", "RegexFilter", "ColumnFilter", "ConditionCheck", "GroupBy", "Export"],
        "system_suffix": """
你是一位精通智能数据筛选的审计专家。执行筛选时必须：
1. 基于自然语言条件精确筛选记录
2. 支持正则匹配（摘要、对方户名、附言等文本列）
3. 支持金额阈值过滤（大于/小于/区间/百分比）
4. 抽样默认 MUS（货币单位抽样：间隔+随机起点+固定种子可复现；
   零/负金额剔除单独考虑；≥间隔高层项目全部入选）
5. 输出筛选结果 + 汇总统计 + 抽样清单
适用场景：单表筛选、审计抽样、实质性分析、大额交易筛查等
""",
        "review_points": [
            "抽样方法（MUS/简单随机/分层，默认 MUS）",
            "样本评价：tainting 错报推断 + 基本精确度界限",
        ],
        "dify_prompt": """## 预设模式：智能筛选

你是数据筛选专家。本次编译以下为推荐算子序列，可根据数据实际情况增加预处理算子（NoiseFilter/Sort等），但核心算子必须包含。

### 推荐算子序列（核心算子必须包含，可按需增加预处理）
Step 1: Load — 读取数据文件
Step 2: RegexFilter/ColumnFilter — 按条件筛选
Step 3: ConditionCheck — 阈值过滤
Step 4: GroupBy — 汇总统计
Step 5: Sort — 排序
Step 6: Export — 导出筛选结果 + 汇总统计

### 固化审计准则（不可偏离）
1. 筛选条件全部写进 params，禁止口头描述
2. 单表场景不虚构第二表
3. 抽样走 MUS（间隔+随机起点+固定种子），不使用"排序取前 N"

### 必须的人工复核点
1. 筛选条件是否完整覆盖意图
2. 抽样方法与样本量是否符合 CSA 1314

### 置信度评分
- 0.9-1.0：筛选列与条件明确
- < 0.7：条件模糊，建议补充说明""",
    },

    "文档生成": {
        "label": "报告与函证生成", "icon": "📄",
        "scenario": "doc_generation",
        "engine": "template_engine",
        "aliases": ["报告生成", "函证生成", "底稿生成"],
        "default_operators": ["Load", "Aggregate", "Diff", "Export"],
        "system_suffix": """
你是一位精通审计文档生成的专家。生成文档时必须：
1. 基于已审定/已筛选的数据生成报告正文和附注
2. 报告数字必须与底稿审定数同源勾稽，不一致禁止出具
3. 函证默认积极式（重大/异常必须积极式，消极式仅限低风险小额）；
   未回函给替代程序（期后回款/对账单/余额调节表/原始凭证）
4. 优先套用事务所模板（内部知识库 C_底稿模板/询证函范本）
5. 输出 Word/Excel 可下载文件
适用场景：审计报告生成、函证管理、工作底稿生成等
""",
        "review_points": [
            "报告数字与底稿审定数必须一致",
            "函证形式（积极/消极）及未回函替代程序",
        ],
        "dify_prompt": """## 预设模式：文档生成

你是审计文档生成专家。本次编译以下为推荐算子序列，可根据数据实际情况增加预处理算子（NoiseFilter/Sort等），但核心算子必须包含。

### 推荐算子序列（核心算子必须包含，可按需增加预处理）
Step 1: Load — 读取审定数据
Step 2: Aggregate — 汇总关键指标
Step 3: Diff — 与底稿同源勾稽
Step 4: Export — 生成文档（Word/Excel，优先事务所模板）

### 固化审计准则（不可偏离）
1. 报告数字与底稿审定数同源勾稽，不一致禁止出具
2. 函证默认积极式；未回函给替代程序清单
3. 口语表述转规范表述

### 必须的人工复核点
1. 报告数字与底稿一致性
2. 函证形式与替代程序
3.【预设模式】模板套用（C_底稿模板/询证函范本）由平台模板引擎执行

### 置信度评分
- 0.9-1.0：审定数据完整，模板可用
- < 0.7：数据不完整，建议先完成数据加工""",
    },

    "跨文件对比": {
        "label": "跨文件对比", "icon": "📑",
        "scenario": "cross_doc_compare",
        "engine": "document_loader",
        "aliases": ["跨文档比对", "多文件对比", "文档对比", "文件比对"],
        "default_operators": ["Load", "Load", "Diff", "Export"],
        "system_suffix": """
你是跨文件数据对比专家。必须遵守（xlsx/csv/docx/pdf/md/txt 任意混合）：
1. 文档中的表格与 Excel 同权参与比对（平台自动提取 docx/pdf/md 内嵌表格）
2. 先确认双方用于比对的列/键语义一致（严禁序号/行号）
3. 逐笔容差 ±0.01 元；汇总容差须审计师指定
4. 文本部分按段落/关键词比对，差异标注来源文件
5. 差异表必须同时列示：仅左方有/仅右方有/双方都有但不一致 三类
适用场景：台账.xlsx×报告.pdf、合同.docx×台账.xlsx、两版底稿差异等
""",
        "review_points": [
            "请确认两文件比对的键/列语义一致",
            "扫描件 PDF 无文本层时提示需 OCR",
        ],
        "dify_prompt": """## 预设模式：跨文件对比

你是跨文件数据对比专家。本次编译以下为推荐算子序列，可根据数据实际情况增加预处理算子（NoiseFilter/Sort等），但核心算子必须包含。
支持 xlsx/csv/docx/pdf/md/txt 任意混合（文档表格与 Excel 同权）。

### 推荐算子序列（核心算子必须包含，可按需增加预处理）
Step 1: Load — 读取文件A（任意支持格式）
Step 2: Load — 读取文件B（任意支持格式）
Step 3: Diff — 按键比对（三类差异：仅左/仅右/双方不等）
Step 4: Export — 导出差异表（标注来源文件）

### 固化审计准则（不可偏离）
1. 键/列语义必须一致，不同名用 keys/left_on/right_on 显式映射
2. 逐笔容差 ±0.01 元；汇总容差须意图指定
3. 差异表必须标注每条差异的来源文件
4. 文本型内容按段落/关键词比对

### params 填充规则
- Load.source_file：必须逐字来自 Data Catalog（含 docx/pdf/md/txt）
- Diff.keys：业务键（禁止序号/行号）；金额列不同名时 col_a/col_b 显式指定

### 必须的人工复核点
1. 请确认比对键/列语义
2. 扫描件 PDF 提示需 OCR
3.【预设模式】纯文本差异（无表格）建议自由编译

### 置信度评分
- 0.9-1.0：双方表格结构清晰、键映射明确
- 0.7-0.9：文档表格提取成功但键需确认
- < 0.5：文档无可用表格，建议自由编译
- ⚠️ 注意：confidence_score 为必填字段，必须输出具体数值（如 0.85），禁止填空值或 0。仅1个源文件且两 Load 引同一文件时，最高不超过 0.60。""",
    },

    # ── 特殊路由（不走 DAG 编译，前端跳格式规范 Tab） ──────────
    "格式与纠错": {
        "label": "格式规范与纠错", "icon": "✨",
        "scenario": None, "engine": "format_engine",
        "aliases": ["格式纠错", "格式规范化", "校对"],
        "dag": False,
        "special_route": "format_normalize",
        "system_suffix": "",
        "default_operators": [],
        "review_points": [],
        "dify_prompt": "",
    },
}



# ═══════════════════════════════════════════════════════════════
# 接口函数
# ═══════════════════════════════════════════════════════════════

# 别名 → 规范 key（含旧 Dify 预设名兼容："医保对账"→"医保回款核对" 等）
_ALIAS_MAP: Dict[str, str] = {}
for _key, _p in PRESETS.items():
    _ALIAS_MAP[_key] = _key
    for _a in _p.get("aliases", []):
        _ALIAS_MAP[_a] = _key


def normalize_preset_key(name: Optional[str]) -> Optional[str]:
    """任意别名/旧 key → 规范 key；无法识别返回 None（走自由编译）"""
    if not name:
        return None
    name = str(name).strip()
    return _ALIAS_MAP.get(name)


def get_preset(name: Optional[str]) -> Optional[Dict[str, Any]]:
    """取预设配置（自动别名归一）"""
    key = normalize_preset_key(name)
    return PRESETS.get(key) if key else None


def is_dag_preset(name: Optional[str]) -> bool:
    """该预设是否走 DAG 编译（"格式与纠错"等特殊路由返回 False）"""
    p = get_preset(name)
    return bool(p and p.get("dag", True))


def public_list() -> List[Dict[str, str]]:
    """前端按钮列表（GET /api/v3/presets 的响应体）"""
    out = []
    for key, p in PRESETS.items():
        out.append({
            "value": key, "label": p["label"], "icon": p["icon"],
            "scenario": p.get("scenario") or "",
            "dag": bool(p.get("dag", True)),
            "special_route": p.get("special_route", ""),
        })
    return out


def all_keys_with_aliases() -> Dict[str, str]:
    """别名映射全表（dag_compiler/fallback_prompts 兼容层用）"""
    return dict(_ALIAS_MAP)


# ═══════════════════════════════════════════════════════════════
# Dify preset_prompts.md 生成器（python -m config.presets 重新生成）
# ═══════════════════════════════════════════════════════════════

_MD_HEADER = """# Dify 预设按钮模式 System Prompt

> ⚠️ 本文件由 `config/presets.py` 生成（python -m config.presets），请勿手改。
> 这些 Prompt 用于 Dify 工作流中 **IF(preset_button 不为空)** 分支的 LLM 节点。
> 与自由编译模式的区别：大模型不能自由规划算子拓扑，必须严格使用预设序列，
> 但每个算子的 params 根据 Data Catalog 和意图动态填充。

---

## 通用约束（所有预设模式共享）

```
你是「审计业务逻辑编译器 —— 预设模式」。

## 核心规则
1.【强制模板】严格使用下方指定算子序列，不能增删算子，不能改变顺序。
2.【参数填充】params 必须根据 Data Catalog 中的实际列名和审计意图动态填写。
3.【列名对齐】params 引用的列名必须逐字来自 Data Catalog，不能编造、不能照抄示例。
4.【键纪律】严禁把"序号/编号/行号"用作连接键。
5.【拒绝自由发挥】超出模板能力的需求写入 human_review_points，提示审计师人工处理。
6.【输出格式】严格的 DAG JSON，与自由编译模式 Schema 一致。

## 输出格式
{
  "blueprint_id": "bp_{timestamp}_{random}",
  "generated_at": "ISO时间戳",
  "run_id": null,
  "objective": "编译后的目标描述（包含预设模式标识）",
  "raw_intent": "原始大白话",
  "confidence_score": 0.0-1.0,
  "operators": [{"id": "op_N", "name": "算子名", "description": "...",
                 "input_from": ["上游op_id"], "source_file": "数据来源文件名",
                 "params": {}, "output_alias": "别名"}],
  "context": {},
  "risk_alerts": [],
  "human_review_points": ["至少2条"],
  "expected_outputs": []
}
```

## 预设模式路由（preset_button 取值 → 章节）

| preset_button | 预设章节 |
|---|---|
__ROUTE_TABLE__

> 兼容别名：__ALIAS_NOTE__（均归一到对应章节）。
> 无法识别的值 → 按自由编译模式处理（ELSE 分支）。

---
"""

_MD_FOOTER = """
---

## Dify 工作流配置方法

### IF 分支的 LLM 节点配置

1. 在 Dify 的 **IF(preset_button 不为空)** 分支中添加 LLM 节点
2. **系统 Prompt**：复制上方对应场景的完整 Prompt
3. **用户 Prompt**：

```
## 数据目录
{{#start.catalog_text#}}

## 审计意图
{{#start.user_intent#}}

## 预设模式
{{#start.preset_button#}}
```

4. **模型**：选择 vLLM 模型供应商（`qwen3-235b`）
5. **温度**：0.3
6. **输出格式**：JSON

### 与自由编译模式 LLM 节点的区别

| 对比项 | 自由编译模式 (ELSE分支) | 预设按钮模式 (IF分支) |
|--------|----------------------|---------------------|
| 算子拓扑 | 大模型自主规划 | **固定模板，不可更改** |
| 审计准则 | 大模型现场推演 | **事务所固化注入** |
| 列名映射 | 大模型推断 | **大模型填充，但必须来自Data Catalog** |
| 容差/阈值 | 大模型推断 | **固定值（可人工复核后调整）** |
| human_review_points | 通用 | **包含预设模式固有复核点** |
"""


def render_dify_md() -> str:
    """从注册表生成 dify/preset_prompts.md 全文"""
    route_rows = []
    alias_notes = []
    for key, p in PRESETS.items():
        if not p.get("dag", True):
            continue
        route_rows.append(f"| {key} | 预设：{p['label']} |")
        if p.get("aliases"):
            alias_notes.append("、".join(f"{a}→{key}" for a in p["aliases"]))
    header = _MD_HEADER.replace("__ROUTE_TABLE__", "\n".join(route_rows))
    header = header.replace("__ALIAS_NOTE__", "；".join(alias_notes) or "无")
    parts = [header]
    for i, (key, p) in enumerate(PRESETS.items(), 1):
        if not p.get("dag", True):
            continue
        parts.append(f"\n## 预设{i}：{p['label']}（preset_button = \"{key}\"）\n")
        parts.append("### System Prompt\n")
        parts.append("```")
        parts.append(p["dify_prompt"])
        parts.append("```\n")
        parts.append("---\n")
    parts.append(_MD_FOOTER)
    return "\n".join(parts)


def write_dify_md(path=None) -> str:
    """重新生成 dify/preset_prompts.md"""
    from pathlib import Path
    target = Path(path) if path else (
        Path(__file__).resolve().parent.parent / "dify" / "preset_prompts.md")
    content = render_dify_md()
    target.write_text(content, encoding="utf-8")
    return str(target)


if __name__ == "__main__":
    out = write_dify_md()
    print(f"已重新生成: {out}")
    print(f"注册预设: {list(PRESETS.keys())}")


# ═══════════════════════════════════════════════════════════════
# Dify「LLM 预设按钮模式」节点统一 system prompt 渲染器
# ═══════════════════════════════════════════════════════════════

def render_unified_preset_node_prompt() -> str:
    """渲染 Dify 语义编译器工作流中"LLM 预设按钮模式"节点的完整 system prompt。

    一个节点 = 一段 system prompt，因此把 7 个预设章节全部内联，
    由路由指令根据 {{#start.preset_button#}} 选择对应章节执行。
    与 preset_prompts.md 同源（均出自本注册表），保持一处维护。
    """
    # 别名归一表文本
    alias_pairs = []
    for key, p in PRESETS.items():
        for a in p.get("aliases", []):
            alias_pairs.append(f"{a}")
    lines = [
        "你是「审计业务逻辑编译器 —— 预设模式」。",
        "",
        "## 工作方式",
        "1. 读取用户消息中的「预设模式名称」，在下文预设章节中找到对应章节，",
        "   严格按该章节的固定算子序列与固化准则执行。",
        "2. 别名归一：",
    ]
    for key, p in PRESETS.items():
        if not p.get("dag", True):
            continue
        if p.get("aliases"):
            lines.append(f"   - {'、'.join(p['aliases'])} → {key}")
    lines += [
        "3. 预设名称为空或无法匹配 → 按自由编译处理，并在 human_review_points 注明。",
        "",
        "## 通用规则（所有预设共享）",
        "1.【强制模板】严格使用章节指定算子序列，不增删、不改序。",
        "2.【参数填充】params 根据 Data Catalog 实际列名和意图动态填写。",
        "3.【列名对齐】source_file 与列名必须逐字来自 Data Catalog，禁止编造/照抄示例。",
        "4.【键纪律】严禁把「序号/编号/行号」用作连接键；两表键名不同用 left_on/right_on 显式映射。",
        "5.【容差纪律】逐笔核对 tolerance_abs=0.01 元（精确到分）；",
        "   百分比容差（tolerance_pct）仅汇总层面且须意图明确指定。",
        "6.【数据纪律】利息/手续费/冲正不删除，单独成类；",
        "   缺失值只标记报告，绝不前向填充或填 0。",
        "7.【输出格式】严格 DAG JSON，Schema 与自由编译一致；只输出 JSON。",
        "",
        "# 预设章节",
        "",
    ]
    i = 0
    for key, p in PRESETS.items():
        if not p.get("dag", True):
            continue
        i += 1
        lines.append(f"## 章节{i}（preset_button = \"{key}\"）")
        lines.append("")
        lines.append(p["dify_prompt"].strip())
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def write_unified_preset_node_prompt(path=None) -> str:
    """把统一 prompt 写到 dify/manual_updates/ 供人工粘贴"""
    from pathlib import Path
    target = Path(path) if path else (
        Path(__file__).resolve().parent.parent / "dify" / "manual_updates"
        / "01_语义编译器_预设节点_system_prompt.txt")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_unified_preset_node_prompt(), encoding="utf-8")
    return str(target)
