from flask import request, jsonify, current_app, Response
from flask_login import login_required, current_user
from sqlalchemy.sql import text
from datetime import datetime
import json
import csv
import io
from functools import wraps
from . import api_bp
from app.services.mrp_service import MRPEngine
from flask import session
from app.core.utils import format_date   # ensure this import is present

def get_engine():
    return current_app.extensions['sqlalchemy'].engine

def date_to_key(date_str):
    """Convert YYYY-MM-DD to integer YYYYMMDD."""
    if not date_str:
        return None
    return int(date_str.replace('-', ''))


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({'error': 'Authentication required'}), 401
        # Query the database for user roles
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


# ----------------------------------------------------------------------
# KPI cards
# ----------------------------------------------------------------------
@api_bp.route('/production-orders/kpi')
@login_required
def kpi_counts():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT Status, COUNT(*) as cnt
            FROM t_ProductionOrder
            GROUP BY Status
        """)).fetchall()
    counts = {row[0]: row[1] for row in rows}
    return jsonify({
        'Draft': counts.get('Draft', 0),
        'Released': counts.get('Released', 0),
        'InProgress': counts.get('InProgress', 0),
        'Completed': counts.get('Completed', 0),
        'Closed': counts.get('Closed', 0)
    })

# ----------------------------------------------------------------------
# List orders with filters
# ----------------------------------------------------------------------



@api_bp.route('/production-orders/list')
@login_required
def list_orders():
    status = request.args.get('status')
    product = request.args.get('product')
    project = request.args.get('project')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')

    engine = get_engine()
    params = {}
    conditions = []
    if status:
        conditions.append("po.Status = :status")
        params['status'] = status
    if product:
        conditions.append("po.ProductId = :product")
        params['product'] = product
    if project:
        conditions.append("po.ProjectID = :project")
        params['project'] = project
    if date_from:
        conditions.append("po.EndDateKey >= :from_key")
        params['from_key'] = date_to_key(date_from)
    if date_to:
        conditions.append("po.EndDateKey <= :to_key")
        params['to_key'] = date_to_key(date_to)

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    sql = f"""
        SELECT po.ProductionOrderId, po.ProductId, p.descEnglish as ProductName,
               po.OrderType, po.OrderQuantity, po.Status, po.EndDateKey,
               po.ProjectID, pr.ProjectName
        FROM t_ProductionOrder po
        LEFT JOIN t_Product p ON po.ProductId = p.ProductId
        LEFT JOIN t_Project pr ON po.ProjectID = pr.ProjectID
        WHERE {where_clause}
        ORDER BY po.StartDateKey DESC
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()

    # Determine language and date format
    lang = session.get('lang', 'en')
    date_format = 'persian' if lang == 'fa' else ('hijri' if lang == 'ar' else 'gregorian')

    orders = []
    for r in rows:
        due_date_key = r[6]
        formatted_due_date = format_date(due_date_key, date_format, lang) if due_date_key else ''
        orders.append({
            'id': r[0],
            'productId': r[1],
            'productName': r[2],
            'orderType': r[3],
            'quantity': r[4],
            'status': r[5],
            'dueDateKey': due_date_key,
            'dueDateFormatted': formatted_due_date,   # <-- new field
            'projectId': r[7],
            'projectName': r[8],
            'ecoWarning': False,
            'engVersion': 'N/A',
            'completedQuantity': 0
        })
    return jsonify(orders)


# ----------------------------------------------------------------------
# Create order (auto BOM explode)
# ----------------------------------------------------------------------
@api_bp.route('/production-orders/create', methods=['POST'])
@login_required
@admin_required
def create_order():
    data = request.get_json()
    product_id = data.get('productId')
    quantity = data.get('quantity')
    due_date = data.get('dueDate')          # YYYY-MM-DD
    order_type = data.get('orderType', 'Manufacturing')
    project_id = data.get('projectId')
    priority = data.get('priority', 1)

    if not product_id or not quantity:
        return jsonify({'error': 'Product and quantity required'}), 400

    today_key = int(datetime.now().strftime('%Y%m%d'))
    due_key = date_to_key(due_date) if due_date else None

    engine = get_engine()
    with engine.begin() as conn:
        # Generate next order ID (simple increment)
        res = conn.execute(text("SELECT MAX(CAST(SUBSTR(ProductionOrderId, 4) AS INTEGER)) FROM t_ProductionOrder"))
        max_num = res.scalar() or 0
        next_num = max_num + 1
        order_id = f"PO-{next_num:05d}"

        # Insert main order
        conn.execute(
            text("""
                INSERT INTO t_ProductionOrder
                (ProductionOrderId, ProductId, OrderType, OrderQuantity, Status,
                 StartDateKey, EndDateKey, Priority, ProjectID, CreatedBy, OrderSource)
                VALUES (:oid, :pid, :otype, :qty, 'Draft',
                        :today, :due, :prio, :proj, :user, 'Manual')
            """),
            {'oid': order_id, 'pid': product_id, 'otype': order_type, 'qty': quantity,
             'today': today_key, 'due': due_key, 'prio': priority, 'proj': project_id,
             'user': current_user.UserID}
        )

        # Insert placeholder detail row (ActualQuantity will be updated later)
        conn.execute(
            text("""
                INSERT INTO t_ProductionOrderDetail
                (ProductionOrderId, Status, BatchId)
                VALUES (:oid, 'Planned', :batch)
            """),
            {'oid': order_id, 'batch': f"BATCH-{order_id}"}
        )

        # Explode BOM (effective today)
        bom_sql = """
            SELECT ComponentProductID, Quantity, ScrapFactor
            FROM t_BOM
            WHERE ParentProductID = :pid
              AND (EffectivityDateKey IS NULL OR EffectivityDateKey <= :today)
              AND (ObsoleteDateKey IS NULL OR ObsoleteDateKey > :today)
        """
        components = conn.execute(text(bom_sql), {'pid': product_id, 'today': today_key}).fetchall()
        for comp in components:
            comp_id = comp[0]
            req_qty = comp[1] * quantity * (1 + (comp[2] or 0) / 100.0)  # include scrap
            conn.execute(
                text("""
                    INSERT INTO t_ProductionOrderComponents
                    (ProductionOrderId, ComponentProductID, RequiredQuantity, IssuedQuantity)
                    VALUES (:oid, :cid, :req, 0)
                """),
                {'oid': order_id, 'cid': comp_id, 'req': req_qty}
            )

    return jsonify({'message': 'Order created', 'orderId': order_id}), 201

# ----------------------------------------------------------------------
# Release order
# ----------------------------------------------------------------------
@api_bp.route('/production-orders/release/<string:order_id>', methods=['POST'])
@login_required
@admin_required
def release_order(order_id):
    today_key = int(datetime.now().strftime('%Y%m%d'))
    engine = get_engine()
    with engine.begin() as conn:
        # Update status and release date
        conn.execute(
            text("""
                UPDATE t_ProductionOrder
                SET Status = 'Released', ReleasedDateKey = :rel
                WHERE ProductionOrderId = :oid
            """),
            {'rel': today_key, 'oid': order_id}
        )
    return jsonify({'message': 'Order released'})

# ----------------------------------------------------------------------
# Issue materials (component consumption)
# ----------------------------------------------------------------------
@api_bp.route('/production-orders/issue-materials', methods=['POST'])
@login_required
def issue_materials():
    data = request.get_json()
    order_id = data['orderId']
    items = data['items']   # list of {componentProductId, issuedQuantity}

    today_key = int(datetime.now().strftime('%Y%m%d'))
    engine = get_engine()
    with engine.begin() as conn:
        for item in items:
            comp_id = item['componentProductId']
            qty = item['issuedQuantity']
            # Update issued quantity in components table
            conn.execute(
                text("""
                    UPDATE t_ProductionOrderComponents
                    SET IssuedQuantity = IssuedQuantity + :qty
                    WHERE ProductionOrderId = :oid AND ComponentProductID = :cid
                """),
                {'qty': qty, 'oid': order_id, 'cid': comp_id}
            )
            # Record inventory transaction
            conn.execute(
                text("""
                    INSERT INTO t_InventoryTransactions
                    (ProductID, TransactionType, Quantity, DateKey, Reference, CreatedBy)
                    VALUES (:pid, 'ISSUE', :qty, :dkey, :ref, :user)
                """),
                {'pid': comp_id, 'qty': -qty, 'dkey': today_key,
                 'ref': f"ProductionOrder_{order_id}", 'user': current_user.UserID}
            )
            # Update on-hand
            conn.execute(
                text("""
                    UPDATE t_InventoryOnHand
                    SET QuantityOnHand = QuantityOnHand - :qty
                    WHERE ProductID = :pid
                """),
                {'qty': qty, 'pid': comp_id}
            )
        # Set order status to InProgress if it was Released
        conn.execute(
            text("""
                UPDATE t_ProductionOrder
                SET Status = 'InProgress'
                WHERE ProductionOrderId = :oid AND Status = 'Released'
            """),
            {'oid': order_id}
        )
    return jsonify({'message': 'Materials issued'})

# ----------------------------------------------------------------------
# Report completion (finished goods)
# ----------------------------------------------------------------------
@api_bp.route('/production-orders/report-completion', methods=['POST'])
@login_required
def report_completion():
    data = request.get_json()
    order_id = data['orderId']
    completed_qty = data['completedQuantity']
    generate_serials = data.get('generateSerials', False)
    serial_prefix = data.get('serialPrefix', '')

    today_key = int(datetime.now().strftime('%Y%m%d'))
    engine = get_engine()
    with engine.begin() as conn:
        # Update detail row with actual quantity
        conn.execute(
            text("""
                UPDATE t_ProductionOrderDetail
                SET ActualQuantity = COALESCE(ActualQuantity, 0) + :cq,
                    ActualEnd = date(:today),
                    Status = 'Completed'
                WHERE ProductionOrderId = :oid
            """),
            {'cq': completed_qty, 'today': f"{today_key[:4]}-{today_key[4:6]}-{today_key[6:]}",
             'oid': order_id}
        )
        # Update order status
        conn.execute(
            text("""
                UPDATE t_ProductionOrder
                SET Status = CASE
                    WHEN COALESCE((SELECT ActualQuantity FROM t_ProductionOrderDetail WHERE ProductionOrderId = :oid), 0) >= OrderQuantity
                    THEN 'Completed'
                    ELSE 'InProgress'
                END,
                ActualEndDateKey = :today
                WHERE ProductionOrderId = :oid
            """),
            {'oid': order_id, 'today': today_key}
        )
        # Inventory receipt
        product_row = conn.execute(
            text("SELECT ProductId FROM t_ProductionOrder WHERE ProductionOrderId = :oid"),
            {'oid': order_id}
        ).first()
        product_id = product_row[0]
        conn.execute(
            text("""
                INSERT INTO t_InventoryTransactions
                (ProductID, TransactionType, Quantity, DateKey, Reference, CreatedBy)
                VALUES (:pid, 'RECEIPT', :qty, :dkey, :ref, :user)
            """),
            {'pid': product_id, 'qty': completed_qty, 'dkey': today_key,
             'ref': f"Completion_{order_id}", 'user': current_user.UserID}
        )
        conn.execute(
            text("""
                UPDATE t_InventoryOnHand
                SET QuantityOnHand = QuantityOnHand + :qty
                WHERE ProductID = :pid
            """),
            {'qty': completed_qty, 'pid': product_id}
        )
        # Generate serial numbers if requested
        if generate_serials and completed_qty > 0:
            # Get last used number for this product/prefix
            res = conn.execute(
                text("""
                    SELECT COALESCE(MAX(CAST(SUBSTR(SerialNumber, LENGTH(:prefix)+1) AS INTEGER)), 0)
                    FROM t_SerialNumbers
                    WHERE ProductID = :pid AND SerialNumber LIKE :pattern
                """),
                {'prefix': serial_prefix, 'pid': product_id, 'pattern': f"{serial_prefix}%"}
            )
            last_num = res.scalar() or 0
            for i in range(1, completed_qty + 1):
                serial_num = f"{serial_prefix}{last_num + i}"
                conn.execute(
                    text("""
                        INSERT INTO t_SerialNumbers
                        (ProductID, SerialNumber, Status, ManufacturedDateKey, ProductionOrderId)
                        VALUES (:pid, :sn, 'Available', :mfg, :oid)
                    """),
                    {'pid': product_id, 'sn': serial_num, 'mfg': today_key, 'oid': order_id}
                )
    return jsonify({'message': 'Completion recorded'})

