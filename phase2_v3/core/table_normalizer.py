#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
表格形态探测与归一化引擎 (table_normalizer.py)
================================================
任意二维表格 → 标准契约格式 [期间, 主体, 金额, 方向]

核心理念：表格结构只有 7 种原子形态（可组合），不是无穷无尽的格式。
    1. 标准长表    一行一记录，列即字段
    2. 多Sheet同构  一年/月一sheet，sheet名即期间
    3. 交叉表       主体×期间横排，需 melt/unpivot
    4. 多级表头     两行表头/合并单元格，需拍平为单列名
    5. 段落式       每单位一个标题块，下面挂明细
    6. 合计混杂     小计/合计行嵌在明细行里
    7. 竖排转置     字段在行，记录在列

探测分流器 → 动作注册表 → 契约校验。新增格式只需加配置，不动代码。

使用方：chaos_input 之后、DAG 编译之前。
        银行对账引擎的使用方自行调用，本模块不改变银行对账逻辑。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

# ═══════════════════════════════════════════════════════════════
# 契约列名
# ═══════════════════════════════════════════════════════════════

CONTRACT_PERIOD = "期间"
CONTRACT_ENTITY = "主体"
CONTRACT_AMOUNT = "金额"
CONTRACT_DIRECTION = "方向"

# ═══════════════════════════════════════════════════════════════
# 合计行关键词
# ═══════════════════════════════════════════════════════════════

SUBTOTAL_KEYWORDS = {
    "合计", "小计", "总计", "累计", "合 计", "小 计", "总 计",
    "subtotal", "total", "sum", "grand total",
}




# ═══════════════════════════════════════════════════════════════
# 表头定位与清洗（前置步骤）
# ═══════════════════════════════════════════════════════════════

def _is_blank(val) -> bool:
    """判断单元格是否为空：NaN、None、空字符串、纯空格。"""
    if val is None:
        return True
    try:
        if pd.isna(val):
            return True
    except Exception:
        pass
    s = str(val).strip()
    return s == "" or s.lower() == "nan"


def _row_blank_ratio(row) -> float:
    """一行中空白单元格的占比（包括纯空格串）。"""
    if len(row) == 0:
        return 1.0
    blanks = sum(1 for v in row if _is_blank(v))
    return blanks / len(row)


def _is_potential_header_row(row, col_count: int) -> bool:
    """判断一行是否像表头：非空值多、值多样、不含明显的合计/数值模式。"""
    non_blank = sum(1 for v in row if not _is_blank(v))
    if non_blank < max(col_count * 0.3, 2):
        return False  # 太稀疏，不是表头
    # 检查是否为纯数据行（全是数字）
    numeric_count = 0
    for v in row:
        if _is_blank(v):
            continue
        try:
            float(str(v).replace(",", "").replace("，", ""))
            numeric_count += 1
        except ValueError:
            pass
    if numeric_count >= non_blank * 0.7:
        return False  # 太像数据行
    # 检查是否含合计关键词
    for v in row:
        s = str(v).strip()
        if any(kw in s for kw in SUBTOTAL_KEYWORDS):
            return False
    return True


def find_header_row(df: pd.DataFrame, max_scan: int = 20) -> int:
    """在 DataFrame 中定位真正的表头行。
    
    策略：从前 max_scan 行中找最像表头的那一行。打分依据：
    - 非空率高
    - 值多样性高（表头每列不同，数据行有重复）
    - 不含明显的数字模式或合计关键词
    
    返回 0-based 行号。如果找不到，返回 0（假定第一行就是表头）。
    """
    if df.empty:
        return 0
    col_count = len(df.columns)
    scan_rows = min(len(df), max_scan)
    best_row, best_score = 0, 0
    for i in range(scan_rows):
        row = df.iloc[i]
        if not _is_potential_header_row(row, col_count):
            continue
        # 打分：非空率 + 唯一值率
        non_blank = sum(1 for v in row if not _is_blank(v))
        unique_vals = len({str(v).strip() for v in row if not _is_blank(v)})
        score = non_blank * 2 + unique_vals
        if score > best_score:
            best_score = score
            best_row = i
    return best_row


