#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
项目记忆模块 (project_memory.py)
==================================
为每个审计项目维护 agents.md 记忆文件，实现跨会话知识复用。

核心功能：
- 创建/读取/更新项目记忆文件
- 自动归纳对话中的关键信息（列名映射、容差偏好、关注领域）
- Run 之间知识继承（不同于 run_snapshot 的 DAG 摘要，这是"偏好记忆"）

文件结构：
    data/projects/{project_code}/
    ├── agents.md          ← 项目记忆（人类可读 + Agent 可解析）
    └── runs/              ← 各 Run 的物理目录（由 run_snapshot 管理）

agents.md 格式示例：
    # 项目 {project_code} 记忆
    ## 审计偏好
    - 容差: 1%
    - 关键词: （根据实际业务确定，如工程回款、采购付款等）
    ## 列名映射
    - 对方客户名称: 对方户名, 交易对手
    - 交易金额: 金额, 发生额
    ## 历史经验（最近3次）
    - [2025-03-15] 业务匹配: 用RegexFilter + NoiseFilter，差异<2%
    - [2025-03-10] 大额筛查: 阈值10万，需人工标注风险等级
"""

from __future__ import annotations
import json, os, re
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

MEMORY_ROOT = Path(os.environ.get("AUDIT_DATA_ROOT", str(Path(__file__).parent.parent / "data")))
PROJECTS_DIR = MEMORY_ROOT / "projects"


def _project_dir(project_code: str) -> Path:
    d = PROJECTS_DIR / project_code
    d.mkdir(parents=True, exist_ok=True)
    return d


def _memory_path(project_code: str) -> Path:
    return _project_dir(project_code) / "agents.md"


def init_memory(project_code: str, subject: str = "") -> str:
    """初始化项目记忆文件（如果不存在）。返回文件路径。"""
    mp = _memory_path(project_code)
    if not mp.exists():
        mp.write_text(
            f"# 项目 {project_code} 记忆\n\n"
            f"## 基本信息\n"
            f"- 创建时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"- 审计科目: {subject or '待补充'}\n\n"
            f"## 审计偏好\n"
            f"- 容差: 1%\n"
            f"- 关注领域: \n"
            f"- 关键词: \n\n"
            f"## 列名映射\n"
            f"(格式: 标准列名: 实际出现过的别名, ...)\n\n"
            f"## 历史经验\n",
            encoding="utf-8",
        )
    return str(mp)


def read_memory(project_code: str) -> str:
    """读取项目记忆文件内容。"""
    mp = _memory_path(project_code)
    if mp.exists():
        return mp.read_text(encoding="utf-8")
    return ""


def remember(project_code: str, section: str, content: str) -> None:
    """向项目记忆文件的指定章节追加内容。"""
    mp = _memory_path(project_code)
    if not mp.exists():
        init_memory(project_code)

    text = mp.read_text(encoding="utf-8")

    # 找到对应章节，追加内容
    section_header = f"## {section}"
    if section_header in text:
        # 在章节末尾追加（下一个 ## 之前）
        idx = text.index(section_header)
        next_section = text.find("\n## ", idx + len(section_header))
        if next_section < 0:
            next_section = len(text)
        text = text[:next_section] + f"\n- [{datetime.now().strftime('%Y-%m-%d')}] {content}" + text[next_section:]
    else:
        # 新章节
        text += f"\n{section_header}\n- [{datetime.now().strftime('%Y-%m-%d')}] {content}\n"

    mp.write_text(text, encoding="utf-8")


def extract_key_info(user_intent: str, catalog_summary: str = "") -> Dict[str, str]:
    """从用户意图和数据目录中提取可记忆的关键信息。

    提取规则（本地正则，不依赖 LLM）：
    - 容差: 匹配 "差异控制在X%"/"容差X%"/"X%以内"
    - 关键词: 匹配引号内的词组或常见审计关键词
    - 列名: 匹配 "列: ..." 模式
    """
    info: Dict[str, str] = {}

    # 容差提取
    tol_match = re.search(r"(\d+(?:\.\d+)?)\s*%", user_intent)
    if tol_match:
        info["tolerance"] = f"{tol_match.group(1)}%"

    # 关键词提取
    quoted = re.findall(r"[「「]([^」」]+)[」」]", user_intent)
    if quoted:
        info["keywords"] = ", ".join(quoted)

    # 列名提取
    cols = re.findall(r"列[：:]\s*(.+?)(?:\\n|$)", catalog_summary)
    if cols:
        info["columns"] = cols[0]

    return info


def auto_remember(project_code: str, user_intent: str, result_summary: str,
                  catalog_summary: str = "") -> None:
    """自动从一次交互中提取信息并写入项目记忆。"""
    init_memory(project_code)

    # 提取关键信息
    info = extract_key_info(user_intent, catalog_summary)
    if info.get("tolerance"):
        remember(project_code, "审计偏好",
                 f"容差偏好: {info['tolerance']}（来源: {user_intent[:60]}）")

    if info.get("keywords"):
        remember(project_code, "审计偏好",
                 f"关键词: {info['keywords']}")

    # 记录历史经验
    summary_short = result_summary[:80].replace("\n", " ")
    remember(project_code, "历史经验", summary_short)


def get_memory_context(project_code: str, max_chars: int = 2000) -> str:
    """获取项目记忆作为 LLM 上下文（截断到 max_chars）。"""
    memory = read_memory(project_code)
    if not memory:
        return ""

    # 如果太长，保留基本信息 + 最近3条历史经验
    if len(memory) > max_chars:
        lines = memory.split("\n")
        header = []
        history = []
        in_history = False
        for line in lines:
            if line.startswith("## 历史经验"):
                in_history = True
                continue
            if in_history and line.startswith("## "):
                in_history = False
            if in_history:
                history.append(line)
            else:
                header.append(line)

        recent = history[-3:] if len(history) > 3 else history
        memory = "\n".join(header + ["## 历史经验"] + recent)

    return f"\n\n## 项目记忆（{project_code}）\n{memory[:max_chars]}"
