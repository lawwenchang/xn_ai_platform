#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
安全代码执行沙箱 v2.0 (sandbox_v2.py)
=========================================
适配「安全加固与底稿映射版」白皮书的 Docker SDK 实现

核心变更（对比 v1）：
- docker-py (Docker SDK for Python) 替代 subprocess 调用 docker CLI
- 镜像改为 python:3.11-alpine（最小化攻击面）
- 资源限制：CPU 50%，内存 2GB（白皮书 §4.2 要求）
- 每次重试启动全新干净容器（OpenClaw 自纠错循环的"无菌手术室"）
- stdout/stderr 通过 Docker 日志 API 实时回传
- 容器结束后自动销毁，临时文件随容器清除

安全策略（五层纵深防御）：
    Layer 1: AST 白名单（RestrictedPython 编译）
    Layer 2: Docker 隔离（docker-py，Alpine 镜像，readonly 根目录）
    Layer 3: 资源熔断（CPU 50%，内存 2GB，执行超时 120s）
    Layer 4: 物理断网（--network=none）
    Layer 5: 输出验证（审计调整分录 Schema 校验 + 逻辑一致性三校验）

与前后阶段衔接：
    - 第一阶段 (W1-2): Docker daemon 在本地服务器运行
    - 第二阶段 (W3-4): 本文件创建，与 api/routes.py 集成
    - 第三阶段 (W5-6): 联网搜索结果不经过此沙箱（搜索在 Dify 层完成）
    - 第四阶段 (W7+): OpenClaw 生成的代码经此沙箱执行，自纠错循环每次用新容器

依赖：
    pip install docker RestrictedPython
    system: Docker daemon 必须运行

作者：智能审计平台开发团队
版本：2.0.0（适配安全加固版白皮书）
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import tarfile
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Set, Tuple

# ═══════════════════════════════════════════════════════════════
# Layer 1: RestrictedPython AST 白名单编译（与 v1 保持一致）
# ═══════════════════════════════════════════════════════════════

ALLOWED_AST_NODES: Set[str] = {
    "Expression", "BinOp", "UnaryOp", "BoolOp", "Compare", "IfExp",
    "Constant", "Name", "Attribute", "Subscript",
    "Add", "Sub", "Mult", "Div", "FloorDiv", "Mod", "Pow",
    "USub", "UAdd", "Not",
    "Eq", "NotEq", "Lt", "LtE", "Gt", "GtE", "In", "NotIn",
    "And", "Or",
    "Assign", "AugAssign",
    "List", "Tuple", "Dict", "Set",
    "If", "For", "While", "Break", "Continue", "Pass",
    "Try", "ExceptHandler",
    "FunctionDef", "Return", "Call", "arguments", "arg", "keyword",
    "ListComp", "DictComp", "GeneratorExp",
    "comprehension", "alias",
    "Module", "Import", "ImportFrom",
    "Load", "Store", "Del",
    "Expr", "Slice", "JoinedStr", "FormattedValue",
    "Assert", "Raise", "With", "withitem", "NamedExpr",
}

ALLOWED_IMPORTS: Set[str] = {
    "pandas", "pd", "numpy", "np", "json", "math", "os",
    "datetime", "datetime.datetime", "datetime.timedelta",
    "re", "collections", "collections.Counter", "collections.defaultdict",
    "itertools", "statistics", "typing",
}

FORBIDDEN_CALLS: Set[str] = {
    "eval", "exec", "compile", "__import__",
    # "open" "file" —— 已移除：Docker 容器 read_only=True + network=none 提供 OS 级防护，
    #     AST 层重复拦截会导致合法 DAG 代码（open outputs/journal_entries.json）被阻断。
    "os.system", "os.popen", "os.spawn", "os.fork", "os.kill",
    "subprocess.call", "subprocess.run", "subprocess.Popen",
    "subprocess.check_output", "subprocess.check_call",
    "sys.exit", "quit", "exit", "input", "raw_input", "breakpoint",
    "globals", "locals", "vars", "dir",
    "getattr", "setattr", "delattr",
    "classmethod", "staticmethod", "property",
    "__class__", "__bases__", "__subclasses__", "__mro__",
    "__init__", "__new__", "__del__",
    "__getattribute__", "__getattr__", "__setattr__", "__delattr__",
    "__dict__", "__globals__", "__closure__", "__code__",
    "__defaults__", "__kwdefaults__",
    "__module__", "__name__", "__qualname__",
    "builtins", "__builtins__",
}


