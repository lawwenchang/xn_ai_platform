#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audit_sampling 抽样测试：MUS 正确性/可复现性/错报推断/随机/分层"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np
import pandas as pd
from core.audit_sampling import (mus_sample, evaluate_mus, random_sample,
                                 stratified_sample, run_sampling,
                                 MUS_RELIABILITY_FACTORS)

ok = lambda msg: print(f"  [OK] {msg}")

# 1) MUS 基本正确性：间隔 = 总体/sample_size；金额≥间隔必中
df = pd.DataFrame({"编号": range(1, 101), "金额": [1000] * 95 + [60000, 0, -500, 800, 2500]})
r = mus_sample(df, "金额", sample_size=10, seed=7)
assert r.population_size == 98           # 100 行中 0 和 -500 剔除 → 98
assert r.excluded_count == 2
assert r.interval == round(158300 / 10, 2)  # 95000+60000+800+2500=158300
ok("MUS 总体剔除零/负金额 + 间隔计算")

# 2) 高层项目必中：60000 ≥ 间隔(15830) → 必在样本
assert any(i["金额"] == 60000 for i in r.items) and r.top_stratum_count == 1
ok("高层项目（≥抽样间隔）必中")

# 3) 可复现性：同种子同结果
r2 = mus_sample(df, "金额", sample_size=10, seed=7)
assert [i["编号"] for i in r.items] == [i["编号"] for i in r2.items]
assert r.random_start == r2.random_start
r3 = mus_sample(df, "金额", sample_size=10, seed=99)
assert r.random_start != r3.random_start or [i["编号"] for i in r.items] != [i["编号"] for i in r3.items]
ok("固定种子可复现，换种子结果不同")

# 4) 显式间隔与可容忍错报推间隔
r4 = mus_sample(df, "金额", interval=10000, seed=7)
assert r4.interval == 10000
r5 = mus_sample(df, "金额", tolerable_misstatement=30000, risk_pct=5.0, seed=7)
assert r5.interval == round(30000 / MUS_RELIABILITY_FACTORS[5.0][0], 2)  # 30000/3.0
ok("间隔参数优先级（显式 > 可容忍错报/可靠因子 > 总体/样本量）")

# 5) 错报推断（tainting 法）
#    间隔10000；样本错报：账面5000→4000（taint 0.2）；账面20000→19000（高层，实际错报1000）
ev = evaluate_mus([{"book": 5000, "audited": 4000},
                   {"book": 20000, "audited": 19000}], interval=10000, risk_pct=5.0)
assert ev["projected_misstatement"] == round(0.2 * 10000 + 1000, 2)  # 3000
assert ev["basic_precision"] == 30000.0                              # 10000*3.00
assert ev["incremental_allowance"] == round(10000 * 0.2 * 0.75, 2)   # 1500
assert ev["upper_misstatement_bound"] == 3000 + 30000 + 1500
ok("MUS 错报上限推断（推断+基本界限+递增界限）")

# 6) 随机抽样：样本量与可复现
rs = random_sample(df, 15, seed=1)
assert rs.sample_size == 15
rs2 = random_sample(df, 15, seed=1)
assert [i["编号"] for i in rs.items] == [i["编号"] for i in rs2.items]
ok("简单随机抽样（固定种子）")

# 7) 分层抽样：每层等额
sdf = pd.DataFrame({"科目": ["应收"] * 10 + ["应付"] * 6 + ["费用"] * 4,
                    "金额": range(100, 120)})
st = stratified_sample(sdf, "科目", per_stratum=3, seed=1)
cnt = {}
for i in st.items:
    cnt[i["科目"]] = cnt.get(i["科目"], 0) + 1
assert cnt == {"应收": 3, "应付": 3, "费用": 3}, cnt
ok("分层抽样（每层等额，层不足全取）")

# 8) 统一入口
out = run_sampling(df, "金额", method="monetary_unit", sample_size=10, seed=7)
assert out["sample_size"] == r.sample_size and "summary_text" in out
ok("run_sampling 统一入口")

print("\n全部通过：audit_sampling 符合 CSA1314 的抽样与评价")
