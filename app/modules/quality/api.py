# app/modules/quality/api.py
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request, session, send_file, current_app
from app.core.database import get_db_connection
from sqlalchemy.sql import text
import sqlite3, csv, io
from flask_login import login_required, current_user
from flask import request
from app.modules.auth import verify_password

quality_api = Blueprint('quality_api', __name__, url_prefix='/api/quality')


def get_engine():
    """Get SQLAlchemy engine from Flask app."""
    return current_app.extensions['sqlalchemy'].engine


def _today_key():
    return int(datetime.now().strftime('%Y%m%d'))


# ========== REMOVED PAGES (Not in 10-page Quality Module) ==========
# The following endpoints have been removed as per the specification.
# They are commented out so they can be restored later if needed.

# ========== REMOVED: Operation Library ==========
# @quality_api.route('/operations/<int:operation_id>', methods=['GET'])
# def get_operation(operation_id):
#     ...

# @quality_api.route('/operations/<int:operation_id>', methods=['DELETE'])
# def delete_operation(operation_id):
#     ...

# @quality_api.route('/operations/<int:operation_id>/copy', methods=['POST'])
# def copy_operation(operation_id):
#     ...

# @quality_api.route('/operations/<int:operation_id>/versions', methods=['GET'])
# def get_operation_versions(operation_id):
#     ...

# @quality_api.route('/operations', methods=['POST'])
# def create_operation():
#     ...

# @quality_api.route('/operations/<int:operation_id>', methods=['PUT'])
# def update_operation(operation_id):
#     ...

# @quality_api.route('/operations', methods=['GET'])
# def list_quality_operations():
#     ...

# @quality_api.route('/operations/stats', methods=['GET'])
# def operations_stats():
#     ...

# @quality_api.route('/operations/<int:op_id>/templates', methods=['GET'])
# def get_templates_using_operation(op_id):
#     ...

# @quality_api.route('/operations/import', methods=['POST'])
# def import_operations():
#     ...

# @quality_api.route('/operations/export', methods=['GET'])
# def export_operations():
#     ...

# ========== REMOVED: Equipment (used by Operation Library) ==========
# @quality_api.route('/equipment', methods=['GET'])
# def get_equipment():
#     ...

# @quality_api.route('/equipment-list', methods=['GET'])
# def get_equipment_list():
#     ...

# ========== REMOVED: Incoming Inspection ==========
# @quality_api.route('/incoming/kpi')
# def incoming_kpi():
#     ...

# @quality_api.route('/incoming/batches')
# def incoming_batches():
#     ...

# @quality_api.route('/incoming/batch/<int:batch_id>')
# def get_incoming_batch(batch_id):
#     ...

# @quality_api.route('/incoming/compare/<int:batch_id>')
# def compare_results(batch_id):
#     ...

# @quality_api.route('/incoming/batch/<int:batch_id>/disposition', methods=['POST'])
# def dispose_batch(batch_id):
#     ...

# @quality_api.route('/incoming/suppliers')
# def incoming_suppliers():
#     ...

# @quality_api.route('/incoming/po-numbers')
# def incoming_po_numbers():
#     ...

# ========== REMOVED: In-Process Inspection ==========
# @quality_api.route('/inprocess/kpi')
# def inprocess_kpi():
#     ...

# @quality_api.route('/inprocess/batches')
# def inprocess_batches():
#     ...

# @quality_api.route('/inprocess/batch/<int:batch_id>')
# def get_inprocess_batch(batch_id):
#     ...

# @quality_api.route('/inprocess/result', methods=['POST'])
# def save_inprocess_result():
#     ...

# @quality_api.route('/inprocess/batch/<int:batch_id>/defect', methods=['POST'])
# def report_inprocess_defect(batch_id):
#     ...

# @quality_api.route('/inprocess/batch/<int:batch_id>/signoff', methods=['POST'])
# def inprocess_sign_off(batch_id):
#     ...

# @quality_api.route('/inprocess/workcenters')
# def inprocess_workcenters():
#     ...

# @quality_api.route('/inprocess/production-orders')
# def inprocess_production_orders():
#     ...

# ========== REMOVED: Serial Numbers ==========
# @quality_api.route('/serials/kpi')
# def serials_kpi():
#     ...

# @quality_api.route('/serials/list')
# def serials_list():
#     ...

# @quality_api.route('/serials/<int:serial_id>/history')
# def serials_history(serial_id):
#     ...

# @quality_api.route('/serials/<int:serial_id>/warranty')
# def serials_warranty(serial_id):
#     ...

# @quality_api.route('/serials/<int:serial_id>/label')
# def serials_label(serial_id):
#     ...

# @quality_api.route('/serials/products')
# def serials_products():
#     ...

# @quality_api.route('/serials/projects')
# def serials_projects():
#     ...

# @quality_api.route('/serials/statuses')
# def serials_statuses():
#     ...

# ========== REMOVED: Supplier Inspection Portal ==========
# @quality_api.route('/supplier-portal/kpi')
# @login_required
# def supplier_portal_kpi():
#     ...

# @quality_api.route('/supplier-portal/orders')
# @login_required
# def supplier_portal_orders():
#     ...

# @quality_api.route('/supplier-portal/batch/<int:batch_id>')
# @login_required
# def supplier_portal_batch(batch_id):
#     ...

# @quality_api.route('/supplier-portal/submit', methods=['POST'])
# @login_required
# def supplier_portal_submit():
#     ...

# @quality_api.route('/supplier-portal/me')
# @login_required
# def supplier_portal_me():
#     ...

# ========== REMOVED: Defect Management ==========
# @quality_api.route('/defects/kpi')
# @login_required
# def defects_kpi():
#     ...

# @quality_api.route('/defects/list')
# @login_required
# def defects_list():
#     ...

# @quality_api.route('/defects/<int:ncr_id>')
# @login_required
# def get_defect(ncr_id):
#     ...

# @quality_api.route('/defects/<int:ncr_id>/disposition', methods=['POST'])
# @login_required
# def defect_disposition(ncr_id):
#     ...

# @quality_api.route('/defects/<int:ncr_id>/root-cause', methods=['POST'])
# @login_required
# def defect_root_cause(ncr_id):
#     ...

# @quality_api.route('/defects/products')
# @login_required
# def defect_products():
#     ...

# @quality_api.route('/defects/sources')
# @login_required
# def defect_sources():
#     ...

# @quality_api.route('/defects/export')
# @login_required
# def export_defects():
#     ...

# ========== REMOVED: Supplier NCR ==========
# @quality_api.route('/supplier-ncr/kpi')
# @login_required
# def supplier_ncr_kpi():
#     ...

# @quality_api.route('/supplier-ncr/list')
# @login_required
# def supplier_ncr_list():
#     ...

# @quality_api.route('/supplier-ncr/send', methods=['POST'])
# @login_required
# def send_supplier_ncr():
#     ...

# @quality_api.route('/supplier-ncr/<int:ncr_id>/response', methods=['POST'])
# @login_required
# def submit_supplier_response(ncr_id):
#     ...

# @quality_api.route('/supplier-ncr/suppliers')
# @login_required
# def supplier_ncr_suppliers():
#     ...

# @quality_api.route('/supplier-ncr/export')
# @login_required
# def export_supplier_ncr():
#     ...

# ========== REMOVED: Supplier Scorecard ==========
# @quality_api.route('/supplier-scorecard/kpi')
# @login_required
# def supplier_scorecard_kpi():
#     ...

# @quality_api.route('/supplier-scorecard/list')
# @login_required
# def supplier_scorecard_list():
#     ...

# @quality_api.route('/supplier-scorecard/<supplier_id>')
# @login_required
# def get_supplier_scorecard(supplier_id):
#     ...

# @quality_api.route('/supplier-scorecard/update', methods=['POST'])
# @login_required
# def update_supplier_ratings():
#     ...

# @quality_api.route('/supplier-scorecard/recalculate', methods=['POST'])
# @login_required
# def recalculate_supplier_ratings():
#     ...

# @quality_api.route('/supplier-scorecard/export')
# @login_required
# def export_supplier_scorecard():
#     ...

# @quality_api.route('/supplier-scorecard/categories')
# @login_required
# def supplier_scorecard_categories():
#     ...

# ========== REMOVED: Quality Dashboard ==========
# @quality_api.route('/dashboard/kpi')
# @login_required
# def quality_dashboard_kpi():
#     ...

# @quality_api.route('/dashboard/trend')
# @login_required
# def quality_dashboard_trend():
#     ...

# @quality_api.route('/dashboard/products')
# @login_required
# def quality_dashboard_products():
#     ...

# @quality_api.route('/dashboard/workcenters')
# @login_required
# def quality_dashboard_workcenters():
#     ...

# ========== REMOVED: Digital Signatures (embedded, but if not used) ==========
# @quality_api.route('/sign/inspection/<int:result_id>', methods=['POST'])
# @login_required
# def sign_inspection_result(result_id):
#     ...

# @quality_api.route('/sign/ncr/<int:ncr_id>/disposition', methods=['POST'])
# @login_required
# def sign_ncr_disposition(ncr_id):
#     ...

# @quality_api.route('/sign/audit-log', methods=['GET'])
# @login_required
# def signature_audit_log():
#     ...

# ========== PRODUCTS PLACEHOLDER ==========
@quality_api.route('/products', methods=['GET'])
def products_placeholder():
    """Temporary placeholder for the Products page (since view-products redirects here)."""
    return jsonify({'message': 'Products module not yet implemented. Please create Product Management first.'}), 501


# ========== 1. QUALITY TEMPLATES API ==========
@quality_api.route('/quality_templates/list')
@login_required
def quality_templates_list():
    family = request.args.get('family', '')
    active_only = request.args.get('active_only', 'false') == 'true'
    search = request.args.get('search', '')
    
    engine = get_engine()
    
    # SQL Server compatible query (no LIMIT, use TOP 1000)
    query = """
        SELECT TOP 1000
            t.TemplateID,
            t.TemplateName,
            t.ProductFamily,
            t.Description,
            t.IsActive,
            t.CreatedDateKey,
            t.CreatedBy
        FROM t_QualityPlanTemplate t
        WHERE 1=1
    """
    params = {}
    if family:
        query += " AND t.ProductFamily = :family"
        params['family'] = family
    if active_only:
        query += " AND t.IsActive = 1"
    if search:
        query += " AND (t.TemplateName LIKE :search OR t.Description LIKE :search)"
        params['search'] = f'%{search}%'
    query += " ORDER BY t.TemplateName"
    
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
        templates = []
        for r in rows:
            templates.append({
                'TemplateID': r[0],
                'TemplateName': r[1],
                'ProductFamily': r[2],
                'Description': r[3],
                'IsActive': bool(r[4]),
                'CreatedDate': str(r[5]) if r[5] else ''
            })
    return jsonify(templates)


