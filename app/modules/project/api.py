from flask import Blueprint, request, jsonify, current_app, Response
from sqlalchemy.sql import text
from flask_login import login_required, current_user
import csv
import io
import json
import traceback
import datetime
import xml.etree.ElementTree as ET
from datetime import datetime

api_bp = Blueprint('project_api', __name__, url_prefix='/api/project')

def get_engine():
    return current_app.extensions['sqlalchemy'].engine

# ==================== HELPER: CAST NUMERIC ====================
def cast_numeric(column):
    """Return SQL Server compatible CAST for numeric operations."""
    return f"CAST({column} AS DECIMAL(18,4))"

def is_active_true(value):
    """
    Interpret an NVARCHAR 'boolean' column value.
    Accepts '1', 'true', 'True', 1, True as True; anything else False.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    return str(value).strip().lower() in ('1', 'true', 'yes')

# ==================== PORTFOLIO DATA ====================
@api_bp.route('/data')
def portfolio_data():
    engine = get_engine()
    status = request.args.get('status', '')
    project_manager = request.args.get('project_manager', '')
    project_type = request.args.get('project_type', '')
    won_lost = request.args.get('won_lost', 'all')
    start_date = request.args.get('start_date_key', '')
    end_date = request.args.get('end_date_key', '')
    project_id = request.args.get('project_id', 'All')
    
    print(f"DEBUG: Received project_id = {project_id}")
    
    try:
        with engine.connect() as conn:
            conditions = []
            params = {}

            if status:
                conditions.append("p.Status = :status")
                params['status'] = status
            if project_manager:
                conditions.append("p.ProjectManager = :pm")
                params['pm'] = project_manager
            if project_type:
                conditions.append("p.ProjectType = :ptype")
                params['ptype'] = project_type
            if won_lost == 'won':
                conditions.append("(p.WinLossReason IS NOT NULL AND p.WinLossReason != '')")
            elif won_lost == 'lost':
                conditions.append("(p.WinLossReason IS NOT NULL AND p.WinLossReason != '') OR p.Status = 'Lost'")
            if start_date and start_date.strip():
                conditions.append("p.StartDateKey >= :start_key")
                params['start_key'] = int(start_date)
            if end_date and end_date.strip():
                conditions.append("p.EndDateKey <= :end_key")
                params['end_key'] = int(end_date)
            if project_id and project_id != 'All':
                conditions.append("p.ProjectID = :proj_id")
                params['proj_id'] = project_id

            where_sql = " AND ".join(conditions) if conditions else "1=1"

            query = text(f"""
                SELECT
                    p.ProjectID,
                    p.ProjectName,
                    '' as CustomerName,
                    p.Status,
                    p.WinLossReason,
                    p.PercentComplete,
                    p.ProjectManager,
                    p.ProjectType,
                    ISNULL(CAST((
                        SELECT TOP 1 CommittedCost 
                        FROM t_ProjectFinancials pf 
                        WHERE pf.ProjectID = p.ProjectID
                    ) AS DECIMAL(18,4)), 0) as Value,
                    u.FullName as CreatedByName
                FROM t_Project p
                LEFT JOIN t_Users u ON p.CreatedByUserID = u.UserID
                WHERE {where_sql}
                ORDER BY p.ProjectID
            """)
            rows = conn.execute(query, params).fetchall()

            # KPIs with CAST for numeric columns
            if project_id and project_id != 'All':
                active = conn.execute(text("SELECT COUNT(*) FROM t_Project WHERE Status = 'Active' AND ProjectID = :pid"), {'pid': project_id}).scalar()
                on_hold = conn.execute(text("SELECT COUNT(*) FROM t_Project WHERE Status = 'On Hold' AND ProjectID = :pid"), {'pid': project_id}).scalar()
                completed = conn.execute(text("SELECT COUNT(*) FROM t_Project WHERE Status = 'Completed' AND ProjectID = :pid"), {'pid': project_id}).scalar()
                total_value = conn.execute(text("SELECT ISNULL(SUM(CAST(CommittedCost AS DECIMAL(18,4))),0) FROM t_ProjectFinancials WHERE ProjectID = :pid"), {'pid': project_id}).scalar()
                lost_count = conn.execute(text("SELECT COUNT(*) FROM t_Project WHERE (WinLossReason IS NOT NULL OR Status = 'Lost') AND ProjectID = :pid"), {'pid': project_id}).scalar()
            else:
                active = conn.execute(text("SELECT COUNT(*) FROM t_Project WHERE Status = 'Active'")).scalar()
                on_hold = conn.execute(text("SELECT COUNT(*) FROM t_Project WHERE Status = 'On Hold'")).scalar()
                completed = conn.execute(text("SELECT COUNT(*) FROM t_Project WHERE Status = 'Completed'")).scalar()
                total_value = conn.execute(text("SELECT ISNULL(SUM(CAST(CommittedCost AS DECIMAL(18,4))),0) FROM t_ProjectFinancials")).scalar()
                lost_count = conn.execute(text("SELECT COUNT(*) FROM t_Project WHERE WinLossReason IS NOT NULL OR Status = 'Lost'")).scalar()

            projects = []
            for row in rows:
                projects.append({
                    'project_id': row[0],
                    'project_name': row[1],
                    'customer': row[2],
                    'status': row[3],
                    'winloss_reason': row[4] or '',
                    'percent_complete': float(row[5] if row[5] else 0),
                    'project_manager': row[6] or '',
                    'project_type': row[7] or '',
                    'value': float(row[8] if row[8] else 0),
                    'created_by': row[9] or ''
                })

            return jsonify({
                'kpis': {
                    'active': active,
                    'on_hold': on_hold,
                    'completed': completed,
                    'total_value': total_value,
                    'lost_count': lost_count
                },
                'projects': projects
            })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

# ==================== FILTER OPTIONS ====================
@api_bp.route('/filter-options')
def filter_options():
    engine = get_engine()
    with engine.connect() as conn:
        managers = [row[0] for row in conn.execute(text("SELECT DISTINCT ProjectManager FROM t_Project WHERE ProjectManager IS NOT NULL AND ProjectManager != ''")).fetchall()]
        types = [row[0] for row in conn.execute(text("SELECT DISTINCT ProjectType FROM t_Project WHERE ProjectType IS NOT NULL AND ProjectType != ''")).fetchall()]
        statuses = ['Active', 'On Hold', 'Completed', 'Cancelled', 'Lost']
    return jsonify({
        'project_managers': managers,
        'project_types': types,
        'statuses': statuses
    })

# ==================== CREATE PROJECT ====================
@api_bp.route('/create', methods=['POST'])
def create_project():
    data = request.json
    engine = get_engine()
    today = int(datetime.date.today().strftime("%Y%m%d"))
    created_by = current_user.get_id() if current_user.is_authenticated else None
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO t_Project (
                    ProjectID, ProjectName, CompanyID, CustomerCompanyID, Status, WinLossReason,
                    AdditionalAttributes, PercentComplete, ProjectManager,
                    ProjectType, StartDateKey, EndDateKey, CreatedByUserID
                ) VALUES (
                    :pid, :pname, :company, :cust, :status, :winloss,
                    :attrs, :pct, :pmgr,
                    :ptype, :start_key, :end_key, :created_by
                )
            """), {
                'pid': data['project_id'],
                'pname': data['project_name'],
                'company': data.get('company_id', ''),
                'cust': data.get('customer_id'),
                'status': data.get('status', 'Active'),
                'winloss': data.get('winloss_reason'),
                'attrs': json.dumps(data.get('additional_attrs', {})),
                'pct': float(data.get('percent_complete', 0)),
                'pmgr': data.get('project_manager'),
                'ptype': data.get('project_type'),
                'start_key': data.get('start_date_key'),
                'end_key': data.get('end_date_key'),
                'created_by': created_by
            })
            # Financials
            budget = data.get('budget', 0)
            actual = data.get('actual_cost', 0)
            committed = data.get('committed_cost', 0)
            currency = data.get('currency', 'USD')
            if budget or actual or committed:
                conn.execute(text("""
                    INSERT INTO t_ProjectFinancials (ProjectID, Budget, ActualCost, CommittedCost, Currency, LastUpdatedDateKey)
                    VALUES (:pid, :budget, :actual, :committed, :curr, :today)
                """), {
                    'pid': data['project_id'],
                    'budget': float(budget),
                    'actual': float(actual),
                    'committed': float(committed),
                    'curr': currency,
                    'today': today
                })
            # Required documents
            required_docs = data.get('required_documents', [])
            if required_docs:
                conn.execute(text("DELETE FROM t_ProjectDocumentType WHERE ProjectID = :pid"), {'pid': data['project_id']})
                for doc in required_docs:
                    conn.execute(text("""
                        INSERT INTO t_ProjectDocumentType (ProjectID, DocumentTypeID, IsRequired, MaxRevisions, WorkflowStep, CreatedDateKey, ModifiedDateKey)
                        VALUES (:pid, :dtid, :req, :maxrev, :step, :today, :today)
                    """), {
                        'pid': data['project_id'],
                        'dtid': doc['document_type_id'],
                        'req': 1 if doc.get('is_required') else 0,
                        'maxrev': doc.get('max_revisions', 1),
                        'step': doc.get('workflow_step', ''),
                        'today': today
                    })
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 400

