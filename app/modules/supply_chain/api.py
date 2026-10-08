# =============================================================================
# supply_chain/api.py
# =============================================================================
# Sections:
#   1. Imports & helpers
#   2. RFQ Management             (Page 27)
#   3. Purchase Orders            (Page 28)
#   4. Inventory Status           (Page 29)
#   5. Receiving & Shipping       (Page 30)
#   6. Stock Overview             (KPI, list, movements, adjust/transfer/export)
#   7. Warehouse Management       (Page 32)
#   8. Suppliers (full management + portal user + documents + audit)
#   9. Current User               (module-scoped + global alias)
# =============================================================================

from . import api_bp
from flask import request, jsonify, current_app, session
from flask_login import login_required, current_user
from sqlalchemy.sql import text
from datetime import datetime
import os
import csv
from io import StringIO
import json


# =============================================================================
# 1. HELPERS
# =============================================================================

def get_engine():
    return current_app.extensions['sqlalchemy'].engine


def today_key():
    return int(datetime.now().strftime('%Y%m%d'))


def get_po_status_counts():
    """KPI counts for the Purchase Orders page."""
    engine = get_engine()
    today = today_key()
    demo_industry = session.get('demo_industry', 'valve')
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT po.Status,
                   po.PurchaseOrderID,
                   SUM(TRY_CAST(pod.Quantity AS DECIMAL(18,4))) as total_qty,
                   SUM(TRY_CAST(pod.ReceivedQuantity AS DECIMAL(18,4))) as total_received,
                   MIN(pod.ExpectedDeliveryDateKey) as min_delivery
            FROM t_PurchaseOrder po
            LEFT JOIN t_PurchaseOrderDetail pod ON po.PurchaseOrderID = pod.PurchaseOrderID
            WHERE po.DemoIndustryCode = :industry
            GROUP BY po.PurchaseOrderID, po.Status
        """), {'industry': demo_industry}).fetchall()

    counts = {'Draft':0, 'Sent':0, 'Partially Received':0, 'Closed':0, 'Overdue':0}
    for row in rows:
        status = row[0] or 'Draft'
        total_qty = row[2] or 0
        total_received = row[3] or 0
        min_delivery = row[4]
        if total_qty > 0 and total_received >= total_qty:
            counts['Closed'] += 1
        elif total_received > 0 and total_received < total_qty:
            counts['Partially Received'] += 1
        elif min_delivery and min_delivery < today and status.lower() not in ['delivered','closed'] and total_received < total_qty:
            counts['Overdue'] += 1
        elif status.lower() == 'sent':
            counts['Sent'] += 1
        else:
            counts['Draft'] += 1
    return counts


# =============================================================================
# 2. RFQ MANAGEMENT (PAGE 27)
# =============================================================================

@api_bp.route('/supply-chain/rfq/list')
@login_required
def list_rfqs():
    status = request.args.get('status', '')
    supplier = request.args.get('supplier', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    project = request.args.get('project', '')

    demo_industry = session.get('demo_industry', 'valve')

    query = """
        SELECT r.RFQID, r.RFQNumber, r.Title, r.Status, r.DueDateKey, r.ProjectID,
               r.CreatedDateKey,
               (SELECT COUNT(*) FROM t_RFQResponse rr WHERE rr.RFQID = r.RFQID) as SupplierCount,
               (SELECT MIN(QuoteAmount) FROM t_RFQResponse rr WHERE rr.RFQID = r.RFQID) as MinQuote
        FROM t_RFQ r
        WHERE r.DemoIndustryCode = :industry
    """
    params = {'industry': demo_industry}

    if status:
        query += " AND r.Status = :status"
        params['status'] = status
    if supplier:
        query += " AND EXISTS (SELECT 1 FROM t_RFQResponse rr WHERE rr.RFQID = r.RFQID AND rr.SupplierID = :supplier)"
        params['supplier'] = supplier
    if date_from:
        query += " AND r.CreatedDateKey >= :date_from"
        params['date_from'] = int(date_from.replace('-', ''))
    if date_to:
        query += " AND r.CreatedDateKey <= :date_to"
        params['date_to'] = int(date_to.replace('-', ''))
    if project and project != 'All':
        query += " AND r.ProjectID = :project"
        params['project'] = project

    query += " ORDER BY r.CreatedDateKey DESC"

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

    items = [{
        'id': r[0],
        'number': r[1],
        'title': r[2],
        'status': r[3] or 'Open',
        'dueDateKey': r[4],
        'projectId': r[5],
        'createdDateKey': r[6],
        # ✅ Renamed: frontend expects `supplierCount`, not `responseCount`
        'supplierCount': r[7] or 0,
        'minQuote': float(r[8]) if r[8] else 0
    } for r in rows]

    with engine.connect() as conn:
        kpi_rows = conn.execute(text("""
            SELECT Status, COUNT(*)
            FROM t_RFQ
            WHERE DemoIndustryCode = :industry
            GROUP BY Status
        """), {'industry': demo_industry}).fetchall()

    kpi = {'open': 0, 'sent': 0, 'received': 0, 'converted': 0, 'closed': 0, 'total': 0}
    for row in kpi_rows:
        status_key = row[0].lower() if row[0] else 'open'
        if status_key in kpi:
            kpi[status_key] = row[1]
        kpi['total'] += row[1]

    return jsonify({'kpi': kpi, 'items': items})


@api_bp.route('/supply-chain/rfq', methods=['POST'])
@login_required
def create_rfq():
    data = request.get_json()
    required = ['title', 'suppliers', 'items']
    for f in required:
        if not data.get(f):
            return jsonify({'error': f'Missing {f}'}), 400

    demo_industry = session.get('demo_industry', 'valve')
    rfq_id = f"RFQ-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    today = today_key()

    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO t_RFQ
            (RFQID, RFQNumber, Title, Description, Status, DueDateKey, ProjectID,
             CreatedDateKey, CreatedBy, DemoIndustryCode)
            VALUES (:id, :number, :title, :desc, 'Open', :due, :project,
                    :created, :user, :industry)
        """), {
            'id': rfq_id,
            'number': f"RFQ-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            'title': data['title'],
            'desc': data.get('description', ''),
            'due': data.get('dueDateKey', today + 30),
            'project': data.get('projectId'),
            'created': today,
            'user': current_user.get_id(),
            'industry': demo_industry
        })

        for item in data['items']:
            conn.execute(text("""
                INSERT INTO t_RFQItem
                (RFQID, ProductID, Quantity, Unit)
                VALUES (:rfq_id, :product, :qty, :unit)
            """), {
                'rfq_id': rfq_id,
                'product': item['productId'],
                'qty': item['quantity'],
                'unit': item.get('unit', 'EA')
            })

        for supplier_id in data['suppliers']:
            conn.execute(text("""
                INSERT INTO t_RFQResponse
                (RFQID, SupplierID, Status)
                VALUES (:rfq_id, :supplier, 'Pending')
            """), {
                'rfq_id': rfq_id,
                'supplier': supplier_id
            })

        conn.commit()

    return jsonify({'id': rfq_id, 'message': 'RFQ created'}), 201


@api_bp.route('/supply-chain/rfq/<rfq_id>/send', methods=['POST'])
@login_required
def send_rfq(rfq_id):
    data = request.get_json()
    suppliers = data.get('suppliers', [])
    if not suppliers:
        return jsonify({'error': 'No suppliers selected'}), 400

    engine = get_engine()
    with engine.connect() as conn:
        for supplier_id in suppliers:
            conn.execute(text("""
                UPDATE t_RFQResponse
                SET Status = 'Sent', SentDateKey = :date
                WHERE RFQID = :rfq_id AND SupplierID = :supplier
            """), {
                'date': today_key(),
                'rfq_id': rfq_id,
                'supplier': supplier_id
            })

        conn.execute(text("""
            UPDATE t_RFQ SET Status = 'Sent' WHERE RFQID = :id
        """), {'id': rfq_id})
        conn.commit()

    return jsonify({'message': 'RFQ sent to suppliers'})


@api_bp.route('/supply-chain/rfq/<rfq_id>/response', methods=['POST'])
@login_required
def record_rfq_response():
    data = request.get_json()
    required = ['supplierId', 'quoteAmount']
    for f in required:
        if not data.get(f):
            return jsonify({'error': f'Missing {f}'}), 400

    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("""
            UPDATE t_RFQResponse
            SET QuoteAmount = :amount, Currency = :currency,
                ValidityDateKey = :validity, Notes = :notes,
                Status = 'Received', ResponseDateKey = :date
            WHERE RFQID = :rfq_id AND SupplierID = :supplier
        """), {
            'amount': data['quoteAmount'],
            'currency': data.get('currency', 'USD'),
            'validity': data.get('validityDateKey'),
            'notes': data.get('notes', ''),
            'date': today_key(),
            'rfq_id': data.get('rfqId'),
            'supplier': data['supplierId']
        })

        pending = conn.execute(text("""
            SELECT COUNT(*) FROM t_RFQResponse
            WHERE RFQID = :rfq_id AND Status = 'Pending'
        """), {'rfq_id': data.get('rfqId')}).fetchone()[0]

        if pending == 0:
            conn.execute(text("""
                UPDATE t_RFQ SET Status = 'Received' WHERE RFQID = :id
            """), {'id': data.get('rfqId')})

        conn.commit()

    return jsonify({'message': 'Response recorded'})


