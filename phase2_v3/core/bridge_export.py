#!/usr/bin/env python3
"""
中间件桥接导出引擎 (§4.3.1)
=============================
将AI清洗后的审计数据导出为第三方行业软件兼容格式:
- CSV (中注协通用接口)
- XML (鼎信诺/审计大师标准)
- 一键导出 Run 结果

白皮书原文: "将AI清洗后的差异数据和审定数据，导出为符合中注协接口标准
的.csv或.xml中间数据包，供鼎信诺、审计大师等行业软件无缝对接。"
"""
from __future__ import annotations
import csv, json, os, xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "outputs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class BridgeExportResult:
    """一次导出操作的结果"""
    format: str           # csv / xml / both
    file_paths: List[str] = field(default_factory=list)
    row_count: int = 0
    error: str = ""


def export_to_csv(data: List[Dict], filename: str, output_dir: str = "") -> str:
    """
    导出为 CSV（中注协标准接口格式）。
    自动处理中文编码(BOM)、千分位数字等。
    """
    if not output_dir:
        output_dir = str(OUTPUT_DIR)
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, filename)
    if not data:
        return filepath

    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(data[0].keys()))
        writer.writeheader()
        writer.writerows(data)
    return filepath


def export_to_xml(data: List[Dict], root_tag: str, filename: str,
                  output_dir: str = "", metadata: Dict = None) -> str:
    """
    导出为 XML（鼎信诺/审计大师兼容格式）。

    XML结构:
    <AuditData>
      <Meta>
        <GeneratedAt>2026-07-17T10:00:00</GeneratedAt>
        <RunID>RUN_xxx</RunID>
        <RecordCount>127</RecordCount>
      </Meta>
      <Records>
        <Record>
          <机构>医保中心</机构>
          <差额>5000.00</差额>
        </Record>
      </Records>
    </AuditData>
    """
    if not output_dir:
        output_dir = str(OUTPUT_DIR)
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, filename)

    root = ET.Element("AuditData")

    # 元数据
    meta = ET.SubElement(root, "Meta")
    ET.SubElement(meta, "GeneratedAt").text = datetime.now().isoformat()
    if metadata:
        for k, v in metadata.items():
            el = ET.SubElement(meta, k)
            el.text = str(v)
    ET.SubElement(meta, "RecordCount").text = str(len(data))

    # 数据记录
    records = ET.SubElement(root, "Records")
    for row in data:
        rec = ET.SubElement(records, "Record")
        for key, val in row.items():
            el = ET.SubElement(rec, _safe_tag(key))
            el.text = str(val) if val is not None else ""

    # 格式化输出
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(filepath, encoding="utf-8", xml_declaration=True)
    return filepath


def export_run_results(run_id: str, output_dir: str = "",
                       formats: List[str] = None) -> BridgeExportResult:
    """
    一键导出 Run 的所有成果物为第三方兼容格式。
    自动读取 run 的 JSON 摘要和差异明细数据。
    """
    if not formats:
        formats = ["csv", "xml"]

    result = BridgeExportResult(format="+".join(formats))
    run_dir = Path(__file__).resolve().parent.parent / "data" / "runs" / run_id

    if not run_dir.exists():
        result.error = f"Run 目录不存在: {run_dir}"
        return result

    # 尝试读取 outputs 下的数据
    outputs_dir = run_dir / "outputs"
    data = []
    summary = {}

    # 优先读 journal_entries.json
    jp = outputs_dir / "journal_entries.json"
    if jp.exists():
        try:
            summary = json.loads(jp.read_text(encoding="utf-8"))
        except Exception:
            pass

    # 读取差异明细 CSV
    for csv_file in outputs_dir.glob("*.csv"):
        try:
            with open(csv_file, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                data = list(reader)
            if data:
                break
        except Exception:
            continue

    # 如果没有 CSV，尝试从 summary 重建
    if not data and summary.get("details"):
        data = summary["details"]

    if not data and summary:
        data = [summary]

    result.row_count = len(data)

    meta = {
        "RunID": run_id,
        "GeneratedAt": datetime.now().isoformat(),
        "Source": str(outputs_dir),
    }

    if "csv" in formats and data:
        fp = export_to_csv(data, f"{run_id}_bridge.csv", output_dir)
        result.file_paths.append(fp)

    if "xml" in formats:
        fp = export_to_xml(data, "AuditRecords",
                           f"{run_id}_bridge.xml", output_dir, meta)
        result.file_paths.append(fp)

    return result


def _safe_tag(name: str) -> str:
    """列名 → 合法 XML 标签名"""
    safe = name.replace(" ", "_").replace("/", "_").replace("(", "").replace(")", "")
    safe = "".join(c for c in safe if c.isalnum() or c in "_-.")
    return safe or "Field"


def list_exports(output_dir: str = "") -> List[Dict]:
    """列出所有已导出的桥接文件"""
    d = output_dir or str(OUTPUT_DIR)
    if not os.path.isdir(d):
        return []
    files = []
    for f in Path(d).glob("*.csv"):
        files.append({"name": f.name, "format": "csv",
                       "size": f.stat().st_size, "time": datetime.fromtimestamp(f.stat().st_mtime).isoformat()})
    for f in Path(d).glob("*.xml"):
        files.append({"name": f.name, "format": "xml",
                       "size": f.stat().st_size, "time": datetime.fromtimestamp(f.stat().st_mtime).isoformat()})
    return sorted(files, key=lambda x: x["time"], reverse=True)
