#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多Agent协作管线 (multi_agent.py)
==================================
4个专业Agent按审计工作流串联：问题抽取→逻辑推理→法规检索→报告撰写。
每个Agent有独立System Prompt和工具偏好，输出标准化JSON。
"""
import asyncio, json, os, re
from typing import List, Dict, Any, Optional
from core.toolbox import TOOLS, execute_tool


def _clean_output(text: str) -> str:
    """剥离 <think> 推理块，避免污染下游 Agent 上下文与前端 JSON 解析"""
    return re.sub(r"<think>[\s\S]*?</think>", "", text or "").strip()


_http_client: Any = None
_client_loop_id: Any = None

def _get_client():
    """共享 httpx 客户端（事件循环切换时自动重建，防 'Event loop is closed'）"""
    global _http_client, _client_loop_id
    try:
        loop_id = id(asyncio.get_running_loop())
    except RuntimeError:
        loop_id = None
    if _http_client is None or _http_client.is_closed or loop_id != _client_loop_id:
        import httpx
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=20),
        )
        _client_loop_id = loop_id
    return _http_client

_LLM_URL = os.environ.get("VLLM_TUNNEL_URL", "http://localhost:18000/v1/chat/completions")
_LLM_MODEL = os.environ.get("VLLM_MODEL", "qwen3-235b")

# ══════════════════════════════════════════════════
# 四个专业Agent定义
# ══════════════════════════════════════════════════

AGENT_DEFINITIONS = {
    "issue_extractor": {
        "role": "问题抽取Agent",
        "system_prompt": """你是审计问题抽取专家。唯一职责：从底稿提炼关键问题和异常。

输出严格JSON格式：
{"findings":[{"id":"F001","category":"金额异常|勾稽不一致|流程缺失|合规风险|其他","severity":"HIGH|MEDIUM|LOW","title":"标题","detail":"详细描述含具体数字","source":"来源"}],"summary":"总结"}

规则：1)只输出JSON 2)每项有具体数字 3)至少2项最多8项""",
        "tools": ["read_file", "parse_md_structure", "extract_section", "diff_documents"],
    },
    "logic_reasoner": {
        "role": "逻辑推理Agent",
        "system_prompt": """你是审计逻辑推理专家。分析问题抽取Agent的输出，推理业务逻辑。

输出JSON：
{"analysis":[{"finding_id":"F001","root_cause":"根因","business_impact":"影响","related_items":["F002"],"risk_assessment":"评估"}],"cross_references":[{"finding_ids":["F001","F002"],"relationship":"关联说明"}],"overall_assessment":"整体评估200字"}

规则：1)只输出JSON 2)每个finding分析根因和影响 3)检查交叉关联""",
        "tools": ["search_knowledge"],
    },
    "regulation_searcher": {
        "role": "法规检索Agent",
        "system_prompt": """你是审计法规检索专家。根据前面Agent的分析，检索匹配的审计准则和法规。

输出JSON：
{"regulations":[{"finding_id":"F001","standard":"准则编号","clause":"条款内容","requirement":"要求","compliance_gap":"差距","recommendation":"建议"}],"reference_docs":[],"summary":"总结"}

规则：1)只输出JSON 2)检索不到说明"未检索到" 3)建议具体可操作""",
        "tools": ["search_knowledge"],
    },
    "report_writer": {
        "role": "报告撰写Agent",
        "system_prompt": """你是资深审计报告撰写专家。根据前面3个Agent的全部输出生成专业审计报告。

输出完整Markdown报告，包含：1)标题和基本信息 2)审计发现汇总表 3)逐项说明(问题→根因→法规→建议) 4)整体评估和意见类型

