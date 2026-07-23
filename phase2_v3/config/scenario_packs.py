#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
场景知识包 (scenario_packs.py)
=================================
审计场景注册表——场景以数据形式注册，而非代码分支。
新增场景只需在此追加一个 Pack，无需改动任何引擎。

每个 Pack 定义：
- cues:           意图关键词（确定性初筛）
- checklist:      规划检查单（注入 LLM prompt，把 RAG 知识升级为规划约束）
- required_ops:   DAG 必须包含的算子/能力（校验器强制检查，缺失自动补或熔断）
- deliverables:   该场景的专业交付物（expected_outputs 对照）
- tolerance_rule: 容差纪律（逐笔=±0.01 元；汇总=用户指定）
- engine:         可选的确定性专用引擎（有则快车道，无则 LLM 编排通用算子）

覆盖实际审计工作的主要场景族：
  明细级逐笔对账 / 汇总级（年度·分类）勾稽 / 提取式部分金额匹配 /
  大额交易筛查 / 单表分析 / 函证 / 抽样 / 穿行测试 / 报告生成 / 跨文档比对
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional
import pandas as pd
# ═══════════════════════════════════════════════════════════════
# 唯一场景注册表（v3.2 — cues 加权 + report_meta + 降级 prompt 字段）
# ═══════════════════════════════════════════════════════════════

