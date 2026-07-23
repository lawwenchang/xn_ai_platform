#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from pathlib import Path
s = Path("config/few_shot_examples.py").read_text(encoding="utf-8").replace("\r\n", "\n")
i = s.find('col_a": "交易金额", "col_b": "业务金额", "tolerance_pct": 1.0, "output_mode": "all"')
print("found at:", i)
seg = s[i - 20:i + 260]
print(repr(seg))
# 直接做单行替换测试
old1 = '"params": {"col_a": "交易金额", "col_b": "业务金额", "tolerance_pct": 1.0, "output_mode": "all"}},'
print("单行命中次数:", s.count(old1))
