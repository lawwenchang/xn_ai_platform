#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""routes.py 补丁 D3b：legacy 路径对账快车道 + 文档格式过滤"""
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "api" / "routes.py"
src = P.read_text(encoding="utf-8").replace("\r\n", "\n")

OLD = '''        input_files = list(input_dir.iterdir()) if input_dir.exists() else []
        excel_files = [f for f in input_files if f.suffix.lower() in (".xlsx", ".xls", ".csv")]

        if len(excel_files) >= 2 and ("匹配" in intent or "核对" in intent or "对账" in intent or "比对" in intent):'''

NEW = '''        input_files = list(input_dir.iterdir()) if input_dir.exists() else []
        excel_files = [f for f in input_files if f.suffix.lower()
                       in (".xlsx", ".xls", ".csv", ".docx", ".doc", ".pdf", ".md", ".txt")]

        # ═══ 专业对账快车道（legacy 路径）：序时账×流水 → bank_reconcile_engine ═══
        if any(k in intent for k in ("对账", "核对", "核账", "相符", "对一下")):
            _pair = _detect_reconcile_scenario(input_dir)
            if _pair:
                try:
                    from core.bank_reconcile_engine import reconcile_files
                    _cfg = {}
                    _acc = _account_from_intent(intent)
                    if _acc:
                        _cfg["account"] = _acc
                    print(f"[对账快车道] 序时账={_pair[0].name} × 流水={_pair[1].name}, cfg={_cfg}")
                    _res = reconcile_files(_pair[0], _pair[1], _cfg, output_dir)
                    _st = _res["stats"]
                    output_files_final = [f.name for f in output_dir.iterdir() if f.is_file()]
                    _get_snapshot_mgr().update_outputs(
                        run_id=run_id, output_files=output_files_final,
                        validation_results=[{"check": "bank_reconcile_engine", "passed": True}],
                        all_passed=True)
                    _get_snapshot_mgr().update_status(run_id, "COMPLETED")
                    _save_logs(run_id, [
                        f"[对账快车道] 匹配率 账={_st['book_match_rate']}% 银={_st['bank_match_rate']}%",
                        f"[对账快车道] 未达四分类: {_st['timing_categories']}",
                        f"[对账快车道] 交付物: {_res.get('output_files')}"])
                    if record:
                        _generate_report_if_needed(run_id, output_dir, [])
                    return
                except Exception as _e:
                    print(f"[对账快车道] 执行失败，回退常规链路: {_e}")

        if len(excel_files) >= 2 and ("匹配" in intent or "核对" in intent or "对账" in intent or "比对" in intent):'''

assert src.count(OLD) == 1, f"legacy 分支命中 {src.count(OLD)} 次"
src = src.replace(OLD, NEW)
P.write_text(src, encoding="utf-8", newline="\n")
import ast
ast.parse(src)
print("routes 补丁D3b 完成，AST OK")
