#!/usr/bin/env python3
"""RPA 法规采集引擎 (rpa_collector.py) —— 白皮书 §4.2 动态法条更新"""
from __future__ import annotations
import hashlib, json, re, os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

REGULATIONS_DIR = Path("data/regulations")
REGULATIONS_DIR.mkdir(parents=True, exist_ok=True)

REGULATION_SOURCES = [
    {"name": "中注协", "url": "https://www.cicpa.org.cn", "type": "audit_standards"},
    {"name": "财政部会计司", "url": "https://kjs.mof.gov.cn", "type": "accounting_standards"},
    {"name": "国家税务总局", "url": "https://www.chinatax.gov.cn", "type": "tax"},
    {"name": "国家法律法规数据库", "url": "https://flk.npc.gov.cn", "type": "laws"},
]

@dataclass
class RegulationRecord:
    source: str = ""; title: str = ""; publish_date: str = ""; effective_date: str = ""
    status: str = "PENDING_REVIEW"  # PENDING_REVIEW/APPROVED/REJECTED/EXPIRED
    category: str = ""; summary: str = ""; keywords: List[str] = field(default_factory=list)
    text: str = ""; file_path: str = ""; vector_id: str = ""
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat()+"Z")

class RegulationStore:
    def __init__(self):
        self.index_path = REGULATIONS_DIR / "index.json"
        self._index: Dict[str, RegulationRecord] = {}
        if self.index_path.exists():
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
            for k, v in data.items():
                self._index[k] = RegulationRecord(**{kk: vv for kk, vv in v.items() if kk != "keywords"})
                self._index[k].keywords = v.get("keywords", [])

    def _save(self):
        d = {k: {"source": v.source, "title": v.title, "publish_date": v.publish_date,
                 "effective_date": v.effective_date, "status": v.status, "category": v.category,
                 "summary": v.summary, "keywords": v.keywords, "text": v.text[:500],
                 "file_path": v.file_path, "vector_id": v.vector_id, "created_at": v.created_at}
              for k, v in self._index.items()}
        self.index_path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

    def _key(self, title, pub): return hashlib.md5(f"{title}||{pub}".encode()).hexdigest()[:16]

    def exists(self, title, pub): return self._key(title, pub) in self._index

    def add(self, r: RegulationRecord):
        self._index[self._key(r.title, r.publish_date)] = r
        self._save()

    def get_pending(self): return [r for r in self._index.values() if r.status == "PENDING_REVIEW"]
    def get_approved(self): return [r for r in self._index.values() if r.status == "APPROVED"]
    def get_all(self): return list(self._index.values())

    def approve(self, title, pub):
        k = self._key(title, pub)

def scan_local_files(base_dir: Optional[Path] = None) -> List[RegulationRecord]:
    """扫描本地法规文件目录，自动提取元数据入库（白皮书「采集层」离线兜底方案）"""
    base = base_dir or Path("D:/审计准则与法规文件整理")
    if not base.exists():
        return []
    records = []
    store = RegulationStore()
    for fp in base.rglob("*"):
        if not fp.is_file(): continue
        ext = fp.suffix.lower()
        if ext not in (".txt", ".md", ".pdf"): continue
        m = re.match(r'(\d{4}-\d{2}-\d{2})_', fp.name)
        pub = m.group(1) if m else ""
        if store.exists(fp.stem, pub): continue
        text = ""
        if ext in (".txt", ".md"):
            try: text = fp.read_text(encoding="utf-8")[:10000]
            except Exception: pass
        rec = RegulationRecord(source=base.name, title=fp.stem, publish_date=pub,
                               text=text, file_path=str(fp))
        records.append(rec); store.add(rec)
    return records


def vectorize_approved() -> int:
    """向量化层：将已审批通过的法规 Embedding 入库"""
    try:
        from core.rag_engine import index_documents
    except ImportError:
        print("[RPA] rag_engine 不可用"); return 0
    store = RegulationStore(); new = 0
    for rec in store.get_approved():
        if rec.vector_id or not rec.text: continue
        try:
            vid = index_documents([{"text": rec.text, "source": rec.title}])
            if vid: rec.vector_id = vid; store.add(rec); new += 1
        except Exception as e: print(f"[RPA] 向量化失败 {rec.title}: {e}")
    return new

    def reject(self, title, pub):
        k = self._key(title, pub)
        if k in self._index: self._index[k].status = "REJECTED"; self._save()

    def mark_expired(self, title, pub):
        k = self._key(title, pub)
        if k in self._index: self._index[k].status = "EXPIRED"; self._save()

    def count(self): return len(self._index)
