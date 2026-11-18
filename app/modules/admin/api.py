# app/modules/admin/api.py
import csv
import secrets
import io
import os
import json
import uuid
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, current_app, session
from flask_login import login_required, current_user
from sqlalchemy.sql import text
from app.modules.admin import admin_required
from app.core.menu_permissions import MENU_ITEMS, get_user_permission_set, has_permission
# §C1 — canonical Werkzeug-aware verifier + hasher
from app.modules.auth import verify_password, hash_password  # noqa: F401


api_bp = Blueprint('admin_api', __name__, url_prefix='/api/admin')


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def get_engine():
    return current_app.extensions['sqlalchemy'].engine

def _new_id():
    """Generate a nvarchar PK for this schema (no identity on any PK)."""
    return str(uuid.uuid4()).upper()

def _truthy(v):
    if v is None: return False
    if isinstance(v, bool): return v
    if isinstance(v, (int, float)): return v != 0
    return str(v).strip().lower() in ('1', 'true', 'yes', 'y', 'on')

def _active_str(v):
    return '1' if _truthy(v) else '0'

def _get_tenant_id():
    """Retrieve current tenant ID (0 = Super Admin)."""
    return session.get('tenant_id', 1)

# NOTE: hash_password and verify_password are imported from
# app.modules.auth (§C1). The previous local definitions were
# incompatible with Werkzeug hashes (broke admin password-change
# for any user whose password was hashed by t_Users.hash_password).
# Do NOT re-declare them here.

def _normalize_user_payload(data: dict) -> dict:
    if not data: data = {}
    def pick(*keys):
        for k in keys:
            if k in data and data[k] is not None: return data[k]
        return None

    roles_raw = pick('role_ids', 'Roles', 'roles', 'user_roles')
    if roles_raw is None: role_ids = []
    elif isinstance(roles_raw, str):
        try:
            parsed = json.loads(roles_raw)
            role_ids = parsed if isinstance(parsed, list) else [roles_raw]
        except Exception:
            role_ids = [r.strip() for r in roles_raw.split('|') if r.strip()]
    elif isinstance(roles_raw, list):
        role_ids = [(r.get('RoleCode') or r.get('code') or r.get('RoleName')) if isinstance(r, dict) else r for r in roles_raw]
        role_ids = [r for r in role_ids if r]
    else:
        role_ids = []

    perms_raw = pick('permissions', 'Permissions')
    if perms_raw is None: permissions = {}
    elif isinstance(perms_raw, dict): permissions = perms_raw
    elif isinstance(perms_raw, str):
        try: permissions = json.loads(perms_raw) if perms_raw else {}
        except Exception: permissions = {}
    else: permissions = {}

    return {
        'tenant_id': pick('tenant_id', 'TenantID'),
        'username': pick('username', 'Username') or '',
        'email': pick('email', 'Email') or '',
        'full_name': pick('full_name', 'FullName') or '',
        'full_name_local': pick('full_name_local', 'FullNameLocal') or '',
        'password': pick('password', 'Password') or '',
        'department': pick('department', 'Department') or '',
        'position': pick('position', 'Position') or '',
        'employee_id': pick('employee_id', 'EmployeeID') or '',
        'user_type': pick('user_type', 'UserType') or 'Internal',
        'is_active': _truthy(pick('is_active', 'IsActive')) if ('is_active' in data or 'IsActive' in data) else True,
        'must_change_password': _truthy(pick('must_change_password', 'MustChangePassword')),
        'role_ids': role_ids,
        'permissions': permissions,
    }

def log_audit(action, details, affected_user_id=None):
    """Write to t_AuditLog."""
    try:
        engine = get_engine()
        today_key = str(int(datetime.now().strftime('%Y%m%d')))
        tenant_id = _get_tenant_id()
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO t_AuditLog
                (AuditID, Timestamp, UserID, ActionType, Module, Description, IPAddress, DateKey, TenantID)
                VALUES (:aid, :ts, :uid, :action, 'Admin', :details, :ip, :date_key, :tenant_id)
            """), {
                'aid': _new_id(),
                'ts': datetime.now().isoformat(),
                'uid': current_user.get_id(),
                'action': action,
                'details': details,
                'ip': request.remote_addr if request else None,
                'date_key': today_key,
                'tenant_id': tenant_id
            })
    except Exception as e:
        print(f"Audit log error: {e}")

# ----------------------------------------------------------------------
# API: get user
# ----------------------------------------------------------------------
@api_bp.route('/users/<string:user_id>', methods=['GET'])
@login_required
@admin_required
def get_user(user_id):
    tenant_id = _get_tenant_id()
    filter_tenant = request.args.get('tenant_id')
    
    engine = get_engine()
    with engine.connect() as conn:
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
                   PortalAccess, PortalPermissions, DefaultCalendar, TenantID
            FROM t_Users 
            WHERE UserID = :id
        """
        params = {'id': user_id}
        
        if tenant_id != 0:
            query += " AND TenantID = :tenant_id"
            params['tenant_id'] = tenant_id
        elif filter_tenant:
            query += " AND TenantID = :tenant_id"
            params['tenant_id'] = int(filter_tenant)

        row = conn.execute(text(query), params).fetchone()

        if not row:
            return jsonify({'success': False, 'error': 'User not found or access denied'}), 404

        user_roles = []
        if row[9]:
            try: user_roles = json.loads(row[9]) if isinstance(row[9], str) else row[9]
            except Exception: pass

        permissions = {}
        if row[10]:
            try: permissions = json.loads(row[10]) if isinstance(row[10], str) else row[10]
            except Exception: pass

        user = {
            'UserID': row[0], 'Username': row[1], 'Email': row[2],
            'FullName': row[3], 'FullNameLocal': row[4] or '',
            'Department': row[5] or '', 'Position': row[6] or '',
            'IsActive': _truthy(row[7]), 'UserType': row[8] or 'Internal',
            'UserRoles': user_roles, 'Roles': user_roles,
            'Permissions': permissions, 'MustChangePassword': _truthy(row[11]),
            'LastLogin': row[12] if row[12] else None, 'CreatedDate': row[13] if row[13] else None,
            'EmployeeID': row[14] if row[14] else '', 'LanguageCode': row[15] if row[15] else 'en',
            'DefaultCalendar': row[34] if len(row) > 34 and row[34] else 'gregorian',
            'TenantID': row[35]
        }

        roles_rows = conn.execute(
            text("SELECT RoleCode, RoleName FROM t_Roles WHERE (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0) ORDER BY RoleName"),
            {'tenant_id': tenant_id}
        ).fetchall()
        user['AllRoles'] = [{'RoleCode': r[0], 'RoleName': r[1]} for r in roles_rows]

        return jsonify({'success': True, 'data': user})

