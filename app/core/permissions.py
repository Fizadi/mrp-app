"""
Permission checking utilities - SQL Server compatible.
"""

from functools import wraps
from flask import abort
from flask_login import current_user
import json
from app.core.database import get_db_connection


def has_permission(permission_code):
    """Check if current user has a specific permission - SQL Server compatible."""
    if not current_user or not current_user.is_authenticated:
        return False
    
    # Admin has all permissions
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
    
    # Check user permissions (stored as JSON in t_Users.Permissions)
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
    
    # Check via database role permissions
    user_id = current_user.get_id()
    if user_id:
        conn = get_db_connection(use_teardown=True)
        if conn:
            try:
                cursor = conn.cursor()
                # SQL Server uses @param
                cursor.execute(
                    "SELECT UserRoles FROM t_Users WHERE UserID = @user_id",
                    {'user_id': user_id}
                )
                row = cursor.fetchone()
                if row and row[0]:
                    try:
                        roles = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                        if isinstance(roles, list):
                            # Build dynamic parameters
                            param_placeholders = []
                            params = {}
                            for i, role in enumerate(roles):
                                param_name = f'@role_{i}'
                                param_placeholders.append(param_name)
                                params[param_name] = role
                            params['permission_code'] = permission_code
                            placeholders_str = ','.join(param_placeholders)
                            
                            query = f"""
                                SELECT TOP 1 1
                                FROM t_RolePermissions rp
                                JOIN t_Permissions p ON rp.PermissionID = p.PermissionID
                                JOIN t_Roles r ON rp.RoleID = r.RoleID
                                WHERE r.RoleCode IN ({placeholders_str}) AND p.PermissionCode = @permission_code
                            """
                            cursor.execute(query, params)
                            if cursor.fetchone():
                                return True
                    except (json.JSONDecodeError, TypeError):
                        pass
            except Exception as e:
                print(f"Error checking permission: {e}")
            finally:
                conn.close()
    
    return False


def require_permission(permission_code):
    """Decorator to require a permission for a route."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not has_permission(permission_code):
                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator