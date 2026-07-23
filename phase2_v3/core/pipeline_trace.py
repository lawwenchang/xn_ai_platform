#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全链路可观测性 (pipeline_trace.py) —— 计划 B2
==================================================
记录每个 Run 在"指令→交付"链路各阶段的耗时/状态/重试，
回答"链路哪一环最拖后腿"（Dify 编译多久、自纠错几轮、降级多频繁）。

设计原则：
- 零侵入失败：埋点自身异常绝不影响业务链路（全部吞掉）
- 独立存储：data/pipeline_trace.db（WAL），不与 run_snapshot 抢锁
- 两种用法：
    with trace(run_id, "dag_compile"):          # 上下文自动计时
        ...
    record(run_id, "fallback", "WARN", 0, "Dify超时降级vLLM")   # 手动打点

标准阶段名（stage）：
    input_flatten / privacy_sanitize / rag_inject / few_shot_inject /
    dag_compile / dag_fallback / dag_validate / approve_wait /
    sandbox_run / self_correct / constraint_check / delivery / download

自测：python -m core.pipeline_trace
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "pipeline_trace.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pipeline_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'OK',      -- OK / FAIL / WARN
    duration_ms INTEGER NOT NULL DEFAULT 0,
    detail TEXT DEFAULT '',
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trace_run ON pipeline_events(run_id);
"""


def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH), timeout=5)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(_SCHEMA)
    return c


def record(run_id: str, stage: str, status: str = "OK",
           duration_ms: int = 0, detail: str = "") -> None:
    """手动打点。任何异常静默吞掉，绝不影响业务。"""
    try:
        with _conn() as c:
            c.execute(
                "INSERT INTO pipeline_events(run_id,stage,status,duration_ms,detail,ts) "
                "VALUES(?,?,?,?,?,?)",
                (run_id, stage, status, int(duration_ms), str(detail)[:500], time.time()))
    except Exception:
        pass


@contextmanager
def trace(run_id: str, stage: str, detail: str = ""):
    """上下文计时打点；块内异常照常抛出，但状态记为 FAIL。"""
    t0 = time.time()
    try:
        yield
        record(run_id, stage, "OK", (time.time() - t0) * 1000, detail)
    except Exception as e:
        record(run_id, stage, "FAIL", (time.time() - t0) * 1000,
               f"{detail} | {type(e).__name__}: {e}"[:500])
        raise


def get_trace(run_id: str) -> list:
    try:
        with _conn() as c:
            rows = c.execute(
                "SELECT stage,status,duration_ms,detail,ts FROM pipeline_events "
                "WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
        return [dict(stage=r[0], status=r[1], duration_ms=r[2],
                     detail=r[3], ts=r[4]) for r in rows]
    except Exception:
        return []


def waterfall(run_id: str) -> str:
    """单个 Run 的链路瀑布文本（前端/日志直接展示）"""
    events = get_trace(run_id)
    if not events:
        return f"[trace] {run_id} 无埋点记录"
    total = sum(e["duration_ms"] for e in events) or 1
    lines = [f"Run {run_id} 链路瀑布（总 {total/1000:.1f}s）："]
    for e in events:
        bar = "█" * max(1, int(e["duration_ms"] / total * 30))
        mark = {"OK": " ", "FAIL": "✗", "WARN": "!"}.get(e["status"], "?")
        lines.append(f"  {mark} {e['stage']:<18} {e['duration_ms']:>7}ms {bar}"
                     + (f"  {e['detail'][:60]}" if e["detail"] else ""))
    return "\n".join(lines)


def stats(last_n_runs: int = 50) -> dict:
    """跨 Run 聚合：各阶段平均耗时/失败率/降级次数 —— 优化决策依据"""
    try:
        with _conn() as c:
            runs = [r[0] for r in c.execute(
                "SELECT DISTINCT run_id FROM pipeline_events "
                "ORDER BY id DESC LIMIT ?", (last_n_runs,)).fetchall()]
            if not runs:
                return {"runs": 0, "stages": {}}
            ph = ",".join("?" * len(runs))
            rows = c.execute(
                f"SELECT stage, COUNT(*), AVG(duration_ms), "
                f"SUM(CASE WHEN status='FAIL' THEN 1 ELSE 0 END), "
                f"SUM(CASE WHEN status='WARN' THEN 1 ELSE 0 END) "
                f"FROM pipeline_events WHERE run_id IN ({ph}) GROUP BY stage",
                runs).fetchall()
        return {"runs": len(runs), "stages": {
            r[0]: dict(count=r[1], avg_ms=round(r[2] or 0),
                       fails=r[3], warns=r[4]) for r in rows}}
    except Exception:
        return {"runs": 0, "stages": {}}


if __name__ == "__main__":
    # 自测：模拟一个 Run 的完整链路
    rid = f"RUN_TRACE_SELFTEST_{int(time.time())}"
    with trace(rid, "input_flatten"):
        time.sleep(0.01)
    with trace(rid, "dag_compile", "dify主链路"):
        time.sleep(0.03)
    record(rid, "dag_fallback", "WARN", 5, "Dify超时，降级vLLM直连")
    try:
        with trace(rid, "sandbox_run"):
            time.sleep(0.02)
            raise RuntimeError("模拟容器OOM")
    except RuntimeError:
        pass
    record(rid, "self_correct", "OK", 1200, "规则修复:第2轮成功")

    events = get_trace(rid)
    assert len(events) == 5, f"应有5条事件，实际{len(events)}"
    assert events[2]["status"] == "WARN" and events[3]["status"] == "FAIL"
    print(waterfall(rid))
    s = stats(10)
    assert s["runs"] >= 1 and "dag_compile" in s["stages"]
    print(f"\n聚合统计（近{s['runs']}个Run）: "
          f"{json.dumps(s['stages'], ensure_ascii=False)}")
    print("\npipeline_trace 自测全部通过 ✅")