# ----------------------------------------------------------------------
# API: list users
# ----------------------------------------------------------------------
@api_bp.route('/users')
@login_required
def list_users():
    try:
        tenant_id = _get_tenant_id()
        filter_tenant = request.args.get('tenant_id')
        
        search = (request.args.get('search') or '').strip()
        status = (request.args.get('status') or '').strip()
        role_filter = (request.args.get('role') or '').strip()
        department = (request.args.get('department') or '').strip()
        user_type = (request.args.get('user_type') or '').strip()
        try: limit = int(request.args.get('limit', 20))
        except: limit = 20
        try: offset = int(request.args.get('offset', 0))
        except: offset = 0
        if limit <= 0 or limit > 500: limit = 20
        if offset < 0: offset = 0

        where = ["1=1"]
        params = {}
        
        # Strict Tenant Filtering
        if tenant_id != 0:
            where.append("TenantID = :tenant_id")
            params['tenant_id'] = tenant_id
        elif filter_tenant:
            where.append("TenantID = :tenant_id")
            params['tenant_id'] = int(filter_tenant)

        if user_type in ('Internal', 'External'):
            where.append("UserType = :utype")
            params['utype'] = user_type
        if department:
            where.append("Department = :dept")
            params['dept'] = department
        if status == 'Active': where.append("IsActive = '1'")
        elif status == 'Inactive': where.append("IsActive = '0'")
        if search:
            where.append("(FullName LIKE :q OR Username LIKE :q OR Email LIKE :q OR FullNameLocal LIKE :q)")
            params['q'] = f"%{search}%"
        if role_filter:
            where.append("UserRoles LIKE :role_pat")
            params['role_pat'] = f'%"{role_filter}"%'

        where_sql = " AND ".join(where)

        engine = get_engine()
        with engine.connect() as conn:
            total_row = conn.execute(text(f"SELECT COUNT(*) FROM t_Users WHERE {where_sql}"), params).fetchone()
            total = int(total_row[0]) if total_row else 0

            data_sql = f"""
                SELECT UserID, Username, Email, FullName, FullNameLocal,
                       Department, Position, IsActive, UserType, UserRoles,
                       Permissions, MustChangePassword, LastLogin, CreatedDate, EmployeeID, TenantID
                FROM t_Users
                WHERE {where_sql}
                ORDER BY TenantID, FullName
                OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY
            """
            page_params = dict(params)
            page_params['offset'] = offset
            page_params['limit'] = limit
            rows = conn.execute(text(data_sql), page_params).fetchall()

            roles_rows = conn.execute(
                text("SELECT RoleCode, RoleName FROM t_Roles WHERE (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0)"),
                {'tenant_id': tenant_id}
            ).fetchall()
            all_roles = {r[0]: r[1] for r in roles_rows}

            users = []
            for row in rows:
                try: user_roles = json.loads(row[9]) if row[9] else []
                except: user_roles = []
                if not isinstance(user_roles, list): user_roles = []
                
                role_names = [all_roles.get(r, r) for r in user_roles]
                users.append({
                    'UserID': row[0], 'Username': row[1], 'Email': row[2],
                    'FullName': row[3], 'FullNameLocal': row[4] or '',
                    'Department': row[5] or '', 'Position': row[6] or '',
                    'IsActive': _truthy(row[7]), 'UserType': row[8] or 'Internal',
                    'Roles': user_roles, 'UserRoles': user_roles, 'RoleNames': role_names,
                    'Permissions': (json.loads(row[10]) if row[10] else {}),
                    'MustChangePassword': _truthy(row[11]),
                    'LastLogin': row[12] if row[12] else None,
                    'CreatedDate': row[13] if row[13] else None,
                    'EmployeeID': row[14] if row[14] else '',
                    'TenantID': row[15]
                })

            kpi = {
                'active_users': int(conn.execute(text(f"SELECT COUNT(*) FROM t_Users WHERE {where_sql} AND IsActive = '1'"), params).scalar() or 0),
                'inactive_users': int(conn.execute(text(f"SELECT COUNT(*) FROM t_Users WHERE {where_sql} AND IsActive = '0'"), params).scalar() or 0),
                'total_users': total,
            }

        return jsonify({'success': True, 'users': users, 'total': total, 'kpi': kpi})
    except Exception as e:
        print(f"Error in list_users: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ----------------------------------------------------------------------
# API: users KPI stats & departments
# ----------------------------------------------------------------------
@api_bp.route('/users/stats')
@login_required
def users_stats():
    try:
        tenant_id = _get_tenant_id()
        filter_tenant = request.args.get('tenant_id')
        engine = get_engine()
        with engine.connect() as conn:
            where = ["1=1"]
            params = {}
            
            if tenant_id != 0:
                where.append("TenantID = :tenant_id")
                params['tenant_id'] = tenant_id
            elif filter_tenant:
                where.append("TenantID = :tenant_id")
                params['tenant_id'] = int(filter_tenant)
                
            where_sql = " AND ".join(where)
            
            total_users = int(conn.execute(text(f"SELECT COUNT(*) FROM t_Users WHERE {where_sql}"), params).scalar() or 0)
            active_users = int(conn.execute(text(f"SELECT COUNT(*) FROM t_Users WHERE {where_sql} AND IsActive = '1'"), params).scalar() or 0)
            inactive_users = int(conn.execute(text(f"SELECT COUNT(*) FROM t_Users WHERE {where_sql} AND IsActive = '0'"), params).scalar() or 0)
            
            if tenant_id != 0:
                roles_count = conn.execute(text("SELECT COUNT(*) FROM t_Roles WHERE TenantID = :tid OR TenantID IS NULL"), {'tid': tenant_id}).scalar()
            else:
                roles_count = conn.execute(text("SELECT COUNT(*) FROM t_Roles")).scalar()

        return jsonify({'success': True, 'data': {'total_users': total_users, 'active_users': active_users, 'inactive_users': inactive_users, 'total_roles': roles_count}})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@api_bp.route('/users/departments')
@login_required
def users_departments():
    try:
        tenant_id = _get_tenant_id()
        filter_tenant = request.args.get('tenant_id')
        engine = get_engine()
        with engine.connect() as conn:
            query = "SELECT DISTINCT Department FROM t_Users WHERE Department IS NOT NULL AND Department <> ''"
            params = {}
            if tenant_id != 0:
                query += " AND TenantID = :tenant_id"
                params['tenant_id'] = tenant_id
            elif filter_tenant:
                query += " AND TenantID = :tenant_id"
                params['tenant_id'] = int(filter_tenant)
            query += " ORDER BY Department"
            
            rows = conn.execute(text(query), params).fetchall()
        return jsonify({'success': True, 'data': [r[0] for r in rows]})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ----------------------------------------------------------------------
# API: activate/deactivate user
# ----------------------------------------------------------------------
@api_bp.route('/users/<string:user_id>/activate', methods=['POST'])
@login_required
@admin_required
def activate_user(user_id):
    tenant_id = _get_tenant_id()
    filter_tenant = request.args.get('tenant_id')
    data = request.get_json(silent=True) or {}
    
    if 'is_active' in data: is_active = _truthy(data['is_active'])
    elif 'IsActive' in data: is_active = _truthy(data['IsActive'])
    else: return jsonify({'success': False, 'error': 'Missing is_active'}), 400

    engine = get_engine()
    with engine.connect() as conn:
        query = "SELECT UserID FROM t_Users WHERE UserID = :uid"
        params = {'uid': user_id}
        if tenant_id != 0:
            query += " AND TenantID = :tenant_id"
            params['tenant_id'] = tenant_id
        elif filter_tenant:
            query += " AND TenantID = :tenant_id"
            params['tenant_id'] = int(filter_tenant)
            
        if not conn.execute(text(query), params).fetchone():
            return jsonify({'success': False, 'error': 'User not found or access denied'}), 404

        conn.execute(
            text(f"""UPDATE t_Users SET IsActive = :active, ModifiedDate = :mod, ModifiedBy = :mby
                    WHERE UserID = :uid {'AND TenantID = :tenant_id' if 'tenant_id' in params else ''}"""),
            {**params, 'active': _active_str(is_active), 'mod': datetime.now().isoformat(), 'mby': current_user.get_id()}
        )
        conn.commit()

    log_audit('ACTIVATE_USER' if is_active else 'DEACTIVATE_USER', f"User status changed for ID {user_id}", user_id)
    return jsonify({'success': True, 'message': 'User status updated'})

# ----------------------------------------------------------------------
# API: create/update/delete user
# ----------------------------------------------------------------------
@api_bp.route('/users', methods=['POST'])
@login_required
def create_user():
    tenant_id = _get_tenant_id()
    raw = request.get_json(silent=True) or {}
    p = _normalize_user_payload(raw)

    if not p['username'] or not p['email'] or not p['full_name'] or not p['password'] or len(p['password']) < 6:
        return jsonify({'success': False, 'error': 'Missing required fields or password too short'}), 400

    # Determine target TenantID
    if tenant_id != 0:
        target_tenant = tenant_id
    else:
        target_tenant = p.get('tenant_id')
        if target_tenant is None:
            return jsonify({'success': False, 'error': 'Super Admin must specify tenant_id'}), 400
        try: target_tenant = int(target_tenant)
        except: return jsonify({'success': False, 'error': 'Invalid tenant_id'}), 400

    engine = get_engine()
    with engine.connect() as conn:
        existing = conn.execute(
            text("SELECT UserID FROM t_Users WHERE Username = :u AND TenantID = :tid"), 
            {'u': p['username'], 'tid': target_tenant}
        ).fetchone()
        if existing: return jsonify({'success': False, 'error': 'Username already exists in this tenant'}), 409

        pwd_hash = hash_password(p['password'])
        now = datetime.now().isoformat()
        new_id = _new_id()

        insert_sql = """
            INSERT INTO t_Users 
            (UserID, TenantID, Username, Email, PasswordHash, FullName, FullNameLocal,
             Department, Position, EmployeeID, IsActive, MustChangePassword,
             UserType, CreatedDate, ModifiedDate, CreatedBy, ModifiedBy,
             UserRoles, Permissions)
            VALUES 
            (:user_id, :tenant_id, :username, :email, :pwdhash, :fullname, :fullnamelocal,
             :dept, :pos, :empid, :active, :mustchange,
             :utype, :created, :modified, :createdby, :modifiedby,
             :roles, :perms)
        """
        conn.execute(text(insert_sql), {
            'user_id': new_id, 'tenant_id': target_tenant, 'username': p['username'],
            'email': p['email'], 'pwdhash': pwd_hash, 'fullname': p['full_name'],
            'fullnamelocal': p['full_name_local'], 'dept': p['department'], 'pos': p['position'],
            'empid': p['employee_id'], 'active': _active_str(p['is_active']),
            'mustchange': _active_str(p['must_change_password']), 'utype': p['user_type'] or 'Internal',
            'created': now, 'modified': now, 'createdby': current_user.get_id(), 'modifiedby': current_user.get_id(),
            'roles': json.dumps(p['role_ids']), 'perms': json.dumps(p['permissions']),
        })
        conn.commit()

    log_audit('CREATE_USER', f"Created user {p['username']} for Tenant {target_tenant}", new_id)
    return jsonify({'success': True, 'message': 'User created', 'UserID': new_id}), 201

@api_bp.route('/users/<string:user_id>', methods=['PUT'])
@login_required
def update_user(user_id):
    tenant_id = _get_tenant_id()
    raw = request.get_json(silent=True) or {}
    p = _normalize_user_payload(raw)

    fields = {}
    if 'username' in raw or 'Username' in raw:
        if p['username']: fields['Username'] = p['username']
    if 'email' in raw or 'Email' in raw:
        if p['email']: fields['Email'] = p['email']
    if 'full_name' in raw or 'FullName' in raw: fields['FullName'] = p['full_name']
    if 'full_name_local' in raw or 'FullNameLocal' in raw: fields['FullNameLocal'] = p['full_name_local']
    if 'department' in raw or 'Department' in raw: fields['Department'] = p['department']
    if 'position' in raw or 'Position' in raw: fields['Position'] = p['position']
    if 'employee_id' in raw or 'EmployeeID' in raw: fields['EmployeeID'] = p['employee_id']
    if 'user_type' in raw or 'UserType' in raw: fields['UserType'] = p['user_type'] or 'Internal'
    if 'is_active' in raw or 'IsActive' in raw: fields['IsActive'] = _active_str(p['is_active'])
    if 'must_change_password' in raw or 'MustChangePassword' in raw: fields['MustChangePassword'] = _active_str(p['must_change_password'])
    if 'role_ids' in raw or 'Roles' in raw or 'roles' in raw: fields['UserRoles'] = json.dumps(p['role_ids'])
    if 'permissions' in raw or 'Permissions' in raw: fields['Permissions'] = json.dumps(p['permissions'])

    new_password = p['password']
    if new_password and len(new_password) >= 6:
        fields['PasswordHash'] = hash_password(new_password)

    engine = get_engine()
    with engine.connect() as conn:
        query = "SELECT TenantID FROM t_Users WHERE UserID = :uid"
        params = {'uid': user_id}
        row = conn.execute(text(query), params).fetchone()
        if not row: return jsonify({'success': False, 'error': 'User not found'}), 404
        
        target_tenant = row[0]
        
        # Reject if tenant admin tries to edit a user outside their tenant
        if tenant_id != 0 and target_tenant != tenant_id:
            return jsonify({'success': False, 'error': 'Access denied'}), 403

        if fields:
            set_clauses = [f"{k} = :{k}" for k in fields.keys()]
            params = dict(fields)
            set_clauses.append("ModifiedDate = :mod")
            params['mod'] = datetime.now().isoformat()
            set_clauses.append("ModifiedBy = :mby")
            params['mby'] = current_user.get_id()
            params['uid'] = user_id
            
            sql = f"UPDATE t_Users SET {', '.join(set_clauses)} WHERE UserID = :uid"
            conn.execute(text(sql), params)
            conn.commit()

    log_audit('UPDATE_USER', f"Updated user ID {user_id}", user_id)
    return jsonify({'success': True, 'message': 'User updated'})

@api_bp.route('/users/<string:user_id>', methods=['DELETE'])
@login_required
def delete_user(user_id):
    tenant_id = _get_tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("SELECT TenantID FROM t_Users WHERE UserID = :uid"), {'uid': user_id}).fetchone()
        if not row: return jsonify({'success': False, 'error': 'User not found'}), 404
        
        target_tenant = row[0]
        if tenant_id != 0 and target_tenant != tenant_id:
            return jsonify({'success': False, 'error': 'Access denied'}), 403

        conn.execute(
            text("UPDATE t_Users SET IsActive = '0', ModifiedDate = :mod, ModifiedBy = :mby WHERE UserID = :uid"),
            {'mod': datetime.now().isoformat(), 'mby': current_user.get_id(), 'uid': user_id}
        )
        conn.commit()

    log_audit('DELETE_USER', f"Deactivated user ID {user_id}", user_id)
    return jsonify({'success': True, 'message': 'User deactivated'})

# ----------------------------------------------------------------------
# API: reset password, sessions, import, export
# ----------------------------------------------------------------------
@api_bp.route('/users/<string:user_id>/reset-password', methods=['POST'])
@login_required
def reset_password(user_id):
    tenant_id = _get_tenant_id()
    data = request.get_json() or {}
    new_password = data.get('new_password')
    if not new_password or len(new_password) < 6:
        return jsonify({'success': False, 'error': 'Password must be at least 6 characters'}), 400

    engine = get_engine()
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT UserID FROM t_Users WHERE UserID = :uid AND (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0)"),
            {'uid': user_id, 'tenant_id': tenant_id}
        ).fetchone()
        if not exists: return jsonify({'success': False, 'error': 'User not found'}), 404

        history = conn.execute(
            text("SELECT TOP 3 PasswordHash FROM t_PasswordHistory WHERE UserID = :uid ORDER BY ChangedDate DESC"),
            {'uid': user_id}
        ).fetchall()
        for h in history:
            if verify_password(h[0], new_password):
                return jsonify({'success': False, 'error': 'Password already used recently'}), 400

        new_hash = hash_password(new_password)
        conn.execute(
            text("UPDATE t_Users SET PasswordHash = :hash, MustChangePassword = '0', ModifiedDate = :mod WHERE UserID = :uid"),
            {'hash': new_hash, 'mod': datetime.now().isoformat(), 'uid': user_id}
        )
        conn.execute(
            text("INSERT INTO t_PasswordHistory (PasswordHistoryID, UserID, PasswordHash, ChangedDate) VALUES (:phid, :uid, :hash, :changed)"),
            {'phid': _new_id(), 'uid': user_id, 'hash': new_hash, 'changed': datetime.now().isoformat()}
        )
        conn.commit()

    log_audit('RESET_PASSWORD', f"Reset password for user ID {user_id}", user_id)
    return jsonify({'success': True, 'message': 'Password reset successfully'})

@api_bp.route('/users/<string:user_id>/sessions')
@login_required
def get_user_sessions(user_id):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT SessionID, LoginTime, LastActivity, IPAddress, UserAgent, IsActive
            FROM t_UserSessions WHERE UserID = :uid AND IsActive = '1' ORDER BY LastActivity DESC
        """), {'uid': user_id}).fetchall()

    return jsonify([{
        'SessionID': r[0], 'LoginTime': r[1], 'LastActivity': r[2], 'IPAddress': r[3],
        'UserAgent': (r[4][:80] + '...') if r[4] and len(r[4]) > 80 else r[4],
        'IsActive': str(r[5]) == '1' if r[5] is not None else False
    } for r in rows])

@api_bp.route('/users/<string:user_id>/sessions/<string:session_id>', methods=['DELETE'])
@login_required
def terminate_session(user_id, session_id):
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(
            text("UPDATE t_UserSessions SET IsActive = '0' WHERE UserID = :uid AND SessionID = :sid"),
            {'uid': user_id, 'sid': session_id}
        )
        conn.commit()
    return jsonify({'success': True, 'message': 'Session terminated'})

@api_bp.route('/users/import', methods=['POST'])
@login_required
def import_users():
    tenant_id = _get_tenant_id()
    if 'file' not in request.files: return jsonify({'success': False, 'error': 'No file uploaded'}), 400
    file = request.files['file']
    if not file.filename.endswith('.csv'): return jsonify({'success': False, 'error': 'Only CSV files accepted'}), 400

    stream = io.TextIOWrapper(file.stream, encoding='utf-8')
    reader = csv.DictReader(stream)
    engine = get_engine()
    created = 0
    errors = []

    with engine.connect() as conn:
        for row in reader:
            username, email, fullname = row.get('Username'), row.get('Email'), row.get('FullName')
            if not username or not email or not fullname:
                errors.append(f"Missing fields in row: {row}"); continue

            existing = conn.execute(
                text("SELECT UserID FROM t_Users WHERE Username = :u AND (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0)"), 
                {'u': username, 'tenant_id': tenant_id}
            ).fetchone()
            if existing: errors.append(f"Username {username} exists, skipped"); continue

            pwd_hash = hash_password('Ch@ngeMe123')
            roles_list = [r.strip() for r in row.get('Roles', '').split('|') if r.strip()]
            
            conn.execute(text("""
                INSERT INTO t_Users 
                (UserID, TenantID, Username, Email, PasswordHash, FullName, Department, Position, EmployeeID, 
                 IsActive, MustChangePassword, UserType, CreatedDate, ModifiedDate, UserRoles)
                VALUES (:uid, :tenant_id, :un, :em, :pwd, :fn, :dept, :pos, :eid, :active, '1', 'Internal', :cdate, :mdate, :roles)
            """), {
                'uid': _new_id(), 'tenant_id': tenant_id, 'un': username, 'em': email,
                'pwd': pwd_hash, 'fn': fullname, 'dept': row.get('Department', ''), 'pos': row.get('Position', ''),
                'eid': row.get('EmployeeID', ''), 'active': '1' if row.get('IsActive', '1').lower() in ('1', 'true', 'yes') else '0',
                'cdate': datetime.now().isoformat(), 'mdate': datetime.now().isoformat(), 'roles': json.dumps(roles_list)
            })
            created += 1
        conn.commit()

    log_audit('IMPORT_USERS', f"Imported {created} users")
    return jsonify({'success': True, 'message': f'Imported {created} users', 'errors': errors})

@api_bp.route('/users/export', methods=['GET'])
@login_required
def export_users():
    tenant_id = _get_tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT Username, Email, FullName, Department, Position, EmployeeID, IsActive, UserRoles, CreatedDate
            FROM t_Users WHERE UserType = 'Internal' AND (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0)
            ORDER BY FullName
        """), {'tenant_id': tenant_id}).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Username', 'Email', 'FullName', 'Department', 'Position', 'EmployeeID', 'IsActive', 'Roles', 'CreatedDate'])
    for r in rows:
        try: roles = json.loads(r[7]) if r[7] else []
        except: roles = []
        writer.writerow([r[0], r[1], r[2], r[3] or '', r[4] or '', r[5] or '', 'Active' if str(r[6]) == '1' else 'Inactive', '|'.join(roles), r[8] if r[8] else ''])

    return current_app.response_class(output.getvalue().encode('utf-8'), mimetype='text/csv', headers={'Content-Disposition': 'attachment; filename=internal_users.csv'})