# ==================== UPDATE PROJECT ====================
@api_bp.route('/update/<string:project_id>', methods=['POST'])
def update_project(project_id):
    data = request.json
    engine = get_engine()
    today = int(datetime.date.today().strftime("%Y%m%d"))
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                UPDATE t_Project SET
                    ProjectName = :pname, CompanyID = :company, CustomerCompanyID = :cust, Status = :status,
                    WinLossReason = :winloss, AdditionalAttributes = :attrs,
                    PercentComplete = :pct, ProjectManager = :pmgr, ProjectType = :ptype,
                    StartDateKey = :start_key, EndDateKey = :end_key
                WHERE ProjectID = :pid
            """), {
                'pid': project_id,
                'pname': data['project_name'],
                'company': data.get('company_id', ''),
                'cust': data.get('customer_id'),
                'status': data.get('status', 'Active'),
                'winloss': data.get('winloss_reason'),
                'attrs': json.dumps(data.get('additional_attrs', {})),
                'pct': float(data.get('percent_complete', 0)),
                'pmgr': data.get('project_manager'),
                'ptype': data.get('project_type'),
                'start_key': data.get('start_date_key'),
                'end_key': data.get('end_date_key')
            })
            # Replace financials
            budget = data.get('budget', 0)
            actual = data.get('actual_cost', 0)
            committed = data.get('committed_cost', 0)
            currency = data.get('currency', 'USD')
            conn.execute(text("DELETE FROM t_ProjectFinancials WHERE ProjectID = :pid"), {'pid': project_id})
            conn.execute(text("""
                INSERT INTO t_ProjectFinancials (ProjectID, Budget, ActualCost, CommittedCost, Currency, LastUpdatedDateKey)
                VALUES (:pid, :budget, :actual, :committed, :curr, :today)
            """), {
                'pid': project_id,
                'budget': float(budget),
                'actual': float(actual),
                'committed': float(committed),
                'curr': currency,
                'today': today
            })
            # Replace required documents
            required_docs = data.get('required_documents', [])
            conn.execute(text("DELETE FROM t_ProjectDocumentType WHERE ProjectID = :pid"), {'pid': project_id})
            for doc in required_docs:
                conn.execute(text("""
                    INSERT INTO t_ProjectDocumentType (ProjectID, DocumentTypeID, IsRequired, MaxRevisions, WorkflowStep, CreatedDateKey, ModifiedDateKey)
                    VALUES (:pid, :dtid, :req, :maxrev, :step, :today, :today)
                """), {
                    'pid': project_id,
                    'dtid': doc['document_type_id'],
                    'req': 1 if doc.get('is_required') else 0,
                    'maxrev': doc.get('max_revisions', 1),
                    'step': doc.get('workflow_step', ''),
                    'today': today
                })
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 400

# ==================== DASHBOARD DATA ====================


# ==================== DASHBOARD DATA ====================
@api_bp.route('/dashboard_data/<string:project_id>')
def dashboard_data(project_id):
    """
    Aggregated dashboard data for a single project.

    Every sub-query is wrapped in try/except so a single failing query
    doesn't 500 the whole endpoint. Failures are logged with the actual
    error message and fall back to safe defaults.
    """
    engine = get_engine()
    try:
        with engine.connect() as conn:

            # ---------- Project header ----------
            proj = conn.execute(text("""
                SELECT ProjectName, Status, PercentComplete, ProjectManager,
                       WinLossReason, AdditionalAttributes,
                       StartDateKey, EndDateKey, CompanyID, CustomerCompanyID
                FROM t_Project WHERE ProjectID = :pid
            """), {'pid': project_id}).fetchone()
            if not proj:
                return jsonify({'error': 'Project not found'}), 404

            # ---------- Financials ----------
            try:
                fin = conn.execute(text("""
                    SELECT ISNULL(CAST(Budget        AS DECIMAL(18,4)), 0),
                           ISNULL(CAST(ActualCost    AS DECIMAL(18,4)), 0),
                           ISNULL(CAST(CommittedCost AS DECIMAL(18,4)), 0),
                           ISNULL(Currency, 'USD')
                    FROM t_ProjectFinancials WHERE ProjectID = :pid
                """), {'pid': project_id}).fetchone()
                budget, actual, committed, currency = fin if fin else (0, 0, 0, 'USD')
                budget_util = (actual / budget * 100) if budget and budget > 0 else 0
            except Exception as e:
                print(f"⚠️ Financials query failed for {project_id}: {e}")
                budget, actual, committed, currency = 0, 0, 0, 'USD'
                budget_util = 0

            # ---------- Milestones ----------
            try:
                milestone = conn.execute(text("""
                    SELECT COUNT(*) AS total,
                           SUM(CASE WHEN Status = 'Completed' THEN 1 ELSE 0 END) AS completed
                    FROM t_ProjectMilestones WHERE ProjectID = :pid
                """), {'pid': project_id}).fetchone()
                total_ms = milestone[0] or 0
                completed_ms = milestone[1] or 0
                milestone_progress = (completed_ms / total_ms * 100) if total_ms > 0 else 0
            except Exception as e:
                print(f"⚠️ Milestones query failed for {project_id}: {e}")
                milestone_progress = 0

            # ---------- Pending Reminders ----------
            try:
                today = int(datetime.date.today().strftime("%Y%m%d"))
                reminders = conn.execute(text("""
                    SELECT COUNT(*) FROM t_Reminder
                    WHERE ProjectID = :pid
                      AND DueDateKey < :today
                      AND ISNULL(Status, '') != 'Completed'
                """), {'pid': project_id, 'today': today}).scalar() or 0
            except Exception as e:
                print(f"⚠️ Reminders query failed for {project_id}: {e}")
                reminders = 0

            # ---------- Service Requests & Warranty ----------
            # t_ServiceRequest.CompanyProjectID is NULL for all current rows,
            # so service requests cannot be attributed to a project yet.
            # Truthful per-project count is 0 until linkage is populated.
            service_orders = 0
            warranty_warning = 0

            return jsonify({
                'project': {
                    'name': proj[0],
                    'status': proj[1],
                    'percent_complete': float(proj[2] or 0),
                    'manager': proj[3],                 # may be None; frontend handles
                    'winloss_reason': proj[4] or '',
                    'additional_attrs': proj[5] or '{}',
                    'start_date': proj[6],
                    'end_date': proj[7],
                    'company_id': proj[8] or '',
                    'customer_id': proj[9] or ''
                },
                'kpis': {
                    'percent_complete': float(proj[2] or 0),
                    'budget_utilisation': round(budget_util, 1),
                    'milestone_progress': round(milestone_progress, 1),
                    'active_service_orders': service_orders,
                    'pending_reminders': reminders,
                    'warranty_expiry_warning': warranty_warning > 0
                },
                'financial': {
                    'budget': float(budget),
                    'actual_cost': float(actual),
                    'committed_cost': float(committed),
                    'currency': currency
                }
            })
    except Exception as e:
        print("Dashboard error:", e)
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

# ==================== MILESTONES ====================
@api_bp.route('/milestones/<string:project_id>')
def get_milestones(project_id):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT MilestoneID, MilestoneName, DueDateKey, CompletionDateKey, Status
            FROM t_ProjectMilestones WHERE ProjectID = :pid ORDER BY DueDateKey
        """), {'pid': project_id}).fetchall()
    milestones = [{
        'id': r[0], 'name': r[1], 'planned_date': r[2], 'actual_date': r[3], 'status': r[4]
    } for r in rows]
    return jsonify(milestones)

