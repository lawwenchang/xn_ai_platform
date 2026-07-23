#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""预设统一 BCD：dag_compiler 派生 + fallback_prompts 新键 + routes 接线"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def patch(path, old, new, tag):
    p = ROOT / path
    src = p.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert src.count(old) == 1, f"[{tag}] 命中 {src.count(old)} 次而非 1 次"
    src = src.replace(old, new)
    p.write_text(src, encoding="utf-8", newline="\n")
    import ast
    ast.parse(src)
    print(f"  [PATCH] {tag}")


# ── B. dag_compiler.PRESET_BUTTONS → 从 config.presets 派生 ────
p1 = ROOT / "core" / "dag_compiler.py"
s1 = p1.read_text(encoding="utf-8").replace("\r\n", "\n")
i1 = s1.index('PRESET_BUTTONS: Dict[str, Dict[str, Any]] = {')
i2 = s1.index('def get_preset_button_config')
NEW_BLOCK = '''# ═══════════════════════════════════════════════════════════════
# 预设按钮的固化 Prompt 映射（由 config.presets 派生，单一事实来源）
# ═══════════════════════════════════════════════════════════════

def _build_preset_buttons() -> Dict[str, Dict[str, Any]]:
    """从 config.presets 注册表派生（含别名归一），取代旧的四键硬编码"""
    try:
        from config.presets import PRESETS, all_keys_with_aliases
        out: Dict[str, Dict[str, Any]] = {}
        canon: Dict[str, Dict[str, Any]] = {}
        for key, p in PRESETS.items():
            if not p.get("dag", True):
                continue
            canon[key] = {
                "system_prompt_suffix": p.get("system_suffix", ""),
                "default_operators": p.get("default_operators", []),
                "scenario": p.get("scenario", ""),
                "review_points": p.get("review_points", []),
            }
        for alias, key in all_keys_with_aliases().items():
            if key in canon:
                out[alias] = canon[key]
        return out
    except Exception:
        return {}


PRESET_BUTTONS: Dict[str, Dict[str, Any]] = _build_preset_buttons()


'''
s1 = s1[:i1] + NEW_BLOCK + s1[i2:]
p1.write_text(s1, encoding="utf-8", newline="\n")
import ast
ast.parse(s1)
print("  [PATCH] dag_compiler PRESET_BUTTONS 派生化")

# ── C. fallback_prompts：注册表派生 + 场景关键词扩充 ───────────
patch("config/fallback_prompts.py",
'''def get_fallback_prompt(scenario: str) -> str:
    """
    获取降级模式 Prompt''',
'''def _registry_fallback_prompts() -> Dict[str, str]:
    """从 config.presets 注册表派生降级 Prompt（含别名归一）"""
    out: Dict[str, str] = {}
    try:
        from config.presets import PRESETS, all_keys_with_aliases
        canon: Dict[str, str] = {}
        for key, p in PRESETS.items():
            if not p.get("dag", True):
                continue
            suffix = (p.get("system_suffix") or "").strip()
            if suffix:
                canon[key] = f"{DEGRADATION_HEADER}\\n\\n## {p['label']}（降级模式）\\n" + suffix
        for alias, key in all_keys_with_aliases().items():
            if key in canon:
                out[alias] = canon[key]
    except Exception:
        pass
    return out


# 注册表派生的降级 Prompt 并入（注册表优先，同名旧键被覆盖）
FALLBACK_PROMPTS.update(_registry_fallback_prompts())


def get_fallback_prompt(scenario: str) -> str:
    """
    获取降级模式 Prompt''',
    "fallback_prompts 注册表派生")

patch("config/fallback_prompts.py",
'''    if any(kw in intent_lower for kw in ["生成报告", "报告生成", "出报告", "写报告", "报告正文", "附注", "函证", "文档"]):
        return "文档生成"''',
'''    if any(kw in intent_lower for kw in ["医保", "社保", "统筹", "补贴", "退费", "专项拨款", "提取"]):
        return "提取式核对"
    elif any(kw in intent_lower for kw in ["对账", "核账", "相符", "银企"]):
        return "银行对账"
    elif any(kw in intent_lower for kw in ["跨文件", "跨文档", "两份", "两个文件", "文档对比", "文件比对"]):
        return "跨文件对比"
    elif any(kw in intent_lower for kw in ["大额"]):
        return "大额交易筛查"
    elif any(kw in intent_lower for kw in ["生成报告", "报告生成", "出报告", "写报告", "报告正文", "附注", "函证", "文档"]):
        return "文档生成"''',
    "detect_scenario 场景关键词扩充")

# ── D. routes：fallback 链路使用 preset_button + /presets 端点 ─
patch("api/routes.py",
'''async def _fallback_compiler(catalog_text: str, user_intent: str, preset_button: Optional[str]) -> Any:
    scenario = detect_scenario(user_intent)
    system_prompt = get_fallback_prompt(scenario)''',
'''async def _fallback_compiler(catalog_text: str, user_intent: str, preset_button: Optional[str]) -> Any:
    # 预设按钮优先（别名归一），未指定则按意图推断场景
    scenario = None
    if preset_button:
        try:
            from config.presets import normalize_preset_key
            scenario = normalize_preset_key(preset_button)
        except Exception:
            scenario = None
    if not scenario:
        scenario = detect_scenario(user_intent)
    system_prompt = get_fallback_prompt(scenario)
    print(f"[降级编译] 场景: {scenario}（preset_button={preset_button or '无'}）")''',
    "fallback 链路 preset_button 生效")

patch("api/routes.py",
'''@router.get("/internal-kb/summary", summary="事务所内部知识库概览")''',
'''@router.get("/presets", summary="预设按钮统一注册表")
async def list_presets():
    """前端预设按钮列表（单一事实来源 config/presets.py；前端动态渲染用）"""
    try:
        from config.presets import public_list
        return {"success": True, "presets": public_list()}
    except Exception as e:
        return {"success": False, "error": str(e), "presets": []}


@router.get("/internal-kb/summary", summary="事务所内部知识库概览")''',
    "/presets 端点")

print("预设统一 BCD 完成")
