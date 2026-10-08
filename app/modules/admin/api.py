# app/modules/admin/api.py
import csv
import hashlib
import secrets
import io
from datetime import datetime
from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user
from sqlalchemy.sql import text
from app.modules.admin import admin_required
from datetime import datetime, timedelta
from app.core.menu_permissions import MENU_ITEMS, get_user_permission_set, has_permission


api_bp = Blueprint('admin_api', __name__, url_prefix='/api/admin')

# ----------------------------------------------------------------------
# Password helpers (compatible with existing hash format: salt:sha256)
# ----------------------------------------------------------------------
def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)          # 16 bytes -> 32 hex chars
    pwd_hash = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}:{pwd_hash}"

def verify_password(stored_hash: str, password: str) -> bool:
    if ':' not in stored_hash:
        return False
    salt, pwd_hash = stored_hash.split(':', 1)
    computed = hashlib.sha256((salt + password).encode()).hexdigest()
    return computed == pwd_hash

def get_engine():
    return current_app.extensions['sqlalchemy'].engine

# ----------------------------------------------------------------------
# Helper: get user roles as list of role codes from JSON column
# ----------------------------------------------------------------------
def get_user_roles(conn, user_id: int):
    row = conn.execute(
        text("SELECT UserRoles FROM t_Users WHERE UserID = :uid"),
        {'uid': user_id}
    ).fetchone()
    if row and row[0]:
        import json
        return json.loads(row[0]) if isinstance(row[0], str) else row[0]
    return []

def set_user_roles(conn, user_id: int, role_codes: list):
    import json
    conn.execute(
        text("UPDATE t_Users SET UserRoles = :roles WHERE UserID = :uid"),
        {'roles': json.dumps(role_codes), 'uid': user_id}
    )

# ----------------------------------------------------------------------
# API: list users (with KPI data, filtering)
# ----------------------------------------------------------------------


@api_bp.route('/users/<user_id>', methods=['GET'])
@login_required
@admin_required
def get_user(user_id):
    """Get user details by ID (supports both string and integer IDs)"""
    engine = get_engine()
    with engine.connect() as conn:
        # Handle both string and integer IDs
        if user_id.isdigit():
            query = """
                SELECT UserID, Username, Email, FullName, FullNameLocal, 
                       Department, Position, IsActive, UserType, UserRoles, 
                       Permissions, MustChangePassword, LastLogin, CreatedDate,
                       EmployeeID, LanguageCode, FailedLoginAttempts, 
                       AccountLockedUntil, ModifiedDate, CreatedBy, ModifiedBy,
                       DashboardLayout, DefaultProjectID, NotificationPreferences,
                       QualityCertifications, MaxQualitySkillLevel,
                       QualityAssignmentPreferences, WorkflowPreferences,
                       DelegationChain, NotificationSettings, CompanyID,
                       PortalAccess, PortalPermissions, DefaultCalendar
                FROM t_Users 
                WHERE UserID = :id
            """
        else:
            query = """
                SELECT UserID, Username, Email, FullName, FullNameLocal, 
                       Department, Position, IsActive, UserType, UserRoles, 
                       Permissions, MustChangePassword, LastLogin, CreatedDate,
                       EmployeeID, LanguageCode, FailedLoginAttempts, 
                       AccountLockedUntil, ModifiedDate, CreatedBy, ModifiedBy,
                       DashboardLayout, DefaultProjectID, NotificationPreferences,
                       QualityCertifications, MaxQualitySkillLevel,
                       QualityAssignmentPreferences, WorkflowPreferences,
                       DelegationChain, NotificationSettings, CompanyID,
                       PortalAccess, PortalPermissions, DefaultCalendar
                FROM t_Users 
                WHERE UserID = :id
            """
        
        row = conn.execute(text(query), {'id': user_id}).fetchone()
        
        if not row:
            return jsonify({'success': False, 'error': 'User not found'}), 404
        
        # Parse JSON fields
        user_roles = []
        if row[9]:  # UserRoles column index
            try:
                user_roles = json.loads(row[9]) if isinstance(row[9], str) else row[9]
            except:
                user_roles = []
        
        permissions = {}
        if row[10]:  # Permissions column index
            try:
                permissions = json.loads(row[10]) if isinstance(row[10], str) else row[10]
            except:
                permissions = {}
        
        user = {
            'UserID': row[0],
            'Username': row[1],
            'Email': row[2],
            'FullName': row[3],
            'FullNameLocal': row[4] or '',
            'Department': row[5] or '',
            'Position': row[6] or '',
            'IsActive': row[7] in (1, '1', True, 'true'),
            'UserType': row[8] or 'Internal',
            'UserRoles': user_roles,
            'Permissions': permissions,
            'MustChangePassword': row[11] in (1, '1', True, 'true'),
            'LastLogin': row[12] if row[12] else None,
            'CreatedDate': row[13] if row[13] else None,
            'EmployeeID': row[14] if row[14] else '',
            'LanguageCode': row[15] if row[15] else 'en',
            'DefaultCalendar': row[34] if len(row) > 34 and row[34] else 'gregorian'
        }
        
        # Also get roles from t_Roles for the dropdown
        roles_rows = conn.execute(text("SELECT RoleCode, RoleName FROM t_Roles ORDER BY RoleName")).fetchall()
        all_roles = [{'RoleCode': r[0], 'RoleName': r[1]} for r in roles_rows]
        user['AllRoles'] = all_roles
        
        return jsonify({'success': True, 'data': user})



@api_bp.route('/users')
@login_required
def list_users():
    print("list_users called")
    try:
        department = request.args.get('department', '')
        role_filter = request.args.get('role', '')
        active_status = request.args.get('active_status', '')
        
        engine = get_engine()
        with engine.connect() as conn:
            # Base query
            query = """
                SELECT UserID, Username, Email, FullName, Department, Position,
                       IsActive, LastLogin, EmployeeID, MustChangePassword,
                       UserRoles, CreatedDate
                FROM t_Users
                WHERE UserType = 'Internal'
            """
            params = {}
            if department:
                query += " AND Department = :dept"
                params['dept'] = department
            if active_status in ('0', '1'):
                query += " AND IsActive = :active"
                params['active'] = int(active_status)
            query += " ORDER BY FullName"
            
            rows = conn.execute(text(query), params).fetchall()
            
            # Fetch all roles for mapping
            roles_rows = conn.execute(text("SELECT RoleCode, RoleName FROM t_Roles")).fetchall()
            all_roles = {r[0]: r[1] for r in roles_rows}
            
            users = []
            # Role filter (client-side because SQLite JSON support may be limited)
            for row in rows:
                user_roles = json.loads(row[10]) if row[10] else []  # UserRoles column
                if role_filter and role_filter not in user_roles:
                    continue
                # Get role display names
                role_names = [all_roles.get(r, r) for r in user_roles]
                users.append({
                    'UserID': row[0],
                    'Username': row[1],
                    'Email': row[2],
                    'FullName': row[3],
                    'Department': row[4],
                    'Position': row[5],
                    'IsActive': bool(row[6]),
                    'LastLogin': row[7] if row[7] else None,
                    'EmployeeID': row[8],
                    'MustChangePassword': bool(row[9]),
                    'Roles': user_roles,
                    'RoleNames': role_names,
                    'CreatedDate': row[11] if row[11] else None
                })
        
        # KPI: Active users count and breakdown by role
        active_users = [u for u in users if u['IsActive']]
        kpi_active = len(active_users)
        kpi_by_role = {}
        for u in active_users:
            for r in u['Roles']:
                kpi_by_role[r] = kpi_by_role.get(r, 0) + 1
        
        return jsonify({
            'users': users,
            'kpi': {
                'active_users': kpi_active,
                'by_role': [{'role': r, 'count': c} for r, c in kpi_by_role.items()]
            }
        })
    
    except Exception as e:
        print(f"Error in list_users: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

# ----------------------------------------------------------------------
# API: create new internal user
# ----------------------------------------------------------------------
@api_bp.route('/users', methods=['POST'])
@login_required
def create_user():
    data = request.get_json()
    required = ['Username', 'Email', 'FullName', 'Password']
    for f in required:
        if f not in data:
            return jsonify({'error': f'Missing {f}'}), 400
    
    engine = get_engine()
    with engine.connect() as conn:
        # Check if username exists
        existing = conn.execute(text("SELECT UserID FROM t_Users WHERE Username = :u"), {'u': data['Username']}).fetchone()
        if existing:
            return jsonify({'error': 'Username already exists'}), 409
        
        # Hash password
        pwd_hash = hash_password(data['Password'])
        
        # Insert new user
        now = datetime.now().isoformat()
        insert_sql = """
            INSERT INTO t_Users 
            (Username, Email, PasswordHash, FullName, Department, Position, 
             EmployeeID, IsActive, MustChangePassword, UserType, CreatedDate, ModifiedDate, CreatedBy, ModifiedBy, UserRoles)
            VALUES 
            (:username, :email, :pwdhash, :fullname, :dept, :pos, 
             :empid, :active, :mustchange, 'Internal', :created, :modified, :createdby, :modifiedby, :roles)
        """
        roles_json = json.dumps(data.get('Roles', []))
        conn.execute(text(insert_sql), {
            'username': data['Username'],
            'email': data['Email'],
            'pwdhash': pwd_hash,
            'fullname': data['FullName'],
            'dept': data.get('Department', ''),
            'pos': data.get('Position', ''),
            'empid': data.get('EmployeeID', ''),
            'active': data.get('IsActive', True),
            'mustchange': data.get('MustChangePassword', False),
            'created': now,
            'modified': now,
            'createdby': current_user.get_id(),
            'modifiedby': current_user.get_id(),
            'roles': roles_json
        })
        conn.commit()
        
        # Get new user ID
        new_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()
    
    return jsonify({'message': 'User created', 'UserID': new_id}), 201

# ----------------------------------------------------------------------
# API: update user (except password)
# ----------------------------------------------------------------------
@api_bp.route('/users/<int:user_id>', methods=['PUT'])
@login_required
def update_user(user_id):
    data = request.get_json()
    allowed_fields = ['Email', 'FullName', 'Department', 'Position', 'EmployeeID', 
                      'IsActive', 'MustChangePassword', 'Roles']
    
    engine = get_engine()
    with engine.connect() as conn:
        # Build update dynamically
        updates = []
        params = {'uid': user_id}
        for field in allowed_fields:
            if field in data:
                if field == 'Roles':
                    updates.append("UserRoles = :roles")
                    params['roles'] = json.dumps(data['Roles'])
                else:
                    updates.append(f"{field} = :{field}")
                    params[field] = data[field]
        if updates:
            updates.append("ModifiedDate = :mod")
            params['mod'] = datetime.now().isoformat()
            updates.append("ModifiedBy = :modby")
            params['modby'] = current_user.get_id()
            sql = f"UPDATE t_Users SET {', '.join(updates)} WHERE UserID = :uid"
            conn.execute(text(sql), params)
            conn.commit()
    
    return jsonify({'message': 'User updated'})

# ----------------------------------------------------------------------
# API: delete (deactivate) user - set IsActive = 0
# ----------------------------------------------------------------------
@api_bp.route('/users/<int:user_id>', methods=['DELETE'])
@login_required
def delete_user(user_id):
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("UPDATE t_Users SET IsActive = 0, ModifiedDate = :mod WHERE UserID = :uid"),
                     {'mod': datetime.now().isoformat(), 'uid': user_id})
        conn.commit()
    return jsonify({'message': 'User deactivated'})

