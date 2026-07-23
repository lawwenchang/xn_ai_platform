#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dag_compiler 预设 prompt 修正 + sandbox_v2 Dockerfile 补文档解析库"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ── 1. dag_compiler 预设 prompt：机构名清洗限定 + 容差分层 ─────
P1 = ROOT / "core" / "dag_compiler.py"
src1 = P1.read_text(encoding="utf-8").replace("\r\n", "\n")
OLD1 = '''你是一位精通审计数据比对的专家。执行数据比对时必须：
1. 自动识别两表/多表的共有关键列（机构名称、日期、金额等）
2. 机构名称标准化处理（去除市县区中心管理等后缀）
3. 金额比对设定合理容差（默认1%，审计师可指定）
4. 支持日期窗口匹配（±3天默认）'''
NEW1 = '''你是一位精通审计数据比对的专家。执行数据比对时必须：
1. 自动识别两表/多表的共有关键列（机构名称、日期、金额等）；
   严禁使用"序号/编号/行号"作为连接键
2. 机构名称"去市县区中心管理后缀"仅用于医保回款场景，其他场景禁止
3. 容差分层：逐笔核对必须精确到分（tolerance_abs=0.01 元）；
   百分比容差（tolerance_pct）仅用于汇总层面，且须审计师明确指定
4. 支持日期窗口匹配（date_window_days，默认±3天）；
   银行对账注意方向镜像（序时账借方↔流水贷方收入）'''
assert src1.count(OLD1) == 1, f"dag_compiler 命中 {src1.count(OLD1)} 次"
src1 = src1.replace(OLD1, NEW1)
P1.write_text(src1, encoding="utf-8", newline="\n")
import ast
ast.parse(src1)
print("  [PATCH] dag_compiler 预设 prompt 修正")

# ── 2. sandbox_v2 Dockerfile：补文档解析库 ─────────────────────
P2 = ROOT / "engine" / "sandbox_v2.py"
src2 = P2.read_text(encoding="utf-8").replace("\r\n", "\n")
OLD2 = '''RUN apk add --no-cache gcc g++ musl-dev libffi-dev openssl-dev && \\
    pip install --no-cache-dir pandas numpy openpyxl xlrd && \\
    apk del gcc g++ musl-dev libffi-dev openssl-dev'''
NEW2 = '''RUN apk add --no-cache gcc g++ musl-dev libffi-dev openssl-dev && \\
    pip install --no-cache-dir pandas numpy openpyxl xlrd \\
        python-docx PyMuPDF pdfplumber chardet rapidfuzz && \\
    apk del gcc g++ musl-dev libffi-dev openssl-dev
# 注：python-docx/PyMuPDF/pdfplumber/chardet 使沙箱内可直接解析 docx/pdf/md/txt；
# 镜像需重建生效（docker build）。平台层 document_loader 物化路径不依赖本镜像。'''
assert src2.count(OLD2) == 1, f"sandbox_v2 命中 {src2.count(OLD2)} 次"
src2 = src2.replace(OLD2, NEW2)
P2.write_text(src2, encoding="utf-8", newline="\n")
ast.parse(src2)
print("  [PATCH] sandbox_v2 Dockerfile 补文档解析库")
print("全部完成")