# ----------------------------------------------------------------------
# Subcontract: Issue to supplier
# ----------------------------------------------------------------------
@api_bp.route('/production-orders/issue-to-supplier', methods=['POST'])
@login_required
def issue_to_supplier():
    data = request.get_json()
    order_id = data['orderId']
    supplier_id = data['supplierId']
    po_number = data.get('poNumber')

    today_key = int(datetime.now().strftime('%Y%m%d'))
    engine = get_engine()
    with engine.begin() as conn:
        # Create purchase order row
        po_res = conn.execute(
            text("""
                INSERT INTO t_PurchaseOrder
                (PONumber, SupplierID, OrderDateKey, Status, TotalAmount)
                VALUES (:pono, :supp, :dkey, 'Open', 0)
                RETURNING PurchaseOrderID
            """),
            {'pono': po_number or f"SUB-{order_id}", 'supp': supplier_id, 'dkey': today_key}
        )
        po_id = po_res.fetchone()[0]
        # Link to production order (you may create t_ProductionOrderPurchaseOrder if exists; if not, skip)
        # I assume you have that table; if not, just update order status and ignore.
        try:
            conn.execute(
                text("""
                    INSERT INTO t_ProductionOrderPurchaseOrder
                    (ProductionOrderID, PurchaseOrderID, Status)
                    VALUES (:oid, :poid, 'Issued')
                """),
                {'oid': order_id, 'poid': po_id}
            )
        except:
            pass  # Table may not exist – no worries
        # Update order status
        conn.execute(
            text("UPDATE t_ProductionOrder SET Status = 'At Supplier' WHERE ProductionOrderId = :oid"),
            {'oid': order_id}
        )
    return jsonify({'message': 'Issued to supplier', 'purchaseOrderId': po_id})

@api_bp.route('/production-orders/receive-from-supplier', methods=['POST'])
@login_required
def receive_from_supplier():
    data = request.get_json()
    order_id = data['orderId']
    po_id = data['purchaseOrderId']
    received_qty = data['receivedQuantity']

    today_key = int(datetime.now().strftime('%Y%m%d'))
    engine = get_engine()
    with engine.begin() as conn:
        # Update PO status
        conn.execute(
            text("UPDATE t_PurchaseOrder SET Status='Completed' WHERE PurchaseOrderID=:poid"),
            {'poid': po_id}
        )
        # Update link table if exists
        try:
            conn.execute(
                text("UPDATE t_ProductionOrderPurchaseOrder SET Status='Received' WHERE ProductionOrderID=:oid AND PurchaseOrderID=:poid"),
                {'oid': order_id, 'poid': po_id}
            )
        except:
            pass
        # Report completion for the received quantity
        # This will trigger finish goods receipt
        product_row = conn.execute(
            text("SELECT ProductId FROM t_ProductionOrder WHERE ProductionOrderId = :oid"),
            {'oid': order_id}
        ).first()
        product_id = product_row[0]
        # Inventory receipt
        conn.execute(
            text("""
                INSERT INTO t_InventoryTransactions
                (ProductID, TransactionType, Quantity, DateKey, Reference, CreatedBy)
                VALUES (:pid, 'RECEIPT', :qty, :dkey, :ref, :user)
            """),
            {'pid': product_id, 'qty': received_qty, 'dkey': today_key,
             'ref': f"Subcontract_{order_id}", 'user': current_user.UserID}
        )
        conn.execute(
            text("""
                UPDATE t_InventoryOnHand
                SET QuantityOnHand = QuantityOnHand + :qty
                WHERE ProductID = :pid
            """),
            {'qty': received_qty, 'pid': product_id}
        )
        # Update order status back to Released or Completed
        conn.execute(
            text("UPDATE t_ProductionOrder SET Status = 'Released' WHERE ProductionOrderId = :oid"),
            {'oid': order_id}
        )
    return jsonify({'message': 'Supplier material received'})

# ----------------------------------------------------------------------
# Quality batches for an order
# ----------------------------------------------------------------------
@api_bp.route('/production-orders/quality-batches/<string:order_id>')
@login_required
def quality_batches(order_id):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT BatchId, BatchNumber, InspectionDateKey, Status FROM t_QualityInspectionBatch WHERE ProductionOrderID = :oid"),
            {'oid': order_id}
        ).fetchall()
    batches = [{'id': r[0], 'number': r[1], 'dateKey': r[2], 'status': r[3]} for r in rows]
    return jsonify(batches)

# ----------------------------------------------------------------------
# Product search
# ----------------------------------------------------------------------
@api_bp.route('/production-orders/products-search')
@login_required
def product_search():
    q = request.args.get('q', '')
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT ProductId, descEnglish FROM t_Product WHERE ProductId LIKE :pattern OR descEnglish LIKE :pattern LIMIT 20"),
            {'pattern': f'%{q}%'}
        ).fetchall()
    products = [{'id': r[0], 'name': r[1]} for r in rows]
    return jsonify(products)

# ----------------------------------------------------------------------
# CSV Export / Import
# ----------------------------------------------------------------------
@api_bp.route('/production-orders/export-csv')
@login_required
def export_csv():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT * FROM t_ProductionOrder")).fetchall()
        col_names = [desc[0] for desc in conn.execute(text("PRAGMA table_info(t_ProductionOrder)")).fetchall()]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(col_names)
    for row in rows:
        writer.writerow(row)
    return Response(output.getvalue(), mimetype='text/csv',
                    headers={"Content-Disposition": "attachment;filename=production_orders.csv"})

@api_bp.route('/production-orders/import-csv', methods=['POST'])
@login_required
@admin_required
def import_csv():
    file = request.files['file']
    if not file:
        return jsonify({'error': 'No file'}), 400
    stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
    reader = csv.DictReader(stream)
    engine = get_engine()
    with engine.begin() as conn:
        for row in reader:
            # Basic mapping – adjust to your actual columns
            conn.execute(
                text("""
                    INSERT OR IGNORE INTO t_ProductionOrder
                    (ProductionOrderId, ProductId, OrderType, OrderQuantity, Status, EndDateKey, ProjectID)
                    VALUES (:oid, :pid, :otype, :qty, :status, :endkey, :proj)
                """),
                {'oid': row.get('ProductionOrderId'), 'pid': row.get('ProductId'),
                 'otype': row.get('OrderType', 'Manufacturing'), 'qty': row.get('OrderQuantity'),
                 'status': row.get('Status', 'Draft'), 'endkey': row.get('EndDateKey'),
                 'proj': row.get('ProjectID')}
            )
    return jsonify({'message': 'Import completed'})
    
 
@api_bp.route('/production-orders/components/<string:order_id>')
@login_required
def order_components(order_id):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT ComponentProductID, RequiredQuantity, IssuedQuantity FROM t_ProductionOrderComponents WHERE ProductionOrderId = :oid"),
            {'oid': order_id}
        ).fetchall()
    components = [{'componentId': r[0], 'requiredQuantity': r[1], 'issuedQuantity': r[2]} for r in rows]
    return jsonify(components)
    
@api_bp.route('/production-orders/<string:order_id>')
@login_required
def get_order(order_id):
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT ProductionOrderId, ProductId, OrderType, OrderQuantity,
                       EndDateKey, ProjectID, Priority, Status
                FROM t_ProductionOrder
                WHERE ProductionOrderId = :oid
            """),
            {'oid': order_id}
        ).first()
    if not row:
        return jsonify({'error': 'Order not found'}), 404
    return jsonify({
        'id': row[0],
        'productId': row[1],
        'orderType': row[2],
        'quantity': row[3],
        'dueDateKey': row[4],
        'projectId': row[5],
        'priority': row[6],
        'status': row[7]
    })

@api_bp.route('/production-orders/<string:order_id>', methods=['PUT'])
@login_required
@admin_required
def update_order(order_id):
    data = request.get_json()
    engine = get_engine()
    with engine.begin() as conn:
        # Only allow updates for Draft orders
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
        if 'projectId' in data:
            updates.append("ProjectID = :proj")
            params['proj'] = data['projectId']
        if 'priority' in data:
            updates.append("Priority = :prio")
            params['prio'] = data['priority']
        if 'orderType' in data:
            updates.append("OrderType = :otype")
            params['otype'] = data['orderType']
        if updates:
            sql = f"UPDATE t_ProductionOrder SET {', '.join(updates)} WHERE ProductionOrderId = :oid"
            conn.execute(text(sql), params)
    return jsonify({'message': 'Order updated'})
    
 
# -------------------- Shop Floor Control --------------------
@api_bp.route('/shop-floor/assigned-jobs')
@login_required
def assigned_jobs():
    work_center = request.args.get('work_center')
    shift = request.args.get('shift')

    engine = get_engine()
    with engine.connect() as conn:
        # Active jobs
        jobs = []
        sql_active = """
            SELECT sft.TransactionId, sft.OrderId, sft.WorkCenterId, sft.OperationCode,
                   sft.Status, sft.StartTime, po.ProductionOrderId, po.ProductId
            FROM t_ShopFloorTransactions sft
            JOIN t_ProductionOrder po ON sft.OrderId = po.ProductionOrderId
            WHERE sft.Status = 'Active'
            ORDER BY sft.StartTime DESC
        """
        rows = conn.execute(text(sql_active)).fetchall()
        for r in rows:
            jobs.append({
                'id': r[0],
                'orderId': r[1],
                'orderNumber': r[6],      # ProductionOrderId as order number
                'productId': r[7],
                'workCenter': r[2],
                'operation': r[3],
                'status': r[4],
                'startTime': r[5],
                'isActive': True
            })

        # Pending released orders (no active job)
        sql_pending = """
            SELECT po.ProductionOrderId, po.ProductId
            FROM t_ProductionOrder po
            WHERE po.Status = 'Released'
              AND NOT EXISTS (SELECT 1 FROM t_ShopFloorTransactions sft WHERE sft.OrderId = po.ProductionOrderId AND sft.Status = 'Active')
            ORDER BY po.StartDateKey DESC
        """
        pending = conn.execute(text(sql_pending)).fetchall()
        for p in pending:
            jobs.append({
                'id': None,
                'orderId': p[0],
                'orderNumber': p[0],
                'productId': p[1],
                'workCenter': '',
                'operation': 'Not Started',
                'status': 'Pending',
                'startTime': None,
                'isActive': False
            })
    return jsonify(jobs)

@api_bp.route('/shop-floor/start-job', methods=['POST'])
@login_required
def start_job():
    data = request.get_json()
    order_id = data['orderId']
    work_center = data.get('workCenter')
    operation = data.get('operation', 'Default')

    engine = get_engine()
    with engine.begin() as conn:
        # Create a new shop floor transaction
        now = datetime.now().isoformat()
        conn.execute(
            text("""
                INSERT INTO t_ShopFloorTransactions
                (OrderId, WorkCenterId, OperationCode, Status, StartTime, CreatedBy)
                VALUES (:oid, :wc, :op, 'Active', :start, :user)
            """),
            {'oid': order_id, 'wc': work_center, 'op': operation, 'start': now,
             'user': current_user.UserID}
        )
        # Optionally update order status to 'InProgress' if not already
        conn.execute(
            text("UPDATE t_ProductionOrder SET Status = 'InProgress' WHERE ProductionOrderId = :oid AND Status = 'Released'"),
            {'oid': order_id}
        )
    return jsonify({'message': 'Job started'})

@api_bp.route('/shop-floor/report-quantity', methods=['POST'])
@login_required
def report_quantity():
    data = request.get_json()
    transaction_id = data['transactionId']
    quantity = data['quantity']
    engine = get_engine()
    with engine.begin() as conn:
        # Update reported quantity
        conn.execute(
            text("UPDATE t_ShopFloorTransactions SET ReportedQuantity = ReportedQuantity + :qty WHERE TransactionId = :tid"),
            {'qty': quantity, 'tid': transaction_id}
        )
        # Also update order detail actual quantity (optional)
        order_id_row = conn.execute(
            text("SELECT OrderId FROM t_ShopFloorTransactions WHERE TransactionId = :tid"),
            {'tid': transaction_id}
        ).first()
        if order_id_row:
            order_id = order_id_row[0]
            conn.execute(
                text("""
                    UPDATE t_ProductionOrderDetail
                    SET ActualQuantity = COALESCE(ActualQuantity, 0) + :qty
                    WHERE ProductionOrderId = :oid
                """),
                {'qty': quantity, 'oid': order_id}
            )
    return jsonify({'message': 'Quantity reported'})

@api_bp.route('/shop-floor/downtime', methods=['POST'])
@login_required
def report_downtime():
    data = request.get_json()
    transaction_id = data.get('transactionId')
    reason = data['reason']
    duration_minutes = data.get('durationMinutes', 0)
    engine = get_engine()
    with engine.begin() as conn:
        # Record downtime in a separate row or add to transaction
        # For simplicity, update the active transaction with downtime info
        conn.execute(
            text("""
                UPDATE t_ShopFloorTransactions
                SET Status = 'Interrupted', DowntimeReason = :reason
                WHERE TransactionId = :tid
            """),
            {'reason': reason, 'tid': transaction_id}
        )
        # Optionally insert a downtime log entry (could be separate table, but skip)
    return jsonify({'message': 'Downtime recorded'})

@api_bp.route('/shop-floor/self-check', methods=['POST'])
@login_required
def self_check():
    data = request.get_json()
    transaction_id = data['transactionId']
    inspection_results = data['results']  # dict of parameter: value
    engine = get_engine()
    with engine.begin() as conn:
        # Get order and product from the transaction
        row = conn.execute(
            text("SELECT OrderId FROM t_ShopFloorTransactions WHERE TransactionId = :tid"),
            {'tid': transaction_id}
        ).first()
        if not row:
            return jsonify({'error': 'Transaction not found'}), 404
        order_id = row[0]
        # Insert into t_QualityInspectionResult (assuming it has appropriate columns)
        for param, value in inspection_results.items():
            conn.execute(
                text("""
                    INSERT INTO t_QualityInspectionResult
                    (OrderId, TransactionId, Parameter, MeasuredValue, InspectionDate, InspectedBy)
                    VALUES (:oid, :tid, :param, :val, datetime('now'), :user)
                """),
                {'oid': order_id, 'tid': transaction_id, 'param': param, 'val': value,
                 'user': current_user.UserID}
            )
    return jsonify({'message': 'Self-inspection recorded'})

@api_bp.route('/shop-floor/kpi')
@login_required
def shop_floor_kpi():
    engine = get_engine()
    with engine.connect() as conn:
        # Active jobs
        active = conn.execute(
            text("SELECT COUNT(*) FROM t_ShopFloorTransactions WHERE Status = 'Active'")
        ).scalar() or 0
        # Completed today (jobs completed today, or quantity reported today)
        today = datetime.now().strftime('%Y-%m-%d')
        completed_today = conn.execute(
            text("SELECT COUNT(*) FROM t_ShopFloorTransactions WHERE date(EndTime) = :today AND Status = 'Completed'"),
            {'today': today}
        ).scalar() or 0
        # Downtime today (sum of duration if stored, else count)
        downtime_today = conn.execute(
            text("SELECT COUNT(*) FROM t_ShopFloorTransactions WHERE date(StartTime) = :today AND DowntimeReason IS NOT NULL"),
            {'today': today}
        ).scalar() or 0
    return jsonify({
        'activeJobs': active,
        'completedToday': completed_today,
        'downtimeToday': downtime_today
    })

@api_bp.route('/shop-floor/work-centers')
@login_required
def work_centers():
    engine = get_engine()
    with engine.connect() as conn:
        # Check if there is a Name column; if not, use WorkCenterID as name
        rows = conn.execute(text("SELECT WorkCenterID FROM t_WorkCenter ORDER BY WorkCenterID")).fetchall()
    centers = [{'id': r[0], 'name': r[0]} for r in rows]
    return jsonify(centers)

@api_bp.route('/work-centers/stats')
@login_required
def work_centers_stats():
    engine = get_engine()
    type_filter = request.args.get('type')
    active_only = request.args.get('active_only') == 'true'
    with engine.connect() as conn:
        total = conn.execute(text("SELECT COUNT(*) FROM t_WorkCenter WHERE IsArchived = 0")).scalar() or 0
        internal = conn.execute(text("SELECT COUNT(*) FROM t_WorkCenter WHERE Type = 'Internal' AND IsArchived = 0")).scalar() or 0
        external = conn.execute(text("SELECT COUNT(*) FROM t_WorkCenter WHERE Type = 'External' AND IsArchived = 0")).scalar() or 0
        pending_ecos = 0  # placeholder
    return jsonify({'total': total, 'internal': internal, 'external': external, 'pendingEcos': pending_ecos})

@api_bp.route('/work-centers')
@login_required
def list_work_centers():
    type_filter = request.args.get('type')
    active_only = request.args.get('active_only') == 'true'
    search = request.args.get('search', '')
    engine = get_engine()
    params = {}
    conditions = ["IsArchived = 0"]
    if type_filter and type_filter != 'All':
        conditions.append("Type = :type")
        params['type'] = type_filter
    if active_only:
        conditions.append("IsActive = 1")
    if search:
        conditions.append("(WorkCenterID LIKE :search OR Name LIKE :search)")
        params['search'] = f'%{search}%'
    where = " AND ".join(conditions)
    sql = f"""
        SELECT WorkCenterID, Name, Type, Location, CapacityHours, HourlyRate, IsActive, IsArchived
        FROM t_WorkCenter
        WHERE {where}
        ORDER BY Name
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    items = []
    for r in rows:
        items.append({
            'id': r[0],
            'name': r[1],
            'type': r[2],
            'location': r[3],
            'capacityHours': r[4],
            'hourlyRate': r[5],
            'isActive': bool(r[6]) if r[6] is not None else True,
            'isArchived': bool(r[7]) if r[7] is not None else False
        })
    print("DEBUG: list_work_centers called")
    return jsonify(items)

