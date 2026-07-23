#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模板管理器 (template_manager.py)
================================
本地预提取模板格式规则，不发送二进制文件到大模型。

三种使用方式：
    1. generate_report(template_name, data) → Word 报告（docxtpl Jinja2）
    2. apply_template(target_file, template_name) → 格式规范化
    3. get_template_info() → 列出可用模板供前端选择
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

# ═══════════════════════════════════════════════════════
# 路径配置
# ═══════════════════════════════════════════════════════

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "data" / "templates"
RULES_CACHE = TEMPLATES_DIR / "rules.json"


def get_available_templates() -> Dict[str, List[str]]:
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
    return result


def extract_rules_from_specs() -> Dict[str, Any]:
    """
    从 specs/ 目录下的格式说明文档中提取规则
    
    这些是 Word 文档，用 python-docx 读取文字内容，
    提取字体、行间距、编号等关键数值。
    """
    from docx import Document
    
    rules = {
        "fonts": {},
        "spacing": {},
        "numbering": {},
        "tables": {},
        "page": {},
    }
    
    specs_dir = TEMPLATES_DIR / "specs"
    if not specs_dir.exists():
        return rules
    
    for spec_file in specs_dir.iterdir():
        if not spec_file.suffix == ".docx":
            continue
        try:
            doc = Document(str(spec_file))
            full_text = "\n".join(p.text for p in doc.paragraphs)
            
            # 提取常见格式关键词
            import re
            
            # 字体大小
            for m in re.finditer(r'(?:字体|字号)[^\d]*(\d+)\s*(?:号|pt|磅)', full_text):
                rules["fonts"][m.group(0)] = int(m.group(1))
            
            # 行间距
            for m in re.finditer(r'(?:行间距|行距)[^\d]*(\d+\.?\d*)\s*(?:倍|pt|磅)', full_text):
                rules["spacing"][m.group(0)] = float(m.group(1))
            
            # 页边距
            for side in ["上", "下", "左", "右"]:
                for m in re.finditer(rf'(?:{side}边距|{side}页边距)[^\d]*(\d+\.?\d*)\s*(?:cm|厘米)', full_text):
                    rules["page"][side] = float(m.group(1))
            
            # 缩进
            for m in re.finditer(r'(?:首行缩进|缩进)[^\d]*(\d+\.?\d*)\s*(?:字符|cm|厘米)', full_text):
                rules["spacing"]["indent"] = float(m.group(1))
            
            print(f"[模板] 从 '{spec_file.name}' 提取到 {len(full_text)} 字符规则")
        except Exception as e:
            print(f"[模板] 读取 '{spec_file.name}' 失败: {e}")
    
    # 缓存
    RULES_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with open(RULES_CACHE, "w", encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False, indent=2)
    
    return rules


def get_rules() -> Dict[str, Any]:
    """获取格式规则（优先缓存）"""
    if RULES_CACHE.exists():
        try:
            with open(RULES_CACHE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return extract_rules_from_specs()


def apply_template_format(target_path: str, template_category: str = "excel", output_dir: Optional[str] = None) -> str:
    """
    按模板格式转换文件
    
    Args:
        target_path: 待转换的文件路径
        template_category: word / excel
        output_dir: 输出目录
    
    Returns: 转换后的文件路径
    """
    tmpl_dir = TEMPLATES_DIR / template_category
    if not tmpl_dir.exists():
        raise FileNotFoundError(f"模板目录不存在: {tmpl_dir}")
    
    templates = list(tmpl_dir.glob("*.docx")) + list(tmpl_dir.glob("*.xlsx")) + list(tmpl_dir.glob("*.xlsm"))
    if not templates:
        raise FileNotFoundError(f"模板目录为空: {tmpl_dir}")
    
    template_path = str(templates[0])  # 使用第一个模板
    out_dir = output_dir or str(Path(target_path).parent / "formatted")
    
    from core.format_engine import normalize_format
    results = normalize_format(template_path, [target_path], out_dir)
    return results[0] if results else ""


def generate_report(template_name: str, data: Dict[str, Any], output_path: str) -> str:
    """
    使用 Word 模板 + Jinja2 渲染生成报告
    
    Args:
        template_name: 模板文件名（如 '3.2（通用）专项审计报告模板.docx'）
        data: 结构化数据 {key: value}
        output_path: 输出路径
    
    Returns: 输出文件路径
    """
    from docxtpl import DocxTemplate
    
    tmpl_path = TEMPLATES_DIR / "word" / template_name
    if not tmpl_path.exists():
        alt = list((TEMPLATES_DIR / "word").glob("*.docx"))
        if alt:
            tmpl_path = alt[0]
        else:
            raise FileNotFoundError(f"找不到 Word 模板: {template_name}")
    
    doc = DocxTemplate(str(tmpl_path))
    doc.render(data)
    doc.save(output_path)
    return output_path


# 启动时自动提取规则
def init_templates():
    """初始化模板系统（服务启动时调用）"""
    rules = extract_rules_from_specs()
    templates = get_available_templates()
    print(f"[模板] 已加载 {sum(len(v) for v in templates.values())} 个模板文件, 规则 {len(rules.get('fonts',{}))} 条")
    return rules
