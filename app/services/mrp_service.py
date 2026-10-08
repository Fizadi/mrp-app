import time
from datetime import datetime, timedelta
from collections import defaultdict
from sqlalchemy.sql import text

def date_key_to_date(date_key):
    if not date_key:
        return None
    s = str(date_key)
    return datetime(int(s[:4]), int(s[4:6]), int(s[6:8]))

def date_to_key(dt):
    return int(dt.strftime('%Y%m%d'))

class MRPEngine:
    def __init__(self, engine, planning_number, params):
        self.engine = engine
        self.planning_number = planning_number
        self.params = params
        self.horizon_days = params.get('planningHorizonDays', 90)
        start_key = params.get('planningStartDateKey', int(datetime.now().strftime('%Y%m%d')))
        self.start_date = date_key_to_date(start_key) or datetime.now()
        self.end_date = self.start_date + timedelta(days=self.horizon_days)
        self.today = datetime.now().date()
        self.net_requirements = defaultdict(dict)
        self.planned_receipts = defaultdict(dict)
        self.planned_releases = defaultdict(dict)
        self.exceptions = []

    def log_step(self, step, message):
        with self.engine.begin() as conn:
            conn.execute(
                text("INSERT INTO t_RunLogs (RunID, Step, Message) VALUES (:pn, :step, :msg)"),
                {'pn': self.planning_number, 'step': step, 'msg': message}
            )

    def load_demand(self):
        self.log_step("DemandLoad", "Loading demand from sales orders and forecasts")
        with self.engine.connect() as conn:
            try:
                rows = conn.execute(text("""
                    SELECT ProductID, DeliveryDateKey, SUM(Qty) as total_qty
                    FROM t_SalesOrder
                    WHERE Status IN ('Confirmed', 'Open', 'Approved')
                      AND DeliveryDateKey IS NOT NULL
                    GROUP BY ProductID, DeliveryDateKey
                """)).fetchall()
                print(f"DEBUG: Found {len(rows)} sales order demand rows")
                for row in rows:
                    prod = row[0]
                    dk = int(row[1])
                    qty = row[2]
                    self.net_requirements[prod][dk] = self.net_requirements[prod].get(dk, 0) + qty
            except Exception as e:
                print(f"DEBUG: Sales order demand error: {e}")
                self.log_step("DemandLoad", f"Sales order error: {str(e)}")
            try:
                rows = conn.execute(text("""
                    SELECT ProductId, DateKey, Qty
                    FROM t_Forecasts
                    WHERE DateKey BETWEEN :start AND :end
                """), {'start': date_to_key(self.start_date), 'end': date_to_key(self.end_date)}).fetchall()
                print(f"DEBUG: Found {len(rows)} forecast rows")
                for row in rows:
                    prod = row[0]
                    dk = int(row[1])
                    qty = row[2]
                    self.net_requirements[prod][dk] = self.net_requirements[prod].get(dk, 0) + qty
            except Exception as e:
                print(f"DEBUG: Forecast error: {e}")
                self.log_step("DemandLoad", f"Forecasts error: {str(e)}")
        print(f"DEBUG: Total distinct product-demand pairs: {len(self.net_requirements)}")
        self.log_step("DemandLoad", f"Loaded demand for {len(self.net_requirements)} products")

    def load_onhand_and_receipts(self):
        self.log_step("SupplyLoad", "Loading current inventory and scheduled receipts")
        with self.engine.connect() as conn:
            onhand_rows = conn.execute(text("""
                SELECT ProductID, SUM(Qty) as total
                FROM t_InventoryOnHand
                WHERE Status = 'Available'
                GROUP BY ProductID
            """)).fetchall()
            self.onhand = {r[0]: r[1] for r in onhand_rows}
            print(f"DEBUG: On-hand for {len(self.onhand)} products")
            po_rows = conn.execute(text("""
                SELECT ProductID, ExpectedDeliveryDateKey, SUM(Qty) as total
                FROM t_PurchaseOrder
                WHERE Status IN ('Open', 'Approved')
                  AND ExpectedDeliveryDateKey IS NOT NULL
                GROUP BY ProductID, ExpectedDeliveryDateKey
            """)).fetchall()
            self.scheduled_receipts = defaultdict(dict)
            for row in po_rows:
                prod = row[0]
                dk = int(row[1])
                qty = row[2]
                self.scheduled_receipts[prod][dk] = self.scheduled_receipts[prod].get(dk, 0) + qty
            print(f"DEBUG: Purchase order receipts for {len(self.scheduled_receipts)} products")
            prod_rows = conn.execute(text("""
                SELECT ProductId, EndDateKey, OrderQuantity
                FROM t_ProductionOrder
                WHERE Status IN ('Released', 'InProgress')
                  AND EndDateKey IS NOT NULL
            """)).fetchall()
            for row in prod_rows:
                prod = row[0]
                dk = int(row[1])
                qty = row[2]
                self.scheduled_receipts[prod][dk] = self.scheduled_receipts[prod].get(dk, 0) + qty
            print(f"DEBUG: Total scheduled receipts products: {len(self.scheduled_receipts)}")
        self.log_step("SupplyLoad", f"On-hand: {len(self.onhand)} products, receipts: {len(self.scheduled_receipts)} products")

    def explode_bom(self, product, qty, due_date_key, level=0, max_level=10):
        if level > max_level:
            return
        with self.engine.connect() as conn:
            components = conn.execute(text("""
                SELECT ComponentProductID, Quantity, ScrapFactor
                FROM t_BOM
                WHERE ParentProductID = :pid
                  AND (EffectivityDateKey IS NULL OR EffectivityDateKey <= :now)
                  AND (ObsoleteDateKey IS NULL OR ObsoleteDateKey > :now)
            """), {'pid': product, 'now': date_to_key(datetime.now())}).fetchall()
        for comp in components:
            comp_id = comp[0]
            comp_qty = qty * (comp[1] or 1) * (1 + (comp[2] or 0)/100)
            self.net_requirements[comp_id][due_date_key] = self.net_requirements[comp_id].get(due_date_key, 0) + comp_qty
            self.explode_bom(comp_id, comp_qty, due_date_key, level+1, max_level)

    def calculate_net_requirements(self):
        self.log_step("Netting", "Calculating net requirements")
        for prod, demand in list(self.net_requirements.items()):
            onhand = self.onhand.get(prod, 0)
            receipts = self.scheduled_receipts.get(prod, {})
            demand_keys = [k for k in demand.keys() if k is not None]
            receipt_keys = [k for k in receipts.keys() if k is not None]
            all_dates = sorted(set(demand_keys) | set(receipt_keys))
            cumulative_onhand = onhand
            for dk in all_dates:
                if dk in receipts:
                    cumulative_onhand += receipts[dk]
                req = demand.get(dk, 0)
                if cumulative_onhand >= req:
                    cumulative_onhand -= req
                else:
                    shortage = req - cumulative_onhand
                    self.net_requirements[prod][dk] = shortage
                    cumulative_onhand = 0
                    if shortage > 0:
                        self.exceptions.append({
                            'product': prod,
                            'date_key': dk,
                            'type': 'Shortage',
                            'details': f"Shortage of {shortage} units on {dk}",
                            'priority': 2
                        })
        print(f"DEBUG: After netting, net_requirements has {len(self.net_requirements)} products")
        self.log_step("Netting", f"Processed {len(self.net_requirements)} products")

    def generate_planned_orders(self):
        self.log_step("OrderGeneration", "Generating planned orders")
        count = 0
        for prod, req_by_date in self.net_requirements.items():
            for dk, qty in req_by_date.items():
                if qty <= 0 or dk is None:
                    continue
                count += 1
                with self.engine.connect() as conn:
                    prod_info = conn.execute(text("""
                        SELECT ProcurementType, MinOrderQty, BatchSize, LeadTime
                        FROM t_Product WHERE ProductId = :pid
                    """), {'pid': prod}).first()
                lot = 1
                if prod_info:
                    if prod_info[0] == 'Purchased' and prod_info[1]:
                        lot = prod_info[1]
                    elif prod_info[2]:
                        lot = prod_info[2]
                order_qty = ((qty + lot - 1) // lot) * lot
                order_type = 'Purchase' if prod_info and prod_info[0] == 'Purchased' else 'Manufacturing'
                try:
                    with self.engine.begin() as conn:
                        conn.execute(
                            text("""
                                INSERT INTO t_MRPRunResults
                                (RunID, ProductID, DateKey, PlannedOrderReceipts, PlannedOrderReleases, OrderType)
                                VALUES (:rid, :pid, :dk, :receipt, :release, :otype)
                            """),
                            {'rid': self.planning_number, 'pid': prod, 'dk': dk,
                             'receipt': order_qty, 'release': order_qty, 'otype': order_type}
                        )
                except Exception as e:
                    print(f"DEBUG: Insert failed for {prod}, {dk}: {e}")
        print(f"DEBUG: Generated {count} planned orders")
        self.log_step("OrderGeneration", f"Generated {len(self.planned_receipts)} planned orders")

    def record_exceptions(self):
        for exc in self.exceptions:
            try:
                with self.engine.begin() as conn:
                    conn.execute(
                        text("""
                            INSERT INTO t_MRPExceptions
                            (PlanningNumber, ProductID, MessageType, Details, RunDate, Priority, DateKey, Status)
                            VALUES (:rid, :prod, :type, :details, :rundate, :priority, :dkey, 'Open')
                        """),
                        {'rid': self.planning_number, 'prod': exc['product'], 'type': exc['type'],
                         'details': exc['details'], 'rundate': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                         'priority': exc['priority'], 'dkey': exc['date_key']}
                    )
            except Exception as e:
                print(f"DEBUG: Exception insert failed: {e}")
        print(f"DEBUG: Recorded {len(self.exceptions)} exceptions")
        self.log_step("Exceptions", f"Recorded {len(self.exceptions)} exceptions")

    def run(self):
        self.load_demand()
        self.load_onhand_and_receipts()
        for prod, reqs in list(self.net_requirements.items()):
            for dk, qty in reqs.items():
                self.explode_bom(prod, qty, dk)
        self.calculate_net_requirements()
        self.generate_planned_orders()
        self.record_exceptions()
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE t_PlanningRuns SET Status = 'Completed' WHERE PlanningNumber = :pn"),
                {'pn': self.planning_number}
            )
        self.log_step("Complete", f"MRP run {self.planning_number} finished")

def run_mrp_async(planning_number, params, engine):
    mrp = MRPEngine(engine, planning_number, params)
    mrp.run()