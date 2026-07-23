import sys; sys.stdout.reconfigure(encoding="utf-8"); p=r"d:\Liu\ai_platform_code\phase2_v3\api\routes.py"; c=open(p,encoding="utf-8").read(); marker="5. 查询执行状态"; idx=c.find(marker); 
while idx > 0 and c[idx-1] != chr(10): idx -= 1; 
idx -= 1;
new_code = """
# ── 4b. 确认关键词提案 ────────────────────────────────

class KeywordConfirmBody(BaseModel):
    action: str = "approve"
    patterns: str = ""
    category: str = ""
    approved_by: str = "审计师"


@router.post("/runs/{run_id}/confirm_keywords", summary="确认/修订关键词提案")
async def confirm_keywords(run_id: str, body: KeywordConfirmBody, background_tasks: BackgroundTasks):
    from core.keyword_resolver import (
        get_proposal, clear_proposal, approve_and_intake,
        backtest_patterns as _kw_backtest,
    )
    record = _get_snapshot_mgr().get_run(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="Run 不存在")
    if record.status != "PENDING_KEYWORD_CONFIRM":
        raise HTTPException(status_code=400, detail=f"当前状态 {record.status} 不允许确认关键词")
    proposal = get_proposal(run_id)
    final_patterns = body.patterns or (proposal.get("patterns", "") if proposal else "")
    if not final_patterns:
        raise HTTPException(status_code=400, detail="关键词不能为空")
    preview = {}
    try:
        import pandas as _pd3
        input_dir = record.input_dir
        input_files = list(input_dir.iterdir()) if input_dir.exists() else []
        bank_files = [f for f in input_files if f.is_file() and f.suffix.lower() in (".xlsx", ".xls", ".csv")]
        if bank_files:
            _sample = _pd3.read_excel(bank_files[0], nrows=5000) if bank_files[0].suffix.lower() != ".csv" else _pd3.read_csv(bank_files[0], nrows=5000, encoding="utf-8-sig")
            preview = _kw_backtest(final_patterns, _sample, ["摘要", "对方客户名称", "附言", "用途"])
    except Exception as _be:
        print(f"[confirm_keywords] backtest 失败: {_be}")
    kw_source, kw_version = "", ""
    if body.action == "approve":
        category = body.category or (proposal.get("category", "用户提案") if proposal else "用户提案")
        meta = proposal or {}
        kw_version = approve_and_intake(category, final_patterns, meta, body.approved_by)
        kw_source = "dictionary_" + kw_version
    else:
        category = body.category or "用户修订"
        meta = proposal or {}
        meta["依据摘要"] = "用户手动修订关键词"
        kw_version = approve_and_intake(category, final_patterns, meta, body.approved_by)
        kw_source = "user_approved@" + _now_date()
    clear_proposal(run_id)
    _get_snapshot_mgr().update_status(run_id, "RUNNING")
    background_tasks.add_task(_execute_keyword_confirmed, run_id=run_id, record=record, final_patterns=final_patterns, kw_source=kw_source, kw_version=kw_version, preview=preview)
    return {"run_id": run_id, "status": "RUNNING", "patterns": final_patterns, "kw_source": kw_source, "kw_version": kw_version, "preview": preview}


def _now_date() -> str:
    from datetime import date
    return date.today().isoformat()

""";
c = c[:idx] + new_code + c[idx:];
open(p,"w",encoding="utf-8").write(c);
print("Inserted!");
