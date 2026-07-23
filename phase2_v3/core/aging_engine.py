#!/usr/bin/env python3
"""账龄分析引擎 — 复用 column_semantics 列识别 + 方向归一 + 标签层"""
from __future__ import annotations
import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd

DEFAULT_BUCKETS = [
    (0, 12, "1年以内"), (12, 24, "1-2年"), (24, 36, "2-3年"),
    (36, 60, "3-5年"), (60, 9999, "5年以上"),
]
DEFAULT_REF = "2025-12-31"


def run_aging_analysis(df, reference_date=DEFAULT_REF, date_col=None, amount_col=None,
                       direction_col=None, buckets=None, output_dir=None):
    """账龄分析主入口。返回 {"stats":{...}, "detail":DataFrame, "aging_summary":DataFrame}"""
    if buckets is None: buckets = DEFAULT_BUCKETS
    ref_date = pd.to_datetime(reference_date)

    # 1) 列识别（复用 column_semantics）
    from core.column_semantics import detect_column_roles, infer_amount_columns
    roles = detect_column_roles(df)
    if date_col is None: date_col = roles.get("date")
    if amount_col is None:
        amts = infer_amount_columns(df, max_cols=2)
        amount_col = amts[0] if amts else None
    use_dc = False
    if direction_col is None:
        if "debit" in roles and "credit" in roles:
            use_dc = True
        elif "direction" in roles:
            direction_col = roles["direction"]

    if date_col is None or date_col not in df.columns:
        raise ValueError(f"无法识别日期列: {list(df.columns)}")
    if amount_col is None or amount_col not in df.columns:
        num_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
        amount_col = num_cols[0] if num_cols else None
    if amount_col is None:
        raise ValueError(f"无法识别金额列: {list(df.columns)}")

    # 2) 清洗
    out = df.copy()
    out["_date"] = pd.to_datetime(out[date_col], errors="coerce")
    out["_amount"] = pd.to_numeric(
        out[amount_col].astype(str).str.replace(",","").str.replace("，","").str.replace("¥","").str.strip(),
        errors="coerce").fillna(0)

    # 方向归一（借贷双列）
    if use_dc and "debit" in roles and "credit" in roles:
        dc, cc = roles["debit"], roles["credit"]
        if dc in df.columns and cc in df.columns:
            d = pd.to_numeric(df[dc].astype(str).str.replace(",","").str.strip(), errors="coerce").fillna(0)
            c = pd.to_numeric(df[cc].astype(str).str.replace(",","").str.strip(), errors="coerce").fillna(0)
            out["_amount"] = d - c
    elif direction_col and direction_col in df.columns:
        dv = out[direction_col].astype(str).str.strip()
        out.loc[dv.str.contains("贷|付|支"), "_amount"] = -out["_amount"].abs()

    out = out[out["_amount"] != 0].copy()
    out = out[out["_date"].notna()].copy()
    if out.empty:
        return {"stats": {"total_rows":0,"total_amount":0}, "detail":pd.DataFrame(), "aging_summary":pd.DataFrame()}

    # 3) 标签
    out["_tag"] = ""
    for i, r in out.iterrows():
        s = str(r.get("summary", r.get("摘要", "")))
        for kw in ("冲正","冲销","红冲","撤销","调账"):
            if kw in s: out.at[i,"_tag"] = "冲正"; break

    # 4) 账龄(月)
    out["_aging_months"] = ((ref_date - out["_date"]).dt.days / 30.44).round(1).clip(lower=0)

    # 5) 分桶
    def bl(m):
        for lo, hi, lb in buckets:
            if lo <= m < hi: return lb
        return buckets[-1][2] if buckets else "?"
    out["_bucket"] = out["_aging_months"].apply(bl)

    # 6) 汇总
    rows = []
    for lo, hi, lb in buckets:
        sub = out[(out["_aging_months"]>=lo)&(out["_aging_months"]<hi)]
        rev = sub["_tag"]=="冲正"
        rows.append({"账龄区间":lb,"笔数":len(sub),"原币金额":round(float(sub["_amount"].sum()),2),
                      "冲正笔数":int(rev.sum()),"冲正金额":round(float(sub[rev]["_amount"].sum()),2)})
    ag = pd.DataFrame(rows)
    total = {"账龄区间":"合计","笔数":len(out),"原币金额":round(float(out["_amount"].sum()),2),
             "冲正笔数":int((out["_tag"]=="冲正").sum()),
             "冲正金额":round(float(out[out["_tag"]=="冲正"]["_amount"].sum()),2)}
    ag = pd.concat([ag, pd.DataFrame([total])], ignore_index=True)

    stats = {"total_rows":len(out),"total_amount":round(float(out["_amount"].sum()),2),
             "reference_date":reference_date,"date_col":date_col,"amount_col":amount_col,"buckets":len(buckets)}

    if output_dir:
        op = Path(output_dir); op.mkdir(parents=True, exist_ok=True)
        dc = [c for c in out.columns if not c.startswith("_")]
        out[dc+["_aging_months","_bucket","_tag"]].to_excel(str(op/"账龄分析明细.xlsx"), index=False)
        ag.to_excel(str(op/"账龄分析汇总.xlsx"), index=False)
        (op/"aging_summary.json").write_text(json.dumps({"stats":stats,"aging_summary":ag.to_dict("records")},
                                             ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    return {"stats":stats,"detail":out,"aging_summary":ag}


if __name__ == "__main__":
    import sys
    if len(sys.argv)<2:
        print("用法: python -m core.aging_engine <往来明细.xlsx> [资产负债表日] [输出目录]"); sys.exit(1)
    fp=sys.argv[1]; ref=sys.argv[2] if len(sys.argv)>2 else DEFAULT_REF
    odir=sys.argv[3] if len(sys.argv)>3 else "outputs/aging"
    df=pd.read_excel(fp); res=run_aging_analysis(df,reference_date=ref,output_dir=odir)
    print(f"行数:{res['stats']['total_rows']} 金额:{res['stats']['total_amount']:,.2f}")
    print(res["aging_summary"].to_string(index=False))