# app/core/sync_registry.py
"""
Central registry for Hamkaran → MRP sync handlers.

To add a new module's sync:
  1. Write a handler: def sync_<scope>(tenant_id) -> dict
  2. Register it below in SYNC_HANDLERS

The endpoint /api/sync/<scope> automatically picks it up.
"""
import importlib
import traceback


def _run_etl_scripts(tenant_id):
    """Helper for the current file-based ETL. Returns merged counts."""
    # Ensure project root is on sys.path (scripts/ is not a package)
    import sys, os
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)

    load_mod  = importlib.import_module('scripts.etl_load_from_hamkaran')
    merge_mod = importlib.import_module('scripts.etl_merge_staging_to_master')

    load_counts  = load_mod.run(tenant_id)
    merge_counts = merge_mod.run(tenant_id)
    return {'loaded': load_counts, 'merged': merge_counts}


# ============================================================
# HANDLERS — one per sync scope
# ============================================================

def sync_products(tenant_id):
    """Scope 'products': parts + price list."""
    return _run_etl_scripts(tenant_id)


def sync_customers(tenant_id):
    """Scope 'customers': parties + contacts + phones."""
    return _run_etl_scripts(tenant_id)


def sync_finance(tenant_id):
    """Scope 'finance': GL + SL + vouchers (stub — implement when the
    finance ETL lands)."""
    raise NotImplementedError('Finance sync not yet implemented')


def sync_inventory(tenant_id):
    """Scope 'inventory': stock on hand + movements (stub)."""
    raise NotImplementedError('Inventory sync not yet implemented')


# ============================================================
# REGISTRY — the only file you edit when adding a new module
# ============================================================

SYNC_HANDLERS = {
    # scope name         handler function   required permission
    'products':         (sync_products,     'SALES_VIEW'),
    'customers':        (sync_customers,    'SALES_VIEW'),
    'finance':          (sync_finance,      'FINANCE_VIEW'),
    'inventory':        (sync_inventory,    'INVENTORY_VIEW'),
}


def run_sync(scope, tenant_id):
    """Execute the sync handler for the given scope. Returns result dict."""
    if scope not in SYNC_HANDLERS:
        raise ValueError(f"Unknown sync scope: '{scope}'")

    handler, required_perm = SYNC_HANDLERS[scope]

    # Permission is checked by the caller (route) — this just executes.
    try:
        result = handler(tenant_id)
        return {'status': 'ok', 'scope': scope, 'tenant_id': tenant_id, 'result': result}
    except NotImplementedError as e:
        return {'status': 'not_implemented', 'scope': scope, 'error': str(e)}
    except Exception as e:
        traceback.print_exc()
        return {'status': 'error', 'scope': scope, 'error': str(e)}


def get_required_permission(scope):
    """Return the permission code needed to trigger this sync scope."""
    if scope not in SYNC_HANDLERS:
        return None
    return SYNC_HANDLERS[scope][1]