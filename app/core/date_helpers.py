# app/core/date_helpers.py
from flask import session, current_app
from sqlalchemy.sql import text

def get_engine():
    return current_app.extensions['sqlalchemy'].engine

def get_current_language():
    return session.get('lang', 'en')

def get_current_calendar():
    return session.get('calendar', 'gregorian')

# ---------- DimDate lookup ----------
_dimdate_cache = {}

def get_dimdate_row(date_key):
    if not date_key:
        return None
    try:
        date_key = int(date_key)
    except (ValueError, TypeError):
        return None
    if date_key in _dimdate_cache:
        return _dimdate_cache[date_key]
    try:
        engine = get_engine()
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT * FROM DimDate WHERE DateKey = :dk"
            ), {'dk': date_key}).mappings().first()
            result = dict(row) if row else None
            _dimdate_cache[date_key] = result
            return result
    except Exception as e:
        print(f"[date_helpers] DimDate lookup failed for {date_key}: {e}")
        return None

# ---------- Formatting ----------
def format_date(date_key, calendar=None):
    """
    Presentation-only. Gregorian output from the key; Persian from DimDate.
    """
    if not date_key:
        return ''
    try:
        date_key = int(date_key)
    except (ValueError, TypeError):
        return ''

    calendar = calendar or get_current_calendar()

    # Gregorian
    if calendar == 'gregorian':
        y = date_key // 10000
        m = (date_key // 100) % 100
        d = date_key % 100
        return f"{y:04d}-{m:02d}-{d:02d}"

    # Persian — from DimDate
    row = get_dimdate_row(date_key)
    if not row:
        # Graceful fallback
        y = date_key // 10000
        m = (date_key // 100) % 100
        d = date_key % 100
        return f"{y:04d}-{m:02d}-{d:02d}"

    # Try pre-formatted column first
    keys_lower = {k.lower(): k for k in row.keys()}
    for candidate in ('persianstr', 'persiandate', 'jalalidate', 'shamsidate'):
        if candidate in keys_lower:
            val = row[keys_lower[candidate]]
            if val:
                return str(val)

    # Otherwise build from PersianInt (e.g., 13881022)
    for candidate in ('persianint',):
        if candidate in keys_lower:
            val = row[keys_lower[candidate]]
            if val:
                s = str(int(val))
                return f"{s[:4]}/{s[4:6]}/{s[6:8]}"

    # Or from PersianYearInt / PersianMonthNo / PersianDayInMonth
    py = row.get(keys_lower.get('persianyearint')) if 'persianyearint' in keys_lower else None
    pm = row.get(keys_lower.get('persianmonthno')) if 'persianmonthno' in keys_lower else None
    pd = row.get(keys_lower.get('persiandayinmonth')) if 'persiandayinmonth' in keys_lower else None
    if py and pm and pd:
        return f"{int(py):04d}/{int(pm):02d}/{int(pd):02d}"

    # Last resort
    return f"{date_key // 10000:04d}-{(date_key // 100) % 100:02d}-{date_key % 100:02d}"


def display_date(date_key):
    """Template helper: {{ display_date(date_key) }}"""
    return format_date(date_key)

def date_to_key(date_str):
    if not date_str:
        return None
    return int(str(date_str).replace('-', '').replace('/', ''))