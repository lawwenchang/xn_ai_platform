#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""routes.py 补丁 E3：场景知识包注入编译 + 快车道明细级闸门 + 场景算子强制"""
from pathlib import Path

P = Path(__file__).resolve().parent.parent / "api" / "routes.py"
src = P.read_text(encoding="utf-8").replace("\r\n", "\n")


def rep(old: str, new: str, tag: str):
    global src
    assert src.count(old) == 1, f"[{tag}] 命中 {src.count(old)} 次而非 1 次"
    src = src.replace(old, new)
    print(f"  [PATCH] {tag}")


# ── 1. 编译链路：场景知识包检查单注入 ──────────────────────────
rep('''    from config.few_shot_examples import build_dynamic_few_shot
    few_shot_text = build_dynamic_few_shot(user_intent, max_examples=3)
    catalog_text = few_shot_text + "\\n\\n" + catalog_text''',
    '''    from config.few_shot_examples import build_dynamic_few_shot
    few_shot_text = build_dynamic_few_shot(user_intent, max_examples=3)
    catalog_text = few_shot_text + "\\n\\n" + catalog_text

    # 🎯 场景知识包：规划检查单注入（RAG 知识 → 规划约束，覆盖逐笔/汇总/提取式等场景）
    try:
        from config.scenario_packs import detect_scenario as _detect_scn, build_scenario_prompt
        _scn = _detect_scn(user_intent or "")
        _scn_prompt = build_scenario_prompt(_scn)
        if _scn_prompt:
            catalog_text = _scn_prompt + "\\n\\n" + catalog_text
            print(f"[编译] 场景知识包: {_scn}")
    except Exception as e:
        print(f"[编译] 场景知识包降级（非致命）: {e}")''',
    "场景知识包注入编译")

# ── 2. 快车道：汇总级台账不走逐笔引擎 ──────────────────────────
rep('''        return (journal, bank) if journal and bank else None
    except Exception as e:
        print(f"[对账快车道] 场景检测失败（按普通链路处理）: {e}")
        return None''',
    '''        if journal and bank:
            # 汇总级台账（按年/分类汇总）不走逐笔快车道 → 交 LLM 按 summary_compare 规划
            try:
                from config.scenario_packs import is_detail_level
                if not is_detail_level(load_tables(journal)[0]):
                    print(f"[对账快车道] {journal.name} 为汇总级数据，交 LLM 汇总勾稽路径")
                    return None
            except Exception:
                pass
            return (journal, bank)
        return None
    except Exception as e:
        print(f"[对账快车道] 场景检测失败（按普通链路处理）: {e}")
        return None''',
    "快车道明细级闸门")

# ── 3. 算子强制：银行对账场景必须含 Reconcile ──────────────────
rep('''    need_merge = any(k in user_intent for k in ["核对", "对账", "匹配", "比对", "两表"])''',
    '''    try:
        from config.scenario_packs import detect_scenario as _ds2, required_ops_for
        _scn_req = required_ops_for(_ds2(user_intent or ""))
    except Exception:
        _scn_req = []
    if "Reconcile" in _scn_req and "Reconcile" not in existing and load_count >= 2:
        max_id += 1; load_ids2 = [op["id"] for op in ops if op.get("name") in ("Load", "load")]
        ops.append({"id": f"op_{max_id}", "name": "Reconcile", "input_from": load_ids2[:2],
                    "params": {"tolerance_abs": 0.01, "date_window_days": 3},
                    "output_alias": f"df_reconciled_{max_id}"})
        existing.add("Reconcile"); changed = True
    need_merge = any(k in user_intent for k in ["核对", "对账", "匹配", "比对", "两表"])''',
    "场景算子强制（Reconcile）")

P.write_text(src, encoding="utf-8", newline="\n")
import ast
ast.parse(src)
print("routes 补丁E3 完成，AST OK")