# ----------------------------------------------------------------------
# External Users Management
# ----------------------------------------------------------------------
@api_bp.route('/external-users')
@login_required
@admin_required
def list_external_users():
    tenant_id = _get_tenant_id()
    company_type = request.args.get('company_type', '')
    active_status = request.args.get('active_status', '')

    engine = get_engine()
    with engine.connect() as conn:
        query = """
            SELECT u.UserID, u.Username, u.Email, u.FullName, u.IsActive, u.LastLogin,
                   u.UserRoles, u.PortalPermissions, u.PortalAccess,
                   c.companyID, c.Name as CompanyName, c.IsCustomer, c.IsSupplier
            FROM t_Users u
            LEFT JOIN t_companies c ON u.CompanyID = c.companyID
            WHERE u.UserType = 'External' AND u.PortalAccess = '1'
              AND (u.TenantID IS NULL OR u.TenantID = :tenant_id OR :tenant_id = 0)
        """
        params = {'tenant_id': tenant_id}
        if active_status in ('0', '1'):
            query += " AND u.IsActive = :active"
            params['active'] = active_status
        if company_type == 'Customer':
            query += " AND c.IsCustomer = '1'"
        elif company_type == 'Supplier':
            query += " AND c.IsSupplier = '1'"
        query += " ORDER BY u.FullName"

        rows = conn.execute(text(query), params).fetchall()
        users = []
        for row in rows:
            try: roles = json.loads(row[6]) if row[6] else []
            except: roles = []
            users.append({
                'UserID': row[0], 'Username': row[1], 'Email': row[2], 'FullName': row[3],
                'IsActive': str(row[4]) == '1' if row[4] is not None else False, 'LastLogin': row[5],
                'UserRoles': roles, 'PortalPermissions': row[7], 'PortalAccess': str(row[8]) == '1',
                'CompanyID': row[9], 'CompanyName': row[10],
                'IsCustomer': str(row[11]) == '1', 'IsSupplier': str(row[12]) == '1'
            })

    active_users = [u for u in users if u['IsActive']]
    kpi_by_type = {'Customer': 0, 'Supplier': 0}
    for u in active_users:
        if u['IsCustomer']: kpi_by_type['Customer'] += 1
        if u['IsSupplier']: kpi_by_type['Supplier'] += 1

    return jsonify({'users': users, 'kpi': {'active_users': len(active_users), 'by_company_type': [{'type': t, 'count': c} for t, c in kpi_by_type.items()]}})

@api_bp.route('/external-users/companies')
@login_required
@admin_required
def get_external_companies():
    tenant_id = _get_tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT companyID, Name, IsCustomer, IsSupplier
            FROM t_companies
            WHERE (IsCustomer = '1' OR IsSupplier = '1')
              AND (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0)
            ORDER BY Name
        """), {'tenant_id': tenant_id}).fetchall()
    return jsonify([{'id': r[0], 'name': r[1], 'isCustomer': str(r[2]) == '1', 'isSupplier': str(r[3]) == '1'} for r in rows])

@api_bp.route('/external-users', methods=['POST'])
@login_required
@admin_required
def create_external_user():
    tenant_id = _get_tenant_id()
    data = request.get_json()
    for f in ['Username', 'Email', 'FullName', 'CompanyID']:
        if f not in data: return jsonify({'error': f'Missing {f}'}), 400

    engine = get_engine()
    with engine.connect() as conn:
        existing = conn.execute(
            text("SELECT UserID FROM t_Users WHERE Username = :u AND (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0)"), 
            {'u': data['Username'], 'tenant_id': tenant_id}
        ).fetchone()
        if existing: return jsonify({'error': 'Username already exists'}), 409

        pwd_hash = hash_password('Portal@2024')
        new_id = _new_id()
        conn.execute(text("""
            INSERT INTO t_Users
            (UserID, TenantID, Username, Email, PasswordHash, FullName, CompanyID, UserType, PortalAccess,
             IsActive, MustChangePassword, UserRoles, PortalPermissions, CreatedDate, ModifiedDate, CreatedBy, ModifiedBy)
            VALUES (:uid, :tenant_id, :un, :em, :pwd, :fn, :cid, 'External', '1',
             :active, '1', :roles, :perms, :cdate, :mdate, :cby, :mby)
        """), {
            'uid': new_id, 'tenant_id': tenant_id, 'un': data['Username'], 'em': data['Email'], 'pwd': pwd_hash,
            'fn': data['FullName'], 'cid': data['CompanyID'], 'active': '1' if data.get('IsActive', True) else '0',
            'roles': json.dumps(data.get('UserRoles', [])), 'perms': data.get('PortalPermissions', ''),
            'cdate': datetime.now().isoformat(), 'mdate': datetime.now().isoformat(), 
            'cby': current_user.get_id(), 'mby': current_user.get_id()
        })
        conn.commit()

    log_audit('CREATE_EXTERNAL_USER', f"Created external user {data['Username']}", new_id)
    return jsonify({'message': 'External user created', 'UserID': new_id}), 201

@api_bp.route('/external-users/<string:user_id>', methods=['PUT'])
@login_required
@admin_required
def update_external_user(user_id):
    tenant_id = _get_tenant_id()
    data = request.get_json()
    allowed = ['Email', 'FullName', 'CompanyID', 'IsActive', 'UserRoles', 'PortalPermissions']

    engine = get_engine()
    with engine.connect() as conn:
        updates = []
        params = {'uid': user_id, 'tenant_id': tenant_id}
        for field in allowed:
            if field in data:
                if field == 'UserRoles':
                    updates.append("UserRoles = :roles")
                    params['roles'] = json.dumps(data['UserRoles'])
                elif field == 'IsActive':
                    updates.append("IsActive = :active")
                    params['active'] = '1' if data['IsActive'] else '0'
                else:
                    updates.append(f"{field} = :{field}")
                    params[field] = data[field]
        if updates:
            updates.append("ModifiedDate = :mod")
            params['mod'] = datetime.now().isoformat()
            updates.append("ModifiedBy = :modby")
            params['modby'] = current_user.get_id()
            
            sql = f"UPDATE t_Users SET {', '.join(updates)} WHERE UserID = :uid AND (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0)"
            conn.execute(text(sql), params)
            conn.commit()
            log_audit('UPDATE_EXTERNAL_USER', f"Updated external user ID {user_id}", user_id)

    return jsonify({'message': 'External user updated'})

@api_bp.route('/external-users/<string:user_id>', methods=['DELETE'])
@login_required
@admin_required
def delete_external_user(user_id):
    tenant_id = _get_tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(
            text("UPDATE t_Users SET IsActive = '0', ModifiedDate = :mod WHERE UserID = :uid AND (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0)"),
            {'mod': datetime.now().isoformat(), 'uid': user_id, 'tenant_id': tenant_id}
        )
        conn.commit()
        log_audit('DEACTIVATE_EXTERNAL_USER', f"Deactivated external user ID {user_id}", user_id)
    return jsonify({'message': 'External user deactivated'})

@api_bp.route('/external-users/<string:user_id>/reset-password', methods=['POST'])
@login_required
@admin_required
def reset_external_user_password(user_id):
    tenant_id = _get_tenant_id()
    data = request.get_json()
    new_password = data.get('new_password')
    if not new_password or len(new_password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400

    engine = get_engine()
    with engine.connect() as conn:
        new_hash = hash_password(new_password)
        conn.execute(
            text("UPDATE t_Users SET PasswordHash = :hash, MustChangePassword = '1', ModifiedDate = :mod WHERE UserID = :uid AND (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0)"),
            {'hash': new_hash, 'mod': datetime.now().isoformat(), 'uid': user_id, 'tenant_id': tenant_id}
        )
        conn.execute(
            text("INSERT INTO t_PasswordHistory (PasswordHistoryID, UserID, PasswordHash, ChangedDate) VALUES (:phid, :uid, :hash, :changed)"),
            {'phid': _new_id(), 'uid': user_id, 'hash': new_hash, 'changed': datetime.now().isoformat()}
        )
        conn.commit()
        log_audit('RESET_PASSWORD', f"Reset password for external user ID {user_id}", user_id)
    return jsonify({'message': 'Password reset successfully'})

@api_bp.route('/external-users/<string:user_id>/audit-log')
@login_required
@admin_required
def get_external_user_audit_log(user_id):
    tenant_id = _get_tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT TOP 50 AuditID, Timestamp, ActionType, Description, UserID
            FROM t_AuditLog
            WHERE Description LIKE :pattern AND (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0)
            ORDER BY Timestamp DESC
        """), {'pattern': f'%user ID {user_id}%', 'tenant_id': tenant_id}).fetchall()
    return jsonify([{'id': r[0], 'timestamp': r[1] if r[1] else None, 'action': r[2], 'details': r[3], 'actor_id': r[4]} for r in rows])

