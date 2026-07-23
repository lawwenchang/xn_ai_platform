#!/usr/bin/env python3
"""
审计取证人机协同引擎 (§4.4 人工补录 + 智能建议)
=================================================
vLLM 分析沙箱执行结果，对匹配度低的记录给出人工补录建议
"""
import json, os, re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class OverrideSuggestion:
    """一条人工补录建议"""
    record_index: int = 0
    original_text: str = ""     # 原始摘要/户名
    suggested_action: str = ""  # INCLUDE / EXCLUDE / REVIEW
    reason: str = ""            # 建议理由
    confidence: float = 0.0     # 置信度


@dataclass
class OverrideReport:
    run_id: str = ""
    suggestions: List[OverrideSuggestion] = field(default_factory=list)
    total_records: int = 0
    suggested_count: int = 0


def analyze_for_overrides(run_id: str, match_result: dict,
                          unmatched_records: list = None,
                          threshold: float = 0.7) -> OverrideReport:
    """
    vLLM 分析匹配结果，识别可能需要人工补录的记录。

    典型场景: 银行流水中有"医疗补助拨款"的摘要，不是"医保回款"关键词，
    但业务实质属于医保回款，需要审计师人工判定。
    """
    report = OverrideReport(run_id=run_id)
    records = unmatched_records or match_result.get("unmatched", [])

    if not records:
        return report

    report.total_records = len(records)

    # 构建 prompt
    brief = []
    for i, rec in enumerate(records[:30]):
        summary = rec.get("摘要", rec.get("summary", str(rec)[:100]))
        brief.append(f"[{i}] {summary}")

    prompt = f"""你是资深审计师。以下记录在"医保回款"筛选中未被自动匹配，但可能属于实质性医保回款。

【待判定记录】
{chr(10).join(brief[:30])}

【判定规则】
- INCLUDE: 业务实质属于医保/社保/政府医疗拨款（即使摘要不含"医保"）
- EXCLUDE: 明确非医保款项（如门诊收入/药品采购/设备维修/工资等）
- REVIEW: 无法确定，需人工查证

输出JSON: {{"suggestions":[{{"index":0,"action":"INCLUDE|EXCLUDE|REVIEW","reason":"一句话理由","confidence":0.8}}]}}
只输出JSON。"""

    try:
        import httpx
        r = httpx.post(
            os.environ.get("VLLM_TUNNEL_URL", "http://localhost:18000/v1/chat/completions"),
            headers={"Authorization": "Bearer EMPTY"},
            json={"model": os.environ.get("VLLM_MODEL", "qwen3-235b"),
                  "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.2, "max_tokens": 1024}, timeout=30)
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"].strip()
        m = re.search(r'\{[\s\S]*\}', text)
        if m:
            data = json.loads(m.group(0))
            for s in data.get("suggestions", []):
                idx = s.get("index", 0)
                if idx < len(records):
                    report.suggestions.append(OverrideSuggestion(
                        record_index=idx,
                        original_text=records[idx].get("摘要", records[idx].get("summary", "")),
                        suggested_action=s.get("action", "REVIEW"),
                        reason=s.get("reason", ""),
                        confidence=s.get("confidence", 0.5)))
    except Exception as e:
        # 降级：基于关键字给建议
        for i, rec in enumerate(records[:10]):
            summary = str(rec.get("摘要", rec.get("summary", str(rec))))
            action = "REVIEW"
            reason = "vLLM不可用，请人工判定"
            if any(kw in summary for kw in ["医疗", "卫生", "卫健委", "拨款", "补助", "统筹"]):
                action = "INCLUDE"
                reason = "摘要含医疗相关关键词，可能属实质性医保回款"
            elif any(kw in summary for kw in ["门诊", "采购", "工资", "维修", "水电", "利息"]):
                action = "EXCLUDE"
                reason = "摘要明确指示非医保款项"
            report.suggestions.append(OverrideSuggestion(
                record_index=i, original_text=summary,
                suggested_action=action, reason=reason, confidence=0.6))

    report.suggested_count = len(report.suggestions)
    return report


def log_override(run_id: str, record_index: int, decision: str,
                  reason: str, auditor: str = "") -> dict:
    """
    记录一条人工补录操作，写入哈希链。
    decision: INCLUDE / EXCLUDE
    """
    from core.run_snapshot import HashChain
    entry = HashChain.record(
        project_code=run_id.split("_")[1] if "_" in run_id else "DEFAULT",
        run_id=run_id,
        event_type="MANUAL_OVERRIDE",
        content=f"[{decision}] idx={record_index} | 理由: {reason}",
        operator=auditor or "审计师",
    )
    return {"ok": True, "entry": entry.to_dict() if hasattr(entry, "to_dict") else str(entry)}
