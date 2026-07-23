"""Restore part 1: Token budget + API keys + sandbox fallback + constraint check + audit trace + scenario detection"""
import pathlib

path = pathlib.Path(r"d:\Liu\ai_platform_code\phase2_v3\api\routes.py")
c = path.read_text("utf-8")

# 1. Token budget
c = c.replace(
    'catalog_text = few_shot_text + \"\\n\\n\" + catalog_text\n\n    parent_summary = \"\"',
    'catalog_text = few_shot_text + \"\\n\\n\" + catalog_text\n\n    _MAX_CATALOG_CHARS = 35000\n    if len(catalog_text) > _MAX_CATALOG_CHARS:\n        _truncated = catalog_text[:_MAX_CATALOG_CHARS]\n        _last_nl = _truncated.rfind(\"\\n\")\n        if _last_nl > _MAX_CATALOG_CHARS * 0.8:\n            _truncated = _truncated[:_last_nl]\n        catalog_text = _truncated + \"\\n\\n[提示：数据目录过长已自动截断]\"\n        print(f\"[编译] Token 预算保护\")\n\n    parent_summary = \"\"'
)

# 2. 4 Dify API keys
c = c.replace(
    'DIFY_REFINE_API_KEY = os.environ.get(\"DIFY_REFINE_API_KEY\", \"\")',
    'DIFY_REFINE_API_KEY = os.environ.get(\"DIFY_REFINE_API_KEY\", \"\")\n\nDIFY_SINGLE_TABLE_KEY = os.environ.get(\"DIFY_SINGLE_TABLE_KEY\", \"\")\nDIFY_REPORT_GEN_KEY = os.environ.get(\"DIFY_REPORT_GEN_KEY\", \"\")\nDIFY_REPORT_REVIEW_KEY = os.environ.get(\"DIFY_REPORT_REVIEW_KEY\", \"\")\nDIFY_KNOWLEDGE_QA_KEY = os.environ.get(\"DIFY_KNOWLEDGE_QA_KEY\", \"\")'
)

# 3. Sandbox fallback
c = c.replace(
    '# ═══ 降级：本地 subprocess（Windows 无 Docker 时的兜底） ═══',
    '# ═══ 降级：本地 subprocess（Windows 无 Docker 时的兜底） ═══\n    await _execute_in_sandbox_legacy(run_id, code, run_dir)'
)

# 4. Constraint check
c = c.replace(
    '_generate_report_if_needed(run_id, output_dir, logs)\n            trace_record(run_id, \"sandbox_run\",',
    '_generate_report_if_needed(run_id, output_dir, logs)\n                _run_constraint_check(run_id, output_dir, logs)\n            trace_record(run_id, \"sandbox_run\",'
)

# 5. Audit trace + hash
c = c.replace(
    'code = _dag_to_python(dag_source, record)\n    except Exception as e:\n        _get_snapshot_mgr().update_status(run_id, \"FAILED\")',
    'code = _dag_to_python(dag_source, record)\n        _write_audit_trace(run_id, record.user_intent or \"\", dag_source)\n        _append_approval_hash(run_id, record.user_intent or \"\", dag_source, request.confirmed)\n    except Exception as e:\n        _get_snapshot_mgr().update_status(run_id, \"FAILED\")'
)

# 6. Scenario detection
c = c.replace(
    'dag_blueprint = await _call_dify_compiler(\n            catalog=catalog,',
    'dag_blueprint = None\n        is_single = (catalog.total_files == 1 and any(k in user_intent for k in [\"筛选\", \"筛查\", \"分类\"]) and not any(k in user_intent for k in [\"核对\", \"对账\", \"匹配\", \"比对\"]))\n        if is_single:\n            catalog_text = _format_catalog_for_prompt(catalog)\n            single_result = await _call_dify_single_table(catalog_text, user_intent)\n            if single_result:\n                print(f\"[场景路由] 单表筛选模式\")\n                dag_blueprint = _parse_single_table_result(single_result, catalog)\n        if dag_blueprint is None:\n            dag_blueprint = await _call_dify_compiler(\n                catalog=catalog,'
)

path.write_text(c, "utf-8")
print("Part 1 done")
