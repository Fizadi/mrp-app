# app/modules/auth/api.py
from flask import Blueprint, request, jsonify, session
from flask_login import login_required, current_user
from app import db
from sqlalchemy.sql import text
from datetime import datetime
import uuid

auth_api_bp = Blueprint('auth_api', __name__, url_prefix='/api/auth')


@auth_api_bp.route('/me', methods=['GET'])
@login_required
def get_me():
    """Return the current user's session context — used by tenant switcher and diagnostics."""
    return jsonify({
        'user_id': session.get('user_id'),
        'tenant_id': session.get('tenant_id'),
        'demo_industry': session.get('demo_industry'),
        'lang': session.get('lang'),
    })


@auth_api_bp.route('/tenants', methods=['GET'])
@login_required
def list_tenants():
    """List all active tenants. Only for Global Super Admin (Tenant 0)."""
    if session.get('tenant_id') != 0:
        return jsonify({'error': 'Unauthorized. Super Admin only.'}), 403

    with db.engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT TenantID, CompanyName, LogoURL, PrimaryColor, IsActive 
            FROM t_Tenants 
            ORDER BY TenantID
        """)).fetchall()
        
        tenants = [{
            'tenant_id': r[0],
            'company_name': r[1],
            'logo_url': r[2],
            'primary_color': r[3],
            'is_active': bool(r[4])
        } for r in rows]
        
    return jsonify(tenants)



@auth_api_bp.route('/switch-tenant', methods=['POST'])
@login_required
def switch_tenant():
    data = request.get_json() or {}
    target_tenant_id = data.get('tenant_id')
    
    if target_tenant_id is None:
        return jsonify({'error': 'tenant_id is required'}), 400

    try:
        target_tenant_id = int(target_tenant_id)
    except ValueError:
        return jsonify({'error': 'Invalid tenant_id'}), 400

    # Allow the ORIGINAL Super Admin to switch (detected by user_id=1 or current tenant=0)
    # Normal users (tenant_id 1-6, 8+) cannot switch to other tenants.
    current_tid = session.get('tenant_id')
    is_super_admin = (current_tid == 0 or session.get('user_id') == '1')
    
    if not is_super_admin:
        return jsonify({'error': 'Unauthorized. Only Super Admins can switch tenants.'}), 403

    with db.engine.connect() as conn:
        exists = conn.execute(text("SELECT 1 FROM t_Tenants WHERE TenantID = :tid"), {'tid': target_tenant_id}).first()
        if not exists:
            return jsonify({'error': 'Tenant not found'}), 404

    # §E1: added 9 → 'solar'
    tenant_to_industry = {
        1: 'valve', 2: 'furniture', 3: 'jobshop', 4: 'gauge',
        5: 'paint', 6: 'personal', 7: 'elec', 8: 'valve',
        9: 'solar',
    }

    session['tenant_id'] = target_tenant_id
    session['demo_industry'] = tenant_to_industry.get(target_tenant_id, 'valve')
    session['current_project_id'] = 'All'
    session.modified = True

    return jsonify({
        'status': 'ok',
        'message': f'Switched to Tenant {target_tenant_id}',
        'tenant_id': target_tenant_id,
        'demo_industry': session['demo_industry'],
    })

@auth_api_bp.route('/provision-tenant', methods=['POST'])
@login_required
def provision_tenant():
    """
    Creates a new tenant (Tenant 7+) and its initial admin user.
    Only for Global Super Admin (Tenant 0).
    """
    if session.get('tenant_id') != 0:
        return jsonify({'error': 'Unauthorized. Super Admin only.'}), 403

    data = request.get_json()
    company_name = data.get('company_name')
    admin_username = data.get('admin_username')
    admin_email = data.get('admin_email')
    admin_password = data.get('admin_password')

    if not all([company_name, admin_username, admin_email, admin_password]):
        return jsonify({'error': 'Missing required fields'}), 400

    # Import hash_password from your auth/__init__.py
    from app.modules.auth import hash_password

    today_key = int(datetime.now().strftime('%Y%m%d'))
    hashed_pw = hash_password(admin_password)

    with db.engine.connect() as conn:
        # 1. Check if username/email already exists
        existing = conn.execute(text(
            "SELECT 1 FROM t_Users WHERE Username = :un OR Email = :em"
        ), {'un': admin_username, 'em': admin_email}).first()
        
        if existing:
            return jsonify({'error': 'Username or Email already exists'}), 400

        # 2. Create the Tenant
        # Get the next available TenantID (since demo tenants are 1-7)
        max_tid = conn.execute(text("SELECT MAX(TenantID) FROM t_Tenants")).scalar() or 0
        new_tid = max(max_tid + 1, 8) # Force real clients to start at 8 just in case

        conn.execute(text("""
            INSERT INTO t_Tenants (TenantID, CompanyName, IsActive) 
            VALUES (:tid, :name, 1)
        """), {'tid': new_tid, 'name': company_name})

        # 3. Create the Admin User for this tenant
        new_user_id = str(uuid.uuid4()).upper()
        conn.execute(text("""
            INSERT INTO t_Users (
                UserID, Username, Email, PasswordHash, FullName, 
                IsActive, UserType, TenantID, UserRoles, 
                CreatedDate, CreatedDateKey
            ) VALUES (
                :uid, :un, :em, :pw, :fn, 
                '1', 'Internal', :tid, '["ADMIN"]', 
                :cd, :cdk
            )
        """), {
            'uid': new_user_id, 'un': admin_username, 'em': admin_email, 
            'pw': hashed_pw, 'fn': f"{company_name} Admin", 
            'tid': new_tid, 'cd': datetime.now(), 'cdk': today_key
        })

        # 4. Enable default modules for this new tenant
        # §E2: lowercase + full 6-module set to match Tenant 1 exactly.
        # Context processors and sidebar templates check membership in
        # this list; case-mismatches silently disable modules.
        modules = ['crm', 'finance', 'manufacturing', 'projects',
                   'quality', 'supply_chain']
        for mod in modules:
            conn.execute(text("""
                INSERT INTO t_TenantModules (TenantID, ModuleCode, IsEnabled) 
                VALUES (:tid, :mod, 1)
            """), {'tid': new_tid, 'mod': mod})

        conn.commit()

    return jsonify({
        'status': 'ok',
        'message': 'Tenant provisioned successfully',
        'tenant_id': new_tid,
        'admin_user_id': new_user_id
    }), 201