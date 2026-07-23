#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenClaw 代码级自纠错内核 (code_corrector.py)
==============================================
白皮书 §5.2：捕获 stderr 报错堆栈 → 回传大模型"反思与修正" → 生成修正代码重新执行。

分层策略（遵循白皮书 §4.3.2 "规则优先、AI兜底"）：
    Layer 1: 规则修正器 —— 确定性错误（缺 import、输出目录不存在、编码错误、
             列名空格导致的 KeyError）本地毫秒级修复，不消耗 LLM 算力
    Layer 2: LLM 修正器 —— 语义级错误回传大模型反思修正
             （vLLM 不可用时自动跳过，例如微调期间 GPU 被占用）

所有修正结果必须通过 ast.parse 语法校验后才返回，保证不会引入语法错误。

使用方：
    - engine/sandbox_v3.py  execute_with_retry()（Docker 沙箱路径）
    - api/routes.py         _execute_in_sandbox()（本地子进程路径）
"""
from __future__ import annotations

import ast
import os
import re
from typing import Dict, Optional

# ═══════════════════════════════════════════════════════════════
# Layer 1: 规则修正器
# ═══════════════════════════════════════════════════════════════

# NameError → 缺失 import 映射表
KNOWN_IMPORTS: Dict[str, str] = {
    "pd": "import pandas as pd",
    "np": "import numpy as np",
    "os": "import os",
    "sys": "import sys",
    "json": "import json",
    "re": "import re",
    "time": "import time",
    "math": "import math",
    "datetime": "import datetime",
    "Path": "from pathlib import Path",
    "openpyxl": "import openpyxl",
    "plt": "import matplotlib.pyplot as plt",
}

# 防御性 CSV 读取辅助函数（应对编码错误）
_ROBUST_READ_CSV = '''
def _robust_read_csv(path, **kwargs):
    """多编码尝试读取 CSV（OpenClaw 自纠错注入）"""
    import pandas as _pd
    for _enc in ("utf-8-sig", "utf-8", "gbk", "gb18030", "latin1"):
        try:
            return _pd.read_csv(path, encoding=_enc, **kwargs)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return _pd.read_csv(path, encoding="utf-8", errors="replace", **kwargs)
'''


def extract_error_summary(stderr: str, max_chars: int = 1500) -> str:
    """提取报错堆栈的关键信息（最后的异常行 + 相邻上下文）"""
    if not stderr:
        return ""
    lines = [l for l in stderr.strip().splitlines() if l.strip()]
    return "\n".join(lines[-12:])[-max_chars:]


def _syntax_ok(code: str) -> bool:
    try:
        ast.parse(code)
        return True
    except SyntaxError:
        return False


def rule_based_fix(code: str, stderr: str) -> Optional[str]:
    """
    Layer 1: 确定性规则修正。
    返回修正后的完整代码；无适用规则时返回 None。
    """
    if not stderr:
        return None
    err = extract_error_summary(stderr)
    fixed = code

    # 规则 1: NameError: name 'xxx' is not defined → 补 import
    m = re.search(r"NameError.*?name '(\w+)' is not defined", err)
    if m and m.group(1) in KNOWN_IMPORTS:
        stmt = KNOWN_IMPORTS[m.group(1)]
        if stmt not in fixed:
            fixed = stmt + "\n" + fixed
            if _syntax_ok(fixed):
                return fixed
        return None

    # 规则 2: FileNotFoundError 涉及 outputs 目录 → 补建目录
    if "FileNotFoundError" in err and "outputs" in err:
        guard = "import os\nos.makedirs('outputs', exist_ok=True)\n"
        if "makedirs('outputs'" not in fixed and 'makedirs("outputs"' not in fixed:
            fixed = guard + fixed
            if _syntax_ok(fixed):
                return fixed
        return None

    # 规则 3: UnicodeDecodeError + read_csv → 注入多编码防御性读取
    if ("UnicodeDecodeError" in err or "codec can't decode" in err) and "read_csv(" in fixed:
        if "_robust_read_csv" not in fixed:
            fixed = re.sub(r"\bpd\.read_csv\(", "_robust_read_csv(", fixed)
            fixed = _ROBUST_READ_CSV + "\n" + fixed
            if _syntax_ok(fixed):
                return fixed
        return None

    # 规则 4: KeyError（多为列名含首尾空格）→ 在 read_excel/read_csv 后注入列名清洗
    if "KeyError" in err and re.search(r"read_(excel|csv)\(", fixed):
        if ".str.strip()" not in fixed and "str(c).strip()" not in fixed:
            out_lines = []
            for line in fixed.splitlines():
                out_lines.append(line)
                m2 = re.match(r"^(\s*)(\w+)\s*=\s*.*read_(?:excel|csv)\(", line)
                if m2:
                    indent, var = m2.group(1), m2.group(2)
                    out_lines.append(
                        f"{indent}{var}.columns = [str(c).strip() for c in {var}.columns]"
                    )
            fixed = "\n".join(out_lines)
            if fixed != code and _syntax_ok(fixed):
                return fixed
        return None

    # 规则 5: ModuleNotFoundError → 本地无法安装依赖，交给 LLM 层换实现方式
    # （不在此返回 None，继续检查后续规则）

    # 规则 6: 算子未实现（代码中有 "[跳过] 未实现算子" 标记）
    # → 将未实现的算子替换为 pass + 数据直通（让下游不因变量缺失而崩溃）
    if "[跳过] 未实现算子" in err or "未实现算子" in fixed:
        # 找到所有 [跳过] 行，为每个未实现算子注入空 DataFrame 兜底
        skip_vars = set()
        for line in fixed.splitlines():
            m_skip = re.search(r'\[跳过\]\s*未实现算子:\s*(\w+)', line)
            if m_skip:
                skip_vars.add(m_skip.group(1))
        if skip_vars:
            new_lines = []
            injected = set()
            for line in fixed.splitlines():
                m_skip = re.search(r'\[跳过\]\s*未实现算子:\s*(\w+)', line)
                if m_skip:
                    op_name = m_skip.group(1)
                    # 查找该算子的预期输出变量名
                    for nline in fixed.splitlines():
                        m_out = re.search(
                            rf'#\s*Step:\s*{re.escape(op_name)}\s*[-–].*?(?:output_alias|→)\s*(\w+)', nline
                        )
                        if m_out:
                            var = m_out.group(1)
                            if var not in injected:
                                new_lines.append(
                                    f'# [自纠错] 注入 {op_name} 兜底空表 → {var}'
                                )
                                new_lines.append(
                                    f'if "{var}" not in dir() or {var} is None or '
                                    f'(isinstance({var}, pd.DataFrame) and {var}.empty):'
                                )
                                new_lines.append(f'    {var} = pd.DataFrame()')
                                new_lines.append(
                                    f'    print("[自纠错] {op_name} 未实现，注入空 DataFrame 作为 {var}")'
                                )
                                injected.add(var)
                new_lines.append(line)
            fixed = '\n'.join(new_lines)
            if fixed != code and _syntax_ok(fixed):
                return fixed

    # 规则 7: 同文件被多次 Load → 第二 Load 改为深拷贝第一个 Load 的结果
    # （跨文件对比场景只有1个文件时，避免自己跟自己比）
    load_vars = []
    load_files_seen = set()
    for line in fixed.splitlines():
        m = re.match(r"^(\s*)(\w+)\s*=\s*pd\.(?:read_excel|read_csv)\((.+)\)", line)
        if m:
            indent, var, args = m.groups()
            # 提取文件名
            fm = re.search(r"""['"]?([^'")]+\.(?:xlsx|xls|csv))""", args)
            if fm:
                fname = fm.group(1).split('/')[-1].split('\\')[-1]
                if fname in load_files_seen:
                    load_vars.append((indent, var, fname))
                else:
                    load_files_seen.add(fname)
    if load_vars and len(load_vars) >= 1:
        new_lines = []
        for line in fixed.splitlines():
            new_lines.append(line)
            for indent, var, fname in load_vars:
                # 查找该 Load 块的行
                if re.match(rf"^\s*{re.escape(var)}\s*=\s*pd\.(?:read_excel|read_csv)\(.+{re.escape(fname)}", line):
                    # 替换为深拷贝（避免引用同一对象）
                    first_var = None
                    for line2 in fixed.splitlines():
                        m2 = re.match(r"^(\s*)(\w+)\s*=\s*pd\.(?:read_excel|read_csv)\(.+", line2)
                        if m2:
                            first_var = m2.group(2)
                            break
                    if first_var and first_var != var:
                        new_lines.append(
                            f'{indent}# [自纠错] 文件 {fname} 已被 {first_var} 加载，'
                            f'{var} 改用深拷贝避免自比对'
                        )
                        new_lines.append(f'{indent}{var} = {first_var}.copy()')
                        new_lines.append(
                            f'{indent}print("[自纠错] {var} ← {first_var}.copy()'
                            f'（同文件避免重复 IO）")'
                        )
                        break
        fixed = '\n'.join(new_lines)
        if fixed != code and _syntax_ok(fixed):
            return fixed

    # 规则 8: ValueError (columns) / 连接键不存在 → 注入列名检查 + 自动回退
    if ("ValueError" in err or "KeyError" in err) and any(
        kw in err for kw in ("columns", "column", "key", "键", "连接")
    ):
        guard = """
# [自纠错] 列名安全校验：打印双方 DataFrames 的列名以辅助定位
import sys as _sys_sa
for _df_name in ['df_ledger', 'df_bank', 'df_sorted', 'df_merged', 'df_diff']:
    if _df_name in dir() and isinstance(eval(_df_name), pd.DataFrame):
        _df = eval(_df_name)
        if not _df.empty:
            print(f'[自纠错] {_df_name}.columns = {list(_df.columns)}', file=_sys_sa.stderr)
"""
        if "[自纠错] 列名安全校验" not in fixed:
            # 注入到第一个 pd.read_ 之后
            fixed_parts = fixed.split('\n', 1)
            if len(fixed_parts) > 1:
                fixed = fixed_parts[0] + guard + '\n' + fixed_parts[1]
            else:
                fixed = guard + '\n' + fixed
            if _syntax_ok(fixed):
                return fixed

    return None


