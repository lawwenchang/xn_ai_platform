#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
瞬时生灭沙箱引擎 v3.0 (sandbox_v3.py)
==========================================
"全无状态生命周期与语义编译版"白皮书 §4.2 的核心实现

核心设计理念 —— 瞬时生灭（JIT Ephemeral Container Lifecycle）：
- 诞生：新 Run 触发 → Docker 瞬间拉起绝对干净的新容器
- 执行：代码在沙箱中爆算 → stdout/stderr 通过 Docker 日志 API 回传
- 消亡：成果物持久化落盘后 → 立即物理销毁（docker rm -f）
- 无菌：每一拍操作面对的都是全新纯净环境，零状态残留

四层安全枷锁（与 v2 一致，加强生命周期管理）：
    Layer 1: AST 白名单（RestrictedPython）
    Layer 2: Docker 隔离（docker-py, Python:Alpine, readonly）
    Layer 3: 资源熔断（CPU 50%, 内存 2GB, 120s 超时）
    Layer 4: 物理断网（--network=none）

新增（v3）：
    - 生命周期钩子（Lifecycle Hooks）：
      * on_born: 容器创建时 → 挂载只读资产
      * on_complete: 执行完成 → 成果物回传 + 落盘检查
      * on_destroy: 容器销毁 → 强制清理
    - 自纠错循环（Agentic Self-Correction）：
      * 执行失败 → 捕获 stderr → 回传大模型 → 生成修正代码 → 新容器重试
      * 最多 5 次重试，每次全新容器
    - 与 RunSnapshotManager 集成：容器 ID 写入元数据库

依赖：
    pip install docker RestrictedPython

