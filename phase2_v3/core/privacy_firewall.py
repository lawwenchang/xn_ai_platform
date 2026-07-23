#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
隐私防火墙中间件 (privacy_firewall.py)
三层防御：Regex硬规则 → SpaCy NER → 关键词黑名单
"""
import re
from typing import Dict, List, Optional, Tuple

# ── 第一层：Regex ──────────────────────────────────
BANK_ACCOUNT_PATTERNS = [
    re.compile(r'(?<!\d)[1-9]\d{15,18}(?!\d)'),
    re.compile(r'(?<!\d)\d{12,20}(?!\d)'),
]
PHONE_PATTERNS = [re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)')]
ID_CARD_PATTERNS = [
    re.compile(r'(?<!\\d)\\d{17}[\\dXx](?!\\d)'),
    re.compile(r'(?<!\\d)\\d{15}(?!\\d)'),
]
CREDIT_CODE_PATTERN = re.compile(r'\b[0-9A-HJ-NPQRTUWXY]{2}\d{6}[0-9A-HJ-NPQRTUWXY]{10}\b')
EMAIL_PATTERN = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')

_SENSITIVE_KEYWORDS = {
    "医保基金": "BIZ_FUND_001",
    "社保基金": "BIZ_FUND_002",
    "住房公积金": "BIZ_FUND_003",
}

_NLP = None

def _get_nlp():
    global _NLP
    if _NLP is None:
        try:
            import spacy
            _NLP = spacy.load("zh_core_web_sm")
        except Exception:
            pass
    return _NLP


def _regex_sanitize(text: str) -> Tuple[str, Dict[str, List[str]]]:
    findings: Dict[str, List[str]] = {}
    all_sensitive = set()
    for cat, patterns in [("id_card", ID_CARD_PATTERNS), ("phone", PHONE_PATTERNS)]:
        findings[cat] = []
        for p in patterns:
            findings[cat].extend(p.findall(text))
        all_sensitive.update(findings[cat])
    findings["credit_code"] = CREDIT_CODE_PATTERN.findall(text)
    all_sensitive.update(findings["credit_code"])
    findings["bank_account"] = []
    for p in BANK_ACCOUNT_PATTERNS:
        for m in p.finditer(text):
            if m.group() not in all_sensitive:
                findings["bank_account"].append(m.group())
    findings["email"] = EMAIL_PATTERN.findall(text)
    entity_map = {}
    counter = 0
    sanitized = text
    for cat, vals in findings.items():
        for v in sorted(vals, key=len, reverse=True):
            if v not in entity_map:
                counter += 1
                entity_map[v] = f"ENTITY_{counter:04d}"
    for orig, repl in entity_map.items():
        sanitized = sanitized.replace(orig, repl)
    return sanitized, findings


def _ner_sanitize(text: str) -> str:
    nlp = _get_nlp()
    if nlp is None:
        return text
    doc = nlp(text)
    reps = []
    for ent in doc.ents:
        if ent.label_ in ("ORG", "PERSON", "GPE"):
            pid = f"ORG_{hash(ent.text)%10000:04d}" if ent.label_=="ORG" else f"PER_{hash(ent.text)%10000:04d}" if ent.label_=="PERSON" else f"LOC_{hash(ent.text)%10000:04d}"
            reps.append((ent.text, pid))
    reps.sort(key=lambda x: len(x[0]), reverse=True)
    for orig, repl in reps:
        text = text.replace(orig, repl)
    return text


def _keyword_sanitize(text: str, keywords: Dict[str, str]) -> str:
    s = text
    for kw, code in keywords.items():
        s = s.replace(kw, code)
    return s


class PrivacyFirewall:
    """三层防御脱敏：Regex → SpaCy NER → 关键词"""

    def __init__(self, custom_keywords: Optional[Dict[str, str]] = None):
        self.keywords = {**_SENSITIVE_KEYWORDS, **(custom_keywords or {})}

    def sanitize(self, text: str) -> Tuple[str, Dict]:
        if not text:
            return text, {}
        sanitized, regex_findings = _regex_sanitize(text)
        sanitized = _ner_sanitize(sanitized)
        sanitized = _keyword_sanitize(sanitized, self.keywords)
        report = {
            "original_length": len(text),
            "sanitized_length": len(sanitized),
            "regex_findings": {k: len(v) for k, v in regex_findings.items() if v},
            "ner_available": _get_nlp() is not None,
        }
        return sanitized, report

    def sanitize_dict(self, data: Dict) -> Tuple[Dict, Dict]:
        sanitized = {}
        total_report = {}
        for key, value in data.items():
            if isinstance(value, str):
                sanitized[key], report = self.sanitize(value)
                total_report[key] = report
            else:
                sanitized[key] = value
        return sanitized, total_report


_firewall: Optional[PrivacyFirewall] = None

def get_firewall() -> PrivacyFirewall:
    global _firewall
    if _firewall is None:
        _firewall = PrivacyFirewall()
    return _firewall


