# app/modules/quality/routes.py
import sqlite3
from flask import render_template, Blueprint, flash, redirect, url_for, current_app, request, session
from . import quality_bp
from flask_login import login_required, current_user
from sqlalchemy.sql import text

def _row_to_dict(cursor, row):
    """Convert sqlite3.Row to plain dict."""
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}

def get_engine():
    """Get SQLAlchemy engine from Flask app."""
    return current_app.extensions['sqlalchemy'].engine


# ============================================================================
# QUALITY MODULE - PAGE ROUTES (10 PAGES - v42)
# ============================================================================

# Page 37: Quality Templates
@quality_bp.route("/template/")
def quality_templates():
    return render_template("quality/quality_templates.html")


# Page 38: Product Quality Plans (Planning Assignments)
@quality_bp.route("/plan_assignment/")
def quality_plan_assignment():
    from datetime import datetime
    
    engine = get_engine()
    
    with engine.connect() as conn:
        # 1. Assignments
        result = conn.execute(text("""
            SELECT TOP 100
                AssignmentID,
                QCOperationID,
                SourceType,
                SourceID,
                WorkCenterID,
                OperationSequence,
                IsRequired,
                IsCritical,
                SamplingSize,
                SamplingFrequency,
                EffectiveDateKey,
                ExpiryDateKey,
                IsActive,
                WorkflowStatus,
                ApprovalRequired,
                ProjectID,
                CreatedDate,
                CreatedBy
            FROM t_QualityOperationAssignment
            ORDER BY AssignmentID DESC
        """))
        assignments = [dict(row._mapping) for row in result.fetchall()]
        
        # 2. Source types
        result = conn.execute(text("""
            SELECT DISTINCT SourceType 
            FROM t_QualityOperationAssignment 
            WHERE SourceType IS NOT NULL
        """))
        source_types = [row[0] for row in result.fetchall()]
        
        # 3. Products
        result = conn.execute(text("""
            SELECT ProductId, descEnglish 
            FROM t_Product 
            ORDER BY ProductId
        """))
        products = result.fetchall()
        
        # 4. Projects
        result = conn.execute(text("""
            SELECT ProjectID, ProjectCode, ProjectName 
            FROM t_Project 
            ORDER BY ProjectCode
        """))
        projects = result.fetchall()
        
        # 5. Work centers
        result = conn.execute(text("""
            SELECT WorkCenterID, Name 
            FROM t_WorkCenter 
            ORDER BY Name
        """))
        work_centers = result.fetchall()
        
        # 6. Quality plan templates
        result = conn.execute(text("""
            SELECT TemplateID, TemplateName, TemplateName as TemplateCode 
            FROM t_QualityPlanTemplate 
            WHERE IsActive = 1 
            ORDER BY TemplateName
        """))
        templates = result.fetchall()
        
        # 7. Quality operations
        result = conn.execute(text("""
            SELECT DefinitionID as QCOperationID, Code as OperationCode, Name as OperationName
            FROM t_QualityOperationMaster
            WHERE DefinitionType = 'Operation' AND IsActive = 1
            ORDER BY Code
        """))
        operations = result.fetchall()
    
    current_time = datetime.now().strftime('%Y-%m-%d')
    
    return render_template(
        "quality/plan_assignment.html",
        assignments=assignments,
        source_types=source_types,
        products=products,
        projects=projects,
        work_centers=work_centers,
        templates=templates,
        quality={'operations': operations},
        current_time=current_time
    )


# Page 39: Project Quality Plans
@quality_bp.route('/quality-plans/')
def project_quality_plans():
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT ProjectID, ProjectName 
            FROM t_Project 
            ORDER BY ProjectName
        """))
        projects = [dict(row._mapping) for row in result.fetchall()]
    return render_template("quality/project_quality_plans.html", projects=projects)


# Page 40: Inspection Workbench
@quality_bp.route('/inspection-workbench/')
def inspection_workbench():
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT DISTINCT WorkCenterID 
            FROM t_QualityInspectionBatch 
            WHERE WorkCenterID IS NOT NULL
        """))
        workcenters = [row[0] for row in result.fetchall()]
    return render_template('quality/inspection_workbench.html', workcenters=workcenters)