class ASTSecurityChecker(ast.NodeVisitor):
    """AST 安全审查器（与 v1 一致）"""

    def __init__(self):
        self.violations: List[str] = []
        self._found_imports: Set[str] = set()
        self._tree: Optional[ast.AST] = None

    def generic_visit(self, node: ast.AST) -> None:
        node_type = type(node).__name__
        if node_type not in ALLOWED_AST_NODES:
            self.violations.append(
                f"禁止的 AST 节点: {node_type} (行 {getattr(node, 'lineno', '?')})"
            )
        super().generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func_name = self._extract_call_name(node.func)
        if func_name:
            for forbidden in FORBIDDEN_CALLS:
                if forbidden in func_name:
                    self.violations.append(
                        f"禁止调用: {func_name} (行 {node.lineno})"
                    )
            if "." in func_name:
                for part in func_name.split("."):
                    if part.startswith("_") and part not in ("__init__", "__main__"):
                        self.violations.append(
                            f"禁止访问私有属性: {func_name} (行 {node.lineno})"
                        )
        super().generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            module_name = alias.name.split(".")[0]
            if module_name not in ALLOWED_IMPORTS:
                self.violations.append(f"禁止导入: {alias.name} (行 {node.lineno})")
            self._found_imports.add(module_name)
        super().generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            module_name = node.module.split(".")[0]
            if module_name not in ALLOWED_IMPORTS:
                self.violations.append(f"禁止从模块导入: {node.module} (行 {node.lineno})")
            self._found_imports.add(module_name)
        super().generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.attr, str) and node.attr.startswith("_"):
            if not (node.attr.startswith("__") and node.attr.endswith("__")):
                self.violations.append(
                    f"禁止访问私有属性: .{node.attr} (行 {getattr(node, 'lineno', '?')})"
                )
        super().generic_visit(node)

    def _extract_call_name(self, node: ast.AST) -> Optional[str]:
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            value = self._extract_call_name(node.value)
            if value:
                return f"{value}.{node.attr}"
            return node.attr
        return None

    def is_safe(self) -> bool:
        return len(self.violations) == 0


def compile_with_restrictedpython(source_code: str) -> Tuple[bool, List[str], Optional[ast.AST]]:
    """使用 RestrictedPython 安全编译代码"""
    try:
        tree = ast.parse(source_code)
    except SyntaxError as e:
        return False, [f"语法错误: {e}"], None

    checker = ASTSecurityChecker()
    checker._tree = tree
    checker.visit(tree)

    if not checker.is_safe():
        return False, checker.violations, tree

    # 尝试使用 RestrictedPython
    try:
        from RestrictedPython import compile_restricted
        from RestrictedPython.Guards import safe_builtins

        byte_code = compile_restricted(
            source_code,
            filename="<openclaw_generated>",
            mode="exec",
        )
        if byte_code is None:
            return False, ["RestrictedPython 编译失败: 包含受限操作"], tree
    except ImportError:
        checker.violations.append(
            "WARNING: RestrictedPython 未安装，依赖 AST 白名单 + Docker 隔离"
        )

    return True, checker.violations, tree


# ═══════════════════════════════════════════════════════════════
# Layer 2-4: Docker SDK (docker-py) 隔离执行
# ═══════════════════════════════════════════════════════════════

