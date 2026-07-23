#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""routes.py 补丁 D2：意图澄清槽位专业化（对账必问账户/期间，抽样必问方法）"""
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "api" / "routes.py"
src = P.read_text(encoding="utf-8").replace("\r\n", "\n")


def rep(old: str, new: str, tag: str):
    global src
    assert src.count(old) == 1, f"[{tag}] 命中 {src.count(old)} 次而非 1 次"
    src = src.replace(old, new)
    print(f"  [PATCH] {tag}")


rep('''_SLOT_REQUIRED = {
    "核对": ["数据键", "容差"],
    "对账": ["数据键", "容差"],
    "匹配": ["匹配维度（金额/日期/对手方）"],
    "函证": ["阈值", "模板类型"],
    "抽样": ["样本量", "排序方式"],
    "报告": ["底稿来源"],
    "分析": ["指标", "期间"],
}''',
    '''_SLOT_REQUIRED = {
    "核对": ["数据键"],
    "对账": ["银行账户", "对账期间", "数据键"],
    "匹配": ["匹配维度（金额/日期/对手方）"],
    "函证": ["阈值", "模板类型"],
    "抽样": ["抽样方法", "样本量"],
    "报告": ["底稿来源"],
    "分析": ["指标", "期间"],
}''',
    "槽位定义专业化")

rep('''            if "容差" in slots and not re.search(r"\\d+\\s*(万|元|%|以内|不超过|以内)", intent):
                missing.append("容差阈值（如'5万以内'或'10%'）")
            if "数据键" in slots and not re.search(r"按\\s*\\S+|根据\\s*\\S+|用\\s*\\S+", intent):
                missing.append("对账依据（如'按机构名称'或'按订单号'）")
            if "阈值" in slots and not re.search(r"\\d+\\s*(万|元|以上|超过|大于)", intent):
                missing.append("筛选阈值（如'超过50万'）")
            if "样本量" in slots and not re.search(r"\\d+\\s*笔|抽\\s*\\d+|取\\s*\\d+", intent):
                missing.append("样本量（如'抽20笔'）")
            break''',
    '''            if "容差" in slots and not re.search(r"\\d+\\s*(万|元|%|以内|不超过|以内)", intent):
                missing.append("容差阈值（如'5万以内'或'10%'；逐笔银行核对默认 ±0.01 元无需指定）")
            if "数据键" in slots and not re.search(r"按\\s*\\S+|根据\\s*\\S+|用\\s*\\S+", intent):
                missing.append("对账依据（如'按金额+日期'或'按凭证号'，不指定则自动识别）")
            if "银行账户" in slots and not re.search(r"(账号|账户|\\d{6,})", intent):
                missing.append("对账银行账户/账号（如'农行5927'；流水含多账户时强烈建议指定）")
            if "对账期间" in slots and not re.search(r"(\\d{4}\\s*年|\\d+\\s*个?月|季度|期间|年度|\\d{4}[-/]\\d+)", intent):
                missing.append("对账期间（如'2026年1-3月'）")
            if "抽样方法" in slots and not re.search(r"(MUS|货币单位|随机|分层|系统选样|PPS)", intent, re.I):
                missing.append("抽样方法（MUS货币单位/简单随机/分层，默认 MUS）")
            if "阈值" in slots and not re.search(r"\\d+\\s*(万|元|以上|超过|大于)", intent):
                missing.append("筛选阈值（如'超过50万'）")
            if "样本量" in slots and not re.search(r"\\d+\\s*笔|抽\\s*\\d+|取\\s*\\d+", intent):
                missing.append("样本量（如'抽20笔'）")
            break''',
    "槽位检查逻辑专业化")

P.write_text(src, encoding="utf-8", newline="\n")
import ast
ast.parse(src)
print("routes 补丁D2 完成，AST OK")
