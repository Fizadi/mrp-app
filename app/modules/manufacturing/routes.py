# app/modules/manufacturing/routes.py
from flask import render_template, session, request, redirect, url_for
from flask_login import login_required
from app.core.context_processors import INDUSTRY_DATA
from . import manufacturing_bp

# ============================================================================
# MANUFACTURING MODULE - PAGE ROUTES (CONSOLIDATED)
# ============================================================================

@manufacturing_bp.route('/job-orders')
@login_required
def job_orders():
    """Job Orders page."""
    demo_industry = session.get('demo_industry', 'valve')
    industry_data = INDUSTRY_DATA.get(demo_industry, INDUSTRY_DATA['valve'])
    
    industries = []
    for code, info in INDUSTRY_DATA.items():
        industries.append({
            'code': code,
            'name': info['name'],
            'icon': info['icon'],
            'scenario': info['scenario']
        })
    
    return render_template('manufacturing/job_orders.html', 
                         demo_industry=demo_industry,
                         industry_display=industry_data['name'],
                         industry_icon=industry_data['icon'],
                         industry_scenario=industry_data.get('scenario', ''),
                         industry_description=industry_data.get('description', ''),
                         sample_company=industry_data.get('sample_company', ''),
                         industries=industries)


@manufacturing_bp.route('/job-order/<string:order_id>')
@manufacturing_bp.route('/job-orders/<string:order_id>')
@login_required
def job_order_detail(order_id):
    """Job Order Detail page."""
    return render_template('manufacturing/job_order_detail.html', 
                         order_id=order_id,
                         demo_industry=session.get('demo_industry', 'valve'))


@manufacturing_bp.route('/workstations')
@login_required
def workstations():
    """Workstations page."""
    return render_template('manufacturing/workstations.html')


@manufacturing_bp.route('/machines')
@login_required
def machines():
    """Machine Management page."""
    return render_template('manufacturing/machines.html')


@manufacturing_bp.route('/shop-floor')
@login_required
def shop_floor():
    """Shop Floor Control page."""
    return render_template('manufacturing/shop_floor.html')


@manufacturing_bp.route('/load-board')
@login_required
def load_board():
    """Load Board page."""
    return render_template('manufacturing/load_board.html')


@manufacturing_bp.route('/material-planning')
@login_required
def material_planning():
    """Material Planning page."""
    return render_template('manufacturing/material_planning.html')


@manufacturing_bp.route('/subcontractors')
@login_required
def subcontractors():
    """Subcontractor Management page."""
    return render_template('manufacturing/subcontractors.html')


@manufacturing_bp.route('/maintenance')
@login_required
def maintenance():
    """Maintenance Management page."""
    return render_template('manufacturing/maintenance.html')


# ============================================================================
# LEGACY ROUTES (Redirects)
# ============================================================================

@manufacturing_bp.route('/production-orders')
@login_required
def production_orders():
    return redirect(url_for('manufacturing.job_orders'))


@manufacturing_bp.route('/work-center-routing')
@login_required
def work_center_routing():
    work_center_id = request.args.get('work_center_id')
    return render_template('manufacturing/work_center_routing.html', work_center_id=work_center_id)


@manufacturing_bp.route('/capacity-planning')
@login_required
def capacity_planning():
    return redirect(url_for('manufacturing.load_board'))


@manufacturing_bp.route('/mrp-run-dashboard')
@manufacturing_bp.route('/mrp/planned-orders')
@manufacturing_bp.route('/mrp/exceptions')
@manufacturing_bp.route('/what-if')
@login_required
def mrp_redirect():
    return redirect(url_for('manufacturing.material_planning'))


@manufacturing_bp.route('/subcontract-dashboard')
@login_required
def subcontract_dashboard():
    return redirect(url_for('manufacturing.subcontractors'))


@manufacturing_bp.route('/shop-floor-barcode')
@login_required
def shop_floor_barcode():
    return render_template('manufacturing/shop_floor_barcode.html')


@manufacturing_bp.route('/work-centers')
@login_required
def work_centers():
    return redirect(url_for('manufacturing.workstations'))


@manufacturing_bp.route('/process-flow')
@login_required
def process_flow():
    return render_template('manufacturing/process_flow.html')


@manufacturing_bp.route('/shop-floor-layout')
@login_required
def shop_floor_layout():
    return render_template('manufacturing/shop_floor_layout.html')