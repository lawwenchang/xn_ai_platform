#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v4 微调数据集构建器 (build_v4_datasets.py)
================================================
基于 inspect_datasets.py 体检结论重组三轮训练数据（基座换 Qwen3-32B）：

体检事实：
- training_data.jsonl  18612 条，100% 英文 Python（中英混杂根源）→ 限量采样
- final_train.jsonl    1965 条 = 33条审计QA×10 + 31条风格×5 + 17条DAG×10 + 代码
- round3_train_final   589 条（zh_excel_qa_500 的 498 条已在其中）
- zh_excel_qa_500      500 条纯中文 → 不再单独加，去重后并入中文池

产出（data/finetune/v4/）：
- v4_stage1_zh_code.jsonl   第一轮：中文优先的代码/数据处理底座
- v4_stage2_audit_dag.jsonl 第二轮：审计领域 + DAG 编译（含 7 场景变体扩增）
- v4_stage3_mix.jsonl       第三轮：低学习率巩固混合
- v4_val.jsonl              分层验证集（与训练集按签名严格不重叠）
- v4_report.txt             构成报告

用法：python scripts/build_v4_datasets.py
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT_DIR = ROOT / "data" / "finetune" / "v4"
OUT_DIR.mkdir(parents=True, exist_ok=True)
random.seed(42)

# 与现有训练数据一致的 system 提示（generate_training_data_v2.py 同款）
DAG_SYSTEM = ("你是审计 DAG 编译器。将审计师意图编译为 DAG JSON。可用算子："
              "Load/RegexFilter/ColumnFilter/GroupBy/Merge/Diff/NoiseFilter/Sort/"
              "ConditionCheck/Aggregate/Export/Extract/Reconcile。只输出 JSON。")

REPORT = []


def log(msg):
    print(msg)
    REPORT.append(str(msg))


