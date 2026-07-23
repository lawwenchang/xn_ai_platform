#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
异步下载引擎 (async_download.py) —— 白皮书 §7.2
=================================================
将打包压缩操作从请求线程中剥离，避免阻塞 API 响应。

设计：
- 提交下载请求 → 立即返回 task_id
- 后台线程执行打包 → 写 zip 到磁盘 → 标记完成
- 前端轮询 GET /api/v3/download/status/{task_id}

无外部依赖（不强制 Redis/Celery），用 threading + 字典做任务队列。
生产环境升级 Celery 只需替换 _execute 内部的打包逻辑即可。
"""
from __future__ import annotations

import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

DOWNLOADS_DIR = Path("data/downloads")
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

# 任务存活时间（秒），超时自动清理
TASK_TTL = 3600


@dataclass
class DownloadTask:
    task_id: str
    run_id: str
    status: str = "QUEUED"  # QUEUED → PACKING → COMPLETED / FAILED
    zip_path: str = ""
    file_size: int = 0
    error: str = ""
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None


# 内存任务表（生产环境改为 Redis）
_tasks: Dict[str, DownloadTask] = {}


def submit(run_id: str, output_dir: Path) -> DownloadTask:
    """提交异步打包任务。立即返回 DownloadTask（含 task_id），后台线程打包。"""
    # 清理过期任务
    _gc_expired()

    task_id = f"dl_{uuid.uuid4().hex[:12]}"
    task = DownloadTask(task_id=task_id, run_id=run_id)
    _tasks[task_id] = task

    t = threading.Thread(target=_pack, args=(task, output_dir), daemon=True)
    t.start()
    return task


def get_status(task_id: str) -> Optional[DownloadTask]:
    _gc_expired()
    return _tasks.get(task_id)


def _pack(task: DownloadTask, output_dir: Path) -> None:
    task.status = "PACKING"
    try:
        if not output_dir.exists() or not any(output_dir.iterdir()):
            task.status = "FAILED"
            task.error = "成果物目录为空"
            return

        zip_path = DOWNLOADS_DIR / f"{task.task_id}.zip"
        with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
            for fp in output_dir.iterdir():
                if fp.is_file():
                    zf.write(str(fp), fp.name)

        task.status = "COMPLETED"
        task.zip_path = str(zip_path)
        task.file_size = zip_path.stat().st_size
        task.completed_at = time.time()
    except Exception as e:
        task.status = "FAILED"
        task.error = str(e)


def _gc_expired() -> None:
    """清理超时任务"""
    now = time.time()
    expired = [tid for tid, t in _tasks.items() if now - t.created_at > TASK_TTL]
    for tid in expired:
        # 删 zip 文件
        zp = Path(_tasks[tid].zip_path)
        if zp.exists():
            zp.unlink(missing_ok=True)
        del _tasks[tid]
