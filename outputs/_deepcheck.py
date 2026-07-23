# -*- coding: utf-8 -*-
"""深度静态检查（无第三方依赖）：
1) UNDEFINED  : 引用了任何作用域都未定义的名字（NameError 运行时炸弹）
2) BAD_IMPORT : from core.x import y —— y 在目标模块中不存在（ImportError 炸弹）
3) NO_MODULE  : 导入的项目内模块文件不存在
结果 → outputs/_deepcheck_result.txt
"""
import os
import ast
import builtins
import symtable

ROOT = r"d:\Liu\ai_platform_code"
PROJ = os.path.join(ROOT, "phase2_v3")
OUT = os.path.join(ROOT, "outputs", "_deepcheck_result.txt")
EXCLUDE_DIRS = {"node_modules", "__pycache__", ".vs", ".idea", ".git",
                "models", "dist", "rag_cache", "venv", ".venv"}
BUILTIN_NAMES = set(dir(builtins)) | {
    "__file__", "__name__", "__doc__", "__package__", "__spec__",
    "__loader__", "__builtins__", "__debug__", "WindowsError",
}

py_files, sources = [], {}
for base, dirs, files in os.walk(ROOT):
    dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
    for f in files:
        if f.endswith(".py"):
            p = os.path.join(base, f)
            py_files.append(p)
            with open(p, encoding="utf-8-sig", errors="replace") as fh:
                sources[p] = fh.read()

report = []


def module_key(path):
    """phase2_v3/core/x.py -> 'core.x'（项目内可导入模块名）"""
    rel = os.path.relpath(path, PROJ)
    if rel.startswith(".."):
        return None
    return rel[:-3].replace(os.sep, ".")


def first_line_of(src, name):
    for i, ln in enumerate(src.splitlines(), 1):
        if name in ln:
            return i
    return 0


# ── 每个文件：模块级已定义名集合（symtable 精确覆盖 if/try 嵌套与 global 声明） ──
module_defined = {}
module_star = {}
tables = {}
for p, src in sources.items():
    try:
        st = symtable.symtable(src, p, "exec")
    except Exception as e:
        report.append("PARSE_FAIL %s : %s" % (p.replace(ROOT, "."), e))
        continue
    tables[p] = st
    defined = set()
    for s in st.get_symbols():
        if s.is_assigned() or s.is_imported():
            defined.add(s.get_name())

    def collect_globals(tb):
        for s in tb.get_symbols():
            if s.is_declared_global() and s.is_assigned():
                defined.add(s.get_name())
        for ch in tb.get_children():
            collect_globals(ch)

    collect_globals(st)
    module_defined[p] = defined
    module_star[p] = any(
        isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names)
        for n in ast.walk(ast.parse(src))
    )

key2path = {}
for p in sources:
    k = module_key(p)
    if k:
        key2path[k] = p

# ── 1) 未定义名检查 ──
for p, src in sources.items():
    if p not in tables or module_star.get(p):
        continue
    defined = module_defined[p] | BUILTIN_NAMES
    hits = {}

    def walk(tb):
        for s in tb.get_symbols():
            name = s.get_name()
            if not s.is_referenced():
                continue
            if s.is_parameter() or s.is_free():
                continue
            local_ok = s.is_assigned() or s.is_imported()
            if tb.get_type() in ("function", "class") and local_ok:
                continue
            if tb.get_type() == "module" and local_ok:
                continue
            if s.is_global() or tb.get_type() == "module":
                if name not in defined:
                    hits.setdefault(name, tb.get_name())
        for ch in tb.get_children():
            walk(ch)

    walk(tables[p])
    for name, scope in sorted(hits.items()):
        report.append("UNDEFINED  %s:%d  name='%s' (scope: %s)"
                      % (p.replace(ROOT, "."), first_line_of(src, name), name, scope))

# ── 2/3) 项目内导入核验 ──
for p, src in sources.items():
    try:
        tree = ast.parse(src)
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mod = node.module
            if not mod.split(".")[0] in ("core", "engine", "config", "api", "scripts", "tests"):
                continue
            tp = key2path.get(mod)
            if tp is None:
                report.append("NO_MODULE  %s:%d  from %s import ..." % (p.replace(ROOT, "."), node.lineno, mod))
                continue
            if module_star.get(tp):
                continue
            for a in node.names:
                if a.name != "*" and a.name not in module_defined.get(tp, set()):
                    report.append("BAD_IMPORT %s:%d  from %s import %s  (目标模块无此名)"
                                  % (p.replace(ROOT, "."), node.lineno, mod, a.name))
        elif isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split(".")[0]
                if top in ("core", "engine", "config", "api", "scripts", "tests"):
                    if a.name not in key2path and not os.path.isdir(os.path.join(PROJ, a.name.replace(".", os.sep))):
                        report.append("NO_MODULE  %s:%d  import %s" % (p.replace(ROOT, "."), node.lineno, a.name))

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("checked: %d files\nissues: %d\n" % (len(sources), len(report)))
    fh.write("\n".join(report))