@api_bp.route('/supply-chain/rfq/<rfq_id>/convert', methods=['POST'])
@login_required
def convert_rfq_to_po():
    data = request.get_json()
    rfq_id = data.get('rfqId')
    supplier_id = data.get('supplierId')
    if not rfq_id or not supplier_id:
        return jsonify({'error': 'RFQ ID and Supplier ID required'}), 400

    engine = get_engine()
    with engine.connect() as conn:
        rfq = conn.execute(text("""
            SELECT Title, ProjectID, DemoIndustryCode
            FROM t_RFQ WHERE RFQID = :id
        """), {'id': rfq_id}).fetchone()
        if not rfq:
            return jsonify({'error': 'RFQ not found'}), 404

        items = conn.execute(text("""
            SELECT ProductID, Quantity, Unit
            FROM t_RFQItem WHERE RFQID = :id
        """), {'id': rfq_id}).fetchall()

        quote = conn.execute(text("""
            SELECT QuoteAmount, Currency
            FROM t_RFQResponse
            WHERE RFQID = :rfq_id AND SupplierID = :supplier
        """), {'rfq_id': rfq_id, 'supplier': supplier_id}).fetchone()

        po_id = f"PO-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        today = today_key()

        conn.execute(text("""
            INSERT INTO t_PurchaseOrder
            (PurchaseOrderID, SupplierID, OrderDateKey, Status, CreatedBy,
             UpdatedDate, ProjectID, DemoIndustryCode)
            VALUES (:id, :supplier, :date, 'Draft', :user, :updated, :project, :industry)
        """), {
            'id': po_id,
            'supplier': supplier_id,
            'date': today,
            'user': current_user.get_id(),
            'updated': datetime.now().isoformat(),
            'project': rfq[1],
            'industry': rfq[2]
        })

        for item in items:
            conn.execute(text("""
                INSERT INTO t_PurchaseOrderDetail
                (PurchaseOrderID, ProductID, Quantity, UnitPrice, ReceivedQuantity)
                VALUES (:po_id, :product, :qty, :price, 0)
            """), {
                'po_id': po_id,
                'product': item[0],
                'qty': item[1],
                'price': quote[0] if quote else 0
            })

        conn.execute(text("""
            UPDATE t_RFQ SET Status = 'Converted' WHERE RFQID = :id
        """), {'id': rfq_id})

        conn.commit()

    return jsonify({'poId': po_id, 'message': 'PO created from RFQ'}), 201


@api_bp.route('/supply-chain/rfq/<rfq_id>/compare')
@login_required
def compare_rfq_quotes(rfq_id):
    engine = get_engine()
    with engine.connect() as conn:
        # ✅ Added rr.LeadTimeDays to the SELECT
        rows = conn.execute(text("""
            SELECT rr.SupplierID, s.SupplierName, rr.QuoteAmount, rr.Currency,
                   rr.ValidityDateKey, rr.Notes, rr.ResponseDateKey,
                   r.RFQNumber, r.Title, rr.LeadTimeDays
            FROM t_RFQResponse rr
            JOIN t_Suppliers s ON rr.SupplierID = s.SupplierID
            JOIN t_RFQ r ON rr.RFQID = r.RFQID
            WHERE rr.RFQID = :rfq_id AND rr.Status = 'Received'
        """), {'rfq_id': rfq_id}).fetchall()

    return jsonify([{
        'supplierId': r[0],
        'supplierName': r[1],
        'quoteAmount': float(r[2]) if r[2] else 0,
        'currency': r[3] or 'USD',
        'validityDateKey': r[4],
        'notes': r[5],
        'responseDateKey': r[6],
        'rfqNumber': r[7],
        'rfqTitle': r[8],
        # ✅ New: compare modal reads `leadTime`
        'leadTime': r[9] if r[9] is not None else 0
    } for r in rows])


@api_bp.route('/supply-chain/rfq/<rfq_id>')
@login_required
def get_rfq(rfq_id):
    """
    ✅ New endpoint — used by sendRFQModal().
    Returns the RFQ header, its suppliers (with status), and its items.
    """
    engine = get_engine()
    with engine.connect() as conn:
        rfq = conn.execute(text("""
            SELECT RFQID, RFQNumber, Title, Description, Status, DueDateKey, ProjectID
            FROM t_RFQ WHERE RFQID = :id
        """), {'id': rfq_id}).fetchone()
        if not rfq:
            return jsonify({'error': 'RFQ not found'}), 404

        suppliers = conn.execute(text("""
            SELECT rr.SupplierID, s.SupplierName
            FROM t_RFQResponse rr
            LEFT JOIN t_Suppliers s ON rr.SupplierID = s.SupplierID
            WHERE rr.RFQID = :id
        """), {'id': rfq_id}).fetchall()

        items = conn.execute(text("""
            SELECT ItemID, ProductID, Quantity, Unit
            FROM t_RFQItem WHERE RFQID = :id
        """), {'id': rfq_id}).fetchall()

    return jsonify({
        'id': rfq[0],
        'number': rfq[1],
        'title': rfq[2],
        'description': rfq[3],
        'status': rfq[4],
        'dueDateKey': rfq[5],
        'projectId': rfq[6],
        'suppliers': [{'id': s[0], 'name': s[1] or s[0]} for s in suppliers],
        'items': [{
            'itemId': i[0],
            'productId': i[1],
            'quantity': float(i[2]),
            'unit': i[3]
        } for i in items]
    })