# ----------------------------------------------------------------------
# API: reset password (with history check)
# ----------------------------------------------------------------------
@api_bp.route('/users/<int:user_id>/reset-password', methods=['POST'])
@login_required
def reset_password(user_id):
    data = request.get_json()
    new_password = data.get('new_password')
    if not new_password or len(new_password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400
    
    engine = get_engine()
    with engine.connect() as conn:
        # Check password history (last 3)
        history = conn.execute(
            text("SELECT PasswordHash FROM t_PasswordHistory WHERE UserID = :uid ORDER BY ChangedDate DESC LIMIT 3"),
            {'uid': user_id}
        ).fetchall()
        for h in history:
            if verify_password(h[0], new_password):
                return jsonify({'error': 'Password already used recently'}), 400
        
        # Hash new password
        new_hash = hash_password(new_password)
        # Update user
        conn.execute(text("UPDATE t_Users SET PasswordHash = :hash, MustChangePassword = 0, ModifiedDate = :mod WHERE UserID = :uid"),
                     {'hash': new_hash, 'mod': datetime.now().isoformat(), 'uid': user_id})
        # Insert into password history
        conn.execute(text("INSERT INTO t_PasswordHistory (UserID, PasswordHash, ChangedDate) VALUES (:uid, :hash, :changed)"),
                     {'uid': user_id, 'hash': new_hash, 'changed': datetime.now().isoformat()})
        conn.commit()
    
    return jsonify({'message': 'Password reset successfully'})

# ----------------------------------------------------------------------
# API: get active sessions for a user
# ----------------------------------------------------------------------
@api_bp.route('/users/<int:user_id>/sessions')
@login_required
def get_user_sessions(user_id):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT SessionID, LoginTime, LastActivity, IPAddress, UserAgent, IsActive
            FROM t_UserSessions
            WHERE UserID = :uid AND IsActive = 1
            ORDER BY LastActivity DESC
        """), {'uid': user_id}).fetchall()
    
    sessions = []
    for r in rows:
        sessions.append({
            'SessionID': r[0],
            'LoginTime': r[1] if r[1] else None,
            'LastActivity': r[2] if r[2] else None,
            'IPAddress': r[3],
            'UserAgent': r[4][:80] + '...' if len(r[4] or '') > 80 else r[4],
            'IsActive': bool(r[5])
        })
    return jsonify(sessions)

# ----------------------------------------------------------------------
# API: terminate a session
# ----------------------------------------------------------------------
@api_bp.route('/users/<int:user_id>/sessions/<session_id>', methods=['DELETE'])
@login_required
def terminate_session(user_id, session_id):
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("UPDATE t_UserSessions SET IsActive = 0 WHERE UserID = :uid AND SessionID = :sid"),
                     {'uid': user_id, 'sid': session_id})
        conn.commit()
    return jsonify({'message': 'Session terminated'})

# ----------------------------------------------------------------------
# API: import users from CSV
# ----------------------------------------------------------------------
@api_bp.route('/users/import', methods=['POST'])
@login_required
def import_users():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    file = request.files['file']
    if not file.filename.endswith('.csv'):
        return jsonify({'error': 'Only CSV files accepted'}), 400
    
    stream = io.TextIOWrapper(file.stream, encoding='utf-8')
    reader = csv.DictReader(stream)
    engine = get_engine()
    created = 0
    errors = []
    
    with engine.connect() as conn:
        for row in reader:
            username = row.get('Username')
            email = row.get('Email')
            fullname = row.get('FullName')
            if not username or not email or not fullname:
                errors.append(f"Missing required fields in row: {row}")
                continue
            
            # Check existing
            existing = conn.execute(text("SELECT UserID FROM t_Users WHERE Username = :u"), {'u': username}).fetchone()
            if existing:
                errors.append(f"Username {username} already exists, skipped")
                continue
            
            # Default password = 'Ch@ngeMe123' + force change
            default_pwd = 'Ch@ngeMe123'
            pwd_hash = hash_password(default_pwd)
            
            roles = row.get('Roles', '')
            roles_list = [r.strip() for r in roles.split('|') if r.strip()]
            roles_json = json.dumps(roles_list)
            
            now = datetime.now().isoformat()
            conn.execute(text("""
                INSERT INTO t_Users 
                (Username, Email, PasswordHash, FullName, Department, Position, EmployeeID, 
                 IsActive, MustChangePassword, UserType, CreatedDate, ModifiedDate, UserRoles)
                VALUES 
                (:un, :em, :pwd, :fn, :dept, :pos, :eid, :active, 1, 'Internal', :cdate, :mdate, :roles)
            """), {
                'un': username,
                'em': email,
                'pwd': pwd_hash,
                'fn': fullname,
                'dept': row.get('Department', ''),
                'pos': row.get('Position', ''),
                'eid': row.get('EmployeeID', ''),
                'active': row.get('IsActive', '1').lower() in ('1', 'true', 'yes'),
                'cdate': now,
                'mdate': now,
                'roles': roles_json
            })
            created += 1
        conn.commit()
    
    return jsonify({'message': f'Imported {created} users', 'errors': errors})

# ----------------------------------------------------------------------
# API: export users to CSV
# ----------------------------------------------------------------------
@api_bp.route('/users/export', methods=['GET'])
@login_required
def export_users():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT Username, Email, FullName, Department, Position, EmployeeID, 
                   IsActive, UserRoles, CreatedDate
            FROM t_Users
            WHERE UserType = 'Internal'
            ORDER BY FullName
        """)).fetchall()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Username', 'Email', 'FullName', 'Department', 'Position', 'EmployeeID', 'IsActive', 'Roles', 'CreatedDate'])
    for r in rows:
        roles = json.loads(r[7]) if r[7] else []
        writer.writerow([
            r[0], r[1], r[2], r[3] or '', r[4] or '', r[5] or '',
            'Active' if r[6] else 'Inactive',
            '|'.join(roles),
            r[8] if r[8] else ''
        ])
    
    return current_app.response_class(
        output.getvalue().encode('utf-8'),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=internal_users.csv'}
    )

from functools import wraps
from flask import flash, redirect, url_for
from flask_login import current_user
from sqlalchemy.sql import text
import json

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login'))
        # Get user roles from database (or session)
        from app import db
        with db.engine.connect() as conn:
            row = conn.execute(text("SELECT UserRoles FROM t_Users WHERE UserID = :uid"), {'uid': current_user.get_id()}).fetchone()
            roles = json.loads(row[0]) if row and row[0] else []
            if 'ADMIN' not in roles:
                flash('Admin access required', 'danger')
                return redirect(url_for('dashboard.dashboard'))
        return f(*args, **kwargs)
    return decorated
    

# ----------------------------------------------------------------------
# External Users Management (reuses t_Users + t_companies)
# ----------------------------------------------------------------------

@api_bp.route('/external-users')
@login_required
@admin_required
def list_external_users():
    """List external users with company info and KPIs"""
    company_type = request.args.get('company_type', '')   # 'Customer', 'Supplier', or ''
    active_status = request.args.get('active_status', '')
    
    engine = get_engine()
    with engine.connect() as conn:
        # Base query joining t_Users and t_companies
        query = """
            SELECT u.UserID, u.Username, u.Email, u.FullName, u.IsActive, u.LastLogin,
                   u.UserRoles, u.PortalPermissions, u.PortalAccess,
                   c.companyID, c.Name as CompanyName, c.IsCustomer, c.IsSupplier
            FROM t_Users u
            LEFT JOIN t_companies c ON u.CompanyID = c.companyID
            WHERE u.UserType = 'External' AND u.PortalAccess = 1
        """
        params = {}
        if active_status in ('0', '1'):
            query += " AND u.IsActive = :active"
            params['active'] = int(active_status)
        if company_type == 'Customer':
            query += " AND c.IsCustomer = 1"
        elif company_type == 'Supplier':
            query += " AND c.IsSupplier = 1"
        query += " ORDER BY u.FullName"
        
        rows = conn.execute(text(query), params).fetchall()
        
        users = []
        for row in rows:
            users.append({
                'UserID': row[0],
                'Username': row[1],
                'Email': row[2],
                'FullName': row[3],
                'IsActive': bool(row[4]),
                'LastLogin': row[5] if row[5] else None,
                'UserRoles': json.loads(row[6]) if row[6] else [],
                'PortalPermissions': row[7],
                'PortalAccess': bool(row[8]),
                'CompanyID': row[9],
                'CompanyName': row[10],
                'IsCustomer': bool(row[11]),
                'IsSupplier': bool(row[12])
            })
    
    # KPI: active external users
    active_users = [u for u in users if u['IsActive']]
    kpi_active = len(active_users)
    # by company type
    kpi_by_type = {'Customer': 0, 'Supplier': 0}
    for u in active_users:
        if u['IsCustomer']:
            kpi_by_type['Customer'] += 1
        if u['IsSupplier']:
            kpi_by_type['Supplier'] += 1
    
    return jsonify({
        'users': users,
        'kpi': {
            'active_users': kpi_active,
            'by_company_type': [{'type': t, 'count': c} for t, c in kpi_by_type.items()]
        }
    })

