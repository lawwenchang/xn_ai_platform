#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""现金流量表自动生成器 - 间接法"""
from __future__ import annotations
from pathlib import Path
import pandas as pd

def _sf(v):
    try: return float(pd.to_numeric(v, errors="coerce") or 0)
    except: return 0.0

def _tb_cols(tb):
    s = a = b = None
    for c in tb.columns:
        cs = str(c)
        if "科目名称" in cs and "辅助" not in cs: s = str(c)
        if ("期末余额" in cs or ("余额" in cs and "期初" not in cs and "累计" not in cs)) and a is None: a = str(c)
        if any(kw in cs for kw in ["期初余额", "年初余额"]): b = str(c)
    return s, a, b

def _build(tb, sc, ac, bc=None):
    d = {}
    for _, r in tb.iterrows():
        n = str(r[sc]).strip()
        d[n] = (_sf(r[ac]), _sf(r[bc]) if bc else 0.0)
    return d

def _find(tb_dict, keywords):
    for kw in keywords:
        for n, (end, beg) in tb_dict.items():
            if kw in str(n) and "减值" not in str(n) and "坏账" not in str(n):
                return end, beg
    return 0.0, 0.0

def _find_name(tb_dict, keywords):
    """返回(期末, 期初, 匹配到的科目名称)"""
    for kw in keywords:
        for name, (end, beg) in tb_dict.items():
            if kw in str(name) and "减值" not in str(name) and "坏账" not in str(name):
                return end, beg, name
    return 0.0, 0.0, None

