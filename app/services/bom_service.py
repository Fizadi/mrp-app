"""
BOM Service - Handles BOM explosion and child order creation
"""
from flask import current_app
from sqlalchemy.sql import text
from datetime import datetime


def get_engine():
    return current_app.extensions['sqlalchemy'].engine

def date_to_key(date_str=None):
    if date_str:
        return int(date_str.replace('-', ''))
    return int(datetime.now().strftime('%Y%m%d'))

def explode_bom(parent_product_id, quantity=1, bom_version_id=None, visited=None, level=0):
    """
    Recursively explode BOM for a product.
    Returns list of components with sourcing types.
    """
    if visited is None:
        visited = set()
    
    # Prevent circular references
    if parent_product_id in visited:
        return []
    visited.add(parent_product_id)
    
    engine = get_engine()
    components = []
    today_key = date_to_key()
    
    try:
        with engine.connect() as conn:
            # Get BOM for this product
            sql = """
                SELECT 
                    b.ComponentProductID,
                    b.Quantity,
                    b.BOMLevel,
                    COALESCE(b.SourcingType, 'Make') as SourcingType,
                    b.BOMLineID,
                    p.descEnglish as ComponentName,
                    COALESCE(p.IsManufactured, 0) as IsManufactured
                FROM t_BOM b
                JOIN t_Product p ON b.ComponentProductID = p.ProductId
                WHERE b.ParentProductID = :product_id
                AND b.IsActive = 1
                AND (b.EffectiveDateKey IS NULL OR b.EffectiveDateKey <= :today)
                AND (b.ObsoleteDateKey IS NULL OR b.ObsoleteDateKey > :today)
                ORDER BY b.BOMLevel, b.OperationSequence
            """
            if bom_version_id:
                sql += " AND b.BOMVersionID = :bom_version_id"
            
            rows = conn.execute(text(sql), {
                'product_id': parent_product_id,
                'today': today_key,
                'bom_version_id': bom_version_id
            }).fetchall()
            
            for row in rows:
                component = {
                    'product_id': row[0],
                    'quantity': row[1] * quantity,
                    'bom_level': row[2] or 0,
                    'sourcing_type': row[3] or 'Make',
                    'bom_line_id': row[4],
                    'component_name': row[5] or row[0],
                    'is_manufactured': row[6] or False,
                    'children': [],
                    'level': level + 1
                }
                
                # Recursively explode if component has its own BOM and is 'Make' or 'Phantom'
                if component['sourcing_type'] in ['Make', 'Phantom']:
                    # Check if this component has a BOM
                    has_bom = conn.execute(
                        text("SELECT COUNT(*) FROM t_BOM WHERE ParentProductID = :pid AND IsActive = 1"),
                        {'pid': row[0]}
                    ).scalar() > 0
                    
                    if has_bom:
                        child_components = explode_bom(row[0], row[1] * quantity, bom_version_id, visited.copy(), level + 1)
                        if child_components:
                            component['children'] = child_components
                
                components.append(component)
    except Exception as e:
        print(f"❌ Error in explode_bom: {str(e)}")
        import traceback
        traceback.print_exc()
    
    return components

def create_child_orders(parent_order_id, components, parent_data, conn):
    """
    Create child orders based on sourcing type.
    Returns list of created child orders.
    """
    child_orders = []
    today_key = date_to_key()
    industry = parent_data.get('industry', 'valve')
    created_by = parent_data.get('created_by', 1)
    
    try:
        for comp in components:
            sourcing_type = comp['sourcing_type']
            product_id = comp['product_id']
            quantity = comp['quantity']
            
            if sourcing_type == 'Make':
                # Create child Manufacturing Order
                child_order_id = _create_manufacturing_order(
                    conn, 
                    product_id, 
                    quantity,
                    parent_order_id,
                    parent_data
                )
                child_orders.append({
                    'product_id': product_id,
                    'order_id': child_order_id,
                    'type': 'Manufacturing',
                    'sourcing_type': 'Make',
                    'parent_order_id': parent_order_id
                })
                
                # Recursively create for subcomponents
                if comp.get('children'):
                    sub_children = create_child_orders(
                        child_order_id, 
                        comp['children'], 
                        parent_data,
                        conn
                    )
                    child_orders.extend(sub_children)
                    
            elif sourcing_type == 'Buy':
                # Create Purchase Requisition reference
                po_id = f"PR-{parent_order_id}-{product_id}"
                child_orders.append({
                    'product_id': product_id,
                    'order_id': po_id,
                    'type': 'Purchase',
                    'sourcing_type': 'Buy',
                    'parent_order_id': parent_order_id
                })
                
            elif sourcing_type == 'Outsource':
                # Create Subcontract Order reference
                subcon_id = f"SO-{parent_order_id}-{product_id}"
                child_orders.append({
                    'product_id': product_id,
                    'order_id': subcon_id,
                    'type': 'Subcontract',
                    'sourcing_type': 'Outsource',
                    'parent_order_id': parent_order_id
                })
                
            elif sourcing_type == 'Phantom':
                # Phantom: Skip, use children directly in parent
                if comp.get('children'):
                    sub_children = create_child_orders(
                        parent_order_id, 
                        comp['children'], 
                        parent_data,
                        conn
                    )
                    child_orders.extend(sub_children)
    except Exception as e:
        print(f"❌ Error in create_child_orders: {str(e)}")
        import traceback
        traceback.print_exc()
    
    return child_orders