@api_bp.route('/external-users/import', methods=['POST'])
@login_required
@admin_required
def import_external_users():
    tenant_id = _get_tenant_id()
    if 'file' not in request.files: return jsonify({'error': 'No file uploaded'}), 400
    file = request.files['file']
    if not file.filename.endswith('.csv'): return jsonify({'error': 'Only CSV files accepted'}), 400

    stream = io.TextIOWrapper(file.stream, encoding='utf-8')
    reader = csv.DictReader(stream)
    engine = get_engine()
    created = 0
    errors = []

    with engine.connect() as conn:
        for row in reader:
            username, email, fullname, company_name = row.get('Username'), row.get('Email'), row.get('FullName'), row.get('CompanyName')
            if not username or not email or not fullname or not company_name:
                errors.append(f"Missing required fields: {row}"); continue

            comp_row = conn.execute(
                text("SELECT companyID FROM t_companies WHERE Name = :name AND (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0)"),
                {'name': company_name, 'tenant_id': tenant_id}
            ).fetchone()
            if not comp_row: errors.append(f"Company not found: {company_name}"); continue

            existing = conn.execute(
                text("SELECT UserID FROM t_Users WHERE Username = :u AND (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0)"), 
                {'u': username, 'tenant_id': tenant_id}
            ).fetchone()
            if existing: errors.append(f"Username {username} already exists, skipped"); continue

            pwd_hash = hash_password('Portal@2024')
            roles_list = [r.strip() for r in row.get('Roles', '').split('|') if r.strip()]
            
            conn.execute(text("""
                INSERT INTO t_Users
                (UserID, TenantID, Username, Email, PasswordHash, FullName, CompanyID, UserType, PortalAccess,
                 IsActive, MustChangePassword, UserRoles, PortalPermissions, CreatedDate, ModifiedDate, CreatedBy, ModifiedBy)
                VALUES
                (:uid, :tenant_id, :un, :em, :pwd, :fn, :cid, 'External', '1',
                 :active, '1', :roles, :perms, :cdate, :mdate, :cby, :mby)
            """), {
                'uid': _new_id(), 'tenant_id': tenant_id, 'un': username, 'em': email, 'pwd': pwd_hash, 'fn': fullname,
                'cid': comp_row[0], 'active': '1' if row.get('IsActive', '1').lower() in ('1', 'true', 'yes') else '0',
                'roles': json.dumps(roles_list), 'perms': row.get('PortalPermissions', ''),
                'cdate': datetime.now().isoformat(), 'mdate': datetime.now().isoformat(),
                'cby': current_user.get_id(), 'mby': current_user.get_id()
            })
            created += 1
        conn.commit()
    return jsonify({'message': f'Imported {created} external users', 'errors': errors})

@api_bp.route('/external-users/export', methods=['GET'])
@login_required
@admin_required
def export_external_users():
    tenant_id = _get_tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT u.Username, u.Email, u.FullName, c.Name as CompanyName,
                   u.IsActive, u.UserRoles, u.PortalPermissions, u.CreatedDate
            FROM t_Users u
            LEFT JOIN t_companies c ON u.CompanyID = c.companyID
            WHERE u.UserType = 'External' AND u.PortalAccess = '1'
              AND (u.TenantID IS NULL OR u.TenantID = :tenant_id OR :tenant_id = 0)
            ORDER BY u.FullName
        """), {'tenant_id': tenant_id}).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Username', 'Email', 'FullName', 'CompanyName', 'IsActive', 'Roles', 'PortalPermissions', 'CreatedDate'])
    for r in rows:
        try: roles = json.loads(r[5]) if r[5] else []
        except: roles = []
        writer.writerow([r[0], r[1], r[2], r[3], 'Active' if str(r[4]) == '1' else 'Inactive', '|'.join(roles), r[6] or '', r[7] if r[7] else ''])
    return current_app.response_class(output.getvalue().encode('utf-8'), mimetype='text/csv', headers={'Content-Disposition': 'attachment; filename=external_users.csv'})


# ----------------------------------------------------------------------
# Roles & Permissions
# ----------------------------------------------------------------------
@api_bp.route('/permissions/modules')
@login_required
@admin_required
def get_permission_modules():
    tenant_id = _get_tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT DISTINCT Module FROM t_Permissions WHERE IsActive = '1' AND (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0) ORDER BY Module"),
            {'tenant_id': tenant_id}
        ).fetchall()
    return jsonify([r[0] for r in rows])

@api_bp.route('/permissions')
@login_required
@admin_required
def get_all_permissions():
    tenant_id = _get_tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT PermissionID, PermissionCode, Module, PermissionName, Description
            FROM t_Permissions
            WHERE IsActive = '1' AND (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0)
            ORDER BY Module, PermissionName
        """), {'tenant_id': tenant_id}).fetchall()
    permissions_by_module = {}
    for row in rows:
        module = row[2]
        permissions_by_module.setdefault(module, []).append({
            'id': row[0], 'code': row[1], 'name': row[3], 'description': row[4]
        })
    return jsonify({'success': True, 'data': permissions_by_module})

@api_bp.route('/roles')
@login_required
@admin_required
def get_roles():
    tenant_id = _get_tenant_id()
    engine = get_engine()
    roles = []
    with engine.connect() as conn:
        roles_raw = conn.execute(text("""
            SELECT RoleID, RoleCode, RoleName, Description, Permissions, IsSystemRole
            FROM t_Roles WHERE (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0) ORDER BY RoleName
        """), {'tenant_id': tenant_id}).fetchall()
        
        for r in roles_raw:
            role_code = r[1]
            try: perms = json.loads(r[4]) if r[4] else {}
            except: perms = {}
            
            count_row = conn.execute(
                text("SELECT COUNT(*) FROM t_Users WHERE UserRoles LIKE :pat AND (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0)"),
                {'pat': f'%"{role_code}"%', 'tenant_id': tenant_id}
            ).scalar() or 0
            
            roles.append({
                'id': r[0], 'code': role_code, 'name': r[2], 'description': r[3], 'permissions': perms,
                'isSystem': str(r[5]) == '1' if r[5] is not None else False, 'userCount': count_row,
                'RoleID': r[0], 'RoleCode': role_code, 'RoleName': r[2],
            })
    return jsonify({'success': True, 'data': roles})

@api_bp.route('/roles', methods=['POST'])
@login_required
@admin_required
def create_role():
    tenant_id = _get_tenant_id()
    data = request.get_json()
    if not data.get('RoleCode') or not data.get('RoleName'):
        return jsonify({'error': 'Missing RoleCode or RoleName'}), 400

    engine = get_engine()
    with engine.connect() as conn:
        existing = conn.execute(
            text("SELECT RoleID FROM t_Roles WHERE RoleCode = :code AND (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0)"),
            {'code': data['RoleCode'], 'tenant_id': tenant_id}
        ).fetchone()
        if existing: return jsonify({'error': 'Role code already exists'}), 409

        today_key = str(int(datetime.now().strftime('%Y%m%d')))
        new_id = _new_id()
        conn.execute(text("""
            INSERT INTO t_Roles
            (RoleID, TenantID, RoleCode, RoleName, Description, Permissions, WorkflowRights, IsSystemRole, CreatedDateKey, ModifiedDateKey)
            VALUES (:rid, :tenant_id, :code, :name, :desc, :perms, :wf, '0', :cdate, :mdate)
        """), {
            'rid': new_id, 'tenant_id': tenant_id, 'code': data['RoleCode'], 'name': data['RoleName'],
            'desc': data.get('Description', ''), 'perms': json.dumps(data.get('Permissions', {})),
            'wf': json.dumps(data.get('WorkflowRights', {})), 'cdate': today_key, 'mdate': today_key
        })
        conn.commit()

    return jsonify({'message': 'Role created', 'RoleID': new_id}), 201

@api_bp.route('/roles/<string:role_id>', methods=['PUT'])
@login_required
@admin_required
def update_role(role_id):
    tenant_id = _get_tenant_id()
    data = request.get_json()
    allowed = ['RoleName', 'Description', 'Permissions', 'WorkflowRights']
    engine = get_engine()
    with engine.connect() as conn:
        updates = []
        params = {'rid': role_id, 'tenant_id': tenant_id, 'mdate': str(int(datetime.now().strftime('%Y%m%d')))}
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
            # Prevent tenant admins from editing global system roles
            sql = f"UPDATE t_Roles SET {', '.join(updates)} WHERE RoleID = :rid AND (TenantID = :tenant_id OR :tenant_id = 0)"
            conn.execute(text(sql), params)
            conn.commit()
    return jsonify({'message': 'Role updated'})

@api_bp.route('/roles/<string:role_id>', methods=['DELETE'])
@login_required
@admin_required
def delete_role(role_id):
    tenant_id = _get_tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        role_info = conn.execute(
            text("SELECT RoleCode, IsSystemRole FROM t_Roles WHERE RoleID = :rid AND (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0)"),
            {'rid': role_id, 'tenant_id': tenant_id}
        ).fetchone()
        if not role_info: return jsonify({'error': 'Role not found'}), 404
        if str(role_info[1]) == '1': return jsonify({'error': 'Cannot delete system role'}), 403

        user_with_role = conn.execute(
            text("SELECT COUNT(*) FROM t_Users WHERE UserRoles LIKE :pat AND (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0)"),
            {'pat': f'%"{role_info[0]}"%', 'tenant_id': tenant_id}
        ).scalar() or 0
        if user_with_role > 0:
            return jsonify({'error': f'Cannot delete role: {user_with_role} user(s) still have this role'}), 409

        # Prevent tenant admins from deleting global system roles
        conn.execute(text("DELETE FROM t_Roles WHERE RoleID = :rid AND (TenantID = :tenant_id OR :tenant_id = 0)"), {'rid': role_id, 'tenant_id': tenant_id})
        conn.commit()
    return jsonify({'message': 'Role deleted'})

@api_bp.route('/user-exceptions')
@login_required
@admin_required
def get_user_exceptions():
    tenant_id = _get_tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT UserID, Username, FullName, Email, Permissions
            FROM t_Users
            WHERE Permissions IS NOT NULL AND Permissions != '{}' AND Permissions != ''
              AND (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0)
            ORDER BY FullName
        """), {'tenant_id': tenant_id}).fetchall()
    return jsonify([{
        'id': r[0], 'username': r[1], 'fullname': r[2], 'email': r[3],
        'permissions': json.loads(r[4]) if r[4] else {}
    } for r in rows])

@api_bp.route('/user-exceptions/<string:user_id>', methods=['PUT'])
@login_required
@admin_required
def update_user_exception(user_id):
    tenant_id = _get_tenant_id()
    data = request.get_json()
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(
            text("UPDATE t_Users SET Permissions = :perms WHERE UserID = :uid AND (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0)"),
            {'perms': json.dumps(data.get('Permissions', {})), 'uid': user_id, 'tenant_id': tenant_id}
        )
        conn.commit()
    return jsonify({'message': 'User permissions updated'})

@api_bp.route('/user-exceptions/<string:user_id>', methods=['DELETE'])
@login_required
@admin_required
def delete_user_exception(user_id):
    tenant_id = _get_tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(
            text("UPDATE t_Users SET Permissions = NULL WHERE UserID = :uid AND (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0)"),
            {'uid': user_id, 'tenant_id': tenant_id}
        )
        conn.commit()
    return jsonify({'message': 'Custom permissions removed'})

# ----------------------------------------------------------------------
# Entity Permissions & Projects
# ----------------------------------------------------------------------
@api_bp.route('/entity-permissions')
@login_required
@admin_required
def get_entity_permissions():
    tenant_id = _get_tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT ep.EntityPermissionID, ep.ProjectID, p.ProjectName,
                   ep.RoleID, r.RoleName, ep.PermissionCode, ep.IsAllowed, ep.ExpiryDateKey
            FROM t_EntityPermission ep
            LEFT JOIN t_Project p ON ep.ProjectID = p.ProjectID
            LEFT JOIN t_Roles r ON ep.RoleID = r.RoleID
            WHERE (ep.TenantID IS NULL OR ep.TenantID = :tenant_id OR :tenant_id = 0)
            ORDER BY p.ProjectName, r.RoleName, ep.PermissionCode
        """), {'tenant_id': tenant_id}).fetchall()
    return jsonify([{
        'id': r[0], 'projectId': r[1], 'projectName': r[2], 'roleId': r[3], 'roleName': r[4],
        'permissionCode': r[5], 'isAllowed': str(r[6]) == '1' if r[6] is not None else False, 'expiryDateKey': r[7]
    } for r in rows])

@api_bp.route('/entity-permissions', methods=['POST'])
@login_required
@admin_required
def add_entity_permission():
    tenant_id = _get_tenant_id()
    data = request.get_json()
    if not data.get('ProjectID') or not data.get('RoleID') or not data.get('PermissionCode'):
        return jsonify({'error': 'Missing required fields'}), 400

    engine = get_engine()
    with engine.connect() as conn:
        existing = conn.execute(text("""
            SELECT EntityPermissionID FROM t_EntityPermission
            WHERE ProjectID = :proj AND RoleID = :role AND PermissionCode = :perm AND TenantID = :tenant_id
        """), {'proj': data['ProjectID'], 'role': data['RoleID'], 'perm': data['PermissionCode'], 'tenant_id': tenant_id}).fetchone()
        if existing: return jsonify({'error': 'Permission already defined for this project/role'}), 409

        expiry = data.get('ExpiryDateKey')
        conn.execute(text("""
            INSERT INTO t_EntityPermission
            (EntityPermissionID, TenantID, ProjectID, RoleID, PermissionCode, IsAllowed, ExpiryDateKey)
            VALUES (:eid, :tenant_id, :proj, :role, :perm, :allowed, :expiry)
        """), {
            'eid': _new_id(), 'tenant_id': tenant_id, 'proj': data['ProjectID'],
            'role': data['RoleID'], 'perm': data['PermissionCode'],
            'allowed': '1' if data.get('IsAllowed', 1) else '0',
            'expiry': str(expiry) if expiry else None
        })
        conn.commit()
    return jsonify({'message': 'Entity permission added'}), 201

@api_bp.route('/entity-permissions/<string:entity_perm_id>', methods=['DELETE'])
@login_required
@admin_required
def delete_entity_permission(entity_perm_id):
    tenant_id = _get_tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(
            text("DELETE FROM t_EntityPermission WHERE EntityPermissionID = :id AND (TenantID = :tenant_id OR :tenant_id = 0)"),
            {'id': entity_perm_id, 'tenant_id': tenant_id}
        )
        conn.commit()
    return jsonify({'message': 'Entity permission removed'})

@api_bp.route('/projects/list')
@login_required
@admin_required
def get_projects_list():
    tenant_id = _get_tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT ProjectID, ProjectName FROM t_Project WHERE (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0) ORDER BY ProjectName"),
            {'tenant_id': tenant_id}
        ).fetchall()
    return jsonify([{'id': r[0], 'name': r[1]} for r in rows])

@api_bp.route('/all-users')
@login_required
@admin_required
def get_all_users():
    tenant_id = _get_tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT UserID, Username, FullName, Email, UserType FROM t_Users WHERE (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0) ORDER BY FullName"),
            {'tenant_id': tenant_id}
        ).fetchall()
    return jsonify([{'id': r[0], 'username': r[1], 'fullname': r[2], 'email': r[3], 'type': r[4]} for r in rows])

# ----------------------------------------------------------------------
# Workflow Designer
# ----------------------------------------------------------------------
@api_bp.route('/workflows')
@login_required
@admin_required
def list_workflows():
    tenant_id = _get_tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT wf.WorkflowID, wf.WorkflowCode, wf.WorkflowName, wf.Module, wf.EntityType,
                   wf.Version, wf.IsActive, wf.Description, wf.CreatedDateKey, wf.ModifiedDateKey
            FROM t_WorkflowDefinitions wf
            WHERE (wf.TenantID IS NULL OR wf.TenantID = :tenant_id OR :tenant_id = 0)
            ORDER BY wf.WorkflowCode, TRY_CAST(wf.Version AS INT) DESC
        """), {'tenant_id': tenant_id}).fetchall()
    workflows = [{
        'id': r[0], 'code': r[1], 'name': r[2], 'module': r[3], 'entityType': r[4],
        'version': r[5], 'isActive': str(r[6]) == '1' if r[6] is not None else False,
        'description': r[7], 'createdDateKey': r[8], 'modifiedDateKey': r[9]
    } for r in rows]

    active_codes = {w['code'] for w in workflows if w['isActive']}
    codes_all = {w['code'] for w in workflows}
    return jsonify({
        'workflows': workflows,
        'kpi': {'active_workflows': len(active_codes), 'pending_activations': len(codes_all - active_codes)}
    })

@api_bp.route('/workflows/<string:workflow_id>')
@login_required
@admin_required
def get_workflow(workflow_id):
    tenant_id = _get_tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT WorkflowID, WorkflowCode, WorkflowName, Module, EntityType, Description, Steps, Version, IsActive, Conditions, ApplicableTo, CreatedDateKey, ModifiedDateKey
            FROM t_WorkflowDefinitions
            WHERE WorkflowID = :id AND (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0)
        """), {'id': workflow_id, 'tenant_id': tenant_id}).fetchone()
        if not row: return jsonify({'error': 'Not found'}), 404
        
        def safe_json_load(val, default):
            try: return json.loads(val) if val else default
            except: return default

        return jsonify({
            'id': row[0], 'code': row[1], 'name': row[2], 'module': row[3], 'entityType': row[4], 'description': row[5],
            'steps': safe_json_load(row[6], []), 'version': row[7], 'isActive': str(row[8]) == '1',
            'conditions': safe_json_load(row[9], {}), 'applicableTo': safe_json_load(row[10], {}),
            'createdDateKey': row[11], 'modifiedDateKey': row[12]
        })

@api_bp.route('/workflows', methods=['POST'])
@login_required
@admin_required
def create_workflow():
    tenant_id = _get_tenant_id()
    data = request.get_json()
    if not data.get('code') or not data.get('name') or not data.get('module') or not data.get('entityType'):
        return jsonify({'error': 'Missing required fields'}), 400

    engine = get_engine()
    with engine.connect() as conn:
        existing = conn.execute(
            text("SELECT WorkflowID FROM t_WorkflowDefinitions WHERE WorkflowCode = :code AND (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0)"),
            {'code': data['code'], 'tenant_id': tenant_id}
        ).fetchone()
        if existing: return jsonify({'error': 'Workflow code already exists'}), 409

        new_id = _new_id()
        today_key = str(int(datetime.now().strftime('%Y%m%d')))
        conn.execute(text("""
            INSERT INTO t_WorkflowDefinitions
            (WorkflowID, TenantID, WorkflowCode, WorkflowName, Module, EntityType, Description, Steps, Version, IsActive, Conditions, ApplicableTo, CreatedDateKey, ModifiedDateKey, CreatedBy, ModifiedBy)
            VALUES (:wid, :tenant_id, :code, :name, :mod, :entity, :desc, :steps, '1', '0', :cond, :app, :cdate, :mdate, :cby, :mby)
        """), {
            'wid': new_id, 'tenant_id': tenant_id, 'code': data['code'], 'name': data['name'], 'mod': data['module'],
            'entity': data['entityType'], 'desc': data.get('description', ''), 'steps': json.dumps(data.get('steps', [])),
            'cond': json.dumps(data.get('conditions', {})), 'app': json.dumps(data.get('applicableTo', {})),
            'cdate': today_key, 'mdate': today_key, 'cby': current_user.get_id(), 'mby': current_user.get_id()
        })
        conn.commit()
    return jsonify({'message': 'Workflow created', 'id': new_id}), 201