SCENARIO_PACKS: Dict[str, Dict[str, Any]] = {

    "bank_reconcile_detail": {
        "name": "明细级逐笔对账（序时账/台账 × 银行流水）",
        "cues": {"对账": 10, "序时账": 10, "日记账": 10, "逐笔": 8,
                 "核账": 5, "核对": 5, "相符": 5, "流水对": 8, "银企": 5},
        "checklist": [
            "① 流水含多账户时，先按银行账号过滤到与账方同一账户",
            "② 方向镜像：账方 借方-贷方 对应 流水 贷方（收入）-借方（支取），归一后同号勾对",
            "③ 对账前双方各自勾稽：期初+本期发生=期末，不平先报数据完整性问题",
            "④ 逐笔匹配层级：金额精确(0.01)+同日 → 金额精确+日期窗口 → n:m 拆分合并 → 模糊（仅人工复核）",
            "⑤ 未匹配项默认待人工核查；接近期末且有窗口证据的才列未达账项候选（四分类），需期后验证",
            "⑥ 交付：逐笔对账明细底稿 + 银行存款余额调节表 + 未达/待核查清单 + 异常资金交易清单",
            "⑦ P1双向核对：Diff必须同时输出双向差异（流水有账上无+账上有流水无），高危差异单独标红",
            "⑧ P6账户优先级：多账户优先基本户/余额大户/发生额大户；发生额大余额小/零余额/销户户高危优先",
        ],
        "required_ops": ["Reconcile"],
        "deliverables": ["逐笔对账明细底稿", "银行存款余额调节表",
                         "未达账项与待核查清单", "异常资金交易清单"],
        "tolerance_rule": "逐笔核对 0.01 元（精确到分），禁止百分比容差",
        "engine": "bank_reconcile_engine",
        "report_section": "match",
    },

    "summary_compare": {
        "name": "汇总级勾稽（年度/分类汇总台账 × 汇总流水）",
        "cues": {"汇总": 8, "总额": 8, "合计": 8, "按年": 5, "年度": 5,
                 "按月": 5, "按类别": 5, "分类汇总": 8, "勾稽": 8, "对比总": 5},
        "checklist": [
            "① 双方按同一维度（年度/月份/科目/单位）GroupBy 汇总后再比对，禁止逐笔硬对",
            "② 汇总键必须双方语义一致（如 年度<->年度、科目<->科目），禁止用序号/行号",
            "③ 差异表必须同时列示：仅左方有/仅右方有/双方都有但金额不等 三类",
            "④ 容差由审计师指定（如 1% 或 5 万），未指定时差异全列示不隐藏",
            "⑤ 大额差异必须追溯到明细层的建议（提示可进一步逐笔核对）",
        ],
        "required_ops": ["GroupBy", "Merge", "Diff"],
        "deliverables": ["汇总勾稽差异表", "差异分析说明"],
        "tolerance_rule": "汇总层面容差由审计师指定；未指定则全量列示",
        "engine": None,
        "report_section": "balance",
    },

    "filtered_extraction_match": {
        "name": "提取式部分金额匹配（如提取流水中医保回款与台账核对）",
        "cues": {"提取": 8, "医保": 10, "回款": 8, "社保": 10, "统筹": 8,
                 "筛选": 3, "中的": 2, "相关": 2, "部分": 2},
        "checklist": [
            "① 先把提取规则确定化：关键词/条件必须写进 RegexFilter 的 pattern，禁止口头描述",
            "② 提取规则要留痕：哪一列、哪个关键词命中多少笔，随结果一并输出",
            "③ 提取后按对方/机构等维度汇总，再与台账对应列比对",
            "④ 未命中的流水不得视为不存在——单列未提取部分供检查提取规则完整性",
            "⑤ 利息/手续费/冲正不删除，单独成类",
        ],
        "required_ops": ["RegexFilter", "GroupBy", "Diff"],
        "deliverables": ["提取明细及命中统计", "汇总比对差异表"],
        "tolerance_rule": "汇总比对容差由审计师指定",
        "engine": "matching_engine",
        "report_section": "medical",
    },

    "large_txn_screen": {
        "name": "大额交易筛查",
        "cues": {"大额": 10, "超过": 5, "以上": 3, "筛查": 8, "异常交易": 8, "风险": 5},
        "checklist": [
            "① 阈值条件写进 ConditionCheck（如大于等于50万），方向（收/支）明确",
            "② 结果按金额降序并标注风险等级",
            "③ 关注整数大额、非营业时间、一收一付同额等红旗特征",
        ],
        "required_ops": ["ConditionCheck", "Sort"],
        "deliverables": ["大额交易清单"],
        "tolerance_rule": "不适用",
        "engine": None,
        "report_section": "screening",
    },

    "single_table_analysis": {
        "name": "单表分析（筛选/汇总/透视/去重/排序/计算列）— 默认兜底",
        "cues": {"筛选": 5, "汇总": 3, "透视": 5, "统计": 5, "分析": 5,
                 "去重": 8, "排序": 5, "分组": 5, "计算": 3, "新增列": 3,
                 "格式转换": 5, "清洗": 5, "空值": 5, "缺失值": 5,
                 "合并列": 5, "拆分列": 5, "金额列": 3, "求和": 3,
                 "平均值": 5, "环比": 5, "同比": 5, "占比": 5, "增长率": 5,
                 "排名": 5, "top": 5, "最大": 3, "最小": 3, "前": 2,
                 "降序": 5, "升序": 5},
        "checklist": [
            '① 单表场景不虚构第二表，仅一个 Load 算子',
            '② 汇总维度与金额列以 Data Catalog 中的真实列名为准，禁止编造列名',
            '③ 金额列必须先验证数据类型（pd.to_numeric(errors=coerce)），非法值标记而非静默丢弃',
            '④ 缺失值只标记报告，不做前向填充/填0/删除行（审计红线：数据完整性不可破坏）',
            '⑤ 日期列统一转为 datetime，跨度超1年自动建议年度分组',
            '⑥ 排序前确认列数据类型（文本排文本、数字排数字），禁止字符串数字混排',
            '⑦ 去重须明确哪些列组合判定为重复，多列去重必须写进 params',
            '⑧ 透视/交叉表需指定 index/columns/values/aggfunc 四项，缺一项视为未完成',
            '⑨ 计算列公式使用列名原样引用（如 df[金额]*0.13），禁止硬编码位置索引',
            '⑩ 输出至少包含：结果明细表 + 汇总统计行（记录数/空值数/数值列min-max-mean）',
        ],
        "required_ops": ["Load"],
        "deliverables": [
            "处理结果明细表",
            "汇总统计（行数/空值列清单/数值列min-max-mean）",
            "数据质量摘要（空值率>20%的列标注提醒）",
        ],
        "tolerance_rule": "数值计算默认保留2位小数；百分比结果保留1位小数并标%",
        "engine": None,
        "report_section": "general",
    },

    "confirmation": {
        "name": "函证",
        "cues": {"函证": 10, "询证": 10, "确认函": 10},
        "checklist": [
            "① 默认积极式；消极式仅限低风险小额且需说明理由",
            "② 未回函项目必须给替代程序：期后回款/对账单/余额调节表/原始凭证",
            "③ 回函差异逐笔登记并分析原因",
        ],
        "required_ops": [],
        "deliverables": ["函证清单", "回函差异登记表"],
        "tolerance_rule": "不适用",
        "engine": None,
        "report_section": None,
    },

    "sampling": {
        "name": "审计抽样（CSA 1314）",
        "cues": {"抽样": 10, "抽凭": 10, "抽选": 5, "抽查": 5},
        "checklist": [
            "① 默认 MUS（货币单位抽样）：抽样间隔+随机起点，固定种子可复现",
            "② 零/负金额剔除 MUS 总体单独考虑；>=间隔的高层项目全部入选",
            "③ 样本评价：tainting 错报推断 + 基本精确度界限 + 递增错报界限",
        ],
        "required_ops": [],
        "deliverables": ["抽样清单（含间隔/随机起点/种子）", "样本评价表"],
        "tolerance_rule": "不适用",
        "engine": "audit_sampling",
        "report_section": None,
    },

    "walkthrough": {
        "name": "穿行测试",
        "cues": {"穿行": 10, "流程": 5, "断点": 5},
        "checklist": [
            "① 流程节点由业务实际决定（如 请购→审批→验收→入库→付款），不得只看日期/金额/状态",
            "② 断点明确定义：某单据在下一环节找不到对应记录",
        ],
        "required_ops": ["Merge"],
        "deliverables": ["穿行测试底稿（含断点标记）"],
        "tolerance_rule": "不适用",
        "engine": None,
        "report_section": None,
    },

    "doc_generation": {
        "name": "报告/底稿生成",
        "cues": {"报告": 10, "底稿": 10, "生成": 5, "出具": 5, "文档": 5},
        "checklist": [
            "① 报告数字必须与底稿审定数同源勾稽，不一致禁止出具",
            "② 口语表述转规范表述；模板优先",
        ],
        "required_ops": [],
        "deliverables": ["审计报告/底稿文档"],
        "tolerance_rule": "不适用",
        "engine": None,
        "report_section": "general",
    },

    "cross_doc_compare": {
        "name": "跨文档比对（xlsx/csv/docx/pdf/md/txt 混合）",
        "cues": {"文档": 5, "报告对比": 5, "两份": 8, "跨文档": 10, "docx": 5, "pdf": 5},
        "checklist": [
            "① 文档中的表格与 Excel 同权参与 Merge/Diff",
            "② 文本部分按段落/关键词比对差异",
        ],
        "required_ops": ["Load", "Diff"],
        "deliverables": ["跨文档差异表"],
        "tolerance_rule": "按内容类型决定",
        "engine": "document_loader",
        "report_section": None,
    },

    "aging_analysis": {
        "name": "账龄分析（往来款项）",
        "cues": {"账龄": 10, "往来": 5, "应收": 5, "应付": 5, "坏账": 10, "长账龄": 10},
        "checklist": [
            "① 识别日期列和金额列，确认资产负债表日",
            "② 按账龄区间分桶（1年内/1-2年/2-3年/3-5年/5年以上）",
            "③ 红冲/冲正记录单独标注，不混入正常账龄计算",
            "④ 借贷方向归一：应收账款看借方余额，应付账款看贷方余额",
            "⑤ 交付：账龄分析明细 + 账龄汇总表 + 长账龄坏账提示",
        ],
        "required_ops": ["Load", "Export"],
        "deliverables": ["账龄分析明细", "账龄分析汇总表", "长账龄坏账提示"],
        "tolerance_rule": "不适用（日期计算）",
        "engine": "aging_engine",
        "report_section": None,
    },
}

