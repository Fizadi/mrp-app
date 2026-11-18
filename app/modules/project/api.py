# app/modules/project/api.py
from flask import request, jsonify, current_app, Response
from sqlalchemy.sql import text
from flask_login import login_required, current_user
import csv
import io
import json
import traceback
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, date


from . import api_bp
from app.modules.auth import verify_password  # noqa: F401 — Playbook G5


def get_engine():
    return current_app.extensions['sqlalchemy'].engine


def _new_id():
    return str(uuid.uuid4())


def _today_key():
    return int(datetime.now().strftime('%Y%m%d'))


def _current_industry():
    return session.get('demo_industry', 'valve') if 'session' in globals() else 'valve'


def cast_numeric(column):
    return f"CAST({column} AS DECIMAL(18,4))"


def is_active_true(value):
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    return str(value).strip().lower() in ('1', 'true', 'yes')


def _industry_filter(col='DemoIndustryCode'):
    return f"({col} = :industry OR {col} IS NULL)"


from flask import session  # needs to be imported at module level for _current_industry


# ==================== PORTFOLIO DATA ====================
@api_bp.route('/data')
@login_required
def portfolio_data():
    engine = get_engine()
    status = request.args.get('status', '')
    project_manager = request.args.get('project_manager', '')
    project_type = request.args.get('project_type', '')
    won_lost = request.args.get('won_lost', 'all')
    start_date = request.args.get('start_date_key', '')
    end_date = request.args.get('end_date_key', '')
    project_id = request.args.get('project_id', 'All')
    industry = _current_industry()

    try:
        with engine.connect() as conn:
            conditions = [_industry_filter('p.DemoIndustryCode')]
            params = {'industry': industry}

            if status:
                conditions.append("p.Status = :status")
                params['status'] = status
            if project_manager:
                conditions.append("p.ProjectManager = :pm")
                params['pm'] = project_manager
            if project_type:
                conditions.append("p.ProjectType = :ptype")
                params['ptype'] = project_type
            # D2=a — Win/Lost is defined by Status, not WinLossReason
            if won_lost == 'won':
                conditions.append("p.Status = 'Won'")
            elif won_lost == 'lost':
                conditions.append("p.Status = 'Lost'")
            if start_date and start_date.strip():
                conditions.append("TRY_CAST(p.StartDateKey AS INT) >= :start_key")
                params['start_key'] = int(start_date)
            if end_date and end_date.strip():
                conditions.append("TRY_CAST(p.EndDateKey AS INT) <= :end_key")
                params['end_key'] = int(end_date)
            if project_id and project_id != 'All':
                conditions.append("p.ProjectID = :proj_id")
                params['proj_id'] = project_id

            where_sql = " AND ".join(conditions) if conditions else "1=1"

            rows = conn.execute(text(f"""
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
            """), params).fetchall()

            if project_id and project_id != 'All':
                active = conn.execute(text("SELECT COUNT(*) FROM t_Project WHERE Status = 'Active' AND ProjectID = :pid"), {'pid': project_id}).scalar() or 0
                on_hold = conn.execute(text("SELECT COUNT(*) FROM t_Project WHERE Status = 'On Hold' AND ProjectID = :pid"), {'pid': project_id}).scalar() or 0
                completed = conn.execute(text("SELECT COUNT(*) FROM t_Project WHERE Status = 'Completed' AND ProjectID = :pid"), {'pid': project_id}).scalar() or 0
                total_value = conn.execute(text("SELECT ISNULL(SUM(CAST(CommittedCost AS DECIMAL(18,4))),0) FROM t_ProjectFinancials WHERE ProjectID = :pid"), {'pid': project_id}).scalar() or 0
                lost_count = conn.execute(text("SELECT COUNT(*) FROM t_Project WHERE Status = 'Lost' AND ProjectID = :pid"), {'pid': project_id}).scalar() or 0
            else:
                active = conn.execute(text(f"SELECT COUNT(*) FROM t_Project WHERE Status = 'Active' AND {_industry_filter()}"), {'industry': industry}).scalar() or 0
                on_hold = conn.execute(text(f"SELECT COUNT(*) FROM t_Project WHERE Status = 'On Hold' AND {_industry_filter()}"), {'industry': industry}).scalar() or 0
                completed = conn.execute(text(f"SELECT COUNT(*) FROM t_Project WHERE Status = 'Completed' AND {_industry_filter()}"), {'industry': industry}).scalar() or 0
                total_value = conn.execute(text(f"""
                    SELECT ISNULL(SUM(CAST(pf.CommittedCost AS DECIMAL(18,4))),0)
                    FROM t_ProjectFinancials pf
                    JOIN t_Project p ON pf.ProjectID = p.ProjectID
                    WHERE {_industry_filter('p.DemoIndustryCode')}
                """), {'industry': industry}).scalar() or 0
                lost_count = conn.execute(text(f"SELECT COUNT(*) FROM t_Project WHERE Status = 'Lost' AND {_industry_filter()}"), {'industry': industry}).scalar() or 0

            projects = []
            for row in rows:
                m = row._mapping
                projects.append({
                    'project_id': m['ProjectID'],
                    'project_name': m['ProjectName'],
                    'customer': m['CustomerName'],
                    'status': m['Status'],
                    'winloss_reason': m['WinLossReason'] or '',
                    'percent_complete': float(m['PercentComplete'] or 0),
                    'project_manager': m['ProjectManager'] or '',
                    'project_type': m['ProjectType'] or '',
                    'value': float(m['Value'] or 0),
                    'created_by': m['CreatedByName'] or ''
                })

            return jsonify({
                'kpis': {
                    'active': active,
                    'on_hold': on_hold,
                    'completed': completed,
                    'total_value': float(total_value),
                    'lost_count': lost_count
                },
                'projects': projects
            })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ==================== FILTER OPTIONS ====================
@api_bp.route('/filter-options')
@login_required
def filter_options():
    engine = get_engine()
    with engine.connect() as conn:
        managers = [r._mapping['ProjectManager'] for r in conn.execute(
            text("SELECT DISTINCT ProjectManager FROM t_Project WHERE ProjectManager IS NOT NULL AND ProjectManager != ''")
        ).fetchall()]
        types = [r._mapping['ProjectType'] for r in conn.execute(
            text("SELECT DISTINCT ProjectType FROM t_Project WHERE ProjectType IS NOT NULL AND ProjectType != ''")
        ).fetchall()]
    return jsonify({
        'project_managers': managers,
        'project_types': types,
        'statuses': ['Active', 'On Hold', 'Completed', 'Cancelled', 'Won', 'Lost']
    })


