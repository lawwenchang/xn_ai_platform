#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""智能预审检查引擎"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import pandas as pd

def _sf(v):
    try: return float(pd.to_numeric(v, errors="coerce") or 0)
    except: return 0.0

def _tb_cols(tb):
    s = a = b = d = None
    for c in tb.columns:
        cs = str(c)
        if "科目名称" in cs and "辅助" not in cs: s = str(c)
        if ("期末余额" in cs or ("余额" in cs and "期初" not in cs and "累计" not in cs)) and a is None: a = str(c)
        if any(kw in cs for kw in ["期初余额", "年初余额"]): b = str(c)
        if cs.strip() in ("方向", "余额方向"): d = str(c)
    return s, a, b, d

def _analyze_fluctuations(tb, prior_tb=None):
    s, a, _, _ = _tb_cols(tb)
    if s is None or a is None: return pd.DataFrame()
    curr = {}
    for _, r in tb.iterrows(): curr[str(r[s]).strip()] = _sf(r[a])
    prior = {}
    if prior_tb is not None:
        ps, pa, _, _ = _tb_cols(prior_tb)
        for _, r in prior_tb.iterrows(): prior[str(r[ps or s]).strip()] = _sf(r[pa or a])
    findings = []
    for n, cv in curr.items():
        if abs(cv) < 100: continue
        pv = prior.get(n, 0)
        if abs(pv) < 100: continue
        chg = cv - pv
        pct = round(chg / abs(pv) * 100, 1) if abs(pv) > 1 else 0
        ap = abs(pct)
        if ap >= 100: risk = "🔴 高"
        elif ap >= 50: risk = "🟡 中"
        elif ap >= 30: risk = "🟢 低"
        else: continue
        findings.append({"科目": n, "本期余额": round(cv, 2), "上期余额": round(pv, 2),
                         "变动额": round(chg, 2), "变动率%": pct, "风险等级": risk,
                         "方向": "增加" if chg > 0 else "减少"})
    df = pd.DataFrame(findings)
    if not df.empty:
        order = {"🔴 高": 0, "🟡 中": 1, "🟢 低": 2}
        df["_s"] = df["风险等级"].map(order)
        df = df.sort_values(["_s", "变动率%"], ascending=[True, False]).drop(columns=["_s"])
    return df

CROSS_RULES = [
    ("累计折旧/固定资产原值", ["累计折旧"], ["固定资产"], lambda a, b: 0.01 < a/b < 0.60 if b > 0 else True, "折旧覆盖率1%~60%"),
    ("利息支出/借款余额≈利率", ["利息支出", "利息费用"], ["短期借款", "长期借款"], lambda a, b: 0.01 < a/b < 0.20 if b > 0 else True, "推算年利率1%~20%"),
    ("应交税费/利润≈税负率", ["应交税费", "所得税费用"], ["利润总额", "净利润"], lambda a, b: 0 < a/b < 0.50 if b > 0 else True, "税负率0%~50%"),
    ("应收账款/营业收入≈赊销比", ["应收账款"], ["营业收入", "主营业务收入"], lambda a, b: True, "赊销占比"),
    ("存货/营业成本≈库存周转", ["存货"], ["营业成本", "主营业务成本"], lambda a, b: True, "存货与成本比"),
    ("货币资金/流动负债≈速动", ["货币资金"], ["流动负债"], lambda a, b: True, "现金覆盖短期债务"),
]

def _get_amt(tb_dict, keywords):
    for kw in keywords:
        for n, a in tb_dict.items():
            if kw in str(n) and "减值" not in str(n) and "坏账" not in str(n):
                return abs(a)
    return 0.0

def _analyze_cross_checks(tb):
    s, a, _, _ = _tb_cols(tb)
    if s is None or a is None: return pd.DataFrame()
    tb_dict = {}
    for _, r in tb.iterrows(): tb_dict[str(r[s]).strip()] = _sf(r[a])
    findings = []
    for rn, ak, bk, cf, note in CROSS_RULES:
        va = _get_amt(tb_dict, [rn.split("/")[0]] + ak)
        vb = _get_amt(tb_dict, bk)
        if va == 0 and vb == 0: continue
        ratio = round(va / vb, 4) if vb > 0 else None
        passed = cf(va, vb) if vb > 0 else True
        findings.append({"勾稽项": rn, "科目A值": round(va, 2), "科目B值": round(vb, 2),
                         "比值": ratio, "期望": note, "结果": "✅" if passed else "⚠️ 异常"})
    return pd.DataFrame(findings)


