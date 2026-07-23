#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
事务所内部知识注册中心 (internal_kb_registry.py)
=================================================
把 D:\\审计准则与法规文件整理\\03_事务所内部文件 的文件名与目录结构
（不读取文件内容）解析为结构化索引，供平台在运行时按需取用：

- C_底稿模板：D01~D91 实质性程序底稿（按科目）、完成阶段 A 系列、
  询证函范本（银行/往来积极式·消极式/存货/固定资产/律师/前后任CPA）
- B_操作规范与SOP：资产类/负债权益类/损益类审计实操手册、
  初步业务活动 B 系列、完成阶段 A 系列（必填项）
- A_质控手册 / D_培训与案例：当前为空目录，注册但标记为空

合规说明：本模块只读取文件名与目录结构，永不打开文件内容。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

INTERNAL_KB_DIR = Path("D:/审计准则与法规文件整理/03_事务所内部文件")
INDEX_CACHE = Path(__file__).resolve().parent.parent / "data" / "internal_kb_index.json"

# 底稿阶段分类（按目录名归一）
_STAGE_MAP = {
    "初步业务活动": "初步业务活动",
    "风险评估": "风险评估",
    "实质性程序": "实质性程序",
    "所需资料": "所需资料",
    "业务完成": "业务完成阶段",
}

# 询证函范本关键词 → 适用科目/场景（纯文件名规则）
_CONFIRMATION_FORM_RULES = [
    (("银行",), "1.银行询证函", "银行存款/借款"),
    (("消极式",), "3.往来账项询证函-消极式", "往来款项（低风险小额）"),
    (("积极式", "格式1"), "3.往来账项询证函-积极式（格式1）", "往来款项（默认）"),
    (("积极式", "格式2"), "3.往来账项询证函-积极式（格式2）", "往来款项（默认备选）"),
    (("存货", "代管"), "4.存货-委托代管存货询证函", "存货（委托代管）"),
    (("固定资产", "租赁"), "5.固定资产租赁询证函", "固定资产（租赁）"),
    (("律师",), "6.律师询证函", "诉讼/或有事项"),
    (("前后任",), "7.前后任CPA沟通函", "首次承接"),
    (("短期投资", "经销商"), "2.短期投资-对经销商保管的有价证券询证函", "短期投资"),
    (("短期投资", "第三方"), "2.短期投资-由券商之外的第三方保管的有价证券询证函", "短期投资"),
    (("短期投资", "证券投资"), "2.短期投资-证券投资询证函", "短期投资"),
]

# 科目关键词 → D 系列底稿编号前缀（纯文件名规则）
_SUBJECT_TO_D_CODE = {
    "货币资金": "D01", "银行": "D01", "现金": "D01",
    "短期投资": "D02", "应收票据": "D04", "应收股息": "D05",
    "应收账款": "D07", "其他应收款": "D08", "坏账准备": "D09",
    "预付账款": "D10", "存货": "D11", "生产成本": "D12",
    "存货跌价准备": "D13", "待摊费用": "D14", "长期投资": "D16",
    "固定资产": "D20", "累计折旧": "D21", "工程物资": "D24",
    "在建工程": "D25", "固定资产清理": "D28", "无形资产": "D29",
    "长期待摊费用": "D32",
    "短期借款": "D40", "应付票据": "D41", "应付账款": "D42",
    "预收账款": "D43", "应付工资": "D44", "职工薪酬": "D44",
    "应交税金": "D45", "其他应交款": "D46", "其他应付款": "D47",
    "应付利润": "D48", "预提费用": "D49", "长期借款": "D50",
    "长期应付款": "D51",
    "实收资本": "D70", "资本公积": "D71", "盈余公积": "D72", "未分配利润": "D73",
    "销售收入": "D80", "销售成本": "D81", "税及附加": "D82",
    "营业费用": "D83", "其他业务收支": "D84", "管理费用": "D85",
    "财务费用": "D86", "投资收益": "D87", "营业外收支": "D88",
    "补贴收入": "D89", "所得税": "D90", "以前年度损益调整": "D91",
}



def _stage_of(rel_path: str) -> str:
    for kw, stage in _STAGE_MAP.items():
        if kw in rel_path:
            return stage
    return "综合"


