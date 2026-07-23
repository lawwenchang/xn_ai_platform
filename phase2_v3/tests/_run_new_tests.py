#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性运行新增测试（分摊 pandas/fitz 导入开销）"""
import runpy
import sys
import traceback

FAILED = []
for t in ["tests/test_document_loader.py",
          "tests/test_bank_reconcile_engine.py",
          "tests/test_audit_sampling.py",
          "tests/test_generic_ledger.py",
          "tests/test_codegen_e2e.py",
          "tests/test_presets.py"]:
    print("=" * 25, t)
    try:
        runpy.run_path(t, run_name="__main__")
    except SystemExit as e:
        if e.code:
            FAILED.append((t, f"SystemExit({e.code})"))
            traceback.print_exc()
    except Exception:
        FAILED.append((t, "exception"))
        traceback.print_exc()

print("\n" + "=" * 50)
if FAILED:
    print("失败:", [f[0] for f in FAILED])
    sys.exit(1)
print("全部测试通过")
