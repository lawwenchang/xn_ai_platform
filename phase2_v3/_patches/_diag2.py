#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from pathlib import Path
s = Path("engine/sandbox_v2.py").read_text(encoding="utf-8").replace("\r\n", "\n")
i = s.find("apk add")
print(repr(s[i - 60:i + 260]))
