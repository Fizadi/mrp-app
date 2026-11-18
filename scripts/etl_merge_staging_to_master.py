"""
ETL Step 2 — Merge stg_Rahkaran_* → master tables (t_Product, t_companies,
t_ProductPriceHistory) for Tenant 9.

Idempotent. Safe to re-run.

Schema notes:
- t_Product:             HAS TenantID, RahkaranPartID
- t_companies:           HAS TenantID, RahkaranPartyID
- t_ProductPriceHistory: NO TenantID (shared catalog); tenant isolation is
                         achieved via the ProductID join to t_Product.TenantID

Callable as:
    - CLI:      python scripts/etl_merge_staging_to_master.py
    - In-proc:  from scripts.etl_merge_staging_to_master import run
                counts = run(tenant_id=9)
"""
import pyodbc
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.core.db_config import get_db_connection_string

CONN_STR = get_db_connection_string()

TENANT_ID = 9


def merge_parts(cur):
    """stg_Rahkaran_Part → t_Product"""
    # 1) UPDATE existing (match on RahkaranPartID, Tenant 9)
    cur.execute("""
        UPDATE t_Product
        SET descEnglish      = s.Name,
            ProductNameLocal = s.Name
        FROM t_Product
        JOIN stg_Rahkaran_Part s
          ON t_Product.RahkaranPartID = s.RahkaranPartID
         AND t_Product.TenantID       = s.TenantID
        WHERE s.TenantID = ?
    """, (TENANT_ID,))
    updated = cur.rowcount

    # 2) INSERT new (where RahkaranPartID not yet present)
    cur.execute("""
        INSERT INTO t_Product (
            ProductId, descEnglish, ProductNameLocal,
            UOM, IsManufactured,
            RahkaranPartID, TenantID
        )
        SELECT
            'RK-PART-' + CAST(s.RahkaranPartID AS NVARCHAR(20)) AS ProductId,
            s.Name,
            s.Name,
            'EA',
            '1',
            s.RahkaranPartID,
            s.TenantID
        FROM stg_Rahkaran_Part s
        WHERE s.TenantID = ?
          AND NOT EXISTS (
              SELECT 1 FROM t_Product p
              WHERE p.RahkaranPartID = s.RahkaranPartID
                AND p.TenantID      = s.TenantID
          )
    """, (TENANT_ID,))
    inserted = cur.rowcount
    print(f"[t_Product]             updated {updated:3d}  inserted {inserted:3d}")


def merge_parties(cur):
    """stg_Rahkaran_Party (PartyType=1 = companies) → t_companies"""
    # 1) UPDATE existing
    cur.execute("""
        UPDATE t_companies
        SET Name      = s.FullName,
            Telephone = COALESCE(NULLIF(t_companies.Telephone, ''), s.Mobile)
        FROM t_companies
        JOIN stg_Rahkaran_Party s
          ON t_companies.RahkaranPartyID = s.RahkaranPartyID
         AND t_companies.TenantID         = s.TenantID
        WHERE s.TenantID  = ?
          AND s.PartyType = 1
    """, (TENANT_ID,))
    updated = cur.rowcount

    # 2) INSERT new (skip if companyID already exists via RahkaranPartyID)
    #    Hamkaran's Party table doesn't distinguish customer vs supplier
    #    (both are Type=1 = company), so B2B companies get both flags = '1'.
    cur.execute("""
        INSERT INTO t_companies (
            companyID, Name, Telephone,
            IsCustomer, IsSupplier,
            RahkaranPartyID, TenantID
        )
        SELECT
            'RK-PARTY-' + CAST(s.RahkaranPartyID AS NVARCHAR(20)),
            s.FullName,
            s.Mobile,
            '1',
            '1',
            s.RahkaranPartyID,
            s.TenantID
        FROM stg_Rahkaran_Party s
        WHERE s.TenantID  = ?
          AND s.PartyType = 1
          AND NOT EXISTS (
              SELECT 1 FROM t_companies c
              WHERE c.RahkaranPartyID = s.RahkaranPartyID
                AND c.TenantID        = s.TenantID
          )
    """, (TENANT_ID,))
    inserted = cur.rowcount
    print(f"[t_companies]           updated {updated:3d}  inserted {inserted:3d}")


