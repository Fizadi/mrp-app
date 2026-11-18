"""
Permission checking utilities - SQL Server compatible (tenant-scoped).
"""

from functools import wraps
from flask import abort, g, session
from flask_login import current_user
import json
from app.core.database import get_db_connection


# ============================================================================
# TENANT MODULE CACHE (per-request, avoids 50+ SELECTs per page load)
# ============================================================================

def _get_tenant_modules():
    """
    Return the set of enabled module codes for the current tenant.
    Cached in Flask's `g` object for the duration of the request.
    Tenant 0 (Super Admin) gets a sentinel `{'*'}` meaning "all modules".
    """
    if hasattr(g, '_tenant_modules_cache'):
        return g._tenant_modules_cache

    tenant_id = session.get('tenant_id', 1)

    # Super Admin — everything is always enabled
    if tenant_id == 0:
        g._tenant_modules_cache = {'*'}
        return g._tenant_modules_cache

    conn = get_db_connection(use_teardown=True)
    if not conn:
        g._tenant_modules_cache = set()
        return g._tenant_modules_cache

    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT ModuleCode FROM t_TenantModules "
            "WHERE TenantID = ? AND IsEnabled = 1",
            (tenant_id,)
        )
        rows = cursor.fetchall()
        # Normalize to lowercase; also accept 'supplychain' as 'supply_chain'
        modules = set()
        for row in rows:
            code = (row[0] or '').strip().lower()
            if code == 'supplychain':
                code = 'supply_chain'
            if code:
                modules.add(code)
        g._tenant_modules_cache = modules
        return modules
    except Exception as e:
        print(f"Error loading tenant modules: {e}")
        g._tenant_modules_cache = set()
        return g._tenant_modules_cache
    # NOTE: do NOT call conn.close() — teardown_appcontext handles it.


# ============================================================================
# CORE PERMISSION CHECK
# ============================================================================

def has_permission(permission_code):
    """Check if current user has a specific permission (tenant-scoped)."""
    if not current_user or not current_user.is_authenticated:
        return False

    # 1) Admin has all permissions
    if hasattr(current_user, 'UserRoles') and current_user.UserRoles:
        try:
            if isinstance(current_user.UserRoles, str):
                roles = json.loads(current_user.UserRoles)
            else:
                roles = current_user.UserRoles
            if isinstance(roles, list) and 'ADMIN' in roles:
                return True
        except (json.JSONDecodeError, TypeError):
            pass

    # 2) User-level permissions JSON in t_Users.Permissions
    if hasattr(current_user, 'Permissions') and current_user.Permissions:
        try:
            if isinstance(current_user.Permissions, str):
                perms = json.loads(current_user.Permissions)
            else:
                perms = current_user.Permissions
            if isinstance(perms, dict) and perms.get(permission_code, False):
                return True
        except (json.JSONDecodeError, TypeError):
            pass

    # 3) Role-level permissions via database (tenant-scoped)
    user_id = current_user.get_id()
    if not user_id:
        return False

    tenant_id = session.get('tenant_id', 1)
    conn = get_db_connection(use_teardown=True)
    if not conn:
        return False

    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT UserRoles FROM t_Users WHERE UserID = ?",
            (user_id,)
        )
        row = cursor.fetchone()
        if not row or not row[0]:
            return False

        try:
            roles = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        except (json.JSONDecodeError, TypeError):
            return False

        if not isinstance(roles, list) or not roles:
            return False

        # Build positional params: roles... + permission_code + tenant_id (x2)
        # The `OR ? = 0` clause makes Super Admin (tenant 0) bypass the
        # t_RolePermissions TenantID filter entirely — tenant 0 never has
        # rows in that table, so a strict `rp.TenantID = ?` would 403 every
        # Super Admin action that hits a permission-checked route.
        placeholders = ','.join(['?'] * len(roles))
        query = f"""
            SELECT TOP 1 1
            FROM t_RolePermissions rp
            JOIN t_Permissions p ON rp.PermissionID = p.PermissionID
            JOIN t_Roles r ON rp.RoleID = r.RoleID
            WHERE r.RoleCode IN ({placeholders})
              AND p.PermissionCode = ?
              AND (rp.TenantID = ? OR ? = 0)
        """
        params = list(roles) + [permission_code, tenant_id, tenant_id]
        cursor.execute(query, params)
        return cursor.fetchone() is not None
    except Exception as e:
        print(f"Error checking permission: {e}")
        return False
    # NOTE: do NOT call conn.close() — the connection is managed by
    # Flask's teardown_appcontext (registered in app/core/database.py).
    # Closing it here breaks _get_tenant_modules() which reuses the same g.db.


# ============================================================================
# DECORATORS
# ============================================================================

def require_module(module_code):
    """
    Ensure the current tenant has the given module enabled.
    Tenant 0 (Super Admin) always passes.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            modules = _get_tenant_modules()
            # Super Admin wildcard
            if '*' in modules:
                return f(*args, **kwargs)
            # Normalize the requested module code
            normalized = (module_code or '').strip().lower()
            if normalized == 'supplychain':
                normalized = 'supply_chain'
            if normalized not in modules:
                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def require_permission(permission_code):
    """Require a specific permission (implies module check should be done separately)."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not has_permission(permission_code):
                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator