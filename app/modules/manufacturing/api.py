# app/modules/manufacturing/api.py
print("🔄 Loading manufacturing API endpoints...")

from flask import request, jsonify, current_app, Response, session
from flask_login import login_required, current_user
from sqlalchemy.sql import text
from datetime import datetime
import json
import csv
import io
from app.services.bom_service import explode_bom, create_child_orders, get_order_hierarchy
from app.services.mrp_service import MRPEngine
from functools import wraps
from . import api_bp
from app.core.date_helpers import format_date as format_date_key
import requests

def get_engine():
    return current_app.extensions['sqlalchemy'].engine

def date_to_key(date_str):
    """Convert YYYY-MM-DD to integer YYYYMMDD."""
    if not date_str:
        return None
    return int(date_str.replace('-', ''))

def get_today_key():
    """Get today's DateKey (YYYYMMDD as integer) for SQL Server."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT (YEAR(GETDATE()) * 10000 + MONTH(GETDATE()) * 100 + DAY(GETDATE())) AS TodayKey")
            ).scalar()
            return result
    except Exception as e:
        print(f"⚠️ Could not get today_key from SQL Server: {e}")
        return int(datetime.now().strftime('%Y%m%d'))

def get_today_datekey_sql():
    """
    Return the SQL expression for today's DateKey.
    Use this in queries where you need to compare with DateKey columns.
    """
    return "(YEAR(GETDATE()) * 10000 + MONTH(GETDATE()) * 100 + DAY(GETDATE()))"

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({'error': 'Authentication required'}), 401
        engine = get_engine()
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT UserRoles FROM t_Users WHERE UserID = :uid"),
                {'uid': current_user.get_id()}
            ).fetchone()
            roles = json.loads(row[0]) if row and row[0] else []
            if 'ADMIN' not in roles:
                return jsonify({'error': 'Admin permission required'}), 403
        return f(*args, **kwargs)
    return decorated


# ============================================================================
# FIX: CURRENT USER ENDPOINT - FIXES 404 ERROR
# ============================================================================

@api_bp.route('/current-user', methods=['GET'])
def current_user_info():
    """Return current user info, or anonymous placeholder.
    Must NOT be wrapped in @login_required — base.html calls it on every page,
    and a 302 here cascades into a dashboard redirect (see handoff)."""
    from flask_login import current_user
    if not current_user.is_authenticated:
        return jsonify({
            'id': None,
            'name': 'Guest',
            'username': '',
            'email': '',
            'fullname': 'Guest',
            'language_code': 'en',
            'authenticated': False,
        })
    return jsonify({
        'id': current_user.id,
        'name': current_user.fullname,
        'username': current_user.username,
        'email': current_user.email,
        'fullname': current_user.fullname,
        'language_code': getattr(current_user, 'language_code', 'en'),
        'authenticated': True,
    })

# ============================================================================
# FIX: PROJECTS LIST ENDPOINT - FIXES 404 ERROR
# ============================================================================

@api_bp.route('/projects/list', methods=['GET'])
@login_required
def projects_list():
    """List all projects for dropdown."""
    industry = session.get('demo_industry', 'valve')
    lang = session.get('lang', 'en')
    
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT ProjectID, ProjectName, ProjectNameLocal
                FROM t_Project 
                WHERE DemoIndustryCode = :industry OR DemoIndustryCode IS NULL
                ORDER BY ProjectName
            """),
            {'industry': industry}
        ).fetchall()
    
    projects = []
    for r in rows:
        name = r[1]
        if lang != 'en' and r[2] and str(r[2]).strip():
            name = r[2]
        
        projects.append({
            'ProjectID': r[0],
            'ProjectName': name,
            'ProjectNameEnglish': r[1],
            'ProjectNameLocal': r[2]
        })
    
    return jsonify(projects)


# ============================================================================
# WORKFLOW MODULE API HELPER - FIXED
# ============================================================================

def call_workflow_api(endpoint, method='GET', data=None):
    """Helper to call Workflow module API."""
    try:
        from app.services.workflow_service import WorkflowService
        
        print(f"📡 call_workflow_api: endpoint={endpoint}, method={method}, data={data}")
        
        if 'status' in endpoint and method.upper() == 'GET':
            parts = endpoint.split('/')
            parts = [p for p in parts if p]
            
            if len(parts) >= 3:
                entity_type = parts[1]
                entity_id = parts[2]
            else:
                entity_type = data.get('entity_type') if data else None
                entity_id = data.get('entity_id') if data else None
            
            if entity_type and entity_id:
                result = WorkflowService.get_workflow_status(entity_type, entity_id)
                if result is not None:
                    return result, None
                return None, 'not_found'
            return None, 'Invalid request format'
        
        elif 'check' in endpoint and method.upper() == 'GET':
            parts = endpoint.split('/')
            parts = [p for p in parts if p]
            
            if len(parts) >= 3:
                entity_type = parts[1]
                entity_id = parts[2]
            else:
                entity_type = data.get('entity_type') if data else None
                entity_id = data.get('entity_id') if data else None
                
            if entity_type and entity_id:
                result = WorkflowService.check_workflow_complete(entity_type, entity_id)
                return result, None
            return None, 'Invalid request format'
        
        else:
            return None, f'Unsupported endpoint: {endpoint}'
            
    except ImportError as e:
        current_app.logger.error(f"Could not import WorkflowService: {e}")
        import traceback
        traceback.print_exc()
        return None, f'Workflow service not available: {str(e)}'
    except Exception as e:
        current_app.logger.error(f"Workflow API call error: {str(e)}")
        import traceback
        traceback.print_exc()
        return None, f'Workflow API call failed: {str(e)}'



# ============================================================================
# JOB ORDER DETAIL & SHOP FLOOR DASHBOARD API ENDPOINTS
# ============================================================================

def get_order_workflow_status(order_id, engine):
    """Get workflow status for this order - SQL Server compatible."""
    try:
        with engine.connect() as conn:
            order = conn.execute(
                text("SELECT WorkflowID FROM t_ProductionOrder WHERE ProductionOrderId = :oid"),
                {'oid': order_id}
            ).first()
            
            if not order or not order[0]:
                return {
                    'hasWorkflow': False,
                    'status': 'NotStarted',
                    'message': 'No workflow associated with this order'
                }
            
            workflow_id = order[0]
            
            workflow = conn.execute(
                text("""
                    SELECT TOP 1
                        w.WorkflowID,
                        w.Status,
                        w.CurrentStep,
                        wf.WorkflowName,
                        wf.Steps,
                        w.CreatedDateKey,
                        w.StartedDateKey,
                        w.CompletedDateKey,
                        w.AssignedTo,
                        u.FullName as AssignedToName,
                        w.DueDateKey
                    FROM t_Workflow w
                    JOIN t_WorkflowDefinitions wf ON w.WorkflowTemplateID = wf.WorkflowID
                    LEFT JOIN t_Users u ON w.AssignedTo = u.UserID
                    WHERE w.WorkflowID = :wfid
                """),
                {'wfid': workflow_id}
            ).first()
            
            if not workflow:
                return {
                    'hasWorkflow': False,
                    'status': 'NotStarted',
                    'message': 'Workflow not found'
                }
            
            try:
                current_step = int(workflow[2] or 0)
            except (ValueError, TypeError):
                current_step = 0
            
            steps = json.loads(workflow[4]) if workflow[4] else []
            
            current_step_name = 'Complete'
            if steps and current_step > 0:
                step_index = current_step - 1
                if step_index < len(steps):
                    current_step_name = steps[step_index].get('name', f'Step {current_step}')
            
            # Add formatted dates
            created_date_key = workflow[5]
            started_date_key = workflow[6]
            completed_date_key = workflow[7]
            due_date_key = workflow[10]
            
            return {
                'hasWorkflow': True,
                'workflowId': workflow[0],
                'status': workflow[1] or 'Pending',
                'currentStep': current_step,
                'workflowName': workflow[3],
                'steps': steps,
                'currentStepName': current_step_name,
                'currentStepRole': steps[current_step - 1]['role'] if steps and current_step > 0 and current_step <= len(steps) else None,
                'createdDateKey': created_date_key,
                'createdDateFormatted': format_date_key(created_date_key) if created_date_key else '',
                'startedDateKey': started_date_key,
                'startedDateFormatted': format_date_key(started_date_key) if started_date_key else '',
                'completedDateKey': completed_date_key,
                'completedDateFormatted': format_date_key(completed_date_key) if completed_date_key else '',
                'assignedTo': workflow[8],
                'assignedToName': workflow[9],
                'dueDateKey': due_date_key,
                'dueDateFormatted': format_date_key(due_date_key) if due_date_key else '',
                'isComplete': workflow[1] == 'Completed'
            }
            
    except Exception as e:
        print(f"❌ Error getting workflow status: {e}")
        import traceback
        traceback.print_exc()
        return {
            'hasWorkflow': False,
            'status': 'Error',
            'message': str(e)
        }


@api_bp.route('/orders/<string:order_id>/detail')
@login_required
def get_job_order_detail(order_id):
    """
    Get comprehensive job order detail for the dashboard.
    Includes header info, routing, components, quality, serials, and workflow.
    """
    print(f"🔍 get_job_order_detail called for order: {order_id}")
    
    engine = get_engine()
    industry = session.get('demo_industry', 'valve')
    
    with engine.connect() as conn:
        order = conn.execute(
            text("""
                SELECT 
                    po.ProductionOrderId,
                    po.ProductId,
                    p.descEnglish as ProductName,
                    p.descFarsi as ProductNameFarsi,
                    po.OrderType,
                    po.OrderQuantity,
                    po.Status,
                    po.Priority,
                    po.StartDateKey,
                    po.EndDateKey,
                    po.ScheduleStartDateKey,
                    po.ScheduleEndDateKey,
                    po.ActualStartDateKey,
                    po.ActualEndDateKey,
                    po.ProjectID,
                    pr.ProjectName,
                    po.OrderSource,
                    po.SalesOrderID,
                    po.CustomerReference,
                    po.EngineeringVersion,
                    po.BOMVersion,
                    po.RoutingVersion,
                    po.EstimatedTotalCost,
                    po.ActualTotalCost,
                    po.CostVariance,
                    po.HoldReason,
                    po.BlockedReason,
                    po.QualityStatus,
                    po.WorkflowStatus,
                    po.CurrentRoutingStep,
                    po.NextRoutingStep,
                    po.DemoIndustryCode
                FROM t_ProductionOrder po
                LEFT JOIN t_Product p ON po.ProductId = p.ProductId
                LEFT JOIN t_Project pr ON po.ProjectID = pr.ProjectID
                WHERE po.ProductionOrderId = :oid
            """),
            {'oid': order_id}
        ).first()
        
        if not order:
            return jsonify({'error': 'Order not found'}), 404
        
        lang = session.get('lang', 'en')
        product_name = order[2]
        if lang != 'en' and order[3] and str(order[3]).strip():
            product_name = order[3]
        
        routing = get_order_routing(order_id, order[1], engine)
        components = get_order_components(order_id, order[1], engine)
        quality = get_order_quality(order_id, order[1], engine)
        serials = get_order_serials(order_id, engine)
        workflow = get_order_workflow_status(order_id, engine)
        stats = calculate_order_stats(order, routing, components, serials, quality)
        
        # Format date keys
        start_date_key = order[8]
        end_date_key = order[9]
        schedule_start_key = order[10]
        schedule_end_key = order[11]
        actual_start_key = order[12]
        actual_end_key = order[13]
        
        return jsonify({
            'order': {
                'id': order[0],
                'productId': order[1],
                'productName': product_name,
                'orderType': order[4],
                'quantity': order[5],
                'status': order[6],
                'priority': order[7],
                'startDateKey': start_date_key,
                'startDateFormatted': format_date_key(start_date_key) if start_date_key else '',
                'endDateKey': end_date_key,
                'endDateFormatted': format_date_key(end_date_key) if end_date_key else '',
                'scheduleStartDateKey': schedule_start_key,
                'scheduleStartDateFormatted': format_date_key(schedule_start_key) if schedule_start_key else '',
                'scheduleEndDateKey': schedule_end_key,
                'scheduleEndDateFormatted': format_date_key(schedule_end_key) if schedule_end_key else '',
                'actualStartDateKey': actual_start_key,
                'actualStartDateFormatted': format_date_key(actual_start_key) if actual_start_key else '',
                'actualEndDateKey': actual_end_key,
                'actualEndDateFormatted': format_date_key(actual_end_key) if actual_end_key else '',
                'projectId': order[14],
                'projectName': order[15],
                'orderSource': order[16],
                'salesOrderId': order[17],
                'customerReference': order[18],
                'engineeringVersion': order[19] or 1,
                'bomVersion': order[20] or 1,
                'routingVersion': order[21] or 1,
                'estimatedTotalCost': float(order[22] or 0),
                'actualTotalCost': float(order[23] or 0),
                'costVariance': float(order[24] or 0),
                'holdReason': order[25],
                'blockedReason': order[26],
                'qualityStatus': order[27] or 'Pending',
                'workflowStatus': order[28] or 'NotStarted',
                'currentRoutingStep': order[29],
                'nextRoutingStep': order[30],
                'industry': order[31] or 'valve'
            },
            'routing': routing,
            'components': components,
            'quality': quality,
            'serials': serials,
            'workflow': workflow,
            'stats': stats
        })


def get_order_routing(order_id, product_id, engine):
    """Get routing with actual vs planned times."""
    today_sql = get_today_datekey_sql()
    
    with engine.connect() as conn:
        rows = conn.execute(
            text(f"""
                SELECT 
                    CAST(r.OperationSequence AS INT) as OperationSequence,
                    r.WorkCenterID,
                    wc.Name as WorkCenterName,
                    wc.Type as WorkCenterType,
                    CAST(r.SetupTimeHours AS DECIMAL(18,4)) as PlannedSetup,
                    CAST(r.RunTimeHoursPerUnit AS DECIMAL(18,4)) as PlannedRun,
                    CAST((CAST(r.SetupTimeHours AS DECIMAL(18,4)) + (CAST(r.RunTimeHoursPerUnit AS DECIMAL(18,4)) * :qty)) AS DECIMAL(18,4)) as PlannedTotal,
                    CAST(pd.SetupTimeHours AS DECIMAL(18,4)) as ActualSetup,
                    CAST(pd.RunTimeHours AS DECIMAL(18,4)) as ActualRun,
                    pd.ActualStart,
                    pd.ActualEnd,
                    pd.Status as StepStatus,
                    pd.BatchId,
                    pd.MachineID,
                    m.MachineCode,
                    m.MachineName
                FROM t_WorkCenterRouting r
                JOIN t_WorkCenter wc ON r.WorkCenterID = wc.WorkCenterID
                LEFT JOIN t_ProductionOrderDetail pd ON pd.ProductionOrderId = :oid 
                    AND pd.OperationSequence = r.OperationSequence
                LEFT JOIN t_Machine m ON pd.MachineID = m.MachineID
                WHERE r.ProductID = :pid
                  AND (r.ObsoleteDateKey IS NULL OR r.ObsoleteDateKey > {today_sql})
                ORDER BY CAST(r.OperationSequence AS INT)
            """),
            {'oid': order_id, 'pid': product_id, 'qty': 1}
        ).fetchall()
    
    routing = []
    for r in rows:
        planned_total = float(r[6] or 0)
        actual_setup = float(r[7] or 0)
        actual_run = float(r[8] or 0)
        actual_total = actual_setup + actual_run
        
        status = r[11] or 'Pending'
        is_completed = status == 'Completed'
        is_in_progress = status == 'InProgress'
        
        routing.append({
            'sequence': int(r[0]) if r[0] else 0,
            'workCenterId': r[1],
            'workCenterName': r[2] or r[1],
            'workCenterType': r[3] or 'Internal',
            'plannedSetup': float(r[4] or 0),
            'plannedRun': float(r[5] or 0),
            'plannedTotal': planned_total,
            'actualSetup': actual_setup,
            'actualRun': actual_run,
            'actualTotal': actual_total,
            'varianceSetup': actual_setup - float(r[4] or 0),
            'varianceRun': actual_run - float(r[5] or 0),
            'varianceTotal': actual_total - planned_total,
            'actualStart': r[9],
            'actualEnd': r[10],
            'status': status,
            'isCompleted': is_completed,
            'isInProgress': is_in_progress,
            'isPending': not is_completed and not is_in_progress,
            'batchId': r[12],
            'machineId': r[13],
            'machineCode': r[14],
            'machineName': r[15]
        })
    
    return routing


def get_order_components(order_id, product_id, engine):
    """Get BOM components with their current stages."""
    today_sql = get_today_datekey_sql()
    
    with engine.connect() as conn:
        rows = conn.execute(
            text(f"""
                SELECT 
                    b.ComponentProductID,
                    p.descEnglish as ComponentName,
                    p.descFarsi as ComponentNameFarsi,
                    CAST(b.Quantity AS DECIMAL(18,4)) as RequiredQuantity,
                    CAST(b.BOMLevel AS INT) as BOMLevel,
                    CAST(b.OperationSequence AS INT) as OperationSequence,
                    CAST(COALESCE(pc.IssuedQuantity, 0) AS DECIMAL(18,4)) as IssuedQuantity,
                    CAST(COALESCE(ioh.Qty, 0) AS DECIMAL(18,4)) as AvailableQuantity
                FROM t_BOM b
                JOIN t_Product p ON b.ComponentProductID = p.ProductId
                LEFT JOIN t_ProductionOrderComponents pc ON pc.ComponentProductID = b.ComponentProductID 
                    AND pc.ProductionOrderId = :oid
                LEFT JOIN t_InventoryOnHand ioh ON b.ComponentProductID = ioh.ProductID
                WHERE b.ParentProductID = :pid
                  AND (b.ObsoleteDateKey IS NULL OR b.ObsoleteDateKey > {today_sql})
                  AND b.Status = 'Active'
                ORDER BY CAST(b.BOMLevel AS INT), CAST(b.OperationSequence AS INT)
            """),
            {'oid': order_id, 'pid': product_id}
        ).fetchall()
    
    lang = session.get('lang', 'en')
    components = []
    for r in rows:
        comp_name = r[1]
        if lang != 'en' and r[2] and str(r[2]).strip():
            comp_name = r[2]
        
        required = float(r[3] or 0)
        issued = float(r[6] or 0)
        available = float(r[7] or 0)
        shortage = max(0, required - available)
        
        if issued >= required:
            status = 'Fully Issued'
        elif issued > 0:
            status = 'Partially Issued'
        else:
            status = 'Pending'
        
        if issued >= required:
            stage = 'Complete'
        elif issued > 0:
            stage = 'In Progress'
        else:
            stage = 'Not Started'
        
        components.append({
            'componentId': r[0],
            'componentName': comp_name,
            'requiredQuantity': required,
            'bomLevel': int(r[4] or 1),
            'operationSequence': int(r[5] or 0),
            'issuedQuantity': issued,
            'stage': stage,
            'status': status,
            'shortageQuantity': shortage,
            'availableQuantity': available,
            'currentWorkCenter': 'STOCK',
            'isShortage': shortage > 0,
            'isFullyIssued': issued >= required,
            'isPartiallyIssued': 0 < issued < required
        })
    
    return components

def get_order_quality(order_id, product_id, engine):
    """Get quality operations and their results for this order."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT 
                    qoa.AssignmentID,
                    qoa.QCOperationID,
                    qom.Name as OperationName,
                    qom.Category,
                    qoa.IsRequired,
                    qoa.IsCritical,
                    qoa.SamplingSize,
                    qoa.SamplingFrequency,
                    qoa.SpecMin,
                    qoa.SpecTarget,
                    qoa.SpecMax,
                    qoa.MeasurementUnit,
                    qoa.WorkCenterID,
                    wc.Name as WorkCenterName,
                    qoa.OperationSequence,
                    ib.BatchNumber,
                    ib.Status as BatchStatus,
                    ib.OverallResult as BatchResult,
                    ib.InspectionDateKey,
                    ir.ResultID,
                    ir.PassFail,
                    ir.MeasuredValue,
                    ir.InspectedBy,
                    ir.InspectedDateKey,
                    ir.InspectorNotes,
                    u.FullName as InspectorName
                FROM t_QualityOperationAssignment qoa
                JOIN t_QualityOperationMaster qom ON qoa.QCOperationID = qom.DefinitionID
                LEFT JOIN t_WorkCenter wc ON qoa.WorkCenterID = wc.WorkCenterID
                LEFT JOIN t_QualityInspectionBatch ib ON ib.ProductID = :pid 
                    AND ib.SourceID = :oid
                LEFT JOIN t_QualityInspectionResult ir ON ir.BatchID = ib.BatchID 
                    AND ir.QCOperationID = qoa.QCOperationID
                LEFT JOIN t_Users u ON ir.InspectedBy = u.UserID
                WHERE qoa.SourceType = 'Product'
                  AND qoa.SourceID = :pid
                  AND qoa.IsActive = 1
                ORDER BY CAST(qoa.OperationSequence AS INT)
            """),
            {'oid': order_id, 'pid': product_id}
        ).fetchall()
    
    quality_ops = []
    for r in rows:
        inspection_date_key = r[18]
        inspected_date_key = r[23]
        
        quality_ops.append({
            'assignmentId': r[0],
            'qcOperationId': r[1],
            'operationName': r[2],
            'description': '',
            'category': r[3] or 'General',
            'isRequired': bool(r[4]),
            'isCritical': bool(r[5]),
            'samplingSize': r[6] or 1,
            'samplingFrequency': r[7] or 'Each',
            'specMin': float(r[8]) if r[8] is not None else None,
            'specTarget': float(r[9]) if r[9] is not None else None,
            'specMax': float(r[10]) if r[10] is not None else None,
            'measurementUnit': r[11],
            'workCenterId': r[12],
            'workCenterName': r[13] or r[12],
            'operationSequence': r[14] or 0,
            'batchNumber': r[15],
            'batchStatus': r[16] or 'Pending',
            'batchResult': r[17],
            'inspectionDateKey': inspection_date_key,
            'inspectionDateFormatted': format_date_key(inspection_date_key) if inspection_date_key else '',
            'resultId': r[19],
            'passFail': r[20],
            'measuredValue': float(r[21]) if r[21] is not None else None,
            'inspectedBy': r[22],
            'inspectedDateKey': inspected_date_key,
            'inspectedDateFormatted': format_date_key(inspected_date_key) if inspected_date_key else '',
            'comment': r[24],
            'inspectorName': r[25] or r[22]
        })
    
    return quality_ops


def get_order_serials(order_id, engine):
    """Get serial numbers for this order."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT 
                    SerialNumber,
                    ProductID,
                    Status,
                    InspectionResult,
                    CurrentLocation,
                    ProductionDateKey,
                    WarrantyEndDateKey
                FROM t_SerialNumbers
                WHERE ProductionOrderID = :oid
                ORDER BY SerialNumber
            """),
            {'oid': order_id}
        ).fetchall()
    
    serials = []
    for r in rows:
        production_date_key = r[5]
        warranty_end_date_key = r[6]
        
        serials.append({
            'serialNumber': r[0],
            'productId': r[1],
            'status': r[2] or 'InProduction',
            'inspectionResult': r[3] or 'Pending',
            'currentLocation': r[4] or 'Not Scanned',
            'workCenterId': None,
            'workCenterName': 'Unknown',
            'productionDateKey': production_date_key,
            'productionDateFormatted': format_date_key(production_date_key) if production_date_key else '',
            'warrantyEndDateKey': warranty_end_date_key,
            'warrantyEndDateFormatted': format_date_key(warranty_end_date_key) if warranty_end_date_key else ''
        })
    
    return serials


