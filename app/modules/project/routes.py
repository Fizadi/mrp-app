# app/modules/project/routes.py
from flask import render_template, session, current_app, jsonify, redirect
from flask_login import login_required
from sqlalchemy.sql import text
from . import project_bp


# ============================================================================
# PROJECT MODULE - PAGE ROUTES
# ============================================================================

@project_bp.route('/project_portfolio')
@login_required
def portfolio_page():
    """Project portfolio overview page."""
    return render_template('project_portfolio.html')


@project_bp.route('/dashboard/<string:project_id>')
@login_required
def dashboard(project_id):
    """Project dashboard page."""
    engine = current_app.extensions['sqlalchemy'].engine
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT ProjectName, Status, PercentComplete, ProjectManager FROM t_Project WHERE ProjectID = :pid"),
            {'pid': project_id}
        ).fetchone()
        if not result:
            return "Project not found", 404
        project = {
            'id': project_id,
            'name': result[0],
            'status': result[1],
            'percent_complete': result[2] or 0,
            'manager': result[3] or 'Not assigned'
        }
    return render_template('project_dashboard.html', project_id=project_id, project=project)


@project_bp.route('/milestones/<string:project_id>')
@login_required
def milestones_page(project_id):
    """Project milestones page."""
    return render_template('project_milestones.html', project_id=project_id)


@project_bp.route('/resources/<string:project_id>')
@login_required
def resources_page(project_id):
    """Project resources page."""
    return render_template('project_resources.html', project_id=project_id)


@project_bp.route('/bom/<string:project_id>')
@login_required
def project_bom_page(project_id):
    """Project BOM page."""
    return render_template('project_bom.html', project_id=project_id)


@project_bp.route('/deliverables/<string:project_id>')
@login_required
def deliverables_page(project_id):
    """Project deliverables page."""
    return render_template('project_deliverables.html', project_id=project_id)


@project_bp.route('/customer_projects')
@login_required
def customer_projects_list():
    """Customer projects list page."""
    return render_template('customer_projects_list.html')


@project_bp.route('/customer_projects/<string:cp_id>')
@login_required
def customer_project_detail(cp_id):
    """Customer project detail page."""
    return render_template('customer_project_detail.html', cp_id=cp_id)


@project_bp.route('/product-tree')
@login_required
def product_tree():
    """Product tree page - shows product hierarchy for current project."""
    project_id = session.get('current_project_id', 'All')
    return render_template('product_tree.html', project_id=project_id)


@project_bp.route('/certificates/<string:project_id>')
@login_required
def project_certificates(project_id):
    """Project certificates list page."""
    return render_template('project_certificates_list.html', project_id=project_id)


@project_bp.route('/certificate/<int:cert_id>')
@login_required
def certificate_detail(cert_id):
    """Certificate detail page."""
    return render_template('project_certificate_detail.html', cert_id=cert_id)


@project_bp.route('/boq/<string:project_id>')
@login_required
def project_boq(project_id):
    """Project Bill of Quantities page."""
    return render_template('project_boq.html', project_id=project_id)



# NOTE: /dashboard_standalone route removed in v41.
# The template dashboard_standalone.html does not exist. Re-add this route
# along with the template if a sidebar-less dashboard is needed in the future.



@project_bp.route('/ms-project-integration')
@login_required
def ms_project_integration_index():
    """
    Index route for MS Project Integration.

    If a project is selected in the session (via the sidebar project
    selector), redirect to its detail page. Otherwise, fall back to the
    project portfolio so the user can pick a project first.
    """
    project_id = session.get('current_project_id', 'All')

    if not project_id or project_id == 'All':
        return redirect('/project/project_portfolio')

    return redirect(f'/project/ms-project-integration/{project_id}')


@project_bp.route('/ms-project-integration/<string:project_id>')
@login_required
def ms_project_integration(project_id):
    """MS Project integration page."""
    return render_template('project_ms_integration.html', project_id=project_id)
 
 

@project_bp.route('/_debug_routes')
@login_required
def _debug_routes():
    from flask import current_app
    out = []
    for rule in current_app.url_map.iter_rules():
        if 'ms-project' in str(rule.rule):
            out.append({
                'rule': str(rule.rule),
                'endpoint': rule.endpoint,
                'methods': sorted(m for m in rule.methods if m not in ('HEAD', 'OPTIONS'))
            })
    return jsonify({'routes': out})