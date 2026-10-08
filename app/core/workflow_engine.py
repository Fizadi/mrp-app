# workflow_engine.py
import json
import logging
import re
from datetime import datetime, timedelta
from sqlalchemy.sql import text

class WorkflowEngine:
    def __init__(self, engine):
        """Initialize with SQLAlchemy engine (not a raw connection)."""
        self.engine = engine

    def _get_conn(self):
        """Get a connection from the engine."""
        return self.engine.connect()

    # ================================
    # Workflow Selection
    # ================================
    def get_applicable_workflow(self, entity_type, entity_data=None):
        """Find the most suitable active workflow for the given entity type."""
        entity_data = entity_data or {}

        try:
            with self._get_conn() as conn:
                # 1. Check explicit mappings (highest priority)
                mapping = conn.execute(
                    text("""
                        SELECT TOP 1 wd.* 
                        FROM t_EntityWorkflowMapping ewm
                        JOIN t_WorkflowDefinitions wd ON ewm.WorkflowID = wd.WorkflowID
                        WHERE ewm.EntityType = :entity_type AND wd.IsActive = 1
                        ORDER BY ewm.Priority DESC, ewm.IsDefault DESC
                    """),
                    {'entity_type': entity_type}
                ).first()

                if mapping:
                    return dict(mapping._mapping)

                # 2. Fallback to default workflow for entity type
                default = conn.execute(
                    text("""
                        SELECT TOP 1 * FROM t_WorkflowDefinitions 
                        WHERE EntityType = :entity_type AND IsActive = 1 AND IsDefault = 1
                    """),
                    {'entity_type': entity_type}
                ).first()

                if default:
                    return dict(default._mapping)

                # 3. Any active workflow for this entity type
                any_wf = conn.execute(
                    text("""
                        SELECT TOP 1 * FROM t_WorkflowDefinitions 
                        WHERE EntityType = :entity_type AND IsActive = 1
                        ORDER BY WorkflowID
                    """),
                    {'entity_type': entity_type}
                ).first()

                return dict(any_wf._mapping) if any_wf else None

        except Exception as e:
            logging.error(f"Error finding applicable workflow for {entity_type}: {e}")
            return None

    # ================================
    # Condition Evaluation
    # ================================
    def _evaluate_conditions(self, conditions, entity_data):
        """Evaluate JSON-defined conditions against entity data."""
        try:
            for cond in conditions:
                field = cond.get('field')
                op = cond.get('operator')
                value = cond.get('value')

                if field not in entity_data:
                    return False

                entity_val = entity_data[field]

                # Numeric comparison
                if isinstance(value, (int, float)) and isinstance(entity_val, (int, float)):
                    if op == '=' and entity_val != value: return False
                    if op == '>' and not (entity_val > value): return False
                    if op == '<' and not (entity_val < value): return False
                    if op == '>=' and not (entity_val >= value): return False
                    if op == '<=' and not (entity_val <= value): return False
                else:
                    ev = str(entity_val).lower()
                    vv = str(value).lower()
                    if op == '=' and ev != vv: return False
                    if op == '!=' and ev == vv: return False
                    if op == 'contains' and vv not in ev: return False
                    if op == 'in' and ev not in [str(v).lower() for v in value]: return False
                    if op == 'regex' and not re.match(vv, ev): return False

            return True
        except Exception as e:
            logging.error(f"Condition evaluation error: {e}")
            return False

    # ================================
    # Instance Creation
    # ================================
    def create_workflow_instance(self, workflow_id, entity_type, entity_id, created_by, entity_data=None):
        """Create a workflow instance."""
        entity_data = entity_data or {}

        try:
            with self._get_conn() as conn:
                # Resolve workflow
                if not workflow_id:
                    workflow = self.get_applicable_workflow(entity_type, entity_data)
                    if not workflow:
                        return None, "No applicable workflow found"
                    workflow_id = workflow['WorkflowID']
                else:
                    workflow = conn.execute(
                        text("SELECT * FROM t_WorkflowDefinitions WHERE WorkflowID = :wfid AND IsActive = 1"),
                        {'wfid': workflow_id}
                    ).first()
                    if not workflow:
                        return None, "Workflow definition not found or inactive"
                    workflow = dict(workflow._mapping)

                # Check conditions if present
                if workflow.get('Conditions'):
                    conditions = json.loads(workflow['Conditions'])
                    if not self._evaluate_conditions(conditions, entity_data):
                        return None, "Conditions not met for this workflow"

                steps_json = workflow['Steps']
                try:
                    steps = json.loads(steps_json)
                except json.JSONDecodeError as e:
                    return None, f"Invalid steps JSON in workflow {workflow_id}: {e}"

                # Create instance - SQL Server uses OUTPUT INSERTED
                result = conn.execute(
                    text("""
                        INSERT INTO t_WorkflowInstances 
                        (WorkflowID, EntityType, EntityID, CurrentStep, Status, CreatedBy, CreatedDate, EntityData, LastUpdated)
                        OUTPUT INSERTED.InstanceID
                        VALUES (:wfid, :entity_type, :entity_id, 0, 'Active', :created_by, GETDATE(), :entity_data, GETDATE())
                    """),
                    {
                        'wfid': workflow_id,
                        'entity_type': entity_type,
                        'entity_id': entity_id,
                        'created_by': created_by,
                        'entity_data': json.dumps(entity_data) if entity_data else None
                    }
                )
                instance_id = result.scalar()

                # Create steps
                for step_def in steps:
                    assigned_to = self._resolve_assignment(
                        step_def.get('assignedTo'),
                        step_def.get('assignedToType', 'ROLE'),
                        entity_data,
                        step_def.get('assignmentRule')
                    ) or created_by  # fallback to creator

                    due_date = None
                    if step_def.get('timeLimitHours'):
                        due_date = (datetime.now() + timedelta(hours=step_def['timeLimitHours'])).isoformat()

                    conn.execute(
                        text("""
                            INSERT INTO t_WorkflowSteps 
                            (InstanceID, StepNumber, StepName, StepType, AssignedTo, AssignedToType,
                             Status, Required, ActionRequired, TimeLimitHours, DueDate, CreatedDate)
                            VALUES (:instance_id, :step_num, :step_name, :step_type, :assigned_to, :assigned_to_type,
                                    'Pending', :required, :action_required, :time_limit, :due_date, GETDATE())
                        """),
                        {
                            'instance_id': instance_id,
                            'step_num': step_def.get('step', 1),
                            'step_name': step_def.get('name', 'Approval Step'),
                            'step_type': step_def.get('type', 'Approval'),
                            'assigned_to': assigned_to,
                            'assigned_to_type': step_def.get('assignedToType', 'ROLE'),
                            'required': step_def.get('required', True),
                            'action_required': step_def.get('actionRequired', 'Approve/Reject'),
                            'time_limit': step_def.get('timeLimitHours'),
                            'due_date': due_date
                        }
                    )

                self._log_history(instance_id, None, 'Created', created_by,
                                  f"Workflow started for {entity_type} #{entity_id}")

                return instance_id, "Workflow instance created successfully"

        except Exception as e:
            logging.error(f"Error creating workflow instance: {e}")
            import traceback
            traceback.print_exc()
            return None, str(e)

    # ================================
    # Assignment Resolution
    # ================================
    def _resolve_assignment(self, ref, assign_type, entity_data, rule=None):
        """Resolve assigned user based on type and optional rule."""
        try:
            with self._get_conn() as conn:
                if assign_type == 'USER':
                    return ref

                if assign_type == 'ROLE':
                    role_code = ref
                    if isinstance(ref, str) and ':' in ref:
                        role_code = ref.split(':', 1)[1]

                    user = conn.execute(
                        text("""
                            SELECT TOP 1 u.UserID 
                            FROM t_Users u
                            JOIN t_UserRoles ur ON u.UserID = ur.UserID
                            JOIN t_Roles r ON ur.RoleID = r.RoleID
                            WHERE r.RoleCode = :role_code AND u.IsActive = 1
                        """),
                        {'role_code': role_code}
                    ).first()
                    return user[0] if user else None

                if assign_type == 'FIELD' and ref in entity_data:
                    return entity_data[ref]

                return None
        except Exception as e:
            logging.error(f"Error resolving assignment: {e}")
            return None

    # ================================
    # Step Processing
    # ================================
    def process_step_action(self, instance_id, step_id, user_id, action, comments=None, data=None):
        try:
            with self._get_conn() as conn:
                step = conn.execute(
                    text("SELECT * FROM t_WorkflowSteps WHERE StepID = :step_id"),
                    {'step_id': step_id}
                ).first()
                instance = conn.execute(
                    text("SELECT * FROM t_WorkflowInstances WHERE InstanceID = :instance_id"),
                    {'instance_id': instance_id}
                ).first()

                if not step or not instance:
                    return False, "Step or instance not found"

                if not self._check_permission(user_id, step, instance):
                    return False, "Permission denied"

                # Update step
                conn.execute(
                    text("""
                        UPDATE t_WorkflowSteps 
                        SET Status = :action, CompletedBy = :user_id, CompletedDate = GETDATE(), 
                            Comments = :comments, ActionTaken = :data
                        WHERE StepID = :step_id
                    """),
                    {
                        'action': action,
                        'user_id': user_id,
                        'comments': comments or '',
                        'data': json.dumps(data) if data else None,
                        'step_id': step_id
                    }
                )

                self._log_history(instance_id, step_id, action, user_id, comments)

                if action == 'Approved':
                    return self._handle_approval(conn, instance, step)
                if action == 'Rejected':
                    return self._handle_rejection(conn, instance, step, user_id, comments)
                if action == 'Returned':
                    return self._handle_return(conn, instance, step, user_id, comments)

                return True, f"Action {action} processed"

        except Exception as e:
            logging.error(f"Error processing step action: {e}")
            return False, str(e)

    def _handle_approval(self, conn, instance, step):
        next_step = conn.execute(
            text("""
                SELECT TOP 1 * FROM t_WorkflowSteps 
                WHERE InstanceID = :instance_id AND StepNumber > :step_num AND Status = 'Pending'
                ORDER BY StepNumber
            """),
            {'instance_id': instance['InstanceID'], 'step_num': step['StepNumber']}
        ).first()

        if next_step:
            conn.execute(
                text("""
                    UPDATE t_WorkflowInstances 
                    SET CurrentStep = :step, LastUpdated = GETDATE() 
                    WHERE InstanceID = :instance_id
                """),
                {'step': next_step['StepNumber'], 'instance_id': instance['InstanceID']}
            )
            self._notify_user(next_step['AssignedTo'], 'workflow_step_assigned', dict(instance))
            return True, "Approved - moved to next step"

        # Final approval
        conn.execute(
            text("""
                UPDATE t_WorkflowInstances 
                SET Status = 'Completed', CurrentStep = NULL, CompletedDate = GETDATE(), LastUpdated = GETDATE()
                WHERE InstanceID = :instance_id
            """),
            {'instance_id': instance['InstanceID']}
        )
        self._update_entity_status(instance['EntityType'], instance['EntityID'], 'Approved')
        self._notify_user(instance['CreatedBy'], 'workflow_completed', dict(instance))
        return True, "Workflow completed"

    def _handle_rejection(self, conn, instance, step, user_id, comments):
        conn.execute(
            text("""
                UPDATE t_WorkflowInstances 
                SET Status = 'Rejected', CompletedDate = GETDATE(), LastUpdated = GETDATE()
                WHERE InstanceID = :instance_id
            """),
            {'instance_id': instance['InstanceID']}
        )
        self._update_entity_status(instance['EntityType'], instance['EntityID'], 'Rejected')
        self._notify_user(instance['CreatedBy'], 'workflow_rejected', {
            **dict(instance),
            'rejected_by': user_id,
            'reason': comments
        })
        return True, "Workflow rejected"

    def _handle_return(self, conn, instance, step, user_id, comments):
        prev = conn.execute(
            text("""
                SELECT TOP 1 * FROM t_WorkflowSteps 
                WHERE InstanceID = :instance_id AND StepNumber < :step_num 
                ORDER BY StepNumber DESC
            """),
            {'instance_id': instance['InstanceID'], 'step_num': step['StepNumber']}
        ).first()

        if not prev:
            return False, "No previous step to return to"

        conn.execute(
            text("""
                UPDATE t_WorkflowSteps 
                SET Status = 'Pending', CompletedBy = NULL, CompletedDate = NULL, Comments = NULL
                WHERE StepID IN (:step_id, :prev_id)
            """),
            {'step_id': step['StepID'], 'prev_id': prev['StepID']}
        )

        conn.execute(
            text("""
                UPDATE t_WorkflowInstances 
                SET CurrentStep = :step, LastUpdated = GETDATE() 
                WHERE InstanceID = :instance_id
            """),
            {'step': prev['StepNumber'], 'instance_id': instance['InstanceID']}
        )

        self._notify_user(prev['AssignedTo'], 'workflow_returned', {
            **dict(instance),
            'returned_by': user_id,
            'reason': comments
        })
        return True, "Returned to previous step"

    # ================================
    # Helpers
    # ================================
    def _check_permission(self, user_id, step, instance):
        try:
            with self._get_conn() as conn:
                if step['AssignedTo'] == user_id:
                    return True
                if step['AssignedToType'] == 'ROLE':
                    has_role = conn.execute(
                        text("""
                            SELECT TOP 1 1 FROM t_UserRoles ur
                            JOIN t_Roles r ON ur.RoleID = r.RoleID
                            WHERE ur.UserID = :user_id AND r.RoleCode = (
                                SELECT RoleCode FROM t_Roles WHERE RoleID = :role_id
                            )
                        """),
                        {'user_id': user_id, 'role_id': step['AssignedTo']}
                    ).first()
                    if has_role:
                        return True
                return False
        except Exception as e:
            logging.error(f"Error checking permission: {e}")
            return False

    def _update_entity_status(self, entity_type, entity_id, status):
        """Centralized entity status update."""
        mapping = {
            'QualityAssignment': ('t_QualityOperationAssignment', 'AssignmentID', 'WorkflowStatus'),
            'PurchaseOrder':     ('t_PurchaseOrder',            'PurchaseOrderID', 'Status'),
            'SalesOrder':        ('t_SalesOrder',               'SalesOrderID',    'Status'),
            'ProductionOrder':   ('t_ProductionOrder',          'ProductionOrderId','Status'),
            'QualityPlan':       ('t_ProjectQualityPlan',       'ProjectQualityPlanID', 'Status'),
        }

        table, id_col, status_col = mapping.get(entity_type, (None, None, None))
        if not table:
            return

        try:
            with self._get_conn() as conn:
                conn.execute(
                    text(f"""
                        UPDATE {table} 
                        SET {status_col} = :status, UpdatedDate = GETDATE()
                        WHERE {id_col} = :entity_id
                    """),
                    {'status': status, 'entity_id': entity_id}
                )
        except Exception as e:
            logging.warning(f"Failed to update {entity_type} status: {e}")

    def _log_history(self, instance_id, step_id, action, user_id, comments=""):
        try:
            with self._get_conn() as conn:
                conn.execute(
                    text("""
                        INSERT INTO t_WorkflowHistory (InstanceID, StepID, Action, PerformedBy, Comments, PerformedAt)
                        VALUES (:instance_id, :step_id, :action, :user_id, :comments, GETDATE())
                    """),
                    {
                        'instance_id': instance_id,
                        'step_id': step_id,
                        'action': action,
                        'user_id': user_id,
                        'comments': comments
                    }
                )
        except Exception as e:
            pass  # History table may not exist in early schema

    def _notify_user(self, user_id, notif_type, data):
        logging.info(f"Notification [{notif_type}] to user {user_id}: {data}")
        # Integrate email/push notification system here later

    # ================================
    # Public Utilities
    # ================================
    def get_workflow_status(self, entity_type, entity_id):
        try:
            with self._get_conn() as conn:
                instance = conn.execute(
                    text("""
                        SELECT TOP 1 wi.*, wd.WorkflowName
                        FROM t_WorkflowInstances wi
                        JOIN t_WorkflowDefinitions wd ON wi.WorkflowID = wd.WorkflowID
                        WHERE wi.EntityType = :entity_type AND wi.EntityID = :entity_id
                        ORDER BY wi.CreatedDate DESC
                    """),
                    {'entity_type': entity_type, 'entity_id': entity_id}
                ).first()

                if not instance:
                    return None

                instance_dict = dict(instance._mapping)

                steps = conn.execute(
                    text("""
                        SELECT ws.*, u.Username, u.FullName
                        FROM t_WorkflowSteps ws
                        LEFT JOIN t_Users u ON ws.CompletedBy = u.UserID
                        WHERE ws.InstanceID = :instance_id
                        ORDER BY ws.StepNumber
                    """),
                    {'instance_id': instance_dict['InstanceID']}
                ).fetchall()

                history = conn.execute(
                    text("""
                        SELECT TOP 50 wh.*, u.Username, u.FullName
                        FROM t_WorkflowHistory wh
                        LEFT JOIN t_Users u ON wh.PerformedBy = u.UserID
                        WHERE wh.InstanceID = :instance_id
                        ORDER BY wh.PerformedAt DESC
                    """),
                    {'instance_id': instance_dict['InstanceID']}
                ).fetchall()

                return {
                    'instance': instance_dict,
                    'steps': [dict(s._mapping) for s in steps],
                    'history': [dict(h._mapping) for h in history] if history else [],
                    'current_step': self.get_current_step(instance_dict['InstanceID'])
                }
        except Exception as e:
            logging.error(f"Error getting workflow status: {e}")
            return None

    def get_current_step(self, instance_id):
        try:
            with self._get_conn() as conn:
                step = conn.execute(
                    text("""
                        SELECT TOP 1 * FROM t_WorkflowSteps 
                        WHERE InstanceID = :instance_id AND Status = 'Pending'
                        ORDER BY StepNumber
                    """),
                    {'instance_id': instance_id}
                ).first()
                return dict(step._mapping) if step else None
        except Exception as e:
            logging.error(f"Error getting current step: {e}")
            return None

    def cancel_workflow(self, instance_id, user_id, reason=""):
        try:
            with self._get_conn() as conn:
                instance = conn.execute(
                    text("SELECT * FROM t_WorkflowInstances WHERE InstanceID = :instance_id"),
                    {'instance_id': instance_id}
                ).first()
                if not instance:
                    return False, "Instance not found"

                conn.execute(
                    text("""
                        UPDATE t_WorkflowInstances 
                        SET Status = 'Cancelled', LastUpdated = GETDATE()
                        WHERE InstanceID = :instance_id
                    """),
                    {'instance_id': instance_id}
                )

                self._update_entity_status(instance['EntityType'], instance['EntityID'], 'Cancelled')
                self._log_history(instance_id, None, 'Cancelled', user_id, reason)
                return True, "Workflow cancelled"
        except Exception as e:
            logging.error(f"Error cancelling workflow: {e}")
            return False, str(e)