@api_bp.route('/supply-chain/rfq/<rfq_id>/suppliers')
@login_required
def get_rfq_suppliers(rfq_id):
    """
    ✅ New endpoint — used by responseModal().
    Returns id / name / status per supplier for this RFQ.
    """
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT rr.SupplierID, s.SupplierName, rr.Status
            FROM t_RFQResponse rr
            LEFT JOIN t_Suppliers s ON rr.SupplierID = s.SupplierID
            WHERE rr.RFQID = :id
        """), {'id': rfq_id}).fetchall()
    return jsonify([
        {'id': r[0], 'name': r[1] or r[0], 'status': r[2]}
        for r in rows
    ])


# =============================================================================
# 3. PURCHASE ORDERS (PAGE 28)
# =============================================================================


@api_bp.route('/supply-chain/purchase-orders/list')
@login_required
def list_purchase_orders():
    from app.core.date_helpers import format_date

    demo_industry = session.get('demo_industry', 'valve')
    supplier = request.args.get('supplier', '')
    status = request.args.get('status', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    project = request.args.get('project', '')

    query = """
        SELECT po.PurchaseOrderID, po.SupplierID, s.SupplierName, po.OrderDateKey,
               po.Status, po.ProjectID, po.WorkflowStatus,
               STRING_AGG(pod.ProductID, ',') as products,
               SUM(TRY_CAST(pod.Quantity AS DECIMAL(18,4)) *
                   TRY_CAST(pod.UnitPrice AS DECIMAL(18,4))) as total_amount,
               MIN(pod.ExpectedDeliveryDateKey) as earliest_delivery
        FROM t_PurchaseOrder po
        LEFT JOIN t_Suppliers s ON po.SupplierID = s.SupplierID
        LEFT JOIN t_PurchaseOrderDetail pod ON po.PurchaseOrderID = pod.PurchaseOrderID
        WHERE po.DemoIndustryCode = :industry
    """
    params = {'industry': demo_industry}

    if supplier:
        query += " AND po.SupplierID = :supplier"
        params['supplier'] = supplier
    if status:
        query += " AND po.Status = :status"
        params['status'] = status
    if date_from:
        query += " AND po.OrderDateKey >= :date_from"
        params['date_from'] = int(date_from.replace('-', ''))
    if date_to:
        query += " AND po.OrderDateKey <= :date_to"
        params['date_to'] = int(date_to.replace('-', ''))
    if project:
        query += " AND po.ProjectID = :project"
        params['project'] = project

    query += """
        GROUP BY po.PurchaseOrderID, po.SupplierID, s.SupplierName,
                 po.OrderDateKey, po.Status, po.ProjectID, po.WorkflowStatus
        ORDER BY po.OrderDateKey DESC
    """

    engine = get_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(query), params).fetchall()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    items = []
    for r in rows:
        # Null-safe DateKey handling — the DB has NULLs here, don't crash format_date
        order_date_key = int(r[3]) if r[3] is not None else None
        expected_delivery_key = int(r[9]) if r[9] is not None else None

        items.append({
            'id': r[0],
            'supplierId': r[1],
            'supplierName': r[2] or r[1],
            'orderDateKey': order_date_key,
            'orderDateFormatted': format_date(order_date_key) if order_date_key else '',
            'status': r[4] or 'Draft',
            'projectId': r[5],
            'workflowStatus': r[6],
            'products': r[7].split(',') if r[7] else [],
            'totalAmount': float(r[8]) if r[8] else 0,
            'expectedDeliveryDateKey': expected_delivery_key,
            'expectedDeliveryFormatted': format_date(expected_delivery_key) if expected_delivery_key else ''
        })

    kpi = get_po_status_counts()
    return jsonify({'kpi': kpi, 'items': items})

# =============================================================================
# 4. INVENTORY STATUS (PAGE 29)
# =============================================================================

@api_bp.route('/supply-chain/inventory/list')
@login_required
def list_inventory():
    product = request.args.get('product', '')
    warehouse = request.args.get('warehouse', '')
    status_filter = request.args.get('status', '')

    demo_industry = session.get('demo_industry', 'valve')

    query = """
        SELECT
            ioh.ProductID,
            MAX(p.descEnglish) AS ProductName,
            ioh.WarehouseID,
            MAX(w.Name) AS WarehouseName,
            SUM(ioh.Qty) AS OnHand,
            AVG(ioh.UnitCost) AS UnitCost,
            MAX(ioh.Currency) AS Currency,
            MAX(ISNULL(p.SafetyStock, 0)) AS SafetyStock,
            MAX(ISNULL(p.MaxStock, 0)) AS MaxStock,
            MAX(p.ProductId) AS ProductCode
        FROM t_InventoryOnHand ioh
        JOIN t_Product p ON ioh.ProductID = p.ProductId
        LEFT JOIN t_Warehouses w ON ioh.WarehouseID = w.WarehouseID
        WHERE p.DemoIndustryCode = :industry
    """
    params = {'industry': demo_industry}

    if product:
        query += " AND (p.ProductId LIKE :prod OR p.descEnglish LIKE :prod)"
        params['prod'] = f'%{product}%'
    if warehouse:
        query += " AND ioh.WarehouseID = :wh"
        params['wh'] = warehouse

    query += " GROUP BY ioh.ProductID, ioh.WarehouseID"

    if status_filter == 'Low':
        query += " HAVING SUM(ioh.Qty) < MAX(ISNULL(p.SafetyStock, 0)) AND SUM(ioh.Qty) > 0"
    elif status_filter == 'Out':
        query += " HAVING SUM(ioh.Qty) = 0"
    elif status_filter == 'In Stock':
        query += " HAVING SUM(ioh.Qty) >= MAX(ISNULL(p.SafetyStock, 0))"

    query += " ORDER BY MAX(p.descEnglish), MAX(w.Name)"

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

    items = []
    for r in rows:
        on_hand = r[4] or 0
        safety = r[7] or 0
        max_stock = r[8] or 0

        if on_hand == 0:
            status = 'Out of Stock'
            status_color = 'danger'
        elif on_hand < safety:
            status = 'Low Stock'
            status_color = 'warning'
        elif max_stock > 0 and on_hand > max_stock:
            status = 'Over Stock'
            status_color = 'info'
        else:
            status = 'In Stock'
            status_color = 'success'

        items.append({
            'productId': r[0], 'productCode': r[9],
            'productName': r[1], 'warehouseId': r[2],
            'warehouseName': r[3] or r[2], 'onHand': on_hand,
            'unitCost': float(r[5]) if r[5] else 0,
            'currency': r[6] or 'USD', 'safetyStock': safety,
            'maxStock': max_stock, 'status': status,
            'statusColor': status_color
        })

    total_products = len(set(item['productId'] for item in items))
    low_stock = sum(1 for item in items if item['status'] == 'Low Stock')
    out_of_stock = sum(1 for item in items if item['status'] == 'Out of Stock')
    over_stock = sum(1 for item in items if item['status'] == 'Over Stock')

    return jsonify({
        'kpi': {
            'total_products': total_products,
            'low_stock': low_stock,
            'out_of_stock': out_of_stock,
            'over_stock': over_stock
        },
        'items': items
    })


@api_bp.route('/supply-chain/inventory/<product_id>/movements')
@login_required
def get_inventory_movements(product_id):
    warehouse = request.args.get('warehouse', '')

    query = """
        SELECT TOP 100 TransactionID, TransactionDateKey, TransactionType, ReferenceID,
               Qty, WarehouseID, CreatedBy, Notes
        FROM t_InventoryTransactions
        WHERE ProductID = :prod
    """
    params = {'prod': product_id}
    if warehouse:
        query += " AND WarehouseID = :wh"
        params['wh'] = warehouse
    query += " ORDER BY TransactionDateKey DESC"

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

    return jsonify([{
        'id': r[0], 'dateKey': r[1], 'type': r[2], 'reference': r[3],
        'qty': r[4], 'warehouseId': r[5], 'createdBy': r[6], 'notes': r[7]
    } for r in rows])


@api_bp.route('/supply-chain/inventory/adjust', methods=['POST'])
@login_required
def adjust_inventory():
    data = request.get_json()
    required = ['productId', 'warehouseId', 'quantity', 'reason']
    for f in required:
        if f not in data:
            return jsonify({'error': f'Missing {f}'}), 400
    return stock_adjust()


# =============================================================================
# 5. RECEIVING & SHIPPING (PAGE 30)
# =============================================================================

@api_bp.route('/supply-chain/receiving/list')
@login_required
def list_receiving_transactions():
    status = request.args.get('status', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')

    demo_industry = session.get('demo_industry', 'valve')

    query = """
        SELECT TOP 200
            t.TransactionID, t.TransactionDateKey, t.TransactionType,
            t.ReferenceID, t.ProductID, t.Qty, t.WarehouseID,
            t.CreatedBy, t.Notes,
            p.descEnglish AS ProductName,
            w.Name AS WarehouseName
        FROM t_InventoryTransactions t
        LEFT JOIN t_Product p ON t.ProductID = p.ProductId
        LEFT JOIN t_Warehouses w ON t.WarehouseID = w.WarehouseID
        WHERE t.TransactionType IN ('Purchase Receipt', 'Return Receipt')
          AND p.DemoIndustryCode = :industry
    """
    params = {'industry': demo_industry}

    if status:
        if status == 'Pending':
            query += " AND t.ReferenceID LIKE 'PO-%'"
        elif status == 'Completed':
            query += " AND t.ReferenceID NOT LIKE 'PO-%'"

    if date_from:
        query += " AND t.TransactionDateKey >= :date_from"
        params['date_from'] = int(date_from.replace('-', ''))
    if date_to:
        query += " AND t.TransactionDateKey <= :date_to"
        params['date_to'] = int(date_to.replace('-', ''))

    query += " ORDER BY t.TransactionDateKey DESC"

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

    items = [{
        'id': r[0], 'dateKey': r[1], 'type': r[2], 'reference': r[3],
        'productId': r[4], 'productName': r[9] or r[4],
        'qty': r[5], 'warehouseId': r[6],
        'warehouseName': r[10] or r[6],
        'createdBy': r[7], 'notes': r[8],
        'status': 'Completed' if r[2] in ('Purchase Receipt', 'Return Receipt') else 'Pending'
    } for r in rows]

    with engine.connect() as conn:
        pending = conn.execute(text("""
            SELECT COUNT(*) FROM t_QualityInspectionBatch WHERE Status = 'Pending'
        """)).fetchone()[0]

    return jsonify({
        'kpi': {
            'pending_receipts': len([i for i in items if i['status'] == 'Pending']),
            'pending_inspections': pending,
            'pending_shipments': 0
        },
        'items': items
    })


@api_bp.route('/supply-chain/receiving/open-pos')
@login_required
def get_open_pos_for_receiving():
    demo_industry = session.get('demo_industry', 'valve')

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT po.PurchaseOrderID, s.SupplierName, po.OrderDateKey
            FROM t_PurchaseOrder po
            LEFT JOIN t_Suppliers s ON po.SupplierID = s.SupplierID
            WHERE po.Status IN ('Approved', 'Ordered', 'Partially Received')
              AND po.DemoIndustryCode = :industry
            ORDER BY po.OrderDateKey DESC
        """), {'industry': demo_industry}).fetchall()

    return jsonify([{
        'id': r[0], 'supplierName': r[1] or r[0], 'orderDateKey': r[2]
    } for r in rows])


@api_bp.route('/supply-chain/receiving/kpi')
@login_required
def receiving_kpi():
    today = today_key()
    demo_industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    with engine.connect() as conn:
        expected_today = conn.execute(text("""
            SELECT COUNT(DISTINCT po.PurchaseOrderID)
            FROM t_PurchaseOrder po
            JOIN t_PurchaseOrderDetail pod ON po.PurchaseOrderID = pod.PurchaseOrderID
            WHERE pod.ExpectedDeliveryDateKey = :today
              AND ISNULL(pod.ReceivedQuantity, 0) < pod.Quantity
              AND po.DemoIndustryCode = :industry
        """), {'today': today, 'industry': demo_industry}).fetchone()[0]

        overdue = conn.execute(text("""
            SELECT COUNT(DISTINCT po.PurchaseOrderID)
            FROM t_PurchaseOrder po
            JOIN t_PurchaseOrderDetail pod ON po.PurchaseOrderID = pod.PurchaseOrderID
            WHERE pod.ExpectedDeliveryDateKey < :today
              AND ISNULL(pod.ReceivedQuantity, 0) < pod.Quantity
              AND po.DemoIndustryCode = :industry
        """), {'today': today, 'industry': demo_industry}).fetchone()[0]

        try:
            pending_inspection = conn.execute(text("""
                SELECT COUNT(*) FROM t_QualityInspectionBatch WHERE Status = 'Pending'
            """)).fetchone()[0]
        except Exception:
            pending_inspection = 0

    return jsonify({
        'expected_today': expected_today,
        'overdue': overdue,
        'pending_inspection': pending_inspection
    })


