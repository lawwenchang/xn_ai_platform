# -*- coding: utf-8 -*-
"""启动器：拉起前端 vite（分离进程）+ 同步执行冒烟测试，结果落盘"""
import subprocess
import sys

OUT_DIR = r"d:\Liu\ai_platform_code\outputs"
FE_DIR = r"d:\Liu\ai_platform_code\phase2_v3\frontend"
PROJ = r"d:\Liu\ai_platform_code\phase2_v3"
NODE = r"E:\Program Files\nodejs\node.exe"

# 1) 前端 vite dev server（分离进程，日志 → frontend.log）
flog = open(OUT_DIR + r"\frontend.log", "w", encoding="utf-8")
subprocess.Popen(
    [NODE, r"node_modules\vite\bin\vite.js"],
    cwd=FE_DIR, stdout=flog, stderr=subprocess.STDOUT,
    creationflags=0x00000008 | 0x00000200,  # DETACHED_PROCESS | NEW_PROCESS_GROUP
)

# 2) 冒烟测试（同步等待，日志 → smoke.log）
with open(OUT_DIR + r"\smoke.log", "w", encoding="utf-8") as slog:
    try:
        rc = subprocess.run(
            [sys.executable, r"tests\smoke_test_new_modules.py"],
            cwd=PROJ, stdout=slog, stderr=subprocess.STDOUT, timeout=600,
        )
        slog.write("\nEXITCODE=%d\n" % rc.returncode)
    except Exception as e:
        slog.write("\nLAUNCHER_ERROR: %r\n" % e)

open(OUT_DIR + r"\_launcher_done.txt", "w").write("ALL_DONE")
