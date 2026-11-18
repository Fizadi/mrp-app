# app/modules/workflow/api.py
from flask import jsonify, request, session, current_app
from . import workflow_bp, workflow_api_bp
from app.core.auth import login_required
from app.modules.auth import verify_password  # noqa: F401 — Playbook G5
import json
import logging
import uuid
from datetime import datetime
from sqlalchemy.sql import text


def get_engine():
    return current_app.extensions['sqlalchemy'].engine


def _new_id():
    return str(uuid.uuid4())


def _today_key():
    return int(datetime.now().strftime('%Y%m%d'))


def _row_dict(row):
    return dict(row._mapping) if row is not None else None


# ============================================================================
# MY TASKS
# ============================================================================

@workflow_api_bp.route('/my-tasks', methods=['GET'])
@login_required
def get_my_tasks():
    """Get tasks assigned to the current user (direct and role-based)."""
    try:
        from flask_login import current_user
        engine = get_engine()
        user_id = current_user.get_id()

        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT UserRoles FROM t_Users WHERE UserID = :uid"),
                {'uid': user_id}
            ).first()

            if not row:
                return jsonify({'tasks': [], 'message': 'User not found'}), 404

            roles = []
            if row._mapping.get('UserRoles'):
                try:
                    roles = json.loads(row._mapping['UserRoles'])
                except Exception:
                    roles = []

        tasks = []

        with engine.connect() as conn:
            # 1. Direct assignments
            direct_rows = conn.execute(
                text("""
                    SELECT TOP 100
                        w.WorkflowID,
                        w.EntityType,
                        w.EntityID,
                        w.CurrentStep,
                        w.Status,
                        w.Priority,
                        w.DueDateKey,
                        w.CreatedDateKey,
                        wf.WorkflowName,
                        wf.Steps
                    FROM t_Workflow w
                    LEFT JOIN t_WorkflowDefinitions wf ON w.WorkflowTemplateID = wf.WorkflowID
                    WHERE w.AssignedToType = 'User'
                      AND w.AssignedTo = :uid
                      AND w.Status IN ('Pending', 'InProgress')
                      AND w.IsActive = '1'
                    ORDER BY w.Priority DESC, w.DueDateKey
                """),
                {'uid': user_id}
            ).fetchall()

            for r in direct_rows:
                m = r._mapping
                steps_data = []
                if m['Steps']:
                    try:
                        steps_data = json.loads(m['Steps'])
                    except Exception:
                        pass

                tasks.append({
                    'workflowId': m['WorkflowID'],
                    'entityType': m['EntityType'],
                    'entityId': m['EntityID'],
                    'currentStep': m['CurrentStep'],
                    'status': m['Status'],
                    'priority': m['Priority'],
                    'dueDateKey': m['DueDateKey'],
                    'createdDateKey': m['CreatedDateKey'],
                    'workflowName': m['WorkflowName'],
                    'steps': steps_data,
                    'assignmentType': 'Direct'
                })

            # 2. Role-based assignments
            if roles:
                placeholders = ','.join([':role_' + str(i) for i in range(len(roles))])
                params = {}
                for i, role in enumerate(roles):
                    params[f'role_{i}'] = role

                role_query = f"""
                    SELECT TOP 100
                        w.WorkflowID,
                        w.EntityType,
                        w.EntityID,
                        w.CurrentStep,
                        w.Status,
                        w.Priority,
                        w.DueDateKey,
                        w.CreatedDateKey,
                        wf.WorkflowName,
                        wf.Steps
                    FROM t_Workflow w
                    LEFT JOIN t_WorkflowDefinitions wf ON w.WorkflowTemplateID = wf.WorkflowID
                    WHERE w.AssignedToType = 'Role'
                      AND w.AssignedTo IN ({placeholders})
                      AND w.Status IN ('Pending', 'InProgress')
                      AND w.IsActive = '1'
                    ORDER BY w.Priority DESC, w.DueDateKey
                """

                role_rows = conn.execute(text(role_query), params).fetchall()
                for r in role_rows:
                    m = r._mapping
                    steps_data = []
                    if m['Steps']:
                        try:
                            steps_data = json.loads(m['Steps'])
                        except Exception:
                            pass

                    tasks.append({
                        'workflowId': m['WorkflowID'],
                        'entityType': m['EntityType'],
                        'entityId': m['EntityID'],
                        'currentStep': m['CurrentStep'],
                        'status': m['Status'],
                        'priority': m['Priority'],
                        'dueDateKey': m['DueDateKey'],
                        'createdDateKey': m['CreatedDateKey'],
                        'workflowName': m['WorkflowName'],
                        'steps': steps_data,
                        'assignmentType': 'Role'
                    })

        return jsonify({'tasks': tasks, 'total': len(tasks)})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ============================================================================
# TASK ACTIONS
# ============================================================================