@api_bp.route('/supply-chain/receiving/expected-list')
@login_required
def receiving_expected_list():
    supplier = request.args.get('supplier', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    po_number = request.args.get('po_number', '')
    demo_industry = session.get('demo_industry', 'valve')

    query = """
        SELECT po.PurchaseOrderID,
               s.SupplierName,
               MIN(pod.ExpectedDeliveryDateKey) AS ExpectedDate,
               SUM(pod.Quantity - ISNULL(pod.ReceivedQuantity, 0)) AS OutstandingQty,
               po.Status
        FROM t_PurchaseOrder po
        JOIN t_PurchaseOrderDetail pod ON po.PurchaseOrderID = pod.PurchaseOrderID
        LEFT JOIN t_Suppliers s ON po.SupplierID = s.SupplierID
        WHERE ISNULL(pod.ReceivedQuantity, 0) < pod.Quantity
          AND po.DemoIndustryCode = :industry
    """
    params = {'industry': demo_industry}

    if supplier:
        query += " AND (s.SupplierName LIKE :supp OR po.SupplierID LIKE :supp)"
        params['supp'] = f'%{supplier}%'
    if date_from:
        query += " AND pod.ExpectedDeliveryDateKey >= :date_from"
        params['date_from'] = int(date_from.replace('-', ''))
    if date_to:
        query += " AND pod.ExpectedDeliveryDateKey <= :date_to"
        params['date_to'] = int(date_to.replace('-', ''))
    if po_number:
        query += " AND po.PurchaseOrderID LIKE :po"
        params['po'] = f'%{po_number}%'

    query += """
        GROUP BY po.PurchaseOrderID, s.SupplierName, po.Status
        ORDER BY MIN(pod.ExpectedDeliveryDateKey) ASC
    """

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

    return jsonify([{
        'poId': r[0], 'supplierName': r[1] or r[0],
        'expectedDateKey': r[2],
        'outstandingQty': float(r[3]) if r[3] else 0,
        'status': r[4] or 'Open'
    } for r in rows])


@api_bp.route('/supply-chain/receiving/po-details/<po_id>')
@login_required
def receiving_po_details(po_id):
    engine = get_engine()
    with engine.connect() as conn:
        po = conn.execute(text("""
            SELECT po.PurchaseOrderID, s.SupplierName, po.ProjectID
            FROM t_PurchaseOrder po
            LEFT JOIN t_Suppliers s ON po.SupplierID = s.SupplierID
            WHERE po.PurchaseOrderID = :id
        """), {'id': po_id}).fetchone()
        if not po:
            return jsonify({'error': 'PO not found'}), 404
        lines = conn.execute(text("""
            SELECT LineID, ProductID, Quantity, ISNULL(ReceivedQuantity, 0),
                   UnitPrice, ExpectedDeliveryDateKey
            FROM t_PurchaseOrderDetail
            WHERE PurchaseOrderID = :id AND ISNULL(ReceivedQuantity, 0) < Quantity
        """), {'id': po_id}).fetchall()

    return jsonify({
        'poId': po[0], 'supplierName': po[1], 'projectId': po[2],
        'lines': [{
            'lineId': l[0], 'productId': l[1],
            'orderedQty': float(l[2]), 'receivedQty': float(l[3]),
            'unitPrice': float(l[4]) if l[4] else 0,
            'expectedDateKey': l[5]
        } for l in lines]
    })


@api_bp.route('/supply-chain/receiving/receive', methods=['POST'])
@login_required
def receiving_receive():
    data = request.get_json()
    po_id = data.get('poId')
    receipts = data.get('receipts', [])
    warehouse = data.get('warehouseId', 'RAW')
    notes = data.get('notes', '')
    create_inspection = data.get('createInspection', False)
    project_id = data.get('projectId')

    if not po_id or not receipts:
        return jsonify({'error': 'poId and receipts required'}), 400

    engine = get_engine()
    today = today_key()
    batch_numbers = []
    new_status = None
    try:
        with engine.connect() as conn:
            for rec in receipts:
                line_id = rec['lineId']
                qty = float(rec['quantity'])
                line = conn.execute(text("""
                    SELECT ProductID, Quantity, ISNULL(ReceivedQuantity, 0)
                    FROM t_PurchaseOrderDetail WHERE LineID = :lid
                """), {'lid': line_id}).fetchone()
                if not line:
                    return jsonify({'error': f'Line {line_id} not found'}), 404
                product_id, ordered, received_so_far = line
                new_received = received_so_far + qty
                if new_received > ordered:
                    return jsonify({'error': f'Cannot receive more than ordered for line {line_id}'}), 400
                conn.execute(text("""
                    UPDATE t_PurchaseOrderDetail SET ReceivedQuantity = :q WHERE LineID = :lid
                """), {'q': new_received, 'lid': line_id})
                conn.execute(text("""
                    INSERT INTO t_InventoryTransactions
                    (ProductID, WarehouseID, TransactionDateKey, TransactionType,
                     ReferenceID, Qty, CreatedBy, ProjectID, Notes)
                    VALUES (:p, :w, :d, 'Purchase Receipt', :r, :q, :u, :proj, :notes)
                """), {
                    'p': product_id, 'w': warehouse, 'd': today,
                    'r': po_id, 'q': qty, 'u': current_user.get_id(),
                    'proj': project_id, 'notes': notes
                })
                if create_inspection:
                    batch_number = f"QC-{po_id}-{line_id}-{int(datetime.now().timestamp())}"
                    batch_numbers.append(batch_number)
                    conn.execute(text("""
                        INSERT INTO t_QualityInspectionBatch
                        (BatchNumber, SourceType, SourceID, ProductID, Quantity,
                         InspectionDateKey, Status, ProjectID, DueDateKey)
                        VALUES (:b, 'PurchaseOrder', :src, :p, :q, :d, 'Pending', :proj, :due)
                    """), {
                        'b': batch_number, 'src': po_id, 'p': product_id, 'q': qty,
                        'd': today, 'proj': project_id, 'due': today + 7
                    })

            all_lines = conn.execute(text("""
                SELECT Quantity, ISNULL(ReceivedQuantity, 0)
                FROM t_PurchaseOrderDetail WHERE PurchaseOrderID = :id
            """), {'id': po_id}).fetchall()
            tot_ordered = sum(l[0] for l in all_lines)
            tot_received = sum(l[1] for l in all_lines)
            new_status = 'Closed' if tot_received >= tot_ordered else ('Partially Received' if tot_received > 0 else 'Sent')
            conn.execute(text("UPDATE t_PurchaseOrder SET Status = :s WHERE PurchaseOrderID = :id"),
                         {'s': new_status, 'id': po_id})
            conn.commit()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    msg = f"Received {len(receipts)} line(s)."
    if batch_numbers:
        msg += f" Created inspections: {', '.join(batch_numbers)}"
    return jsonify({'message': msg, 'newStatus': new_status})


@api_bp.route('/supply-chain/receiving/upload-attachment', methods=['POST'])
@login_required
def receiving_upload_attachment():
    from werkzeug.utils import secure_filename

    po_id = request.form.get('poId')
    if not po_id:
        return jsonify({'error': 'poId required'}), 400
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'Empty filename'}), 400

    title = request.form.get('title', file.filename)

    upload_dir = os.path.join(
        current_app.root_path, '..', 'uploads', 'receiving', po_id
    )
    upload_dir = os.path.abspath(upload_dir)
    os.makedirs(upload_dir, exist_ok=True)

    safe_name = secure_filename(file.filename)
    file_path = os.path.join(upload_dir, safe_name)
    file.save(file_path)

    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO t_ReceivingAttachments
            (PurchaseOrderID, Title, FileName, FilePath, UploadedBy, UploadedAt)
            VALUES (:po, :title, :fname, :fpath, :user, CURRENT_TIMESTAMP)
        """), {
            'po': po_id, 'title': title, 'fname': safe_name,
            'fpath': file_path, 'user': current_user.get_id()
        })
        conn.commit()

    return jsonify({'message': 'Attachment uploaded', 'fileName': safe_name}), 201


@api_bp.route('/supply-chain/shipping/list')
@login_required
def list_shipping_transactions():
    status = request.args.get('status', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')

    demo_industry = session.get('demo_industry', 'valve')

    query = """
        SELECT s.ShipmentID, s.ShipmentNumber, s.SalesOrderID,
               s.ShipmentDateKey, s.Status, s.Carrier, s.TrackingNumber,
               c.CustomerName, so.ProjectID
        FROM t_Shipments s
        LEFT JOIN t_SalesOrder so ON s.SalesOrderID = so.SalesOrderID
        LEFT JOIN t_Customer c ON so.CustomerID = c.CustomerID
        WHERE so.DemoIndustryCode = :industry
    """
    params = {'industry': demo_industry}

    if status:
        query += " AND s.Status = :status"
        params['status'] = status
    if date_from:
        query += " AND s.ShipmentDateKey >= :date_from"
        params['date_from'] = int(date_from.replace('-', ''))
    if date_to:
        query += " AND s.ShipmentDateKey <= :date_to"
        params['date_to'] = int(date_to.replace('-', ''))

    query += " ORDER BY s.ShipmentDateKey DESC"

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

    items = [{
        'id': r[0], 'number': r[1], 'salesOrderId': r[2],
        'dateKey': r[3], 'status': r[4] or 'Pending',
        'carrier': r[5], 'trackingNumber': r[6],
        'customerName': r[7] or r[2], 'projectId': r[8]
    } for r in rows]

    pending_shipments = len([i for i in items if i['status'] in ['Draft', 'Packed']])

    return jsonify({
        'kpi': {'pending_shipments': pending_shipments},
        'items': items
    })


@api_bp.route('/supply-chain/shipping/kpi')
@login_required
def shipping_kpi():
    today = today_key()
    engine = get_engine()
    with engine.connect() as conn:
        today_count = conn.execute(text(
            "SELECT COUNT(*) FROM t_Shipments WHERE ShipmentDateKey = :t"
        ), {'t': today}).fetchone()[0]

        pending = conn.execute(text(
            "SELECT COUNT(*) FROM t_Shipments WHERE Status IN ('Draft', 'Packed')"
        )).fetchone()[0]

        carrier_rows = conn.execute(text(
            "SELECT Carrier, COUNT(*) FROM t_Shipments GROUP BY Carrier"
        )).fetchall()
        by_carrier = {(row[0] or 'Unknown'): row[1] for row in carrier_rows}

    return jsonify({'today': today_count, 'pending': pending, 'by_carrier': by_carrier})


@api_bp.route('/supply-chain/shipping/sales-orders-for-picking')
@login_required
def shipping_sales_orders_for_picking():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT so.SalesOrderID, c.CustomerName, sol.LineID, sol.ProductID,
                   sol.Quantity, ISNULL(sol.ShippedQuantity, 0) AS ShippedQty,
                   (sol.Quantity - ISNULL(sol.ShippedQuantity, 0)) AS Remaining
            FROM t_SalesOrder so
            JOIN t_SalesOrderLine sol ON so.SalesOrderID = sol.SalesOrderID
            LEFT JOIN t_Customer c ON so.CustomerID = c.CustomerID
            WHERE so.Status IN ('Confirmed', 'Released', 'Partially Shipped')
              AND sol.Quantity > ISNULL(sol.ShippedQuantity, 0)
            ORDER BY so.OrderDateKey, sol.LineID
        """)).fetchall()

    return jsonify([{
        'salesOrderId': r[0], 'customerName': r[1] or r[0],
        'lineId': r[2], 'productId': r[3],
        'orderedQty': float(r[4]), 'shippedSoFar': float(r[5]),
        'remainingQty': float(r[6])
    } for r in rows])