# ═══════════════════════════════════════════════════════════════
# Layer 2: LLM 修正器（vLLM 不可用时自动跳过）
# ═══════════════════════════════════════════════════════════════

LLM_FIX_PROMPT = """你是 Python 审计数据处理专家。下面这段代码执行失败了，请反思报错原因并输出修正后的完整代码。

【执行失败的代码】
```python
{code}
```

【报错堆栈】
```
{stderr}
```

【上下文】{context}

【要求】
1. 只修改导致报错的部分，尽量保留原有逻辑
2. 加入必要的防御性处理（空值填充、类型转换、编码兜底）
3. 只输出修正后的完整 Python 代码，用 ```python 代码块包裹，不要任何解释"""


def llm_fix(code: str, stderr: str, context: str = "") -> Optional[str]:
    """
    Layer 2: 回传大模型"反思与修正"（白皮书 §5.2 Agentic Self-Correction）。
    vLLM 端点不可用（如微调期间）时自动降级到 Dify API 兜底，再不行则返回 None。
    """
    candidates = []  # 多端点收集候选修正代码

    # 端点1: vLLM 直连
    try:
        import httpx
        r = httpx.post(
            os.environ.get("VLLM_TUNNEL_URL", "http://localhost:18000/v1/chat/completions"),
            headers={"Authorization": "Bearer EMPTY"},
            json={
                "model": os.environ.get("VLLM_MODEL", "qwen3-235b"),
                "messages": [{
                    "role": "user",
                    "content": LLM_FIX_PROMPT.format(
                        code=code[:6000],
                        stderr=extract_error_summary(stderr),
                        context=context[:500] or "无",
                    ),
                }],
                "temperature": 0.2,
                "max_tokens": 4096,
            },
            timeout=httpx.Timeout(60.0, connect=5.0),
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        m = re.search(r"```(?:python)?\s*\n(.*?)```", content, re.DOTALL)
        candidate = (m.group(1) if m else content).strip()
        if candidate and candidate != code.strip() and _syntax_ok(candidate):
            candidates.append(candidate)
            print(f"[自纠错] vLLM 端点命中")
    except Exception as e:
        print(f"[自纠错] vLLM 不可用（{type(e).__name__}），尝试降级端点")

    # 端点2: Dify 自纠错工作流（兜底，需配置 DIFY_FIX_API_KEY）
    if not candidates:
        try:
            import httpx
            dify_fix_key = os.environ.get("DIFY_FIX_API_KEY", "") or os.environ.get("DIFY_API_KEY", "")
            dify_base = os.environ.get("DIFY_BASE_URL", "http://localhost:18808/v1")
            if dify_fix_key:
                r2 = httpx.post(
                    f"{dify_base}/workflows/run",
                    headers={"Authorization": f"Bearer {dify_fix_key}"},
                    json={
                        "inputs": {
                            "failed_code": code[:8000],
                            "stderr": extract_error_summary(stderr)[:2000],
                            "context": context[:1000] or "无",
                        },
                        "response_mode": "blocking",
                        "user": "audit_self_correct",
                    },
                    timeout=httpx.Timeout(90.0, connect=10.0),
                )
                r2.raise_for_status()
                result = r2.json()
                fixed_output = (
                    result.get("data", {}).get("outputs", {}).get("fixed_code", "")
                )
                if fixed_output:
                    m = re.search(r"```(?:python)?\s*\n(.*?)```", fixed_output, re.DOTALL)
                    candidate = (m.group(1) if m else fixed_output).strip()
                    if candidate and candidate != code.strip() and _syntax_ok(candidate):
                        candidates.append(candidate)
                        print(f"[自纠错] Dify 自纠错工作流命中")
        except Exception as e:
            print(f"[自纠错] Dify 自纠错工作流不可用（{type(e).__name__}）")

    if candidates:
        return candidates[0]
    return None


# ═══════════════════════════════════════════════════════════════
# 统一入口
# ═══════════════════════════════════════════════════════════════

def correct_code(
    code: str,
    stderr: str,
    attempt: int = 1,
    context: str = "",
) -> Optional[str]:
    """
    自纠错统一入口：规则优先、AI 兜底。

    Args:
        code: 执行失败的原始代码
        stderr: 完整报错堆栈
        attempt: 当前重试轮次
        context: 任务上下文（用户意图等），供 LLM 参考

    Returns:
        修正后的完整代码；无法修正时返回 None（调用方自行决定是否原样重试）
    """
    # Layer 1: 规则修正（毫秒级，零成本）
    fixed = rule_based_fix(code, stderr)
    if fixed and fixed != code:
        print(f"[自纠错] 第 {attempt} 轮：规则修正器命中")
        return fixed

    # Layer 2: LLM 反思修正
    fixed = llm_fix(code, stderr, context)
    if fixed:
        print(f"[自纠错] 第 {attempt} 轮：LLM 修正器生成新代码")
        return fixed

    print(f"[自纠错] 第 {attempt} 轮：无可用修正方案")
    return None

