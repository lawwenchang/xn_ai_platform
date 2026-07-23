# 2. Add docx/pdf handling in Load operator
path = r"d:\Liu\ai_platform_code\phase2_v3\api\routes.py"
with open(path, "r", encoding="utf-8") as f:
    c = f.read()

old = 'var_name = f"df_{op_alias}" if not op_alias.startswith(\'df_\') else op_alias'
new = '''_ext = os.path.splitext(source)[1].lower()
            if _ext in ('.docx', '.doc'):
                var_name = f"df_{op_alias}" if not op_alias.startswith('df_') else op_alias
                code_lines.extend([
                    f"# [Load] docx: {source}",
                    f"_tables = _read_docx_tables(source_file)",
                    f"if _tables:",
                    f"    {var_name} = _tables[0][1]",
                    f"    print('[Load] docx表格: ' + os.path.basename(source_file) + ' -> {var_name}, rows=' + str(len({var_name})))",
                    f"else:",
                    f"    _text = _read_docx_text(source_file)",
                    f"    {var_name} = pd.DataFrame({{'content': _text.split(chr(10))}})",
                    f"    print('[Load] docx文本: ' + os.path.basename(source_file) + ' -> {var_name}, rows=' + str(len({var_name})))",
                ])
            elif _ext == '.pdf':
                var_name = f"df_{op_alias}" if not op_alias.startswith('df_') else op_alias
                code_lines.extend([
                    f"# [Load] PDF: {source}",
                    f"_text = _read_pdf(source_file)",
                    f"{var_name} = pd.DataFrame({{'content': _text.split(chr(10))}})",
                    f"print('[Load] PDF: ' + os.path.basename(source_file) + ' -> {var_name}, rows=' + str(len({var_name})))",
                ])
            else:
                var_name = f"df_{op_alias}" if not op_alias.startswith('df_') else op_alias'''

# Only replace if not already extended
if 'docx' not in c.split('var_name = f"df_{op_alias}"')[0]:
    c = c.replace(old, new)
    with open(path, "w", encoding="utf-8") as f:
        f.write(c)
    print("Load extended for docx/pdf")
else:
    print("Already extended")