@api_bp.route('/supply-chain/shipping/create', methods=['POST'])
@login_required
def shipping_create():
    data = request.get_json()
    so_id = data.get('salesOrderId')
    carrier = data.get('carrier', '')
    ship_date = data.get('shipmentDateKey') or today_key()
    lines = data.get('lines', [])

    if not so_id or not lines:
        return jsonify({'error': 'Sales order and lines required'}), 400

    engine = get_engine()
    today = today_key()
    shipment_number = f"SHIP-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    try:
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO t_Shipments
                (ShipmentNumber, SalesOrderID, ShipmentDateKey, Status, Carrier,
                 CreatedDateKey, CreatedBy)
                VALUES (:n, :so, :d, 'Draft', :c, :cd, :u)
            """), {'n': shipment_number, 'so': so_id, 'd': ship_date,
                   'c': carrier, 'cd': today, 'u': current_user.get_id()})

            shipment_id = conn.execute(text("SELECT SCOPE_IDENTITY()")).fetchone()[0]

            for line in lines:
                conn.execute(text("""
                    INSERT INTO t_ShipmentLines
                    (ShipmentID, ProductID, QuantityShipped, SerialNumbers)
                    VALUES (:sid, :p, :q, :s)
                """), {'sid': shipment_id, 'p': line['productId'],
                       'q': line['quantityShipped'],
                       's': line.get('serialNumbers')})

                conn.execute(text("""
                    UPDATE t_SalesOrderLine
                    SET ShippedQuantity = ISNULL(ShippedQuantity, 0) + :q
                    WHERE LineID = :lid
                """), {'q': line['quantityShipped'], 'lid': line['lineId']})

                conn.execute(text("""
                    INSERT INTO t_InventoryTransactions
                    (ProductID, WarehouseID, TransactionDateKey, TransactionType,
                     ReferenceID, Qty, CreatedBy, Notes)
                    VALUES (:p, 'FG', :d, 'Sales Issue', :r, :q, :u, 'Shipment created')
                """), {'p': line['productId'], 'd': today, 'r': shipment_number,
                       'q': -line['quantityShipped'], 'u': current_user.get_id()})

            incomplete = conn.execute(text("""
                SELECT 1 FROM t_SalesOrderLine
                WHERE SalesOrderID = :id AND Quantity > ISNULL(ShippedQuantity, 0)
            """), {'id': so_id}).fetchone()
            if not incomplete:
                conn.execute(text("UPDATE t_SalesOrder SET Status = 'Shipped' WHERE SalesOrderID = :id"),
                             {'id': so_id})

            conn.commit()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    return jsonify({'shipmentId': shipment_id, 'shipmentNumber': shipment_number}), 201


@api_bp.route('/supply-chain/shipping/update-status/<int:shipment_id>', methods=['POST'])
@login_required
def shipping_update_status(shipment_id):
    data = request.get_json()
    status = data.get('status')
    tracking = data.get('trackingNumber', '')
    if not status:
        return jsonify({'error': 'Status required'}), 400

    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("""
            UPDATE t_Shipments
            SET Status = :s, TrackingNumber = COALESCE(:t, TrackingNumber)
            WHERE ShipmentID = :id
        """), {'s': status, 't': tracking or None, 'id': shipment_id})
        conn.commit()
    return jsonify({'message': f'Status updated to {status}'})


@api_bp.route('/supply-chain/shipping/export')
@login_required
def shipping_export():
    customer = request.args.get('customer', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    status = request.args.get('status', '')

    query = """
        SELECT s.ShipmentNumber, so.SalesOrderID, c.CustomerName,
               s.ShipmentDateKey, s.Status, s.Carrier, s.TrackingNumber
        FROM t_Shipments s
        JOIN t_SalesOrder so ON s.SalesOrderID = so.SalesOrderID
        LEFT JOIN t_Customer c ON so.CustomerID = c.CustomerID
        WHERE 1=1
    """
    params = {}
    if customer:
        query += " AND (c.CustomerName LIKE :c OR so.SalesOrderID LIKE :c)"
        params['c'] = f'%{customer}%'
    if date_from:
        query += " AND s.ShipmentDateKey >= :df"
        params['df'] = int(date_from.replace('-', ''))
    if date_to:
        query += " AND s.ShipmentDateKey <= :dt"
        params['dt'] = int(date_to.replace('-', ''))
    if status:
        query += " AND s.Status = :s"
        params['s'] = status
    query += " ORDER BY s.ShipmentDateKey DESC"

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['Shipment#', 'SalesOrder', 'Customer', 'Date', 'Status', 'Carrier', 'Tracking'])
    for r in rows:
        cw.writerow([r[0], r[1], r[2], r[3], r[4], r[5], r[6]])
    return current_app.response_class(
        si.getvalue(), mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=shipments.csv'}
    )


# =============================================================================
# 6. STOCK OVERVIEW
# =============================================================================

@api_bp.route('/supply-chain/warehouses/dropdown')
@login_required
def warehouses_dropdown():
    demo_industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT WarehouseID, Name
            FROM t_Warehouses
            WHERE DemoIndustryCode = :industry AND IsActive = 1
            ORDER BY Name
        """), {'industry': demo_industry}).fetchall()
    return jsonify([{'id': r[0], 'name': r[1]} for r in rows])


@api_bp.route('/supply-chain/products/dropdown')
@login_required
def products_dropdown():
    demo_industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT ProductId, descEnglish
            FROM t_Product
            WHERE DemoIndustryCode = :industry
            ORDER BY ProductId
        """), {'industry': demo_industry}).fetchall()
    return jsonify([{'id': r[0], 'desc': r[1] or r[0]} for r in rows])


@api_bp.route('/supply-chain/stock/kpi')
@login_required
def stock_kpi():
    demo_industry = session.get('demo_industry', 'valve')
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT
                ISNULL(SUM(TRY_CAST(ioh.Qty AS DECIMAL(18,4))), 0) AS total_qty,
                ISNULL(SUM(TRY_CAST(ioh.Qty AS DECIMAL(18,4)) *
                            TRY_CAST(ioh.UnitCost AS DECIMAL(18,4))), 0) AS total_value
            FROM t_InventoryOnHand ioh
            JOIN t_Product p ON ioh.ProductID = p.ProductId
            WHERE p.DemoIndustryCode = :industry
        """), {'industry': demo_industry}).fetchone()

        total_qty = float(row[0]) if row[0] is not None else 0
        total_value = float(row[1]) if row[1] is not None else 0

        low_stock_row = conn.execute(text("""
            SELECT COUNT(*) FROM (
                SELECT ioh.ProductID
                FROM t_InventoryOnHand ioh
                JOIN t_Product p ON ioh.ProductID = p.ProductId
                WHERE p.DemoIndustryCode = :industry
                GROUP BY ioh.ProductID, p.SafetyStock
                HAVING SUM(TRY_CAST(ioh.Qty AS DECIMAL(18,4))) < ISNULL(p.SafetyStock, 0)
                   AND SUM(TRY_CAST(ioh.Qty AS DECIMAL(18,4))) > 0
            ) AS low
        """), {'industry': demo_industry}).fetchone()
        low_stock = int(low_stock_row[0]) if low_stock_row else 0

    return jsonify({
        'total_items': total_qty,
        'total_value': total_value,
        'low_stock': low_stock
    })


@api_bp.route('/supply-chain/stock/list')
@login_required
def stock_list():
    warehouse = request.args.get('warehouse', '')
    category = request.args.get('category', '')
    low_stock = request.args.get('low_stock', '').lower() == 'true'

    demo_industry = session.get('demo_industry', 'valve')

    query = """
        SELECT
            ioh.ProductID,
            MAX(p.descEnglish) AS ProductName,
            ioh.WarehouseID,
            MAX(w.Name) AS WarehouseName,
            SUM(TRY_CAST(ioh.Qty AS DECIMAL(18,4))) AS OnHand,
            MAX(ioh.Currency) AS Currency,
            AVG(TRY_CAST(ioh.UnitCost AS DECIMAL(18,4))) AS UnitCost,
            MAX(ISNULL(p.SafetyStock, 0)) AS SafetyStock
        FROM t_InventoryOnHand ioh
        JOIN t_Product p ON ioh.ProductID = p.ProductId
        LEFT JOIN t_Warehouses w ON ioh.WarehouseID = w.WarehouseID
        WHERE p.DemoIndustryCode = :industry
    """
    params = {'industry': demo_industry}

    if warehouse:
        query += " AND ioh.WarehouseID = :wh"
        params['wh'] = warehouse
    if category:
        query += " AND (p.ProductId LIKE :cat OR p.descEnglish LIKE :cat)"
        params['cat'] = f'%{category}%'

    query += " GROUP BY ioh.ProductID, ioh.WarehouseID"

    if low_stock:
        query += " HAVING SUM(TRY_CAST(ioh.Qty AS DECIMAL(18,4))) < MAX(ISNULL(p.SafetyStock, 0)) AND SUM(TRY_CAST(ioh.Qty AS DECIMAL(18,4))) > 0"

    query += " ORDER BY MAX(p.descEnglish), MAX(w.Name)"

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

    items = []
    for r in rows:
        on_hand = float(r[4]) if r[4] is not None else 0
        safety = float(r[7]) if r[7] is not None else 0
        unit_cost = float(r[6]) if r[6] is not None else 0

        items.append({
            'productId': r[0], 'productName': r[1],
            'warehouseId': r[2], 'warehouseName': r[3] or r[2],
            'onHand': on_hand, 'reserved': 0, 'available': on_hand,
            'unitCost': round(unit_cost, 2),
            'currency': r[5] or 'USD',
            'lowStock': on_hand > 0 and on_hand < safety
        })

    return jsonify(items)


@api_bp.route('/supply-chain/stock-movements/kpi')
@login_required
def stock_movements_kpi():
    today_key_val = today_key()
    engine = get_engine()
    with engine.connect() as conn:
        today_count = conn.execute(
            text("SELECT COUNT(*) FROM t_InventoryTransactions WHERE TransactionDateKey = :today"),
            {'today': today_key_val}
        ).fetchone()[0]
        type_rows = conn.execute(
            text("SELECT TransactionType, COUNT(*) FROM t_InventoryTransactions GROUP BY TransactionType")
        ).fetchall()
        type_counts = {row[0]: row[1] for row in type_rows}
    return jsonify({'today': today_count, 'by_type': type_counts})