@quality_api.route('/quality_templates/stats')
@login_required
def quality_templates_stats():
    engine = get_engine()
    with engine.connect() as conn:
        total = conn.execute(text("SELECT COUNT(*) FROM t_QualityPlanTemplate")).scalar() or 0
        active = conn.execute(text("SELECT COUNT(*) FROM t_QualityPlanTemplate WHERE IsActive = 1")).scalar() or 0
        used_in_products = conn.execute(text("SELECT COUNT(DISTINCT TemplateID) FROM t_ProjectQualityPlan WHERE TemplateID IS NOT NULL")).scalar() or 0
        used_in_projects = conn.execute(text("SELECT COUNT(DISTINCT TemplateID) FROM t_ProjectQualityPlan WHERE ProjectID IS NOT NULL AND TemplateID IS NOT NULL")).scalar() or 0
    
    return jsonify({
        'total': total,
        'active': active,
        'usedInProducts': used_in_products,
        'usedInProjects': used_in_projects
    })


@quality_api.route('/quality_templates/<int:template_id>')
@login_required
def get_quality_template(template_id):
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM t_QualityPlanTemplate WHERE TemplateID = :tid"),
            {'tid': template_id}
        ).first()
        if not row:
            return jsonify({'error': 'Template not found'}), 404
        
        template = dict(row._mapping)
        template['CreatedDate'] = str(template['CreatedDateKey']) if template['CreatedDateKey'] else ''
        template['IsActive'] = bool(template['IsActive'])
    return jsonify(template)


@quality_api.route('/quality_templates', methods=['POST'])
@login_required
def create_quality_template():
    data = request.get_json()
    required = ['TemplateName']
    for f in required:
        if not data.get(f):
            return jsonify({'error': f'Missing required field: {f}'}), 400
    
    engine = get_engine()
    today_key = _today_key()
    
    with engine.begin() as conn:
        result = conn.execute(
            text("""
                INSERT INTO t_QualityPlanTemplate
                (TemplateName, ProductFamily, Description, IsActive, CreatedBy, CreatedDateKey, Version)
                OUTPUT INSERTED.TemplateID
                VALUES (:name, :family, :desc, :active, :user, :date, 1)
            """),
            {
                'name': data['TemplateName'],
                'family': data.get('ProductFamily'),
                'desc': data.get('Description'),
                'active': data.get('IsActive', 1),
                'user': current_user.get_id(),
                'date': today_key
            }
        )
        template_id = result.scalar()
        
        if data.get('copyFromTemplateId'):
            copy_id = data['copyFromTemplateId']
            conn.execute(
                text("""
                    INSERT INTO t_QualityPlanTemplateDetail (TemplateID, QCOperationID, OperationSequence, IsCritical, SamplingSize)
                    SELECT :tid, QCOperationID, OperationSequence, IsCritical, SamplingSize
                    FROM t_QualityPlanTemplateDetail
                    WHERE TemplateID = :copy_id
                """),
                {'tid': template_id, 'copy_id': copy_id}
            )
    
    return jsonify({'TemplateID': template_id, 'message': 'Template created'}), 201


@quality_api.route('/quality_templates/<int:template_id>', methods=['PUT'])
@login_required
def update_quality_template(template_id):
    data = request.get_json()
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE t_QualityPlanTemplate
                SET TemplateName = :name, ProductFamily = :family, Description = :desc, IsActive = :active
                WHERE TemplateID = :tid
            """),
            {
                'name': data.get('TemplateName'),
                'family': data.get('ProductFamily'),
                'desc': data.get('Description'),
                'active': data.get('IsActive', 1),
                'tid': template_id
            }
        )
    return jsonify({'message': 'Template updated'})


@quality_api.route('/quality_templates/<int:template_id>', methods=['DELETE'])
@login_required
def delete_quality_template(template_id):
    engine = get_engine()
    with engine.begin() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM t_ProjectQualityPlan WHERE TemplateID = :tid"),
            {'tid': template_id}
        ).scalar()
        if count > 0:
            return jsonify({'error': 'Cannot delete template because it is used in quality plans'}), 400
        conn.execute(text("DELETE FROM t_QualityPlanTemplateDetail WHERE TemplateID = :tid"), {'tid': template_id})
        conn.execute(text("DELETE FROM t_QualityPlanTemplate WHERE TemplateID = :tid"), {'tid': template_id})
    return jsonify({'message': 'Template deleted'})


@quality_api.route('/quality_templates/<int:template_id>/copy', methods=['POST'])
@login_required
def copy_quality_template(template_id):
    engine = get_engine()
    today_key = _today_key()
    
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT * FROM t_QualityPlanTemplate WHERE TemplateID = :tid"),
            {'tid': template_id}
        ).first()
        if not row:
            return jsonify({'error': 'Template not found'}), 404
        
        original = dict(row._mapping)
        new_name = f"{original['TemplateName']} (Copy)"
        
        result = conn.execute(
            text("""
                INSERT INTO t_QualityPlanTemplate
                (TemplateName, ProductFamily, Description, IsActive, CreatedBy, CreatedDateKey, Version)
                OUTPUT INSERTED.TemplateID
                VALUES (:name, :family, :desc, :active, :user, :date, 1)
            """),
            {
                'name': new_name,
                'family': original['ProductFamily'],
                'desc': original['Description'],
                'active': original['IsActive'],
                'user': current_user.get_id(),
                'date': today_key
            }
        )
        new_id = result.scalar()
        
        conn.execute(
            text("""
                INSERT INTO t_QualityPlanTemplateDetail (TemplateID, QCOperationID, OperationSequence, IsCritical, SamplingSize)
                SELECT :new_id, QCOperationID, OperationSequence, IsCritical, SamplingSize
                FROM t_QualityPlanTemplateDetail
                WHERE TemplateID = :old_id
            """),
            {'new_id': new_id, 'old_id': template_id}
        )
    
    return jsonify({'newTemplateId': new_id, 'message': 'Template copied'})


@quality_api.route('/quality_templates/export')
@login_required
def export_quality_templates():
    family = request.args.get('family', '')
    search = request.args.get('search', '')
    active_only = request.args.get('active_only', 'false') == 'true'
    
    engine = get_engine()
    query = """
        SELECT t.TemplateID, t.TemplateName, t.ProductFamily, t.Description, t.IsActive, t.CreatedDateKey
        FROM t_QualityPlanTemplate t
        WHERE 1=1
    """
    params = {}
    if family:
        query += " AND t.ProductFamily = :family"
        params['family'] = family
    if active_only:
        query += " AND t.IsActive = 1"
    if search:
        query += " AND (t.TemplateName LIKE :search OR t.Description LIKE :search)"
        params['search'] = f'%{search}%'
    
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['TemplateID', 'TemplateName', 'ProductFamily', 'Description', 'IsActive', 'CreatedDateKey'])
    for r in rows:
        writer.writerow([r[0], r[1], r[2], r[3], r[4], r[5]])
    return output.getvalue(), 200, {'Content-Type': 'text/csv', 'Content-Disposition': 'attachment; filename=quality_templates.csv'}


@quality_api.route('/quality_templates/<int:template_id>/operations')
@login_required
def get_template_operations(template_id):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT td.DetailID, td.QCOperationID, td.OperationSequence, td.IsCritical, td.SamplingSize,
                       om.Code as OperationCode, om.Name as OperationName, om.Category, om.UnitOfMeasure
                FROM t_QualityPlanTemplateDetail td
                JOIN t_QualityOperationMaster om ON td.QCOperationID = om.DefinitionID
                WHERE td.TemplateID = :tid
                ORDER BY td.OperationSequence
            """),
            {'tid': template_id}
        ).fetchall()
        
        ops = []
        for r in rows:
            ops.append({
                'TemplateDetailID': r[0],
                'QCOperationID': r[1],
                'OperationCode': r[5],
                'OperationName': r[6],
                'OperationSequence': r[2],
                'IsCritical': bool(r[3]),
                'SamplingSize': r[4],
                'Category': r[7],
                'Unit': r[8]
            })
    return jsonify(ops)