@workflow_api_bp.route('/task/<string:task_id>/complete', methods=['POST'])
@login_required
def complete_task(task_id):
    """Complete a task step."""
    try:
        from flask_login import current_user
        data = request.get_json() or {}
        action = data.get('action', 'complete')
        comment = data.get('comment', '')

        engine = get_engine()
        user_id = current_user.get_id()
        today_key = _today_key()

        with engine.connect() as conn:
            wf_row = conn.execute(
                text("SELECT * FROM t_Workflow WHERE WorkflowID = :task_id"),
                {'task_id': task_id}
            ).first()

            if not wf_row:
                return jsonify({'success': False, 'error': 'Task not found'}), 404

            wf = wf_row._mapping

            # Permission check
            if wf['AssignedToType'] == 'User' and wf['AssignedTo'] != user_id:
                return jsonify({'success': False, 'error': 'Not authorized'}), 403

            if wf['AssignedToType'] == 'Role':
                user_roles = conn.execute(
                    text("SELECT UserRoles FROM t_Users WHERE UserID = :uid"),
                    {'uid': user_id}
                ).first()
                role_codes = json.loads(user_roles._mapping['UserRoles']) if user_roles and user_roles._mapping['UserRoles'] else []
                if wf['AssignedTo'] not in role_codes:
                    return jsonify({'success': False, 'error': 'Not authorized'}), 403

            try:
                current_step_int = int(wf['CurrentStep'] or 0)
            except (TypeError, ValueError):
                current_step_int = 0

            if action in ('approve', 'complete'):
                new_step = current_step_int + 1
                defn = conn.execute(
                    text("SELECT Steps FROM t_WorkflowDefinitions WHERE WorkflowID = :wfid"),
                    {'wfid': wf['WorkflowTemplateID']}
                ).first()

                total_steps = 0
                if defn and defn._mapping.get('Steps'):
                    try:
                        steps = json.loads(defn._mapping['Steps'])
                        if isinstance(steps, dict) and 'steps' in steps:
                            total_steps = len(steps['steps'])
                        elif isinstance(steps, list):
                            total_steps = len(steps)
                    except Exception:
                        pass

                if new_step > total_steps:
                    conn.execute(
                        text("""
                            UPDATE t_Workflow 
                            SET Status = 'Completed', CompletedDateKey = :today, LastUpdatedDateKey = :today 
                            WHERE WorkflowID = :task_id
                        """),
                        {'today': str(today_key), 'task_id': task_id}
                    )
                    message = 'Task completed - workflow finished'
                else:
                    conn.execute(
                        text("""
                            UPDATE t_Workflow 
                            SET CurrentStep = :step, Status = 'InProgress', LastUpdatedDateKey = :today 
                            WHERE WorkflowID = :task_id
                        """),
                        {'step': str(new_step), 'today': str(today_key), 'task_id': task_id}
                    )
                    message = 'Task completed - moved to next step'

            elif action == 'reject':
                conn.execute(
                    text("""
                        UPDATE t_Workflow 
                        SET Status = 'Rejected', CompletedDateKey = :today, LastUpdatedDateKey = :today 
                        WHERE WorkflowID = :task_id
                    """),
                    {'today': str(today_key), 'task_id': task_id}
                )
                message = 'Task rejected'

            else:
                return jsonify({'success': False, 'error': f'Invalid action: {action}'}), 400

            conn.commit()

            # t_WorkflowHistory does not exist in this schema — skipped.
            # TODO: add history logging when t_WorkflowHistory is created.

            return jsonify({'success': True, 'message': message})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@workflow_api_bp.route('/task/<string:task_id>', methods=['GET'])
@login_required
def get_task(task_id):
    """Get task details."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            task = conn.execute(
                text("""
                    SELECT TOP 1 
                        w.*, 
                        wf.WorkflowName, 
                        wf.WorkflowNameLocal, 
                        wf.Steps
                    FROM t_Workflow w
                    LEFT JOIN t_WorkflowDefinitions wf ON w.WorkflowTemplateID = wf.WorkflowID
                    WHERE w.WorkflowID = :task_id
                """),
                {'task_id': task_id}
            ).first()

            if not task:
                return jsonify({'success': False, 'error': 'Task not found'}), 404

            return jsonify({'success': True, 'task': dict(task._mapping)})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@workflow_api_bp.route('/task/<string:task_id>/delegate', methods=['POST'])
@login_required
def delegate_task(task_id):
    """Delegate a task to another user."""
    try:
        from flask_login import current_user
        data = request.get_json() or {}
        new_user_id = data.get('new_user_id')

        if not new_user_id:
            return jsonify({'success': False, 'error': 'Missing new_user_id'}), 400

        engine = get_engine()
        user_id = current_user.get_id()

        with engine.connect() as conn:
            wf = conn.execute(
                text("SELECT AssignedTo, AssignedToType FROM t_Workflow WHERE WorkflowID = :task_id"),
                {'task_id': task_id}
            ).first()

            if not wf:
                return jsonify({'success': False, 'error': 'Task not found'}), 404

            wfm = wf._mapping
            if wfm['AssignedToType'] == 'User' and wfm['AssignedTo'] != user_id:
                return jsonify({'success': False, 'error': 'Not authorized'}), 403

            conn.execute(
                text("""
                    UPDATE t_Workflow
                    SET AssignedTo = :new_user, AssignedToType = 'User'
                    WHERE WorkflowID = :task_id
                """),
                {'new_user': new_user_id, 'task_id': task_id}
            )
            conn.commit()

            return jsonify({'success': True, 'message': 'Task delegated successfully'})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@workflow_api_bp.route('/users', methods=['GET'])
@login_required
def get_users():
    """Get all users for delegation dropdown."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            users = conn.execute(
                text("""
                    SELECT 
                        UserID as id, 
                        Username, 
                        FullName as name
                    FROM t_Users
                    WHERE IsActive = '1'
                    ORDER BY FullName
                """)
            ).fetchall()

            return jsonify({
                'success': True,
                'users': [
                    {'id': u._mapping['id'], 'username': u._mapping['Username'],
                     'name': u._mapping['name'] or u._mapping['Username']}
                    for u in users
                ]
            })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# DELIVERY
# ============================================================================