规则：专业客观、数字两位小数千分位、每项有完整链条、结尾给出审计意见类型""",
        "tools": ["generate_report", "fill_template", "convert_format"],
    },
}


# ══════════════════════════════════════════════════
# 单Agent执行引擎
# ══════════════════════════════════════════════════

async def _call_llm(messages: list, tools: list = None) -> dict:
    client = _get_client()
    payload = {"model": _LLM_MODEL, "messages": messages, "temperature": 0.3, "max_tokens": 2048}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    resp = await client.post(_LLM_URL, headers={"Authorization": "Bearer EMPTY"}, json=payload)
    resp.raise_for_status()
    return resp.json()


async def _run_single_agent(agent_key: str, context: str, max_turns: int = 3) -> str:
    """运行单个专业Agent，返回其最终文本输出。"""
    agent_def = AGENT_DEFINITIONS[agent_key]
    agent_tools = agent_def.get("tools", [])
    tool_list = [t for t in TOOLS if t["function"]["name"] in agent_tools]

    messages = [
        {"role": "system", "content": agent_def["system_prompt"]},
        {"role": "user", "content": context},
    ]

    print(f"  [{agent_def['role']}] 启动 (tools={agent_tools})")

    for turn in range(max_turns):
        data = await _call_llm(messages, tools=tool_list if tool_list else None)
        msg = data["choices"][0]["message"]

        if not msg.get("tool_calls"):
            content = _clean_output(msg.get("content", ""))
            print(f"  [{agent_def['role']}] 完成 ({len(content)} chars)")
            return content

        for tc in msg["tool_calls"]:
            func_name = tc["function"]["name"]
            try:
                func_args = json.loads(tc["function"]["arguments"])
            except json.JSONDecodeError:
                func_args = {}
            print(f"  [{agent_def['role']}] Turn {turn+1}: {func_name}")
            result = execute_tool(func_name, func_args)
            messages.append(msg)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", f"call_{turn}"),
                "content": json.dumps(result, ensure_ascii=False),
            })

        data = await _call_llm(messages, tools=tool_list if tool_list else None)
        fmsg = data["choices"][0]["message"]
        if not fmsg.get("tool_calls"):
            content = _clean_output(fmsg.get("content", ""))
            print(f"  [{agent_def['role']}] 完成 ({len(content)} chars)")
            return content

    return "{}"  # 超轮返回空JSON


# ══════════════════════════════════════════════════
# 多Agent管线编排器
# ══════════════════════════════════════════════════

async def run_audit_pipeline(
    user_message: str,
    uploaded_files: Optional[List[Dict[str, str]]] = None,
    stages: Optional[List[str]] = None,
) -> Dict[str, str]:
    """运行完整审计多Agent管线。

    Args:
        user_message: 审计师指令
        uploaded_files: [{"name":"底稿.xlsx","path":"uploads/底稿.xlsx"}, ...]
        stages: 指定要运行的阶段，默认全部4个

    Returns:
        {"issue_extraction":"...","logic_analysis":"...","regulation_match":"...","final_report":"..."}
    """
    if stages is None:
        stages = ["issue_extractor", "logic_reasoner", "regulation_searcher", "report_writer"]

    uploaded_files = uploaded_files or []
    result: Dict[str, str] = {}
    pipeline_context: List[str] = []

    file_context = ""
    if uploaded_files:
        file_list = "\n".join(
            f"- {f['name']}（read_file 路径: {f.get('path', f['name'])}）" for f in uploaded_files
        )
        file_context = f"【上传文件】\n{file_list}\n\n"

    # Stage 1: 问题抽取
    if "issue_extractor" in stages:
        print("[Pipeline] Stage 1/4: 问题抽取Agent")
        ctx = file_context + f"【指令】{user_message}\n\n请分析材料提炼关键问题。"
        output = await _run_single_agent("issue_extractor", ctx)
        result["issue_extraction"] = output
        pipeline_context.append(f"## 问题抽取\n{output}")

    # Stage 2: 逻辑推理
    if "logic_reasoner" in stages:
        print("[Pipeline] Stage 2/4: 逻辑推理Agent")
        prev = result.get("issue_extraction", "")
        ctx = f"【指令】{user_message}\n\n【问题抽取】\n{prev}\n\n请逐一进行根因分析。"
        output = await _run_single_agent("logic_reasoner", ctx)
        result["logic_analysis"] = output
        pipeline_context.append(f"## 逻辑分析\n{output}")

    # Stage 3: 法规检索
    if "regulation_searcher" in stages:
        print("[Pipeline] Stage 3/4: 法规检索Agent")
        prev = result.get("issue_extraction", "") + "\n" + result.get("logic_analysis", "")
        ctx = f"【指令】{user_message}\n\n【前序分析】\n{prev}\n\n请检索匹配的审计准则。"
        output = await _run_single_agent("regulation_searcher", ctx)
        result["regulation_match"] = output
        pipeline_context.append(f"## 法规匹配\n{output}")

    # Stage 4: 报告撰写
    if "report_writer" in stages:
        print("[Pipeline] Stage 4/4: 报告撰写Agent")
        all_ctx = "\n\n".join(pipeline_context)
        ctx = f"【指令】{user_message}\n\n{all_ctx}\n\n请生成完整审计报告。"
        output = await _run_single_agent("report_writer", ctx, max_turns=2)
        result["final_report"] = output

    return result


def run_pipeline_sync(user_message: str, uploaded_files=None, stages=None) -> Dict[str, str]:
    """同步包装器"""
    return asyncio.run(run_audit_pipeline(user_message, uploaded_files, stages))
