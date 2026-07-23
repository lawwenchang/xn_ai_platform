#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全栈连通性验证 (verify_stack.py)
=====================================
对照白皮书 V2.7 逐项检查基础设施：
Docker / vLLM隧道(30000) / Dify(5001) / Redis(6379) / 后端(8000) / 前端(3000)

用法：python tests/verify_stack.py
输出：stdout + data/verify_stack_report.txt
"""
import json
import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
LINES = []

# vLLM 隧道地址（环境变量 VLLM_API_BASE 可切换端口，如 http://localhost:30000/v1）
VLLM_BASE = os.environ.get("VLLM_API_BASE", "http://localhost:18000/v1")
_VLLM_PORT = urlparse(VLLM_BASE).port or 18000


def log(msg):
    print(msg)
    LINES.append(msg)


def port_open(host, port, timeout=3):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_docker():
    try:
        import docker
        cli = docker.from_env()
        ver = cli.version().get("Version", "?")
        imgs = [t for i in cli.images.list() for t in (i.tags or [])]
        sandbox_imgs = [t for t in imgs if "audit" in t or "sandbox" in t or "python" in t]
        log(f"[PASS] Docker daemon 在线 (v{ver})")
        log(f"       相关镜像: {sandbox_imgs[:6] or '(无 python/audit 镜像，沙箱首次运行会构建)'}")
        return True
    except Exception as e:
        log(f"[FAIL] Docker: {type(e).__name__}: {e}")
        return False


def check_vllm():
    if not port_open("127.0.0.1", _VLLM_PORT):
        log(f"[FAIL] vLLM 隧道 localhost:{_VLLM_PORT} 端口未通（先建 SSH 隧道）")
        return False
    try:
        import httpx
        r = httpx.get(f"{VLLM_BASE}/models",
                      headers={"Authorization": "Bearer EMPTY"}, timeout=10)
        r.raise_for_status()
        ids = [m["id"] for m in r.json().get("data", [])]
        log(f"[PASS] vLLM 隧道在线，已加载模型: {ids}")
        # 试一次真实推理
        r2 = httpx.post(f"{VLLM_BASE}/chat/completions",
                        headers={"Authorization": "Bearer EMPTY"},
                        json={"model": ids[0] if ids else "qwen3-235b",
                              "messages": [{"role": "user", "content": "回复OK两个字母"}],
                              "max_tokens": 8, "temperature": 0},
                        timeout=60)
        r2.raise_for_status()
        content = r2.json()["choices"][0]["message"]["content"]
        log(f"[PASS] vLLM 推理测试: {content!r}")
        return True
    except Exception as e:
        log(f"[WARN] vLLM 端口通但 API 异常: {type(e).__name__}: {e}")
        return False


def check_port(name, port, required):
    ok = port_open("127.0.0.1", port)
    tag = "PASS" if ok else ("FAIL" if required else "WARN")
    log(f"[{tag}] {name} (localhost:{port}) {'在线' if ok else '未启动'}")
    return ok


def check_backend_api():
    if not port_open("127.0.0.1", 8000):
        return False
    try:
        import httpx
        r = httpx.get("http://localhost:8000/docs", timeout=5)
        log(f"[PASS] FastAPI 后端 /docs -> HTTP {r.status_code}")
        return True
    except Exception as e:
        log(f"[WARN] 后端 8000 端口通但 /docs 异常: {e}")
        return False


def main():
    log("=" * 62)
    log("白皮书 V2.7 基础设施连通性验证")
    log("=" * 62)
    results = {
        "Docker": check_docker(),
        "vLLM隧道": check_vllm(),
        "Dify": check_port("Dify 工作流引擎", 5001, required=False),
        "Redis": check_port("Redis (Celery broker)", 6379, required=False),
        "后端": check_backend_api() or check_port("FastAPI 后端", 8000, required=False),
        "前端": check_port("Vue 前端 dev server", 3000, required=False),
    }
    log("-" * 62)
    log("汇总: " + json.dumps({k: ("在线" if v else "离线") for k, v in results.items()},
                              ensure_ascii=False))
    out = ROOT / "data" / "verify_stack_report.txt"
    out.write_text("\n".join(LINES), encoding="utf-8")
    log(f"报告 -> {out}")


if __name__ == "__main__":
    main()