def calculate_order_stats(order, routing, components, serials, quality):
    """Calculate summary statistics for the dashboard."""
    total_steps = len(routing)
    completed_steps = sum(1 for r in routing if r['isCompleted'])
    in_progress_steps = sum(1 for r in routing if r['isInProgress'])
    pending_steps = total_steps - completed_steps - in_progress_steps
    
    planned_total = sum(r['plannedTotal'] for r in routing)
    actual_total = sum(r['actualTotal'] for r in routing)
    
    total_components = len(components)
    issued_components = sum(1 for c in components if c['isFullyIssued'])
    partially_issued = sum(1 for c in components if c['isPartiallyIssued'])
    
    total_quality_ops = len(quality)
    passed_ops = sum(1 for q in quality if q.get('passFail') == 1)
    failed_ops = sum(1 for q in quality if q.get('passFail') == 0)
    pending_ops = total_quality_ops - passed_ops - failed_ops
    
    total_serials = len(serials)
    completed_serials = sum(1 for s in serials if s['status'] in ['Completed', 'InStock', 'Shipped'])
    
    return {
        'routing': {
            'totalSteps': total_steps,
            'completedSteps': completed_steps,
            'inProgressSteps': in_progress_steps,
            'pendingSteps': pending_steps,
            'progressPercent': round((completed_steps / total_steps * 100) if total_steps > 0 else 0, 1),
            'plannedTotalHours': round(planned_total, 2),
            'actualTotalHours': round(actual_total, 2),
            'varianceHours': round(actual_total - planned_total, 2)
        },
        'components': {
            'totalComponents': total_components,
            'fullyIssued': issued_components,
            'partiallyIssued': partially_issued,
            'notIssued': total_components - issued_components - partially_issued,
            'hasShortage': any(c['isShortage'] for c in components),
            'shortageCount': sum(1 for c in components if c['isShortage'])
        },
        'quality': {
            'totalOperations': total_quality_ops,
            'passed': passed_ops,
            'failed': failed_ops,
            'pending': pending_ops
        },
        'serials': {
            'total': total_serials,
            'completed': completed_serials,
            'inProgress': total_serials - completed_serials,
            'progressPercent': round((completed_serials / total_serials * 100) if total_serials > 0 else 0, 1)
        },
        'order': {
            'plannedQuantity': order[5] or 0,
            'estimatedCost': float(order[22] or 0),
            'actualCost': float(order[23] or 0),
            'costVariance': float(order[24] or 0)
        }
    }


# ============================================================================
# JOB ORDER DETAIL - ROUTING ACTION ENDPOINTS
# ============================================================================

@api_bp.route('/orders/<string:order_id>/routing/<int:sequence>/start', methods=['POST'])
@login_required
def start_routing_step(order_id, sequence):
    """Start a routing operation."""
    data = request.get_json()
    machine_id = data.get('machineId')
    operator_id = data.get('operatorId')
    today_key = int(datetime.now().strftime('%Y%m%d'))
    
    engine = get_engine()
    with engine.begin() as conn:
        order = conn.execute(
            text("SELECT Status FROM t_ProductionOrder WHERE ProductionOrderId = :oid"),
            {'oid': order_id}
        ).first()
        if not order:
            return jsonify({'error': 'Order not found'}), 404
        
        if order[0] not in ['Released', 'InProgress']:
            return jsonify({'error': f'Cannot start operation in {order[0]} status'}), 400
        
        existing = conn.execute(
            text("""
                SELECT 1 FROM t_ProductionOrderDetail 
                WHERE ProductionOrderId = :oid AND OperationSequence = :seq
            """),
            {'oid': order_id, 'seq': sequence}
        ).first()
        
        if existing:
            status = conn.execute(
                text("""
                    SELECT Status FROM t_ProductionOrderDetail 
                    WHERE ProductionOrderId = :oid AND OperationSequence = :seq
                """),
                {'oid': order_id, 'seq': sequence}
            ).scalar()
            if status == 'Completed':
                return jsonify({'error': 'Operation already completed'}), 400
            if status == 'InProgress':
                return jsonify({'error': 'Operation already in progress'}), 400
            
            conn.execute(
                text("""
                    UPDATE t_ProductionOrderDetail
                    SET Status = 'InProgress', 
                        ActualStart = GETDATE(),
                        MachineID = :machine_id
                    WHERE ProductionOrderId = :oid AND OperationSequence = :seq
                """),
                {'oid': order_id, 'seq': sequence, 'machine_id': machine_id}
            )
        else:
            conn.execute(
                text("""
                    INSERT INTO t_ProductionOrderDetail
                    (ProductionOrderId, OperationSequence, Status, ActualStart, MachineID)
                    VALUES (:oid, :seq, 'InProgress', GETDATE(), :machine_id)
                """),
                {'oid': order_id, 'seq': sequence, 'machine_id': machine_id}
            )
        
        conn.execute(
            text("""
                UPDATE t_ProductionOrder
                SET CurrentRoutingStep = :seq
                WHERE ProductionOrderId = :oid
            """),
            {'seq': sequence, 'oid': order_id}
        )
        
    return jsonify({
        'message': f'Operation {sequence} started successfully',
        'status': 'InProgress'
    })


@api_bp.route('/orders/<string:order_id>/routing/<int:sequence>/complete', methods=['POST'])
@login_required
def complete_routing_step(order_id, sequence):
    """Complete a routing operation."""
    data = request.get_json()
    actual_setup = data.get('actualSetup', 0)
    actual_run = data.get('actualRun', 0)
    actual_quantity = data.get('actualQuantity')
    yield_loss = data.get('yieldLoss', 0)
    
    engine = get_engine()
    with engine.begin() as conn:
        detail = conn.execute(
            text("""
                SELECT Status FROM t_ProductionOrderDetail 
                WHERE ProductionOrderId = :oid AND OperationSequence = :seq
            """),
            {'oid': order_id, 'seq': sequence}
        ).first()
        
        if not detail:
            return jsonify({'error': 'Operation not started'}), 400
        
        if detail[0] != 'InProgress':
            return jsonify({'error': f'Operation is {detail[0]}, cannot complete'}), 400
        
        conn.execute(
            text("""
                UPDATE t_ProductionOrderDetail
                SET Status = 'Completed',
                    ActualEnd = GETDATE(),
                    SetupTimeHours = :setup,
                    RunTimeHours = :run,
                    ActualQuantity = COALESCE(:qty, ActualQuantity),
                    YieldLoss = :yield
                WHERE ProductionOrderId = :oid AND OperationSequence = :seq
            """),
            {
                'oid': order_id,
                'seq': sequence,
                'setup': actual_setup,
                'run': actual_run,
                'qty': actual_quantity,
                'yield': yield_loss
            }
        )
        
        next_step = conn.execute(
            text("""
                SELECT TOP 1 OperationSequence 
                FROM t_WorkCenterRouting 
                WHERE ProductID = (SELECT ProductId FROM t_ProductionOrder WHERE ProductionOrderId = :oid)
                  AND OperationSequence > :seq
                ORDER BY CAST(OperationSequence AS INT)
            """),
            {'oid': order_id, 'seq': sequence}
        ).scalar()
        
        conn.execute(
            text("""
                UPDATE t_ProductionOrder
                SET CurrentRoutingStep = COALESCE(:next, CurrentRoutingStep),
                    NextRoutingStep = (
                        SELECT TOP 1 OperationSequence 
                        FROM t_WorkCenterRouting 
                        WHERE ProductID = (SELECT ProductId FROM t_ProductionOrder WHERE ProductionOrderId = :oid)
                          AND OperationSequence > COALESCE(:next, :seq)
                        ORDER BY CAST(OperationSequence AS INT)
                    )
                WHERE ProductionOrderId = :oid
            """),
            {'oid': order_id, 'seq': sequence, 'next': next_step}
        )
        
        if next_step is None:
            all_completed = conn.execute(
                text("""
                    SELECT CASE WHEN EXISTS (
                        SELECT 1 FROM t_ProductionOrderDetail
                        WHERE ProductionOrderId = :oid AND Status != 'Completed'
                    ) THEN 0 ELSE 1 END
                """),
                {'oid': order_id}
            ).scalar()
            
            if all_completed:
                conn.execute(
                    text("""
                        UPDATE t_ProductionOrder
                        SET Status = 'Completed',
                            ActualEndDateKey = (YEAR(GETDATE()) * 10000 + MONTH(GETDATE()) * 100 + DAY(GETDATE()))
                        WHERE ProductionOrderId = :oid
                          AND Status = 'InProgress'
                    """),
                    {'oid': order_id}
                )
        
    return jsonify({
        'message': f'Operation {sequence} completed successfully',
        'nextStep': next_step,
        'allComplete': next_step is None
    })


# ============================================================================
# JOB ORDER DETAIL - QUALITY ACTION ENDPOINTS
# ============================================================================

@api_bp.route('/orders/<string:order_id>/quality/<int:assignment_id>/record', methods=['POST'])
@login_required
def record_quality_result(order_id, assignment_id):
    """Record a quality inspection result."""
    data = request.get_json()
    pass_fail = data.get('passFail')
    measured_value = data.get('measuredValue')
    comment = data.get('comment', '')
    today_key = int(datetime.now().strftime('%Y%m%d'))
    
    engine = get_engine()
    with engine.begin() as conn:
        assignment = conn.execute(
            text("""
                SELECT QCOperationID, SourceID, IsCritical
                FROM t_QualityOperationAssignment
                WHERE AssignmentID = :aid
            """),
            {'aid': assignment_id}
        ).first()
        
        if not assignment:
            return jsonify({'error': 'Quality operation not found'}), 404
        
        batch = conn.execute(
            text("""
                SELECT TOP 1 BatchID FROM t_QualityInspectionBatch
                WHERE SourceID = :oid AND ProductID = :pid
                  AND Status IN ('Pending', 'InProgress')
                ORDER BY BatchID DESC
            """),
            {'oid': order_id, 'pid': assignment[1]}
        ).first()
        
        if not batch:
            batch_number = f"BATCH-{order_id}-{today_key}"
            result = conn.execute(
                text("""
                    INSERT INTO t_QualityInspectionBatch
                    (BatchNumber, ProductID, SourceType, SourceID, Status, InspectionDateKey)
                    OUTPUT INSERTED.BatchID
                    VALUES (:bn, :pid, 'ProductionOrder', :oid, 'InProgress', :date)
                """),
                {'bn': batch_number, 'pid': assignment[1], 'oid': order_id, 'date': today_key}
            )
            batch_id = result.scalar()
        else:
            batch_id = batch[0]
        
        conn.execute(
            text("""
                INSERT INTO t_QualityInspectionResult
                (BatchID, QCOperationID, PassFail, MeasuredValue, 
                 InspectedBy, InspectedDateKey, InspectorNotes)
                VALUES (:bid, :qcid, :pf, :mv, :inspector, :date, :comment)
            """),
            {
                'bid': batch_id,
                'qcid': assignment[0],
                'pf': pass_fail,
                'mv': measured_value,
                'inspector': current_user.get_id(),
                'date': today_key,
                'comment': comment
            }
        )
        
        if assignment[2] and pass_fail == 0:
            conn.execute(
                text("""
                    UPDATE t_ProductionOrder
                    SET Status = 'Blocked',
                        BlockedReason = 'Critical quality failure on operation ' + CAST(:qcid AS NVARCHAR)
                    WHERE ProductionOrderId = :oid
                """),
                {'oid': order_id, 'qcid': assignment[0]}
            )
        
        conn.execute(
            text("""
                UPDATE t_ProductionOrder
                SET QualityStatus = CASE 
                    WHEN EXISTS (
                        SELECT 1 FROM t_QualityInspectionResult 
                        WHERE BatchID = :bid AND PassFail = 0
                    ) THEN 'Failed'
                    WHEN EXISTS (
                        SELECT 1 FROM t_QualityInspectionResult 
                        WHERE BatchID = :bid AND PassFail = 1
                    ) THEN 'Passed'
                    ELSE 'Pending'
                END
                WHERE ProductionOrderId = :oid
            """),
            {'bid': batch_id, 'oid': order_id}
        )
        
    return jsonify({
        'message': 'Quality result recorded',
        'passFail': pass_fail,
        'batchId': batch_id
    })


@api_bp.route('/orders/<string:order_id>/components/<string:component_id>/stage', methods=['PUT'])
@login_required
def update_component_stage(order_id, component_id):
    """Update the stage of a BOM component."""
    data = request.get_json()
    stage = data.get('stage')
    work_center_id = data.get('workCenterId')
    
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE t_ProductionOrderComponents
                SET Stage = :stage,
                    CurrentWorkCenterID = :wc,
                    UpdatedDate = GETDATE()
                WHERE ProductionOrderId = :oid
                  AND ComponentProductID = :cid
            """),
            {'oid': order_id, 'cid': component_id, 'stage': stage, 'wc': work_center_id}
        )
    
    return jsonify({
        'message': f'Component {component_id} stage updated to {stage}'
    })


# ============================================================================
# IMPORTANT: This general /orders/<order_id> route MUST come AFTER the /detail route
# ============================================================================


@api_bp.route('/orders/<string:order_id>')
@login_required
def get_job_order(order_id):
    """Get full order detail including BOM, routing, quality, costing, documents."""
    engine = get_engine()
    
    with engine.connect() as conn:
        # Get order details
        row = conn.execute(
            text("""
                SELECT po.ProductionOrderId, po.ProductId, po.OrderType, po.OrderQuantity,
                       po.EndDateKey, po.ProjectID, po.Priority, po.Status,
                       po.StartDateKey, po.CreatedBy, po.ReleasedDateKey, po.ActualEndDateKey,
                       COALESCE(po.SalesOrderID, '') as SalesOrderID,
                       COALESCE(po.CustomerReference, '') as CustomerReference,
                       COALESCE(po.EngineeringVersion, 1) as EngineeringVersion,
                       COALESCE(po.BOMVersion, 1) as BOMVersion,
                       COALESCE(po.RoutingVersion, 1) as RoutingVersion,
                       po.ScheduleStartDateKey, po.ScheduleEndDateKey, po.ActualStartDateKey,
                       po.EstimatedMaterialCost, po.EstimatedLaborCost, po.EstimatedOverheadCost,
                       po.EstimatedTotalCost, po.ActualMaterialCost, po.ActualLaborCost,
                       po.ActualOverheadCost, po.ActualTotalCost, po.CostVariance,
                       po.HoldReason, po.BlockedReason,
                       po.WorkflowID, po.WorkflowStatus, po.NextWorkflowStep,
                       p.descEnglish as ProductName,
                       COALESCE(po.QualityStatus, 'Pending') as QualityStatus
                FROM t_ProductionOrder po
                LEFT JOIN t_Product p ON po.ProductId = p.ProductId
                WHERE po.ProductionOrderId = :oid
            """),
            {'oid': order_id}
        ).first()
        
        if not row:
            return jsonify({'error': 'Order not found'}), 404
        
        # Get components - FIXED: Cast numeric values
        components = conn.execute(
            text("""
                SELECT ComponentProductID, 
                       CAST(RequiredQuantity AS DECIMAL(18,4)) as RequiredQuantity, 
                       CAST(COALESCE(IssuedQuantity, 0) AS DECIMAL(18,4)) as IssuedQuantity
                FROM t_ProductionOrderComponents
                WHERE ProductionOrderId = :oid
            """),
            {'oid': order_id}
        ).fetchall()
        
        components_list = []
        for c in components:
            # Get availability - FIXED: Cast to DECIMAL
            avail = conn.execute(
                text("SELECT CAST(COALESCE(SUM(CAST(Qty AS DECIMAL(18,4))), 0) AS DECIMAL(18,4)) FROM t_InventoryOnHand WHERE ProductID = :pid"),
                {'pid': c[0]}
            ).scalar() or 0
            
            # Now c[1] and c[2] are DECIMAL, avail is DECIMAL
            shortage = max(0, float(c[1]) - float(avail))
            status = 'Shortage' if shortage > 0 else 'Available'
            
            components_list.append({
                'componentId': c[0],
                'required': float(c[1]),
                'issued': float(c[2] or 0),
                'available': float(avail),
                'shortage': float(shortage),
                'status': status,
                'description': ''
            })
        
        # Get operations (routing) - FIXED: Cast numeric values
        product_id = row[1]
        operations = []
        if product_id:
            ops = conn.execute(
                text("""
                    SELECT OperationSequence, 
                           WorkCenterID, 
                           CAST(SetupTimeHours AS DECIMAL(18,4)) as SetupTimeHours, 
                           CAST(RunTimeHoursPerUnit AS DECIMAL(18,4)) as RunTimeHoursPerUnit
                    FROM t_WorkCenterRouting
                    WHERE ProductID = :pid
                      AND (ObsoleteDateKey IS NULL OR ObsoleteDateKey > (YEAR(GETDATE()) * 10000 + MONTH(GETDATE()) * 100 + DAY(GETDATE())))
                    ORDER BY CAST(OperationSequence AS INT)
                """),
                {'pid': product_id}
            ).fetchall()
            
            for op in ops:
                operations.append({
                    'sequence': int(op[0]) if op[0] else 0,
                    'workCenter': op[1],
                    'workCenterName': op[1],
                    'setupTime': float(op[2] or 0),
                    'runTime': float(op[3] or 0),
                    'totalTime': float((op[2] or 0) + (op[3] or 0))
                })
        
        # Get documents
        documents = conn.execute(
            text("""
                SELECT DocumentType, Description, IsMandatory, UploadedDateKey
                FROM t_JobOrderDocument
                WHERE ProductionOrderID = :oid
            """),
            {'oid': order_id}
        ).fetchall()
        
        documents_list = []
        for d in documents:
            uploaded_date_key = d[3]
            documents_list.append({
                'documentType': d[0],
                'description': d[1],
                'isMandatory': bool(d[2]),
                'uploadedDateKey': uploaded_date_key,
                'uploadedDateFormatted': format_date_key(uploaded_date_key) if uploaded_date_key else ''
            })
        
        # Format date keys
        due_date_key = row[4]
        start_date_key = row[8]
        released_date_key = row[10]
        actual_end_date_key = row[11]
        schedule_start_key = row[17]
        schedule_end_key = row[18]
        actual_start_key = row[19]
        
        # Build response
        response_data = {
            'id': row[0],
            'productId': row[1],
            'productName': row[33] or row[1],
            'orderType': row[2],
            'quantity': float(row[3] or 0),
            'dueDateKey': due_date_key,
            'dueDateFormatted': format_date_key(due_date_key) if due_date_key else '',
            'projectId': row[5],
            'priority': int(row[6] or 2),
            'status': row[7],
            'startDateKey': start_date_key,
            'startDateFormatted': format_date_key(start_date_key) if start_date_key else '',
            'createdBy': row[9],
            'releasedDateKey': released_date_key,
            'releasedDateFormatted': format_date_key(released_date_key) if released_date_key else '',
            'actualEndDateKey': actual_end_date_key,
            'actualEndDateFormatted': format_date_key(actual_end_date_key) if actual_end_date_key else '',
            'salesOrderId': row[12],
            'customerReference': row[13],
            'engineeringVersion': int(row[14] or 1),
            'bomVersion': int(row[15] or 1),
            'routingVersion': int(row[16] or 1),
            'scheduleStartDateKey': schedule_start_key,
            'scheduleStartDateFormatted': format_date_key(schedule_start_key) if schedule_start_key else '',
            'scheduleEndDateKey': schedule_end_key,
            'scheduleEndDateFormatted': format_date_key(schedule_end_key) if schedule_end_key else '',
            'actualStartDateKey': actual_start_key,
            'actualStartDateFormatted': format_date_key(actual_start_key) if actual_start_key else '',
            'estimatedMaterialCost': float(row[20] or 0),
            'estimatedLaborCost': float(row[21] or 0),
            'estimatedOverheadCost': float(row[22] or 0),
            'estimatedTotalCost': float(row[23] or 0),
            'actualMaterialCost': float(row[24] or 0),
            'actualLaborCost': float(row[25] or 0),
            'actualOverheadCost': float(row[26] or 0),
            'actualTotalCost': float(row[27] or 0),
            'costVariance': float(row[28] or 0),
            'holdReason': row[29],
            'blockedReason': row[30],
            'components': components_list,
            'operations': operations,
            'qualityStatus': row[34] or 'Pending',
            'documents': documents_list,
            'workflowId': row[31],
            'workflowStatus': row[32] or 'NotStarted',
            'nextWorkflowStep': row[33]
        }
        
        return jsonify(response_data)

# ============================================================================
# JOB ORDERS API ENDPOINTS
# ============================================================================

@api_bp.route('/orders/stats', methods=['GET'])
@login_required
def job_orders_stats():
    """KPI counts for job orders."""
    status = request.args.get('status')
    product = request.args.get('product_id')
    project = request.args.get('project_id')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    
    industry = session.get('demo_industry', 'valve')
    print(f"📊 API - Industry from session: {industry}")
    
    engine = get_engine()
    params = {'industry': industry}
    
    conditions = ["(DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)"]
    
    if status:
        conditions.append("Status = :status")
        params['status'] = status
    if product:
        conditions.append("ProductId = :product")
        params['product'] = product
    if project:
        conditions.append("ProjectID = :project")
        params['project'] = project
    if date_from:
        conditions.append("EndDateKey >= :from_key")
        params['from_key'] = date_to_key(date_from)
    if date_to:
        conditions.append("EndDateKey <= :to_key")
        params['to_key'] = date_to_key(date_to)

    where_clause = " AND ".join(conditions)
    sql = f"""
        SELECT Status, COUNT(*) as cnt
        FROM t_ProductionOrder
        WHERE {where_clause}
        GROUP BY Status
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    
    counts = {row[0]: row[1] for row in rows}
    return jsonify({
        'Draft': counts.get('Draft', 0),
        'Released': counts.get('Released', 0),
        'InProgress': counts.get('InProgress', 0),
        'Completed': counts.get('Completed', 0),
        'Closed': counts.get('Closed', 0),
        'Hold': counts.get('Hold', 0),
        'Blocked': counts.get('Blocked', 0),
        'Canceled': counts.get('Canceled', 0)
    })