@api_bp.route('/external-users/companies')
@login_required
@admin_required
def get_external_companies():
    """Return companies that are customers or suppliers (for dropdown)"""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT companyID, Name, IsCustomer, IsSupplier
            FROM t_companies
            WHERE IsCustomer = 1 OR IsSupplier = 1
            ORDER BY Name
        """)).fetchall()
    companies = [{'id': r[0], 'name': r[1], 'isCustomer': bool(r[2]), 'isSupplier': bool(r[3])} for r in rows]
    return jsonify(companies)

@api_bp.route('/external-users', methods=['POST'])
@login_required
@admin_required
def create_external_user():
    data = request.get_json()
    required = ['Username', 'Email', 'FullName', 'CompanyID']
    for f in required:
        if f not in data:
            return jsonify({'error': f'Missing {f}'}), 400
    
    engine = get_engine()
    with engine.connect() as conn:
        # Check username unique
        existing = conn.execute(text("SELECT UserID FROM t_Users WHERE Username = :u"), {'u': data['Username']}).fetchone()
        if existing:
            return jsonify({'error': 'Username already exists'}), 409
        
        # Default password (force change on first login)
        default_password = 'Portal@2024'
        pwd_hash = hash_password(default_password)
        
        now = datetime.now().isoformat()
        roles_json = json.dumps(data.get('UserRoles', []))
        portal_perms = data.get('PortalPermissions', '')
        
        insert_sql = """
            INSERT INTO t_Users
            (Username, Email, PasswordHash, FullName, CompanyID, UserType, PortalAccess,
             IsActive, MustChangePassword, UserRoles, PortalPermissions,
             CreatedDate, ModifiedDate, CreatedBy, ModifiedBy)
            VALUES
            (:un, :em, :pwd, :fn, :cid, 'External', 1,
             :active, 1, :roles, :perms,
             :cdate, :mdate, :cby, :mby)
        """
        conn.execute(text(insert_sql), {
            'un': data['Username'],
            'em': data['Email'],
            'pwd': pwd_hash,
            'fn': data['FullName'],
            'cid': data['CompanyID'],
            'active': data.get('IsActive', True),
            'roles': roles_json,
            'perms': portal_perms,
            'cdate': now,
            'mdate': now,
            'cby': current_user.get_id(),
            'mby': current_user.get_id()
        })
        conn.commit()
        
        new_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()
    
    # Optionally log to audit log
    log_audit('CREATE_EXTERNAL_USER', f"Created external user {data['Username']}", new_id)
    return jsonify({'message': 'External user created', 'UserID': new_id}), 201

@api_bp.route('/external-users/<int:user_id>', methods=['PUT'])
@login_required
@admin_required
def update_external_user(user_id):
    data = request.get_json()
    allowed = ['Email', 'FullName', 'CompanyID', 'IsActive', 'UserRoles', 'PortalPermissions']
    
    engine = get_engine()
    with engine.connect() as conn:
        updates = []
        params = {'uid': user_id}
        for field in allowed:
            if field in data:
                if field == 'UserRoles':
                    updates.append("UserRoles = :roles")
                    params['roles'] = json.dumps(data['UserRoles'])
                else:
                    updates.append(f"{field} = :{field}")
                    params[field] = data[field]
        if updates:
            updates.append("ModifiedDate = :mod")
            params['mod'] = datetime.now().isoformat()
            updates.append("ModifiedBy = :modby")
            params['modby'] = current_user.get_id()
            sql = f"UPDATE t_Users SET {', '.join(updates)} WHERE UserID = :uid"
            conn.execute(text(sql), params)
            conn.commit()
            log_audit('UPDATE_EXTERNAL_USER', f"Updated external user ID {user_id}", user_id)
    
    return jsonify({'message': 'External user updated'})

@api_bp.route('/external-users/<int:user_id>', methods=['DELETE'])
@login_required
@admin_required
def delete_external_user(user_id):
    """Soft delete: set IsActive = 0"""
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("UPDATE t_Users SET IsActive = 0, ModifiedDate = :mod WHERE UserID = :uid"),
                     {'mod': datetime.now().isoformat(), 'uid': user_id})
        conn.commit()
        log_audit('DEACTIVATE_EXTERNAL_USER', f"Deactivated external user ID {user_id}", user_id)
    return jsonify({'message': 'External user deactivated'})

@api_bp.route('/external-users/<int:user_id>/reset-password', methods=['POST'])
@login_required
@admin_required
def reset_external_user_password(user_id):
    data = request.get_json()
    new_password = data.get('new_password')
    if not new_password or len(new_password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400
    
    engine = get_engine()
    with engine.connect() as conn:
        new_hash = hash_password(new_password)
        conn.execute(text("UPDATE t_Users SET PasswordHash = :hash, MustChangePassword = 1, ModifiedDate = :mod WHERE UserID = :uid"),
                     {'hash': new_hash, 'mod': datetime.now().isoformat(), 'uid': user_id})
        # Insert into password history
        conn.execute(text("INSERT INTO t_PasswordHistory (UserID, PasswordHash, ChangedDate) VALUES (:uid, :hash, :changed)"),
                     {'uid': user_id, 'hash': new_hash, 'changed': datetime.now().isoformat()})
        conn.commit()
        log_audit('RESET_PASSWORD', f"Reset password for external user ID {user_id}", user_id)
    return jsonify({'message': 'Password reset successfully'})

@api_bp.route('/external-users/<int:user_id>/audit-log')
@login_required
@admin_required
def get_external_user_audit_log(user_id):
    """Retrieve audit log entries for this user"""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT LogID, Timestamp, Action, Details, UserID as ActorID
            FROM t_AuditLog
            WHERE AffectedUserID = :uid OR Details LIKE :pattern
            ORDER BY Timestamp DESC
            LIMIT 50
        """), {'uid': user_id, 'pattern': f'%user ID {user_id}%'}).fetchall()
    logs = [{'id': r[0], 'timestamp': r[1] if r[1] else None, 'action': r[2], 'details': r[3], 'actor_id': r[4]} for r in rows]
    return jsonify(logs)

@api_bp.route('/external-users/import', methods=['POST'])
@login_required
@admin_required
def import_external_users():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    file = request.files['file']
    if not file.filename.endswith('.csv'):
        return jsonify({'error': 'Only CSV files accepted'}), 400
    
    stream = io.TextIOWrapper(file.stream, encoding='utf-8')
    reader = csv.DictReader(stream)
    engine = get_engine()
    created = 0
    errors = []
    
    with engine.connect() as conn:
        for row in reader:
            username = row.get('Username')
            email = row.get('Email')
            fullname = row.get('FullName')
            company_name = row.get('CompanyName')
            if not username or not email or not fullname or not company_name:
                errors.append(f"Missing required fields: {row}")
                continue
            
            # Find company ID by name
            comp_row = conn.execute(text("SELECT companyID FROM t_companies WHERE Name = :name"), {'name': company_name}).fetchone()
            if not comp_row:
                errors.append(f"Company not found: {company_name}")
                continue
            company_id = comp_row[0]
            
            # Check existing
            existing = conn.execute(text("SELECT UserID FROM t_Users WHERE Username = :u"), {'u': username}).fetchone()
            if existing:
                errors.append(f"Username {username} already exists, skipped")
                continue
            
            default_pwd = 'Portal@2024'
            pwd_hash = hash_password(default_pwd)
            roles = row.get('Roles', '')
            roles_list = [r.strip() for r in roles.split('|') if r.strip()]
            roles_json = json.dumps(roles_list)
            portal_perms = row.get('PortalPermissions', '')
            is_active = row.get('IsActive', '1').lower() in ('1', 'true', 'yes')
            
            now = datetime.now().isoformat()
            conn.execute(text("""
                INSERT INTO t_Users
                (Username, Email, PasswordHash, FullName, CompanyID, UserType, PortalAccess,
                 IsActive, MustChangePassword, UserRoles, PortalPermissions,
                 CreatedDate, ModifiedDate, CreatedBy, ModifiedBy)
                VALUES
                (:un, :em, :pwd, :fn, :cid, 'External', 1,
                 :active, 1, :roles, :perms,
                 :cdate, :mdate, :cby, :mby)
            """), {
                'un': username, 'em': email, 'pwd': pwd_hash, 'fn': fullname,
                'cid': company_id, 'active': is_active, 'roles': roles_json,
                'perms': portal_perms, 'cdate': now, 'mdate': now,
                'cby': current_user.get_id(), 'mby': current_user.get_id()
            })
            created += 1
        conn.commit()
    
    return jsonify({'message': f'Imported {created} external users', 'errors': errors})

@api_bp.route('/external-users/export', methods=['GET'])
@login_required
@admin_required
def export_external_users():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT u.Username, u.Email, u.FullName, c.Name as CompanyName,
                   u.IsActive, u.UserRoles, u.PortalPermissions, u.CreatedDate
            FROM t_Users u
            LEFT JOIN t_companies c ON u.CompanyID = c.companyID
            WHERE u.UserType = 'External' AND u.PortalAccess = 1
            ORDER BY u.FullName
        """)).fetchall()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Username', 'Email', 'FullName', 'CompanyName', 'IsActive', 'Roles', 'PortalPermissions', 'CreatedDate'])
    for r in rows:
        roles = json.loads(r[5]) if r[5] else []
        writer.writerow([
            r[0], r[1], r[2], r[3],
            'Active' if r[4] else 'Inactive',
            '|'.join(roles),
            r[6] or '',
            r[7] if r[7] else ''
        ])
    
    return current_app.response_class(
        output.getvalue().encode('utf-8'),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=external_users.csv'}
    )

def log_audit(action, details, affected_user_id=None):
    """Helper to write to t_AuditLog (if table exists)"""
    try:
        from app import db
        with db.engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO t_AuditLog (Timestamp, UserID, Action, Details, AffectedUserID)
                VALUES (CURRENT_TIMESTAMP, :uid, :action, :details, :affected)
            """), {'uid': current_user.get_id(), 'action': action, 'details': details, 'affected': affected_user_id})
            conn.commit()
    except Exception as e:
        print(f"Audit log error: {e}")


