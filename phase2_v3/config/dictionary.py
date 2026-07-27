"""审计词典单一数据源：所有词表只在这里维护，各处 import 使用。
新增词条必须走准入流水线（历史数据回测 + 人工批准），禁止各处手抄。

JSON 加载器：启动时读取 config/extraction_dict.json，
每次访问检查 mtime，文件有变动则自动热更新。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

FEE_WORDS = ("手续费", "收费", "短信费", "年费", "账户管理费", "工本费"
             "服务费", "费用外收", "批量扣费")
INTEREST_WORDS = ("利息", "结息", "批量结息")
REVERSAL_WORDS = ("冲正", "冲销", "红冲", "撤销")
INTERBANK_WORDS = ("支行", "农商行", "工商银行", "中行", "建行", "邮储",
                   "转账农行", "转账农商")

_DICT_PATH: Path = Path(__file__).parent / "extraction_dict.json"
_cache: Optional[Dict[str, Any]] = None
_cache_mtime: float = 0.0
_lock = threading.Lock()


def _load_json() -> Dict[str, Any]:
    with open(_DICT_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _check_and_reload() -> Dict[str, Any]:
    global _cache, _cache_mtime
    try:
        mtime = os.path.getmtime(_DICT_PATH)
    except OSError:
        mtime = 0.0
    with _lock:
        if _cache is None or mtime != _cache_mtime:
            _cache = _load_json()
            _cache_mtime = mtime
        return _cache


def get_raw() -> Dict[str, Any]:
    return _check_and_reload()


def get_dict() -> Dict[str, Any]:
    raw = _check_and_reload()
    result: Dict[str, Any] = {}
    for name, entry in raw.get("categories", {}).items():
        result[name] = entry
    for name, entry in raw.get("ledger_only_categories", {}).items():
        result[name] = entry
    return result


def get_version() -> str:
    return _check_and_reload().get("version", "unknown")


def save_raw(data: Dict[str, Any]) -> None:
    global _cache, _cache_mtime
    with _lock:
        with open(_DICT_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        _cache = data
        try:
            _cache_mtime = os.path.getmtime(_DICT_PATH)
        except OSError:
            _cache_mtime = 0.0


def bump_version() -> str:
    raw = _check_and_reload()
    ver = raw.get("version", "dictionary_v1.0")
    import re as _re
    m = _re.match(r"dictionary_v(\d+)\.(\d+)", ver)
    if m:
        major, minor = int(m.group(1)), int(m.group(2))
        new_ver = f"dictionary_v{major}.{minor + 1}"
    else:
        new_ver = "dictionary_v1.1"
    raw["version"] = new_ver
    raw["updated"] = _now_str()
    save_raw(raw)
    return new_ver


def reload() -> None:
    global _cache, _cache_mtime
    with _lock:
        _cache = None
        _cache_mtime = 0.0


def _now_str() -> str:
    from datetime import date
    return date.today().isoformat()
