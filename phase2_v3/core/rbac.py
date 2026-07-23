#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RBAC 项目隔离 (rbac.py) —— 白皮书 §5.1
==========================================
角色（白皮书定义）：
    partner   项目负责人 —— 全权限
    auditor   审计师     —— 读/写/执行
    intern    实习生     —— 读/执行（不能改数据、不能删、不能审批）
    reviewer  质控复核人 —— 读/审批（复核底稿但不做业务操作）
    admin     系统管理员 —— 全权限 + 用户管理

权限动作：
    read / write / execute / delete / approve

项目隔离：
    用户只能访问自己是成员的项目（project_members 表）。
    admin 可访问全部项目。

启用方式：
    环境变量 RBAC_ENFORCE=1 时强制校验；
    未设置时为宽松模式（放行并记为匿名 admin），保证现有前端零改动可用。
"""
from __future__ import annotations

import os
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set

# 复用 run_snapshot 的元数据库
from core.run_snapshot import DB_PATH

# ═══════════════ 角色权限矩阵（白皮书 §5.1） ═══════════════

ROLE_PERMISSIONS: Dict[str, Set[str]] = {
    "partner":  {"read", "write", "execute", "delete", "approve"},
    "auditor":  {"read", "write", "execute"},
    "intern":   {"read", "execute"},
    "reviewer": {"read", "approve"},
    "admin":    {"read", "write", "execute", "delete", "approve", "manage_users"},
}

VALID_ROLES = set(ROLE_PERMISSIONS.keys())
VALID_ACTIONS = {"read", "write", "execute", "delete", "approve", "manage_users"}


@dataclass
class User:
    user_id: str
    name: str
    role: str
    token: str = ""

    @property
    def permissions(self) -> Set[str]:
        return ROLE_PERMISSIONS.get(self.role, set())


# 宽松模式下的匿名用户（RBAC_ENFORCE 未开启时）
ANONYMOUS_ADMIN = User(user_id="_anonymous", name="匿名（宽松模式）", role="admin")


def rbac_enabled() -> bool:
    return os.environ.get("RBAC_ENFORCE", "0") == "1"


# ═══════════════ 数据库 ═══════════════

def _ensure_tables(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            role TEXT NOT NULL,
            token TEXT NOT NULL UNIQUE,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS project_members (
            project_code TEXT NOT NULL,
            user_id TEXT NOT NULL,
            role_override TEXT,
            PRIMARY KEY (project_code, user_id)
        )
    """)


# ═══════════════ 用户管理 ═══════════════

def create_user(user_id: str, name: str, role: str,
                db_path: Optional[Path] = None) -> User:
    """创建用户，返回带 token 的 User。role 必须是五种角色之一。"""
    if role not in VALID_ROLES:
        raise ValueError(f"非法角色: {role}，可选: {sorted(VALID_ROLES)}")
    token = secrets.token_hex(16)
    db = db_path or DB_PATH
    with sqlite3.connect(str(db)) as conn:
        _ensure_tables(conn)
        conn.execute(
            "INSERT OR REPLACE INTO users (user_id, name, role, token) VALUES (?, ?, ?, ?)",
            (user_id, name, role, token))
        conn.commit()
    return User(user_id=user_id, name=name, role=role, token=token)


def get_user_by_token(token: str, db_path: Optional[Path] = None) -> Optional[User]:
    """按 token 查用户（API 认证入口）"""
    if not token:
        return None
    db = db_path or DB_PATH
    with sqlite3.connect(str(db)) as conn:
        _ensure_tables(conn)
        row = conn.execute(
            "SELECT user_id, name, role, token FROM users WHERE token = ?", (token,)
        ).fetchone()
    return User(*row) if row else None