# ----------------------------------------------------------------------
# Role & Permissions Management
# ----------------------------------------------------------------------

@api_bp.route('/permissions/modules')
@login_required
@admin_required
def get_permission_modules():
    """Return distinct modules from t_Permissions"""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT DISTINCT Module FROM t_Permissions WHERE IsActive = 1 ORDER BY Module")).fetchall()
    modules = [r[0] for r in rows]
    return jsonify(modules)

@api_bp.route('/permissions')
@login_required
@admin_required
def get_all_permissions():
    """Return all permissions grouped by module"""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT PermissionID, PermissionCode, Module, PermissionName, Description
            FROM t_Permissions
            WHERE IsActive = 1
            ORDER BY Module, PermissionName
        """)).fetchall()
    permissions_by_module = {}
    for row in rows:
        module = row[2]
        if module not in permissions_by_module:
            permissions_by_module[module] = []
        permissions_by_module[module].append({
            'id': row[0],
            'code': row[1],
            'name': row[3],
            'description': row[4]
        })
    return jsonify(permissions_by_module)

@api_bp.route('/roles')
@login_required
@admin_required
def get_roles():
    """List all roles with permission summary"""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT RoleID, RoleCode, RoleName, Description, Permissions, IsSystemRole,
                   (SELECT COUNT(*) FROM t_Users WHERE json_extract(UserRoles, '$') LIKE '%' || RoleCode || '%') as UserCount
            FROM t_Roles
            ORDER BY RoleName
        """)).fetchall()
    roles = []
    for r in rows:
        # Parse Permissions JSON
        perms = json.loads(r[4]) if r[4] else {}
        roles.append({
            'id': r[0],
            'code': r[1],
            'name': r[2],
            'description': r[3],
            'permissions': perms,
            'isSystem': bool(r[5]),
            'userCount': r[6]
        })
    return jsonify(roles)

@api_bp.route('/roles', methods=['POST'])
@login_required
@admin_required
def create_role():
    data = request.get_json()
    required = ['RoleCode', 'RoleName']
    for f in required:
        if not data.get(f):
            return jsonify({'error': f'Missing {f}'}), 400
    engine = get_engine()
    with engine.connect() as conn:
        # Check duplicate code
        existing = conn.execute(text("SELECT RoleID FROM t_Roles WHERE RoleCode = :code"), {'code': data['RoleCode']}).fetchone()
        if existing:
            return jsonify({'error': 'Role code already exists'}), 409
        # Generate date keys
        today_key = int(datetime.now().strftime('%Y%m%d'))
        perms_json = json.dumps(data.get('Permissions', {}))
        workflow_json = json.dumps(data.get('WorkflowRights', {}))
        insert_sql = """
            INSERT INTO t_Roles
            (RoleCode, RoleName, Description, Permissions, WorkflowRights, IsSystemRole, CreatedDateKey, ModifiedDateKey)
            VALUES
            (:code, :name, :desc, :perms, :wf, 0, :cdate, :mdate)
        """
        conn.execute(text(insert_sql), {
            'code': data['RoleCode'],
            'name': data['RoleName'],
            'desc': data.get('Description', ''),
            'perms': perms_json,
            'wf': workflow_json,
            'cdate': today_key,
            'mdate': today_key
        })
        conn.commit()
        new_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()
    return jsonify({'message': 'Role created', 'RoleID': new_id}), 201

@api_bp.route('/roles/<int:role_id>', methods=['PUT'])
@login_required
@admin_required
def update_role(role_id):
    data = request.get_json()
    allowed = ['RoleName', 'Description', 'Permissions', 'WorkflowRights']
    engine = get_engine()
    with engine.connect() as conn:
        # Check if system role – system roles cannot be renamed or have certain fields changed? We'll allow but warn frontend.
        updates = []
        params = {'rid': role_id, 'mdate': int(datetime.now().strftime('%Y%m%d'))}
        for field in allowed:
            if field in data:
                if field == 'Permissions':
                    updates.append("Permissions = :perms")
                    params['perms'] = json.dumps(data['Permissions'])
                elif field == 'WorkflowRights':
                    updates.append("WorkflowRights = :wf")
                    params['wf'] = json.dumps(data['WorkflowRights'])
                else:
                    updates.append(f"{field} = :{field}")
                    params[field] = data[field]
        if updates:
            updates.append("ModifiedDateKey = :mdate")
            sql = f"UPDATE t_Roles SET {', '.join(updates)} WHERE RoleID = :rid"
            conn.execute(text(sql), params)
            conn.commit()
    return jsonify({'message': 'Role updated'})

@api_bp.route('/roles/<int:role_id>', methods=['DELETE'])
@login_required
@admin_required
def delete_role(role_id):
    engine = get_engine()
    with engine.connect() as conn:
        # Check if any user has this role
        role_info = conn.execute(text("SELECT RoleCode, IsSystemRole FROM t_Roles WHERE RoleID = :rid"), {'rid': role_id}).fetchone()
        if not role_info:
            return jsonify({'error': 'Role not found'}), 404
        if role_info[1]:  # system role
            return jsonify({'error': 'Cannot delete system role'}), 403
        # Check users
        user_with_role = conn.execute(
            text("SELECT COUNT(*) FROM t_Users WHERE json_extract(UserRoles, '$') LIKE '%' || :code || '%'"),
            {'code': role_info[0]}
        ).scalar()
        if user_with_role > 0:
            return jsonify({'error': f'Cannot delete role: {user_with_role} user(s) still have this role'}), 409
        conn.execute(text("DELETE FROM t_Roles WHERE RoleID = :rid"), {'rid': role_id})
        conn.commit()
    return jsonify({'message': 'Role deleted'})

@api_bp.route('/user-exceptions')
@login_required
@admin_required
def get_user_exceptions():
    """List users who have custom permissions (non-empty Permissions JSON)"""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT UserID, Username, FullName, Email, Permissions
            FROM t_Users
            WHERE Permissions IS NOT NULL AND Permissions != '{}' AND Permissions != ''
            ORDER BY FullName
        """)).fetchall()
    users = []
    for r in rows:
        users.append({
            'id': r[0],
            'username': r[1],
            'fullname': r[2],
            'email': r[3],
            'permissions': json.loads(r[4]) if r[4] else {}
        })
    return jsonify(users)

@api_bp.route('/user-exceptions/<int:user_id>', methods=['PUT'])
@login_required
@admin_required
def update_user_exception(user_id):
    data = request.get_json()
    permissions = data.get('Permissions', {})
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("UPDATE t_Users SET Permissions = :perms WHERE UserID = :uid"),
                     {'perms': json.dumps(permissions), 'uid': user_id})
        conn.commit()
    return jsonify({'message': 'User permissions updated'})

@api_bp.route('/user-exceptions/<int:user_id>', methods=['DELETE'])
@login_required
@admin_required
def delete_user_exception(user_id):
    """Remove custom permissions (set to NULL)"""
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("UPDATE t_Users SET Permissions = NULL WHERE UserID = :uid"), {'uid': user_id})
        conn.commit()
    return jsonify({'message': 'Custom permissions removed'})

# ----------------------------------------------------------------------
# Entity Permissions (Project-level)
# ----------------------------------------------------------------------
@api_bp.route('/entity-permissions')
@login_required
@admin_required
def get_entity_permissions():
    """Get all project-role permission assignments"""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT ep.EntityPermissionID, ep.ProjectID, p.ProjectName,
                   ep.RoleID, r.RoleName, ep.PermissionCode, ep.IsAllowed, ep.ExpiryDateKey
            FROM t_EntityPermission ep
            LEFT JOIN t_Project p ON ep.ProjectID = p.ProjectID
            LEFT JOIN t_Roles r ON ep.RoleID = r.RoleID
            ORDER BY p.ProjectName, r.RoleName, ep.PermissionCode
        """)).fetchall()
    perms = []
    for r in rows:
        perms.append({
            'id': r[0],
            'projectId': r[1],
            'projectName': r[2],
            'roleId': r[3],
            'roleName': r[4],
            'permissionCode': r[5],
            'isAllowed': bool(r[6]),
            'expiryDateKey': r[7]
        })
    return jsonify(perms)

@api_bp.route('/entity-permissions', methods=['POST'])
@login_required
@admin_required
def add_entity_permission():
    data = request.get_json()
    required = ['ProjectID', 'RoleID', 'PermissionCode']
    for f in required:
        if not data.get(f):
            return jsonify({'error': f'Missing {f}'}), 400
    engine = get_engine()
    with engine.connect() as conn:
        # Check if identical exists
        existing = conn.execute(text("""
            SELECT EntityPermissionID FROM t_EntityPermission
            WHERE ProjectID = :proj AND RoleID = :role AND PermissionCode = :perm
        """), {'proj': data['ProjectID'], 'role': data['RoleID'], 'perm': data['PermissionCode']}).fetchone()
        if existing:
            return jsonify({'error': 'Permission already defined for this project/role'}), 409
        expiry = data.get('ExpiryDateKey')
        insert_sql = """
            INSERT INTO t_EntityPermission (ProjectID, RoleID, PermissionCode, IsAllowed, ExpiryDateKey)
            VALUES (:proj, :role, :perm, :allowed, :expiry)
        """
        conn.execute(text(insert_sql), {
            'proj': data['ProjectID'],
            'role': data['RoleID'],
            'perm': data['PermissionCode'],
            'allowed': data.get('IsAllowed', 1),
            'expiry': expiry if expiry else None
        })
        conn.commit()
    return jsonify({'message': 'Entity permission added'}), 201

@api_bp.route('/entity-permissions/<int:entity_perm_id>', methods=['DELETE'])
@login_required
@admin_required
def delete_entity_permission(entity_perm_id):
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM t_EntityPermission WHERE EntityPermissionID = :id"), {'id': entity_perm_id})
        conn.commit()
    return jsonify({'message': 'Entity permission removed'})