@api_bp.route('/orders', methods=['GET'])
@login_required
def list_job_orders():
    """List job orders with filters and workflow status (SQL Server safe)."""
    import json
    import traceback
    try:
        from flask import session, request, jsonify
        from sqlalchemy import text

        # Correct helpers for this codebase:
        #  - get_engine            lives in date_helpers
        #  - date_to_key           lives in date_helpers
        #  - format_date           lives in date_helpers (NOT format_date_key)
        #  - get_industry_filter_with_or_null lives in database
        from app.core.date_helpers import get_engine, date_to_key, format_date
        from app.core.database import get_industry_filter_with_or_null

        industry  = session.get('demo_industry', 'valve')
        status    = request.args.get('status')
        product   = request.args.get('product_id')
        project   = request.args.get('project_id') or session.get('current_project_id', 'All')
        date_from = request.args.get('date_from')
        date_to   = request.args.get('date_to')

        args = []

        # Industry filter — helper inlines session industry, no binding needed
        conditions = [get_industry_filter_with_or_null('po.')]

        if status:
            conditions.append("po.Status = ?");     args.append(status)
        if product:
            conditions.append("po.ProductId = ?");  args.append(product)

        # Project filter via t_ProjectProducts (Context §4.2)
        if project and project != 'All' and str(project).strip() != '':
            proj_list = [p.strip() for p in str(project).split(',') if p.strip()]
            if proj_list:
                ph = ', '.join(['?'] * len(proj_list))
                conditions.append(
                    f"po.ProductId IN (SELECT ProductID FROM t_ProjectProducts "
                    f"WHERE ProjectID IN ({ph}))"
                )
                args.extend(proj_list)

        if date_from:
            conditions.append("po.EndDateKey >= ?")
            args.append(date_to_key(date_from))
        if date_to:
            conditions.append("po.EndDateKey <= ?")
            args.append(date_to_key(date_to))

        where_clause = " AND ".join(conditions)

        sql = f"""
            SELECT
                po.ProductionOrderId, po.ProductId,
                p.descEnglish AS ProductName, p.descFarsi AS ProductNameFarsi,
                po.OrderType, po.OrderQuantity, po.Status, po.EndDateKey,
                po.ProjectID, pr.ProjectName, pr.ProjectNameLocal,
                po.OrderSource, po.SalesOrderID, po.CustomerReference,
                po.EngineeringVersion, po.CompanyProjectID,
                po.EstimatedTotalCost, po.ActualTotalCost,
                po.WorkflowID, po.WorkflowStatus
            FROM t_ProductionOrder po
            LEFT JOIN t_Product p  ON po.ProductId  = p.ProductId
            LEFT JOIN t_Project pr ON po.ProjectID  = pr.ProjectID
            WHERE {where_clause}
            ORDER BY po.StartDateKey DESC
        """

        engine = get_engine()

        with engine.connect() as conn:
            rows = conn.execute(text(sql), tuple(args)).fetchall()

        # ---- Batch-fetch all workflows in ONE query (named params) ----
        workflow_ids = list({r._mapping.get('WorkflowID') for r in rows
                             if r._mapping.get('WorkflowID')})
        workflow_map = {}
        if workflow_ids:
            wf_ph = ', '.join([f':w{i}' for i in range(len(workflow_ids))])
            wf_params = {f'w{i}': wfid for i, wfid in enumerate(workflow_ids)}
            wf_sql = f"""
                SELECT w.WorkflowID, w.CurrentStep, w.Status,
                       wf.Steps, wf.WorkflowCode
                FROM t_Workflow w
                LEFT JOIN t_WorkflowDefinitions wf
                       ON w.WorkflowTemplateID = wf.WorkflowID
                WHERE w.WorkflowID IN ({wf_ph})
            """
            with engine.connect() as conn2:
                for wfr in conn2.execute(text(wf_sql), wf_params):
                    workflow_map[wfr[0]] = {
                        'current_step':  wfr[1],
                        'status':        wfr[2],
                        'steps_json':    wfr[3],
                        'workflow_code': wfr[4],
                    }

        lang = session.get('lang', 'en')

        def _f(v):
            try: return float(v or 0)
            except (TypeError, ValueError): return 0.0
        def _i(v, d=1):
            try: return int(v or d)
            except (TypeError, ValueError): return d

        orders = []
        for r in rows:
            rd = dict(r._mapping)

            due_date_key = rd.get('EndDateKey')
            try:
                formatted_due_date = format_date(due_date_key) if due_date_key else ''
            except Exception:
                try:
                    dk = int(due_date_key) if due_date_key else 0
                    formatted_due_date = (
                        f"{dk//10000:04d}-{(dk//100)%100:02d}-{dk%100:02d}" if dk else ''
                    )
                except Exception:
                    formatted_due_date = ''

            product_name = rd.get('ProductName') or ''
            if lang != 'en' and (rd.get('ProductNameFarsi') or '').strip():
                product_name = rd.get('ProductNameFarsi')

            project_name = rd.get('ProjectName') or ''
            if lang != 'en' and (rd.get('ProjectNameLocal') or '').strip():
                project_name = rd.get('ProjectNameLocal')

            workflow_id     = rd.get('WorkflowID')
            workflow_status = rd.get('WorkflowStatus') or 'NotStarted'
            workflow_step_display = 'Not Started'
            workflow_code = None

            if workflow_id and workflow_id in workflow_map:
                wf = workflow_map[workflow_id]
                wf_status    = wf['status'] or 'Pending'
                steps_json   = wf['steps_json']
                workflow_code= wf.get('workflow_code')
                current_step = _i(wf['current_step'], 1)

                if wf_status == 'Completed':
                    workflow_step_display = 'Complete'
                elif wf_status == 'Rejected':
                    workflow_step_display = 'Rejected'
                else:
                    step_name = ''
                    if steps_json:
                        try:
                            steps = json.loads(steps_json) if isinstance(steps_json, str) else steps_json
                            if isinstance(steps, list):
                                for step in steps:
                                    if step.get('step') == current_step:
                                        step_name = step.get('name', ''); break
                        except Exception as e:
                            print("[job_orders] steps parse:", e)
                    workflow_step_display = (
                        f"Step {current_step}: {step_name}" if step_name
                        else f"Step {current_step}"
                    )

            orders.append({
                'id':                 rd.get('ProductionOrderId'),
                'productId':          rd.get('ProductId'),
                'productName':        product_name,
                'orderType':          rd.get('OrderType') or 'Manufacturing',
                'quantity':           _f(rd.get('OrderQuantity')),
                'status':             rd.get('Status') or 'Draft',
                'dueDateKey':         due_date_key,
                'dueDateFormatted':   formatted_due_date,
                'projectId':          rd.get('ProjectID'),
                'projectName':        project_name,
                'orderSource':        rd.get('OrderSource') or 'Manual',
                'salesOrderId':       rd.get('SalesOrderID'),
                'customerReference':  rd.get('CustomerReference'),
                'engineeringVersion': _i(rd.get('EngineeringVersion'), 1),
                'companyProjectId':   rd.get('CompanyProjectID'),
                'estimatedCost':      _f(rd.get('EstimatedTotalCost')),
                'actualCost':         _f(rd.get('ActualTotalCost')),
                'ecoWarning':         False,
                'completedQuantity':  0,
                'workflowId':         workflow_id,
                'workflowStatus':     workflow_status,
                'workflowStepDisplay':workflow_step_display,
                'workflowState':      workflow_status,
                'workflowCode':       workflow_code,
            })

        return jsonify(orders)

    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500



@api_bp.route('/orders', methods=['POST'])
@login_required
def create_job_order():
    """Create a new job order with full BOM explosion and child order creation."""
    data = request.get_json()
    product_id = data.get('productId')
    quantity = data.get('quantity')
    due_date = data.get('dueDate')
    order_type = data.get('orderType', 'Manufacturing')
    project_id = data.get('projectId')
    priority = data.get('priority', 2)
    sales_order_id = data.get('salesOrderId')
    customer_id = data.get('customerId')
    customer_reference = data.get('customerReference')
    schedule_start = data.get('scheduleStart')
    schedule_end = data.get('scheduleEnd')
    order_source = data.get('orderSource', 'Manual')
    company_project_id = data.get('companyProjectId')
    
    if not quantity or quantity <= 0:
        return jsonify({'error': 'Valid quantity is required'}), 400

    industry = session.get('demo_industry', 'valve')
    today_key = int(datetime.now().strftime('%Y%m%d'))
    due_key = date_to_key(due_date) if due_date else None
    sched_start_key = date_to_key(schedule_start) if schedule_start else None
    sched_end_key = date_to_key(schedule_end) if schedule_end else None

    engine = get_engine()
    child_orders = []
    created_child_orders = []
    
    should_explode = data.get('explodeBOM', True)
    
    print(f"🚀 Creating job order for product: {product_id}, quantity: {quantity}")
    print(f"🔧 should_explode: {should_explode}")
    
    with engine.begin() as conn:
        res = conn.execute(text("SELECT MAX(CAST(SUBSTRING(ProductionOrderId, 4, LEN(ProductionOrderId) - 3) AS INT)) FROM t_ProductionOrder"))
        max_num = res.scalar() or 0
        next_num = max_num + 1
        order_id = f"PO-{next_num:05d}"
        print(f"📋 Generated order ID: {order_id}")

        bom_ver = 1
        routing_ver = 1
        eng_ver = 1
        est_mat_cost = 0
        est_lab_cost = 0
        est_oh_cost = 0
        est_total = 0

        if product_id:
            bom_ver = conn.execute(
                text("SELECT COUNT(*) FROM t_BOM WHERE ParentProductID = :pid"),
                {'pid': product_id}
            ).scalar() or 1
            
            routing_ver = conn.execute(
                text("SELECT COUNT(*) FROM t_WorkCenterRouting WHERE ProductID = :pid"),
                {'pid': product_id}
            ).scalar() or 1
            
            eng_ver = conn.execute(
                text("SELECT COALESCE(EngineeringVersion, 1) FROM t_Product WHERE ProductId = :pid"),
                {'pid': product_id}
            ).scalar() or 1

            mat_cost = conn.execute(
                text("""
                    SELECT SUM(b.Quantity * COALESCE(p.StandardCost, 0))
                    FROM t_BOM b
                    JOIN t_Product p ON b.ComponentProductID = p.ProductId
                    WHERE b.ParentProductID = :pid
                      AND b.IsActive = 1
                      AND (b.EffectiveDateKey IS NULL OR b.EffectiveDateKey <= :today)
                """),
                {'pid': product_id, 'today': today_key}
            ).scalar() or 0
            est_mat_cost = mat_cost * quantity

            labor_cost = conn.execute(
                text("""
                    SELECT SUM((wcr.SetupTimeHours + (wcr.RunTimeHoursPerUnit * :qty)) * COALESCE(wc.HourlyRate, 0))
                    FROM t_WorkCenterRouting wcr
                    LEFT JOIN t_WorkCenter wc ON wcr.WorkCenterID = wc.WorkCenterID
                    WHERE wcr.ProductID = :pid
                      AND wcr.IsActive = 1
                      AND (wcr.ObsoleteDateKey IS NULL OR wcr.ObsoleteDateKey > :today)
                """),
                {'pid': product_id, 'qty': quantity, 'today': today_key}
            ).scalar() or 0

            est_lab_cost = labor_cost
            est_oh_cost = labor_cost * 0.15
            est_total = est_mat_cost + est_lab_cost + est_oh_cost

        conn.execute(
            text("""
                INSERT INTO t_ProductionOrder
                (ProductionOrderId, ProductId, OrderType, OrderQuantity, Status,
                 StartDateKey, EndDateKey, Priority, ProjectID, CreatedBy, OrderSource,
                 SalesOrderID, CustomerReference, EngineeringVersion, BOMVersion,
                 RoutingVersion, ScheduleStartDateKey, ScheduleEndDateKey,
                 EstimatedMaterialCost, EstimatedLaborCost, EstimatedOverheadCost,
                 EstimatedTotalCost, CompanyProjectID, DemoIndustryCode,
                 WorkflowStatus, OrderCategory, SourcingType, ChildOrderCount)
                VALUES (:oid, :pid, :otype, :qty, 'Draft',
                        :start, :due, :prio, :proj, :user, :source,
                        :soid, :cref, :engv, :bomv, :routev,
                        :sched_start, :sched_end, :mat_cost, :lab_cost, :oh_cost,
                        :total_est, :company_proj, :industry,
                        'NotStarted', 'Assembly', 'Make', 0)
            """),
            {'oid': order_id, 'pid': product_id, 'otype': order_type, 'qty': quantity,
             'start': today_key, 'due': due_key, 'prio': priority, 'proj': project_id,
             'user': current_user.get_id(), 'source': order_source,
             'soid': sales_order_id, 'cref': customer_reference,
             'engv': eng_ver, 'bomv': bom_ver, 'routev': routing_ver,
             'sched_start': sched_start_key, 'sched_end': sched_end_key,
             'mat_cost': est_mat_cost, 'lab_cost': est_lab_cost,
             'oh_cost': est_oh_cost, 'total_est': est_total,
             'company_proj': company_project_id, 'industry': industry}
        )

        conn.execute(
            text("""
                INSERT INTO t_ProductionOrderDetail
                (ProductionOrderId, Status, BatchId)
                VALUES (:oid, 'Planned', :batch)
            """),
            {'oid': order_id, 'batch': f"BATCH-{order_id}"}
        )

        if product_id and should_explode:
            try:
                from app.services.bom_service import explode_bom, create_child_orders
                
                print(f"🔨 Exploding BOM for product: {product_id}, quantity: {quantity}")
                
                components = explode_bom(product_id, quantity)
                
                print(f"📋 Found {len(components)} components")
                
                if components:
                    for comp in components:
                        print(f"  - {comp['product_id']} ({comp['sourcing_type']}) x {comp['quantity']}")
                        if comp.get('children'):
                            for child in comp['children']:
                                print(f"    - {child['product_id']} ({child['sourcing_type']}) x {child['quantity']}")
                    
                    parent_order_data = {
                        'industry': industry,
                        'created_by': current_user.get_id(),
                        'parent_order_id': order_id
                    }
                    
                    created_child_orders = create_child_orders(
                        order_id, 
                        components, 
                        parent_order_data,
                        conn
                    )
                    
                    print(f"✅ Created {len(created_child_orders)} child orders")
                    
                    if created_child_orders:
                        conn.execute(
                            text("""
                                UPDATE t_ProductionOrder 
                                SET ChildOrderCount = :count 
                                WHERE ProductionOrderId = :oid
                            """),
                            {'count': len(created_child_orders), 'oid': order_id}
                        )
                    
                    all_components = []
                    def flatten_components(comp_list, level=0):
                        for comp in comp_list:
                            all_components.append({
                                'product_id': comp['product_id'],
                                'quantity': comp['quantity'],
                                'sourcing_type': comp['sourcing_type']
                            })
                            if comp.get('children'):
                                flatten_components(comp['children'], level + 1)
                    
                    flatten_components(components)
                    
                    print(f"📋 Total components to insert: {len(all_components)}")
                    
                    for comp in all_components:
                        scrap = conn.execute(
                            text("""
                                SELECT ScrapFactor FROM t_BOM 
                                WHERE ParentProductID = :pid AND ComponentProductID = :cid
                                AND IsActive = 1
                            """),
                            {'pid': product_id, 'cid': comp['product_id']}
                        ).scalar() or 0
                        
                        req_qty = comp['quantity'] * (1 + (scrap or 0) / 100.0)
                        
                        conn.execute(
                            text("""
                                INSERT INTO t_ProductionOrderComponents
                                (ProductionOrderId, ComponentProductID, RequiredQuantity, IssuedQuantity, SourcingType)
                                VALUES (:oid, :cid, :req, 0, :stype)
                            """),
                            {'oid': order_id, 'cid': comp['product_id'], 
                             'req': req_qty, 'stype': comp['sourcing_type']}
                        )
                else:
                    print(f"⚠️ No BOM components found for product: {product_id}")
            except Exception as e:
                print(f"❌ BOM explosion failed: {str(e)}")
                import traceback
                traceback.print_exc()
        else:
            print(f"⏭️ Skipping BOM explosion (product_id: {product_id}, should_explode: {should_explode})")

    workflow_triggered = False
    workflow_id = None
    workflow_message = None
    
    try:
        workflow_data = {
            'entity_type': 'ProductionOrder',
            'entity_id': order_id,
            'scenario': industry,
            'trigger_event': 'Draft',
            'created_by': current_user.get_id(),
            'additional_data': {
                'project_id': project_id or company_project_id,
                'product_id': product_id,
                'customer_id': customer_id,
                'sales_order_id': sales_order_id
            }
        }
        
        print(f"⏳ Triggering workflow for order: {order_id}")
        wf_response, wf_error = call_workflow_api('/trigger', 'POST', workflow_data)
        
        if wf_error and wf_error != 'not_found':
            workflow_message = f"Workflow trigger failed: {wf_error}"
            print(f"❌ Workflow trigger failed: {wf_error}")
        elif wf_response and wf_response.get('success'):
            workflow_triggered = True
            workflow_id = wf_response.get('workflow_id')
            workflow_message = wf_response.get('message', 'Workflow triggered successfully')
            print(f"✅ Workflow triggered: {workflow_id}")
            
            with engine.begin() as conn:
                conn.execute(
                    text("""
                        UPDATE t_ProductionOrder
                        SET WorkflowID = :wfid, WorkflowStatus = 'Pending'
                        WHERE ProductionOrderId = :oid
                    """),
                    {'wfid': workflow_id, 'oid': order_id}
                )
                
                if created_child_orders:
                    for child in created_child_orders:
                        if child.get('type') == 'Manufacturing':
                            print(f"⏳ Creating workflow for child order: {child['order_id']}")
                            child_wf_data = {
                                'entity_type': 'ProductionOrder',
                                'entity_id': child['order_id'],
                                'scenario': industry,
                                'trigger_event': 'Draft',
                                'created_by': current_user.get_id()
                            }
                            child_wf_response, child_wf_error = call_workflow_api('/trigger', 'POST', child_wf_data)
                            if child_wf_response and child_wf_response.get('success'):
                                conn.execute(
                                    text("""
                                        UPDATE t_ProductionOrder
                                        SET WorkflowID = :wfid, WorkflowStatus = 'Pending'
                                        WHERE ProductionOrderId = :oid
                                    """),
                                    {'wfid': child_wf_response.get('workflow_id'), 'oid': child['order_id']}
                                )
                                print(f"✅ Child workflow created: {child_wf_response.get('workflow_id')}")
        else:
            workflow_message = "Workflow trigger returned unknown response"
            print(f"⚠️ Unknown workflow response: {wf_response}")
            
    except Exception as e:
        workflow_message = f"Workflow trigger exception: {str(e)}"
        print(f"❌ Workflow trigger exception: {str(e)}")
        import traceback
        traceback.print_exc()

    response_data = {
        'message': 'Job order created',
        'orderId': order_id,
        'dueDateKey': due_key,
        'dueDateFormatted': format_date_key(due_key) if due_key else '',
        'childOrders': created_child_orders if created_child_orders else [],
        'childOrderCount': len(created_child_orders) if created_child_orders else 0,
        'workflow': {
            'triggered': workflow_triggered,
            'workflow_id': workflow_id,
            'message': workflow_message
        }
    }
    
    print(f"📦 Response: {response_data}")
    return jsonify(response_data), 201


@api_bp.route('/orders/<string:order_id>', methods=['PUT'])
@login_required
def update_job_order(order_id):
    """Update a job order (only Draft allowed)."""
    data = request.get_json()
    engine = get_engine()
    with engine.begin() as conn:
        cur_status = conn.execute(
            text("SELECT Status FROM t_ProductionOrder WHERE ProductionOrderId = :oid"),
            {'oid': order_id}
        ).scalar()
        if cur_status != 'Draft':
            return jsonify({'error': 'Only Draft orders can be edited'}), 400

        updates = []
        params = {'oid': order_id}
        if 'quantity' in data:
            updates.append("OrderQuantity = :qty")
            params['qty'] = data['quantity']
        if 'dueDate' in data:
            due_key = date_to_key(data['dueDate'])
            updates.append("EndDateKey = :due")
            params['due'] = due_key
        if 'scheduleStart' in data:
            sched_start_key = date_to_key(data['scheduleStart'])
            updates.append("ScheduleStartDateKey = :sched_start")
            params['sched_start'] = sched_start_key
        if 'scheduleEnd' in data:
            sched_end_key = date_to_key(data['scheduleEnd'])
            updates.append("ScheduleEndDateKey = :sched_end")
            params['sched_end'] = sched_end_key
        if 'projectId' in data:
            updates.append("ProjectID = :proj")
            params['proj'] = data['projectId']
        if 'priority' in data:
            updates.append("Priority = :prio")
            params['prio'] = data['priority']
        if 'orderType' in data:
            updates.append("OrderType = :otype")
            params['otype'] = data['orderType']
        if 'salesOrderId' in data:
            updates.append("SalesOrderID = :soid")
            params['soid'] = data['salesOrderId']
        if 'customerReference' in data:
            updates.append("CustomerReference = :cref")
            params['cref'] = data['customerReference']
        if updates:
            sql = f"UPDATE t_ProductionOrder SET {', '.join(updates)} WHERE ProductionOrderId = :oid"
            conn.execute(text(sql), params)
        else:
            return jsonify({'error': 'No fields to update'}), 400

    return jsonify({'message': 'Job order updated'}), 200


# ============================================================================
# ORDER ACTION ENDPOINTS
# ============================================================================

@api_bp.route('/orders/<string:order_id>/release', methods=['POST'])
@login_required
def release_job_order(order_id):
    """Release a job order with workflow validation."""
    try:
        today_key = int(datetime.now().strftime('%Y%m%d'))
        engine = get_engine()
        
        workflow_response, error = call_workflow_api(f'/check/ProductionOrder/{order_id}', 'GET')
        
        if error and error != 'not_found':
            current_app.logger.error(f"Workflow check failed: {error}")
            return jsonify({'error': f'Workflow check failed: {error}'}), 503
        
        if workflow_response and not workflow_response.get('complete', False):
            current_step = workflow_response.get('current_step_name', 'Unknown')
            return jsonify({
                'error': 'Cannot release order until workflow is complete',
                'current_step': current_step
            }), 400
        
        with engine.begin() as conn:
            order = conn.execute(
                text("SELECT Status FROM t_ProductionOrder WHERE ProductionOrderId = :oid"),
                {'oid': order_id}
            ).first()
            
            if not order:
                return jsonify({'error': 'Order not found'}), 404                
            if order[0] != 'Draft':
                return jsonify({'error': f'Cannot release order in {order[0]} status'}), 400
            
            conn.execute(
                text("""
                    UPDATE t_ProductionOrder
                    SET Status = 'Released', ReleasedDateKey = :rel
                    WHERE ProductionOrderId = :oid
                """),
                {'rel': today_key, 'oid': order_id}
            )
        
        return jsonify({
            'message': 'Order released successfully',
            'releasedDateKey': today_key,
            'releasedDateFormatted': format_date_key(today_key)
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Release error: {str(e)}")
        return jsonify({'error': str(e)}), 500


@api_bp.route('/orders/<string:order_id>/start', methods=['POST'])
@login_required
def start_job_order(order_id):
    """Start production (Released → InProgress)."""
    today_key = int(datetime.now().strftime('%Y%m%d'))
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE t_ProductionOrder
                SET Status = 'InProgress', ActualStartDateKey = :today
                WHERE ProductionOrderId = :oid AND Status = 'Released'
            """),
            {'today': today_key, 'oid': order_id}
        )
    return jsonify({
        'message': 'Order started',
        'actualStartDateKey': today_key,
        'actualStartDateFormatted': format_date_key(today_key)
    })


@api_bp.route('/orders/<string:order_id>/hold', methods=['POST'])
@login_required
def hold_job_order(order_id):
    """Put order on hold."""
    data = request.get_json()
    reason = data.get('reason', '')
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE t_ProductionOrder
                SET Status = 'Hold', HoldReason = :reason
                WHERE ProductionOrderId = :oid
                  AND Status IN ('Released', 'InProgress')
            """),
            {'reason': reason, 'oid': order_id}
        )
    return jsonify({'message': 'Order placed on hold'})


@api_bp.route('/orders/<string:order_id>/resume', methods=['POST'])
@login_required
def resume_job_order(order_id):
    """Resume a held order."""
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE t_ProductionOrder
                SET Status = 'InProgress', HoldReason = NULL
                WHERE ProductionOrderId = :oid AND Status = 'Hold'
            """),
            {'oid': order_id}
        )
    return jsonify({'message': 'Order resumed'})


@api_bp.route('/orders/<string:order_id>/components', methods=['GET'])
@login_required
def get_order_components_endpoint(order_id):
    """Get components for an order (for issue materials modal)."""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT ComponentProductID, RequiredQuantity, IssuedQuantity
                FROM t_ProductionOrderComponents
                WHERE ProductionOrderId = :oid
            """),
            {'oid': order_id}
        ).fetchall()
    
    components = []
    for r in rows:
        components.append({
            'componentId': r[0],
            'requiredQuantity': float(r[1]),
            'issuedQuantity': float(r[2] or 0)
        })
    return jsonify(components)


