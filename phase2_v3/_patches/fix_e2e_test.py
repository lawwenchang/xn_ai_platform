#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修复 test_codegen_e2e.py：子进程 UTF-8 环境"""
from pathlib import Path

p = Path(__file__).resolve().parent.parent / "tests" / "test_codegen_e2e.py"
s = p.read_text(encoding="utf-8")

old1 = '''(RUN / "sandbox_exec.py").write_text(code, encoding="utf-8")
r = subprocess.run([sys.executable, "sandbox_exec.py"], cwd=str(RUN),
                   capture_output=True, text=True, timeout=120)'''
new1 = '''(RUN / "sandbox_exec.py").write_text(code, encoding="utf-8")
import os as _os
_env = {**_os.environ, "PYTHONIOENCODING": "utf-8"}
r = subprocess.run([sys.executable, "sandbox_exec.py"], cwd=str(RUN),
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace", env=_env, timeout=120)'''
old2 = '''r2 = subprocess.run([sys.executable, "sandbox_exec2.py"], cwd=str(RUN),
                    capture_output=True, text=True, timeout=120)'''
new2 = '''r2 = subprocess.run([sys.executable, "sandbox_exec2.py"], cwd=str(RUN),
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", env=_env, timeout=120)'''
assert old1 in s, "p1 未命中"
assert old2 in s, "p2 未命中"
s = s.replace(old1, new1).replace(old2, new2)
p.write_text(s, encoding="utf-8", newline="\n")
import ast
ast.parse(s)
print("e2e test 修复完成")
