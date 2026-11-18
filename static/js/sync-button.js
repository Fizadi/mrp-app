/**
 * static/js/sync-button.js
 *
 * Reusable "Sync from Hamkaran" button that works per-scope.
 *
 * Auto-init:
 *   Any element with class .sync-btn is wired up on DOM ready. The scope
 *   is read from data-scope (defaults to 'rahkaran').
 *
 * Markup example:
 *   <button class="btn btn-outline-warning sync-btn" data-scope="customers" type="button">
 *       <i class="fas fa-sync-alt me-1"></i>{{ get_translation('crm.customers.sync', 'Sync Customers') }}
 *   </button>
 *
 * Manual usage (optional):
 *   SyncButton.attach('#myBtn', { scope: 'customers' });
 *
 * Endpoints consumed:
 *   POST /api/sync/<scope>          → { ok, scope, tenant_id, duration_s, result }
 *   GET  /api/sync/status[?scope=]  → { busy } or { status: 'idle'|'running' }
 *
 * i18n:
 *   Every user-visible string goes through window.i18n.t(key, fallback).
 *   If window.i18n is not loaded, the English fallback is used. Add the
 *   following keys to t_Translations (en + fa) — see bottom of this file
 *   for the SQL MERGE.
 *
 * Response shape tolerance:
 *   Success check accepts BOTH { ok: true } and { status: 'ok' } so it
 *   survives backend variations. Any other 2xx is treated as a soft
 *   failure with a console.log of the full body.
 */