@quality_api.route('/operations/list')
@login_required
def list_all_operations():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT DefinitionID as QCOperationID, Code as OperationCode, Name as OperationName,
                       Category, Description, UnitOfMeasure
                FROM t_QualityOperationMaster
                WHERE DefinitionType = 'Operation' AND IsActive = 1
                ORDER BY Code
            """)
        ).fetchall()
        ops = [dict(r._mapping) for r in rows]
    return jsonify(ops)


@quality_api.route('/quality_templates/<int:template_id>/operations', methods=['POST'])
@login_required
def add_template_operations(template_id):
    data = request.get_json()
    operation_ids = data.get('operationIds', [])
    if not operation_ids:
        return jsonify({'error': 'No operations selected'}), 400
    
    engine = get_engine()
    with engine.begin() as conn:
        max_seq = conn.execute(
            text("SELECT MAX(OperationSequence) FROM t_QualityPlanTemplateDetail WHERE TemplateID = :tid"),
            {'tid': template_id}
        ).scalar() or 0
        seq = max_seq + 1
        
        for op_id in operation_ids:
            conn.execute(
                text("""
                    INSERT INTO t_QualityPlanTemplateDetail (TemplateID, QCOperationID, OperationSequence, IsCritical, SamplingSize)
                    VALUES (:tid, :op_id, :seq, 0, 1)
                """),
                {'tid': template_id, 'op_id': op_id, 'seq': seq}
            )
            seq += 1
    
    return jsonify({'message': f'{len(operation_ids)} operations added'})


@quality_api.route('/quality_templates/operation-detail/<int:detail_id>', methods=['DELETE'])
@login_required
def remove_template_operation(detail_id):
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM t_QualityPlanTemplateDetail WHERE DetailID = :did"),
            {'did': detail_id}
        )
    return jsonify({'message': 'Operation removed'})


# ========== 2. PRODUCT QUALITY PLANS API (Planning Assignments) ==========


@quality_api.route('/planning/assignments/list')
@login_required
def planning_assignments_list():
    source_type = request.args.get('source_type', '')
    source_id = request.args.get('source_id', '')
    project_id = request.args.get('project_id', '')
    is_active_param = request.args.get('is_active', '')
    search = request.args.get('search', '')

    query = """
        SELECT 
            a.AssignmentID,
            a.QCOperationID,
            a.SourceType,
            a.SourceID,
            a.WorkCenterID,
            a.OperationSequence,
            a.IsRequired,
            a.IsCritical,
            a.SamplingSize,
            a.SamplingFrequency,
            a.EffectiveDateKey,
            a.ExpiryDateKey,
            a.IsActive,
            a.WorkflowStatus,
            a.ApprovalRequired,
            a.ProjectID,
            o.Code as OperationCode,
            o.Name as OperationName,
            CAST(a.EffectiveDateKey AS VARCHAR(8)) as EffectiveDateFormatted,
            CAST(a.ExpiryDateKey AS VARCHAR(8)) as ExpiryDateFormatted
        FROM t_QualityOperationAssignment a
        LEFT JOIN t_QualityOperationMaster o ON a.QCOperationID = o.DefinitionID
        WHERE 1=1
    """
    params = {}
    if source_type:
        query += " AND a.SourceType = :source_type"
        params['source_type'] = source_type
    if source_id:
        query += " AND a.SourceID = :source_id"
        params['source_id'] = source_id
    if project_id:
        query += " AND a.ProjectID = :project_id"
        params['project_id'] = project_id
    if is_active_param == 'true':
        query += " AND a.IsActive = 1"
    elif is_active_param == 'false':
        query += " AND a.IsActive = 0"
    if search:
        query += " AND (o.Code LIKE :search OR o.Name LIKE :search OR a.SourceID LIKE :search)"
        params['search'] = f"%{search}%"
    query += " ORDER BY a.AssignmentID DESC"

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
        assignments = []
        for r in rows:
            assignment = dict(r._mapping)
            # Format dates in Python instead of SQL
            if assignment.get('EffectiveDateKey'):
                d = str(assignment['EffectiveDateKey'])
                # Ensure it's 8 digits (YYYYMMDD)
                if len(d) >= 8:
                    assignment['EffectiveDateFormatted'] = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
                else:
                    assignment['EffectiveDateFormatted'] = d
            else:
                assignment['EffectiveDateFormatted'] = ''
            
            if assignment.get('ExpiryDateKey'):
                d = str(assignment['ExpiryDateKey'])
                if len(d) >= 8:
                    assignment['ExpiryDateFormatted'] = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
                else:
                    assignment['ExpiryDateFormatted'] = d
            else:
                assignment['ExpiryDateFormatted'] = ''
            
            assignments.append(assignment)
    return jsonify(assignments)

@quality_api.route('/planning/assignments', methods=['POST'])
@login_required
def create_planning_assignment():
    data = request.get_json()
    engine = get_engine()
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text("""
                    INSERT INTO t_QualityOperationAssignment
                    (QCOperationID, SourceType, SourceID, WorkCenterID, OperationSequence,
                     IsRequired, IsCritical, SamplingSize, SamplingFrequency,
                     EffectiveDateKey, ExpiryDateKey, IsActive, WorkflowStatus,
                     ApprovalRequired, ProjectID, CreatedDate, CreatedBy)
                    OUTPUT INSERTED.AssignmentID
                    VALUES (:qcop, :stype, :sid, :wc, :seq,
                            :req, :crit, :ssize, :sfreq,
                            :eff, :exp, :active, :wfs,
                            :approv, :proj, GETDATE(), :user)
                """),
                {
                    'qcop': data['QCOperationID'],
                    'stype': data['SourceType'],
                    'sid': data['SourceID'],
                    'wc': data.get('WorkCenterID'),
                    'seq': data['OperationSequence'],
                    'req': data.get('IsRequired', 1),
                    'crit': data.get('IsCritical', 0),
                    'ssize': data.get('SamplingSize'),
                    'sfreq': data.get('SamplingFrequency'),
                    'eff': data.get('EffectiveDateKey'),
                    'exp': data.get('ExpiryDateKey'),
                    'active': data.get('IsActive', 1),
                    'wfs': data.get('WorkflowStatus', 'Draft'),
                    'approv': data.get('ApprovalRequired', 0),
                    'proj': data.get('ProjectID'),
                    'user': current_user.get_id()
                }
            )
            assignment_id = result.scalar()
        return jsonify({'success': True, 'message': 'Assignment created', 'assignment_id': assignment_id}), 201
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@quality_api.route('/planning/assignments/<int:assignment_id>', methods=['PUT'])
@login_required
def update_planning_assignment(assignment_id):
    data = request.get_json()
    engine = get_engine()
    try:
        with engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE t_QualityOperationAssignment
                    SET QCOperationID = :qcop, SourceType = :stype, SourceID = :sid, WorkCenterID = :wc,
                        OperationSequence = :seq, IsRequired = :req, IsCritical = :crit,
                        SamplingSize = :ssize, SamplingFrequency = :sfreq, EffectiveDateKey = :eff,
                        ExpiryDateKey = :exp, IsActive = :active, WorkflowStatus = :wfs,
                        ApprovalRequired = :approv, ProjectID = :proj, UpdatedDate = GETDATE()
                    WHERE AssignmentID = :aid
                """),
                {
                    'qcop': data['QCOperationID'],
                    'stype': data['SourceType'],
                    'sid': data['SourceID'],
                    'wc': data.get('WorkCenterID'),
                    'seq': data['OperationSequence'],
                    'req': data.get('IsRequired', 1),
                    'crit': data.get('IsCritical', 0),
                    'ssize': data.get('SamplingSize'),
                    'sfreq': data.get('SamplingFrequency'),
                    'eff': data.get('EffectiveDateKey'),
                    'exp': data.get('ExpiryDateKey'),
                    'active': data.get('IsActive', 1),
                    'wfs': data.get('WorkflowStatus', 'Draft'),
                    'approv': data.get('ApprovalRequired', 0),
                    'proj': data.get('ProjectID'),
                    'aid': assignment_id
                }
            )
        return jsonify({'success': True, 'message': 'Assignment updated'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@quality_api.route('/planning/assignments/<int:assignment_id>', methods=['DELETE'])
@login_required
def delete_planning_assignment(assignment_id):
    engine = get_engine()
    try:
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE t_QualityOperationAssignment SET IsActive = 0 WHERE AssignmentID = :aid"),
                {'aid': assignment_id}
            )
        return jsonify({'success': True, 'message': 'Assignment deactivated'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@quality_api.route('/planning/assignments/<int:assignment_id>', methods=['GET'])
@login_required
def get_planning_assignment(assignment_id):
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT 
                    a.AssignmentID,
                    a.QCOperationID,
                    a.SourceType,
                    a.SourceID,
                    a.WorkCenterID,
                    a.OperationSequence,
                    a.IsRequired,
                    a.IsCritical,
                    a.SamplingSize,
                    a.SamplingFrequency,
                    a.EffectiveDateKey,
                    a.ExpiryDateKey,
                    a.IsActive,
                    a.WorkflowStatus,
                    a.ApprovalRequired,
                    a.ProjectID,
                    o.Code as OperationCode,
                    o.Name as OperationName
                FROM t_QualityOperationAssignment a
                LEFT JOIN t_QualityOperationMaster o ON a.QCOperationID = o.DefinitionID
                WHERE a.AssignmentID = :aid
            """),
            {'aid': assignment_id}
        ).first()
        if not row:
            return jsonify({'success': False, 'error': 'Assignment not found'}), 404
        
        assignment = dict(row._mapping)
        if assignment.get('EffectiveDateKey'):
            d = str(assignment['EffectiveDateKey'])
            assignment['EffectiveDateFormatted'] = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        if assignment.get('ExpiryDateKey'):
            d = str(assignment['ExpiryDateKey'])
            assignment['ExpiryDateFormatted'] = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return jsonify({'success': True, 'assignment': assignment})


# ========== 3. PROJECT QUALITY PLANS API ==========
@quality_api.route('/quality-plans/kpi')
@login_required
def quality_plans_kpi():
    return jsonify({'Draft': 2, 'Pending Approval': 1, 'Approved': 5})


@quality_api.route('/quality-plans/list')
@login_required
def quality_plans_list():
    project = request.args.get('project', '')
    status = request.args.get('status', '')
    
    query = """
        SELECT pqp.ProjectQualityPlanID, p.ProjectName, pr.ProductId, pqp.PlanName,
               pqp.PlanStatus, pqp.RevisionNumber, pqp.EffectiveDateKey
        FROM t_ProjectQualityPlan pqp
        JOIN t_Project p ON pqp.ProjectID = p.ProjectID
        JOIN t_Product pr ON pqp.ProductID = pr.ProductId
        WHERE 1=1
    """
    params = {}
    if project:
        query += " AND pqp.ProjectID = :project"
        params['project'] = project
    if status:
        query += " AND pqp.PlanStatus = :status"
        params['status'] = status
    query += " ORDER BY p.ProjectName, pr.ProductId"
    
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
        plans = [{
            'id': r[0], 'projectName': r[1], 'productId': r[2], 'planName': r[3],
            'status': r[4], 'revision': r[5], 'effectiveDateKey': r[6]
        } for r in rows]
    return jsonify(plans)


@quality_api.route('/quality-plans/plan', methods=['POST'])
@login_required
def create_quality_plan():
    data = request.get_json()
    today_key = _today_key()
    
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(
            text("""
                INSERT INTO t_ProjectQualityPlan
                (ProjectID, ProductID, PlanName, PlanStatus, RevisionNumber, CreatedBy, CreatedDate, EffectiveDateKey)
                OUTPUT INSERTED.ProjectQualityPlanID
                VALUES (:proj, :prod, :name, 'Draft', 1, :user, GETDATE(), :date)
            """),
            {
                'proj': data['projectId'],
                'prod': data['productId'],
                'name': data['planName'],
                'user': request.headers.get('X-User-ID', 'system'),
                'date': today_key
            }
        )
        plan_id = result.scalar()
    return jsonify({'id': plan_id, 'message': 'Plan created'}), 201


@quality_api.route('/quality-plans/plan/<int:plan_id>', methods=['GET'])
@login_required
def get_quality_plan(plan_id):
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM t_ProjectQualityPlan WHERE ProjectQualityPlanID = :pid"),
            {'pid': plan_id}
        ).first()
        if not row:
            return jsonify({'error': 'Not found'}), 404
        
        plan = dict(row._mapping)
        
        detail_rows = conn.execute(
            text("""
                SELECT qoa.AssignmentID, qom.DefinitionID as operationId, qom.Code, qom.Name,
                       qoa.OperationSequence, qoa.SamplingSize, qoa.IsCritical,
                       qoa.SpecMin, qoa.SpecMax, qoa.MeasurementUnit,
                       qoa.ApprovalStatus
                FROM t_QualityOperationAssignment qoa
                JOIN t_QualityOperationMaster qom ON qoa.QCOperationID = qom.DefinitionID
                WHERE qoa.PlanID = :pid
                ORDER BY qoa.OperationSequence
            """),
            {'pid': plan_id}
        ).fetchall()
        details = [dict(r._mapping) for r in detail_rows]
    
    return jsonify({
        'id': plan['ProjectQualityPlanID'],
        'projectId': plan['ProjectID'],
        'productId': plan['ProductID'],
        'planName': plan['PlanName'],
        'status': plan['PlanStatus'],
        'revision': plan['RevisionNumber'],
        'effectiveDateKey': plan['EffectiveDateKey'],
        'notes': plan['Notes'],
        'details': details
    })


@quality_api.route('/quality-plans/plan/<int:plan_id>', methods=['PUT'])
@login_required
def update_quality_plan(plan_id):
    data = request.get_json()
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE t_ProjectQualityPlan
                SET PlanName = :name, EffectiveDateKey = :date, Notes = :notes
                WHERE ProjectQualityPlanID = :pid
            """),
            {
                'name': data['planName'],
                'date': data.get('effectiveDateKey'),
                'notes': data.get('notes'),
                'pid': plan_id
            }
        )
    return jsonify({'message': 'Plan updated'})


@quality_api.route('/quality-plans/plan/<int:plan_id>/detail', methods=['POST'])
@login_required
def save_plan_detail(plan_id):
    data = request.get_json()
    engine = get_engine()
    with engine.begin() as conn:
        if 'assignmentId' in data and data['assignmentId']:
            conn.execute(
                text("""
                    UPDATE t_QualityOperationAssignment
                    SET OperationSequence = :seq, SamplingSize = :ssize, IsCritical = :crit,
                        SpecMin = :min, SpecMax = :max, MeasurementUnit = :unit,
                        UpdatedDate = GETDATE()
                    WHERE AssignmentID = :aid
                """),
                {
                    'seq': data['sequence'],
                    'ssize': data['sampleSize'],
                    'crit': data['isCritical'],
                    'min': data.get('specMin'),
                    'max': data.get('specMax'),
                    'unit': data.get('unit'),
                    'aid': data['assignmentId']
                }
            )
        else:
            conn.execute(
                text("""
                    INSERT INTO t_QualityOperationAssignment
                    (QCOperationID, SourceType, SourceID, OperationSequence, SamplingSize, IsCritical,
                     SpecMin, SpecMax, MeasurementUnit, PlanID, CreatedDate, CreatedBy, IsActive)
                    VALUES (:opid, 'Plan', :plan, :seq, :ssize, :crit,
                            :min, :max, :unit, :plan, GETDATE(), :user, 1)
                """),
                {
                    'opid': data['operationId'],
                    'plan': str(plan_id),
                    'seq': data['sequence'],
                    'ssize': data['sampleSize'],
                    'crit': data['isCritical'],
                    'min': data.get('specMin'),
                    'max': data.get('specMax'),
                    'unit': data.get('unit'),
                    'user': request.headers.get('X-User-ID', 'system')
                }
            )
    return jsonify({'message': 'Detail saved'})


@quality_api.route('/quality-plans/plan/<int:plan_id>/detail/<int:assignment_id>', methods=['DELETE'])
@login_required
def delete_plan_detail(plan_id, assignment_id):
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM t_QualityOperationAssignment WHERE AssignmentID = :aid"),
            {'aid': assignment_id}
        )
    return jsonify({'message': 'Deleted'})


