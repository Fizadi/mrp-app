from flask import render_template
from flask_login import login_required
from . import crm_bp

@crm_bp.route('/opportunities')
@login_required
def opportunities():
    return render_template('crm/opportunities.html')

@crm_bp.route('/opportunity/<int:opportunity_id>')
@login_required
def opportunity_detail(opportunity_id):
    return render_template('crm/opportunity_detail.html', opportunity_id=opportunity_id)

# =====================================================
# NEW ROUTES FOR CRM MODULE
# =====================================================

@crm_bp.route('/quotes')
@login_required
def quotes():
    """Quotes listing page"""
    return render_template('crm/quotes.html')

@crm_bp.route('/quote/<string:quote_id>')
@login_required
def quote_detail(quote_id):
    """Quote detail page"""
    return render_template('crm/quote_detail.html', quote_id=quote_id)

@crm_bp.route('/customer-pricing')
@login_required
def customer_pricing():
    """Customer price overrides management page"""
    return render_template('crm/customer_pricing.html')

@crm_bp.route('/sales-dashboard')
@login_required
def sales_dashboard():
    """Sales executive dashboard"""
    return render_template('crm/sales_dashboard.html')

@crm_bp.route('/customers')
@login_required
def customers():
    """Customer master list page"""
    return render_template('crm/customers.html')

@crm_bp.route('/activities')
@login_required
def activities():
    """Activity log across all opportunities"""
    return render_template('crm/activities.html')

@crm_bp.route('/forecast')
@login_required
def forecast():
    """Sales forecast page"""
    return render_template('crm/forecast.html')