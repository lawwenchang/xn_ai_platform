import pandas as pd
import json
import os

os.makedirs('outputs', exist_ok=True)

# === 文件追踪：按序分配 inputs 目录中的文件 ===
_inputs_dir = os.path.join(os.path.dirname(__file__), 'inputs')
_used_files = []

# === DAG 执行代码 ===
# 目标: 默认合并对账
# 原始意图: 无预设意图
# 算子数: 7


# Step: Load - Load
# 智能文件匹配
_dag_file = '文件1.xlsx'
_all_inputs = os.listdir(_inputs_dir) if os.path.exists(_inputs_dir) else []
_available = [f for f in _all_inputs if f not in _used_files and f.endswith(('.xlsx','.xls','.csv'))]
if _available:
    _pick = _available[0]
    source_file = os.path.join(_inputs_dir, _pick)
    _used_files.append(_pick)
    print('[Load] 自动分配: ' + _pick)
else:
    source_file = os.path.join(_inputs_dir, _dag_file) if os.path.exists(os.path.join(_inputs_dir, _dag_file)) else 'data/readonly/' + _dag_file
if not os.path.exists(source_file):
    print('[Load] 跳过: 文件不存在 ' + source_file)
    df_source1 = pd.DataFrame()
else:
    # 自动检测表头行
    df_source1 = None
    _best_score = -1
    _best_hr = 0
    for _hr in range(6):
        try:
            _tmp = pd.read_excel(source_file, header=_hr, nrows=0)
            _cols = list(_tmp.columns)
            _score = 0
            for _c in _cols:
                _cs = str(_c)
                if _cs.startswith('Unnamed'): _score -= 1
                elif len(_cs) > 2 and not any('\u4e00' <= ch <= '\u9fff' for ch in _cs): _score -= 1
                else: _score += 1
            # 加分：列数多的行更可能是真正的表头
            _score += len(_cols) * 0.5
            if _score > _best_score:
                _best_score = _score
                _best_hr = _hr
        except Exception:
            continue
    if _best_score > 0:
        df_source1 = pd.read_excel(source_file, header=_best_hr)
        print('[Load] ' + os.path.basename(source_file) + ' -> df_source1, h=' + str(_best_hr) + ', rows=' + str(len(df_source1)) + ', cols=' + str(list(df_source1.columns)))
    else:
        df_source1 = pd.read_excel(source_file, header=None)
        df_source1.columns = [f'Col_{i}' for i in range(len(df_source1.columns))]
        print('[Load] ' + os.path.basename(source_file) + ' -> df_source1 (无表头), rows=' + str(len(df_source1)))

# Step: Load - Load
# 智能文件匹配
_dag_file = '文件2.xlsx'
_all_inputs = os.listdir(_inputs_dir) if os.path.exists(_inputs_dir) else []
_available = [f for f in _all_inputs if f not in _used_files and f.endswith(('.xlsx','.xls','.csv'))]
if _available:
    _pick = _available[0]
    source_file = os.path.join(_inputs_dir, _pick)
    _used_files.append(_pick)
    print('[Load] 自动分配: ' + _pick)
else:
    source_file = os.path.join(_inputs_dir, _dag_file) if os.path.exists(os.path.join(_inputs_dir, _dag_file)) else 'data/readonly/' + _dag_file
if not os.path.exists(source_file):
    print('[Load] 跳过: 文件不存在 ' + source_file)
    df_source2 = pd.DataFrame()
else:
    # 自动检测表头行
    df_source2 = None
    _best_score = -1
    _best_hr = 0
    for _hr in range(6):
        try:
            _tmp = pd.read_excel(source_file, header=_hr, nrows=0)
            _cols = list(_tmp.columns)
            _score = 0
            for _c in _cols:
                _cs = str(_c)
                if _cs.startswith('Unnamed'): _score -= 1
                elif len(_cs) > 2 and not any('\u4e00' <= ch <= '\u9fff' for ch in _cs): _score -= 1
                else: _score += 1
            # 加分：列数多的行更可能是真正的表头
            _score += len(_cols) * 0.5
            if _score > _best_score:
                _best_score = _score
                _best_hr = _hr
        except Exception:
            continue
    if _best_score > 0:
        df_source2 = pd.read_excel(source_file, header=_best_hr)
        print('[Load] ' + os.path.basename(source_file) + ' -> df_source2, h=' + str(_best_hr) + ', rows=' + str(len(df_source2)) + ', cols=' + str(list(df_source2.columns)))
    else:
        df_source2 = pd.read_excel(source_file, header=None)
        df_source2.columns = [f'Col_{i}' for i in range(len(df_source2.columns))]
        print('[Load] ' + os.path.basename(source_file) + ' -> df_source2 (无表头), rows=' + str(len(df_source2)))

