import logging
logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)
logging.getLogger('sqlalchemy.engine.Engine').setLevel(logging.WARNING)
logging.getLogger('sqlalchemy.pool').setLevel(logging.WARNING)
logging.getLogger('sqlalchemy').setLevel(logging.WARNING)
logging.basicConfig(level=logging.WARNING)


import os
import sys
import traceback

print("=== Diagnostic Run ===")
print(f"Current directory: {os.getcwd()}")
print(f"Python path: {sys.path}")

print("\n--- Attempting to import 'app' module ---")
try:
    import app
    print("✅ 'app' module imported successfully")
    print(f"Module location: {app.__file__}")
    print(f"Attributes in app: {[x for x in dir(app) if not x.startswith('_')]}")

    if hasattr(app, 'create_app'):
        print("✅ 'create_app' found in app module")
    else:
        print("❌ 'create_app' NOT found in app module")
        sys.exit(1)
except Exception as e:
    print(f"❌ Failed to import 'app': {e}")
    traceback.print_exc()
    sys.exit(1)

print("\n--- Calling create_app() ---")
try:
    app_instance = app.create_app()
    print("✅ App created successfully")
except Exception as e:
    print(f"❌ Error in create_app: {e}")
    traceback.print_exc()
    sys.exit(1)

if __name__ == '__main__':
    # Debug off by default. Set FLASK_DEBUG=1 in the shell to enable.
    debug_mode = os.environ.get('FLASK_DEBUG', '0') == '1'

    print(f"\n--- Starting Flask server (debug={debug_mode}) ---")
    try:
        app_instance.run(
            debug=debug_mode,
            port=8000,
            use_reloader=False,
            threaded=True,
        )
    except Exception as e:
        print(f"❌ Server error: {e}")
        traceback.print_exc()