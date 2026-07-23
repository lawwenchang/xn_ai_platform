import re

with open(r"d:\Liu\ai_platform_code\phase2_v3\api\routes.py", "r", encoding="utf-8") as f:
    content = f.read()

# Old block: from "# LLM 提案路径" to the end of the except block
old_pattern = r"(                        # LLM 提案路径：生成命中率预览.+?                            except Exception as _pv:\n                                print\(f\"\[关键词预览\] 生成失败（非致命）: \{_pv\}\"\))"

match = re.search(old_pattern, content, re.DOTALL)
if match:
    old_text = match.group(1)
    new_text = '''                        # 加载流水文件做预览
                        preview = {}
                        try:
                            bank_files = [f for f in input_files if f.is_file()
                                          and f.suffix.lower() in (".xlsx", ".xls", ".csv")]
                            if bank_files and proposal.get("patterns"):
                                import pandas as _pd2
                                _sample = _pd2.read_excel(bank_files[0], nrows=5000) \
                                    if bank_files[0].suffix.lower() != ".csv" \
                                    else _pd2.read_csv(bank_files[0], nrows=5000,
                                                        encoding="utf-8-sig")
                                preview = _kw_backtest(
                                    proposal["patterns"],
                                    _sample,
                                    ["摘要", "对方客户名称", "附言", "用途"],
                                )
                                print(f"[关键词预览] 命中 {preview['hit_count']} 行 "
                                      f"({preview['hit_rate']:.1%})，"
                                      f"排除TOP: {list(preview.get('excluded_top', {}).keys())[:3]}")
                        except Exception as _pv:
                            print(f"[关键词预览] 生成失败（非致命）: {_pv}")

                        # 保存提案 + 挂起等用户确认
                        _kw_save_proposal(run_id, {**proposal, "preview": preview})
                        _get_snapshot_mgr().update_status(run_id, "PENDING_KEYWORD_CONFIRM")
                        logs.append(f"[关键词提案] 已生成候选词条，等待用户确认")
                        print(f"[关键词] Run {run_id} 挂起，状态=PENDING_KEYWORD_CONFIRM")
                        return  # 挂起，等用户确认'''
    content = content[:match.start(1)] + new_text + content[match.end(1):]
    with open(r"d:\Liu\ai_platform_code\phase2_v3\api\routes.py", "w", encoding="utf-8") as f:
        f.write(content)
    print("Replacement done!")
else:
    print("Pattern not found!")