def _create_manufacturing_order(conn, product_id, quantity, parent_order_id, parent_data):
    """Create a child manufacturing order."""
    try:
        # Generate order ID
        res = conn.execute(text("SELECT MAX(CAST(SUBSTR(ProductionOrderId, 4) AS INTEGER)) FROM t_ProductionOrder"))
        max_num = res.scalar() or 0
        next_num = max_num + 1
        order_id = f"PO-{next_num:05d}"
        
        today_key = date_to_key()
        industry = parent_data.get('industry', 'valve')
        created_by = parent_data.get('created_by', 1)
        
        # Get product details for costing
        product = conn.execute(
            text("SELECT StandardCost, StandardLaborHours, descEnglish FROM t_Product WHERE ProductId = :pid"),
            {'pid': product_id}
        ).first()
        
        est_mat_cost = (product[0] or 0) * quantity if product else 0
        est_lab_cost = (product[1] or 0) * quantity if product else 0
        est_total = est_mat_cost + est_lab_cost
        
        conn.execute(
            text("""
                INSERT INTO t_ProductionOrder
                (ProductionOrderId, ProductId, OrderType, OrderQuantity, Status,
                 StartDateKey, Priority, CreatedBy, OrderSource, 
                 DemoIndustryCode, ParentOrderID, OrderCategory, 
                 ComponentProductID, SourcingType, WorkflowStatus,
                 EstimatedMaterialCost, EstimatedLaborCost, EstimatedTotalCost)
                VALUES (:oid, :pid, 'Manufacturing', :qty, 'Draft',
                        :start, 2, :user, 'BOM Explosion',
                        :industry, :parent_id, 'Component',
                        :pid, 'Make', 'NotStarted',
                        :mat_cost, :lab_cost, :total)
            """),
            {
                'oid': order_id,
                'pid': product_id,
                'qty': quantity,
                'start': today_key,
                'user': created_by,
                'industry': industry,
                'parent_id': parent_order_id,
                'mat_cost': est_mat_cost,
                'lab_cost': est_lab_cost,
                'total': est_total
            }
        )
        
        # Insert detail row
        conn.execute(
            text("""
                INSERT INTO t_ProductionOrderDetail
                (ProductionOrderId, Status, BatchId)
                VALUES (:oid, 'Planned', :batch)
            """),
            {'oid': order_id, 'batch': f"BATCH-{order_id}"}
        )
        
        return order_id
    except Exception as e:
        print(f"❌ Error creating manufacturing order: {str(e)}")
        import traceback
        traceback.print_exc()
        return None