@api_bp.route('/work-centers/<string:work_center_id>')
@login_required
def get_work_center(work_center_id):
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("SELECT * FROM t_WorkCenter WHERE WorkCenterID = :id"), {'id': work_center_id}).first()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    columns = [desc[0] for desc in conn.execute(text("PRAGMA table_info(t_WorkCenter)")).fetchall()]
    result = {col: row[idx] for idx, col in enumerate(columns)}
    return jsonify(result)

@api_bp.route('/work-centers', methods=['POST'])
@login_required
@admin_required
def create_work_center():
    data = request.get_json()
    from datetime import datetime
    today_key = int(datetime.now().strftime('%Y%m%d'))
    engine = get_engine()
    work_center_id = data.get('workCenterId', '').strip()
    if not work_center_id:
        work_center_id = data['name'].upper().replace(' ', '_')[:20]
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO t_WorkCenter
            (WorkCenterID, Name, Type, Location, CapacityHours, MaxConcurrentJobs,
             HourlyRate, CurrencyCode, SkillsRequired, EffectiveStartDateKey, EffectiveEndDateKey,
             IsActive, CreatedDateKey, ModifiedDateKey, Description)
            VALUES (:id, :name, :type, :loc, :cap, :maxjobs, :rate, :curr, :skills,
                    :start, :end, :active, :cdate, :mdate, :desc)
        """), {
            'id': work_center_id,
            'name': data['name'],
            'type': data.get('type', 'Internal'),
            'loc': data.get('location'),
            'cap': data.get('capacityHoursPerDay') or data.get('capacityHours'),  # accept both
            'maxjobs': data.get('maxConcurrentJobs', 1),
            'rate': data.get('hourlyRate'),
            'curr': data.get('currencyCode', 'USD'),
            'skills': json.dumps(data.get('skillsRequired', [])),
            'start': data.get('effectiveStartDateKey'),
            'end': data.get('effectiveEndDateKey'),
            'active': 1 if data.get('isActive', True) else 0,
            'cdate': today_key,
            'mdate': today_key,
            'desc': data.get('description')
        })
    return jsonify({'message': 'Work center created', 'id': work_center_id}), 201

# Similarly adjust PUT, COPY, etc. (use CapacityHours, not CapacityHoursPerDay)

    
 
# -------------------- Work Center Routing --------------------
@api_bp.route('/work-center-routing/kpi')
@login_required
def routing_kpi():
    engine = get_engine()
    with engine.connect() as conn:
        total = conn.execute(text("SELECT COUNT(*) FROM t_WorkCenterRouting WHERE ObsoleteDateKey IS NULL")).scalar() or 0
        # If IsExternal column exists, use it; otherwise fallback
        try:
            internal = conn.execute(text("SELECT COUNT(*) FROM t_WorkCenterRouting WHERE ObsoleteDateKey IS NULL AND IsExternal = 0")).scalar() or 0
            external = conn.execute(text("SELECT COUNT(*) FROM t_WorkCenterRouting WHERE ObsoleteDateKey IS NULL AND IsExternal = 1")).scalar() or 0
        except:
            internal = external = 0
    return jsonify({'totalSteps': total, 'internalSteps': internal, 'externalSteps': external})


@api_bp.route('/work-center-routing/list')
@login_required
def list_routing():
    product_id = request.args.get('product_id')
    show_effective_only = request.args.get('show_effective_only') == 'true'
    engine = get_engine()
    today_key = int(datetime.now().strftime('%Y%m%d')) if show_effective_only else None

    params = {}
    conditions = ["1=1"]
    if product_id:
        conditions.append("wcr.ProductID = :pid")
        params['pid'] = product_id
    if show_effective_only:
        conditions.append("(wcr.EffectivityDateKey IS NULL OR wcr.EffectivityDateKey <= :today)")
        conditions.append("(wcr.ObsoleteDateKey IS NULL OR wcr.ObsoleteDateKey > :today)")
        params['today'] = today_key

    where_clause = " AND ".join(conditions)
    sql = f"""
        SELECT wcr.ProductID, wcr.WorkCenterID, wcr.OperationSequence,
               wcr.SetupTimeHours, wcr.RunTimeHoursPerUnit,
               wcr.QueueTimeHours, wcr.MoveTimeHours,
               wcr.EffectivityDateKey, wcr.ObsoleteDateKey,
               p.descEnglish as ProductName
        FROM t_WorkCenterRouting wcr
        LEFT JOIN t_Product p ON wcr.ProductID = p.ProductId
        WHERE {where_clause}
        ORDER BY wcr.ProductID, wcr.OperationSequence
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    steps = []
    for r in rows:
        steps.append({
            'productId': r[0],
            'productName': r[9] if len(r) > 9 else None,
            'workCenter': r[1],
            'sequence': r[2],
            'setupHours': r[3],
            'runHours': r[4],
            'queueHours': r[5],
            'moveHours': r[6],
            'effectivityDateKey': r[7],
            'obsoleteDateKey': r[8],
            'isExternal': False,
            'supplierId': None
        })
    return jsonify(steps)

@api_bp.route('/work-center-routing/step', methods=['POST'])
@login_required
@admin_required
def create_routing_step():
    data = request.get_json()
    engine = get_engine()
    today_key = int(datetime.now().strftime('%Y%m%d'))
    with engine.begin() as conn:
        # Check if sequence already exists for product
        existing = conn.execute(
            text("SELECT 1 FROM t_WorkCenterRouting WHERE ProductID = :pid AND OperationSequence = :seq"),
            {'pid': data['productId'], 'seq': data['sequence']}
        ).first()
        if existing:
            return jsonify({'error': 'Operation sequence already exists for this product'}), 400
        conn.execute(
            text("""
                INSERT INTO t_WorkCenterRouting
                (ProductID, WorkCenterID, OperationSequence, SetupTimeHours, RunTimeHoursPerUnit,
                 QueueTimeHours, MoveTimeHours, EffectivityDateKey, IsExternal, SupplierID, created_at)
                VALUES (:pid, :wc, :seq, :setup, :run, :queue, :move, :eff, :ext, :supp, CURRENT_TIMESTAMP)
            """),
            {'pid': data['productId'], 'wc': data['workCenter'], 'seq': data['sequence'],
             'setup': data.get('setupHours', 0), 'run': data.get('runHours', 0),
             'queue': data.get('queueHours', 0), 'move': data.get('moveHours', 0),
             'eff': data.get('effectivityDateKey'), 'ext': 1 if data.get('isExternal') else 0,
             'supp': data.get('supplierId')}
        )
    return jsonify({'message': 'Routing step created'}), 201