# Helper: get all projects for dropdown
@api_bp.route('/projects/list')
@login_required
@admin_required
def get_projects_list():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT ProjectID, ProjectName FROM t_Project ORDER BY ProjectName")).fetchall()
    return jsonify([{'id': r[0], 'name': r[1]} for r in rows])
    
 
@api_bp.route('/all-users')
@login_required
@admin_required
def get_all_users():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT UserID, Username, FullName, Email, UserType FROM t_Users ORDER BY FullName")).fetchall()
    return jsonify([{'id': r[0], 'username': r[1], 'fullname': r[2], 'email': r[3], 'type': r[4]} for r in rows])
 
# ----------------------------------------------------------------------
# Workflow Designer API
# ----------------------------------------------------------------------
@api_bp.route('/workflows')
@login_required
@admin_required
def list_workflows():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT wf.WorkflowID, wf.WorkflowCode, wf.WorkflowName, wf.Module, wf.EntityType,
                   wf.Version, wf.IsActive, wf.Description, wf.CreatedDateKey, wf.ModifiedDateKey
            FROM t_WorkflowDefinitions wf
            ORDER BY wf.WorkflowCode, wf.Version DESC
        """)).fetchall()
    workflows = []
    for r in rows:
        workflows.append({
            'id': r[0],
            'code': r[1],
            'name': r[2],
            'module': r[3],
            'entityType': r[4],
            'version': r[5],
            'isActive': bool(r[6]),
            'description': r[7],
            'createdDateKey': r[8],      # integer YYYYMMDD
            'modifiedDateKey': r[9]
        })
    # KPI calculations (same as before)
    active_codes = set()
    for w in workflows:
        if w['isActive']:
            active_codes.add(w['code'])
    kpi_active = len(active_codes)
    codes_with_active = set()
    codes_all = set()
    for w in workflows:
        codes_all.add(w['code'])
        if w['isActive']:
            codes_with_active.add(w['code'])
    pending = len(codes_all - codes_with_active)
    return jsonify({
        'workflows': workflows,
        'kpi': {'active_workflows': kpi_active, 'pending_activations': pending}
    })

@api_bp.route('/workflows/<int:workflow_id>')
@login_required
@admin_required
def get_workflow(workflow_id):
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT WorkflowID, WorkflowCode, WorkflowName, Module, EntityType,
                   Description, Steps, Version, IsActive, Conditions, ApplicableTo,
                   CreatedDateKey, ModifiedDateKey
            FROM t_WorkflowDefinitions
            WHERE WorkflowID = :id
        """), {'id': workflow_id}).fetchone()
        if not row:
            return jsonify({'error': 'Not found'}), 404
        steps = json.loads(row[6]) if row[6] else []
        conditions = json.loads(row[9]) if row[9] else {}
        applicableTo = json.loads(row[10]) if row[10] else {}
        return jsonify({
            'id': row[0],
            'code': row[1],
            'name': row[2],
            'module': row[3],
            'entityType': row[4],
            'description': row[5],
            'steps': steps,
            'version': row[7],
            'isActive': bool(row[8]),
            'conditions': conditions,
            'applicableTo': applicableTo,
            'createdDateKey': row[11],
            'modifiedDateKey': row[12]
        })

@api_bp.route('/workflows', methods=['POST'])
@login_required
@admin_required
def create_workflow():
    data = request.get_json()
    required = ['code', 'name', 'module', 'entityType']
    for f in required:
        if not data.get(f):
            return jsonify({'error': f'Missing {f}'}), 400
    engine = get_engine()
    with engine.connect() as conn:
        existing = conn.execute(text("SELECT WorkflowID FROM t_WorkflowDefinitions WHERE WorkflowCode = :code"), {'code': data['code']}).fetchone()
        if existing:
            return jsonify({'error': 'Workflow code already exists'}), 409
        steps_json = json.dumps(data.get('steps', []))
        conditions_json = json.dumps(data.get('conditions', {}))
        applicable_json = json.dumps(data.get('applicableTo', {}))
        today_key = int(datetime.now().strftime('%Y%m%d'))
        insert_sql = """
            INSERT INTO t_WorkflowDefinitions
            (WorkflowCode, WorkflowName, Module, EntityType, Description, Steps, Version, IsActive,
             Conditions, ApplicableTo, CreatedDateKey, ModifiedDateKey, CreatedBy, ModifiedBy)
            VALUES
            (:code, :name, :mod, :entity, :desc, :steps, 1, 0,
             :cond, :app, :cdate, :mdate, :cby, :mby)
        """
        conn.execute(text(insert_sql), {
            'code': data['code'], 'name': data['name'], 'mod': data['module'],
            'entity': data['entityType'], 'desc': data.get('description', ''),
            'steps': steps_json, 'cond': conditions_json, 'app': applicable_json,
            'cdate': today_key, 'mdate': today_key,
            'cby': current_user.get_id(), 'mby': current_user.get_id()
        })
        conn.commit()
        new_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()
    return jsonify({'message': 'Workflow created', 'id': new_id}), 201

@api_bp.route('/workflows/<int:workflow_id>', methods=['PUT'])
@login_required
@admin_required
def update_workflow(workflow_id):
    data = request.get_json()
    create_new_version = data.get('create_new_version', False)
    engine = get_engine()
    with engine.connect() as conn:
        cur = conn.execute(text("SELECT WorkflowCode, Version FROM t_WorkflowDefinitions WHERE WorkflowID = :id"), {'id': workflow_id}).fetchone()
        if not cur:
            return jsonify({'error': 'Workflow not found'}), 404
        code = cur[0]
        new_version = cur[1] + 1 if create_new_version else cur[1]
        steps_json = json.dumps(data.get('steps', []))
        conditions_json = json.dumps(data.get('conditions', {}))
        applicable_json = json.dumps(data.get('applicableTo', {}))
        today_key = int(datetime.now().strftime('%Y%m%d'))
        if create_new_version:
            insert_sql = """
                INSERT INTO t_WorkflowDefinitions
                (WorkflowCode, WorkflowName, Module, EntityType, Description, Steps, Version, IsActive,
                 Conditions, ApplicableTo, CreatedDateKey, ModifiedDateKey, CreatedBy, ModifiedBy)
                VALUES
                (:code, :name, :mod, :entity, :desc, :steps, :version, 0,
                 :cond, :app, :cdate, :mdate, :cby, :mby)
            """
            conn.execute(text(insert_sql), {
                'code': code,
                'name': data.get('name'),
                'mod': data.get('module'),
                'entity': data.get('entityType'),
                'desc': data.get('description', ''),
                'steps': steps_json,
                'version': new_version,
                'cond': conditions_json,
                'app': applicable_json,
                'cdate': today_key,
                'mdate': today_key,
                'cby': current_user.get_id(),
                'mby': current_user.get_id()
            })
            conn.commit()
            new_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()
            return jsonify({'message': 'New version created', 'id': new_id, 'version': new_version}), 200
        else:
            update_sql = """
                UPDATE t_WorkflowDefinitions SET
                WorkflowName = :name, Module = :mod, EntityType = :entity, Description = :desc,
                Steps = :steps, Conditions = :cond, ApplicableTo = :app,
                ModifiedDateKey = :mdate, ModifiedBy = :mby
                WHERE WorkflowID = :id
            """
            conn.execute(text(update_sql), {
                'id': workflow_id,
                'name': data.get('name'),
                'mod': data.get('module'),
                'entity': data.get('entityType'),
                'desc': data.get('description', ''),
                'steps': steps_json,
                'cond': conditions_json,
                'app': applicable_json,
                'mdate': today_key,
                'mby': current_user.get_id()
            })
            conn.commit()
            return jsonify({'message': 'Workflow updated', 'id': workflow_id}), 200
 
 
# ----------------------------------------------------------------------
# System Configuration (t_AdminConfig)
# ----------------------------------------------------------------------

@api_bp.route('/config')
@login_required
@admin_required
def get_config():
    """Return all configuration keys with values, descriptions, categories"""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT ConfigKey, ConfigValue, Description, Category FROM t_AdminConfig ORDER BY Category, ConfigKey")).fetchall()
    configs = [{'key': r[0], 'value': r[1], 'description': r[2], 'category': r[3] or 'General'} for r in rows]
    return jsonify(configs)

@api_bp.route('/config/categories')
@login_required
@admin_required
def get_config_categories():
    """Return distinct categories for filter dropdown"""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT DISTINCT Category FROM t_AdminConfig WHERE Category IS NOT NULL")).fetchall()
    categories = [r[0] for r in rows if r[0]]
    if not categories:
        categories = ['General']
    return jsonify(categories)

@api_bp.route('/config/<string:key>', methods=['PUT'])
@login_required
@admin_required
def update_config(key):
    data = request.get_json()
    new_value = data.get('value')
    description = data.get('description')
    category = data.get('category')
    if new_value is None:
        return jsonify({'error': 'Missing value'}), 400
    engine = get_engine()
    with engine.connect() as conn:
        # Check if key exists
        existing = conn.execute(text("SELECT ConfigKey FROM t_AdminConfig WHERE ConfigKey = :key"), {'key': key}).fetchone()
        if not existing:
            return jsonify({'error': 'Configuration key not found'}), 404
        update_parts = ["ConfigValue = :val"]
        params = {'key': key, 'val': new_value}
        if description is not None:
            update_parts.append("Description = :desc")
            params['desc'] = description
        if category is not None:
            update_parts.append("Category = :cat")
            params['cat'] = category
        sql = f"UPDATE t_AdminConfig SET {', '.join(update_parts)} WHERE ConfigKey = :key"
        conn.execute(text(sql), params)
        conn.commit()
        # Log audit
        log_audit('UPDATE_CONFIG', f"Updated config key {key} to {new_value}")
    return jsonify({'message': 'Configuration updated'})

@api_bp.route('/config/export', methods=['GET'])
@login_required
@admin_required
def export_config():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT ConfigKey, ConfigValue, Description, Category FROM t_AdminConfig ORDER BY ConfigKey")).fetchall()
    config_data = [{'key': r[0], 'value': r[1], 'description': r[2], 'category': r[3]} for r in rows]
    output = json.dumps(config_data, indent=2)
    return current_app.response_class(
        output,
        mimetype='application/json',
        headers={'Content-Disposition': 'attachment; filename=system_config_backup.json'}
    )

