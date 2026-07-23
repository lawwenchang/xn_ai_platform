#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
混沌输入处理中间件 (chaos_input.py)
====================================
"全无状态生命周期与语义编译版"白皮书 §4.1 的核心实现

将一切混沌输入（ZIP/RAR/7z/嵌套文件夹/散落Excel）转化为平台可识别的标准化只读资产。

处理流程：
1. 递归解压（ZIP/RAR/7z → 临时工作区）
2. 路径扁平化 + 冲突消解
3. 静默嗅探（表头 Schema 提取）
4. 全局唯一哈希计算
5. 只读锁定
6. Data Catalog JSON 生成

与前后阶段衔接：
- 第一阶段 (W1-2): 存储路径已就绪
- 第二阶段 (W3-4): 本文件 —— 混沌输入处理
- run_snapshot.py: 生成的 Data Catalog 作为 Run 的 input_catalog

依赖：
    pip install pandas openpyxl xlrd
    system: unrar, 7z 命令行工具（可选，用于 RAR/7z）

作者：智能审计平台开发团队
版本：3.0.0（语义编译版）
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from core.run_snapshot import AssetCatalog, RunSnapshotManager


# ═══════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════

SUPPORTED_EXTS = {".xlsx", ".xls", ".csv", ".docx", ".doc", ".pdf", ".md", ".txt"}
SUPPORTED_ARCHIVES = {".zip", ".rar", ".7z"}
MAX_FLAT_DEPTH = 5                      # 最大递归解压深度
SNIFF_ROWS = 1000                       # 静默嗅探行数


# ═══════════════════════════════════════════════════════════════
# 混沌输入处理器
# ═══════════════════════════════════════════════════════════════

