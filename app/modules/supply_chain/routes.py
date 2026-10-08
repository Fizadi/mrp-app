from flask import render_template
from flask_login import login_required
from . import supply_chain_bp

@supply_chain_bp.route('/purchase-orders')
@login_required
def purchase_orders():
    return render_template('supply_chain/purchase_orders.html')

@supply_chain_bp.route('/rfq')
@login_required
def rfq():
    return render_template('supply_chain/rfq.html')  # ← Change from placeholder.html

@supply_chain_bp.route('/subcontract-orders')
@login_required
def subcontract_orders():
    return render_template('supply_chain/subcontract_orders.html')

@supply_chain_bp.route('/suppliers')
@login_required
def suppliers():
    return render_template('supply_chain/suppliers.html')

@supply_chain_bp.route('/stock-overview')
@login_required
def stock_overview():
    return render_template('supply_chain/stock_overview.html')

@supply_chain_bp.route('/warehouses')
@login_required
def warehouses():
    return render_template('supply_chain/warehouses.html')

@supply_chain_bp.route('/stock-movements')
@login_required
def stock_movements():
    return render_template('supply_chain/stock_movements.html')

@supply_chain_bp.route('/inventory-valuation')
@login_required
def inventory_valuation():
    return render_template('supply_chain/inventory_valuation.html')

@supply_chain_bp.route('/receiving')
@login_required
def receiving():
    return render_template('supply_chain/receiving.html')

@supply_chain_bp.route('/shipping')
@login_required
def shipping():
    return render_template('supply_chain/shipping.html')

# ===== NEW ROUTES =====

@supply_chain_bp.route('/inventory-status')
@login_required
def inventory_status():
    """Page 29: Inventory Status - Real-time inventory visibility"""
    return render_template('supply_chain/inventory_status.html')

@supply_chain_bp.route('/receiving-shipping')
@login_required
def receiving_shipping():
    """Page 30: Receiving & Shipping - Track inbound and outbound"""
    return render_template('supply_chain/receiving_shipping.html')

# Note: Delivery Management (Page 31) belongs to Workflow module per context
# It should be at /workflow/delivery, not /supply-chain/delivery