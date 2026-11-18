"""
ETL Step 1 — Copy Hamkaran_Database → MRP_database.stg_Rahkaran_*

Same .\SQLEXPRESS instance, so this is pure SQL-to-SQL (no pandas).
Read-only against Hamkaran_Database.

Callable as:
    - CLI:      python scripts/etl_load_from_hamkaran.py
    - In-proc:  from scripts.etl_load_from_hamkaran import run
                counts = run(tenant_id=9)
"""
import pyodbc
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.core.db_config import get_db_connection_string

CONN_STR = get_db_connection_string()

TENANT_ID = 9

# Each tuple: (staging_table, staging_columns, hamkaran_select_sql)
JOBS = [
    (
        "stg_Rahkaran_Part",
        ["TenantID", "RahkaranPartID", "Code", "Name",
         "CategoryRef", "MajorUnitRef"],
        """
        SELECT {tid} AS TenantID,
               PartID AS RahkaranPartID,
               Code, Name,
               CategoryRef, MajorUnitRef
        FROM Hamkaran_Database.dbo.Part
        """,
    ),
    (
        "stg_Rahkaran_PriceListItem",
        ["TenantID", "RahkaranPriceListItemID", "RahkaranPartID", "Fee"],
        """
        SELECT {tid} AS TenantID,
               PriceListItemID AS RahkaranPriceListItemID,
               PartRef         AS RahkaranPartID,
               Fee
        FROM Hamkaran_Database.dbo.PriceListItem
        """,
    ),
    (
        "stg_Rahkaran_Party",
        ["TenantID", "RahkaranPartyID", "FullName",
         "NationalID", "EconomicCode", "Mobile", "PartyType"],
        """
        SELECT {tid} AS TenantID,
               PartyID AS RahkaranPartyID,
               FullName, NationalID, EconomicCode, Mobile,
               Type    AS PartyType
        FROM Hamkaran_Database.dbo.Party
        """,
    ),
]


def run(tenant_id=9):
    """Run ETL Step 1. Returns dict of counts. Raises on failure."""
    global TENANT_ID
    TENANT_ID = tenant_id

    print(f"Connecting to {CONN_STR.split('DATABASE=')[1].split(';')[0]} ...")
    conn = pyodbc.connect(CONN_STR, autocommit=False)
    cur = conn.cursor()

    try:
        for stg_table, cols, sel_sql in JOBS:
            src_sql = sel_sql.replace("{tid}", str(TENANT_ID))

            # Truncate staging (tenant-scoped — safe for demo since only Tenant 9 uses it)
            cur.execute(f"DELETE FROM {stg_table} WHERE TenantID = ?", (TENANT_ID,))
            deleted = cur.rowcount
            print(f"\n[{stg_table}] cleared {deleted} existing rows (Tenant {TENANT_ID})")

            # INSERT ... SELECT (SQL Server allows cross-database SELECT in same instance)
            col_list = ", ".join(cols)
            insert_sql = f"""
                INSERT INTO {stg_table} ({col_list})
                {src_sql}
            """
            cur.execute(insert_sql)
            inserted = cur.rowcount
            print(f"[{stg_table}] inserted {inserted} rows from Hamkaran_Database")

        conn.commit()
        print("\n✅ Staging load complete.")

        # Capture row counts for the caller
        counts = {}
        for stg_table, *_ in JOBS:
            cur.execute(f"SELECT COUNT(*) FROM {stg_table} WHERE TenantID = ?", (TENANT_ID,))
            n = cur.fetchone()[0]
            print(f"   {stg_table:30s} = {n} rows")
            # Key by short name: stg_Rahkaran_Part → 'part'
            short = stg_table.replace('stg_Rahkaran_', '').lower()
            counts[short] = n
        return counts

    except Exception as e:
        conn.rollback()
        print(f"\n❌ FAILED: {e}", file=sys.stderr)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    run(9)