#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Run Snapshot 管理器 (run_snapshot.py)
=======================================
"全无状态生命周期与语义编译版"白皮书 §3.3 + §5 的核心实现

设计理念：
- 每次执行创建唯一 Run_ID（RUN_项目编号_科目_时间戳_v序号）
- 每个 Run 拥有独立的物理文件夹，版本树（v1/v2/v3）平行展现
- 时序完全解耦：Run_v1 和 Run_v2 物理隔离，互不污染
- 逻辑继承：通过结构化摘要（非原始明细）注入 Dify Prompt 上下文
- 成果物原子提交：所有文件落盘后才允许容器销毁

与前后阶段衔接：
- 第一阶段 (W1-2): SSH 隧道已打通，本地存储就绪
- 第二阶段 (W3-4): 本文件 —— Run Snapshot 管理 + 元数据库
- 第三阶段 (W5-6): 元数据库记录脱敏状态
- 第四阶段 (W7+): 成果物持久化、下载、归档

依赖：
    pip install aiosqlite

作者：智能审计平台开发团队
版本：3.0.0（语义编译版）
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

# ═══════════════════════════════════════════════════════════════
# 常量（支持环境变量覆盖，兼容 Windows/Linux）
# ═══════════════════════════════════════════════════════════════
# 获取 run_snapshot.py 所在目录的父目录（项目根目录）
_BASE_DIR = Path(__file__).parent.parent
_DATA_ROOT = Path(os.environ.get("AUDIT_DATA_ROOT", str(_BASE_DIR / "data")))
RUNS_BASE = Path(os.environ.get("AUDIT_RUNS_DIR", str(_DATA_ROOT / "runs")))
DOWNLOADS_BASE = Path(os.environ.get("AUDIT_DOWNLOADS_DIR", str(_DATA_ROOT / "downloads")))
TEMP_BASE = Path(os.environ.get("AUDIT_TEMP_DIR", str(_DATA_ROOT / "temp")))
DB_PATH = Path(os.environ.get("AUDIT_DB_PATH", str(_DATA_ROOT / "meta" / "audit_meta.db")))

# Run_ID 格式：RUN_{项目编号}_{科目}_{时间戳}_v{序号}
RUN_ID_PREFIX = "RUN"

# ═══════════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════════

@dataclass
class RunRecord:
    """Run 元数据记录"""
    run_id: str                          # 唯一标识，如 RUN_A2025001_医保_20250115_103000_v1
    project_code: str                    # 项目编号
    subject: str                         # 审计科目/主题
    version: int                         # 版本序号（v1, v2, v3...）
    parent_run_id: Optional[str]         # 父 Run ID（用于版本链）
    status: Literal["QUEUED", "RUNNING", "COMPLETED", "FAILED", "PENDING_REVIEW", "PENDING_KEYWORD_CONFIRM"] = "QUEUED"
    
    # 输入信息
    user_intent: str = ""               # 审计师的大白话意图
    preset_button: Optional[str] = None  # 预设按钮名称（用于执行阶段快车道判断）
    input_files_hash: str = ""          # 输入文件全局哈希
    input_catalog: Dict = field(default_factory=dict)  # Data Catalog JSON
    
    # 输出信息
    dag_blueprint: Optional[Dict] = None    # DAG JSON 蓝图
    output_files: List[str] = field(default_factory=list)  # 成果物路径列表
    
    # 执行信息
    container_id: Optional[str] = None   # Docker 容器 ID
    sandbox_code: Optional[str] = None   # OpenClaw 编译的 Python 代码
    execution_logs: List[str] = field(default_factory=list)  # 执行日志
    retry_count: int = 0                 # 自纠错重试次数
    
    # 时间戳
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    
    # 成果物验证
    validation_results: List[Dict] = field(default_factory=list)
    all_validations_passed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def run_dir(self) -> Path:
        """该 Run 的物理存储目录"""
        return RUNS_BASE / self.run_id

    @property
    def output_dir(self) -> Path:
        """成果物输出目录"""
        return self.run_dir / "outputs"

    @property
    def input_dir(self) -> Path:
        """输入文件目录（只读）"""
        return self.run_dir / "inputs"


@dataclass
class AssetCatalog:
    """Data Catalog：文件资产目录"""
    global_hash: str                     # 全局唯一哈希
    files: List[Dict] = field(default_factory=list)  # 文件列表（含 schema）
    total_files: int = 0
    total_size_mb: float = 0.0