def clean_dataframe(
    df: pd.DataFrame,
    auto_header: bool = True,
    strip_blank_rows: bool = True,
    max_header_scan: int = 20,
) -> pd.DataFrame:
    """清洗 DataFrame：自动定位表头、丢弃表头上方的垃圾行、去除全空行。
    
    Args:
        df: 原始 DataFrame
        auto_header: True = 自动探测表头位置
        strip_blank_rows: True = 丢弃全空行（所有单元格都是空白）
        max_header_scan: 表头探测扫描行数上限
    
    Returns:
        清洗后的 DataFrame（表头已正确设定，列名来自探测到的表头行）
    """
    if df.empty:
        return df
    result = df.copy()
    # 1) 全空行置 NA（把 "   " 这类纯空格转成真正的 NaN）
    result = result.replace(r"^\s*$", None, regex=True)
    # 2) 自动定位表头
    if auto_header:
        header_idx = find_header_row(result, max_header_scan)
        if header_idx > 0:
            # 提取表头行 → 设列名 → 丢弃表头上方所有行
            new_cols = [str(v).strip() if not _is_blank(v) else f"Col_{i}"
                        for i, v in enumerate(result.iloc[header_idx])]
            # 去重后缀
            seen: Dict[str, int] = {}
            final_cols = []
            for c in new_cols:
                if c in seen:
                    seen[c] += 1
                    final_cols.append(f"{c}_{seen[c]}")
                else:
                    seen[c] = 1
                    final_cols.append(c)
            result = result.iloc[header_idx + 1:].copy()
            result.columns = final_cols
            result = result.reset_index(drop=True)
        # else: header_idx==0，表头已在第一行，无需调整
    # 3) 丢弃全空行
    if strip_blank_rows:
        blank_mask = result.apply(lambda row: _row_blank_ratio(row) >= 0.9, axis=1)
        result = result[~blank_mask].copy()
        result = result.reset_index(drop=True)
    return result

# ═══════════════════════════════════════════════════════════════
# 探测分流器：detect_table_shape
# ═══════════════════════════════════════════════════════════════

def detect_table_shape(df: pd.DataFrame, filename: str = "") -> Dict[str, bool]:
    """一次扫描判定所有形态特征。"""
    shapes = {
        "standard": True, "multi_sheet": False, "cross_table": False,
        "multi_header": False, "paragraph": False,
        "with_subtotals": False, "transposed": False,
    }
    if df.empty or len(df.columns) == 0:
        return shapes

    cols = [str(c) for c in df.columns]
    col_count = len(cols)
    row_count = len(df)

    # ── 探针 1：竖排转置 ──
    if col_count <= 3 and row_count >= max(col_count * 2, 4):
        first_col_vals = df.iloc[:, 0].dropna().astype(str).head(20).tolist()
        field_score = sum(1 for v in first_col_vals
                          if any(kw in v for kw in ["日期", "金额", "名称", "摘要",
                                                     "凭证", "科目", "账号", "余额"]))
        if field_score >= 2:
            shapes["transposed"] = True
            shapes["standard"] = False
            return shapes

    # ── 探针 2：多级表头 ──
    unnamed_count = sum(1 for c in cols if "unnamed" in c.lower()
                        or str(c).strip() == "" or str(c) == "nan")
    if unnamed_count >= len(cols) * 0.15:
        shapes["multi_header"] = True
        shapes["standard"] = False

    # ── 探针 3：交叉表 ──
    numeric_col_count, date_col_count = 0, 0
    for c in cols:
        cs = str(c)
        if cs.replace(".", "").replace("-", "").isdigit():
            numeric_col_count += 1
        elif re.match(r"^\d{4}[-/年]\d{1,2}", cs) or re.match(r"^\d{1,2}月$", cs) or re.match(r"^\d{4}年$", cs):
            date_col_count += 1
    if (numeric_col_count + date_col_count) >= 3 and col_count >= 4:
        text_cols = [c for c in cols if not (
            str(c).replace(".", "").replace("-", "").isdigit()
            or re.match(r"^\d{4}[-/年]\d{1,2}", str(c))
            or re.match(r"^\d{1,2}月$", str(c))
            or re.match(r"^\d{4}年$", str(c))
        )]
        if text_cols:
            shapes["cross_table"] = True
            shapes["standard"] = False

    # ── 探针 4：段落式 ──
    if col_count >= 2:
        first_col = df.iloc[:, 0]
        null_count = sum(1 for v in first_col if _is_blank(v))
        null_ratio = null_count / max(len(first_col), 1)
        if 0.05 < null_ratio <= 0.65:
            shapes["paragraph"] = True
            shapes["standard"] = False

    # ── 探针 5：合计混杂 ──
    subtotal_hits = 0
    for idx, row in df.head(min(len(df), 200)).iterrows():
        for val in row:
            s = str(val).strip().lower()
            if s in SUBTOTAL_KEYWORDS or any(kw in s for kw in ["合计", "小计", "总计"]):
                subtotal_hits += 1
                break
    if subtotal_hits >= 1:
        shapes["with_subtotals"] = True

    has_transform = any([
        shapes["transposed"], shapes["multi_header"],
        shapes["cross_table"], shapes["paragraph"],
    ])
    shapes["standard"] = not has_transform
    return shapes



