# app/core/context_processors.py
from flask import session
from datetime import datetime
from app.core.database import get_db_connection


print("🔄 Loading context_processors.py...")

# Industry data
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
    # ============================================================
    # NEW: Paint Manufacturing Industry (5th Industry)
    # ============================================================
    'paint': {
        'name': 'Paint Manufacturing (MTS/CTO)',
        'name_fa': 'تولید رنگ (MTS/CTO)',
        'icon': 'fa-paint-roller',
        'description': 'MTS/CTO - Paint and Coatings Manufacturing',
        'scenario': 'MTS',
        'sample_company': 'Pars Alborz Poushesh Co.'
    },
    # ============================================================
    # NEW: Personal Sales / Taraz Tamin (6th Industry)
    # ============================================================
    'personal': {
        'name': 'Personal Sales / Taraz Tamin',
        'name_fa': 'فروش شخصی / تراز تأمین',
        'icon': 'fa-user-tie',
        'description': 'Personal CRM for Taraz Tamin business development',
        'scenario': 'CTO',
        'sample_company': 'Taraz Tamin',
    }
}

def inject_industry_context():
    """Inject industry-related variables into all templates."""
    print("🔄 inject_industry_context called")
    
    # Get industry from session, default to 'valve'
    industry = session.get('demo_industry', 'valve')
    data = INDUSTRY_DATA.get(industry, INDUSTRY_DATA['valve'])
    
    # Get company info from database
    company_info = get_company_info(industry)
    
    # Get calendar from session
    calendar = session.get('calendar', 'gregorian')
    lang = session.get('lang', 'en')
    
    print(f"📋 Industry from session: {industry}")
    print(f"📋 Language from session: {lang}")
    print(f"📋 Calendar from session: {calendar}")
    
    # Build industries list for the dropdown
    industries = []
    for code, info in INDUSTRY_DATA.items():
        industries.append({
            'code': code,
            'name': info['name'],
            'icon': info['icon'],
            'scenario': info['scenario']
        })
    
    print(f"📋 Industries count: {len(industries)}")
    
    return {
        'demo_industry': industry,
        'industry_display': data['name'],
        'industry_icon': data['icon'],
        'industry_description': data['description'],
        'industry_scenario': data['scenario'],
        'sample_company': data['sample_company'],
        'industries': industries,
        'current_calendar': calendar,
        # NEW: Company info for the current industry
        'company': company_info
    }


def get_company_info(industry_code):
    """Get company information for a specific industry."""
    conn = get_db_connection(use_teardown=False)
    if not conn:
        return None
    
    try:
        row = conn.execute("""
            SELECT * FROM t_IndustryCompany WHERE IndustryCode = :code
        """, {'code': industry_code}).fetchone()
        
        if row:
            return dict(row)
    except Exception as e:
        print(f"Error getting company info: {e}")
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
        'roles': flask_session.get('roles', [])
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