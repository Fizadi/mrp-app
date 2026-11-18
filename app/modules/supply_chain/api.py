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
#   8. Suppliers (full management + portal user + documents)
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
import uuid


# =============================================================================
# 1. HELPERS
# =============================================================================

def get_engine():
    return current_app.extensions['sqlalchemy'].engine


def _new_id():
    return str(uuid.uuid4())


def today_key():
    return int(datetime.now().strftime('%Y%m%d'))


def _today_str():
    return str(today_key())


def _current_industry():
    return session.get('demo_industry', 'valve')


def get_po_status_counts():
    """KPI counts for the Purchase Orders page."""
    engine = get_engine()
    today = today_key()
    demo_industry = _current_industry()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT po.Status,
                   po.PurchaseOrderID,
                   SUM(TRY_CAST(pod.Quantity AS DECIMAL(18,4))) as total_qty,
                   SUM(TRY_CAST(pod.ReceivedQuantity AS DECIMAL(18,4))) as total_received,
                   MIN(TRY_CAST(pod.ExpectedDeliveryDateKey AS INT)) as min_delivery
            FROM t_PurchaseOrder po
            LEFT JOIN t_PurchaseOrderDetail pod ON po.PurchaseOrderID = pod.PurchaseOrderID
            WHERE po.DemoIndustryCode = :industry
            GROUP BY po.PurchaseOrderID, po.Status
        """), {'industry': demo_industry}).fetchall()

    counts = {'Draft': 0, 'Sent': 0, 'Partially Received': 0, 'Closed': 0, 'Overdue': 0}
    for row in rows:
        status = row[0] or 'Draft'
        total_qty = row[2] or 0
        total_received = row[3] or 0
        min_delivery = row[4]
        if total_qty > 0 and total_received >= total_qty:
            counts['Closed'] += 1
        elif total_received > 0 and total_received < total_qty:
            counts['Partially Received'] += 1
        elif min_delivery and min_delivery < today and status.lower() not in ['delivered', 'closed'] and total_received < total_qty:
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

    demo_industry = _current_industry()

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
        query += " AND TRY_CAST(r.CreatedDateKey AS INT) >= :date_from"
        params['date_from'] = int(date_from.replace('-', ''))
    if date_to:
        query += " AND TRY_CAST(r.CreatedDateKey AS INT) <= :date_to"
        params['date_to'] = int(date_to.replace('-', ''))
    if project and project != 'All':
        query += " AND r.ProjectID = :project"
        params['project'] = project

    query += " ORDER BY TRY_CAST(r.CreatedDateKey AS INT) DESC"

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

    items = [{
        'id': r._mapping['RFQID'],
        'number': r._mapping['RFQNumber'],
        'title': r._mapping['Title'],
        'status': r._mapping['Status'] or 'Open',
        'dueDateKey': r._mapping['DueDateKey'],
        'projectId': r._mapping['ProjectID'],
        'createdDateKey': r._mapping['CreatedDateKey'],
        'supplierCount': r._mapping['SupplierCount'] or 0,
        'minQuote': float(r._mapping['MinQuote']) if r._mapping['MinQuote'] else 0
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
        m = row._mapping
        status_key = (m['Status'] or 'open').lower()
        cnt = list(m.values())[1]
        if status_key in kpi:
            kpi[status_key] = cnt
        kpi['total'] += cnt

    return jsonify({'kpi': kpi, 'items': items})


@api_bp.route('/supply-chain/rfq', methods=['POST'])
@login_required
def create_rfq():
    data = request.get_json() or {}
    required = ['title', 'suppliers', 'items']
    for f in required:
        if not data.get(f):
            return jsonify({'error': f'Missing {f}'}), 400

    demo_industry = _current_industry()
    rfq_id = _new_id()
    rfq_number = f"RFQ-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    today = today_key()

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO t_RFQ
            (RFQID, RFQNumber, Title, Description, Status, DueDateKey, ProjectID,
             CreatedDateKey, CreatedBy, DemoIndustryCode)
            VALUES (:id, :number, :title, :desc, 'Open', :due, :project,
                    :created, :user, :industry)
        """), {
            'id': rfq_id,
            'number': rfq_number,
            'title': data['title'],
            'desc': data.get('description', ''),
            'due': int(data.get('dueDateKey', today + 30)),
            'project': data.get('projectId'),
            'created': today,
            'user': int(current_user.get_id()) if str(current_user.get_id()).isdigit() else None,
            'industry': demo_industry
        })

        for item in data['items']:
            # t_RFQItem.ItemID is INT IDENTITY — do not supply
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
            # t_RFQResponse.ResponseID is INT IDENTITY — do not supply
            conn.execute(text("""
                INSERT INTO t_RFQResponse
                (RFQID, SupplierID, Status)
                VALUES (:rfq_id, :supplier, 'Pending')
            """), {
                'rfq_id': rfq_id,
                'supplier': supplier_id
            })

    return jsonify({'id': rfq_id, 'message': 'RFQ created'}), 201


@api_bp.route('/supply-chain/rfq/<string:rfq_id>/send', methods=['POST'])
@login_required
def send_rfq(rfq_id):
    data = request.get_json() or {}
    suppliers = data.get('suppliers', [])
    if not suppliers:
        return jsonify({'error': 'No suppliers selected'}), 400

    engine = get_engine()
    with engine.begin() as conn:
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

    return jsonify({'message': 'RFQ sent to suppliers'})


