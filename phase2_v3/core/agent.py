#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tool-Use Agent v2.0 (agent.py)
================================
异步非阻塞 + CoT强制推理 + 工具结果校验 + 反思闭环。

与 v1 的区别：
- 异步：asyncio + 共享 httpx 客户端，不阻塞事件循环
- CoT：SYSTEM_PROMPT 强制"分析→规划→执行→反思→决定"五步推理
- 校验：工具返回失败时自动反馈给 LLM 换策略
- 反思：每轮结束记录"已完成的步骤 + 当前状态 + 下一步计划"
"""
import asyncio, json, os
from pathlib import Path
from typing import List, Dict, Any, Optional
from core.toolbox import TOOLS, execute_tool

SYSTEM_PROMPT = """你是资深审计文档处理专家。用户会上传各种格式的文档（md/docx/xlsx/txt/csv），你需要自主规划并调用工具完成用户需求。

## 可用工具
{TOOL_LIST}

## Chain-of-Thought 强制推理流程
每次收到指令后，你必须按照以下步骤思考（在脑海中完成，不输出给用户）：
1. 【分析需求】用户真正想要什么？是读取/解析/填充/格式化/生成/对比？
2. 【规划步骤】需要几步？每步用什么工具？输入输出是什么？
3. 【执行工具】选择最合适的工具，从{TOOL_NAMES}中挑选一个调用
4. 【反思结果】工具返回了什么？成功还是失败？下一步应该做什么？
5. 【决定行动】如果成功→继续下一步或输出结果；如果失败→换工具或告知用户

## 核心规则
1. 每次只调用一个工具，等待结果后再决定下一步
2. 工具返回 {"ok": false, ...} 表示失败，必须换策略或告知用户，不可忽略
3. 用户需求不明确时，先问清楚再操作
4. 最终回复简洁专业，直接用中文说明操作结果
5. 处理完成后，简要总结你做了什么、结果如何
"""

# ── 共享 HTTP 客户端（懒加载） ──
_http_client: Any = None

def _get_client():
    global _http_client
    if _http_client is None:
        import httpx
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=20),
        )
    return _http_client

_LLM_URL = os.environ.get("VLLM_TUNNEL_URL", "http://localhost:18000/v1/chat/completions")
_LLM_MODEL = os.environ.get("VLLM_MODEL", "qwen3-235b")


def _build_system_prompt() -> str:
    """动态构建 System Prompt（注入工具列表和名称）"""
    tool_descs = []
    tool_names = []
    for t in TOOLS:
        fn = t["function"]
        tool_names.append(fn["name"])
        tool_descs.append(f"- {fn['name']}: {fn['description']}")
    return SYSTEM_PROMPT.format(
        TOOL_LIST="\n".join(tool_descs),
        TOOL_NAMES=", ".join(tool_names),
    )


async def _call_llm(messages: list) -> dict:
    """异步调用 vLLM，返回完整响应 JSON"""
    client = _get_client()
    resp = await client.post(
        _LLM_URL,
        headers={"Authorization": "Bearer EMPTY"},
        json={
            "model": _LLM_MODEL,
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": "auto",
            "temperature": 0.3,
            "max_tokens": 2048,
        },
    )
    resp.raise_for_status()
    return resp.json()


async def agent_run(
    user_message: str,
    uploaded_files: Optional[List[Dict[str, str]]] = None,
    max_turns: int = 6,
) -> str:
    """Tool-Use Agent 主循环（异步 + CoT + 校验 + 反思闭环）。

    Args:
        user_message: 用户自然语言指令
        uploaded_files: [{"name": "报告.md", "path": "uploads/报告.md"}, ...]
        max_turns: 最大工具调用轮数（默认 6 轮，足够复杂任务）

    Returns:
        Agent 的最终文本回复
    """
    uploaded_files = uploaded_files or []

    # ── 构建初始消息 ──
    full_message = user_message
    if uploaded_files:
        file_list = "\n".join(
            f"- {f['name']} ({f.get('format', '未知格式')})" for f in uploaded_files
        )
        full_message = f"【用户上传的文件】\n{file_list}\n\n【用户指令】\n{user_message}"

    messages = [
        {"role": "system", "content": _build_system_prompt()},
        {"role": "user", "content": full_message},
    ]

    reflection_log: List[str] = []

    # ── Agent 主循环 ──
    for turn in range(max_turns):
        data = await _call_llm(messages)
        choice = data["choices"][0]
        msg = choice["message"]

        # 如果 LLM 决定不调用工具，直接返回回复
        if not msg.get("tool_calls"):
            return msg.get("content", "处理完成。")

        # ── 执行工具调用 + 结果校验 ──
        tool_failures = 0
        for tc in msg["tool_calls"]:
            func_name = tc["function"]["name"]
            try:
                func_args = json.loads(tc["function"]["arguments"])
            except json.JSONDecodeError:
                func_args = {}
                reflection_log.append(f"[Turn {turn+1}] JSON参数解析失败，已使用空参数")

            print(f"[Agent] Turn {turn+1}: {func_name}({func_args})")
            result = execute_tool(func_name, func_args)

            # ── 工具结果校验：失败时注入反思提示 ──
            if isinstance(result, dict) and not result.get("ok", True):
                tool_failures += 1
                error_msg = result.get("error", "未知错误")
                result["_reflection_hint"] = (
                    f"工具 {func_name} 执行失败：{error_msg}。"
                    "请考虑：1)参数是否正确 2)文件是否存在 3)是否应该尝试其他工具"
                )
                reflection_log.append(
                    f"[Turn {turn+1}] {func_name} 失败: {error_msg}"
                )
            else:
                reflection_log.append(
                    f"[Turn {turn+1}] {func_name} 成功"
                )

            # 将工具调用和结果加入消息历史
            messages.append(msg)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", f"call_{turn}"),
                "content": json.dumps(result, ensure_ascii=False),
            })

        # ── 反思闭环：连续失败时主动降级 ──
        if tool_failures > 0:
            reflection_summary = (
                f"\n[系统反思] 本轮 {tool_failures} 个工具调用失败。"
                "请重新评估是否需要换工具或简化步骤。"
            )
            messages.append({
                "role": "user",
                "content": reflection_summary,
            })

        # ── 让 LLM 根据结果继续 ──
        data = await _call_llm(messages)
        fmsg = data["choices"][0]["message"]

        if not fmsg.get("tool_calls"):
            return fmsg.get("content", "操作完成。")

    # ── 超轮数：返回反思日志帮助审计师理解状态 ──
    summary = "\n".join(reflection_log[-5:]) if reflection_log else "无记录"
    return (
        f"已达到最大操作轮数（{max_turns}轮）。以下是执行摘要：\n\n{summary}\n\n"
        "如有未完成的任务，请简化指令后重试。"
    )


def agent_run_sync(user_message: str, uploaded_files=None, max_turns: int = 6) -> str:
    """同步包装器（兼容旧调用方，内部使用 asyncio.run）。"""
    return asyncio.run(agent_run(user_message, uploaded_files, max_turns))