@quality_api.route('/quality-plans/plan/<int:plan_id>/submit', methods=['POST'])
@login_required
def submit_quality_plan(plan_id):
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT PlanStatus FROM t_ProjectQualityPlan WHERE ProjectQualityPlanID = :pid"),
            {'pid': plan_id}
        ).first()
        if not row or row[0] != 'Draft':
            return jsonify({'error': 'Only draft plans can be submitted'}), 400
        
        result = conn.execute(
            text("""
                INSERT INTO t_Workflow (EntityType, EntityID, CurrentStep, Status, Priority,
                                        CreatedBy, CreatedDateKey, IsActive)
                OUTPUT INSERTED.WorkflowID
                SELECT 'QualityPlan', :pid, 1, 'InProgress', 1, :user, :date, 1
                FROM t_WorkflowDefinitions
                WHERE EntityType = 'QualityPlan' AND IsActive = 1
            """),
            {
                'pid': plan_id,
                'user': request.headers.get('X-User-ID', 'system'),
                'date': _today_key()
            }
        )
        wf_id = result.scalar()
        
        conn.execute(
            text("""
                UPDATE t_ProjectQualityPlan
                SET WorkflowInstanceID = :wfid, PlanStatus = 'Pending Approval'
                WHERE ProjectQualityPlanID = :pid
            """),
            {'wfid': wf_id, 'pid': plan_id}
        )
    return jsonify({'message': 'Plan submitted', 'workflowId': wf_id})


@quality_api.route('/quality-plans/operations')
@login_required
def list_quality_operations_for_plan():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT DefinitionID, Code, Name FROM t_QualityOperationMaster WHERE DefinitionType = 'Operation' AND IsActive = 1 ORDER BY Code")
        ).fetchall()
    return jsonify([{'id': r[0], 'code': r[1], 'name': r[2]} for r in rows])


@quality_api.route('/projects/<project_id>/products')
@login_required
def project_products_for_plan(project_id):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT ProductID FROM t_ProjectProducts WHERE ProjectID = :pid"),
            {'pid': project_id}
        ).fetchall()
    return jsonify([{'productId': r[0]} for r in rows])


# ========== 4. SUPPLIER QUALITY PLANS API ==========
@quality_api.route('/supplier-plans/kpi')
@login_required
def supplier_plans_kpi():
    engine = get_engine()
    with engine.connect() as conn:
        total = conn.execute(
            text("SELECT COUNT(*) FROM t_QualityOperationAssignment WHERE SourceType = 'SupplierProduct' AND IsActive = 1")
        ).scalar() or 0
        
        suppliers = conn.execute(
            text("SELECT COUNT(DISTINCT LEFT(SourceID, CHARINDEX('||', SourceID) - 1)) FROM t_QualityOperationAssignment WHERE SourceType = 'SupplierProduct' AND IsActive = 1")
        ).scalar() or 0
        
        products = conn.execute(
            text("SELECT COUNT(DISTINCT SUBSTRING(SourceID, CHARINDEX('||', SourceID) + 2, LEN(SourceID))) FROM t_QualityOperationAssignment WHERE SourceType = 'SupplierProduct' AND IsActive = 1")
        ).scalar() or 0
    
    return jsonify({'total': total, 'suppliers': suppliers, 'products': products})


@quality_api.route('/supplier-plans/list')
@login_required
def supplier_plans_list():
    supplier_filter = request.args.get('supplier', '')
    product_filter = request.args.get('product', '')
    
    query = """
        SELECT qoa.AssignmentID, qoa.SourceID, qoa.QCOperationID, 
               COALESCE(qom.Code, '?') AS Code, 
               COALESCE(qom.Name, '[Missing Operation]') AS Name,
               qoa.SamplingSize, qoa.SamplingFrequency,
               qoa.SpecMin, qoa.SpecMax, qoa.MeasurementUnit,
               qoa.IsRequired, qoa.IsCritical, qoa.CertificateRequired,
               qoa.EffectiveDateKey, qoa.ExpiryDateKey, qoa.IsActive
        FROM t_QualityOperationAssignment qoa
        LEFT JOIN t_QualityOperationMaster qom ON qoa.QCOperationID = qom.DefinitionID
        WHERE qoa.SourceType = 'SupplierProduct'
    """
    params = {}
    if supplier_filter:
        query += " AND qoa.SourceID LIKE :supplier"
        params['supplier'] = f"{supplier_filter}||%"
    if product_filter:
        query += " AND qoa.SourceID LIKE :product"
        params['product'] = f"%||{product_filter}"
    query += " ORDER BY qoa.SourceID, qom.Code"
    
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
        
        assignments = []
        for r in rows:
            parts = r[1].split('||', 1)
            supplier_id = parts[0] if len(parts) > 0 else ''
            product_id = parts[1] if len(parts) > 1 else ''
            
            sup_row = conn.execute(
                text("SELECT SupplierName FROM t_Suppliers WHERE SupplierID = :sid"),
                {'sid': supplier_id}
            ).first()
            supplier_name = sup_row[0] if sup_row else supplier_id
            
            assignments.append({
                'assignmentId': r[0],
                'supplierId': supplier_id,
                'supplierName': supplier_name,
                'productId': product_id,
                'operationId': r[2],
                'operationCode': r[3],
                'operationName': r[4],
                'samplingSize': r[5],
                'samplingFrequency': r[6],
                'specMin': r[7],
                'specMax': r[8],
                'unit': r[9],
                'isRequired': bool(r[10]),
                'isCritical': bool(r[11]),
                'certificateRequired': bool(r[12]),
                'effectiveDateKey': r[13],
                'expiryDateKey': r[14],
                'isActive': bool(r[15])
            })
    return jsonify(assignments)