@api_bp.route('/add_milestone', methods=['POST'])
def add_milestone():
    data = request.json
    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO t_ProjectMilestones (ProjectID, MilestoneName, DueDateKey, Status)
                VALUES (:pid, :name, :due, :status)
            """), {
                'pid': data['project_id'],
                'name': data['milestone_name'],
                'due': data['planned_date_key'],
                'status': data.get('status', 'Pending')
            })
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

# ==================== MILESTONES LIST ====================
@api_bp.route('/milestones_list/<string:project_id>')
def get_milestones_list(project_id):
    status_filter = request.args.get('status', '')
    due_date_from = request.args.get('due_date_from', '')
    due_date_to = request.args.get('due_date_to', '')
    
    engine = get_engine()
    with engine.connect() as conn:
        base_sql = """
            SELECT MilestoneID, MilestoneName, DueDateKey, 
                   CompletionDateKey, Status, Weight
            FROM t_ProjectMilestones
            WHERE ProjectID = :pid
        """
        params = {'pid': project_id}
        conditions = []
        if status_filter and status_filter.strip():
            conditions.append("Status = :status")
            params['status'] = status_filter
        if due_date_from and due_date_from.strip():
            conditions.append("DueDateKey >= :from_date")
            params['from_date'] = int(due_date_from)
        if due_date_to and due_date_to.strip():
            conditions.append("DueDateKey <= :to_date")
            params['to_date'] = int(due_date_to)
        if conditions:
            base_sql += " AND " + " AND ".join(conditions)
        base_sql += " ORDER BY DueDateKey"
        
        rows = conn.execute(text(base_sql), params).fetchall()
        
        total = conn.execute(text("SELECT COUNT(*) FROM t_ProjectMilestones WHERE ProjectID = :pid"), {'pid': project_id}).scalar()
        completed = conn.execute(text("SELECT COUNT(*) FROM t_ProjectMilestones WHERE ProjectID = :pid AND Status = 'Completed'"), {'pid': project_id}).scalar()
        today = int(datetime.date.today().strftime("%Y%m%d"))
        overdue = conn.execute(text("""
            SELECT COUNT(*) FROM t_ProjectMilestones 
            WHERE ProjectID = :pid AND DueDateKey < :today AND Status != 'Completed'
        """), {'pid': project_id, 'today': today}).scalar()
        on_time = conn.execute(text("""
            SELECT COUNT(*) FROM t_ProjectMilestones 
            WHERE ProjectID = :pid AND Status = 'Completed' AND CompletionDateKey <= DueDateKey
        """), {'pid': project_id}).scalar()
        
        milestones = []
        for row in rows:
            is_overdue = (row[2] and row[2] < today) and (row[4] != 'Completed')
            milestones.append({
                'id': row[0],
                'name': row[1],
                'due_date': row[2],
                'completion_date': row[3],
                'status': row[4],
                'weight': float(row[5]) if row[5] else 0,
                'is_overdue': is_overdue
            })
        return jsonify({
            'kpis': {'total': total, 'completed': completed, 'on_time': on_time, 'overdue': overdue},
            'milestones': milestones
        })

@api_bp.route('/complete_milestone', methods=['POST'])
def complete_milestone():
    data = request.json
    engine = get_engine()
    today = int(datetime.date.today().strftime("%Y%m%d"))
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                UPDATE t_ProjectMilestones
                SET Status = 'Completed', CompletionDateKey = :today
                WHERE MilestoneID = :mid
            """), {'today': today, 'mid': data['milestone_id']})
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

# ==================== PRODUCTS ====================
@api_bp.route('/products/<string:project_id>')
def get_products(project_id):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT ProductID, RequiredQuantity, UnitPrice, (CAST(RequiredQuantity AS DECIMAL(18,4)) * CAST(UnitPrice AS DECIMAL(18,4))) as TotalPrice
            FROM t_ProjectProducts WHERE ProjectID = :pid
        """), {'pid': project_id}).fetchall()
    products = [{'id': r[0], 'qty': r[1], 'unit_price': float(r[2] or 0), 'total': float(r[3] or 0)} for r in rows]
    return jsonify(products)

# ==================== QUALITY ====================
@api_bp.route('/quality/<string:project_id>')
def get_quality(project_id):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT ProjectQualityPlanID, PlanName, PlanStatus, EffectiveDateKey
            FROM t_ProjectQualityPlan WHERE ProjectID = :pid
        """), {'pid': project_id}).fetchall()
    quality = [{'id': r[0], 'name': r[1], 'status': r[2], 'due_date': r[3]} for r in rows]
    return jsonify(quality)

# ==================== DOCUMENTS ====================
@api_bp.route('/documents/<string:project_id>')
def get_documents(project_id):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT DocumentID, DocumentNumber, Title, Revision, Status
            FROM t_EngineeringDocument WHERE ProjectID = :pid
        """), {'pid': project_id}).fetchall()
    docs = [{'id': r[0], 'number': r[1], 'title': r[2], 'rev': r[3], 'status': r[4]} for r in rows]
    return jsonify(docs)

# ==================== SERVICE ORDERS ====================
@api_bp.route('/service_orders/<string:project_id>')
def get_service_orders(project_id):
    engine = get_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT OrderID, OrderNumber, OrderDateKey, Status, TotalAmount
                FROM t_ServiceOrder WHERE ProjectID = :pid
            """), {'pid': project_id}).fetchall()
        orders = [{'id': r[0], 'number': r[1], 'date': r[2], 'status': r[3], 'amount': float(r[4])} for r in rows]
        return jsonify(orders)
    except:
        return jsonify([])

# ==================== REMINDERS ====================
@api_bp.route('/reminders/<string:project_id>')
def get_reminders(project_id):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT ReminderID, Title, DueDateKey, Status, AssignedTo
            FROM t_Reminder WHERE ProjectID = :pid ORDER BY DueDateKey
        """), {'pid': project_id}).fetchall()
    reminders = [{'id': r[0], 'title': r[1], 'due_date': r[2], 'status': r[3], 'assigned_to': r[4]} for r in rows]
    return jsonify(reminders)

@api_bp.route('/add_reminder', methods=['POST'])
def add_reminder():
    data = request.json
    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO t_Reminder (ProjectID, Title, DueDateKey, AssignedTo)
                VALUES (:pid, :title, :due, :assigned)
            """), {
                'pid': data['project_id'],
                'title': data['title'],
                'due': data['due_date_key'],
                'assigned': data.get('assigned_to')
            })
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

# ==================== UPDATE FINANCIALS ====================
@api_bp.route('/update_financials', methods=['POST'])
def update_financials():
    data = request.json
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("""
            UPDATE t_ProjectFinancials 
            SET Budget = :budget, ActualCost = :actual, CommittedCost = :committed, Currency = :currency
            WHERE ProjectID = :pid
        """), {
            'pid': data['project_id'],
            'budget': data['budget'],
            'actual': data['actual_cost'],
            'committed': data['committed_cost'],
            'currency': data['currency']
        })
        conn.commit()
    return jsonify({'success': True})

# ==================== UPDATE ATTRIBUTES ====================
@api_bp.route('/update_attributes', methods=['POST'])
def update_attributes():
    data = request.json
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("UPDATE t_Project SET AdditionalAttributes = :attrs WHERE ProjectID = :pid"),
                     {'attrs': json.dumps(data['attributes']), 'pid': data['project_id']})
        conn.commit()
    return jsonify({'success': True})

# ==================== PROJECT BOM ====================
@api_bp.route('/bom/<string:project_id>')
def get_project_bom(project_id):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT pp.ProjectProductID, pp.ProductID, p.descEnglish, pp.RequiredQuantity,
                   pp.IsCustom, pp.AlternativeComponent, pp.CustomSpec, pp.Status
            FROM t_ProjectProducts pp
            LEFT JOIN t_Product p ON pp.ProductID = p.ProductId
            WHERE pp.ProjectID = :pid
            ORDER BY pp.ProductID
        """), {'pid': project_id}).fetchall()
        
        total = len(rows)
        custom = sum(1 for r in rows if r[4] == 1)
        
        components = []
        for row in rows:
            components.append({
                'id': row[0],
                'component_id': row[1],
                'name': row[2] or row[1],
                'quantity': float(row[3]) if row[3] else 0,
                'is_custom': bool(row[4]),
                'alternative': row[5] or '',
                'custom_spec': row[6] or '',
                'status': row[7] or ''
            })
        
        return jsonify({
            'kpis': {'total_components': total, 'custom_components': custom},
            'components': components
        })

@api_bp.route('/bom/add', methods=['POST'])
def add_project_bom_component():
    data = request.json
    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO t_ProjectProducts
                (ProjectID, ProductID, RequiredQuantity, IsCustom, AlternativeComponent, CustomSpec, Status)
                VALUES (:pid, :prod, :qty, :iscustom, :alt, :spec, 'Active')
            """), {
                'pid': data['project_id'],
                'prod': data['component_id'],
                'qty': float(data['quantity']),
                'iscustom': 1 if data.get('is_custom') else 0,
                'alt': data.get('alternative', ''),
                'spec': data.get('custom_spec', '')
            })
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@api_bp.route('/bom/delete/<int:line_id>', methods=['POST'])
def delete_project_bom_component(line_id):
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM t_ProjectProducts WHERE ProjectProductID = :lid"), {'lid': line_id})
        conn.commit()
    return jsonify({'success': True})