@api_bp.route('/supply-chain/rfq/<string:rfq_id>/response', methods=['POST'])
@login_required
def record_rfq_response(rfq_id):
    data = request.get_json() or {}
    required = ['supplierId', 'quoteAmount']
    for f in required:
        if not data.get(f):
            return jsonify({'error': f'Missing {f}'}), 400

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE t_RFQResponse
            SET QuoteAmount = :amount, Currency = :currency,
                ValidityDateKey = :validity, Notes = :notes,
                Status = 'Received', ResponseDateKey = :date
            WHERE RFQID = :rfq_id AND SupplierID = :supplier
        """), {
            'amount': data['quoteAmount'],
            'currency': data.get('currency', 'USD'),
            'validity': int(data['validityDateKey']) if data.get('validityDateKey') else None,
            'notes': data.get('notes', ''),
            'date': today_key(),
            'rfq_id': rfq_id,
            'supplier': data['supplierId']
        })

        pending = conn.execute(text("""
            SELECT COUNT(*) FROM t_RFQResponse
            WHERE RFQID = :rfq_id AND Status = 'Pending'
        """), {'rfq_id': rfq_id}).scalar() or 0

        if pending == 0:
            conn.execute(text("""
                UPDATE t_RFQ SET Status = 'Received' WHERE RFQID = :id
            """), {'id': rfq_id})

    return jsonify({'message': 'Response recorded'})


@api_bp.route('/supply-chain/rfq/<string:rfq_id>/convert', methods=['POST'])
@login_required
def convert_rfq_to_po(rfq_id):
    data = request.get_json() or {}
    supplier_id = data.get('supplierId')
    if not rfq_id or not supplier_id:
        return jsonify({'error': 'RFQ ID and Supplier ID required'}), 400

    engine = get_engine()
    with engine.begin() as conn:
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

        po_id = _new_id()
        today = today_key()

        conn.execute(text("""
            INSERT INTO t_PurchaseOrder
            (PurchaseOrderID, SupplierID, OrderDateKey, Status, CreatedBy,
             UpdatedDate, ProjectID, DemoIndustryCode, RFQID)
            VALUES (:id, :supplier, :date, 'Draft', :user, :updated, :project, :industry, :rfqid)
        """), {
            'id': po_id,
            'supplier': supplier_id,
            'date': str(today),
            'user': current_user.get_id(),
            'updated': datetime.now().isoformat(),
            'project': rfq._mapping['ProjectID'],
            'industry': rfq._mapping['DemoIndustryCode'],
            'rfqid': rfq_id
        })

        for item in items:
            line_id = _new_id()
            conn.execute(text("""
                INSERT INTO t_PurchaseOrderDetail
                (LineID, PurchaseOrderID, ProductID, Quantity, UnitPrice, ReceivedQuantity)
                VALUES (:lid, :po_id, :product, :qty, :price, 0)
            """), {
                'lid': line_id,
                'po_id': po_id,
                'product': item._mapping['ProductID'],
                'qty': item._mapping['Quantity'],
                'price': quote._mapping['QuoteAmount'] if quote else 0
            })

        conn.execute(text("""
            UPDATE t_RFQ SET Status = 'Converted' WHERE RFQID = :id
        """), {'id': rfq_id})

    return jsonify({'poId': po_id, 'message': 'PO created from RFQ'}), 201


@api_bp.route('/supply-chain/rfq/<string:rfq_id>/compare')
@login_required
def compare_rfq_quotes(rfq_id):
    engine = get_engine()
    with engine.connect() as conn:
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
        'supplierId': r._mapping['SupplierID'],
        'supplierName': r._mapping['SupplierName'],
        'quoteAmount': float(r._mapping['QuoteAmount']) if r._mapping['QuoteAmount'] else 0,
        'currency': r._mapping['Currency'] or 'USD',
        'validityDateKey': r._mapping['ValidityDateKey'],
        'notes': r._mapping['Notes'],
        'responseDateKey': r._mapping['ResponseDateKey'],
        'rfqNumber': r._mapping['RFQNumber'],
        'rfqTitle': r._mapping['Title'],
        'leadTime': r._mapping['LeadTimeDays'] if r._mapping['LeadTimeDays'] is not None else 0
    } for r in rows])


@api_bp.route('/supply-chain/rfq/<string:rfq_id>')
@login_required
def get_rfq(rfq_id):
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

    rm = rfq._mapping
    return jsonify({
        'id': rm['RFQID'],
        'number': rm['RFQNumber'],
        'title': rm['Title'],
        'description': rm['Description'],
        'status': rm['Status'],
        'dueDateKey': rm['DueDateKey'],
        'projectId': rm['ProjectID'],
        'suppliers': [{'id': s._mapping['SupplierID'], 'name': s._mapping['SupplierName'] or s._mapping['SupplierID']} for s in suppliers],
        'items': [{
            'itemId': i._mapping['ItemID'],
            'productId': i._mapping['ProductID'],
            'quantity': float(i._mapping['Quantity']),
            'unit': i._mapping['Unit']
        } for i in items]
    })


@api_bp.route('/supply-chain/rfq/<string:rfq_id>/suppliers')
@login_required
def get_rfq_suppliers(rfq_id):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT rr.SupplierID, s.SupplierName, rr.Status
            FROM t_RFQResponse rr
            LEFT JOIN t_Suppliers s ON rr.SupplierID = s.SupplierID
            WHERE rr.RFQID = :id
        """), {'id': rfq_id}).fetchall()
    return jsonify([
        {'id': r._mapping['SupplierID'],
         'name': r._mapping['SupplierName'] or r._mapping['SupplierID'],
         'status': r._mapping['Status']}
        for r in rows
    ])


# =============================================================================
# 3. PURCHASE ORDERS (PAGE 28)
# =============================================================================

