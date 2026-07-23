p = __import__("pathlib").Path(r"d:\Liu\ai_platform_code\phase2_v3\api\routes.py")
c = p.read_text("utf-8")
# Fix: add missing colon after columns condition
c = c.replace(
    "_LEFT' in {op_alias}_common.columns\")",
    "_LEFT' in {op_alias}_common.columns:\")"
)
p.write_text(c, "utf-8")
print("Fixed. Test:", "_LEFT' in {op_alias}_common.columns:\" in c:", "_LEFT' in {op_alias}_common.columns:\"" in c)