# ==================== CREATE PROJECT ====================
@api_bp.route('/create', methods=['POST'])
@login_required
def create_project():
    data = request.json or {}
    engine = get_engine()
    today = _today_key()
    created_by = current_user.get_id() if current_user.is_authenticated else None
    project_id = data.get('project_id') or _new_id()

    try:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO t_Project (
                    ProjectID, ProjectName, CompanyID, CustomerCompanyID, Status, WinLossReason,
                    AdditionalAttributes, PercentComplete, ProjectManager,
                    ProjectType, StartDateKey, EndDateKey, CreatedByUserID, CreatedDateKey
                ) VALUES (
                    :pid, :pname, :company, :cust, :status, :winloss,
                    :attrs, :pct, :pmgr,
                    :ptype, :start_key, :end_key, :created_by, :today
                )
            """), {
                'pid': project_id,
                'pname': data['project_name'],
                'company': data.get('company_id', ''),
                'cust': data.get('customer_id'),
                'status': data.get('status', 'Active'),
                'winloss': data.get('winloss_reason'),
                'attrs': json.dumps(data.get('additional_attrs', {})),
                'pct': str(data.get('percent_complete', 0)),
                'pmgr': data.get('project_manager'),
                'ptype': data.get('project_type'),
                'start_key': str(data.get('start_date_key')) if data.get('start_date_key') else None,
                'end_key': str(data.get('end_date_key')) if data.get('end_date_key') else None,
                'created_by': created_by,
                'today': str(today)
            })
            budget = data.get('budget', 0)
            actual = data.get('actual_cost', 0)
            committed = data.get('committed_cost', 0)
            currency = data.get('currency', 'USD')
            if budget or actual or committed:
                conn.execute(text("""
                    INSERT INTO t_ProjectFinancials (FinancialID, ProjectID, Budget, ActualCost, CommittedCost, Currency, LastUpdatedDateKey)
                    VALUES (:fid, :pid, :budget, :actual, :committed, :curr, :today)
                """), {
                    'fid': _new_id(),
                    'pid': project_id,
                    'budget': str(float(budget)),
                    'actual': str(float(actual)),
                    'committed': str(float(committed)),
                    'curr': currency,
                    'today': str(today)
                })
            required_docs = data.get('required_documents', [])
            for doc in required_docs:
                conn.execute(text("""
                    INSERT INTO t_ProjectDocumentType
                    (ProjectDocumentTypeID, ProjectID, DocumentTypeID, IsRequired, MaxRevisions, WorkflowStep, CreatedDateKey, ModifiedDateKey)
                    VALUES (:pdid, :pid, :dtid, :req, :maxrev, :step, :today, :today)
                """), {
                    'pdid': _new_id(),
                    'pid': project_id,
                    'dtid': doc['document_type_id'],
                    'req': '1' if doc.get('is_required') else '0',
                    'maxrev': str(doc.get('max_revisions', 1)),
                    'step': doc.get('workflow_step', ''),
                    'today': str(today)
                })
        return jsonify({'success': True, 'project_id': project_id})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 400


# ==================== UPDATE PROJECT ====================
@api_bp.route('/update/<string:project_id>', methods=['POST'])
@login_required
def update_project(project_id):
    data = request.json or {}
    engine = get_engine()
    today = _today_key()
    try:
        with engine.begin() as conn:
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
                'pct': str(data.get('percent_complete', 0)),
                'pmgr': data.get('project_manager'),
                'ptype': data.get('project_type'),
                'start_key': str(data.get('start_date_key')) if data.get('start_date_key') else None,
                'end_key': str(data.get('end_date_key')) if data.get('end_date_key') else None
            })
            # Replace financials
            conn.execute(text("DELETE FROM t_ProjectFinancials WHERE ProjectID = :pid"), {'pid': project_id})
            conn.execute(text("""
                INSERT INTO t_ProjectFinancials (FinancialID, ProjectID, Budget, ActualCost, CommittedCost, Currency, LastUpdatedDateKey)
                VALUES (:fid, :pid, :budget, :actual, :committed, :curr, :today)
            """), {
                'fid': _new_id(),
                'pid': project_id,
                'budget': str(float(data.get('budget', 0))),
                'actual': str(float(data.get('actual_cost', 0))),
                'committed': str(float(data.get('committed_cost', 0))),
                'curr': data.get('currency', 'USD'),
                'today': str(today)
            })
            # Replace required documents
            conn.execute(text("DELETE FROM t_ProjectDocumentType WHERE ProjectID = :pid"), {'pid': project_id})
            for doc in data.get('required_documents', []):
                conn.execute(text("""
                    INSERT INTO t_ProjectDocumentType
                    (ProjectDocumentTypeID, ProjectID, DocumentTypeID, IsRequired, MaxRevisions, WorkflowStep, CreatedDateKey, ModifiedDateKey)
                    VALUES (:pdid, :pid, :dtid, :req, :maxrev, :step, :today, :today)
                """), {
                    'pdid': _new_id(),
                    'pid': project_id,
                    'dtid': doc['document_type_id'],
                    'req': '1' if doc.get('is_required') else '0',
                    'maxrev': str(doc.get('max_revisions', 1)),
                    'step': doc.get('workflow_step', ''),
                    'today': str(today)
                })
        return jsonify({'success': True})
    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 400


# ==================== DASHBOARD DATA ====================
@api_bp.route('/dashboard_data/<string:project_id>')
@login_required
def dashboard_data(project_id):
    engine = get_engine()
    try:
        with engine.connect() as conn:
            proj = conn.execute(text("""
                SELECT ProjectName, Status, PercentComplete, ProjectManager,
                       WinLossReason, AdditionalAttributes,
                       StartDateKey, EndDateKey, CompanyID, CustomerCompanyID
                FROM t_Project WHERE ProjectID = :pid
            """), {'pid': project_id}).fetchone()
            if not proj:
                return jsonify({'error': 'Project not found'}), 404
            pm = proj._mapping

            try:
                fin = conn.execute(text("""
                    SELECT ISNULL(CAST(Budget        AS DECIMAL(18,4)), 0) AS Budget,
                           ISNULL(CAST(ActualCost    AS DECIMAL(18,4)), 0) AS ActualCost,
                           ISNULL(CAST(CommittedCost AS DECIMAL(18,4)), 0) AS CommittedCost,
                           ISNULL(Currency, 'USD') AS Currency
                    FROM t_ProjectFinancials WHERE ProjectID = :pid
                """), {'pid': project_id}).fetchone()
                if fin:
                    fm = fin._mapping
                    budget = float(fm['Budget'])
                    actual = float(fm['ActualCost'])
                    committed = float(fm['CommittedCost'])
                    currency = fm['Currency']
                else:
                    budget, actual, committed, currency = 0, 0, 0, 'USD'
                budget_util = (actual / budget * 100) if budget > 0 else 0
            except Exception as e:
                print(f"⚠️ Financials query failed for {project_id}: {e}")
                budget, actual, committed, currency = 0, 0, 0, 'USD'
                budget_util = 0

            try:
                milestone = conn.execute(text("""
                    SELECT COUNT(*) AS total,
                           SUM(CASE WHEN Status = 'Completed' THEN 1 ELSE 0 END) AS completed
                    FROM t_ProjectMilestones WHERE ProjectID = :pid
                """), {'pid': project_id}).fetchone()
                mm = milestone._mapping
                total_ms = mm['total'] or 0
                completed_ms = mm['completed'] or 0
                milestone_progress = (completed_ms / total_ms * 100) if total_ms > 0 else 0
            except Exception as e:
                print(f"⚠️ Milestones query failed for {project_id}: {e}")
                milestone_progress = 0

            try:
                today = _today_key()
                reminders = conn.execute(text("""
                    SELECT COUNT(*) FROM t_Reminder
                    WHERE ProjectID = :pid
                      AND TRY_CAST(DueDateKey AS INT) < :today
                      AND ISNULL(Status, '') != 'Completed'
                """), {'pid': project_id, 'today': today}).scalar() or 0
            except Exception as e:
                print(f"⚠️ Reminders query failed for {project_id}: {e}")
                reminders = 0

            return jsonify({
                'project': {
                    'name': pm['ProjectName'],
                    'status': pm['Status'],
                    'percent_complete': float(pm['PercentComplete'] or 0),
                    'manager': pm['ProjectManager'],
                    'winloss_reason': pm['WinLossReason'] or '',
                    'additional_attrs': pm['AdditionalAttributes'] or '{}',
                    'start_date': pm['StartDateKey'],
                    'end_date': pm['EndDateKey'],
                    'company_id': pm['CompanyID'] or '',
                    'customer_id': pm['CustomerCompanyID'] or ''
                },
                'kpis': {
                    'percent_complete': float(pm['PercentComplete'] or 0),
                    'budget_utilisation': round(budget_util, 1),
                    'milestone_progress': round(milestone_progress, 1),
                    'active_service_orders': 0,
                    'pending_reminders': reminders,
                    'warranty_expiry_warning': False
                },
                'financial': {
                    'budget': budget,
                    'actual_cost': actual,
                    'committed_cost': committed,
                    'currency': currency
                }
            })
    except Exception as e:
        print("Dashboard error:", e)
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ==================== MILESTONES ====================
@api_bp.route('/milestones/<string:project_id>')
@login_required
def get_milestones(project_id):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT MilestoneID, MilestoneName, DueDateKey, CompletionDateKey, Status
            FROM t_ProjectMilestones WHERE ProjectID = :pid ORDER BY TRY_CAST(DueDateKey AS INT)
        """), {'pid': project_id}).fetchall()
    return jsonify([{
        'id': r._mapping['MilestoneID'],
        'name': r._mapping['MilestoneName'],
        'planned_date': r._mapping['DueDateKey'],
        'actual_date': r._mapping['CompletionDateKey'],
        'status': r._mapping['Status']
    } for r in rows])