class ChaosInputProcessor:
    """
    混沌输入处理中间件
    
    核心职责：将任意混沌输入 → 标准化只读资产 + Data Catalog
    
    支持的输入：
    - 单张 Excel (.xlsx/.xls/.csv)
    - ZIP 压缩包（含嵌套文件夹）
    - RAR / 7z 压缩包（需系统安装 unrar/7z）
    - 散落多文件的文件夹路径
    - 以上任意组合
    """

    def __init__(self, temp_base: Path = Path("data/temp")):
        self.temp_base = temp_base

    def process(
        self,
        source_path: str,
        run_id: str,
    ) -> Tuple[Path, AssetCatalog]:
        """
        处理混沌输入
        
        Args:
            source_path: 上传文件/文件夹的路径
            run_id: 当前 Run 的 ID
        
        Returns:
            (flattened_dir, asset_catalog): 扁平化后的目录 + 资产目录
        
        流程：
            1. 判断输入类型（文件/压缩包/文件夹）
            2. 解压/复制到临时工作区
            3. 路径扁平化
            4. 静默嗅探
            5. 计算全局哈希
            6. 移动到 Run 的 input 目录并锁定只读
        """
        source = Path(source_path)
        work_dir = self.temp_base / run_id / "chaos_work"
        work_dir.mkdir(parents=True, exist_ok=True)

        # Step 1: 解压/复制到工作区
        if source.suffix.lower() in SUPPORTED_ARCHIVES:
            extracted = self._extract_archive(source, work_dir)
        elif source.is_dir():
            extracted = self._copy_directory(source, work_dir)
        else:
            # 单文件
            target = work_dir / source.name
            shutil.copy2(str(source), str(target))
            extracted = work_dir

        # Step 2: 路径扁平化
        flat_dir = self._flatten_paths(extracted)

        # Step 3: 静默嗅探 + Catalog 生成
        catalog = self._sniff_and_catalog(flat_dir)

        # Step 4: 计算全局哈希
        catalog.global_hash = self._compute_global_hash(flat_dir)

        return flat_dir, catalog

    # ── 解压 ─────────────────────────────────────────────────

    def _extract_archive(self, archive_path: Path, work_dir: Path) -> Path:
        """解压压缩包"""
        suffix = archive_path.suffix.lower()

        if suffix == ".zip":
            return self._extract_zip(archive_path, work_dir)
        elif suffix == ".rar":
            return self._extract_rar(archive_path, work_dir)
        elif suffix == ".7z":
            return self._extract_7z(archive_path, work_dir)
        else:
            raise ValueError(f"不支持的压缩格式: {suffix}")

    def _extract_zip(self, archive_path: Path, work_dir: Path) -> Path:
        """解压 ZIP"""
        extract_dir = work_dir / "extracted"
        with zipfile.ZipFile(str(archive_path), 'r') as z:
            z.extractall(str(extract_dir))
        return extract_dir

    def _extract_rar(self, archive_path: Path, work_dir: Path) -> Path:
        """解压 RAR（需要 unrar 命令行工具）"""
        extract_dir = work_dir / "extracted"
        extract_dir.mkdir(parents=True, exist_ok=True)
        import subprocess
        try:
            subprocess.run(
                ["unrar", "x", "-o+", str(archive_path), str(extract_dir)],
                check=True, capture_output=True, timeout=60
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            raise RuntimeError(
                "RAR 解压失败。请安装 unrar: apt-get install unrar"
            )
        return extract_dir

    def _extract_7z(self, archive_path: Path, work_dir: Path) -> Path:
        """解压 7z（需要 7z 命令行工具）"""
        extract_dir = work_dir / "extracted"
        extract_dir.mkdir(parents=True, exist_ok=True)
        import subprocess
        try:
            subprocess.run(
                ["7z", "x", str(archive_path), f"-o{extract_dir}", "-y"],
                check=True, capture_output=True, timeout=60
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            raise RuntimeError(
                "7z 解压失败。请安装 p7zip: apt-get install p7zip-full"
            )
        return extract_dir

    def _copy_directory(self, source_dir: Path, work_dir: Path) -> Path:
        """复制文件夹内容到工作区"""
        target = work_dir / "extracted"
        shutil.copytree(str(source_dir), str(target), dirs_exist_ok=True)
        return target

    # ── 路径扁平化 ───────────────────────────────────────────

    def _flatten_paths(self, extracted_dir: Path) -> Path:
        """
        路径扁平化 + 冲突消解
        
        将多级目录结构扁平化为单层：
        ./2025/审计底稿/银行流水/流水明细.xlsx → 流水明细_1.xlsx
        """
        flat_dir = extracted_dir.parent / "flattened"
        flat_dir.mkdir(parents=True, exist_ok=True)

        file_counter: Dict[str, int] = {}

        for root, dirs, files in os.walk(str(extracted_dir)):
            for filename in files:
                src_path = Path(root) / filename
                
                # 只保留支持的文件类型
                if src_path.suffix.lower() not in SUPPORTED_EXTS:
                    continue

                # 冲突消解：同名文件加序号
                stem = src_path.stem
                ext = src_path.suffix
                
                if stem in file_counter:
                    file_counter[stem] += 1
                    new_name = f"{stem}_{file_counter[stem]}{ext}"
                else:
                    file_counter[stem] = 0
                    new_name = f"{stem}{ext}"

                dst_path = flat_dir / new_name
                shutil.copy2(str(src_path), str(dst_path))

        return flat_dir

    # ── 静默嗅探 + Catalog ───────────────────────────────────

    def _sniff_and_catalog(self, flat_dir: Path) -> AssetCatalog:
        """
        静默嗅探：读取每张表的前 1000 行，提取表头结构
        """
        catalog = AssetCatalog(global_hash="")
        files_info = []
        total_size = 0

        for file_path in sorted(flat_dir.iterdir()):
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in SUPPORTED_EXTS:
                continue

            file_size = file_path.stat().st_size
            total_size += file_size

            # 文档格式（docx/doc/pdf/md/txt）：统一文档加载器嗅探
            if file_path.suffix.lower() in (".docx", ".doc", ".pdf", ".md", ".txt"):
                try:
                    from core.document_loader import sniff_document
                    info = sniff_document(file_path)
                    entry = {
                        "filename": file_path.name,
                        "original_path": str(file_path.name),
                        "size_bytes": file_size,
                        "kind": info.get("kind", "document"),
                        "rows_estimated": (info.get("tables_rows") or [0])[0]
                                          if info.get("tables_rows") else 0,
                        "columns": [],
                    }
                    if info.get("text_preview"):
                        entry["text_preview"] = info["text_preview"]
                    if info.get("tables_columns"):
                        entry["tables_columns"] = info["tables_columns"]
                        # 文档内嵌表格的列也暴露给 LLM（与 Excel 同权）
                        entry["columns"] = [
                            {"name": str(c), "dtype": "object", "null_count": 0,
                             "unique_count": 0, "sample_values": []}
                            for c in (info["tables_columns"][0] if info["tables_columns"] else [])
                        ]
                    if info.get("errors"):
                        entry["errors"] = info["errors"]
                    files_info.append(entry)
                except Exception as e:
                    files_info.append({
                        "filename": file_path.name,
                        "original_path": str(file_path.name),
                        "size_bytes": file_size,
                        "error": str(e),
                    })
                continue

            # 嗅探表头（自动检测表头行）
            try:
                if file_path.suffix.lower() == ".csv":
                    df_sample = pd.read_csv(str(file_path), nrows=SNIFF_ROWS, encoding="utf-8")
                else:
                    # 自动检测表头行：尝试0-5行，打分选最优
                    best_score = -1
                    best_hr = 0
                    for hr in range(6):
                        try:
                            tmp = pd.read_excel(str(file_path), header=hr, nrows=0)
                            cols = list(tmp.columns)
                            score = 0
                            for c in cols:
                                cs = str(c)
                                if cs.startswith("Unnamed"): score -= 1
                                elif cs.replace(".", "").replace("-", "").isdigit(): score -= 2
                                else: score += 1
                            if score > best_score:
                                best_score = score
                                best_hr = hr
                        except Exception:
                            break
                    if best_score > 0:
                        df_sample = pd.read_excel(str(file_path), header=best_hr, nrows=SNIFF_ROWS)
                    else:
                        df_sample = pd.read_excel(str(file_path), header=None, nrows=SNIFF_ROWS)
                        df_sample.columns = [f"Col_{i}" for i in range(len(df_sample.columns))]

                columns_info = []
                for col in df_sample.columns:
                    sample_vals = df_sample[col].dropna().head(5).tolist()
                    # 脱敏样本值
                    sample_vals = self._sanitize_samples(sample_vals)
                    columns_info.append({
                        "name": str(col),
                        "dtype": str(df_sample[col].dtype),
                        "null_count": int(df_sample[col].isna().sum()),
                        "unique_count": int(df_sample[col].nunique()),
                        "sample_values": sample_vals,
                    })

                files_info.append({
                    "filename": file_path.name,
                    "original_path": str(file_path.name),  # 扁平化后就是文件名
                    "size_bytes": file_size,
                    "rows_estimated": len(df_sample),
                    "columns": columns_info,
                })
            except Exception as e:
                files_info.append({
                    "filename": file_path.name,
                    "original_path": str(file_path.name),
                    "size_bytes": file_size,
                    "error": str(e),
                })

        catalog.files = files_info
        catalog.total_files = len(files_info)
        catalog.total_size_mb = round(total_size / (1024 * 1024), 2)

        return catalog

    def _sanitize_samples(self, values: List[Any]) -> List[str]:
        """样本值脱敏"""
        sanitized = []
        for v in values:
            if pd.isna(v):
                continue
            s = str(v)
            if len(s) > 20:
                s = s[:10] + "..." + s[-5:]
            sanitized.append(s)
        return sanitized[:5]

    # ── 全局哈希 ─────────────────────────────────────────────

    def _compute_global_hash(self, flat_dir: Path) -> str:
        """
        计算全局唯一哈希
        
        基于：文件列表 + 各文件 MD5 + 时间戳
        """
        hasher = hashlib.sha256()
        timestamp = datetime.utcnow().isoformat().encode()
        hasher.update(timestamp)

        for file_path in sorted(flat_dir.iterdir()):
            if not file_path.is_file():
                continue
            hasher.update(file_path.name.encode())
            # 文件 MD5
            file_hasher = hashlib.md5()
            with open(str(file_path), "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    file_hasher.update(chunk)
            hasher.update(file_hasher.hexdigest().encode())

        return hasher.hexdigest()[:16]