def merge_price_history(cur):
    """stg_Rahkaran_PriceListItem → t_ProductPriceHistory (PriceType='List')

    t_ProductPriceHistory has NO TenantID column — it's a shared price catalog.
    Tenant isolation is via the ProductID join (each row joins to a t_Product
    that IS tenant-scoped). RahkaranPriceListItemID is globally unique (it's
    Hamkaran's PK), so idempotency is safe across tenants too.
    """
    cur.execute("""
        INSERT INTO t_ProductPriceHistory (
            PriceID, ProductID, Price, Currency,
            EffectiveDateKey, PriceType,
            RahkaranPriceListItemID,
            CreatedDateKey, CreatedBy
        )
        SELECT
            'RK-PLI-' + CAST(s.RahkaranPriceListItemID AS NVARCHAR(20)),
            p.ProductId,
            CAST(s.Fee AS NVARCHAR(50)),
            'IRR',
            CONVERT(CHAR(8), GETDATE(), 112),
            'List',
            s.RahkaranPriceListItemID,
            CONVERT(INT, CONVERT(CHAR(8), GETDATE(), 112)),
            'system'
        FROM stg_Rahkaran_PriceListItem s
        JOIN t_Product p
          ON p.RahkaranPartID = s.RahkaranPartID
         AND p.TenantID      = s.TenantID
        WHERE s.TenantID = ?
          AND NOT EXISTS (
              SELECT 1 FROM t_ProductPriceHistory ph
              WHERE ph.RahkaranPriceListItemID = s.RahkaranPriceListItemID
          )
    """, (TENANT_ID,))
    inserted = cur.rowcount
    print(f"[t_ProductPriceHistory] inserted {inserted:3d}")


def run(tenant_id=9):
    """Run ETL Step 2. Returns dict of counts. Raises on failure."""
    global TENANT_ID
    TENANT_ID = tenant_id

    conn = pyodbc.connect(CONN_STR, autocommit=False)
    cur = conn.cursor()
    try:
        merge_parts(cur)
        merge_parties(cur)
        merge_price_history(cur)
        conn.commit()
        print("\n✅ Merge complete.")

        # ---- Verification + return counts ----
        cur.execute("SELECT COUNT(*) FROM t_Product WHERE TenantID = ?", (TENANT_ID,))
        n_products = cur.fetchone()[0]
        print(f"   t_Product (Tenant {TENANT_ID})              = {n_products} rows")

        cur.execute("SELECT COUNT(*) FROM t_companies WHERE TenantID = ?", (TENANT_ID,))
        n_companies = cur.fetchone()[0]
        print(f"   t_companies (Tenant {TENANT_ID})            = {n_companies} rows")

        cur.execute("""
            SELECT COUNT(*)
            FROM t_ProductPriceHistory ph
            JOIN t_Product p ON p.ProductId = ph.ProductID
            WHERE p.TenantID = ?
        """, (TENANT_ID,))
        n_prices = cur.fetchone()[0]
        print(f"   t_ProductPriceHistory (Tenant {TENANT_ID})  = {n_prices} rows")

        cur.execute("""SELECT COUNT(*) FROM t_Product
                       WHERE TenantID = ? AND RahkaranPartID IS NOT NULL""", (TENANT_ID,))
        n_products_linked = cur.fetchone()[0]
        print(f"   t_Product with RahkaranPartID               = {n_products_linked} rows")

        cur.execute("""SELECT COUNT(*) FROM t_companies
                       WHERE TenantID = ? AND RahkaranPartyID IS NOT NULL""", (TENANT_ID,))
        n_companies_linked = cur.fetchone()[0]
        print(f"   t_companies with RahkaranPartyID            = {n_companies_linked} rows")

        return {
            'products_total':      n_products,
            'companies_total':     n_companies,
            'prices_total':        n_prices,
            'products_linked':     n_products_linked,
            'companies_linked':    n_companies_linked,
        }

    except Exception as e:
        conn.rollback()
        print(f"\n❌ FAILED: {e}", file=sys.stderr)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    run(9)