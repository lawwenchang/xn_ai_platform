#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""等待 Docker 引擎就绪后自动执行沙箱端到端验证 + vLLM 隧道复测。
用法：python tests/wait_and_verify.py   （输出：data/online_verify_report.txt）
"""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "online_verify_report.txt"
LINES = []

# vLLM 隧道地址（环境变量 VLLM_API_BASE 可切换端口）
VLLM_BASE = os.environ.get("VLLM_API_BASE", "http://localhost:18000/v1")
_VLLM_PORT = urlparse(VLLM_BASE).port or 18000


def log(msg):
    print(msg, flush=True)
    LINES.append(str(msg))
    OUT.write_text("\n".join(LINES), encoding="utf-8")


def wait_docker(max_wait=180) -> bool:
    log(f"[1/3] 等待 Docker 引擎就绪（最多 {max_wait}s）...")
    t0 = time.time()
    while time.time() - t0 < max_wait:
        try:
            import docker
            docker.from_env().ping()
            log(f"      Docker 引擎就绪，耗时 {time.time() - t0:.0f}s")
            return True
        except Exception:
            time.sleep(5)
    log("      [FAIL] Docker 引擎超时未就绪")
    return False


def check_vllm() -> bool:
    log(f"[2/3] 复测 vLLM 隧道 localhost:{_VLLM_PORT} ...")
    try:
        with socket.create_connection(("127.0.0.1", _VLLM_PORT), timeout=3):
            pass
    except OSError:
        log("      [FAIL] 端口未通（本机无 SSH 隧道监听）")
        return False
    try:
        import httpx
        r = httpx.get(f"{VLLM_BASE}/models",
                      headers={"Authorization": "Bearer EMPTY"}, timeout=15)
        ids = [m["id"] for m in r.json().get("data", [])]
        log(f"      [PASS] vLLM 在线，已加载模型: {ids}")
        r2 = httpx.post(f"{VLLM_BASE}/chat/completions",
                        headers={"Authorization": "Bearer EMPTY"},
                        json={"model": ids[0] if ids else "qwen3-235b",
                              "messages": [{"role": "user", "content": "只回复两个字：在线"}],
                              "max_tokens": 10, "temperature": 0}, timeout=90)
        log(f"      [PASS] 真实推理返回: {r2.json()['choices'][0]['message']['content']!r}")
        return True
    except Exception as e:
        log(f"      [FAIL] API 异常: {type(e).__name__}: {e}")
        return False


def run_sandbox() -> bool:
    log("[3/3] Docker 沙箱端到端验证（构建镜像→挂载只读→执行→销毁）...")
    p = subprocess.run([sys.executable, str(ROOT / "tests" / "verify_docker_sandbox.py")],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=900, cwd=str(ROOT))
    tail = (p.stdout or "") + (p.stderr or "")
    for ln in tail.strip().splitlines()[-12:]:
        log("      " + ln)
    ok = p.returncode == 0 and "验证通过" in tail
    log(f"      沙箱验证: {'PASS' if ok else 'FAIL'} (exit={p.returncode})")
    return ok


if __name__ == "__main__":
    d = wait_docker()
    v = check_vllm()
    s = run_sandbox() if d else False
    log("-" * 50)
    log(f"结论: Docker引擎={'OK' if d else 'X'} | vLLM隧道={'OK' if v else 'X'} | 沙箱生灭={'OK' if s else 'X'}")