@api_bp.route('/orders/<string:order_id>/issue', methods=['POST'])
@login_required
def issue_materials(order_id):
    """Issue materials for an order."""
    data = request.get_json()
    items = data.get('items', [])
    
    if not items:
        return jsonify({'error': 'No items to issue'}), 400
    
    engine = get_engine()
    with engine.begin() as conn:
        for item in items:
            comp_id = item.get('componentProductId')
            qty = item.get('issuedQuantity', 0)
            
            if qty <= 0:
                continue
            
            conn.execute(
                text("""
                    UPDATE t_ProductionOrderComponents
                    SET IssuedQuantity = IssuedQuantity + :qty
                    WHERE ProductionOrderId = :oid AND ComponentProductID = :cid
                """),
                {'qty': qty, 'oid': order_id, 'cid': comp_id}
            )
            
            conn.execute(
                text("""
                    UPDATE t_InventoryOnHand
                    SET Qty = Qty - :qty
                    WHERE ProductID = :pid
                """),
                {'qty': qty, 'pid': comp_id}
            )
            
            conn.execute(
                text("""
                    INSERT INTO t_InventoryTransactions
                    (ProductID, TransactionType, Quantity, ReferenceID, TransactionDateKey)
                    VALUES (:pid, 'ISSUE', :qty, :ref, :date)
                """),
                {
                    'pid': comp_id,
                    'qty': -qty,
                    'ref': f"Order {order_id}",
                    'date': int(datetime.now().strftime('%Y%m%d'))
                }
            )
    
    return jsonify({'message': 'Materials issued successfully'})


@api_bp.route('/orders/<string:order_id>/quantity', methods=['POST'])
@login_required
def report_quantity(order_id):
    """Report completed quantity."""
    data = request.get_json()
    completed_qty = data.get('completedQuantity', 0)
    scrap_qty = data.get('scrapQuantity', 0)
    generate_serials = data.get('generateSerials', False)
    serial_prefix = data.get('serialPrefix', '')
    
    today_key = int(datetime.now().strftime('%Y%m%d'))
    engine = get_engine()
    
    with engine.begin() as conn:
        order = conn.execute(
            text("""
                SELECT OrderQuantity, ProductId, Status
                FROM t_ProductionOrder
                WHERE ProductionOrderId = :oid
            """),
            {'oid': order_id}
        ).first()
        
        if not order:
            return jsonify({'error': 'Order not found'}), 404
        
        if order[2] not in ['Released', 'InProgress']:
            return jsonify({'error': f'Cannot report quantity for order in {order[2]} status'}), 400
        
        conn.execute(
            text("""
                UPDATE t_ProductionOrderDetail
                SET ActualQuantity = COALESCE(ActualQuantity, 0) + :cq,
                    YieldLoss = COALESCE(YieldLoss, 0) + :scrap
                WHERE ProductionOrderId = :oid
            """),
            {'cq': completed_qty, 'scrap': scrap_qty, 'oid': order_id}
        )
        
        current_total = conn.execute(
            text("SELECT ActualQuantity FROM t_ProductionOrderDetail WHERE ProductionOrderId = :oid"),
            {'oid': order_id}
        ).scalar() or 0
        
        if current_total >= order[0]:
            new_status = 'Completed'
            conn.execute(
                text("""
                    UPDATE t_ProductionOrder
                    SET Status = 'Completed', ActualEndDateKey = :today
                    WHERE ProductionOrderId = :oid
                """),
                {'today': today_key, 'oid': order_id}
            )
        else:
            new_status = 'InProgress'
        
        if generate_serials and order[1]:
            prefix = serial_prefix or f"{order[1]}-"
            for i in range(completed_qty):
                serial_number = f"{prefix}{today_key}-{i+1:04d}"
                conn.execute(
                    text("""
                        INSERT INTO t_SerialNumbers
                        (ProductID, SerialNumber, ProductionOrderID, ProductionDateKey, Status)
                        VALUES (:pid, :sn, :oid, :date, 'Produced')
                    """),
                    {'pid': order[1], 'sn': serial_number, 'oid': order_id, 'date': today_key}
                )
        
        if order[1]:
            conn.execute(
                text("""
                    UPDATE t_InventoryOnHand
                    SET Qty = Qty + :qty
                    WHERE ProductID = :pid AND WarehouseID = 'MAIN'
                """),
                {'qty': completed_qty, 'pid': order[1]}
            )
    
    return jsonify({
        'message': 'Quantity reported',
        'status': new_status,
        'completedTotal': current_total,
        'plannedTotal': order[0]
    })


@api_bp.route('/orders/<string:order_id>/complete', methods=['POST'])
@login_required
def complete_job_order(order_id):
    """Complete the order."""
    today_key = int(datetime.now().strftime('%Y%m%d'))
    engine = get_engine()
    with engine.begin() as conn:
        order = conn.execute(
            text("SELECT Status FROM t_ProductionOrder WHERE ProductionOrderId = :oid"),
            {'oid': order_id}
        ).first()
        
        if not order:
            return jsonify({'error': 'Order not found'}), 404
        
        if order[0] != 'InProgress':
            return jsonify({'error': f'Cannot complete order in {order[0]} status'}), 400
        
        conn.execute(
            text("""
                UPDATE t_ProductionOrder
                SET Status = 'Completed', ActualEndDateKey = :today
                WHERE ProductionOrderId = :oid
            """),
            {'today': today_key, 'oid': order_id}
        )
    
    return jsonify({
        'message': 'Order completed',
        'actualEndDateKey': today_key,
        'actualEndDateFormatted': format_date_key(today_key)
    })


@api_bp.route('/orders/<string:order_id>/close', methods=['POST'])
@login_required
def close_job_order(order_id):
    """Close the order."""
    engine = get_engine()
    with engine.begin() as conn:
        order = conn.execute(
            text("SELECT Status FROM t_ProductionOrder WHERE ProductionOrderId = :oid"),
            {'oid': order_id}
        ).first()
        
        if not order:
            return jsonify({'error': 'Order not found'}), 404
        
        if order[0] != 'Completed':
            return jsonify({'error': f'Cannot close order in {order[0]} status'}), 400
        
        conn.execute(
            text("""
                UPDATE t_ProductionOrder
                SET Status = 'Closed'
                WHERE ProductionOrderId = :oid
            """),
            {'oid': order_id}
        )
    
    return jsonify({'message': 'Order closed'})


@api_bp.route('/orders/<string:order_id>/to-supplier', methods=['POST'])
@login_required
def issue_to_supplier(order_id):
    """Issue order to supplier (for subcontract orders)."""
    data = request.get_json()
    supplier_id = data.get('supplierId')
    po_number = data.get('poNumber', '')
    
    if not supplier_id:
        return jsonify({'error': 'Supplier ID required'}), 400
    
    engine = get_engine()
    with engine.begin() as conn:
        order = conn.execute(
            text("SELECT Status, OrderType FROM t_ProductionOrder WHERE ProductionOrderId = :oid"),
            {'oid': order_id}
        ).first()
        
        if not order:
            return jsonify({'error': 'Order not found'}), 404
        
        if order[1] != 'Subcontract':
            return jsonify({'error': 'Order is not a subcontract order'}), 400
        
        conn.execute(
            text("""
                INSERT INTO t_ProductionOrderPurchaseOrder
                (ProductionOrderID, PurchaseOrderID, Status, CreatedDateKey)
                VALUES (:oid, :po, 'Issued', :date)
            """),
            {
                'oid': order_id,
                'po': po_number or f"PO-{order_id}",
                'date': int(datetime.now().strftime('%Y%m%d'))
            }
        )
        
        conn.execute(
            text("""
                UPDATE t_ProductionOrder
                SET Status = 'Released'
                WHERE ProductionOrderId = :oid
            """),
            {'oid': order_id}
        )
    
    return jsonify({'message': 'Order issued to supplier'})


@api_bp.route('/orders/<string:order_id>/receive', methods=['POST'])
@login_required
def receive_from_supplier(order_id):
    """Receive materials from supplier (for subcontract orders)."""
    data = request.get_json()
    po_id = data.get('purchaseOrderId')
    received_qty = data.get('receivedQuantity', 0)
    
    if not po_id or received_qty <= 0:
        return jsonify({'error': 'PO ID and quantity required'}), 400
    
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE t_ProductionOrderPurchaseOrder
                SET Status = 'Received',
                    ReceivedDateKey = :date,
                    ReceivedQuantity = :qty
                WHERE ProductionOrderID = :oid AND PurchaseOrderID = :po
            """),
            {
                'oid': order_id,
                'po': po_id,
                'qty': received_qty,
                'date': int(datetime.now().strftime('%Y%m%d'))
            }
        )
        
        conn.execute(
            text("""
                UPDATE t_ProductionOrder
                SET Status = 'InProgress'
                WHERE ProductionOrderId = :oid
            """),
            {'oid': order_id}
        )
    
    return jsonify({'message': 'Supplier material received'})


@api_bp.route('/orders/<string:order_id>/purchase-orders', methods=['GET'])
@login_required
def get_order_purchase_orders(order_id):
    """Get purchase orders for an order (for receive from supplier dropdown)."""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT PurchaseOrderID, SupplierID, Status, CreatedDateKey
                FROM t_ProductionOrderPurchaseOrder
                WHERE ProductionOrderID = :oid AND Status != 'Received'
            """),
            {'oid': order_id}
        ).fetchall()
    
    purchase_orders = []
    for r in rows:
        created_date_key = r[3]
        purchase_orders.append({
            'purchaseOrderId': r[0],
            'supplierId': r[1],
            'status': r[2],
            'createdDateKey': created_date_key,
            'createdDateFormatted': format_date_key(created_date_key) if created_date_key else ''
        })
    return jsonify(purchase_orders)


@api_bp.route('/orders/<string:order_id>/workflow', methods=['GET'])
@login_required
def get_order_workflow(order_id):
    """Get workflow status for an order - Direct database query."""
    try:
        print(f"🔍 get_order_workflow called for order_id: {order_id}")
        
        engine = get_engine()
        with engine.connect() as conn:
            order = conn.execute(
                text("SELECT WorkflowID FROM t_ProductionOrder WHERE ProductionOrderId = :oid"),
                {'oid': order_id}
            ).first()
            
            print(f"📋 Order WorkflowID: {order[0] if order else 'None'}")
            
            if not order or not order[0]:
                return jsonify({
                    'has_workflow': False,
                    'message': 'No workflow associated with this order'
                }), 200
            
            workflow_id = order[0]
            
            workflow = conn.execute(
                text("""
                    SELECT TOP 1
                        w.WorkflowID,
                        w.Status,
                        w.CurrentStep,
                        wf.WorkflowName,
                        wf.Steps,
                        w.CreatedDateKey,
                        w.StartedDateKey,
                        w.CompletedDateKey,
                        w.AssignedTo,
                        u.FullName as AssignedToName,
                        w.DueDateKey,
                        wf.WorkflowCode
                    FROM t_Workflow w
                    JOIN t_WorkflowDefinitions wf ON w.WorkflowTemplateID = wf.WorkflowID
                    LEFT JOIN t_Users u ON w.AssignedTo = u.UserID
                    WHERE w.WorkflowID = :wfid
                """),
                {'wfid': workflow_id}
            ).first()
            
            print(f"📋 Workflow found: {workflow is not None}")
            
            if not workflow:
                return jsonify({
                    'has_workflow': False,
                    'message': f'Workflow ID {workflow_id} not found'
                }), 200
            
            try:
                current_step = int(workflow[2] or 0)
            except (ValueError, TypeError):
                current_step = 0
            
            steps_json = workflow[4] if workflow[4] else '[]'
            try:
                steps = json.loads(steps_json) if isinstance(steps_json, str) else steps_json
            except:
                steps = []
            
            current_step_name = 'Complete'
            current_step_role = None
            
            if steps and current_step > 0:
                step_index = current_step - 1
                if step_index < len(steps):
                    current_step_name = steps[step_index].get('name', f'Step {current_step}')
                    current_step_role = steps[step_index].get('role', None)
            
            created_date_key = workflow[5]
            started_date_key = workflow[6]
            completed_date_key = workflow[7]
            due_date_key = workflow[10]
            
            return jsonify({
                'has_workflow': True,
                'workflow': {
                    'workflowId': int(workflow[0]) if workflow[0] else None,
                    'status': workflow[1] or 'Pending',
                    'currentStep': current_step,
                    'workflowName': workflow[3],
                    'steps': steps,
                    'currentStepName': current_step_name,
                    'currentStepRole': current_step_role,
                    'createdDateKey': int(created_date_key) if created_date_key else None,
                    'createdDateFormatted': format_date_key(created_date_key) if created_date_key else '',
                    'startedDateKey': int(started_date_key) if started_date_key else None,
                    'startedDateFormatted': format_date_key(started_date_key) if started_date_key else '',
                    'completedDateKey': int(completed_date_key) if completed_date_key else None,
                    'completedDateFormatted': format_date_key(completed_date_key) if completed_date_key else '',
                    'assignedTo': workflow[8],
                    'assignedToName': workflow[9] or 'Not Assigned',
                    'dueDateKey': int(due_date_key) if due_date_key else None,
                    'dueDateFormatted': format_date_key(due_date_key) if due_date_key else '',
                    'workflowCode': workflow[11],
                    'isComplete': workflow[1] == 'Completed'
                }
            }), 200
            
    except Exception as e:
        current_app.logger.error(f"Error getting workflow: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@api_bp.route('/orders/<string:order_id>/hierarchy', methods=['GET'])
@login_required
def get_order_hierarchy_endpoint(order_id):
    """
    Get the full order hierarchy (parent and all children).
    Returns a tree structure of parent-child relationships.
    """
    try:
        print(f"🌳 get_order_hierarchy_endpoint called for order_id: {order_id}")
        
        from app.services.bom_service import get_order_hierarchy
        
        hierarchy = get_order_hierarchy(order_id)
        
        print(f"🌳 Hierarchy data: {hierarchy}")
        
        return jsonify(hierarchy), 200
    except Exception as e:
        current_app.logger.error(f"Error getting hierarchy: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ============================================================================
# PRODUCT / BOM / ROUTING ENDPOINTS
# ============================================================================

@api_bp.route('/production-orders/products-search')
@login_required
def product_search():
    """Search products for dropdown - filtered by industry."""
    q = request.args.get('q', '')
    industry = session.get('demo_industry', 'valve')
    
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT TOP 20 ProductId, descEnglish, descFarsi
                FROM t_Product 
                WHERE (ProductId LIKE :pattern OR descEnglish LIKE :pattern OR descFarsi LIKE :pattern)
                  AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
            """),
            {'pattern': f'%{q}%', 'industry': industry}
        ).fetchall()
    
    lang = session.get('lang', 'en')
    products = []
    for r in rows:
        name = r[1]
        if lang != 'en' and r[2] and str(r[2]).strip():
            name = r[2]
        products.append({
            'id': r[0],
            'name': name,
            'descEnglish': r[1],
            'descFarsi': r[2]
        })
    return jsonify(products)


@api_bp.route('/products/<string:product_id>/bom', methods=['GET'])
@login_required
def get_product_bom(product_id):
    """Get BOM for a product."""
    industry = session.get('demo_industry', 'valve')
    today_sql = get_today_datekey_sql()
    engine = get_engine()
    
    with engine.connect() as conn:
        rows = conn.execute(
            text(f"""
                SELECT b.ComponentProductID, b.Quantity, b.ScrapFactor,
                       p.descEnglish as Description,
                       COALESCE(ioh.Qty, 0) as Available
                FROM t_BOM b
                LEFT JOIN t_Product p ON b.ComponentProductID = p.ProductId
                LEFT JOIN t_InventoryOnHand ioh ON b.ComponentProductID = ioh.ProductID
                WHERE b.ParentProductID = :pid
                  AND (b.DemoIndustryCode = :industry OR b.DemoIndustryCode IS NULL)
                  AND (b.ObsoleteDateKey IS NULL OR b.ObsoleteDateKey > {today_sql})
                ORDER BY CAST(b.BOMLevel AS INT), CAST(b.OperationSequence AS INT)
            """),
            {'pid': product_id, 'industry': industry}
        ).fetchall()
    
    components = []
    for r in rows:
        required = r[1] * (1 + (r[2] or 0) / 100.0)
        shortage = max(0, required - (r[4] or 0))
        components.append({
            'componentId': r[0],
            'quantity': float(r[1]),
            'scrapFactor': float(r[2] or 0),
            'description': r[3] or '',
            'available': float(r[4] or 0),
            'requiredWithScrap': float(required),
            'shortage': float(shortage),
            'status': 'Shortage' if shortage > 0 else 'Available'
        })
    return jsonify(components)


@api_bp.route('/products/<string:product_id>/routing', methods=['GET'])
@login_required
def get_product_routing(product_id):
    """Get routing for a product."""
    industry = session.get('demo_industry', 'valve')
    today_sql = get_today_datekey_sql()
    engine = get_engine()
    
    with engine.connect() as conn:
        rows = conn.execute(
            text(f"""
                SELECT wcr.OperationSequence, wcr.WorkCenterID, 
                       wc.Name as WorkCenterName,
                       wcr.SetupTimeHours, wcr.RunTimeHoursPerUnit,
                       (wcr.SetupTimeHours + wcr.RunTimeHoursPerUnit) as TotalTime
                FROM t_WorkCenterRouting wcr
                LEFT JOIN t_WorkCenter wc ON wcr.WorkCenterID = wc.WorkCenterID
                LEFT JOIN t_Product p ON wcr.ProductID = p.ProductId
                WHERE wcr.ProductID = :pid
                  AND (p.DemoIndustryCode = :industry OR p.DemoIndustryCode IS NULL)
                  AND (wcr.ObsoleteDateKey IS NULL OR wcr.ObsoleteDateKey > {today_sql})
                ORDER BY CAST(wcr.OperationSequence AS INT)
            """),
            {'pid': product_id, 'industry': industry}
        ).fetchall()
    
    operations = []
    for r in rows:
        operations.append({
            'sequence': r[0],
            'workCenter': r[1],
            'workCenterName': r[2] or r[1],
            'setupTime': float(r[3] or 0),
            'runTime': float(r[4] or 0),
            'totalTime': float(r[5] or 0)
        })
    return jsonify(operations)


@api_bp.route('/products/<string:product_id>/quality', methods=['GET'])
@login_required
def get_product_quality(product_id):
    """Get quality operations for a product."""
    industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT qom.Name, qom.Category, qra.OperationSequence,
                       qra.IsCritical, qra.SamplingSize
                FROM t_QualityRoutingAssignment qra
                JOIN t_QualityOperationMaster qom ON qra.QCOperationID = qom.DefinitionID
                JOIN t_Product p ON qra.ProductID = p.ProductId
                WHERE qra.ProductID = :pid
                  AND (p.DemoIndustryCode = :industry OR p.DemoIndustryCode IS NULL)
                  AND qom.IsActive = 1
                ORDER BY CAST(qra.OperationSequence AS INT)
            """),
            {'pid': product_id, 'industry': industry}
        ).fetchall()
    
    quality_ops = []
    for r in rows:
        quality_ops.append({
            'name': r[0],
            'category': r[1] or 'General',
            'sequence': r[2] or 0,
            'isCritical': bool(r[3]),
            'samplingSize': r[4] or 1
        })
    return jsonify(quality_ops)


@api_bp.route('/products/<string:product_id>/versions', methods=['GET'])
@login_required
def get_product_versions(product_id):
    """Get product versions."""
    industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    with engine.connect() as conn:
        bom_count = conn.execute(
            text("SELECT COUNT(*) FROM t_BOM WHERE ParentProductID = :pid"),
            {'pid': product_id}
        ).scalar() or 0
        bom_version = 1 if bom_count == 0 else bom_count
        
        routing_count = conn.execute(
            text("SELECT COUNT(*) FROM t_WorkCenterRouting WHERE ProductID = :pid"),
            {'pid': product_id}
        ).scalar() or 0
        routing_version = 1 if routing_count == 0 else routing_count
        
        eng_version = conn.execute(
            text("SELECT COALESCE(EngineeringVersion, 1) FROM t_Product WHERE ProductId = :pid"),
            {'pid': product_id}
        ).scalar() or 1
    
    return jsonify({
        'bomVersion': bom_version,
        'routingVersion': routing_version,
        'engineeringVersion': eng_version
    })


# ============================================================================
# CRM/ORDERS API ENDPOINTS
# ============================================================================

@api_bp.route('/crm/customers/list', methods=['GET'])
@login_required
def customers_list():
    """List all customers for dropdown."""
    industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT CustomerID, CustomerName 
                FROM t_Customer 
                WHERE (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
                ORDER BY CustomerName
            """),
            {'industry': industry}
        ).fetchall()
    customers = [{'CustomerID': r[0], 'CustomerName': r[1]} for r in rows]
    return jsonify(customers)


@api_bp.route('/crm/orders/list', methods=['GET'])
@login_required
def crm_orders_list():
    """List sales orders for dropdown."""
    industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT SalesOrderID, CustomerID, ProductID, Qty 
                FROM t_SalesOrder 
                WHERE (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
                ORDER BY SalesOrderID DESC
            """),
            {'industry': industry}
        ).fetchall()
    
    orders = []
    for r in rows:
        orders.append({
            'id': r[0],
            'customerId': r[1],
            'productId': r[2],
            'qty': r[3],
            'display': f"{r[0]} - {r[1]}"
        })
    return jsonify(orders)


@api_bp.route('/projects/list', methods=['GET'])
@login_required
def projects_list_endpoint():
    """List all projects for dropdown."""
    industry = session.get('demo_industry', 'valve')
    lang = session.get('lang', 'en')
    
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT ProjectID, ProjectName, ProjectNameLocal
                FROM t_Project 
                WHERE DemoIndustryCode = :industry OR DemoIndustryCode IS NULL
                ORDER BY ProjectName
            """),
            {'industry': industry}
        ).fetchall()
    
    projects = []
    for r in rows:
        name = r[1]
        if lang != 'en' and r[2] and str(r[2]).strip():
            name = r[2]
        
        projects.append({
            'ProjectID': r[0],
            'ProjectName': name,
            'ProjectNameEnglish': r[1],
            'ProjectNameLocal': r[2]
        })
    
    return jsonify(projects)


@api_bp.route('/crm/projects/list', methods=['GET'])
@login_required
def crm_projects_list():
    """List all projects for dropdown - filtered by industry."""
    industry = session.get('demo_industry', 'valve')
    lang = session.get('lang', 'en')
    
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT ProjectID, ProjectName, ProjectNameLocal
                FROM t_Project 
                WHERE (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
                  AND (Status = 'Active' OR Status IS NULL)
                ORDER BY ProjectName
            """),
            {'industry': industry}
        ).fetchall()
    
    projects = []
    for r in rows:
        name = r[1]
        if lang != 'en' and r[2] and str(r[2]).strip():
            name = r[2]
        
        projects.append({
            'ProjectID': r[0],
            'ProjectName': name,
            'ProjectNameEnglish': r[1],
            'ProjectNameLocal': r[2]
        })
    
    return jsonify(projects)


# ============================================================================
# WORKFLOW STATUS PROXY
# ============================================================================

@api_bp.route('/workflow/status/<entity_type>/<entity_id>', methods=['GET'])
@login_required
def workflow_status_proxy(entity_type, entity_id):
    """Proxy to workflow module status endpoint."""
    response, error = call_workflow_api(f'/status/{entity_type}/{entity_id}', 'GET')
    
    if error == 'not_found':
        return jsonify({'error': 'Workflow not found'}), 404
    elif error:
        return jsonify({'error': error}), 503
    
    return jsonify(response)


# ============================================================================
# EXPORT ENDPOINTS
# ============================================================================

