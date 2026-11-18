# app/core/context_processors.py
from flask import session
from datetime import datetime
from app.core.database import get_db_connection


print("🔄 Loading context_processors.py...")


# ============================================================
# INDUSTRY_DATA — kept as a legacy fallback only.
# New code paths should read tenant info from t_Tenants.
# ============================================================
INDUSTRY_DATA = {
    'valve': {
        'name': 'Valve Manufacturing (ETO)',
        'name_fa': 'تولید شیرآلات (ETO)',
        'icon': 'fa-industry',
        'description': 'ETO - Engineering to Order',
        'scenario': 'ETO',
        'sample_company': 'Precision Valve Co.'
    },
    'furniture': {
        'name': 'Furniture Manufacturing (CTO)',
        'name_fa': 'تولید مبلمان (CTO)',
        'icon': 'fa-chair',
        'description': 'CTO - Configure to Order',
        'scenario': 'CTO',
        'sample_company': 'Modern Furniture MFR'
    },
    'jobshop': {
        'name': 'Job Shop (MTO)',
        'name_fa': 'کارگاه سفارشی (MTO)',
        'icon': 'fa-tools',
        'description': 'MTO - Make to Order',
        'scenario': 'MTO',
        'sample_company': 'Heavy Machining Inc.'
    },
    'gauge': {
        'name': 'Gauge Manufacturing (MTS)',
        'name_fa': 'تولید گیج (MTS)',
        'icon': 'fa-ruler',
        'description': 'MTS - Make to Stock',
        'scenario': 'MTS',
        'sample_company': 'ProbeTech Industries'
    },
    'paint': {
        'name': 'Paint Manufacturing (MTS/CTO)',
        'name_fa': 'تولید رنگ (MTS/CTO)',
        'icon': 'fa-paint-roller',
        'description': 'MTS/CTO - Paint and Coatings Manufacturing',
        'scenario': 'MTS',
        'sample_company': 'Pars Alborz Poushesh Co.'
    },
    'elec': {
        'name': 'Electrical Industry (EPC / MTO / MTS)',
        'name_fa': 'صنعت برق (EPC / MTO / MTS)',
        'icon': 'fa-bolt',
        'description': 'Substations, switchgear, transformers, solar PV EPC',
        'scenario': 'ETO',
        'sample_company': 'VoltGrid Engineering Co.'
    },
    'personal': {
        'name': 'Personal Sales / Taraz Tamin',
        'name_fa': 'فروش شخصی / تراز تأمین',
        'icon': 'fa-user-tie',
        'description': 'Personal CRM for Taraz Tamin business development',
        'scenario': 'CTO',
        'sample_company': 'Taraz Tamin',
    },
    # §F1: added for Tenant 9 (solar.admin) so the header/industry fallback
    # resolves to "Solar Energy Co." instead of "Precision Valve Co."
    'solar': {
        'name': 'Solar Energy (EPC / MTO)',
        'name_fa': 'انرژی خورشیدی (EPC / MTO)',
        'icon': 'fa-solar-panel',
        'description': 'Solar PV EPC, inverters, panels, installation services',
        'scenario': 'ETO',
        'sample_company': 'Solar Energy Co.'
    }
}


def _load_tenant_modules(tenant_id):
    """
    Return a list of enabled module codes for the given tenant.

    - Tenant 0 (Super Admin) sees ALL modules.
    - Other tenants see only their enabled modules from t_TenantModules.
    - Codes are normalized to lowercase so that sidebar checks like
      `{% if 'crm' in tenant_modules %}` work regardless of how they
      were seeded (case-insensitive).
    """
    if tenant_id == 0:
        # Super Admin — always sees everything
        return ['crm', 'projects', 'manufacturing', 'quality',
                'supply_chain', 'finance', 'engineering']

    conn = get_db_connection(use_teardown=False)
    if not conn:
        return []

    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT ModuleCode FROM t_TenantModules WHERE TenantID = ? AND IsEnabled = 1",
            (tenant_id,)
        )
        rows = cursor.fetchall()
        # Normalize to lowercase for the sidebar Jinja checks.
        # Also tolerate the 'supplychain' (no underscore) variant that
        # provision_tenant() writes vs the 'supply_chain' used by the sidebar.
        out = []
        for row in rows:
            code = (row[0] or '').strip().lower()
            if code == 'supplychain':
                code = 'supply_chain'
            if code:
                out.append(code)
        return out
    except Exception as e:
        print(f"Error loading tenant modules for tenant {tenant_id}: {e}")
        return []
    finally:
        conn.close()