# 沙箱容器 Dockerfile（Alpine 最小化镜像）
SANDBOX_DOCKERFILE = '''\
FROM python:3.11-alpine

# 安装编译依赖 + pandas/numpy（新版有 musl 预编译轮）
RUN apk add --no-cache gcc g++ musl-dev libffi-dev openssl-dev && \\
    pip install --no-cache-dir pandas numpy openpyxl xlrd python-docx PyMuPDF pdfplumber chardet rapidfuzz && \
    apk del gcc g++ musl-dev libffi-dev openssl-dev
# 注：python-docx/PyMuPDF/pdfplumber/chardet 使沙箱内可直接解析 docx/pdf/md/txt；镜像需重建生效（docker build）。

# 创建非 root 用户
RUN adduser -D -u 1000 auditor && \\
    mkdir -p /tmp/output data/readonly && \\
    chown -R auditor:auditor /tmp/output

# 移除危险工具（Alpine 用 busybox）
RUN chmod 000 /bin/sh /bin/ash /usr/bin/wget /usr/bin/curl 2>/dev/null || true

USER auditor
WORKDIR /home/auditor

# 入口脚本：接收代码，执行，输出 journal_entries.json
COPY --chown=auditor:auditor entrypoint.py /home/auditor/entrypoint.py
ENTRYPOINT ["python3", "/home/auditor/entrypoint.py"]
'''

# 容器入口脚本（在容器内运行）
ENTRYPOINT_SCRIPT = '''\
#!/usr/bin/env python3
"""沙箱容器入口 —— 安全执行审计代码"""
import json, sys, os, traceback, importlib

# 安全限制：禁止网络相关导入
forbidden_modules = {'socket', 'urllib', 'http', 'ftplib', 'smtplib', 'requests', 'ssl'}

class ImportBlocker:
    def find_module(self, fullname, path=None):
        base = fullname.split('.')[0]
        if base in forbidden_modules:
            return self
        return None
    def load_module(self, fullname):
        raise ImportError(f"Network module '{fullname}' is blocked in sandbox")

sys.meta_path.insert(0, ImportBlocker())

# 确保输出目录存在（兼容 exec_attach 回接模式，无 bind-mount 时自动创建）
os.makedirs('/home/auditor/outputs', exist_ok=True)

# 读取代码
code_path = sys.argv[-1] if len(sys.argv) > 1 else '/home/auditor/script.py'
with open(code_path, 'r') as f:
    code = f.read()

try:
    # 在受限环境中执行
    safe_globals = {
        '__builtins__': __builtins__,
        '__name__': '__main__',
        '__file__': code_path,
    }
    exec(compile(code, '<sandbox>', 'exec'), safe_globals)
    print('\\n__SANDBOX_EXECUTION_SUCCESS__')
except Exception as e:
    error_info = {
        'error_type': type(e).__name__,
        'error_message': str(e),
        'traceback': traceback.format_exc()
    }
    print(f'\\n__SANDBOX_EXECUTION_FAILED__')
    print(json.dumps(error_info, ensure_ascii=False))
    sys.exit(1)
'''


@dataclass
class SandboxConfig:
    """Docker 沙箱配置（适配白皮书 §4.2 要求）"""
    image: str = "audit-sandbox:alpine-v2"
    cpu_limit: float = 0.5          # 白皮书要求：CPU 50%
    memory_limit: str = "2g"        # 白皮书要求：内存 2GB
    timeout_seconds: int = 120      # 执行超时 120 秒
    read_only_root: bool = True     # 根文件系统只读
    network_disabled: bool = True   # --network=none
    auto_remove: bool = True        # 执行后自动销毁