@api_bp.route('/supply-chain/stock-movements/list')
@login_required
def stock_movements_list():
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    product = request.args.get('product', '')
    trans_type = request.args.get('type', '')

    query = """
        SELECT t.TransactionID, t.ProductID, p.descEnglish AS ProductName,
               t.TransactionDateKey, t.TransactionType, t.ReferenceID,
               t.Qty, t.UnitCost, t.Currency, t.WarehouseID, w.Name AS WarehouseName,
               t.FromWarehouseID, t.ToWarehouseID, t.CreatedBy, t.Notes
        FROM t_InventoryTransactions t
        LEFT JOIN t_Product p ON t.ProductID = p.ProductId
        LEFT JOIN t_Warehouses w ON t.WarehouseID = w.WarehouseID
        WHERE 1=1
    """
    params = {}
    if date_from:
        query += " AND t.TransactionDateKey >= :date_from"
        params['date_from'] = int(date_from.replace('-', ''))
    if date_to:
        query += " AND t.TransactionDateKey <= :date_to"
        params['date_to'] = int(date_to.replace('-', ''))
    if product:
        query += " AND (t.ProductID LIKE :prod OR p.descEnglish LIKE :prod)"
        params['prod'] = f'%{product}%'
    if trans_type:
        query += " AND t.TransactionType = :type"
        params['type'] = trans_type
    query += " ORDER BY t.TransactionDateKey DESC"

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

    return jsonify([{
        'id': r[0], 'productId': r[1], 'productName': r[2],
        'dateKey': r[3], 'type': r[4], 'reference': r[5],
        'quantity': r[6], 'unitCost': float(r[7]) if r[7] else 0,
        'currency': r[8] or 'USD', 'warehouseId': r[9], 'warehouseName': r[10],
        'fromWarehouse': r[11], 'toWarehouse': r[12],
        'createdBy': r[13], 'notes': r[14]
    } for r in rows])


@api_bp.route('/supply-chain/stock-movements/export')
@login_required
def stock_movements_export():
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    product = request.args.get('product', '')
    trans_type = request.args.get('type', '')

    query = """
        SELECT t.TransactionDateKey, t.ProductID, p.descEnglish,
               t.TransactionType, t.ReferenceID, t.Qty, t.UnitCost, t.Currency,
               t.WarehouseID, t.Notes
        FROM t_InventoryTransactions t
        LEFT JOIN t_Product p ON t.ProductID = p.ProductId
        WHERE 1=1
    """
    params = {}
    if date_from:
        query += " AND t.TransactionDateKey >= :date_from"
        params['date_from'] = int(date_from.replace('-', ''))
    if date_to:
        query += " AND t.TransactionDateKey <= :date_to"
        params['date_to'] = int(date_to.replace('-', ''))
    if product:
        query += " AND (t.ProductID LIKE :prod OR p.descEnglish LIKE :prod)"
        params['prod'] = f'%{product}%'
    if trans_type:
        query += " AND t.TransactionType = :type"
        params['type'] = trans_type
    query += " ORDER BY t.TransactionDateKey DESC"

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['Date', 'ProductID', 'Product Name', 'Type', 'Reference',
                 'Quantity', 'Unit Cost', 'Currency', 'Warehouse', 'Notes'])
    for r in rows:
        cw.writerow([r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9]])
    return current_app.response_class(
        si.getvalue(), mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=stock_movements.csv'}
    )


@api_bp.route('/supply-chain/stock/adjust', methods=['POST'])
@login_required
def stock_adjust():
    data = request.get_json() or {}
    product_id = data.get('productId')
    warehouse_id = data.get('warehouseId')
    quantity = data.get('quantity')
    reason = data.get('reason')

    if not product_id or not warehouse_id or quantity is None or not reason:
        return jsonify({'error': 'productId, warehouseId, quantity, reason are required'}), 400

    try:
        quantity = float(quantity)
    except (TypeError, ValueError):
        return jsonify({'error': 'quantity must be numeric'}), 400

    demo_industry = session.get('demo_industry', 'valve')
    today = today_key()

    engine = get_engine()
    with engine.connect() as conn:
        prod = conn.execute(text("""
            SELECT 1 FROM t_Product
            WHERE ProductId = :pid AND DemoIndustryCode = :industry
        """), {'pid': product_id, 'industry': demo_industry}).fetchone()
        if not prod:
            return jsonify({'error': 'Product not found'}), 404

        wh = conn.execute(text("""
            SELECT 1 FROM t_Warehouses
            WHERE WarehouseID = :wid AND DemoIndustryCode = :industry
        """), {'wid': warehouse_id, 'industry': demo_industry}).fetchone()
        if not wh:
            return jsonify({'error': 'Warehouse not found'}), 404

        conn.execute(text("""
            INSERT INTO t_InventoryTransactions
            (ProductID, WarehouseID, Qty, TransactionType, ReferenceID,
             TransactionDateKey, CreatedBy, Notes)
            VALUES (:pid, :wid, :qty, 'Adjustment', :ref, :date, :user, :notes)
        """), {
            'pid': product_id, 'wid': warehouse_id, 'qty': quantity,
            'ref': f"ADJ-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            'date': today, 'user': current_user.get_id(), 'notes': reason
        })

        existing = conn.execute(text("""
            SELECT 1 FROM t_InventoryOnHand
            WHERE ProductID = :pid AND WarehouseID = :wid
        """), {'pid': product_id, 'wid': warehouse_id}).fetchone()

        if existing:
            conn.execute(text("""
                UPDATE t_InventoryOnHand
                SET Qty = TRY_CAST(Qty AS DECIMAL(18,4)) + :delta
                WHERE ProductID = :pid AND WarehouseID = :wid
            """), {'delta': quantity, 'pid': product_id, 'wid': warehouse_id})
        else:
            conn.execute(text("""
                INSERT INTO t_InventoryOnHand
                (ProductID, WarehouseID, Qty, UnitCost, Currency)
                VALUES (:pid, :wid, :qty, :cost, :currency)
            """), {
                'pid': product_id, 'wid': warehouse_id, 'qty': quantity,
                'cost': data.get('unitCost') or 0,
                'currency': data.get('currency') or 'USD'
            })

        conn.commit()

    return jsonify({'message': 'Stock adjusted'})


@api_bp.route('/supply-chain/stock/transfer', methods=['POST'])
@login_required
def stock_transfer():
    data = request.get_json() or {}
    product_id = data.get('productId')
    from_wh = data.get('fromWarehouse')
    to_wh = data.get('toWarehouse')
    quantity = data.get('quantity')
    reason = data.get('reason')

    if not product_id or not from_wh or not to_wh or quantity is None or not reason:
        return jsonify({'error': 'productId, fromWarehouse, toWarehouse, quantity, reason are required'}), 400
    if from_wh == to_wh:
        return jsonify({'error': 'Source and destination must differ'}), 400

    try:
        quantity = float(quantity)
    except (TypeError, ValueError):
        return jsonify({'error': 'quantity must be numeric'}), 400
    if quantity <= 0:
        return jsonify({'error': 'quantity must be positive'}), 400

    today = today_key()
    engine = get_engine()
    with engine.connect() as conn:
        src = conn.execute(text("""
            SELECT ISNULL(SUM(TRY_CAST(Qty AS DECIMAL(18,4))), 0)
            FROM t_InventoryOnHand
            WHERE ProductID = :pid AND WarehouseID = :wid
        """), {'pid': product_id, 'wid': from_wh}).fetchone()
        available = float(src[0]) if src and src[0] is not None else 0
        if available < quantity:
            return jsonify({'error': f'Insufficient stock: {available} available'}), 400

        ref = f"XFER-{datetime.now().strftime('%Y%m%d%H%M%S')}"

        conn.execute(text("""
            UPDATE t_InventoryOnHand
            SET Qty = TRY_CAST(Qty AS DECIMAL(18,4)) - :qty
            WHERE ProductID = :pid AND WarehouseID = :wid
        """), {'qty': quantity, 'pid': product_id, 'wid': from_wh})

        existing = conn.execute(text("""
            SELECT 1 FROM t_InventoryOnHand
            WHERE ProductID = :pid AND WarehouseID = :wid
        """), {'pid': product_id, 'wid': to_wh}).fetchone()
        if existing:
            conn.execute(text("""
                UPDATE t_InventoryOnHand
                SET Qty = TRY_CAST(Qty AS DECIMAL(18,4)) + :qty
                WHERE ProductID = :pid AND WarehouseID = :wid
            """), {'qty': quantity, 'pid': product_id, 'wid': to_wh})
        else:
            conn.execute(text("""
                INSERT INTO t_InventoryOnHand
                (ProductID, WarehouseID, Qty, UnitCost, Currency)
                VALUES (:pid, :wid, :qty, 0, 'USD')
            """), {'pid': product_id, 'wid': to_wh, 'qty': quantity})

        conn.execute(text("""
            INSERT INTO t_InventoryTransactions
            (ProductID, WarehouseID, Qty, TransactionType, ReferenceID,
             TransactionDateKey, CreatedBy, Notes)
            VALUES (:pid, :wid, :qty, 'Transfer Out', :ref, :date, :user, :notes)
        """), {'pid': product_id, 'wid': from_wh, 'qty': -quantity,
               'ref': ref, 'date': today, 'user': current_user.get_id(), 'notes': reason})

        conn.execute(text("""
            INSERT INTO t_InventoryTransactions
            (ProductID, WarehouseID, Qty, TransactionType, ReferenceID,
             TransactionDateKey, CreatedBy, Notes)
            VALUES (:pid, :wid, :qty, 'Transfer In', :ref, :date, :user, :notes)
        """), {'pid': product_id, 'wid': to_wh, 'qty': quantity,
               'ref': ref, 'date': today, 'user': current_user.get_id(), 'notes': reason})

        conn.commit()

    return jsonify({'message': 'Stock transferred'})


