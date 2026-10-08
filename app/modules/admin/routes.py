from flask import render_template, session, abort
from . import mrp_bp
from app.core.auth import login_required
from app.core.database import get_db_connection
from datetime import datetime
from .helpers import get_mrp_summary   # if needed for run details

@mrp_bp.route('/dashboard')
@login_required
def dashboard():
    lang = session.get('lang', 'en')
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    conn = get_db_connection()
    # Dates for dropdown
    dates = conn.execute('''
        SELECT DateKey, GregorianDate, PersianStr
        FROM DimDate
        WHERE DateKey >= 20250101 AND DateKey <= 20251231
        ORDER BY DateKey
        LIMIT 100
    ''').fetchall()

    # Demand sources
    sales_orders = conn.execute('''
        SELECT so.*, p.descEnglish
        FROM t_SalesOrder so
        LEFT JOIN t_Product p ON so.ProductID = p.ProductId
        WHERE so.Status IN ('Pending', 'Confirmed', 'Processing')
        ORDER BY so.DeliveryDateKey
    ''').fetchall()

    forecasts = conn.execute('''
        SELECT f.*, p.descEnglish
        FROM t_Forecasts f
        LEFT JOIN t_Product p ON f.ProductId = p.ProductId
        WHERE f.DateKey BETWEEN 20250101 AND 20251231
        ORDER BY f.DateKey
    ''').fetchall()

    # Supply sources
    purchase_orders = conn.execute('''
        SELECT po.*, p.descEnglish
        FROM t_PurchaseOrder po
        LEFT JOIN t_Product p ON po.ProductID = p.ProductId
        WHERE po.Status IN ('Pending', 'Confirmed')
        ORDER BY po.DeliveryDateKey
    ''').fetchall()

    production_orders = conn.execute('''
        SELECT po.*, p.descEnglish
        FROM t_ProductionOrder po
        LEFT JOIN t_Product p ON po.ProductId = p.ProductId
        WHERE po.Status IN ('Planned', 'In Progress')
        ORDER BY po.EndDateKey
    ''').fetchall()

    inventory_items = conn.execute('''
        SELECT 
            ProductID,
            SUM(Qty) as OnHandQty,
            MAX(GregorianDate) as LastUpdated,
            GROUP_CONCAT(WarehouseID) as Warehouses
        FROM t_InventoryOnHand 
        WHERE Status = 'Available'
        GROUP BY ProductID
        HAVING SUM(Qty) > 0
        ORDER BY ProductID
    ''').fetchall()

    # Do NOT close conn here – let Flask handle it

    return render_template('mrp/dashboard.html',
                           lang=lang,
                           current_time=current_time,
                           dates=dates,
                           sales_orders=sales_orders,
                           forecasts=forecasts,
                           purchase_orders=purchase_orders,
                           production_orders=production_orders,
                           inventory_items=inventory_items,
                           workflow_task_count=0)
    
    print(f"Sales orders count: {len(sales_orders)}")
    print(f"Forecasts count: {len(forecasts)}")
    print(f"Purchase orders count: {len(purchase_orders)}")
    print(f"Production orders count: {len(production_orders)}")
    print(f"Inventory items count: {len(inventory_items)}")



@mrp_bp.route('/run/<run_id>')
@login_required
def run_details(run_id):
    lang = session.get('lang', 'en')
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    conn = get_db_connection()
    try:
        # Run info
        run = conn.execute('SELECT * FROM t_PlanningRuns WHERE PlanningNumber = ?', (run_id,)).fetchone()
        if not run:
            abort(404)

        # Logs
        logs = conn.execute('SELECT * FROM t_RunLogs WHERE RunID = ? ORDER BY Timestamp', (run_id,)).fetchall()

        # Summary (pass connection)
        summary = get_mrp_summary(run_id, conn=conn)

        # Planned orders
        try:
            planned_orders = conn.execute('''
                SELECT po.*, p.descEnglish as ProductDescription,
                       d.GregorianDate as RequiredDate,
                       d2.GregorianDate as ReleaseDate
                FROM t_PlannedOrders po
                LEFT JOIN t_Product p ON po.ProductID = p.ProductId
                LEFT JOIN DimDate d ON po.RequiredDateKey = d.DateKey
                LEFT JOIN DimDate d2 ON po.PlannedReleaseDateKey = d2.DateKey
                WHERE po.RunID = ?
                ORDER BY po.RequiredDateKey, po.ProductID
            ''', (run_id,)).fetchall()
            print(f"Planned orders count: {len(planned_orders)}")
        except Exception as e:
            print("Error in planned_orders:", e)
            planned_orders = []

        # MRP results (shortages)
        try:
            mrp_results = conn.execute('''
                SELECT mr.*, p.descEnglish as ProductDescription, d.GregorianDate
                FROM t_MRPRunResults mr
                LEFT JOIN t_Product p ON mr.ProductID = p.ProductId
                LEFT JOIN DimDate d ON mr.DateKey = d.DateKey
                WHERE mr.RunID = ?
                  AND (mr.NetRequirements > 0 OR mr.ProjectedOnHand < 0)
                ORDER BY mr.ProductID, mr.DateKey
            ''', (run_id,)).fetchall()
            print(f"MRP results count: {len(mrp_results)}")
        except Exception as e:
            print("Error in mrp_results:", e)
            mrp_results = []

        # Capacity loads
        try:
            capacity_loads = conn.execute('''
                SELECT cl.*, wc.Name as WorkCenterName, d.GregorianDate
                FROM t_CapacityLoad cl
                LEFT JOIN t_WorkCenter wc ON cl.WorkCenterID = wc.WorkCenterID
                LEFT JOIN DimDate d ON cl.DateKey = d.DateKey
                WHERE cl.RunID = ?
                ORDER BY cl.DateKey, cl.WorkCenterID
            ''', (run_id,)).fetchall()
            print(f"Capacity loads count: {len(capacity_loads)}")
        except Exception as e:
            print("Error in capacity_loads:", e)
            capacity_loads = []

    except Exception as e:
        print("Unexpected error in run_details:", e)
        raise
    
        

    return render_template('mrp/run_details.html',
                           run=run,
                           logs=logs,
                           summary=summary,
                           planned_orders=planned_orders,
                           mrp_results=mrp_results,
                           capacity_loads=capacity_loads,
                           lang=lang,
                           current_time=current_time,
                           workflow_task_count=0)