#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B1/B2/B3 交付快速验证（配合计划第一批）"""
import py_compile
import sys
from pathlib import Path

# Windows GBK 控制台/重定向防御：强制 UTF-8 输出
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 1) 语法编译
for f in ["api/routes.py", "config/few_shot_examples.py",
          "core/pipeline_trace.py", "scripts/audit_bench.py",
          "scripts/build_v4_datasets.py"]:
    py_compile.compile(str(ROOT / f), doraise=True)
print("[PASS] 5 个文件语法编译通过")

# 2) B3 动态注入行为
from config.few_shot_examples import build_dynamic_few_shot, build_few_shot_section
full = build_few_shot_section()
hit = build_dynamic_few_shot("帮我核对银行流水和台账，差异控制在5%以内")
assert "最相关" in hit and "银行流水与台账核对" in hit
assert len(hit) < len(full) * 0.7, "动态注入应显著短于全量"
miss = build_dynamic_few_shot("完全无关的指令xyz")
assert "请参照以下范例" in miss, "未命中应回退全量"
print(f"[PASS] B3 动态注入：命中 {len(hit)} 字符 vs 全量 {len(full)} 字符"
      f"（省 {1 - len(hit) / len(full):.0%} token）；未命中自动回退全量")

# 3) B2 埋点与 API 接线（静态符号检查，避免重量级 import）
src = (ROOT / "api/routes.py").read_text(encoding="utf-8")
n_calls = src.count("trace_record(")
assert n_calls >= 7, f"埋点数不足: {n_calls}"
assert '@router.get("/runs/{run_id}/trace"' in src
assert '@router.get("/pipeline/stats"' in src
assert "build_dynamic_few_shot(user_intent" in src
print(f"[PASS] routes.py：trace_record x{n_calls} + 2 个可观测 API + B3 接线全部就位")

# 4) B2 模块功能（复用其自测）
from core.pipeline_trace import record, get_trace, waterfall, stats
rid = "RUN_B123_CHECK"
record(rid, "dag_compile", "OK", 1234, "verify")
assert any(e["stage"] == "dag_compile" for e in get_trace(rid))
assert "dag_compile" in waterfall(rid)
assert stats(5)["runs"] >= 1
print("[PASS] B2 pipeline_trace 读写/瀑布/聚合正常")

print("\nB1/B2/B3 交付验证全部通过 ✅")