def get_order_hierarchy(order_id, level=0):
    """
    Get the full order hierarchy (parent and all children).
    Returns a tree structure of parent-child relationships.
    """
    from datetime import datetime
    from flask import session
    
    engine = get_engine()
    result = {
        'order_id': order_id,
        'level': level,
        'children': []
    }
    
    lang = session.get('lang', 'en')
    today_key = int(datetime.now().strftime('%Y%m%d'))
    
    print(f"🌍 Language: {lang}, Order: {order_id}")
    
    try:
        with engine.connect() as conn:
            # SIMPLE QUERY - Get parent order
            order = conn.execute(
                text("""
                    SELECT 
                        po.ProductionOrderId,
                        po.ProductId,
                        po.OrderQuantity,
                        po.Status,
                        po.OrderCategory,
                        po.SourcingType,
                        po.WorkflowStatus
                    FROM t_ProductionOrder po
                    WHERE po.ProductionOrderId = :order_id
                """),
                {'order_id': order_id}
            ).first()
            
            if not order:
                print(f"❌ Order not found: {order_id}")
                return result
            
            # Get product name separately
            product_name = order[1]  # ProductId as fallback
            if order[1]:
                prod = conn.execute(
                    text("""
                        SELECT descEnglish, descFarsi
                        FROM t_Product 
                        WHERE ProductId = :pid
                    """),
                    {'pid': order[1]}
                ).first()
                if prod:
                    if lang != 'en' and prod[1] and str(prod[1]).strip():
                        product_name = prod[1]
                    else:
                        product_name = prod[0] or order[1]
            
            result['product_id'] = order[1]
            result['product_name'] = product_name
            result['quantity'] = order[2] or 1
            result['status'] = order[3]
            result['category'] = order[4] or 'Assembly'
            result['sourcing_type'] = order[5] or 'Make'
            result['workflow_status'] = order[6]
            
            print(f"📦 Parent: {order[1]} -> {product_name}")
            
            # Get child orders
            children = conn.execute(
                text("""
                    SELECT 
                        ProductionOrderId,
                        ProductId,
                        OrderQuantity,
                        Status,
                        OrderCategory,
                        SourcingType,
                        WorkflowStatus
                    FROM t_ProductionOrder
                    WHERE ParentOrderID = :order_id
                    ORDER BY OrderCategory, ProductId
                """),
                {'order_id': order_id}
            ).fetchall()
            
            print(f"📋 Found {len(children)} child orders")
            
            # Process each child
            for child in children:
                # Get product name for child
                child_name = child[1]
                if child[1]:
                    prod = conn.execute(
                        text("""
                            SELECT descEnglish, descFarsi
                            FROM t_Product 
                            WHERE ProductId = :pid
                        """),
                        {'pid': child[1]}
                    ).first()
                    if prod:
                        if lang != 'en' and prod[1] and str(prod[1]).strip():
                            child_name = prod[1]
                        else:
                            child_name = prod[0] or child[1]
                
                child_data = {
                    'order_id': child[0],
                    'product_id': child[1],
                    'product_name': child_name,
                    'quantity': child[2] or 1,
                    'status': child[3],
                    'category': child[4] or 'Component',
                    'sourcing_type': child[5] or 'Make',
                    'workflow_status': child[6],
                    'level': level + 1,
                    'children': []
                }
                
                # Recursively get grandchildren
                grand_children = get_order_hierarchy(child[0], level + 1)
                if grand_children and grand_children.get('children'):
                    child_data['children'] = grand_children['children']
                
                result['children'].append(child_data)
            
            # Get BOM components (planned items)
            if order[1]:  # If parent has a product
                bom_components = conn.execute(
                    text("""
                        SELECT 
                            b.ComponentProductID,
                            b.Quantity,
                            b.SourcingType
                        FROM t_BOM b
                        WHERE b.ParentProductID = :pid
                        AND b.IsActive = 1
                        AND (b.EffectiveDateKey IS NULL OR b.EffectiveDateKey <= :today)
                        ORDER BY b.BOMLevel
                    """),
                    {'pid': order[1], 'today': today_key}
                ).fetchall()
                
                # Get existing child order product IDs
                existing_children = [c[1] for c in children] if children else []
                
                for bom_comp in bom_components:
                    # Check if this component already has an order
                    if bom_comp[0] not in existing_children:
                        # Get product name for BOM component
                        comp_name = bom_comp[0]
                        prod = conn.execute(
                            text("""
                                SELECT descEnglish, descFarsi
                                FROM t_Product 
                                WHERE ProductId = :pid
                            """),
                            {'pid': bom_comp[0]}
                        ).first()
                        if prod:
                            if lang != 'en' and prod[1] and str(prod[1]).strip():
                                comp_name = prod[1]
                            else:
                                comp_name = prod[0] or bom_comp[0]
                        
                        result['children'].append({
                            'product_id': bom_comp[0],
                            'product_name': comp_name,
                            'quantity': bom_comp[1],
                            'sourcing_type': bom_comp[2] or 'Make',
                            'category': 'Planned',
                            'status': 'Planned',
                            'is_planned': True,
                            'children': []
                        })
            
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return result
    
    return result