@workflow_api_bp.route('/deliveries', methods=['GET'])
@login_required
def get_deliveries():
    """List deliveries with filters and industry filtering."""
    try:
        search = request.args.get('search', '')
        project_id = request.args.get('project_id')
        status = request.args.get('status')
        lang = session.get('lang', 'en')
        industry = session.get('demo_industry', 'valve')

        engine = get_engine()

        with engine.connect() as conn:
            query = """
                SELECT 
                    d.DeliveryID,
                    d.DeliveryNumber,
                    d.CompanyProjectID,
                    d.ItemsJSON,
                    d.DeliveryDateKey,
                    d.DeliveryLocation,
                    d.DeliveryLocationLocal,
                    d.Status,
                    d.AcceptedBy,
                    d.AcceptanceDateKey,
                    cp.ProjectName,
                    cp.ProjectNameLocal,
                    cp.DemoIndustryCode
                FROM t_Delivery d
                LEFT JOIN t_CompanyProject cp ON d.CompanyProjectID = cp.CompanyProjectID
                WHERE 1=1
                  AND (cp.DemoIndustryCode = :industry OR cp.DemoIndustryCode IS NULL)
            """
            params = {'industry': industry}

            if search:
                query += """ AND (
                    d.DeliveryNumber LIKE :search 
                    OR cp.ProjectName LIKE :search 
                    OR cp.ProjectNameLocal LIKE :search 
                    OR d.DeliveryLocation LIKE :search 
                    OR d.DeliveryLocationLocal LIKE :search
                )"""
                params['search'] = f'%{search}%'

            if project_id:
                query += " AND d.CompanyProjectID = :project_id"
                params['project_id'] = project_id

            if status:
                query += " AND d.Status = :status"
                params['status'] = status

            query += " ORDER BY d.DeliveryDateKey DESC"

            deliveries = conn.execute(text(query), params).fetchall()

            result = []
            for d in deliveries:
                m = d._mapping
                project_name = m['ProjectName']
                project_name_local = m['ProjectNameLocal']
                if lang != 'en' and project_name_local and str(project_name_local).strip():
                    project_display = project_name_local
                else:
                    project_display = project_name or 'No Project'

                location = m['DeliveryLocation']
                location_local = m['DeliveryLocationLocal']
                if lang != 'en' and location_local and str(location_local).strip():
                    location_display = location_local
                else:
                    location_display = location or '-'

                items_count = 0
                if m['ItemsJSON']:
                    try:
                        items_count = len(json.loads(m['ItemsJSON']))
                    except Exception:
                        pass

                result.append({
                    'DeliveryID': m['DeliveryID'],
                    'DeliveryNumber': m['DeliveryNumber'],
                    'CompanyProjectID': m['CompanyProjectID'],
                    'ItemsJSON': m['ItemsJSON'],
                    'DeliveryDateKey': m['DeliveryDateKey'],
                    'DeliveryDate': str(m['DeliveryDateKey']) if m['DeliveryDateKey'] else None,
                    'DeliveryLocation': location_display,
                    'Status': m['Status'],
                    'AcceptedBy': m['AcceptedBy'],
                    'AcceptanceDateKey': m['AcceptanceDateKey'],
                    'ProjectName': project_display,
                    'ItemsCount': items_count
                })

            return jsonify({'success': True, 'deliveries': result})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@workflow_api_bp.route('/deliveries', methods=['POST'])
@login_required
def create_delivery():
    """Create a new delivery."""
    try:
        data = request.get_json() or {}
        engine = get_engine()
        today_key = _today_key()

        with engine.begin() as conn:
            max_num = conn.execute(
                text("""
                    SELECT ISNULL(MAX(TRY_CAST(SUBSTRING(DeliveryNumber, 4, LEN(DeliveryNumber)) AS INT)), 0)
                    FROM t_Delivery
                    WHERE DeliveryNumber LIKE 'DL-[0-9]%'
                """)
            ).scalar() or 0

            next_num = max_num + 1
            delivery_number = f"DL-{next_num:05d}"
            new_id = _new_id()

            conn.execute(
                text("""
                    INSERT INTO t_Delivery
                    (DeliveryID, DeliveryNumber, CompanyProjectID, ItemsJSON, DeliveryDateKey, 
                     DeliveryLocation, Status, CreatedDateKey)
                    VALUES (:did, :number, :project_id, :items, :date, :location, 'Preparing', :today)
                """),
                {
                    'did': new_id,
                    'number': delivery_number,
                    'project_id': data.get('project_id'),
                    'items': data.get('items_json', '[]'),
                    'date': str(data.get('delivery_date')) if data.get('delivery_date') else None,
                    'location': data.get('location'),
                    'today': str(today_key)
                }
            )

            return jsonify({
                'success': True,
                'message': 'Delivery created',
                'DeliveryID': new_id,
                'delivery_number': delivery_number
            })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@workflow_api_bp.route('/deliveries/<string:delivery_id>/ship', methods=['POST'])
@login_required
def ship_delivery(delivery_id):
    """Mark delivery as shipped."""
    try:
        engine = get_engine()
        with engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE t_Delivery
                    SET Status = 'Shipped'
                    WHERE DeliveryID = :delivery_id
                """),
                {'delivery_id': delivery_id}
            )
            return jsonify({'success': True, 'message': 'Delivery shipped'})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@workflow_api_bp.route('/deliveries/<string:delivery_id>/accept', methods=['POST'])
@login_required
def accept_delivery(delivery_id):
    """Record delivery acceptance."""
    try:
        data = request.get_json() or {}
        engine = get_engine()

        with engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE t_Delivery
                    SET Status = 'Accepted',
                        AcceptedBy = :accepted_by,
                        AcceptanceDateKey = :accept_date,
                        AcceptanceCertificate = :certificate
                    WHERE DeliveryID = :delivery_id
                """),
                {
                    'accepted_by': data.get('accepted_by'),
                    'accept_date': str(data.get('accept_date')) if data.get('accept_date') else None,
                    'certificate': data.get('certificate'),
                    'delivery_id': delivery_id
                }
            )
            return jsonify({'success': True, 'message': 'Delivery accepted'})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@workflow_api_bp.route('/projects', methods=['GET'])
@login_required
def get_projects():
    """Get projects for dropdown with localized names and industry filtering."""
    try:
        lang = session.get('lang', 'en')
        industry = session.get('demo_industry', 'valve')

        engine = get_engine()
        with engine.connect() as conn:
            projects = conn.execute(
                text("""
                    SELECT 
                        CompanyProjectID, 
                        ProjectName,
                        ProjectNameLocal,
                        DemoIndustryCode
                    FROM t_CompanyProject
                    WHERE Status = 'Active'
                      AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
                    ORDER BY ProjectName
                """),
                {'industry': industry}
            ).fetchall()

            result = []
            for p in projects:
                m = p._mapping
                project_name = m['ProjectName']
                project_name_local = m['ProjectNameLocal']
                if lang != 'en' and project_name_local and str(project_name_local).strip():
                    display_name = project_name_local
                else:
                    display_name = project_name

                result.append({
                    'CompanyProjectID': m['CompanyProjectID'],
                    'ProjectName': display_name,
                    'ProjectNameEnglish': m['ProjectName'],
                    'ProjectNameLocal': m['ProjectNameLocal']
                })

            return jsonify({'success': True, 'projects': result})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# SERVICE REQUESTS