@api_bp.route('/config/import', methods=['POST'])
@login_required
@admin_required
def import_config():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    file = request.files['file']
    if not file.filename.endswith('.json'):
        return jsonify({'error': 'Only JSON files accepted'}), 400
    try:
        content = file.read().decode('utf-8')
        data = json.loads(content)
    except Exception as e:
        return jsonify({'error': f'Invalid JSON: {str(e)}'}), 400
    if not isinstance(data, list):
        return jsonify({'error': 'JSON must be an array of config objects'}), 400
    engine = get_engine()
    with engine.connect() as conn:
        for item in data:
            key = item.get('key')
            value = item.get('value')
            description = item.get('description', '')
            category = item.get('category', 'General')
            if not key or value is None:
                continue
            # Use UPSERT: update if exists, else insert
            conn.execute(text("""
                INSERT INTO t_AdminConfig (ConfigKey, ConfigValue, Description, Category)
                VALUES (:key, :val, :desc, :cat)
                ON CONFLICT(ConfigKey) DO UPDATE SET
                    ConfigValue = excluded.ConfigValue,
                    Description = excluded.Description,
                    Category = excluded.Category
            """), {'key': key, 'val': value, 'desc': description, 'cat': category})
        conn.commit()
    log_audit('IMPORT_CONFIG', f"Imported {len(data)} configuration entries")
    return jsonify({'message': f'Imported {len(data)} configuration entries'}), 200

@api_bp.route('/config', methods=['POST'])
@login_required
@admin_required
def create_config():
    """Create a new configuration key (extra)"""
    data = request.get_json()
    key = data.get('key')
    value = data.get('value')
    description = data.get('description', '')
    category = data.get('category', 'General')
    if not key or value is None:
        return jsonify({'error': 'Key and value required'}), 400
    engine = get_engine()
    with engine.connect() as conn:
        existing = conn.execute(text("SELECT ConfigKey FROM t_AdminConfig WHERE ConfigKey = :key"), {'key': key}).fetchone()
        if existing:
            return jsonify({'error': 'Key already exists'}), 409
        conn.execute(text("""
            INSERT INTO t_AdminConfig (ConfigKey, ConfigValue, Description, Category)
            VALUES (:key, :val, :desc, :cat)
        """), {'key': key, 'val': value, 'desc': description, 'cat': category})
        conn.commit()
    log_audit('CREATE_CONFIG', f"Created config key {key}")
    return jsonify({'message': 'Configuration created'}), 201

def log_audit(action, details):
    """Helper to log to t_AuditLog if available"""
    try:
        from app import db
        from flask_login import current_user
        with db.engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO t_AuditLog (Timestamp, UserID, ActionType, Module, Description, IPAddress)
                VALUES (CURRENT_TIMESTAMP, :uid, :action, 'Admin', :details, :ip)
            """), {'uid': current_user.get_id(), 'action': action, 'details': details, 'ip': request.remote_addr})
            conn.commit()
    except Exception as e:
        print(f"Audit log error: {e}")
        
@api_bp.route('/config/<string:key>', methods=['DELETE'])
@login_required
@admin_required
def delete_config(key):
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text("DELETE FROM t_AdminConfig WHERE ConfigKey = :key"), {'key': key})
        conn.commit()
        if result.rowcount == 0:
            return jsonify({'error': 'Key not found'}), 404
    log_audit('DELETE_CONFIG', f"Deleted config key {key}")
    return jsonify({'message': 'Configuration deleted'})
    
# ----------------------------------------------------------------------
# Audit Log API
# ----------------------------------------------------------------------

# ============================================================================
# IMPORTANT: Audit Log now joins with DimDate for formatted date display
# ============================================================================
# The DimDate table provides Gregorian and Persian date formats for display.
# This allows the frontend to show dates in the user's preferred calendar
# without needing to convert integer DateKeys in JavaScript.
#
# DimDate columns used:
#   - GregorianDate: '2025-06-15' format for Gregorian calendar display
#   - PersianStr: '1404-03-25' format for Persian calendar display
# ============================================================================

@api_bp.route('/audit-log')
@login_required
@admin_required
def get_audit_log():
    """Return audit log entries with filters and KPIs using DateKey with DimDate join"""
    user_id = request.args.get('user_id')
    module = request.args.get('module')
    action_type = request.args.get('action_type')
    date_from = request.args.get('date_from')   # expected YYYY-MM-DD
    date_to = request.args.get('date_to')       # expected YYYY-MM-DD
    limit = request.args.get('limit', 200, type=int)

    engine = get_engine()
    
    # ============================================================================
    # Build query with DimDate join for formatted dates
    # ============================================================================
    # Joining with DimDate provides GregorianDate (e.g., '2025-06-15') and
    # PersianStr (e.g., '1404-03-25') for display in the user's preferred calendar.
    # This eliminates the need for client-side date conversion and ensures
    # consistent date formatting across the application.
    # ============================================================================
    query = """
        SELECT TOP (:lim)
            a.AuditID, a.Timestamp, a.DateKey,
            d.GregorianDate, d.PersianStr,
            a.UserID, u.Username, u.FullName,
            a.ActionType, a.Module, a.TableName, a.RecordID,
            a.OldValues, a.NewValues, a.IPAddress, a.Description
        FROM t_AuditLog a
        LEFT JOIN t_Users u ON a.UserID = u.UserID
        LEFT JOIN DimDate d ON a.DateKey = d.DateKey
        WHERE 1=1
    """
    params = {}

    if user_id:
        query += " AND a.UserID = :uid"
        params['uid'] = user_id
    if module:
        query += " AND a.Module = :mod"
        params['mod'] = module
    if action_type:
        query += " AND a.ActionType = :act"
        params['act'] = action_type
    if date_from:
        # Convert YYYY-MM-DD to integer YYYYMMDD
        df_int = int(date_from.replace('-', ''))
        query += " AND a.DateKey >= :df"
        params['df'] = df_int
    if date_to:
        dt_int = int(date_to.replace('-', ''))
        query += " AND a.DateKey <= :dt"
        params['dt'] = dt_int

    query += " ORDER BY a.Timestamp DESC"
    params['lim'] = limit

    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

    logs = []
    for r in rows:
        logs.append({
            'id': r[0],
            'timestamp': str(r[1]) if r[1] else None,
            'dateKey': r[2],  # Keep as integer for DimDate lookup
            # Formatted dates from DimDate
            'gregorianDate': r[3] if r[3] else None,
            'persianStr': r[4] if r[4] else None,
            'userId': r[5],
            'username': r[6] or f'User {r[5]}',
            'fullName': r[7],
            'action': r[8],
            'module': r[9],
            'tableName': r[10],
            'recordId': r[11],
            'oldValues': r[12],
            'newValues': r[13],
            'ipAddress': r[14],
            'description': r[15]
        })

    # KPI: today's events using DateKey
    today_key = int(datetime.now().strftime('%Y%m%d'))
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT COUNT(*) FROM t_AuditLog WHERE DateKey = :today"),
            {'today': today_key}
        ).fetchone()
        kpi_today = row[0] if row else 0

        # Top users by event count (last 30 days using DateKey)
        thirty_days_ago = int((datetime.now() - timedelta(days=30)).strftime('%Y%m%d'))
        rows = conn.execute(
            text("""
                SELECT TOP 5
                    u.Username, COUNT(*) as cnt
                FROM t_AuditLog a
                LEFT JOIN t_Users u ON a.UserID = u.UserID
                WHERE a.DateKey >= :cutoff
                GROUP BY u.Username
                ORDER BY cnt DESC
            """),
            {'cutoff': thirty_days_ago}
        ).fetchall()
        kpi_by_user = [{'username': r[0] or 'Unknown', 'count': r[1]} for r in rows]

    return jsonify({
        'logs': logs,
        'kpi': {'today_events': kpi_today, 'by_user': kpi_by_user}
    })


@api_bp.route('/audit-log/filters')
@login_required
@admin_required
def get_audit_filters():
    """Return distinct users, modules, action types for filter dropdowns"""
    engine = get_engine()
    with engine.connect() as conn:
        users = conn.execute(text("""
            SELECT DISTINCT a.UserID, u.Username, u.FullName
            FROM t_AuditLog a
            LEFT JOIN t_Users u ON a.UserID = u.UserID
            ORDER BY u.Username
        """)).fetchall()
        modules = conn.execute(text("SELECT DISTINCT Module FROM t_AuditLog WHERE Module IS NOT NULL ORDER BY Module")).fetchall()
        actions = conn.execute(text("SELECT DISTINCT ActionType FROM t_AuditLog WHERE ActionType IS NOT NULL ORDER BY ActionType")).fetchall()

    return jsonify({
        'users': [{'id': u[0], 'username': u[1] or f'User {u[0]}', 'fullName': u[2]} for u in users],
        'modules': [m[0] for m in modules],
        'actions': [a[0] for a in actions]
    })



@api_bp.route('/audit-log/export')
@login_required
@admin_required
def export_audit_log():
    user_id = request.args.get('user_id')
    module = request.args.get('module')
    action_type = request.args.get('action_type')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')

    engine = get_engine()
    query = """
        SELECT a.Timestamp, u.Username, a.ActionType, a.Module, a.TableName, a.RecordID,
               a.Description, a.IPAddress
        FROM t_AuditLog a
        LEFT JOIN t_Users u ON a.UserID = u.UserID
        WHERE 1=1
    """
    params = {}
    if user_id:
        query += " AND a.UserID = :uid"
        params['uid'] = user_id
    if module:
        query += " AND a.Module = :mod"
        params['mod'] = module
    if action_type:
        query += " AND a.ActionType = :act"
        params['act'] = action_type
    if date_from:
        df_int = int(date_from.replace('-', ''))
        query += " AND a.DateKey >= :df"
        params['df'] = df_int
    if date_to:
        dt_int = int(date_to.replace('-', ''))
        query += " AND a.DateKey <= :dt"
        params['dt'] = dt_int
    query += " ORDER BY a.Timestamp DESC"

    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Timestamp', 'User', 'Action', 'Module', 'Table', 'Record ID', 'Description', 'IP Address'])
    for r in rows:
        writer.writerow([r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7]])

    return current_app.response_class(
        output.getvalue().encode('utf-8-sig'),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=audit_log.csv'}
    )
   
   
   
# ----------------------------------------------------------------------
# Portal Settings (using t_AdminConfig)
# ----------------------------------------------------------------------

@api_bp.route('/portal-settings')
@login_required
@admin_required
def get_portal_settings():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT ConfigKey, ConfigValue, Description
            FROM t_AdminConfig
            WHERE Category = 'Portal' OR ConfigKey LIKE 'portal_%'
        """)).fetchall()
    settings = {r[0]: {'value': r[1], 'description': r[2]} for r in rows}
    # Default values if not present
    defaults = {
        'portal_company_name': 'My Manufacturing Co.',
        'portal_primary_color': '#0d6efd',
        'portal_logo_path': '',
        'portal_welcome_message': 'Welcome to our supplier/customer portal',
        'portal_modules': '["sales","inventory"]',
        'portal_terms_conditions': '',
        'portal_footer_text': '© 2025 All rights reserved.',
        'portal_login_background_image': ''
    }
    for key, default_val in defaults.items():
        if key not in settings:
            settings[key] = {'value': default_val, 'description': ''}
    return jsonify(settings)

