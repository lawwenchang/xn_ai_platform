#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内部知识库接线：template_manager 合并内部模板 + 函证计划挂范本 + 查询端点"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def patch(path, old, new, tag, count=1):
    p = ROOT / path
    src = p.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert src.count(old) == count, f"[{tag}] 命中 {src.count(old)} 次而非 {count} 次"
    src = src.replace(old, new)
    p.write_text(src, encoding="utf-8", newline="\n")
    import ast
    ast.parse(src)
    print(f"  [PATCH] {tag}")


# ── 1. template_manager：可用模板合并内部知识库 ────────────────
patch("core/template_manager.py",
'''def get_available_templates() -> Dict[str, List[str]]:
    """列出所有可用模板"""
    result = {"word": [], "excel": [], "specs": []}
    for category in result:
        d = TEMPLATES_DIR / category
        if d.exists():
            result[category] = [f.name for f in d.iterdir() if f.is_file()]
    return result''',
'''def get_available_templates() -> Dict[str, List[str]]:
    """列出所有可用模板（平台本地 + 事务所内部知识库 C_底稿模板 等）"""
    result = {"word": [], "excel": [], "specs": []}
    for category in result:
        d = TEMPLATES_DIR / category
        if d.exists():
            result[category] = [f.name for f in d.iterdir() if f.is_file()]
    # 合并内部知识库模板（纯文件名，不读内容；C_底稿模板 D 系列/A 系列/询证函范本）
    try:
        from core.internal_kb_registry import build_index
        for t in build_index().get("templates", []):
            key = "excel" if t["ext"] in (".xls", ".xlsx") else "word"
            result.setdefault(key, []).append(f"{t['category']}/{t['rel'].split('/')[-1]}")
    except Exception:
        pass
    return result''',
    "template_manager 合并内部模板")

# ── 2. 函证计划：挂接询证函范本（积极式默认，含替代程序） ──────
patch("core/audit_procedures.py",
'''        "config": {"procedure_type": "confirmation", "threshold": threshold,
                   "amount_column": amount_column,
                   "form": "积极式（默认；重大/异常项目必须积极式，消极式仅限低风险小额）",
                   "follow_up": "未回函项目须执行替代程序：检查期后回款/对账单/"
                                "银行存款余额调节表/原始凭证，并登记回函差异"}}''',
'''        "config": {"procedure_type": "confirmation", "threshold": threshold,
                   "amount_column": amount_column,
                   "form": "积极式（默认；重大/异常项目必须积极式，消极式仅限低风险小额）",
                   "follow_up": "未回函项目须执行替代程序：检查期后回款/对账单/"
                                "银行存款余额调节表/原始凭证，并登记回函差异",
                   "form_template": _suggest_form_template(amount_column)}}''',
    "函证计划挂询证函范本")

# 在 build_confirmation_plan 前注入范本推荐助手
patch("core/audit_procedures.py",
'''def build_confirmation_plan(data_file: str, amount_column: str = "余额",
                            threshold: float = 500000) -> Dict[str, Any]:''',
'''def _suggest_form_template(subject: str = "") -> str:
    """按科目推荐本所询证函范本（仅文件路径，来自内部知识库文件名索引）"""
    try:
        from core.internal_kb_registry import suggest_confirmation_form
        t = suggest_confirmation_form(subject or "往来款项", "积极式")
        return t["path"] if t else ""
    except Exception:
        return ""


def build_confirmation_plan(data_file: str, amount_column: str = "余额",
                            threshold: float = 500000) -> Dict[str, Any]:''',
    "函证范本推荐助手")

# ── 3. routes：内部知识库查询端点 ──────────────────────────────
patch("api/routes.py",
'''# ═══════════════════════════════════════════════════════════════
# RAG 知识库管理 API
# ═══════════════════════════════════════════════════════════════''',
'''@router.get("/internal-kb/summary", summary="事务所内部知识库概览")
async def internal_kb_summary():
    """内部文件（SOP/底稿模板/询证函范本）文件名索引概览（不读文件内容）"""
    try:
        from core.internal_kb_registry import build_index, completion_checklist, summary
        idx = build_index()
        return {"success": True, "summary_text": summary(),
                "categories": idx.get("categories", {}),
                "templates_count": len(idx.get("templates", [])),
                "confirmation_forms": [t["rel"] for t in idx.get("confirmation_forms", [])],
                "completion_required": completion_checklist()}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════
# RAG 知识库管理 API
# ═══════════════════════════════════════════════════════════════''',
    "内部知识库查询端点")

print("内部知识库接线完成")