@dataclass
class HashChainEntry:
    """哈希链中的一条记录（不可篡改）"""
    id: int = -1
    project_code: str = ""
    event_type: str = ""               # RUN_CREATED | DAG_APPROVED | EXECUTION_COMPLETED | MANUAL_CORRECTION | REPORT_FINALIZED
    content_hash: str = ""             # SHA256(事件内容原文)
    chain_hash: str = ""               # SHA256(上一链哈希 || event_type || timestamp || content_hash)
    prev_chain_hash: str = ""          # 前一条记录的 chain_hash，首条为 "GENESIS"
    timestamp: str = ""
    run_id: str = ""
    operator: str = ""                 # 操作人

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class HashChain:
    """
    审计操作哈希链（白皮书 §6.4 不可篡改存储）

    链式结构：chain_hash = SHA256(prev_chain_hash || event_type || timestamp || content_hash)
    篡改任意一条记录会导致后续所有链哈希断裂，可被 verify() 检测。

    生命周期事件：
        RUN_CREATED         → 审计师提交任务
        DAG_APPROVED        → DAG 蓝图审批通过（含审批决策原文哈希）
        EXECUTION_COMPLETED → 沙箱执行完成，成果物落盘
        MANUAL_CORRECTION   → 审计师手动修正（补录/调参/纠错确认）
        REPORT_FINALIZED    → 最终报告生成
    """

    @staticmethod
    def compute_content_hash(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def compute_chain_hash(prev_hash: str, event_type: str, timestamp: str, content_hash: str) -> str:
        """链哈希：SHA256(prev || type || ts || content_hash)"""
        raw = f"{prev_hash}||{event_type}||{timestamp}||{content_hash}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _ensure_table(conn) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hash_chain (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_code TEXT NOT NULL,
                event_type TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                chain_hash TEXT NOT NULL UNIQUE,
                prev_chain_hash TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                run_id TEXT,
                operator TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_hash_chain_project ON hash_chain(project_code)")

    @staticmethod
    def record(project_code: str, run_id: str, event_type: str,
               content: str, operator: str = "",
               db_path: Optional[Path] = None) -> HashChainEntry:
        """写入一条哈希链记录并返回"""
        db = db_path or DB_PATH
        ts = datetime.utcnow().isoformat() + "Z"
        content_hash = HashChain.compute_content_hash(content)

        with sqlite3.connect(str(db)) as conn:
            HashChain._ensure_table(conn)
            row = conn.execute(
                """SELECT chain_hash FROM hash_chain WHERE project_code = ?
                   ORDER BY id DESC LIMIT 1""", (project_code,)
            ).fetchone()
            prev = row[0] if row else "GENESIS"
            chain_hash = HashChain.compute_chain_hash(prev, event_type, ts, content_hash)

            cur = conn.execute("""
                INSERT INTO hash_chain (project_code, event_type, content_hash,
                    chain_hash, prev_chain_hash, timestamp, run_id, operator)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (project_code, event_type, content_hash,
                  chain_hash, prev, ts, run_id, operator))
            conn.commit()
            entry_id = cur.lastrowid

        return HashChainEntry(
            id=entry_id, project_code=project_code, event_type=event_type,
            content_hash=content_hash, chain_hash=chain_hash,
            prev_chain_hash=prev, timestamp=ts, run_id=run_id, operator=operator,
        )

    @staticmethod
    def verify(project_code: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
        """验证哈希链完整性。返回 {"valid": bool, "total": int, "break_index": int|None, "chain": [...]}"""
        db = db_path or DB_PATH
        chain = HashChain.get_chain(project_code, db)
        if not chain:
            return {"valid": True, "total": 0, "break_index": None, "chain": []}
        for i in range(1, len(chain)):
            expected = HashChain.compute_chain_hash(
                chain[i - 1].chain_hash, chain[i].event_type,
                chain[i].timestamp, chain[i].content_hash)
            if chain[i].chain_hash != expected:
                return {"valid": False, "total": len(chain), "break_index": i, "chain": chain}
        return {"valid": True, "total": len(chain), "break_index": None, "chain": chain}

    @staticmethod
    def get_chain(project_code: str, db_path: Optional[Path] = None) -> List[HashChainEntry]:
        """获取项目的完整哈希链"""
        db = db_path or DB_PATH
        with sqlite3.connect(str(db)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM hash_chain WHERE project_code = ? ORDER BY id", (project_code,)
            ).fetchall() if HashChain._table_exists(conn) else []
        return [HashChainEntry(
            id=r["id"], project_code=r["project_code"],
            event_type=r["event_type"], content_hash=r["content_hash"],
            chain_hash=r["chain_hash"], prev_chain_hash=r["prev_chain_hash"],
            timestamp=r["timestamp"], run_id=r["run_id"] or "",
            operator=r["operator"] or "",
        ) for r in rows]

    @staticmethod
    def _table_exists(conn) -> bool:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='hash_chain'"
        ).fetchone() is not None


# ═══════════════════════════════════════════════════════════════
# Run Snapshot 管理器
# ═══════════════════════════════════════════════════════════════

class RunSnapshotManager:
    """
    Run Snapshot 管理器
    
    核心职责：
    1. Run_ID 生成与分配
    2. 物理目录创建与管理
    3. SQLite 元数据库 CRUD
    4. 版本树查询与对比
    5. 成果物原子提交
    6. 生命周期清理
    """

    def __init__(self):
        self._ensure_dirs()
        self._ensure_db()


    def _ensure_dirs(self) -> None:
        """确保基础目录存在"""

        for d in [RUNS_BASE, DOWNLOADS_BASE, TEMP_BASE, DB_PATH.parent]:
            print(f">>> 创建目录: {d}")  # ← 加这一行
            d.mkdir(parents=True, exist_ok=True)


    def _ensure_db(self) -> None:
        """初始化 SQLite 元数据库（WAL 模式 + 性能优化）"""
        with sqlite3.connect(str(DB_PATH)) as conn:
            # ── 性能 PRAGMA（SQLite 官网推荐） ──
            conn.execute("PRAGMA journal_mode=WAL")        # 写前日志 → 读写并发
            conn.execute("PRAGMA synchronous=NORMAL")       # 放宽同步（WAL 下安全）
            conn.execute("PRAGMA cache_size=-64000")        # 64MB 缓存（负数为 KB）
            conn.execute("PRAGMA busy_timeout=5000")        # 5s 忙等待
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA temp_store=MEMORY")        # 临时表放内存
            conn.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    project_code TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    parent_run_id TEXT,
                    status TEXT DEFAULT 'QUEUED',
                    user_intent TEXT,
                    preset_button TEXT,
                    input_files_hash TEXT,
                    input_catalog TEXT,
                    dag_blueprint TEXT,
                    output_files TEXT,
                    container_id TEXT,
                    sandbox_code TEXT,
                    execution_logs TEXT,
                    retry_count INTEGER DEFAULT 0,
                    created_at TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    validation_results TEXT,
                    all_validations_passed INTEGER DEFAULT 0
                )
            """)
            # 兼容旧库：preset_button 列不存在时自动追加
            try:
                conn.execute("ALTER TABLE runs ADD COLUMN preset_button TEXT")
            except Exception:
                pass  # 列已存在，忽略
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_runs_project ON runs(project_code)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_runs_parent ON runs(parent_run_id)
            """)
            conn.commit()

    # ── Run_ID 生成 ──────────────────────────────────────────

    def create_run(
        self,
        project_code: str,
        subject: str,
        user_intent: str,
        input_catalog: AssetCatalog,
        parent_run_id: Optional[str] = None,
        preset_button: Optional[str] = None,
    ) -> RunRecord:
        """
        创建新的 Run
        
        流程：
        1. 生成唯一 Run_ID
        2. 创建物理目录
        3. 写入元数据库
        4. 锁定输入目录为只读
        """
        # 1. 确定版本号
        version = self._get_next_version(project_code, subject)
        
        # 2. 生成 Run_ID
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_id = f"{RUN_ID_PREFIX}_{project_code}_{subject}_{timestamp}_v{version}"
        # 3. 创建 RunRecord
        record = RunRecord(
            run_id=run_id,
            project_code=project_code,
            subject=subject,
            version=version,
            parent_run_id=parent_run_id,
            user_intent=user_intent,
            preset_button=preset_button,
            input_files_hash=input_catalog.global_hash,
            input_catalog=input_catalog.to_dict() if hasattr(input_catalog, 'to_dict') else {},
            status="QUEUED",
        )

        # 4. 创建物理目录
        record.run_dir.mkdir(parents=True, exist_ok=True)
        record.output_dir.mkdir(parents=True, exist_ok=True)
        record.input_dir.mkdir(parents=True, exist_ok=True)

        # 5. 写入元数据库
        self._db_insert(record)

        # 6. 写入 Run 元数据 JSON（冗余备份）
        meta_path = record.run_dir / "run_meta.json"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(record.to_dict(), f, ensure_ascii=False, indent=2, default=str)
        
        return record

    def _get_next_version(self, project_code: str, subject: str) -> int:
        """获取下一个版本号"""
        with sqlite3.connect(str(DB_PATH)) as conn:
            cursor = conn.execute(
                "SELECT MAX(version) FROM runs WHERE project_code = ? AND subject = ?",
                (project_code, subject)
            )
            max_version = cursor.fetchone()[0]
            return (max_version or 0) + 1

    # ── 数据库操作 ───────────────────────────────────────────

    def _db_insert(self, record: RunRecord) -> None:
        """插入 Run 记录"""
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.execute("""
                INSERT INTO runs VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
            """, (
                record.run_id, record.project_code, record.subject,
                record.version, record.parent_run_id, record.status,
                record.user_intent, record.preset_button, record.input_files_hash,
                json.dumps(record.input_catalog, ensure_ascii=False),
                json.dumps(record.dag_blueprint, ensure_ascii=False) if record.dag_blueprint else None,
                json.dumps(record.output_files, ensure_ascii=False),
                record.container_id, record.sandbox_code,
                json.dumps(record.execution_logs, ensure_ascii=False),
                record.retry_count,
                record.created_at, record.started_at, record.completed_at,
                json.dumps(record.validation_results, ensure_ascii=False),
                int(record.all_validations_passed),
            ))
            conn.commit()

    def update_status(self, run_id: str, status: str) -> None:
        """更新 Run 状态"""
        started_at = datetime.utcnow().isoformat() if status == "RUNNING" else None
        completed_at = datetime.utcnow().isoformat() if status in ("COMPLETED", "FAILED") else None
        
        with sqlite3.connect(str(DB_PATH)) as conn:
            if started_at:
                conn.execute(
                    "UPDATE runs SET status = ?, started_at = ? WHERE run_id = ?",
                    (status, started_at, run_id)
                )
            elif completed_at:
                conn.execute(
                    "UPDATE runs SET status = ?, completed_at = ? WHERE run_id = ?",
                    (status, completed_at, run_id)
                )
            else:
                conn.execute(
                    "UPDATE runs SET status = ? WHERE run_id = ?",
                    (status, run_id)
                )
            conn.commit()

    def update_blueprint(self, run_id: str, dag_blueprint: Dict) -> None:
        """更新 DAG 蓝图"""
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.execute(
                "UPDATE runs SET dag_blueprint = ? WHERE run_id = ?",
                (json.dumps(dag_blueprint, ensure_ascii=False), run_id)
            )
            conn.commit()

    def update_sandbox_code(self, run_id: str, code: str) -> None:
        """更新沙箱执行代码"""
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.execute(
                "UPDATE runs SET sandbox_code = ? WHERE run_id = ?",
                (code, run_id)
            )
            conn.commit()

    def update_outputs(
        self,
        run_id: str,
        output_files: List[str],
        validation_results: List[Dict],
        all_passed: bool,
    ) -> None:
        """
        原子提交成果物
        
        原则：所有文件完整写入磁盘 + 索引记录成功后，才更新状态
        """
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.execute("""
                UPDATE runs SET 
                    output_files = ?,
                    validation_results = ?,
                    all_validations_passed = ?,
                    status = ?
                WHERE run_id = ?
            """, (
                json.dumps(output_files, ensure_ascii=False),
                json.dumps(validation_results, ensure_ascii=False),
                int(all_passed),
                "COMPLETED" if all_passed else "PENDING_REVIEW",
                run_id,
            ))
            conn.commit()

    def get_run(self, run_id: str) -> Optional[RunRecord]:
        """查询单个 Run"""
        from pathlib import Path
        print(f"DEBUG: 正在尝试连接数据库文件: {Path(DB_PATH).absolute()}")

        with sqlite3.connect(str(DB_PATH)) as conn:
            # 2. 统计当前数据库总共有多少行，确认是不是空的
            count = conn.execute("SELECT count(*) FROM runs").fetchone()[0]
            print(f"DEBUG: 当前数据库中总共有 {count} 条记录")

            # 3. 检查有没有该 run_id
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()

            if row:
                print(
                    f"DEBUG: 找到记录，dag_blueprint 长度: {len(row['dag_blueprint']) if row['dag_blueprint'] else 'IS NULL'}")
                return self._row_to_record(row)
            else:
                print(f"DEBUG: 数据库中不存在 run_id: {run_id}")
                return None

    def get_version_tree(self, project_code: str, subject: str) -> List[RunRecord]:
        """获取版本树（同一项目同一科目的所有 Run）"""
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM runs WHERE project_code = ? AND subject = ? ORDER BY version",
                (project_code, subject)
            ).fetchall()
            return [self._row_to_record(r) for r in rows]

    def get_recent_runs(self, limit: int = 20) -> List[RunRecord]:
        """获取最近的 Run 列表"""
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
            return [self._row_to_record(r) for r in rows]

    def _row_to_record(self, row: sqlite3.Row) -> RunRecord:
        """数据库行转 RunRecord"""
        return RunRecord(
            run_id=row["run_id"],
            project_code=row["project_code"],
            subject=row["subject"],
            version=row["version"],
            parent_run_id=row["parent_run_id"],
            status=row["status"],
            user_intent=row["user_intent"] or "",
            preset_button=row["preset_button"],
            input_files_hash=row["input_files_hash"] or "",
            input_catalog=json.loads(row["input_catalog"]) if row["input_catalog"] else {},
            dag_blueprint=json.loads(row["dag_blueprint"]) if row["dag_blueprint"] else None,
            output_files=json.loads(row["output_files"]) if row["output_files"] else [],
            container_id=row["container_id"],
            sandbox_code=row["sandbox_code"],
            execution_logs=json.loads(row["execution_logs"]) if row["execution_logs"] else [],
            retry_count=row["retry_count"],
            created_at=row["created_at"] or "",
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            validation_results=json.loads(row["validation_results"]) if row["validation_results"] else [],
            all_validations_passed=bool(row["all_validations_passed"]),
        )

    # ── 只读锁定 ─────────────────────────────────────────────

    @staticmethod
    def lock_readonly(path: Path) -> None:
        """
        将目录设置为只读（递归）
        
        技术：chmod -R 555（所有者/组/其他只能读+执行，不能写）
        """
        if not path.exists():
            return
        for root, dirs, files in os.walk(str(path)):
            for d in dirs:
                dpath = Path(root) / d
                os.chmod(str(dpath), 0o555)
            for f in files:
                fpath = Path(root) / f
                os.chmod(str(fpath), 0o444)
        os.chmod(str(path), 0o555)

    # ── 生命周期清理 ─────────────────────────────────────────

    def cleanup_temp(self, run_id: str) -> None:
        """清理临时文件（成果物持久化后调用）"""
        temp_dir = TEMP_BASE / run_id
        if temp_dir.exists():
            shutil.rmtree(str(temp_dir), ignore_errors=True)

    def archive_old_runs(self, days: int = 90) -> List[str]:
        """
        归档超期 Run
        
        策略：将超过 N 天的 Run 压缩为 .tar.gz 移至冷存储
        """
        archived = []
        cutoff = time.time() - days * 86400
        
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT run_id FROM runs WHERE created_at < datetime(?, 'unixepoch') AND status = 'COMPLETED'",
                (cutoff,)
            ).fetchall()
            
            for row in rows:
                run_id = row["run_id"]
                run_dir = RUNS_BASE / run_id
                if run_dir.exists():
                    archive_path = RUNS_BASE / "archived" / f"{run_id}.tar.gz"
                    archive_path.parent.mkdir(parents=True, exist_ok=True)
                    
                    # 打包
                    import tarfile
                    with tarfile.open(str(archive_path), "w:gz") as tar:
                        tar.add(str(run_dir), arcname=run_id)
                    
                    # 删除原目录
                    shutil.rmtree(str(run_dir), ignore_errors=True)
                    archived.append(run_id)
        
        return archived

    # ── 结构化摘要提取（用于逻辑继承）───────────────────────

    def extract_summary_for_inheritance(self, run_id: str) -> Dict[str, str]:
        """
        从父 Run 提取结构化摘要，注入新 Run 的 Dify Prompt 上下文
        
        提取内容：
        - 审计意图
        - 关键发现（差异汇总）
        - 已确认的假设
        - 使用的科目和规则
        
        注意：只提取摘要文本，不包含原始明细数据
        """
        record = self.get_run(run_id)
        if not record:
            return {}
        
        summary = {
            "previous_intent": record.user_intent,
            "previous_subject": record.subject,
            "previous_status": record.status,
            "previous_validations": json.dumps(record.validation_results, ensure_ascii=False),
        }
        
        # 提取 DAG 蓝图中的关键信息
        if record.dag_blueprint:
            dag = record.dag_blueprint
            operators = dag.get("operators", [])
            summary["previous_operators"] = ", ".join(
                op.get("name", "") for op in operators
            )
            summary["previous_objective"] = dag.get("objective", "")
        
        return summary