# ============================================================================

@workflow_api_bp.route('/service-requests', methods=['GET'])
@login_required
def get_service_requests():
    """List service requests with filters and industry filtering."""
    try:
        search = request.args.get('search', '')
        status = request.args.get('status')
        warranty_status = request.args.get('warranty_status')
        priority = request.args.get('priority')
        lang = session.get('lang', 'en')
        industry = session.get('demo_industry', 'valve')

        engine = get_engine()

        with engine.connect() as conn:
            query = """
                SELECT 
                    sr.ServiceRequestID,
                    sr.RequestNumber,
                    sr.CustomerID,
                    sr.ProductID,
                    sr.SerialNumberID,
                    sr.IssueType,
                    sr.IssueDescription,
                    sr.Priority,
                    sr.Status,
                    sr.IsUnderWarranty,
                    sr.AssignedTo,
                    sr.Resolution,
                    sr.Cost,
                    sr.CreatedDateKey,
                    c.CustomerName,
                    c.CustomerNameLocal,
                    sn.SerialNumber,
                    p.descEnglish as ProductName,
                    p.descFarsi as ProductNameFarsi,
                    u.FullName as AssignedToName,
                    u.FullNameLocal as AssignedToNameLocal
                FROM t_ServiceRequest sr
                LEFT JOIN t_Customer c ON sr.CustomerID = c.CustomerID
                LEFT JOIN t_SerialNumbers sn ON sr.SerialNumberID = sn.SerialNumberID
                LEFT JOIN t_Product p ON sr.ProductID = p.ProductId
                LEFT JOIN t_Users u ON sr.AssignedTo = u.UserID
                WHERE 1=1
                  AND (c.DemoIndustryCode = :industry OR p.DemoIndustryCode = :industry 
                       OR (c.DemoIndustryCode IS NULL AND p.DemoIndustryCode IS NULL))
            """
            params = {'industry': industry}

            if search:
                query += """ AND (
                    sr.RequestNumber LIKE :search 
                    OR c.CustomerName LIKE :search 
                    OR c.CustomerNameLocal LIKE :search 
                    OR sn.SerialNumber LIKE :search
                )"""
                params['search'] = f'%{search}%'

            if status:
                query += " AND sr.Status = :status"
                params['status'] = status

            if priority:
                query += " AND sr.Priority = :priority"
                params['priority'] = priority

            if warranty_status == 'UnderWarranty':
                query += " AND sr.IsUnderWarranty = '1'"
            elif warranty_status == 'OutOfWarranty':
                query += " AND sr.IsUnderWarranty = '0'"

            query += " ORDER BY sr.CreatedDateKey DESC"

            requests_rows = conn.execute(text(query), params).fetchall()

            result = []
            for r in requests_rows:
                m = r._mapping
                customer_name = m['CustomerName']
                customer_name_local = m['CustomerNameLocal']
                if lang != 'en' and customer_name_local and str(customer_name_local).strip():
                    customer_display = customer_name_local
                else:
                    customer_display = customer_name or 'Unknown'

                product_name = m['ProductName']
                product_name_farsi = m['ProductNameFarsi']
                if lang != 'en' and product_name_farsi and str(product_name_farsi).strip():
                    product_display = product_name_farsi
                else:
                    product_display = product_name or 'Unknown'

                assigned_name = m['AssignedToName']
                assigned_name_local = m['AssignedToNameLocal']
                if lang != 'en' and assigned_name_local and str(assigned_name_local).strip():
                    assigned_display = assigned_name_local
                else:
                    assigned_display = assigned_name or 'Unassigned'

                result.append({
                    'ServiceRequestID': m['ServiceRequestID'],
                    'RequestNumber': m['RequestNumber'],
                    'CustomerID': m['CustomerID'],
                    'ProductID': m['ProductID'],
                    'SerialNumberID': m['SerialNumberID'],
                    'IssueType': m['IssueType'],
                    'IssueDescription': m['IssueDescription'],
                    'Priority': m['Priority'],
                    'Status': m['Status'],
                    'IsUnderWarranty': str(m['IsUnderWarranty']) == '1',
                    'AssignedTo': m['AssignedTo'],
                    'Resolution': m['Resolution'],
                    'Cost': m['Cost'] or 0,
                    'CreatedDateKey': m['CreatedDateKey'],
                    'CustomerName': customer_display,
                    'SerialNumber': m['SerialNumber'],
                    'ProductName': product_display,
                    'AssignedToName': assigned_display,
                    'WarrantyStatus': 'UnderWarranty' if str(m['IsUnderWarranty']) == '1' else 'OutOfWarranty'
                })

            return jsonify({'success': True, 'requests': result})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@workflow_api_bp.route('/service-requests/<string:request_id>', methods=['GET'])
