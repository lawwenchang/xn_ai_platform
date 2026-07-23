# Fix: extend Load for docx/pdf
path = r"d:\Liu\ai_platform_code\phase2_v3\api\routes.py"
with open(path, "r", encoding="utf-8") as f:
    c = f.read()

# 1. Extend file matching to include docx/doc/pdf
old1 = "f.endswith(('.xlsx','.xls','.csv'))"
new1 = "f.endswith(('.xlsx','.xls','.csv','.docx','.doc','.pdf'))"
c = c.replace(old1, new1)

# 2. Add extension-based reading logic after file assignment
old2 = '''f\"if not os.path.exists(source_file):",
                f\"    print('[Load] 跳过: 文件不存在 ' + source_file)",
                f\"    {var_name} = pd.DataFrame()",'''
new2 = '''f\"if not os.path.exists(source_file):",
                f\"    print('[Load] 跳过: 文件不存在 ' + source_file)",
                f\"    {var_name} = pd.DataFrame()",
                f\"elif source_file.endswith(('.docx','.doc')):",
                f\"    _tables = _read_docx_tables(source_file)",
                f\"    if _tables:",
                f\"        {var_name} = _tables[0][1]",
                f\"        for _tn, _tdf in _tables[1:]:",
                f\"            _alias = f'df_{{op_alias}}_{{{_tn}}}'",
                f\"            locals()[_alias] = _tdf",
                f\"            print(f'[Load] docx表: {{{{_tn}}}} -> {{{{_alias}}}}, rows={{{{len(_tdf)}}}}')",
                f\"        print(f'[Load] docx表: Table_1 -> {{{var_name}}}, rows={{{{len({var_name})}}}}')",
                f\"    else:",
                f\"        _text = _read_docx_text(source_file)",
                f\"        {var_name} = pd.DataFrame({{{{'content': _text.split(chr(10))}}}})",
                f\"        print('[Load] docx文本: ' + os.path.basename(source_file) + f' -> {{{var_name}}}, rows={{{{len({var_name})}}}}')",
                f\"elif source_file.endswith('.pdf'):",
                f\"    _text = _read_pdf(source_file)",
                f\"    {var_name} = pd.DataFrame({{{{'content': _text.split(chr(10))}}}})",
                f\"    print('[Load] PDF: ' + os.path.basename(source_file) + f' -> {{{var_name}}}, rows={{{{len({var_name})}}}}')",'''

c = c.replace(old2, new2)

with open(path, "w", encoding="utf-8") as f:
    f.write(c)
print("Load extended:", ".docx" in c and "_read_docx_tables" in c)