@api_bp.route('/work-center-routing/step/<string:product_id>/<int:sequence>', methods=['PUT'])
@login_required
@admin_required
def update_routing_step(product_id, sequence):
    data = request.get_json()
    engine = get_engine()
    with engine.begin() as conn:
        # Check if step exists
        exists = conn.execute(
            text("SELECT 1 FROM t_WorkCenterRouting WHERE ProductID = :pid AND OperationSequence = :seq"),
            {'pid': product_id, 'seq': sequence}
        ).first()
        if not exists:
            return jsonify({'error': 'Step not found'}), 404
        conn.execute(
            text("""
                UPDATE t_WorkCenterRouting
                SET WorkCenterID = :wc,
                    SetupTimeHours = :setup,
                    RunTimeHoursPerUnit = :run,
                    QueueTimeHours = :queue,
                    MoveTimeHours = :move,
                    EffectivityDateKey = :eff,
                    ObsoleteDateKey = :obs,
                    IsExternal = :ext,
                    SupplierID = :supp
                WHERE ProductID = :pid AND OperationSequence = :seq
            """),
            {'pid': product_id, 'seq': sequence, 'wc': data['workCenter'],
             'setup': data.get('setupHours', 0), 'run': data.get('runHours', 0),
             'queue': data.get('queueHours', 0), 'move': data.get('moveHours', 0),
             'eff': data.get('effectivityDateKey'), 'obs': data.get('obsoleteDateKey'),
             'ext': 1 if data.get('isExternal') else 0, 'supp': data.get('supplierId')}
        )
    return jsonify({'message': 'Routing step updated'})

@api_bp.route('/work-center-routing/step/<string:product_id>/<int:sequence>', methods=['DELETE'])
@login_required
@admin_required
def delete_routing_step(product_id, sequence):
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM t_WorkCenterRouting WHERE ProductID = :pid AND OperationSequence = :seq"),
            {'pid': product_id, 'seq': sequence}
        )
    return jsonify({'message': 'Step deleted'})

@api_bp.route('/work-center-routing/quality-checks/<string:work_center>/<int:sequence>')
@login_required
def quality_checks(work_center, sequence):
    # Placeholder – relies on t_QualityRoutingAssignment table
    # Return list of quality operations assigned to this work center/step
    engine = get_engine()
    try:
        rows = conn.execute(
            text("SELECT AssignmentID, DefinitionID, Code, Name FROM t_QualityRoutingAssignment WHERE WorkCenterID = :wc AND OperationSequence = :seq"),
            {'wc': work_center, 'seq': sequence}
        ).fetchall()
    except:
        rows = []
    checks = [{'id': r[0], 'defId': r[1], 'code': r[2], 'name': r[3]} for r in rows]
    return jsonify(checks)

@api_bp.route('/work-center-routing/work-centers')
@login_required
def routing_work_centers():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT WorkCenterID FROM t_WorkCenter ORDER BY WorkCenterID")).fetchall()
    centers = [{'id': r[0], 'name': r[0]} for r in rows]
    return jsonify(centers)

@api_bp.route('/work-center-routing/suppliers')
@login_required
def routing_suppliers():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT companyID, Name FROM t_companies WHERE IsSupplier = 1")).fetchall()
    suppliers = [{'id': r[0], 'name': r[1]} for r in rows]
    return jsonify(suppliers)

@api_bp.route('/work-center-routing/products')
@login_required
def routing_products():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT ProductId, descEnglish FROM t_Product ORDER BY descEnglish")).fetchall()
    products = [{'id': r[0], 'name': r[1]} for r in rows]
    return jsonify(products)

@api_bp.route('/work-center-routing/export-csv')
@login_required
def export_routing_csv():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT * FROM t_WorkCenterRouting")).fetchall()
        col_names = [desc[0] for desc in conn.execute(text("PRAGMA table_info(t_WorkCenterRouting)")).fetchall()]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(col_names)
    for row in rows:
        writer.writerow(row)
    return Response(output.getvalue(), mimetype='text/csv',
                    headers={"Content-Disposition": "attachment;filename=work_center_routing.csv"})

@api_bp.route('/work-center-routing/import-csv', methods=['POST'])
@login_required
@admin_required
def import_routing_csv():
    file = request.files['file']
    if not file:
        return jsonify({'error': 'No file'}), 400
    stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
    reader = csv.DictReader(stream)
    engine = get_engine()
    with engine.begin() as conn:
        for row in reader:
            conn.execute(
                text("""
                    INSERT OR REPLACE INTO t_WorkCenterRouting
                    (ProductID, WorkCenterID, OperationSequence, SetupTimeHours, RunTimeHoursPerUnit,
                     QueueTimeHours, MoveTimeHours, EffectivityDateKey, ObsoleteDateKey, IsExternal, SupplierID)
                    VALUES (:pid, :wc, :seq, :setup, :run, :queue, :move, :eff, :obs, :ext, :supp)
                """),
                {'pid': row['ProductID'], 'wc': row['WorkCenterID'], 'seq': row['OperationSequence'],
                 'setup': row.get('SetupTimeHours', 0), 'run': row.get('RunTimeHoursPerUnit', 0),
                 'queue': row.get('QueueTimeHours', 0), 'move': row.get('MoveTimeHours', 0),
                 'eff': row.get('EffectivityDateKey'), 'obs': row.get('ObsoleteDateKey'),
                 'ext': 1 if row.get('IsExternal') == '1' else 0, 'supp': row.get('SupplierID')}
            )
    return jsonify({'message': 'Import completed'})
    
 
 
@api_bp.route('/work-center-routing/step/<string:product_id>/<int:sequence>')
@login_required
def get_routing_step(product_id, sequence):
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM t_WorkCenterRouting WHERE ProductID = :pid AND OperationSequence = :seq"),
            {'pid': product_id, 'seq': sequence}
        ).first()
    if not row:
        return jsonify({'error': 'Step not found'}), 404
    return jsonify({
        'productId': row[1],
        'workCenter': row[2],
        'sequence': row[3],
        'setupHours': row[4],
        'runHours': row[5],
        'queueHours': row[6],
        'moveHours': row[7],
        'effectivityDateKey': row[8],
        'obsoleteDateKey': row[9],
        'isExternal': row[10] == 1 if len(row) > 10 else False,
        'supplierId': row[11] if len(row) > 11 else None
    })
    
  
# -------------------- Capacity Planning --------------------
@api_bp.route('/capacity/work-centers')
@login_required
def capacity_work_centers():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT WorkCenterID FROM t_WorkCenter ORDER BY WorkCenterID")).fetchall()
    return jsonify([r[0] for r in rows])

@api_bp.route('/capacity/kpi')
@login_required
def capacity_kpi():
    wc = request.args.get('work_center')
    date_from = request.args.get('date_from')   # YYYY-MM-DD
    date_to = request.args.get('date_to')
    engine = get_engine()
    # Total load = sum of (setup + run*quantity) for all released orders within routing valid period
    today_key = int(datetime.now().strftime('%Y%m%d'))
    from_key = date_to_key(date_from) if date_from else today_key
    to_key = date_to_key(date_to) if date_to else today_key + 30
    params = {'from_key': from_key, 'to_key': to_key}
    wc_cond = " AND wcr.WorkCenterID = :wc" if wc else ""
    if wc:
        params['wc'] = wc
    sql_total_load = f"""
        SELECT SUM(wcr.SetupTimeHours + (wcr.RunTimeHoursPerUnit * po.OrderQuantity)) as total_load
        FROM t_ProductionOrder po
        JOIN t_WorkCenterRouting wcr ON po.ProductID = wcr.ProductID
        WHERE po.Status IN ('Released', 'InProgress')
          AND (wcr.EffectivityDateKey IS NULL OR wcr.EffectivityDateKey <= :to_key)
          AND (wcr.ObsoleteDateKey IS NULL OR wcr.ObsoleteDateKey > :from_key)
          {wc_cond}
    """
    with engine.connect() as conn:
        total_load = conn.execute(text(sql_total_load), params).scalar() or 0
        # Get total available capacity from t_CapacityLoad for the period
        sql_avail = f"""
            SELECT SUM(AvailableCapacityHours) FROM t_CapacityLoad
            WHERE DateKey BETWEEN :from_key AND :to_key
            {wc_cond}
        """
        avail = conn.execute(text(sql_avail), params).scalar() or 0
    util = (total_load / avail * 100) if avail > 0 else 0
    # Overloaded work centers: where load > avail on any day
    sql_over = f"""
        SELECT wc.WorkCenterID, SUM(load_daily.load) as total_load, SUM(cl.AvailableCapacityHours) as avail
        FROM (SELECT WorkCenterID, DateKey, SUM(SetupTimeHours + (RunTimeHoursPerUnit * po.OrderQuantity)) as load
              FROM t_ProductionOrder po
              JOIN t_WorkCenterRouting wcr ON po.ProductID = wcr.ProductID
              WHERE po.Status IN ('Released', 'InProgress')
                AND DateKey BETWEEN :from_key AND :to_key
              GROUP BY WorkCenterID, DateKey) load_daily
        JOIN t_CapacityLoad cl ON load_daily.WorkCenterID = cl.WorkCenterID AND load_daily.DateKey = cl.DateKey
        GROUP BY load_daily.WorkCenterID
        HAVING total_load > avail
    """
    overloaded = conn.execute(text(sql_over), params).fetchall()
    overloaded_centers = [r[0] for r in overloaded]
    return jsonify({
        'utilization': round(util, 1),
        'overloadedCenters': overloaded_centers,
        'totalLoadHours': round(total_load, 2)
    })

@api_bp.route('/capacity/load-profile')
@login_required
def load_profile():
    wc = request.args.get('work_center')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    engine = get_engine()
    from_key = date_to_key(date_from) if date_from else int(datetime.now().strftime('%Y%m%d'))
    to_key = date_to_key(date_to) if date_to else from_key + 30
    params = {'from_key': from_key, 'to_key': to_key}
    wc_cond = ""
    if wc:
        wc_cond = " AND wcr.WorkCenterID = :wc"
        params['wc'] = wc

    sql = f"""
        SELECT d.DateKey,
               COALESCE(load_daily_alias.load, 0) as load,
               COALESCE(cl.AvailableCapacityHours, 0) as avail
        FROM DimDate d
        LEFT JOIN (
            SELECT wcr.WorkCenterID,
                   po.EndDateKey as DateKey,
                   SUM(wcr.SetupTimeHours + (wcr.RunTimeHoursPerUnit * po.OrderQuantity)) as load
            FROM t_ProductionOrder po
            JOIN t_WorkCenterRouting wcr ON po.ProductId = wcr.ProductID
            WHERE po.Status IN ('Released', 'InProgress')
              AND po.EndDateKey BETWEEN :from_key AND :to_key
              {wc_cond}
            GROUP BY wcr.WorkCenterID, po.EndDateKey
        ) AS load_daily_alias ON d.DateKey = load_daily_alias.DateKey
        LEFT JOIN t_CapacityLoad cl ON d.DateKey = cl.DateKey
            AND cl.WorkCenterID = load_daily_alias.WorkCenterID
        WHERE d.DateKey BETWEEN :from_key AND :to_key
        ORDER BY d.DateKey
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    data = [{'dateKey': r[0], 'load': float(r[1]), 'available': float(r[2])} for r in rows]
    return jsonify(data)


@api_bp.route('/capacity/gantt')
@login_required
def gantt_data():
    wc = request.args.get('work_center')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    from_key = date_to_key(date_from) if date_from else int(datetime.now().strftime('%Y%m%d'))
    to_key = date_to_key(date_to) if date_to else from_key + 30
    engine = get_engine()
    params = {'from_key': from_key, 'to_key': to_key}
    wc_cond = ""
    if wc:
        wc_cond = " AND wcr.WorkCenterID = :wc"
        params['wc'] = wc

    sql = f"""
        SELECT po.ProductionOrderId as id,
               po.ProductId,
               wcr.WorkCenterID,
               wcr.OperationSequence,
               po.StartDateKey as start,
               po.EndDateKey as end,
               (wcr.SetupTimeHours + (wcr.RunTimeHoursPerUnit * po.OrderQuantity)) as hours
        FROM t_ProductionOrder po
        JOIN t_WorkCenterRouting wcr ON po.ProductId = wcr.ProductID
        WHERE po.Status IN ('Released', 'InProgress')
          AND po.EndDateKey BETWEEN :from_key AND :to_key
          AND (wcr.EffectivityDateKey IS NULL OR wcr.EffectivityDateKey <= :to_key)
          AND (wcr.ObsoleteDateKey IS NULL OR wcr.ObsoleteDateKey > :from_key)
          {wc_cond}
        ORDER BY wcr.WorkCenterID, po.StartDateKey
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    tasks = []
    for r in rows:
        tasks.append({
            'id': r[0],
            'product': r[1],
            'workCenter': r[2],
            'opSeq': r[3],
            'start': r[4],
            'end': r[5],
            'hours': float(r[6])
        })
    return jsonify(tasks)