def inject_industry_context():
    """
    Inject tenant-related variables into all templates.

    Primary source of truth is t_Tenants (TenantID → CompanyName, LogoURL,
    PrimaryColor, IsActive) plus t_TenantModules (enabled module codes).

    Legacy industry fields are still exposed for backward compatibility
    so existing templates that reference them keep working.
    """
    print("🔄 inject_industry_context called")

    # 1. Tenant
    tenant_id = session.get('tenant_id', 1)
    company_info = get_company_info(tenant_id)

    # 2. Calendar / lang
    calendar = session.get('calendar', 'gregorian')
    lang = session.get('lang', 'en')

    # 3. Legacy industry fields
    industry = session.get('demo_industry', 'valve')
    data = INDUSTRY_DATA.get(industry, INDUSTRY_DATA['valve'])

    industries = []
    for code, info in INDUSTRY_DATA.items():
        industries.append({
            'code': code,
            'name': info['name'],
            'icon': info['icon'],
            'scenario': info['scenario']
        })

    # 4. Enabled modules
    tenant_modules = _load_tenant_modules(tenant_id)

    print(f"📋 Tenant ID from session: {tenant_id}")
    print(f"📋 Company from t_Tenants: {company_info}")
    print(f"📋 Enabled modules: {tenant_modules}")
    print(f"📋 Legacy industry from session: {industry}")
    print(f"📋 Language from session: {lang}")
    print(f"📋 Calendar from session: {calendar}")

    # 5. Return context
    return {
        # ---- TENANT (primary) ----
        'tenant_id': tenant_id,
        'company': company_info,
        'tenant_company_name': company_info['CompanyName'] if company_info else 'Unknown',
        'tenant_logo_url': company_info['LogoURL'] if company_info else None,
        'tenant_primary_color': company_info['PrimaryColor'] if company_info else '#2c3e50',
        'tenant_modules': tenant_modules,
        'current_calendar': calendar,

        # ---- LEGACY industry context (backward compat) ----
        'demo_industry': industry,
        'industry_display': data['name'],
        'industry_icon': data['icon'],
        'industry_description': data['description'],
        'industry_scenario': data['scenario'],
        'sample_company': data['sample_company'],
        'industries': industries,
    }


def get_company_info(tenant_id):
    """
    Fetch tenant details from the master t_Tenants table.

    Uses pyodbc cursor execution because get_db_connection returns a raw
    pyodbc connection here. See PROJECT CONTEXT §5.2 — if get_db_connection
    is ever refactored to return a SQLAlchemy-wrapped connection, this
    function must switch to conn.execute(text(...), {...}) with named
    parameters.
    """
    conn = get_db_connection(use_teardown=False)
    if not conn:
        return None

    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT TenantID, CompanyName, LogoURL, PrimaryColor, IsActive "
            "FROM t_Tenants WHERE TenantID = ?",
            (tenant_id,)
        )
        row = cursor.fetchone()

        if row:
            return {
                'TenantID': row[0],
                'CompanyName': row[1],
                'LogoURL': row[2],
                'PrimaryColor': row[3],
                'IsActive': row[4]
            }
    except Exception as e:
        print(f"Error getting company info for tenant {tenant_id}: {e}")
    finally:
        conn.close()

    return None


def inject_current_time():
    """Inject current time for sidebar."""
    return {
        'current_time': datetime.now().strftime('%H:%M:%S')
    }


# Optional: Add a function to inject user info
def inject_user_info():
    """Inject user-related variables into all templates."""
    from flask import session as flask_session
    user_info = {
        'user_id': flask_session.get('user_id'),
        'username': flask_session.get('username'),
        'full_name': flask_session.get('full_name'),
        'roles': flask_session.get('roles', []),
        'tenant_id': flask_session.get('tenant_id'),
    }
    return {
        'current_user_info': user_info
    }


# Optional: Add a function to inject system status
def inject_system_status():
    """Inject system status variables."""
    return {
        'system_status': 'Operational',
        'system_version': '1.0.0'
    }


# Register all context processors in a list for easy registration
CONTEXT_PROCESSORS = [
    inject_industry_context,
    inject_current_time,
    # inject_user_info,  # Uncomment if needed
    # inject_system_status,  # Uncomment if needed
]