# Page 41: NCR Register
@quality_bp.route('/ncr-register/')
@login_required
def ncr_register():
    engine = get_engine()
    with engine.connect() as conn:
        products = conn.execute(text("""
            SELECT DISTINCT ProductID 
            FROM t_NonConformance 
            WHERE ProductID IS NOT NULL 
            ORDER BY ProductID
        """)).fetchall()
        sources = conn.execute(text("""
            SELECT DISTINCT SourceType 
            FROM t_NonConformance 
            ORDER BY SourceType
        """)).fetchall()
    return render_template('quality/ncr_register.html', 
                          products=[row[0] for row in products], 
                          sources=[row[0] for row in sources])


# Page 42: CAPA
@quality_bp.route('/capa/')
@login_required
def capa():
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT UserID, FullName 
            FROM t_Users 
            WHERE UserType='Internal' 
            ORDER BY FullName
        """))
        assignees = [dict(row._mapping) for row in result.fetchall()]
    return render_template('quality/capa.html', assignees=assignees)


# Page 43: Supplier Quality Plans
@quality_bp.route('/supplier-plans/')
def supplier_quality_plans():
    engine = get_engine()
    with engine.connect() as conn:
        suppliers = conn.execute(text("""
            SELECT SupplierID, SupplierName 
            FROM t_Suppliers 
            WHERE Status='Active' 
            ORDER BY SupplierName
        """)).fetchall()
        products = conn.execute(text("""
            SELECT ProductId, descEnglish 
            FROM t_Product 
            ORDER BY ProductId
        """)).fetchall()
    return render_template('quality/supplier_quality_plans.html', 
                          suppliers=suppliers, 
                          products=products)


# Page 44: Certificate Management
@quality_bp.route('/certificates/')
@login_required
def certificates():
    engine = get_engine()
    with engine.connect() as conn:
        projects = conn.execute(text("""
            SELECT ProjectID, ProjectName 
            FROM t_Project
        """)).fetchall()
        products = conn.execute(text("""
            SELECT DISTINCT ProductId, descEnglish 
            FROM t_Product 
            ORDER BY ProductId
        """)).fetchall()
    return render_template('quality/certificates.html', 
                          products=products,
                          projects=projects)


# Page 45: Audit Management
@quality_bp.route('/audit-management/')
@login_required
def audit_management():
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT UserID, FullName 
            FROM t_Users 
            WHERE UserType='Internal' 
            ORDER BY FullName
        """))
        auditors = [dict(row._mapping) for row in result.fetchall()]
    return render_template('quality/audit_management.html', auditors=auditors)


# Page 46: Calibration Management
@quality_bp.route('/calibration-management/')
@login_required
def calibration_management():
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT DISTINCT EquipmentName 
            FROM t_EquipmentMaintenance 
            WHERE MaintenanceType='Calibration' 
            AND EquipmentName IS NOT NULL
        """))
        types = [{'type': row[0]} for row in result.fetchall()]
    return render_template('quality/calibration_management.html', types=types)


# ============================================================================
# REDIRECTS (For backward compatibility)
# ============================================================================

@quality_bp.route('/product-quality-plans')
def product_quality_plans_redirect():
    """Redirect from /quality/product-quality-plans to /quality/plan_assignment/ with product_id filter"""
    product_id = request.args.get('product_id')
    if product_id:
        return redirect(url_for('quality.quality_plan_assignment') + f'?product_id={product_id}')
    return redirect(url_for('quality.quality_plan_assignment'))


# ============================================================================
# REMOVED PAGES (Not in 10-Page Quality Module v42)
# ============================================================================
# The following pages have been removed to align with the 10-page Quality module:
# - Incoming Inspection (/incoming-inspection/)
# - In-Process Inspection (/inprocess-inspection/)
# - Supplier Inspection Portal (/supplier-portal/)
# - Defect Management (/defect-management/)
# - Supplier NCR (/supplier-ncr/)
# - Supplier Scorecard (/supplier-scorecard/)
# - Serial Numbers (/serials/)
# - Signature Audit Log (/signature-audit/)
# - Operation Library (/operations/)
# - Quality Dashboard (/quality-dashboard/)
# - Operation Detail (/operation/<int:operation_id>/)