@api_bp.route('/export-csv', methods=['GET'])
@login_required
def export_csv_legacy():
    """Legacy export endpoint."""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT * FROM t_ProductionOrder")).fetchall()
        col_names = [desc[0] for desc in conn.execute(text("SELECT TOP 0 * FROM t_ProductionOrder")).cursor.description]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(col_names)
    for row in rows:
        writer.writerow(row)
    return Response(output.getvalue(), mimetype='text/csv',
                    headers={"Content-Disposition": "attachment;filename=production_orders.csv"})


# ============================================================================
# WORKSTATIONS API ENDPOINTS (Page 19)
# ============================================================================

@api_bp.route('/workstations/stats', methods=['GET'])
@login_required
def workstations_stats():
    """KPI counts for workstations."""
    industry = session.get('demo_industry', 'valve')
    
    engine = get_engine()
    with engine.connect() as conn:
        total = conn.execute(
            text("SELECT COUNT(*) FROM t_WorkCenter WHERE (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)"),
            {'industry': industry}
        ).scalar() or 0
        
        internal = conn.execute(
            text("SELECT COUNT(*) FROM t_WorkCenter WHERE Type = 'Internal' AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)"),
            {'industry': industry}
        ).scalar() or 0
        
        external = conn.execute(
            text("SELECT COUNT(*) FROM t_WorkCenter WHERE Type = 'External' AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)"),
            {'industry': industry}
        ).scalar() or 0
        
        ecos = 0
    
    return jsonify({
        'total': total,
        'internal': internal,
        'external': external,
        'pendingEcos': ecos
    })


@api_bp.route('/workstations', methods=['GET'])
@login_required
def list_workstations():
    """List workstations with filters."""
    industry = session.get('demo_industry', 'valve')
    lang = session.get('lang', 'en')
    
    work_type = request.args.get('type')
    active_only = request.args.get('active_only')
    search = request.args.get('search', '')
    
    engine = get_engine()
    params = {'industry': industry}
    
    conditions = ["(DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)"]
    
    if work_type and work_type != 'All':
        conditions.append("Type = :type")
        params['type'] = work_type
    
    if active_only == 'true':
        conditions.append("IsActive = 1 AND IsArchived = 0")
    
    if search:
        conditions.append("(WorkCenterID LIKE :search OR Name LIKE :search OR NameLocal LIKE :search)")
        params['search'] = f'%{search}%'
    
    where_clause = " AND ".join(conditions)
    
    sql = f"""
        SELECT WorkCenterID, Name, NameLocal, Type, Location,
               CapacityHours, MaxConcurrentJobs, HourlyRate, CurrencyCode,
               IsActive, IsArchived, Description, DescriptionLocal,
               EffectiveStartDateKey, EffectiveEndDateKey
        FROM t_WorkCenter
        WHERE {where_clause}
        ORDER BY IsActive DESC, Name
    """
    
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    
    workstations = []
    for r in rows:
        name = r[1] if lang != 'fa' or not r[2] else r[2]
        description = r[11] if lang != 'fa' or not r[12] else r[12]
        
        effective_start_key = r[13]
        effective_end_key = r[14]
        
        workstations.append({
            'id': r[0],
            'name': name,
            'WorkCenterID': r[0],
            'Name': name,
            'NameLocal': r[2],
            'Type': r[3] or 'Internal',
            'Location': r[4] or '',
            'CapacityHours': r[5] or 0,
            'MaxConcurrentJobs': r[6] or 1,
            'HourlyRate': r[7] or 0,
            'CurrencyCode': r[8] or 'USD',
            'IsActive': bool(r[9]),
            'IsArchived': bool(r[10]),
            'Description': description,
            'EffectiveStartDateKey': effective_start_key,
            'EffectiveStartDateFormatted': format_date_key(effective_start_key) if effective_start_key else '',
            'EffectiveEndDateKey': effective_end_key,
            'EffectiveEndDateFormatted': format_date_key(effective_end_key) if effective_end_key else ''
        })
    
    return jsonify(workstations)


@api_bp.route('/workstations/<string:workstation_id>', methods=['GET'])
@login_required
def get_workstation(workstation_id):
    """Get workstation detail."""
    industry = session.get('demo_industry', 'valve')
    
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT WorkCenterID, Name, NameLocal, Type, Location,
                       CapacityHours, MaxConcurrentJobs, HourlyRate, CurrencyCode,
                       IsActive, IsArchived, Description, DescriptionLocal,
                       EffectiveStartDateKey, EffectiveEndDateKey,
                       SkillsRequired, CreatedDateKey, ModifiedDateKey
                FROM t_WorkCenter
                WHERE WorkCenterID = :wid
                  AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
            """),
            {'wid': workstation_id, 'industry': industry}
        ).first()
        
        if not row:
            return jsonify({'error': 'Workstation not found'}), 404
        
        effective_start_key = row[13]
        effective_end_key = row[14]
        created_date_key = row[16]
        modified_date_key = row[17]
        
        return jsonify({
            'WorkCenterID': row[0],
            'Name': row[1],
            'NameLocal': row[2],
            'Type': row[3] or 'Internal',
            'Location': row[4] or '',
            'CapacityHours': row[5] or 0,
            'MaxConcurrentJobs': row[6] or 1,
            'HourlyRate': row[7] or 0,
            'CurrencyCode': row[8] or 'USD',
            'IsActive': int(row[9]),
            'IsArchived': int(row[10]),
            'Description': row[11] or '',
            'DescriptionLocal': row[12] or '',
            'EffectiveStartDateKey': effective_start_key,
            'EffectiveStartDateFormatted': format_date_key(effective_start_key) if effective_start_key else '',
            'EffectiveEndDateKey': effective_end_key,
            'EffectiveEndDateFormatted': format_date_key(effective_end_key) if effective_end_key else '',
            'SkillsRequired': json.loads(row[15]) if row[15] else [],
            'CreatedDateKey': created_date_key,
            'CreatedDateFormatted': format_date_key(created_date_key) if created_date_key else '',
            'ModifiedDateKey': modified_date_key,
            'ModifiedDateFormatted': format_date_key(modified_date_key) if modified_date_key else ''
        })


@api_bp.route('/workstations', methods=['POST'])
@login_required
def create_workstation():
    """Create a new workstation."""
    data = request.get_json()
    industry = session.get('demo_industry', 'valve')
    today_key = int(datetime.now().strftime('%Y%m%d'))
    
    if not data.get('name') or not data.get('workstationId'):
        return jsonify({'error': 'Code and Name are required'}), 400
    
    engine = get_engine()
    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT 1 FROM t_WorkCenter WHERE WorkCenterID = :wid"),
            {'wid': data['workstationId']}
        ).first()
        
        if existing:
            return jsonify({'error': f'Workstation ID {data["workstationId"]} already exists'}), 400
        
        conn.execute(
            text("""
                INSERT INTO t_WorkCenter (
                    WorkCenterID, Name, NameLocal, Type, Location,
                    CapacityHours, MaxConcurrentJobs, HourlyRate, CurrencyCode,
                    IsActive, IsArchived, Description, DescriptionLocal,
                    EffectiveStartDateKey, EffectiveEndDateKey,
                    SkillsRequired, CreatedDateKey, DemoIndustryCode
                ) VALUES (
                    :id, :name, :name_local, :type, :location,
                    :capacity, :max_jobs, :rate, :currency,
                    :active, 0, :desc, :desc_local,
                    :start_date, :end_date,
                    :skills, :created, :industry
                )
            """),
            {
                'id': data['workstationId'],
                'name': data['name'],
                'name_local': data.get('nameLocal', ''),
                'type': data.get('type', 'Internal'),
                'location': data.get('location', ''),
                'capacity': data.get('capacityHours', 0),
                'max_jobs': data.get('maxConcurrentJobs', 1),
                'rate': data.get('hourlyRate', 0),
                'currency': data.get('currencyCode', 'USD'),
                'active': 1 if data.get('isActive', True) else 0,
                'desc': data.get('description', ''),
                'desc_local': data.get('descriptionLocal', ''),
                'start_date': data.get('effectiveStartDateKey'),
                'end_date': data.get('effectiveEndDateKey'),
                'skills': json.dumps(data.get('skillsRequired', [])),
                'created': today_key,
                'industry': industry
            }
        )
    
    return jsonify({
        'message': 'Workstation created',
        'createdDateKey': today_key,
        'createdDateFormatted': format_date_key(today_key)
    }), 201


@api_bp.route('/workstations/<string:workstation_id>', methods=['PUT'])
@login_required
def update_workstation(workstation_id):
    """Update a workstation."""
    data = request.get_json()
    today_key = int(datetime.now().strftime('%Y%m%d'))
    
    engine = get_engine()
    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT 1 FROM t_WorkCenter WHERE WorkCenterID = :wid"),
            {'wid': workstation_id}
        ).first()
        
        if not existing:
            return jsonify({'error': 'Workstation not found'}), 404
        
        updates = []
        params = {'wid': workstation_id, 'modified': today_key}
        
        field_map = {
            'name': 'Name',
            'nameLocal': 'NameLocal',
            'type': 'Type',
            'location': 'Location',
            'capacityHours': 'CapacityHours',
            'maxConcurrentJobs': 'MaxConcurrentJobs',
            'hourlyRate': 'HourlyRate',
            'currencyCode': 'CurrencyCode',
            'isActive': 'IsActive',
            'description': 'Description',
            'descriptionLocal': 'DescriptionLocal',
            'effectiveStartDateKey': 'EffectiveStartDateKey',
            'effectiveEndDateKey': 'EffectiveEndDateKey'
        }
        
        for key, db_col in field_map.items():
            if key in data:
                updates.append(f"{db_col} = :{key}")
                params[key] = data[key]
        
        if 'skillsRequired' in data:
            updates.append("SkillsRequired = :skills")
            params['skills'] = json.dumps(data['skillsRequired'])
        
        if updates:
            updates.append("ModifiedDateKey = :modified")
            sql = f"UPDATE t_WorkCenter SET {', '.join(updates)} WHERE WorkCenterID = :wid"
            conn.execute(text(sql), params)
    
    return jsonify({
        'message': 'Workstation updated',
        'modifiedDateKey': today_key,
        'modifiedDateFormatted': format_date_key(today_key)
    })


@api_bp.route('/workstations/<string:workstation_id>/archive', methods=['POST'])
@login_required
def archive_workstation(workstation_id):
    """Archive a workstation."""
    data = request.get_json()
    today_key = int(datetime.now().strftime('%Y%m%d'))
    
    engine = get_engine()
    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT 1 FROM t_WorkCenter WHERE WorkCenterID = :wid AND IsArchived = 0"),
            {'wid': workstation_id}
        ).first()
        
        if not existing:
            return jsonify({'error': 'Workstation not found or already archived'}), 404
        
        conn.execute(
            text("""
                UPDATE t_WorkCenter
                SET IsArchived = 1, IsActive = 0, ModifiedDateKey = :modified
                WHERE WorkCenterID = :wid
            """),
            {'wid': workstation_id, 'modified': today_key}
        )
    
    return jsonify({
        'message': 'Workstation archived',
        'modifiedDateKey': today_key,
        'modifiedDateFormatted': format_date_key(today_key)
    })


@api_bp.route('/workstations/<string:workstation_id>/copy', methods=['POST'])
@login_required
def copy_workstation(workstation_id):
    """Copy a workstation."""
    data = request.get_json()
    new_id = data.get('newId')
    industry = session.get('demo_industry', 'valve')
    today_key = int(datetime.now().strftime('%Y%m%d'))
    
    if not new_id:
        return jsonify({'error': 'New code required'}), 400
    
    engine = get_engine()
    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT 1 FROM t_WorkCenter WHERE WorkCenterID = :wid"),
            {'wid': new_id}
        ).first()
        
        if existing:
            return jsonify({'error': f'Workstation ID {new_id} already exists'}), 400
        
        row = conn.execute(
            text("""
                SELECT Name, NameLocal, Type, Location,
                       CapacityHours, MaxConcurrentJobs, HourlyRate, CurrencyCode,
                       Description, DescriptionLocal, SkillsRequired
                FROM t_WorkCenter
                WHERE WorkCenterID = :wid
            """),
            {'wid': workstation_id}
        ).first()
        
        if not row:
            return jsonify({'error': 'Source workstation not found'}), 404
        
        conn.execute(
            text("""
                INSERT INTO t_WorkCenter (
                    WorkCenterID, Name, NameLocal, Type, Location,
                    CapacityHours, MaxConcurrentJobs, HourlyRate, CurrencyCode,
                    IsActive, IsArchived, Description, DescriptionLocal,
                    SkillsRequired, CreatedDateKey, DemoIndustryCode
                ) VALUES (
                    :id, :name, :name_local, :type, :location,
                    :capacity, :max_jobs, :rate, :currency,
                    1, 0, :desc, :desc_local,
                    :skills, :created, :industry
                )
            """),
            {
                'id': new_id,
                'name': row[0] + ' (Copy)',
                'name_local': row[1] + ' (کپی)' if row[1] else '',
                'type': row[2] or 'Internal',
                'location': row[3] or '',
                'capacity': row[4] or 0,
                'max_jobs': row[5] or 1,
                'rate': row[6] or 0,
                'currency': row[7] or 'USD',
                'desc': row[8] or '',
                'desc_local': row[9] or '',
                'skills': row[10] or '[]',
                'created': today_key,
                'industry': industry
            }
        )
    
    return jsonify({
        'message': 'Workstation copied',
        'newId': new_id,
        'createdDateKey': today_key,
        'createdDateFormatted': format_date_key(today_key)
    })


# ============================================================================
# MACHINE MANAGEMENT API ENDPOINTS (Page 20)
# ============================================================================

@api_bp.route('/machines/stats', methods=['GET'])
@login_required
def machines_stats():
    """KPI counts for machines."""
    industry = session.get('demo_industry', 'valve')
    
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT Status, COUNT(*) as cnt
                FROM t_Machine
                WHERE DemoIndustryCode = :industry OR DemoIndustryCode IS NULL
                GROUP BY Status
            """),
            {'industry': industry}
        ).fetchall()
    
    counts = {row[0]: row[1] for row in rows}
    
    return jsonify({
        'available': counts.get('Available', 0),
        'running': counts.get('Running', 0),
        'maintenance': counts.get('Maintenance', 0),
        'down': counts.get('Down', 0)
    })


@api_bp.route('/machines', methods=['GET'])
@login_required
def list_machines():
    """List machines with filters."""
    industry = session.get('demo_industry', 'valve')
    lang = session.get('lang', 'en')
    
    workstation_id = request.args.get('workstation_id')
    status = request.args.get('status')
    active_only = request.args.get('active_only') == 'true'
    search = request.args.get('search', '')
    
    engine = get_engine()
    params = {'industry': industry}
    
    conditions = ["(m.DemoIndustryCode = :industry OR m.DemoIndustryCode IS NULL)"]
    
    if workstation_id:
        conditions.append("m.WorkCenterID = :wc")
        params['wc'] = workstation_id
    
    if status:
        conditions.append("m.Status = :status")
        params['status'] = status
    
    if active_only:
        conditions.append("m.IsActive = 1")
    
    if search:
        conditions.append("(m.MachineCode LIKE :search OR m.MachineName LIKE :search)")
        params['search'] = f'%{search}%'
    
    where_clause = " AND ".join(conditions)
    
    sql = f"""
        SELECT 
            m.MachineID,
            m.MachineCode,
            m.MachineName,
            m.WorkCenterID,
            wc.Name as WorkCenterName,
            wc.NameLocal as WorkCenterNameLocal,
            m.MachineType,
            m.Manufacturer,
            m.Model,
            m.SerialNumber,
            m.CapacityHours,
            m.HourlyRate,
            m.Currency,
            m.Status,
            m.CurrentJobID,
            m.IsActive,
            m.DemoIndustryCode,
            po.ProductionOrderId as CurrentOrderId,
            p.descEnglish as CurrentProductName,
            p.descFarsi as CurrentProductNameFarsi
        FROM t_Machine m
        LEFT JOIN t_WorkCenter wc ON m.WorkCenterID = wc.WorkCenterID
        LEFT JOIN t_ProductionOrder po ON m.CurrentJobID = po.ProductionOrderId
        LEFT JOIN t_Product p ON po.ProductId = p.ProductId
        WHERE {where_clause}
        ORDER BY m.Status, m.MachineName
    """
    
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    
    machines = []
    for r in rows:
        wc_name = r[4] or r[3] or '-'
        if lang != 'en' and r[5] and str(r[5]).strip():
            wc_name = r[5]
        
        product_name = r[18]
        if lang != 'en' and r[19] and str(r[19]).strip():
            product_name = r[19]
        
        status_display = r[13] or 'Available'
        status_color = {
            'Available': 'success',
            'Running': 'primary',
            'Maintenance': 'warning',
            'Down': 'danger'
        }.get(status_display, 'secondary')
        
        try:
            capacity = float(r[10] or 0)
        except (ValueError, TypeError):
            capacity = 0
            
        try:
            hourly_rate = float(r[11] or 0)
        except (ValueError, TypeError):
            hourly_rate = 0
        
        currency = r[12] or 'USD'
        is_active = bool(r[15]) if r[15] is not None else True
        
        machines.append({
            'id': r[0],
            'code': r[1],
            'name': r[2],
            'workstationId': r[3],
            'workstationName': wc_name,
            'machineType': r[6] or 'Standard',
            'manufacturer': r[7] or '',
            'model': r[8] or '',
            'serialNumber': r[9] or '',
            'capacityHours': capacity,
            'hourlyRate': hourly_rate,
            'currency': currency,
            'status': status_display,
            'statusColor': status_color,
            'statusBadge': f'<span class="badge bg-{status_color}">{status_display}</span>',
            'currentJobId': r[14],
            'currentOrderId': r[17],
            'currentProductName': product_name or 'N/A',
            'isActive': is_active
        })
    
    return jsonify(machines)


