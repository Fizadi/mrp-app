from flask import render_template, session
from . import workflow_bp
from app.core.auth import login_required
from datetime import datetime

@workflow_bp.route('/tasks', endpoint='workflow_tasks')
@login_required
def tasks():
    lang = session.get('lang', 'en')
    return render_template('workflow/workflow_tasks.html', lang=lang)

@workflow_bp.route('/my-tasks', endpoint='my_tasks')
@login_required
def my_tasks():
    lang = session.get('lang', 'en')
    return render_template('workflow/my_tasks.html', lang=lang)

@workflow_bp.route('/delivery', endpoint='delivery')
@login_required
def delivery():
    lang = session.get('lang', 'en')
    return render_template('workflow/delivery.html', lang=lang)

@workflow_bp.route('/service-requests', endpoint='service_requests')
@login_required
def service_requests():
    lang = session.get('lang', 'en')
    return render_template('workflow/service_requests.html', lang=lang)

# ============================================================
# WORKFLOW DESIGNER - MOVED FROM ADMIN
# ============================================================

@workflow_bp.route('/designer', endpoint='designer')
@login_required
def designer():
    """Workflow Designer page for visual workflow creation and editing."""
    lang = session.get('lang', 'en')
    return render_template('workflow/workflow_designer.html', lang=lang)