def _scan_red_flags(tb, journal=None):
    flags = []
    s, a, b, d = _tb_cols(tb)
    if s is None or a is None: return pd.DataFrame()
    if d is not None:
        for _, r in tb.iterrows():
            n = str(r[s]).strip(); amt = _sf(r[a]); dire = str(r.get(d, "")).strip()
            if dire == "借" and amt < -1:
                flags.append({"红旗类型": "方向异常", "科目": n, "说明": f"借方科目贷方余额({amt:,.0f})", "程度": "中"})
            elif dire == "贷" and amt > 1:
                flags.append({"红旗类型": "方向异常", "科目": n, "说明": f"贷方科目借方余额({amt:,.0f})", "程度": "中"})
    if b is not None:
        for _, r in tb.iterrows():
            n = str(r[s]).strip(); beg = _sf(r[b]); end = _sf(r[a])
            if abs(beg) > 10000 and abs(end) < 1:
                flags.append({"红旗类型": "余额清零", "科目": n, "说明": f"期初{beg:,.0f}→期末清零", "程度": "中"})
    for _, r in tb.iterrows():
        n = str(r[s]).strip(); amt = abs(_sf(r[a]))
        if amt >= 100000 and amt % 10000 == 0:
            flags.append({"红旗类型": "整数异常", "科目": n, "说明": f"余额为整万({amt:,.0f})", "程度": "低"})
    total = 0.0
    for _, r in tb.iterrows():
        if "资产总计" in str(r[s]): total = abs(_sf(r[a])); break
    if total > 0:
        for kw, th, desc in [("应收账款", 0.35, "应收>35%"), ("存货", 0.30, "存货>30%")]:
            for _, r in tb.iterrows():
                n = str(r[s]).strip(); amt = abs(_sf(r[a]))
                if kw in n and amt/total > th:
                    flags.append({"红旗类型": "结构异常", "科目": n, "说明": f"{desc}({amt/total:.0%})", "程度": "中"})
                    break
    seen = set(); unique = []
    for f in flags:
        k = (f["红旗类型"], f["科目"], f["说明"])
        if k not in seen: seen.add(k); unique.append(f)
    return pd.DataFrame(unique)


def _compute_risk_scores(fluct, cross, flags):
    scores = {}
    if not fluct.empty:
        for _, r in fluct.iterrows():
            pct = abs(float(r["变动率%"]))
            scores[str(r["科目"])] = scores.get(str(r["科目"]), 0) + (30 if pct >= 100 else 15 if pct >= 50 else 5)
    if not flags.empty:
        sv = {"高": 20, "中": 10, "低": 5}
        for _, r in flags.iterrows():
            scores[str(r["科目"])] = scores.get(str(r["科目"]), 0) + sv.get(str(r["程度"]), 5)
    if not scores: return pd.DataFrame()
    rows = []
    for n, sc in sorted(scores.items(), key=lambda x: -x[1]):
        rows.append({"科目": n, "风险分": sc, "综合评级": "🔴 高风险" if sc >= 30 else "🟡 关注" if sc >= 15 else "🟢 低风险"})
    return pd.DataFrame(rows)


def run_preaudit(tb, prior_tb=None, journal=None, output=None):
    """执行智能预审检查"""
    print("[预审引擎] 开始扫描...")
    print("  [1/4] 异常波动分析...")
    fluct = _analyze_fluctuations(tb, prior_tb)
    print(f"        发现 {len(fluct)} 项显著波动" + (" (缺上期TB)" if prior_tb is None else ""))
    print("  [2/4] 科目间勾稽校验...")
    cross = _analyze_cross_checks(tb)
    na = (cross["结果"] == "⚠️ 异常").sum() if not cross.empty else 0
    print(f"        检测 {len(cross)} 项，异常 {na} 项")
    print("  [3/4] 红旗标记扫描...")
    flags = _scan_red_flags(tb, journal)
    print(f"        标记 {len(flags)} 项红旗")
    print("  [4/4] 综合风险评分...")
    scores = _compute_risk_scores(fluct, cross, flags)
    hr = (scores["综合评级"] == "🔴 高风险").sum() if not scores.empty else 0
    print(f"        高风险科目 {hr} 个")
    result = {"异常波动分析": fluct, "科目间勾稽校验": cross, "红旗标记": flags, "综合风险评分": scores}
    if output:
        p = Path(output); p.parent.mkdir(parents=True, exist_ok=True)
        with pd.ExcelWriter(str(p), engine="openpyxl") as w:
            for sn, df in result.items():
                if not df.empty: df.to_excel(w, sheet_name=sn, index=False)
            summ = [{"检查项": "异常波动", "数量": len(fluct)}, {"检查项": "科目间勾稽", "数量": len(cross), "说明": f"异常{na}项"},
                    {"检查项": "红旗标记", "数量": len(flags)}, {"检查项": "高风险科目", "数量": hr}]
            pd.DataFrame(summ).to_excel(w, sheet_name="汇总", index=False)
        print(f"  ✅ 预审报告已保存: {p}")
    return result