@api_bp.route('/machines/<string:machine_id>', methods=['GET'])
@login_required
def get_machine(machine_id):
    """Get machine detail."""
    industry = session.get('demo_industry', 'valve')
    
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT 
                    m.MachineID,
                    m.MachineCode,
                    m.MachineName,
                    m.WorkCenterID,
                    wc.Name as WorkCenterName,
                    m.MachineType,
                    m.Manufacturer,
                    m.Model,
                    m.SerialNumber,
                    m.CapacityHours,
                    m.HourlyRate,
                    m.Currency,
                    m.Status,
                    m.CurrentJobID,
                    m.IsActive,
                    m.LastMaintenanceDateKey,
                    m.NextMaintenanceDateKey,
                    m.MaintenanceIntervalDays,
                    m.ShopFloorLocation,
                    m.CoordinatesX,
                    m.CoordinatesY
                FROM t_Machine m
                LEFT JOIN t_WorkCenter wc ON m.WorkCenterID = wc.WorkCenterID
                WHERE m.MachineID = :mid
                  AND (m.DemoIndustryCode = :industry OR m.DemoIndustryCode IS NULL)
            """),
            {'mid': machine_id, 'industry': industry}
        ).first()
        
        if not row:
            return jsonify({'error': 'Machine not found'}), 404
    
    try:
        capacity = float(row[9] or 0)
    except (ValueError, TypeError):
        capacity = 0
        
    try:
        hourly_rate = float(row[10] or 0)
    except (ValueError, TypeError):
        hourly_rate = 0
        
    try:
        maintenance_interval = int(row[17] or 180)
    except (ValueError, TypeError):
        maintenance_interval = 180
    
    last_maintenance_key = row[15]
    next_maintenance_key = row[16]
    
    return jsonify({
        'id': row[0],
        'code': row[1],
        'name': row[2],
        'workstationId': row[3],
        'workstationName': row[4] or row[3],
        'machineType': row[5] or 'Standard',
        'manufacturer': row[6] or '',
        'model': row[7] or '',
        'serialNumber': row[8] or '',
        'capacityHours': capacity,
        'hourlyRate': hourly_rate,
        'currency': row[11] or 'USD',
        'status': row[12] or 'Available',
        'currentJobId': row[13],
        'isActive': bool(row[14]) if row[14] is not None else True,
        'lastMaintenanceDateKey': last_maintenance_key,
        'lastMaintenanceDateFormatted': format_date_key(last_maintenance_key) if last_maintenance_key else '',
        'nextMaintenanceDateKey': next_maintenance_key,
        'nextMaintenanceDateFormatted': format_date_key(next_maintenance_key) if next_maintenance_key else '',
        'maintenanceIntervalDays': maintenance_interval,
        'shopFloorLocation': row[18] or '',
        'coordinatesX': row[19],
        'coordinatesY': row[20]
    })


@api_bp.route('/machines', methods=['POST'])
@login_required
def create_machine():
    """Create a new machine."""
    data = request.get_json()
    industry = session.get('demo_industry', 'valve')
    today_key = int(datetime.now().strftime('%Y%m%d'))
    
    if not data.get('code') or not data.get('name'):
        return jsonify({'error': 'Code and Name are required'}), 400
    
    engine = get_engine()
    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT 1 FROM t_Machine WHERE MachineCode = :code"),
            {'code': data['code']}
        ).first()
        
        if existing:
            return jsonify({'error': f'Machine code {data["code"]} already exists'}), 400
        
        conn.execute(
            text("""
                INSERT INTO t_Machine (
                    MachineCode, MachineName, WorkCenterID, MachineType,
                    Manufacturer, Model, SerialNumber,
                    CapacityHours, HourlyRate, Currency,
                    Status, IsActive,
                    LastMaintenanceDateKey, MaintenanceIntervalDays,
                    ShopFloorLocation, CoordinatesX, CoordinatesY,
                    DemoIndustryCode, CreatedDateKey
                ) VALUES (
                    :code, :name, :wc, :type,
                    :manufacturer, :model, :serial,
                    :capacity, :rate, :currency,
                    :status, :active,
                    :last_maintenance, :maintenance_interval,
                    :location, :x, :y,
                    :industry, :created
                )
            """),
            {
                'code': data['code'],
                'name': data['name'],
                'wc': data.get('workstationId'),
                'type': data.get('machineType', 'Standard'),
                'manufacturer': data.get('manufacturer', ''),
                'model': data.get('model', ''),
                'serial': data.get('serialNumber', ''),
                'capacity': data.get('capacityHours', 0),
                'rate': data.get('hourlyRate', 0),
                'currency': data.get('currency', 'USD'),
                'status': data.get('status', 'Available'),
                'active': 1 if data.get('isActive', True) else 0,
                'last_maintenance': data.get('lastMaintenanceDateKey'),
                'maintenance_interval': data.get('maintenanceIntervalDays', 180),
                'location': data.get('shopFloorLocation', ''),
                'x': data.get('coordinatesX'),
                'y': data.get('coordinatesY'),
                'industry': industry,
                'created': today_key
            }
        )
    
    return jsonify({
        'message': 'Machine created',
        'createdDateKey': today_key,
        'createdDateFormatted': format_date_key(today_key)
    }), 201


@api_bp.route('/machines/<int:machine_id>', methods=['PUT'])
@login_required
def update_machine(machine_id):
    """Update a machine."""
    data = request.get_json()
    today_key = int(datetime.now().strftime('%Y%m%d'))
    
    engine = get_engine()
    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT 1 FROM t_Machine WHERE MachineID = :mid"),
            {'mid': machine_id}
        ).first()
        
        if not existing:
            return jsonify({'error': 'Machine not found'}), 404
        
        updates = []
        params = {'mid': machine_id, 'modified': today_key}
        
        field_map = {
            'name': 'MachineName',
            'workstationId': 'WorkCenterID',
            'machineType': 'MachineType',
            'manufacturer': 'Manufacturer',
            'model': 'Model',
            'serialNumber': 'SerialNumber',
            'capacityHours': 'CapacityHours',
            'hourlyRate': 'HourlyRate',
            'currency': 'Currency',
            'status': 'Status',
            'isActive': 'IsActive',
            'lastMaintenanceDateKey': 'LastMaintenanceDateKey',
            'maintenanceIntervalDays': 'MaintenanceIntervalDays',
            'shopFloorLocation': 'ShopFloorLocation',
            'coordinatesX': 'CoordinatesX',
            'coordinatesY': 'CoordinatesY'
        }
        
        for key, db_col in field_map.items():
            if key in data:
                updates.append(f"{db_col} = :{key}")
                params[key] = data[key]
        
        if updates:
            updates.append("ModifiedDateKey = :modified")
            sql = f"UPDATE t_Machine SET {', '.join(updates)} WHERE MachineID = :mid"
            conn.execute(text(sql), params)
    
    return jsonify({
        'message': 'Machine updated',
        'modifiedDateKey': today_key,
        'modifiedDateFormatted': format_date_key(today_key)
    })


@api_bp.route('/machines/<int:machine_id>/assign', methods=['POST'])
@login_required
def assign_job_to_machine(machine_id):
    """Assign a job to a machine."""
    data = request.get_json()
    job_id = data.get('jobId')
    today_key = int(datetime.now().strftime('%Y%m%d'))
    
    if not job_id:
        return jsonify({'error': 'Job ID required'}), 400
    
    engine = get_engine()
    with engine.begin() as conn:
        machine = conn.execute(
            text("SELECT Status FROM t_Machine WHERE MachineID = :mid"),
            {'mid': machine_id}
        ).first()
        
        if not machine:
            return jsonify({'error': 'Machine not found'}), 404
        
        if machine[0] == 'Running':
            return jsonify({'error': 'Machine is already running a job'}), 400
        
        conn.execute(
            text("""
                UPDATE t_Machine
                SET Status = 'Running',
                    CurrentJobID = :job,
                    ModifiedDateKey = :date
                WHERE MachineID = :mid
            """),
            {'mid': machine_id, 'job': job_id, 'date': today_key}
        )
    
    return jsonify({
        'message': f'Job {job_id} assigned to machine',
        'modifiedDateKey': today_key,
        'modifiedDateFormatted': format_date_key(today_key)
    })


@api_bp.route('/machines/<int:machine_id>/downtime', methods=['POST'])
@login_required
def record_machine_downtime(machine_id):
    """Record machine downtime."""
    data = request.get_json()
    reason = data.get('reason', 'Unplanned downtime')
    today_key = int(datetime.now().strftime('%Y%m%d'))
    
    engine = get_engine()
    with engine.begin() as conn:
        machine = conn.execute(
            text("SELECT 1 FROM t_Machine WHERE MachineID = :mid"),
            {'mid': machine_id}
        ).first()
        
        if not machine:
            return jsonify({'error': 'Machine not found'}), 404
        
        conn.execute(
            text("""
                UPDATE t_Machine
                SET Status = 'Down',
                    ModifiedDateKey = :date
                WHERE MachineID = :mid
            """),
            {'mid': machine_id, 'date': today_key}
        )
    
    return jsonify({
        'message': 'Downtime recorded',
        'modifiedDateKey': today_key,
        'modifiedDateFormatted': format_date_key(today_key)
    })


@api_bp.route('/machines/<int:machine_id>/complete', methods=['POST'])
@login_required
def complete_machine_job(machine_id):
    """Complete the current job on a machine."""
    engine = get_engine()
    today_key = int(datetime.now().strftime('%Y%m%d'))
    
    with engine.begin() as conn:
        machine = conn.execute(
            text("SELECT Status, CurrentJobID FROM t_Machine WHERE MachineID = :mid"),
            {'mid': machine_id}
        ).first()
        
        if not machine:
            return jsonify({'error': 'Machine not found'}), 404
        
        if machine[0] != 'Running':
            return jsonify({'error': 'Machine is not running a job'}), 400
        
        conn.execute(
            text("""
                UPDATE t_Machine
                SET Status = 'Available',
                    CurrentJobID = NULL,
                    ModifiedDateKey = :date
                WHERE MachineID = :mid
            """),
            {'mid': machine_id, 'date': today_key}
        )
    
    return jsonify({
        'message': 'Job completed on machine',
        'modifiedDateKey': today_key,
        'modifiedDateFormatted': format_date_key(today_key)
    })


# ============================================================================
# SHOP FLOOR CONTROL API ENDPOINTS (Page 21)
# ============================================================================

@api_bp.route('/shop-floor/stats', methods=['GET'])
@login_required
def shop_floor_stats():
    """KPI counts for shop floor."""
    industry = session.get('demo_industry', 'valve')
    today_key = int(datetime.now().strftime('%Y%m%d'))
    
    engine = get_engine()
    
    with engine.connect() as conn:
        active = conn.execute(
            text("""
                SELECT COUNT(*) FROM t_ProductionOrder 
                WHERE Status = 'InProgress' 
                  AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
            """),
            {'industry': industry}
        ).scalar() or 0
        
        completed = conn.execute(
            text("""
                SELECT COUNT(*) FROM t_ProductionOrder 
                WHERE Status = 'Completed' 
                  AND ActualEndDateKey = :today
                  AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
            """),
            {'industry': industry, 'today': today_key}
        ).scalar() or 0
        
        downtime = conn.execute(
            text("""
                SELECT COUNT(*) FROM t_ShopFloorTransactions 
                WHERE TransactionType = 'Downtime' 
                  AND CAST(FORMAT(TransactionTime, 'yyyyMMdd') AS INT) = :today
            """),
            {'today': today_key}
        ).scalar() or 0
        
        pass_rate = conn.execute(
            text("""
                SELECT 
                    COALESCE(ROUND(100.0 * SUM(CASE WHEN QualityStatus = 'Pass' THEN 1 ELSE 0 END) / 
                    NULLIF(SUM(CASE WHEN QualityStatus IN ('Pass', 'Fail') THEN 1 ELSE 0 END), 0), 1), 0) as pass_rate
                FROM t_ShopFloorTransactions 
                WHERE TransactionType = 'QualityCheck'
                  AND QualityStatus IN ('Pass', 'Fail')
            """),
            {}
        ).scalar() or 0
    
    return jsonify({
        'active': active,
        'completed': completed,
        'downtime': downtime,
        'passRate': pass_rate
    })


@api_bp.route('/shop-floor/jobs', methods=['GET'])
@login_required
def shop_floor_jobs():
    """Get active shop floor jobs with transactions."""
    industry = session.get('demo_industry', 'valve')
    lang = session.get('lang', 'en')
    
    workstation_id = request.args.get('workstation_id')
    status = request.args.get('status')
    operator_id = request.args.get('operator_id')
    
    engine = get_engine()
    params = {'industry': industry}
    
    conditions = ["(po.DemoIndustryCode = :industry OR po.DemoIndustryCode IS NULL)"]
    
    if workstation_id:
        conditions.append("po.MachineID IN (SELECT MachineID FROM t_Machine WHERE WorkCenterID = :wc)")
        params['wc'] = workstation_id
    
    if status:
        conditions.append("po.Status = :status")
        params['status'] = status
    else:
        conditions.append("po.Status IN ('InProgress', 'Released')")
    
    if operator_id:
        conditions.append("EXISTS (SELECT 1 FROM t_ShopFloorTransactions sft WHERE sft.ProductionOrderID = po.ProductionOrderId AND sft.OperatorID = :op)")
        params['op'] = operator_id
    
    where_clause = " AND ".join(conditions)
    
    sql = f"""
        SELECT 
            po.ProductionOrderId,
            po.ProductId,
            p.descEnglish as ProductName,
            p.descFarsi as ProductNameFarsi,
            po.OrderQuantity,
            po.Status,
            po.StartDateKey,
            po.ActualStartDateKey,
            po.MachineID,
            m.MachineCode,
            m.MachineName,
            m.WorkCenterID,
            wc.Name as WorkCenterName,
            wc.NameLocal as WorkCenterNameLocal,
            COALESCE(pod.ActualQuantity, 0) as CompletedQty,
            COALESCE(pod.YieldLoss, 0) as YieldLoss,
            pod.OperationSequence,
            (SELECT TOP 1 OperatorID FROM t_ShopFloorTransactions 
             WHERE ProductionOrderID = po.ProductionOrderId 
               AND TransactionType = 'Start' 
             ORDER BY TransactionTime DESC) as OperatorId,
            (SELECT FullName FROM t_Users 
             WHERE UserID = (SELECT TOP 1 OperatorID FROM t_ShopFloorTransactions 
                             WHERE ProductionOrderID = po.ProductionOrderId 
                               AND TransactionType = 'Start' 
                             ORDER BY TransactionTime DESC)
            ) as OperatorName
        FROM t_ProductionOrder po
        LEFT JOIN t_Product p ON po.ProductId = p.ProductId
        LEFT JOIN t_Machine m ON po.MachineID = m.MachineID
        LEFT JOIN t_WorkCenter wc ON m.WorkCenterID = wc.WorkCenterID
        LEFT JOIN t_ProductionOrderDetail pod ON pod.ProductionOrderId = po.ProductionOrderId
        WHERE {where_clause}
        ORDER BY po.Status DESC, po.StartDateKey DESC
    """
    
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    
    jobs = []
    for r in rows:
        product_name = r[2]
        if lang != 'en' and r[3] and str(r[3]).strip():
            product_name = r[3]
        
        wc_name = r[12] or r[11] or '-'
        if lang != 'en' and r[13] and str(r[13]).strip():
            wc_name = r[13]
        
        status_display = r[5] or 'Draft'
        status_color = {
            'Draft': 'secondary',
            'Released': 'primary',
            'InProgress': 'info',
            'Completed': 'success',
            'Closed': 'dark',
            'Hold': 'warning',
            'Blocked': 'danger'
        }.get(status_display, 'secondary')
        
        start_date_key = r[6]
        actual_start_date_key = r[7]
        
        jobs.append({
            'id': r[0],
            'productId': r[1],
            'productName': product_name,
            'quantity': r[4] or 0,
            'status': status_display,
            'statusBadge': f'<span class="badge bg-{status_color}">{status_display}</span>',
            'startDateKey': start_date_key,
            'startDateFormatted': format_date_key(start_date_key) if start_date_key else '',
            'actualStartDateKey': actual_start_date_key,
            'actualStartDateFormatted': format_date_key(actual_start_date_key) if actual_start_date_key else '',
            'machineId': r[8],
            'machineCode': r[9] or '-',
            'machineName': r[10] or '-',
            'workstationId': r[11],
            'workstationName': wc_name,
            'completedQty': r[14] or 0,
            'yieldLoss': r[15] or 0,
            'operationSequence': r[16] or 0,
            'operatorId': r[17],
            'operatorName': r[18] or 'Unassigned',
            'progress': round((r[14] or 0) / (r[4] or 1) * 100, 1) if r[4] else 0
        })
    
    return jsonify(jobs)


@api_bp.route('/shop-floor/start', methods=['POST'])
@login_required
def shop_floor_start_job():
    """Start a job on the shop floor."""
    data = request.get_json()
    order_id = data.get('orderId')
    workstation_id = data.get('workstationId')
    operator_id = data.get('operatorId') or current_user.get_id()
    machine_id = data.get('machineId')
    
    if not order_id:
        return jsonify({'error': 'Order ID required'}), 400
    
    today_key = int(datetime.now().strftime('%Y%m%d'))
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    engine = get_engine()
    with engine.begin() as conn:
        order = conn.execute(
            text("SELECT Status FROM t_ProductionOrder WHERE ProductionOrderId = :oid"),
            {'oid': order_id}
        ).first()
        
        if not order:
            return jsonify({'error': 'Order not found'}), 404
        
        if order[0] not in ['Released', 'Draft']:
            return jsonify({'error': f'Cannot start order in {order[0]} status'}), 400
        
        conn.execute(
            text("""
                UPDATE t_ProductionOrder
                SET Status = 'InProgress',
                    ActualStartDateKey = :date,
                    MachineID = :machine
                WHERE ProductionOrderId = :oid
            """),
            {'oid': order_id, 'date': today_key, 'machine': machine_id}
        )
        
        conn.execute(
            text("""
                INSERT INTO t_ShopFloorTransactions (
                    ProductionOrderID, WorkCenterID, TransactionType,
                    TransactionTime, OperatorID, OrderId, Status,
                    StartTime, CreatedBy
                ) VALUES (
                    :oid, :wc, 'Start', :now, :op, :oid, 'Active', :now, :user
                )
            """),
            {
                'oid': order_id,
                'wc': workstation_id,
                'now': now,
                'op': operator_id,
                'user': current_user.get_id()
            }
        )
        
        conn.execute(
            text("""
                UPDATE t_ProductionOrderDetail
                SET Status = 'InProgress',
                    ActualStart = :now,
                    MachineID = :machine
                WHERE ProductionOrderId = :oid
            """),
            {'oid': order_id, 'now': now, 'machine': machine_id}
        )
    
    return jsonify({
        'message': f'Job {order_id} started successfully',
        'actualStartDateKey': today_key,
        'actualStartDateFormatted': format_date_key(today_key)
    })


@api_bp.route('/shop-floor/quantity', methods=['POST'])
@login_required
def shop_floor_report_quantity():
    """Report quantity completed."""
    data = request.get_json()
    order_id = data.get('orderId')
    completed_qty = data.get('completedQuantity', 0)
    scrap_qty = data.get('scrapQuantity', 0)
    quality_status = data.get('qualityStatus', 'Pending')
    
    if not order_id or completed_qty <= 0:
        return jsonify({'error': 'Order ID and valid quantity required'}), 400
    
    today_key = int(datetime.now().strftime('%Y%m%d'))
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    engine = get_engine()
    with engine.begin() as conn:
        order = conn.execute(
            text("SELECT OrderQuantity, Status FROM t_ProductionOrder WHERE ProductionOrderId = :oid"),
            {'oid': order_id}
        ).first()
        
        if not order:
            return jsonify({'error': 'Order not found'}), 404
        
        conn.execute(
            text("""
                UPDATE t_ProductionOrderDetail
                SET ActualQuantity = COALESCE(ActualQuantity, 0) + :qty,
                    YieldLoss = COALESCE(YieldLoss, 0) + :scrap
                WHERE ProductionOrderId = :oid
            """),
            {'oid': order_id, 'qty': completed_qty, 'scrap': scrap_qty}
        )
        
        conn.execute(
            text("""
                INSERT INTO t_ShopFloorTransactions (
                    ProductionOrderID, TransactionType, TransactionTime,
                    Quantity, OperatorID, QualityStatus, OrderId,
                    ReportedQuantity, CreatedBy
                ) VALUES (
                    :oid, 'Quantity', :now, :qty, :op, :status, :oid, :qty, :user
                )
            """),
            {
                'oid': order_id,
                'now': now,
                'qty': completed_qty,
                'op': current_user.get_id(),
                'status': quality_status,
                'user': current_user.get_id()
            }
        )
        
        current_total = conn.execute(
            text("SELECT ActualQuantity FROM t_ProductionOrderDetail WHERE ProductionOrderId = :oid"),
            {'oid': order_id}
        ).scalar() or 0
        
        if current_total >= order[0]:
            new_status = 'Completed'
            conn.execute(
                text("""
                    UPDATE t_ProductionOrder
                    SET Status = 'Completed',
                        ActualEndDateKey = :today
                    WHERE ProductionOrderId = :oid
                """),
                {'oid': order_id, 'today': today_key}
            )
        else:
            new_status = 'InProgress'
    
    return jsonify({
        'message': 'Quantity reported',
        'status': new_status,
        'completedTotal': current_total,
        'plannedTotal': order[0]
    })


@api_bp.route('/shop-floor/downtime', methods=['POST'])
@login_required
def shop_floor_log_downtime():
    """Log machine downtime."""
    data = request.get_json()
    workstation_id = data.get('workstationId')
    reason = data.get('reason', '')
    notes = data.get('notes', '')
    
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO t_ShopFloorTransactions (
                    WorkCenterID, TransactionType, TransactionTime,
                    DowntimeReason, Notes, OperatorID, CreatedBy
                ) VALUES (
                    :wc, 'Downtime', :now, :reason, :notes, :op, :user
                )
            """),
            {
                'wc': workstation_id,
                'now': now,
                'reason': reason,
                'notes': notes,
                'op': current_user.get_id(),
                'user': current_user.get_id()
            }
        )
    
    return jsonify({'message': 'Downtime logged'})