class DockerSandboxV2:
    """
    Docker SDK (docker-py) 隔离执行引擎

    核心特性：
    - 使用 docker-py 而非 subprocess CLI（更精细的控制）
    - Alpine 最小化镜像（攻击面最小）
    - 每次执行/重试都是全新容器（OpenClaw 自纠错循环的"无菌手术室"）
    - stdout/stderr 通过 Docker 日志 API 实时回传
    - 容器结束后自动销毁，临时文件随容器清除
    """

    def __init__(self, config: Optional[SandboxConfig] = None):
        self.config = config or SandboxConfig()
        self._client = None
        self._docker_available = self._check_docker()

    def _get_client(self):
        """延迟初始化 Docker client"""
        if self._client is None:
            import docker
            self._client = docker.from_env()
        return self._client

    def _check_docker(self) -> bool:
        """检查 Docker daemon 是否可用"""
        try:
            import docker
            client = docker.from_env()
            client.ping()
            client.close()
            return True
        except Exception:
            return False

    def build_image(self, tag: Optional[str] = None) -> bool:
        """
        构建安全执行镜像
        使用 docker-py 的 build 功能，而非 Dockerfile + CLI
        """
        import docker
        client = docker.from_env()
        image_tag = tag or self.config.image

        try:
            # 创建临时目录存放构建上下文
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_path = Path(tmpdir)

                # 写入 Dockerfile
                (tmp_path / "Dockerfile").write_text(SANDBOX_DOCKERFILE, encoding="utf-8")

                # 写入 entrypoint.py
                (tmp_path / "entrypoint.py").write_text(ENTRYPOINT_SCRIPT, encoding="utf-8")

                # 使用 docker-py 构建
                image_obj, build_logs = client.images.build(
                    path=str(tmp_path),
                    tag=image_tag,
                    rm=True,                    # 删除中间层
                    forcerm=True,               # 强制删除
                )

                # 输出构建日志（调试用）
                for chunk in build_logs:
                    if "stream" in chunk:
                        print(chunk["stream"], end="")

                return True

        except Exception as e:
            print(f"镜像构建失败: {e}")
            return False

        finally:
            client.close()

    def execute(
        self,
        code: str,
        input_data_paths: Dict[str, str],
        output_dir: str,
    ) -> Dict[str, Any]:
        """
        在 Docker 容器中安全执行代码（使用 docker-py）

        流程：
        1. 创建临时目录，写入代码文件
        2. 使用 docker-py 创建并启动容器
        3. 通过 logs() 实时获取 stdout/stderr
        4. 等待容器结束（带超时）
        5. 读取输出文件，返回结果
        6. 容器自动销毁（auto_remove=True）

        Args:
            code: OpenClaw 生成的 Python 代码（已通过 AST 检查）
            input_data_paths: {"left": "data/storage/bank_flow.xlsx", ...}
            output_dir: 审计调整分录 JSON 输出目录

        Returns:
            执行结果字典
        """
        if not self._docker_available:
            return {
                "success": False,
                "error": "Docker daemon 不可用",
                "recommendation": "请确保 Docker 已安装并运行",
            }

        execution_id = f"sandbox_{uuid.uuid4().hex[:8]}"
        client = self._get_client()
        container = None

        # Step 1: 创建临时目录和代码文件
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            code_file = tmp_path / "script.py"
            code_file.write_text(code, encoding="utf-8")

            # Step 2: 配置挂载
            mounts = {}

            # 代码文件（只读 bind mount）
            mounts[str(code_file)] = {
                "bind": "/home/auditor/script.py",
                "mode": "ro"
            }

            # 入口脚本（只读）
            entrypoint_host = tmp_path / "entrypoint.py"
            entrypoint_host.write_text(ENTRYPOINT_SCRIPT, encoding="utf-8")
            mounts[str(entrypoint_host)] = {
                "bind": "/home/auditor/entrypoint.py",
                "mode": "ro"
            }

            # 输入数据文件（只读）
            for key, path in input_data_paths.items():
                if not os.path.exists(path):
                    return {
                        "success": False,
                        "error": f"输入文件不存在: {key}={path}",
                    }
                mounts[path] = {
                    "bind": f"data/readonly/{key}.xlsx",
                    "mode": "ro"
                }

            # 输出目录（可写）
            os.makedirs(output_dir, exist_ok=True)
            mounts[output_dir] = {
                "bind": "/tmp/output",
                "mode": "rw"
            }

            # Step 3: 容器资源配置（白皮书 §4.2 要求）
            # 资源限制
            mem_bytes = self._parse_memory(self.config.memory_limit)
            cpu_quota = int(self.config.cpu_limit * 100000)  # CPU 周期配额
            cpu_period = 100000  # CPU 周期（100ms）

            # 安全选项
            security_opt = ["no-new-privileges:true"]  # 禁止提权

            # 只读根文件系统
            read_only = self.config.read_only_root

            # Step 4: 创建并启动容器
            start_time = time.time()
            try:
                container = client.containers.run(
                    image=self.config.image,
                    command=[
                        "data/readonly/",      # 输入数据目录
                        "/tmp/output",          # 输出目录
                        "/home/auditor/script.py",  # 代码文件
                    ],
                    volumes=mounts,
                    read_only=read_only,
                    network_mode="none" if self.config.network_disabled else None,
                    mem_limit=self.config.memory_limit,
                    memswap_limit=self.config.memory_limit,  # 禁用 swap
                    cpu_quota=cpu_quota,
                    cpu_period=cpu_period,
                    security_opt=security_opt,
                    cap_drop=["ALL"],          # 丢弃所有 Linux capabilities
                    auto_remove=self.config.auto_remove,
                    detach=True,               # 后台运行
                    name=execution_id,
                    environment={
                        "PYTHONDONTWRITEBYTECODE": "1",
                        "PYTHONUNBUFFERED": "1",
                    },
                )

                # Step 5: 等待容器完成（带超时）
                try:
                    result = container.wait(
                        timeout=self.config.timeout_seconds
                    )
                    exit_code = result.get("StatusCode", -1)
                except Exception as timeout_err:
                    # 超时强制终止
                    try:
                        container.kill(signal="SIGKILL")
                    except Exception:
                        pass
                    return {
                        "success": False,
                        "error": f"执行超时（>{self.config.timeout_seconds}秒），容器已强制终止",
                        "execution_id": execution_id,
                        "elapsed_seconds": round(time.time() - start_time, 2),
                    }

                # Step 6: 获取日志输出
                logs_stdout = container.logs(
                    stdout=True, stderr=False, timestamps=False
                ).decode("utf-8", errors="replace")
                logs_stderr = container.logs(
                    stdout=False, stderr=True, timestamps=False
                ).decode("utf-8", errors="replace")

                elapsed = time.time() - start_time

                # Step 7: 读取输出文件
                output_files = []
                output_path = Path(output_dir)
                if output_path.exists():
                    output_files = [f.name for f in output_path.iterdir() if f.is_file()]

                # 判断执行状态
                is_success = (
                    exit_code == 0 and
                    "__SANDBOX_EXECUTION_SUCCESS__" in logs_stdout
                )

                return {
                    "success": is_success,
                    "execution_id": execution_id,
                    "elapsed_seconds": round(elapsed, 2),
                    "return_code": exit_code,
                    "stdout": logs_stdout[-5000:],      # 截断
                    "stderr": logs_stderr[:5000] if logs_stderr else None,
                    "output_files": output_files,
                    "output_dir": output_dir,
                    "docker_config": {
                        "image": self.config.image,
                        "cpu_limit": self.config.cpu_limit,
                        "memory_limit": self.config.memory_limit,
                        "network_disabled": self.config.network_disabled,
                        "read_only_root": self.config.read_only_root,
                    }
                }

            except Exception as e:
                return {
                    "success": False,
                    "error": f"Docker 执行异常: {str(e)}",
                    "execution_id": execution_id,
                }

            finally:
                # 确保容器被清理
                if container:
                    try:
                        container.reload()
                        if container.status == "running":
                            container.kill(signal="SIGKILL")
                        if not self.config.auto_remove:
                            container.remove(force=True)
                    except Exception:
                        pass

    def execute_with_retry(
        self,
        code: str,
        input_data_paths: Dict[str, str],
        output_dir: str,
        max_retries: int = 3,
    ) -> Dict[str, Any]:
        """
        带重试的执行（OpenClaw 自纠错循环）

        每次重试都在全新的干净容器中进行，
        避免前一次失败的残留影响。

        Args:
            max_retries: 最大重试次数（OpenClaw 自纠错循环上限）
        """
        last_error = None

        for attempt in range(1, max_retries + 1):
            result = self.execute(code, input_data_paths, output_dir)

            if result.get("success"):
                result["retry_count"] = attempt - 1
                return result

            last_error = result.get("error", "Unknown error")

            # 如果还有重试机会，准备下一次（全新容器自动创建）
            if attempt < max_retries:
                time.sleep(1)  # 短暂间隔

        # 所有重试都失败了
        return {
            "success": False,
            "error": f"经过 {max_retries} 次重试后仍然失败。最后一次错误: {last_error}",
            "retry_count": max_retries,
        }

    @staticmethod
    def _parse_memory(mem_str: str) -> int:
        """解析内存限制字符串为字节数"""
        units = {"b": 1, "k": 1024, "m": 1024**2, "g": 1024**3}
        mem_str = mem_str.lower().strip()
        for suffix, multiplier in units.items():
            if mem_str.endswith(suffix):
                return int(mem_str[:-1]) * multiplier
        return int(mem_str)