@api_bp.route('/capacity/reschedule', methods=['POST'])
@login_required
@admin_required
def reschedule_order():
    data = request.get_json()
    order_id = data['orderId']
    new_start = date_to_key(data['newStartDate'])
    new_end = date_to_key(data['newEndDate'])
    engine = get_engine()
    with engine.begin() as conn:
        # Update t_ProductionOrder dates
        conn.execute(
            text("UPDATE t_ProductionOrder SET StartDateKey = :start, EndDateKey = :end WHERE ProductionOrderId = :oid"),
            {'start': new_start, 'end': new_end, 'oid': order_id}
        )
        # Also update t_ProductionSchedule if exists
        try:
            conn.execute(
                text("UPDATE t_ProductionSchedule SET ScheduledStartDateKey = :start, ScheduledEndDateKey = :end WHERE ProductionOrderID = :oid"),
                {'start': new_start, 'end': new_end, 'oid': order_id}
            )
        except:
            pass
    return jsonify({'message': 'Rescheduled'})

@api_bp.route('/capacity/add-shift', methods=['POST'])
@login_required
@admin_required
def add_shift():
    data = request.get_json()
    wc = data['workCenter']
    date_key = date_to_key(data['date'])
    extra_hours = data['extraHours']
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO t_CapacityLoad (WorkCenterID, DateKey, AvailableCapacityHours, RunID)
                VALUES (:wc, :dkey, :hours, 'MANUAL')
                ON CONFLICT(WorkCenterID, DateKey, RunID) DO UPDATE SET AvailableCapacityHours = AvailableCapacityHours + :hours
            """),
            {'wc': wc, 'dkey': date_key, 'hours': extra_hours}
        )
    return jsonify({'message': 'Shift added'})

@api_bp.route('/capacity/export-csv')
@login_required
def export_capacity_csv():
    wc = request.args.get('work_center')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    from_key = date_to_key(date_from) if date_from else int(datetime.now().strftime('%Y%m%d'))
    to_key = date_to_key(date_to) if date_to else from_key + 30
    params = {'from_key': from_key, 'to_key': to_key}
    cond = " AND WorkCenterID = :wc" if wc else ""
    if wc:
        params['wc'] = wc
    sql = f"""
        SELECT WorkCenterID, DateKey, TotalLoadHours, AvailableCapacityHours, UtilizationPercent
        FROM t_CapacityLoad
        WHERE DateKey BETWEEN :from_key AND :to_key {cond}
        ORDER BY WorkCenterID, DateKey
    """
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['WorkCenterID', 'DateKey', 'TotalLoadHours', 'AvailableCapacityHours', 'UtilizationPercent'])
    for r in rows:
        writer.writerow(r)
    return Response(output.getvalue(), mimetype='text/csv',
                    headers={"Content-Disposition": "attachment;filename=capacity_load.csv"})
                    
                
                

# -------------------- MRP Run Dashboard --------------------
import uuid
import threading
from datetime import datetime

def _run_mrp_background(planning_number, params):
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.services.mrp_service import run_mrp_async
        engine = get_engine()
        try:
            run_mrp_async(planning_number, params, engine)
        except Exception as e:
            import traceback
            traceback.print_exc()
            with engine.begin() as conn:
                conn.execute(text("UPDATE t_PlanningRuns SET Status = 'Failed' WHERE PlanningNumber = :pn"), {'pn': planning_number})
                conn.execute(text("INSERT INTO t_RunLogs (RunID, Step, Message) VALUES (:pn, 'Error', :msg)"), {'pn': planning_number, 'msg': str(e)})

@api_bp.route('/mrp/kpi')
@login_required
def mrp_kpi():
    engine = get_engine()
    with engine.connect() as conn:
        # Last run status
        last_run = conn.execute(
            text("SELECT Status, RunDate FROM t_PlanningRuns ORDER BY RunDate DESC LIMIT 1")
        ).first()
        last_status = last_run[0] if last_run else 'N/A'
        # Total runs
        total_runs = conn.execute(text("SELECT COUNT(*) FROM t_PlanningRuns")).scalar() or 0
        # Average duration (using logs: for each run, get min and max timestamp, compute seconds)
        # We'll compute from start and end log entries
        avg_duration = 0
        runs_with_duration = conn.execute(text("""
            SELECT r.PlanningNumber,
                   MIN(l.Timestamp) as start_time,
                   MAX(l.Timestamp) as end_time
            FROM t_PlanningRuns r
            JOIN t_RunLogs l ON r.PlanningNumber = l.RunID
            GROUP BY r.PlanningNumber
            HAVING start_time IS NOT NULL AND end_time IS NOT NULL
        """)).fetchall()
        durations = []
        for row in runs_with_duration:
            start = datetime.fromisoformat(row[1])
            end = datetime.fromisoformat(row[2])
            durations.append((end - start).total_seconds())
        if durations:
            avg_duration = sum(durations) / len(durations)
    return jsonify({
        'lastRunStatus': last_status,
        'totalRuns': total_runs,
        'avgDurationSeconds': round(avg_duration, 1)
    })

@api_bp.route('/mrp/runs')
@login_required
def mrp_runs():
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    status = request.args.get('status')
    engine = get_engine()
    params = {}
    conditions = ["1=1"]
    if date_from:
        conditions.append("RunDate >= :date_from")
        params['date_from'] = date_from
    if date_to:
        conditions.append("RunDate <= :date_to")
        params['date_to'] = date_to
    if status:
        conditions.append("Status = :status")
        params['status'] = status
    where = " AND ".join(conditions)
    sql = f"""
        SELECT pr.PlanningNumber, pr.RunDate, pr.Status, pr.CreatedBy,
               MIN(l.Timestamp) as start_time, MAX(l.Timestamp) as end_time
        FROM t_PlanningRuns pr
        LEFT JOIN t_RunLogs l ON pr.PlanningNumber = l.RunID
        WHERE {where}
        GROUP BY pr.PlanningNumber
        ORDER BY pr.RunDate DESC
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    runs = []
    for r in rows:
        duration = None
        if r[4] and r[5]:
            start = datetime.fromisoformat(r[4])
            end = datetime.fromisoformat(r[5])
            duration = round((end - start).total_seconds(), 1)
        runs.append({
            'planningNumber': r[0],
            'runDate': r[1],
            'status': r[2],
            'createdBy': r[3],
            'durationSeconds': duration
        })
    return jsonify(runs)



@api_bp.route('/mrp/start-run', methods=['POST'])
def start_mrp_run():
    try:
        data = request.get_json()
        description = data.get('description', f"MRP Run - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        planning_horizon = data.get('planningHorizonDays', 90)
        is_simulation = data.get('isSimulation', False)
        include_capacity = data.get('includeCapacityCheck', False)
        planning_number = f"MRP-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        engine = get_engine()
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO t_PlanningRuns
                    (PlanningNumber, RunDate, IsSimulation, Description, PlanningHorizonDays,
                     PlanningStartDateKey, IncludeCapacityCheck, Status, CreatedBy)
                    VALUES (:pn, :rundate, :sim, :desc, :horizon, :startkey, :cap, 'Running', :user)
                """),
                {'pn': planning_number,
                 'rundate': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                 'sim': 1 if is_simulation else 0,
                 'desc': description,
                 'horizon': planning_horizon,
                 'startkey': int(datetime.now().strftime('%Y%m%d')),
                 'cap': 1 if include_capacity else 0,
                 'user': 1}   # hardcoded admin user ID (from your t_Users table)
            )
            conn.execute(
                text("INSERT INTO t_RunLogs (RunID, Step, Message) VALUES (:pn, 'Start', 'MRP calculation started')"),
                {'pn': planning_number}
            )
        # Start background thread
        thread = threading.Thread(target=_run_mrp_background, args=(planning_number, data))
        thread.daemon = True
        thread.start()
        return jsonify({'planningNumber': planning_number, 'message': 'MRP run started'}), 202
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@api_bp.route('/mrp/run-status/<string:planning_number>')
@login_required
def mrp_run_status(planning_number):
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT Status FROM t_PlanningRuns WHERE PlanningNumber = :pn"),
            {'pn': planning_number}
        ).first()
        if not row:
            return jsonify({'error': 'Run not found'}), 404
        status = row[0]
        # Get latest log message
        log = conn.execute(
            text("SELECT Message, Timestamp FROM t_RunLogs WHERE RunID = :pn ORDER BY LogID DESC LIMIT 1"),
            {'pn': planning_number}
        ).first()
        last_message = log[0] if log else ''
        progress = 0
        if status == 'Completed':
            progress = 100
        elif status == 'Running':
            progress = 50  # approximate
        return jsonify({'status': status, 'progress': progress, 'lastMessage': last_message})

@api_bp.route('/mrp/run-log/<string:planning_number>')
@login_required
def mrp_run_log(planning_number):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT Step, Message, Timestamp FROM t_RunLogs WHERE RunID = :pn ORDER BY LogID"),
            {'pn': planning_number}
        ).fetchall()
    logs = [{'step': r[0], 'message': r[1], 'timestamp': r[2]} for r in rows]
    return jsonify(logs)

@api_bp.route('/mrp/run-details/<string:planning_number>')
@login_required
def mrp_run_details(planning_number):
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM t_PlanningRuns WHERE PlanningNumber = :pn"),
            {'pn': planning_number}
        ).first()
    if not row:
        return jsonify({'error': 'Run not found'}), 404
    # Convert row to dict (assuming row has ._asdict() or we use keys)
    columns = [desc[0] for desc in conn.execute(text("PRAGMA table_info(t_PlanningRuns)")).fetchall()]
    details = {col: row[idx] for idx, col in enumerate(columns)}
    return jsonify(details)
    



# -------------------- MRP – Planned Orders --------------------
@api_bp.route('/mrp/planned-orders/kpi')
@login_required
def planned_orders_kpi():
    run_id = request.args.get('run_id')
    engine = get_engine()
    total = purchase = manufacturing = past_due = 0
    if run_id:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT SUM(PlannedOrderReceipts), OrderType FROM t_MRPRunResults WHERE RunID = :rid GROUP BY OrderType"), {'rid': run_id}).fetchall()
            for row in rows:
                qty = row[0] or 0
                total += qty
                if row[1] == 'Purchase':
                    purchase += qty
                else:
                    manufacturing += qty
            today = int(datetime.now().strftime('%Y%m%d'))
            past_due = conn.execute(text("SELECT SUM(PlannedOrderReceipts) FROM t_MRPRunResults WHERE RunID = :rid AND DateKey < :today"), {'rid': run_id, 'today': today}).scalar() or 0
    return jsonify({'total': total, 'purchase': purchase, 'manufacturing': manufacturing, 'pastDue': past_due})

@api_bp.route('/mrp/planned-orders/list')
@login_required
def list_planned_orders():
    run_id = request.args.get('run_id')
    product = request.args.get('product')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    order_type = request.args.get('order_type')
    engine = get_engine()
    params = {}
    conditions = ["1=1"]
    if run_id:
        conditions.append("RunID = :rid")
        params['rid'] = run_id
    if product:
        conditions.append("ProductID = :prod")
        params['prod'] = product
    if date_from:
        conditions.append("DateKey >= :from")
        params['from'] = date_to_key(date_from)
    if date_to:
        conditions.append("DateKey <= :to")
        params['to'] = date_to_key(date_to)
    if order_type:
        conditions.append("OrderType = :otype")
        params['otype'] = order_type
    where = " AND ".join(conditions)
    sql = f"""
        SELECT ProductID, DateKey, PlannedOrderReceipts, OrderType, IsFirmed, RunID
        FROM t_MRPRunResults
        WHERE {where}
        ORDER BY DateKey
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    orders = []
    today = int(datetime.now().strftime('%Y%m%d'))
    for r in rows:
        orders.append({
            'productId': r[0],
            'dateKey': r[1],
            'quantity': r[2],
            'orderType': r[3],
            'isFirmed': bool(r[4]),
            'runId': r[5],
            'isPastDue': r[1] < today if r[1] else False
        })
    return jsonify(orders)

@api_bp.route('/mrp/planned-orders/firm', methods=['POST'])
@login_required
def firm_planned_order():
    data = request.get_json()
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE t_MRPRunResults SET IsFirmed = 1, FirmedBy = :user, FirmedDate = CURRENT_TIMESTAMP WHERE RunID = :rid AND ProductID = :pid AND DateKey = :dkey"),
            {'rid': data['runId'], 'pid': data['productId'], 'dkey': data['dateKey'], 'user': current_user.get_id()}
        )
    return jsonify({'message': 'Order firmed'})