# —— 路由反歧义阈值 ——
AMBIGUITY_THRESHOLD = 3

def detect_scenario(intent: str, default: str = "single_table_analysis",
                    ask_user: bool = True) -> str:
    """平台唯一场景路由。加权 cues + ASK_USER 反歧义。"""
    if not intent:
        return default
    scores = {}
    for sid, pack in SCENARIO_PACKS.items():
        cues = pack["cues"]
        if isinstance(cues, list):
            scores[sid] = sum(5 for c in cues if c in intent)
        else:
            scores[sid] = sum(w for c, w in cues.items() if c in intent)
    if not scores or max(scores.values()) == 0:
        # 零命中 → embedding 兜底 → 再不行才 default
        emb = embedding_fallback(intent)
        if emb:
            return emb
        return default
    sorted_scenes = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top1_id, top1_score = sorted_scenes[0]
    top2_score = sorted_scenes[1][1] if len(sorted_scenes) > 1 else 0
    if ask_user and (top1_score - top2_score) < AMBIGUITY_THRESHOLD:
        return "ASK_USER"
    return top1_id


def assemble_fallback_prompt(scenario_id: str) -> str:
    """降级模式：从注册表组装 system prompt"""
    pack = SCENARIO_PACKS.get(scenario_id) or SCENARIO_PACKS["single_table_analysis"]
    chk = build_scenario_prompt(scenario_id)
    return f"""【降级模式激活】Dify 不可用。只能执行确定性操作。
## 当前场景：{pack['name']}
{chk}
## 通用规则
- Load：每个文件一个 Load，列名来自 Data Catalog
- 严禁用序号/行号做连接键
- 输出严格的 DAG JSON"""