# ═══════════════════════════════════════════════════════════════
# 动作注册表：表形 → 归一化操作序列
# ═══════════════════════════════════════════════════════════════

SHAPE_PIPELINES: Dict[str, List[str]] = {
    "standard":       [],
    "multi_header":   ["flatten_header"],
    "cross_table":    ["melt_cross_table"],
    "paragraph":      ["split_paragraphs"],
    "with_subtotals": ["strip_subtotals"],
    "transposed":     ["transpose_table"],
    # 组合
    "cross_table+with_subtotals":              ["strip_subtotals", "melt_cross_table"],
    "multi_header+cross_table":                ["flatten_header", "melt_cross_table"],
    "multi_header+with_subtotals":             ["flatten_header", "strip_subtotals"],
    "paragraph+with_subtotals":                ["split_paragraphs", "strip_subtotals"],
    "cross_table+multi_header+with_subtotals": ["flatten_header", "strip_subtotals", "melt_cross_table"],
}


# ═══════════════════════════════════════════════════════════════
# 原子动作实现
# ═══════════════════════════════════════════════════════════════

def flatten_header(df: pd.DataFrame) -> pd.DataFrame:
    """拍平多级表头：合并前两行非空列名。"""
    if len(df) < 2:
        return df
    first_row = df.iloc[0].fillna("").astype(str).tolist()
    second_row = df.iloc[1].fillna("").astype(str).tolist()
    null_count = sum(1 for v in df.iloc[0] if _is_blank(v))
    null_ratio = null_count / max(len(df.columns), 1)
    if null_ratio < 0.1:
        return df
    new_cols = []
    prev_filled = ""
    for i, (r1, r2) in enumerate(zip(first_row, second_row)):
        r1c = r1.strip() if r1 and r1 != "nan" else ""
        r2c = r2.strip() if r2 and r2 != "nan" else ""
        if r1c:
            prev_filled = r1c
        if not r1c and not r2c:
            new_cols.append(str(df.columns[i]))
        elif r1c and r2c:
            new_cols.append(f"{r1c}_{r2c}")
        elif r1c:
            new_cols.append(r1c)
        else:
            new_cols.append(f"{prev_filled}_{r2c}" if prev_filled else r2c)
    seen: Dict[str, int] = {}
    final_cols = []
    for c in new_cols:
        if c in seen:
            seen[c] += 1
            final_cols.append(f"{c}_{seen[c]}")
        else:
            seen[c] = 1
            final_cols.append(c)
    df = df.iloc[2:].copy()
    df.columns = final_cols
    df = df.reset_index(drop=True)
    return df


def melt_cross_table(df: pd.DataFrame) -> pd.DataFrame:
    """交叉表→长表：数字/日期列 melt 为 [期间, 金额]。"""
    cols = [str(c) for c in df.columns]
    id_cols, value_cols = [], []
    for c in cols:
        cs = str(c)
        if (cs.replace(".", "").replace("-", "").isdigit()
                or re.match(r"^\d{4}[-/年]\d{1,2}", cs)
                or re.match(r"^\d{1,2}月$", cs) or re.match(r"^\d{4}年$", cs)):
            value_cols.append(c)
        else:
            id_cols.append(c)
    if not id_cols:
        id_cols = cols[:min(2, len(cols))]
        value_cols = [c for c in cols if c not in id_cols]
    if not value_cols:
        return df
    df_melted = pd.melt(df, id_vars=id_cols, value_vars=value_cols,
                        var_name=CONTRACT_PERIOD, value_name=CONTRACT_AMOUNT)
    if id_cols:
        df_melted = df_melted.rename(columns={id_cols[0]: CONTRACT_ENTITY})
    return df_melted


