#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""matching_engine.py 补丁 B：_block_candidates 三修复/噪音词/兜底/文档输入"""
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "core" / "matching_engine.py"
src = P.read_text(encoding="utf-8").replace("\r\n", "\n")


def rep(old: str, new: str, tag: str):
    global src
    assert src.count(old) == 1, f"[{tag}] 命中 {src.count(old)} 次而非 1 次"
    src = src.replace(old, new)
    print(f"  [PATCH] {tag}")


# ── 5. _block_candidates：分桶 bug + 集合 bug + 静默截断 ───────
rep('''    from collections import defaultdict
    amount_buckets = defaultdict(list)
    for i, r in enumerate(ledger_rows):
        amt = _normalize_amount(r.get("amount", 0))
        if amt > 0:
            key = int(amt / max(1, amt * amount_pct))
            amount_buckets[key].append((i, r))
    candidates = []
    for bi, br in enumerate(bank_rows):
        b_amt = _normalize_amount(br.get("amount", 0))
        if b_amt <= 0:
            continue
        key = int(b_amt / max(1, b_amt * amount_pct))
        nearby = {key - 1: key + 1}
        for nk in nearby:
            for li, lr in amount_buckets.get(nk, []):
                candidates.append((bi, li, br, lr))
    return candidates or [(i, j, bank_rows[i], ledger_rows[j])
                          for i in range(min(500, len(bank_rows)))
                          for j in range(min(500, len(ledger_rows)))]''',
    '''    from collections import defaultdict
    import statistics
    # 修复1：分桶键原写法 int(amt/max(1, amt*pct)) 对任意 amt>10 恒等于 10，
    # 所有记录挤在同一个桶里。改为固定桶宽（金额中位数×容差，下限 1 元）。
    amts = [_normalize_amount(r.get("amount", 0)) for r in ledger_rows]
    pos = [a for a in amts if a > 0]
    width = max(1.0, (statistics.median(pos) if pos else 1.0) * amount_pct)
    amount_buckets = defaultdict(list)
    for i, r in enumerate(ledger_rows):
        amt = _normalize_amount(r.get("amount", 0))
        if amt > 0:
            amount_buckets[int(amt // width)].append((i, r))
    candidates = []
    for bi, br in enumerate(bank_rows):
        b_amt = _normalize_amount(br.get("amount", 0))
        if b_amt <= 0:
            continue
        key = int(b_amt // width)
        # 修复2：{key-1: key+1} 是字典不是集合，迭代只得 key-1 一个桶
        for nk in {key - 1, key, key + 1}:
            for li, lr in amount_buckets.get(nk, []):
                candidates.append((bi, li, br, lr))
    if candidates:
        return candidates
    # 修复3：兜底 500×500 静默截断 → 显式告警 + 上限内全量笛卡尔积
    total = len(bank_rows) * len(ledger_rows)
    hard_cap = 200_000
    if total > hard_cap:
        print(f"[Blocking] ⚠ 兜底候选对 {total:,} 超过上限 {hard_cap:,}，"
              f"已分块截断（可能遗漏匹配，请缩小数据范围或检查分桶）")
    pairs = []
    for i in range(len(bank_rows)):
        for j in range(len(ledger_rows)):
            pairs.append((i, j, bank_rows[i], ledger_rows[j]))
            if len(pairs) >= hard_cap:
                return pairs
    return pairs''',
    "_block_candidates 三修复")

# ── 6. 噪音词表：利息/冲正移出（单独成类，不删除） ─────────────
rep('''# 噪音费用词表（与 AUDIT_DOMAIN_KNOWLEDGE 噪音排除口径一致）
NOISE_FEE_WORDS = ("手续费", "短信费", "年费", "账户管理费", "工本费",
                   "服务费", "冲正", "测试", "利息")''',
    '''# 噪音费用词表（仅影响"是否参与逐笔匹配"，不做删除）
# 注意：利息、冲正已从噪音中移出——准则要求关注存款收益与规模匹配性
# （问题解答第12号），冲正重做是典型舞弊手法，二者单独成类输出。
NOISE_FEE_WORDS = ("手续费", "短信费", "年费", "账户管理费", "工本费",
                   "服务费", "测试")
INTEREST_WORDS = ("利息", "结息")
REVERSAL_WORDS = ("冲正", "冲销", "红冲", "撤销")''',
    "噪音词表移除利息/冲正")

# ── 7. 异常分类：规则先行扩充 + 兜底改"待人工核查" ─────────────
rep('''    row_text = (json.dumps(bank_row, ensure_ascii=False)
                + json.dumps(ledger_row, ensure_ascii=False))
    if any(w in row_text for w in NOISE_FEE_WORDS):
        return "噪音费用"''',
    '''    row_text = (json.dumps(bank_row, ensure_ascii=False)
                + json.dumps(ledger_row, ensure_ascii=False))
    if any(w in row_text for w in INTEREST_WORDS):
        return "利息收支（需与存款规模匹配性分析）"
    if any(w in row_text for w in REVERSAL_WORDS):
        return "冲正/重做交易（需关注业务合理性）"
    if any(w in row_text for w in NOISE_FEE_WORDS):
        return "噪音费用"''',
    "异常分类利息/冲正单独成类")

rep('''    except Exception:
        pass
    return "未达账项"  # 离线兜底''',
    '''    except Exception:
        pass
    return "待人工核查"  # 离线兜底：解释不了的一律待查，禁止默认洗白成未达账项''',
    "离线兜底改待人工核查")

# ── 8. run_matching_pipeline：支持文档格式输入 ─────────────────
rep('''    excel_files = [f for f in input_dir.glob("*") if f.suffix.lower() in (".xlsx", ".xls", ".csv")]''',
    '''    excel_files = [f for f in input_dir.glob("*")
                   if f.suffix.lower() in (".xlsx", ".xls", ".csv",
                                           ".docx", ".doc", ".pdf", ".md", ".txt")]''',
    "匹配流水线接受文档格式")

rep('''        try:
            if f.suffix.lower() == ".csv":
                df = pd.read_csv(f, encoding="utf-8-sig")
            else:
                df = _read_excel_auto_header(f)
            dfs[f.name] = df
            ftypes[f.name] = detect_file_type(df, f.name)
        except Exception as e:
            print(f"[警告] 无法读取 {f.name}: {e}")''',
    '''        try:
            if f.suffix.lower() in (".xlsx", ".xls", ".csv"):
                if f.suffix.lower() == ".csv":
                    df = pd.read_csv(f, encoding="utf-8-sig")
                else:
                    df = _read_excel_auto_header(f)
            else:
                # 文档格式（docx/doc/pdf/md/txt）：经统一文档加载器提取表格
                from core.document_loader import load_tables
                tables = load_tables(f)
                if not tables:
                    raise ValueError("文档中未提取到表格")
                df = tables[0]
            dfs[f.name] = df
            ftypes[f.name] = detect_file_type(df, f.name)
        except Exception as e:
            print(f"[警告] 无法读取 {f.name}: {e}")''',
    "匹配流水线文档表格提取")

P.write_text(src, encoding="utf-8", newline="\n")
import ast
ast.parse(src)
print("matching_engine 补丁B 完成，AST OK")
