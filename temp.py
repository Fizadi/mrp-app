# save as check_routes.py and run with: python check_routes.py
from app import create_app   # adjust if your factory has a different name

app = create_app()
with app.app_context():
    print("=== Registered Quality routes ===")
    for rule in app.url_map.iter_rules():
        if "quality" in str(rule):
            print(f"{rule.methods}  {rule}")