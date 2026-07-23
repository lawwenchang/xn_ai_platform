p=r"d:\Liu\ai_platform_code\phase2_v3\api\routes.py";c=open(p,encoding="utf-8").read();marker="# ── 5. 查询执行状态";idx=c.find(marker);
while idx>0 and c[idx-1]!=chr(10): idx-=1;
idx-=1;
fn="""
async def _execute_keyword_confirmed(run_id, record, final_patterns, kw_source, kw_version, preview):
    from core.matching_engine import run_matching_pipeline
    logs = []
    try:
        input_dir = record.input_dir
        output_dir = record.run_dir / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        print(f"[Standalone] Run {run_id} 确认后执行: pattern={final_patterns[:60]} source={kw_source}")
        match_result = run_matching_pipeline(input_dir, output_dir, patterns=final_patterns, kw_source=kw_source, kw_version=kw_version)
        logs.append("[匹配引擎] 匹配流水线执行完成")
        output_files_final = [f.name for f in output_dir.iterdir() if f.is_file()]
        if output_files_final:
            _get_snapshot_mgr().update_outputs(run_id=run_id, output_files=output_files_final, validation_results=[{"check": "matching_engine", "passed": True}], all_passed=True)
        dag_ops = (record.dag_blueprint or {}).get("operators", [])
        input_names = [f.name for f in input_dir.iterdir() if f.is_file()]
        from core.report_generator import generate_audit_report
        dag_bp = record.dag_blueprint or {}
        explanation = dag_bp.get("match_explanation", "") if isinstance(dag_bp, dict) else ""
        engine_match_logic = match_result.get("match_logic", {}) if match_result else {}
        match_info = {"patterns": final_patterns, "columns": engine_match_logic.get("筛选列", []), "kw_source": kw_source, "kw_preview": preview or {}, "method": match_result.get("strategy_name", "多列联合匹配") if match_result else "多列联合匹配", "explanation": explanation}
        rp = generate_audit_report(run_id=run_id, user_intent=record.user_intent or "", dag_operators=dag_ops, output_dir=output_dir, input_files=input_names, execution_logs=logs, match_logic=match_info)
        output_files_final.append(rp.name)
        _get_snapshot_mgr().update_outputs(run_id=run_id, output_files=output_files_final, validation_results=[{"check": "matching_engine", "passed": True}], all_passed=True)
        _get_snapshot_mgr().update_status(run_id, "COMPLETED")
        print(f"[Standalone] Run {run_id} 完成")
    except Exception as e:
        import traceback
        print(f"[Standalone] Run {run_id} 失败: {traceback.format_exc()}")
        _get_snapshot_mgr().update_status(run_id, "FAILED")


""";
c=c[:idx]+fn+c[idx:];
open(p,"w",encoding="utf-8").write(c);
print("Inserted fn");
