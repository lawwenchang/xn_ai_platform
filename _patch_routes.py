path = r'd:\Liu\ai_platform_code\phase2_v3\api\routes.py'
with open(path, 'r', encoding='utf-8') as f:
    c = f.read()

old_start = c.find('# LLM \u63d0\u6848\u8def\u5f84\uff1a\u751f\u6210\u547d\u4e2d\u7387\u9884\u89c8')
# Find end of the except block (3 \n after the last print)
marker = 'print(f\"[\u5173\u952e\u8bcd\u9884\u89c8] \u751f\u6210\u5931\u8d25'
old_end = c.find(marker, old_start)
old_end = c.find('\n', old_end) + 1  # end of print line
old_end = c.find('\n', old_end) + 1  # blank line
print(f'Block from {old_start} to {old_end}')

new_text = '''                        # \u52a0\u8f7d\u6d41\u6c34\u6587\u4ef6\u505a\u9884\u89c8
                        preview = {}
                        try:
                            bank_files = [f for f in input_files if f.is_file()
                                          and f.suffix.lower() in (".xlsx", ".xls", ".csv")]
                            if bank_files and proposal.get("patterns"):
                                import pandas as _pd2
                                _sample = _pd2.read_excel(bank_files[0], nrows=5000) \\
                                    if bank_files[0].suffix.lower() != ".csv" \\
                                    else _pd2.read_csv(bank_files[0], nrows=5000,
                                                        encoding="utf-8-sig")
                                preview = _kw_backtest(
                                    proposal["patterns"],
                                    _sample,
                                    ["\u6458\u8981", "\u5bf9\u65b9\u5ba2\u6237\u540d\u79f0", "\u9644\u8a00", "\u7528\u9014"],
                                )
                                print(f"[\u5173\u952e\u8bcd\u9884\u89c8] \u547d\u4e2d {preview['hit_count']} \u884c "
                                      f"({preview['hit_rate']:.1%})\uff0c"
                                      f"\u6392\u9664TOP: {list(preview.get('excluded_top', {}).keys())[:3]}")
                        except Exception as _pv:
                            print(f"[\u5173\u952e\u8bcd\u9884\u89c8] \u751f\u6210\u5931\u8d25\uff08\u975e\u81f4\u547d\uff09: {_pv}")

                        # \u4fdd\u5b58\u63d0\u6848 + \u6302\u8d77\u7b49\u7528\u6237\u786e\u8ba4
                        _kw_save_proposal(run_id, {**proposal, "preview": preview})
                        _get_snapshot_mgr().update_status(run_id, "PENDING_KEYWORD_CONFIRM")
                        logs.append(f"[\u5173\u952e\u8bcd\u63d0\u6848] \u5df2\u751f\u6210\u5019\u9009\u8bcd\u6761\uff0c\u7b49\u5f85\u7528\u6237\u786e\u8ba4")
                        print(f"[\u5173\u952e\u8bcd] Run {run_id} \u6302\u8d77\uff0c\u72b6\u6001=PENDING_KEYWORD_CONFIRM")
                        return  # \u2190 \u6302\u8d77\uff0c\u7b49\u7528\u6237\u786e\u8ba4
'''

c = c[:old_start] + new_text + c[old_end:]
with open(path, 'w', encoding='utf-8') as f:
    f.write(c)
print('Done!')
