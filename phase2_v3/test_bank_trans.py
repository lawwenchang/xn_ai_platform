# -*- coding: utf-8 -*-
"""银行对账引擎端到端测试脚本（本地直跑，绕开平台，迭代用）"""
import sys, time
import pandas as pd

sys.path.insert(0, r"D:\Liu\ai_platform_code\phase2_v3")
from core.bank_reconcile_engine import reconcile_files,normalize_counterpart_name

BOOK = r"D:\audit_files\序时账_朱丽春（20260723）.xlsx"   # ← 按实际路径调整
BANK = r"D:\audit_files\银行流水_朱丽春（20260723）.xlsx"  # ← 按实际路径调整
OUT  = r"D:\test_out\Second"

# ═══════════ 1. 跑引擎 ═══════════
t0 = time.time()
res = reconcile_files(BOOK, BANK, out_dir=OUT,
                      config={"progress_callback": lambda p, s: print(f"[{p}%] {s}")})
st = res["stats"]
print(f"\n引擎耗时 {time.time()-t0:.1f}s，交付物目录: {OUT}")


# ═══════════ 2. 总览 ═══════════
print("\n═══════════ 总览 ═══════════")
print(f"行数      账={st['book_rows']}  银={st['bank_rows']}")
print(f"匹配率    账={st['book_match_rate']}%  银={st['bank_match_rate']}%")
print(f"日期粒度: {st.get('date_granularity')}")
print(f"账户过滤: {st['account_filter']}")

# ═══════════ 3. 分层命中（每轮迭代的核心观测） ═══════════
print("\n═══════════ 分层命中 ═══════════")
for k in ["matched_L1", "matched_L2", "matched_L3_subset",
          "matched_L3_month", "matched_L3_fee", "matched_L3_fee_month", "matched_L3_counterpart", "review_L4"]:
    if k in st:
        print(f"  {k:24s} = {st[k]}")
print(f"  未匹配 账={st['unmatched_book']}  银={st['unmatched_bank']}")
if "timing_categories" in st:
    print(f"  未达四分类: {st['timing_categories']}")

# ═══════════ 4. 未匹配聚类（形态发现的望远镜） ═══════════
print("\n═══════════ 账方未匹配聚类 ═══════════")
udf = pd.DataFrame(res["unmatched_book"])
if len(udf):
    print("按分类:")
    print(udf["classification"].value_counts().to_string())
    print("\n摘要高频（前12）:")
    print(udf["summary"].astype(str).str[:8].value_counts().head(12).to_string())
    print(f"\n金额: 中位数={udf['net_amount'].abs().median():,.0f}  "
          f"最大={udf['net_amount'].abs().max():,.0f}")

print("\n═══════════ 银方未匹配聚类 ═══════════")
kdf = pd.DataFrame(res["unmatched_bank"])
if len(kdf):
    print("摘要类型分布:")
    print(kdf["summary"].astype(str).value_counts().head(10).to_string())
    if "content_tag" in kdf.columns:
        print("\n标签分布:")
        print(kdf["content_tag"].replace("", "(无标签)").value_counts().head(8).to_string())

# ═══════════ 5. 对手方大额集群验证（账方提取 vs 银方） ═══════════
print("\n═══════════ 对手方集群 ═══════════")
book_std, bank_std = res["book_std"], res["bank_std"]
fee_kw = book_std[book_std["summary"].astype(str).str.contains("手续费")]
print("\n═══════════ 大户月度透视（前3名） ═══════════")
for name in ["天津恒运能源", "阳信康润商", "山东巨久运输"]:
    bs = book_std[book_std["counterpart"].astype(str).str.contains(name, na=False)]
    ks = bank_std[bank_std["counterpart"].astype(str).str.contains(name, na=False)]
    bm = bs.groupby(bs["date"].dt.to_period("M"))["net_cents"].sum() / 100
    km = ks.groupby(ks["date"].dt.to_period("M"))["net_cents"].sum() / 100
    cmp_t = pd.DataFrame({"账": bm, "银": km}).fillna(0).round(0).astype(int)
    cmp_t["差"] = cmp_t["账"] - cmp_t["银"]
    cmp_t["账笔数"] = bs.groupby(bs["date"].dt.to_period("M")).size()
    cmp_t["银笔数"] = ks.groupby(ks["date"].dt.to_period("M")).size()
    print(f"\n--- {name} ---")
    print(cmp_t.fillna(0))