def load_jsonl(fp: Path):
    if not fp.exists():
        log(f"[警告] {fp.name} 不存在，跳过")
        return []
    recs = []
    with open(fp, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                try:
                    recs.append(json.loads(ln))
                except json.JSONDecodeError:
                    pass
    return recs


def sig(rec) -> str:
    parts = [m.get("content", "") for m in rec.get("messages", [])
             if m.get("role") in ("user", "assistant")]
    return hashlib.md5("\x1e".join(parts).encode("utf-8")).hexdigest()


def sys_of(rec) -> str:
    return next((m.get("content", "") for m in rec.get("messages", [])
                 if m.get("role") == "system"), "")


def dedup(recs):
    seen, out = set(), []
    for r in recs:
        s = sig(r)
        if s not in seen:
            seen.add(s)
            out.append(r)
    return out


def classify(rec) -> str:
    s = sys_of(rec)
    if "DAG" in s:
        return "dag"
    if "审计准则" in s or "资深审计专家" in s:
        return "audit_qa"
    if "审计师助手" in s or "审计工作规范" in s:
        return "style"
    if "Excel" in s:
        return "excel"
    if "Python" in s or "programming" in s:
        return "en_code"
    return "other"


def zh_ratio(rec) -> float:
    a = next((m.get("content", "") for m in rec.get("messages", [])
              if m.get("role") == "assistant"), "")
    zh = len(re.findall(r"[\u4e00-\u9fff]", a))
    en = len(re.findall(r"[A-Za-z]", a))
    return zh / max(1, zh + en)


# ══════════════════════════════════════════════════════
# 1. 装载与分池
# ══════════════════════════════════════════════════════

def build_pools():
    src_r1 = load_jsonl(ROOT / "training_data.jsonl")
    src_r2 = load_jsonl(ROOT / "final_train.jsonl")
    src_r3 = load_jsonl(ROOT / "round3_train_final.jsonl")
    src_zh = load_jsonl(ROOT / "zh_excel_qa_500.jsonl")
    src_val = load_jsonl(ROOT / "final_val.jsonl")

    pools = {"dag": [], "audit_qa": [], "style": [], "excel_zh": [],
             "excel_mixed": [], "en_code": []}

    # round3 + zh500 优先（纯中文池，二者 498 条重叠，去重合并）
    for r in dedup(src_r3 + src_zh):
        c = classify(r)
        if c == "excel":
            pools["excel_zh"].append(r)
        elif c in pools:
            pools[c].append(r)

    # final_train：去掉 x10/x5 上采样重复后并入；Excel 类按中文占比分流
    have = {sig(r) for v in pools.values() for r in v}
    for r in dedup(src_r2):
        s = sig(r)
        if s in have:
            continue
        have.add(s)
        c = classify(r)
        if c == "excel":
            (pools["excel_zh"] if zh_ratio(r) >= 0.5 else pools["excel_mixed"]).append(r)
        elif c in pools:
            pools[c].append(r)

    # 第一轮英文代码：过滤超长后限量采样（防止再次淹没中文能力）
    en = [r for r in src_r1
          if sum(len(m.get("content", "")) for m in r.get("messages", [])) < 3000]
    random.shuffle(en)
    pools["en_code"] = en[:3500]

    # 扩产纯中文 Excel QA（原脚本只取了题库前 500 条）
    try:
        from scripts.zh_excel_qa_bank import gen_all, SYSTEM as ZH_SYS
        items = gen_all()
        extra = [{"messages": [
                    {"role": "system", "content": ZH_SYS},
                    {"role": "user", "content": q},
                    {"role": "assistant", "content": a}]}
                 for q, a in items]
        before = len(pools["excel_zh"])
        merged = dedup(pools["excel_zh"] + extra)
        pools["excel_zh"] = merged[:1200]
        log(f"[扩产] zh_excel_qa_bank 全量 {len(items)} 条，excel_zh 池 "
            f"{before} → {len(pools['excel_zh'])}")
    except Exception as e:
        log(f"[扩产] zh_excel_qa_bank 不可用（{e}），沿用现有中文池")

    log("── 独本数据池（去重后） ──")
    for k, v in pools.items():
        log(f"  {k:<12} {len(v)} 条")
    log(f"  旧验证集     {len(src_val)} 条")
    return pools, src_val


# ══════════════════════════════════════════════════════
# 2. DAG 变体扩增（基于 7 个 few-shot 场景种子）
# ══════════════════════════════════════════════════════

FILE_SWAPS = {
    "银行流水.xlsx": ["银行流水明细.xlsx", "2025年流水.xlsx", "工行流水导出.xlsx"],
    "医保回款汇总表.xlsx": ["医保回款台账.xlsx", "回款情况统计表.xlsx"],
    "业务台账.xlsx": ["收入台账.xlsx", "往来台账2025.xlsx"],
}
PREFIXES = ["帮我", "麻烦", "请帮忙", "需要你"]
KW_EXTRA = ["医疗保障", "职工医保", "居民医保", "拨付"]
TOL_PCT = [0.5, 1.0, 2.0, 3.0]


def _intent_variants(intent: str):
    out = {intent}
    core = re.sub(r"^(帮我|麻烦|请帮忙|需要你|请)", "", intent)
    for p in PREFIXES:
        out.add(p + core)
    return list(out)


def gen_dag_variations(per_seed: int = 30):
    try:
        from config.few_shot_examples import FEW_SHOT_EXAMPLES
    except Exception as e:
        log(f"[DAG扩增] few_shot_examples 导入失败: {e}")
        return []
    samples = []
    for seed_idx, ex in enumerate(FEW_SHOT_EXAMPLES):
        base_json = json.dumps(
            {"intent": ex["user_intent"], "summary": ex["catalog_summary"],
             "dag": ex["dag_output"]}, ensure_ascii=False)
        variants, tries = set(), 0
        while len(variants) < per_seed and tries < per_seed * 20:
            tries += 1
            s = base_json
            # ① 文件名一致性替换（意图/目录/DAG 参数同步变）
            for old, news in FILE_SWAPS.items():
                if old in s and random.random() < 0.7:
                    s = s.replace(old, random.choice(news))
            obj = json.loads(s)
            intent = random.choice(_intent_variants(obj["intent"]))
            dag = obj["dag"]
            # ② 容差扰动（意图文本与 DAG 参数保持一致）
            m = re.search(r"(\d+(?:\.\d+)?)\s*%", intent)
            if m and isinstance(dag.get("context"), dict) and "tolerance_pct" in dag["context"]:
                new_pct = random.choice(TOL_PCT)
                intent = intent.replace(m.group(0), f"{new_pct:g}%")
                dag["context"]["tolerance_pct"] = new_pct
                for op in dag.get("operators", []):
                    if "tolerance_pct" in op.get("params", {}):
                        op["params"]["tolerance_pct"] = new_pct
            # ③ 正则关键词扩充
            if random.random() < 0.5:
                for op in dag.get("operators", []):
                    pat = op.get("params", {}).get("pattern")
                    if pat and "医保" in pat:
                        op["params"]["pattern"] = pat + "|" + random.choice(KW_EXTRA)
                        break
            user = f"## 审计意图\n{intent}\n\n## 数据目录\n{obj['summary']}"
            key = hashlib.md5(user.encode()).hexdigest()
            if key in variants:
                continue
            variants.add(key)
            samples.append({"_seed": seed_idx, "messages": [
                {"role": "system", "content": DAG_SYSTEM},
                {"role": "user", "content": user},
                {"role": "assistant",
                 "content": json.dumps(dag, ensure_ascii=False)}]})
    log(f"[DAG扩增] {len(set(r['_seed'] for r in samples))} 场景种子 → {len(samples)} 条变体")
    return samples



# ══════════════════════════════════════════════════════
# 3. 组装三轮训练集 + 验证集
# ══════════════════════════════════════════════════════

def strip_meta(recs):
    return [{"messages": r["messages"]} for r in recs]


def write(fp: Path, recs):
    with open(fp, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    log(f"  -> {fp.name}: {len(recs)} 条")


def main():
    pools, old_val = build_pools()
    dag_vars = gen_dag_variations(per_seed=30)

    # ── 验证集（先扣，保证与训练零重叠）──
    by_seed = {}
    for r in dag_vars:
        by_seed.setdefault(r["_seed"], []).append(r)
    val_dag, train_dag_vars = [], []
    for lst in by_seed.values():
        random.shuffle(lst)
        val_dag.extend(lst[:2])          # 每场景留 2 条进验证集
        train_dag_vars.extend(lst[2:])

    random.shuffle(pools["excel_zh"])
    val_excel = pools["excel_zh"][:12]
    pool_excel = pools["excel_zh"][12:]

    val = dedup(old_val + strip_meta(val_dag) + val_excel)
    val_sigs = {sig(r) for r in val}

    def not_in_val(recs):
        return [r for r in recs if sig(r) not in val_sigs]

    dag_u = not_in_val(pools["dag"])
    qa_u = not_in_val(pools["audit_qa"])
    style_u = not_in_val(pools["style"])
    excel_u = not_in_val(pool_excel)
    dag_v = not_in_val(strip_meta(train_dag_vars))

    # ── 第一轮：中文优先能力底座 ──
    stage1 = excel_u * 2 + pools["en_code"]
    random.shuffle(stage1)

    # ── 第二轮：审计领域 + DAG（重复 ≤6x，靠变体而非复制撑量）──
    stage2 = (qa_u * 4 + style_u * 4 + dag_u * 6 + dag_v
              + random.sample(excel_u, min(300, len(excel_u))))
    random.shuffle(stage2)

    # ── 第三轮：全域巩固（独本 1x + 少量底座防遗忘）──
    stage3 = dedup(qa_u + style_u + dag_u + dag_v
                   + random.sample(excel_u, min(400, len(excel_u)))
                   + random.sample(pools["en_code"], min(300, len(pools["en_code"]))))
    random.shuffle(stage3)

    log("── 产出 ──")
    write(OUT_DIR / "v4_stage1_zh_code.jsonl", stage1)
    write(OUT_DIR / "v4_stage2_audit_dag.jsonl", stage2)
    write(OUT_DIR / "v4_stage3_mix.jsonl", stage3)
    write(OUT_DIR / "v4_val.jsonl", val)

    log("── 第二轮构成（按类型） ──")
    log(json.dumps(dict(Counter(classify(r) for r in stage2)), ensure_ascii=False))
    log(f"验证集: {len(val)} 条（旧val {len(old_val)} + DAG变体 {len(val_dag)} "
        f"+ 中文Excel {len(val_excel)}，签名去重后）")

    # 训练/验证零重叠断言
    train_sigs = {sig(r) for r in stage1} | {sig(r) for r in stage2} | {sig(r) for r in stage3}
    overlap = train_sigs & val_sigs
    log(f"训练∩验证 重叠检查: {len(overlap)} 条（应为 0）")

    (OUT_DIR / "v4_report.txt").write_text("\n".join(REPORT), encoding="utf-8")
    log(f"报告 -> {OUT_DIR / 'v4_report.txt'}")


if __name__ == "__main__":
    main()