@api_bp.route('/add_milestone', methods=['POST'])
@login_required
def add_milestone():
    data = request.json or {}
    engine = get_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO t_ProjectMilestones (MilestoneID, ProjectID, MilestoneName, DueDateKey, Status)
                VALUES (:mid, :pid, :name, :due, :status)
            """), {
                'mid': _new_id(),
                'pid': data['project_id'],
                'name': data['milestone_name'],
                'due': str(data['planned_date_key']),
                'status': data.get('status', 'Pending')
            })
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@api_bp.route('/milestones_list/<string:project_id>')
@login_required
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
            conditions.append("TRY_CAST(DueDateKey AS INT) >= :from_date")
            params['from_date'] = int(due_date_from)
        if due_date_to and due_date_to.strip():
            conditions.append("TRY_CAST(DueDateKey AS INT) <= :to_date")
            params['to_date'] = int(due_date_to)
        if conditions:
            base_sql += " AND " + " AND ".join(conditions)
        base_sql += " ORDER BY TRY_CAST(DueDateKey AS INT)"

        rows = conn.execute(text(base_sql), params).fetchall()

        total = conn.execute(text("SELECT COUNT(*) FROM t_ProjectMilestones WHERE ProjectID = :pid"), {'pid': project_id}).scalar() or 0
        completed = conn.execute(text("SELECT COUNT(*) FROM t_ProjectMilestones WHERE ProjectID = :pid AND Status = 'Completed'"), {'pid': project_id}).scalar() or 0
        today = _today_key()
        overdue = conn.execute(text("""
            SELECT COUNT(*) FROM t_ProjectMilestones 
            WHERE ProjectID = :pid AND TRY_CAST(DueDateKey AS INT) < :today AND Status != 'Completed'
        """), {'pid': project_id, 'today': today}).scalar() or 0
        on_time = conn.execute(text("""
            SELECT COUNT(*) FROM t_ProjectMilestones 
            WHERE ProjectID = :pid AND Status = 'Completed'
              AND TRY_CAST(CompletionDateKey AS INT) <= TRY_CAST(DueDateKey AS INT)
        """), {'pid': project_id}).scalar() or 0

        milestones = []
        for row in rows:
            m = row._mapping
            due_int = int(m['DueDateKey']) if m['DueDateKey'] else None
            is_overdue = bool(due_int and due_int < today and m['Status'] != 'Completed')
            milestones.append({
                'id': m['MilestoneID'],
                'name': m['MilestoneName'],
                'due_date': m['DueDateKey'],
                'completion_date': m['CompletionDateKey'],
                'status': m['Status'],
                'weight': float(m['Weight']) if m['Weight'] else 0,
                'is_overdue': is_overdue
            })
        return jsonify({
            'kpis': {'total': total, 'completed': completed, 'on_time': on_time, 'overdue': overdue},
            'milestones': milestones
        })


@api_bp.route('/complete_milestone', methods=['POST'])
@login_required
def complete_milestone():
    data = request.json or {}
    engine = get_engine()
    today = _today_key()
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE t_ProjectMilestones
                SET Status = 'Completed', CompletionDateKey = :today
                WHERE MilestoneID = :mid
            """), {'today': str(today), 'mid': data['milestone_id']})
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


# ==================== PRODUCTS ====================
@api_bp.route('/products/<string:project_id>')
@login_required
def get_products(project_id):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT ProductID, RequiredQuantity, UnitPrice,
                   (TRY_CAST(RequiredQuantity AS DECIMAL(18,4)) * TRY_CAST(UnitPrice AS DECIMAL(18,4))) as TotalPrice
            FROM t_ProjectProducts WHERE ProjectID = :pid
        """), {'pid': project_id}).fetchall()
    return jsonify([{
        'id': r._mapping['ProductID'],
        'qty': r._mapping['RequiredQuantity'],
        'unit_price': float(r._mapping['UnitPrice'] or 0),
        'total': float(r._mapping['TotalPrice'] or 0)
    } for r in rows])


# ==================== QUALITY ====================
@api_bp.route('/quality/<string:project_id>')
@login_required
def get_quality(project_id):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT ProjectQualityPlanID, PlanName, PlanStatus, EffectiveDateKey
            FROM t_ProjectQualityPlan WHERE ProjectID = :pid
        """), {'pid': project_id}).fetchall()
    return jsonify([{
        'id': r._mapping['ProjectQualityPlanID'],
        'name': r._mapping['PlanName'],
        'status': r._mapping['PlanStatus'],
        'due_date': r._mapping['EffectiveDateKey']
    } for r in rows])


# ==================== DOCUMENTS ====================
@api_bp.route('/documents/<string:project_id>')
@login_required
def get_documents(project_id):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT DocumentID, DocumentNumber, Title, Revision, Status
            FROM t_EngineeringDocument WHERE ProjectID = :pid
        """), {'pid': project_id}).fetchall()
    return jsonify([{
        'id': r._mapping['DocumentID'],
        'number': r._mapping['DocumentNumber'],
        'title': r._mapping['Title'],
        'rev': r._mapping['Revision'],
        'status': r._mapping['Status']
    } for r in rows])


# ==================== SERVICE ORDERS ====================
@api_bp.route('/service_orders/<string:project_id>')
@login_required
def get_service_orders(project_id):
    engine = get_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT OrderID, OrderNumber, OrderDateKey, Status, TotalAmount
                FROM t_ServiceOrder WHERE ProjectID = :pid
            """), {'pid': project_id}).fetchall()
        return jsonify([{
            'id': r._mapping['OrderID'],
            'number': r._mapping['OrderNumber'],
            'date': r._mapping['OrderDateKey'],
            'status': r._mapping['Status'],
            'amount': float(r._mapping['TotalAmount'] or 0)
        } for r in rows])
    except Exception as e:
        print(f"⚠️ t_ServiceOrder query failed for {project_id}: {e}")
        return jsonify([])


# ==================== REMINDERS ====================
@api_bp.route('/reminders/<string:project_id>')
@login_required
def get_reminders(project_id):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT ReminderID, Title, DueDateKey, Status, AssignedTo
            FROM t_Reminder WHERE ProjectID = :pid ORDER BY TRY_CAST(DueDateKey AS INT)
        """), {'pid': project_id}).fetchall()
    return jsonify([{
        'id': r._mapping['ReminderID'],
        'title': r._mapping['Title'],
        'due_date': r._mapping['DueDateKey'],
        'status': r._mapping['Status'],
        'assigned_to': r._mapping['AssignedTo']
    } for r in rows])


@api_bp.route('/add_reminder', methods=['POST'])
@login_required
def add_reminder():
    data = request.json or {}
    engine = get_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO t_Reminder (ReminderID, ProjectID, Title, DueDateKey, AssignedTo)
                VALUES (:rid, :pid, :title, :due, :assigned)
            """), {
                'rid': _new_id(),
                'pid': data['project_id'],
                'title': data['title'],
                'due': str(data['due_date_key']),
                'assigned': data.get('assigned_to')
            })
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


