"""
Generic Hamkaran → MRP sync dispatcher.

Single endpoint: POST /api/sync/<scope>
Status endpoint: GET  /api/sync/status?scope=<scope>

Each scope maps to a handler that returns a dict of counts. Handlers run
in-process (they call `run(tenant_id)` from scripts/etl_*.py). Long-running
scopes should move to a worker queue — for the demo, in-process is fine.

Adding a new scope:
    1. Write sync_<scope>(tenant_id) -> dict
    2. Register it in SYNC_HANDLERS below
    3. Drop <button class="sync-btn" data-scope="<scope>"> on any page
    No changes to this file's routes or to sync-button.js.
"""
import threading
import time

from flask import Blueprint, request, jsonify, session
from flask_login import login_required

# CORRECTED: use the 1-arg, tenant-aware has_permission() from
# app.core.permissions. app.core.auth.has_permission takes
# (user_id, permission_code) — calling it with one arg raises
# TypeError on every sync click.
from app.core.permissions import has_permission

sync_bp = Blueprint('sync', __name__, url_prefix='/api/sync')


# ---------------------------------------------------------------------
# Process-wide lock so two users can't run the same ETL concurrently.
# (The load step does a tenant-scoped TRUNCATE+INSERT that is not safe
# under parallelism.)
# ---------------------------------------------------------------------
_SYNC_LOCK = threading.Lock()

# Last run's summary, for the /status endpoint to report if asked.
_LAST_RUN = {'scope': None, 'ok': None, 'duration_s': None, 'finished_at': None}


# ---------------------------------------------------------------------
# Handlers — one per scope.
# ---------------------------------------------------------------------
def _require_tenant():
    """Sync is tenant-scoped. Super Admin (tenant 0) must switch first."""
    tenant_id = session.get('tenant_id', 0) or 0
    if tenant_id == 0:
        return None, (
            jsonify({'ok': False, 'error': 'Switch to a specific tenant first.'}),
            400,
        )
    return tenant_id, None


def sync_products(tenant_id):
    """Hamkaran Parts + PriceList → t_Product / t_ProductPriceHistory."""
    from scripts.etl_load_from_hamkaran import run as load_run
    from scripts.etl_merge_staging_to_master import run as merge_run

    load_counts = load_run(tenant_id=tenant_id)
    merge_counts = merge_run(tenant_id=tenant_id)
    return {'load': load_counts, 'merge': merge_counts}


def sync_customers(tenant_id):
    """Hamkaran Parties → t_companies.

    Reuses the same two ETL scripts as sync_products. Once the merge is
    split into per-table entrypoints, this should call only merge_parties.
    Until then, it runs the full pipeline — idempotent, so it is safe.
    """
    from scripts.etl_load_from_hamkaran import run as load_run
    from scripts.etl_merge_staging_to_master import run as merge_run

    load_counts = load_run(tenant_id=tenant_id)
    merge_counts = merge_run(tenant_id=tenant_id)
    return {'load': load_counts, 'merge': merge_counts}


def sync_finance(tenant_id):
    raise NotImplementedError('Finance sync not yet implemented.')


def sync_inventory(tenant_id):
    raise NotImplementedError('Inventory sync not yet implemented.')


def sync_all(tenant_id):
    """Run every implemented sync scope in sequence.

    Order matters only for readability — each scope is idempotent and
    independent. If any scope raises, the whole sync_all raises and the
    dispatcher reports a 207; earlier scopes' work is already committed
    by their own ETL scripts.
    """
    results = {}
    # Only implemented scopes — do NOT include stubs here.
    implemented = ('products', 'customers')
    for scope in implemented:
        handler, _perm = SYNC_HANDLERS[scope]
        results[scope] = handler(tenant_id)
    return {
        'scopes_run': list(results.keys()),
        'results':    results,
    }


# Scope registry: scope → (handler, required_permission)
SYNC_HANDLERS = {
    'products':  (sync_products,  'SALES_VIEW'),
    'customers': (sync_customers, 'SALES_VIEW'),
    'finance':   (sync_finance,   'FINANCE_VIEW'),
    'inventory': (sync_inventory, 'INVENTORY_VIEW'),
    'all':       (sync_all,       'SALES_VIEW'),
}


# ---------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------
@sync_bp.route('/status', methods=['GET'])
@login_required
def sync_status():
    """Frontend polls this for progress.

    Stateless: returns the current lock state plus the last run summary.
    The JS tolerates both shapes ('busy' or 'status'), so we return both
    keys for maximum compatibility with older/newer sync-button.js.
    """
    scope = (request.args.get('scope') or '').strip()
    busy = _SYNC_LOCK.locked()
    return jsonify({
        'busy': busy,
        'status': 'running' if busy else 'idle',
        'scope': scope,
        'message': 'Sync in progress' if busy else 'No sync in progress',
        'last_run': _LAST_RUN if not busy else None,
    })


@sync_bp.route('/<string:scope>', methods=['POST'])
@login_required
def sync_by_scope(scope):
    """
    Run the handler registered for <scope>.

    Response shape (200/207):
        {
          'ok': bool,
          'scope': str,
          'tenant_id': int,
          'duration_s': float,
          'result': {...},           # on success — the handler's dict
          'error': str,              # on failure — human-readable
          'trace': str               # on failure — truncated exception
        }

    Codes:
        200 — success
        207 — partial (handler raised after some work landed)
        400 — tenant 0 (must switch first)
        403 — permission missing
        404 — unknown scope
        501 — scope registered but not implemented
        500 — unexpected exception
    """
    entry = SYNC_HANDLERS.get(scope)
    if entry is None:
        return jsonify({
            'ok': False, 'scope': scope,
            'error': f'Unknown sync scope: {scope}',
        }), 404

    handler, required_perm = entry

    if not has_permission(required_perm):
        return jsonify({
            'ok': False, 'scope': scope,
            'error': f'Permission required: {required_perm}',
        }), 403

    tenant_id, err = _require_tenant()
    if err is not None:
        return err

    if not _SYNC_LOCK.acquire(blocking=False):
        return jsonify({
            'ok': False, 'scope': scope,
            'error': 'A sync is already running. Please wait and try again.',
        }), 409

    t0 = time.monotonic()
    try:
        try:
            result = handler(tenant_id)
        except NotImplementedError as e:
            duration = round(time.monotonic() - t0, 2)
            _LAST_RUN.update({
                'scope': scope, 'ok': False,
                'duration_s': duration, 'finished_at': time.time(),
            })
            return jsonify({
                'ok': False, 'scope': scope,
                'duration_s': duration,
                'error': str(e) or f'Sync for "{scope}" is not implemented yet.',
            }), 501
        except Exception as e:
            duration = round(time.monotonic() - t0, 2)
            trace = f'{type(e).__name__}: {e}'
            _LAST_RUN.update({
                'scope': scope, 'ok': False,
                'duration_s': duration, 'finished_at': time.time(),
            })
            return jsonify({
                'ok': False, 'scope': scope,
                'tenant_id': tenant_id,
                'duration_s': duration,
                'error': f'Sync failed: {e}',
                'trace': trace,
            }), 207

        duration = round(time.monotonic() - t0, 2)
        _LAST_RUN.update({
            'scope': scope, 'ok': True,
            'duration_s': duration, 'finished_at': time.time(),
        })
        return jsonify({
            'ok': True,
            'scope': scope,
            'tenant_id': tenant_id,
            'duration_s': duration,
            'result': result,
        }), 200

    finally:
        _SYNC_LOCK.release()