@api_bp.route('/bom/import', methods=['POST'])
def import_project_bom():
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'No file'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'message': 'Empty file'}), 400
    
    stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
    csv_input = csv.DictReader(stream)
    engine = get_engine()
    created = 0
    errors = []
    
    with engine.connect() as conn:
        for row in csv_input:
            try:
                conn.execute(text("""
                    INSERT INTO t_ProjectProducts
                    (ProjectID, ProductID, RequiredQuantity, IsCustom, AlternativeComponent, CustomSpec, Status)
                    VALUES (:pid, :prod, :qty, :iscustom, :alt, :spec, 'Active')
                """), {
                    'pid': row['ProjectID'],
                    'prod': row['ProductID'],
                    'qty': float(row['RequiredQuantity']),
                    'iscustom': int(row.get('IsCustom', 0)),
                    'alt': row.get('AlternativeComponent', ''),
                    'spec': row.get('CustomSpec', '')
                })
                created += 1
            except Exception as e:
                errors.append(f"Row {csv_input.line_num}: {str(e)}")
        conn.commit()
    return jsonify({'success': True, 'created': created, 'errors': errors})

@api_bp.route('/bom/export/<string:project_id>')
def export_project_bom(project_id):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT pp.ProductID, p.descEnglish, pp.RequiredQuantity, pp.IsCustom, pp.AlternativeComponent, pp.CustomSpec
            FROM t_ProjectProducts pp
            LEFT JOIN t_Product p ON pp.ProductID = p.ProductId
            WHERE pp.ProjectID = :pid
        """), {'pid': project_id}).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ProductID', 'ComponentName', 'RequiredQuantity', 'IsCustom', 'AlternativeComponent', 'CustomSpec'])
    for row in rows:
        writer.writerow([row[0], row[1] or row[0], row[2], row[3], row[4], row[5]])
    output.seek(0)
    return Response(output.getvalue(), mimetype='text/csv', headers={"Content-Disposition": f"attachment;filename=project_bom_{project_id}.csv"})

# ==================== DELIVERABLES ====================
@api_bp.route('/deliverables/<string:project_id>')
def get_deliverables(project_id):
    status_filter = request.args.get('status', '')
    responsible_filter = request.args.get('responsible', '')
    
    engine = get_engine()
    with engine.connect() as conn:
        sql = """
            SELECT pp.ProjectProductID, p.descEnglish as DeliverableName,
                   pp.DeliveryDateKey, pp.Responsible, pp.Status,
                   pp.CustomerApproval, pp.Notes, pp.ApprovalDateKey
            FROM t_ProjectProducts pp
            LEFT JOIN t_Product p ON pp.ProductID = p.ProductId
            WHERE pp.ProjectID = :pid
        """
        params = {'pid': project_id}
        if status_filter:
            sql += " AND pp.Status = :status"
            params['status'] = status_filter
        if responsible_filter:
            sql += " AND pp.Responsible = :resp"
            params['resp'] = responsible_filter
        sql += " ORDER BY pp.DeliveryDateKey"
        
        rows = conn.execute(text(sql), params).fetchall()
        
        total = conn.execute(text("SELECT COUNT(*) FROM t_ProjectProducts WHERE ProjectID = :pid"), {'pid': project_id}).scalar()
        pending = conn.execute(text("SELECT COUNT(*) FROM t_ProjectProducts WHERE ProjectID = :pid AND Status = 'Pending'"), {'pid': project_id}).scalar()
        submitted = conn.execute(text("SELECT COUNT(*) FROM t_ProjectProducts WHERE ProjectID = :pid AND Status = 'Submitted'"), {'pid': project_id}).scalar()
        approved = conn.execute(text("SELECT COUNT(*) FROM t_ProjectProducts WHERE ProjectID = :pid AND Status = 'Approved'"), {'pid': project_id}).scalar()
        rejected = conn.execute(text("SELECT COUNT(*) FROM t_ProjectProducts WHERE ProjectID = :pid AND Status = 'Rejected'"), {'pid': project_id}).scalar()
        
        deliverables = []
        responsibles = set()
        for row in rows:
            name = row[1] if row[1] else f"Product {row[0]}"
            responsible = row[3] or ''
            status = row[4] or 'Pending'
            customer_approval = bool(row[5])
            notes = row[6] or ''
            approval_date = row[7]
            if responsible:
                responsibles.add(responsible)
            deliverables.append({
                'id': row[0],
                'name': name,
                'due_date': row[2],
                'responsible': responsible,
                'status': status,
                'customer_approval': customer_approval,
                'notes': notes,
                'approval_date': approval_date
            })
        
        return jsonify({
            'kpis': {'total': total, 'pending': pending, 'submitted': submitted, 'approved': approved, 'rejected': rejected},
            'deliverables': deliverables,
            'filter_options': {
                'statuses': ['Pending', 'Submitted', 'Approved', 'Rejected'],
                'responsibles': sorted(list(responsibles))
            }
        })

@api_bp.route('/deliverable/submit_approval', methods=['POST'])
def submit_deliverable_approval():
    data = request.json
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("""
            UPDATE t_ProjectProducts
            SET Status = 'Submitted'
            WHERE ProjectProductID = :did AND Status = 'Pending'
        """), {'did': data['deliverable_id']})
        conn.commit()
    return jsonify({'success': True})

@api_bp.route('/deliverable/approve', methods=['POST'])
def approve_deliverable():
    data = request.json
    engine = get_engine()
    today = int(datetime.date.today().strftime("%Y%m%d"))
    with engine.connect() as conn:
        conn.execute(text("""
            UPDATE t_ProjectProducts
            SET Status = 'Approved', CustomerApproval = 1, ApprovalDateKey = :today
            WHERE ProjectProductID = :did AND Status = 'Submitted'
        """), {'did': data['deliverable_id'], 'today': today})
        conn.commit()
    return jsonify({'success': True})

@api_bp.route('/deliverable/reject', methods=['POST'])
def reject_deliverable():
    data = request.json
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("""
            UPDATE t_ProjectProducts
            SET Status = 'Rejected', CustomerApproval = 0
            WHERE ProjectProductID = :did AND Status = 'Submitted'
        """), {'did': data['deliverable_id']})
        conn.commit()
    return jsonify({'success': True})

@api_bp.route('/deliverables/import', methods=['POST'])
def import_deliverables():
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'No file'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'message': 'Empty file'}), 400
    
    stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
    csv_input = csv.DictReader(stream)
    engine = get_engine()
    created = 0
    errors = []
    
    with engine.connect() as conn:
        for row in csv_input:
            try:
                conn.execute(text("""
                    INSERT INTO t_ProjectProducts (ProjectID, ProductID, DeliveryDateKey, Responsible, Status, Notes)
                    VALUES (:pid, :prod, :due, :resp, :status, :notes)
                """), {
                    'pid': row['ProjectID'],
                    'prod': row['ProductID'],
                    'due': int(row['DeliveryDateKey']) if row.get('DeliveryDateKey') else None,
                    'resp': row.get('Responsible', ''),
                    'status': row.get('Status', 'Pending'),
                    'notes': row.get('Notes', '')
                })
                created += 1
            except Exception as e:
                errors.append(f"Row {csv_input.line_num}: {str(e)}")
        conn.commit()
    return jsonify({'success': True, 'created': created, 'errors': errors})

@api_bp.route('/deliverables/export/<string:project_id>')
def export_deliverables(project_id):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT pp.ProductID, p.descEnglish, pp.DeliveryDateKey, pp.Responsible, pp.Status, pp.Notes
            FROM t_ProjectProducts pp
            LEFT JOIN t_Product p ON pp.ProductID = p.ProductId
            WHERE pp.ProjectID = :pid
        """), {'pid': project_id}).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ProductID', 'DeliverableName', 'DeliveryDateKey', 'Responsible', 'Status', 'Notes'])
    for row in rows:
        writer.writerow([row[0], row[1] or row[0], row[2], row[3], row[4], row[5]])
    output.seek(0)
    return Response(output.getvalue(), mimetype='text/csv', headers={"Content-Disposition": f"attachment;filename=deliverables_{project_id}.csv"})

# ==================== CUSTOMER PROJECTS ====================
@api_bp.route('/customer_projects')
def get_customer_projects():
    status_filter = request.args.get('status', '')
    company_filter = request.args.get('company', '')
    
    engine = get_engine()
    with engine.connect() as conn:
        sql = """
            SELECT cp.CustomerProjectID, c.Name as CompanyName, cp.ProjectName,
                   cp.Location, cp.Value, cp.Status, cp.StartDateKey, cp.EndDateKey,
                   (SELECT COUNT(*) FROM t_Project WHERE CustomerProjectID = cp.CustomerProjectID) as LinkedCount
            FROM t_CustomerProject cp
            LEFT JOIN t_companies c ON cp.CompanyID = c.companyID
            WHERE 1=1
        """
        params = {}
        if status_filter:
            sql += " AND cp.Status = :status"
            params['status'] = status_filter
        if company_filter:
            sql += " AND cp.CompanyID = :company"
            params['company'] = company_filter
        sql += " ORDER BY cp.CustomerProjectID"
        
        rows = conn.execute(text(sql), params).fetchall()
        
        total = conn.execute(text("SELECT COUNT(*) FROM t_CustomerProject")).scalar()
        active = conn.execute(text("SELECT COUNT(*) FROM t_CustomerProject WHERE Status = 'Active'")).scalar()
        completed = conn.execute(text("SELECT COUNT(*) FROM t_CustomerProject WHERE Status = 'Completed'")).scalar()
        on_hold = conn.execute(text("SELECT COUNT(*) FROM t_CustomerProject WHERE Status = 'On Hold'")).scalar()
        
        projects = []
        for row in rows:
            projects.append({
                'id': row[0],
                'company': row[1] or '',
                'name': row[2],
                'location': row[3] or '',
                'value': float(row[4]) if row[4] else 0,
                'status': row[5],
                'start_date': row[6],
                'end_date': row[7],
                'linked_count': row[8] or 0
            })
        
        companies = conn.execute(text("SELECT companyID, Name FROM t_companies WHERE IsCustomer = 1")).fetchall()
        company_list = [{'id': c[0], 'name': c[1]} for c in companies]
        
        return jsonify({
            'kpis': {'total': total, 'active': active, 'completed': completed, 'on_hold': on_hold},
            'projects': projects,
            'filter_options': {
                'statuses': ['Active', 'On Hold', 'Completed', 'Cancelled'],
                'companies': company_list
            }
        })