@api_bp.route('/mrp/planned-orders/mass-update', methods=['POST'])
@login_required
def mass_update_planned_orders():
    data = request.get_json()
    engine = get_engine()
    params = {'rid': data['runId'], 'firm': 1 if data.get('firm', False) else 0}
    conditions = ["RunID = :rid"]
    if data.get('productFilter'):
        conditions.append("ProductID LIKE :prod")
        params['prod'] = f"%{data['productFilter']}%"
    if data.get('orderType'):
        conditions.append("OrderType = :otype")
        params['otype'] = data['orderType']
    where = " AND ".join(conditions)
    sql = f"UPDATE t_MRPRunResults SET IsFirmed = :firm WHERE {where}"
    with engine.begin() as conn:
        conn.execute(text(sql), params)
    return jsonify({'message': 'Mass update completed'})

@api_bp.route('/mrp/planned-orders/export-csv')
@login_required
def export_planned_orders_csv():
    run_id = request.args.get('run_id')
    engine = get_engine()
    params = {}
    where = "1=1"
    if run_id:
        where = "RunID = :rid"
        params['rid'] = run_id
    with engine.connect() as conn:
        rows = conn.execute(text(f"SELECT * FROM t_MRPRunResults WHERE {where}"), params).fetchall()
        col_names = [desc[0] for desc in conn.execute(text("PRAGMA table_info(t_MRPRunResults)")).fetchall()]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(col_names)
    for row in rows:
        writer.writerow(row)
    return Response(output.getvalue(), mimetype='text/csv', headers={"Content-Disposition": "attachment;filename=planned_orders.csv"})

# -------------------- MRP – Exceptions --------------------
@api_bp.route('/mrp/exceptions/kpi')
@login_required
def exceptions_kpi():
    run_id = request.args.get('run_id')
    engine = get_engine()
    params = {}
    cond = "1=1"
    if run_id:
        cond = "PlanningNumber = :rid"
        params['rid'] = run_id
    sql = f"SELECT Priority, COUNT(*) FROM t_MRPExceptions WHERE {cond} GROUP BY Priority"
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    counts = {'High': 0, 'Medium': 0, 'Low': 0}
    for row in rows:
        pri = row[0]
        cnt = row[1]
        if pri >= 3:
            counts['High'] += cnt
        elif pri == 2:
            counts['Medium'] += cnt
        else:
            counts['Low'] += cnt
    return jsonify(counts)

@api_bp.route('/mrp/exceptions/list')
@login_required
def list_exceptions():
    run_id = request.args.get('run_id')
    severity = request.args.get('severity')
    status = request.args.get('status')
    product = request.args.get('product')
    engine = get_engine()
    params = {}
    conditions = ["1=1"]
    if run_id:
        conditions.append("PlanningNumber = :rid")
        params['rid'] = run_id
    if severity:
        if severity == 'High':
            conditions.append("Priority >= 3")
        elif severity == 'Medium':
            conditions.append("Priority = 2")
        else:
            conditions.append("Priority <= 1")
    if status:
        conditions.append("Status = :stat")
        params['stat'] = status
    if product:
        conditions.append("ProductID = :prod")
        params['prod'] = product
    where = " AND ".join(conditions)
    sql = f"""
        SELECT PlanningNumber, ProductID, MessageType, Details, RunDate, Priority, DateKey, Status
        FROM t_MRPExceptions
        WHERE {where}
        ORDER BY Priority DESC, DateKey
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    exceptions = []
    for r in rows:
        exceptions.append({
            'runId': r[0],
            'productId': r[1],
            'type': r[2],
            'details': r[3],
            'runDate': r[4],
            'priority': r[5],
            'dateKey': r[6],
            'status': r[7] or 'Open'
        })
    return jsonify(exceptions)

@api_bp.route('/mrp/exceptions/resolve', methods=['POST'])
@login_required
def resolve_exception():
    data = request.get_json()
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE t_MRPExceptions SET Status = 'Resolved' WHERE PlanningNumber = :rid AND ProductID = :pid AND MessageType = :type"),
            {'rid': data['runId'], 'pid': data['productId'], 'type': data['messageType']}
        )
    return jsonify({'message': 'Exception resolved'})

@api_bp.route('/mrp/exceptions/export-csv')
@login_required
def export_exceptions_csv():
    run_id = request.args.get('run_id')
    engine = get_engine()
    params = {}
    where = "1=1"
    if run_id:
        where = "PlanningNumber = :rid"
        params['rid'] = run_id
    with engine.connect() as conn:
        rows = conn.execute(text(f"SELECT * FROM t_MRPExceptions WHERE {where}"), params).fetchall()
        col_names = [desc[0] for desc in conn.execute(text("PRAGMA table_info(t_MRPExceptions)")).fetchall()]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(col_names)
    for row in rows:
        writer.writerow(row)
    return Response(output.getvalue(), mimetype='text/csv', headers={"Content-Disposition": "attachment;filename=exceptions.csv"})

# -------------------- MRP – What If Scenarios --------------------
@api_bp.route('/mrp/scenarios/kpi')
@login_required
def scenarios_kpi():
    engine = get_engine()
    with engine.connect() as conn:
        total = conn.execute(text("SELECT COUNT(*) FROM t_InventoryProjectionScenarios")).scalar() or 0
        active = conn.execute(text("SELECT COUNT(*) FROM t_InventoryProjectionScenarios WHERE Status = 'Published'")).scalar() or 0
    return jsonify({'totalScenarios': total, 'activeScenario': active})

@api_bp.route('/mrp/scenarios/list')
@login_required
def list_scenarios():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT ScenarioID, Name, Description, Status, CreatedDateKey, CreatedBy FROM t_InventoryProjectionScenarios ORDER BY CreatedDateKey DESC")).fetchall()
    scenarios = []
    for r in rows:
        scenarios.append({
            'id': r[0],
            'name': r[1],
            'description': r[2],
            'status': r[3],
            'createdDateKey': r[4],
            'createdBy': r[5]
        })
    return jsonify(scenarios)


@api_bp.route('/mrp/scenarios/create', methods=['POST'])
@login_required
def create_scenario():
    try:
        data = request.get_json()
        if not data or not data.get('name'):
            return jsonify({'error': 'Name is required'}), 400
        today_key = int(datetime.now().strftime('%Y%m%d'))
        engine = get_engine()
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO t_InventoryProjectionScenarios (Name, Description, Status, CreatedDateKey, CreatedBy, ScenarioData)
                    VALUES (:name, :desc, 'Draft', :cdate, :user, :data)
                """),
                {'name': data['name'], 'desc': data.get('description', ''), 'cdate': today_key,
                 'user': int(current_user.get_id()), 'data': json.dumps(data.get('scenarioData', {}))}
            )
            new_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()
        return jsonify({'scenarioId': new_id, 'message': 'Scenario created'}), 201
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@api_bp.route('/mrp/scenarios/publish/<int:scenario_id>', methods=['POST'])
@login_required
def publish_scenario(scenario_id):
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("UPDATE t_InventoryProjectionScenarios SET Status = 'Published' WHERE ScenarioID = :sid"), {'sid': scenario_id})
    return jsonify({'message': 'Scenario published. Run MRP to see effects.'})

@api_bp.route('/mrp/scenarios/compare')
@login_required
def compare_scenarios():
    # Simplified – in real implementation, run MRP for both scenarios and compare
    return jsonify({
        'scenario1': {'name': 'Base', 'data': {}},
        'scenario2': {'name': 'Proposed', 'data': {}},
        'differences': []
    })