# ==================== UPDATE FINANCIALS ====================
@api_bp.route('/update_financials', methods=['POST'])
@login_required
def update_financials():
    data = request.json or {}
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE t_ProjectFinancials 
            SET Budget = :budget, ActualCost = :actual, CommittedCost = :committed, Currency = :currency
            WHERE ProjectID = :pid
        """), {
            'pid': data['project_id'],
            'budget': str(data['budget']),
            'actual': str(data['actual_cost']),
            'committed': str(data['committed_cost']),
            'currency': data['currency']
        })
    return jsonify({'success': True})


# ==================== UPDATE ATTRIBUTES ====================
@api_bp.route('/update_attributes', methods=['POST'])
@login_required
def update_attributes():
    data = request.json or {}
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("UPDATE t_Project SET AdditionalAttributes = :attrs WHERE ProjectID = :pid"),
                     {'attrs': json.dumps(data['attributes']), 'pid': data['project_id']})
    return jsonify({'success': True})


# ==================== PROJECT BOM ====================
@api_bp.route('/bom/<string:project_id>')
@login_required
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
        custom = sum(1 for r in rows if is_active_true(r._mapping['IsCustom']))

        components = [{
            'id': r._mapping['ProjectProductID'],
            'component_id': r._mapping['ProductID'],
            'name': r._mapping['descEnglish'] or r._mapping['ProductID'],
            'quantity': float(r._mapping['RequiredQuantity']) if r._mapping['RequiredQuantity'] else 0,
            'is_custom': is_active_true(r._mapping['IsCustom']),
            'alternative': r._mapping['AlternativeComponent'] or '',
            'custom_spec': r._mapping['CustomSpec'] or '',
            'status': r._mapping['Status'] or ''
        } for r in rows]

        return jsonify({
            'kpis': {'total_components': total, 'custom_components': custom},
            'components': components
        })


@api_bp.route('/bom/add', methods=['POST'])
@login_required
def add_project_bom_component():
    data = request.json or {}
    engine = get_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO t_ProjectProducts
                (ProjectProductID, ProjectID, ProductID, RequiredQuantity, IsCustom, AlternativeComponent, CustomSpec, Status)
                VALUES (:ppid, :pid, :prod, :qty, :iscustom, :alt, :spec, 'Active')
            """), {
                'ppid': _new_id(),
                'pid': data['project_id'],
                'prod': data['component_id'],
                'qty': str(float(data['quantity'])),
                'iscustom': '1' if data.get('is_custom') else '0',
                'alt': data.get('alternative', ''),
                'spec': data.get('custom_spec', '')
            })
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@api_bp.route('/bom/delete/<string:line_id>', methods=['POST'])
@login_required
def delete_project_bom_component(line_id):
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM t_ProjectProducts WHERE ProjectProductID = :lid"), {'lid': line_id})
    return jsonify({'success': True})


@api_bp.route('/bom/import', methods=['POST'])
@login_required
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

    with engine.begin() as conn:
        for row in csv_input:
            try:
                conn.execute(text("""
                    INSERT INTO t_ProjectProducts
                    (ProjectProductID, ProjectID, ProductID, RequiredQuantity, IsCustom, AlternativeComponent, CustomSpec, Status)
                    VALUES (:ppid, :pid, :prod, :qty, :iscustom, :alt, :spec, 'Active')
                """), {
                    'ppid': _new_id(),
                    'pid': row['ProjectID'],
                    'prod': row['ProductID'],
                    'qty': str(float(row['RequiredQuantity'])),
                    'iscustom': str(int(row.get('IsCustom', 0) or 0)),
                    'alt': row.get('AlternativeComponent', ''),
                    'spec': row.get('CustomSpec', '')
                })
                created += 1
            except Exception as e:
                errors.append(f"Row {csv_input.line_num}: {str(e)}")
    return jsonify({'success': True, 'created': created, 'errors': errors})


@api_bp.route('/bom/export/<string:project_id>')
@login_required
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
        m = row._mapping
        writer.writerow([m['ProductID'], m['descEnglish'] or m['ProductID'], m['RequiredQuantity'], m['IsCustom'], m['AlternativeComponent'], m['CustomSpec']])
    return Response(output.getvalue(), mimetype='text/csv', headers={"Content-Disposition": f"attachment;filename=project_bom_{project_id}.csv"})


# ==================== DELIVERABLES ====================
@api_bp.route('/deliverables/<string:project_id>')
@login_required
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
        sql += " ORDER BY TRY_CAST(pp.DeliveryDateKey AS INT)"

        rows = conn.execute(text(sql), params).fetchall()

        total = conn.execute(text("SELECT COUNT(*) FROM t_ProjectProducts WHERE ProjectID = :pid"), {'pid': project_id}).scalar() or 0
        pending = conn.execute(text("SELECT COUNT(*) FROM t_ProjectProducts WHERE ProjectID = :pid AND Status = 'Pending'"), {'pid': project_id}).scalar() or 0
        submitted = conn.execute(text("SELECT COUNT(*) FROM t_ProjectProducts WHERE ProjectID = :pid AND Status = 'Submitted'"), {'pid': project_id}).scalar() or 0
        approved = conn.execute(text("SELECT COUNT(*) FROM t_ProjectProducts WHERE ProjectID = :pid AND Status = 'Approved'"), {'pid': project_id}).scalar() or 0
        rejected = conn.execute(text("SELECT COUNT(*) FROM t_ProjectProducts WHERE ProjectID = :pid AND Status = 'Rejected'"), {'pid': project_id}).scalar() or 0

        deliverables = []
        responsibles = set()
        for row in rows:
            m = row._mapping
            name = m['DeliverableName'] or f"Product {m['ProjectProductID']}"
            responsible = m['Responsible'] or ''
            status = m['Status'] or 'Pending'
            if responsible:
                responsibles.add(responsible)
            deliverables.append({
                'id': m['ProjectProductID'],
                'name': name,
                'due_date': m['DeliveryDateKey'],
                'responsible': responsible,
                'status': status,
                'customer_approval': is_active_true(m['CustomerApproval']),
                'notes': m['Notes'] or '',
                'approval_date': m['ApprovalDateKey']
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
@login_required
def submit_deliverable_approval():
    data = request.json or {}
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE t_ProjectProducts
            SET Status = 'Submitted'
            WHERE ProjectProductID = :did AND Status = 'Pending'
        """), {'did': data['deliverable_id']})
    return jsonify({'success': True})


@api_bp.route('/deliverable/approve', methods=['POST'])
@login_required
def approve_deliverable():
    data = request.json or {}
    engine = get_engine()
    today = _today_key()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE t_ProjectProducts
            SET Status = 'Approved', CustomerApproval = '1', ApprovalDateKey = :today
            WHERE ProjectProductID = :did AND Status = 'Submitted'
        """), {'did': data['deliverable_id'], 'today': str(today)})
    return jsonify({'success': True})