@quality_api.route('/supplier-plans/assignment', methods=['POST'])
@login_required
def create_supplier_plan_assignment():
    data = request.get_json()
    
    required = ['supplierId', 'productId', 'operationId']
    for field in required:
        if not data.get(field):
            return jsonify({'error': f'Missing required field: {field}'}), 400
    
    source_id = f"{data['supplierId']}||{data['productId']}"
    
    engine = get_engine()
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text("""
                    INSERT INTO t_QualityOperationAssignment
                    (QCOperationID, SourceType, SourceID, OperationSequence, SamplingSize, SamplingFrequency,
                     SpecMin, SpecMax, MeasurementUnit, IsRequired, IsCritical, CertificateRequired,
                     EffectiveDateKey, ExpiryDateKey, IsActive, CreatedDate, CreatedBy)
                    OUTPUT INSERTED.AssignmentID
                    VALUES (:opid, 'SupplierProduct', :sid, :seq, :ssize, :sfreq,
                            :min, :max, :unit, :req, :crit, :cert,
                            :eff, :exp, 1, GETDATE(), :user)
                """),
                {
                    'opid': data['operationId'],
                    'sid': source_id,
                    'seq': data.get('sequence', 1),
                    'ssize': data.get('samplingSize'),
                    'sfreq': data.get('samplingFrequency'),
                    'min': data.get('specMin'),
                    'max': data.get('specMax'),
                    'unit': data.get('unit'),
                    'req': data.get('isRequired', 1),
                    'crit': data.get('isCritical', 0),
                    'cert': data.get('certificateRequired', 0),
                    'eff': data.get('effectiveDateKey'),
                    'exp': data.get('expiryDateKey'),
                    'user': request.headers.get('X-User-ID', 'system')
                }
            )
            assignment_id = result.scalar()
        return jsonify({'assignmentId': assignment_id, 'message': 'Created'}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@quality_api.route('/supplier-plans/assignment/<int:assignment_id>', methods=['PUT'])
@login_required
def update_supplier_plan_assignment(assignment_id):
    data = request.get_json()
    if not data.get('operationId'):
        return jsonify({'error': 'operationId is required'}), 400
    
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE t_QualityOperationAssignment
                SET QCOperationID = :opid, OperationSequence = :seq, SamplingSize = :ssize, SamplingFrequency = :sfreq,
                    SpecMin = :min, SpecMax = :max, MeasurementUnit = :unit, IsRequired = :req, IsCritical = :crit,
                    CertificateRequired = :cert, EffectiveDateKey = :eff, ExpiryDateKey = :exp, UpdatedDate = GETDATE()
                WHERE AssignmentID = :aid
            """),
            {
                'opid': data['operationId'],
                'seq': data.get('sequence', 1),
                'ssize': data.get('samplingSize'),
                'sfreq': data.get('samplingFrequency'),
                'min': data.get('specMin'),
                'max': data.get('specMax'),
                'unit': data.get('unit'),
                'req': data.get('isRequired', 1),
                'crit': data.get('isCritical', 0),
                'cert': data.get('certificateRequired', 0),
                'eff': data.get('effectiveDateKey'),
                'exp': data.get('expiryDateKey'),
                'aid': assignment_id
            }
        )
    return jsonify({'message': 'Updated'})


@quality_api.route('/supplier-plans/assignment/<int:assignment_id>', methods=['DELETE'])
@login_required
def delete_supplier_plan_assignment(assignment_id):
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM t_QualityOperationAssignment WHERE AssignmentID = :aid"),
            {'aid': assignment_id}
        )
    return jsonify({'message': 'Deleted'})


@quality_api.route('/supplier-plans/assignment/<int:assignment_id>', methods=['GET'])
@login_required
def get_supplier_plan_assignment(assignment_id):
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM t_QualityOperationAssignment WHERE AssignmentID = :aid"),
            {'aid': assignment_id}
        ).first()
        if not row:
            return jsonify({'error': 'Not found'}), 404
        
        result = dict(row._mapping)
        parts = result['SourceID'].split('||', 1)
        result['supplierId'] = parts[0] if len(parts) > 0 else ''
        result['productId'] = parts[1] if len(parts) > 1 else ''
    return jsonify(result)


# ========== 5. INSPECTION WORKBENCH API ==========
@quality_api.route('/inspection/kpi')
@login_required
def inspection_kpi():
    engine = get_engine()
    today_key = _today_key()
    with engine.connect() as conn:
        pending = conn.execute(
            text("SELECT COUNT(*) FROM t_QualityInspectionBatch WHERE Status = 'Pending'")
        ).scalar() or 0
        
        completed_today = conn.execute(
            text("SELECT COUNT(*) FROM t_QualityInspectionBatch WHERE InspectionDateKey = :today AND Status = 'Completed'"),
            {'today': today_key}
        ).scalar() or 0
        
        defects_today = conn.execute(
            text("""
                SELECT COUNT(*) FROM t_NonConformance n
                JOIN t_QualityInspectionBatch b ON n.BatchLotNumber = b.BatchNumber
                WHERE b.InspectionDateKey = :today
            """),
            {'today': today_key}
        ).scalar() or 0
    
    return jsonify({
        'pending': pending,
        'completedToday': completed_today,
        'defectRate': f"{(defects_today / max(completed_today,1)) * 100:.1f}%"
    })


@quality_api.route('/inspection/batches')
@login_required
def inspection_batches():
    workcenter = request.args.get('workcenter', '')
    status = request.args.get('status', '')
    inspector = request.args.get('inspector', '')
    critical_only = request.args.get('critical_only', 'false') == 'true'
    
    query = """
        SELECT b.BatchID, b.BatchNumber, b.ProductID, b.Quantity, b.Status,
               b.InspectionDateKey, b.DueDateKey, b.WorkCenterID, b.InspectorID,
               CASE WHEN EXISTS (SELECT 1 FROM t_QualityOperationAssignment a
                       WHERE a.SourceType='Product' AND a.SourceID=b.ProductID AND a.IsCritical=1) 
                    THEN 1 ELSE 0 END AS HasCritical
        FROM t_QualityInspectionBatch b
        WHERE 1=1
    """
    params = {}
    if workcenter:
        query += " AND b.WorkCenterID = :wc"
        params['wc'] = workcenter
    if status:
        query += " AND b.Status = :status"
        params['status'] = status
    if inspector:
        query += " AND b.InspectorID = :inspector"
        params['inspector'] = inspector
    if critical_only:
        query += " AND HasCritical = 1"
    query += " ORDER BY b.DueDateKey, b.BatchNumber"
    
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
        batches = [dict(r._mapping) for r in rows]
    return jsonify(batches)


@quality_api.route('/inspection/batch/<int:batch_id>')
@login_required
def get_inspection_batch(batch_id):
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM t_QualityInspectionBatch WHERE BatchID = :bid"),
            {'bid': batch_id}
        ).first()
        if not row:
            return jsonify({'error': 'Batch not found'}), 404
        
        batch = dict(row._mapping)
        
        op_rows = conn.execute(
            text("""
                SELECT a.*, m.Code, m.Name, m.UnitOfMeasure,
                       CASE WHEN EXISTS (SELECT ResultID FROM t_QualityInspectionResult WHERE BatchID = :bid AND QCOperationID = a.QCOperationID) 
                            THEN 1 ELSE 0 END AS HasResult
                FROM t_QualityOperationAssignment a
                JOIN t_QualityOperationMaster m ON a.QCOperationID = m.DefinitionID
                WHERE a.SourceType = 'Product' AND a.SourceID = :pid AND a.IsActive = 1
            """),
            {'bid': batch_id, 'pid': batch['ProductID']}
        ).fetchall()
        operations = [dict(r._mapping) for r in op_rows]
        
        result_rows = conn.execute(
            text("""
                SELECT r.*, m.Code, m.Name
                FROM t_QualityInspectionResult r
                JOIN t_QualityOperationMaster m ON r.QCOperationID = m.DefinitionID
                WHERE r.BatchID = :bid
            """),
            {'bid': batch_id}
        ).fetchall()
        results = [dict(r._mapping) for r in result_rows]
    
    return jsonify({'batch': batch, 'operations': operations, 'results': results})


@quality_api.route('/inspection/result', methods=['POST'])
@login_required
def save_inspection_result():
    data = request.get_json()
    engine = get_engine()
    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT ResultID FROM t_QualityInspectionResult WHERE BatchID = :bid AND QCOperationID = :qcid"),
            {'bid': data['batchId'], 'qcid': data['operationId']}
        ).first()
        
        if existing:
            conn.execute(
                text("""
                    UPDATE t_QualityInspectionResult
                    SET SerialNumber = :sn, MeasuredValue = :mv, PassFail = :pf, InspectorNotes = :notes,
                        InspectedBy = :insp, InspectedDateKey = :date, DigitalSignature = :ds, SignedBy = :signed_by, SignedDateKey = :signed_date
                    WHERE ResultID = :rid
                """),
                {
                    'sn': data.get('serialNumber'),
                    'mv': data.get('measuredValue'),
                    'pf': data.get('passFail'),
                    'notes': data.get('notes'),
                    'insp': data.get('inspectedBy'),
                    'date': data.get('inspectedDateKey'),
                    'ds': data.get('digitalSignature'),
                    'signed_by': data.get('signedBy'),
                    'signed_date': data.get('signedDateKey'),
                    'rid': existing[0]
                }
            )
            result_id = existing[0]
        else:
            result = conn.execute(
                text("""
                    INSERT INTO t_QualityInspectionResult
                    (BatchID, QCOperationID, SerialNumber, MeasuredValue, PassFail, InspectorNotes,
                     InspectedBy, InspectedDateKey, DigitalSignature, SignedBy, SignedDateKey)
                    OUTPUT INSERTED.ResultID
                    VALUES (:bid, :qcid, :sn, :mv, :pf, :notes,
                            :insp, :date, :ds, :signed_by, :signed_date)
                """),
                {
                    'bid': data['batchId'],
                    'qcid': data['operationId'],
                    'sn': data.get('serialNumber'),
                    'mv': data.get('measuredValue'),
                    'pf': data.get('passFail'),
                    'notes': data.get('notes'),
                    'insp': data.get('inspectedBy'),
                    'date': data.get('inspectedDateKey'),
                    'ds': data.get('digitalSignature'),
                    'signed_by': data.get('signedBy'),
                    'signed_date': data.get('signedDateKey')
                }
            )
            result_id = result.scalar()
    
    return jsonify({'resultId': result_id, 'message': 'Result saved'})


@quality_api.route('/inspection/batch/<int:batch_id>/defect', methods=['POST'])
@login_required
def report_defect(batch_id):
    data = request.get_json()
    engine = get_engine()
    with engine.begin() as conn:
        batch = conn.execute(
            text("SELECT BatchNumber, ProductID FROM t_QualityInspectionBatch WHERE BatchID = :bid"),
            {'bid': batch_id}
        ).first()
        if not batch:
            return jsonify({'error': 'Batch not found'}), 404
        
        ncr_count = conn.execute(
            text("SELECT COUNT(*) FROM t_NonConformance")
        ).scalar() + 1
        ncr_number = f"NCR-{datetime.now().strftime('%Y%m')}-{ncr_count:04d}"
        
        conn.execute(
            text("""
                INSERT INTO t_NonConformance
                (NCRNumber, DateRaised, RaisedBy, SourceType, ProductID, BatchLotNumber, Quantity,
                 DefectCode, DefectDescription, Severity, Status, CreatedDate, ProjectID)
                VALUES (:ncr, :date, :raised, 'Inspection', :pid, :batch, :qty,
                        :code, :desc, :sev, 'Open', GETDATE(), :proj)
            """),
            {
                'ncr': ncr_number,
                'date': datetime.now().strftime('%Y-%m-%d'),
                'raised': data.get('raisedBy'),
                'pid': batch[1],
                'batch': batch[0],
                'qty': data.get('quantity'),
                'code': data.get('defectCode'),
                'desc': data.get('defectDescription'),
                'sev': data.get('severity'),
                'proj': data.get('projectId')
            }
        )
        ncr_id = conn.execute(
            text("SELECT SCOPE_IDENTITY()")
        ).scalar()
    
    return jsonify({'ncrId': ncr_id, 'ncrNumber': ncr_number, 'message': 'Defect reported'})


@quality_api.route('/inspection/batch/<int:batch_id>/signoff', methods=['POST'])
@login_required
def batch_sign_off(batch_id):
    data = request.get_json()
    engine = get_engine()
    with engine.begin() as conn:
        pending = conn.execute(
            text("""
                SELECT COUNT(*) FROM t_QualityOperationAssignment a
                WHERE a.SourceType='Product' AND a.SourceID = (SELECT ProductID FROM t_QualityInspectionBatch WHERE BatchID=:bid)
                AND NOT EXISTS (SELECT 1 FROM t_QualityInspectionResult r WHERE r.BatchID=:bid AND r.QCOperationID=a.QCOperationID AND r.PassFail=1)
            """),
            {'bid': batch_id}
        ).scalar()
        if pending > 0:
            return jsonify({'error': f'{pending} operations not passed.'}), 400
        
        conn.execute(
            text("""
                UPDATE t_QualityInspectionBatch
                SET Status = 'Completed', OverallResult = 'Pass', InspectionDateKey = :date, InspectorID = :insp
                WHERE BatchID = :bid
            """),
            {
                'date': data.get('inspectionDateKey'),
                'insp': data.get('inspectorId'),
                'bid': batch_id
            }
        )
    return jsonify({'message': 'Batch signed off successfully'})


@quality_api.route('/inspection/workcenters')
@login_required
def inspection_workcenters():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT DISTINCT WorkCenterID FROM t_QualityInspectionBatch WHERE WorkCenterID IS NOT NULL")
        ).fetchall()
    return jsonify([{'id': r[0], 'name': r[0]} for r in rows])


@quality_api.route('/inspection/inspectors')
@login_required
def inspection_inspectors():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT UserID, FullName FROM t_Users WHERE UserRoles LIKE '%INSPECTOR%'")
        ).fetchall()
    return jsonify([{'id': r[0], 'name': r[1]} for r in rows])


@quality_api.route('/inspection/batch/<int:batch_id>/attachments', methods=['GET', 'POST'])
@login_required
def batch_attachments(batch_id):
    if request.method == 'GET':
        engine = get_engine()
        with engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT DocumentID, DocumentNumber, Title, FileName, CreatedDateKey
                    FROM t_EngineeringDocument
                    WHERE EngineeringEntityType = 'InspectionBatch' AND EngineeringEntityID = :bid
                """),
                {'bid': batch_id}
            ).fetchall()
        return jsonify([{'id': r[0], 'number': r[1], 'title': r[2], 'filename': r[3], 'dateKey': r[4]} for r in rows])
    else:
        return jsonify({'message': 'Attachment upload not yet implemented'}), 501


