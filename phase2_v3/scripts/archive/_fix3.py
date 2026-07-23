p = __import__("pathlib").Path(r"d:\Liu\ai_platform_code\phase2_v3\api\routes.py")
c = p.read_text("utf-8")

# Diff multi-column
old = "f\"    {op_alias}_common = pd.merge({left_var}, {right_var}, how='inner')\",\n            ])"
new = """f"    {op_alias}_common = pd.merge({left_var}, {right_var}, how='inner', suffixes=('_LEFT', '_RIGHT'))",
            ])
            col_pairs = params.get("columns_pairs", [])
            if not col_pairs:
                col_a = params.get("col_a", "金额")
                col_b = params.get("col_b", "金额")
                col_pairs = [(col_a, col_b)]
            for ci, (c_a, c_b) in enumerate(col_pairs):
                code_lines.append(f"    if '{c_a}_LEFT' in {op_alias}_common.columns")
                code_lines.append(f"        {op_alias}_common['差异_{c_a}'] = {op_alias}_common['{c_a}_LEFT'] - {op_alias}_common['{c_b}_RIGHT']")
            code_lines.append(f"    print(f'[Diff] 差异列: {{len(col_pairs)}}')")"""
c = c.replace(old, new)
p.write_text(c, "utf-8")
print("Diff:", "columns_pairs" in p.read_text("utf-8"))
