from flask import render_template
from . import dashboard_bp
from app.core.auth import login_required


@dashboard_bp.route('/')
@dashboard_bp.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard/dashboard.html')