def split_paragraphs(df: pd.DataFrame) -> pd.DataFrame:
    """段落式拆解：标题块单位名 → '主体'列。"""
    if df.empty or len(df.columns) < 2:
        return df
    first_col = df.columns[0]
    other_cols = [c for c in df.columns if c != first_col]
    title_mask = ~df[first_col].apply(_is_blank)
    if other_cols:
        other_null = df[other_cols].isna().sum(axis=1)
        title_mask = title_mask & (other_null >= len(other_cols) * 0.6)
    entity_values = df[first_col].where(title_mask, None)
    entity_values = entity_values.fillna(method="ffill")
    df = df[~title_mask].copy()
    df[CONTRACT_ENTITY] = entity_values[~title_mask].values
    df = df.drop(columns=[first_col], errors="ignore")
    df = df.reset_index(drop=True)
    return df


def strip_subtotals(df: pd.DataFrame) -> pd.DataFrame:
    """剥离合计行：识别并移除小计/合计。"""
    if df.empty:
        return df
    first_col = df.columns[0]
    keyword_mask = df[first_col].astype(str).apply(
        lambda x: any(kw in str(x).strip() for kw in SUBTOTAL_KEYWORDS)
    )
    sparse_mask = df.apply(lambda row: sum(1 for v in row if not _is_blank(v)) <= 2, axis=1)
    subtotal_mask = keyword_mask | (sparse_mask & keyword_mask)
    df_clean = df[~subtotal_mask].copy()
    df_clean = df_clean.reset_index(drop=True)
    return df_clean


def transpose_table(df: pd.DataFrame) -> pd.DataFrame:
    """竖排转置：字段在行、记录在列 → 标准长表。"""
    if df.empty or len(df.columns) < 2:
        return df
    df_t = df.set_index(df.columns[0]).T
    df_t = df_t.reset_index(drop=True)
    df_t.columns = [str(c).strip() for c in df_t.columns]
    return df_t


_ACTION_REGISTRY = {
    "flatten_header": flatten_header,
    "melt_cross_table": melt_cross_table,
    "split_paragraphs": split_paragraphs,
    "strip_subtotals": strip_subtotals,
    "transpose_table": transpose_table,
}


def _pipeline_key(shapes: Dict[str, bool]) -> str:
    active = []
    if shapes.get("multi_header"): active.append("multi_header")
    if shapes.get("cross_table"):  active.append("cross_table")
    if shapes.get("paragraph"):    active.append("paragraph")
    if shapes.get("with_subtotals"): active.append("with_subtotals")
    if shapes.get("transposed"):   active.append("transposed")
    return "+".join(active) if active else "standard"


# ═══════════════════════════════════════════════════════════════
# 主入口：normalize_to_contract
# ═══════════════════════════════════════════════════════════════

