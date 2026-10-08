# app/services/workflow_service.py
import json
import logging
from flask import current_app
from sqlalchemy.sql import text

class WorkflowService:
    """Workflow service for managing workflow instances."""
    
    @staticmethod
    def get_engine():
        """Get SQLAlchemy engine from current app."""
        return current_app.extensions['sqlalchemy'].engine
    
    @staticmethod
    def trigger_workflow(data):
        """Trigger a workflow for an entity."""
        try:
            entity_type = data.get('entity_type')
            entity_id = data.get('entity_id')
            scenario = data.get('scenario', 'valve')
            trigger_event = data.get('trigger_event', 'Draft')
            created_by = data.get('created_by', 1)
            
            if not entity_type or not entity_id:
                return {'success': False, 'error': 'Entity type and ID required'}
            
            engine = WorkflowService.get_engine()
            with engine.connect() as conn:
                # Find the workflow definition for this scenario
                wf_def = conn.execute(
                    text("""
                        SELECT TOP 1 WorkflowID, Steps, WorkflowCode, WorkflowName
                        FROM t_WorkflowDefinitions
                        WHERE WorkflowCode = :scenario || '_JOB_ORDER'
                          AND Status = 'Active'
                    """),
                    {'scenario': scenario.upper()}
                ).first()
                
                if not wf_def:
                    # Try alternate matching
                    wf_def = conn.execute(
                        text("""
                            SELECT TOP 1 WorkflowID, Steps, WorkflowCode, WorkflowName
                            FROM t_WorkflowDefinitions
                            WHERE WorkflowCode LIKE :pattern
                              AND Status = 'Active'
                        """),
                        {'pattern': f'%{scenario.upper()}%'}
                    ).first()
                
                if not wf_def:
                    # Fallback to any active workflow for ProductionOrder
                    wf_def = conn.execute(
                        text("""
                            SELECT TOP 1 WorkflowID, Steps, WorkflowCode, WorkflowName
                            FROM t_WorkflowDefinitions
                            WHERE EntityType = 'ProductionOrder'
                              AND Status = 'Active'
                        """),
                        {}
                    ).first()
                
                if not wf_def:
                    return {'success': False, 'error': 'No workflow definition found'}
                
                # Parse steps
                steps = []
                try:
                    steps = json.loads(wf_def[1]) if wf_def[1] else []
                except:
                    steps = []
                
                # Create workflow instance - SQL Server syntax
                result = conn.execute(
                    text("""
                        INSERT INTO t_Workflow (EntityType, EntityID, WorkflowTemplateID, Status, CurrentStep, CreatedDateKey, CreatedBy)
                        OUTPUT INSERTED.WorkflowID
                        VALUES (:entity_type, :entity_id, :wf_id, 'Pending', 1, CAST(FORMAT(GETDATE(), 'yyyyMMdd') AS INT), :created_by)
                    """),
                    {
                        'entity_type': entity_type,
                        'entity_id': entity_id,
                        'wf_id': wf_def[0],
                        'created_by': created_by
                    }
                )
                workflow_id = result.scalar()
                
                # Update the production order with workflow ID
                if entity_type == 'ProductionOrder':
                    conn.execute(
                        text("""
                            UPDATE t_ProductionOrder
                            SET WorkflowID = :wf_id, WorkflowStatus = 'Pending'
                            WHERE ProductionOrderId = :entity_id
                        """),
                        {'wf_id': workflow_id, 'entity_id': entity_id}
                    )
                
                return {
                    'success': True,
                    'workflow_id': workflow_id,
                    'message': 'Workflow triggered successfully',
                    'steps': steps
                }
                
        except Exception as e:
            logging.error(f"Error triggering workflow: {e}")
            import traceback
            traceback.print_exc()
            return {'success': False, 'error': str(e)}
    
    @staticmethod
    def get_workflow_status(entity_type, entity_id):
        """Get workflow status for an entity."""
        try:
            engine = WorkflowService.get_engine()
            with engine.connect() as conn:
                workflow = conn.execute(
                    text("""
                        SELECT TOP 1
                            w.WorkflowID,
                            w.Status,
                            w.CurrentStep,
                            wf.WorkflowName,
                            wf.Steps,
                            wf.WorkflowCode
                        FROM t_Workflow w
                        JOIN t_WorkflowDefinitions wf ON w.WorkflowTemplateID = wf.WorkflowID
                        WHERE w.EntityType = :entity_type 
                          AND (w.EntityID = :entity_id OR w.EntityID = :entity_id_int)
                        ORDER BY w.WorkflowID DESC
                    """),
                    {
                        'entity_type': entity_type,
                        'entity_id': entity_id,
                        'entity_id_int': int(entity_id.replace('PO-', '')) if isinstance(entity_id, str) and entity_id.startswith('PO-') else entity_id
                    }
                ).first()
                
                if not workflow:
                    return None
                
                steps = json.loads(workflow[4]) if workflow[4] else []
                current_step = workflow[2] or 0
                
                return {
                    'workflow_id': workflow[0],
                    'status': workflow[1] or 'Pending',
                    'current_step': current_step,
                    'workflow_name': workflow[3],
                    'steps': steps,
                    'workflow_code': workflow[5],
                    'is_complete': workflow[1] == 'Completed'
                }
        except Exception as e:
            logging.error(f"Error getting workflow status: {e}")
            return None
    
    @staticmethod
    def check_workflow_complete(entity_type, entity_id):
        """Check if a workflow is complete."""
        try:
            engine = WorkflowService.get_engine()
            with engine.connect() as conn:
                result = conn.execute(
                    text("""
                        SELECT TOP 1 Status
                        FROM t_Workflow
                        WHERE EntityType = :entity_type 
                          AND (EntityID = :entity_id OR EntityID = :entity_id_int)
                        ORDER BY WorkflowID DESC
                    """),
                    {
                        'entity_type': entity_type,
                        'entity_id': entity_id,
                        'entity_id_int': int(entity_id.replace('PO-', '')) if isinstance(entity_id, str) and entity_id.startswith('PO-') else entity_id
                    }
                ).first()
                
                if not result:
                    return {'complete': True, 'hasWorkflow': False}
                
                return {
                    'complete': result[0] == 'Completed',
                    'hasWorkflow': True,
                    'status': result[0]
                }
        except Exception as e:
            logging.error(f"Error checking workflow complete: {e}")
            return {'complete': True, 'hasWorkflow': False}
    
    @staticmethod
    def approve_step(workflow_id, user_id, comment, entity_type=None, entity_id=None):
        """Approve a workflow step."""
        try:
            engine = WorkflowService.get_engine()
            with engine.begin() as conn:
                # Get current step
                workflow = conn.execute(
                    text("""
                        SELECT CurrentStep, Status, WorkflowTemplateID
                        FROM t_Workflow
                        WHERE WorkflowID = :wf_id
                    """),
                    {'wf_id': workflow_id}
                ).first()
                
                if not workflow:
                    return {'success': False, 'error': 'Workflow not found'}
                
                if workflow[1] == 'Completed':
                    return {'success': False, 'error': 'Workflow already completed'}
                
                current_step = workflow[0] or 1
                next_step = current_step + 1
                
                # Get workflow definition to check max steps
                wf_def = conn.execute(
                    text("""
                        SELECT Steps
                        FROM t_WorkflowDefinitions
                        WHERE WorkflowID = :wf_id
                    """),
                    {'wf_id': workflow[2]}
                ).first()
                
                max_steps = 0
                if wf_def and wf_def[0]:
                    try:
                        steps = json.loads(wf_def[0])
                        max_steps = len(steps)
                    except:
                        max_steps = 4
                
                # Update workflow
                if next_step > max_steps:
                    # Workflow complete
                    conn.execute(
                        text("""
                            UPDATE t_Workflow
                            SET Status = 'Completed', CurrentStep = :step, CompletedDateKey = CAST(FORMAT(GETDATE(), 'yyyyMMdd') AS INT)
                            WHERE WorkflowID = :wf_id
                        """),
                        {'step': current_step, 'wf_id': workflow_id}
                    )
                    
                    # Update entity status
                    if entity_type == 'ProductionOrder' and entity_id:
                        conn.execute(
                            text("""
                                UPDATE t_ProductionOrder
                                SET WorkflowStatus = 'Completed'
                                WHERE ProductionOrderId = :entity_id
                            """),
                            {'entity_id': entity_id}
                        )
                else:
                    # Move to next step
                    conn.execute(
                        text("""
                            UPDATE t_Workflow
                            SET CurrentStep = :step
                            WHERE WorkflowID = :wf_id
                        """),
                        {'step': next_step, 'wf_id': workflow_id}
                    )
                
                return {'success': True, 'message': 'Step approved', 'next_step': next_step}
                
        except Exception as e:
            logging.error(f"Error approving step: {e}")
            return {'success': False, 'error': str(e)}
    
    @staticmethod
    def reject_step(workflow_id, user_id, comment, entity_type=None, entity_id=None):
        """Reject a workflow step."""
        try:
            engine = WorkflowService.get_engine()
            with engine.begin() as conn:
                conn.execute(
                    text("""
                        UPDATE t_Workflow
                        SET Status = 'Rejected'
                        WHERE WorkflowID = :wf_id
                    """),
                    {'wf_id': workflow_id}
                )
                
                if entity_type == 'ProductionOrder' and entity_id:
                    conn.execute(
                        text("""
                            UPDATE t_ProductionOrder
                            SET WorkflowStatus = 'Rejected'
                            WHERE ProductionOrderId = :entity_id
                        """),
                        {'entity_id': entity_id}
                    )
                
                return {'success': True, 'message': 'Step rejected'}
                
        except Exception as e:
            logging.error(f"Error rejecting step: {e}")
            return {'success': False, 'error': str(e)}