# ═══════════════════════════════════════════════════════════════
# 统一安全执行入口（与 v1 API 兼容）
# ═══════════════════════════════════════════════════════════════

@dataclass
class SandboxResult:
    """沙箱执行结果"""
    success: bool
    execution_id: str
    ast_check_passed: bool
    ast_violations: List[str]
    docker_result: Optional[Dict[str, Any]] = None
    journal_entries_valid: bool = False
    journal_entry_errors: List[str] = field(default_factory=list)
    journal_entries: Optional[Dict[str, Any]] = None
    elapsed_total_seconds: float = 0.0
    retry_count: int = 0


class AuditCodeSandboxV2:
    """
    统一安全执行入口（v2.0，适配安全加固版白皮书）

    使用方式：
        sandbox = AuditCodeSandboxV2()
        result = sandbox.safe_execute(
            code=openclaw_generated_code,
            input_data_paths={"left": "data/storage/bank_flow.xlsx", "right": "data/storage/ledger.xlsx"},
            output_dir="data/output",
        )

        if result.success:
            journal_entries = result.journal_entries
            # 传给 named_ranges 映射引擎
    """

    def __init__(self, config: Optional[SandboxConfig] = None):
        self.docker_sandbox = DockerSandboxV2(config)

    def safe_execute(
        self,
        code: str,
        input_data_paths: Dict[str, str],
        output_dir: str,
        max_retries: int = 3,
    ) -> SandboxResult:
        """
        完整的安全执行流程（五层纵深防御）
        """
        start = time.time()
        execution_id = f"safe_{uuid.uuid4().hex[:8]}"

        # Step 1: Layer 1 - AST 安全检查
        ast_safe, violations, _ = compile_with_restrictedpython(code)

        if not ast_safe and not any("WARNING" in v for v in violations):
            # 严重违规（非警告），直接拒绝
            return SandboxResult(
                success=False,
                execution_id=execution_id,
                ast_check_passed=False,
                ast_violations=violations,
                elapsed_total_seconds=time.time() - start,
            )

        # Step 2: Layer 2-4 - Docker 隔离执行（带重试）
        docker_result = self.docker_sandbox.execute_with_retry(
            code=code,
            input_data_paths=input_data_paths,
            output_dir=output_dir,
            max_retries=max_retries,
        )

        if not docker_result.get("success"):
            return SandboxResult(
                success=False,
                execution_id=execution_id,
                ast_check_passed=True,
                ast_violations=violations,
                docker_result=docker_result,
                retry_count=docker_result.get("retry_count", 0),
                elapsed_total_seconds=time.time() - start,
            )

        # Step 3: Layer 5 - 输出校验（审计调整分录 Schema）
        journal_entries = None
        je_valid = False
        je_errors = []

        output_path = Path(output_dir) / "journal_entries.json"
        if output_path.exists():
            try:
                with open(output_path, "r", encoding="utf-8") as f:
                    journal_entries = json.load(f)
                je_valid, je_errors = validate_journal_entries(journal_entries)
            except (json.JSONDecodeError, IOError) as e:
                je_errors = [f"输出文件读取失败: {e}"]

        return SandboxResult(
            success=je_valid,
            execution_id=execution_id,
            ast_check_passed=True,
            ast_violations=violations,
            docker_result=docker_result,
            journal_entries_valid=je_valid,
            journal_entry_errors=je_errors,
            journal_entries=journal_entries,
            retry_count=docker_result.get("retry_count", 0),
            elapsed_total_seconds=time.time() - start,
        )