def normalize_to_contract(
    df: pd.DataFrame,
    filename: str = "",
    sheet_name: Optional[str] = None,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """任意表形 → 标准契约 [期间, 主体, 金额, 方向]。
    
    Returns:
        (normalized_df, meta):
            - meta["shapes"]: 探测到的形态
            - meta["pipeline"]: 执行的原子动作序列
            - meta["row_before"] / meta["row_after"]: 归一化前后行数
    """
    meta: Dict[str, Any] = {
        "filename": filename, "sheet_name": sheet_name,
        "row_before": len(df), "col_before": len(df.columns),
    }

    # Step 0: 清洗——自动定位表头、丢弃垃圾行、空格转空值
    df_clean = clean_dataframe(df, auto_header=True, strip_blank_rows=True)
    meta["cleaned"] = (len(df_clean) != len(df) or list(df_clean.columns) != list(df.columns))
    if meta["cleaned"]:
        meta["header_row_found"] = find_header_row(df) if len(df) > 0 else 0
        meta["rows_dropped"] = len(df) - len(df_clean)

    # Step 1: 探测形态
    shapes = detect_table_shape(df_clean, filename)
    meta["shapes"] = shapes
    pipe_key = _pipeline_key(shapes)
    pipeline = SHAPE_PIPELINES.get(pipe_key, [])
    meta["pipeline_key"] = pipe_key
    meta["pipeline"] = pipeline

    result = df_clean.copy()
    for action_name in pipeline:
        action = _ACTION_REGISTRY.get(action_name)
        if action is None:
            print(f"[TableNormalizer] 未知动作 '{action_name}'，跳过")
            continue
        try:
            result = action(result)
            meta[f"after_{action_name}_rows"] = len(result)
        except Exception as e:
            print(f"[TableNormalizer] 动作 '{action_name}' 失败: {e}，保留当前结果")
            meta[f"error_{action_name}"] = str(e)

    if shapes.get("multi_sheet") and sheet_name:
        result[CONTRACT_PERIOD] = sheet_name

    meta["row_after"] = len(result)
    meta["col_after"] = len(result.columns)
    return result, meta


# ═══════════════════════════════════════════════════════════════
# 契约校验器：validate_contract
# ═══════════════════════════════════════════════════════════════

def validate_contract(df: pd.DataFrame) -> Tuple[bool, List[str]]:
    """校验归一化结果是否符合标准契约。
    
    Returns:
        (passed, issues): passed 为 True 表示校验通过
    """
    issues: List[str] = []
    if df.empty:
        return False, ["数据为空"]

    cols = [str(c) for c in df.columns]

    # 1) 金额列可解析
    amount_col = None
    for c in cols:
        if c in (CONTRACT_AMOUNT, "金额(元)", "金额（元）", "交易金额", "发生额"):
            amount_col = c
            break
    if amount_col is None:
        for c in cols:
            try:
                if pd.api.types.is_numeric_dtype(df[c]):
                    amount_col = c
                    break
            except Exception:
                pass
    if amount_col is None:
        issues.append("未找到可解析的金额列")
    else:
        try:
            num = pd.to_numeric(df[amount_col], errors="coerce")
            nr = num.isna().sum() / max(len(num), 1)
            if nr > 0.3:
                issues.append(f"金额列 '{amount_col}' 无法解析率 {nr:.0%}")
        except Exception as e:
            issues.append(f"金额列 '{amount_col}' 转换失败: {e}")

    # 2) 期间/日期列
    period_candidates = [c for c in cols if any(
        kw in c for kw in [CONTRACT_PERIOD, "日期", "月份", "年度", "期间", "date", "period"]
    )]
    if not period_candidates:
        issues.append("未找到期间/日期列")

    # 3) 无合计行残留
    if amount_col and len(df) > 0:
        first_col = df.columns[0]
        for idx, val in enumerate(df[first_col].astype(str).head(20)):
            if any(kw in str(val).strip() for kw in SUBTOTAL_KEYWORDS):
                issues.append(f"疑似合计行残留（第{idx+1}行: {str(val)[:30]}）")
                break

    # 4) 数据行数合理
    if len(df) < 2:
        issues.append(f"仅 {len(df)} 行，可能归一化异常")

    # 5) 主体列无全空
    entity_col = None
    for c in cols:
        if c in (CONTRACT_ENTITY, "单位", "机构名称", "客户名称", "部门"):
            entity_col = c
            break
    if entity_col is None:
        for c in cols:
            if c != amount_col:
                try:
                    if not pd.api.types.is_numeric_dtype(df[c]):
                        entity_col = c
                        break
                except Exception:
                    pass
    if entity_col:
        nr = df[entity_col].isna().sum() / max(len(df), 1)
        if nr > 0.5:
            issues.append(f"主体列 '{entity_col}' 空值率 {nr:.0%}，段落拆解可能不完整")

    passed = len(issues) == 0
    if not passed:
        print(f"[TableNormalizer] 契约校验未通过 ({len(issues)} 项):")
        for issue in issues:
            print(f"  - {issue}")
    return passed, issues


# ═══════════════════════════════════════════════════════════════
# 便捷入口：一键归一化 + 校验
# ═══════════════════════════════════════════════════════════════

def normalize_and_validate(
    df: pd.DataFrame,
    filename: str = "",
    sheet_name: Optional[str] = None,
    raise_on_failure: bool = False,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """一键归一化 → 校验。
    
    校验失败时默认降级返回原始 df + 错误元信息（不阻断主流程）。
    raise_on_failure=True 时抛异常（用于测试）。
    """
    normalized, meta = normalize_to_contract(df, filename, sheet_name)
    passed, issues = validate_contract(normalized)
    meta["contract_valid"] = passed
    meta["contract_issues"] = issues

    if not passed:
        if raise_on_failure:
            raise ValueError(
                f"表格 '{filename}' 契约校验失败: {'; '.join(issues)}"
            )
        print(f"[TableNormalizer] '{filename}' 校验未通过，降级返回原表。"
              f"问题: {'; '.join(issues)}")
        return df, meta

    return normalized, meta