@api_bp.route('/shop-floor/inspect', methods=['POST'])
@login_required
def shop_floor_self_inspect():
    """Self-inspection for a job."""
    data = request.get_json()
    order_id = data.get('orderId')
    result = data.get('result')
    notes = data.get('notes', '')
    
    if not order_id or result not in ['Pass', 'Fail']:
        return jsonify({'error': 'Order ID and valid result required'}), 400
    
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO t_ShopFloorTransactions (
                    ProductionOrderID, TransactionType, TransactionTime,
                    QualityStatus, Notes, OperatorID, CreatedBy
                ) VALUES (
                    :oid, 'QualityCheck', :now, :result, :notes, :op, :user
                )
            """),
            {
                'oid': order_id,
                'now': now,
                'result': result,
                'notes': notes,
                'op': current_user.get_id(),
                'user': current_user.get_id()
            }
        )
        
        if result == 'Fail':
            conn.execute(
                text("""
                    UPDATE t_ProductionOrder
                    SET QualityStatus = 'Failed'
                    WHERE ProductionOrderId = :oid
                """),
                {'oid': order_id}
            )
        else:
            conn.execute(
                text("""
                    UPDATE t_ProductionOrder
                    SET QualityStatus = 'Passed'
                    WHERE ProductionOrderId = :oid
                """),
                {'oid': order_id}
            )
    
    return jsonify({'message': f'Inspection result: {result}'})


@api_bp.route('/shop-floor/complete', methods=['POST'])
@login_required
def shop_floor_complete_job():
    """Complete a job on the shop floor."""
    data = request.get_json()
    order_id = data.get('orderId')
    final_qty = data.get('finalQuantity')
    notes = data.get('notes', '')
    
    if not order_id:
        return jsonify({'error': 'Order ID required'}), 400
    
    today_key = int(datetime.now().strftime('%Y%m%d'))
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    engine = get_engine()
    with engine.begin() as conn:
        order = conn.execute(
            text("SELECT OrderQuantity, Status FROM t_ProductionOrder WHERE ProductionOrderId = :oid"),
            {'oid': order_id}
        ).first()
        
        if not order:
            return jsonify({'error': 'Order not found'}), 404
        
        if order[1] != 'InProgress':
            return jsonify({'error': f'Cannot complete order in {order[1]} status'}), 400
        
        if final_qty:
            conn.execute(
                text("""
                    UPDATE t_ProductionOrderDetail
                    SET ActualQuantity = :qty
                    WHERE ProductionOrderId = :oid
                """),
                {'oid': order_id, 'qty': final_qty}
            )
        
        conn.execute(
            text("""
                UPDATE t_ProductionOrder
                SET Status = 'Completed',
                    ActualEndDateKey = :date
                WHERE ProductionOrderId = :oid
            """),
            {'oid': order_id, 'date': today_key}
        )
        
        conn.execute(
            text("""
                INSERT INTO t_ShopFloorTransactions (
                    ProductionOrderID, TransactionType, TransactionTime,
                    Notes, OperatorID, CreatedBy
                ) VALUES (
                    :oid, 'Complete', :now, :notes, :op, :user
                )
            """),
            {
                'oid': order_id,
                'now': now,
                'notes': notes,
                'op': current_user.get_id(),
                'user': current_user.get_id()
            }
        )
    
    return jsonify({
        'message': f'Job {order_id} completed',
        'actualEndDateKey': today_key,
        'actualEndDateFormatted': format_date_key(today_key)
    })


# ============================================================================
# LOAD BOARD API ENDPOINTS (Page 22)
# ============================================================================

@api_bp.route('/load-board/data', methods=['GET'])
@login_required
def load_board_data():
    """Get load board data with Gantt chart information."""
    industry = session.get('demo_industry', 'valve')
    workstation_id = request.args.get('workstation_id')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    today_sql = get_today_datekey_sql()
    
    engine = get_engine()
    
    params = {'industry': industry}
    conditions = ["(po.DemoIndustryCode = :industry OR po.DemoIndustryCode IS NULL)"]
    
    if workstation_id:
        conditions.append("ps.WorkCenterID = :wc")
        params['wc'] = workstation_id
    
    if date_from:
        conditions.append("ps.ScheduledStartDateKey >= :date_from")
        params['date_from'] = int(date_from)
    
    if date_to:
        conditions.append("ps.ScheduledEndDateKey <= :date_to")
        params['date_to'] = int(date_to)
    
    where_clause = " AND ".join(conditions)
    
    sql = f"""
        SELECT 
            ps.ScheduleID,
            ps.ProductionOrderID,
            po.ProductId,
            p.descEnglish as ProductName,
            p.descFarsi as ProductNameFarsi,
            ps.WorkCenterID,
            wc.Name as WorkCenterName,
            wc.NameLocal as WorkCenterNameLocal,
            ps.SequenceNumber,
            ps.ScheduledStartDateKey,
            ps.ScheduledEndDateKey,
            ps.ScheduledHours,
            ps.Status,
            po.Priority,
            ps.QualityStatus,
            ps.IsQualityCritical
        FROM t_ProductionSchedule ps
        JOIN t_ProductionOrder po ON ps.ProductionOrderID = po.ProductionOrderId
        LEFT JOIN t_Product p ON po.ProductId = p.ProductId
        LEFT JOIN t_WorkCenter wc ON ps.WorkCenterID = wc.WorkCenterID
        WHERE {where_clause}
        ORDER BY CAST(ps.ScheduledStartDateKey AS INT), ps.WorkCenterID, CAST(ps.SequenceNumber AS INT)
    """
    
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    
    schedules = []
    for r in rows:
        product_name = r[3]
        lang = session.get('lang', 'en')
        if lang != 'en' and r[4] and str(r[4]).strip():
            product_name = r[4]
        
        wc_name = r[6] or r[5]
        if lang != 'en' and r[7] and str(r[7]).strip():
            wc_name = r[7]
        
        try:
            scheduled_hours = float(r[11] or 0)
        except (ValueError, TypeError):
            scheduled_hours = 0
        
        try:
            priority = int(r[13] or 2)
        except (ValueError, TypeError):
            priority = 2
        
        start_date_key = r[9]
        end_date_key = r[10]
        
        schedules.append({
            'id': r[0],
            'orderId': r[1],
            'productId': r[2],
            'productName': product_name,
            'workstationId': r[5],
            'workstationName': wc_name,
            'sequence': int(r[8]) if r[8] else 0,
            'startDateKey': start_date_key,
            'startDateFormatted': format_date_key(start_date_key) if start_date_key else '',
            'endDateKey': end_date_key,
            'endDateFormatted': format_date_key(end_date_key) if end_date_key else '',
            'scheduledHours': scheduled_hours,
            'status': r[12] or 'Scheduled',
            'priority': priority,
            'qualityStatus': r[14] or 'Pending',
            'isQualityCritical': bool(r[15]) if r[15] is not None else False
        })
    
    capacity_data = get_capacity_load(workstation_id, date_from, date_to, engine)
    
    return jsonify({
        'schedules': schedules,
        'capacity': capacity_data,
        'totalSchedules': len(schedules)
    })


def get_capacity_load(workstation_id, date_from, date_to, engine):
    """Calculate capacity load by workstation."""
    industry = session.get('demo_industry', 'valve')
    
    params = {'industry': industry}
    conditions = ["(wc.DemoIndustryCode = :industry OR wc.DemoIndustryCode IS NULL)"]
    
    if workstation_id:
        conditions.append("wc.WorkCenterID = :wc")
        params['wc'] = workstation_id
    
    where_clause = " AND ".join(conditions)
    
    sql = f"""
        SELECT 
            wc.WorkCenterID,
            wc.Name as WorkCenterName,
            wc.NameLocal as WorkCenterNameLocal,
            CAST(wc.CapacityHours AS DECIMAL(18,4)) as CapacityHours,
            CAST(COALESCE(SUM(CAST(ps.ScheduledHours AS DECIMAL(18,4))), 0) AS DECIMAL(18,4)) as TotalLoad,
            COUNT(ps.ScheduleID) as JobCount
        FROM t_WorkCenter wc
        LEFT JOIN t_ProductionSchedule ps ON wc.WorkCenterID = ps.WorkCenterID
        LEFT JOIN t_ProductionOrder po ON ps.ProductionOrderID = po.ProductionOrderId
        WHERE {where_clause}
        GROUP BY wc.WorkCenterID, wc.Name, wc.NameLocal, wc.CapacityHours
        ORDER BY wc.Name
    """
    
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    
    lang = session.get('lang', 'en')
    capacity = []
    for r in rows:
        wc_name = r[1]
        if lang != 'en' and r[2] and str(r[2]).strip():
            wc_name = r[2]
        
        try:
            capacity_hours = float(r[3] or 160)
        except (ValueError, TypeError):
            capacity_hours = 160.0
        
        try:
            total_load = float(r[4] or 0)
        except (ValueError, TypeError):
            total_load = 0.0
        
        utilization = round((total_load / capacity_hours * 100), 1) if capacity_hours > 0 else 0
        
        if utilization > 100:
            status_color = 'danger'
        elif utilization > 80:
            status_color = 'warning'
        else:
            status_color = 'success'
        
        capacity.append({
            'workstationId': r[0],
            'workstationName': wc_name,
            'capacityHours': capacity_hours,
            'totalLoad': total_load,
            'jobCount': r[5] or 0,
            'utilization': utilization,
            'statusColor': status_color
        })
    
    return capacity


@api_bp.route('/load-board/stats', methods=['GET'])
@login_required
def load_board_stats():
    """Get load board KPI stats."""
    industry = session.get('demo_industry', 'valve')
    workstation_id = request.args.get('workstation_id')
    
    engine = get_engine()
    params = {'industry': industry}
    conditions = ["(wc.DemoIndustryCode = :industry OR wc.DemoIndustryCode IS NULL)"]
    
    if workstation_id:
        conditions.append("wc.WorkCenterID = :wc")
        params['wc'] = workstation_id
    
    where_clause = " AND ".join(conditions)
    
    sql = f"""
        SELECT 
            COUNT(DISTINCT wc.WorkCenterID) as total_workstations,
            CAST(COALESCE(SUM(CAST(wc.CapacityHours AS DECIMAL(18,4))), 0) AS DECIMAL(18,4)) as total_capacity,
            CAST(COALESCE(SUM(CAST(ps.ScheduledHours AS DECIMAL(18,4))), 0) AS DECIMAL(18,4)) as total_load,
            COUNT(ps.ScheduleID) as total_jobs
        FROM t_WorkCenter wc
        LEFT JOIN t_ProductionSchedule ps ON wc.WorkCenterID = ps.WorkCenterID
        WHERE {where_clause}
    """
    
    with engine.connect() as conn:
        row = conn.execute(text(sql), params).first()
    
    if not row:
        return jsonify({
            'totalWorkstations': 0,
            'totalCapacity': 0,
            'totalLoad': 0,
            'utilization': 0,
            'bottleneckCount': 0
        })
    
    try:
        total_capacity = float(row[1] or 0)
    except (ValueError, TypeError):
        total_capacity = 0
    
    try:
        total_load = float(row[2] or 0)
    except (ValueError, TypeError):
        total_load = 0
    
    utilization = round((total_load / total_capacity * 100), 1) if total_capacity > 0 else 0
    
    bottleneck_sql = f"""
        SELECT COUNT(*) as bottleneck_count
        FROM (
            SELECT 
                wc.WorkCenterID,
                CAST(COALESCE(SUM(CAST(ps.ScheduledHours AS DECIMAL(18,4))), 0) AS DECIMAL(18,4)) as TotalLoad,
                CAST(wc.CapacityHours AS DECIMAL(18,4)) as CapacityHours
            FROM t_WorkCenter wc
            LEFT JOIN t_ProductionSchedule ps ON wc.WorkCenterID = ps.WorkCenterID
            WHERE {where_clause}
            GROUP BY wc.WorkCenterID, wc.CapacityHours
            HAVING CAST(COALESCE(SUM(CAST(ps.ScheduledHours AS DECIMAL(18,4))), 0) AS DECIMAL(18,4)) > (CAST(wc.CapacityHours AS DECIMAL(18,4)) * 0.8)
        ) AS bottlenecks
    """
    
    with engine.connect() as conn:
        bottleneck_count = conn.execute(text(bottleneck_sql), params).scalar() or 0
    
    return jsonify({
        'totalWorkstations': row[0] or 0,
        'totalCapacity': total_capacity,
        'totalLoad': total_load,
        'utilization': utilization,
        'bottleneckCount': bottleneck_count
    })


@api_bp.route('/load-board/generate', methods=['POST'])
@login_required
def generate_load_board():
    """Generate load board data from production orders."""
    industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    today_sql = get_today_datekey_sql()
    
    with engine.begin() as conn:
        conn.execute(
            text("""
                DELETE FROM t_ProductionSchedule 
                WHERE ProductionOrderID IN (
                    SELECT ProductionOrderId FROM t_ProductionOrder 
                    WHERE DemoIndustryCode = :industry
                )
            """),
            {'industry': industry}
        )
        
        orders = conn.execute(
            text(f"""
                SELECT 
                    po.ProductionOrderId,
                    po.ProductId,
                    CAST(po.OrderQuantity AS DECIMAL(18,4)) as OrderQuantity,
                    CAST(po.Priority AS INT) as Priority,
                    COALESCE(po.ScheduleStartDateKey, {today_sql}) as ScheduleStartDateKey,
                    COALESCE(po.ScheduleEndDateKey, {today_sql} + 7) as ScheduleEndDateKey,
                    wcr.WorkCenterID,
                    CAST(wcr.OperationSequence AS INT) as OperationSequence,
                    CAST(wcr.SetupTimeHours AS DECIMAL(18,4)) as SetupTimeHours,
                    CAST(wcr.RunTimeHoursPerUnit AS DECIMAL(18,4)) as RunTimeHoursPerUnit
                FROM t_ProductionOrder po
                JOIN t_WorkCenterRouting wcr ON po.ProductId = wcr.ProductID
                WHERE po.DemoIndustryCode = :industry
                  AND po.Status IN ('Released', 'InProgress')
                ORDER BY po.Priority, po.ScheduleStartDateKey
            """),
            {'industry': industry}
        ).fetchall()
        
        inserted_count = 0
        for r in orders:
            try:
                setup = float(r[8] or 0)
                run = float(r[9] or 0)
                qty = float(r[2] or 1)
                scheduled_hours = setup + (run * qty)
            except (ValueError, TypeError):
                scheduled_hours = 0
            
            conn.execute(
                text("""
                    INSERT INTO t_ProductionSchedule (
                        ProductionOrderID, WorkCenterID, ProductID,
                        SequenceNumber, ScheduledStartDateKey, ScheduledEndDateKey,
                        ScheduledHours, Status
                    ) VALUES (
                        :order_id, :wc, :product_id,
                        :seq, :start, :end,
                        :hours, 'Scheduled'
                    )
                """),
                {
                    'order_id': r[0],
                    'wc': r[6],
                    'product_id': r[1],
                    'seq': int(r[7]) if r[7] else 0,
                    'start': r[4],
                    'end': r[5],
                    'hours': scheduled_hours
                }
            )
            inserted_count += 1
        
        if inserted_count > 0:
            capacity_data = conn.execute(
                text(f"""
                    SELECT 
                        wc.WorkCenterID,
                        CAST(COALESCE(SUM(CAST(ps.ScheduledHours AS DECIMAL(18,4))), 0) AS DECIMAL(18,4)) as TotalLoad,
                        CAST(wc.CapacityHours AS DECIMAL(18,4)) as CapacityHours
                    FROM t_WorkCenter wc
                    LEFT JOIN t_ProductionSchedule ps ON wc.WorkCenterID = ps.WorkCenterID
                    WHERE wc.DemoIndustryCode = :industry
                    GROUP BY wc.WorkCenterID, wc.CapacityHours
                """),
                {'industry': industry}
            ).fetchall()
            
            for c in capacity_data:
                try:
                    total_load = float(c[1] or 0)
                    capacity_hours = float(c[2] or 160)
                except (ValueError, TypeError):
                    total_load = 0
                    capacity_hours = 160
                
                utilization = round((total_load / capacity_hours * 100), 1) if capacity_hours > 0 else 0
                
                today = int(datetime.now().strftime('%Y%m%d'))
                
                conn.execute(
                    text("""
                        MERGE INTO t_CapacityLoad AS target
                        USING (SELECT :wc AS WorkCenterID, :date AS DateKey) AS source
                        ON target.WorkCenterID = source.WorkCenterID AND target.DateKey = source.DateKey
                        WHEN MATCHED THEN
                            UPDATE SET TotalLoadHours = :load, AvailableCapacityHours = :capacity, 
                                       UtilizationPercent = :util, RunID = 'GENERATED'
                        WHEN NOT MATCHED THEN
                            INSERT (WorkCenterID, DateKey, TotalLoadHours, AvailableCapacityHours, UtilizationPercent, RunID)
                            VALUES (:wc, :date, :load, :capacity, :util, 'GENERATED');
                    """),
                    {
                        'wc': c[0],
                        'date': today,
                        'load': total_load,
                        'capacity': capacity_hours,
                        'util': utilization
                    }
                )
    
    return jsonify({
        'message': 'Load board generated successfully',
        'inserted': inserted_count
    })


@api_bp.route('/load-board/reschedule', methods=['POST'])
@login_required
def reschedule_job():
    """Reschedule a job on the load board."""
    data = request.get_json()
    schedule_id = data.get('scheduleId')
    new_start_date = data.get('newStartDate')
    new_end_date = data.get('newEndDate')
    new_workstation = data.get('newWorkstation')
    
    if not schedule_id:
        return jsonify({'error': 'Schedule ID required'}), 400
    
    engine = get_engine()
    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT 1 FROM t_ProductionSchedule WHERE ScheduleID = :sid"),
            {'sid': schedule_id}
        ).first()
        
        if not existing:
            return jsonify({'error': 'Schedule not found'}), 404
        
        updates = []
        params = {'sid': schedule_id}
        
        if new_start_date:
            updates.append("ScheduledStartDateKey = :start")
            params['start'] = int(new_start_date)
        
        if new_end_date:
            updates.append("ScheduledEndDateKey = :end")
            params['end'] = int(new_end_date)
        
        if new_workstation:
            updates.append("WorkCenterID = :wc")
            params['wc'] = new_workstation
        
        if updates:
            updates.append("ModifiedAt = GETDATE()")
            sql = f"UPDATE t_ProductionSchedule SET {', '.join(updates)} WHERE ScheduleID = :sid"
            conn.execute(text(sql), params)
    
    return jsonify({'message': 'Job rescheduled successfully'})


# ============================================================================
# MATERIAL PLANNING API ENDPOINTS (Page 23)
# ============================================================================

@api_bp.route('/material-planning/stats', methods=['GET'])
@login_required
def material_planning_stats():
    """Get KPI stats for material planning."""
    industry = session.get('demo_industry', 'valve')
    run_id = request.args.get('run_id')
    
    engine = get_engine()
    params = {'industry': industry}
    
    latest_run = None
    with engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT TOP 1 PlanningNumber FROM t_PlanningRuns 
                WHERE Status != 'Failed'
                ORDER BY RunDate DESC
            """)
        ).first()
        if row:
            latest_run = row[0]
    
    target_run = run_id or latest_run
    
    if not target_run:
        return jsonify({
            'requirements': 0,
            'plannedOrders': 0,
            'exceptions': 0,
            'shortages': 0
        })
    
    with engine.connect() as conn:
        requirements = conn.execute(
            text("""
                SELECT COALESCE(SUM(NetRequirements), 0) 
                FROM t_MRPRunResults 
                WHERE RunID = :run
            """),
            {'run': target_run}
        ).scalar() or 0
        
        planned_orders = conn.execute(
            text("""
                SELECT COALESCE(SUM(PlannedOrderReceipts), 0) 
                FROM t_MRPRunResults 
                WHERE RunID = :run
            """),
            {'run': target_run}
        ).scalar() or 0
        
        exceptions = conn.execute(
            text("""
                SELECT COUNT(*) 
                FROM t_MRPExceptions 
                WHERE PlanningNumber = :run AND Status = 'Open'
            """),
            {'run': target_run}
        ).scalar() or 0
        
        shortages = conn.execute(
            text("""
                SELECT COALESCE(SUM(NetRequirements), 0) 
                FROM t_MRPRunResults 
                WHERE RunID = :run AND NetRequirements > 0
            """),
            {'run': target_run}
        ).scalar() or 0
    
    return jsonify({
        'requirements': int(requirements),
        'plannedOrders': int(planned_orders),
        'exceptions': int(exceptions),
        'shortages': int(shortages)
    })


@api_bp.route('/material-planning/runs', methods=['GET'])
@login_required
def material_planning_runs():
    """Get list of MRP runs."""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT 
                    PlanningNumber,
                    RunDate,
                    Status,
                    CreatedBy,
                    Description,
                    PlanningHorizonDays,
                    PlanningStartDateKey,
                    PlanningEndDateKey,
                    IsSimulation
                FROM t_PlanningRuns 
                ORDER BY RunDate DESC
            """)
        ).fetchall()
    
    runs = []
    for r in rows:
        status_color = {
            'Completed': 'success',
            'Running': 'warning',
            'Failed': 'danger',
            'Pending': 'secondary',
            'Approved': 'primary'
        }.get(r[2], 'secondary')
        
        start_date_key = r[6]
        end_date_key = r[7]
        
        runs.append({
            'id': r[0],
            'date': r[1],
            'status': r[2],
            'statusColor': status_color,
            'createdBy': r[3],
            'description': r[4] or 'MRP Run',
            'horizon': r[5] or 90,
            'startDateKey': start_date_key,
            'startDateFormatted': format_date_key(start_date_key) if start_date_key else '',
            'endDateKey': end_date_key,
            'endDateFormatted': format_date_key(end_date_key) if end_date_key else '',
            'isSimulation': bool(r[8])
        })
    
    return jsonify(runs)


@api_bp.route('/material-planning/results', methods=['GET'])
@login_required
def material_planning_results():
    """Get MRP results for a specific run."""
    run_id = request.args.get('run_id')
    product = request.args.get('product')
    exception_type = request.args.get('exception_type')
    
    if not run_id:
        return jsonify({'error': 'Run ID required'}), 400
    
    engine = get_engine()
    params = {'run': run_id}
    conditions = ["RunID = :run"]
    
    if product:
        conditions.append("ProductID LIKE :product")
        params['product'] = f'%{product}%'
    
    if exception_type:
        conditions.append("NetRequirements > 0")
    
    where_clause = " AND ".join(conditions)
    
    sql = f"""
        SELECT 
            ProductID,
            DateKey,
            GrossRequirements,
            ScheduledReceipts,
            ProjectedOnHand,
            NetRequirements,
            PlannedOrderReceipts,
            PlannedOrderReleases,
            OrderType,
            IsFirm
        FROM t_MRPRunResults
        WHERE {where_clause}
        ORDER BY ProductID, DateKey
    """
    
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    
    results = []
    for r in rows:
        status = 'On Track'
        if r[5] > 0:
            status = 'Shortage'
        elif r[6] > 0:
            status = 'Planned'
        
        date_key = r[1]
        
        results.append({
            'productId': r[0],
            'dateKey': date_key,
            'dateFormatted': format_date_key(date_key) if date_key else '',
            'grossRequirements': r[2] or 0,
            'scheduledReceipts': r[3] or 0,
            'projectedOnHand': r[4] or 0,
            'netRequirements': r[5] or 0,
            'plannedOrderReceipts': r[6] or 0,
            'plannedOrderReleases': r[7] or 0,
            'orderType': r[8] or 'Manufacturing',
            'isFirm': bool(r[9]),
            'status': status
        })
    
    return jsonify({
        'runId': run_id,
        'results': results,
        'total': len(results)
    })


@api_bp.route('/material-planning/exceptions', methods=['GET'])
@login_required
def material_planning_exceptions():
    """Get MRP exceptions."""
    run_id = request.args.get('run_id')
    status = request.args.get('status', 'Open')
    product = request.args.get('product')
    
    engine = get_engine()
    params = {'status': status}
    conditions = ["Status = :status"]
    
    if run_id:
        conditions.append("PlanningNumber = :run")
        params['run'] = run_id
    
    if product:
        conditions.append("ProductID LIKE :product")
        params['product'] = f'%{product}%'
    
    where_clause = " AND ".join(conditions)
    
    sql = f"""
        SELECT 
            PlanningNumber,
            ProductID,
            MessageType,
            Details,
            DateKey,
            Status,
            Priority
        FROM t_MRPExceptions
        WHERE {where_clause}
        ORDER BY Priority DESC, DateKey
    """
    
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    
    exceptions = []
    for r in rows:
        priority_color = {
            1: 'danger',
            2: 'warning',
            3: 'info'
        }.get(r[6], 'secondary')
        
        date_key = r[4]
        
        exceptions.append({
            'planningNumber': r[0],
            'productId': r[1],
            'type': r[2],
            'details': r[3],
            'dateKey': date_key,
            'dateFormatted': format_date_key(date_key) if date_key else '',
            'status': r[5],
            'priority': r[6],
            'priorityColor': priority_color
        })
    
    return jsonify(exceptions)


@api_bp.route('/material-planning/run', methods=['POST'])
@login_required
def run_mrp():
    """Start a new MRP run."""
    data = request.get_json()
    planning_horizon = data.get('planningHorizonDays', 90)
    description = data.get('description', 'MRP Run')
    is_simulation = data.get('isSimulation', False)
    
    industry = session.get('demo_industry', 'valve')
    today = datetime.now()
    planning_number = f"MRP-{today.strftime('%Y%m%d%H%M%S')}"
    today_key = int(today.strftime('%Y%m%d'))
    
    engine = get_engine()
    
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO t_PlanningRuns (
                    PlanningNumber, RunDate, IsSimulation, Priority,
                    Description, PlanningHorizonDays,
                    PlanningStartDateKey, PlanningEndDateKey,
                    Status, CreatedBy
                ) VALUES (
                    :pn, :date, :sim, 2,
                    :desc, :horizon,
                    :start, :end,
                    'Running', :user
                )
            """),
            {
                'pn': planning_number,
                'date': today.strftime('%Y-%m-%d %H:%M:%S'),
                'sim': 1 if is_simulation else 0,
                'desc': description,
                'horizon': planning_horizon,
                'start': today_key,
                'end': today_key + planning_horizon * 100,
                'user': current_user.get_id()
            }
        )
    
    try:
        from app.services.mrp_service import run_mrp_async
        import threading
        
        params = {
            'planningHorizonDays': planning_horizon,
            'planningStartDateKey': today_key,
            'industry': industry
        }
        
        thread = threading.Thread(
            target=run_mrp_async,
            args=(planning_number, params, engine)
        )
        thread.start()
    except Exception as e:
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE t_PlanningRuns SET Status = 'Failed' WHERE PlanningNumber = :pn"),
                {'pn': planning_number}
            )
        return jsonify({'error': str(e)}), 500
    
    return jsonify({
        'message': 'MRP run started',
        'planningNumber': planning_number,
        'startDateKey': today_key,
        'startDateFormatted': format_date_key(today_key)
    }), 202


@api_bp.route('/material-planning/firm/<int:result_id>', methods=['POST'])
@login_required
def firm_planned_order(result_id):
    """Firm a planned order."""
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text("""
                SELECT ProductID, DateKey, PlannedOrderReceipts, OrderType
                FROM t_MRPRunResults 
                WHERE ResultID = :rid
            """),
            {'rid': result_id}
        ).first()
        
        if not row:
            return jsonify({'error': 'Planned order not found'}), 404
        
        conn.execute(
            text("""
                UPDATE t_MRPRunResults
                SET IsFirm = 1, IsFirmed = 1, FirmedBy = :user, FirmedDate = GETDATE()
                WHERE ResultID = :rid
            """),
            {'rid': result_id, 'user': current_user.get_id()}
        )
        
        product_id = row[0]
        date_key = row[1]
        qty = row[2]
        order_type = row[3]
        
        today_key = int(datetime.now().strftime('%Y%m%d'))
        order_id = f"PO-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        if order_type == 'Manufacturing':
            conn.execute(
                text("""
                    INSERT INTO t_ProductionOrder (
                        ProductionOrderId, ProductId, OrderQuantity, Status,
                        StartDateKey, EndDateKey, OrderSource, DemoIndustryCode
                    ) VALUES (
                        :oid, :pid, :qty, 'Draft',
                        :today, :date, 'MRP', :industry
                    )
                """),
                {
                    'oid': order_id,
                    'pid': product_id,
                    'qty': qty,
                    'today': today_key,
                    'date': date_key,
                    'industry': session.get('demo_industry', 'valve')
                }
            )
        else:
            conn.execute(
                text("""
                    INSERT INTO t_PurchaseOrder (
                        PurchaseOrderId, ProductId, Qty, Status,
                        OrderDateKey, ExpectedDeliveryDateKey, OrderSource
                    ) VALUES (
                        :oid, :pid, :qty, 'Draft',
                        :today, :date, 'MRP'
                    )
                """),
                {
                    'oid': f"PO-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                    'pid': product_id,
                    'qty': qty,
                    'today': today_key,
                    'date': date_key
                }
            )
    
    return jsonify({
        'message': 'Planned order firmed',
        'orderId': order_id,
        'dueDateKey': date_key,
        'dueDateFormatted': format_date_key(date_key) if date_key else ''
    })


# ============================================================================
# SUBCONTRACTOR MANAGEMENT API ENDPOINTS (Using Existing Tables)
# ============================================================================

@api_bp.route('/subcontractors/stats', methods=['GET'])
@login_required
def subcontractors_stats():
    """KPI counts for subcontractors using existing tables."""
    industry = session.get('demo_industry', 'valve')
    
    engine = get_engine()
    with engine.connect() as conn:
        total = conn.execute(
            text("""
                SELECT COUNT(*) FROM t_Suppliers 
                WHERE (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
                  AND Status = 'Active'
            """),
            {'industry': industry}
        ).scalar() or 0
        
        pending = conn.execute(
            text("""
                SELECT COUNT(*) FROM t_ProductionOrderPurchaseOrder 
                WHERE Status IN ('Issued')
            """),
            {}
        ).scalar() or 0
        
        completed = conn.execute(
            text("""
                SELECT COUNT(*) FROM t_ProductionOrderPurchaseOrder 
                WHERE Status = 'Received'
            """),
            {}
        ).scalar() or 0
        
        on_time = conn.execute(
            text("""
                SELECT COUNT(*) FROM t_SupplierScorecard 
                WHERE CAST(OnTimeDeliveryPercent AS DECIMAL(18,4)) >= 90
            """),
            {}
        ).scalar() or 0
        
        total_scorecards = conn.execute(
            text("SELECT COUNT(*) FROM t_SupplierScorecard"),
            {}
        ).scalar() or 1
        
        on_time_percent = round((on_time / total_scorecards * 100), 1) if total_scorecards > 0 else 0
    
    return jsonify({
        'total': total,
        'pendingOrders': pending,
        'completedOrders': completed,
        'onTimePercent': on_time_percent
    })