@api_bp.route('/deliverable/reject', methods=['POST'])
@login_required
def reject_deliverable():
    data = request.json or {}
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE t_ProjectProducts
            SET Status = 'Rejected', CustomerApproval = '0'
            WHERE ProjectProductID = :did AND Status = 'Submitted'
        """), {'did': data['deliverable_id']})
    return jsonify({'success': True})


@api_bp.route('/deliverables/import', methods=['POST'])
@login_required
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

    with engine.begin() as conn:
        for row in csv_input:
            try:
                conn.execute(text("""
                    INSERT INTO t_ProjectProducts
                    (ProjectProductID, ProjectID, ProductID, DeliveryDateKey, Responsible, Status, Notes)
                    VALUES (:ppid, :pid, :prod, :due, :resp, :status, :notes)
                """), {
                    'ppid': _new_id(),
                    'pid': row['ProjectID'],
                    'prod': row['ProductID'],
                    'due': str(int(row['DeliveryDateKey'])) if row.get('DeliveryDateKey') else None,
                    'resp': row.get('Responsible', ''),
                    'status': row.get('Status', 'Pending'),
                    'notes': row.get('Notes', '')
                })
                created += 1
            except Exception as e:
                errors.append(f"Row {csv_input.line_num}: {str(e)}")
    return jsonify({'success': True, 'created': created, 'errors': errors})


@api_bp.route('/deliverables/export/<string:project_id>')
@login_required
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
        m = row._mapping
        writer.writerow([m['ProductID'], m['descEnglish'] or m['ProductID'], m['DeliveryDateKey'], m['Responsible'], m['Status'], m['Notes']])
    return Response(output.getvalue(), mimetype='text/csv', headers={"Content-Disposition": f"attachment;filename=deliverables_{project_id}.csv"})


# ==================== CUSTOMER PROJECTS ====================
@api_bp.route('/customer_projects')
@login_required
def get_customer_projects():
    status_filter = request.args.get('status', '')
    company_filter = request.args.get('company', '')
    industry = _current_industry()

    engine = get_engine()
    with engine.connect() as conn:
        sql = """
            SELECT cp.CustomerProjectID, c.Name as CompanyName, cp.ProjectName,
                   cp.Location, cp.Value, cp.Status, cp.StartDateKey, cp.EndDateKey,
                   (SELECT COUNT(*) FROM t_Project WHERE CustomerProjectID = cp.CustomerProjectID) as LinkedCount
            FROM t_CustomerProject cp
            LEFT JOIN t_companies c ON cp.CompanyID = c.companyID
            WHERE (cp.DemoIndustryCode = :industry OR cp.DemoIndustryCode IS NULL)
        """
        params = {'industry': industry}
        if status_filter:
            sql += " AND cp.Status = :status"
            params['status'] = status_filter
        if company_filter:
            sql += " AND cp.CompanyID = :company"
            params['company'] = company_filter
        sql += " ORDER BY cp.CustomerProjectID"

        rows = conn.execute(text(sql), params).fetchall()

        total = conn.execute(text(f"SELECT COUNT(*) FROM t_CustomerProject WHERE {_industry_filter()}"), {'industry': industry}).scalar() or 0
        active = conn.execute(text(f"SELECT COUNT(*) FROM t_CustomerProject WHERE Status = 'Active' AND {_industry_filter()}"), {'industry': industry}).scalar() or 0
        completed = conn.execute(text(f"SELECT COUNT(*) FROM t_CustomerProject WHERE Status = 'Completed' AND {_industry_filter()}"), {'industry': industry}).scalar() or 0
        on_hold = conn.execute(text(f"SELECT COUNT(*) FROM t_CustomerProject WHERE Status = 'On Hold' AND {_industry_filter()}"), {'industry': industry}).scalar() or 0

        companies = conn.execute(text("SELECT companyID, Name FROM t_companies WHERE IsCustomer = '1'")).fetchall()

        return jsonify({
            'kpis': {'total': total, 'active': active, 'completed': completed, 'on_hold': on_hold},
            'projects': [{
                'id': r._mapping['CustomerProjectID'],
                'company': r._mapping['CompanyName'] or '',
                'name': r._mapping['ProjectName'],
                'location': r._mapping['Location'] or '',
                'value': float(r._mapping['Value']) if r._mapping['Value'] else 0,
                'status': r._mapping['Status'],
                'start_date': r._mapping['StartDateKey'],
                'end_date': r._mapping['EndDateKey'],
                'linked_count': r._mapping['LinkedCount'] or 0
            } for r in rows],
            'filter_options': {
                'statuses': ['Active', 'On Hold', 'Completed', 'Cancelled'],
                'companies': [{'id': c._mapping['companyID'], 'name': c._mapping['Name']} for c in companies]
            }
        })


@api_bp.route('/customer_project/<string:cp_id>')
@login_required
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
        pm = proj._mapping

        internal = conn.execute(text("""
            SELECT ProjectID, ProjectName, Status, PercentComplete
            FROM t_Project WHERE CustomerProjectID = :pid
        """), {'pid': cp_id}).fetchall()

        contacts = conn.execute(text("""
            SELECT ContactID, ContactName, ContactRole, IsActive
            FROM t_ProjectContact WHERE CustomerProjectID = :pid
        """), {'pid': cp_id}).fetchall()

        return jsonify({
            'project': {
                'id': pm['CustomerProjectID'],
                'company': pm['CompanyName'] or '',
                'name': pm['ProjectName'],
                'description': pm['Description'] or '',
                'location': pm['Location'] or '',
                'value': float(pm['Value']) if pm['Value'] else 0,
                'status': pm['Status'],
                'start_date': pm['StartDateKey'],
                'end_date': pm['EndDateKey']
            },
            'internal_projects': [{
                'id': r._mapping['ProjectID'],
                'name': r._mapping['ProjectName'],
                'status': r._mapping['Status'],
                'progress': r._mapping['PercentComplete']
            } for r in internal],
            'contacts': [{
                'id': r._mapping['ContactID'],
                'name': r._mapping['ContactName'] or '',
                'role': r._mapping['ContactRole'] or '',
                'active': is_active_true(r._mapping['IsActive'])
            } for r in contacts]
        })


@api_bp.route('/customer_project/create', methods=['POST'])
@login_required
def create_customer_project():
    data = request.json or {}
    engine = get_engine()
    created_by = current_user.get_id() if current_user.is_authenticated else None
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO t_CustomerProject
                (CustomerProjectID, CompanyID, ProjectName, Description, Location, Value, Status, StartDateKey, EndDateKey, CreatedByUserID)
                VALUES (:id, :company, :name, :desc, :loc, :value, :status, :start, :end, :created_by)
            """), {
                'id': data.get('customer_project_id') or _new_id(),
                'company': data['company_id'],
                'name': data['project_name'],
                'desc': data.get('description', ''),
                'loc': data.get('location', ''),
                'value': str(float(data.get('value', 0))),
                'status': data.get('status', 'Active'),
                'start': str(data.get('start_date_key')) if data.get('start_date_key') else None,
                'end': str(data.get('end_date_key')) if data.get('end_date_key') else None,
                'created_by': created_by
            })
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


# ==================== RESOURCE ALLOCATION ====================
@api_bp.route('/resources/<string:project_id>')
@login_required
def get_resource_allocations(project_id):
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
        """), {'pid': project_id}).scalar() or 0

        user_count = conn.execute(text("""
            SELECT COUNT(DISTINCT PersonID) FROM t_ProjectContact
            WHERE CustomerProjectID = :pid AND IsActive = '1'
        """), {'pid': project_id}).scalar() or 0

        roles = conn.execute(text("""
            SELECT DISTINCT ContactType FROM t_ProjectContact
            WHERE CustomerProjectID = :pid AND ContactType IS NOT NULL AND ContactType != ''
        """), {'pid': project_id}).fetchall()

        allocations = []
        for row in rows:
            m = row._mapping
            alloc_pct = m['AllocationPercent']
            if alloc_pct is None or alloc_pct == '':
                alloc_pct = 100
            else:
                try:
                    alloc_pct = float(alloc_pct)
                except (ValueError, TypeError):
                    alloc_pct = 100

            allocations.append({
                'id': m['ContactID'],
                'employee': m['FullName'] if m['FullName'] else f"User {m['ContactID']}",
                'role': m['Role'] or '',
                'allocation_percent': alloc_pct,
                'start_date': m['StartDateKey'],
                'end_date': m['EndDateKey'],
                'status': 'Active' if is_active_true(m['Status']) else 'Released'
            })

        return jsonify({
            'kpis': {
                'total_allocated_percent': float(total_alloc),
                'active_users': user_count
            },
            'allocations': allocations,
            'filter_options': {'roles': [r._mapping['ContactType'] for r in roles]}
        })


@api_bp.route('/resources/assign', methods=['POST'])
@login_required
def assign_resource():
    data = request.json or {}
    engine = get_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO t_ProjectContact
                (ContactID, CustomerProjectID, PersonID, ContactType, AllocationPercent, AssignedDateKey, EndDateKey, IsActive)
                VALUES (:cid, :pid, :uid, :role, :percent, :start, :end, '1')
            """), {
                'cid': _new_id(),
                'pid': data['project_id'],
                'uid': data['user_id'],
                'role': data.get('role', ''),
                'percent': str(data.get('allocation_percent', 100)),
                'start': str(data['start_date_key']) if data.get('start_date_key') else None,
                'end': str(data['end_date_key']) if data.get('end_date_key') else None
            })
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