@api_bp.route('/supply-chain/purchase-orders/list')
@login_required
def list_purchase_orders():
    from app.core.date_helpers import format_date

    demo_industry = _current_industry()
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
        query += " AND TRY_CAST(po.OrderDateKey AS INT) >= :date_from"
        params['date_from'] = int(date_from.replace('-', ''))
    if date_to:
        query += " AND TRY_CAST(po.OrderDateKey AS INT) <= :date_to"
        params['date_to'] = int(date_to.replace('-', ''))
    if project:
        query += " AND po.ProjectID = :project"
        params['project'] = project

    query += """
        GROUP BY po.PurchaseOrderID, po.SupplierID, s.SupplierName,
                 po.OrderDateKey, po.Status, po.ProjectID, po.WorkflowStatus
        ORDER BY TRY_CAST(po.OrderDateKey AS INT) DESC
    """

    engine = get_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(query), params).fetchall()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    items = []
    for r in rows:
        m = r._mapping
        order_date_key = int(m['OrderDateKey']) if m['OrderDateKey'] is not None else None
        expected_delivery_key = int(m['earliest_delivery']) if m['earliest_delivery'] is not None else None

        items.append({
            'id': m['PurchaseOrderID'],
            'supplierId': m['SupplierID'],
            'supplierName': m['SupplierName'] or m['SupplierID'],
            'orderDateKey': order_date_key,
            'orderDateFormatted': format_date(order_date_key) if order_date_key else '',
            'status': m['Status'] or 'Draft',
            'projectId': m['ProjectID'],
            'workflowStatus': m['WorkflowStatus'],
            'products': (m['products'] or '').split(',') if m['products'] else [],
            'totalAmount': float(m['total_amount']) if m['total_amount'] else 0,
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

    demo_industry = _current_industry()

    query = """
        SELECT
            ioh.ProductID,
            MAX(p.descEnglish) AS ProductName,
            ioh.WarehouseID,
            MAX(w.Name) AS WarehouseName,
            SUM(ioh.Qty) AS OnHand,
            AVG(ioh.UnitCost) AS UnitCost,
            MAX(ioh.Currency) AS Currency,
            MAX(TRY_CAST(ISNULL(p.SafetyStock, 0) AS DECIMAL(18,4))) AS SafetyStock,
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
        query += " HAVING SUM(ioh.Qty) < MAX(TRY_CAST(ISNULL(p.SafetyStock, 0) AS DECIMAL(18,4))) AND SUM(ioh.Qty) > 0"
    elif status_filter == 'Out':
        query += " HAVING SUM(ioh.Qty) = 0"
    elif status_filter == 'In Stock':
        query += " HAVING SUM(ioh.Qty) >= MAX(TRY_CAST(ISNULL(p.SafetyStock, 0) AS DECIMAL(18,4)))"

    query += " ORDER BY MAX(p.descEnglish), MAX(w.Name)"

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

    items = []
    for r in rows:
        m = r._mapping
        on_hand = float(m['OnHand']) if m['OnHand'] is not None else 0
        safety = float(m['SafetyStock']) if m['SafetyStock'] is not None else 0

        if on_hand == 0:
            status = 'Out of Stock'
            status_color = 'danger'
        elif on_hand < safety:
            status = 'Low Stock'
            status_color = 'warning'
        else:
            status = 'In Stock'
            status_color = 'success'

        items.append({
            'productId': m['ProductID'],
            'productCode': m['ProductCode'],
            'productName': m['ProductName'],
            'warehouseId': m['WarehouseID'],
            'warehouseName': m['WarehouseName'] or m['WarehouseID'],
            'onHand': on_hand,
            'unitCost': float(m['UnitCost']) if m['UnitCost'] else 0,
            'currency': m['Currency'] or 'USD',
            'safetyStock': safety,
            'status': status,
            'statusColor': status_color
        })

    total_products = len(set(item['productId'] for item in items))
    low_stock = sum(1 for item in items if item['status'] == 'Low Stock')
    out_of_stock = sum(1 for item in items if item['status'] == 'Out of Stock')

    return jsonify({
        'kpi': {
            'total_products': total_products,
            'low_stock': low_stock,
            'out_of_stock': out_of_stock
        },
        'items': items
    })


@api_bp.route('/supply-chain/inventory/<string:product_id>/movements')
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
    query += " ORDER BY TRY_CAST(TransactionDateKey AS INT) DESC"

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

    return jsonify([{
        'id': r._mapping['TransactionID'],
        'dateKey': r._mapping['TransactionDateKey'],
        'type': r._mapping['TransactionType'],
        'reference': r._mapping['ReferenceID'],
        'qty': r._mapping['Qty'],
        'warehouseId': r._mapping['WarehouseID'],
        'createdBy': r._mapping['CreatedBy'],
        'notes': r._mapping['Notes']
    } for r in rows])


@api_bp.route('/supply-chain/inventory/adjust', methods=['POST'])
@login_required
def adjust_inventory():
    data = request.get_json() or {}
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

    demo_industry = _current_industry()

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
        query += " AND TRY_CAST(t.TransactionDateKey AS INT) >= :date_from"
        params['date_from'] = int(date_from.replace('-', ''))
    if date_to:
        query += " AND TRY_CAST(t.TransactionDateKey AS INT) <= :date_to"
        params['date_to'] = int(date_to.replace('-', ''))

    query += " ORDER BY TRY_CAST(t.TransactionDateKey AS INT) DESC"

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

    items = [{
        'id': r._mapping['TransactionID'],
        'dateKey': r._mapping['TransactionDateKey'],
        'type': r._mapping['TransactionType'],
        'reference': r._mapping['ReferenceID'],
        'productId': r._mapping['ProductID'],
        'productName': r._mapping['ProductName'] or r._mapping['ProductID'],
        'qty': r._mapping['Qty'],
        'warehouseId': r._mapping['WarehouseID'],
        'warehouseName': r._mapping['WarehouseName'] or r._mapping['WarehouseID'],
        'createdBy': r._mapping['CreatedBy'],
        'notes': r._mapping['Notes'],
        'status': 'Completed' if r._mapping['TransactionType'] in ('Purchase Receipt', 'Return Receipt') else 'Pending'
    } for r in rows]

    with engine.connect() as conn:
        pending = conn.execute(text("""
            SELECT COUNT(*) FROM t_QualityInspectionBatch WHERE Status = 'Pending'
        """)).scalar() or 0

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
    demo_industry = _current_industry()

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT po.PurchaseOrderID, s.SupplierName, po.OrderDateKey
            FROM t_PurchaseOrder po
            LEFT JOIN t_Suppliers s ON po.SupplierID = s.SupplierID
            WHERE po.Status IN ('Approved', 'Ordered', 'Partially Received')
              AND po.DemoIndustryCode = :industry
            ORDER BY TRY_CAST(po.OrderDateKey AS INT) DESC
        """), {'industry': demo_industry}).fetchall()

    return jsonify([{
        'id': r._mapping['PurchaseOrderID'],
        'supplierName': r._mapping['SupplierName'] or r._mapping['PurchaseOrderID'],
        'orderDateKey': r._mapping['OrderDateKey']
    } for r in rows])


@api_bp.route('/supply-chain/receiving/kpi')
@login_required
def receiving_kpi():
    today = today_key()
    demo_industry = _current_industry()
    engine = get_engine()
    with engine.connect() as conn:
        expected_today = conn.execute(text("""
            SELECT COUNT(DISTINCT po.PurchaseOrderID)
            FROM t_PurchaseOrder po
            JOIN t_PurchaseOrderDetail pod ON po.PurchaseOrderID = pod.PurchaseOrderID
            WHERE TRY_CAST(pod.ExpectedDeliveryDateKey AS INT) = :today
              AND ISNULL(TRY_CAST(pod.ReceivedQuantity AS DECIMAL(18,4)), 0) < TRY_CAST(pod.Quantity AS DECIMAL(18,4))
              AND po.DemoIndustryCode = :industry
        """), {'today': today, 'industry': demo_industry}).scalar() or 0

        overdue = conn.execute(text("""
            SELECT COUNT(DISTINCT po.PurchaseOrderID)
            FROM t_PurchaseOrder po
            JOIN t_PurchaseOrderDetail pod ON po.PurchaseOrderID = pod.PurchaseOrderID
            WHERE TRY_CAST(pod.ExpectedDeliveryDateKey AS INT) < :today
              AND ISNULL(TRY_CAST(pod.ReceivedQuantity AS DECIMAL(18,4)), 0) < TRY_CAST(pod.Quantity AS DECIMAL(18,4))
              AND po.DemoIndustryCode = :industry
        """), {'today': today, 'industry': demo_industry}).scalar() or 0

        pending_inspection = conn.execute(text("""
            SELECT COUNT(*) FROM t_QualityInspectionBatch WHERE Status = 'Pending'
        """)).scalar() or 0

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
    demo_industry = _current_industry()

    query = """
        SELECT po.PurchaseOrderID,
               s.SupplierName,
               MIN(TRY_CAST(pod.ExpectedDeliveryDateKey AS INT)) AS ExpectedDate,
               SUM(TRY_CAST(pod.Quantity AS DECIMAL(18,4)) - ISNULL(TRY_CAST(pod.ReceivedQuantity AS DECIMAL(18,4)), 0)) AS OutstandingQty,
               po.Status
        FROM t_PurchaseOrder po
        JOIN t_PurchaseOrderDetail pod ON po.PurchaseOrderID = pod.PurchaseOrderID
        LEFT JOIN t_Suppliers s ON po.SupplierID = s.SupplierID
        WHERE ISNULL(TRY_CAST(pod.ReceivedQuantity AS DECIMAL(18,4)), 0) < TRY_CAST(pod.Quantity AS DECIMAL(18,4))
          AND po.DemoIndustryCode = :industry
    """
    params = {'industry': demo_industry}

    if supplier:
        query += " AND (s.SupplierName LIKE :supp OR po.SupplierID LIKE :supp)"
        params['supp'] = f'%{supplier}%'
    if date_from:
        query += " AND TRY_CAST(pod.ExpectedDeliveryDateKey AS INT) >= :date_from"
        params['date_from'] = int(date_from.replace('-', ''))
    if date_to:
        query += " AND TRY_CAST(pod.ExpectedDeliveryDateKey AS INT) <= :date_to"
        params['date_to'] = int(date_to.replace('-', ''))
    if po_number:
        query += " AND po.PurchaseOrderID LIKE :po"
        params['po'] = f'%{po_number}%'

    query += """
        GROUP BY po.PurchaseOrderID, s.SupplierName, po.Status
        ORDER BY MIN(TRY_CAST(pod.ExpectedDeliveryDateKey AS INT)) ASC
    """

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

    return jsonify([{
        'poId': r._mapping['PurchaseOrderID'],
        'supplierName': r._mapping['SupplierName'] or r._mapping['PurchaseOrderID'],
        'expectedDateKey': r._mapping['ExpectedDate'],
        'outstandingQty': float(r._mapping['OutstandingQty']) if r._mapping['OutstandingQty'] else 0,
        'status': r._mapping['Status'] or 'Open'
    } for r in rows])