def get_report_meta(scenario_id: str):
    """报告生成器用：从注册表拿场景元信息"""
    pack = SCENARIO_PACKS.get(scenario_id) or SCENARIO_PACKS["single_table_analysis"]
    goals = {
        "bank_reconcile_detail": "对序时账与银行流水逐笔核对，发现不一致记录并分析原因。",
        "summary_compare": "按指定维度汇总双方数据，比对总额差异。",
        "filtered_extraction_match": "从流水中提取特定业务，与台账核对金额一致性。",
        "large_txn_screen": "按阈值筛查大额/异常交易，风险分级预警。",
        "single_table_analysis": "按用户指令进行数据处理和分析。",
        "confirmation": "生成函证清单，记录回函差异。",
        "sampling": "按 MUS 方法抽取样本，生成样本清单。",
        "walkthrough": "按业务节点串联多表数据，标记断点。",
        "doc_generation": "根据审定数据生成报告/底稿文档。",
        "cross_doc_compare": "跨文档格式比对，发现文本和数字差异。",
    }
    return {
        "name": pack["name"], "goal": goals.get(scenario_id, "按用户指令处理数据"),
        "section": pack.get("report_section", "general"),
        "tolerance_rule": pack["tolerance_rule"],
        "deliverables": pack["deliverables"],
    }




def is_detail_level(df, min_rows: int = 10) -> bool:
    """判断台账/序时账是否明细级（逐笔）而非汇总级。

    v3.3 复合特征：行数(弱) + 日期粒度 + 凭证号 + 金额重复度，加权打分。
    """
    try:
        from core.column_semantics import detect_column_roles, infer_amount_columns
        roles = detect_column_roles(df)
        score = 0

        # 1) 日期列存在，且粒度是"日"级（加分），"月"级（扣分）
        if "date" in roles:
            score += 2
            date_col = roles["date"]
            try:
                dates = pd.to_datetime(df[date_col], errors="coerce").dropna()
                if len(dates) > 0:
                    span_days = (dates.max() - dates.min()).days
                    if span_days > 30 and len(dates) > 50:
                        score += 2  # 跨度>30天+50笔以上 → 日级明细
                    elif span_days <= 30 and len(dates) < 20:
                        score -= 1  # 跨度<30天+少于20笔 → 可能是月汇总
            except Exception:
                pass
        else:
            score -= 3  # 无日期列 → 大概率汇总

        # 2) 凭证号/订单号 → 强明细信号
        if any(r in roles for r in ("voucher_no", "order_no")):
            score += 4

        # 3) 行数（弱特征）
        n = len(df)
        if n > 1000:      score += 2
        elif n > 500:     score += 1
        elif n < 50:      score -= 1
        elif n < 20:      score -= 2

        # 4) 金额列重复度
        amts = infer_amount_columns(df, max_cols=3)
        if amts:
            all_amounts = []
            for col in amts[:2]:
                vals = pd.to_numeric(df[col], errors="coerce").dropna()
                vals = vals[vals.abs() > 0.01]
                if len(vals) > 0:
                    all_amounts.append(vals)
            if all_amounts:
                combined = pd.concat(all_amounts)
                unique_ratio = combined.nunique() / max(len(combined), 1)
                if unique_ratio > 0.5:   score += 2
                elif unique_ratio > 0.3: score += 1
                else:                    score -= 2  # 金额高度重复 → 汇总

        return score >= 1
    except Exception:
        return True  # 检测失败不阻断（交给人工/LLM）


