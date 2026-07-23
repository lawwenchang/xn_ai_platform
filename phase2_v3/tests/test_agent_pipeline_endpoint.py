#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多Agent管线端点冒烟测试 (test_agent_pipeline_endpoint.py)
==========================================================
验证 /agent/pipeline：文件上传→四Agent串行→结构化输出（真机 vLLM）。

运行：cd phase2_v3 && python tests/test_agent_pipeline_endpoint.py
"""
import sys, os, json, re, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from fastapi.testclient import TestClient
from api.routes import create_app

client = TestClient(create_app())
FAILED = False

WORKPAPER = """# 骨科医院货币资金审计底稿（节选）

## 银行存款
- 银行存款日记账余额 8,652,000.00 元，银行对账单余额 8,684,000.00 元，
  差异 32,000.00 元，截至审计日未编制余额调节表。

## 应收账款
- 应收医保统筹款期末余额 1,250,000.00 元，其中账龄 1 年以上占比 60%，未计提坏账准备。

## 收入确认
- 一笔 500,000.00 元的设备销售收入于 12 月 31 日确认，发货单日期为次年 1 月 5 日。
"""

def parse_json_lenient(text):
    t = re.sub(r"<think>[\s\S]*?</think>", "", text or "")
    t = t.replace("```json", "").replace("```", "").strip()
    m = re.search(r"\{[\s\S]*\}", t)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None

# ── 1. 快速模式：仅问题抽取（验证文件上传+路径+单阶段） ──
t0 = time.time()
r1 = client.post(
    "/api/v3/agent/pipeline",
    data={"message": "分析这份底稿，找出所有异常问题", "stages": "issue_extractor"},
    files=[("files", ("底稿.md", WORKPAPER.encode("utf-8"), "text/markdown"))],
)
d1 = r1.json()
issue = (d1.get("data") or {}).get("issue_extraction", "")
parsed1 = parse_json_lenient(issue)
n_findings = len((parsed1 or {}).get("findings", []))
print(f"[1.快速模式] HTTP={r1.status_code} success={d1.get('success')} "
      f"耗时={round(time.time()-t0,1)}s 输出={len(issue)}字 findings={n_findings}")
if parsed1 and n_findings:
    for f in parsed1["findings"][:3]:
        print(f"    - [{f.get('severity')}] {f.get('id')} {f.get('title')}")
if r1.status_code != 200 or not d1.get("success") or not issue:
    FAILED = True

# ── 2. 完整管线：四Agent串行（复杂问题自动拆解全链路） ──
t0 = time.time()
r2 = client.post(
    "/api/v3/agent/pipeline",
    data={"message": "分析这份底稿的异常问题，推理根因，匹配审计准则，并生成审计报告", "stages": ""},
    files=[("files", ("底稿.md", WORKPAPER.encode("utf-8"), "text/markdown"))],
)
d2 = r2.json()
data = d2.get("data") or {}
print(f"[2.完整管线] HTTP={r2.status_code} success={d2.get('success')} "
      f"后端耗时={d2.get('elapsed_seconds')}s")
for key, label in [("issue_extraction", "问题抽取"), ("logic_analysis", "逻辑推理"),
                   ("regulation_match", "法规检索"), ("final_report", "报告撰写")]:
    out = (data.get(key) or "").strip()
    status = "OK" if out else "EMPTY"
    print(f"    Stage {label}: {status} ({len(out)}字)")
    if not out:
        FAILED = True
if r2.status_code != 200 or not d2.get("success"):
    FAILED = True

report = (data.get("final_report") or "").strip()
if report:
    print("-" * 60)
    print("最终报告预览（前400字）：")
    print(report[:400])
    print("-" * 60)

# ── 3. 非法文件类型拦截 ──
r3 = client.post(
    "/api/v3/agent/pipeline",
    data={"message": "测试", "stages": "issue_extractor"},
    files=[("files", ("bad.exe", b"MZ", "application/octet-stream"))],
)
print(f"[3.安全拦截] HTTP={r3.status_code}（.exe 应返回 400）")
if r3.status_code != 400:
    FAILED = True

print()
print("RESULT:", "FAIL" if FAILED else "PASS")
sys.exit(1 if FAILED else 0)
