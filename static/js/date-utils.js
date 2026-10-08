/* =========================================================
   Global Date Utilities — Dual Calendar Support
   ---------------------------------------------------------
   English mode → Flatpickr (Gregorian grid)
   Persian mode → mds.MdsPersianDateTimePicker (Jalali grid)
   In BOTH modes the hidden input holds a Gregorian ISO value.
   ========================================================= */

(function () {
    'use strict';

    // ---- DateKey helpers ----
    window.toDateKey = function (dateStr) {
        if (!dateStr) return null;
        return parseInt(String(dateStr).replace(/[-/]/g, ''), 10);
    };

    window.fromDateKey = function (dateKey) {
        if (!dateKey) return '';
        var s = String(dateKey);
        return s.slice(0, 4) + '-' + s.slice(4, 6) + '-' + s.slice(6, 8);
    };

    function isPersian() {
        return window.currentCalendar === 'persian';
    }

    // ---- Destroy any existing picker on an element ----
    function destroyPicker(el) {
        if (el._flatpickr) {
            try { el._flatpickr.destroy(); } catch (e) {}
            el._flatpickr = null;
        }
        if (el._mdsPicker && typeof el._mdsPicker.dispose === 'function') {
            try { el._mdsPicker.dispose(); } catch (e) {}
        }
        el._mdsPicker = null;

        // Remove any sibling visible input we created earlier
        if (el.id) {
            var vis = document.getElementById(el.id + '-visible');
            if (vis && vis.parentNode) vis.parentNode.removeChild(vis);
        }
        el.style.display = '';
        el.removeAttribute('readonly');
    }

    // ---- Gregorian picker (Flatpickr) ----
    function initGregorian(el) {
        flatpickr(el, {
            dateFormat: 'Y-m-d',
            altInput: false,
            allowInput: true
        });
    }

    // ---- Persian picker (MD.BootstrapPersianDateTimePicker) ----
    function initPersian(el) {
        var Ctor =
            (window.mds && typeof window.mds.MdsPersianDateTimePicker === 'function')
                ? window.mds.MdsPersianDateTimePicker
                : null;

        if (!Ctor) {
            console.error('❌ Persian picker constructor not found. window.mds =', window.mds);
            initGregorian(el); // graceful fallback
            return;
        }

        // Give the original input a unique id
        if (!el.id) el.id = 'dt-' + Math.random().toString(36).slice(2, 9);

        // Create a visible sibling text input
        var visibleId = el.id + '-visible';
        var visible = document.getElementById(visibleId);
        if (!visible) {
            visible = document.createElement('input');
            visible.type = 'text';
            visible.id = visibleId;
            visible.className = el.className;
            visible.placeholder = el.placeholder || 'انتخاب تاریخ';
            visible.autocomplete = 'off';
            el.parentNode.insertBefore(visible, el);
        }

        // Copy current value into the visible input (Gregorian → shown as Jalali by lib)
        visible.value = el.value || '';

        // Hide the original
        el.style.display = 'none';

        try {
            var picker = new Ctor(visible, {
                targetTextSelector: '#' + visibleId,
                targetDateSelector: '#' + el.id,
                isGregorian: false,          // Jalali calendar grid
                dateFormat: 'yyyy-MM-dd',    // Gregorian value written into the hidden input
                textFormat: 'yyyy/MM/dd',    // Jalali text shown to the user
                enableTimePicker: false
            });
            el._mdsPicker = picker;

            // Sync: when the hidden input changes, fire a change event so any
            // legacy listeners on the original input still receive it.
            var observer = new MutationObserver(function () {
                el.dispatchEvent(new Event('change', { bubbles: true }));
            });
            observer.observe(el, { attributes: true, attributeFilter: ['value'] });
            el._mdsObserver = observer;

            console.log('✅ Persian picker initialized for', el.id);
        } catch (e) {
            console.error('❌ Persian picker init failed:', e.message);
            el.style.display = '';
            if (visible && visible.parentNode) visible.parentNode.removeChild(visible);
            initGregorian(el);
        }
    }

    // ---- Main entry ----
    window.initDatePickers = function (root) {
        root = root || document;
        var els = root.querySelectorAll(
            'input[type="date"], input.date-picker, .date-picker-hidden'
        );

        els.forEach(function (el) {
            destroyPicker(el);
            if (isPersian()) {
                initPersian(el);
            } else {
                initGregorian(el);
            }
        });

        console.log('📅 Date pickers initialized | calendar =',
                    isPersian() ? 'jalali' : 'gregorian',
                    '| count =', els.length);
    };

    window.reinitDatePickers = function () {
        initDatePickers();
    };

    // ---- Auto-init on DOM ready ----
    document.addEventListener('DOMContentLoaded', function () {
        // Small delay so Bootstrap modals & dynamic content settle
        setTimeout(function () { initDatePickers(); }, 150);
    });

    console.log('✅ date-utils.js loaded | calendar =', window.currentCalendar);
})();