@api_bp.route('/mrp/simulate', methods=['POST'])
@login_required
def simulate_mrp():
    """Run MRP simulation with user-selected input sources."""
    data = request.get_json()
    horizon_days = data.get('horizon_days', 90)
    start_date_str = data.get('start_date')
    include_sales = data.get('include_sales_orders', True)
    include_forecasts = data.get('include_forecasts', True)
    include_purchase = data.get('include_purchase_orders', True)
    include_production = data.get('include_production_orders', True)

    start_date = datetime.strptime(start_date_str, '%Y-%m-%d') if start_date_str else datetime.now()
    start_key = int(start_date.strftime('%Y%m%d'))
    tmp_planning = f"SIM-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    engine = get_engine()

    mrp = MRPEngine(engine, tmp_planning, {'planningHorizonDays': horizon_days, 'planningStartDateKey': start_key})

    def filtered_demand():
        mrp.log_step("DemandLoad", "Filtered demand")
        with engine.connect() as conn:
            if include_sales:
                rows = conn.execute(text("""
                    SELECT ProductID, DeliveryDateKey, SUM(Qty) as total_qty
                    FROM t_SalesOrder
                    WHERE Status IN ('Confirmed', 'Open', 'Approved')
                      AND DeliveryDateKey >= :start
                    GROUP BY ProductID, DeliveryDateKey
                """), {'start': start_key}).fetchall()
                for row in rows:
                    prod = row[0]
                    dk = int(row[1])
                    qty = row[2]
                    mrp.net_requirements[prod][dk] = mrp.net_requirements[prod].get(dk, 0) + qty
            if include_forecasts:
                rows = conn.execute(text("""
                    SELECT ProductId, DateKey, Qty
                    FROM t_Forecasts
                    WHERE DateKey >= :start
                """), {'start': start_key}).fetchall()
                for row in rows:
                    prod = row[0]
                    dk = int(row[1])
                    qty = row[2]
                    mrp.net_requirements[prod][dk] = mrp.net_requirements[prod].get(dk, 0) + qty

    def filtered_supply():
        mrp.log_step("SupplyLoad", "Filtered supply")
        with engine.connect() as conn:
            onhand_rows = conn.execute(text("""
                SELECT ProductID, SUM(Qty) as total
                FROM t_InventoryOnHand WHERE Status = 'Available' GROUP BY ProductID
            """)).fetchall()
            mrp.onhand = {r[0]: r[1] for r in onhand_rows}
            mrp.scheduled_receipts = defaultdict(dict)
            if include_purchase:
                rows = conn.execute(text("""
                    SELECT ProductID, ExpectedDeliveryDateKey, SUM(Qty) as total
                    FROM t_PurchaseOrder
                    WHERE Status IN ('Open', 'Approved') AND ExpectedDeliveryDateKey >= :start
                    GROUP BY ProductID, ExpectedDeliveryDateKey
                """), {'start': start_key}).fetchall()
                for row in rows:
                    prod = row[0]
                    dk = int(row[1])
                    qty = row[2]
                    mrp.scheduled_receipts[prod][dk] = mrp.scheduled_receipts[prod].get(dk, 0) + qty
            if include_production:
                rows = conn.execute(text("""
                    SELECT ProductId, EndDateKey, OrderQuantity
                    FROM t_ProductionOrder
                    WHERE Status IN ('Released', 'InProgress') AND EndDateKey >= :start
                """), {'start': start_key}).fetchall()
                for row in rows:
                    prod = row[0]
                    dk = int(row[1])
                    qty = row[2]
                    mrp.scheduled_receipts[prod][dk] = mrp.scheduled_receipts[prod].get(dk, 0) + qty

    mrp.load_demand = filtered_demand
    mrp.load_onhand_and_receipts = filtered_supply

    try:
        mrp.load_demand()
        mrp.load_onhand_and_receipts()
        for prod, reqs in list(mrp.net_requirements.items()):
            for dk, qty in reqs.items():
                mrp.explode_bom(prod, qty, dk)
        mrp.calculate_net_requirements()
        planned_orders = []
        exceptions = []
        for prod, req_by_date in mrp.net_requirements.items():
            for dk, qty in req_by_date.items():
                if qty <= 0 or dk is None:
                    continue
                with engine.connect() as conn:
                    prod_info = conn.execute(text("""
                        SELECT ProcurementType, MinOrderQty, BatchSize FROM t_Product WHERE ProductId = :pid
                    """), {'pid': prod}).first()
                lot = 1
                if prod_info:
                    if prod_info[0] == 'Purchased' and prod_info[1]:
                        lot = prod_info[1]
                    elif prod_info[2]:
                        lot = prod_info[2]
                order_qty = ((qty + lot - 1) // lot) * lot
                order_type = 'Purchase' if prod_info and prod_info[0] == 'Purchased' else 'Manufacturing'
                planned_orders.append({'productId': prod, 'dateKey': dk, 'quantity': order_qty, 'orderType': order_type})
        for exc in mrp.exceptions:
            exceptions.append({'productId': exc['product'], 'type': exc['type'], 'details': exc['details'], 'dateKey': exc['date_key']})
        return jsonify({'planned_orders': planned_orders, 'exceptions': exceptions})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500    
        
        

# -------------------- Subcontract Dashboard --------------------
@api_bp.route('/subcontract/kpi')
@login_required
def subcontract_kpi():
    engine = get_engine()
    with engine.connect() as conn:
        # Count by status of production order (for subcontracted orders)
        rows = conn.execute(text("""
            SELECT po.Status, COUNT(*) as cnt
            FROM t_ProductionOrder po
            WHERE po.OrderType = 'Subcontract'
            GROUP BY po.Status
        """)).fetchall()
        status_counts = {row[0]: row[1] for row in rows}
        # Overdue: EndDateKey < today and status not 'Completed' or 'Closed'
        today = int(datetime.now().strftime('%Y%m%d'))
        overdue = conn.execute(text("""
            SELECT COUNT(*) FROM t_ProductionOrder
            WHERE OrderType = 'Subcontract'
              AND Status NOT IN ('Completed', 'Closed')
              AND EndDateKey < :today
        """), {'today': today}).scalar() or 0
    return jsonify({
        'Open': status_counts.get('Released', 0),
        'InProgress': status_counts.get('InProgress', 0),
        'Completed': status_counts.get('Completed', 0),
        'Overdue': overdue
    })

@api_bp.route('/subcontract/production-list')
@login_required
def subcontract_list():
    supplier = request.args.get('supplier')
    status = request.args.get('status')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    engine = get_engine()
    params = {}
    conditions = ["po.OrderType = 'Subcontract'"]
    if supplier:
        conditions.append("s.SupplierID = :supp")
        params['supp'] = supplier
    if status:
        conditions.append("po.Status = :stat")
        params['stat'] = status
    if date_from:
        from_key = date_to_key(date_from)
        conditions.append("po.EndDateKey >= :from")
        params['from'] = from_key
    if date_to:
        to_key = date_to_key(date_to)
        conditions.append("po.EndDateKey <= :to")
        params['to'] = to_key

    where_clause = " AND ".join(conditions)
    sql = f"""
        SELECT po.ProductionOrderId, po.ProductId, po.Status, po.EndDateKey,
               po.ProjectID, popo.PurchaseOrderID, popo.Status as link_status,
               s.SupplierID, s.SupplierName,
               wcr.WorkCenterID as operation
        FROM t_ProductionOrder po
        LEFT JOIN t_ProductionOrderPurchaseOrder popo ON po.ProductionOrderId = popo.ProductionOrderID
        LEFT JOIN t_PurchaseOrder p ON popo.PurchaseOrderID = p.PurchaseOrderID
        LEFT JOIN t_Suppliers s ON p.SupplierID = s.SupplierID
        LEFT JOIN t_WorkCenterRouting wcr ON po.ProductId = wcr.ProductID AND wcr.OperationSequence = (SELECT MIN(OperationSequence) FROM t_WorkCenterRouting WHERE ProductID = po.ProductId)
        WHERE {where_clause}
        ORDER BY po.EndDateKey ASC
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    items = []
    for r in rows:
        items.append({
            'productionOrderId': r[0],
            'productId': r[1],
            'status': r[2],
            'dueDateKey': r[3],
            'projectId': r[4],
            'purchaseOrderId': r[5],
            'poLinkStatus': r[6],
            'supplierId': r[7],
            'supplierName': r[8],
            'operation': r[9] or 'Subcontract'
        })
    return jsonify(items)

@api_bp.route('/subcontract/send-reminder', methods=['POST'])
@login_required
def subcontract_send_reminder():
    data = request.get_json()
    po_id = data.get('purchaseOrderId')
    # In a real system, send an email or notification.
    # For now, just log and return success.
    print(f"Reminder sent for PO {po_id}")
    return jsonify({'message': 'Reminder sent'})

@api_bp.route('/subcontract/expedite', methods=['POST'])
@login_required
def subcontract_expedite():
    data = request.get_json()
    po_id = data.get('purchaseOrderId')
    # Placeholder: update purchase order priority, send request to supplier.
    print(f"Expedite request for PO {po_id}")
    return jsonify({'message': 'Expedite request recorded'})

@api_bp.route('/subcontract/export-csv')
@login_required
def export_subcontract_csv():
    supplier = request.args.get('supplier')
    status = request.args.get('status')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    engine = get_engine()
    params = {}
    conditions = ["po.OrderType = 'Subcontract'"]
    if supplier:
        conditions.append("s.SupplierID = :supp")
        params['supp'] = supplier
    if status:
        conditions.append("po.Status = :stat")
        params['stat'] = status
    if date_from:
        from_key = date_to_key(date_from)
        conditions.append("po.EndDateKey >= :from")
        params['from'] = from_key
    if date_to:
        to_key = date_to_key(date_to)
        conditions.append("po.EndDateKey <= :to")
        params['to'] = to_key
    where_clause = " AND ".join(conditions)
    sql = f"""
        SELECT po.ProductionOrderId, po.ProductId, po.Status, po.EndDateKey,
               s.SupplierName, popo.PurchaseOrderID, wcr.WorkCenterID as operation
        FROM t_ProductionOrder po
        LEFT JOIN t_ProductionOrderPurchaseOrder popo ON po.ProductionOrderId = popo.ProductionOrderID
        LEFT JOIN t_PurchaseOrder p ON popo.PurchaseOrderID = p.PurchaseOrderID
        LEFT JOIN t_Suppliers s ON p.SupplierID = s.SupplierID
        LEFT JOIN t_WorkCenterRouting wcr ON po.ProductId = wcr.ProductID AND wcr.OperationSequence = (SELECT MIN(OperationSequence) FROM t_WorkCenterRouting WHERE ProductID = po.ProductId)
        WHERE {where_clause}
        ORDER BY po.EndDateKey
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Production Order ID', 'Product', 'Status', 'Due Date Key', 'Supplier', 'PO#', 'Operation'])
    for r in rows:
        writer.writerow(r)
    return Response(output.getvalue(), mimetype='text/csv', headers={"Content-Disposition": "attachment;filename=subcontract_orders.csv"})
    
  
# -------------------- Maintenance Management --------------------
@api_bp.route('/maintenance/kpi')
@login_required
def maintenance_kpi():
    engine = get_engine()
    today = datetime.now().date()
    today_key = int(today.strftime('%Y%m%d'))
    # Overdue: NextMaintenanceDate < today and Status != 'Completed'
    overdue = 0
    due_this_week = 0
    completed = 0
    with engine.connect() as conn:
        # Overdue
        overdue = conn.execute(text("""
            SELECT COUNT(*) FROM t_EquipmentMaintenance
            WHERE date(NextMaintenanceDate) < date(:today) AND Status != 'Completed'
        """), {'today': today.isoformat()}).scalar() or 0
        # Due this week (next 7 days)
        due_this_week = conn.execute(text("""
            SELECT COUNT(*) FROM t_EquipmentMaintenance
            WHERE date(NextMaintenanceDate) BETWEEN date(:today) AND date(:today, '+7 days')
              AND Status != 'Completed'
        """), {'today': today.isoformat()}).scalar() or 0
        # Completed
        completed = conn.execute(text("""
            SELECT COUNT(*) FROM t_EquipmentMaintenance
            WHERE Status = 'Completed'
        """)).scalar() or 0
    return jsonify({'overdue': overdue, 'dueThisWeek': due_this_week, 'completed': completed})

@api_bp.route('/maintenance/list')
@login_required
def maintenance_list():
    equip_type = request.args.get('type')
    status = request.args.get('status')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    engine = get_engine()
    params = {}
    conditions = ["1=1"]
    if equip_type:
        conditions.append("MaintenanceType = :type")
        params['type'] = equip_type
    if status:
        conditions.append("Status = :stat")
        params['stat'] = status
    if date_from:
        conditions.append("date(NextMaintenanceDate) >= :from")
        params['from'] = date_from
    if date_to:
        conditions.append("date(NextMaintenanceDate) <= :to")
        params['to'] = date_to
    where_clause = " AND ".join(conditions)
    sql = f"""
        SELECT MaintenanceID, EquipmentID, EquipmentName, MaintenanceType,
               ScheduleType, LastMaintenanceDate, NextMaintenanceDate,
               Status, WorkCenterID, TechnicianID
        FROM t_EquipmentMaintenance
        WHERE {where_clause}
        ORDER BY NextMaintenanceDate ASC
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    items = []
    for r in rows:
        items.append({
            'id': r[0],
            'equipmentId': r[1],
            'equipmentName': r[2],
            'type': r[3],
            'scheduleType': r[4],
            'lastMaintenance': r[5],
            'nextDue': r[6],
            'status': r[7],
            'workCenter': r[8],
            'technicianId': r[9]
        })
    return jsonify(items)

@api_bp.route('/maintenance/create', methods=['POST'])
@login_required
@admin_required
def create_maintenance():
    data = request.get_json()
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO t_EquipmentMaintenance
            (EquipmentID, EquipmentName, WorkCenterID, MaintenanceType, ScheduleType,
             IntervalDays, LastMaintenanceDate, NextMaintenanceDate, Status, CreatedDate)
            VALUES (:eid, :ename, :wc, :mtype, :sched, :interval, :last, :next, 'Scheduled', CURRENT_TIMESTAMP)
        """), {
            'eid': data['equipmentId'],
            'ename': data['equipmentName'],
            'wc': data.get('workCenter'),
            'mtype': data['maintenanceType'],
            'sched': data.get('scheduleType'),
            'interval': data.get('intervalDays'),
            'last': data.get('lastDate'),
            'next': data.get('nextDue')
        })
    return jsonify({'message': 'Maintenance record created'}), 201

@api_bp.route('/maintenance/complete/<int:maintenance_id>', methods=['POST'])
@login_required
@admin_required
def complete_maintenance(maintenance_id):
    data = request.get_json()
    engine = get_engine()
    with engine.begin() as conn:
        # Get current record to compute next due if recurring
        row = conn.execute(text("SELECT ScheduleType, IntervalDays, NextMaintenanceDate FROM t_EquipmentMaintenance WHERE MaintenanceID = :mid"), {'mid': maintenance_id}).first()
        if not row:
            return jsonify({'error': 'Not found'}), 404
        # Update current record as completed
        conn.execute(text("""
            UPDATE t_EquipmentMaintenance
            SET Status = 'Completed', ActualDate = :actual, TechnicianID = :tech, DurationHours = :dur, Cost = :cost, Notes = :notes
            WHERE MaintenanceID = :mid
        """), {
            'actual': data.get('actualDate'),
            'tech': data.get('technicianId'),
            'dur': data.get('durationHours'),
            'cost': data.get('cost'),
            'notes': data.get('notes'),
            'mid': maintenance_id
        })
        # If recurring, create a new record for next occurrence
        if row[0] and row[1] and row[0] != 'Once':
            # Calculate next due date from actual completion date
            from datetime import datetime, timedelta
            actual_date = datetime.strptime(data.get('actualDate'), '%Y-%m-%d')
            next_due = actual_date + timedelta(days=row[1])
            # Insert new record
            conn.execute(text("""
                INSERT INTO t_EquipmentMaintenance
                (EquipmentID, EquipmentName, WorkCenterID, MaintenanceType, ScheduleType,
                 IntervalDays, LastMaintenanceDate, NextMaintenanceDate, Status, CreatedDate)
                SELECT EquipmentID, EquipmentName, WorkCenterID, MaintenanceType, ScheduleType,
                       IntervalDays, :last, :next, 'Scheduled', CURRENT_TIMESTAMP
                FROM t_EquipmentMaintenance WHERE MaintenanceID = :mid
            """), {'last': data.get('actualDate'), 'next': next_due.strftime('%Y-%m-%d'), 'mid': maintenance_id})
    return jsonify({'message': 'Maintenance completed and next scheduled'})

@api_bp.route('/maintenance/export-csv')
@login_required
def export_maintenance_csv():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT * FROM t_EquipmentMaintenance")).fetchall()
        col_names = [desc[0] for desc in conn.execute(text("PRAGMA table_info(t_EquipmentMaintenance)")).fetchall()]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(col_names)
    for row in rows:
        writer.writerow(row)
    return Response(output.getvalue(), mimetype='text/csv', headers={"Content-Disposition": "attachment;filename=maintenance.csv"})

@api_bp.route('/maintenance/import-csv', methods=['POST'])
@login_required
@admin_required
def import_maintenance_csv():
    file = request.files['file']
    if not file:
        return jsonify({'error': 'No file'}), 400
    stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
    reader = csv.DictReader(stream)
    engine = get_engine()
    with engine.begin() as conn:
        for row in reader:
            conn.execute(text("""
                INSERT INTO t_EquipmentMaintenance
                (EquipmentID, EquipmentName, WorkCenterID, MaintenanceType, ScheduleType,
                 IntervalDays, LastMaintenanceDate, NextMaintenanceDate, Status)
                VALUES (:eid, :ename, :wc, :mtype, :sched, :interval, :last, :next, 'Scheduled')
            """), {
                'eid': row.get('EquipmentID'),
                'ename': row.get('EquipmentName'),
                'wc': row.get('WorkCenterID'),
                'mtype': row.get('MaintenanceType'),
                'sched': row.get('ScheduleType'),
                'interval': row.get('IntervalDays'),
                'last': row.get('LastMaintenanceDate'),
                'next': row.get('NextMaintenanceDate')
            })
    return jsonify({'message': 'Import completed'})
    
 
