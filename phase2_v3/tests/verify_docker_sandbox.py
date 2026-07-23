#!/usr/bin/env python3
"""Docker 沙箱端到端验证"""
import tempfile, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.sandbox_v3 import EphemeralSandbox

run_dir = Path(tempfile.mkdtemp())
(run_dir / "inputs").mkdir()
(run_dir / "outputs").mkdir()
(run_dir / "inputs" / "test.csv").write_text("金额,类别\n100,医保\n200,统筹\n50,手续费\n", encoding="utf-8")

code = '''
import pandas as pd, json
df = pd.read_csv("/data/readonly/test.csv")
with open("/home/auditor/outputs/journal_entries.json","w") as f:
    json.dump({"rows":len(df),"columns":list(df.columns)},f)
'''

# 3. 先重建镜像
from engine.sandbox_v3 import EphemeralSandbox as ES
s = ES()
print("rebuild:", s.build_image())

# 4. 执行
r = s.execute("DOCKER_FINAL_TEST", code, run_dir)
print(f"status={r.status} exit={r.exit_code}")
print(f"stdout={r.stdout[:500] if r.stdout else '(empty)'}")
print(f"stderr={r.stderr[:300] if r.stderr else '(none)'}")
print(f"error={r.error_message}")

# 5. 验证
out_path = run_dir / "outputs" / "journal_entries.json"
if out_path.exists():
    out = json.loads(out_path.read_text(encoding="utf-8"))
    print(f"output={out}")
    assert out["rows"] == 3
    print("\n✅✅✅ Docker 沙箱端到端验证通过！✅✅✅")
else:
    print("\n❌ 输出文件未生成，检查 outputs 目录:", list((run_dir / "outputs").iterdir()))
    sys.exit(1)