print(f"关键词命中 {len(fee_kw)} 行")
print(fee_kw["content_tag"].value_counts())
for cp in ["阳信康润商", "天津恒运能", "山东巨久运"]:
    b_cp = book_std[book_std["counterpart"].str.contains(cp, na=False)]
    k_cp = bank_std[bank_std["counterpart"].str.contains(cp, na=False)]
    print(f"  {cp}: 账{len(b_cp)}笔/{b_cp['net_amount'].sum():>15,.0f}  "
          f"银{len(k_cp)}笔/{k_cp['net_amount'].sum():>15,.0f}")

print("\n═══════════ 红旗清单 ═══════════")
from collections import Counter
print(Counter(f.get("type") for f in res["red_flags"]))
"""
for f in res["red_flags"]:
    print(f"  [{f.get('type', '?')}] {str(f.get('detail', ''))[:90]}")
print(f"共 {len(res['red_flags'])} 面")

# ═══════════ 6. 月模式质检：可疑错配抽查 ═══════════
print("\n═══════════ L1/L2 错配质检（费用标签错配） ═══════════")
susp = 0
for m in res["matches_L1"] + res["matches_L2"]:
    bk = bank_std.loc[m["bank_idx"]]
    bj = book_std.loc[m["book_idx"]]
    if "费用" in str(bk.get("content_tag", "")) and "手续费" not in str(bj["summary"]) and "费用" not in str(bj["summary"]):
        susp += 1
        if susp <= 3:
            print(f"  可疑: 账{m['book_idx']}({bj['summary'][:15]}) ↔ 银{m['bank_idx']}(费用外收) {bj['net_amount']}")

print(f"  可疑错配共 {susp} 对" + ("（>0 建议降级复核）" if susp else "（干净）"))
"""
print("\n完成。交付物清单见输出目录。")
# 加进 test_bank.py 末尾跑
book_std, bank_std = res["book_std"], res["bank_std"]

fee_bank = bank_std[bank_std["content_tag"].str.contains("费用", na=False)]
print(f"银方费用标签行数: {len(fee_bank)}")
print(fee_bank["summary"].value_counts().head())

monthly = fee_bank.groupby(fee_bank["date"].dt.to_period("M"))["net_cents"].sum()
print("\n流水费用月度合计（分）:")
print(monthly.head(10))

book_fee = book_std[book_std["summary"].astype(str).str.contains("手续费|费用", na=False)]
print(f"\n账面手续费行数: {len(book_fee)}")
print(book_fee[["row_id", "date", "net_cents", "summary"]].head(10))
fee_b = book_std[book_std["summary"].astype(str).str.contains("手续费|费用", na=False)]
fee_k = bank_std[bank_std["content_tag"].str.contains("费用", na=False)]
b_m = fee_b.groupby(fee_b["date"].dt.to_period("M"))["net_cents"].sum()
k_m = fee_k.groupby(fee_k["date"].dt.to_period("M"))["net_cents"].sum()
cmp = pd.DataFrame({"账面": b_m, "流水": k_m}).fillna(0)
cmp["差"] = cmp["账面"] - cmp["流水"]
print(cmp)
print(f"\n差额≤1分的月份: {(cmp['差'].abs() <= 100).sum()} / {len(cmp)}")
print("\n═══════════ 剩余转存/转取 按对手方归集 ═══════════")
_zz = kdf[kdf["summary"].astype(str).str.strip().isin(["转存", "转取"])].copy()
_zz["_cp"] = _zz["counterpart"].map(normalize_counterpart_name)
_top = _zz.groupby("_cp").agg(笔数=("net_amount", "size"), 净额合计元=("net_amount", "sum"))
_top["净额合计元"] = _top["净额合计元"].round(0)
print(_top.sort_values("笔数", ascending=False).head(15))
print(f"\n无户名行数: {(_zz['_cp'].str.len() < 2).sum()} / {len(_zz)}")