# -------------------- Barcode/RFID Interface --------------------
@api_bp.route('/barcode/active-sessions')
@login_required
def barcode_active_sessions():
    """Return count of active jobs for the current operator."""
    engine = get_engine()
    with engine.connect() as conn:
        count = conn.execute(text("""
            SELECT COUNT(*) FROM t_ShopFloorTransactions
            WHERE OperatorID = :op AND EndTime IS NULL
        """), {'op': current_user.get_id()}).scalar() or 0
        today = datetime.now().strftime('%Y-%m-%d')
        scans_today = conn.execute(text("""
            SELECT COUNT(*) FROM t_ShopFloorTransactions
            WHERE date(TransactionTime) = :today
        """), {'today': today}).scalar() or 0
    return jsonify({'activeSessions': count, 'scansToday': scans_today})

@api_bp.route('/barcode/scan', methods=['POST'])
@login_required
def barcode_scan():
    data = request.get_json()
    barcode = data.get('barcode', '').strip()
    if not barcode:
        return jsonify({'error': 'No barcode provided'}), 400

    parts = barcode.split(':', 1)
    if len(parts) == 2:
        barcode_type, value = parts[0].upper(), parts[1]
    else:
        if barcode.startswith('PO-') or barcode.startswith('WO-'):
            barcode_type, value = 'JOB', barcode
        elif barcode.isdigit():
            barcode_type, value = 'EMP', barcode
        elif barcode.startswith('SER'):
            barcode_type, value = 'SERIAL', barcode
        else:
            barcode_type, value = 'WC', barcode

    engine = get_engine()
    response = {'message': '', 'action': None, 'success': False}

    if barcode_type == 'JOB':
        with engine.begin() as conn:
            order = conn.execute(text("SELECT Status FROM t_ProductionOrder WHERE ProductionOrderId = :oid"), {'oid': value}).first()
            if not order:
                return jsonify({'error': 'Order not found'}), 404
            if order[0] not in ('Released', 'InProgress'):
                return jsonify({'error': f'Order cannot be started (status: {order[0]})'}), 400
            active = conn.execute(text("SELECT TransactionId FROM t_ShopFloorTransactions WHERE OrderId = :oid AND EndTime IS NULL"), {'oid': value}).first()
            if active:
                response['message'] = f'Job {value} already active'
                response['action'] = 'resume'
                response['success'] = True
            else:
                now = datetime.now().isoformat()
                conn.execute(text("""
                    INSERT INTO t_ShopFloorTransactions
                    (OrderId, WorkCenterID, OperationCode, Status, StartTime, Quantity, OperatorID, TransactionType)
                    VALUES (:oid, NULL, 'Started', 'Active', :start, 0, :op, 'Start')
                """), {'oid': value, 'start': now, 'op': current_user.get_id()})
                response['message'] = f'Job {value} started'
                response['action'] = 'start'
                response['success'] = True
    elif barcode_type == 'SERIAL':
        with engine.begin() as conn:
            serial = conn.execute(text("SELECT ProductID, ProductionOrderId, Status FROM t_SerialNumbers WHERE SerialNumber = :sn"), {'sn': value}).first()
            if not serial:
                return jsonify({'error': 'Serial number not found'}), 404
            order_id = serial[1]
            if not order_id:
                return jsonify({'error': 'Serial number not linked to a production order'}), 400
            trans = conn.execute(text("SELECT TransactionId FROM t_ShopFloorTransactions WHERE OrderId = :oid AND OperatorID = :op AND EndTime IS NULL"), {'oid': order_id, 'op': current_user.get_id()}).first()
            if not trans:
                return jsonify({'error': 'No active job for this serial number'}), 400
            conn.execute(text("UPDATE t_ShopFloorTransactions SET Quantity = Quantity + 1, ReportedQuantity = ReportedQuantity + 1 WHERE TransactionId = :tid"), {'tid': trans[0]})
            conn.execute(text("UPDATE t_SerialNumbers SET Status = 'Completed' WHERE SerialNumber = :sn"), {'sn': value})
            response['message'] = f'Quantity reported for {value}'
            response['action'] = 'report_quantity'
            response['success'] = True
    elif barcode_type == 'EMP':
        response['message'] = f'Employee {value} scanned'
        response['action'] = 'employee'
        response['success'] = True
    elif barcode_type == 'WC':
        response['message'] = f'Work center {value} selected'
        response['action'] = 'work_center'
        response['success'] = True
    else:
        return jsonify({'error': f'Unknown barcode type: {barcode_type}'}), 400

    return jsonify(response)

@api_bp.route('/barcode/current-status')
@login_required
def barcode_current_status():
    engine = get_engine()
    with engine.connect() as conn:
        trans = conn.execute(text("""
            SELECT st.OrderId, po.ProductId, po.OrderQuantity, st.Quantity as completed,
                   wc.WorkCenterID, st.StartTime
            FROM t_ShopFloorTransactions st
            JOIN t_ProductionOrder po ON st.OrderId = po.ProductionOrderId
            LEFT JOIN t_WorkCenterRouting wcr ON po.ProductId = wcr.ProductID
            LEFT JOIN t_WorkCenter wc ON wcr.WorkCenterID = wc.WorkCenterID
            WHERE st.OperatorID = :op AND st.EndTime IS NULL
            LIMIT 1
        """), {'op': current_user.get_id()}).first()
        if trans:
            return jsonify({
                'orderId': trans[0],
                'productId': trans[1],
                'totalQuantity': trans[2],
                'completedQuantity': trans[3] or 0,
                'workCenter': trans[4],
                'startTime': trans[5]
            })
        else:
            return jsonify({'active': False})
            
            
      
@api_bp.route('/process-flow/<string:product_id>')
@login_required
def get_process_flow(product_id):
    """Return BOM + routing data for a product, including machine layout and flow edges."""
    engine = get_engine()
    
    with engine.connect() as conn:
        # 1. Get product info
        product = conn.execute(
            text("SELECT ProductId, descEnglish FROM t_Product WHERE ProductId = :pid"),
            {'pid': product_id}
        ).first()
        if not product:
            return jsonify({'error': 'Product not found'}), 404
        
        # 2. Get all work centers (for layout)
        machines = conn.execute(
            text("SELECT ml.MachineID, ml.WorkCenterID, ml.CoordinatesX, ml.CoordinatesY, ml.MachineSize, wc.Name "
                 "FROM t_MachineLayout ml JOIN t_WorkCenter wc ON ml.WorkCenterID = wc.WorkCenterID")
        ).fetchall()
        
        machine_layout = [
            {
                'id': r[0],
                'workCenterId': r[1],
                'x': r[2],
                'y': r[3],
                'size': r[4],
                'name': r[5]
            }
            for r in machines
        ]
        
        # 3. Get BOM tree (recursive)
        def get_bom_tree(pid, level=0, max_level=3):
            rows = conn.execute(
                text("SELECT ComponentProductID, Quantity FROM t_BOM WHERE ParentProductID = :pid"),
                {'pid': pid}
            ).fetchall()
            children = []
            for r in rows:
                child = {
                    'productId': r[0],
                    'quantity': r[1],
                    'level': level,
                    'children': get_bom_tree(r[0], level+1, max_level) if level < max_level else []
                }
                children.append(child)
            return children
        
        bom_tree = get_bom_tree(product_id)
        
        # 4. Get routing for the main product and all its BOM components
        all_product_ids = [product_id]
        def collect_product_ids(pid):
            rows = conn.execute(text("SELECT ComponentProductID FROM t_BOM WHERE ParentProductID = :pid"), {'pid': pid}).fetchall()
            for r in rows:
                all_product_ids.append(r[0])
                collect_product_ids(r[0])
        collect_product_ids(product_id)
        
        # Remove duplicates
        all_product_ids = list(set(all_product_ids))
        
        # Get routing for all products
        routings = {}
        for pid in all_product_ids:
            rows = conn.execute(
                text("""
                    SELECT wr.WorkCenterID, wr.OperationSequence, wr.SetupTimeHours, wr.RunTimeHoursPerUnit,
                           wr.QueueTimeHours, wr.MoveTimeHours, wr.AssemblyPoint, wr.ParallelOperation,
                           p.descEnglish as ProductName
                    FROM t_WorkCenterRouting wr
                    JOIN t_Product p ON wr.ProductID = p.ProductId
                    WHERE wr.ProductID = :pid AND (wr.ObsoleteDateKey IS NULL OR wr.ObsoleteDateKey > date('now'))
                    ORDER BY wr.OperationSequence
                """),
                {'pid': pid}
            ).fetchall()
            if rows:
                routings[pid] = [
                    {
                        'workCenterId': r[0],
                        'sequence': r[1],
                        'setupTime': r[2],
                        'runTime': r[3],
                        'queueTime': r[4],
                        'moveTime': r[5],
                        'assemblyPoint': bool(r[6]) if r[6] is not None else False,
                        'parallelOperation': bool(r[7]) if r[7] is not None else False,
                        'productName': r[8]
                    }
                    for r in rows
                ]
        
        # 5. Build flow edges (material flow between operations)
        edges = []
        for pid, routing in routings.items():
            # If product is a component, add edge from its BOM parent to its first routing step
            if pid != product_id:
                parent = conn.execute(
                    text("SELECT ParentProductID FROM t_BOM WHERE ComponentProductID = :pid LIMIT 1"),
                    {'pid': pid}
                ).first()
                if parent:
                    # Find the routing step of the parent that corresponds to this component
                    parent_routing = routings.get(parent[0], [])
                    if parent_routing:
                        edges.append({
                            'source': parent[0],
                            'target': pid,
                            'type': 'component_flow'
                        })
            
            # Add edges between routing steps
            for i in range(len(routing) - 1):
                edges.append({
                    'source': pid,
                    'target': pid,
                    'sourceWorkCenter': routing[i]['workCenterId'],
                    'targetWorkCenter': routing[i+1]['workCenterId'],
                    'sequence': i,
                    'type': 'routing_flow'
                })
        
        return jsonify({
            'product': {
                'id': product[0],
                'name': product[1]
            },
            'bomTree': bom_tree,
            'routings': routings,
            'machineLayout': machine_layout,
            'edges': edges
        })
        
        
        
@api_bp.route('/shop-floor-layout')
@login_required
def shop_floor_layout():
    """Return shop floor layout data with work center status and order counts."""
    engine = get_engine()
    
    with engine.connect() as conn:
        # Get all work centers
        wc_rows = conn.execute(text("SELECT WorkCenterID, Name FROM t_WorkCenter")).fetchall()
        work_centers = {r[0]: {'name': r[1], 'orders': [], 'count': 0, 'status': 'No Orders'} for r in wc_rows}
        
        # Get active transactions
        trans_rows = conn.execute(text("""
            SELECT st.WorkCenterID, po.ProductionOrderId, st.Status
            FROM t_ShopFloorTransactions st
            JOIN t_ProductionOrder po ON st.OrderId = po.ProductionOrderId
            WHERE st.Status = 'Active'
        """)).fetchall()
        
        # Group by work center
        for row in trans_rows:
            wc_id = row[0]
            order_id = row[1]
            if wc_id in work_centers:
                work_centers[wc_id]['orders'].append(order_id)
                work_centers[wc_id]['count'] += 1
                work_centers[wc_id]['status'] = 'Active'
        
        # For work centers with no active orders, check if they have any orders at all
        for wc_id in work_centers:
            if work_centers[wc_id]['count'] == 0:
                planned = conn.execute(text("""
                    SELECT COUNT(*) FROM t_WorkCenterRouting WHERE WorkCenterID = :wc
                """), {'wc': wc_id}).scalar() or 0
                if planned > 0:
                    work_centers[wc_id]['status'] = 'Idle'
                else:
                    work_centers[wc_id]['status'] = 'No Orders'
        
        # Count overloaded (more than 2 active orders per work center)
        for wc_id in work_centers:
            if work_centers[wc_id]['count'] > 2:
                work_centers[wc_id]['status'] = 'Overloaded'
        
        return jsonify({'workCenters': work_centers})