@api_bp.route('/supply-chain/receiving/po-details/<string:po_id>')
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
            SELECT LineID, ProductID, Quantity, ISNULL(ReceivedQuantity, 0) AS ReceivedQuantity,
                   UnitPrice, ExpectedDeliveryDateKey
            FROM t_PurchaseOrderDetail
            WHERE PurchaseOrderID = :id
              AND ISNULL(ReceivedQuantity, 0) < Quantity
        """), {'id': po_id}).fetchall()

    pm = po._mapping
    return jsonify({
        'poId': pm['PurchaseOrderID'],
        'supplierName': pm['SupplierName'],
        'projectId': pm['ProjectID'],
        'lines': [{
            'lineId': l._mapping['LineID'],
            'productId': l._mapping['ProductID'],
            'orderedQty': float(l._mapping['Quantity']),
            'receivedQty': float(l._mapping['ReceivedQuantity']),
            'unitPrice': float(l._mapping['UnitPrice']) if l._mapping['UnitPrice'] else 0,
            'expectedDateKey': l._mapping['ExpectedDeliveryDateKey']
        } for l in lines]
    })


@api_bp.route('/supply-chain/receiving/receive', methods=['POST'])
@login_required
def receiving_receive():
    data = request.get_json() or {}
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
        with engine.begin() as conn:
            for rec in receipts:
                line_id = rec['lineId']
                qty = float(rec['quantity'])
                line = conn.execute(text("""
                    SELECT ProductID, Quantity, ISNULL(ReceivedQuantity, 0) AS ReceivedQuantity
                    FROM t_PurchaseOrderDetail WHERE LineID = :lid
                """), {'lid': line_id}).fetchone()
                if not line:
                    return jsonify({'error': f'Line {line_id} not found'}), 404
                lm = line._mapping
                ordered = float(lm['Quantity'])
                received_so_far = float(lm['ReceivedQuantity'])
                new_received = received_so_far + qty
                if new_received > ordered:
                    return jsonify({'error': f'Cannot receive more than ordered for line {line_id}'}), 400
                conn.execute(text("""
                    UPDATE t_PurchaseOrderDetail SET ReceivedQuantity = :q WHERE LineID = :lid
                """), {'q': new_received, 'lid': line_id})

                conn.execute(text("""
                    INSERT INTO t_InventoryTransactions
                    (TransactionID, ProductID, WarehouseID, TransactionDateKey, TransactionType,
                     ReferenceID, Qty, CreatedBy, ProjectID, Notes)
                    VALUES (:tid, :p, :w, :d, 'Purchase Receipt', :r, :q, :u, :proj, :notes)
                """), {
                    'tid': _new_id(),
                    'p': lm['ProductID'], 'w': warehouse, 'd': str(today),
                    'r': po_id, 'q': str(qty), 'u': current_user.get_id(),
                    'proj': project_id, 'notes': notes
                })

                if create_inspection:
                    batch_number = f"QC-{po_id}-{line_id}-{int(datetime.now().timestamp())}"
                    batch_numbers.append(batch_number)
                    conn.execute(text("""
                        INSERT INTO t_QualityInspectionBatch
                        (BatchID, BatchNumber, SourceType, SourceID, ProductID, Quantity,
                         InspectionDateKey, Status, ProjectID, DueDateKey)
                        VALUES (:bid, :b, 'PurchaseOrder', :src, :p, :q, :d, 'Pending', :proj, :due)
                    """), {
                        'bid': _new_id(),
                        'b': batch_number, 'src': po_id, 'p': lm['ProductID'], 'q': str(qty),
                        'd': str(today), 'proj': project_id, 'due': str(today + 7)
                    })

            all_lines = conn.execute(text("""
                SELECT Quantity, ISNULL(ReceivedQuantity, 0) AS ReceivedQuantity
                FROM t_PurchaseOrderDetail WHERE PurchaseOrderID = :id
            """), {'id': po_id}).fetchall()
            tot_ordered = sum(float(l._mapping['Quantity']) for l in all_lines)
            tot_received = sum(float(l._mapping['ReceivedQuantity']) for l in all_lines)
            new_status = 'Closed' if tot_received >= tot_ordered else ('Partially Received' if tot_received > 0 else 'Sent')
            conn.execute(text("UPDATE t_PurchaseOrder SET Status = :s WHERE PurchaseOrderID = :id"),
                         {'s': new_status, 'id': po_id})
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
    with engine.begin() as conn:
        # t_ReceivingAttachments.AttachmentID is INT IDENTITY — do not supply
        conn.execute(text("""
            INSERT INTO t_ReceivingAttachments
            (PurchaseOrderID, Title, FileName, FilePath, UploadedBy, UploadedAt)
            VALUES (:po, :title, :fname, :fpath, :user, GETDATE())
        """), {
            'po': po_id, 'title': title, 'fname': safe_name,
            'fpath': file_path, 'user': current_user.get_id()
        })

    return jsonify({'message': 'Attachment uploaded', 'fileName': safe_name}), 201


@api_bp.route('/supply-chain/shipping/list')
@login_required
def list_shipping_transactions():
    status = request.args.get('status', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')

    demo_industry = _current_industry()

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
        query += " AND TRY_CAST(s.ShipmentDateKey AS INT) >= :date_from"
        params['date_from'] = int(date_from.replace('-', ''))
    if date_to:
        query += " AND TRY_CAST(s.ShipmentDateKey AS INT) <= :date_to"
        params['date_to'] = int(date_to.replace('-', ''))

    query += " ORDER BY TRY_CAST(s.ShipmentDateKey AS INT) DESC"

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

    items = [{
        'id': r._mapping['ShipmentID'],
        'number': r._mapping['ShipmentNumber'],
        'salesOrderId': r._mapping['SalesOrderID'],
        'dateKey': r._mapping['ShipmentDateKey'],
        'status': r._mapping['Status'] or 'Pending',
        'carrier': r._mapping['Carrier'],
        'trackingNumber': r._mapping['TrackingNumber'],
        'customerName': r._mapping['CustomerName'] or r._mapping['SalesOrderID'],
        'projectId': r._mapping['ProjectID']
    } for r in rows]

    pending_shipments = len([i for i in items if i['status'] in ['Draft', 'Packed']])

    return jsonify({
        'kpi': {'pending_shipments': pending_shipments},
        'items': items
    })


@api_bp.route('/supply-chain/shipping/kpi')
@login_required
def shipping_kpi():
    today = str(today_key())
    engine = get_engine()
    with engine.connect() as conn:
        today_count = conn.execute(text(
            "SELECT COUNT(*) FROM t_Shipments WHERE ShipmentDateKey = :t"
        ), {'t': today}).scalar() or 0

        pending = conn.execute(text(
            "SELECT COUNT(*) FROM t_Shipments WHERE Status IN ('Draft', 'Packed')"
        )).scalar() or 0

        carrier_rows = conn.execute(text(
            "SELECT Carrier, COUNT(*) AS cnt FROM t_Shipments GROUP BY Carrier"
        )).fetchall()
        by_carrier = {(row._mapping['Carrier'] or 'Unknown'): row._mapping['cnt'] for row in carrier_rows}

    return jsonify({'today': today_count, 'pending': pending, 'by_carrier': by_carrier})


@api_bp.route('/supply-chain/shipping/sales-orders-for-picking')
@login_required
def shipping_sales_orders_for_picking():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT so.SalesOrderID, c.CustomerName, sol.LineID, sol.ProductID,
                   TRY_CAST(sol.Quantity AS DECIMAL(18,4)) AS Quantity,
                   TRY_CAST(ISNULL(sol.ShippedQuantity, 0) AS DECIMAL(18,4)) AS ShippedQty,
                   (TRY_CAST(sol.Quantity AS DECIMAL(18,4)) - TRY_CAST(ISNULL(sol.ShippedQuantity, 0) AS DECIMAL(18,4))) AS Remaining
            FROM t_SalesOrder so
            JOIN t_SalesOrderLine sol ON so.SalesOrderID = sol.SalesOrderID
            LEFT JOIN t_Customer c ON so.CustomerID = c.CustomerID
            WHERE so.Status IN ('Confirmed', 'Released', 'Partially Shipped')
              AND TRY_CAST(sol.Quantity AS DECIMAL(18,4)) > TRY_CAST(ISNULL(sol.ShippedQuantity, 0) AS DECIMAL(18,4))
            ORDER BY TRY_CAST(so.OrderDateKey AS INT), sol.LineID
        """)).fetchall()

    return jsonify([{
        'salesOrderId': r._mapping['SalesOrderID'],
        'customerName': r._mapping['CustomerName'] or r._mapping['SalesOrderID'],
        'lineId': r._mapping['LineID'],
        'productId': r._mapping['ProductID'],
        'orderedQty': float(r._mapping['Quantity']),
        'shippedSoFar': float(r._mapping['ShippedQty']),
        'remainingQty': float(r._mapping['Remaining'])
    } for r in rows])


