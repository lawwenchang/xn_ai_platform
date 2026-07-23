#!/usr/bin/env python3
"""测试：Celery异步下载 + RPA法规采集 + 审计三模块"""
import os, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RESULTS = []
def check(name, fn):
    try:
        fn()
        RESULTS.append((name, "PASS"))
        print(f"  [PASS] {name}")
    except Exception as e:
        RESULTS.append((name, "FAIL"))
        print(f"  [FAIL] {name}: {e}")


# ═══ 1. 异步下载 ═══
def t_async_download_submit():
    from core.async_download import submit, get_status
    tmp = Path(tempfile.mkdtemp()) / "outputs"
    tmp.mkdir()
    (tmp / "底稿.xlsx").write_text("test data", encoding="utf-8")
    (tmp / "报告.docx").write_text("report", encoding="utf-8")

    task = submit("RUN_TEST", tmp)
    assert task.status in ("QUEUED", "PACKING"), task.status
    time.sleep(0.5)
    st = get_status(task.task_id)
    assert st and st.status == "COMPLETED", st.status if st else "None"
    assert Path(st.zip_path).exists()
    assert st.file_size > 0
    import shutil; shutil.rmtree(Path(st.zip_path).parent, ignore_errors=True)

def t_async_download_empty_dir():
    from core.async_download import submit, get_status
    tmp = Path(tempfile.mkdtemp()) / "outputs_empty"
    tmp.mkdir()
    task = submit("RUN_EMPTY", tmp)
    time.sleep(0.3)
    st = get_status(task.task_id)
    assert st.status == "FAILED"
    assert "为空" in st.error

# ═══ 2. RPA 法规采集 ═══
def t_rpa_store_roundtrip():
    from core.rpa_collector import RegulationStore, RegulationRecord
    import core.rpa_collector as rpc
    import tempfile
    tmp_dir = Path(tempfile.mkdtemp())
    old_dir = rpc.REGULATIONS_DIR
    rpc.REGULATIONS_DIR = tmp_dir
    try:
        s = RegulationStore()
        r = RegulationRecord(source="中注协", title="测试准则第1号", publish_date="2024-01-01",
                             text="测试正文")
        s.add(r)
        # 验证基础 CRUD 能力（不依赖文件持久化的 approve 闭环）
        assert s.exists("测试准则第1号", "2024-01-01")
        assert len(s.get_all()) >= 1
        # 修改状态
        s.get_all()[0].status = "APPROVED"
        s._save()
        # 重新加载验证持久化
        s2 = RegulationStore()
        assert s2.exists("测试准则第1号", "2024-01-01")
        assert s2.get_all()[0].status == "APPROVED"
    finally:
        rpc.REGULATIONS_DIR = old_dir

def t_rpa_scan():
    from core.rpa_collector import scan_local_files
    # 环境没有真实法规目录，验证不崩溃即可
    records = scan_local_files(Path(tempfile.mkdtemp()))
    assert isinstance(records, list)

# ═══ 3. 审计三模块 DAG 蓝图 ═══
def t_confirmation_dag():
    from core.audit_procedures import build_confirmation_plan
    dag = build_confirmation_plan("应收账款.xlsx", threshold=300000)
    ops = dag["operators"]
    assert dag["procedure"] == "confirmation"
    assert ops[0]["name"] == "Load" and ops[-1]["name"] == "Export"
    assert "ConditionCheck" in [o["name"] for o in ops]

def t_walkthrough_dag():
    from core.audit_procedures import build_walkthrough_dag
    dag = build_walkthrough_dag(["订单.xlsx", "发货.xlsx", "收款.xlsx"], "订单编号")
    assert dag["procedure"] == "walkthrough"
    assert any(o["name"] == "Merge" for o in dag["operators"])

def t_sampling_dag():
    from core.audit_procedures import build_sampling_plan
    dag = build_sampling_plan("银行流水.xlsx", method="monetary_unit", sample_size=15,
                              risk_weight="风险等级")
    assert dag["procedure"] == "sampling"
    assert dag["config"]["sample_size"] == 15

def t_walkthrough_breaks():
    from core.audit_procedures import analyze_walkthrough_breaks
    r = analyze_walkthrough_breaks(["日期", "金额"])
    assert not r["complete"] and "状态" in r["missing_nodes"]
    r2 = analyze_walkthrough_breaks(["日期", "金额", "状态"])
    assert r2["complete"]

def t_procedure_info():
    from core.audit_procedures import get_procedure_info, build_procedure_dag
    info = get_procedure_info()
    assert len(info) == 3
    dag = build_procedure_dag("sampling", data_file="test.xlsx")
    assert dag["procedure"] == "sampling"

if __name__ == "__main__":
    print("=" * 60)
    print("Celery + RPA + 审计三模块 测试")
    for t in [t_async_download_submit, t_async_download_empty_dir,
              t_rpa_store_roundtrip, t_rpa_scan,
              t_confirmation_dag, t_walkthrough_dag, t_sampling_dag,
              t_walkthrough_breaks, t_procedure_info]:
        check(t.__name__, t)
    nf = sum(1 for _, s in RESULTS if s == "FAIL")
    print(f"\n结果: {len(RESULTS) - nf} 通过 / {nf} 失败")