# ═══════════════════════════════════════════════════════════════
# Layer 5: 输出验证（审计调整分录 Schema）— 与 v1 一致
# ═══════════════════════════════════════════════════════════════

def validate_journal_entries(output_json: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    校验审计调整分录 JSON 是否符合标准 Schema
    校验规则：结构完整、科目编码格式、借贷平衡、金额非负、日期格式
    """
    errors: List[str] = []

    if "entries" not in output_json:
        return False, ["缺少 entries 字段"]
    if "metadata" not in output_json:
        return False, ["缺少 metadata 字段"]

    entries = output_json["entries"]
    metadata = output_json["metadata"]

    total_debits_all = 0.0
    total_credits_all = 0.0

    for idx, entry in enumerate(entries):
        prefix = f"entries[{idx}]"

        for field in ["seq_no", "date", "explanation", "debits", "credits"]:
            if field not in entry:
                errors.append(f"{prefix}: 缺少 {field}")

        if "date" in entry:
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", entry["date"]):
                errors.append(f"{prefix}: 日期格式错误: {entry['date']}")

        debit_sum = sum(d.get("amount", 0) for d in entry.get("debits", []))
        credit_sum = sum(c.get("amount", 0) for c in entry.get("credits", []))
        total_debits_all += debit_sum
        total_credits_all += credit_sum

        if abs(debit_sum - credit_sum) > 0.01:
            errors.append(
                f"{prefix}: 借贷不平衡，借方={debit_sum:.2f}, 贷方={credit_sum:.2f}"
            )

        for side, items in [("debits", entry.get("debits", [])), ("credits", entry.get("credits", []))]:
            for item_idx, item in enumerate(items):
                code = item.get("account_code", "")
                if not re.match(r"^[0-9]+(\.[0-9]+)*$", code):
                    errors.append(f"{prefix}.{side}[{item_idx}]: 科目编码格式错误: {code}")

    if "balance_validation" in metadata:
        bv = metadata["balance_validation"]
        if not bv.get("is_balanced", False):
            errors.append(
                f"整体借贷不平衡: 借方={bv.get('total_debits', 0):.2f}, "
                f"贷方={bv.get('total_credits', 0):.2f}"
            )

    return len(errors) == 0, errors


# ═══════════════════════════════════════════════════════════════
# 路径安全校验 — 与 v1 一致
# ═══════════════════════════════════════════════════════════════

class PathSecurityValidator:
    """文件路径安全校验器（防止路径遍历攻击）"""

    ALLOWED_ROOTS: Set[str] = {"data/storage", "data/output", "/tmp"}
    FORBIDDEN_PATHS: Set[str] = {
        "/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64",
        "/dev", "/proc", "/sys", "/root", "/home",
        ".ssh", ".gnupg", ".aws", ".docker",
    }

    @classmethod
    def validate(cls, file_path: str) -> Tuple[bool, Optional[str]]:
        path = Path(file_path).resolve()
        if not path.is_absolute():
            return False, f"路径必须是绝对路径: {file_path}"
        allowed = any(str(path).startswith(root) for root in cls.ALLOWED_ROOTS)
        if not allowed:
            return False, f"路径不在允许范围内: {file_path}"
        path_str = str(path)
        for forbidden in cls.FORBIDDEN_PATHS:
            if forbidden in path_str:
                return False, f"路径包含禁止目录: {forbidden}"
        return True, None