@api_bp.route('/supply-chain/shipping/create', methods=['POST'])
@login_required
def shipping_create():
    data = request.get_json() or {}
    so_id = data.get('salesOrderId')
    carrier = data.get('carrier', '')
    ship_date = data.get('shipmentDateKey') or today_key()
    lines = data.get('lines', [])

    if not so_id or not lines:
        return jsonify({'error': 'Sales order and lines required'}), 400

    engine = get_engine()
    today = today_key()
    shipment_number = f"SHIP-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    shipment_id = _new_id()

    try:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO t_Shipments
                (ShipmentID, ShipmentNumber, SalesOrderID, ShipmentDateKey, Status, Carrier,
                 CreatedDateKey, CreatedBy)
                VALUES (:sid, :n, :so, :d, 'Draft', :c, :cd, :u)
            """), {
                'sid': shipment_id, 'n': shipment_number, 'so': so_id,
                'd': str(ship_date), 'c': carrier, 'cd': str(today),
                'u': current_user.get_id()
            })

            for line in lines:
                conn.execute(text("""
                    INSERT INTO t_ShipmentLines
                    (LineID, ShipmentID, ProductID, QuantityShipped, SerialNumbers)
                    VALUES (:lid, :sid, :p, :q, :s)
                """), {
                    'lid': _new_id(),
                    'sid': shipment_id,
                    'p': line['productId'],
                    'q': str(line['quantityShipped']),
                    's': line.get('serialNumbers')
                })

                conn.execute(text("""
                    UPDATE t_SalesOrderLine
                    SET ShippedQuantity = TRY_CAST(ISNULL(ShippedQuantity, 0) AS DECIMAL(18,4)) + :q
                    WHERE LineID = :lid
                """), {'q': float(line['quantityShipped']), 'lid': line['lineId']})

                conn.execute(text("""
                    INSERT INTO t_InventoryTransactions
                    (TransactionID, ProductID, WarehouseID, TransactionDateKey, TransactionType,
                     ReferenceID, Qty, CreatedBy, Notes)
                    VALUES (:tid, :p, 'FG', :d, 'Sales Issue', :r, :q, :u, 'Shipment created')
                """), {
                    'tid': _new_id(),
                    'p': line['productId'],
                    'd': str(today),
                    'r': shipment_number,
                    'q': str(-float(line['quantityShipped'])),
                    'u': current_user.get_id()
                })

            incomplete = conn.execute(text("""
                SELECT 1 FROM t_SalesOrderLine
                WHERE SalesOrderID = :id
                  AND TRY_CAST(Quantity AS DECIMAL(18,4)) > TRY_CAST(ISNULL(ShippedQuantity, 0) AS DECIMAL(18,4))
            """), {'id': so_id}).fetchone()
            if not incomplete:
                conn.execute(text("UPDATE t_SalesOrder SET Status = 'Shipped' WHERE SalesOrderID = :id"),
                             {'id': so_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    return jsonify({'shipmentId': shipment_id, 'shipmentNumber': shipment_number}), 201


@api_bp.route('/supply-chain/shipping/update-status/<string:shipment_id>', methods=['POST'])
@login_required
def shipping_update_status(shipment_id):
    data = request.get_json() or {}
    status = data.get('status')
    tracking = data.get('trackingNumber', '')
    if not status:
        return jsonify({'error': 'Status required'}), 400

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE t_Shipments
            SET Status = :s, TrackingNumber = COALESCE(:t, TrackingNumber)
            WHERE ShipmentID = :id
        """), {'s': status, 't': tracking or None, 'id': shipment_id})

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
        query += " AND TRY_CAST(s.ShipmentDateKey AS INT) >= :df"
        params['df'] = int(date_from.replace('-', ''))
    if date_to:
        query += " AND TRY_CAST(s.ShipmentDateKey AS INT) <= :dt"
        params['dt'] = int(date_to.replace('-', ''))
    if status:
        query += " AND s.Status = :s"
        params['s'] = status
    query += " ORDER BY TRY_CAST(s.ShipmentDateKey AS INT) DESC"

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['Shipment#', 'SalesOrder', 'Customer', 'Date', 'Status', 'Carrier', 'Tracking'])
    for r in rows:
        m = r._mapping
        cw.writerow([m['ShipmentNumber'], m['SalesOrderID'], m['CustomerName'],
                     m['ShipmentDateKey'], m['Status'], m['Carrier'], m['TrackingNumber']])
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
    demo_industry = _current_industry()
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT WarehouseID, Name
            FROM t_Warehouses
            WHERE DemoIndustryCode = :industry AND IsActive = '1'
            ORDER BY Name
        """), {'industry': demo_industry}).fetchall()
    return jsonify([{'id': r._mapping['WarehouseID'], 'name': r._mapping['Name']} for r in rows])


@api_bp.route('/supply-chain/products/dropdown')
@login_required
def products_dropdown():
    demo_industry = _current_industry()
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT ProductId, descEnglish
            FROM t_Product
            WHERE DemoIndustryCode = :industry
            ORDER BY ProductId
        """), {'industry': demo_industry}).fetchall()
    return jsonify([{'id': r._mapping['ProductId'], 'desc': r._mapping['descEnglish'] or r._mapping['ProductId']} for r in rows])