# ═══════════ 7. 审计发现汇总（md 交付物） ═══════════
from collections import defaultdict
import os

print("\n═══════════ 生成审计发现汇总.md ═══════════")
rf = res["red_flags"]
by_type = defaultdict(list)
for f in rf:
    by_type[f.get("type", "其他")].append(f)

def _amt(f):
    import re
    m = re.findall(r"([\d,]+\.?\d*)元", str(f.get("detail", "")))
    return float(m[0].replace(",", "")) if m else 0.0

L = []
L.append("# 审计发现汇总（银行流水对账）\n")
L.append(f"- 账方匹配率 {st['book_match_rate']}%，银方匹配率 {st['bank_match_rate']}%")
L.append(f"- 分层: L1={st['matched_L1']} fee_month={st['matched_L3_fee_month']} "
         f"counterpart={st.get('matched_L3_counterpart')} 等")
L.append(f"- 未匹配: 账{st['unmatched_book']} / 银{st['unmatched_bank']}，红旗 {len(rf)} 面\n")

# 一、优先级最高：整月单边（账外收支嫌疑）
L.append("## 一、对手方整月单边记录（账外收支嫌疑，最高优先）\n")
gap = sorted(by_type.get("对手方整月单边记录", []), key=_amt, reverse=True)
for f in gap:
    L.append(f"- {f['detail']}")
L.append("")

# 二、费用账户存疑
L.append("## 二、费用小额月度差异（费用扣款账户存疑）\n")
for f in by_type.get("费用小额月度差异", []):
    L.append(f"- {f['detail']}")
L.append("")

# 三、整数大额：聚合呈现，不刷屏
big = by_type.get("整数大额", [])
if big:
    big_s = sorted(big, key=_amt, reverse=True)
    L.append(f"## 三、整数大额交易（共 {len(big)} 笔，列前 20）\n")
    for f in big_s[:20]:
        L.append(f"- {f['detail']}")
    if len(big_s) > 20:
        L.append(f"- ……其余 {len(big_s)-20} 笔见底稿《异常资金交易清单》")
    L.append("")

# 四、其余红旗
L.append("## 四、其他红旗\n")
for t, fs in by_type.items():
    if t in ("对手方整月单边记录", "费用小额月度差异", "整数大额"):
        continue
    L.append(f"### {t}（{len(fs)} 项）")
    for f in fs[:10]:
        L.append(f"- {f['detail']}")
    L.append("")

# 五、未匹配归集（审计程序入口）
L.append("## 五、剩余转存/转取归集（抽凭/核查入口）\n")
L.append(_top.sort_values("笔数", ascending=False).head(15).to_markdown())
L.append("\n\n> 处置建议：一节逐项核查原始凭证与期后流水；二节索取其他银行账户流水；"
         "三节抽样；五节大额抽凭+函证。未达账项见《余额调节表》。")

md_path = os.path.join(OUT, "审计发现汇总.md")
with open(md_path, "w", encoding="utf-8") as fp:
    fp.write("\n".join(L))
print(f"已写出: {md_path}")

# ═══════════ 8. 未匹配分类核查清单（Excel 交付物） ═══════════
print("\n═══════════ 生成未匹配分类核查清单.xlsx ═══════════")

_FEE_WORDS = ("手续费", "收费", "短信费", "年费", "账户管理费", "工本费", "服务费")
_TRANSFER_JUNK = ("转账", "转入", "农行", "中行", "农商行", "银行询证函")

