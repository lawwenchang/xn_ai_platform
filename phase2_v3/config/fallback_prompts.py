#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
降级模式 Prompt 生成器 — v3.2 消费者模式
==========================================
不再手写 9 段 FALLBACK_PROMPTS，从 scenario_packs 注册表组装。
与 Dify 主链路共享同一数据源，永不漂移。
"""
from __future__ import annotations
from typing import Dict


def get_fallback_prompt(scenario_id: str) -> str:
    """从 scenario_packs 注册表组装降级 prompt（消费者，不维护场景知识）"""
    from config.scenario_packs import assemble_fallback_prompt
    return assemble_fallback_prompt(scenario_id)


def detect_scenario(intent: str, default: str = "single_table_analysis") -> str:
    """降级模式下不触发 ASK_USER（没法交互），直接取最高分场景"""
    from config.scenario_packs import detect_scenario as _ds
    return _ds(intent, default=default, ask_user=False)
