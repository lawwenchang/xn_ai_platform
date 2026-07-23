# -*- coding: utf-8 -*-
"""验证"合并记账"假说：同日同方向流水合计能否命中账面单笔"""
import sys, time
import pandas as pd

sys.path.insert(0, r"项目根目录")          # ← 改成你的项目根目录（core/ 的上一级）
from core.bank_reconcile_engine import reconcile_files

BOOK = r"E:\xwechat_files\wxid_3gxk7jel715h22_8cb0\msg\file\2026-07\序时账1（巨久-农行5927）20260716.xlsx"   # ← 改成实际路径
BANK = r"E:\xwechat_files\wxid_3gxk7jel715h22_8cb0\msg\file\2026-07\银行流水1（巨久-所有银行）20260716.xlsx"  # ← 改成实际路径
res = reconcile_files(BOOK, BANK, out_dir=r"D:\test_out",
                      config={"progress_callback": lambda p, s: print(f"[{p}%] {s}")})
# 第一步：跑引擎，拿到结果对象 res
t0 = time.time()

print(f"引擎耗时 {time.time()-t0:.1f}s")

# res 是 dict，主要键：
#   stats                统计（匹配率、各层命中数、账户过滤说明）
#   book_std / bank_std  标准化后的两侧明细（含 date / net_cents / net_amount 等列）
#   unmatched_book / unmatched_bank / red_flags / balance_reconciliation ...
st = res["stats"]
print(f"匹配率  账={st['book_match_rate']}%  银={st['bank_match_rate']}%")
print(f"L1={st['matched_L1']}  L2={st['matched_L2']}  L3组={st['matched_L3_groups']}  待复核L4={st['review_L4']}")
print(f"账户过滤: {st['account_filter']}")
print(f"未匹配: 账{st['unmatched_book']}  银{st['unmatched_bank']}")

# 第二步：合并记账验证
book_std = res["book_std"].copy()
bank_std = res["bank_std"].copy()

# 日期转 datetime，丢掉解析失败的行（NaT 行无法按日聚合）
bank_std["date"] = pd.to_datetime(bank_std["date"], errors="coerce")
bank_valid = bank_std.dropna(subset=["date"])

# 流水按 日期+方向（收/付）分组求和
daily = bank_valid.groupby(
    [bank_valid["date"].dt.date, bank_valid["net_cents"].gt(0)]
)["net_cents"].sum()

# 账面所有单笔金额（分）
book_cents = set(pd.to_numeric(book_std["net_cents"], errors="coerce").dropna().astype(int))

# 命中：某个"日期×方向"的流水合计，恰好等于账面某一笔
hits = daily[daily.isin(book_cents)]

print("\n===== 合并记账验证 =====")
print(f"流水有效行数（日期可解析）: {len(bank_valid)} / {len(bank_std)}")
print(f"日期×方向分组数: {len(daily)}")
print(f"其中合计金额能命中账面单笔的: {len(hits)} 组")
print(hits.head(10))

# 进一步：这些命中组覆盖了多少笔流水
if len(hits):
    cover = bank_valid[
        bank_valid["date"].dt.date.isin({d for d, _ in hits.index})
    ]
    print(f"涉及流水约 {len(cover)} 笔（粗略口径，含同方向未命中组）")