# ==================== PROJECTS LIST ====================
@api_bp.route('/projects_list')
@login_required
def get_all_internal_projects():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT ProjectID, ProjectName FROM t_Project ORDER BY ProjectID")).fetchall()
    return jsonify([{'id': r._mapping['ProjectID'], 'name': r._mapping['ProjectName']} for r in rows])


@api_bp.route('/internal_projects')
@login_required
def get_internal_projects():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT ProjectID, ProjectName FROM t_Project ORDER BY ProjectID")).fetchall()
    return jsonify([{'id': r._mapping['ProjectID'], 'name': r._mapping['ProjectName']} for r in rows])


# ==================== DOCUMENT TYPES ====================
@api_bp.route('/document_types')
@login_required
def get_document_types():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT DocumentTypeID, Name FROM t_DocumentType
            WHERE IsActive = '1' ORDER BY Name
        """)).fetchall()
    return jsonify([{'id': r._mapping['DocumentTypeID'], 'name': r._mapping['Name']} for r in rows])


@api_bp.route('/document_types_list')
@login_required
def list_document_types():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT DocumentTypeID, Name FROM t_DocumentType
            WHERE IsActive = '1' ORDER BY Name
        """)).fetchall()
    return jsonify([{'id': r._mapping['DocumentTypeID'], 'name': r._mapping['Name']} for r in rows])


# ==================== REQUIRED DOCUMENTS ====================
@api_bp.route('/required_documents/<string:project_id>')
@login_required
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
        'id': r._mapping['DocumentTypeID'],
        'name': r._mapping['Name'],
        'is_required': is_active_true(r._mapping['IsRequired']),
        'max_revisions': r._mapping['MaxRevisions'],
        'workflow_step': r._mapping['WorkflowStep'] or ''
    } for r in rows])


# ==================== PRODUCT TREE ====================
@api_bp.route('/<string:project_id>/products')
@login_required
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
                          AND (b.ObsoleteDateKey IS NULL OR TRY_CAST(b.ObsoleteDateKey AS INT) > CAST(GETDATE() AS INT))
                    """), {'pid': parent}).fetchall()
                    for child in children:
                        all_relations.append((parent, child))
                        stack.append(child._mapping['ComponentProductID'])

                node_map = {}
                for parent, child in all_relations:
                    if parent not in node_map:
                        node_map[parent] = []
                    cm = child._mapping
                    node_map[parent].append({
                        'component_id': cm['ComponentProductID'],
                        'component_name': cm['descEnglish'],
                        'quantity': float(cm['Quantity']) if cm['Quantity'] else 0,
                        'scrap_factor': float(cm['ScrapFactor']) if cm['ScrapFactor'] else 0,
                        'effectivity_date': cm['EffectivityDateKey'],
                        'obsolete_date': cm['ObsoleteDateKey'],
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
                m = row._mapping
                products.append({
                    'project_product_id': m['ProjectProductID'],
                    'product_id': m['ProductID'],
                    'product_name': m['descEnglish'],
                    'required_quantity': float(m['RequiredQuantity']) if m['RequiredQuantity'] else 0,
                    'delivery_date': m['DeliveryDateKey'],
                    'status': m['Status'],
                    'unit_price': float(m['UnitPrice']) if m['UnitPrice'] else 0,
                    'notes': m['Notes'],
                    'installation_sequence': m['InstallationSequence'],
                    'site_location': m['SiteLocation'],
                    'responsible': m['Responsible'],
                    'is_custom': is_active_true(m['IsCustom']),
                    'alternative': m['AlternativeComponent'],
                    'custom_spec': m['CustomSpec'],
                    'children': build_bom_tree(m['ProductID'])
                })
            return jsonify({'project_id': project_id, 'products': products})
    except Exception as e:
        print("Error in get_project_products_tree:", e)
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ==================== BOQ ====================
@api_bp.route('/<string:project_id>/boq')
@login_required
def get_project_boq(project_id):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT pp.ProjectProductID, pp.ProductID, p.descEnglish,
                   pp.RequiredQuantity, pp.Unit, pp.UnitPrice,
                   (TRY_CAST(pp.RequiredQuantity AS DECIMAL(18,4)) * TRY_CAST(pp.UnitPrice AS DECIMAL(18,4))) as TotalPrice,
                   pp.Notes
            FROM t_ProjectProducts pp
            LEFT JOIN t_Product p ON pp.ProductID = p.ProductId
            WHERE pp.ProjectID = :pid
        """), {'pid': project_id}).fetchall()
        items = []
        total_value = 0
        for r in rows:
            m = r._mapping
            total = float(m['TotalPrice']) if m['TotalPrice'] else 0
            total_value += total
            items.append({
                'id': m['ProjectProductID'],
                'product_id': m['ProductID'],
                'description': m['descEnglish'] or m['ProductID'] or 'Custom Item',
                'quantity': float(m['RequiredQuantity']) if m['RequiredQuantity'] else 0,
                'unit': m['Unit'] or 'pcs',
                'unit_price': float(m['UnitPrice']) if m['UnitPrice'] else 0,
                'total_price': total,
                'notes': m['Notes']
            })
        return jsonify({
            'items': items,
            'kpis': {'total_value': total_value, 'item_count': len(items)}
        })


@api_bp.route('/boq', methods=['POST'])
@login_required
def add_boq_item():
    data = request.json or {}
    engine = get_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO t_ProjectProducts
                (ProjectProductID, ProjectID, ProductID, RequiredQuantity, Unit, UnitPrice, Notes)
                VALUES (:ppid, :pid, :prod, :qty, :unit, :price, :notes)
            """), {
                'ppid': _new_id(),
                'pid': data['project_id'],
                'prod': data.get('product_id'),
                'qty': str(float(data['quantity'])),
                'unit': data.get('unit', 'pcs'),
                'price': str(float(data.get('unit_price', 0))),
                'notes': data.get('notes', '')
            })
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@api_bp.route('/boq/<string:item_id>', methods=['PUT'])
@login_required
def update_boq_item(item_id):
    data = request.json or {}
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE t_ProjectProducts SET
                ProductID = :prod, RequiredQuantity = :qty, Unit = :unit,
                UnitPrice = :price, Notes = :notes
            WHERE ProjectProductID = :id
        """), {
            'id': item_id,
            'prod': data.get('product_id'),
            'qty': str(float(data['quantity'])),
            'unit': data.get('unit', 'pcs'),
            'price': str(float(data.get('unit_price', 0))),
            'notes': data.get('notes', '')
        })
    return jsonify({'success': True})


@api_bp.route('/boq/<string:item_id>', methods=['DELETE'])
@login_required
def delete_boq_item(item_id):
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM t_ProjectProducts WHERE ProjectProductID = :id"), {'id': item_id})
    return jsonify({'success': True})


