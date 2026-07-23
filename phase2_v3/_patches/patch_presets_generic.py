#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""presets.py 补丁：医保预设→通用"提取式核对" + 新增"跨文件对比"预设"""
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "config" / "presets.py"
src = P.read_text(encoding="utf-8").replace("\r\n", "\n")

# ── 1. 医保回款核对 → 提取式核对（通用模式，医保只是实例） ─────
start = src.index('    "医保回款核对": {')
end = src.index('    "大额交易筛查": {')
GENERIC_EXTRACT = '''    "提取式核对": {
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

你是专项提取核对专家。本次编译必须严格遵循以下固定算子模板。
注意：这是通用模式——"医保回款"只是其中一个实例；任何
"从流水/台账中提取某类业务金额再与另一方核对"的需求都走本模板。

### 固定算子序列（严格按顺序，不可增删）
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

'''
src = src[:start] + GENERIC_EXTRACT + src[end:]
print("  [PATCH] 医保预设 → 通用提取式核对")

# ── 2. 新增"跨文件对比"预设（插在"格式与纠错"特殊路由之前） ────
anchor = '''    # ── 特殊路由（不走 DAG 编译，前端跳格式规范 Tab） ──────────'''
assert src.count(anchor) == 1
CROSS_DOC = '''    "跨文件对比": {
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

你是跨文件数据对比专家。本次编译必须严格遵循以下固定算子模板。
支持 xlsx/csv/docx/pdf/md/txt 任意混合（文档表格与 Excel 同权）。

### 固定算子序列（严格按顺序，不可增删）
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
- < 0.5：文档无可用表格，建议自由编译""",
    },

'''
src = src.replace(anchor, CROSS_DOC + anchor)
print("  [PATCH] 新增跨文件对比预设")

P.write_text(src, encoding="utf-8", newline="\n")
import ast
ast.parse(src)
print("presets.py 泛化完成，AST OK")