# ==================== CUSTOMER PROJECT DETAIL ====================
@api_bp.route('/customer_project/<string:cp_id>')
def get_customer_project_detail(cp_id):
    engine = get_engine()
    with engine.connect() as conn:
        proj = conn.execute(text("""
            SELECT cp.CustomerProjectID, c.Name as CompanyName, cp.ProjectName,
                   cp.Description, cp.Location, cp.Value, cp.Status, cp.StartDateKey, cp.EndDateKey
            FROM t_CustomerProject cp
            LEFT JOIN t_companies c ON cp.CompanyID = c.companyID
            WHERE cp.CustomerProjectID = :pid
        """), {'pid': cp_id}).fetchone()
        if not proj:
            return jsonify({'error': 'Not found'}), 404
        
        internal = conn.execute(text("""
            SELECT ProjectID, ProjectName, Status, PercentComplete
            FROM t_Project WHERE CustomerProjectID = :pid
        """), {'pid': cp_id}).fetchall()
        internal_list = [{'id': r[0], 'name': r[1], 'status': r[2], 'progress': r[3]} for r in internal]
        
        contacts = conn.execute(text("""
            SELECT ContactID, ContactName, ContactRole, IsActive
            FROM t_ProjectContact WHERE CustomerProjectID = :pid
        """), {'pid': cp_id}).fetchall()
        contact_list = [{'id': r[0], 'name': r[1] or '', 'role': r[2] or '', 'active': is_active_true(r[3])} for r in contacts]
        
        return jsonify({
            'project': {
                'id': proj[0], 'company': proj[1] or '', 'name': proj[2],
                'description': proj[3] or '', 'location': proj[4] or '',
                'value': float(proj[5]) if proj[5] else 0,
                'status': proj[6],
                'start_date': proj[7], 'end_date': proj[8]
            },
            'internal_projects': internal_list,
            'contacts': contact_list
        })

# ==================== CUSTOMER PROJECT CREATE ====================
@api_bp.route('/customer_project/create', methods=['POST'])
def create_customer_project():
    data = request.json
    engine = get_engine()
    created_by = current_user.get_id() if current_user.is_authenticated else None
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO t_CustomerProject
                (CustomerProjectID, CompanyID, ProjectName, Description, Location, Value, Status, StartDateKey, EndDateKey, CreatedByUserID)
                VALUES (:id, :company, :name, :desc, :loc, :value, :status, :start, :end, :created_by)
            """), {
                'id': data['customer_project_id'],
                'company': data['company_id'],
                'name': data['project_name'],
                'desc': data.get('description', ''),
                'loc': data.get('location', ''),
                'value': float(data.get('value', 0)),
                'status': data.get('status', 'Active'),
                'start': data.get('start_date_key'),
                'end': data.get('end_date_key'),
                'created_by': created_by
            })
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

# ==================== RESOURCE ALLOCATION ====================
@api_bp.route('/resources/<string:project_id>')
def get_resource_allocations(project_id):
    """
    Get resource allocations for a project.

    NVARCHAR fixes applied:
      - AllocationPercent is nvarchar → CAST to DECIMAL before SUM
      - IsActive is nvarchar → compare to '1' (string), not 1 (int)
      - Python-side truthiness guards replaced with explicit parsing,
        because "0" is a truthy string in Python.
    """
    role_filter = request.args.get('role', '')

    engine = get_engine()
    with engine.connect() as conn:
        sql = """
            SELECT pc.ContactID, u.FullName, pc.ContactType as Role,
                   CAST(pc.AllocationPercent AS DECIMAL(18,4)) AS AllocationPercent,
                   pc.AssignedDateKey as StartDateKey, pc.EndDateKey,
                   pc.IsActive as Status
            FROM t_ProjectContact pc
            LEFT JOIN t_Users u ON pc.PersonID = u.UserID
            WHERE pc.CustomerProjectID = :pid
        """
        params = {'pid': project_id}
        if role_filter:
            sql += " AND pc.ContactType = :role"
            params['role'] = role_filter
        sql += " ORDER BY u.FullName"

        rows = conn.execute(text(sql), params).fetchall()

        total_alloc = conn.execute(text("""
            SELECT ISNULL(SUM(CAST(AllocationPercent AS DECIMAL(18,4))), 0)
            FROM t_ProjectContact
            WHERE CustomerProjectID = :pid AND IsActive = '1'
        """), {'pid': project_id}).scalar()

        user_count = conn.execute(text("""
            SELECT COUNT(DISTINCT PersonID) FROM t_ProjectContact
            WHERE CustomerProjectID = :pid AND IsActive = '1'
        """), {'pid': project_id}).scalar()

        roles = conn.execute(text("""
            SELECT DISTINCT ContactType FROM t_ProjectContact
            WHERE CustomerProjectID = :pid AND ContactType IS NOT NULL AND ContactType != ''
        """), {'pid': project_id}).fetchall()
        role_list = [r[0] for r in roles]

        allocations = []
        for row in rows:
            # Safe numeric conversion for AllocationPercent
            alloc_pct = row[3]
            if alloc_pct is None or alloc_pct == '':
                alloc_pct = 100
            else:
                try:
                    alloc_pct = float(alloc_pct)
                except (ValueError, TypeError):
                    alloc_pct = 100

            # IsActive is nvarchar: '1' or '0'
            is_active = is_active_true(row[6])

            allocations.append({
                'id': row[0],
                'employee': row[1] if row[1] else f"User {row[0]}",
                'role': row[2] or '',
                'allocation_percent': alloc_pct,
                'start_date': row[4],
                'end_date': row[5],
                'status': 'Active' if is_active else 'Released'
            })

        return jsonify({
            'kpis': {
                'total_allocated_percent': float(total_alloc) if total_alloc else 0,
                'active_users': user_count
            },
            'allocations': allocations,
            'filter_options': {'roles': role_list}
        })

@api_bp.route('/resources/assign', methods=['POST'])
def assign_resource():
    data = request.json
    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO t_ProjectContact
                (CustomerProjectID, PersonID, ContactType, AllocationPercent, AssignedDateKey, EndDateKey, IsActive)
                VALUES (:pid, :uid, :role, :percent, :start, :end, '1')
            """), {
                'pid': data['project_id'],
                'uid': data['user_id'],
                'role': data.get('role', ''),
                'percent': data.get('allocation_percent', 100),
                'start': data.get('start_date_key'),
                'end': data.get('end_date_key')
            })
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

# ==================== PROJECTS LIST ====================
@api_bp.route('/projects_list')
def get_all_internal_projects():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT ProjectID, ProjectName FROM t_Project ORDER BY ProjectID")).fetchall()
    return jsonify([{'id': r[0], 'name': r[1]} for r in rows])

# ==================== INTERNAL PROJECTS ====================
@api_bp.route('/internal_projects')
def get_internal_projects():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT ProjectID, ProjectName FROM t_Project ORDER BY ProjectID")).fetchall()
    return jsonify([{'id': r[0], 'name': r[1]} for r in rows])

# ==================== DOCUMENT TYPES ====================
@api_bp.route('/document_types')
def get_document_types():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT DocumentTypeID, Name FROM t_DocumentType
            WHERE IsActive = '1' ORDER BY Name
        """)).fetchall()
    return jsonify([{'id': r[0], 'name': r[1]} for r in rows])

# ==================== DOCUMENT TYPES LIST ====================
@api_bp.route('/document_types_list')
def list_document_types():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT DocumentTypeID, Name FROM t_DocumentType
            WHERE IsActive = '1' ORDER BY Name
        """)).fetchall()
    return jsonify([{'id': r[0], 'name': r[1]} for r in rows])