def build_index(force: bool = False) -> Dict[str, Any]:
    """扫描内部文件目录（仅文件名），构建/缓存结构化索引。"""
    if INDEX_CACHE.exists() and not force:
        try:
            return json.loads(INDEX_CACHE.read_text(encoding="utf-8"))
        except Exception:
            pass
    idx: Dict[str, Any] = {"root": str(INTERNAL_KB_DIR),
                           "categories": {}, "templates": [],
                           "confirmation_forms": [], "empty_dirs": []}
    if not INTERNAL_KB_DIR.exists():
        idx["error"] = f"目录不存在: {INTERNAL_KB_DIR}"
        return idx
    for top in sorted(INTERNAL_KB_DIR.iterdir()):
        if not top.is_dir():
            continue
        files = [f for f in top.rglob("*") if f.is_file()]
        idx["categories"][top.name] = {
            "file_count": len(files),
            "subdirs": sorted({str(f.parent.relative_to(top)) for f in files}),
        }
        if not files:
            idx["empty_dirs"].append(top.name)
        for f in files:
            if f.suffix.lower() not in (".doc", ".docx", ".xls", ".xlsx"):
                continue
            rel = str(f.relative_to(INTERNAL_KB_DIR))
            entry = {
                "path": str(f), "rel": rel, "name": f.name,
                "category": top.name,
                "stage": _stage_of(rel),
                "ext": f.suffix.lower(),
                "size_kb": round(f.stat().st_size / 1024, 1),
            }
            m = re.match(r"^([A-D]\d{1,2})", f.name)
            if m:
                entry["code"] = m.group(1)
            if "询证函" in rel:
                entry["is_confirmation_form"] = True
                idx["confirmation_forms"].append(entry)
            idx["templates"].append(entry)
    INDEX_CACHE.parent.mkdir(parents=True, exist_ok=True)
    INDEX_CACHE.write_text(json.dumps(idx, ensure_ascii=False, indent=1),
                           encoding="utf-8")
    return idx


def find_workpaper_templates(subject: str = "", stage: str = "") -> List[Dict]:
    """按 科目/阶段 找底稿模板（文件名匹配，供工作产出套用格式）。"""
    idx = build_index()
    out = []
    for t in idx["templates"]:
        if stage and stage not in t["stage"]:
            continue
        if subject:
            if subject in t["name"]:
                out.append(t)
                continue
            code = next((c for kw, c in _SUBJECT_TO_D_CODE.items()
                         if kw in subject), None)
            if code and t.get("code", "").startswith(code.rstrip("0123456789")[:1] + code[1:]):
                out.append(t)
        else:
            out.append(t)
    return out


def suggest_confirmation_form(subject: str = "往来款项",
                              form: str = "积极式") -> Optional[Dict]:
    """按科目与形式推荐询证函范本（积极式默认；低风险小额可消极式）。"""
    idx = build_index()
    forms = idx["confirmation_forms"]
    if not forms:
        return None
    # 1) 科目优先：银行/存货/固定资产/律师/前后任/短期投资
    for t in forms:
        for kws, _, _ in _CONFIRMATION_FORM_RULES:
            if all(k in t["name"] for k in kws) and any(k in subject for k in kws):
                return t
    # 2) 往来款项：按形式
    for t in forms:
        if "往来账项" in t["name"] and form in t["name"]:
            return t
    # 3) 兜底：第一个往来账项
    for t in forms:
        if "往来账项" in t["name"]:
            return t
    return forms[0] if forms else None


def completion_checklist() -> List[str]:
    """业务完成阶段"必填"清单（A 系列，按文件名含'必填'识别）。"""
    idx = build_index()
    return [t["name"] for t in idx["templates"]
            if t["stage"] == "业务完成阶段" and "必填" in t["name"]]


def summary() -> str:
    idx = build_index()
    lines = [f"内部知识库: {idx['root']}"]
    for cat, info in idx.get("categories", {}).items():
        mark = "（空目录）" if cat in idx.get("empty_dirs", []) else ""
        lines.append(f"  {cat}: {info['file_count']} 个文件{mark}")
    lines.append(f"  底稿模板总数: {len(idx.get('templates', []))}, "
                 f"其中询证函范本: {len(idx.get('confirmation_forms', []))}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(summary())
    print("\n完成阶段必填清单:", completion_checklist())
    f = suggest_confirmation_form("银行存款", "积极式")
    print("银行询证函推荐:", f["rel"] if f else None)
