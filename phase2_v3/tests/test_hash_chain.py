#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SHA-256 哈希链验证测试（白皮书 §6.4）
运行：cd phase2_v3 && python tests/test_hash_chain.py
"""
import os
import sys
import sqlite3
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.run_snapshot import HashChain, HashChainEntry

RESULTS = []

def check(name, fn):
    try:
        fn()
        RESULTS.append((name, "PASS"))
        print(f"  [PASS] {name}")
    except Exception as e:
        RESULTS.append((name, "FAIL"))
        print(f"  [FAIL] {name}: {type(e).__name__}: {e}")
        import traceback; traceback.print_exc(limit=2)


# 用临时数据库做隔离测试
TMP_DB = Path(tempfile.gettempdir()) / "test_hash_chain.db"


def t_genesis_and_chain():
    """基本链构建：5 条生命周期事件"""
    if TMP_DB.exists():
        TMP_DB.unlink()
    e1 = HashChain.record("PRJ001", "RUN_PRJ001_医保_v1", "RUN_CREATED",
                          "审计师提交：帮我核对医保回款", "张三", db_path=TMP_DB)
    assert e1.prev_chain_hash == "GENESIS", e1.prev_chain_hash
    assert len(e1.chain_hash) == 64  # SHA-256 hex

    e2 = HashChain.record("PRJ001", "RUN_PRJ001_医保_v1", "DAG_APPROVED",
                          "审批通过：使用RegexFilter+Reconcile方案", "李四", db_path=TMP_DB)
    assert e2.prev_chain_hash == e1.chain_hash  # 链式连接

    e3 = HashChain.record("PRJ001", "RUN_PRJ001_医保_v1", "EXECUTION_COMPLETED",
                          "执行完成：匹配127笔，差异3笔共32000.00元", db_path=TMP_DB)
    e4 = HashChain.record("PRJ001", "RUN_PRJ001_医保_v1", "MANUAL_CORRECTION",
                          "补录：摘要'医疗补助'实质属医保回款，理由：业务实质判断", "张三", db_path=TMP_DB)
    e5 = HashChain.record("PRJ001", "RUN_PRJ001_医保_v1", "REPORT_FINALIZED",
                          "报告定稿：医保回款核对底稿.xlsx", "王五", db_path=TMP_DB)
    assert e5.prev_chain_hash == e4.chain_hash


def t_verify_valid():
    """完整链验证通过"""
    r = HashChain.verify("PRJ001", db_path=TMP_DB)
    assert r["valid"] is True, r
    assert r["total"] == 5, r["total"]
    assert r["break_index"] is None


def t_tamper_detection():
    """篡改检测：直接改数据库中某条记录的内容哈希 → 链断裂"""
    with sqlite3.connect(str(TMP_DB)) as conn:
        # 模拟篡改：把第3条记录的 content_hash 改掉（伪造执行结果）
        conn.execute(
            "UPDATE hash_chain SET content_hash = ? WHERE project_code='PRJ001' AND event_type='EXECUTION_COMPLETED'",
            ("f" * 64,))
        conn.commit()

    r = HashChain.verify("PRJ001", db_path=TMP_DB)
    assert r["valid"] is False, "篡改未被检测到！"
    assert r["break_index"] == 2, f"断裂位置应为2（第3条），实际{r['break_index']}"


def t_tamper_chain_hash():
    """篡改检测：伪造者同时改 chain_hash 试图掩盖 → 下一条断裂"""
    if TMP_DB.exists():
        TMP_DB.unlink()
    HashChain.record("PRJ002", "r1", "RUN_CREATED", "内容A", db_path=TMP_DB)
    HashChain.record("PRJ002", "r1", "DAG_APPROVED", "内容B", db_path=TMP_DB)
    HashChain.record("PRJ002", "r1", "REPORT_FINALIZED", "内容C", db_path=TMP_DB)

    # 伪造者篡改第2条的 content_hash 并重算它自己的 chain_hash（高级篡改）
    chain = HashChain.get_chain("PRJ002", db_path=TMP_DB)
    fake_content_hash = HashChain.compute_content_hash("被篡改的内容")
    fake_chain_hash = HashChain.compute_chain_hash(
        chain[0].chain_hash, chain[1].event_type, chain[1].timestamp, fake_content_hash)
    with sqlite3.connect(str(TMP_DB)) as conn:
        conn.execute(
            "UPDATE hash_chain SET content_hash=?, chain_hash=? WHERE id=?",
            (fake_content_hash, fake_chain_hash, chain[1].id))
        conn.commit()

    # 第2条自身看似自洽，但第3条的 prev 对不上 → 断裂在第3条
    r = HashChain.verify("PRJ002", db_path=TMP_DB)
    assert r["valid"] is False, "高级篡改未被检测到！"
    assert r["break_index"] == 2, f"断裂位置应为2，实际{r['break_index']}"


def t_multi_project_isolation():
    """多项目隔离：不同项目各自成链"""
    if TMP_DB.exists():
        TMP_DB.unlink()
    HashChain.record("PRJ_A", "ra", "RUN_CREATED", "A项目", db_path=TMP_DB)
    HashChain.record("PRJ_B", "rb", "RUN_CREATED", "B项目", db_path=TMP_DB)
    HashChain.record("PRJ_A", "ra", "REPORT_FINALIZED", "A定稿", db_path=TMP_DB)

    a = HashChain.get_chain("PRJ_A", db_path=TMP_DB)
    b = HashChain.get_chain("PRJ_B", db_path=TMP_DB)
    assert len(a) == 2 and len(b) == 1
    assert a[0].prev_chain_hash == "GENESIS"
    assert b[0].prev_chain_hash == "GENESIS"  # B 有自己的创世
    assert a[1].prev_chain_hash == a[0].chain_hash  # A 的链没被 B 打断
    assert HashChain.verify("PRJ_A", db_path=TMP_DB)["valid"]
    assert HashChain.verify("PRJ_B", db_path=TMP_DB)["valid"]


def t_empty_chain():
    """空链验证不报错"""
    r = HashChain.verify("不存在的项目", db_path=TMP_DB)
    assert r["valid"] is True and r["total"] == 0


if __name__ == "__main__":
    print("=" * 60)
    print("SHA-256 哈希链测试（白皮书 §6.4 不可篡改存储）")
    print("=" * 60)
    for t in [t_genesis_and_chain, t_verify_valid, t_tamper_detection,
              t_tamper_chain_hash, t_multi_project_isolation, t_empty_chain]:
        check(t.__name__, t)
    if TMP_DB.exists():
        TMP_DB.unlink()
    n_fail = sum(1 for _, s in RESULTS if s == "FAIL")
    print(f"\n结果: {len(RESULTS) - n_fail} 通过 / {n_fail} 失败")
    sys.exit(1 if n_fail else 0)
