#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据质量预检门 (data_quality.py) —— 计划 B4
====================================================
在 chaos_input 完成后、DAG 编译前，对上传数据做确定性质量诊断，
生成质量报告卡,随 Data Catalog 进入审批页。

检查项：空值率 / 金额列文本化 / 行级重复 / 表头异常 / 日期格式混用
设计原则：只读诊断不修改原始数据；全部确定性代码不调用 LLM；采样前 5000 行；
         单文件秒级完成。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List

import pandas as pd

SAMPLE_ROWS = 5000


@dataclass
class ColIssue:
    column: str
    issue_type: str
    severity: str  # WARN / INFO
    detail: str


@dataclass
class FileQualityReport:
    filename: str
    total_rows: int
    total_cols: int
    sampled_rows: int
    issues: List[ColIssue] = field(default_factory=list)
    overall: str = "CLEAN"

    def to_dict(self):
        return {
            "filename": self.filename,
            "total_rows": self.total_rows,
            "total_cols": self.total_cols,
            "sampled_rows": self.sampled_rows,
            "issues": [{"column": i.column, "type": i.issue_type,
                        "severity": i.severity, "detail": i.detail}
                       for i in self.issues],
            "overall": self.overall,
        }


def inspect_file(file_path: str) -> FileQualityReport:
    """对单文件做数据质量诊断，返回报告对象"""
    import os
    fname = os.path.basename(file_path)
    try:
        ext = file_path.rsplit(".", 1)[-1].lower()
        if ext == "csv":
            df = pd.read_csv(file_path, nrows=SAMPLE_ROWS)
        else:
            df = pd.read_excel(file_path, nrows=SAMPLE_ROWS)
    except Exception:
        return FileQualityReport(
            filename=fname, total_rows=0, total_cols=0, sampled_rows=0,
            overall="POOR",
            issues=[ColIssue("(全部)", "bad_file", "WARN",
                             "文件无法读取，请检查格式")])

    n_rows, n_cols = len(df), len(df.columns)
    rpt = FileQualityReport(filename=fname, total_rows=n_rows,
                            total_cols=n_cols,
                            sampled_rows=min(n_rows, SAMPLE_ROWS))

    # ① 列级空值率
    for c in df.columns:
        rate = df[c].isna().mean()
        if rate > 0.8:
            rpt.issues.append(ColIssue(str(c), "empty_rate", "WARN",
                                       f"空值率 {rate:.0%}"))
        elif rate > 0.5:
            rpt.issues.append(ColIssue(str(c), "empty_rate", "INFO",
                                       f"空值率 {rate:.0%}"))

    # ② 金额列文本化：列名含金额关键词且值是 object 沾数字
    amount_kw = ["金额", "发生额", "余额", "收入", "支出", "借方", "贷方",
                 "合计", "总计", "amount", "balance", "sum"]
    for c in df.columns:
        if not any(k in str(c).lower() for k in amount_kw):
            continue
        s = df[c].dropna()
        if len(s) == 0 or s.dtype != object:
            continue
        numeric_ratio = s.apply(
            lambda x: bool(re.match(r"^[\d,.，\s]+$", str(x)))
            if isinstance(x, str) else False
        ).mean()
        if numeric_ratio > 0.3:
            rpt.issues.append(ColIssue(
                str(c), "non_numeric", "WARN",
                f"金额列{str(c)} {numeric_ratio:.0%}行存为文本('1,234.56'),"
                "沙箱计算可能失败，建议Excel转为数值"))

    # ③ 行级重复
    dup_count = df.duplicated().sum()
    if dup_count > n_rows * 0.5:
        rpt.issues.append(ColIssue("(全部行)", "duplicates", "WARN",
                                   f"{dup_count}行重复({dup_count/n_rows:.0%})"))
    elif dup_count > 0:
        rpt.issues.append(ColIssue("(全部行)", "duplicates", "INFO",
                                   f"{dup_count}行重复"))

    # ④ 表头异常（空列名/Unnamed）
    empty_hdr = [str(c) for c in df.columns
                 if not str(c).strip() or str(c).startswith("Unnamed")]
    if len(empty_hdr) >= n_cols * 0.5:
        rpt.issues.append(ColIssue("表头", "bad_header", "WARN",
                                   f"{len(empty_hdr)}/{n_cols}列标题为空(多行表头?)"))
    elif empty_hdr:
        rpt.issues.append(ColIssue(str(empty_hdr[0]), "bad_header", "INFO",
                                   f"{len(empty_hdr)}列标题为空"))


    # ⑤ 日期格式混用
    date_kw = ["日期", "时间", "date", "time", "记账"]
    for c in df.columns:
        if not any(k in str(c).lower() for k in date_kw):
            continue
        s = df[c].dropna().astype(str)
        fmts = {"slash": s.str.match(r"^\d{4}/\d{1,2}/\d{1,2}$").sum(),
                "dash": s.str.match(r"^\d{4}-\d{1,2}-\d{1,2}$").sum(),
                "cn": s.str.match(r"^\d{4}年\d{1,2}月\d{1,2}日$").sum()}
        non_zero = [k for k, v in fmts.items() if v > 0]
        if len(non_zero) > 1:
            rpt.issues.append(ColIssue(str(c), "mixed_date", "INFO",
                                       f"日期列存在多种格式：{', '.join(non_zero)}"))

    # ⑥ 总体评级
    warns = sum(1 for i in rpt.issues if i.severity == "WARN")
    if warns >= 3 or (rpt.issues and rpt.issues[0].issue_type == "bad_file"):
        rpt.overall = "POOR"
    elif warns >= 1:
        rpt.overall = "WARNING"

    return rpt


def inspect_catalog(input_dir: str) -> List[dict]:
    """扫描 Run 输入目录中所有数据文件，返回质量报告列表"""
    from pathlib import Path
    reports = []
    src = Path(input_dir)
    if not src.exists():
        return reports
    for f in sorted(src.iterdir()):
        if f.suffix.lower() in {".xlsx", ".xls", ".csv"}:
            try:
                reports.append(inspect_file(str(f)).to_dict())
            except Exception:
                pass
    return reports
