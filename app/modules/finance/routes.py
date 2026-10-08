# app/modules/finance/routes.py
from flask import render_template, session
from . import finance_bp
from app.core.auth import login_required, permission_required
import logging

# ============================================================================
# SALES FORECAST PAGES
# ============================================================================

@finance_bp.route('/sales-forecast')
@login_required
@permission_required('FINANCE_FORECAST_VIEW')
def sales_forecast_list():
    """Sales Forecast List page."""
    print(">>> SALES FORECAST LIST ROUTE REACHED! <<<")
    logging.info("=== sales_forecast_list route called ===")
    lang = session.get('lang', 'en')
    return render_template('finance/sales_forecast_list.html', lang=lang)

@finance_bp.route('/sales-forecast/create')
@login_required
@permission_required('FINANCE_FORECAST_CREATE')
def sales_forecast_create():
    """Sales Forecast Create page."""
    lang = session.get('lang', 'en')
    return render_template('finance/sales_forecast_edit.html', lang=lang, forecast_id=None)

@finance_bp.route('/sales-forecast/<int:forecast_id>/edit')
@login_required
@permission_required('FINANCE_FORECAST_EDIT')
def sales_forecast_edit(forecast_id):
    """Sales Forecast Edit page."""
    lang = session.get('lang', 'en')
    return render_template('finance/sales_forecast_edit.html', lang=lang, forecast_id=forecast_id)

@finance_bp.route('/sales-forecast/<int:forecast_id>/view')
@login_required
@permission_required('FINANCE_FORECAST_VIEW')
def sales_forecast_view(forecast_id):
    """Sales Forecast View page."""
    lang = session.get('lang', 'en')
    return render_template('finance/sales_forecast_edit.html', lang=lang, forecast_id=forecast_id, view_mode=True)


# ============================================================================
# PURCHASE FORECAST PAGES
# ============================================================================

@finance_bp.route('/purchase-forecast')
@login_required
@permission_required('FINANCE_FORECAST_VIEW')
def purchase_forecast_list():
    """Purchase Forecast List page."""
    lang = session.get('lang', 'en')
    return render_template('finance/purchase_forecast_list.html', lang=lang)

@finance_bp.route('/purchase-forecast/create')
@login_required
@permission_required('FINANCE_FORECAST_CREATE')
def purchase_forecast_create():
    """Purchase Forecast Create page."""
    lang = session.get('lang', 'en')
    return render_template('finance/purchase_forecast_edit.html', lang=lang, forecast_id=None)

@finance_bp.route('/purchase-forecast/<int:forecast_id>/edit')
@login_required
@permission_required('FINANCE_FORECAST_EDIT')
def purchase_forecast_edit(forecast_id):
    """Purchase Forecast Edit page."""
    lang = session.get('lang', 'en')
    return render_template('finance/purchase_forecast_edit.html', lang=lang, forecast_id=forecast_id)

@finance_bp.route('/purchase-forecast/<int:forecast_id>/view')
@login_required
@permission_required('FINANCE_FORECAST_VIEW')
def purchase_forecast_view(forecast_id):
    """Purchase Forecast View page."""
    lang = session.get('lang', 'en')
    return render_template('finance/purchase_forecast_edit.html', lang=lang, forecast_id=forecast_id, view_mode=True)


# ============================================================================
# CASH FLOW PAGES
# ============================================================================

@finance_bp.route('/cash-flow')
@login_required
@permission_required('FINANCE_CASH_FLOW_VIEW')
def cash_flow_list():
    """Cash Flow List page."""
    lang = session.get('lang', 'en')
    return render_template('finance/cash_flow_list.html', lang=lang)

@finance_bp.route('/cash-flow/generate')
@login_required
@permission_required('FINANCE_CASH_FLOW_CREATE')
def cash_flow_generate():
    """Cash Flow Generate page."""
    lang = session.get('lang', 'en')
    return render_template('finance/cash_flow_generate.html', lang=lang)

@finance_bp.route('/cash-flow/<int:projection_id>/view')
@login_required
@permission_required('FINANCE_CASH_FLOW_VIEW')
def cash_flow_view(projection_id):
    """Cash Flow View page."""
    lang = session.get('lang', 'en')
    return render_template('finance/cash_flow_view.html', lang=lang, projection_id=projection_id)


# ============================================================================
# OTHER FINANCE PAGES
# ============================================================================

@finance_bp.route('/job-costing')
@login_required
def job_costing():
    """Job Costing page."""
    lang = session.get('lang', 'en')
    return render_template('finance/job_costing.html', lang=lang)

@finance_bp.route('/progress-certificates')
@login_required
def progress_certificates():
    """Progress Certificates page."""
    lang = session.get('lang', 'en')
    return render_template('finance/progress_certificates.html', lang=lang)

@finance_bp.route('/supplier-invoices')
@login_required
def supplier_invoices():
    """Supplier Invoices page."""
    lang = session.get('lang', 'en')
    return render_template('finance/supplier_invoices.html', lang=lang)

@finance_bp.route('/currencies')
@login_required
def currencies():
    """Currency Management page."""
    lang = session.get('lang', 'en')
    return render_template('finance/currencies.html', lang=lang)