# ========== 6. NCR REGISTER API ==========
@quality_api.route('/ncr/kpi')
@login_required
def ncr_kpi():
    engine = get_engine()
    with engine.connect() as conn:
        open_count = conn.execute(
            text("SELECT COUNT(*) FROM t_NonConformance WHERE Status='Open'")
        ).scalar() or 0
        closed_count = conn.execute(
            text("SELECT COUNT(*) FROM t_NonConformance WHERE Status='Closed'")
        ).scalar() or 0
        severity_rows = conn.execute(
            text("SELECT Severity, COUNT(*) FROM t_NonConformance GROUP BY Severity")
        ).fetchall()
        unsigned_critical = conn.execute(
            text("SELECT COUNT(*) FROM t_NonConformance WHERE Severity='Critical' AND DispositionSignedBy IS NULL")
        ).scalar() or 0
    
    severity_counts = {'Critical': 0, 'Major': 0, 'Minor': 0}
    for row in severity_rows:
        severity_counts[row[0]] = row[1]
    
    return jsonify({
        'open': open_count,
        'closed': closed_count,
        'critical': severity_counts.get('Critical', 0),
        'major': severity_counts.get('Major', 0),
        'minor': severity_counts.get('Minor', 0),
        'unsignedCritical': unsigned_critical
    })


@quality_api.route('/ncr/list')
@login_required
def ncr_list():
    severity = request.args.get('severity', '')
    status = request.args.get('status', '')
    source = request.args.get('source', '')
    signature_status = request.args.get('signature_status', '')
    
    query = """
        SELECT n.NCRID, n.NCRNumber, n.SourceType, n.ProductID, n.Severity, n.Status,
               CASE WHEN n.DispositionSignedBy IS NOT NULL THEN 'Yes' ELSE 'No' END AS DispositionSigned,
               n.DateRaised, n.DefectDescription
        FROM t_NonConformance n
        WHERE 1=1
    """
    params = {}
    if severity:
        query += " AND n.Severity = :sev"
        params['sev'] = severity
    if status:
        query += " AND n.Status = :stat"
        params['stat'] = status
    if source:
        query += " AND n.SourceType = :src"
        params['src'] = source
    if signature_status == 'signed':
        query += " AND n.DispositionSignedBy IS NOT NULL"
    elif signature_status == 'unsigned':
        query += " AND n.DispositionSignedBy IS NULL"
    query += " ORDER BY n.DateRaised DESC"
    
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
        ncrs = [dict(r._mapping) for r in rows]
    return jsonify(ncrs)


@quality_api.route('/ncr/<int:ncr_id>')
@login_required
def get_ncr(ncr_id):
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM t_NonConformance WHERE NCRID = :nid"),
            {'nid': ncr_id}
        ).first()
        if not row:
            return jsonify({'error': 'NCR not found'}), 404
        
        ncr = dict(row._mapping)
        
        action_rows = conn.execute(
            text("""
                SELECT ActionID, ActionNumber, ActionType, Description, Status, DueDateKey
                FROM t_QualityAction
                WHERE SourceType = 'NonConformance' AND SourceID = :nid
            """),
            {'nid': ncr_id}
        ).fetchall()
        actions = [dict(r._mapping) for r in action_rows]
    return jsonify({'ncr': ncr, 'actions': actions})


@quality_api.route('/ncr', methods=['POST'])
@login_required
def create_ncr():
    data = request.get_json()
    engine = get_engine()
    with engine.begin() as conn:
        ncr_count = conn.execute(
            text("SELECT COUNT(*) FROM t_NonConformance")
        ).scalar() + 1
        ncr_number = f"NCR-{datetime.now().strftime('%Y%m')}-{ncr_count:04d}"
        date_raised = datetime.now().strftime('%Y-%m-%d')
        
        conn.execute(
            text("""
                INSERT INTO t_NonConformance
                (NCRNumber, DateRaised, RaisedBy, SourceType, ProductID, BatchLotNumber, Quantity,
                 DefectCode, DefectDescription, Severity, Status, CreatedDate, ProjectID)
                VALUES (:ncr, :date, :raised, :stype, :pid, :batch, :qty,
                        :code, :desc, :sev, 'Open', GETDATE(), :proj)
            """),
            {
                'ncr': ncr_number,
                'date': date_raised,
                'raised': current_user.get_id(),
                'stype': data['sourceType'],
                'pid': data.get('productId'),
                'batch': data.get('batchNumber'),
                'qty': data.get('quantity'),
                'code': data.get('defectCode'),
                'desc': data.get('description'),
                'sev': data.get('severity'),
                'proj': data.get('projectId')
            }
        )
        ncr_id = conn.execute(
            text("SELECT SCOPE_IDENTITY()")
        ).scalar()
    return jsonify({'ncrId': ncr_id, 'ncrNumber': ncr_number, 'message': 'NCR raised'}), 201


@quality_api.route('/ncr/<int:ncr_id>/close', methods=['POST'])
@login_required
def close_ncr(ncr_id):
    data = request.get_json()
    date_closed = datetime.now().strftime('%Y-%m-%d')
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE t_NonConformance
                SET Status = 'Closed', ClosedDate = :date
                WHERE NCRID = :nid
            """),
            {'date': date_closed, 'nid': ncr_id}
        )
    return jsonify({'message': 'NCR closed'})


@quality_api.route('/ncr/sources')
@login_required
def ncr_sources():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT DISTINCT SourceType FROM t_NonConformance ORDER BY SourceType")
        ).fetchall()
    return jsonify([{'source': r[0]} for r in rows])


@quality_api.route('/ncr/products')
@login_required
def ncr_products():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT DISTINCT ProductID FROM t_NonConformance WHERE ProductID IS NOT NULL ORDER BY ProductID")
        ).fetchall()
    return jsonify([{'id': r[0]} for r in rows])


@quality_api.route('/ncr/export')
@login_required
def export_ncr():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT NCRNumber, SourceType, ProductID, Severity, Status,
                       CASE WHEN DispositionSignedBy IS NOT NULL THEN 'Yes' ELSE 'No' END AS Signed,
                       DateRaised, DefectDescription
                FROM t_NonConformance
                ORDER BY DateRaised DESC
            """)
        ).fetchall()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['NCR Number', 'Source', 'Product', 'Severity', 'Status', 'Signed', 'Date Raised', 'Description'])
    for r in rows:
        writer.writerow([r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7]])
    return output.getvalue(), 200, {'Content-Type': 'text/csv', 'Content-Disposition': 'attachment; filename=ncr_register.csv'}


# ========== 7. CAPA API ==========
@quality_api.route('/capa/kpi')
@login_required
def capa_kpi():
    engine = get_engine()
    today_key = _today_key()
    with engine.connect() as conn:
        open_actions = conn.execute(
            text("SELECT COUNT(*) FROM t_QualityAction WHERE Status NOT IN ('Closed', 'Completed')")
        ).scalar() or 0
        overdue = conn.execute(
            text("SELECT COUNT(*) FROM t_QualityAction WHERE Status NOT IN ('Closed', 'Completed') AND DueDateKey < :today"),
            {'today': today_key}
        ).scalar() or 0
        completed = conn.execute(
            text("SELECT COUNT(*) FROM t_QualityAction WHERE Status IN ('Closed', 'Completed')")
        ).scalar() or 0
    return jsonify({'open': open_actions, 'overdue': overdue, 'completed': completed})


@quality_api.route('/capa/list')
@login_required
def capa_list():
    action_type = request.args.get('type', '')
    status = request.args.get('status', '')
    assignee = request.args.get('assignee', '')
    
    query = """
        SELECT a.ActionID, a.ActionNumber, a.ActionType, a.SourceType, a.SourceID,
               a.Description, a.AssignedTo, a.DueDateKey, a.Status, a.CompletionDateKey,
               a.EffectivenessCheck, u.FullName AS AssigneeName
        FROM t_QualityAction a
        LEFT JOIN t_Users u ON a.AssignedTo = u.UserID
        WHERE 1=1
    """
    params = {}
    if action_type:
        query += " AND a.ActionType = :atype"
        params['atype'] = action_type
    if status:
        query += " AND a.Status = :stat"
        params['stat'] = status
    if assignee:
        query += " AND a.AssignedTo = :assign"
        params['assign'] = assignee
    query += " ORDER BY a.DueDateKey, a.ActionID"
    
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
        actions = [dict(r._mapping) for r in rows]
    return jsonify(actions)


