#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""routes.py 补丁 D3a：对账检测助手 + Docker 路径快车道"""
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "api" / "routes.py"
src = P.read_text(encoding="utf-8").replace("\r\n", "\n")


def rep(old: str, new: str, tag: str):
    global src
    assert src.count(old) == 1, f"[{tag}] 命中 {src.count(old)} 次而非 1 次"
    src = src.replace(old, new)
    print(f"  [PATCH] {tag}")


rep('''async def _execute_in_sandbox(run_id: str, code: str, run_dir: Path) -> None:
    """在沙箱中执行审计代码（异步非阻塞 + 并发信号量控制）。
    策略：优先 Docker 容器（sandbox_v3），失败/不可用时降级为本地 subprocess。
    """''',
    '''def _detect_reconcile_scenario(input_dir: Path):
    """检测 序时账×银行流水 组合，返回 (book_path, bank_path) 或 None。

    基于列特征确定性识别（凭证号/科目 → 序时账；对方/账号/余额 → 流水），
    支持 Excel/CSV 与 docx/pdf/md 文档中的表格。
    """
    try:
        from core.bank_reconcile_engine import detect_book_type, JOURNAL, BANK_STATEMENT
        from core.document_loader import load_tables
        journal = bank = None
        for f in sorted(input_dir.glob("*")):
            if f.suffix.lower() not in (".xlsx", ".xls", ".csv", ".docx", ".pdf", ".md"):
                continue
            try:
                tables = load_tables(f)
                if not tables:
                    continue
                t = detect_book_type(tables[0], f.name)
                if t == JOURNAL and journal is None:
                    journal = f
                elif t == BANK_STATEMENT and bank is None:
                    bank = f
            except Exception:
                continue
            if journal and bank:
                break
        return (journal, bank) if journal and bank else None
    except Exception as e:
        print(f"[对账快车道] 场景检测失败（按普通链路处理）: {e}")
        return None


def _account_from_intent(intent: str) -> str:
    """从用户意图提取银行账号线索（如 '农行5927' / 纯数字账号）"""
    m = re.search(r"(?:账号|账户|农行|工行|建行|中行|招行|交行|徽商|邮储|农商)(\\D{0,4}?\\d{3,})", intent)
    if m:
        return m.group(1)
    m2 = re.search(r"\\b(\\d{4,})\\b", intent)
    return m2.group(1) if m2 else ""


async def _execute_in_sandbox(run_id: str, code: str, run_dir: Path) -> None:
    """在沙箱中执行审计代码（异步非阻塞 + 并发信号量控制）。
    策略：优先 Docker 容器（sandbox_v3），失败/不可用时降级为本地 subprocess。
    """''',
    "对账场景检测助手")

rep('''    output_dir = run_dir / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    input_dir = run_dir / "inputs"

    # ═══ 优先：Docker 沙箱（sandbox_v3 完整容器生灭） ═══''',
    '''    output_dir = run_dir / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    input_dir = run_dir / "inputs"

    # ═══ 专业对账快车道：序时账×银行流水 → bank_reconcile_engine（平台层执行） ═══
    _rec0 = _get_snapshot_mgr().get_run(run_id)
    _intent0 = (_rec0.user_intent or "") if _rec0 else ""
    if any(k in _intent0 for k in ("对账", "核对", "核账", "相符", "对一下")):
        _pair = _detect_reconcile_scenario(input_dir)
        if _pair:
            try:
                from core.bank_reconcile_engine import reconcile_files
                _cfg = {}
                _acc = _account_from_intent(_intent0)
                if _acc:
                    _cfg["account"] = _acc
                print(f"[对账快车道] 序时账={_pair[0].name} × 流水={_pair[1].name}, cfg={_cfg}")
                _res = await asyncio.to_thread(reconcile_files, _pair[0], _pair[1], _cfg, output_dir)
                _st = _res["stats"]
                _get_snapshot_mgr().update_status(run_id, "COMPLETED")
                _update_outputs(run_id, output_dir)
                _save_logs(run_id, [
                    f"[对账快车道] 匹配率 账={_st['book_match_rate']}% 银={_st['bank_match_rate']}% "
                    f"(L1={_st['matched_L1']}, L2={_st['matched_L2']}, L3组={_st['matched_L3_groups']}, L4待复核={_st['review_L4']})",
                    f"[对账快车道] 未达四分类: {_st['timing_categories']}",
                    f"[对账快车道] 交付物: {_res.get('output_files')}",
                ])
                _generate_report_if_needed(run_id, output_dir, [])
                return
            except Exception as _e:
                print(f"[对账快车道] 执行失败，回退常规链路: {_e}")

    # ═══ 优先：Docker 沙箱（sandbox_v3 完整容器生灭） ═══''',
    "Docker 路径对账快车道")

P.write_text(src, encoding="utf-8", newline="\n")
import ast
ast.parse(src)
print("routes 补丁D3a 完成，AST OK")