@login_required
def get_service_request(request_id):
    """Get a single service request with full details."""
    try:
        lang = session.get('lang', 'en')
        engine = get_engine()

        with engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT TOP 1
                        sr.ServiceRequestID,
                        sr.RequestNumber,
                        sr.CustomerID,
                        sr.ProductID,
                        sr.SerialNumberID,
                        sr.IssueType,
                        sr.IssueDescription,
                        sr.Priority,
                        sr.Status,
                        sr.IsUnderWarranty,
                        sr.AssignedTo,
                        sr.Resolution,
                        sr.Cost,
                        sr.CreatedDateKey,
                        c.CustomerName,
                        c.CustomerNameLocal,
                        sn.SerialNumber,
                        p.descEnglish as ProductName,
                        p.descFarsi as ProductNameFarsi,
                        u.FullName as AssignedToName,
                        u.FullNameLocal as AssignedToNameLocal
                    FROM t_ServiceRequest sr
                    LEFT JOIN t_Customer c ON sr.CustomerID = c.CustomerID
                    LEFT JOIN t_SerialNumbers sn ON sr.SerialNumberID = sn.SerialNumberID
                    LEFT JOIN t_Product p ON sr.ProductID = p.ProductId
                    LEFT JOIN t_Users u ON sr.AssignedTo = u.UserID
                    WHERE sr.ServiceRequestID = :request_id
                """),
                {'request_id': request_id}
            ).first()

            if not row:
                return jsonify({'success': False, 'error': 'Request not found'}), 404

            m = row._mapping

            customer_name = m['CustomerName']
            customer_name_local = m['CustomerNameLocal']
            if lang != 'en' and customer_name_local and str(customer_name_local).strip():
                customer_display = customer_name_local
            else:
                customer_display = customer_name or 'Unknown'

            product_name = m['ProductName']
            product_name_farsi = m['ProductNameFarsi']
            if lang != 'en' and product_name_farsi and str(product_name_farsi).strip():
                product_display = product_name_farsi
            else:
                product_display = product_name or 'Unknown'

            assigned_name = m['AssignedToName']
            assigned_name_local = m['AssignedToNameLocal']
            if lang != 'en' and assigned_name_local and str(assigned_name_local).strip():
                assigned_display = assigned_name_local
            else:
                assigned_display = assigned_name or 'Unassigned'

            result = {
                'ServiceRequestID': m['ServiceRequestID'],
                'RequestNumber': m['RequestNumber'],
                'CustomerID': m['CustomerID'],
                'ProductID': m['ProductID'],
                'SerialNumberID': m['SerialNumberID'],
                'IssueType': m['IssueType'],
                'IssueDescription': m['IssueDescription'],
                'Priority': m['Priority'],
                'Status': m['Status'],
                'IsUnderWarranty': str(m['IsUnderWarranty']) == '1',
                'AssignedTo': m['AssignedTo'],
                'Resolution': m['Resolution'],
                'Cost': m['Cost'] or 0,
                'CreatedDateKey': m['CreatedDateKey'],
                'CustomerName': customer_display,
                'SerialNumber': m['SerialNumber'],
                'ProductName': product_display,
                'AssignedToName': assigned_display
            }

            return jsonify({'success': True, 'request': result})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@workflow_api_bp.route('/service-requests', methods=['POST'])
@login_required
def create_service_request():
    """Create a new service request."""
    try:
        data = request.get_json() or {}
        engine = get_engine()
        today_key = _today_key()

        with engine.begin() as conn:
            max_num = conn.execute(
                text("""
                    SELECT ISNULL(MAX(TRY_CAST(SUBSTRING(RequestNumber, 4, LEN(RequestNumber)) AS INT)), 0)
                    FROM t_ServiceRequest
                    WHERE RequestNumber LIKE 'SR-[0-9]%'
                """)
            ).scalar() or 0

            next_num = max_num + 1
            request_number = f"SR-{next_num:05d}"
            new_id = _new_id()

            is_under_warranty = '0'
            serial_number = data.get('serial_number')
            if serial_number:
                serial_check = conn.execute(
                    text("""
                        SELECT 1 FROM t_SerialNumbers 
                        WHERE SerialNumber = :serial 
                          AND (WarrantyEndDateKey IS NULL OR WarrantyEndDateKey >= :today)
                    """),
                    {'serial': serial_number, 'today': str(today_key)}
                ).first()
                if serial_check:
                    is_under_warranty = '1'

            conn.execute(
                text("""
                    INSERT INTO t_ServiceRequest
                    (ServiceRequestID, RequestNumber, CustomerID, ProductID, SerialNumberID, IssueType,
                     IssueDescription, Priority, Status, IsUnderWarranty, CreatedDateKey)
                    VALUES (:srid, :number, :customer_id, :product_id, :serial, :issue_type,
                            :description, :priority, 'Open', :warranty, :today)
                """),
                {
                    'srid': new_id,
                    'number': request_number,
                    'customer_id': data.get('customer_id'),
                    'product_id': data.get('product_id'),
                    'serial': data.get('serial_number'),
                    'issue_type': data.get('issue_type'),
                    'description': data.get('issue_description'),
                    'priority': data.get('priority', 'Medium'),
                    'warranty': is_under_warranty,
                    'today': str(today_key)
                }
            )

            return jsonify({
                'success': True,
                'message': 'Service request created',
                'ServiceRequestID': new_id,
                'RequestNumber': request_number
            })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@workflow_api_bp.route('/service-requests/<string:request_id>/assign', methods=['POST'])
@login_required
def assign_service_request(request_id):
    """Assign technician to service request."""
    try:
        data = request.get_json() or {}
        engine = get_engine()
        today_key = _today_key()

        with engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE t_ServiceRequest
                    SET AssignedTo = :technician_id, 
                        Status = 'InProgress', 
                        AssignedDateKey = :today
                    WHERE ServiceRequestID = :request_id
                """),
                {
                    'technician_id': data.get('technician_id'),
                    'today': str(today_key),
                    'request_id': request_id
                }
            )
            return jsonify({'success': True, 'message': 'Technician assigned'})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@workflow_api_bp.route('/service-requests/<string:request_id>/complete', methods=['POST'])
@login_required
def complete_service_request(request_id):
    """Complete a service request."""
    try:
        data = request.get_json() or {}
        engine = get_engine()
        today_key = _today_key()

        with engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE t_ServiceRequest
                    SET Status = 'Resolved',
                        Resolution = :resolution,
                        ResolutionDateKey = :today,
                        Cost = :cost
                    WHERE ServiceRequestID = :request_id
                """),
                {
                    'resolution': data.get('resolution'),
                    'today': str(today_key),
                    'cost': str(data.get('cost', 0)),
                    'request_id': request_id
                }
            )
            return jsonify({'success': True, 'message': 'Service request completed'})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@workflow_api_bp.route('/technicians', methods=['GET'])
@login_required
def get_technicians():
    """Get technicians for assignment."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            technicians = conn.execute(
                text("""
                    SELECT 
                        UserID as id, 
                        Username, 
                        FullName as name
                    FROM t_Users
                    WHERE IsActive = '1'
                    ORDER BY FullName
                """)
            ).fetchall()

            return jsonify({
                'success': True,
                'technicians': [
                    {'id': t._mapping['id'], 'username': t._mapping['Username'],
                     'name': t._mapping['name'] or t._mapping['Username']}
                    for t in technicians
                ]
            })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# WORKFLOW DESIGNER
