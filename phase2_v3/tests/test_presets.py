#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""预设统一注册表测试：注册表完整性/别名归一/四处消费方一致性"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# 防御：E2E 测试在同进程打桩过 config.fallback_prompts，先驱逐再导入真实模块
sys.modules.pop("config.fallback_prompts", None)
ok = lambda msg: print(f"  [OK] {msg}")

from config.presets import (PRESETS, all_keys_with_aliases, get_preset,
                            is_dag_preset, normalize_preset_key, public_list,
                            render_dify_md)

# 1) 注册表完整性：7 个 DAG 预设 + 1 特殊路由
dag_keys = [k for k, p in PRESETS.items() if p.get("dag", True)]
assert len(dag_keys) == 7 and "格式与纠错" not in dag_keys
assert set(dag_keys) == {"银行对账", "数据比对", "提取式核对", "大额交易筛查",
                         "智能筛选", "文档生成", "跨文件对比"}
ok(f"7 个 DAG 预设 + 特殊路由: {dag_keys}")

# 2) 医保不是独立预设，只是"提取式核对"的别名
assert "医保回款核对" not in PRESETS and "医保对账" not in PRESETS
assert normalize_preset_key("医保对账") == "提取式核对"
assert normalize_preset_key("医保回款") == "提取式核对"
assert normalize_preset_key("银行流水核对") == "银行对账"
assert normalize_preset_key("跨文档比对") == "跨文件对比"
assert normalize_preset_key("不存在的按钮") is None
ok("医保别名归一到通用'提取式核对'（非独立预设）")

# 3) 每个 DAG 预设都有 场景映射/后缀/默认算子/Dify prompt/复核点
for k in dag_keys:
    p = PRESETS[k]
    assert p.get("scenario") and p.get("system_suffix") and p.get("default_operators")
    assert p.get("dify_prompt") and p.get("review_points"), k
ok("预设字段完整（scenario/suffix/ops/dify_prompt/review_points）")

# 4) public_list 与 is_dag_preset
pl = public_list()
assert len(pl) == 8 and all("value" in p and "icon" in p for p in pl)
assert is_dag_preset("银行对账") and not is_dag_preset("格式与纠错")
ok("public_list（前端按钮 API 数据源）")

# 5) dag_compiler 派生（旧接口兼容 + 新键存在 + 别名命中）
from core.dag_compiler import PRESET_BUTTONS, get_preset_button_config
assert "银行对账" in PRESET_BUTTONS and "数据比对" in PRESET_BUTTONS
assert get_preset_button_config("医保对账") is not None  # 别名
assert get_preset_button_config("医保对账")["scenario"] == "filtered_extraction_match"
ok("dag_compiler.PRESET_BUTTONS 派生（含别名）")

# 6) fallback_prompts 派生与场景路由（v3.2 已收敛到 scenario_packs 注册表）
from config.fallback_prompts import detect_scenario, get_fallback_prompt
# 降级 prompt 由 scenario_packs.assemble_fallback_prompt 组装
assert "【降级模式激活】" in get_fallback_prompt("bank_reconcile_detail")
assert detect_scenario("比较两个文件是否账款相符") == "bank_reconcile_detail"
assert detect_scenario("提取流水中的医保回款和台账核对") == "filtered_extraction_match"
assert detect_scenario("单笔超过50万的大额交易筛出来") == "large_txn_screen"
assert detect_scenario("对比这两份docx文档的数字差异") == "cross_doc_compare"
assert "降级模式" in get_fallback_prompt("filtered_extraction_match")
ok("fallback_prompts 派生 + detect_scenario 新场景路由")

# 7) Dify md 生成（路由表含 7 预设，含别名说明，含键纪律）
md = render_dify_md()
for k in dag_keys:
    assert f'preset_button = "{k}"' in md, k
assert "序号/编号/行号" in md and "提取式核对（通用）" in md
assert "医保" in md  # 仅作为别名/实例出现
ok("dify/preset_prompts.md 由注册表生成")

# 8) 场景包交叉引用：预设 scenario 均存在于 scenario_packs
from config.scenario_packs import SCENARIO_PACKS
for k in dag_keys:
    sc = PRESETS[k]["scenario"]
    assert sc in SCENARIO_PACKS, (k, sc)
ok("预设与场景知识包映射一致")

print("\n全部通过：预设统一注册表（单一事实来源）")