def generate_cashflow(tb, prior_tb=None, output=None):
    """间接法生成现金流量表，每行标注数据来源TB科目"""
    sc, ac, bc = _tb_cols(tb)
    if sc is None or ac is None: raise ValueError("TB缺少科目名称或期末余额列")
    curr = _build(tb, sc, ac, bc)
    prior = _build(prior_tb, sc, ac, bc) if prior_tb is not None else {}

    def chg_info(kws, sign=1):
        """变动额 + 来源说明"""
        ce, cb, cn = _find_name(curr, kws)
        pe, pb, pn = _find_name(prior, kws)
        src = cn or pn or kws[0]
        if prior_tb is not None and cb != 0 and pe != 0 and abs(cb - pe) < abs(ce - pe) * 2:
            val = (ce - cb) * sign
            method = f"期末{ce:,.0f}-期初{cb:,.0f}" if abs(val) > 0 else ""
        else:
            val = (ce - cb) * sign
            method = f"期末{ce:,.0f}-期初{cb:,.0f}" if abs(val) > 0 else ""
        return val, src, method

    def bal_info(kws, sign=1):
        """期末余额 + 来源说明"""
        e, _, n = _find_name(curr, kws)
        return e * sign, n or kws[0], f"期末余额{e:,.0f}"

    lines = []
    # ═══ 一、经营活动 ═══
    lines.append({"项目": "一、经营活动产生的现金流量", "金额": "", "来源TB科目": "", "取数说明": ""})
    np_val, np_src, np_msg = bal_info(["净利润"]); lines.append({"项目": "  净利润", "金额": round(np_val, 2), "来源TB科目": np_src, "取数说明": np_msg})
    adj_total = 0.0
    # 关键词顺序：P&L科目在前（匹配到→用期末余额），累积科目在后（降级→用变动额）
    for label, kws, sign in [("资产减值准备", ["资产减值损失", "坏账准备", "减值准备"], 1),
                              ("固定资产折旧", ["折旧费", "固定资产折旧", "累计折旧"], 1),
                              ("无形资产摊销", ["无形资产摊销", "摊销费", "累计摊销"], 1),
                              ("长期待摊费用摊销", ["长期待摊费用摊销", "待摊费用摊销", "长期待摊费用"], 1),
                              ("财务费用", ["利息支出", "利息费用", "财务费用"], 1),
                              ("投资损失", ["投资损失", "投资收益"], -1)]:
        # 先搜到哪个就用哪个；含"累计"/"坏账"/"准备"→变动额，否则→期末余额
        # 注：长期待摊费用摊销的关键词降级匹配到BS科目"长期待摊费用"时也需要变动额（期末-期初）
        # chg_info对P&L科目同样是正确的（期初=0，期末-0=期末）
        v, src, m = 0, kws[0], ""
        if any("累计" in kw or "坏账" in kw or "准备" in kw for kw in kws) or label == "长期待摊费用摊销":
            v, src, m = chg_info(kws, sign)
        else:
            v, src, m = bal_info(kws, sign)
        if abs(v) > 1: adj_total += v; lines.append({"项目": f"  加：{label}", "金额": round(v, 2), "来源TB科目": src, "取数说明": m})
    wc_total = 0.0
    for label, kws, sign in [("经营性应收增加(减现金)", ["应收账款", "应收票据", "预付账款", "其他应收款"], -1),
                              ("存货增加(减现金)", ["存货"], -1),
                              ("经营性应付增加(增现金)", ["应付账款", "应付票据", "预收账款", "其他应付款", "应付职工薪酬", "应交税费"], 1)]:
        for kw in kws:
            v, src, m = chg_info([kw], sign)
            if abs(v) > 1: wc_total += v; lines.append({"项目": f"  {label}-{kw}", "金额": round(v, 2), "来源TB科目": src, "取数说明": m})
    operating = np_val + adj_total + wc_total  # 净利润 + 调整项目 + 营运资金变动
    lines.append({"项目": "  经营活动现金净流量", "金额": round(operating, 2), "来源TB科目": "∑上述", "取数说明": ""})

    # ═══ 二、投资活动 ═══
    lines.append({"项目": "", "金额": "", "来源TB科目": "", "取数说明": ""})
    lines.append({"项目": "二、投资活动产生的现金流量", "金额": "", "来源TB科目": "", "取数说明": ""})
    invest = 0.0
    for label, kws in [("购建固定资产等", ["固定资产", "在建工程", "无形资产"]), ("投资支出", ["长期股权投资", "交易性金融资产"])]:
        for kw in kws:
            v, src, m = chg_info([kw], -1)
            if abs(v) > 1: invest += v; lines.append({"项目": f"  减：{label}-{kw}", "金额": round(v, 2), "来源TB科目": src, "取数说明": m})
    lines.append({"项目": "  投资活动现金净流量", "金额": round(invest, 2), "来源TB科目": "∑上述", "取数说明": ""})

    # ═══ 三、筹资活动 ═══
    lines.append({"项目": "", "金额": "", "来源TB科目": "", "取数说明": ""})
    lines.append({"项目": "三、筹资活动产生的现金流量", "金额": "", "来源TB科目": "", "取数说明": ""})
    finance = 0.0
    for label, kws, sign in [("借款净增加", ["短期借款", "长期借款"], 1), ("吸收投资", ["实收资本", "资本公积"], 1),
                              ("分配股利付息", ["应付股利", "应付利息", "利润分配"], -1)]:
        for kw in kws:
            v, src, m = chg_info([kw], sign)
            if abs(v) > 1: finance += v; lines.append({"项目": f"  {'加' if sign>0 else '减'}：{label}-{kw}", "金额": round(v, 2), "来源TB科目": src, "取数说明": m})
    lines.append({"项目": "  筹资活动现金净流量", "金额": round(finance, 2), "来源TB科目": "∑上述", "取数说明": ""})

    # ═══ 勾稽验证 ═══
    total = operating + invest + finance
    # 现金 = 库存现金 + 银行存款 + 其他货币资金
    cash_items = ["库存现金", "银行存款", "其他货币资金"]
    cash_beg = 0.0; cash_end = 0.0; beg_srcs = []; end_srcs = []
    for kw in cash_items:
        e, b, n = _find_name(curr, [kw])
        if n:
            cash_end += e; cash_beg += b
            end_srcs.append(f"{n}(期末{e:,.0f})")
            beg_srcs.append(f"{n}(期初{b:,.0f})")
    cash_chg = cash_end - cash_beg
    diff = round(total - cash_chg, 2)
    lines.append({"项目": "", "金额": "", "来源TB科目": "", "取数说明": ""})
    lines.append({"项目": "现金净增加额（推算）", "金额": round(total, 2), "来源TB科目": "∑三大活动", "取数说明": ""})
    lines.append({"项目": "  TB现金余额(期初)", "金额": round(cash_beg, 2), "来源TB科目": "; ".join(beg_srcs), "取数说明": ""})
    lines.append({"项目": "  TB现金余额(期末)", "金额": round(cash_end, 2), "来源TB科目": "; ".join(end_srcs), "取数说明": ""})
    lines.append({"项目": "  TB现金变动", "金额": round(cash_chg, 2), "来源TB科目": "期末-期初", "取数说明": ""})
    lines.append({"项目": "  勾稽差异", "金额": diff, "来源TB科目": "推算-TB变动", "取数说明": "⚠️ 需复核" if abs(diff) > max(cash_end * 0.01, 1000) else "✅ 一致"})

    df = pd.DataFrame(lines)
    df = df[df["金额"] != ""].reset_index(drop=True)
    if output:
        p = Path(output); p.parent.mkdir(parents=True, exist_ok=True)
        df.to_excel(str(p), index=False)
        print(f"[现金流] 已生成: {p}")
        print(f"  经营CF: {operating:,.0f} | 投资CF: {invest:,.0f} | 筹资CF: {finance:,.0f}")
        print(f"  推算: {total:,.0f} | TB现金变动: {cash_chg:,.0f} | 差异: {diff:,.0f}")
    return df
