#!/usr/bin/env python3
"""B4/B5 语法编译 + 功能自检"""
import py_compile, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 1) 语法编译
for f in ["core/data_quality.py", "core/matching_engine.py", "api/routes.py"]:
    py_compile.compile(str(ROOT / f), doraise=True)
print("[PASS] 3 文件语法编译通过")

# 2) B4 数据质量功能
from core.data_quality import inspect_file, inspect_catalog, FileQualityReport
assert isinstance(inspect_file.__doc__, str)
# 造一个 dirty 数据跑检查
import tempfile, pandas as pd
tmp = Path(tempfile.mkdtemp()) / "dirty_test.xlsx"
d = pd.DataFrame({
    "日期": ["2026/01/01", "2026-01-02", "2026年01月03日"],
    "金额": ["1,234.56", "2,000", None], "摘要": [None, None, None],
    "客户": ["甲", "甲", "乙"]
})
d.to_excel(str(tmp), index=False)
r = inspect_file(str(tmp))
assert r.overall == "WARNING", f"脏数据应触发警告，实际 {r.overall}"
issues = {i.issue_type for i in r.issues}
assert "empty_rate" in issues
assert "non_numeric" in issues
assert "mixed_date" in issues
print(f"[PASS] B4 质量门：检出 {len(r.issues)} 项缺陷（空值/文本金额/日期混用），正确评级={r.overall}")

# 3) B5 匹配增强
from core.matching_engine import _rapidfuzz_weighted_score, _block_candidates, _route_by_confidence

# 加权评分
s = _rapidfuzz_weighted_score(
    {"amount": "50000", "date": "2026-01-01", "desc": "医保回款", "counterparty": "市中心医院"},
    {"amount": "50000", "date": "2026-01-02", "desc": "医保统筹回款", "counterparty": "市中心医院区部"},
    {"amount": 0.35, "date": 0.25, "desc": 0.20, "counterparty": 0.20})
assert s > 0.5, f"相似记录应中等以上分，实际 {s}"

# 置信度路由
matches = [{"confidence": 0.95, "bank_row": {}, "ledger_row": {}},
           {"confidence": 0.82, "bank_row": {}, "ledger_row": {}},
           {"confidence": 0.45, "bank_row": {}, "ledger_row": {}}]
routed = _route_by_confidence(matches)
assert len(routed["auto"]) == 1 and "待人工复核" in str(routed["review"])
print(f"[PASS] B5 置信度路由: auto={len(routed['auto'])} review={len(routed['review'])} exception={len(routed['exception'])}")

# 4) 意图澄清端点（静态符号检查）
src = (ROOT / "api/routes.py").read_text(encoding="utf-8")
assert "clarify_intent" in src and "@router.post(\"/runs/{run_id}/clarify\"" in src
assert "get_quality_report" in src and "@router.get(\"/runs/{run_id}/quality\"" in src
print("[PASS] B4/B5 API 端点符号就位（/quality + /clarify）")

import shutil; shutil.rmtree(tmp.parent, ignore_errors=True)
print("\nB4+B5 交付验证全部通过 ✅")
