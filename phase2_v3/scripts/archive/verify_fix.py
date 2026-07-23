#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证银行流水对账报错修复：用同一组输入重新生成并执行 sandbox code"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Windows 控制台 UTF-8 防御
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

RUN_ID = "RUN_PROJ_2026_001_比较一下两个文件是否账款相符_20260721_090759_v7"
RUN_DIR = ROOT / "data" / "runs" / RUN_ID

if not RUN_DIR.exists():
    print(f"[ERROR] 找不到运行目录: {RUN_DIR}")
    sys.exit(1)

# 1. 读取原始 DAG
with open(RUN_DIR / "dag_blueprint.json", "r", encoding="utf-8") as f:
    dag_dict = json.load(f)

print(f"[INFO] 原始 DAG 算子: {[op['name'] for op in dag_dict.get('operators', [])]}")

# 2. 应用 _ensure_essential_operators（模拟后端执行前的修正）
from api.routes import _ensure_essential_operators
user_intent = "比较一下两个文件是否账款相符"
dag_fixed = _ensure_essential_operators(dag_dict, user_intent)

print(f"[INFO] 修正后 DAG 算子: {[op['name'] for op in dag_fixed.get('operators', [])]}")

# 3. 构造最小 RunRecord 并生成代码
from core.run_snapshot import RunRecord
record = RunRecord(
    run_id=RUN_ID,
    project_code="PROJ_2026_001",
    subject="比较一下两个文件是否账款相符",
    version=7,
    parent_run_id=None,
    user_intent=user_intent,
)
record.dag_blueprint = dag_fixed

from api.routes import _dag_to_python
new_code = _dag_to_python(dag_fixed, record)

# 4. 写入临时目录执行
TMP_DIR = ROOT / "data" / "verify_fix_tmp"
if TMP_DIR.exists():
    shutil.rmtree(TMP_DIR, ignore_errors=True)
TMP_DIR.mkdir(parents=True, exist_ok=True)

# 复制 inputs
shutil.copytree(RUN_DIR / "inputs", TMP_DIR / "inputs")

# 写入代码
code_path = TMP_DIR / "sandbox_code.py"
code_path.write_text(new_code, encoding="utf-8")

# 5. 执行
print("[INFO] 开始执行修复后的 sandbox code...")
proc = subprocess.run(
    [sys.executable, str(code_path)],
    cwd=str(TMP_DIR),
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    timeout=120,
)

print("--- STDOUT ---")
try:
    sys.stdout.buffer.write(proc.stdout.encode("utf-8", errors="replace"))
except Exception:
    print(proc.stdout)
print("\n--- STDERR ---")
try:
    sys.stdout.buffer.write(proc.stderr.encode("utf-8", errors="replace"))
except Exception:
    print(proc.stderr)
print(f"\n--- RETURN CODE: {proc.returncode} ---")

# 6. 检查输出
outputs_dir = TMP_DIR / "outputs"
if outputs_dir.exists():
    files = list(outputs_dir.iterdir())
    print(f"[INFO] 输出文件: {[f.name + ' (' + str(f.stat().st_size) + ')' for f in files]}")
else:
    print("[WARN] 没有 outputs 目录")

if proc.returncode == 0 and any(outputs_dir.glob("analysis_result.*")):
    print("\n✅ 修复验证通过：sandbox 执行成功并产出对账结果")
else:
    print("\n❌ 修复验证失败")
    sys.exit(1)
