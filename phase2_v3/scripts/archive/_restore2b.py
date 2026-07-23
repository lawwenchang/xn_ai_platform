# Part 2b: essential operators only
import pathlib
path = pathlib.Path(r"d:\Liu\ai_platform_code\phase2_v3\api\routes.py")
c = path.read_text("utf-8")

# Fix: _ensure_essential_operators call
c = c.replace(
    'blueprint_dict = dag_blueprint.to_dict() if hasattr(dag_blueprint, \"to_dict\") else dag_blueprint.__dict__\n        _get_snapshot_mgr().update_blueprint(run_id, blueprint_dict)',
    'blueprint_dict = dag_blueprint.to_dict() if hasattr(dag_blueprint, \"to_dict\") else dag_blueprint.__dict__\n        blueprint_dict = _ensure_essential_operators(blueprint_dict, user_intent)\n        _get_snapshot_mgr().update_blueprint(run_id, blueprint_dict)'
)

path.write_text(c, "utf-8")
print("essential ops added:", "_ensure_essential_operators" in path.read_text("utf-8"))