# ==================== REQUIRED DOCUMENTS ====================
@api_bp.route('/required_documents/<string:project_id>')
def get_project_required_documents(project_id):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT dt.DocumentTypeID, dt.Name, pdt.IsRequired, pdt.MaxRevisions, pdt.WorkflowStep
            FROM t_ProjectDocumentType pdt
            JOIN t_DocumentType dt ON pdt.DocumentTypeID = dt.DocumentTypeID
            WHERE pdt.ProjectID = :pid
        """), {'pid': project_id}).fetchall()
    return jsonify([{
        'id': r[0], 'name': r[1], 'is_required': is_active_true(r[2]),
        'max_revisions': r[3], 'workflow_step': r[4] or ''
    } for r in rows])

# ==================== PRODUCT TREE ====================
@api_bp.route('/<string:project_id>/products')
def get_project_products_tree(project_id):
    engine = get_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT pp.ProjectProductID, pp.ProductID, p.descEnglish, pp.RequiredQuantity,
                       pp.DeliveryDateKey, pp.Status, pp.UnitPrice, pp.Notes,
                       pp.InstallationSequence, pp.SiteLocation, pp.Responsible, pp.IsCustom,
                       pp.AlternativeComponent, pp.CustomSpec
                FROM t_ProjectProducts pp
                JOIN t_Product p ON pp.ProductID = p.ProductId
                WHERE pp.ProjectID = :pid
            """), {'pid': project_id}).fetchall()

            def build_bom_tree(root_product_id):
                all_relations = []
                stack = [root_product_id]
                visited = set()
                while stack:
                    parent = stack.pop()
                    if parent in visited:
                        continue
                    visited.add(parent)
                    children = conn.execute(text("""
                        SELECT b.ComponentProductID, p.descEnglish, b.Quantity, b.ScrapFactor,
                               b.EffectivityDateKey, b.ObsoleteDateKey
                        FROM t_BOM b
                        JOIN t_Product p ON b.ComponentProductID = p.ProductId
                        WHERE b.ParentProductID = :pid
                          AND (b.ObsoleteDateKey IS NULL OR b.ObsoleteDateKey > CAST(GETDATE() AS INT))
                    """), {'pid': parent}).fetchall()
                    for child in children:
                        all_relations.append((parent, child))
                        stack.append(child[0])

                node_map = {}
                for parent, child in all_relations:
                    if parent not in node_map:
                        node_map[parent] = []
                    node_map[parent].append({
                        'component_id': child[0],
                        'component_name': child[1],
                        'quantity': float(child[2]),
                        'scrap_factor': float(child[3]) if child[3] else 0,
                        'effectivity_date': child[4],
                        'obsolete_date': child[5],
                        'children': []
                    })
                def build_subtree(pid):
                    kids = node_map.get(pid, [])
                    for kid in kids:
                        kid['children'] = build_subtree(kid['component_id'])
                    return kids
                return build_subtree(root_product_id)

            products = []
            for row in rows:
                product_tree = build_bom_tree(row[1])
                products.append({
                    'project_product_id': row[0],
                    'product_id': row[1],
                    'product_name': row[2],
                    'required_quantity': float(row[3]) if row[3] else 0,
                    'delivery_date': row[4],
                    'status': row[5],
                    'unit_price': float(row[6]) if row[6] else 0,
                    'notes': row[7],
                    'installation_sequence': row[8],
                    'site_location': row[9],
                    'responsible': row[10],
                    'is_custom': is_active_true(row[11]),
                    'alternative': row[12],
                    'custom_spec': row[13],
                    'children': product_tree
                })
            return jsonify({'project_id': project_id, 'products': products})
    except Exception as e:
        print("Error in get_project_products_tree:", e)
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

# ==================== BOQ ====================
@api_bp.route('/<string:project_id>/boq')
def get_project_boq(project_id):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT pp.ProjectProductID, pp.ProductID, p.descEnglish,
                   pp.RequiredQuantity, pp.Unit, pp.UnitPrice,
                   (CAST(pp.RequiredQuantity AS DECIMAL(18,4)) * CAST(pp.UnitPrice AS DECIMAL(18,4))) as TotalPrice,
                   pp.Notes
            FROM t_ProjectProducts pp
            LEFT JOIN t_Product p ON pp.ProductID = p.ProductId
            WHERE pp.ProjectID = :pid
        """), {'pid': project_id}).fetchall()
        items = []
        total_value = 0
        for r in rows:
            total = float(r[6] if r[6] else 0)
            total_value += total
            items.append({
                'id': r[0],
                'product_id': r[1],
                'description': r[2] or r[1] or 'Custom Item',
                'quantity': float(r[3] or 0),
                'unit': r[4] or 'pcs',
                'unit_price': float(r[5] or 0),
                'total_price': total,
                'notes': r[7]
            })
        return jsonify({
            'items': items,
            'kpis': {'total_value': total_value, 'item_count': len(items)}
        })

@api_bp.route('/project/boq', methods=['POST'])
def add_boq_item():
    data = request.json
    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO t_ProjectProducts
                (ProjectID, ProductID, RequiredQuantity, Unit, UnitPrice, Notes)
                VALUES (:pid, :prod, :qty, :unit, :price, :notes)
            """), {
                'pid': data['project_id'],
                'prod': data.get('product_id'),
                'qty': float(data['quantity']),
                'unit': data.get('unit', 'pcs'),
                'price': float(data.get('unit_price', 0)),
                'notes': data.get('notes', '')
            })
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@api_bp.route('/project/boq/<int:item_id>', methods=['PUT'])
def update_boq_item(item_id):
    data = request.json
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("""
            UPDATE t_ProjectProducts SET
                ProductID = :prod, RequiredQuantity = :qty, Unit = :unit,
                UnitPrice = :price, Notes = :notes
            WHERE ProjectProductID = :id
        """), {
            'id': item_id,
            'prod': data.get('product_id'),
            'qty': float(data['quantity']),
            'unit': data.get('unit', 'pcs'),
            'price': float(data.get('unit_price', 0)),
            'notes': data.get('notes', '')
        })
        conn.commit()
    return jsonify({'success': True})

@api_bp.route('/project/boq/<int:item_id>', methods=['DELETE'])
def delete_boq_item(item_id):
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM t_ProjectProducts WHERE ProjectProductID = :id"), {'id': item_id})
        conn.commit()
    return jsonify({'success': True})

# ==================== CERTIFICATES ====================
@api_bp.route('/certificate/<int:cert_id>')
def get_certificate(cert_id):
    engine = get_engine()
    with engine.connect() as conn:
        cert = conn.execute(text("""
            SELECT CertificateID, CertificateNumber, PeriodStartDateKey, PeriodEndDateKey,
                   Status, TotalCompletedAmount, RetentionPercent, RetentionAmount,
                   AdvanceRecovery, PreviousTotal, NetPayable, Notes
            FROM t_ProgressCertificate WHERE CertificateID = :cid
        """), {'cid': cert_id}).fetchone()
        if not cert:
            return jsonify({'error': 'Not found'}), 404
        
        lines = conn.execute(text("""
            SELECT cl.LineID, pp.ProductID, p.descEnglish, pp.Unit,
                   cl.PreviousQuantity, cl.CompletedQuantity,
                   (cl.PreviousQuantity + cl.CompletedQuantity) as TotalCompleted,
                   pp.UnitPrice, (CAST(cl.CompletedQuantity AS DECIMAL(18,4)) * CAST(pp.UnitPrice AS DECIMAL(18,4))) as Amount
            FROM t_CertificateLine cl
            JOIN t_ProjectProducts pp ON cl.ProjectProductID = pp.ProjectProductID
            LEFT JOIN t_Product p ON pp.ProductID = p.ProductId
            WHERE cl.CertificateID = :cid
        """), {'cid': cert_id}).fetchall()
        
        return jsonify({
            'certificate': {
                'id': cert[0],
                'number': cert[1],
                'start': cert[2],
                'end': cert[3],
                'status': cert[4],
                'total_completed': float(cert[5] or 0),
                'retention_percent': float(cert[6] or 0),
                'retention_amount': float(cert[7] or 0),
                'advance_recovery': float(cert[8] or 0),
                'previous_total': float(cert[9] or 0),
                'net_payable': float(cert[10] or 0),
                'notes': cert[11] or ''
            },
            'lines': [{
                'id': l[0],
                'product_id': l[1],
                'description': l[2] or l[1],
                'unit': l[3] or 'pcs',
                'previous_qty': float(l[4] or 0),
                'completed_qty': float(l[5] or 0),
                'total_completed': float(l[6] or 0),
                'unit_price': float(l[7] or 0),
                'amount': float(l[8] or 0)
            } for l in lines]
        })