@quality_api.route('/capa/action', methods=['POST'])
@login_required
def create_capa_action():
    data = request.get_json()
    engine = get_engine()
    with engine.begin() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM t_QualityAction")
        ).scalar() + 1
        action_number = f"CAPA-{datetime.now().strftime('%Y%m')}-{count:04d}"
        
        result = conn.execute(
            text("""
                INSERT INTO t_QualityAction
                (ActionNumber, ActionType, SourceType, SourceID, Description, AssignedTo, DueDateKey, Status)
                OUTPUT INSERTED.ActionID
                VALUES (:num, :atype, :stype, :sid, :desc, :assign, :due, 'Open')
            """),
            {
                'num': action_number,
                'atype': data['actionType'],
                'stype': data['sourceType'],
                'sid': data.get('sourceID'),
                'desc': data['description'],
                'assign': data.get('assignedTo'),
                'due': data.get('dueDateKey')
            }
        )
        action_id = result.scalar()
    return jsonify({'actionId': action_id, 'actionNumber': action_number, 'message': 'Action created'}), 201


@quality_api.route('/capa/action/<int:action_id>/verify', methods=['POST'])
@login_required
def verify_capa_action(action_id):
    data = request.get_json()
    effectiveness = data.get('effectivenessCheck')
    completed_date_key = _today_key()
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE t_QualityAction
                SET EffectivenessCheck = :eff, Status = 'Completed', CompletionDateKey = :date, ClosedBy = :user
                WHERE ActionID = :aid
            """),
            {
                'eff': effectiveness,
                'date': completed_date_key,
                'user': current_user.get_id(),
                'aid': action_id
            }
        )
    return jsonify({'message': 'Action verified and closed'})


@quality_api.route('/capa/action/<int:action_id>', methods=['GET'])
@login_required
def get_capa_action(action_id):
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM t_QualityAction WHERE ActionID = :aid"),
            {'aid': action_id}
        ).first()
        if not row:
            return jsonify({'error': 'Not found'}), 404
    return jsonify(dict(row._mapping))


@quality_api.route('/capa/types')
@login_required
def capa_types():
    return jsonify([{'type': 'Corrective'}, {'type': 'Preventive'}])


@quality_api.route('/capa/statuses')
@login_required
def capa_statuses():
    return jsonify([{'status': 'Open'}, {'status': 'InProgress'}, {'status': 'Completed'}])


@quality_api.route('/capa/assignees')
@login_required
def capa_assignees():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT UserID, FullName FROM t_Users WHERE UserType='Internal' ORDER BY FullName")
        ).fetchall()
    return jsonify([{'id': r[0], 'name': r[1]} for r in rows])


@quality_api.route('/capa/export')
@login_required
def export_capa():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT a.ActionNumber, a.ActionType, a.SourceType, a.SourceID, a.Description,
                       u.FullName AS Assignee, a.DueDateKey, a.Status, a.CompletionDateKey, a.EffectivenessCheck
                FROM t_QualityAction a
                LEFT JOIN t_Users u ON a.AssignedTo = u.UserID
                ORDER BY a.DueDateKey
            """)
        ).fetchall()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Action #', 'Type', 'Source Type', 'Source ID', 'Description', 'Assignee', 'Due Date', 'Status', 'Completion Date', 'Effectiveness'])
    for r in rows:
        writer.writerow(r)
    return output.getvalue(), 200, {'Content-Type': 'text/csv', 'Content-Disposition': 'attachment; filename=capa_report.csv'}


# ========== 8. CERTIFICATE MANAGEMENT API ==========
@quality_api.route('/certificates/kpi')
@login_required
def certificates_kpi():
    engine = get_engine()
    today_key = _today_key()
    first_day = int(datetime.now().replace(day=1).strftime('%Y%m%d'))
    with engine.connect() as conn:
        generated_this_month = conn.execute(
            text("SELECT COUNT(*) FROM t_EngineeringDocument WHERE DocumentType='COC' AND CreatedDateKey BETWEEN :first AND :today"),
            {'first': first_day, 'today': today_key}
        ).scalar() or 0
        pending_signatures = conn.execute(
            text("SELECT COUNT(*) FROM t_EngineeringDocument WHERE DocumentType='COC' AND Status='Draft'")
        ).scalar() or 0
    return jsonify({
        'generatedThisMonth': generated_this_month,
        'pendingSignatures': pending_signatures
    })


@quality_api.route('/certificates/list')
@login_required
def certificates_list():
    product = request.args.get('product', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    status = request.args.get('status', '')
    
    query = """
        SELECT d.DocumentID, d.DocumentNumber, d.Title, d.CreatedDateKey, d.Status,
               d.Notes, d.ProjectID, d.EngineeringEntityID
        FROM t_EngineeringDocument d
        WHERE d.DocumentType = 'COC'
    """
    params = {}
    if product:
        query += " AND d.Title LIKE :product"
        params['product'] = f'%{product}%'
    if date_from:
        query += " AND d.CreatedDateKey >= :from"
        params['from'] = int(date_from.replace('-', ''))
    if date_to:
        query += " AND d.CreatedDateKey <= :to"
        params['to'] = int(date_to.replace('-', ''))
    if status:
        query += " AND d.Status = :stat"
        params['stat'] = status
    query += " ORDER BY d.CreatedDateKey DESC"
    
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
        certs = []
        for r in rows:
            serials = []
            try:
                import json
                serials = json.loads(r[5]) if r[5] else []
            except:
                serials = []
            certs.append({
                'id': r[0],
                'number': r[1],
                'title': r[2],
                'product': r[2],
                'serialNumbers': serials,
                'generatedDateKey': r[3],
                'status': r[4]
            })
    return jsonify(certs)


@quality_api.route('/certificates/generate', methods=['POST'])
@login_required
def generate_certificate():
    data = request.get_json()
    serial_numbers = data.get('serialNumbers', [])
    product_id = data.get('productId')
    project_id = data.get('projectId')
    
    if not serial_numbers or not product_id:
        return jsonify({'error': 'Serial numbers and product ID required'}), 400
    
    engine = get_engine()
    with engine.begin() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM t_EngineeringDocument WHERE DocumentType='COC'")
        ).scalar() + 1
        cert_number = f"COC-{datetime.now().strftime('%Y%m')}-{count:04d}"
        
        product = conn.execute(
            text("SELECT descEnglish FROM t_Product WHERE ProductId = :pid"),
            {'pid': product_id}
        ).first()
        product_desc = product[0] if product else product_id
        title = f"Certificate of Conformance - {product_desc}"
        
        import json
        serials_json = json.dumps(serial_numbers)
        today_key = _today_key()
        
        result = conn.execute(
            text("""
                INSERT INTO t_EngineeringDocument
                (DocumentNumber, DocumentType, Title, Status, CreatedDateKey, Notes, ProjectID, EngineeringEntityType, EngineeringEntityID)
                OUTPUT INSERTED.DocumentID
                VALUES (:num, 'COC', :title, 'Draft', :date, :notes, :proj, 'Certificate', :id)
            """),
            {
                'num': cert_number,
                'title': title,
                'date': today_key,
                'notes': serials_json,
                'proj': project_id,
                'id': count
            }
        )
        cert_id = result.scalar()
    
    return jsonify({
        'certificateId': cert_id,
        'certificateNumber': cert_number,
        'message': 'Certificate generated',
        'viewUrl': f'/api/quality/certificates/{cert_id}/view'
    })


