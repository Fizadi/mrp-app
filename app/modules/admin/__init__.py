# app/modules/admin/__init__.py
from flask import Blueprint, render_template, flash, redirect, url_for
from flask_login import login_required, current_user
from functools import wraps
from sqlalchemy.sql import text
import json

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login'))
        from app import db
        with db.engine.connect() as conn:
            row = conn.execute(text("SELECT UserRoles FROM t_Users WHERE UserID = :uid"), {'uid': current_user.get_id()}).fetchone()
            roles = json.loads(row[0]) if row and row[0] else []
            if 'ADMIN' not in roles:
                flash('Admin access required', 'danger')
                return redirect(url_for('dashboard.dashboard'))
        return f(*args, **kwargs)
    return decorated

# ============================================================================
# ADMIN PAGE ROUTES
# ============================================================================
# Note: Workflow Designer (workflow-designer) has been moved to the Workflow
# module at /workflow/designer. This route is kept for backward compatibility
# but will redirect to the new location. Remove this route once the frontend
# has been updated to use /workflow/designer.
# ============================================================================

@admin_bp.route('/users')
@login_required
@admin_required
def users():
    """Users & Roles management page."""
    return render_template('admin/users.html')

@admin_bp.route('/internal-users')
@login_required
@admin_required
def internal_users():
    return render_template('admin/internal_users.html')

@admin_bp.route('/external-users')
@login_required
@admin_required
def external_users():
    return render_template('admin/external_users.html')

@admin_bp.route('/role-permissions')
@login_required
@admin_required
def role_permissions():
    return render_template('admin/role_permissions.html')

# ============================================================================
# WORKFLOW DESIGNER - MOVED TO WORKFLOW MODULE
# ============================================================================
# The Workflow Designer page has been moved from Administration to the Workflow
# module. This redirect ensures backward compatibility for existing bookmarks.
# The new URL is: /workflow/designer
# ============================================================================

@admin_bp.route('/workflow-designer')
@login_required
@admin_required
def workflow_designer():
    """Redirect to the new Workflow Designer location in the Workflow module."""
    from flask import redirect, url_for
    flash('Workflow Designer has been moved to the Workflow module.', 'info')
    return redirect(url_for('workflow.workflow_designer'))

@admin_bp.route('/system-config')
@login_required
@admin_required
def system_config():
    return render_template('admin/system_config.html')

@admin_bp.route('/audit-log')
@login_required
@admin_required
def audit_log():
    return render_template('admin/audit_log.html')

@admin_bp.route('/portal-settings')
@login_required
@admin_required
def portal_settings():
    return render_template('admin/portal_settings.html')

@admin_bp.route('/project-roles')
@login_required
@admin_required
def project_roles():
    return render_template('admin/project_roles.html')

@admin_bp.route('/document-types/<int:doc_id>')
@login_required
@admin_required
def document_type_detail(doc_id):
    return render_template('admin/document_type_detail.html', doc_id=doc_id)

@admin_bp.route('/document-types')
@login_required
@admin_required
def document_types():
    return render_template('admin/document_types.html')