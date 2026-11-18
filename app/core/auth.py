# app/core/auth.py
import hashlib
import secrets
import logging
import json
from datetime import datetime
from functools import wraps
from flask import session, flash, redirect, url_for, request
from app.core.database import get_db_connection

def hash_password(password, salt=None):
    """PBKDF2‑SHA256 password hashing."""
    salt = salt or secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'),
                              salt.encode('utf-8'), 100000)
    return f"{salt}:{key.hex()}"

def verify_password(stored_password, provided_password):
    """Verify a password against its stored hash.

    Delegates to the canonical verifier so Werkzeug hashes (scrypt/pbkdf2)
    are accepted too. Kept for backward compatibility — no live callers.
    """
    from app.modules.auth import verify_password as _canon
    try:
        return _canon(stored_password, provided_password)
    except Exception:
        return False


def has_permission(user_id, permission_code):
    """Check if a user has a specific permission (tenant-scoped)."""
    print(f">>> has_permission called: user_id={user_id}, permission={permission_code}")

    # UserID is nvarchar. Existing users are numeric ("1","18"); new tenants
    # provision GUIDs. Tolerate both.
    if user_id is None:
        return False
    # no int() coercion — the SQL below binds the value as-is

    conn = get_db_connection()
    if not conn:
        print(f">>> DB connection FAILED in has_permission")
        return False
    
    print(f">>> DB connection OK")

    try:
        user_row = conn.execute(
            "SELECT UserRoles FROM t_Users WHERE UserID = ?", 
            (str(user_id),)
        ).fetchone()
        
        print(f">>> user_row: {user_row}")
        
        role_codes = []
        if user_row and user_row[0]:
            try:
                parsed = json.loads(user_row[0])
                if isinstance(parsed, list):
                    role_codes = [str(r) for r in parsed if r]
            except (json.JSONDecodeError, TypeError, AttributeError):
                role_codes = []
        
        print(f">>> role_codes: {role_codes}")
        
        if 'ADMIN' in role_codes:
            print(f">>> ADMIN found - granting permission")
            return True
        
        if role_codes:
            # Tenant-scoped permission check
            tenant_id = session.get('tenant_id', 1)
            placeholders = ','.join(['?'] * len(role_codes))
            query = f"""
                SELECT 1
                FROM t_RolePermissions rp
                JOIN t_Permissions p ON rp.PermissionID = p.PermissionID
                JOIN t_Roles r ON rp.RoleID = r.RoleID
                WHERE r.RoleCode IN ({placeholders}) 
                  AND p.PermissionCode = ?
                  AND rp.TenantID = ?
            """
            params = role_codes + [permission_code, tenant_id]
            result = conn.execute(query, params).fetchone()
            print(f">>> permission check result: {result}")
            if result:
                return True

        print(f">>> permission DENIED")
        return False
    except Exception as e:
        print(f">>> ERROR in has_permission: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        conn.close()


def log_audit(user_id, action_type, module, table_name=None,
              record_id=None, old_values=None, new_values=None,
              description=None):
    """Insert an audit trail entry."""
    conn = get_db_connection()
    try:
        conn.execute("""
            INSERT INTO t_AuditLog
            (UserID, ActionType, Module, TableName, RecordID,
             OldValues, NewValues, IPAddress, Description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, action_type, module, table_name, record_id,
            old_values, new_values, request.remote_addr, description
        ))
        conn.commit()
    except Exception as e:
        logging.error(f"Audit log error: {e}")
    finally:
        conn.close()


# Decorators
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def permission_required(permission_code):
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            if not has_permission(session.get('user_id'), permission_code):
                flash('You do not have permission to access this page.', 'error')
                return redirect(url_for('dashboard.dashboard'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator