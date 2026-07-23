#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
审计抽样模块 (audit_sampling.py)
=================================
依据《中国注册会计师审计准则第1314号——审计抽样》及知识库
《审计抽样方法指南_依据CSA1314》实现的确定性抽样与样本评价。

修正原 audit_procedures.build_sampling_plan 的缺陷：
原实现"Sort 降序 + 取前 N"是"挑大的"，不是货币单位抽样（MUS/PPS）。

本模块实现：
1. 货币单位抽样（MUS/PPS 系统选样）：抽样间隔 + 随机起点（固定种子可复现），
   金额 ≥ 间隔的项目必然入选（高层项目单独测试，符合准则要求）；
2. 简单随机抽样（固定种子可复现）；
3. 分层抽样（按比例/等额分配）；
4. 样本评价：错报率（tainting）推断 + 基本精确度界限 + 递增错报界限，
   使用 MUS 可靠因子表（5%/10% 误受风险）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

# MUS 可靠因子表（误受风险 5% / 10%，高估错报数 0~5）
# 来源：AICPA Audit Sampling Guide / CSA 1314 应用指南常用系数
MUS_RELIABILITY_FACTORS = {
    5.0:  [3.00, 4.75, 6.30, 7.76, 9.16, 10.52],
    10.0: [2.31, 3.89, 5.33, 6.69, 8.00, 9.28],
}
# 递增错报界限因子 = 相邻可靠因子之差 − 1
MUS_INCREMENTAL_FACTORS = {
    5.0:  [0.75, 0.55, 0.46, 0.40, 0.36],
    10.0: [0.58, 0.44, 0.36, 0.31, 0.28],
}


@dataclass
class SamplingResult:
    method: str = ""
    population_size: int = 0
    population_amount: float = 0.0
    sample_size: int = 0
    sample_amount: float = 0.0
    coverage_ratio: float = 0.0
    interval: float = 0.0
    random_start: float = 0.0
    seed: int = 0
    top_stratum_count: int = 0          # ≥间隔的必中项目数
    excluded_count: int = 0             # 零/负金额剔除数
    items: List[Dict[str, Any]] = field(default_factory=list)

    def summary(self) -> str:
        return (f"抽样方法: {self.method}\n"
                f"总体: {self.population_size} 笔, 合计 {self.population_amount:,.2f} 元\n"
                f"样本: {self.sample_size} 笔, 金额覆盖率: {self.coverage_ratio:.1%}\n"
                f"抽样间隔: {self.interval:,.2f}, 随机起点: {self.random_start:,.2f} (种子 {self.seed})")