def _band(amt):
    a = abs(amt)
    return "≥100万" if a >= 1_000_000 else ("10-100万" if a >= 100_000 else "<10万")

def _cls_book(r):
    s, cp = str(r.get("summary", "")), str(r.get("counterpart", ""))
    if r.get("special") == "银行费用" or any(w in s for w in _FEE_WORDS):
        return "①费用账户存疑"
    if any(w in cp for w in _TRANSFER_JUNK):
        return "②银行间互转（非往来户）"
    if r.get("classification") not in (None, "待人工核查"):
        return "③未达账项候选"
    if len(cp.strip()) >= 2:
        return "④对手方待抽凭"
    return "⑤其他待人工"

def _cls_bank(r):
    s, cp = str(r.get("summary", "")), str(r.get("counterpart", ""))
    if s.strip() in ("费用外收", "批量扣费"):
        return "①费用类"
    if "退回" in s:
        return "②汇款退回等特殊业务"
    if r.get("classification") not in (None, "待人工核查"):
        return "③未达账项候选"
    if len(cp.strip()) >= 2:
        return "④对手方待核查（抽凭）"
    return "⑤其他待人工"

udf2 = pd.DataFrame(res["unmatched_book"])
kdf2 = pd.DataFrame(res["unmatched_bank"])

for df, fn, side in ((udf2, _cls_book, "账"), (kdf2, _cls_bank, "银")):
    if len(df):
        df["核查分类"] = df.apply(fn, axis=1)
        df["金额带"] = df["net_amount"].map(_band)
        df = df.sort_values(["核查分类", "net_amount"],
                            ascending=[True, False])
        df["net_amount"] = df["net_amount"].round(2)

cols = ["row_id", "date", "summary", "counterpart", "net_amount",
        "金额带", "classification", "basis", "核查分类"]
udf2 = udf2[[c for c in cols if c in udf2.columns]] if len(udf2) else udf2
kdf2 = kdf2[[c for c in cols if c in kdf2.columns]] if len(kdf2) else kdf2

# 汇总看板：分类 × 笔数/金额
def _board(df, side):
    if not len(df):
        return pd.DataFrame(columns=["侧", "核查分类", "笔数", "金额合计", "处置建议"])
    g = df.groupby("核查分类")["net_amount"].agg(笔数="size", 金额合计="sum").reset_index()
    g.insert(0, "侧", side)
    g["金额合计"] = g["金额合计"].round(2)
    return g

board = pd.concat([_board(udf2, "账方"), _board(kdf2, "银方")], ignore_index=True)
_ADV = {
    "①费用账户存疑": "对照《费用小额月度差异》红旗，索取其他账户流水核对扣款账户",
    "①费用类": "同左；与账方费用按月比对，差额进调节表或核查",
    "②银行间互转（非往来户）": "核对企业全部银行账户，确认是否他户互转漏记",
    "②汇款退回等特殊业务": "逐笔核查原始回单",
    "③未达账项候选": "期后1-2个月对账单验证，进《余额调节表》",
    "④对手方待抽凭": "大额逐笔抽凭+函证；小额分析程序+抽样",
    "④对手方待核查（抽凭）": "同左；优先核查《整月单边记录》红旗涉及户名",
    "⑤其他待人工": "人工逐笔判断",
}
board["处置建议"] = board["核查分类"].map(_ADV)

# 红旗 sheet
rf_df = pd.DataFrame([{"类型": f.get("type"), "侧": f.get("side"),
                       "说明": f.get("detail", "")} for f in res["red_flags"]])