@api_bp.route('/subcontractors/service-types', methods=['GET'])
@login_required
def get_service_types():
    """Get list of service types from t_ServiceType."""
    industry = session.get('demo_industry', 'valve')
    lang = session.get('lang', 'en')
    
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT ServiceTypeID, ServiceName, Category, Description,
                       StandardLeadTimeDays, IsActive
                FROM t_ServiceType
                WHERE IsActive = 1
                ORDER BY ServiceName
            """),
            {}
        ).fetchall()
    
    service_types = []
    for r in rows:
        service_types.append({
            'id': r[0],
            'name': r[1],
            'category': r[2] or 'General',
            'description': r[3] or '',
            'leadTimeDays': int(r[4] or 0),
            'isActive': bool(r[5])
        })
    
    return jsonify(service_types)


@api_bp.route('/subcontractors', methods=['GET'])
@login_required
def list_subcontractors():
    """List subcontractors with filters from t_Suppliers."""
    industry = session.get('demo_industry', 'valve')
    lang = session.get('lang', 'en')
    
    service_type = request.args.get('service_type')
    rating = request.args.get('rating')
    active_only = request.args.get('active_only', 'true') == 'true'
    search = request.args.get('search', '')
    
    engine = get_engine()
    params = {'industry': industry}
    
    conditions = ["(DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)"]
    
    if active_only:
        conditions.append("Status = 'Active'")
    
    if search:
        conditions.append("(SupplierName LIKE :search OR SupplierCode LIKE :search OR ContactPerson LIKE :search)")
        params['search'] = f'%{search}%'
    
    if service_type:
        conditions.append("SupplierType = :service_type")
        params['service_type'] = service_type
    
    if rating:
        conditions.append("CAST(QualityRating AS DECIMAL(18,4)) >= :rating")
        params['rating'] = int(rating)
    
    where_clause = " AND ".join(conditions)
    
    sql = f"""
        SELECT 
            SupplierID as id,
            SupplierCode as code,
            SupplierName as name,
            SupplierNameLocal as name_local,
            SupplierType as specialty,
            ContactPerson,
            Phone,
            Email,
            Website,
            LeadTimeDays,
            CAST(QualityRating AS DECIMAL(18,4)) as quality_rating,
            CAST(DeliveryRating AS DECIMAL(18,4)) as delivery_rating,
            CAST(OverallRating AS DECIMAL(18,4)) as overall_rating,
            Status,
            Address,
            City,
            Country,
            DemoIndustryCode
        FROM t_Suppliers
        WHERE {where_clause}
        ORDER BY SupplierName
    """
    
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    
    subcontractors = []
    for r in rows:
        name = r[2]
        if lang != 'en' and r[3] and str(r[3]).strip():
            name = r[3]
        
        subcontractors.append({
            'id': r[0],
            'code': r[1] or r[0],
            'name': name,
            'specialty': r[4] or 'Other',
            'contactPerson': r[5] or '',
            'phone': r[6] or '',
            'email': r[7] or '',
            'website': r[8] or '',
            'leadTimeDays': int(r[9] or 0),
            'qualityRating': float(r[10] or 0),
            'deliveryRating': float(r[11] or 0),
            'overallRating': float(r[12] or 0),
            'status': r[13] or 'Active',
            'address': r[14] or '',
            'city': r[15] or '',
            'country': r[16] or '',
            'isActive': r[13] == 'Active'
        })
    
    return jsonify(subcontractors)


@api_bp.route('/subcontractors/<string:subcontractor_id>', methods=['GET'])
@login_required
def get_subcontractor(subcontractor_id):
    """Get subcontractor detail from t_Suppliers."""
    industry = session.get('demo_industry', 'valve')
    
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT 
                    SupplierID as id,
                    SupplierCode as code,
                    SupplierName as name,
                    SupplierNameLocal as name_local,
                    SupplierType as specialty,
                    ContactPerson,
                    Phone,
                    Email,
                    Website,
                    PaymentTerms,
                    LeadTimeDays,
                    CAST(QualityRating AS DECIMAL(18,4)) as quality_rating,
                    CAST(DeliveryRating AS DECIMAL(18,4)) as delivery_rating,
                    CAST(PriceRating AS DECIMAL(18,4)) as price_rating,
                    CAST(OverallRating AS DECIMAL(18,4)) as overall_rating,
                    Status,
                    Address,
                    City,
                    Country,
                    DemoIndustryCode
                FROM t_Suppliers
                WHERE SupplierID = :id
                  AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
            """),
            {'id': subcontractor_id, 'industry': industry}
        ).first()
        
        if not row:
            return jsonify({'error': 'Subcontractor not found'}), 404
    
    return jsonify({
        'id': row[0],
        'code': row[1] or row[0],
        'name': row[2],
        'nameLocal': row[3] or '',
        'specialty': row[4] or 'Other',
        'contactPerson': row[5] or '',
        'phone': row[6] or '',
        'email': row[7] or '',
        'website': row[8] or '',
        'paymentTerms': row[9] or '',
        'leadTimeDays': int(row[10] or 0),
        'qualityRating': float(row[11] or 0),
        'deliveryRating': float(row[12] or 0),
        'priceRating': float(row[13] or 0),
        'overallRating': float(row[14] or 0),
        'status': row[15] or 'Active',
        'address': row[16] or '',
        'city': row[17] or '',
        'country': row[18] or '',
        'isActive': row[15] == 'Active'
    })


@api_bp.route('/subcontractors/send', methods=['POST'])
@login_required
def send_to_subcontractor():
    """Send a job to a subcontractor."""
    data = request.get_json()
    order_id = data.get('orderId')
    supplier_id = data.get('supplierId')
    po_number = data.get('poNumber', '')
    
    if not order_id or not supplier_id:
        return jsonify({'error': 'Order ID and Supplier ID required'}), 400
    
    engine = get_engine()
    today_key = int(datetime.now().strftime('%Y%m%d'))
    
    with engine.begin() as conn:
        order = conn.execute(
            text("""
                SELECT OrderType, Status FROM t_ProductionOrder 
                WHERE ProductionOrderId = :oid
            """),
            {'oid': order_id}
        ).first()
        
        if not order:
            return jsonify({'error': 'Order not found'}), 404
        
        if order[0] != 'Subcontract':
            return jsonify({'error': 'Order is not a subcontract order'}), 400
        
        po_id = po_number or f"PO-SUP-{today_key}-{supplier_id}"
        
        conn.execute(
            text("""
                INSERT INTO t_PurchaseOrder (
                    PurchaseOrderId, SupplierID, OrderDateKey, Status
                ) VALUES (
                    :po_id, :supplier_id, :date, 'Issued'
                )
            """),
            {
                'po_id': po_id,
                'supplier_id': supplier_id,
                'date': today_key
            }
        )
        
        conn.execute(
            text("""
                INSERT INTO t_ProductionOrderPurchaseOrder (
                    ProductionOrderID, PurchaseOrderID, Status, CreatedDateKey
                ) VALUES (
                    :oid, :po_id, 'Issued', :date
                )
            """),
            {
                'oid': order_id,
                'po_id': po_id,
                'date': today_key
            }
        )
        
        conn.execute(
            text("""
                UPDATE t_ProductionOrder
                SET Status = 'Released'
                WHERE ProductionOrderId = :oid
            """),
            {'oid': order_id}
        )
    
    return jsonify({
        'message': 'Job sent to subcontractor',
        'purchaseOrderId': po_id,
        'sentDateKey': today_key,
        'sentDateFormatted': format_date_key(today_key)
    })


@api_bp.route('/subcontractors/receive', methods=['POST'])
@login_required
def receive_from_subcontractor():
    """Receive a completed job from subcontractor."""
    data = request.get_json()
    order_id = data.get('orderId')
    po_id = data.get('purchaseOrderId')
    received_qty = data.get('receivedQuantity', 0)
    quality_status = data.get('qualityStatus', 'Passed')
    
    if not order_id or not po_id:
        return jsonify({'error': 'Order ID and Purchase Order ID required'}), 400
    
    engine = get_engine()
    today_key = int(datetime.now().strftime('%Y%m%d'))
    
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE t_PurchaseOrder
                SET Status = 'Received',
                    ReceivedDateKey = :date
                WHERE PurchaseOrderId = :po_id
            """),
            {
                'po_id': po_id,
                'date': today_key
            }
        )
        
        conn.execute(
            text("""
                UPDATE t_ProductionOrderPurchaseOrder
                SET Status = 'Received',
                    CreatedDateKey = :date
                WHERE ProductionOrderID = :oid AND PurchaseOrderID = :po_id
            """),
            {
                'oid': order_id,
                'po_id': po_id,
                'date': today_key
            }
        )
        
        conn.execute(
            text("""
                UPDATE t_ProductionOrder
                SET Status = 'InProgress',
                    QualityStatus = :quality
                WHERE ProductionOrderId = :oid
            """),
            {
                'oid': order_id,
                'quality': quality_status
            }
        )
    
    return jsonify({
        'message': 'Job received from subcontractor',
        'qualityStatus': quality_status,
        'receivedDateKey': today_key,
        'receivedDateFormatted': format_date_key(today_key)
    })


@api_bp.route('/subcontractors/scorecard', methods=['POST'])
@login_required
def create_subcontractor_scorecard():
    """Create a scorecard entry for a subcontractor."""
    data = request.get_json()
    supplier_id = data.get('supplierId')
    
    if not supplier_id:
        return jsonify({'error': 'Supplier ID required'}), 400
    
    engine = get_engine()
    today_key = int(datetime.now().strftime('%Y%m%d'))
    
    with engine.begin() as conn:
        supplier = conn.execute(
            text("SELECT 1 FROM t_Suppliers WHERE SupplierID = :id"),
            {'id': supplier_id}
        ).first()
        
        if not supplier:
            return jsonify({'error': 'Supplier not found'}), 404
        
        scorecard_id = f"SC-{supplier_id}-{today_key}"
        
        conn.execute(
            text("""
                INSERT INTO t_SupplierScorecard (
                    ScorecardID, SupplierID, PeriodStartDateKey, PeriodEndDateKey,
                    QualityScore, DeliveryScore, PriceScore, ResponsivenessScore,
                    TechnicalSupportScore, OverallScore, OverallRating,
                    NumberOfOrders, NumberOfDefects, DefectRate,
                    AverageLeadTimeDays, OnTimeDeliveryPercent, TotalSpent,
                    ScorecardStatus, CreatedDateKey
                ) VALUES (
                    :id, :supplier_id, :period_start, :period_end,
                    :quality, :delivery, :price, :responsiveness,
                    :tech_support, :overall, :overall_rating,
                    :orders, :defects, :defect_rate,
                    :lead_time, :on_time, :spent,
                    'Draft', :created
                )
            """),
            {
                'id': scorecard_id,
                'supplier_id': supplier_id,
                'period_start': today_key,
                'period_end': today_key + 90,
                'quality': 0,
                'delivery': 0,
                'price': 0,
                'responsiveness': 0,
                'tech_support': 0,
                'overall': 0,
                'overall_rating': 'Pending',
                'orders': 0,
                'defects': 0,
                'defect_rate': 0,
                'lead_time': 0,
                'on_time': 0,
                'spent': 0,
                'created': today_key
            }
        )
    
    return jsonify({
        'message': 'Scorecard created',
        'scorecardId': scorecard_id,
        'periodStartDateKey': today_key,
        'periodStartDateFormatted': format_date_key(today_key),
        'periodEndDateKey': today_key + 90,
        'periodEndDateFormatted': format_date_key(today_key + 90)
    })
    

# ============================================================================
# MAINTENANCE MANAGEMENT API ENDPOINTS - MATCHING YOUR TABLE STRUCTURE
# ============================================================================

@api_bp.route('/maintenance/stats', methods=['GET'])
@login_required
def maintenance_stats():
    """KPI counts for maintenance."""
    industry = session.get('demo_industry', 'valve')
    today_key = int(datetime.now().strftime('%Y%m%d'))
    
    today_str = datetime.now().strftime('%Y-%m-%d')
    
    engine = get_engine()
    with engine.connect() as conn:
        scheduled = conn.execute(
            text("""
                SELECT COUNT(*) FROM t_EquipmentMaintenance 
                WHERE Status = 'Scheduled'
                  AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
            """),
            {'industry': industry}
        ).scalar() or 0
        
        in_progress = conn.execute(
            text("""
                SELECT COUNT(*) FROM t_EquipmentMaintenance 
                WHERE Status = 'InProgress'
                  AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
            """),
            {'industry': industry}
        ).scalar() or 0
        
        overdue = conn.execute(
            text("""
                SELECT COUNT(*) FROM t_EquipmentMaintenance 
                WHERE Status IN ('Scheduled', 'InProgress')
                  AND CAST(NextMaintenanceDate AS DATE) < CAST(GETDATE() AS DATE)
                  AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
            """),
            {'industry': industry}
        ).scalar() or 0
        
        completed = conn.execute(
            text("""
                SELECT COUNT(*) FROM t_EquipmentMaintenance 
                WHERE Status = 'Completed'
                  AND (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
            """),
            {'industry': industry}
        ).scalar() or 0
    
    return jsonify({
        'scheduled': scheduled,
        'inProgress': in_progress,
        'overdue': overdue,
        'completed': completed
    })


@api_bp.route('/maintenance/equipment', methods=['GET'])
@login_required
def maintenance_equipment_list():
    """Get list of equipment for dropdown."""
    industry = session.get('demo_industry', 'valve')
    
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT DISTINCT 
                    EquipmentID as id,
                    EquipmentName as name
                FROM t_EquipmentMaintenance
                WHERE (DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)
                  AND EquipmentID IS NOT NULL
                ORDER BY EquipmentName
            """),
            {'industry': industry}
        ).fetchall()
    
    equipment = []
    for r in rows:
        equipment.append({
            'id': r[0],
            'name': r[1] or r[0],
            'code': r[0]
        })
    
    return jsonify(equipment)


@api_bp.route('/maintenance', methods=['GET'])
@login_required
def list_maintenance():
    """List maintenance records with filters."""
    industry = session.get('demo_industry', 'valve')
    lang = session.get('lang', 'en')
    
    equipment_id = request.args.get('equipment_id')
    status = request.args.get('status')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    
    engine = get_engine()
    params = {'industry': industry}
    
    conditions = ["(DemoIndustryCode = :industry OR DemoIndustryCode IS NULL)"]
    
    if equipment_id:
        conditions.append("EquipmentID = :equipment_id")
        params['equipment_id'] = equipment_id
    
    if status:
        conditions.append("Status = :status")
        params['status'] = status
    
    if date_from:
        conditions.append("CAST(NextMaintenanceDate AS DATE) >= CAST(:date_from AS DATE)")
        params['date_from'] = date_from
    
    if date_to:
        conditions.append("CAST(NextMaintenanceDate AS DATE) <= CAST(:date_to AS DATE)")
        params['date_to'] = date_to
    
    where_clause = " AND ".join(conditions)
    
    sql = f"""
        SELECT 
            MaintenanceID as id,
            EquipmentID,
            EquipmentName,
            WorkCenterID,
            MaintenanceType as type,
            ScheduleType,
            IntervalDays,
            LastMaintenanceDate as lastMaintenance,
            NextMaintenanceDate as nextDue,
            Status,
            ActualDate as actualCompletionDate,
            TechnicianID,
            DurationHours,
            Cost,
            Notes,
            CreatedDate,
            ModifiedDate,
            DemoIndustryCode
        FROM t_EquipmentMaintenance
        WHERE {where_clause}
        ORDER BY NextMaintenanceDate ASC, Status
    """
    
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    
    maintenance = []
    for r in rows:
        status_display = r[9] or 'Scheduled'
        maintenance.append({
            'id': r[0],
            'equipmentId': r[1],
            'equipmentName': r[2] or r[1],
            'workCenterId': r[3],
            'type': r[4] or 'Preventive',
            'scheduleType': r[5] or 'Once',
            'intervalDays': int(r[6] or 0),
            'lastMaintenance': r[7] if r[7] else None,
            'nextDue': r[8] if r[8] else None,
            'status': status_display,
            'actualCompletionDate': r[10] if r[10] else None,
            'technicianId': r[11],
            'durationHours': float(r[12] or 0),
            'cost': float(r[13] or 0),
            'notes': r[14] or '',
            'createdDate': r[15],
            'modifiedDate': r[16]
        })
    
    return jsonify(maintenance)


@api_bp.route('/maintenance', methods=['POST'])
@login_required
def create_maintenance():
    """Create a new maintenance record."""
    data = request.get_json()
    industry = session.get('demo_industry', 'valve')
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    equipment_id = data.get('equipmentId')
    maintenance_type = data.get('maintenanceType', 'Preventive')
    schedule_type = data.get('scheduleType', 'Once')
    interval_days = data.get('intervalDays', 0)
    last_date = data.get('lastDate')
    next_due = data.get('nextDue')
    assigned_to = data.get('assignedTo')
    work_center_id = data.get('workCenterId')
    description = data.get('description', '')
    
    if not equipment_id or not next_due:
        return jsonify({'error': 'Equipment and Next Due Date required'}), 400
    
    engine = get_engine()
    with engine.begin() as conn:
        equip_name = conn.execute(
            text("SELECT MachineName FROM t_Machine WHERE MachineID = :id"),
            {'id': equipment_id}
        ).scalar() or equipment_id
        
        result = conn.execute(
            text("SELECT MAX(CAST(MaintenanceID AS INT)) FROM t_EquipmentMaintenance")
        ).scalar() or 0
        new_id = result + 1
        
        conn.execute(
            text("""
                INSERT INTO t_EquipmentMaintenance (
                    MaintenanceID, EquipmentID, EquipmentName, WorkCenterID,
                    MaintenanceType, ScheduleType, IntervalDays,
                    LastMaintenanceDate, NextMaintenanceDate, Status,
                    Notes, CreatedDate, DemoIndustryCode
                ) VALUES (
                    :id, :equip_id, :equip_name, :wc_id,
                    :type, :schedule_type, :interval,
                    :last_date, :next_due, 'Scheduled',
                    :desc, :created, :industry
                )
            """),
            {
                'id': str(new_id),
                'equip_id': equipment_id,
                'equip_name': equip_name,
                'wc_id': work_center_id,
                'type': maintenance_type,
                'schedule_type': schedule_type,
                'interval': interval_days,
                'last_date': last_date if last_date else None,
                'next_due': next_due,
                'desc': description,
                'created': now,
                'industry': industry
            }
        )
    
    return jsonify({
        'message': 'Maintenance scheduled successfully',
        'id': str(new_id)
    }), 201


@api_bp.route('/maintenance/<string:maintenance_id>/start', methods=['POST'])
@login_required
def start_maintenance(maintenance_id):
    """Start a maintenance work order."""
    engine = get_engine()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT Status FROM t_EquipmentMaintenance WHERE MaintenanceID = :id"),
            {'id': maintenance_id}
        ).first()
        
        if not existing:
            return jsonify({'error': 'Maintenance record not found'}), 404
        
        if existing[0] == 'Completed':
            return jsonify({'error': 'Maintenance already completed'}), 400
        
        conn.execute(
            text("""
                UPDATE t_EquipmentMaintenance
                SET Status = 'InProgress',
                    ModifiedDate = :now
                WHERE MaintenanceID = :id
            """),
            {'id': maintenance_id, 'now': now}
        )
    
    return jsonify({'message': 'Maintenance started'})


@api_bp.route('/maintenance/<string:maintenance_id>/complete', methods=['POST'])
@login_required
def complete_maintenance(maintenance_id):
    """Complete a maintenance work order."""
    data = request.get_json()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    actual_date = data.get('actualDate')
    technician_id = data.get('technicianId')
    duration_hours = data.get('durationHours', 0)
    cost = data.get('cost', 0)
    notes = data.get('notes', '')
    
    if not actual_date:
        return jsonify({'error': 'Completion date required'}), 400
    
    engine = get_engine()
    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT Status, IntervalDays FROM t_EquipmentMaintenance WHERE MaintenanceID = :id"),
            {'id': maintenance_id}
        ).first()
        
        if not existing:
            return jsonify({'error': 'Maintenance record not found'}), 404
        
        if existing[0] == 'Completed':
            return jsonify({'error': 'Maintenance already completed'}), 400
        
        interval = int(existing[1] or 0)
        next_due = None
        if interval > 0:
            from datetime import datetime, timedelta
            actual_dt = datetime.strptime(actual_date, '%Y-%m-%d')
            next_dt = actual_dt + timedelta(days=interval)
            next_due = next_dt.strftime('%Y-%m-%d')
        
        conn.execute(
            text("""
                UPDATE t_EquipmentMaintenance
                SET Status = 'Completed',
                    ActualDate = :actual_date,
                    TechnicianID = :technician,
                    DurationHours = :duration,
                    Cost = :cost,
                    Notes = :notes,
                    LastMaintenanceDate = :last_date,
                    NextMaintenanceDate = COALESCE(:next_due, NextMaintenanceDate),
                    ModifiedDate = :now
                WHERE MaintenanceID = :id
            """),
            {
                'id': maintenance_id,
                'actual_date': actual_date,
                'technician': technician_id,
                'duration': duration_hours,
                'cost': cost,
                'notes': notes,
                'last_date': actual_date,
                'next_due': next_due,
                'now': now
            }
        )
    
    return jsonify({'message': 'Maintenance completed successfully'})


@api_bp.route('/maintenance/<string:maintenance_id>', methods=['PUT'])
@login_required
def update_maintenance(maintenance_id):
    """Update a maintenance record."""
    data = request.get_json()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    engine = get_engine()
    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT 1 FROM t_EquipmentMaintenance WHERE MaintenanceID = :id"),
            {'id': maintenance_id}
        ).first()
        
        if not existing:
            return jsonify({'error': 'Maintenance record not found'}), 404
        
        updates = []
        params = {'id': maintenance_id, 'now': now}
        
        if 'maintenanceType' in data:
            updates.append("MaintenanceType = :type")
            params['type'] = data['maintenanceType']
        
        if 'scheduleType' in data:
            updates.append("ScheduleType = :schedule_type")
            params['schedule_type'] = data['scheduleType']
        
        if 'intervalDays' in data:
            updates.append("IntervalDays = :interval")
            params['interval'] = data['intervalDays']
        
        if 'lastDate' in data:
            updates.append("LastMaintenanceDate = :last_date")
            params['last_date'] = data['lastDate']
        
        if 'nextDue' in data:
            updates.append("NextMaintenanceDate = :next_due")
            params['next_due'] = data['nextDue']
        
        if 'assignedTo' in data:
            updates.append("AssignedTo = :assigned_to")
            params['assigned_to'] = data['assignedTo']
        
        if 'notes' in data:
            updates.append("Notes = :notes")
            params['notes'] = data['notes']
        
        if updates:
            updates.append("ModifiedDate = :now")
            sql = f"UPDATE t_EquipmentMaintenance SET {', '.join(updates)} WHERE MaintenanceID = :id"
            conn.execute(text(sql), params)
    
    return jsonify({'message': 'Maintenance updated successfully'})


@api_bp.route('/maintenance/export-csv', methods=['GET'])
@login_required
def export_maintenance_csv():
    """Export maintenance data to CSV."""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT * FROM t_EquipmentMaintenance")).fetchall()
        col_names = [desc[0] for desc in conn.execute(text("SELECT TOP 0 * FROM t_EquipmentMaintenance")).cursor.description]
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(col_names)
    for row in rows:
        writer.writerow(row)
    
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={"Content-Disposition": "attachment;filename=maintenance_schedule.csv"}
    )