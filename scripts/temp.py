"""
Route verification script for Flask app.
Run from the FlaskApp root folder: python temp.py
"""

import sys
import os

# ============================================================================
# CRITICAL: Set up paths BEFORE importing app
# ============================================================================
# Get the directory where THIS script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
print(f"Script directory: {SCRIPT_DIR}")

# Add this directory to the front of sys.path
sys.path.insert(0, SCRIPT_DIR)

# Remove any conflicting paths pointing to Python install dir
sys.path = [p for p in sys.path if 'AppData\\Local\\Programs\\Python' not in p or 'site-packages' in p]

print(f"Python path[0:3]: {sys.path[0:3]}")
print()

print("=" * 80)
print("FLASK ROUTE VERIFICATION")
print("=" * 80)
print()

# ============================================================================
# STEP 1: Verify we can find the app package
# ============================================================================
print("--- STEP 1: Verifying app package location ---")

app_dir = os.path.join(SCRIPT_DIR, 'app')
if os.path.isdir(app_dir):
    print(f"✅ Found 'app' directory: {app_dir}")
    init_file = os.path.join(app_dir, '__init__.py')
    if os.path.isfile(init_file):
        print(f"✅ Found 'app/__init__.py'")
    else:
        print(f"❌ Missing 'app/__init__.py'")
        sys.exit(1)
else:
    print(f"❌ 'app' directory NOT found at: {app_dir}")
    print(f"   Are you running this from the FlaskApp root folder?")
    sys.exit(1)
print()

# ============================================================================
# STEP 2: Import the app
# ============================================================================
print("--- STEP 2: Importing app module ---")
try:
    # Import create_app specifically
    from app import create_app
    print(f"✅ 'create_app' imported successfully")
except Exception as e:
    print(f"❌ Failed to import 'create_app': {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
print()

# ============================================================================
# STEP 3: Create the app instance
# ============================================================================
print("--- STEP 3: Creating app instance ---")
try:
    app_instance = create_app()
    print("✅ App instance created successfully")
except Exception as e:
    print(f"❌ Failed to create app: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
print()

# ============================================================================
# STEP 4: List all FINANCE routes
# ============================================================================
print("--- STEP 4: All FINANCE routes ---")
print()
print(f"{'ENDPOINT':<55s} {'METHODS':<20s} {'RULE'}")
print("-" * 120)

finance_routes = []
with app_instance.app_context():
    for rule in sorted(app_instance.url_map.iter_rules(), key=lambda r: str(r)):
        rule_str = str(rule)
        if '/finance' in rule_str:
            methods = ','.join(sorted(rule.methods - {'HEAD', 'OPTIONS'}))
            print(f"{rule.endpoint:<55s} {methods:<20s} {rule_str}")
            finance_routes.append(rule_str)

print()
print(f"Total finance routes found: {len(finance_routes)}")
print()

# ============================================================================
# STEP 5: Check for the specific problematic routes
# ============================================================================
print("--- STEP 5: Checking specific routes ---")
print()

target_routes = [
    '/finance/sales-forecast',
    '/finance/job-costing',
    '/api/finance/sales-forecast/stats',
    '/api/finance/sales-forecast',
]

for target in target_routes:
    found = False
    for rule in app_instance.url_map.iter_rules():
        if str(rule) == target:
            methods = ','.join(sorted(rule.methods - {'HEAD', 'OPTIONS'}))
            print(f"✅ FOUND: {target}")
            print(f"   Endpoint: {rule.endpoint}")
            print(f"   Methods:  {methods}")
            found = True
            break
    if not found:
        print(f"❌ NOT FOUND: {target}")
print()

# ============================================================================
# STEP 6: Registered blueprints
# ============================================================================
print("--- STEP 6: Registered blueprints ---")
print()
for name, bp in app_instance.blueprints.items():
    print(f"  - {name}: url_prefix={bp.url_prefix}")
print()

# ============================================================================
# STEP 7: Test routes with Flask test client
# ============================================================================
print("--- STEP 7: Testing routes with Flask test client ---")
print()

with app_instance.test_client() as client:
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['demo_industry'] = 'valve'
        sess['lang'] = 'en'
    
    for target in target_routes:
        try:
            response = client.get(target, follow_redirects=False)
            status = response.status_code
            location = response.headers.get('Location', '')
            
            if status == 200:
                print(f"✅ {target} -> 200 OK")
            elif status in (301, 302, 303, 307, 308):
                print(f"⚠️  {target} -> {status} REDIRECT to: {location}")
            elif status == 403:
                print(f"❌ {target} -> 403 FORBIDDEN (permission denied)")
            elif status == 404:
                print(f"❌ {target} -> 404 NOT FOUND")
            elif status == 500:
                print(f"❌ {target} -> 500 SERVER ERROR")
                print(f"   Response: {response.get_data(as_text=True)[:500]}")
            else:
                print(f"❓ {target} -> {status}")
        except Exception as e:
            print(f"❌ {target} -> EXCEPTION: {e}")
            import traceback
            traceback.print_exc()

print()

# ============================================================================
# STEP 8: Template file check
# ============================================================================
print("--- STEP 8: Template file check ---")
print()

template_paths = [
    os.path.join(SCRIPT_DIR, 'app', 'templates', 'finance', 'sales_forecast_list.html'),
    os.path.join(SCRIPT_DIR, 'templates', 'finance', 'sales_forecast_list.html'),
]

for path in template_paths:
    if os.path.exists(path):
        print(f"✅ Template exists: {path}")
        break
else:
    print(f"❌ Template NOT found in:")
    for path in template_paths:
        print(f"   - {path}")

print()

# ============================================================================
# STEP 9: Test has_permission
# ============================================================================
print("--- STEP 9: Testing has_permission for user 1 ---")
print()

try:
    from app.core.auth import has_permission
    with app_instance.app_context():
        result = has_permission(1, 'FINANCE_FORECAST_VIEW')
        print(f"has_permission(1, 'FINANCE_FORECAST_VIEW') = {result}")
except Exception as e:
    print(f"❌ Error testing has_permission: {e}")
    import traceback
    traceback.print_exc()

print()
print("=" * 80)
print("DONE")
print("=" * 80)