@api_bp.route('/portal-settings', methods=['PUT'])
@login_required
@admin_required
def update_portal_settings():
    data = request.get_json()
    engine = get_engine()
    with engine.connect() as conn:
        for key, value in data.items():
            if not key.startswith('portal_'):
                continue
            conn.execute(text("""
                INSERT INTO t_AdminConfig (ConfigKey, ConfigValue, Category)
                VALUES (:key, :val, 'Portal')
                ON CONFLICT(ConfigKey) DO UPDATE SET ConfigValue = excluded.ConfigValue
            """), {'key': key, 'val': value})
        conn.commit()
    return jsonify({'message': 'Portal settings updated'})

@api_bp.route('/portal-settings/upload-logo', methods=['POST'])
@login_required
@admin_required
def upload_portal_logo():
    if 'logo' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    file = request.files['logo']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    if not allowed_image_file(file.filename):
        return jsonify({'error': 'File type not allowed. Use PNG, JPG, JPEG, GIF'}), 400

    ext = file.filename.rsplit('.', 1)[1].lower()
    filename = f"portal_logo_{datetime.now().strftime('%Y%m%d%H%M%S')}.{ext}"
    upload_folder = get_upload_folder()
    filepath = os.path.join(upload_folder, filename)
    file.save(filepath)
    relative_path = f"uploads/{filename}"

    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO t_AdminConfig (ConfigKey, ConfigValue, Category)
            VALUES ('portal_logo_path', :path, 'Portal')
            ON CONFLICT(ConfigKey) DO UPDATE SET ConfigValue = excluded.ConfigValue
        """), {'path': relative_path})
        conn.commit()
    return jsonify({'logo_path': relative_path})

def allowed_image_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'gif'}
    
 
 # ----------------------------------------------------------------------
# Project Roles & Permissions (using t_ProjectRole)
# ----------------------------------------------------------------------

@api_bp.route('/project-roles/kpi')
@login_required
@admin_required
def project_roles_kpi():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT r.RoleName, COUNT(*) as cnt
            FROM t_ProjectRole pr
            JOIN t_Roles r ON pr.RoleCode = r.RoleCode
            WHERE pr.IsActive = 1 AND (pr.EndDateKey IS NULL OR pr.EndDateKey >= strftime('%Y%m%d', 'now'))
            GROUP BY pr.RoleCode
            ORDER BY cnt DESC
        """)).fetchall()
    return jsonify([{'role': r[0], 'count': r[1]} for r in rows])

@api_bp.route('/project-roles/assignments')
@login_required
@admin_required
def project_roles_list():
    # If project_id is not provided as query param, use session's current project (from sidebar)
    project_id = request.args.get('project_id')
    if not project_id:
        from flask import session
        current_project = session.get('current_project_id', 'All')
        if current_project != 'All':
            project_id = current_project
    
    role_code = request.args.get('role_code')
    active_only = request.args.get('active_only', 'true') == 'true'

    engine = get_engine()
    query = """
        SELECT pr.ProjectRoleID, pr.ProjectID, pj.ProjectName,
               pr.PersonID, u.Username, u.FullName,
               pr.RoleCode, r.RoleName,
               pr.AssignedDateKey, pr.EndDateKey, pr.IsActive
        FROM t_ProjectRole pr
        LEFT JOIN t_Project pj ON pr.ProjectID = pj.ProjectID
        LEFT JOIN t_Users u ON pr.PersonID = u.UserID
        LEFT JOIN t_Roles r ON pr.RoleCode = r.RoleCode
        WHERE 1=1
    """
    params = {}
    if project_id:
        query += " AND pr.ProjectID = :pid"
        params['pid'] = project_id
    if role_code:
        query += " AND pr.RoleCode = :rc"
        params['rc'] = role_code
    if active_only:
        query += " AND pr.IsActive = 1 AND (pr.EndDateKey IS NULL OR pr.EndDateKey >= strftime('%Y%m%d', 'now'))"
    query += " ORDER BY pj.ProjectName, r.RoleName, u.FullName"

    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
    assignments = [{
        'id': r[0], 'projectId': r[1], 'projectName': r[2],
        'userId': r[3], 'username': r[4], 'fullName': r[5],
        'roleCode': r[6], 'roleName': r[7],
        'startDateKey': r[8], 'endDateKey': r[9], 'isActive': bool(r[10])
    } for r in rows]
    return jsonify(assignments)




@api_bp.route('/project-roles/assign', methods=['POST'])
@login_required
@admin_required
def assign_project_role():
    data = request.get_json()
    required = ['projectId', 'userId', 'roleCode', 'startDateKey']
    for f in required:
        if f not in data:
            return jsonify({'error': f'Missing {f}'}), 400
    today_key = int(datetime.now().strftime('%Y%m%d'))
    engine = get_engine()
    with engine.connect() as conn:
        existing = conn.execute(text("""
            SELECT ProjectRoleID FROM t_ProjectRole
            WHERE ProjectID = :pid AND PersonID = :uid AND RoleCode = :rc
            AND IsActive = 1 AND (EndDateKey IS NULL OR EndDateKey >= :today)
        """), {'pid': data['projectId'], 'uid': data['userId'], 'rc': data['roleCode'], 'today': today_key}).fetchone()
        if existing:
            return jsonify({'error': 'User already has this active role in the project'}), 409
        conn.execute(text("""
            INSERT INTO t_ProjectRole
            (ProjectID, PersonID, RoleCode, AssignedDateKey, EndDateKey, IsActive)
            VALUES
            (:pid, :uid, :rc, :start, :end, :active)
        """), {
            'pid': data['projectId'], 'uid': data['userId'], 'rc': data['roleCode'],
            'start': data['startDateKey'], 'end': data.get('endDateKey'), 'active': data.get('isActive', 1)
        })
        conn.commit()
    return jsonify({'message': 'Assignment created'}), 201

@api_bp.route('/project-roles/<int:assignment_id>', methods=['PUT'])
@login_required
@admin_required
def update_project_role(assignment_id):
    data = request.get_json()
    engine = get_engine()
    with engine.connect() as conn:
        updates = []
        params = {'id': assignment_id}
        if 'endDateKey' in data:
            updates.append("EndDateKey = :end")
            params['end'] = data['endDateKey']
        if 'isActive' in data:
            updates.append("IsActive = :active")
            params['active'] = int(data['isActive'])
        if updates:
            sql = f"UPDATE t_ProjectRole SET {', '.join(updates)} WHERE ProjectRoleID = :id"
            conn.execute(text(sql), params)
            conn.commit()
    return jsonify({'message': 'Assignment updated'})

@api_bp.route('/project-roles/<int:assignment_id>', methods=['DELETE'])
@login_required
@admin_required
def delete_project_role(assignment_id):
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM t_ProjectRole WHERE ProjectRoleID = :id"), {'id': assignment_id})
        conn.commit()
    return jsonify({'message': 'Assignment deleted'})