@api_bp.route('/supply-chain/stock/kpi')
@login_required
def stock_kpi():
    demo_industry = _current_industry()
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

        total_qty = float(row._mapping['total_qty']) if row._mapping['total_qty'] is not None else 0
        total_value = float(row._mapping['total_value']) if row._mapping['total_value'] is not None else 0

        low_stock = conn.execute(text("""
            SELECT COUNT(*) FROM (
                SELECT ioh.ProductID
                FROM t_InventoryOnHand ioh
                JOIN t_Product p ON ioh.ProductID = p.ProductId
                WHERE p.DemoIndustryCode = :industry
                GROUP BY ioh.ProductID, p.SafetyStock
                HAVING SUM(TRY_CAST(ioh.Qty AS DECIMAL(18,4))) < TRY_CAST(ISNULL(p.SafetyStock, 0) AS DECIMAL(18,4))
                   AND SUM(TRY_CAST(ioh.Qty AS DECIMAL(18,4))) > 0
            ) AS low
        """), {'industry': demo_industry}).scalar() or 0

    return jsonify({
        'total_items': total_qty,
        'total_value': total_value,
        'low_stock': int(low_stock)
    })


@api_bp.route('/supply-chain/stock/list')
@login_required
def stock_list():
    warehouse = request.args.get('warehouse', '')
    category = request.args.get('category', '')
    low_stock = request.args.get('low_stock', '').lower() == 'true'

    demo_industry = _current_industry()

    query = """
        SELECT
            ioh.ProductID,
            MAX(p.descEnglish) AS ProductName,
            ioh.WarehouseID,
            MAX(w.Name) AS WarehouseName,
            SUM(TRY_CAST(ioh.Qty AS DECIMAL(18,4))) AS OnHand,
            MAX(ioh.Currency) AS Currency,
            AVG(TRY_CAST(ioh.UnitCost AS DECIMAL(18,4))) AS UnitCost,
            MAX(TRY_CAST(ISNULL(p.SafetyStock, 0) AS DECIMAL(18,4))) AS SafetyStock
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
        query += " HAVING SUM(TRY_CAST(ioh.Qty AS DECIMAL(18,4))) < MAX(TRY_CAST(ISNULL(p.SafetyStock, 0) AS DECIMAL(18,4))) AND SUM(TRY_CAST(ioh.Qty AS DECIMAL(18,4))) > 0"

    query += " ORDER BY MAX(p.descEnglish), MAX(w.Name)"

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

    items = []
    for r in rows:
        m = r._mapping
        on_hand = float(m['OnHand']) if m['OnHand'] is not None else 0
        safety = float(m['SafetyStock']) if m['SafetyStock'] is not None else 0
        unit_cost = float(m['UnitCost']) if m['UnitCost'] is not None else 0

        items.append({
            'productId': m['ProductID'],
            'productName': m['ProductName'],
            'warehouseId': m['WarehouseID'],
            'warehouseName': m['WarehouseName'] or m['WarehouseID'],
            'onHand': on_hand,
            'reserved': 0,
            'available': on_hand,
            'unitCost': round(unit_cost, 2),
            'currency': m['Currency'] or 'USD',
            'lowStock': on_hand > 0 and on_hand < safety
        })

    return jsonify(items)


@api_bp.route('/supply-chain/stock-movements/kpi')
@login_required
def stock_movements_kpi():
    today_key_val = str(today_key())
    engine = get_engine()
    with engine.connect() as conn:
        today_count = conn.execute(
            text("SELECT COUNT(*) FROM t_InventoryTransactions WHERE TransactionDateKey = :today"),
            {'today': today_key_val}
        ).scalar() or 0
        type_rows = conn.execute(
            text("SELECT TransactionType, COUNT(*) AS cnt FROM t_InventoryTransactions GROUP BY TransactionType")
        ).fetchall()
        type_counts = {row._mapping['TransactionType']: row._mapping['cnt'] for row in type_rows}
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
        query += " AND TRY_CAST(t.TransactionDateKey AS INT) >= :date_from"
        params['date_from'] = int(date_from.replace('-', ''))
    if date_to:
        query += " AND TRY_CAST(t.TransactionDateKey AS INT) <= :date_to"
        params['date_to'] = int(date_to.replace('-', ''))
    if product:
        query += " AND (t.ProductID LIKE :prod OR p.descEnglish LIKE :prod)"
        params['prod'] = f'%{product}%'
    if trans_type:
        query += " AND t.TransactionType = :type"
        params['type'] = trans_type
    query += " ORDER BY TRY_CAST(t.TransactionDateKey AS INT) DESC"

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

    return jsonify([{
        'id': r._mapping['TransactionID'],
        'productId': r._mapping['ProductID'],
        'productName': r._mapping['ProductName'],
        'dateKey': r._mapping['TransactionDateKey'],
        'type': r._mapping['TransactionType'],
        'reference': r._mapping['ReferenceID'],
        'quantity': r._mapping['Qty'],
        'unitCost': float(r._mapping['UnitCost']) if r._mapping['UnitCost'] else 0,
        'currency': r._mapping['Currency'] or 'USD',
        'warehouseId': r._mapping['WarehouseID'],
        'warehouseName': r._mapping['WarehouseName'],
        'fromWarehouse': r._mapping['FromWarehouseID'],
        'toWarehouse': r._mapping['ToWarehouseID'],
        'createdBy': r._mapping['CreatedBy'],
        'notes': r._mapping['Notes']
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
        query += " AND TRY_CAST(t.TransactionDateKey AS INT) >= :date_from"
        params['date_from'] = int(date_from.replace('-', ''))
    if date_to:
        query += " AND TRY_CAST(t.TransactionDateKey AS INT) <= :date_to"
        params['date_to'] = int(date_to.replace('-', ''))
    if product:
        query += " AND (t.ProductID LIKE :prod OR p.descEnglish LIKE :prod)"
        params['prod'] = f'%{product}%'
    if trans_type:
        query += " AND t.TransactionType = :type"
        params['type'] = trans_type
    query += " ORDER BY TRY_CAST(t.TransactionDateKey AS INT) DESC"

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['Date', 'ProductID', 'Product Name', 'Type', 'Reference',
                 'Quantity', 'Unit Cost', 'Currency', 'Warehouse', 'Notes'])
    for r in rows:
        m = r._mapping
        cw.writerow([m['TransactionDateKey'], m['ProductID'], m['descEnglish'],
                     m['TransactionType'], m['ReferenceID'], m['Qty'], m['UnitCost'],
                     m['Currency'], m['WarehouseID'], m['Notes']])
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

    demo_industry = _current_industry()
    today = today_key()

    engine = get_engine()
    with engine.begin() as conn:
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
            (TransactionID, ProductID, WarehouseID, Qty, TransactionType, ReferenceID,
             TransactionDateKey, CreatedBy, Notes)
            VALUES (:tid, :pid, :wid, :qty, 'Adjustment', :ref, :date, :user, :notes)
        """), {
            'tid': _new_id(),
            'pid': product_id, 'wid': warehouse_id, 'qty': str(quantity),
            'ref': f"ADJ-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            'date': str(today), 'user': current_user.get_id(), 'notes': reason
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
    with engine.begin() as conn:
        src = conn.execute(text("""
            SELECT ISNULL(SUM(TRY_CAST(Qty AS DECIMAL(18,4))), 0) AS available
            FROM t_InventoryOnHand
            WHERE ProductID = :pid AND WarehouseID = :wid
        """), {'pid': product_id, 'wid': from_wh}).fetchone()
        available = float(src._mapping['available']) if src and src._mapping['available'] is not None else 0
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
            (TransactionID, ProductID, WarehouseID, Qty, TransactionType, ReferenceID,
             TransactionDateKey, CreatedBy, Notes)
            VALUES (:tid, :pid, :wid, :qty, 'Transfer Out', :ref, :date, :user, :notes)
        """), {
            'tid': _new_id(), 'pid': product_id, 'wid': from_wh,
            'qty': str(-quantity), 'ref': ref, 'date': str(today),
            'user': current_user.get_id(), 'notes': reason
        })

        conn.execute(text("""
            INSERT INTO t_InventoryTransactions
            (TransactionID, ProductID, WarehouseID, Qty, TransactionType, ReferenceID,
             TransactionDateKey, CreatedBy, Notes)
            VALUES (:tid, :pid, :wid, :qty, 'Transfer In', :ref, :date, :user, :notes)
        """), {
            'tid': _new_id(), 'pid': product_id, 'wid': to_wh,
            'qty': str(quantity), 'ref': ref, 'date': str(today),
            'user': current_user.get_id(), 'notes': reason
        })

    return jsonify({'message': 'Stock transferred'})


@api_bp.route('/supply-chain/stock/export')
@login_required
def stock_export():
    from flask import Response
    warehouse = request.args.get('warehouse', '')
    category = request.args.get('category', '')

    demo_industry = _current_industry()
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
        m = r._mapping
        cw.writerow([m['ProductID'], m['ProductName'], m['WarehouseID'], m['WarehouseName'],
                     m['OnHand'], m['Currency'], m['UnitCost']])

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
        query += " AND w.IsActive = '1'"
    elif is_active == '0':
        query += " AND w.IsActive = '0'"

    query += " ORDER BY w.Name"

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

        items = []
        for r in rows:
            m = r._mapping
            cap = float(m['Capacity']) if m['Capacity'] is not None else 0
            util = 0
            if cap > 0:
                qty_sum = conn.execute(
                    text("SELECT ISNULL(SUM(TRY_CAST(Qty AS DECIMAL(18,4))),0) FROM t_InventoryOnHand WHERE WarehouseID = :wh"),
                    {'wh': m['WarehouseID']}
                ).scalar() or 0
                util = round((float(qty_sum) / cap) * 100, 1)

            items.append({
                'id': m['WarehouseID'],
                'name': m['Name'],
                'location': m['Location'],
                'type': m['Type'],
                'capacity': cap,
                'isActive': str(m['IsActive']) == '1' if m['IsActive'] is not None else False,
                'contactPerson': m['ContactPerson'],
                'phone': m['Phone'],
                'email': '',
                'address': m['Address'],
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
    data = request.get_json() or {}
    required = ['warehouseId', 'name', 'type']
    for f in required:
        if not data.get(f):
            return jsonify({'error': f'Missing {f}'}), 400

    engine = get_engine()
    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT 1 FROM t_Warehouses WHERE WarehouseID = :id"),
            {'id': data['warehouseId']}
        ).fetchone()
        if existing:
            return jsonify({'error': 'Warehouse ID already exists'}), 400

        conn.execute(text("""
            INSERT INTO t_Warehouses
            (WarehouseID, Name, Location, Type, Capacity, IsActive,
             ContactPerson, Phone, Address, DemoIndustryCode)
            VALUES (:id, :name, :loc, :type, :cap, :active, :contact,
                    :phone, :addr, :industry)
        """), {
            'id': data['warehouseId'],
            'name': data['name'],
            'loc': data.get('location', ''),
            'type': data['type'],
            'cap': str(data.get('capacity', 0)),
            'active': '1' if data.get('isActive', True) else '0',
            'contact': data.get('contactPerson', ''),
            'phone': data.get('phone', ''),
            'addr': data.get('address', ''),
            'industry': _current_industry()
        })

    return jsonify({'message': 'Warehouse created'}), 201


@api_bp.route('/supply-chain/warehouses/<string:warehouse_id>', methods=['GET'])
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
    m = r._mapping
    return jsonify({
        'id': m['WarehouseID'],
        'name': m['Name'],
        'location': m['Location'],
        'type': m['Type'],
        'capacity': float(m['Capacity']) if m['Capacity'] is not None else 0,
        'isActive': str(m['IsActive']) == '1' if m['IsActive'] is not None else False,
        'contactPerson': m['ContactPerson'],
        'phone': m['Phone'],
        'address': m['Address']
    })


@api_bp.route('/supply-chain/warehouses/<string:warehouse_id>', methods=['PUT'])
@login_required
def update_warehouse(warehouse_id):
    data = request.get_json() or {}
    engine = get_engine()
    with engine.begin() as conn:
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
            'cap': str(data.get('capacity', 0)),
            'active': '1' if data.get('isActive', True) else '0',
            'contact': data.get('contactPerson', ''),
            'phone': data.get('phone', ''),
            'addr': data.get('address', ''),
            'id': warehouse_id
        })
    return jsonify({'message': 'Warehouse updated'})


@api_bp.route('/supply-chain/warehouses/<string:warehouse_id>', methods=['DELETE'])
@login_required
def delete_warehouse(warehouse_id):
    engine = get_engine()
    with engine.begin() as conn:
        inv_exists = conn.execute(
            text("SELECT TOP 1 1 FROM t_InventoryOnHand WHERE WarehouseID = :id"),
            {'id': warehouse_id}
        ).fetchone()
        if inv_exists:
            return jsonify({'error': 'Cannot delete warehouse with existing stock'}), 400

        conn.execute(
            text("UPDATE t_Warehouses SET IsActive = '0' WHERE WarehouseID = :id"),
            {'id': warehouse_id}
        )
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
        m = r._mapping
        cw.writerow([m['WarehouseID'], m['Name'], m['Location'], m['Type'], m['Capacity'],
                     'Active' if str(m['IsActive']) == '1' else 'Inactive',
                     m['ContactPerson'], m['Phone'], m['Address']])

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
        query += " AND TRY_CAST(OverallRating AS DECIMAL(18,4)) >= :rating_min"
        params['rating_min'] = float(rating_min)
    if rating_max:
        query += " AND TRY_CAST(OverallRating AS DECIMAL(18,4)) <= :rating_max"
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

        portal_count = conn.execute(text("""
            SELECT COUNT(*) FROM t_Users
            WHERE UserType = 'External' AND PortalAccess = '1'
        """)).scalar() or 0

    items = []
    ratings = []
    for r in rows:
        m = r._mapping
        rating = float(m['OverallRating']) if m['OverallRating'] is not None else 0
        ratings.append(rating)
        items.append({
            'id': m['SupplierID'],
            'code': m['SupplierCode'] or m['SupplierID'],
            'name': m['SupplierName'],
            'category': m['SupplierType'] or '',
            'email': m['Email'] or '',
            'rating': rating,
            'overallRating': rating,
            'status': m['Status'] or 'Active',
            'contactPerson': m['ContactPerson'] or '',
            'phone': m['Phone'] or '',
            'paymentTerms': m['PaymentTerms'] or '',
            'leadTimeDays': m['LeadTimeDays'] or 0,
            'address': m['Address'] or '',
            'city': m['City'] or '',
            'country': m['Country'] or ''
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
    data = request.get_json() or {}
    required = ['supplierCode', 'supplierName', 'supplierType', 'email']
    for f in required:
        if not data.get(f):
            return jsonify({'error': f'Missing {f}'}), 400

    demo_industry = _current_industry()

    engine = get_engine()
    with engine.begin() as conn:
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
             Status, OverallRating, DemoIndustryCode, CreatedDate)
            VALUES (:id, :name, :type, :email, :contact, :phone, :terms,
                    :lead, :addr, :city, :country, 'Active', '0', :industry, :created)
        """), {
            'id': data['supplierCode'],
            'name': data['supplierName'],
            'type': data['supplierType'],
            'email': data['email'],
            'contact': data.get('contactPerson', ''),
            'phone': data.get('phone', ''),
            'terms': data.get('paymentTerms', ''),
            'lead': str(data.get('leadTimeDays', 0)),
            'addr': data.get('address', ''),
            'city': data.get('city', ''),
            'country': data.get('country', ''),
            'industry': demo_industry,
            'created': datetime.now().isoformat()
        })

    return jsonify({'message': 'Supplier created'}), 201