# ============================================================================

@workflow_api_bp.route('/admin/workflows', methods=['GET'])
@login_required
def get_workflows():
    """Get all workflow definitions for the designer."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            workflows = conn.execute(
                text("""
                    SELECT 
                        wf.WorkflowID as id,
                        wf.WorkflowCode as code,
                        wf.WorkflowName as name,
                        wf.WorkflowNameLocal as name_local,
                        wf.Module as module,
                        wf.EntityType as entityType,
                        wf.Description as description,
                        wf.Steps as steps,
                        wf.IsActive as isActive,
                        wf.Version as version,
                        wf.CreatedDateKey as createdDateKey,
                        wf.ModifiedDateKey as modifiedDateKey
                    FROM t_WorkflowDefinitions wf
                    ORDER BY wf.WorkflowName
                """)
            ).fetchall()

            result = []
            for row in workflows:
                m = row._mapping
                steps_count = 0
                steps_json = m['steps']
                if steps_json:
                    try:
                        steps_data = json.loads(steps_json)
                        if isinstance(steps_data, dict) and 'steps' in steps_data:
                            steps_count = len(steps_data['steps'])
                        elif isinstance(steps_data, list):
                            steps_count = len(steps_data)
                    except Exception:
                        pass

                result.append({
                    'id': m['id'],
                    'code': m['code'],
                    'name': m['name'],
                    'name_local': m['name_local'] or '',
                    'module': m['module'] or '',
                    'entityType': m['entityType'] or '',
                    'description': m['description'] or '',
                    'steps': steps_json,
                    'stepsCount': steps_count,
                    'isActive': str(m['isActive']) == '1' if m['isActive'] is not None else False,
                    'isArchived': False,
                    'version': m['version'] or 1,
                    'createdDateKey': m['createdDateKey'],
                    'modifiedDateKey': m['modifiedDateKey']
                })

            total = len(result)
            active = sum(1 for w in result if w['isActive'])
            pending = sum(1 for w in result if not w['isActive'])

            return jsonify({
                'workflows': result,
                'kpi': {
                    'total_workflows': total,
                    'active_workflows': active,
                    'pending_activations': pending
                }
            })

    except Exception as e:
        logging.error(f"Error getting workflows: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@workflow_api_bp.route('/admin/workflows/<string:workflow_id>', methods=['GET'])
@login_required
def get_workflow(workflow_id):
    """Get a single workflow definition by ID."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT 
                        wf.WorkflowID as id,
                        wf.WorkflowCode as code,
                        wf.WorkflowName as name,
                        wf.WorkflowNameLocal as name_local,
                        wf.Module as module,
                        wf.EntityType as entityType,
                        wf.Description as description,
                        wf.Steps as steps,
                        wf.IsActive as isActive,
                        wf.Version as version,
                        wf.CreatedDateKey as createdDateKey,
                        wf.ModifiedDateKey as modifiedDateKey
                    FROM t_WorkflowDefinitions wf
                    WHERE wf.WorkflowID = :workflow_id
                """),
                {'workflow_id': workflow_id}
            ).first()

            if not row:
                return jsonify({'error': 'Workflow not found'}), 404

            m = row._mapping
            steps_data = []
            if m['steps']:
                try:
                    parsed = json.loads(m['steps'])
                    if isinstance(parsed, dict):
                        steps_data = parsed.get('steps', [])
                    elif isinstance(parsed, list):
                        steps_data = parsed
                except Exception:
                    pass

            return jsonify({
                'id': m['id'],
                'code': m['code'],
                'name': m['name'],
                'name_local': m['name_local'] or '',
                'module': m['module'] or '',
                'entityType': m['entityType'] or '',
                'description': m['description'] or '',
                'steps': steps_data,
                'isActive': str(m['isActive']) == '1' if m['isActive'] is not None else False,
                'isArchived': False,
                'version': m['version'] or 1,
                'createdDateKey': m['createdDateKey'],
                'modifiedDateKey': m['modifiedDateKey']
            })

    except Exception as e:
        logging.error(f"Error getting workflow {workflow_id}: {e}")
        return jsonify({'error': str(e)}), 500


@workflow_api_bp.route('/admin/workflows', methods=['POST'])
@login_required
def create_workflow():
    """Create a new workflow definition."""
    try:
        data = request.get_json() or {}
        engine = get_engine()

        code = (data.get('code') or '').strip()
        name = (data.get('name') or '').strip()
        name_local = (data.get('name_local') or '').strip()
        module = data.get('module', '')
        entity_type = data.get('entityType', '')
        description = data.get('description', '')
        conditions = data.get('conditions', {})
        applicable_to = data.get('applicableTo', {})

        if not code or not name or not module or not entity_type:
            return jsonify({'error': 'Missing required fields'}), 400

        with engine.begin() as conn:
            existing = conn.execute(
                text("SELECT WorkflowID FROM t_WorkflowDefinitions WHERE WorkflowCode = :code"),
                {'code': code}
            ).first()

            if existing:
                return jsonify({'error': 'Workflow code already exists'}), 400

            today_key = _today_key()
            new_id = _new_id()

            steps_data = {
                'conditions': conditions,
                'applicableTo': applicable_to,
                'steps': []
            }
            steps_json = json.dumps(steps_data)

            conn.execute(
                text("""
                    INSERT INTO t_WorkflowDefinitions 
                    (WorkflowID, WorkflowCode, WorkflowName, WorkflowNameLocal, Module, EntityType, 
                     Description, Steps, IsActive, Version, CreatedDateKey, ModifiedDateKey)
                    VALUES (:wid, :code, :name, :name_local, :module, :entity_type, :description, 
                            :steps, '0', '1', :today, :today)
                """),
                {
                    'wid': new_id,
                    'code': code,
                    'name': name,
                    'name_local': name_local,
                    'module': module,
                    'entity_type': entity_type,
                    'description': description,
                    'steps': steps_json,
                    'today': str(today_key)
                }
            )

            return jsonify({
                'success': True,
                'message': 'Workflow created successfully',
                'id': new_id
            })

    except Exception as e:
        logging.error(f"Error creating workflow: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@workflow_api_bp.route('/admin/workflows/<string:workflow_id>', methods=['PUT'])
@login_required
def update_workflow(workflow_id):
    """Update an existing workflow definition."""
    try:
        data = request.get_json() or {}
        engine = get_engine()

        with engine.begin() as conn:
            existing = conn.execute(
                text("SELECT WorkflowID, WorkflowCode, Steps, IsActive FROM t_WorkflowDefinitions WHERE WorkflowID = :workflow_id"),
                {'workflow_id': workflow_id}
            ).first()

            if not existing:
                return jsonify({'error': 'Workflow not found'}), 404

            create_new_version = data.get('create_new_version', False)

            code = (data.get('code') or '').strip()
            name = (data.get('name') or '').strip()
            name_local = (data.get('name_local') or '').strip()
            module = data.get('module', '')
            entity_type = data.get('entityType', '')
            description = data.get('description', '')
            conditions = data.get('conditions', {})
            applicable_to = data.get('applicableTo', {})
            steps_data = data.get('steps', [])
            is_active = data.get('isActive', False)

            today_key = _today_key()

            if create_new_version:
                max_version = conn.execute(
                    text("SELECT ISNULL(MAX(TRY_CAST(Version AS INT)), 0) FROM t_WorkflowDefinitions WHERE WorkflowCode = :code"),
                    {'code': code}
                ).scalar() or 0

                new_version = str(max_version + 1)
                new_id = _new_id()

                steps_json = json.dumps({
                    'conditions': conditions,
                    'applicableTo': applicable_to,
                    'steps': steps_data
                })

                conn.execute(
                    text("""
                        INSERT INTO t_WorkflowDefinitions 
                        (WorkflowID, WorkflowCode, WorkflowName, WorkflowNameLocal, Module, EntityType, 
                         Description, Steps, IsActive, Version, CreatedDateKey, ModifiedDateKey)
                        VALUES (:wid, :code, :name, :name_local, :module, :entity_type, :description, 
                                :steps, '0', :version, :today, :today)
                    """),
                    {
                        'wid': new_id,
                        'code': code,
                        'name': name,
                        'name_local': name_local,
                        'module': module,
                        'entity_type': entity_type,
                        'description': description,
                        'steps': steps_json,
                        'version': new_version,
                        'today': str(today_key)
                    }
                )

                return jsonify({
                    'success': True,
                    'message': 'New version created successfully',
                    'id': new_id,
                    'version': new_version
                })
            else:
                existing_steps = existing._mapping['Steps']
                existing_data = {}
                if existing_steps:
                    try:
                        existing_data = json.loads(existing_steps)
                    except Exception:
                        pass

                final_conditions = conditions if conditions else existing_data.get('conditions', {})
                final_applicable = applicable_to if applicable_to else existing_data.get('applicableTo', {})

                if not steps_data and existing_data.get('steps'):
                    steps_data = existing_data.get('steps', [])

                steps_json = json.dumps({
                    'conditions': final_conditions,
                    'applicableTo': final_applicable,
                    'steps': steps_data
                })

                conn.execute(
                    text("""
                        UPDATE t_WorkflowDefinitions 
                        SET WorkflowCode = :code, 
                            WorkflowName = :name, 
                            WorkflowNameLocal = :name_local, 
                            Module = :module, 
                            EntityType = :entity_type, 
                            Description = :description, 
                            Steps = :steps, 
                            IsActive = :is_active, 
                            ModifiedDateKey = :today
                        WHERE WorkflowID = :workflow_id
                    """),
                    {
                        'code': code,
                        'name': name,
                        'name_local': name_local,
                        'module': module,
                        'entity_type': entity_type,
                        'description': description,
                        'steps': steps_json,
                        'is_active': '1' if is_active else '0',
                        'today': str(today_key),
                        'workflow_id': workflow_id
                    }
                )

                return jsonify({
                    'success': True,
                    'message': 'Workflow updated successfully'
                })

    except Exception as e:
        logging.error(f"Error updating workflow {workflow_id}: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@workflow_api_bp.route('/admin/workflows/<string:workflow_id>/activate', methods=['POST'])
@login_required
def activate_workflow(workflow_id):
    """Activate a workflow definition."""
    try:
        engine = get_engine()
        with engine.begin() as conn:
            workflow = conn.execute(
                text("SELECT WorkflowCode FROM t_WorkflowDefinitions WHERE WorkflowID = :workflow_id"),
                {'workflow_id': workflow_id}
            ).first()

            if not workflow:
                return jsonify({'error': 'Workflow not found'}), 404

            code = workflow._mapping['WorkflowCode']
            today_key = _today_key()

            conn.execute(
                text("""
                    UPDATE t_WorkflowDefinitions 
                    SET IsActive = '0', ModifiedDateKey = :today
                    WHERE WorkflowCode = :code
                """),
                {'today': str(today_key), 'code': code}
            )

            conn.execute(
                text("""
                    UPDATE t_WorkflowDefinitions 
                    SET IsActive = '1', ModifiedDateKey = :today
                    WHERE WorkflowID = :workflow_id
                """),
                {'today': str(today_key), 'workflow_id': workflow_id}
            )

            return jsonify({
                'success': True,
                'message': 'Workflow activated successfully'
            })

    except Exception as e:
        logging.error(f"Error activating workflow {workflow_id}: {e}")
        return jsonify({'error': str(e)}), 500


@workflow_api_bp.route('/admin/workflows/<string:workflow_id>/deactivate', methods=['POST'])
@login_required
def deactivate_workflow(workflow_id):
    """Deactivate a workflow definition."""
    try:
        engine = get_engine()
        today_key = _today_key()

        with engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE t_WorkflowDefinitions 
                    SET IsActive = '0', ModifiedDateKey = :today
                    WHERE WorkflowID = :workflow_id
                """),
                {'today': str(today_key), 'workflow_id': workflow_id}
            )

            return jsonify({
                'success': True,
                'message': 'Workflow deactivated successfully'
            })

    except Exception as e:
        logging.error(f"Error deactivating workflow {workflow_id}: {e}")
        return jsonify({'error': str(e)}), 500


