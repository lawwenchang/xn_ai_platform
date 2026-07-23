#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sandbox_v2.py Dockerfile 补文档解析库（正则容错版）"""
import re
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "engine" / "sandbox_v2.py"
src = P.read_text(encoding="utf-8").replace("\r\n", "\n")

pattern = re.compile(
    r"pip install --no-cache-dir pandas numpy openpyxl xlrd && (\\+)\n")
m = pattern.search(src)
assert m, "未找到 pip install 行"
replacement = ("pip install --no-cache-dir pandas numpy openpyxl xlrd "
               "python-docx PyMuPDF pdfplumber chardet rapidfuzz && " + m.group(1) + "\n")
src = pattern.sub(replacement, src, count=1)
# 附加注释说明（若尚无）
if "镜像需重建生效" not in src:
    anchor = "apk del gcc g++ musl-dev libffi-dev openssl-dev"
    assert src.count(anchor) == 1
    src = src.replace(
        anchor,
        anchor + "\n# 注：python-docx/PyMuPDF/pdfplumber/chardet 使沙箱内可直接解析 "
        "docx/pdf/md/txt；镜像需重建生效（docker build）。", 1)
P.write_text(src, encoding="utf-8", newline="\n")
import ast
ast.parse(src)
print("sandbox_v2 Dockerfile 补丁完成，AST OK")