# ═══════════════════════════════════════════════════════════════
# embedding 零命中兜底（v3.2）
# ═══════════════════════════════════════════════════════════════

_EMBEDDER = None
_SCENARIO_EMBEDDINGS = None
_EMBED_THRESHOLD = 0.45
_EMBEDDER_MODEL_NAME = None  # 实际加载了哪个模型


def _get_embedder():
    """懒加载 embedding 模型。固定用 MiniLM（秒级加载）。"""
    global _EMBEDDER, _EMBEDDER_MODEL_NAME
    if _EMBEDDER is not None or _EMBEDDER is False:
        return _EMBEDDER if _EMBEDDER is not False else None

    import os
    base = os.path.join(os.path.dirname(__file__), "..", "data", "models")
    from sentence_transformers import SentenceTransformer

    minilm_path = os.path.join(base, "sentence-transformers",
                               "paraphrase-multilingual-MiniLM-L12-v2")
    try:
        _EMBEDDER = SentenceTransformer(minilm_path)
        _EMBEDDER_MODEL_NAME = "MiniLM-L12-v2"
        return _EMBEDDER
    except Exception:
        _EMBEDDER = False
        return None


def _get_scenario_embeddings():
    """预计算所有场景名的 embedding"""
    global _SCENARIO_EMBEDDINGS
    if _SCENARIO_EMBEDDINGS is not None:
        return _SCENARIO_EMBEDDINGS
    model = _get_embedder()
    if not model:
        _SCENARIO_EMBEDDINGS = {}
        return {}
    texts = [pack["name"] for pack in SCENARIO_PACKS.values()]
    ids = list(SCENARIO_PACKS.keys())
    vecs = model.encode(texts, convert_to_tensor=True)
    _SCENARIO_EMBEDDINGS = dict(zip(ids, vecs))
    return _SCENARIO_EMBEDDINGS


def embedding_fallback(intent: str) -> str:
    """零命中时用余弦相似度找最匹配场景。返回场景 ID 或空字符串。"""
    model = _get_embedder()
    if not model:
        return ""
    try:
        from sentence_transformers.util import cos_sim
        embeds = _get_scenario_embeddings()
        if not embeds:
            return ""
        intent_vec = model.encode(intent, convert_to_tensor=True)
        best_id, best_sim = "", 0.0
        for sid, svec in embeds.items():
            sim = float(cos_sim(intent_vec, svec)[0][0])
            if sim > best_sim:
                best_id, best_sim = sid, sim
        return best_id if best_sim >= _EMBED_THRESHOLD else ""
    except Exception:
        return ""


def build_scenario_prompt(scenario_id: str) -> str:
    """生成注入编译 prompt 的规划检查单（RAG 知识 → 规划约束）"""
    pack = SCENARIO_PACKS.get(scenario_id)
    if not pack:
        return ""
    lines = [
        f"## 场景规划检查单：{pack['name']}（必须逐条落实）",
        *[f"- {c}" for c in pack["checklist"]],
        f"- 容差纪律：{pack['tolerance_rule']}",
        f"- 应交付：{'、'.join(pack['deliverables'])}",
    ]
    if pack["required_ops"]:
        lines.append(f"- DAG 必须包含算子：{', '.join(pack['required_ops'])}")
    return "\n".join(lines)


def required_ops_for(scenario_id: str) -> List[str]:
    pack = SCENARIO_PACKS.get(scenario_id) or {}
    return list(pack.get("required_ops", []))
