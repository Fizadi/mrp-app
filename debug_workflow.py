# debug_workflow.py
from flask import current_app
from sqlalchemy.sql import text
import json

def debug_workflow():
    """Debug script to check workflow data."""
    
    # Import app context
    from app import create_app
    app = create_app()
    
    with app.app_context():
        engine = current_app.extensions['sqlalchemy'].engine
        
        print("=" * 60)
        print("DEBUGGING WORKFLOW DATA")
        print("=" * 60)
        
        with engine.connect() as conn:
            # 1. Check the order
            print("\n1. Checking order PO-24004:")
            order = conn.execute(
                text("SELECT ProductionOrderId, WorkflowID, WorkflowStatus FROM t_ProductionOrder WHERE ProductionOrderId = 'PO-24004'")
            ).first()
            if order:
                print(f"   Order: {order[0]}, WorkflowID: {order[1]}, WorkflowStatus: {order[2]}")
            else:
                print("   Order not found!")
                return
            
            workflow_id = order[1]
            print(f"\n2. Looking for workflow with ID: {workflow_id}")
            
            # 2. Check the workflow table
            workflow = conn.execute(
                text("""
                    SELECT WorkflowID, EntityType, EntityID, Status, CurrentStep, WorkflowTemplateID
                    FROM t_Workflow
                    WHERE WorkflowID = :wfid
                """),
                {'wfid': workflow_id}
            ).first()
            
            if workflow:
                print(f"   Workflow found:")
                print(f"     WorkflowID: {workflow[0]}")
                print(f"     EntityType: {workflow[1]}")
                print(f"     EntityID: {workflow[2]}")
                print(f"     Status: {workflow[3]}")
                print(f"     CurrentStep: {workflow[4]}")
                print(f"     WorkflowTemplateID: {workflow[5]}")
            else:
                print(f"   ❌ Workflow with ID {workflow_id} NOT found in t_Workflow!")
                print("   This is the problem! The order has WorkflowID but it doesn't exist in t_Workflow.")
                return
            
            # 3. Check the workflow definition
            print(f"\n3. Checking workflow definition:")
            wf_def = conn.execute(
                text("""
                    SELECT WorkflowID, WorkflowCode, WorkflowName, Steps
                    FROM t_WorkflowDefinitions
                    WHERE WorkflowID = :wfid
                """),
                {'wfid': workflow[5]}
            ).first()
            
            if wf_def:
                print(f"   Workflow Definition found:")
                print(f"     WorkflowID: {wf_def[0]}")
                print(f"     WorkflowCode: {wf_def[1]}")
                print(f"     WorkflowName: {wf_def[2]}")
                if wf_def[3]:
                    try:
                        steps = json.loads(wf_def[3])
                        print(f"     Steps: {len(steps)} steps")
                        for i, step in enumerate(steps):
                            print(f"       Step {i+1}: {step.get('name', 'Unknown')}")
                    except:
                        print(f"     Steps: {wf_def[3][:100]}...")
            else:
                print(f"   ❌ Workflow Definition not found!")
                return
            
            # 4. Test the query used in the API
            print(f"\n4. Testing API query for order PO-24004:")
            result = conn.execute(
                text("""
                    SELECT TOP 1
                        w.WorkflowID,
                        w.Status,
                        w.CurrentStep,
                        wf.WorkflowName,
                        wf.Steps
                    FROM t_Workflow w
                    JOIN t_WorkflowDefinitions wf ON w.WorkflowTemplateID = wf.WorkflowID
                    WHERE w.EntityType = 'ProductionOrder' 
                      AND w.EntityID = :oid
                    ORDER BY w.WorkflowID DESC
                """),
                {'oid': 'PO-24004'}
            ).first()
            
            if result:
                print(f"   ✅ API query found workflow: {result[0]}")
            else:
                print(f"   ❌ API query returned NO results!")
                print("   The issue is with the JOIN condition or the EntityID comparison.")
                
                # Try with just the workflow ID
                result2 = conn.execute(
                    text("""
                        SELECT TOP 1
                            w.WorkflowID,
                            w.Status,
                            w.CurrentStep,
                            wf.WorkflowName,
                            wf.Steps
                        FROM t_Workflow w
                        JOIN t_WorkflowDefinitions wf ON w.WorkflowTemplateID = wf.WorkflowID
                        WHERE w.WorkflowID = :wfid
                    """),
                    {'wfid': workflow_id}
                ).first()
                
                if result2:
                    print(f"   ✅ Query by WorkflowID works: {result2[0]}")
                    print("   The problem is the EntityType/EntityID comparison in the JOIN!")
                
            print("\n" + "=" * 60)
            print("DEBUG COMPLETE")
            print("=" * 60)

if __name__ == "__main__":
    debug_workflow()