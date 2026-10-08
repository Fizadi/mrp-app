from flask import Blueprint, render_template, session, request
from app import db
from sqlalchemy.sql import text
from app.core.auth import login_required, permission_required
from app.modules.engineering.api import get_products_for_selector, get_bom_stats


print("=== engineering routes.py LOADING ===")

engineering_bp = Blueprint('engineering', __name__, url_prefix='/engineering')
print("Blueprint object created")


# ==================== EXISTING PAGES ====================

@engineering_bp.route('/products')
@login_required
@permission_required('ENGINEERING_PRODUCT_VIEW')
def products():
    return render_template('engineering/products.html')


@engineering_bp.route('/bom')
@login_required
@permission_required('ENGINEERING_BOM_VIEW')
def bom():
    # get_products_for_selector expects a request object
    products = get_products_for_selector(request)
    stats = get_bom_stats()
    return render_template('engineering/bom.html', products=products, stats=stats)


@engineering_bp.route('/documents')
@login_required
@permission_required('ENGINEERING_DOCUMENT_VIEW')
def documents():
    return render_template('engineering/documents.html')


@engineering_bp.route('/handover')
@login_required
@permission_required('ENGINEERING_HANDOVER_VIEW')
def handover():
    industry = session.get('demo_industry', 'valve')
    projects = []
    try:
        with db.engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT ProjectID, ProjectName 
                FROM t_Project 
                WHERE DemoIndustryCode = :industry
                ORDER BY ProjectName
            """), {'industry': industry}).fetchall()
            projects = [{'ProjectID': r[0], 'ProjectName': r[1]} for r in rows]
    except Exception as e:
        print(f"Error loading handover projects: {e}")
    return render_template('engineering/handover.html', projects=projects)


@engineering_bp.route('/document-checkout')
@login_required
@permission_required('ENGINEERING_DOCUMENT_VIEW')
def document_checkout():
    industry = session.get('demo_industry', 'valve')
    projects = []
    try:
        with db.engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT ProjectID, ProjectName 
                FROM t_Project 
                WHERE DemoIndustryCode = :industry
                ORDER BY ProjectName
            """), {'industry': industry}).fetchall()
            projects = [{'ProjectID': r[0], 'ProjectName': r[1]} for r in rows]
    except Exception as e:
        print(f"Error loading document_checkout projects: {e}")
    return render_template('engineering/document_checkout.html', projects=projects)


# ==================== NEW PAGE 1: PRODUCT CATEGORIES ====================

@engineering_bp.route('/product-categories')
@login_required
@permission_required('ENGINEERING_CATEGORY_VIEW')
def product_categories():
    return render_template('engineering/product_categories.html')


# ==================== NEW PAGE 2: ENGINEERING PARAMETERS ====================

@engineering_bp.route('/parameters')
@login_required
@permission_required('ENGINEERING_PARAMETER_VIEW')
def engineering_parameters():
    industry = session.get('demo_industry', 'valve')
    projects = []
    try:
        with db.engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT CompanyProjectID, ProjectName 
                FROM t_CompanyProject 
                WHERE DemoIndustryCode = :industry
                ORDER BY ProjectName
            """), {'industry': industry}).fetchall()
            projects = [{'CompanyProjectID': r[0], 'ProjectName': r[1]} for r in rows]
    except Exception as e:
        print(f"Error loading engineering_parameters projects: {e}")
    return render_template('engineering/parameters.html', projects=projects)


# ==================== NEW PAGE 3: ROUTINGS & WORK INSTRUCTIONS ====================

@engineering_bp.route('/routings')
@login_required
@permission_required('ENGINEERING_ROUTING_VIEW')
def routings():
    industry = session.get('demo_industry', 'valve')

    products = []
    work_centers = []

    try:
        with db.engine.connect() as conn:
            # Products for Gantt / Flow / Join Map dropdowns
            prod_rows = conn.execute(text("""
                SELECT ProductId, descEnglish 
                FROM t_Product 
                WHERE DemoIndustryCode = :industry
                ORDER BY descEnglish
            """), {'industry': industry}).fetchall()
            products = [{'ProductId': r[0], 'descEnglish': r[1]} for r in prod_rows]

            # Work centers for routing modal dropdown
            wc_rows = conn.execute(text("""
                SELECT WorkCenterID, Name 
                FROM t_WorkCenter 
                WHERE IsActive = 1
                ORDER BY Name
            """)).fetchall()
            work_centers = [{'WorkCenterID': r[0], 'Name': r[1]} for r in wc_rows]

    except Exception as e:
        print(f"Error loading routings page data: {e}")

    return render_template('engineering/routings.html',
                           products=products,
                           work_centers=work_centers)


# ==================== NEW PAGE 4: ENGINEERING CHANGE ORDER (ECO) ====================

@engineering_bp.route('/eco')
@login_required
@permission_required('ENGINEERING_ECO_VIEW')
def eco():
    industry = session.get('demo_industry', 'valve')

    products = []
    projects = []

    try:
        with db.engine.connect() as conn:
            # Products for ECO filter
            prod_rows = conn.execute(text("""
                SELECT ProductId, descEnglish 
                FROM t_Product 
                WHERE DemoIndustryCode = :industry
                ORDER BY descEnglish
            """), {'industry': industry}).fetchall()
            products = [{'ProductId': r[0], 'descEnglish': r[1]} for r in prod_rows]

            # Projects for ECO project filter
            proj_rows = conn.execute(text("""
                SELECT ProjectID, ProjectName 
                FROM t_Project 
                WHERE DemoIndustryCode = :industry
                ORDER BY ProjectName
            """), {'industry': industry}).fetchall()
            projects = [{'ProjectID': r[0], 'ProjectName': r[1]} for r in proj_rows]

    except Exception as e:
        print(f"Error loading ECO page data: {e}")

    return render_template('engineering/eco.html',
                           products=products,
                           projects=projects)


print("=== engineering routes.py LOADED ===")