xl_path = os.path.join(OUT, "未匹配分类核查清单.xlsx")
with pd.ExcelWriter(xl_path, engine="openpyxl") as xw:
    board.to_excel(xw, sheet_name="01_汇总看板", index=False)
    udf2.to_excel(xw, sheet_name="02_账方未匹配", index=False)
    kdf2.to_excel(xw, sheet_name="03_银方未匹配", index=False)
    _top.sort_values("笔数", ascending=False).to_excel(xw, sheet_name="04_对手方归集")
    rf_df.to_excel(xw, sheet_name="05_红旗清单", index=False)
    # 列宽
    for ws in xw.book.worksheets:
        for col in ws.columns:
            w = max(len(str(c.value)) if c.value is not None else 0 for c in col[:200])
            ws.column_dimensions[col[0].column_letter].width = min(max(w + 2, 10), 60)

fs = bank_std["summary"].astype(str).str.strip()
print(fs.value_counts().head(10))
print("费用外收占比:", (fs == "费用外收").mean())

# 探针P1：账方账户分布（是否混了别的银行账户）
print("\n[P1] 账方账户分布:")
print(book_std["account"].astype(str).value_counts().head(10))

# 探针P2：同额对的日期差分布（账面滞后几天？）
import numpy as np
_b = book_std[~book_std.index.isin(set())]  # 全量看
pairs = []
bk = bank_std.groupby(bank_std["net_cents"])
for c, bg in book_std.groupby(book_std["net_cents"]):
    if c in bk.groups and len(bg) <= 5 and len(bk.groups[c]) <= 5:
        for d1 in bg["date"]:
            for d2 in bank_std.loc[bk.groups[c], "date"]:
                if pd.notna(d1) and pd.notna(d2):
                    pairs.append((d1 - d2).days)
if pairs:
    dd = pd.Series(pairs)
    print("\n[P2] 日期差分布（账-银，天）:")
    print(dd.value_counts().sort_index().head(15))
    print(f"中位滞后: {dd.median():.0f} 天, |dd|<=3占比: {(dd.abs()<=3).mean():.1%}")

# 探针P3：费用外收是否和转账行成对出现
fs = bank_std["summary"].astype(str).str.strip()
fee_mask = fs == "费用外收"
print(f"\n[P3] 费用外收 {fee_mask.sum()} 行，占 {fee_mask.mean():.1%}")
print("费用外收金额分布:", bank_std.loc[fee_mask, "net_amount"].abs().describe().round(2).to_dict())

# 诊断D1：费用月度对比（fee_month 为什么闭不上）
print("\n[D1] 费用月度对比:")
fee_b = book_std[book_std["summary"].astype(str).str.contains("手续费|收费|服务费|年费|短信费|账户管理费|工本费", na=False)]
fee_k = bank_std[bank_std["summary"].astype(str).str.strip() == "费用外收"]
print(f"账方费用行 {len(fee_b)} 笔，银方费用外收 {len(fee_k)} 笔")
bm = fee_b.groupby(fee_b["date"].dt.to_period("M"))["net_cents"].sum() / 100
km = fee_k.groupby(fee_k["date"].dt.to_period("M"))["net_cents"].sum() / 100
cmp_f = pd.DataFrame({"账": bm, "银": km}).fillna(0).round(0)
cmp_f["差"] = cmp_f["账"] - cmp_f["银"]
cmp_f["账笔数"] = fee_b.groupby(fee_b["date"].dt.to_period("M")).size()
cmp_f["银笔数"] = fee_k.groupby(fee_k["date"].dt.to_period("M")).size()
print(cmp_f.fillna(0).head(30))

# 诊断D2：账方未匹配的成分
print("\n[D2] 账方未匹配聚类:")
udf3 = pd.DataFrame(res["unmatched_book"])
print("摘要高频前10:")
print(udf3["summary"].astype(str).str[:10].value_counts().head(10).to_string())
udf3["_cp"] = udf3["counterpart"].map(normalize_counterpart_name)
print("\n对手方归集前10:")
print(udf3.groupby("_cp")["net_amount"].agg(["size", "sum"]).sort_values("size", ascending=False).head(10).round(0).to_string())
print(f"\n无户名行数: {(udf3['_cp'].str.strip().str.len() < 2).sum()} / {len(udf3)}")