# ==================== CERTIFICATES ====================
@api_bp.route('/certificate/<string:cert_id>')
@login_required
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
        cm = cert._mapping

        lines = conn.execute(text("""
            SELECT cl.LineID, pp.ProductID, p.descEnglish, pp.Unit,
                   cl.PreviousQuantity, cl.CompletedQuantity,
                   (TRY_CAST(cl.PreviousQuantity AS DECIMAL(18,4)) + TRY_CAST(cl.CompletedQuantity AS DECIMAL(18,4))) as TotalCompleted,
                   pp.UnitPrice,
                   (TRY_CAST(cl.CompletedQuantity AS DECIMAL(18,4)) * TRY_CAST(pp.UnitPrice AS DECIMAL(18,4))) as Amount
            FROM t_CertificateLine cl
            JOIN t_ProjectProducts pp ON cl.ProjectProductID = pp.ProjectProductID
            LEFT JOIN t_Product p ON pp.ProductID = p.ProductId
            WHERE cl.CertificateID = :cid
        """), {'cid': cert_id}).fetchall()

        return jsonify({
            'certificate': {
                'id': cm['CertificateID'],
                'number': cm['CertificateNumber'],
                'start': cm['PeriodStartDateKey'],
                'end': cm['PeriodEndDateKey'],
                'status': cm['Status'],
                'total_completed': float(cm['TotalCompletedAmount'] or 0),
                'retention_percent': float(cm['RetentionPercent'] or 0),
                'retention_amount': float(cm['RetentionAmount'] or 0),
                'advance_recovery': float(cm['AdvanceRecovery'] or 0),
                'previous_total': float(cm['PreviousTotal'] or 0),
                'net_payable': float(cm['NetPayable'] or 0),
                'notes': cm['Notes'] or ''
            },
            'lines': [{
                'id': l._mapping['LineID'],
                'product_id': l._mapping['ProductID'],
                'description': l._mapping['descEnglish'] or l._mapping['ProductID'],
                'unit': l._mapping['Unit'] or 'pcs',
                'previous_qty': float(l._mapping['PreviousQuantity'] or 0),
                'completed_qty': float(l._mapping['CompletedQuantity'] or 0),
                'total_completed': float(l._mapping['TotalCompleted'] or 0),
                'unit_price': float(l._mapping['UnitPrice'] or 0),
                'amount': float(l._mapping['Amount'] or 0)
            } for l in lines]
        })


@api_bp.route('/<string:project_id>/certificates')
@login_required
def get_certificates(project_id):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT CertificateID, CertificateNumber, PeriodStartDateKey, PeriodEndDateKey,
                   Status, TotalCompletedAmount, NetPayable
            FROM t_ProgressCertificate WHERE ProjectID = :pid ORDER BY CertificateID DESC
        """), {'pid': project_id}).fetchall()
    return jsonify([{
        'id': r._mapping['CertificateID'],
        'number': r._mapping['CertificateNumber'],
        'start': r._mapping['PeriodStartDateKey'],
        'end': r._mapping['PeriodEndDateKey'],
        'status': r._mapping['Status'],
        'total': float(r._mapping['TotalCompletedAmount'] or 0),
        'net': float(r._mapping['NetPayable'] or 0)
    } for r in rows])


@api_bp.route('/certificates', methods=['POST'])
@login_required
def create_certificate():
    data = request.json or {}
    engine = get_engine()
    today = _today_key()
    cert_id = _new_id()
    try:
        with engine.begin() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM t_ProgressCertificate WHERE ProjectID = :pid"),
                                 {'pid': data['project_id']}).scalar() or 0
            number = f"CERT-{data['project_id']}-{count + 1}"

            conn.execute(text("""
                INSERT INTO t_ProgressCertificate
                (CertificateID, ProjectID, CertificateNumber, PeriodStartDateKey, PeriodEndDateKey, Status, CreatedDateKey)
                VALUES (:cid, :pid, :num, :start, :end, 'Draft', :today)
            """), {
                'cid': cert_id,
                'pid': data['project_id'],
                'num': number,
                'start': str(data['start_date_key']),
                'end': str(data['end_date_key']),
                'today': str(today)
            })

            boq_items = conn.execute(text("""
                SELECT ProjectProductID, UnitPrice
                FROM t_ProjectProducts WHERE ProjectID = :pid
            """), {'pid': data['project_id']}).fetchall()

            for item in boq_items:
                conn.execute(text("""
                    INSERT INTO t_CertificateLine 
                    (LineID, CertificateID, ProjectProductID, CompletedQuantity, UnitPrice)
                    VALUES (:lid, :cid, :ppid, '0', :price)
                """), {
                    'lid': _new_id(),
                    'cid': cert_id,
                    'ppid': item._mapping['ProjectProductID'],
                    'price': str(item._mapping['UnitPrice'] or 0)
                })
        return jsonify({'success': True, 'certificate_id': cert_id, 'number': number})
    except Exception as e:
        print("Error creating certificate:", e)
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 400


@api_bp.route('/certificate/<string:cert_id>/lines', methods=['PUT'])
@login_required
def update_certificate_lines(cert_id):
    data = request.json or {}
    engine = get_engine()
    try:
        with engine.begin() as conn:
            for line in data.get('lines', []):
                if line.get('id'):
                    conn.execute(text("""
                        UPDATE t_CertificateLine 
                        SET CompletedQuantity = :qty
                        WHERE LineID = :lid AND CertificateID = :cid
                    """), {'qty': str(line['completed_qty']), 'lid': line['id'], 'cid': cert_id})
                else:
                    conn.execute(text("""
                        INSERT INTO t_CertificateLine 
                        (LineID, CertificateID, ProjectProductID, CompletedQuantity, ServiceDescription, ServiceUnit, ServiceUnitPrice, LineType)
                        VALUES (:lid, :cid, NULL, :qty, :desc, :unit, :price, 'service')
                    """), {
                        'lid': _new_id(),
                        'cid': cert_id,
                        'qty': str(line['completed_qty']),
                        'desc': line.get('description'),
                        'unit': line.get('unit'),
                        'price': str(line.get('unit_price') or 0)
                    })
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


# ==================== MS PROJECT INTEGRATION ====================
@api_bp.route('/ms-project/tasks/<string:project_id>')
@login_required
def get_schedule_tasks(project_id):
    supplier = request.args.get('supplier', '')

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
        sql += " ORDER BY TRY_CAST(StartDateKey AS INT)"

        rows = conn.execute(text(sql), params).fetchall()

        total = len(rows)
        behind = sum(1 for r in rows if float(r._mapping['Gap'] or 0) > 0)
        supplier_tasks = sum(1 for r in rows if r._mapping['SupplierID'])
        pending_export = sum(1 for r in rows if float(r._mapping['ActualPercent'] or 0) > 0)

        tasks = []
        for r in rows:
            m = r._mapping
            gap = float(m['Gap']) if m['Gap'] not in (None, '') else 0.0
            status_text = 'On Track'
            status_class = 'success'
            if gap > 5:
                status_text = 'Behind'
                status_class = 'warning'
            if gap > 15:
                status_text = 'Critical'
                status_class = 'danger'

            tasks.append({
                'id': m['TaskID'],
                'mpp_id': m['MPPUniqueID'],
                'name': m['TaskName'],
                'start_date': m['StartDateKey'],
                'finish_date': m['FinishDateKey'],
                'planned_percent': float(m['PlannedPercent']),
                'actual_percent': float(m['ActualPercent']),
                'supplier_id': m['SupplierID'] or '',
                'component_id': m['ComponentID'] or '',
                'gap': gap,
                'status': status_text,
                'status_class': status_class
            })

        suppliers = conn.execute(text("""
            SELECT DISTINCT SupplierID FROM t_ScheduleTask 
            WHERE ProjectID = :pid AND SupplierID IS NOT NULL AND SupplierID != ''
        """), {'pid': project_id}).fetchall()

        return jsonify({
            'kpis': {
                'total_tasks': total,
                'behind_schedule': behind,
                'supplier_tasks': supplier_tasks,
                'pending_export': pending_export
            },
            'tasks': tasks,
            'suppliers': [s._mapping['SupplierID'] for s in suppliers]
        })


@api_bp.route('/ms-project/upload', methods=['POST'])
@login_required
def upload_mpp():
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'Empty file'}), 400

    project_id = request.form.get('project_id')
    overwrite = request.form.get('overwrite') == 'true'

    engine = get_engine()
    today = _today_key()
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

        with engine.begin() as conn:
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

                existing = None
                if uid:
                    existing = conn.execute(
                        text("SELECT TaskID FROM t_ScheduleTask WHERE ProjectID = :pid AND MPPUniqueID = :uid"),
                        {'pid': project_id, 'uid': uid}
                    ).fetchone()
                    if existing and not overwrite:
                        continue

                start_elem = task.find('Start')
                if start_elem is None:
                    start_elem = task.find('mpp:Start', ns)
                start = None
                if start_elem is not None and start_elem.text:
                    date_str = start_elem.text[:10].replace('-', '')
                    if date_str.isdigit():
                        start = date_str

                finish_elem = task.find('Finish')
                if finish_elem is None:
                    finish_elem = task.find('mpp:Finish', ns)
                finish = None
                if finish_elem is not None and finish_elem.text:
                    date_str = finish_elem.text[:10].replace('-', '')
                    if date_str.isdigit():
                        finish = date_str

                pct_elem = task.find('PercentComplete')
                if pct_elem is None:
                    pct_elem = task.find('mpp:PercentComplete', ns)
                pct = 0.0
                if pct_elem is not None and pct_elem.text:
                    try:
                        pct = float(pct_elem.text)
                    except Exception:
                        pct = 0.0

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
                        'pct': str(pct),
                        'today': str(today),
                        'tid': existing._mapping['TaskID']
                    })
                else:
                    conn.execute(text("""
                        INSERT INTO t_ScheduleTask 
                        (TaskID, ProjectID, MPPUniqueID, TaskName, StartDateKey, FinishDateKey, PlannedPercent, LastUpdatedDateKey)
                        VALUES (:tid, :pid, :uid, :name, :start, :finish, :pct, :today)
                    """), {
                        'tid': _new_id(),
                        'pid': project_id,
                        'uid': uid or str(imported + 1),
                        'name': name[:255],
                        'start': start,
                        'finish': finish,
                        'pct': str(pct),
                        'today': str(today)
                    })
                imported += 1

            # Deduplicate safely — subquery scoped to project, inside transaction
            conn.execute(text("""
                DELETE FROM t_ScheduleTask
                WHERE ProjectID = :pid
                  AND TaskID NOT IN (
                      SELECT MIN(TaskID) FROM t_ScheduleTask
                      WHERE ProjectID = :pid AND MPPUniqueID IS NOT NULL
                      GROUP BY MPPUniqueID
                  )
                  AND MPPUniqueID IS NOT NULL
            """), {'pid': project_id})

            conn.execute(text("""
                INSERT INTO t_ScheduleImportLog (LogID, ProjectID, ImportDateKey, FileName, RecordsImported, Status)
                VALUES (:lid, :pid, :today, :fname, :count, 'Success')
            """), {
                'lid': _new_id(),
                'pid': project_id,
                'today': str(today),
                'fname': file.filename,
                'count': str(imported)
            })

        return jsonify({'success': True, 'imported': imported})
    except Exception as e:
        print(f"Upload error: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 400


@api_bp.route('/ms-project/export-supplier', methods=['POST'])
@login_required
def export_supplier_schedule():
    data = request.json or {}
    project_id = data.get('project_id')
    supplier_id = data.get('supplier_id')

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT TaskID, TaskName, StartDateKey, FinishDateKey, PlannedPercent, ActualPercent
            FROM t_ScheduleTask
            WHERE ProjectID = :pid AND SupplierID = :supplier
            ORDER BY TRY_CAST(StartDateKey AS INT)
        """), {'pid': project_id, 'supplier': supplier_id}).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['TaskID', 'TaskName', 'StartDate', 'FinishDate', 'Planned%', 'Actual%', 'Notes'])
    for r in rows:
        m = r._mapping
        writer.writerow([m['TaskID'], m['TaskName'], m['StartDateKey'], m['FinishDateKey'], m['PlannedPercent'], m['ActualPercent'], ''])

    filename = f"supplier_schedule_{supplier_id}_{date.today().strftime('%Y%m%d')}.csv"
    return Response(output.getvalue(), mimetype='text/csv', headers={'Content-Disposition': f'attachment; filename={filename}'})