@api_bp.route('/<string:project_id>/certificates')
def get_certificates(project_id):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT CertificateID, CertificateNumber, PeriodStartDateKey, PeriodEndDateKey,
                   Status, TotalCompletedAmount, NetPayable
            FROM t_ProgressCertificate WHERE ProjectID = :pid ORDER BY CertificateID DESC
        """), {'pid': project_id}).fetchall()
        certs = []
        for r in rows:
            certs.append({
                'id': r[0], 'number': r[1], 'start': r[2], 'end': r[3],
                'status': r[4], 'total': float(r[5] or 0), 'net': float(r[6] or 0)
            })
        return jsonify(certs)

@api_bp.route('/certificates', methods=['POST'])
def create_certificate():
    data = request.json
    engine = get_engine()
    today = int(datetime.date.today().strftime("%Y%m%d"))
    try:
        with engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM t_ProgressCertificate WHERE ProjectID = :pid"), 
                                 {'pid': data['project_id']}).scalar() or 0
            number = f"CERT-{data['project_id']}-{count+1}"
            
            conn.execute(text("""
                INSERT INTO t_ProgressCertificate
                (ProjectID, CertificateNumber, PeriodStartDateKey, PeriodEndDateKey, Status, CreatedDateKey)
                VALUES (:pid, :num, :start, :end, 'Draft', :today)
            """), {
                'pid': data['project_id'],
                'num': number,
                'start': data['start_date_key'],
                'end': data['end_date_key'],
                'today': today
            })
            cert_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()
            
            boq_items = conn.execute(text("""
                SELECT ProjectProductID, UnitPrice
                FROM t_ProjectProducts WHERE ProjectID = :pid
            """), {'pid': data['project_id']}).fetchall()
            
            for item in boq_items:
                conn.execute(text("""
                    INSERT INTO t_CertificateLine 
                    (CertificateID, ProjectProductID, CompletedQuantity, UnitPrice)
                    VALUES (:cid, :ppid, 0, :price)
                """), {'cid': cert_id, 'ppid': item[0], 'price': float(item[1] or 0)})
            
            conn.commit()
        return jsonify({'success': True, 'certificate_id': cert_id, 'number': number})
    except Exception as e:
        print("Error creating certificate:", e)
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 400

@api_bp.route('/certificate/<int:cert_id>/lines', methods=['PUT'])
def update_certificate_lines(cert_id):
    data = request.json
    engine = get_engine()
    try:
        with engine.connect() as conn:
            for line in data.get('lines', []):
                if line.get('id'):
                    conn.execute(text("""
                        UPDATE t_CertificateLine 
                        SET CompletedQuantity = :qty
                        WHERE LineID = :lid AND CertificateID = :cid
                    """), {'qty': line['completed_qty'], 'lid': line['id'], 'cid': cert_id})
                else:
                    conn.execute(text("""
                        INSERT INTO t_CertificateLine 
                        (CertificateID, ProjectProductID, CompletedQuantity, ServiceDescription, ServiceUnit, ServiceUnitPrice, LineType)
                        VALUES (:cid, NULL, :qty, :desc, :unit, :price, 'service')
                    """), {'cid': cert_id, 'qty': line['completed_qty'], 'desc': line.get('description'), 
                           'unit': line.get('unit'), 'price': line.get('unit_price')})
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

# ==================== MS PROJECT INTEGRATION ====================


@api_bp.route('/ms-project/tasks/<string:project_id>')
def get_schedule_tasks(project_id):
    supplier = request.args.get('supplier', '')
    status = request.args.get('status', '')
    linked = request.args.get('linked', '')

    engine = get_engine()
    with engine.connect() as conn:
        sql = """
            SELECT 
                TaskID, 
                MPPUniqueID, 
                TaskName, 
                StartDateKey, 
                FinishDateKey,
                ISNULL(CAST(PlannedPercent AS DECIMAL(18,4)), 0) AS PlannedPercent,
                ISNULL(CAST(ActualPercent  AS DECIMAL(18,4)), 0) AS ActualPercent,
                SupplierID, 
                ComponentID,
                (ISNULL(CAST(PlannedPercent AS DECIMAL(18,4)), 0) 
                 - ISNULL(CAST(ActualPercent  AS DECIMAL(18,4)), 0)) AS Gap
            FROM t_ScheduleTask
            WHERE ProjectID = :pid
        """
        params = {'pid': project_id}
        if supplier:
            sql += " AND SupplierID = :supplier"
            params['supplier'] = supplier
        sql += " ORDER BY StartDateKey"

        rows = conn.execute(text(sql), params).fetchall()

        total = len(rows)
        behind = sum(1 for r in rows if (r[9] or 0) > 0)
        supplier_tasks = sum(1 for r in rows if r[7])
        pending_export = sum(1 for r in rows if (r[6] or 0) > 0)

        tasks = []
        for r in rows:
            gap = float(r[9]) if r[9] not in (None, '') else 0.0
            status_text = 'On Track'
            status_class = 'success'
            if gap > 5:
                status_text = 'Behind'
                status_class = 'warning'
            if gap > 15:
                status_text = 'Critical'
                status_class = 'danger'

            tasks.append({
                'id': r[0],
                'mpp_id': r[1],
                'name': r[2],
                'start_date': r[3],
                'finish_date': r[4],
                'planned_percent': float(r[5] or 0),
                'actual_percent': float(r[6] or 0),
                'supplier_id': r[7] or '',
                'component_id': r[8] or '',
                'gap': gap,
                'status': status_text,
                'status_class': status_class
            })

        suppliers = conn.execute(text("""
            SELECT DISTINCT SupplierID FROM t_ScheduleTask 
            WHERE ProjectID = :pid AND SupplierID IS NOT NULL AND SupplierID != ''
        """), {'pid': project_id}).fetchall()
        supplier_list = [s[0] for s in suppliers]

        return jsonify({
            'kpis': {
                'total_tasks': total,
                'behind_schedule': behind,
                'supplier_tasks': supplier_tasks,
                'pending_export': pending_export
            },
            'tasks': tasks,
            'suppliers': supplier_list
        })


@api_bp.route('/ms-project/upload', methods=['POST'])
def upload_mpp():
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'Empty file'}), 400
    
    project_id = request.form.get('project_id')
    overwrite = request.form.get('overwrite') == 'true'
    
    engine = get_engine()
    today = int(datetime.datetime.now().strftime("%Y%m%d"))
    imported = 0
    
    try:
        content = file.read().decode('utf-8', errors='ignore')
        try:
            root = ET.fromstring(content)
        except ET.ParseError as e:
            return jsonify({'success': False, 'error': f'Invalid XML: {str(e)}'}), 400
        
        ns = {'mpp': 'http://schemas.microsoft.com/project/2010'}
        tasks = root.findall('.//Task')
        if not tasks:
            tasks = root.findall('.//mpp:Task', ns)
        
        if not tasks:
            return jsonify({'success': False, 'error': 'No tasks found in the file.'}), 400
        
        with engine.connect() as conn:
            if overwrite:
                conn.execute(text("DELETE FROM t_ScheduleTask WHERE ProjectID = :pid"), {'pid': project_id})
            
            for task in tasks:
                name_elem = task.find('Name')
                if name_elem is None:
                    name_elem = task.find('mpp:Name', ns)
                if name_elem is None or not name_elem.text:
                    continue
                name = name_elem.text
                
                uid_elem = task.find('UID')
                if uid_elem is None:
                    uid_elem = task.find('mpp:UID', ns)
                uid = uid_elem.text if uid_elem is not None else None
                
                if uid:
                    existing = conn.execute(text("SELECT TaskID FROM t_ScheduleTask WHERE ProjectID = :pid AND MPPUniqueID = :uid"), {'pid': project_id, 'uid': uid}).fetchone()
                    if existing and not overwrite:
                        continue
                
                start_elem = task.find('Start')
                if start_elem is None:
                    start_elem = task.find('mpp:Start', ns)
                start = None
                if start_elem is not None and start_elem.text:
                    date_str = start_elem.text[:10].replace('-', '')
                    if date_str.isdigit():
                        start = int(date_str)
                
                finish_elem = task.find('Finish')
                if finish_elem is None:
                    finish_elem = task.find('mpp:Finish', ns)
                finish = None
                if finish_elem is not None and finish_elem.text:
                    date_str = finish_elem.text[:10].replace('-', '')
                    if date_str.isdigit():
                        finish = int(date_str)
                
                pct_elem = task.find('PercentComplete')
                if pct_elem is None:
                    pct_elem = task.find('mpp:PercentComplete', ns)
                pct = 0
                if pct_elem is not None and pct_elem.text:
                    try:
                        pct = float(pct_elem.text)
                    except:
                        pct = 0
                
                if existing and overwrite:
                    conn.execute(text("""
                        UPDATE t_ScheduleTask SET
                            TaskName = :name,
                            StartDateKey = :start,
                            FinishDateKey = :finish,
                            PlannedPercent = :pct,
                            LastUpdatedDateKey = :today
                        WHERE TaskID = :tid
                    """), {
                        'name': name[:255],
                        'start': start,
                        'finish': finish,
                        'pct': pct,
                        'today': today,
                        'tid': existing[0]
                    })
                else:
                    conn.execute(text("""
                        INSERT INTO t_ScheduleTask 
                        (ProjectID, MPPUniqueID, TaskName, StartDateKey, FinishDateKey, PlannedPercent, LastUpdatedDateKey)
                        VALUES (:pid, :uid, :name, :start, :finish, :pct, :today)
                    """), {
                        'pid': project_id,
                        'uid': uid or str(imported + 1),
                        'name': name[:255],
                        'start': start,
                        'finish': finish,
                        'pct': pct,
                        'today': today
                    })
                imported += 1
            conn.commit()
        
        # Clean up duplicates
        conn.execute(text("""
            DELETE FROM t_ScheduleTask 
            WHERE TaskID NOT IN (
                SELECT MIN(TaskID) 
                FROM t_ScheduleTask 
                WHERE ProjectID = :pid 
                GROUP BY MPPUniqueID
            ) AND ProjectID = :pid AND MPPUniqueID IS NOT NULL
        """), {'pid': project_id})
        conn.commit()
        
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO t_ScheduleImportLog (ProjectID, ImportDateKey, FileName, RecordsImported, Status)
                VALUES (:pid, :today, :fname, :count, 'Success')
            """), {'pid': project_id, 'today': today, 'fname': file.filename, 'count': imported})
            conn.commit()
        
        return jsonify({'success': True, 'imported': imported})
    except Exception as e:
        print(f"Upload error: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 400

@api_bp.route('/ms-project/export-supplier', methods=['POST'])
def export_supplier_schedule():
    data = request.json
    project_id = data.get('project_id')
    supplier_id = data.get('supplier_id')
    
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT TaskID, TaskName, StartDateKey, FinishDateKey, PlannedPercent, ActualPercent
            FROM t_ScheduleTask
            WHERE ProjectID = :pid AND SupplierID = :supplier
            ORDER BY StartDateKey
        """), {'pid': project_id, 'supplier': supplier_id}).fetchall()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['TaskID', 'TaskName', 'StartDate', 'FinishDate', 'Planned%', 'Actual%', 'Notes'])
    for r in rows:
        writer.writerow([r[0], r[1], r[2], r[3], r[4], r[5], ''])
    output.seek(0)
    
    filename = f"supplier_schedule_{supplier_id}_{datetime.date.today().strftime('%Y%m%d')}.csv"
    return Response(output.getvalue(), mimetype='text/csv', headers={'Content-Disposition': f'attachment; filename={filename}'})

@api_bp.route('/ms-project/import-supplier', methods=['POST'])
def import_supplier_update():
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'Empty file'}), 400
    
    project_id = request.form.get('project_id')
    preview = request.form.get('preview') == 'true'
    
    stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
    reader = csv.DictReader(stream)
    
    updates = []
    for row in reader:
        try:
            task_id = int(row.get('TaskID', 0))
            if task_id:
                updates.append({
                    'task_id': task_id,
                    'actual_percent': float(row.get('Actual%', 0)),
                    'notes': row.get('Notes', '')
                })
        except:
            pass
    
    if preview:
        engine = get_engine()
        with engine.connect() as conn:
            preview_data = []
            for u in updates:
                task = conn.execute(text("""
                    SELECT TaskName, PlannedPercent, ActualPercent
                    FROM t_ScheduleTask WHERE TaskID = :tid AND ProjectID = :pid
                """), {'tid': u['task_id'], 'pid': project_id}).fetchone()
                if task:
                    preview_data.append({
                        'task_id': u['task_id'],
                        'task_name': task[0],
                        'planned_percent': float(task[1] or 0),
                        'current_actual': float(task[2] or 0),
                        'new_actual': u['actual_percent']
                    })
            return jsonify({'preview': preview_data})
    else:
        engine = get_engine()
        today = int(datetime.date.today().strftime("%Y%m%d"))
        updated = 0
        with engine.connect() as conn:
            for u in updates:
                result = conn.execute(text("""
                    UPDATE t_ScheduleTask SET
                        ActualPercent = :pct,
                        LastUpdatedDateKey = :today
                    WHERE TaskID = :tid AND ProjectID = :pid
                """), {'pct': u['actual_percent'], 'today': today, 'tid': u['task_id'], 'pid': project_id})
                updated += result.rowcount
            conn.commit()
        return jsonify({'success': True, 'updated': updated})

@api_bp.route('/ms-project/export-mpp/<string:project_id>')
def export_mpp(project_id):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT MPPUniqueID, TaskName, StartDateKey, FinishDateKey, ActualPercent
            FROM t_ScheduleTask
            WHERE ProjectID = :pid
        """), {'pid': project_id}).fetchall()
    
    root = ET.Element('Project', {'xmlns': 'http://schemas.microsoft.com/project/2010'})
    tasks_elem = ET.SubElement(root, 'Tasks')
    for r in rows:
        task_elem = ET.SubElement(tasks_elem, 'Task')
        ET.SubElement(task_elem, 'UID').text = str(r[0] or '')
        ET.SubElement(task_elem, 'Name').text = r[1] or ''
        
        if r[2] and isinstance(r[2], int):
            date_str = str(r[2])
            if len(date_str) == 8:
                ET.SubElement(task_elem, 'Start').text = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}T00:00:00"
        elif r[2]:
            date_str = str(r[2])
            ET.SubElement(task_elem, 'Start').text = date_str
        
        if r[3] and isinstance(r[3], int):
            date_str = str(r[3])
            if len(date_str) == 8:
                ET.SubElement(task_elem, 'Finish').text = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}T00:00:00"
        elif r[3]:
            date_str = str(r[3])
            ET.SubElement(task_elem, 'Finish').text = date_str
        
        ET.SubElement(task_elem, 'PercentComplete').text = str(r[4] or 0)
    
    xml_str = ET.tostring(root, encoding='unicode')
    return Response(xml_str, mimetype='text/xml', headers={'Content-Disposition': f'attachment; filename=updated_schedule_{datetime.datetime.now().strftime("%Y%m%d")}.xml'})

