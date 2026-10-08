import sqlite3
from config import Config

conn = sqlite3.connect(Config.DB_PATH)
cursor = conn.cursor()

# 1. Check t_ProductionOrder
cursor.execute("SELECT COUNT(*) FROM t_ProductionOrder")
count = cursor.fetchone()[0]
print(f"t_ProductionOrder rows: {count}")
if count > 0:
    cursor.execute("SELECT ProductionOrderId, ProductId, Status FROM t_ProductionOrder LIMIT 3")
    for row in cursor.fetchall():
        print(f"  {row}")

# 2. Check t_EngineeringToProduct existence and columns
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='t_EngineeringToProduct'")
if cursor.fetchone():
    cursor.execute("PRAGMA table_info(t_EngineeringToProduct)")
    cols = cursor.fetchall()
    print("\nt_EngineeringToProduct columns:")
    for col in cols:
        print(f"  {col[1]} ({col[2]})")
else:
    print("\nt_EngineeringToProduct table does NOT exist")

# 3. Check t_ProductManufacturingParams
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='t_ProductManufacturingParams'")
if cursor.fetchone():
    cursor.execute("PRAGMA table_info(t_ProductManufacturingParams)")
    cols = cursor.fetchall()
    print("\nt_ProductManufacturingParams columns:")
    for col in cols:
        print(f"  {col[1]} ({col[2]})")
else:
    print("\nt_ProductManufacturingParams table does NOT exist")

# 4. Check t_ProductionOrderDetail
cursor.execute("SELECT COUNT(*) FROM t_ProductionOrderDetail")
detail_count = cursor.fetchone()[0]
print(f"\nt_ProductionOrderDetail rows: {detail_count}")
if detail_count > 0:
    cursor.execute("SELECT ProductionOrderId, Status, ActualQuantity FROM t_ProductionOrderDetail LIMIT 3")
    for row in cursor.fetchall():
        print(f"  {row}")

conn.close()