@api_bp.route('/supply-chain/suppliers/<string:supplier_id>')
@login_required
def get_supplier(supplier_id):
    engine = get_engine()
    with engine.connect() as conn:
        r = conn.execute(text("""
            SELECT SupplierID, SupplierName, SupplierType, Email,
                   OverallRating, Status, ContactPerson, Phone,
                   PaymentTerms, LeadTimeDays, Address, City, Country
            FROM t_Suppliers WHERE SupplierID = :id
        """), {'id': supplier_id}).fetchone()

    if not r:
        return jsonify({'error': 'Supplier not found'}), 404
    m = r._mapping
    return jsonify({
        'id': m['SupplierID'],
        'code': m['SupplierID'],
        'name': m['SupplierName'],
        'category': m['SupplierType'] or '',
        'email': m['Email'] or '',
        'rating': float(m['OverallRating']) if m['OverallRating'] is not None else 0,
        'status': m['Status'] or 'Active',
        'contactPerson': m['ContactPerson'] or '',
        'phone': m['Phone'] or '',
        'paymentTerms': m['PaymentTerms'] or '',
        'leadTimeDays': m['LeadTimeDays'] or 0,
        'address': m['Address'] or '',
        'city': m['City'] or '',
        'country': m['Country'] or ''
    })


@api_bp.route('/supply-chain/suppliers/<string:supplier_id>/portal-user', methods=['POST'])
@login_required
def create_supplier_portal_user(supplier_id):
    data = request.get_json() or {}
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
    with engine.begin() as conn:
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

        new_id = _new_id()
        conn.execute(text("""
            INSERT INTO t_Users
            (UserID, Username, FullName, Email, PasswordHash, CompanyID,
             UserType, IsActive, CreatedDate)
            VALUES (:uid, :username, :fullname, :email, :pw, :supplier,
                    'External', '1', :created)
        """), {
            'uid': new_id,
            'username': data['username'],
            'fullname': data['fullName'],
            'email': data['email'],
            'pw': pw_hash,
            'supplier': supplier_id,
            'created': datetime.now().isoformat()
        })

    return jsonify({'message': 'Portal user created', 'UserID': new_id}), 201


