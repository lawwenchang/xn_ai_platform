#!/usr/bin/env python3
"""采集清单 → ChatML 训练数据转换器"""
import json, re, sys
from pathlib import Path
from typing import Dict, List

OUTPUT = Path(__file__).resolve().parent.parent / "data" / "finetune" / "synthetic" / "checklist_converted.jsonl"
OUTPUT.parent.mkdir(parents=True, exist_ok=True)
DAG = "你是审计 DAG 编译器。将审计师意图编译为 DAG JSON。可用算子：Load/RegexFilter/ColumnFilter/GroupBy/Merge/Diff/NoiseFilter/Export/Extract。只输出 JSON。"

def parse(text: str) -> Dict:
    d = {}
    # 关键词
    for line in re.findall(r'\|\s*(.+?)\s*\|\s*(.+?)\s*\|', text):
        k, v = line[0].strip(), line[1].strip()
        if v and v not in ("你们搜什么词？","---","") and "|" not in k:
            if any(x in k for x in ["医保","款","交易","补助","工资","贷款","收入"]): d[f"kw_{k}"] = v
            if any(x in k for x in ["门槛","标准","容差","容忍","天数","比例","样本量","笔数","期限","波动","阈值"]): d[k] = v
            if any(x in k for x in ["方法","方式","偏好","类型","处理","单位","位数","字体","字号"]): d[k] = v
            if "报告" in k and "|" not in k: d[f"tmpl_{k}"] = v
    # 噪音勾选
    checked = re.findall(r'\[x\]\s*(.+?)(?:\r?\n|$)', text, re.IGNORECASE)
    if checked: d["noise"] = ", ".join(c.strip() for c in checked)
    # 措辞
    m = re.search(r'措辞.*?\|\s*\|\s*\n\|\s*(.+?)\s*\|', text, re.DOTALL)
    if m: d["wording"] = m.group(1).strip()[:300]
    return d

def pairs(data: Dict) -> List:
    out = []
    # 关键词→DAG
    for k, v in data.items():
        if not k.startswith("kw_"): continue
        biz = k[3:]
        for var in [f"帮我核对{biz}", f"筛选{biz}相关交易", f"匹配{biz}流水"]:
            out.append({"messages": [
                {"role":"system","content":DAG},
                {"role":"user","content":f"## 意图\n{var}\n## 数据\n银行流水(摘要/金额/对方户名)"},
                {"role":"assistant","content":json.dumps({"objective":f"{biz}筛选","operators":[{"name":"Load"},{"name":"RegexFilter","params":{"pattern":v}},{"name":"Export"}]},ensure_ascii=False)}
            ]})
    # 噪音排除
    if "noise" in data:
        for var in ["排除噪音交易", "筛选时不要手续费这些", "把这些噪音去掉"]:
            out.append({"messages": [
                {"role":"system","content":DAG},
                {"role":"user","content":f"## 意图\n{var}\n## 排除\n{data['noise']}"},
                {"role":"assistant","content":json.dumps({"objective":"排除噪音","operators":[{"name":"Load"},{"name":"NoiseFilter","params":{"exclude":data["noise"]}},{"name":"Export"}]},ensure_ascii=False)}
            ]})
    # 阈值→ColumnFilter
    for label in ["应收账款发函金额门槛","应付账款发函金额门槛","对账容差"]:
        if label in data:
            v = data[label].strip()
            out.append({"messages": [
                {"role":"system","content":DAG},
                {"role":"user","content":f"## 意图\n按{label}筛选，阈值为{v}"},
                {"role":"assistant","content":json.dumps({"objective":f"按{label}筛选","operators":[{"name":"Load"},{"name":"ColumnFilter","params":{"column":"金额",">":v}},{"name":"Export"}]},ensure_ascii=False)}
            ]})
    return out

def main(p: str):
    text = Path(p).read_text(encoding="utf-8"); data = parse(text)
    if not data: print("未提取到数据。请确认采集清单已填写。"); return
    print(f"提取 {len(data)} 个参数")
    ps = pairs(data); print(f"生成 {len(ps)} 条训练数据")
    seen = set(); uq = []
    for x in ps:
        k = json.dumps(x["messages"],ensure_ascii=False)
        if k not in seen: seen.add(k); uq.append(x)
    with open(OUTPUT,"w",encoding="utf-8") as f:
        for x in uq: f.write(json.dumps(x,ensure_ascii=False)+"\n")
    print(f"去重 {len(uq)} 条 → {OUTPUT}")
    ag = OUTPUT.parent / "auto_generated.jsonl"
    if ag.exists():
        cb = OUTPUT.parent / "combined_training_data.jsonl"
        with open(cb,"w",encoding="utf-8") as cf:
            for s in [ag, OUTPUT]:
                for l in open(s,encoding="utf-8"): cf.write(l)
        total = sum(1 for _ in open(cb,encoding="utf-8"))
        print(f"合并: {cb.name} = {total} 条")

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv)>1 else "docs/质控合伙人训练数据采集清单.md")