@api_bp.route('/workflows/<string:workflow_id>', methods=['PUT'])
@login_required
@admin_required
def update_workflow(workflow_id):
    tenant_id = _get_tenant_id()
    data = request.get_json()
    engine = get_engine()
    with engine.connect() as conn:
        cur = conn.execute(
            text("SELECT WorkflowCode, Version FROM t_WorkflowDefinitions WHERE WorkflowID = :id AND (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0)"),
            {'id': workflow_id, 'tenant_id': tenant_id}
        ).fetchone()
        if not cur: return jsonify({'error': 'Workflow not found'}), 404

        try: current_version_int = int(cur[1] or 1)
        except: current_version_int = 1
        
        new_version_str = str(current_version_int + 1 if data.get('create_new_version', False) else current_version_int)
        today_key = str(int(datetime.now().strftime('%Y%m%d')))
        
        params = {
            'name': data.get('name'), 'mod': data.get('module'), 'entity': data.get('entityType'), 'desc': data.get('description', ''),
            'steps': json.dumps(data.get('steps', [])), 'cond': json.dumps(data.get('conditions', {})), 'app': json.dumps(data.get('applicableTo', {})),
            'mdate': today_key, 'mby': current_user.get_id(), 'tenant_id': tenant_id
        }

        if data.get('create_new_version', False):
            new_id = _new_id()
            params.update({'wid': new_id, 'code': cur[0], 'version': new_version_str, 'cdate': today_key, 'cby': current_user.get_id()})
            conn.execute(text("""
                INSERT INTO t_WorkflowDefinitions
                (WorkflowID, TenantID, WorkflowCode, WorkflowName, Module, EntityType, Description, Steps, Version, IsActive, Conditions, ApplicableTo, CreatedDateKey, ModifiedDateKey, CreatedBy, ModifiedBy)
                VALUES (:wid, :tenant_id, :code, :name, :mod, :entity, :desc, :steps, :version, '0', :cond, :app, :cdate, :mdate, :cby, :mby)
            """), params)
            conn.commit()
            return jsonify({'message': 'New version created', 'id': new_id, 'version': new_version_str}), 200
        else:
            params['id'] = workflow_id
            conn.execute(text("""
                UPDATE t_WorkflowDefinitions SET
                WorkflowName = :name, Module = :mod, EntityType = :entity, Description = :desc,
                Steps = :steps, Conditions = :cond, ApplicableTo = :app, ModifiedDateKey = :mdate, ModifiedBy = :mby
                WHERE WorkflowID = :id AND (TenantID = :tenant_id OR :tenant_id = 0)
            """), params)
            conn.commit()
            return jsonify({'message': 'Workflow updated', 'id': workflow_id}), 200

# ----------------------------------------------------------------------
# System Configuration & Audit
# ----------------------------------------------------------------------
@api_bp.route('/config')
@login_required
@admin_required
def get_config():
    tenant_id = _get_tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT ConfigKey, ConfigValue, Description, Category FROM t_AdminConfig WHERE (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0) ORDER BY Category, ConfigKey"),
            {'tenant_id': tenant_id}
        ).fetchall()
    return jsonify([{'key': r[0], 'value': r[1], 'description': r[2], 'category': r[3] or 'General'} for r in rows])

@api_bp.route('/config/categories')
@login_required
@admin_required
def get_config_categories():
    tenant_id = _get_tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT DISTINCT Category FROM t_AdminConfig WHERE Category IS NOT NULL AND (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0)"),
            {'tenant_id': tenant_id}
        ).fetchall()
    categories = [r[0] for r in rows if r[0]]
    return jsonify(categories if categories else ['General'])

@api_bp.route('/config/<string:key>', methods=['PUT'])
@login_required
@admin_required
def update_config(key):
    tenant_id = _get_tenant_id()
    data = request.get_json()
    if data.get('value') is None: return jsonify({'error': 'Missing value'}), 400
    
    engine = get_engine()
    with engine.connect() as conn:
        existing = conn.execute(
            text("SELECT ConfigKey FROM t_AdminConfig WHERE ConfigKey = :key AND (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0)"),
            {'key': key, 'tenant_id': tenant_id}
        ).fetchone()
        if not existing: return jsonify({'error': 'Configuration key not found'}), 404

        update_parts = ["ConfigValue = :val"]
        params = {'key': key, 'val': data['value'], 'tenant_id': tenant_id}
        if data.get('description') is not None:
            update_parts.append("Description = :desc")
            params['desc'] = data['description']
        if data.get('category') is not None:
            update_parts.append("Category = :cat")
            params['cat'] = data['category']
        
        sql = f"UPDATE t_AdminConfig SET {', '.join(update_parts)} WHERE ConfigKey = :key AND (TenantID = :tenant_id OR :tenant_id = 0)"
        conn.execute(text(sql), params)
        conn.commit()
        log_audit('UPDATE_CONFIG', f"Updated config key {key} to {data['value']}")
    return jsonify({'message': 'Configuration updated'})

@api_bp.route('/audit-log')
@login_required
@admin_required
def get_audit_log():
    tenant_id = _get_tenant_id()
    user_id, module, action_type = request.args.get('user_id'), request.args.get('module'), request.args.get('action_type')
    date_from, date_to = request.args.get('date_from'), request.args.get('date_to')
    limit = request.args.get('limit', 200, type=int)

    engine = get_engine()
    query = """
        SELECT TOP (:lim)
            a.AuditID, a.Timestamp, a.DateKey, d.GregorianDate, d.PersianStr,
            a.UserID, u.Username, u.FullName, a.ActionType, a.Module, a.TableName, a.RecordID,
            a.OldValues, a.NewValues, a.IPAddress, a.Description
        FROM t_AuditLog a
        LEFT JOIN t_Users u ON a.UserID = u.UserID
        LEFT JOIN DimDate d ON a.DateKey = d.DateKey
        WHERE (a.TenantID IS NULL OR a.TenantID = :tenant_id OR :tenant_id = 0)
    """
    params = {'lim': limit, 'tenant_id': tenant_id}

    if user_id: query += " AND a.UserID = :uid"; params['uid'] = user_id
    if module: query += " AND a.Module = :mod"; params['mod'] = module
    if action_type: query += " AND a.ActionType = :act"; params['act'] = action_type
    if date_from: query += " AND TRY_CAST(a.DateKey AS INT) >= :df"; params['df'] = int(date_from.replace('-', ''))
    if date_to: query += " AND TRY_CAST(a.DateKey AS INT) <= :dt"; params['dt'] = int(date_to.replace('-', ''))

    query += " ORDER BY a.Timestamp DESC"

    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
        logs = [{
            'id': r[0], 'timestamp': str(r[1]) if r[1] else None, 'dateKey': r[2],
            'gregorianDate': r[3], 'persianStr': r[4], 'userId': r[5], 'username': r[6] or f'User {r[5]}', 'fullName': r[7],
            'action': r[8], 'module': r[9], 'tableName': r[10], 'recordId': r[11], 'oldValues': r[12], 'newValues': r[13], 'ipAddress': r[14], 'description': r[15]
        } for r in rows]

        today_key_str = str(int(datetime.now().strftime('%Y%m%d')))
        kpi_today = conn.execute(
            text("SELECT COUNT(*) FROM t_AuditLog WHERE DateKey = :today AND (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0)"),
            {'today': today_key_str, 'tenant_id': tenant_id}
        ).scalar() or 0

        thirty_days_ago = str(int((datetime.now() - timedelta(days=30)).strftime('%Y%m%d')))
        kpi_rows = conn.execute(text("""
            SELECT TOP 5 u.Username, COUNT(*) as cnt
            FROM t_AuditLog a
            LEFT JOIN t_Users u ON a.UserID = u.UserID
            WHERE TRY_CAST(a.DateKey AS INT) >= TRY_CAST(:cutoff AS INT)
              AND (a.TenantID IS NULL OR a.TenantID = :tenant_id OR :tenant_id = 0)
            GROUP BY u.Username
            ORDER BY cnt DESC
        """), {'cutoff': thirty_days_ago, 'tenant_id': tenant_id}).fetchall()

    return jsonify({'logs': logs, 'kpi': {'today_events': kpi_today, 'by_user': [{'username': r[0] or 'Unknown', 'count': r[1]} for r in kpi_rows]}})

@api_bp.route('/audit-log/filters')
@login_required
@admin_required
def get_audit_filters():
    tenant_id = _get_tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        users = conn.execute(text("""
            SELECT DISTINCT a.UserID, u.Username, u.FullName
            FROM t_AuditLog a LEFT JOIN t_Users u ON a.UserID = u.UserID
            WHERE (a.TenantID IS NULL OR a.TenantID = :tenant_id OR :tenant_id = 0)
            ORDER BY u.Username
        """), {'tenant_id': tenant_id}).fetchall()
        modules = conn.execute(
            text("SELECT DISTINCT Module FROM t_AuditLog WHERE Module IS NOT NULL AND (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0) ORDER BY Module"),
            {'tenant_id': tenant_id}
        ).fetchall()
        actions = conn.execute(
            text("SELECT DISTINCT ActionType FROM t_AuditLog WHERE ActionType IS NOT NULL AND (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0) ORDER BY ActionType"),
            {'tenant_id': tenant_id}
        ).fetchall()

    return jsonify({
        'users': [{'id': u[0], 'username': u[1] or f'User {u[0]}', 'fullName': u[2]} for u in users],
        'modules': [m[0] for m in modules], 'actions': [a[0] for a in actions]
    })

