"""Restore part 2: essential operators, Diff columns_pairs, defense layer"""
import pathlib

path = pathlib.Path(r"d:\Liu\ai_platform_code\phase2_v3\api\routes.py")
c = path.read_text("utf-8")

# 7. _ensure_essential_operators call
c = c.replace(
    'blueprint_dict = dag_blueprint.to_dict() if hasattr(dag_blueprint, \"to_dict\") else dag_blueprint.__dict__\n        _get_snapshot_mgr().update_blueprint(run_id, blueprint_dict)',
    'blueprint_dict = dag_blueprint.to_dict() if hasattr(dag_blueprint, \"to_dict\") else dag_blueprint.__dict__\n        blueprint_dict = _ensure_essential_operators(blueprint_dict, user_intent)\n        _get_snapshot_mgr().update_blueprint(run_id, blueprint_dict)'
)

# 8. Diff columns_pairs
diff_old = 'f\"    {op_alias}_common = pd.merge({left_var}, {right_var}, how=\\'inner\\')\"'
diff_new = 'f\"    {op_alias}_common = pd.merge({left_var}, {right_var}, how=\\'inner\\', suffixes=(\\'_LEFT\\', \\'_RIGHT\\'))\"\n            ])\n            col_pairs = params.get(\"columns_pairs\", [])\n            if not col_pairs:\n                col_a = params.get(\"col_a\", \"金额\")\n                col_b = params.get(\"col_b\", \"金额\")\n                col_pairs = [(col_a, col_b)]\n            for ci, (c_a, c_b) in enumerate(col_pairs):\n                code_lines.append(f\"    if \\'{c_a}_LEFT\\' in {op_alias}_common.columns and \\'{c_b}_RIGHT\\' in {op_alias}_common.columns:\")\n                code_lines.append(f\"        {op_alias}_common[\\'差异_{c_a}\\'] = {op_alias}_common[\\'{c_a}_LEFT\\'] - {op_alias}_common[\\'{c_b}_RIGHT\\']\")\n            code_lines.extend(['
c = c.replace(diff_old, diff_new)

# 9. Defense layer
ret_old = '])\n\n    return \"\\n\".join(code_lines)'
ret_new = '])\n\n    code_lines.extend([\"\", \"# === 防御层：自动清洗脏数据 ===\", \"for _v in list(locals().values()):\", \"    if isinstance(_v, pd.DataFrame) and not _v.empty:\", \"        _v.dropna(how=\\'all\\', inplace=True)\", \"        _v.dropna(axis=1, how=\\'all\\', inplace=True)\", \"        _v.ffill(inplace=True)\", \"        for _c in _v.columns:\", \"            if _v[_c].dtype in (\\'float64\\', \\'int64\\'):\", \"                _v[_c].fillna(0, inplace=True)\", \"            else:\", \"                _v[_c].fillna(\\'\\', inplace=True)\", \"print(\\'[防御] 空值已清洗\\')\"])\n\n    return \"\\n\".join(code_lines)'
c = c.replace(ret_old, ret_new)

path.write_text(c, "utf-8")
print("Part 2 done.")
c2 = path.read_text("utf-8")
for label, kw in [("Token budget","_MAX_CATALOG_CHARS"),("API keys","DIFY_SINGLE_TABLE_KEY"),("Sandbox fb","await _execute_in_sandbox_legacy"),("Constraint chk","_run_constraint_check"),("Audit trace","_write_audit_trace"),("Hash chain","_append_approval_hash"),("Scene detect","is_single"),("Essential ops","_ensure_essential_operators"),("Diff cols","columns_pairs"),("Defense","防御层")]:
    print(f"  {label}: {'YES' if kw in c2 else 'NO'}")