@api_bp.route('/project-roles/export')
@login_required
@admin_required
def export_project_roles():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT pj.ProjectName, u.Username, u.FullName, r.RoleName,
                   pr.AssignedDateKey, pr.EndDateKey, pr.IsActive
            FROM t_ProjectRole pr
            LEFT JOIN t_Project pj ON pr.ProjectID = pj.ProjectID
            LEFT JOIN t_Users u ON pr.PersonID = u.UserID
            LEFT JOIN t_Roles r ON pr.RoleCode = r.RoleCode
            ORDER BY pj.ProjectName, r.RoleName
        """)).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Project', 'Username', 'Full Name', 'Role', 'Start Date Key', 'End Date Key', 'Is Active'])
    for r in rows:
        writer.writerow([r[0], r[1], r[2], r[3], r[4], r[5], 'Active' if r[6] else 'Inactive'])
    return current_app.response_class(
        output.getvalue().encode('utf-8-sig'),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=project_roles.csv'}
    )

@api_bp.route('/project-roles/import', methods=['POST'])
@login_required
@admin_required
def import_project_roles():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    file = request.files['file']
    if not file.filename.endswith('.csv'):
        return jsonify({'error': 'Only CSV files accepted'}), 400
    stream = io.TextIOWrapper(file.stream, encoding='utf-8')
    reader = csv.DictReader(stream)
    engine = get_engine()
    created = 0
    errors = []
    today_key = int(datetime.now().strftime('%Y%m%d'))
    with engine.connect() as conn:
        for row in reader:
            project_name = row.get('Project')
            username = row.get('Username')
            role_name = row.get('Role')
            if not project_name or not username or not role_name:
                errors.append('Missing required column')
                continue
            proj = conn.execute(text("SELECT ProjectID FROM t_Project WHERE ProjectName = :name"), {'name': project_name}).fetchone()
            if not proj:
                errors.append(f'Project not found: {project_name}')
                continue
            user = conn.execute(text("SELECT UserID FROM t_Users WHERE Username = :un"), {'un': username}).fetchone()
            if not user:
                errors.append(f'User not found: {username}')
                continue
            role = conn.execute(text("SELECT RoleCode FROM t_Roles WHERE RoleName = :rn"), {'rn': role_name}).fetchone()
            if not role:
                errors.append(f'Role not found: {role_name}')
                continue
            start_key = int(row.get('Start Date Key', today_key))
            end_key = int(row.get('End Date Key')) if row.get('End Date Key') else None
            is_active = row.get('Is Active', 'Active').lower() == 'active'
            conn.execute(text("""
                INSERT INTO t_ProjectRole
                (ProjectID, PersonID, RoleCode, AssignedDateKey, EndDateKey, IsActive)
                VALUES
                (:pid, :uid, :rc, :start, :end, :active)
            """), {
                'pid': proj[0], 'uid': user[0], 'rc': role[0],
                'start': start_key, 'end': end_key, 'active': is_active
            })
            created += 1
        conn.commit()
    return jsonify({'message': f'Imported {created} assignments', 'errors': errors})



@api_bp.route('/users/list')
@login_required
@admin_required
def get_users_list():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT UserID, Username, FullName FROM t_Users WHERE IsActive = 1 ORDER BY FullName")).fetchall()
    return jsonify([{'id': r[0], 'username': r[1], 'fullName': r[2]} for r in rows])

@api_bp.route('/roles/list')
@login_required
@admin_required
def get_roles_list():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT RoleCode, RoleName FROM t_Roles ORDER BY RoleName")).fetchall()
    return jsonify([{'code': r[0], 'name': r[1]} for r in rows])


@api_bp.route('/menu')
@login_required
def get_user_menu():
    from app.core.menu_permissions import MENU_ITEMS, get_user_permission_set, has_permission
    from app import db
    from sqlalchemy.sql import text
    import json

    # Fetch user's roles from database
    with db.engine.connect() as conn:
        row = conn.execute(text("SELECT UserRoles FROM t_Users WHERE UserID = :uid"), {'uid': current_user.get_id()}).fetchone()
        user_roles = json.loads(row[0]) if row and row[0] else []
    
    print("DEBUG get_user_menu: user_roles =", user_roles, flush=True)
    
    user_perms = get_user_permission_set(user_roles)
    
    print("DEBUG get_user_menu: user_perms =", user_perms, flush=True)
    
    allowed_keys = []
    for key, item in MENU_ITEMS.items():
        if has_permission(item.get('permission_code'), user_perms):
            allowed_keys.append(key)
    
    print("DEBUG allowed_keys:", allowed_keys, flush=True)
    return jsonify(allowed_keys)
    

# ========== Document Type Management ==========
import csv
import io
from datetime import datetime

@api_bp.route('/document-types')
@login_required
@admin_required
def list_document_types():
    category = request.args.get('category')
    active_only = request.args.get('active_only', 'false') == 'true'
    search = request.args.get('search', '')

    engine = get_engine()
    query = """SELECT DocumentTypeID, Code, Name, Category, Description, IsActive,
                      CreatedDateKey, ModifiedDateKey
               FROM t_DocumentType WHERE 1=1"""
    params = {}
    if category:
        query += " AND Category = :cat"
        params['cat'] = category
    if active_only:
        query += " AND IsActive = 1"
    if search:
        query += " AND (Code LIKE :search OR Name LIKE :search)"
        params['search'] = f"%{search}%"
    query += " ORDER BY Code"

    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
    doc_types = [{
        'id': r[0], 'code': r[1], 'name': r[2], 'category': r[3],
        'description': r[4], 'isActive': bool(r[5]),
        'createdDateKey': r[6], 'modifiedDateKey': r[7]
    } for r in rows]

    # KPI
    categories = {}
    total_active = 0
    for dt in doc_types:
        if dt['isActive']:
            total_active += 1
            cat = dt['category'] or 'Uncategorized'
            categories[cat] = categories.get(cat, 0) + 1

    return jsonify({
        'documentTypes': doc_types,
        'kpi': {
            'total_active': total_active,
            'by_category': [{'category': k, 'count': v} for k, v in categories.items()]
        }
    })

@api_bp.route('/document-types/categories')
@login_required
@admin_required
def get_document_categories():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT DISTINCT Category FROM t_DocumentType WHERE Category IS NOT NULL AND Category != '' ORDER BY Category")).fetchall()
    return jsonify([r[0] for r in rows])

@api_bp.route('/document-types', methods=['POST'])
@login_required
@admin_required
def create_document_type():
    data = request.get_json()
    if not data.get('code') or not data.get('name'):
        return jsonify({'error': 'Code and Name are required'}), 400

    engine = get_engine()
    today_key = int(datetime.now().strftime('%Y%m%d'))
    with engine.connect() as conn:
        existing = conn.execute(text("SELECT DocumentTypeID FROM t_DocumentType WHERE Code = :code"), {'code': data['code']}).fetchone()
        if existing:
            return jsonify({'error': 'Code already exists'}), 409

        conn.execute(text("""
            INSERT INTO t_DocumentType (Code, Name, Category, Description, IsActive, CreatedDateKey, ModifiedDateKey)
            VALUES (:code, :name, :cat, :desc, :active, :cdate, :mdate)
        """), {
            'code': data['code'],
            'name': data['name'],
            'cat': data.get('category'),
            'desc': data.get('description'),
            'active': data.get('isActive', 1),
            'cdate': today_key,
            'mdate': today_key
        })
        conn.commit()
        new_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()
    return jsonify({'message': 'Document type created', 'id': new_id}), 201

@api_bp.route('/document-types/<int:doc_id>', methods=['PUT'])
@login_required
@admin_required
def update_document_type(doc_id):
    data = request.get_json()
    allowed = ['name', 'category', 'description', 'isActive']
    engine = get_engine()
    today_key = int(datetime.now().strftime('%Y%m%d'))
    with engine.connect() as conn:
        updates = []
        params = {'id': doc_id, 'mdate': today_key}
        for field in allowed:
            if field in data:
                updates.append(f"{field} = :{field}")
                params[field] = data[field]
        if updates:
            updates.append("ModifiedDateKey = :mdate")
            sql = f"UPDATE t_DocumentType SET {', '.join(updates)} WHERE DocumentTypeID = :id"
            conn.execute(text(sql), params)
            conn.commit()
    return jsonify({'message': 'Document type updated'})

@api_bp.route('/document-types/<int:doc_id>', methods=['DELETE'])
@login_required
@admin_required
def archive_document_type(doc_id):
    engine = get_engine()
    today_key = int(datetime.now().strftime('%Y%m%d'))
    with engine.connect() as conn:
        conn.execute(text("UPDATE t_DocumentType SET IsActive = 0, ModifiedDateKey = :mdate WHERE DocumentTypeID = :id"),
                     {'id': doc_id, 'mdate': today_key})
        conn.commit()
    return jsonify({'message': 'Document type archived'})

@api_bp.route('/document-types/<int:doc_id>')
@login_required
@admin_required
def get_document_type(doc_id):
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT DocumentTypeID, Code, Name, Category, Description, IsActive,
                   CreatedDateKey, ModifiedDateKey
            FROM t_DocumentType WHERE DocumentTypeID = :id
        """), {'id': doc_id}).fetchone()
        if not row:
            return jsonify({'error': 'Not found'}), 404
        return jsonify({
            'id': row[0], 'code': row[1], 'name': row[2], 'category': row[3],
            'description': row[4], 'isActive': bool(row[5]),
            'createdDateKey': row[6], 'modifiedDateKey': row[7]
        })

@api_bp.route('/document-types/export')
@login_required
@admin_required
def export_document_types():
    category = request.args.get('category')
    active_only = request.args.get('active_only', 'false') == 'true'
    search = request.args.get('search', '')
    engine = get_engine()
    query = "SELECT Code, Name, Category, Description, IsActive FROM t_DocumentType WHERE 1=1"
    params = {}
    if category:
        query += " AND Category = :cat"
        params['cat'] = category
    if active_only:
        query += " AND IsActive = 1"
    if search:
        query += " AND (Code LIKE :search OR Name LIKE :search)"
        params['search'] = f"%{search}%"
    query += " ORDER BY Code"

    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Code', 'Name', 'Category', 'Description', 'IsActive'])
    for r in rows:
        writer.writerow([r[0], r[1], r[2], r[3], 'Active' if r[4] else 'Inactive'])
    return current_app.response_class(
        output.getvalue().encode('utf-8-sig'),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=document_types.csv'}
    )

@api_bp.route('/document-types/import', methods=['POST'])
@login_required
@admin_required
def import_document_types():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    file = request.files['file']
    if not file.filename.endswith('.csv'):
        return jsonify({'error': 'Only CSV files accepted'}), 400
    stream = io.TextIOWrapper(file.stream, encoding='utf-8')
    reader = csv.DictReader(stream)
    engine = get_engine()
    today_key = int(datetime.now().strftime('%Y%m%d'))
    created = 0
    errors = []
    with engine.connect() as conn:
        for row in reader:
            code = row.get('Code')
            name = row.get('Name')
            if not code or not name:
                errors.append(f'Missing Code or Name in row: {row}')
                continue
            category = row.get('Category')
            description = row.get('Description')
            is_active = row.get('IsActive', 'Active').lower() == 'active'
            existing = conn.execute(text("SELECT DocumentTypeID FROM t_DocumentType WHERE Code = :code"), {'code': code}).fetchone()
            if existing:
                errors.append(f'Code {code} already exists, skipped')
                continue
            conn.execute(text("""
                INSERT INTO t_DocumentType (Code, Name, Category, Description, IsActive, CreatedDateKey, ModifiedDateKey)
                VALUES (:code, :name, :cat, :desc, :active, :cdate, :mdate)
            """), {
                'code': code, 'name': name, 'cat': category, 'desc': description,
                'active': 1 if is_active else 0, 'cdate': today_key, 'mdate': today_key
            })
            created += 1
        conn.commit()
    return jsonify({'message': f'Imported {created} document types', 'errors': errors})