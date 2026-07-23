p = __import__("pathlib").Path(r"d:\Liu\ai_platform_code\phase2_v3\api\routes.py")
c = p.read_text("utf-8")
old = '    return \"\\n\".join(code_lines)'
new = '    code_lines.extend([\"\", \"# === 防御层：自动清洗脏数据 ===\", \"for _v in list(locals().values()):\", \"    if isinstance(_v, pd.DataFrame) and not _v.empty:\", \"        _v.dropna(how=\\'all\\', inplace=True)\", \"        _v.dropna(axis=1, how=\\'all\\', inplace=True)\", \"        _v.ffill(inplace=True)\", \"        for _c in _v.columns:\", \"            if _v[_c].dtype in (\\'float64\\', \\'int64\\'):\", \"                _v[_c].fillna(0, inplace=True)\", \"            else:\", \"                _v[_c].fillna(\\'\\', inplace=True)\", \"print(\\'[防御] 所有 DataFrame 空值已清洗\\')\"])\n\n    return \"\\n\".join(code_lines)'
c = c.replace(old, new)
p.write_text(c, "utf-8")
print("Defense:", "防御层" in p.read_text("utf-8"))
