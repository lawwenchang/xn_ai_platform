# Insert helpers before _execute_in_sandbox_legacy
import pathlib
p = pathlib.Path(r"d:\Liu\ai_platform_code\phase2_v3\api\routes.py")
c = p.read_text("utf-8")

# Find insertion point
marker = "async def _execute_in_sandbox_legacy"
idx = c.index(marker)

# Read helpers from a separate file
helpers_path = pathlib.Path(r"d:\Liu\ai_platform_code\phase2_v3\_helpers.txt")
helpers = helpers_path.read_text("utf-8")

c = c[:idx] + helpers + "\n\n" + c[idx:]
p.write_text(c, "utf-8")
print("Done. Checking:", all(k in c for k in ["_run_constraint_check","_write_audit_trace","_append_approval_hash","_ensure_essential_operators"]))
