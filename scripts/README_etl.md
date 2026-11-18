# Hamkaran ETL — Runbook

One-way, read-only pull from `Hamkaran_Database` → `MRP_database` (Tenant 9).

## Run order
```cmd
cd /d D:\projects\mrp\mrp-app
copy /Y .env.windows .env
python scripts\etl_load_from_hamkaran.py     :: Step 1 — stage
python scripts\etl_merge_staging_to_master.py :: Step 2 — merge