# ----------------------------------------------------------------------
# Document Types
# ----------------------------------------------------------------------
@api_bp.route('/document-types')
@login_required
@admin_required
def list_document_types():
    tenant_id = _get_tenant_id()
    query = "SELECT DocumentTypeID, Code, Name, Category, Description, IsActive, CreatedDateKey, ModifiedDateKey FROM t_DocumentType WHERE (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0)"
    params = {'tenant_id': tenant_id}

    if request.args.get('category'):
        query += " AND Category = :cat"
        params['cat'] = request.args.get('category')
    if request.args.get('active_only') == 'true':
        query += " AND IsActive = '1'"
    if request.args.get('search'):
        query += " AND (Code LIKE :search OR Name LIKE :search)"
        params['search'] = f"%{request.args.get('search')}%"
    query += " ORDER BY Code"

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
    
    doc_types = [{
        'id': r[0], 'code': r[1], 'name': r[2], 'category': r[3], 'description': r[4],
        'isActive': str(r[5]) == '1' if r[5] is not None else False,
        'createdDateKey': r[6], 'modifiedDateKey': r[7]
    } for r in rows]

    categories, total_active = {}, 0
    for dt in doc_types:
        if dt['isActive']:
            total_active += 1
            cat = dt['category'] or 'Uncategorized'
            categories[cat] = categories.get(cat, 0) + 1

    return jsonify({'documentTypes': doc_types, 'kpi': {'total_active': total_active, 'by_category': [{'category': k, 'count': v} for k, v in categories.items()]}})

@api_bp.route('/document-types/categories')
@login_required
@admin_required
def get_document_categories():
    tenant_id = _get_tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT DISTINCT Category FROM t_DocumentType WHERE Category IS NOT NULL AND Category != '' AND (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0) ORDER BY Category"),
            {'tenant_id': tenant_id}
        ).fetchall()
    return jsonify([r[0] for r in rows])

@api_bp.route('/document-types', methods=['POST'])
@login_required
@admin_required
def create_document_type():
    tenant_id = _get_tenant_id()
    data = request.get_json()
    if not data.get('code') or not data.get('name'):
        return jsonify({'error': 'Code and Name are required'}), 400

    engine = get_engine()
    with engine.connect() as conn:
        if conn.execute(
            text("SELECT DocumentTypeID FROM t_DocumentType WHERE Code = :code AND (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0)"),
            {'code': data['code'], 'tenant_id': tenant_id}
        ).fetchone(): return jsonify({'error': 'Code already exists'}), 409

        new_id, today_key = _new_id(), str(int(datetime.now().strftime('%Y%m%d')))
        conn.execute(text("""
            INSERT INTO t_DocumentType
            (DocumentTypeID, TenantID, Code, Name, Category, Description, IsActive, CreatedDateKey, ModifiedDateKey)
            VALUES (:did, :tenant_id, :code, :name, :cat, :desc, :active, :cdate, :mdate)
        """), {
            'did': new_id, 'tenant_id': tenant_id, 'code': data['code'], 'name': data['name'],
            'cat': data.get('category'), 'desc': data.get('description'), 'active': '1' if data.get('isActive', 1) else '0',
            'cdate': today_key, 'mdate': today_key
        })
        conn.commit()
    return jsonify({'message': 'Document type created', 'id': new_id}), 201

@api_bp.route('/document-types/<string:doc_id>', methods=['PUT'])
@login_required
@admin_required
def update_document_type(doc_id):
    tenant_id = _get_tenant_id()
    data = request.get_json()
    engine = get_engine()
    with engine.connect() as conn:
        updates, params = [], {'id': doc_id, 'tenant_id': tenant_id, 'mdate': str(int(datetime.now().strftime('%Y%m%d')))}
        for field in ['name', 'category', 'description', 'isActive']:
            if field in data:
                if field == 'isActive':
                    updates.append("IsActive = :isActive")
                    params['isActive'] = '1' if data['isActive'] else '0'
                else:
                    updates.append(f"{field} = :{field}")
                    params[field] = data[field]
        if updates:
            updates.append("ModifiedDateKey = :mdate")
            sql = f"UPDATE t_DocumentType SET {', '.join(updates)} WHERE DocumentTypeID = :id AND (TenantID = :tenant_id OR :tenant_id = 0)"
            conn.execute(text(sql), params)
            conn.commit()
    return jsonify({'message': 'Document type updated'})

@api_bp.route('/document-types/<string:doc_id>', methods=['DELETE'])
@login_required
@admin_required
def archive_document_type(doc_id):
    tenant_id = _get_tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(
            text("UPDATE t_DocumentType SET IsActive = '0', ModifiedDateKey = :mdate WHERE DocumentTypeID = :id AND (TenantID = :tenant_id OR :tenant_id = 0)"),
            {'id': doc_id, 'tenant_id': tenant_id, 'mdate': str(int(datetime.now().strftime('%Y%m%d')))}
        )
        conn.commit()
    return jsonify({'message': 'Document type archived'})

@api_bp.route('/document-types/<string:doc_id>')
@login_required
@admin_required
def get_document_type(doc_id):
    tenant_id = _get_tenant_id()
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT DocumentTypeID, Code, Name, Category, Description, IsActive, CreatedDateKey, ModifiedDateKey
            FROM t_DocumentType WHERE DocumentTypeID = :id AND (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0)
        """), {'id': doc_id, 'tenant_id': tenant_id}).fetchone()
        if not row: return jsonify({'error': 'Not found'}), 404
        return jsonify({
            'id': row[0], 'code': row[1], 'name': row[2], 'category': row[3], 'description': row[4],
            'isActive': str(row[5]) == '1' if row[5] is not None else False,
            'createdDateKey': row[6], 'modifiedDateKey': row[7]
        })

@api_bp.route('/document-types/export')
@login_required
@admin_required
def export_document_types():
    tenant_id = _get_tenant_id()
    query = "SELECT Code, Name, Category, Description, IsActive FROM t_DocumentType WHERE (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0)"
    params = {'tenant_id': tenant_id}

    if request.args.get('category'): query += " AND Category = :cat"; params['cat'] = request.args.get('category')
    if request.args.get('active_only') == 'true': query += " AND IsActive = '1'"
    if request.args.get('search'): query += " AND (Code LIKE :search OR Name LIKE :search)"; params['search'] = f"%{request.args.get('search')}%"
    query += " ORDER BY Code"

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Code', 'Name', 'Category', 'Description', 'IsActive'])
    for r in rows:
        writer.writerow([r[0], r[1], r[2], r[3], 'Active' if str(r[4]) == '1' else 'Inactive'])
    return current_app.response_class(output.getvalue().encode('utf-8-sig'), mimetype='text/csv', headers={'Content-Disposition': 'attachment; filename=document_types.csv'})

@api_bp.route('/document-types/import', methods=['POST'])
@login_required
@admin_required
def import_document_types():
    tenant_id = _get_tenant_id()
    if 'file' not in request.files: return jsonify({'error': 'No file uploaded'}), 400
    file = request.files['file']
    if not file.filename.endswith('.csv'): return jsonify({'error': 'Only CSV files accepted'}), 400
    stream = io.TextIOWrapper(file.stream, encoding='utf-8')
    reader = csv.DictReader(stream)
    engine = get_engine()
    today_key = str(int(datetime.now().strftime('%Y%m%d')))
    created, errors = 0, []

    with engine.connect() as conn:
        for row in reader:
            code, name = row.get('Code'), row.get('Name')
            if not code or not name: errors.append(f'Missing Code or Name in row: {row}'); continue
            
            existing = conn.execute(
                text("SELECT DocumentTypeID FROM t_DocumentType WHERE Code = :code AND (TenantID IS NULL OR TenantID = :tenant_id OR :tenant_id = 0)"),
                {'code': code, 'tenant_id': tenant_id}
            ).fetchone()
            if existing: errors.append(f'Code {code} already exists, skipped'); continue
            
            conn.execute(text("""
                INSERT INTO t_DocumentType
                (DocumentTypeID, TenantID, Code, Name, Category, Description, IsActive, CreatedDateKey, ModifiedDateKey)
                VALUES (:did, :tenant_id, :code, :name, :cat, :desc, :active, :cdate, :mdate)
            """), {
                'did': _new_id(), 'tenant_id': tenant_id, 'code': code, 'name': name, 'cat': row.get('Category'), 'desc': row.get('Description'),
                'active': '1' if row.get('IsActive', 'Active').lower() == 'active' else '0', 'cdate': today_key, 'mdate': today_key
            })
            created += 1
        conn.commit()
    return jsonify({'message': f'Imported {created} document types', 'errors': errors})

# ----------------------------------------------------------------------
# Menu & Permissions Helper
# ----------------------------------------------------------------------
@api_bp.route('/menu')
@login_required
def get_user_menu():
    from app import db
    with db.engine.connect() as conn:
        row = conn.execute(
            text("SELECT UserRoles FROM t_Users WHERE UserID = :uid"),
            {'uid': current_user.get_id()}
        ).fetchone()
        user_roles = json.loads(row[0]) if row and row[0] else []

    user_perms = get_user_permission_set(user_roles)
    allowed_keys = [key for key, item in MENU_ITEMS.items() if has_permission(item.get('permission_code'), user_perms)]
    return jsonify(allowed_keys)