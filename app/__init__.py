# app/__init__.py
from flask import Flask, session, request, redirect, url_for, jsonify, current_app
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from config import Config


import json
import sqlite3  # For workflow_tasks context processor

db = SQLAlchemy()
login_manager = LoginManager()


def create_app(config_class=Config):
    print("Starting create_app()...")

    app = Flask(__name__,
                template_folder='../templates',
                static_folder='../static')

    # ------------------------------------------------------------------
    # REQUEST / RESPONSE LOGGING
    # ------------------------------------------------------------------
    @app.before_request
    def log_every_request():
        print(f">>>>>> REQUEST: {request.method} {request.path}")
        print(f">>>>>> SESSION: user_id={session.get('user_id')}, "
              f"demo_industry={session.get('demo_industry')}")

    @app.after_request
    def log_every_response(response):
        print(f">>>>>> RESPONSE: {request.method} {request.path} -> {response.status_code}")
        if response.status_code in (301, 302, 303, 307, 308):
            print(f">>>>>> REDIRECT TO: {response.headers.get('Location')}")
        return response

    app.config.from_object(config_class)

    # ================================================================
    # REGISTER CONTEXT PROCESSORS FIRST (BEFORE BLUEPRINTS)
    # ================================================================
    from app.core.demo_industry import industry_bp

    from app.core.context_processors import inject_industry_context, inject_current_time
    app.context_processor(inject_industry_context)
    app.context_processor(inject_current_time)
    print("✅ Industry context processors registered")

    # Register Jinja2 filter for date formatting
    from app.core.utils import format_date_filter
    app.jinja_env.filters['format_date'] = format_date_filter

    # Set database URI
    if not app.config.get('SQLALCHEMY_DATABASE_URI'):
        db_path = app.config.get('DB_PATH')
        if db_path:
            app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
        else:
            import os
            basedir = os.path.abspath(os.path.dirname(__file__))
            app.config['SQLALCHEMY_DATABASE_URI'] = (
                f'sqlite:///{os.path.join(basedir, "..", "Instances", "MRP_database.db")}'
            )

    if 'SQLALCHEMY_TRACK_MODIFICATIONS' not in app.config:
        app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    print(f"✅ Config loaded successfully")
    print(f"✅ SECRET_KEY: {app.config.get('SECRET_KEY')}")
    print(f"✅ DB_PATH: {app.config.get('DB_PATH')}")
    print(f"✅ SQLALCHEMY_DATABASE_URI: {app.config.get('SQLALCHEMY_DATABASE_URI')}")

    # Initialize extensions
    db.init_app(app)
    login_manager.init_app(app)

    from app.cli.translation_sync import sync_translations
    app.cli.add_command(sync_translations)

    # ------------------------------------------------------------------
    # USER CLASS AND LOADER
    # ------------------------------------------------------------------
    from flask_login import UserMixin

    class User(UserMixin):
        def __init__(self, user_id, username, fullname, email,
                     portal_access=False, company_id=None,
                     default_calendar='gregorian', language_code='en'):
            self.id = str(user_id)
            self.username = username
            self.fullname = fullname
            self.email = email
            self.portal_access = portal_access
            self.company_id = company_id
            self.default_calendar = default_calendar or 'gregorian'
            self.language_code = language_code or 'en'

        def get_id(self):
            return self.id

    from app.core.date_helpers import display_date, format_date, date_to_key

    @app.context_processor
    def inject_date_helpers():
        return {
            'display_date': display_date,
            'format_date': format_date,
            'date_to_key': date_to_key,
            'current_calendar': lambda: session.get('calendar', 'gregorian'),
            'current_language': lambda: session.get('lang', 'en'),
        }

    @login_manager.user_loader
    def load_user(user_id):
        from sqlalchemy.sql import text
        try:
            with db.engine.connect() as conn:
                row = conn.execute(
                    text("""SELECT UserID, Username, FullName, Email, PortalAccess,
                                  CompanyID, DefaultCalendar, LanguageCode
                           FROM t_Users WHERE UserID = :uid"""),
                    {'uid': user_id}
                ).fetchone()
                if row:
                    return User(row[0], row[1], row[2], row[3],
                                bool(row[4]), row[5], row[6], row[7])
        except Exception as e:
            print(f"User loader error: {e}")
        return None

    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'info'

    # ================================================================
    # REGISTER INDUSTRY BLUEPRINT (MUST BE BEFORE OTHER BLUEPRINTS)
    # ================================================================
    app.register_blueprint(industry_bp)
    print("✅ Industry blueprint registered")

    # ------------------------------------------------------------------
    # IMPORT ALL BLUEPRINTS (72 Pages - V43 FINAL)
    # ------------------------------------------------------------------

    # --- AUTH ---
    from app.modules.auth import auth_bp

    # --- DASHBOARD (3 pages) ---
    from app.modules.dashboard import dashboard_bp, dashboard_api_bp

    # --- PROJECTS (6 pages) ---
    from app.modules.project import project_bp
    from app.modules.project.api import api_bp as project_api_bp

    # --- ENGINEERING (8 pages) ---
    from app.modules.engineering import engineering_bp
    from app.modules.engineering.api import api_bp as engineering_api_bp

    # --- MANUFACTURING (8 pages) ---
    from app.modules.manufacturing import manufacturing_bp, api_bp as manufacturing_api_bp

    # --- QUALITY (10 pages) ---
    from app.modules.quality import quality_bp
    try:
        from app.modules.quality.api import quality_api
    except ImportError:
        quality_api = None
        print("⚠️ quality_api not found - will register later if available")

    # --- SUPPLY CHAIN (7 pages) ---
    from app.modules.supply_chain import supply_chain_bp, api_bp as supply_chain_api_bp

    # --- CRM & SALES (4 pages) ---
    from app.modules.crm import crm_bp, crm_api_bp

    # --- CRM AI (paste-text → extract entities) ---
    from app.modules.crm.ai_routes import ai_bp

    # --- FINANCE (4 pages) ---
    try:
        from app.modules.finance import finance_bp, finance_api_bp
    except ImportError:
        finance_bp = None
        finance_api_bp = None
        print("⚠️ finance module not found")

    # --- HUMAN RESOURCES (4 pages) ---
    try:
        from app.modules.hr import hr_bp, hr_api_bp
    except ImportError:
        hr_bp = None
        hr_api_bp = None
        print("⚠️ hr module not found")

    # --- WORKFLOW (5 pages) ---
    from app.modules.workflow import workflow_bp
    try:
        from app.modules.workflow.api import workflow_api_bp
    except ImportError:
        workflow_api_bp = None
        print("⚠️ workflow_api not found")

    # --- ADMIN (4 pages) ---
    from app.modules.admin import admin_bp
    from app.modules.admin.api import api_bp as admin_api_bp

    # --- REPORTS (3 pages) ---
    from app.modules.reports import reports_bp, api_bp

    # --- INVENTORY (minimal - API only) ---
    try:
        from app.modules.inventory import inventory_bp
    except ImportError:
        inventory_bp = None
        print("⚠️ inventory module not found")

    # ------------------------------------------------------------------
    # REGISTER BLUEPRINTS (Page Blueprints)
    # ------------------------------------------------------------------

    # AUTH
    app.register_blueprint(auth_bp)

    # DASHBOARD
    app.register_blueprint(dashboard_bp, url_prefix='/dashboard')

    # PROJECTS — Do NOT override the blueprint's internal url_prefix
    app.register_blueprint(project_bp)

    # ENGINEERING
    app.register_blueprint(engineering_bp, url_prefix='/engineering')

    # MANUFACTURING
    app.register_blueprint(manufacturing_bp, url_prefix='/manufacturing')

    # QUALITY
    app.register_blueprint(quality_bp, url_prefix='/quality')

    # SUPPLY CHAIN
    app.register_blueprint(supply_chain_bp, url_prefix='/supply-chain')

    # CRM & SALES
    app.register_blueprint(crm_bp, url_prefix='/crm')

    # CRM AI — paste-text → extract entities
    app.register_blueprint(ai_bp)

    # FINANCE
    if finance_bp:
        app.register_blueprint(finance_bp, url_prefix='/finance')

    # HR
    if hr_bp:
        app.register_blueprint(hr_bp, url_prefix='/hr')

    # WORKFLOW
    app.register_blueprint(workflow_bp, url_prefix='/workflow')

    # ADMIN
    app.register_blueprint(admin_bp, url_prefix='/admin')

    # REPORTS
    app.register_blueprint(reports_bp, url_prefix='/reports')

    # INVENTORY
    if inventory_bp:
        app.register_blueprint(inventory_bp, url_prefix='/inventory')

    # ------------------------------------------------------------------
    # REGISTER API BLUEPRINTS
    # ------------------------------------------------------------------

    app.register_blueprint(project_api_bp)
    app.register_blueprint(engineering_api_bp)
    app.register_blueprint(admin_api_bp)
    app.register_blueprint(manufacturing_api_bp)
    app.register_blueprint(supply_chain_api_bp)
    app.register_blueprint(crm_api_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(dashboard_api_bp)

    if quality_api:
        app.register_blueprint(quality_api, url_prefix='/api/quality')
        print("✅ quality_api registered")

    if workflow_api_bp:
        app.register_blueprint(workflow_api_bp, url_prefix='/api/workflow')
        print("✅ workflow_api registered")

    if finance_api_bp:
        app.register_blueprint(finance_api_bp, url_prefix='/api/finance')
        print("✅ finance_api registered")

    if hr_api_bp:
        app.register_blueprint(hr_api_bp, url_prefix='/api/hr')
        print("✅ hr_api registered")

    print("✅ All blueprints registered")

    # ------------------------------------------------------------------
    # CONTEXT PROCESSORS
    # ------------------------------------------------------------------

    @app.context_processor
    def inject_projects():
        from sqlalchemy.sql import text
        from types import SimpleNamespace

        demo_industry = session.get('demo_industry', 'valve')

        try:
            with db.engine.connect() as conn:
                rows = conn.execute(text("""
                    SELECT ProjectID, ProjectName, ProjectCode
                    FROM t_Project
                    WHERE DemoIndustryCode = :industry
                    UNION
                    SELECT CompanyProjectID, ProjectName, ProjectNumber
                    FROM t_CompanyProject
                    WHERE DemoIndustryCode = :industry
                """), {'industry': demo_industry})
                projects = [{'ProjectID': row[0], 'ProjectName': row[1],
                             'ProjectCode': row[2]} for row in rows]
        except Exception as e:
            print(f"Error loading projects for industry {demo_industry}: {e}")
            projects = []

        current_project_id = session.get('current_project_id', 'All')

        current_project = None
        if current_project_id != 'All':
            for p in projects:
                if p['ProjectID'] == current_project_id:
                    current_project = SimpleNamespace(
                        ProjectID=p['ProjectID'],
                        ProjectName=p['ProjectName'],
                        ProjectCode=p['ProjectCode']
                    )
                    break

        return {
            'projects': projects,
            'current_project': current_project
        }

    @app.context_processor
    def inject_template_helpers():
        return {'get_products': lambda: [], 'get_work_centers': lambda: []}

    @app.context_processor
    def inject_global_vars():
        from flask_login import current_user
        return {
            'workflow_task_count': 0,
            'user_display_name': current_user.fullname if current_user.is_authenticated else 'Guest',
            'test_var': 'HELLO',
            'session': session
        }

    # ================================================================
    # CRITICAL: REGISTER TRANSLATION CONTEXT PROCESSOR
    # ================================================================
    try:
        import app.core.translation as translation_module
        print("🔴 Successfully imported translation module")
        app.context_processor(translation_module.inject_translations)
        print("✅ Translation context processor registered")
    except ImportError as e:
        print(f"❌ ERROR: Could not import translation module: {e}")

        @app.context_processor
        def fallback_translations():
            print("🔴 Using fallback translation context processor")
            return {
                'lang': 'en',
                'get_translation': lambda key, default=None: default if default is not None else key,
                'get_localized': lambda row, eng, loc, lang=None: row.get(eng, '') if row else '',
                'get_product_translation': lambda product, lang=None: product.get('descEnglish', '') if product else '',
                'get_operation_translation': lambda op, lang=None: op.get('Name', '') if op else '',
                'format_time_ago': lambda ts: str(ts) if ts else '',
                'js_translations': {}
            }
        app.context_processor(fallback_translations)
        print("✅ Fallback translation context processor registered")

    # ================================================================
    # WORKFLOW TASKS CONTEXT PROCESSOR
    # ================================================================
    def inject_workflow_tasks():
        if not session.get('user_id'):
            return {'workflow_task_count': 0}

        try:
            from app.core.database import get_db_connection
            conn = get_db_connection()
            conn.row_factory = sqlite3.Row

            user_roles = session.get('user_roles', [])
            if not user_roles:
                conn.close()
                return {'workflow_task_count': 0}

            placeholders = ','.join(['?'] * len(user_roles))
            query = f"""
                SELECT COUNT(*) FROM t_Workflow
                WHERE AssignedToRole IN ({placeholders})
                AND Status = 'Pending'
            """
            cur = conn.cursor()
            cur.execute(query, user_roles)
            count = cur.fetchone()[0]
            conn.close()

            return {'workflow_task_count': count}
        except Exception as e:
            print(f"Error counting tasks: {e}")
            return {'workflow_task_count': 0}

    app.context_processor(inject_workflow_tasks)
    print("✅ Workflow tasks context processor registered")

    @app.context_processor
    def inject_sidebar_context():
        active_menu = None

        if request:
            endpoint = request.endpoint or ''
            path = request.path or ''

            if endpoint.startswith('dashboard') or path.startswith('/dashboard'):
                active_menu = 'dashboard'
            elif endpoint.startswith('crm') or path.startswith('/crm'):
                active_menu = 'crm'
            elif endpoint.startswith('project') or path.startswith('/project'):
                active_menu = 'projects'
            elif endpoint.startswith('engineering') or path.startswith('/engineering'):
                active_menu = 'engineering'
            elif path.startswith('/supply-chain'):
                active_menu = 'supply_chain'
            elif endpoint.startswith('manufacturing') or path.startswith('/manufacturing'):
                active_menu = 'manufacturing'
            elif endpoint.startswith('quality') or path.startswith('/quality'):
                active_menu = 'quality'
            elif path.startswith('/finance'):
                active_menu = 'finance'
            elif path.startswith('/hr'):
                active_menu = 'hr'
            elif path.startswith('/workflow'):
                active_menu = 'workflow'
            elif path.startswith('/admin'):
                active_menu = 'admin'
            elif path.startswith('/reports'):
                active_menu = 'reports'

        return {'active_menu': active_menu}

    # ================================================================
    # USER PREFERENCE LOADING HELPER
    # ================================================================

    def load_user_preferences(user_id):
        try:
            from sqlalchemy.sql import text
            with db.engine.connect() as conn:
                row = conn.execute(
                    text("""SELECT LanguageCode, DefaultCalendar
                           FROM t_Users WHERE UserID = :uid"""),
                    {'uid': user_id}
                ).fetchone()
                if row:
                    lang = row[0] or 'en'
                    cal = row[1] or ('persian' if lang == 'fa' else 'gregorian')
                    return lang, cal
        except Exception as e:
            print(f"Error loading user preferences: {e}")
        return 'en', 'gregorian'

    def set_user_session_preferences(user_id):
        lang, calendar = load_user_preferences(user_id)
        session['lang'] = lang
        session['calendar'] = calendar
        print(f"✅ User preferences loaded: lang={lang}, calendar={calendar}")
        return lang, calendar

    # ------------------------------------------------------------------
    # SIMPLE ROUTES
    # ------------------------------------------------------------------

    @app.route('/switch_language/<lang>')
    def switch_language(lang):
        session['lang'] = lang
        session['calendar'] = 'persian' if lang == 'fa' else 'gregorian'
        return redirect(request.referrer or '/')

    # =========================================================================
    # REMOVED: Duplicate /api/set-current-project route
    # =========================================================================
    # The canonical endpoint now lives in app/core/routes.py on the api_bp
    # blueprint. Having it here as well caused a route-conflict warning and
    # unpredictable behavior. Removed in v46 to enforce a single source of
    # truth.
    #
    # If you ever need to roll back, restore:
    #
    @app.route('/api/set-current-project', methods=['POST'])
    def api_set_current_project():
        data = request.get_json()
        project_id = data.get('project_id')
        if project_id is None:
            return jsonify({'status': 'error', 'message': 'Missing project_id'}), 400
        session['current_project_id'] = project_id
        session.modified = True
        print(f"DEBUG: Set session current_project_id = {project_id}")
        return jsonify({'status': 'ok'})
    # =========================================================================
    # NOTE: /api/user/projects is also defined in app/core/routes.py.
    # The version below is kept as a harmless fallback — but if you see a
    # route-conflict warning, remove this one too and rely solely on
    # app/core/routes.py.
    # =========================================================================

    @app.route('/api/user/projects', methods=['GET', 'POST'])
    def user_projects():
        if request.method == 'GET':
            return jsonify(session.get('selected_projects', []))
        data = request.get_json()
        session['selected_projects'] = data.get('projects', [])
        return jsonify({'status': 'ok'})

    # =========================================================================
    # NEW: /api/health — lightweight diagnostic endpoint
    # =========================================================================
    # The browser-console snippet you pasted (a HEAD request to the current
    # URL) was failing with "TypeError: Failed to fetch". This endpoint gives
    # you a reliable, no-auth, JSON-returning way to verify the server is
    # alive from the browser console or from curl:
    #
    #   fetch('/api/health').then(r => r.json()).then(console.log)
    #
    # It returns the current server status, session ID, and a hint about the
    # active industry so you can confirm the server is responding and sessions
    # are working.
    # =========================================================================
    @app.route('/api/health', methods=['GET', 'HEAD', 'OPTIONS'])
    def health_check():
        from flask import current_app
        return jsonify({
            'status': 'ok',
            'server': 'MRP System',
            'session_id': session.get('_id', 'no-session'),
            'session_user': session.get('user_id', 'anonymous'),
            'demo_industry': session.get('demo_industry', 'valve'),
            'debug': bool(current_app.debug),
            'url_map_size': len(list(current_app.url_map.iter_rules())),
        })

    @app.route('/debug-routes')
    def debug_routes():
        routes = [str(rule) for rule in app.url_map.iter_rules()]
        return jsonify(routes)

    @app.route('/test-modal')
    def test_modal():
        from flask import render_template
        return render_template('test_modal.html')

    print("✅ App created successfully!")
    return app