# Step: NoiseFilter - NoiseFilter
# [跳过] 未实现算子: NoiseFilter

# Step: NoiseFilter - NoiseFilter
# [跳过] 未实现算子: NoiseFilter

# Step: Merge - Merge
if 'df_source1' in dir() and 'df_source2' in dir() and not df_source1.empty and not df_source2.empty:
    _merge_candidates = []
    _left_cols = set(df_source1.columns)
    _right_cols = set(df_source2.columns)
    _common = [c for c in _merge_candidates if c in _left_cols and c in _right_cols]
    _missing = [c for c in _merge_candidates if c not in _left_cols or c not in _right_cols]
    if _missing:
        print('[Merge] 警告：以下列不存在于数据中，已自动跳过: ' + str(_missing))
    if _common:
        df_matched = pd.merge(df_source1, df_source2, on=_common, how='outer')
        print('[Merge] on=' + str(_common) + ', how=outer, rows=' + str(len(df_matched)))
    else:
        print('[Merge] 错误：没有公共列可合并！左表列: ' + str(list(_left_cols)) + ', 右表列: ' + str(list(_right_cols)))
        df_matched = pd.DataFrame()
else:
    df_matched = pd.DataFrame()

# Step: Diff - Diff
if 'df_source2' in dir() and 'df_matched' in dir():
    df_diff_only_left = df_source2[~df_source2.index.isin(df_matched.index)]
    df_diff_only_right = df_matched[~df_matched.index.isin(df_source2.index)]
    df_diff_common = pd.merge(df_source2, df_matched, how='inner', suffixes=('_LEFT', '_RIGHT'))
    if '交易金额_LEFT' in df_diff_common.columns
        df_diff_common['差异_交易金额'] = df_diff_common['交易金额_LEFT'] - df_diff_common['金额_RIGHT']
    print(f'[Diff] 差异列: {len(col_pairs)}')

# Step: Export - Export
if 'df_matched' in dir() and df_matched is not None and not df_matched.empty:
    df_matched.to_csv(os.path.join('outputs', 'analysis_result.csv'), index=False, encoding='utf-8-sig')
    print('[Export] ' + 'analysis_result.csv' + ', rows=' + str(len(df_matched)))
else:
    print('[Export] 跳过：数据为空，不导出空文件')

# 生成实际输出文件（基于计算结果，非硬编码）
_final_df = df_matched if 'df_matched' in dir() and df_matched is not None and not df_matched.empty else None
if _final_df is not None:
    # 导出 CSV
    _final_df.to_csv(os.path.join('outputs', 'analysis_result.csv'), index=False, encoding='utf-8-sig')
    print('[Output] CSV 已导出: analysis_result.csv, rows=' + str(len(_final_df)))
    # 同时导出 Excel（保留原表格式）
    try:
        _final_df.to_excel(os.path.join('outputs', 'analysis_result.xlsx'), index=False)
        print('[Output] Excel 已导出: analysis_result.xlsx')
    except Exception as _e:
        print('[Output] Excel 导出失败（可能缺少 openpyxl）: ' + str(_e))
    # 生成 JSON 摘要
    _summary = {
        'total_rows': len(_final_df),
        'columns': list(_final_df.columns),
        'dtypes': {str(k): str(v) for k, v in _final_df.dtypes.items()},
    }
    # 数值列统计
    _num_cols = _final_df.select_dtypes(include='number').columns.tolist()
    if _num_cols:
        _summary['numeric_summary'] = {c: {'sum': float(_final_df[c].sum()), 'mean': float(_final_df[c].mean()), 'max': float(_final_df[c].max()), 'min': float(_final_df[c].min())} for c in _num_cols}
    with open(os.path.join('outputs', 'journal_entries.json'), 'w', encoding='utf-8') as f:
        json.dump(_summary, f, ensure_ascii=False, indent=2)
    print('[Output] JSON 已导出: journal_entries.json')
else:
    print('[Output] 警告：无有效数据可导出')
    with open(os.path.join('outputs', 'journal_entries.json'), 'w', encoding='utf-8') as f:
        json.dump({'error': '无有效数据', 'columns': [], 'total_rows': 0}, f, ensure_ascii=False, indent=2)

# === 防御层：自动清洗脏数据 ===
for _v in list(locals().values()):
    if isinstance(_v, pd.DataFrame) and not _v.empty:
        _v.dropna(how='all', inplace=True)
        _v.dropna(axis=1, how='all', inplace=True)
        _v.ffill(inplace=True)
        for _c in _v.columns:
            if _v[_c].dtype in ('float64', 'int64'):
                _v[_c].fillna(0, inplace=True)
            else:
                _v[_c].fillna('', inplace=True)
print('[防御] 所有 DataFrame 空值已清洗')