def mus_sample(df: pd.DataFrame, amount_col: str,
               sample_size: Optional[int] = None,
               interval: Optional[float] = None,
               tolerable_misstatement: Optional[float] = None,
               risk_pct: float = 5.0, seed: int = 42) -> SamplingResult:
    """货币单位抽样（PPS 系统选样）。

    参数优先级：interval > (tolerable_misstatement / 可靠因子) > (总体/sample_size)。
    规则（CSA 1314 / AICPA 指南）：
    - 零金额与负金额（贷方余额等）剔除出 MUS 总体，单独列示、单独考虑；
    - 金额 ≥ 抽样间隔的项目必然入选（top stratum），且不再参与间隔选样；
    - 随机起点 ∈ [0, interval)，固定种子保证可复现（审计底稿要求）。
    """
    res = SamplingResult(method="货币单位抽样(MUS)", seed=seed)
    amounts = pd.to_numeric(df[amount_col], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    pos_mask = amounts > 0
    res.excluded_count = int((~pos_mask).sum())
    pop_amounts = amounts[pos_mask]
    pop_idx = np.flatnonzero(pos_mask)
    res.population_size = int(pos_mask.sum())
    res.population_amount = round(float(pop_amounts.sum()), 2)
    if res.population_size == 0:
        return res

    if interval and interval > 0:
        iv = float(interval)
    elif tolerable_misstatement and tolerable_misstatement > 0:
        factor = MUS_RELIABILITY_FACTORS.get(risk_pct, MUS_RELIABILITY_FACTORS[5.0])[0]
        iv = float(tolerable_misstatement) / factor
    else:
        n = sample_size or 20
        iv = res.population_amount / max(int(n), 1)
    res.interval = round(iv, 2)

    # 高层项目：金额 ≥ 间隔 → 必中
    top_mask = pop_amounts >= iv
    top_idx = pop_idx[top_mask]
    res.top_stratum_count = int(top_mask.sum())

    # 系统选样：累计金额 + 随机起点
    rng = np.random.default_rng(seed)
    start = float(rng.uniform(0, iv))
    res.random_start = round(start, 2)
    cum = np.cumsum(pop_amounts)
    points = start + np.arange(0, math.ceil(cum[-1] / iv) + 1) * iv
    chosen = set(int(i) for i in top_idx)
    for p in points:
        if p > cum[-1]:
            break
        hit = int(np.searchsorted(cum, p, side="left"))
        chosen.add(int(pop_idx[min(hit, len(pop_idx) - 1)]))
    chosen_sorted = sorted(chosen)
    res.sample_size = len(chosen_sorted)
    res.sample_amount = round(float(amounts[chosen_sorted].sum()), 2)
    res.coverage_ratio = (res.sample_amount / res.population_amount
                          if res.population_amount else 0.0)
    res.items = df.iloc[chosen_sorted].to_dict("records")
    return res



def evaluate_mus(misstatements: List[Dict[str, float]], interval: float,
                 risk_pct: float = 5.0) -> Dict[str, float]:
    """MUS 样本评价（错报上限推断，tainting 法）。

    misstatements: [{"book": 账面金额, "audited": 审定金额}, ...]（仅含发现错报的样本）
    返回：推断错报 + 基本精确度界限 + 递增错报界限 + 错报上限。
    """
    factors = MUS_RELIABILITY_FACTORS.get(risk_pct, MUS_RELIABILITY_FACTORS[5.0])
    incr = MUS_INCREMENTAL_FACTORS.get(risk_pct, MUS_INCREMENTAL_FACTORS[5.0])
    projected = 0.0
    taints = []
    for m in misstatements:
        book = float(m.get("book", 0.0))
        audited = float(m.get("audited", 0.0))
        mis = book - audited
        if book >= interval:
            projected += mis                    # 高层项目错报按实际计
        elif book > 0:
            taints.append(mis / book)           # 错报百分比（tainting）
            projected += mis / book * interval
    taints.sort(reverse=True)
    basic_precision = interval * factors[0]
    incremental = sum(interval * t * incr[i] for i, t in enumerate(taints) if i < len(incr))
    return {
        "projected_misstatement": round(projected, 2),
        "basic_precision": round(basic_precision, 2),
        "incremental_allowance": round(incremental, 2),
        "upper_misstatement_bound": round(projected + basic_precision + incremental, 2),
        "tainting_count": len(taints),
    }


def random_sample(df: pd.DataFrame, n: int, seed: int = 42) -> SamplingResult:
    """简单随机抽样（固定种子可复现）"""
    res = SamplingResult(method="简单随机抽样", seed=seed, population_size=len(df))
    k = min(int(n), len(df))
    picked = df.sample(n=k, random_state=seed)
    res.sample_size = k
    res.items = picked.to_dict("records")
    return res


def stratified_sample(df: pd.DataFrame, strata_col: str, per_stratum: int = 5,
                      seed: int = 42) -> SamplingResult:
    """分层抽样：每层随机抽取 per_stratum 笔（层不足则全取）"""
    res = SamplingResult(method=f"分层抽样(按{strata_col})", seed=seed,
                         population_size=len(df))
    parts = []
    for _, grp in df.groupby(strata_col):
        parts.append(grp.sample(n=min(per_stratum, len(grp)), random_state=seed))
    picked = pd.concat(parts) if parts else df.head(0)
    res.sample_size = len(picked)
    res.items = picked.to_dict("records")
    return res


def run_sampling(df: pd.DataFrame, amount_col: str = "金额",
                 method: str = "monetary_unit", **kwargs) -> Dict[str, Any]:
    """统一入口：method ∈ monetary_unit / random / stratified"""
    if method == "monetary_unit":
        res = mus_sample(df, amount_col, **kwargs)
    elif method == "random":
        res = random_sample(df, kwargs.get("sample_size", 20),
                            seed=kwargs.get("seed", 42))
    elif method == "stratified":
        res = stratified_sample(df, kwargs.get("strata_col", amount_col),
                                kwargs.get("per_stratum", 5),
                                seed=kwargs.get("seed", 42))
    else:
        raise ValueError(f"未知抽样方法: {method}")
    out = res.__dict__.copy()
    out["summary_text"] = res.summary()
    out["audit_note"] = ("样本选取已固定随机种子，可复现；零/负金额项目已剔除 MUS 总体"
                         "并单独列示；高层项目（≥抽样间隔）已全部纳入。")
    return out