@api_bp.route('/supply-chain/stock/export')
@login_required
def stock_export():
    from flask import Response
    warehouse = request.args.get('warehouse', '')
    category = request.args.get('category', '')

    demo_industry = session.get('demo_industry', 'valve')
    query = """
        SELECT ioh.ProductID, MAX(p.descEnglish) AS ProductName,
               ioh.WarehouseID, MAX(w.Name) AS WarehouseName,
               SUM(TRY_CAST(ioh.Qty AS DECIMAL(18,4))) AS OnHand,
               MAX(ioh.Currency) AS Currency,
               AVG(TRY_CAST(ioh.UnitCost AS DECIMAL(18,4))) AS UnitCost
        FROM t_InventoryOnHand ioh
        JOIN t_Product p ON ioh.ProductID = p.ProductId
        LEFT JOIN t_Warehouses w ON ioh.WarehouseID = w.WarehouseID
        WHERE p.DemoIndustryCode = :industry
    """
    params = {'industry': demo_industry}
    if warehouse:
        query += " AND ioh.WarehouseID = :wh"
        params['wh'] = warehouse
    if category:
        query += " AND (p.ProductId LIKE :cat OR p.descEnglish LIKE :cat)"
        params['cat'] = f'%{category}%'
    query += " GROUP BY ioh.ProductID, ioh.WarehouseID ORDER BY MAX(p.descEnglish)"

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['ProductID', 'ProductName', 'WarehouseID', 'WarehouseName',
                 'OnHand', 'Currency', 'UnitCost'])
    for r in rows:
        cw.writerow([r[0], r[1], r[2], r[3], r[4], r[5], r[6]])

    return Response(
        si.getvalue(), mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=stock_overview.csv'}
    )


# =============================================================================
# 7. WAREHOUSE MANAGEMENT (PAGE 32)
# =============================================================================

@api_bp.route('/supply-chain/warehouses/list')
@login_required
def list_warehouses():
    """List warehouses with KPI - Page 32"""
    warehouse_type = request.args.get('type', '')
    is_active = request.args.get('active', '')

    query = """
        SELECT w.WarehouseID, w.Name, w.Location, w.Type, w.Capacity,
               w.IsActive, w.ContactPerson, w.Phone, w.Address
        FROM t_Warehouses w
        WHERE 1=1
    """
    params = {}

    if warehouse_type:
        query += " AND w.Type = :type"
        params['type'] = warehouse_type
    if is_active == '1':
        query += " AND w.IsActive = 1"
    elif is_active == '0':
        query += " AND w.IsActive = 0"

    query += " ORDER BY w.Name"

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

        items = []
        for r in rows:
            util = 0
            if r[4] and r[4] > 0:
                qty_sum = conn.execute(
                    text("SELECT ISNULL(SUM(Qty),0) FROM t_InventoryOnHand WHERE WarehouseID = :wh"),
                    {'wh': r[0]}
                ).fetchone()[0]
                util = round((qty_sum / r[4]) * 100, 1) if r[4] > 0 else 0

            items.append({
                'id': r[0],
                'name': r[1],
                'location': r[2],
                'type': r[3],
                'capacity': float(r[4]) if r[4] else 0,
                'isActive': bool(r[5]),
                'contactPerson': r[6],
                'phone': r[7],
                'email': '',
                'address': r[8],
                'utilisation': util
            })

    total = len(items)
    active = sum(1 for item in items if item['isActive'])
    inactive = total - active
    total_capacity = sum(item['capacity'] for item in items)
    avg_util = round(sum(item['utilisation'] for item in items) / total, 1) if total else 0

    return jsonify({
        'kpi': {
            'total': total,
            'active': active,
            'inactive': inactive,
            'total_capacity': total_capacity,
            'avg_utilisation': avg_util
        },
        'items': items
    })


@api_bp.route('/supply-chain/warehouses', methods=['POST'])
@login_required
def create_warehouse():
    data = request.get_json()
    required = ['warehouseId', 'name', 'type']
    for f in required:
        if not data.get(f):
            return jsonify({'error': f'Missing {f}'}), 400

    engine = get_engine()
    with engine.connect() as conn:
        existing = conn.execute(
            text("SELECT 1 FROM t_Warehouses WHERE WarehouseID = :id"),
            {'id': data['warehouseId']}
        ).fetchone()
        if existing:
            return jsonify({'error': 'Warehouse ID already exists'}), 400

        conn.execute(text("""
            INSERT INTO t_Warehouses
            (WarehouseID, Name, Location, Type, Capacity, IsActive,
             ContactPerson, Phone, Address)
            VALUES (:id, :name, :loc, :type, :cap, :active, :contact,
                    :phone, :addr)
        """), {
            'id': data['warehouseId'],
            'name': data['name'],
            'loc': data.get('location', ''),
            'type': data['type'],
            'cap': data.get('capacity', 0),
            'active': 1 if data.get('isActive', True) else 0,
            'contact': data.get('contactPerson', ''),
            'phone': data.get('phone', ''),
            'addr': data.get('address', '')
        })
        conn.commit()

    return jsonify({'message': 'Warehouse created'}), 201


@api_bp.route('/supply-chain/warehouses/<warehouse_id>', methods=['GET'])
@login_required
def get_warehouse(warehouse_id):
    engine = get_engine()
    with engine.connect() as conn:
        r = conn.execute(text("""
            SELECT WarehouseID, Name, Location, Type, Capacity,
                   IsActive, ContactPerson, Phone, Address
            FROM t_Warehouses WHERE WarehouseID = :id
        """), {'id': warehouse_id}).fetchone()
    if not r:
        return jsonify({'error': 'Warehouse not found'}), 404
    return jsonify({
        'id': r[0],
        'name': r[1],
        'location': r[2],
        'type': r[3],
        'capacity': float(r[4]) if r[4] else 0,
        'isActive': bool(r[5]),
        'contactPerson': r[6],
        'phone': r[7],
        'address': r[8]
    })


@api_bp.route('/supply-chain/warehouses/<warehouse_id>', methods=['PUT'])
@login_required
def update_warehouse(warehouse_id):
    data = request.get_json()
    engine = get_engine()
    with engine.connect() as conn:
        existing = conn.execute(
            text("SELECT 1 FROM t_Warehouses WHERE WarehouseID = :id"),
            {'id': warehouse_id}
        ).fetchone()
        if not existing:
            return jsonify({'error': 'Warehouse not found'}), 404

        conn.execute(text("""
            UPDATE t_Warehouses
            SET Name = :name, Location = :loc, Type = :type, Capacity = :cap,
                IsActive = :active, ContactPerson = :contact, Phone = :phone,
                Address = :addr
            WHERE WarehouseID = :id
        """), {
            'name': data.get('name', ''),
            'loc': data.get('location', ''),
            'type': data.get('type', ''),
            'cap': data.get('capacity', 0),
            'active': 1 if data.get('isActive', True) else 0,
            'contact': data.get('contactPerson', ''),
            'phone': data.get('phone', ''),
            'addr': data.get('address', ''),
            'id': warehouse_id
        })
        conn.commit()
    return jsonify({'message': 'Warehouse updated'})


@api_bp.route('/supply-chain/warehouses/<warehouse_id>', methods=['DELETE'])
@login_required
def delete_warehouse(warehouse_id):
    engine = get_engine()
    with engine.connect() as conn:
        inv_exists = conn.execute(
            text("SELECT TOP 1 1 FROM t_InventoryOnHand WHERE WarehouseID = :id"),
            {'id': warehouse_id}
        ).fetchone()
        if inv_exists:
            return jsonify({'error': 'Cannot delete warehouse with existing stock'}), 400

        conn.execute(
            text("UPDATE t_Warehouses SET IsActive = 0 WHERE WarehouseID = :id"),
            {'id': warehouse_id}
        )
        conn.commit()
    return jsonify({'message': 'Warehouse deactivated'})


@api_bp.route('/supply-chain/warehouses/types')
@login_required
def get_warehouse_types():
    return jsonify([
        {'value': 'Raw Materials', 'label': 'Raw Materials'},
        {'value': 'WIP', 'label': 'Work-in-Progress'},
        {'value': 'Finished Goods', 'label': 'Finished Goods'},
        {'value': 'Packaging', 'label': 'Packaging Materials'},
        {'value': 'Returns', 'label': 'Returns/Quality'},
        {'value': 'Consignment', 'label': 'Consignment/VMI'}
    ])