@workflow_api_bp.route('/admin/roles', methods=['GET'])
@login_required
def get_workflow_roles():
    """Get all roles for the workflow designer dropdown."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            roles = conn.execute(
                text("""
                    SELECT 
                        RoleID as id, 
                        RoleCode as code, 
                        RoleName as name
                    FROM t_Roles
                    ORDER BY RoleName
                """)
            ).fetchall()

            return jsonify([
                {'id': r._mapping['id'], 'code': r._mapping['code'], 'name': r._mapping['name']}
                for r in roles
            ])

    except Exception as e:
        logging.error(f"Error getting roles: {e}")
        return jsonify({'error': str(e)}), 500


@workflow_api_bp.route('/admin/workflows/<string:workflow_id>/export', methods=['GET'])
@login_required
def export_workflow(workflow_id):
    """Export a workflow definition as JSON."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT 
                        WorkflowCode, 
                        WorkflowName, 
                        WorkflowNameLocal, 
                        Module, 
                        EntityType,
                        Description, 
                        Steps, 
                        Version
                    FROM t_WorkflowDefinitions
                    WHERE WorkflowID = :workflow_id
                """),
                {'workflow_id': workflow_id}
            ).first()

            if not row:
                return jsonify({'error': 'Workflow not found'}), 404

            m = row._mapping
            steps_data = {}
            if m['Steps']:
                try:
                    steps_data = json.loads(m['Steps'])
                except Exception:
                    steps_data = {'steps': []}

            return jsonify({
                'workflowCode': m['WorkflowCode'],
                'workflowName': m['WorkflowName'],
                'workflowNameLocal': m['WorkflowNameLocal'] or '',
                'module': m['Module'],
                'entityType': m['EntityType'],
                'description': m['Description'] or '',
                'version': m['Version'] or 1,
                'steps': steps_data.get('steps', []),
                'conditions': steps_data.get('conditions', {}),
                'applicableTo': steps_data.get('applicableTo', {})
            })

    except Exception as e:
        logging.error(f"Error exporting workflow {workflow_id}: {e}")
        return jsonify({'error': str(e)}), 500


@workflow_api_bp.route('/admin/workflows/import', methods=['POST'])
@login_required
def import_workflow():
    """Import a workflow definition from JSON."""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400

        file = request.files['file']
        if not file.filename:
            return jsonify({'error': 'No file selected'}), 400
        if not file.filename.endswith('.json'):
            return jsonify({'error': 'File must be JSON'}), 400

        try:
            data = json.loads(file.read().decode('utf-8'))
        except Exception as e:
            return jsonify({'error': f'Invalid JSON: {e}'}), 400

        engine = get_engine()
        today_key = _today_key()

        with engine.begin() as conn:
            code = (data.get('workflowCode') or '').strip()
            name = (data.get('workflowName') or '').strip()
            name_local = (data.get('workflowNameLocal') or '').strip()
            module = data.get('module', '')
            entity_type = data.get('entityType', '')
            description = data.get('description', '')
            steps_list = data.get('steps', [])
            conditions = data.get('conditions', {})
            applicable_to = data.get('applicableTo', {})

            if not code or not name or not module or not entity_type:
                return jsonify({'error': 'Missing required fields in import data'}), 400

            existing = conn.execute(
                text("SELECT WorkflowID FROM t_WorkflowDefinitions WHERE WorkflowCode = :code"),
                {'code': code}
            ).first()

            if existing:
                code = code + '_imported'

            steps_json = json.dumps({
                'conditions': conditions,
                'applicableTo': applicable_to,
                'steps': steps_list
            })

            new_id = _new_id()

            conn.execute(
                text("""
                    INSERT INTO t_WorkflowDefinitions 
                    (WorkflowID, WorkflowCode, WorkflowName, WorkflowNameLocal, Module, EntityType, 
                     Description, Steps, IsActive, Version, CreatedDateKey, ModifiedDateKey)
                    VALUES (:wid, :code, :name, :name_local, :module, :entity_type, :description, 
                            :steps, '0', '1', :today, :today)
                """),
                {
                    'wid': new_id,
                    'code': code,
                    'name': name,
                    'name_local': name_local,
                    'module': module,
                    'entity_type': entity_type,
                    'description': description,
                    'steps': steps_json,
                    'today': str(today_key)
                }
            )

            return jsonify({
                'success': True,
                'message': 'Workflow imported successfully',
                'id': new_id,
                'code': code
            })

    except Exception as e:
        logging.error(f"Error importing workflow: {e}")
        return jsonify({'error': str(e)}), 500


# ============================================================================
# UTILITY ENDPOINTS
# ============================================================================

@workflow_api_bp.route('/test', methods=['GET'])
@login_required
def test_endpoint():
    return jsonify({'message': 'Workflow API is working!'})


@workflow_api_bp.route('/current-user', methods=['GET'])
@login_required
def current_user_info():
    """Get current user info."""
    from flask_login import current_user
    return jsonify({
        'id': current_user.get_id(),
        'username': getattr(current_user, 'username', ''),
        'fullname': getattr(current_user, 'fullname', ''),
        'email': getattr(current_user, 'email', '')
    })