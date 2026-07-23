#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RBAC 测试：5 角色 + 项目隔离 + FastAPI 依赖"""
import os, sys, tempfile, sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP_DB = Path(tempfile.gettempdir()) / "test_rbac.db"

from core.rbac import (create_user, get_user_by_token, check_permission,
    assign_to_project, remove_from_project, get_project_members,
    require, rbac_enabled, ANONYMOUS_ADMIN, VALID_ROLES, User)

RESULTS = []
def check(name, fn):
    try:
        fn()
        RESULTS.append((name, "PASS"))
        print(f"  [PASS] {name}")
    except Exception as e:
        RESULTS.append((name, "FAIL"))
        print(f"  [FAIL] {name}: {e}")


def setup():
    if TMP_DB.exists(): TMP_DB.unlink()
    global U
    U = {}
    for uid, name, role in [("u_partner", "张所", "partner"), ("u_auditor", "李审", "auditor"),
                            ("u_intern", "王实习生", "intern"), ("u_reviewer", "赵质控", "reviewer"),
                            ("u_admin", "管理员", "admin")]:
        U[uid] = create_user(uid, name, role, db_path=TMP_DB)
    assign_to_project("PRJ001", "u_partner", db_path=TMP_DB)
    assign_to_project("PRJ001", "u_auditor", db_path=TMP_DB)
    assign_to_project("PRJ001", "u_intern", db_path=TMP_DB)
    assign_to_project("PRJ001", "u_reviewer", db_path=TMP_DB)
    assign_to_project("PRJ002", "u_partner", db_path=TMP_DB)

setup()


def t_partner_full():
    for a in ["read", "write", "execute", "delete", "approve"]:
        assert check_permission(U["u_partner"], "PRJ001", a, db_path=TMP_DB), a

def t_auditor_no_delete_approve():
    u = U["u_auditor"]
    for a in ["read", "write", "execute"]:
        assert check_permission(u, "PRJ001", a, db_path=TMP_DB), a
    assert not check_permission(u, "PRJ001", "delete", db_path=TMP_DB)
    assert not check_permission(u, "PRJ001", "approve", db_path=TMP_DB)

def t_intern_read_exec_only():
    u = U["u_intern"]
    assert check_permission(u, "PRJ001", "read", db_path=TMP_DB)
    assert check_permission(u, "PRJ001", "execute", db_path=TMP_DB)
    assert not check_permission(u, "PRJ001", "write", db_path=TMP_DB)

def t_reviewer_read_approve_only():
    u = U["u_reviewer"]
    assert check_permission(u, "PRJ001", "read", db_path=TMP_DB)
    assert check_permission(u, "PRJ001", "approve", db_path=TMP_DB)
    assert not check_permission(u, "PRJ001", "execute", db_path=TMP_DB)

def t_admin_global():
    assert check_permission(U["u_admin"], "PRJ001", "delete", db_path=TMP_DB)
    assert check_permission(U["u_admin"], "PRJ002", "approve", db_path=TMP_DB)

def t_project_isolation():
    assert check_permission(U["u_auditor"], "PRJ001", "read", db_path=TMP_DB)
    assert not check_permission(U["u_auditor"], "PRJ002", "read", db_path=TMP_DB)

def t_role_override():
    assign_to_project("PRJ001", "u_reviewer", "auditor", db_path=TMP_DB)
    u = U["u_reviewer"]
    assert check_permission(u, "PRJ001", "write", db_path=TMP_DB)

def t_require_fastapi_off():
    os.environ["RBAC_ENFORCE"] = "0"
    dep = require("xxx", "write")
    user = dep(x_user_token="")
    assert user.role == "admin"

def t_create_user_validates_role():
    try:
        create_user("bad", "test", "hacker", db_path=TMP_DB)
        assert False, "应抛异常"
    except ValueError: pass

def t_anonymous_admin():
    assert ANONYMOUS_ADMIN.role == "admin"

def t_member_list():
    m = get_project_members("PRJ001", db_path=TMP_DB)
    assert len(m) >= 4, len(m)


if __name__ == "__main__":
    print("=" * 60)
    print("RBAC 测试（5 角色 + 项目隔离 + 宽松模式）")
    for t in [t_partner_full, t_auditor_no_delete_approve, t_intern_read_exec_only,
              t_reviewer_read_approve_only, t_admin_global, t_project_isolation,
              t_role_override, t_require_fastapi_off, t_create_user_validates_role,
              t_anonymous_admin, t_member_list]:
        check(t.__name__, t)
    if TMP_DB.exists():
        TMP_DB.unlink()
    nf = sum(1 for _, s in RESULTS if s == "FAIL")
    print(f"\n结果: {len(RESULTS) - nf} 通过 / {nf} 失败")