# ==================== DEBUG ====================
@api_bp.route('/debug_routes')
def debug_routes():
    from flask import current_app
    routes = []
    for rule in current_app.url_map.iter_rules():
        if '/api/project' in str(rule):
            routes.append(str(rule))
    return jsonify(routes)
    
 

@api_bp.route('/debug-customer-projects-keys')
def debug_customer_projects_keys():
    """
    TEMPORARY DEBUG — verify translation keys for customer_projects page.
    Remove after debugging.
    """
    from flask import session
    engine = get_engine()
    
    keys_to_check = [
        'project.customer_projects.title',
        'project.customer_projects.btn.import',
        'project.customer_projects.btn.export',
        'project.customer_projects.btn.create',
        'project.customer_projects.kpi.total',
        'project.customer_projects.kpi.active',
        'project.customer_projects.kpi.on_hold',
        'project.customer_projects.kpi.completed',
        'project.customer_projects.col.id',
        'project.customer_projects.col.company',
        'project.customer_projects.col.name',
        'project.customer_projects.filter.company',
        'global.status',
        'global.all',
        'global.apply_filters',
        'global.save',
        'global.cancel'
    ]
    
    result = {
        'session_lang': session.get('lang', 'NOT SET'),
        'session_all_keys': sorted(list(session.keys())),
        'keys': {}
    }
    
    with engine.connect() as conn:
        for key in keys_to_check:
            for lang in ['en', 'fa']:
                row = conn.execute(text(
                    "SELECT Value FROM t_Translations WHERE translation_key = :k AND Language = :l"
                ), {'k': key, 'l': lang}).fetchone()
                result['keys'][f"{key} [{lang}]"] = row[0] if row else '❌ NOT IN DB'
    
    return jsonify(result)
    
  
  
 
# ==================== TEMPORARY DEBUG (REMOVE AFTER USE) ====================
@api_bp.route('/debug-set-project', methods=['GET', 'POST'])
def debug_set_project():
    """
    TEMPORARY debug endpoint for project selector.
    Remove after debugging.
    """
    from flask import request, session
    if request.method == 'POST':
        data = request.get_json(silent=True) or request.form
        print(f"🔵 POST /debug-set-project received: {data}")
        print(f"🔵 Request content-type: {request.content_type}")
        project_id = data.get('project_id') if data else None
        if project_id:
            session['current_project_id'] = project_id
            session.modified = True
            print(f"🔵 Session set: current_project_id={project_id}")
            return jsonify({
                'status': 'ok',
                'project_id': project_id,
                'session_after': dict(session)
            })
        return jsonify({'status': 'error', 'message': 'No project_id in request'}), 400
    # GET — show current state
    return jsonify({
        'current_project_id': session.get('current_project_id', 'NOT SET'),
        'all_session_keys': sorted(list(session.keys()))
    })
    
 
@api_bp.route('/current-project', methods=['GET'])
def get_current_project():
    """Return the current project_id from session. Always fresh."""
    from flask import session
    return jsonify({
        'project_id': session.get('current_project_id', 'All')
    })