@api_bp.route('/ms-project/import-supplier', methods=['POST'])
@login_required
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
            task_id = row.get('TaskID', '').strip()
            if task_id:
                updates.append({
                    'task_id': task_id,
                    'actual_percent': float(row.get('Actual%', 0) or 0),
                    'notes': row.get('Notes', '')
                })
        except Exception:
            pass

    engine = get_engine()
    if preview:
        with engine.connect() as conn:
            preview_data = []
            for u in updates:
                task = conn.execute(text("""
                    SELECT TaskName, PlannedPercent, ActualPercent
                    FROM t_ScheduleTask WHERE TaskID = :tid AND ProjectID = :pid
                """), {'tid': u['task_id'], 'pid': project_id}).fetchone()
                if task:
                    tm = task._mapping
                    preview_data.append({
                        'task_id': u['task_id'],
                        'task_name': tm['TaskName'],
                        'planned_percent': float(tm['PlannedPercent'] or 0),
                        'current_actual': float(tm['ActualPercent'] or 0),
                        'new_actual': u['actual_percent']
                    })
            return jsonify({'preview': preview_data})
    else:
        today = _today_key()
        updated = 0
        with engine.begin() as conn:
            for u in updates:
                result = conn.execute(text("""
                    UPDATE t_ScheduleTask SET
                        ActualPercent = :pct,
                        LastUpdatedDateKey = :today
                    WHERE TaskID = :tid AND ProjectID = :pid
                """), {'pct': str(u['actual_percent']), 'today': str(today), 'tid': u['task_id'], 'pid': project_id})
                updated += result.rowcount or 0
        return jsonify({'success': True, 'updated': updated})


@api_bp.route('/ms-project/export-mpp/<string:project_id>')
@login_required
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
        m = r._mapping
        task_elem = ET.SubElement(tasks_elem, 'Task')
        ET.SubElement(task_elem, 'UID').text = str(m['MPPUniqueID'] or '')
        ET.SubElement(task_elem, 'Name').text = m['TaskName'] or ''

        for col, tag in (('StartDateKey', 'Start'), ('FinishDateKey', 'Finish')):
            val = m[col]
            if val:
                s = str(val)
                if len(s) == 8 and s.isdigit():
                    ET.SubElement(task_elem, tag).text = f"{s[:4]}-{s[4:6]}-{s[6:8]}T00:00:00"
                else:
                    ET.SubElement(task_elem, tag).text = s

        ET.SubElement(task_elem, 'PercentComplete').text = str(m['ActualPercent'] or 0)

    xml_str = ET.tostring(root, encoding='unicode')
    return Response(xml_str, mimetype='text/xml', headers={'Content-Disposition': f'attachment; filename=updated_schedule_{datetime.now().strftime("%Y%m%d")}.xml'})


# ==================== CURRENT PROJECT ====================
@api_bp.route('/current-project', methods=['GET'])
@login_required
def get_current_project():
    """Return the current project_id from session."""
    from flask import session as flask_session
    return jsonify({
        'project_id': flask_session.get('current_project_id', 'All')
    })