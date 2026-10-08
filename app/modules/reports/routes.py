from flask import render_template
from flask_login import login_required
from . import reports_bp

@reports_bp.route('/operational')
@login_required
def operational_reports():
    return render_template('reports/operational_reports.html')