def check_permission(user: User, project_code: str, action: str,
                     db_path: Optional[Path] = None) -> bool:
    """
    检查用户对某项目的某操作是否有权限。

    逻辑：
    1. admin 全局放行（管理系统级，不绑特定项目）
    2. 否则查 project_members → 用户必须是该项目成员
    3. 如有 role_override，以 override 的角色判定
    4. 否则以用户在 users 表中的默认角色判定
    """
    if user.role == "admin":
        return True
    db = db_path or DB_PATH
    with sqlite3.connect(str(db)) as conn:
        _ensure_tables(conn)
        row = conn.execute(
            "SELECT role_override FROM project_members WHERE project_code = ? AND user_id = ?",
            (project_code, user.user_id)
        ).fetchone()
    if not row:
        return False  # 非该项目成员
    effective_role = row[0] if row[0] else user.role
    return action in ROLE_PERMISSIONS.get(effective_role, set())


def require(project_code: str, action: str):
    """
    FastAPI 依赖工厂：验证用户对某项目的某操作权限。

    用法（加到 routes.py 路由上）：
        from core.rbac import require
        @router.post("/run")
        async def create_run(..., user: User = Depends(require("PRJ001", "write"))):
            ...

    原理：
        1. RBAC_ENFORCE=0 → 宽松模式，放行所有请求，user=匿名admin
        2. RBAC_ENFORCE=1 → 必须带 header X-User-Token，校验角色和项目成员身份
    """
    from fastapi import Depends, Header, HTTPException

    def _get_user(x_user_token: str = Header(default="", alias="X-User-Token")) -> User:
        if not rbac_enabled():
            return ANONYMOUS_ADMIN

        if not x_user_token:
            raise HTTPException(status_code=401, detail="缺少 X-User-Token 请求头")
        user = get_user_by_token(x_user_token)
        if not user:
            raise HTTPException(status_code=401, detail="无效的 token")

        if not check_permission(user, project_code, action):
            raise HTTPException(status_code=403,
                detail=f"用户 {user.name}({user.role}) 没有 {action} 权限（项目 {project_code}）")
        return user

    return _get_user


def get_project_members(project_code: str, db_path: Optional[Path] = None) -> List[Dict]:
    """列出项目所有成员"""
    db = db_path or DB_PATH
    with sqlite3.connect(str(db)) as conn:
        _ensure_tables(conn)
        rows = conn.execute(
            """SELECT pm.project_code, pm.user_id, u.name, u.role,
                      COALESCE(pm.role_override, u.role) AS effective_role
               FROM project_members pm JOIN users u ON pm.user_id = u.user_id
               WHERE pm.project_code = ?""", (project_code,)
        ).fetchall()
    return [{"project_code": r[0], "user_id": r[1], "name": r[2],
             "default_role": r[3], "effective_role": r[4]} for r in rows]


def user_for_project(project_code: str, user_id: str,
                     db_path: Optional[Path] = None) -> Optional[User]:
    """查用户在某项目的有效角色"""
    db = db_path or DB_PATH
    with sqlite3.connect(str(db)) as conn:
        _ensure_tables(conn)
        row = conn.execute(
            "SELECT role_override FROM project_members WHERE project_code=? AND user_id=?",
            (project_code, user_id)
        ).fetchone()
        if not row:
            return None
        urec = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not urec:
            return None
    effective_role = row[0] if row[0] else urec[1]
    return User(user_id=urec[0], name=urec[1], role=effective_role)


def assign_to_project(project_code: str, user_id: str,
                      role_override: str = "",
                      db_path: Optional[Path] = None) -> None:
    """把用户加入项目。role_override 可对单项目降级/升级角色。"""
    if role_override and role_override not in VALID_ROLES:
        raise ValueError(f"非法角色: {role_override}")
    db = db_path or DB_PATH
    with sqlite3.connect(str(db)) as conn:
        _ensure_tables(conn)
        conn.execute(
            "INSERT OR REPLACE INTO project_members (project_code, user_id, role_override) VALUES (?, ?, ?)",
            (project_code, user_id, role_override or None))
        conn.commit()


def remove_from_project(project_code: str, user_id: str,
                        db_path: Optional[Path] = None) -> None:
    db = db_path or DB_PATH
    with sqlite3.connect(str(db)) as conn:
        _ensure_tables(conn)
        conn.execute(
            "DELETE FROM project_members WHERE project_code = ? AND user_id = ?",
            (project_code, user_id))
        conn.commit()
