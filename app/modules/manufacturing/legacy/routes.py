from flask import render_template
from flask_login import login_required
from . import manufacturing_bp
from flask import request


@manufacturing_bp.route('/production-orders')
@login_required
def production_orders():
    """Main production orders page."""
    return render_template('manufacturing/production_orders.html')
    
 
@manufacturing_bp.route('/shop-floor')
@login_required
def shop_floor():
    return render_template('manufacturing/shop_floor.html')
    
@manufacturing_bp.route('/work-center-routing')
@login_required
def work_center_routing():
    work_center_id = request.args.get('work_center_id')
    return render_template('manufacturing/work_center_routing.html', work_center_id=work_center_id)  
    
 
@manufacturing_bp.route('/capacity-planning')
@login_required
def capacity_planning():
    return render_template('manufacturing/capacity_planning.html')
    
 
@manufacturing_bp.route('/mrp-run-dashboard')
@login_required
def mrp_run_dashboard():
    """MRP Run Dashboard page."""
    return render_template('manufacturing/mrp_run_dashboard.html')
    

@manufacturing_bp.route('/mrp/planned-orders')
@login_required
def mrp_planned_orders():
    return render_template('manufacturing/mrp_planned_orders.html')

@manufacturing_bp.route('/mrp/exceptions')
@login_required
def mrp_exceptions():
    return render_template('manufacturing/mrp_exceptions.html')


  
from datetime import date

@manufacturing_bp.route('/what-if')
def mrp_what_if():
    return render_template('manufacturing/mrp_what_if.html', today=date.today().isoformat())
    
  
@manufacturing_bp.route('/subcontract-dashboard')
@login_required
def subcontract_dashboard():
    """Subcontract Dashboard page."""
    return render_template('manufacturing/subcontract_dashboard.html')
  
  
@manufacturing_bp.route('/maintenance')
@login_required
def maintenance():
    return render_template('manufacturing/maintenance.html')
    
  
@manufacturing_bp.route('/shop-floor-barcode')
@login_required
def shop_floor_barcode():
    return render_template('manufacturing/shop_floor_barcode.html')
    
@manufacturing_bp.route('/work-centers')
@login_required
def work_centers():
    return render_template('manufacturing/work_centers.html')
    
    

@manufacturing_bp.route('/process-flow')
@login_required
def process_flow():
    """Manufacturing process flow visualization page."""
    return render_template('manufacturing/process_flow.html')
    
  
  
@manufacturing_bp.route('/shop-floor-layout')
@login_required
def shop_floor_layout():
    return render_template('manufacturing/shop_floor_layout.html')