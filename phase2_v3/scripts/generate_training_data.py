#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""训练数据自动生成器：从知识库准则条文自动生成 QLoRA 训练数据"""
import json, os, re
from pathlib import Path
from typing import List, Dict

KB_ROOT = Path("D:/审计准则与法规文件整理")
OUTPUT = Path(__file__).resolve().parent.parent / "data" / "finetune" / "synthetic" / "auto_generated.jsonl"
OUTPUT.parent.mkdir(parents=True, exist_ok=True)

SKIP = ["03_事务所内部文件", "06_报告与格式规范", "C_底稿模板"]

DAG_PROMPT = "你是审计 DAG 编译器。将审计师意图编译为 DAG JSON。可用算子：Load/RegexFilter/Merge/Diff/GroupBy/ConditionCheck/Export。只输出 JSON。"
QA_PROMPT = "你是资深审计专家。基于中国注册会计师审计准则，准确回答审计师问题。"

def skip(p): return any(s in str(p) for s in SKIP)
def read_md(f): 
    try: return open(f, encoding="utf-8").read()
    except: return ""

def sections(text):
    secs, title, body = [], "", []
    for line in text.split("\n"):
        m = re.match(r'^(#{1,3})\s+(.+)$', line)
        if m:
            if title and body: secs.append({"t": title, "b": "\n".join(body)})
            title = m.group(2).strip(); body = []
        elif line.strip(): body.append(line.strip())
    if title and body: secs.append({"t": title, "b": "\n".join(body)})
    return secs

def dag_pairs(sec):
    t, b = sec["t"], sec["b"][:400]
    out = []
    kw = t + b
    if any(x in kw for x in ["函证", "询证"]):
        for v in ["生成应收账款函证", "对余额大于50万的客户发函", "生成往来款询证函"]:
            out.append({"messages": [
                {"role":"system","content":DAG_PROMPT},
                {"role":"user","content":f"## 意图\n{v}\n## 参考\n{t}: {b[:200]}"},
                {"role":"assistant","content":json.dumps({"objective":"函证管理","operators":[{"name":"Load"},{"name":"ColumnFilter","desc":"筛选余额>函证标准"},{"name":"Export","desc":"导出函证"}]},ensure_ascii=False)}
            ]})
    if any(x in kw for x in ["抽样", "样本"]):
        for v in ["帮我从5000笔交易里抽20个样本", "按金额从大到小抽样", "对收入进行审计抽样"]:
            out.append({"messages": [
                {"role":"system","content":DAG_PROMPT},
                {"role":"user","content":f"## 意图\n{v}\n## 参考\n{t}: {b[:200]}"},
                {"role":"assistant","content":json.dumps({"objective":"审计抽样","operators":[{"name":"Load"},{"name":"Sort","desc":"按金额降序"},{"name":"Extract","desc":"选取样本"},{"name":"Export"}]},ensure_ascii=False)}
            ]})
    if any(x in kw for x in ["分析", "趋势", "波动", "异常"]):
        for v in ["帮我分析收入月度趋势", "做实质性分析程序", "找出异常波动的科目"]:
            out.append({"messages": [
                {"role":"system","content":DAG_PROMPT},
                {"role":"user","content":f"## 意图\n{v}\n## 参考\n{t}: {b[:200]}"},
                {"role":"assistant","content":json.dumps({"objective":"实质性分析","operators":[{"name":"Load"},{"name":"GroupBy","desc":"按月分组"},{"name":"ConditionCheck","desc":"波动超阈值"},{"name":"Export"}]},ensure_ascii=False)}
            ]})
    return out

def qa_pairs(sec):
    t, b = sec["t"], sec["b"][:600]
    if len(b) < 50: return []
    return [{"messages": [
        {"role":"system","content":QA_PROMPT},
        {"role":"user","content":f"根据审计准则，{t}的要求是什么？"},
        {"role":"assistant","content":f"根据中国注册会计师审计准则，{t}的主要规定如下：\n\n{b[:500]}"}
    ]}]

print("正在扫描知识库...")
all_pairs, fc = [], 0
for root, dirs, files in os.walk(str(KB_ROOT)):
    if skip(root): continue
    for f in files:
        if not f.endswith((".md", ".txt")): continue
        fp = os.path.join(root, f)
        if skip(fp): continue
        text = read_md(fp)
        if not text: continue
        for sec in sections(text):
            all_pairs.extend(dag_pairs(sec))
            all_pairs.extend(qa_pairs(sec))
        fc += 1

seen = set(); unique = []
for p in all_pairs:
    k = json.dumps(p["messages"], ensure_ascii=False)
    if k not in seen: seen.add(k); unique.append(p)

with open(OUTPUT, "w", encoding="utf-8") as f:
    for p in unique: f.write(json.dumps(p, ensure_ascii=False) + "\n")

print(f"已扫描 {fc} 个文件，生成 {len(unique)} 条训练数据 → {OUTPUT}")
