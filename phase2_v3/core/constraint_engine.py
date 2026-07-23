#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
约束引擎 (constraint_engine.py)
==============================
"全无状态生命周期与语义编译版"白皮书 §2.1 约束驱动自纠错的核心实现

解析审计师的约束性自然语言（如"差异控制在5万以内"、"波动不超过20%"），
在沙箱执行完成后校验结果是否满足约束。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class Constraint:
    """一条量化约束"""
    field: str           # 约束字段，如 "差异金额"
    operator: str        # 比较运算符: "<", "<=", ">", ">=", "=", "%"
    value: float         # 阈值
    unit: str            # 单位: "元", "万", "%"
    raw_text: str        # 原始原文
    satisfied: Optional[bool] = None
    actual_value: Optional[float] = None

    def describe(self) -> str:
        return f"{self.field} {self.operator} {self.value}{self.unit}"


# ── 解析正则 ────────────────────────────────────────

# 金额约束: "差异控制在5万以内" / "不超过10万元" / "容差5000元"
AMOUNT_PATTERNS = [
    # "差异控制在X万以内"
    re.compile(r'(?:差异|差额|金额)[^\d]*?(?:控制|不超过|小于|限制)[^\d]*?(\d+(?:\.\d+)?)\s*(万|万元?|元|块)', re.I),
    # "不超过X万"
    re.compile(r'不超过\s*(\d+(?:\.\d+)?)\s*(万|万元?|元)', re.I),
    # "X万以内"
    re.compile(r'(\d+(?:\.\d+)?)\s*(万|万元?)以[内下]', re.I),
    # "容差X元"
    re.compile(r'容差\s*(\d+(?:\.\d+)?)\s*(元|万)', re.I),
    # "差异X万"
    re.compile(r'(?:差异|差额)\s*(\d+(?:\.\d+)?)\s*(万|万元?)', re.I),
    # "阈值X万"
    re.compile(r'阈值\s*(\d+(?:\.\d+)?)\s*(万|万元?)', re.I),
]

# 百分比约束: "波动不超过20%" / "偏差控制在5%"
PERCENT_PATTERNS = [
    re.compile(r'(?:波动|偏差|差异|误差)[^\d]*?不超过\s*(\d+(?:\.\d+)?)\s*%', re.I),
    re.compile(r'(\d+(?:\.\d+)?)\s*%以[内下]', re.I),
    re.compile(r'控制[^\d]*?(\d+(?:\.\d+)?)\s*%', re.I),
]

# 记录数约束
COUNT_PATTERNS = [
    re.compile(r'(?:至少|最少|不小于)\s*(\d+)\s*(条|笔|个|行)', re.I),
    re.compile(r'(?:最多|不超过)\s*(\d+)\s*(条|笔|个|行)', re.I),
]


def parse_constraints(user_intent: str) -> List[Constraint]:
    """从审计师的自然语言意图中解析约束条件"""
    constraints = []

    for p in AMOUNT_PATTERNS:
        m = p.search(user_intent)
        if m:
            val = float(m.group(1))
            unit = m.group(2)
            if unit in ("万", "万元"):
                val *= 10000
                unit = "元"
            constraints.append(Constraint(
                field="差异金额", operator="<", value=val, unit=unit,
                raw_text=m.group(0),
            ))
            break  # 只取第一个匹配

    for p in PERCENT_PATTERNS:
        m = p.search(user_intent)
        if m:
            val = float(m.group(1))
            constraints.append(Constraint(
                field="波动率", operator="<", value=val, unit="%",
                raw_text=m.group(0),
            ))
            break

    return constraints


def extract_tolerance(user_intent: str, default_pct: float = 1.0) -> float:
    """从自然语言中提取金额容差百分比。LLM理解上下文为主，正则兜底。未指定则返回默认值1%"""
    import re
    # 先看用户意图里有没有数字——没有就直接返回默认值
    if not re.search(r'\d', user_intent):
        return default_pct

    # 用 LLM 理解上下文（优先）
    try:
        result = _extract_tolerance_via_llm(user_intent)
        if result is not None:
            return result
    except Exception:
        pass

    # LLM 不可用时用正则兜底
    for p in PERCENT_PATTERNS:
        m = p.search(user_intent)
        if m:
            return float(m.group(1))
    for p in AMOUNT_PATTERNS:
        m = p.search(user_intent)
        if m:
            val = float(m.group(1))
            unit = m.group(2)
            if unit in ("万", "万元"):
                val *= 10000
            return -val  # 负数表示绝对金额
    return default_pct


def _extract_tolerance_via_llm(user_intent: str):
    """用 vLLM 理解上下文，提取容差阈值"""
    import httpx, json
    prompt = f"""你是审计参数提取器。从用户指令中提取"金额匹配容差阈值"。

规则：
- 如果用户明确指定了容差（如"差异控制在5万以内""波动不超过3%"），提取数值。
- 百分比返回正数（如3%→3.0），绝对金额返回负数（如5万→-50000）。
- 如果用户提到的数字不是容差（如"筛选大于5万的记录"），返回null。
- 如果用户没说容差，返回null。

用户指令：
{user_intent}

只回复数字或null，不要解释。"""

    resp = httpx.post(
        os.environ.get("VLLM_TUNNEL_URL", "http://localhost:18000/v1/chat/completions"),
        headers={"Authorization": "Bearer EMPTY"},
        json={"model": "qwen3-235b", "messages": [{"role": "user", "content": prompt}],
              "temperature": 0, "max_tokens": 20},
        timeout=10,
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"].strip()
    if text.lower() == "null":
        return None
    return float(text)


def validate_constraints(result_df, constraints: List[Constraint]) -> List[Constraint]:
    """检查计算结果是否满足约束（需要 pandas DataFrame）"""
    import pandas as pd
    if result_df is None or result_df.empty:
        return constraints

    for c in constraints:
        if c.field == "差异金额" and "差异金额" in result_df.columns:
            max_diff = result_df["差异金额"].abs().max()
            c.actual_value = max_diff
            c.satisfied = max_diff < c.value
        elif c.field == "波动率":
            if "波动率" in result_df.columns:
                max_vol = result_df["波动率"].abs().max()
                c.actual_value = max_vol
                c.satisfied = max_vol < c.value

    return constraints


def format_constraint_report(constraints: List[Constraint]) -> str:
    """生成约束满足度报告"""
    lines = ["=== 约束满足度报告 ==="]
    for c in constraints:
        status = "✅ 满足" if c.satisfied else ("❌ 不满足" if c.satisfied is False else "⚠️ 无法校验")
        actual = f"（实际: {c.actual_value:.2f}）" if c.actual_value is not None else ""
        lines.append(f"{status} | {c.describe()} {actual}")
    if not constraints:
        lines.append("（未检测到约束条件）")
    return "\n".join(lines)