@quality_api.route('/certificates/<int:cert_id>/view')
@login_required
def view_certificate(cert_id):
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM t_EngineeringDocument WHERE DocumentID = :id AND DocumentType='COC'"),
            {'id': cert_id}
        ).first()
        if not row:
            return "Certificate not found", 404
        
        cert = dict(row._mapping)
        
        import json
        serials = json.loads(cert['Notes']) if cert['Notes'] else []
        product_desc = cert['Title'].replace("Certificate of Conformance - ", "")
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Certificate {cert['DocumentNumber']}</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 40px; }}
            .header {{ text-align: center; margin-bottom: 30px; }}
            .content {{ margin: 20px 0; }}
            .footer {{ margin-top: 50px; text-align: center; font-size: 12px; }}
            table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
            th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
            th {{ background-color: #f2f2f2; }}
            .signature {{ margin-top: 40px; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>Certificate of Conformance</h1>
            <h3>Certificate No: {cert['DocumentNumber']}</h3>
            <p>Date: {str(cert['CreatedDateKey'])[:4]}-{str(cert['CreatedDateKey'])[4:6]}-{str(cert['CreatedDateKey'])[6:8]}</p>
        </div>
        <div class="content">
            <p>This is to certify that the following product(s) have been inspected and conform to the applicable specifications and requirements.</p>
            <p><strong>Product:</strong> {product_desc}</p>
            <p><strong>Serial Numbers:</strong></p>
            <table>
                <tr><th>#</th><th>Serial Number</th><th>Status</th></tr>
                {''.join([f"<tr><td>{i+1}</td><td>{sn}</td><td>Passed</td></tr>" for i, sn in enumerate(serials)])}
            </table>
            <p>The inspected items meet all quality criteria and are released for shipment.</p>
        </div>
        <div class="signature">
            <p>Authorized Signature: ________________________</p>
            <p>Quality Manager</p>
        </div>
        <div class="footer">
            <p>This certificate is generated electronically and is valid without signature.</p>
        </div>
        <script>window.onload = function() {{ window.print(); }}</script>
    </body>
    </html>
    """
    return html, 200, {'Content-Type': 'text/html'}


@quality_api.route('/certificates/<int:cert_id>/status', methods=['PUT'])
@login_required
def update_certificate_status(cert_id):
    data = request.get_json()
    status = data.get('status')
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE t_EngineeringDocument SET Status = :stat WHERE DocumentID = :id"),
            {'stat': status, 'id': cert_id}
        )
    return jsonify({'message': f'Status updated to {status}'})


@quality_api.route('/certificates/products')
@login_required
def certificate_products():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT DISTINCT ProductId, descEnglish FROM t_Product ORDER BY ProductId")
        ).fetchall()
    return jsonify([{'id': r[0], 'name': r[1]} for r in rows])


@quality_api.route('/certificates/serial-numbers/<product_id>')
@login_required
def certificate_serial_numbers(product_id):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT s.SerialNumber, s.Status
                FROM t_SerialNumbers s
                WHERE s.ProductID = :pid AND s.Status = 'InProduction' 
                UNION
                SELECT s.SerialNumber, s.Status
                FROM t_SerialNumbers s
                JOIN t_QualityInspectionResult r ON r.SerialNumber = s.SerialNumber
                WHERE s.ProductID = :pid AND r.PassFail = 1
                GROUP BY s.SerialNumber, s.Status
            """),
            {'pid': product_id}
        ).fetchall()
    return jsonify([{'serialNumber': r[0], 'status': r[1]} for r in rows])


# ========== 9. AUDIT MANAGEMENT API ==========
@quality_api.route('/audit/kpi')
@login_required
def audit_kpi():
    engine = get_engine()
    with engine.connect() as conn:
        planned = conn.execute(
            text("SELECT COUNT(*) FROM t_QualityAudit WHERE Status='Planned'")
        ).scalar() or 0
        in_progress = conn.execute(
            text("SELECT COUNT(*) FROM t_QualityAudit WHERE Status='InProgress'")
        ).scalar() or 0
        completed = conn.execute(
            text("SELECT COUNT(*) FROM t_QualityAudit WHERE Status='Completed'")
        ).scalar() or 0
    return jsonify({'planned': planned, 'inProgress': in_progress, 'completed': completed})


@quality_api.route('/audit/list')
@login_required
def audit_list():
    audit_type = request.args.get('type', '')
    status = request.args.get('status', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    
    query = """
        SELECT a.AuditID, a.AuditNumber, a.AuditType, a.AuditDateKey, a.Scope, a.Status, a.Findings,
               u.FullName AS AuditorName
        FROM t_QualityAudit a
        LEFT JOIN t_Users u ON a.AuditorID = u.UserID
        WHERE 1=1
    """
    params = {}
    if audit_type:
        query += " AND a.AuditType = :atype"
        params['atype'] = audit_type
    if status:
        query += " AND a.Status = :stat"
        params['stat'] = status
    if date_from:
        query += " AND a.AuditDateKey >= :from"
        params['from'] = int(date_from.replace('-', ''))
    if date_to:
        query += " AND a.AuditDateKey <= :to"
        params['to'] = int(date_to.replace('-', ''))
    query += " ORDER BY a.AuditDateKey DESC"
    
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
        audits = [dict(r._mapping) for r in rows]
    return jsonify(audits)


@quality_api.route('/audit', methods=['POST'])
@login_required
def create_audit():
    data = request.get_json()
    engine = get_engine()
    with engine.begin() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM t_QualityAudit")
        ).scalar() + 1
        audit_number = f"AUD-{datetime.now().strftime('%Y%m')}-{count:04d}"
        today_key = _today_key()
        
        result = conn.execute(
            text("""
                INSERT INTO t_QualityAudit
                (AuditNumber, AuditType, AuditDateKey, Scope, Status, CreatedBy, CreatedDateKey, ModifiedDateKey, AuditorID)
                OUTPUT INSERTED.AuditID
                VALUES (:num, :atype, :date, :scope, 'Planned', :user, :created, :modified, :auditor)
            """),
            {
                'num': audit_number,
                'atype': data['auditType'],
                'date': data['auditDateKey'],
                'scope': data['scope'],
                'user': current_user.get_id(),
                'created': today_key,
                'modified': today_key,
                'auditor': data.get('auditorId')
            }
        )
        audit_id = result.scalar()
    return jsonify({'auditId': audit_id, 'auditNumber': audit_number}), 201


@quality_api.route('/audit/<int:audit_id>', methods=['GET'])
@login_required
def get_audit(audit_id):
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM t_QualityAudit WHERE AuditID = :aid"),
            {'aid': audit_id}
        ).first()
        if not row:
            return jsonify({'error': 'Not found'}), 404
    return jsonify(dict(row._mapping))


@quality_api.route('/audit/<int:audit_id>/conduct', methods=['POST'])
@login_required
def conduct_audit(audit_id):
    data = request.get_json()
    findings = data.get('findings')
    status = data.get('status')
    report_doc_id = data.get('reportDocumentId')
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE t_QualityAudit
                SET Status = :stat, Findings = :findings, ReportDocumentID = :doc, ModifiedDateKey = :date
                WHERE AuditID = :aid
            """),
            {
                'stat': status,
                'findings': findings,
                'doc': report_doc_id,
                'date': _today_key(),
                'aid': audit_id
            }
        )
    return jsonify({'message': f'Audit updated to {status}'})


@quality_api.route('/audit/types')
@login_required
def audit_types():
    return jsonify([{'type': 'Internal'}, {'type': 'External'}, {'type': 'Supplier'}])


@quality_api.route('/audit/statuses')
@login_required
def audit_statuses():
    return jsonify([{'status': 'Planned'}, {'status': 'InProgress'}, {'status': 'Completed'}])


@quality_api.route('/audit/auditors')
@login_required
def audit_auditors():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT UserID, FullName FROM t_Users WHERE UserType='Internal' ORDER BY FullName")
        ).fetchall()
    return jsonify([{'id': r[0], 'name': r[1]} for r in rows])


@quality_api.route('/audit/export')
@login_required
def export_audit():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT a.AuditNumber, a.AuditType, a.AuditDateKey, a.Scope, a.Status, a.Findings, u.FullName AS Auditor
                FROM t_QualityAudit a
                LEFT JOIN t_Users u ON a.AuditorID = u.UserID
                ORDER BY a.AuditDateKey DESC
            """)
        ).fetchall()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Audit #', 'Type', 'Date', 'Scope', 'Status', 'Findings', 'Auditor'])
    for r in rows:
        writer.writerow(r)
    return output.getvalue(), 200, {'Content-Type': 'text/csv', 'Content-Disposition': 'attachment; filename=audit_report.csv'}


# ========== 10. CALIBRATION MANAGEMENT API ==========
@quality_api.route('/calibration/kpi')
@login_required
def calibration_kpi():
    engine = get_engine()
    today_str = datetime.now().strftime('%Y-%m-%d')
    first_day = datetime.now().replace(day=1).strftime('%Y-%m-%d')
    next_month = datetime.now().replace(day=28) + timedelta(days=4)
    last_day = next_month.replace(day=1) - timedelta(days=1)
    
    with engine.connect() as conn:
        overdue = conn.execute(
            text("SELECT COUNT(*) FROM t_EquipmentMaintenance WHERE MaintenanceType='Calibration' AND Status='Scheduled' AND NextMaintenanceDate < :today"),
            {'today': today_str}
        ).scalar() or 0
        
        due_this_month = conn.execute(
            text("SELECT COUNT(*) FROM t_EquipmentMaintenance WHERE MaintenanceType='Calibration' AND NextMaintenanceDate BETWEEN :first AND :last"),
            {'first': first_day, 'last': last_day.strftime('%Y-%m-%d')}
        ).scalar() or 0
        
        calibrated = conn.execute(
            text("SELECT COUNT(*) FROM t_EquipmentMaintenance WHERE MaintenanceType='Calibration' AND Status='Completed'")
        ).scalar() or 0
    
    return jsonify({'overdue': overdue, 'dueThisMonth': due_this_month, 'calibrated': calibrated})


@quality_api.route('/calibration/list')
@login_required
def calibration_list():
    equip_type = request.args.get('type', '')
    status = request.args.get('status', '')
    due_from = request.args.get('due_from', '')
    due_to = request.args.get('due_to', '')
    
    query = """
        SELECT eq.EquipmentID, eq.EquipmentName, eq.MaintenanceType, eq.LastMaintenanceDate, eq.NextMaintenanceDate,
               eq.Status, eq.Notes
        FROM t_EquipmentMaintenance eq
        WHERE eq.MaintenanceType = 'Calibration'
    """
    params = {}
    if equip_type:
        query += " AND eq.EquipmentName LIKE :type"
        params['type'] = f'%{equip_type}%'
    if status:
        query += " AND eq.Status = :stat"
        params['stat'] = status
    if due_from:
        query += " AND eq.NextMaintenanceDate >= :from"
        params['from'] = due_from
    if due_to:
        query += " AND eq.NextMaintenanceDate <= :to"
        params['to'] = due_to
    query += " ORDER BY eq.NextMaintenanceDate"
    
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
        equipment = []
        for r in rows:
            equipment.append({
                'id': r[0],
                'name': r[1],
                'type': r[2],
                'lastCalibration': r[3],
                'nextDue': r[4],
                'status': r[5],
                'notes': r[6]
            })
    return jsonify(equipment)


@quality_api.route('/calibration/equipment', methods=['POST'])
@login_required
def create_equipment():
    data = request.get_json()
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(
            text("""
                INSERT INTO t_EquipmentMaintenance
                (EquipmentID, EquipmentName, MaintenanceType, LastMaintenanceDate, NextMaintenanceDate, Status, Notes)
                OUTPUT INSERTED.EquipmentID
                VALUES (:id, :name, 'Calibration', :last, :next, 'Scheduled', :notes)
            """),
            {
                'id': data['equipmentId'],
                'name': data['equipmentName'],
                'last': data.get('lastCalibration'),
                'next': data.get('nextDue'),
                'notes': data.get('notes')
            }
        )
        eq_id = result.scalar()
    return jsonify({'equipmentId': eq_id, 'message': 'Equipment added'}), 201


@quality_api.route('/calibration/record', methods=['POST'])
@login_required
def record_calibration():
    data = request.get_json()
    eq_id = data['equipmentId']
    last_date = data['lastCalibration']
    next_date = data['nextDue']
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE t_EquipmentMaintenance
                SET LastMaintenanceDate = :last, NextMaintenanceDate = :next, Status = 'Completed'
                WHERE EquipmentID = :id AND MaintenanceType = 'Calibration'
            """),
            {'last': last_date, 'next': next_date, 'id': eq_id}
        )
    return jsonify({'message': 'Calibration recorded'})


@quality_api.route('/calibration/<eq_id>/out-of-tolerance', methods=['POST'])
@login_required
def out_of_tolerance(eq_id):
    data = request.get_json()
    action = data.get('action')
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE t_EquipmentMaintenance
                SET Status = 'Out of Tolerance', Notes = :notes
                WHERE EquipmentID = :id AND MaintenanceType = 'Calibration'
            """),
            {'notes': action, 'id': eq_id}
        )
    return jsonify({'message': 'Out of tolerance action recorded'})


@quality_api.route('/calibration/types')
@login_required
def calibration_types():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT DISTINCT EquipmentName FROM t_EquipmentMaintenance WHERE MaintenanceType='Calibration' AND EquipmentName IS NOT NULL")
        ).fetchall()
    return jsonify([{'type': r[0]} for r in rows])


@quality_api.route('/calibration/statuses')
@login_required
def calibration_statuses():
    return jsonify([{'status': 'Scheduled'}, {'status': 'Completed'}, {'status': 'Out of Tolerance'}])


@quality_api.route('/calibration/export')
@login_required
def export_calibration():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT EquipmentName, LastMaintenanceDate, NextMaintenanceDate, Status, Notes
                FROM t_EquipmentMaintenance
                WHERE MaintenanceType='Calibration'
                ORDER BY NextMaintenanceDate
            """)
        ).fetchall()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Equipment', 'Last Calibration', 'Next Due', 'Status', 'Notes'])
    for r in rows:
        writer.writerow(r)
    return output.getvalue(), 200, {'Content-Type': 'text/csv', 'Disposition': 'attachment; filename=calibration.csv'}