@api_bp.route('/supply-chain/warehouses/export')
@login_required
def export_warehouses_csv():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT WarehouseID, Name, Location, Type, Capacity, IsActive,
                   ContactPerson, Phone, Address
            FROM t_Warehouses
            ORDER BY Name
        """)).fetchall()

    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['WarehouseID', 'Name', 'Location', 'Type', 'Capacity', 'Status',
                 'Contact Person', 'Phone', 'Address'])
    for r in rows:
        cw.writerow([r[0], r[1], r[2], r[3], r[4], 'Active' if r[5] else 'Inactive',
                     r[6], r[7], r[8]])

    return current_app.response_class(
        si.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=warehouses.csv'}
    )


@api_bp.route('/supply-chain/warehouses/import', methods=['POST'])
@login_required
def warehouses_import():
    return jsonify({'error': 'Not implemented'}), 501


# =============================================================================
# 8. SUPPLIERS
# =============================================================================

@api_bp.route('/supply-chain/suppliers/list')
@login_required
def list_suppliers():
    category = request.args.get('category', '')
    rating_min = request.args.get('rating_min', '')
    rating_max = request.args.get('rating_max', '')
    status = request.args.get('status', '')

    query = """
        SELECT SupplierID, SupplierCode, SupplierName, SupplierType, Email,
               OverallRating, Status, ContactPerson, Phone,
               PaymentTerms, LeadTimeDays, Address, City, Country
        FROM t_Suppliers
        WHERE 1=1
    """
    params = {}

    if category:
        query += " AND SupplierType LIKE :category"
        params['category'] = f'%{category}%'
    if rating_min:
        query += " AND OverallRating >= :rating_min"
        params['rating_min'] = float(rating_min)
    if rating_max:
        query += " AND OverallRating <= :rating_max"
        params['rating_max'] = float(rating_max)
    if status:
        if status.lower() == 'active':
            query += " AND Status = 'Active'"
        elif status.lower() == 'inactive':
            query += " AND Status = 'Inactive'"

    query += " ORDER BY SupplierName"

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

        try:
            portal_count = conn.execute(text("""
                SELECT COUNT(*) FROM t_Users
                WHERE UserType = 'External' AND PortalAccess = 1
            """)).fetchone()[0]
        except Exception:
            portal_count = 0

    items = []
    ratings = []
    for r in rows:
        rating = float(r[5]) if r[5] is not None else 0
        ratings.append(rating)
        items.append({
            'id': r[0], 'code': r[1] or r[0], 'name': r[2],
            'category': r[3] or '', 'email': r[4] or '',
            'rating': rating, 'overallRating': rating,
            'status': r[6] or 'Active',
            'contactPerson': r[7] or '', 'phone': r[8] or '',
            'paymentTerms': r[9] or '', 'leadTimeDays': r[10] or 0,
            'address': r[11] or '', 'city': r[12] or '', 'country': r[13] or ''
        })

    avg_rating = round(sum(ratings) / len(ratings), 1) if ratings else 0

    return jsonify({
        'kpi': {
            'total': len(items),
            'activePortal': portal_count,
            'avgRating': avg_rating
        },
        'items': items
    })


@api_bp.route('/supply-chain/suppliers', methods=['POST'])
@login_required
def create_supplier():
    data = request.get_json()
    required = ['supplierCode', 'supplierName', 'supplierType', 'email']
    for f in required:
        if not data.get(f):
            return jsonify({'error': f'Missing {f}'}), 400

    demo_industry = session.get('demo_industry', 'valve')

    engine = get_engine()
    with engine.connect() as conn:
        existing = conn.execute(
            text("SELECT 1 FROM t_Suppliers WHERE SupplierID = :id"),
            {'id': data['supplierCode']}
        ).fetchone()
        if existing:
            return jsonify({'error': 'Supplier code already exists'}), 400

        conn.execute(text("""
            INSERT INTO t_Suppliers
            (SupplierID, SupplierName, SupplierType, Email, ContactPerson,
             Phone, PaymentTerms, LeadTimeDays, Address, City, Country,
             IsActive, OverallRating, DemoIndustryCode, CreatedDateKey)
            VALUES (:id, :name, :type, :email, :contact, :phone, :terms,
                    :lead, :addr, :city, :country, 1, 0, :industry, :created)
        """), {
            'id': data['supplierCode'],
            'name': data['supplierName'],
            'type': data['supplierType'],
            'email': data['email'],
            'contact': data.get('contactPerson', ''),
            'phone': data.get('phone', ''),
            'terms': data.get('paymentTerms', ''),
            'lead': data.get('leadTimeDays', 0),
            'addr': data.get('address', ''),
            'city': data.get('city', ''),
            'country': data.get('country', ''),
            'industry': demo_industry,
            'created': today_key()
        })
        conn.commit()

    return jsonify({'message': 'Supplier created'}), 201


@api_bp.route('/supply-chain/suppliers/<supplier_id>')
@login_required
def get_supplier(supplier_id):
    engine = get_engine()
    with engine.connect() as conn:
        r = conn.execute(text("""
            SELECT SupplierID, SupplierName, SupplierType, Email,
                   OverallRating, IsActive, ContactPerson, Phone,
                   PaymentTerms, LeadTimeDays, Address, City, Country
            FROM t_Suppliers WHERE SupplierID = :id
        """), {'id': supplier_id}).fetchone()

    if not r:
        return jsonify({'error': 'Supplier not found'}), 404

    return jsonify({
        'id': r[0], 'code': r[0], 'name': r[1],
        'category': r[2] or '', 'email': r[3] or '',
        'rating': float(r[4]) if r[4] is not None else 0,
        'status': 'Active' if r[5] else 'Inactive',
        'contactPerson': r[6] or '', 'phone': r[7] or '',
        'paymentTerms': r[8] or '', 'leadTimeDays': r[9] or 0,
        'address': r[10] or '', 'city': r[11] or '', 'country': r[12] or ''
    })


@api_bp.route('/supply-chain/suppliers/<supplier_id>/portal-user', methods=['POST'])
@login_required
def create_supplier_portal_user(supplier_id):
    data = request.get_json()
    required = ['username', 'fullName', 'email']
    for f in required:
        if not data.get(f):
            return jsonify({'error': f'Missing {f}'}), 400

    password = data.get('password', 'changeme123')

    try:
        from werkzeug.security import generate_password_hash
        pw_hash = generate_password_hash(password)
    except Exception:
        pw_hash = password

    engine = get_engine()
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM t_Suppliers WHERE SupplierID = :id"),
            {'id': supplier_id}
        ).fetchone()
        if not exists:
            return jsonify({'error': 'Supplier not found'}), 404

        dup = conn.execute(
            text("SELECT 1 FROM t_Users WHERE Username = :u"),
            {'u': data['username']}
        ).fetchone()
        if dup:
            return jsonify({'error': 'Username already exists'}), 400

        conn.execute(text("""
            INSERT INTO t_Users
            (Username, FullName, Email, PasswordHash, SupplierID,
             IsActive, CreatedAt)
            VALUES (:username, :fullname, :email, :pw, :supplier, 1, CURRENT_TIMESTAMP)
        """), {
            'username': data['username'], 'fullname': data['fullName'],
            'email': data['email'], 'pw': pw_hash, 'supplier': supplier_id
        })
        conn.commit()

    return jsonify({'message': 'Portal user created'}), 201


@api_bp.route('/supply-chain/suppliers/<supplier_id>/share-document', methods=['POST'])
@login_required
def share_supplier_document(supplier_id):
    from werkzeug.utils import secure_filename

    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'Empty filename'}), 400

    title = request.form.get('title', file.filename)

    upload_dir = os.path.join(
        current_app.root_path, '..', 'uploads', 'suppliers', supplier_id
    )
    upload_dir = os.path.abspath(upload_dir)
    os.makedirs(upload_dir, exist_ok=True)

    safe_name = secure_filename(file.filename)
    file_path = os.path.join(upload_dir, safe_name)
    file.save(file_path)

    engine = get_engine()
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM t_Suppliers WHERE SupplierID = :id"),
            {'id': supplier_id}
        ).fetchone()
        if not exists:
            return jsonify({'error': 'Supplier not found'}), 404

        conn.execute(text("""
            INSERT INTO t_SupplierDocuments
            (SupplierID, Title, FileName, FilePath, SharedBy, SharedAt)
            VALUES (:supplier, :title, :fname, :fpath, :user, CURRENT_TIMESTAMP)
        """), {
            'supplier': supplier_id, 'title': title, 'fname': safe_name,
            'fpath': file_path, 'user': current_user.get_id()
        })
        conn.commit()

    return jsonify({'message': 'Document shared', 'fileName': safe_name}), 201


@api_bp.route('/supply-chain/suppliers/<supplier_id>/documents')
@login_required
def list_supplier_documents(supplier_id):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT DocumentID, Title, FileName, SharedBy, SharedAt
            FROM t_SupplierDocuments
            WHERE SupplierID = :id
            ORDER BY SharedAt DESC
        """), {'id': supplier_id}).fetchall()

    return jsonify([{
        'id': r[0], 'title': r[1], 'fileName': r[2],
        'sharedBy': r[3], 'sharedAt': r[4].isoformat() if r[4] else None
    } for r in rows])


@api_bp.route('/supply-chain/suppliers/<supplier_id>/price-history')
@login_required
def get_supplier_price_history(supplier_id):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT po.PurchaseOrderID, po.OrderDateKey,
                   pod.ProductID,
                   TRY_CAST(pod.UnitPrice AS DECIMAL(18,4)) AS UnitPrice,
                   TRY_CAST(pod.Quantity AS DECIMAL(18,4)) AS Quantity
            FROM t_PurchaseOrder po
            JOIN t_PurchaseOrderDetail pod ON po.PurchaseOrderID = pod.PurchaseOrderID
            WHERE po.SupplierID = :id
            ORDER BY po.OrderDateKey DESC
        """), {'id': supplier_id}).fetchall()

    return jsonify([{
        'poId': r[0], 'orderDateKey': r[1], 'productId': r[2],
        'unitPrice': float(r[3]) if r[3] is not None else 0,
        'quantity': float(r[4]) if r[4] is not None else 0
    } for r in rows])


@api_bp.route('/supply-chain/suppliers/<supplier_id>/audit-log')
@login_required
def get_supplier_audit_log(supplier_id):
    engine = get_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT TOP 100 LogID, Action, EntityType, EntityID,
                       UserID, LogDate, Details
                FROM t_AuditLog
                WHERE EntityType = 'Supplier' AND EntityID = :id
                ORDER BY LogDate DESC
            """), {'id': supplier_id}).fetchall()
        return jsonify([{
            'id': r[0], 'action': r[1], 'entityType': r[2], 'entityId': r[3],
            'userId': r[4], 'logDate': r[5].isoformat() if r[5] else None,
            'details': r[6]
        } for r in rows])
    except Exception:
        return jsonify([])


# =============================================================================
# 9. CURRENT USER
# =============================================================================

@api_bp.route('/supply-chain/current-user')
@login_required
def get_current_user():
    return jsonify({
        'id': current_user.get_id(),
        'username': getattr(current_user, 'username', None),
        'fullName': getattr(current_user, 'fullname', None),
        'email': getattr(current_user, 'email', None),
        'demo_industry': session.get('demo_industry', 'valve')
    })


@api_bp.route('/current-user')
@login_required
def get_current_user_global():
    return jsonify({
        'id': current_user.get_id(),
        'username': getattr(current_user, 'username', None),
        'fullName': getattr(current_user, 'fullname', None),
        'email': getattr(current_user, 'email', None),
        'demo_industry': session.get('demo_industry', 'valve')
    })