# -*- coding: utf-8 -*-
"""全量静态检查：语法 + pyflakes 未定义名 + 孤儿模块扫描 → _pycheck_result.txt"""
import os
import ast
import io
import sys
import subprocess

ROOT = r"d:\Liu\ai_platform_code"
OUT = os.path.join(ROOT, "outputs", "_pycheck_result.txt")
EXCLUDE_DIRS = {"node_modules", "__pycache__", ".vs", ".idea", ".git",
                "models", "dist", "rag_cache", "venv", ".venv"}

py_files = []
for base, dirs, files in os.walk(ROOT):
    dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
    for f in files:
        if f.endswith(".py"):
            py_files.append(os.path.join(base, f))

sources = {}
for p in py_files:
    with open(p, encoding="utf-8-sig", errors="replace") as fh:
        sources[p] = fh.read()

lines = ["scanned: %d py files" % len(py_files)]

# ── 1) 全量语法检查 ──
syntax_fail = []
for p, src in sources.items():
    try:
        ast.parse(src, filename=p)
    except SyntaxError as e:
        syntax_fail.append("SYNTAX %s:%s:%s %s" % (p.replace(ROOT, "."), e.lineno, e.offset, e.msg))
lines.append("syntax_errors: %d" % len(syntax_fail))
lines += syntax_fail

# ── 2) pyflakes 深度检查（自动安装） ──
try:
    import pyflakes.api  # noqa
except ImportError:
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "pyflakes",
                        "--disable-pip-version-check", "--quiet"], timeout=180)
    except Exception as e:
        lines.append("pip_install_failed: %s" % e)

try:
    from pyflakes.api import check
    from pyflakes.reporter import Reporter

    buf_out, buf_err = io.StringIO(), io.StringIO()
    rep = Reporter(buf_out, buf_err)
    total = 0
    for p, src in sources.items():
        total += check(src, p.replace(ROOT, "."), rep)
    lines.append("pyflakes_issues: %d" % total)
    lines.append(buf_out.getvalue())
    ev = buf_err.getvalue()
    if ev.strip():
        lines.append("--- pyflakes stderr ---")
        lines.append(ev)
except ImportError:
    lines.append("pyflakes_not_available")

# ── 3) 孤儿模块扫描：core/engine 模块无任何外部引用 ──
lines.append("--- module reference scan ---")
for pkg in ("core", "engine"):
    pkg_dir = os.path.join(ROOT, "phase2_v3", pkg)
    if not os.path.isdir(pkg_dir):
        continue
    for f in sorted(os.listdir(pkg_dir)):
        if not f.endswith(".py"):
            continue
        mod = f[:-3]
        needle = "%s.%s" % (pkg, mod)
        refs = 0
        for p, src in sources.items():
            if os.path.basename(p) == f and os.path.dirname(p).endswith(pkg):
                continue
            if needle in src:
                refs += 1
        lines.append("%-12s %-28s referenced_by=%d%s" % (pkg, mod, refs, "   <== ORPHAN?" if refs == 0 else ""))

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines))