@api_bp.route('/supply-chain/suppliers/<string:supplier_id>/share-document', methods=['POST'])
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
    with engine.begin() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM t_Suppliers WHERE SupplierID = :id"),
            {'id': supplier_id}
        ).fetchone()
        if not exists:
            return jsonify({'error': 'Supplier not found'}), 404

        conn.execute(text("""
            INSERT INTO t_SupplierDocuments
            (SupplierID, Title, FileName, FilePath, SharedBy, SharedAt)
            VALUES (:supplier, :title, :fname, :fpath, :user, GETDATE())
        """), {
            'supplier': supplier_id, 'title': title, 'fname': safe_name,
            'fpath': file_path, 'user': current_user.get_id()
        })

    return jsonify({'message': 'Document shared', 'fileName': safe_name}), 201


@api_bp.route('/supply-chain/suppliers/<string:supplier_id>/documents')
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
        'id': r._mapping['DocumentID'],
        'title': r._mapping['Title'],
        'fileName': r._mapping['FileName'],
        'sharedBy': r._mapping['SharedBy'],
        'sharedAt': r._mapping['SharedAt'].isoformat() if r._mapping['SharedAt'] else None
    } for r in rows])


@api_bp.route('/supply-chain/suppliers/<string:supplier_id>/price-history')
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
            ORDER BY TRY_CAST(po.OrderDateKey AS INT) DESC
        """), {'id': supplier_id}).fetchall()

    return jsonify([{
        'poId': r._mapping['PurchaseOrderID'],
        'orderDateKey': r._mapping['OrderDateKey'],
        'productId': r._mapping['ProductID'],
        'unitPrice': float(r._mapping['UnitPrice']) if r._mapping['UnitPrice'] is not None else 0,
        'quantity': float(r._mapping['Quantity']) if r._mapping['Quantity'] is not None else 0
    } for r in rows])


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
        'demo_industry': _current_industry()
    })


@api_bp.route('/current-user')
@login_required
def get_current_user_global():
    return jsonify({
        'id': current_user.get_id(),
        'username': getattr(current_user, 'username', None),
        'fullName': getattr(current_user, 'fullname', None),
        'email': getattr(current_user, 'email', None),
        'demo_industry': _current_industry()
    })