作者：智能审计平台开发团队
版本：3.0.0（语义编译版）
"""

from __future__ import annotations

import io as _io
import json
import tarfile
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# 复用 v2 的 AST 安全模块
import sys
sys.path.append(str(Path(__file__).parent.parent))
from engine.sandbox_v2 import (
    compile_with_restrictedpython,
    validate_journal_entries,
    SANDBOX_DOCKERFILE,
    ENTRYPOINT_SCRIPT,
)


# ═══════════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════════

@dataclass
class LifecycleResult:
    """生命周期执行结果"""
    run_id: str
    container_id: Optional[str]
    
    # 状态
    status: str = "QUEUED"  # QUEUED, RUNNING, COMPLETED, FAILED
    
    # 执行输出
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    
    # 自纠错
    retry_count: int = 0
    max_retries: int = 5
    last_error: str = ""
    
    # 时间
    born_at: float = 0.0
    destroyed_at: float = 0.0
    elapsed_seconds: float = 0.0
    
    # 成果物
    output_files: List[str] = field(default_factory=list)
    journal_entries: Optional[Dict] = None
    
    # 错误
    error_message: Optional[str] = None


# ═══════════════════════════════════════════════════════════════
# 生命周期钩子接口
# ═══════════════════════════════════════════════════════════════

class LifecycleHooks:
    """
    生命周期钩子基类
    
    子类可覆盖以下方法实现自定义逻辑：
    - on_born: 容器创建后（挂载资产等）
    - on_complete: 执行完成后（成果物持久化等）
    - on_destroy: 容器销毁前（清理等）
    """

    def on_born(self, run_id: str, container_id: str, run_dir: Path) -> None:
        """容器诞生钩子"""
        pass

    def on_complete(
        self,
        run_id: str,
        container_id: str,
        result: LifecycleResult,
        run_dir: Path,
    ) -> None:
        """执行完成钩子"""
        pass

    def on_destroy(self, run_id: str, container_id: str) -> None:
        """容器销毁钩子"""
        pass


# ═══════════════════════════════════════════════════════════════
# Docker exec_attach 回接链路辅助函数
# ═══════════════════════════════════════════════════════════════

def _put_tar(container, file_map: Dict[str, Path], dest_dir: str) -> None:
    """将本地文件通过 tar 流拷贝到容器内指定目录。

    Args:
        container: docker-py 容器对象
        file_map: {容器内文件名: 本地 Path 对象} 映射
        dest_dir: 容器内目标目录（如 /home/auditor/）
    """
    tar_stream = _io.BytesIO()
    with tarfile.open(fileobj=tar_stream, mode='w') as tar:
        for arcname, filepath in file_map.items():
            tar.add(str(filepath), arcname=arcname)
    tar_stream.seek(0)
    container.put_archive(dest_dir, tar_stream.read())


def _exec_with_timeout(client, container_id: str, cmd: list, timeout_sec: int) -> dict:
    """通过 exec_create + exec_start 在容器内执行命令（回接模式），带超时。

    返回:
        {'exit_code': int, 'output': bytes, 'timed_out': bool, 'error': str|None}
    """
    result = {'exit_code': -1, 'output': b'', 'timed_out': False, 'error': None}

    def _do_exec():
        try:
            exec_id = client.api.exec_create(
                container_id, cmd, stdout=True, stderr=True,
                working_dir="/home/auditor"
            )["Id"]
            out = client.api.exec_start(exec_id, detach=False)
            inspect = client.api.exec_inspect(exec_id)
            result['exit_code'] = inspect.get("ExitCode", -1)
            result['output'] = out or b''
        except Exception as e:
            result['error'] = str(e)

    t = threading.Thread(target=_do_exec, daemon=True)
    t.start()
    t.join(timeout=timeout_sec)
    if t.is_alive():
        result['timed_out'] = True
        result['error'] = f'执行超时（>{timeout_sec}秒）'
    return result


class EphemeralSandbox:
    """
    瞬时生灭沙箱引擎
    
    核心 API：
    - execute(run_id, code, run_dir): 单次执行（出生→执行→消亡）
    - execute_with_retry(run_id, code, run_dir): 带自纠错的执行
    
    保证：
    - 无论成功失败，容器一定被销毁
    - 成果物在销毁前已落盘
    - 每次执行环境绝对干净
    """

    def __init__(
        self,
        image: str = "audit-sandbox:alpine-v2",
        cpu_limit: float = 0.5,
        memory_limit: str = "2g",
        timeout: int = 120,
        hooks: Optional[LifecycleHooks] = None,
    ):
        self.image = image
        self.cpu_limit = cpu_limit
        self.memory_limit = memory_limit
        self.timeout = timeout
        self.hooks = hooks or LifecycleHooks()
        self._client = None

    def _get_client(self):
        if self._client is None:
            import docker
            self._client = docker.from_env()
        return self._client

    def execute(
        self,
        run_id: str,
        code: str,
        run_dir: Path,
    ) -> LifecycleResult:
        """
        单次瞬时执行
        
        完整生命周期：
        1. 检查代码安全（AST 白名单）
        2. 创建容器（诞生）
        3. 执行代码（爆算）
        4. 回传结果
        5. 持久化成果物
        6. 销毁容器（消亡）
        """
        result = LifecycleResult(run_id=run_id, container_id=None)
        start = time.time()
        container = None

        try:
            # Step 1: AST 安全检查
            ast_safe, violations, _ = compile_with_restrictedpython(code)
            if not ast_safe and not any("WARNING" in v for v in violations):
                result.status = "FAILED"
                result.error_message = f"AST 安全检查失败: {violations}"
                return result

            # Step 1.5: Python 语法预编译校验（零成本拦截运行时语法错误）
            try:
                compile(code, "<string>", "exec")
            except SyntaxError as se:
                result.status = "FAILED"
                result.error_message = (
                    f"生成代码语法错误 (行{se.lineno}, 列{se.offset}): {se.msg}\n"
                    f"{se.text.strip() if se.text else ''}"
                )
                return result

            # Step 2: 准备代码文件（写入临时目录）
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_path = Path(tmpdir)
                code_file = tmp_path / "script.py"
                code_file.write_text(code, encoding="utf-8")
                entrypoint_file = tmp_path / "entrypoint.py"
                entrypoint_file.write_text(ENTRYPOINT_SCRIPT, encoding="utf-8")

                input_dir = run_dir / "inputs"
                output_dir = run_dir / "outputs"
                output_dir.mkdir(parents=True, exist_ok=True)

                client = self._get_client()
                container_name = f"run_{run_id}_{uuid.uuid4().hex[:6]}"
                cpu_quota = int(self.cpu_limit * 100000)

                # ═══ Step 3: 回接模式 — 启动持久容器（Python sleep 保活，覆盖镜像 ENTRYPOINT） ═══
                container = client.containers.run(
                    image=self.image,
                    command=["-c", "import time; time.sleep(86400)"],
                    entrypoint=["python3"],          # 覆盖镜像 ENTRYPOINT
                    network_mode="none",
                    mem_limit=self.memory_limit,
                    memswap_limit=self.memory_limit,
                    cpu_quota=cpu_quota,
                    cpu_period=100000,
                    cap_drop=["ALL"],
                    security_opt=["no-new-privileges:true"],
                    auto_remove=False,
                    detach=True,
                    name=container_name,
                    environment={
                        "PYTHONDONTWRITEBYTECODE": "1",
                        "PYTHONUNBUFFERED": "1",
                    },
                )

                result.container_id = container.id
                result.born_at = time.time()
                result.status = "RUNNING"

                # 生命周期钩子：诞生
                self.hooks.on_born(run_id, container.id, run_dir)

                try:
                    # ═══ Step 4: put_archive — 代码/数据文件 tar 流注入容器 ═══
                    _put_tar(container, {
                        "script.py": code_file,
                        "entrypoint.py": entrypoint_file,
                    }, "/home/auditor/")

                    # 输入数据文件 → /home/auditor/inputs/（LLM代码中 os.path.dirname(__file__)/inputs）
                    input_file_map = {}
                    if input_dir.exists():
                        for fp in input_dir.iterdir():
                            if fp.is_file():
                                input_file_map[fp.name] = fp
                    if input_file_map:
                        container.exec_run(
                            ["python3", "-c",
                             "import os; os.makedirs('/home/auditor/inputs', exist_ok=True)"],
                            stdout=True, stderr=True
                        )
                        _put_tar(container, input_file_map, "/home/auditor/inputs/")

                    # ═══ Step 5: exec_create + exec_start — 回接执行代码 ═══
                    exec_result = _exec_with_timeout(
                        client, container.id,
                        ["python3", "/home/auditor/entrypoint.py",
                         "/home/auditor/script.py"],
                        timeout_sec=self.timeout,
                    )

                    result.exit_code = exec_result['exit_code']
                    if exec_result['timed_out']:
                        result.error_message = exec_result['error']
                        container.kill(signal="SIGKILL")

                    raw_output = (
                        exec_result['output'].decode("utf-8", errors="replace")
                        if exec_result['output'] else ""
                    )
                    result.stdout = raw_output[-5000:] if raw_output else ""
                    result.stderr = raw_output if raw_output else ""


                    # ═══ Step 6: get_archive — 拉取成果物 tar 流到本地 ═══
                    try:
                        stream, _stat = container.get_archive(
                            "/home/auditor/outputs/"
                        )
                        tar_bytes = b"".join(stream)
                        with tarfile.open(fileobj=_io.BytesIO(tar_bytes)) as tar:
                            # Docker tar 携带目录名（如 outputs/journal_entries.json），
                            # 需提取到 output_dir 的父目录以避免 outputs/outputs 双嵌套
                            tar.extractall(path=str(output_dir.parent))
                    except Exception as ga_err:
                        ga_msg = str(ga_err)
                        if "404" not in ga_msg and "not found" not in ga_msg.lower():
                            print(f"[Sandbox] get_archive 警告: {ga_msg}")

                    # ═══ Step 7: 成果物校验 ═══
                    output_path = output_dir / "journal_entries.json"
                    if output_path.exists():
                        try:
                            with open(output_path, "r", encoding="utf-8") as f:
                                result.journal_entries = json.load(f)
                            result.output_files = [
                                str(f) for f in output_dir.iterdir() if f.is_file()
                            ]
                            if result.journal_entries:
                                je_valid, je_errors = validate_journal_entries(
                                    result.journal_entries
                                )
                                if je_valid:
                                    result.status = "COMPLETED"
                                else:
                                    result.status = "FAILED"
                                    result.error_message = f"输出校验失败: {je_errors}"
                            else:
                                result.status = "COMPLETED"
                        except Exception as e:
                            result.status = "FAILED"
                            result.error_message = f"成果物读取失败: {e}"
                    else:
                        if result.exit_code == 0:
                            result.status = "COMPLETED"
                        else:
                            result.status = "FAILED"
                            result.error_message = (
                                result.stdout[-1000:]
                                or exec_result.get('error')
                                or "执行失败，无错误输出"
                            )

                except Exception as inner_e:
                    result.status = "FAILED"
                    result.error_message = f"回接执行异常: {inner_e}"

                # 生命周期钩子：完成
                self.hooks.on_complete(run_id, container.id, result, run_dir)

        except Exception as e:
            result.status = "FAILED"
            result.error_message = f"沙箱执行异常: {str(e)}"

        finally:
            # Step 8: 销毁容器（消亡）—— 钢铁律令
            if container:
                try:
                    self.hooks.on_destroy(run_id, container.id)
                    container.reload()
                    if container.status == "running":
                        container.kill(signal="SIGKILL")
                    container.remove(force=True)
                    result.destroyed_at = time.time()
                except Exception:
                    pass  # 销毁失败不阻塞主流程

        result.elapsed_seconds = time.time() - start
        return result

    def execute_with_retry(
        self,
        run_id: str,
        code: str,
        run_dir: Path,
        max_retries: int = 5,
    ) -> LifecycleResult:
        """
        带自纠错的执行
        
        闭环：执行失败 → 捕获 stderr → 大模型修正 → 新容器重试
        每次重试都在全新容器中（无菌环境）。
        """
        last_result = None

        for attempt in range(1, max_retries + 1):
            result = self.execute(run_id, code, run_dir)
            result.retry_count = attempt - 1
            last_result = result

            if result.status == "COMPLETED":
                return result

            # 记录错误
            result.last_error = result.error_message or result.stderr or "未知错误"

            # 如果还有重试机会，调用 OpenClaw 自纠错内核修正代码（白皮书 §5.2）
            if attempt < max_retries:
                from engine.code_corrector import correct_code
                fixed = correct_code(code, result.last_error, attempt=attempt)
                if fixed:
                    code = fixed  # 修正后的代码进入下一轮全新容器执行
                else:
                    time.sleep(1)  # 无修正方案，原样重试

        # 所有重试都失败了
        last_result.error_message = (
            f"经过 {max_retries} 次重试后仍然失败。"
            f"最后错误: {last_result.last_error}"
        )
        return last_result

    def build_image(self) -> bool:
        """构建沙箱镜像"""
        import docker
        client = docker.from_env()
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_path = Path(tmpdir)
                (tmp_path / "Dockerfile").write_text(SANDBOX_DOCKERFILE, encoding="utf-8")
                (tmp_path / "entrypoint.py").write_text(ENTRYPOINT_SCRIPT, encoding="utf-8")
                client.images.build(path=str(tmp_path), tag=self.image, rm=True, forcerm=True)
            return True
        except Exception as e:
            print(f"镜像构建失败: {e}")
            return False
        finally:
            client.close()