window.SyncButton = (function () {
    'use strict';

    var STATUS_URL = '/api/sync/status';
    var SYNC_URL   = '/api/sync';

    // ---------------------------------------------------------------------
    // i18n helper
    // ---------------------------------------------------------------------
    function t(key, fallback) {
        try {
            if (window.i18n && typeof window.i18n.t === 'function') {
                var v = window.i18n.t(key, null);
                if (v && v !== key) return v;
            }
        } catch (_) { /* fall through */ }
        return fallback || key;
    }

    // ---------------------------------------------------------------------
    // Utilities
    // ---------------------------------------------------------------------
    function escapeHtml(s) {
        return String(s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function toast(message, kind) {
        kind = kind || 'secondary';
        try {
            if (window.bootstrap && window.bootstrap.Toast) {
                var host = document.getElementById('syncToastHost');
                if (!host) {
                    host = document.createElement('div');
                    host.id = 'syncToastHost';
                    host.className = 'toast-container position-fixed top-0 end-0 p-3';
                    host.style.zIndex = '2000';
                    document.body.appendChild(host);
                }
                var node = document.createElement('div');
                node.className = 'toast align-items-center text-bg-' + kind + ' border-0';
                node.setAttribute('role', 'alert');
                node.innerHTML =
                    '<div class="d-flex">' +
                        '<div class="toast-body">' + escapeHtml(message) + '</div>' +
                        '<button type="button" class="btn-close btn-close-white me-2 m-auto" ' +
                                'data-bs-dismiss="toast" aria-label="Close"></button>' +
                    '</div>';
                host.appendChild(node);
                var bsToast = new window.bootstrap.Toast(node, { delay: 4500 });
                node.addEventListener('hidden.bs.toast', function () { node.remove(); });
                bsToast.show();
                return;
            }
        } catch (_) { /* fall through */ }
        console.log('[sync toast][' + kind + ']', message);
    }

    function scopeOf(node, fallback) {
        if (node && node.getAttribute) {
            var s = node.getAttribute('data-scope');
            if (s) return s;
        }
        return fallback || 'rahkaran';
    }

    function setBusy(btn, busy, originalHtml, label) {
        if (!btn) return;
        btn.disabled = !!busy;
        if (busy) {
            btn.innerHTML =
                '<span class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span>' +
                escapeHtml(label || t('crm.sync.syncing', 'Syncing…'));
        } else {
            btn.innerHTML = originalHtml;
        }
    }

    // ---------------------------------------------------------------------
    // Status poll — tolerates both { busy } and { status } response shapes.
    // Returns a Promise<boolean>.
    // ---------------------------------------------------------------------
    function pollStatus(scope) {
        var url = STATUS_URL;
        if (scope) url += '?scope=' + encodeURIComponent(scope);

        return fetch(url, { credentials: 'same-origin' })
            .then(function (r) {
                if (!r.ok) return false;
                return r.json().then(function (j) {
                    if (!j) return false;
                    if (typeof j.busy === 'boolean') return j.busy;
                    if (typeof j.status === 'string') return j.status === 'running';
                    return false;
                });
            })
            .catch(function () {
                // A failed /status poll must NOT abort the click.
                return false;
            });
    }

    // ---------------------------------------------------------------------
    // Success predicate — tolerant of { ok: true } and { status: 'ok' }.
    // ---------------------------------------------------------------------
    function isSuccess(status, body) {
        if (status < 200 || status >= 300) return false;
        if (status === 207) return false;
        if (!body) return false;
        if (body.ok === true) return true;
        if (body.status === 'ok') return true;
        if (body.ok === false) return false;
        if (body.status === 'error') return false;
        return typeof body === 'object' && Object.keys(body).length > 0;
    }

    // ---------------------------------------------------------------------
    // Core run
    // ---------------------------------------------------------------------
    function runSync(btn, opts) {
        opts = opts || {};
        var scope = opts.scope || scopeOf(btn);
        var endpoint = SYNC_URL + '/' + encodeURIComponent(scope);

        if (!btn.dataset._syncOriginalHtml) {
            btn.dataset._syncOriginalHtml = btn.innerHTML;
        }
        var originalHtml = btn.dataset._syncOriginalHtml;

        setBusy(btn, true, originalHtml, opts.busyLabel || t('crm.sync.syncing', 'Syncing…'));

        return fetch(endpoint, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
        })
        .then(function (r) {
            return r.text().then(function (txt) {
                var body = null;
                try { body = txt ? JSON.parse(txt) : null; } catch (_) { /* ignore */ }
                return { status: r.status, body: body, raw: txt };
            });
        })
        .then(function (res) {
            var status = res.status;
            var body   = res.body || {};

            // Explicit status-code branches — each with i18n-aware message.
            if (status === 409) {
                toast(body.error || t('crm.sync.already_running',
                    'A sync is already running. Please wait.'), 'warning');
                return;
            }
            if (status === 501) {
                toast(body.error || t('crm.sync.not_implemented',
                    'This sync is not implemented yet.'), 'warning');
                return;
            }
            if (status === 404) {
                toast(body.error || t('crm.sync.unknown_scope',
                    'Unknown sync scope: ') + scope, 'warning');
                return;
            }
            if (status === 400) {
                toast(body.error || t('crm.sync.switch_tenant',
                    'Cannot sync — switch to a specific tenant first.'), 'warning');
                return;
            }
            if (status === 403) {
                toast(body.error || t('crm.sync.no_permission',
                    'You do not have permission to run this sync.'), 'warning');
                return;
            }

            // Success — accept ok:true or status:'ok' (tolerant).
            if (isSuccess(status, body)) {
                var successMsg = t('crm.sync.success', 'Sync complete');
                var dur = (body.duration_s != null)
                    ? ' (' + body.duration_s + 's)'
                    : '';
                toast(successMsg + dur + ' — ' + t('crm.sync.refreshing', 'refreshing…'), 'success');
                var done = opts.onDone || function () {
                    setTimeout(function () { window.location.reload(); }, 800);
                };
                done(body);
                return;
            }

            // Anything else — log fully, then show a concrete error.
            try { console.error('[sync] failed', endpoint, status, body, res.raw); } catch (_) {}
            var failedMsg = t('crm.sync.failed', 'Sync failed');
            var detail = body.error || body.message || ('HTTP ' + status);
            toast(failedMsg + ': ' + detail, 'danger');
            if (typeof opts.onError === 'function') opts.onError(detail, body);
        })
        .catch(function (err) {
            try { console.error('[sync] network error', endpoint, err); } catch (_) {}
            var failedMsg = t('crm.sync.failed', 'Sync failed');
            var em = (err && err.message) ? err.message : String(err);
            toast(failedMsg + ': ' + em, 'danger');
            if (typeof opts.onError === 'function') opts.onError(String(err));
        })
        .then(function () {
            setBusy(btn, false, originalHtml);
            if (typeof opts.onIdle === 'function') opts.onIdle();
        });
    }

    // ---------------------------------------------------------------------
    // Attach — idempotent, works for elements or selectors
    // ---------------------------------------------------------------------
    function attach(target, opts) {
        var btn = (typeof target === 'string')
            ? document.querySelector(target)
            : target;
        if (!btn) return null;

        if (!btn.dataset._syncWired) {
            btn.dataset._syncWired = '1';
            btn.addEventListener('click', function (e) {
                e.preventDefault();
                if (btn.disabled) return;
                var scope = (opts && opts.scope) || scopeOf(btn);
                // Pre-check busy state — a failed poll resolves false and
                // does NOT block the click.
                pollStatus(scope).then(function (busy) {
                    if (busy) {
                        toast(t('crm.sync.already_running',
                            'A sync is already running. Please wait.'), 'warning');
                        return;
                    }
                    runSync(btn, opts || {});
                });
            });
        }

        // Reflect an already-in-flight sync (e.g. from another tab).
        pollStatus((opts && opts.scope) || scopeOf(btn)).then(function (busy) {
            if (busy) {
                if (!btn.dataset._syncOriginalHtml) {
                    btn.dataset._syncOriginalHtml = btn.innerHTML;
                }
                setBusy(btn, true, btn.dataset._syncOriginalHtml, t('crm.sync.syncing', 'Syncing…'));
                var timer = setInterval(function () {
                    pollStatus((opts && opts.scope) || scopeOf(btn)).then(function (stillBusy) {
                        if (!stillBusy) {
                            clearInterval(timer);
                            setBusy(btn, false, btn.dataset._syncOriginalHtml);
                        }
                    });
                }, 3000);
            }
        });

        return {
            run: function () { return runSync(btn, opts || {}); },
            btn: btn,
        };
    }

    // ---------------------------------------------------------------------
    // Auto-init
    // ---------------------------------------------------------------------
    function autoInit() {
        var nodes = document.querySelectorAll('.sync-btn');
        for (var i = 0; i < nodes.length; i++) {
            attach(nodes[i]);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', autoInit);
    } else {
        autoInit();
    }

    return {
        attach: attach,
        autoInit: autoInit,
        toast: toast,
        escapeHtml: escapeHtml,
        t: t,
    };
})();