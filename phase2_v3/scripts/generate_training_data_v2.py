#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
训练数据生成器 v2 — 场景化问答版
================================
1. 清洗垃圾问答（过滤元数据标题）
2. 场景化提问（模拟真实审计工作，不搞"第X条规定了什么"背书题）
3. 补核心场景DAG（医保回款/大额筛查/科目核对/趋势分析）
4. 挖官方问题解答PDF

禁止访问：03_事务所内部文件
输出：data/finetune/synthetic/auto_generated_v2.jsonl
"""
import json, os, re
from pathlib import Path
from typing import List, Dict

KB_ROOT = Path("D:/审计准则与法规文件整理")
OUTPUT = Path(__file__).resolve().parent.parent / "data" / "finetune" / "synthetic" / "auto_generated_v2.jsonl"
OUTPUT.parent.mkdir(parents=True, exist_ok=True)

SKIP_DIRS = ["03_事务所内部文件"]
DAG_PROMPT = "你是审计 DAG 编译器。将审计师意图编译为 DAG JSON。可用算子：Load/RegexFilter/ColumnFilter/GroupBy/Merge/Diff/NoiseFilter/Sort/ConditionCheck/Aggregate/Export/Extract/Reconcile。只输出 JSON。"
QA_PROMPT = "你是资深审计专家。基于中国注册会计师审计准则，准确回答审计师的专业问题。"

JUNK_PATTERNS = [
    r'^附件', r'印发', r'^目录$', r'准则列表', r'准则目录', r'废止',
    r'^第[一二三四五六七八九十]+章', r'^\d+$', r'^附录', r'发布$', r'修订$',
    r'^关于', r'通知$', r'^索引', r'^说明$', r'^前言$', r'^总则$',
]

def is_junk_title(title: str) -> bool:
    if len(title) < 4: return True
    return any(re.search(p, title) for p in JUNK_PATTERNS)

def skip(p): return any(s in str(p) for s in SKIP_DIRS)

def read_pdf(fp) -> str:
    try:
        import warnings, logging
        warnings.filterwarnings("ignore")
        logging.getLogger("pdfminer").setLevel(logging.ERROR)
        import pdfplumber
        with pdfplumber.open(fp, laparams={"detect_vertical": False}) as pdf:
            texts = []
            for p in pdf.pages:
                try:
                    t = p.extract_text()
                    if t: texts.append(t)
                except Exception: continue
            return "\n".join(texts)
    except Exception:
        return ""

def read_md(fp) -> str:
    try: return open(fp, encoding="utf-8").read()
    except: return ""

# ═══════ 官方问题解答PDF提取 ═══════
# 注意：这批PDF的"问题"文字用特殊字体嵌入，文本提取只能得到"答："的内容。
# 策略：提取答案块 → 根据答案关键词反向构造实务问题。

def _make_qa(q: str, a: str, source: str) -> Dict:
    return {"messages": [
        {"role": "system", "content": QA_PROMPT},
        {"role": "user", "content": q},
        {"role": "assistant", "content": f"根据{source}：\n\n{a}"}
    ]}

# 答案关键词 → 实务问题模板（{topic}=问题解答主题，如"函证"）
ANSWER_KEYWORD_Q = [
    ("电子询证函", "使用电子询证函（包括第三方平台）实施函证时，有什么风险和要求？"),
    ("没有收到.{0,6}回函|未收到回函", "询证函发出后长时间没有收到回函，注册会计师应当怎么办？"),
    ("回函", "处理询证函回函时应当注意什么？"),
    ("替代审计程序|替代程序", "什么情况下可以实施替代审计程序？应该怎么做？"),
    ("实质性程序", "在{topic}相关审计中，如何确定是否将其用作实质性程序？"),
    ("职业怀疑", "在{topic}过程中如何保持职业怀疑？"),
    ("监盘", "存货监盘应当如何计划和实施？有哪些关注要点？"),
    ("截止测试|截止性", "如何执行截止性测试？"),
    ("舞弊", "在{topic}中如何识别和应对舞弊风险？"),
    ("关联方", "如何识别关联方及其交易？审计时应关注什么？"),
    ("重要性水平", "如何确定和运用重要性水平？"),
    ("错报", "识别出错报后应当如何评价和处理？"),
    ("会计估计", "审计会计估计时应当关注哪些方面？"),
    ("持续经营", "对持续经营能力存在重大疑虑时，注册会计师应当怎么做？"),
    ("关键审计事项", "如何确定和沟通关键审计事项？"),
    ("非无保留意见|保留意见|否定意见|无法表示意见", "什么情况下应当出具非无保留意见？各类型如何区分？"),
    ("银行存款|货币资金", "货币资金审计（含银行存款函证）有哪些具体要求？"),
    ("底稿", "相关审计工作底稿应当如何记录？"),
    ("质量控制复核|项目质控", "项目质量控制复核有什么要求？"),
    ("集团|组成部分", "集团财务报表审计中对组成部分的工作有什么要求？"),
    ("信息技术|数据分析", "如何运用信息技术识别和应对风险？"),
    ("收入确认", "审计收入确认时应当关注哪些风险点？"),
]

def read_pdf_fitz(fp) -> str:
    """PyMuPDF 提取（比 pdfplumber 完整）"""
    try:
        import fitz
        doc = fitz.open(str(fp))
        text = "\n".join(page.get_text() for page in doc)
        doc.close()
        return text
    except Exception:
        return ""

def extract_official_qa(pdf_text: str, source_name: str) -> List[Dict]:
    """按'答：'切块，从答案内容反向构造实务问题"""
    pairs = []
    # 主题：从来源名提取（"问题解答第2号——函证" → "函证"）
    m = re.search(r'——(.+)', source_name)
    topic = m.group(1).strip() if m else "该领域"

    blocks = re.split(r'\n答：', pdf_text)
    used_q = set()
    for block in blocks[1:]:
        answer = re.sub(r'\n\d+\s*\n', '\n', block).strip()
        answer = re.sub(r'\s+\n', '\n', answer)[:1500]
        if len(answer) < 100:
            continue
        head = answer[:300]
        for pattern, q_tmpl in ANSWER_KEYWORD_Q:
            if re.search(pattern, head):
                q = q_tmpl.format(topic=topic)
                if q in used_q:
                    break
                used_q.add(q)
                pairs.append(_make_qa(q, answer, source_name))
                break
    return pairs


# ═══════ md法规 → 场景化审计问答 ═══════

USELESS_ARTICLE = [
    r'为了规范', r'制定本准则', r'本准则自', r'施行', r'由.*负责解释',
    r'本法自', r'公布之日起',
]

def is_useless_article(content: str) -> bool:
    return any(re.search(p, content[:80]) for p in USELESS_ARTICLE)

# 主题关键词 → 审计师真实会问的问题（{src}=准则/法规名）
SCENARIO_MAP = [
    (r'收入.{0,6}确认|确认.{0,6}收入', [
        "客户的收入确认时点存在争议，{src}对收入确认条件是怎么规定的？",
        "审计收入时发现客户可能提前确认收入，按{src}应满足什么条件才能确认？",
    ]),
    (r'减值|跌价准备', [
        "被审计单位的资产可能存在减值迹象但没有计提，按{src}应该怎么处理？",
        "客户不愿意计提减值准备，{src}对减值的规定是什么？",
    ]),
    (r'预计.{0,4}损失|亏损合同', [
        "客户的合同预计要亏损，但账上没有计提损失准备，按{src}应如何处理？",
    ]),
    (r'合同成本|成本.{0,4}核算', [
        "核实客户的合同成本归集时，{src}规定哪些支出可以计入合同成本？",
    ]),
    (r'披露', [
        "复核报表附注时怀疑客户披露不完整，{src}要求披露哪些内容？",
    ]),
    (r'公允价值', [
        "客户用公允价值计量的资产估值存疑，{src}对公允价值计量有什么要求？",
    ]),
    (r'摊销|折旧', [
        "客户的折旧摊销政策看起来激进，{src}对此是怎么规定的？",
    ]),
    (r'坏账|应收.{0,4}款项', [
        "客户的应收账款账龄很长但坏账计提很少，按{src}应该怎么判断？",
    ]),
    (r'关联方|关联交易', [
        "发现客户有大额关联交易，{src}对关联方的认定和披露有什么要求？",
    ]),
    (r'数据安全|个人信息', [
        "审计中会接触客户敏感数据，{src}对数据处理有什么合规要求？",
    ]),
    (r'社会保险|社保|医保', [
        "审计医院的医保回款时，{src}对相关基金管理有什么规定？",
    ]),
    (r'存货', [
        "客户存货金额重大，{src}对存货的计量和成本确定是怎么规定的？",
    ]),
    (r'借款费用|资本化', [
        "客户把大量利息资本化了，按{src}什么条件下才允许资本化？",
    ]),
    (r'政府补助', [
        "客户收到一笔政府补助直接计入了收入，按{src}应该怎么处理？",
    ]),
]

def extract_md_qa(text: str, source_name: str) -> List[Dict]:
    """条文 → 场景化问答。不匹配场景的条文直接丢弃。"""
    pairs = []
    articles = re.findall(
        r'\*\*(第[一二三四五六七八九十百零0-9０-９]+条)\*\*[\s　]*(.{50,1200}?)(?=\*\*第|\Z)',
        text, re.DOTALL)
    clean_src = re.sub(r'[_（(].*$', '', source_name).strip()

    used = set()
    for art_no, content in articles:
        content = content.strip()[:1000]
        if len(content) < 50 or is_useless_article(content):
            continue
        for pattern, templates in SCENARIO_MAP:
            if not re.search(pattern, content):
                continue
            picked = None
            for tmpl in templates:
                if (pattern, tmpl) not in used:
                    picked = tmpl
                    used.add((pattern, tmpl))
                    break
            if picked:
                pairs.append({"messages": [
                    {"role": "system", "content": QA_PROMPT},
                    {"role": "user", "content": picked.format(src=clean_src)},
                    {"role": "assistant", "content": f"根据{clean_src}{art_no}的规定：{content}"}
                ]})
            break
    return pairs


# ═══════ 核心场景 DAG ═══════

def core_scenario_dags() -> List[Dict]:
    pairs = []
    def dag(intent, catalog, bp):
        return {"messages": [
            {"role": "system", "content": DAG_PROMPT},
            {"role": "user", "content": f"## 审计意图\n{intent}\n\n## 数据目录\n{catalog}"},
            {"role": "assistant", "content": json.dumps(bp, ensure_ascii=False)}]}

    bank_cat = "银行流水.xlsx(交易日期/摘要/对方户名/交易金额/余额), 台账.xlsx(机构名称/回款金额/期间)"
    medical_bp = {"objective": "医保回款跨表核对", "operators": [
        {"id": "op_1", "name": "Load", "source_file": "银行流水.xlsx"},
        {"id": "op_2", "name": "Load", "source_file": "台账.xlsx"},
        {"id": "op_3", "name": "NoiseFilter", "params": {"exclude": "手续费|短信费|年费|利息|账户管理费|冲正"}, "input_from": ["op_1"]},
        {"id": "op_4", "name": "RegexFilter", "params": {"pattern": "医保|统筹|社保|医疗统筹|回款|医管|新农合|异地就医", "column": "摘要"}, "input_from": ["op_3"]},
        {"id": "op_5", "name": "Reconcile", "params": {"left_key": "对方户名", "right_key": "机构名称"}, "input_from": ["op_4", "op_2"]},
        {"id": "op_6", "name": "Export", "params": {"output": "医保回款核对底稿.xlsx"}, "input_from": ["op_5"]}]}
    for it in ["帮我核对医保回款", "把银行流水里的医保回款和台账对一下",
               "医保回款跨表匹配，差异控制在5万以内", "核对一下这家医院的医保收入",
               "帮我对账，找出医保回款的差异", "医保统筹的钱到账了多少，和台账对得上吗",
               "筛选流水里的医保款项并与回款表核对", "查一下医保局打款和我们记录的差额"]:
        pairs.append(dag(it, bank_cat, medical_bp))

    large_bp = {"objective": "大额交易筛查", "operators": [
        {"id": "op_1", "name": "Load", "source_file": "银行流水.xlsx"},
        {"id": "op_2", "name": "NoiseFilter", "params": {"exclude": "手续费|利息|冲正"}, "input_from": ["op_1"]},
        {"id": "op_3", "name": "ConditionCheck", "params": {"column": "交易金额", "operator": ">=", "value": 500000}, "input_from": ["op_2"]},
        {"id": "op_4", "name": "Sort", "params": {"column": "交易金额", "order": "desc"}, "input_from": ["op_3"]},
        {"id": "op_5", "name": "Export", "params": {"output": "大额交易清单.xlsx"}, "input_from": ["op_4"]}]}
    for it in ["筛选50万以上的大额交易", "把大额收支列出来", "找出流水里的大额款项",
               "大额资金流动排查", "帮我看看有没有异常大额交易", "单笔超过50万的交易都列出来"]:
        pairs.append(dag(it, "银行流水.xlsx(交易日期/摘要/对方户名/交易金额)", large_bp))

    balance_bp = {"objective": "科目余额核对", "operators": [
        {"id": "op_1", "name": "Load", "source_file": "科目余额表.xlsx"},
        {"id": "op_2", "name": "Load", "source_file": "明细账.xlsx"},
        {"id": "op_3", "name": "GroupBy", "params": {"by": ["科目编码"], "aggregations": {"发生额": "sum"}}, "input_from": ["op_2"]},
        {"id": "op_4", "name": "Diff", "params": {"on": ["科目编码"], "compare": "余额"}, "input_from": ["op_1", "op_3"]},
        {"id": "op_5", "name": "Export", "params": {"output": "科目核对差异表.xlsx"}, "input_from": ["op_4"]}]}
    for it in ["核对科目余额表和明细账", "总账和明细账勾稽", "科目余额核对一下",
               "查一下余额表和账是不是一致", "帮我做总分核对"]:
        pairs.append(dag(it, "科目余额表.xlsx(科目编码/科目名称/余额), 明细账.xlsx(科目编码/凭证号/发生额)", balance_bp))

    trend_bp = {"objective": "收入趋势分析", "operators": [
        {"id": "op_1", "name": "Load", "source_file": "收入明细.xlsx"},
        {"id": "op_2", "name": "GroupBy", "params": {"by": ["月份"], "aggregations": {"收入金额": "sum"}}, "input_from": ["op_1"]},
        {"id": "op_3", "name": "ConditionCheck", "params": {"condition": "环比波动超过20%"}, "input_from": ["op_2"]},
        {"id": "op_4", "name": "Export", "params": {"output": "收入波动分析表.xlsx"}, "input_from": ["op_3"]}]}
    for it in ["按月分析收入趋势，标出波动超过20%的月份", "收入的月度波动分析",
               "做一下收入的实质性分析程序", "看看哪几个月收入异常", "收入趋势有没有异常波动"]:
        pairs.append(dag(it, "收入明细.xlsx(日期/月份/收入类型/收入金额)", trend_bp))

    return pairs


# ═══════ 主流程 ═══════

def main():
    all_pairs = []

    core = core_scenario_dags()
    all_pairs.extend(core)
    print(f"[1] 核心场景DAG: {len(core)} 条")

    qa_dir = KB_ROOT / "01_中注协审计准则体系" / "C_问题解答"
    pdf_qa = 0
    if qa_dir.exists():
        for fp in qa_dir.rglob("*.pdf"):
            if skip(fp): continue
            text = read_pdf_fitz(str(fp)) or read_pdf(str(fp))
            if not text: continue
            m = re.search(r'问题解答第\d+号——[^（(（]+', fp.name)
            source = m.group(0) if m else fp.stem[:30]
            ps = extract_official_qa(text, source)
            pdf_qa += len(ps)
            all_pairs.extend(ps)
    print(f"[2] 官方问题解答PDF: {pdf_qa} 条")

    md_qa = 0
    for root, dirs, files in os.walk(str(KB_ROOT)):
        if skip(root): continue
        for f in files:
            if not f.endswith(".md"): continue
            fp = os.path.join(root, f)
            if skip(fp): continue
            text = read_md(fp)
            if not text: continue
            m = re.search(r'中华人民共和国[\u4e00-\u9fa5]+法', f)
            source = m.group(0) if m else Path(f).stem[:20]
            ps = extract_md_qa(text, source)
            md_qa += len(ps)
            all_pairs.extend(ps)
    print(f"[3] 场景化条文问答: {md_qa} 条")

    seen = set(); unique = []
    for p in all_pairs:
        key = p["messages"][1]["content"][:150]
        if key in seen: continue
        seen.add(key); unique.append(p)

    final = []
    for p in unique:
        q = p["messages"][1]["content"]
        a = p["messages"][2]["content"]
        if len(a) < 60: continue
        if is_junk_title(q.split("\n")[0][:50]) and "## 审计意图" not in q: continue
        final.append(p)

    with open(OUTPUT, "w", encoding="utf-8") as f:
        for p in final:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    print(f"\n去重后: {len(unique)} -> 质检后: {len(final)} 条")
    print(f"输出: {OUTPUT}")


if __name__ == "__main__":
    main()
